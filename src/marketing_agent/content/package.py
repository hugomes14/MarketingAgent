"""Nucleo partilhado do pacote texto e imagem: pedido, resultado e percurso especializado.

Camada fina sobre `content.rounds.PackageRounds` (Copywriter, Designer e Revisor por voltas):
o produtor do pacote nunca escreve `post`, `post_revision`, `approval`, `feedback_example` nem
`artifact_head`. Escreve, no maximo, a contabilidade do motor (`agent_run`, `agent_step`,
`cost_entry`, `cost_reservation`). Quem chama decide o que fazer com o resultado em memoria: o
percurso habitual grava ativos e revisao no post (`ContentPipeline._save`); a avaliacao grava a
imagem final no item (ativos de media, nunca posts).

Contrato do pedido:
- `PackageRequest.triggers` e a FAMILIA de triggers do pedido (`PackageTriggers`): dela vem o
  trigger de cada `agent_run`, o gasto contra o teto de custo e a quota de 2 imagens, tudo
  derivado da base. Duas familias da mesma marca nao se influenciam.
- Falhas inesperadas (`AgentRunFailed`, `RunConfigurationFailed`) sobem como excecao, como no
  percurso habitual: o chamador decide se repete.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.content.design import Componente, DesignResult
from marketing_agent.content.loops import CopyPayload
from marketing_agent.content.package_costs import (
    COST_CEILING_REASON,
    PackageCostExceeded,
    PackageCostGuard,
    PackageTriggers,
    family_remaining_images,
)
from marketing_agent.content.package_media import DesignSnapshot
from marketing_agent.content.revision_store import GeneratedText
from marketing_agent.content.rounds import (
    CostCeilingReached,
    GeneratedPackage,
    ImageFailure,
    PackageOutcome,
    PackageRounds,
    describe_stop,
)
from marketing_agent.content.schemas import Revisao
from marketing_agent.content.visual import RascunhoVisual
from marketing_agent.harness.engine import RunResult, RunStatus
from marketing_agent.harness.image_calls import GeneratedImage, ImageAllowance
from marketing_agent.images.layouts import RenderedImage

__all__ = [
    "COST_CEILING_REASON",
    "PackageContext",
    "PackageCostExceeded",
    "PackageCostGuard",
    "PackageProducer",
    "PackageRequest",
    "PackageResult",
    "PackageTriggers",
    "SpecializedPath",
]

type PackageOutcomeName = Literal["ok", "needs_human", "blocked_limit", "teto_pacote", "failed"]


@dataclass(frozen=True)
class PackageContext:
    """O que um pacote precisa de saber: o brief e o contexto de marca, ja carregados.

    `payload` reutiliza `CopyPayload` (brief, canais, perfil, pilares, propostas aceites, itens
    da base e exemplos de feedback) sem duplicar campos: o Copywriter e o Revisor leem-no tal
    qual e o Designer deriva dele o brief. `payload.reviewer_notes` e `payload.owner_comment`
    ficam a `None` (as notas nascem das voltas). Fotos, kit e logotipo nao viajam aqui: o
    produtor le-os da media da marca (`DesignMedia`). Sem `design_snapshot` le-os ao vivo
    (percurso habitual); com ele (`capture_design_snapshot`, no inicio de uma corrida) so usa as
    fotos fixadas e a versao fixada do kit, e os dois percursos devem ler a MESMA fonte:
    `DesignMedia.load(brand_id, request.context.design_snapshot)`.
    """

    payload: CopyPayload
    owner_comment: str | None
    design_snapshot: DesignSnapshot | None = None


@dataclass(frozen=True)
class PackageRequest:
    """Um pacote a produzir: a marca, a familia de triggers e o contexto."""

    brand_id: str
    triggers: PackageTriggers
    context: PackageContext


@dataclass(frozen=True)
class PackageResult:
    """Resultado em memoria de um pacote; nada foi gravado em ativos nem em posts.

    - `ok`: `text` (rascunho, parecer efetivo, voltas) e `design` (rascunho visual, imagem final,
      fundo gerado, componentes) completos; `reviews` sao os pareceres das passagens.
    - `failed`: a parte de imagem falhou; `text` segue sozinho (sem parecer) e `reason` e a
      razao fixa da imagem.
    - `needs_human`, `blocked_limit`, `teto_pacote`: nao ha pacote; `reason` e a frase fixa que
      pode ir para `last_error` (nunca texto do modelo).
    """

    outcome: PackageOutcomeName
    text: GeneratedText | None = None
    design: DesignResult | None = None
    image_checked: bool = False
    reviews: tuple[Revisao, ...] = ()
    reason: str | None = None

    @property
    def review_rounds(self) -> int:
        """Voltas do Revisor gastas (0 sem texto)."""
        return 0 if self.text is None else self.text.rounds

    @property
    def visual(self) -> RascunhoVisual | None:
        """Escolha visual do Designer."""
        return None if self.design is None else self.design.draft

    @property
    def final_image(self) -> RenderedImage | None:
        """Imagem final composta (bytes JPEG em memoria)."""
        return None if self.design is None else self.design.final

    @property
    def generated_background(self) -> GeneratedImage | None:
        """Fundo gerado por IA usado, ou `None` nos layouts com foto."""
        return None if self.design is None else self.design.generated

    @property
    def components(self) -> tuple[Componente, ...]:
        """Componentes da imagem final (vazio sem imagem)."""
        return () if self.design is None else self.design.components

    @property
    def is_ai_generated(self) -> bool:
        """A imagem final inclui um fundo gerado."""
        return self.design is not None and self.design.is_ai_generated

    @property
    def failure_message(self) -> str:
        """Razao fixa de um resultado sem pacote completo; erro se nao houver (`ok`)."""
        if self.reason is None:
            raise ValueError("este resultado nao tem razao de falha")
        return self.reason

    def as_package(self) -> GeneratedPackage:
        """O pacote completo (`ok`) no formato que a gravacao do post consome."""
        if self.outcome != "ok" or self.text is None or self.design is None:
            raise ValueError("so um resultado 'ok' tem pacote completo")
        return GeneratedPackage(self.text, self.design, self.image_checked, self.reviews)


class PackageProducer(Protocol):
    """Quem produz um pacote a partir de um pedido; os dois percursos cumprem-no."""

    def produce(self, request: PackageRequest) -> PackageResult:
        """Produz o pacote do pedido sem gravar posts."""
        ...


class SpecializedPath:
    """Percurso especializado: `PackageRounds` sob o teto de custo e a quota de imagens."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        rounds: PackageRounds,
        ceiling_usd: Decimal,
    ) -> None:
        """Guarda a base (gasto e quota), as voltas do pacote e o teto de custo por pacote."""
        self._session_factory = session_factory
        self._rounds = rounds
        self._ceiling_usd = ceiling_usd

    def produce(self, request: PackageRequest) -> PackageResult:
        """Corre as voltas do pacote; quota e teto vem da base, pela familia de triggers.

        Repetir o pedido (worker morto, nova tentativa do job) continua o mesmo teto e a mesma
        quota, porque o gasto das tentativas anteriores esta nas execucoes da familia.
        """
        family = request.triggers.family
        allowance = ImageAllowance(
            family_remaining_images(self._session_factory, request.brand_id, family)
        )
        guard = PackageCostGuard(self._session_factory, request.brand_id, family, self._ceiling_usd)
        outcome = self._rounds.run(
            request.triggers,
            request.brand_id,
            request.context.payload,
            request.context.owner_comment,
            allowance,
            guard,
            request.context.design_snapshot,
        )
        return _to_result(outcome)


def _to_result(outcome: PackageOutcome) -> PackageResult:
    """Traduz o desfecho das voltas para o resultado do pacote."""
    if isinstance(outcome, GeneratedPackage):
        return PackageResult(
            "ok",
            outcome.text,
            outcome.design,
            outcome.image_checked,
            outcome.reviews,
        )
    if isinstance(outcome, ImageFailure):
        return PackageResult("failed", text=outcome.text, reason=outcome.reason)
    if isinstance(outcome, CostCeilingReached):
        return PackageResult("teto_pacote", reason=COST_CEILING_REASON)
    return _stopped(outcome)


def _stopped(result: RunResult) -> PackageResult:
    """Paragem do Copywriter ou do Revisor na 1.a passagem: ajuda humana ou limite."""
    name: PackageOutcomeName = (
        "blocked_limit" if result.status is RunStatus.BLOCKED_LIMIT else "needs_human"
    )
    return PackageResult(name, reason=describe_stop(result))
