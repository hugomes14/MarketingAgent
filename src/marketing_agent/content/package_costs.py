"""Custos e quota de imagens de um pedido de geracao, derivados da base (nunca da memoria).

Um pedido de geracao e um job `generate_post`: as repeticoes do mesmo job (worker morto, lease
vencida, falha com nova tentativa) fazem parte do MESMO pedido; um pedido de alteracoes do
responsavel e um job novo e comeca com a quota cheia. As execucoes de um job ligam-se a ele pelo
`agent_run.trigger` (`post.copy:<job>`, `post.design:<job>`, `post.review:<job>`): a coluna
`agent_run.job_id` e UNIQUE, serve a uma execucao por job e nao a estas tres (ou mais).
Os pacotes da avaliacao (2C) usam o ambito `eval` e o id do `eval_item` como pedido
(`eval.copy:<item>`, ..., `eval.simple:<item>` no percurso simples): o mesmo calculo de custos,
quota e teto serve os dois sem nunca misturar execucoes.

Quota (`remaining_images`): `MAX_IMAGES_PER_REQUEST` menos as imagens deste pedido que o
fornecedor faturou ou pode ter faturado. Contam:
- cada `cost_entry` com `units ? 'images'` (acertada, mesmo se a imagem saiu invalida);
- cada `cost_reservation` de um passo `generate_image` ainda `open` ou `unknown_outcome`.
Uma reserva `released` (nada faturado) nao conta; uma `settled` ja esta no `cost_entry` (cada
reserva e uma coisa ou outra, por isso nunca se conta duas vezes). Sem isto, um worker que
morre depois de faturar e um job reclamado teriam uma `ImageAllowance` nova e poderiam faturar
mais de 2 imagens por pedido.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.db.models import AgentRun, AgentStep, CostEntry, CostReservation
from marketing_agent.harness.image_calls import MAX_IMAGES_PER_REQUEST

type RunStep = Literal["copy", "design", "review", "simple"]
RUN_STEPS: Final[tuple[RunStep, ...]] = ("copy", "design", "review", "simple")
type TriggerScope = Literal["post", "eval"]
IMAGE_STEP_NAME: Final = "generate_image"
_UNSETTLED_IMAGE_STATUSES: Final = ("open", "unknown_outcome")


@dataclass(frozen=True)
class JobCosts:
    """Custos (USD) das execucoes de um pedido: so a imagem e o pacote todo."""

    image_usd: Decimal
    package_usd: Decimal


def run_trigger(step: RunStep, job_id: str, scope: TriggerScope = "post") -> str:
    """`agent_run.trigger` das execucoes de um passo do pacote, ligadas ao pedido (job ou item)."""
    return f"{scope}.{step}:{job_id}"


def request_triggers(job_id: str, scope: TriggerScope = "post") -> list[str]:
    """Todos os `trigger` possiveis das execucoes de um pedido."""
    return [run_trigger(step, job_id, scope) for step in RUN_STEPS]


def request_run_ids(brand_id: str, job_id: str, scope: TriggerScope = "post") -> Select[str]:
    """Consulta dos ids das execucoes de um pedido (todas as tentativas)."""
    triggers = request_triggers(job_id, scope)
    return select(AgentRun.id).where(AgentRun.brand_id == brand_id, AgentRun.trigger.in_(triggers))


def job_costs_in(
    session: Session, brand_id: str, job_id: str, scope: TriggerScope = "post"
) -> JobCosts:
    """Soma os `cost_entry` das execucoes do job (todas as tentativas do pedido).

    O pacote soma os lancamentos e nao `agent_run.cost_usd`: o fecho da execucao de um worker
    morto nunca correu (`cost_usd` ficou 0) mas os lancamentos, escritos no acerto, existem.
    Uma imagem reutilizada nao escreve lancamento, logo nunca se soma duas vezes.
    """
    runs = request_run_ids(brand_id, job_id, scope)
    total = func.coalesce(func.sum(CostEntry.cost_usd), Decimal(0))
    images = func.coalesce(
        func.sum(CostEntry.cost_usd).filter(CostEntry.units.has_key("images")), Decimal(0)
    )
    statement = select(images, total).where(
        CostEntry.brand_id == brand_id, CostEntry.run_id.in_(runs)
    )
    image_usd, package_usd = session.execute(statement).one()
    return JobCosts(Decimal(image_usd), Decimal(package_usd))


def billed_images(
    session_factory: sessionmaker[Session],
    brand_id: str,
    job_id: str,
    scope: TriggerScope = "post",
) -> int:
    """Imagens do pedido faturadas ou que o fornecedor pode ter faturado (ver o modulo)."""
    runs = request_run_ids(brand_id, job_id, scope)
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


def remaining_images(
    session_factory: sessionmaker[Session],
    brand_id: str,
    job_id: str,
    scope: TriggerScope = "post",
) -> int:
    """Lugares de imagem que ainda restam a este pedido (nunca negativo)."""
    billed = billed_images(session_factory, brand_id, job_id, scope)
    return max(0, MAX_IMAGES_PER_REQUEST - billed)
