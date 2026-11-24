"""Percurso simples da avaliacao (2C): texto e escolha visual numa so chamada, sem Revisor.

Para a comparacao com o percurso especializado ser justa (spec 2C, secao 3), o simples usa:

- o mesmo contexto em etiquetas de dados (brief, perfil, pilares, propostas aceites, base,
  exemplos de feedback, kit e fotos), sem notas do Revisor;
- os mesmos validadores em codigo (`validate_draft` e `validate_visual`) pelo `extra_validator`
  do motor, com a mesma politica de repeticao;
- a mesma composicao da imagem (`compose_design`: layouts, geracao do fundo pelo motor e
  proveniencia) e o mesmo teto de custo por pacote.

O resultado tem a forma do especializado (`GeneratedPackage`), sem parecer (`verdict = None`) e
com zero voltas. Nada aqui escreve na base alem das execucoes do motor.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from pydantic import BaseModel, ConfigDict

from marketing_agent.agents.simples import SIMPLES_DIR
from marketing_agent.content.design import (
    DesignPayload,
    DesignResult,
    compose_design,
    offered_photos,
)
from marketing_agent.content.loops import CopyPayload, copy_blocks, limits_text
from marketing_agent.content.package_costs import TriggerScope, run_trigger
from marketing_agent.content.package_guard import PackageCostGuard, run_capped
from marketing_agent.content.package_media import DesignInputs
from marketing_agent.content.revision_store import GeneratedText
from marketing_agent.content.rounds import (
    AgentRunFailed,
    GeneratedPackage,
    raise_if_configuration,
)
from marketing_agent.content.schemas import RascunhoTexto
from marketing_agent.content.validation import validate_draft
from marketing_agent.content.visual import RascunhoVisual, validate_visual
from marketing_agent.harness.engine import (
    AgentEngine,
    ExtraValidator,
    RunContext,
    RunResult,
    RunStatus,
)
from marketing_agent.harness.image_calls import ImageAllowance
from marketing_agent.harness.spec import AgentSpec, load_agent_spec
from marketing_agent.images.generation import ImageQuality
from marketing_agent.kb.service import KbEntry
from marketing_agent.llm.types import Message
from marketing_agent.media.library import PhotoRef
from marketing_agent.strategy.loops import build_request, wrap_data

SIMPLE_REQUEST: Final = (
    "Prepara agora a publicacao completa (texto e escolha visual) para os canais {channels}. "
    "Limites: {limits}. Usa so os dados abaixo."
)


class PacoteSimples(BaseModel):
    """Saida do percurso simples: os campos de `RascunhoTexto` e de `RascunhoVisual`, aninhados.

    Aninhados (e nao achatados) para os validadores do Copywriter e do Designer se aplicarem
    sem adaptacao.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    texto: RascunhoTexto
    visual: RascunhoVisual


@dataclass(frozen=True)
class SimplePayload:
    """Entrada do percurso simples: o contexto do Copywriter e a media do Designer."""

    copy: CopyPayload
    media: DesignInputs
    image_quality: ImageQuality


@dataclass(frozen=True)
class SimpleOutput:
    """Texto e imagem composta de uma execucao do percurso simples."""

    texto: RascunhoTexto
    design: DesignResult


def load_simple_spec() -> AgentSpec:
    """Spec do papel `simples`; `SpecLoadError` se o ficheiro e invalido."""
    return load_agent_spec(SIMPLES_DIR / "spec.yaml", {"PacoteSimples": PacoteSimples})


def validate_simple(
    package: PacoteSimples,
    *,
    channels: Sequence[str],
    valid_kb: Mapping[str, KbEntry],
    photos: Mapping[str, PhotoRef],
) -> list[str]:
    """Violacoes do pacote: as do texto e as do visual, prefixadas pelo campo; nada mais."""
    text = validate_draft(package.texto, channels=channels, valid_kb=valid_kb)
    visual = validate_visual(package.visual, photos=photos)
    return [f"texto: {item}" for item in text] + [f"visual: {item}" for item in visual]


def _simple_messages(payload: SimplePayload) -> list[Message]:
    copy = payload.copy
    instruction = SIMPLE_REQUEST.format(
        channels=", ".join(copy.channels), limits=limits_text(copy.channels)
    )
    kit = payload.media.kit
    return build_request(
        instruction,
        *copy_blocks(copy),
        wrap_data(
            "kit_marca", {"cor_primaria": kit.cor_primaria, "cor_secundaria": kit.cor_secundaria}
        ),
        wrap_data("fotos_biblioteca", {"fotos": offered_photos(payload.media.photos)}),
    )


def _validator(payload: SimplePayload) -> ExtraValidator[PacoteSimples]:
    def check(package: PacoteSimples) -> list[str]:
        return validate_simple(
            package,
            channels=payload.copy.channels,
            valid_kb=payload.copy.kb_entries,
            photos=payload.media.photos,
        )

    return check


def _design_payload(payload: SimplePayload, texto: RascunhoTexto) -> DesignPayload:
    media = payload.media
    return DesignPayload(
        brief=payload.copy.brief,
        texto=texto,
        kit=media.kit,
        logo_png=media.logo_png,
        photos=media.photos,
        photo_bytes=media.photo_bytes,
        image_quality=payload.image_quality,
        reviewer_notes=None,
        previous_background=None,
        force_new_background=False,
    )


class SimpleLoop:
    """Uma chamada estruturada (`PacoteSimples`) e a composicao da imagem pelo codigo comum."""

    def run(self, ctx: RunContext, payload: SimplePayload) -> SimpleOutput:
        """Chama o LLM com o contexto em etiquetas; gera o fundo so depois de o pacote validar."""
        package = ctx.call_llm(
            ctx.spec.load_prompt(SIMPLES_DIR),
            _simple_messages(payload),
            PacoteSimples,
            extra_validator=_validator(payload),
        )
        design = compose_design(ctx, _design_payload(payload, package.texto), package.visual)
        return SimpleOutput(package.texto, design)


class SimplePath:
    """Produz um pacote pelo percurso simples; nunca toca em posts nem em revisoes."""

    def __init__(self, engine: AgentEngine, spec: AgentSpec, image_quality: ImageQuality) -> None:
        """Recebe o motor, o spec `simples` e a qualidade dos fundos gerados."""
        self._engine = engine
        self._spec = spec
        self._image_quality = image_quality

    def run(
        self,
        request_id: str,
        brand_id: str,
        payload: CopyPayload,
        media: DesignInputs,
        allowance: ImageAllowance,
        *,
        scope: TriggerScope = "eval",
        guard: PackageCostGuard | None = None,
    ) -> GeneratedPackage | RunResult:
        """Uma execucao `simples`; limites e `NeedsHuman` voltam como `RunResult`.

        `FAILED` sobe como `RunConfigurationFailed` (rota ou capacidades em falta) ou como
        `AgentRunFailed` (mensagem fixa), como no percurso especializado.
        """
        simple = SimplePayload(
            replace(payload, reviewer_notes=None, owner_comment=None), media, self._image_quality
        )
        result = run_capped(
            guard,
            self._spec,
            lambda spec: self._engine.run(
                spec,
                brand_id,
                run_trigger("simple", request_id, scope),
                SimpleLoop(),
                simple,
                image_allowance=allowance,
            ),
        )
        if result.status is RunStatus.FAILED:
            raise_if_configuration(result, "simples")
            raise AgentRunFailed(
                f"a execucao do simples falhou ({result.run_id or 'sem execucao'})"
            )
        if result.status is not RunStatus.SUCCEEDED:
            return result
        output = result.output
        if not isinstance(output, SimpleOutput):
            raise TypeError(f"simples: saida de tipo inesperado ({type(output).__name__})")
        text = GeneratedText(output.texto, None, [], None, 0, result.run_id)
        return GeneratedPackage(text, output.design, image_checked=False)
