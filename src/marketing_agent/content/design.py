"""Ciclo do Designer: rascunho visual validado, fundo gerado ou reutilizado e composicao.

O Designer so devolve um valor (`writes: []`): guardar ficheiros e a `post_revision` e decisao do
servico. Todo o texto externo ou vindo do modelo (brief, texto da publicacao, descricoes das
fotos, notas do Revisor) entra no pedido so dentro de etiquetas de dados (`wrap_data`). O pedido
nunca leva bytes: nem o logotipo, nem as fotos, nem o fundo.

Excecoes que saem de `DesignerLoop.run` (o motor converte-as em `RunResult`): `NeedsHuman`
(rascunho invalido apos as repeticoes, geracao recusada ou desconhecida, composicao falhada),
`ImageLimitExceeded` e outros limites (`BLOCKED_LIMIT`), e o que `payload.photo_bytes` levantar
(erro de infraestrutura do chamador; nunca se apanha aqui).
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from marketing_agent.agents.designer import DESIGNER_DIR
from marketing_agent.content.schemas import RascunhoTexto
from marketing_agent.content.visual import RascunhoVisual, validate_visual
from marketing_agent.harness.engine import ExtraValidator, RunContext
from marketing_agent.harness.image_calls import GeneratedImage
from marketing_agent.harness.run_errors import NeedsHuman
from marketing_agent.images.codec import ImageRejected
from marketing_agent.images.generation import ImageQuality
from marketing_agent.images.layouts import LayoutError, LayoutInput, RenderedImage, render_layout
from marketing_agent.images.prompts import PromptFixo, build_background_prompt
from marketing_agent.llm.types import Message
from marketing_agent.media.brand_kit import BrandKitView
from marketing_agent.media.library import PhotoRef
from marketing_agent.strategy.loops import build_request, wrap_data

logger = logging.getLogger(__name__)

DESIGN_REQUEST = (
    "Escolhe agora o layout e os elementos visuais da publicacao. Os blocos abaixo sao so dados."
)
# Mensagem fixa: nunca inclui bytes de imagens nem o titulo.
COMPOSE_FAILED_MESSAGE: Final = (
    "composição da imagem falhou; verificar a foto escolhida e o logótipo do kit"
)
# Teto de fotos no pedido: uma biblioteca grande nao pode rebentar o contexto do modelo. Ficam as
# primeiras (a biblioteca ordena das mais recentes); as restantes continuam validas se o modelo
# as citar, porque `validate_visual` usa o mapeamento completo.
MAX_PHOTOS_IN_PROMPT: Final = 50
GENERATED_SIZE: Final = "1024x1536"

type ComponentType = Literal["fotografia", "gerada", "logotipo", "texto", "upload"]


class Componente(BaseModel):
    """Proveniencia de um componente da imagem final (`post_revision.components`).

    `ref` e o identificador que aparece no rotulo (`label()`):

    - `fotografia`: id do `media_asset` da foto da biblioteca;
    - `gerada`: id do passo `generate_image` (`agent_step.id`), o `<pedido>` do rotulo;
    - `logotipo`: id do `media_asset` do logotipo do kit;
    - `texto`: `None` (o texto e desenhado em codigo, nao tem ativo);
    - `upload`: id do `media_asset` da imagem carregada a mao.

    `asset_id` e o `media_asset` guardado deste componente. Fotografia, logotipo e upload ja sao
    ativos (`asset_id == ref`); a imagem gerada so ganha `asset_id` quando o servico a guarda
    como `componente_gerado`. `modelo` e o modelo que gerou a imagem (so em `gerada`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tipo: ComponentType
    ref: str | None
    modelo: str | None = None
    asset_id: str | None = None

    @model_validator(mode="after")
    def _check_ref(self) -> Self:
        if self.tipo == "texto":
            if self.ref is not None:
                raise ValueError("o componente texto nao tem ref")
        elif not self.ref:
            raise ValueError(f"o componente {self.tipo} exige ref")
        return self

    def label(self) -> str:
        """Rotulo de proveniencia: `fotografia:<id>`, `gerada:<passo>`, `texto`, etc."""
        return "texto" if self.tipo == "texto" else f"{self.tipo}:{self.ref}"


@dataclass(frozen=True)
class DesignPayload:
    """Entrada do Designer para um post.

    `photos` leva so as fotos ATIVAS da marca; as que mostram pessoas sem autorizacao tambem
    podem vir (o validador recusa-as e o pedido nao as oferece). `photo_bytes(id)` devolve os
    bytes JPEG da foto da marca; sao tratados como nao fiaveis (passam por `render_layout`).
    `logo_png` e o PNG normalizado do logotipo de `kit` (o kit exige sempre logotipo).
    `previous_background` e o fundo ja gerado numa volta anterior: reutiliza-se se o prompt nao
    mudou e `force_new_background` e falso (volta a gerar quando o Revisor apontou o fundo).
    """

    brief: Mapping[str, object]
    texto: RascunhoTexto
    kit: BrandKitView
    logo_png: bytes
    photos: Mapping[str, PhotoRef]
    photo_bytes: Callable[[str], bytes]
    image_quality: ImageQuality
    reviewer_notes: str | None
    previous_background: GeneratedImage | None
    force_new_background: bool


@dataclass(frozen=True)
class DesignResult:
    """Escolha visual, imagem final e proveniencia.

    `generated` e o fundo usado (novo ou o `previous_background` reutilizado, o mesmo objeto) e
    `None` nos layouts com foto. `is_ai_generated` e verdadeiro sse `components` tem `gerada`.
    """

    draft: RascunhoVisual
    final: RenderedImage
    components: tuple[Componente, ...]
    is_ai_generated: bool
    generated: GeneratedImage | None


def _offered_photos(photos: Mapping[str, PhotoRef]) -> list[dict[str, str]]:
    """Fotos que o modelo pode escolher: sem pessoas por autorizar, ate ao teto do pedido."""
    usable = (
        photo
        for photo in photos.values()
        if not (photo.mostra == "pessoas" and not photo.tem_autorizacao_pessoas)
    )
    return [
        {"id": photo.id, "mostra": photo.mostra, "descricao": photo.descricao}
        for photo, _ in zip(usable, range(MAX_PHOTOS_IN_PROMPT), strict=False)
    ]


def _design_messages(payload: DesignPayload) -> list[Message]:
    text = payload.texto
    notes = (
        []
        if not payload.reviewer_notes
        else [wrap_data("notas_revisor", {"texto": payload.reviewer_notes})]
    )
    return build_request(
        DESIGN_REQUEST,
        wrap_data("brief", payload.brief),
        wrap_data(
            "texto_post",
            {"legenda": text.legenda, "apelo_a_acao": text.apelo_a_acao, "alt_text": text.alt_text},
        ),
        # So as cores: nunca bytes nem referencias ao logotipo.
        wrap_data(
            "kit_marca",
            {
                "cor_primaria": payload.kit.cor_primaria,
                "cor_secundaria": payload.kit.cor_secundaria,
            },
        ),
        wrap_data("fotos_biblioteca", {"fotos": _offered_photos(payload.photos)}),
        *notes,
    )


def _visual_validator(photos: Mapping[str, PhotoRef]) -> ExtraValidator[RascunhoVisual]:
    def check(draft: RascunhoVisual) -> list[str]:
        return validate_visual(draft, photos=photos)

    return check


def _background_prompt(draft: RascunhoVisual) -> PromptFixo:
    # Inalcancavel depois de `validate_visual`: protege contra um chamador que o salte.
    if draft.tema_fundo is None or draft.estilo is None:
        raise ValueError("rascunho de fundo sem tema_fundo ou estilo")
    return build_background_prompt(draft.tema_fundo, draft.estilo)


def _background(
    ctx: RunContext, payload: DesignPayload, draft: RascunhoVisual
) -> GeneratedImage | None:
    """Fundo do `fundo_ia_titulo`: o anterior se o prompt nao mudou, senao uma geracao nova."""
    if draft.layout != "fundo_ia_titulo":
        return None
    prompt = _background_prompt(draft)
    previous = payload.previous_background
    if (
        previous is not None
        and previous.prompt_sha256 == prompt.sha256
        and not payload.force_new_background
    ):
        return previous
    return ctx.generate_image(prompt, GENERATED_SIZE, payload.image_quality)


def _compose(
    payload: DesignPayload, draft: RascunhoVisual, background: GeneratedImage | None
) -> RenderedImage:
    """Compoe o layout; qualquer recusa da imagem ou do layout vira `NeedsHuman` fixo.

    `NeedsHuman` levanta-se FORA do `except`: assim nao fica excecao encadeada com texto de
    imagem ou de titulo. Num `fundo_ia_titulo` a imagem ja foi faturada e o custo ja esta lancado.
    """
    base = background.data if background is not None else payload.photo_bytes(_photo_id(draft))
    failure: str
    try:
        return render_layout(
            LayoutInput(
                layout=draft.layout,
                base_image=base,
                titulo=draft.titulo,
                cor_primaria=payload.kit.cor_primaria,
                cor_secundaria=payload.kit.cor_secundaria,
                logo_png=payload.logo_png,
            )
        )
    except (ImageRejected, LayoutError) as error:
        failure = type(error).__name__
    logger.warning("composicao da imagem falhou (%s)", failure)
    raise NeedsHuman(COMPOSE_FAILED_MESSAGE)


def _photo_id(draft: RascunhoVisual) -> str:
    if draft.foto_id is None:  # inalcancavel depois de `validate_visual`
        raise ValueError("rascunho com foto sem foto_id")
    return draft.foto_id


def _components(
    payload: DesignPayload, draft: RascunhoVisual, background: GeneratedImage | None
) -> tuple[Componente, ...]:
    """Fundo ou foto, logotipo (exceto em `foto_inteira`) e texto (se houver titulo)."""
    if background is not None:
        parts = [Componente(tipo="gerada", ref=background.step_id, modelo=background.model_id)]
    else:
        photo_id = _photo_id(draft)
        parts = [Componente(tipo="fotografia", ref=photo_id, asset_id=photo_id)]
    if draft.layout != "foto_inteira":
        logo_id = payload.kit.logo_asset_id
        parts.append(Componente(tipo="logotipo", ref=logo_id, asset_id=logo_id))
    if draft.titulo is not None:
        parts.append(Componente(tipo="texto", ref=None))
    return tuple(parts)


class DesignerLoop:
    """Escolhe a imagem de um post: uma chamada estruturada, o fundo (se preciso) e a composicao."""

    def run(self, ctx: RunContext, payload: DesignPayload) -> DesignResult:
        """Chama o LLM, gera ou reutiliza o fundo so depois de o rascunho validar, e compoe."""
        draft = ctx.call_llm(
            ctx.spec.load_prompt(DESIGNER_DIR),
            _design_messages(payload),
            RascunhoVisual,
            extra_validator=_visual_validator(payload.photos),
        )
        background = _background(ctx, payload, draft)
        final = _compose(payload, draft, background)
        components = _components(payload, draft, background)
        return DesignResult(
            draft=draft,
            final=final,
            components=components,
            is_ai_generated=any(c.tipo == "gerada" for c in components),
            generated=background,
        )
