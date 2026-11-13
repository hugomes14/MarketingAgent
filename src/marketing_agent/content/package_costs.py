"""Custos e quota de imagens de um pedido de geracao, derivados da base (nunca da memoria).

Um pedido de geracao e um job `generate_post`: as repeticoes do mesmo job (worker morto, lease
vencida, falha com nova tentativa) fazem parte do MESMO pedido; um pedido de alteracoes do
responsavel e um job novo e comeca com a quota cheia. As execucoes de um job ligam-se a ele pelo
`agent_run.trigger` (`post.copy:<job>`, `post.design:<job>`, `post.review:<job>`): a coluna
`agent_run.job_id` e UNIQUE, serve a uma execucao por job e nao a estas tres (ou mais).

Quota (`remaining_images`): `MAX_IMAGES_PER_REQUEST` menos as imagens deste pedido que o
fornecedor faturou ou pode ter faturado. Contam:
- cada `cost_entry` com `units ? 'images'` (acertada, mesmo se a imagem saiu invalida);
- cada `cost_reservation` de um passo `generate_image` ainda `open` ou `unknown_outcome`.
Uma reserva `released` (nada faturado) nao conta; uma `settled` ja esta no `cost_entry` (cada
reserva e uma coisa ou outra, por isso nunca se conta duas vezes). Sem isto, um worker que
morre depois de faturar e um job reclamado teriam uma `ImageAllowance` nova e poderiam faturar
mais de 2 imagens por pedido.

Familia de triggers (`PackageTriggers`): a quota e o teto de custo por pacote (`PackageCostGuard`)
somam as execucoes cujo trigger esta na familia do pedido. O job `generate_post` usa os tres
`post.*`; um item de avaliacao usa um so (`eval_item:<id>`). Familias diferentes da mesma marca
nao se influenciam.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Final, Literal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.budget.controller import CENT_MILLI, PENDING_STATUSES
from marketing_agent.db.models import AgentRun, AgentStep, CostEntry, CostReservation
from marketing_agent.harness.image_calls import MAX_IMAGES_PER_REQUEST
from marketing_agent.harness.spec import AgentSpec

type RunStep = Literal["copy", "design", "review"]
RUN_STEPS: Final[tuple[RunStep, ...]] = ("copy", "design", "review")
IMAGE_STEP_NAME: Final = "generate_image"
EVAL_ITEM_TRIGGER_PREFIX: Final = "eval_item:"
# Frase fixa (nunca texto do modelo) de quando o teto de custo do pacote ja nao deixa chamar.
COST_CEILING_REASON: Final = "pacote: teto de custo atingido"
_UNSETTLED_IMAGE_STATUSES: Final = ("open", "unknown_outcome")


@dataclass(frozen=True)
class JobCosts:
    """Custos (USD) das execucoes de um pedido: so a imagem e o pacote todo."""

    image_usd: Decimal
    package_usd: Decimal


def run_trigger(step: RunStep, job_id: str) -> str:
    """`agent_run.trigger` das execucoes de um passo do pacote, ligadas ao job."""
    return f"post.{step}:{job_id}"


@dataclass(frozen=True)
class PackageTriggers:
    """`agent_run.trigger` de cada passo do pacote: a FAMILIA de triggers de um pedido.

    O pedido e dono das execucoes cujo trigger pertence a `family`; e por essa familia que se
    somam custos e se conta a quota de imagens (nunca por marca ou por post):
    - `for_job`: pedido de geracao de um post, um trigger por passo (`post.<passo>:<job>`);
    - `single`: um so trigger para os tres passos (item de avaliacao: `eval_item:<id>`).
    Duas familias da mesma marca nao se influenciam: tetos e quotas sao independentes.
    """

    copy: str
    design: str
    review: str

    @classmethod
    def for_job(cls, job_id: str) -> "PackageTriggers":
        """Os tres triggers `post.*` do job `generate_post`."""
        return cls(
            run_trigger("copy", job_id),
            run_trigger("design", job_id),
            run_trigger("review", job_id),
        )

    @classmethod
    def single(cls, trigger: str) -> "PackageTriggers":
        """O mesmo trigger nos tres passos (a familia tem um so elemento).

        So aceita triggers de avaliacao (`eval_item:<id>`): assim um trigger de avaliacao nunca
        coincide com a familia `post.*` de um job (`ValueError` de mensagem fixa).
        """
        if not trigger.startswith(EVAL_ITEM_TRIGGER_PREFIX) or trigger == EVAL_ITEM_TRIGGER_PREFIX:
            raise ValueError(f"o trigger unico deve ser '{EVAL_ITEM_TRIGGER_PREFIX}<id>'")
        return cls(trigger, trigger, trigger)

    @property
    def family(self) -> tuple[str, ...]:
        """Os triggers distintos do pedido, pela ordem copy, design, review."""
        return tuple(dict.fromkeys((self.copy, self.design, self.review)))

    def of_step(self, step: RunStep) -> str:
        """Trigger da execucao do passo `step`."""
        return {"copy": self.copy, "design": self.design, "review": self.review}[step]


def _run_ids(brand_id: str, triggers: Sequence[str]) -> Select[str]:
    return select(AgentRun.id).where(AgentRun.brand_id == brand_id, AgentRun.trigger.in_(triggers))


def _job_run_ids(brand_id: str, job_id: str) -> Select[str]:
    return _run_ids(brand_id, PackageTriggers.for_job(job_id).family)


def job_costs_in(session: Session, brand_id: str, job_id: str) -> JobCosts:
    """Soma os `cost_entry` das execucoes do job (todas as tentativas do pedido).

    O pacote soma os lancamentos e nao `agent_run.cost_usd`: o fecho da execucao de um worker
    morto nunca correu (`cost_usd` ficou 0) mas os lancamentos, escritos no acerto, existem.
    Uma imagem reutilizada nao escreve lancamento, logo nunca se soma duas vezes.
    """
    runs = _job_run_ids(brand_id, job_id)
    total = func.coalesce(func.sum(CostEntry.cost_usd), Decimal(0))
    images = func.coalesce(
        func.sum(CostEntry.cost_usd).filter(CostEntry.units.has_key("images")), Decimal(0)
    )
    statement = select(images, total).where(
        CostEntry.brand_id == brand_id, CostEntry.run_id.in_(runs)
    )
    image_usd, package_usd = session.execute(statement).one()
    return JobCosts(Decimal(image_usd), Decimal(package_usd))


def billed_images(session_factory: sessionmaker[Session], brand_id: str, job_id: str) -> int:
    """Imagens do job faturadas ou que o fornecedor pode ter faturado (ver o modulo)."""
    return family_billed_images(session_factory, brand_id, PackageTriggers.for_job(job_id).family)


def family_billed_images(
    session_factory: sessionmaker[Session], brand_id: str, triggers: Sequence[str]
) -> int:
    """Imagens faturadas (ou possivelmente) pelas execucoes cujo trigger esta em `triggers`."""
    runs = _run_ids(brand_id, triggers)
    entries = select(func.count()).where(
        CostEntry.brand_id == brand_id,
        CostEntry.run_id.in_(runs),
        CostEntry.units.has_key("images"),
    )
    unsettled = (
        select(func.count())
        .select_from(CostReservation)
        .join(AgentStep, CostReservation.step_id == AgentStep.id)
        .where(
            CostReservation.brand_id == brand_id,
            CostReservation.run_id.in_(runs),
            CostReservation.status.in_(_UNSETTLED_IMAGE_STATUSES),
            AgentStep.name == IMAGE_STEP_NAME,
        )
    )
    with session_factory() as session:
        return (session.scalar(entries) or 0) + (session.scalar(unsettled) or 0)


def remaining_images(session_factory: sessionmaker[Session], brand_id: str, job_id: str) -> int:
    """Lugares de imagem que ainda restam a este pedido (nunca negativo)."""
    return max(0, MAX_IMAGES_PER_REQUEST - billed_images(session_factory, brand_id, job_id))


def family_remaining_images(
    session_factory: sessionmaker[Session], brand_id: str, triggers: Sequence[str]
) -> int:
    """Lugares de imagem que restam a uma familia de triggers (nunca negativo)."""
    billed = family_billed_images(session_factory, brand_id, triggers)
    return max(0, MAX_IMAGES_PER_REQUEST - billed)


class PackageCostExceeded(Exception):
    """O teto de custo do pacote esta esgotado: nao se faz mais nenhuma chamada paga.

    A mensagem e fixa (`COST_CEILING_REASON`): serve de `last_error` sem copiar texto externo.
    """

    def __init__(self) -> None:
        """Sem argumentos: a mensagem e sempre `COST_CEILING_REASON`."""
        super().__init__(COST_CEILING_REASON)


class PackageCostGuard:
    """Teto de custo de UM pacote, somando todas as tentativas e voltas da sua familia de triggers.

    O gasto vem sempre da base (nunca de memoria), por isso sobrevive a um worker morto e a
    repeticoes do job: `cost_entry` das execucoes da familia mais as reservas ainda pendentes
    (`open` e `unknown_outcome`, esta ultima conta como gasta: pode ter sido faturada). Uma
    reserva ou e pendente ou e um `cost_entry`, por isso nunca se conta duas vezes.

    O teto nao e um maximo absoluto: o custo acertado de uma chamada de texto pode passar da sua
    reserva (preco mais recente, entrada estimada), por isso o total da familia pode exceder o
    teto em alguns centimos. E a mesma propriedade do limite por execucao do 2B.

    `cap` aplica-se a CADA `engine.run` do pacote: o limite de custo da execucao passa a ser o
    que sobra do teto, e como o motor recusa reservas acima do limite da execucao, as reservas do
    pacote nunca ultrapassam o teto (as execucoes de um pacote correm em sequencia).
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        brand_id: str,
        triggers: Sequence[str],
        ceiling_usd: Decimal,
    ) -> None:
        """Guarda a marca, a familia de triggers e o teto (USD, estritamente positivo)."""
        if ceiling_usd <= 0:
            raise ValueError("o teto de custo do pacote deve ser positivo")
        self._session_factory = session_factory
        self._brand_id = brand_id
        self._triggers = tuple(triggers)
        self._ceiling_usd = ceiling_usd

    def spent_usd(self) -> Decimal:
        """Gasto da familia: lancamentos de custo mais reservas pendentes (`PENDING_STATUSES`)."""
        runs = _run_ids(self._brand_id, self._triggers)
        entries = select(func.coalesce(func.sum(CostEntry.cost_usd), Decimal(0))).where(
            CostEntry.brand_id == self._brand_id, CostEntry.run_id.in_(runs)
        )
        pending = select(func.coalesce(func.sum(CostReservation.reserved_usd), Decimal(0))).where(
            CostReservation.brand_id == self._brand_id,
            CostReservation.run_id.in_(runs),
            CostReservation.status.in_(PENDING_STATUSES),
        )
        with self._session_factory() as session:
            return Decimal(session.scalar(entries) or 0) + Decimal(session.scalar(pending) or 0)

    def cap(self, spec: AgentSpec) -> AgentSpec:
        """Copia de `spec` com o limite de custo da execucao reduzido ao que sobra do teto.

        O `spec` recebido nunca e alterado (e partilhado entre threads). O que sobra arredonda-se
        por defeito a 6 casas (a escala da base), para a soma nunca passar o teto; sem nada
        a sobrar levanta `PackageCostExceeded`.
        """
        left = (self._ceiling_usd - self.spent_usd()).quantize(CENT_MILLI, rounding=ROUND_FLOOR)
        if left <= 0:
            raise PackageCostExceeded
        limits = spec.limits.model_copy(
            update={"max_cost_usd": min(spec.limits.max_cost_usd, left)}
        )
        return spec.model_copy(update={"limits": limits})
