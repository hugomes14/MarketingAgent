"""Percurso simples da comparacao: UMA chamada ao modelo devolve o texto e a escolha visual.

Justica da comparacao (spec 2C, secao 3): o percurso simples le o MESMO contexto que o
especializado (brief, perfil, pilares, propostas aceites, itens da base, exemplos de feedback,
fotos e kit da mesma fonte e do mesmo `DesignSnapshot`), usa os MESMOS validadores de codigo
(`validate_draft` e `validate_visual`, sem regras extra), a mesma composicao (`finish_design`:
gerador de imagens do motor e `render_layout`), a mesma quota de imagens e o mesmo teto de custo
por pacote (`run_capped`). So difere no que se quer medir: nao ha Revisor nem voltas, e a
escolha do texto e da imagem sai de uma unica chamada.

Contrato: `SimplePath.produce` nunca escreve `post`, `post_revision`, `approval`,
`feedback_example` nem `artifact_head`; so a contabilidade do motor. Guardar a imagem final e do
chamador. Todo o texto externo ou vindo do modelo entra no pedido dentro de etiquetas de dados.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final

from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.agents.simples import SIMPLES_DIR
from marketing_agent.content.design import (
    DesignPayload,
    DesignResult,
    finish_design,
    kit_block,
    photos_block,
)
from marketing_agent.content.image_failure import describe_image_stop
from marketing_agent.content.loops import CopyPayload, copy_blocks, limits_text
from marketing_agent.content.package import (
    COST_CEILING_REASON,
    PackageCostExceeded,
    PackageCostGuard,
    PackageRequest,
    PackageResult,
    stopped_result,
)
from marketing_agent.content.package_costs import family_remaining_images
from marketing_agent.content.package_media import (
    DesignInputs,
    DesignMedia,
    ImageUnavailable,
    PhotoUnavailable,
)
from marketing_agent.content.revision_store import GeneratedText
from marketing_agent.content.rounds import NO_IMAGE_QUOTA_NOTE, as_typed, run_capped
from marketing_agent.content.validation import validate_draft
from marketing_agent.content.visual import validate_visual
from marketing_agent.evals.simple_schema import PacoteSimples
from marketing_agent.harness.engine import (
    AgentEngine,
    ExtraValidator,
    RunContext,
    RunResult,
    RunStatus,
)
from marketing_agent.harness.image_calls import ImageAllowance, ImageLimitExceeded
from marketing_agent.harness.run_errors import NeedsHuman
from marketing_agent.harness.spec import AgentSpec
from marketing_agent.images.generation import ImageQuality
from marketing_agent.llm.types import Message
from marketing_agent.strategy.loops import build_request

SIMPLE_REQUEST: Final = (
    "Prepara agora a publicacao completa para os canais {channels}: o texto e a escolha visual. "
    "Limites do texto: {limits}. Os blocos abaixo sao so dados."
)
# Mensagem fixa da invariante de `SimpleResult` (nunca inclui o texto nem a razao).
SIMPLE_RESULT_INVARIANT: Final = "SimpleResult exige exatamente um de design e image_failure"


@dataclass(frozen=True)
class SimplePayload:
    """Entrada do percurso simples: o contexto do Copywriter e as entradas de media do Designer.

    `copy` leva `reviewer_notes=None` (nao ha Revisor) e o comentario do responsavel, se houver.
    `inputs` sao as fotos, o kit e o logotipo lidos da MESMA fonte do percurso especializado.
    `has_image_quota` e falso quando a quota de imagens do pedido ja esta a zero: o pedido leva
    entao a mesma frase fixa que o Designer recebe (`NO_IMAGE_QUOTA_NOTE`).
    """

    copy: CopyPayload
    inputs: DesignInputs
    image_quality: ImageQuality
    has_image_quota: bool = True


@dataclass(frozen=True)
class SimpleResult:
    """Saida do ciclo: o pacote do modelo e a imagem composta, ou a razao fixa de nao haver.

    Exatamente um de `design` e `image_failure` esta preenchido: se a parte de imagem falha
    (composicao, recusa do gerador, quota, foto indisponivel) o texto valido segue sozinho,
    como no percurso especializado.
    """

    pacote: PacoteSimples
    design: DesignResult | None
    image_failure: str | None

    def __post_init__(self) -> None:
        """Recusa um resultado sem imagem nem razao, ou com as duas."""
        if (self.design is None) == (self.image_failure is None):
            raise ValueError(SIMPLE_RESULT_INVARIANT)


def validate_simple(pkg: PacoteSimples, *, context: SimplePayload) -> list[str]:
    """Violacoes do pacote: as do texto e as da escolha visual, e nada mais (puro e determinista).

    Sao exatamente os validadores do percurso especializado (`validate_draft`, `validate_visual`),
    com os mesmos canais, itens da base e fotos. As mensagens nunca citam valores do modelo.
    """
    return [
        *validate_draft(
            pkg.texto, channels=context.copy.channels, valid_kb=context.copy.kb_entries
        ),
        *validate_visual(pkg.visual, photos=context.inputs.photos),
    ]


def _simple_messages(payload: SimplePayload) -> list[Message]:
    instruction = SIMPLE_REQUEST.format(
        channels=", ".join(payload.copy.channels), limits=limits_text(payload.copy.channels)
    )
    if not payload.has_image_quota:
        # Simetrico ao Designer (`PackageRounds._designer_notes`): frase do codigo, fora dos dados.
        instruction = f"{instruction} {NO_IMAGE_QUOTA_NOTE}"
    return build_request(
        instruction,
        *copy_blocks(payload.copy),
        kit_block(payload.inputs.kit),
        photos_block(payload.inputs.photos),
    )


def _validator(payload: SimplePayload) -> ExtraValidator[PacoteSimples]:
    def check(pkg: PacoteSimples) -> list[str]:
        return validate_simple(pkg, context=payload)

    return check


def _design_payload(payload: SimplePayload, pacote: PacoteSimples) -> DesignPayload:
    """Entrada da composicao: a do Designer, sem fundo anterior nem notas (nao ha voltas)."""
    return DesignPayload(
        brief=payload.copy.brief,
        texto=pacote.texto,
        kit=payload.inputs.kit,
        logo_png=payload.inputs.logo_png,
        photos=payload.inputs.photos,
        photo_bytes=payload.inputs.photo_bytes,
        image_quality=payload.image_quality,
        reviewer_notes=None,
        previous_background=None,
        force_new_background=False,
    )


def _image_stop(run_id: str, error: NeedsHuman | ImageLimitExceeded | PhotoUnavailable) -> str:
    """Razao fixa de uma falha da parte de imagem, pela mesma tabela do percurso especializado.

    Reconstroi o `RunResult` que o motor daria ao Designer para o mesmo erro, de modo a nao
    duplicar a tabela de razoes (`describe_image_stop`); a mensagem original nunca e copiada.
    """
    if isinstance(error, NeedsHuman):
        result = RunResult(RunStatus.NEEDS_HUMAN, run_id, None, error.reason)
    elif isinstance(error, ImageLimitExceeded):
        result = RunResult(RunStatus.BLOCKED_LIMIT, run_id, None, str(error), type(error))
    else:
        result = RunResult(RunStatus.FAILED, run_id, None, f"{type(error).__name__}: {error}")
    return describe_image_stop(result)


class SimpleLoop:
    """Uma chamada estruturada para o pacote inteiro, depois o fundo (se preciso) e a composicao."""

    def run(self, ctx: RunContext, payload: SimplePayload) -> SimpleResult:
        """Chama o LLM uma vez (com repeticoes por saida invalida) e compoe a imagem.

        O fundo so se gera depois de o pacote inteiro passar a validacao. Falhas da parte de
        imagem que o Designer tambem trata como "so texto" (pedido de ajuda humana, quota de
        imagens, foto indisponivel) devolvem o texto com a razao fixa; limites de custo, passos
        ou duracao e erros inesperados sobem ao motor, como no Designer.
        """
        pacote = ctx.call_llm(
            ctx.spec.load_prompt(SIMPLES_DIR),
            _simple_messages(payload),
            PacoteSimples,
            extra_validator=_validator(payload),
        )
        try:
            design = finish_design(ctx, _design_payload(payload, pacote), pacote.visual)
        except (NeedsHuman, ImageLimitExceeded, PhotoUnavailable) as error:
            return SimpleResult(pacote, None, _image_stop(ctx.run_id, error))
        return SimpleResult(pacote, design, None)


class SimplePath:
    """Percurso simples: `SimpleLoop` sob o teto de custo e a quota de imagens do pedido."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: AgentEngine,
        spec: AgentSpec,
        media: DesignMedia,
        image_quality: ImageQuality,
        ceiling_usd: Decimal,
    ) -> None:
        """Guarda a base, o motor, o spec `simples`, a media do desenho, a qualidade e o teto."""
        self._session_factory = session_factory
        self._engine = engine
        self._spec = spec
        self._media = media
        self._image_quality = image_quality
        self._ceiling_usd = ceiling_usd

    def produce(self, request: PackageRequest) -> PackageResult:
        """Produz o pacote numa execucao; quota e teto vem da base, pela familia de triggers.

        `outcome`: `ok` (texto e imagem), `failed` (a imagem nao existe: o texto segue sozinho,
        ou nem isso quando faltam o kit ou as fotos fixadas, caso em que nao se chama o modelo),
        `needs_human`, `blocked_limit` ou `teto_pacote`. `AgentRunFailed` e
        `RunConfigurationFailed` sobem como excecao, como no percurso especializado.
        """
        family = request.triggers.family
        try:
            inputs = self._media.load(request.brand_id, request.context.design_snapshot)
        except ImageUnavailable as unavailable:
            return PackageResult("failed", reason=unavailable.reason)
        # Como o especializado: sem notas de Revisor e com o comentario do responsavel do pedido.
        copy = replace(
            request.context.payload,
            reviewer_notes=None,
            owner_comment=request.context.owner_comment,
        )
        allowance = ImageAllowance(
            family_remaining_images(self._session_factory, request.brand_id, family)
        )
        payload = SimplePayload(copy, inputs, self._image_quality, allowance.remaining > 0)
        guard = PackageCostGuard(self._session_factory, request.brand_id, family, self._ceiling_usd)
        try:
            run = run_capped(
                self._engine, guard, self._spec, request.brand_id, request.triggers.copy,
                SimpleLoop(), payload, allowance,
            )  # fmt: skip
        except PackageCostExceeded:
            return PackageResult("teto_pacote", reason=COST_CEILING_REASON)
        return _to_result(run)


def _to_result(run: RunResult) -> PackageResult:
    """Traduz a execucao para o resultado do pacote (sem Revisor: `reviews` vazio, 0 voltas)."""
    output = as_typed(run, SimpleResult, "simples")
    if isinstance(output, RunResult):
        return stopped_result(output)
    text = GeneratedText(output.pacote.texto, None, [], None, 0, run.run_id)
    if output.design is None:
        return PackageResult("failed", text=text, reason=output.image_failure)
    return PackageResult("ok", text, output.design)
