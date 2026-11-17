"""Rascunho visual do Designer e a sua validacao em codigo (regras D30 da spec 2B, secao 6).

As violacoes de `validate_visual` voltam ao modelo como `extra_validator` do motor (contam como
`invalid_output`) e podem chegar ao `last_error` e a auditoria. Por isso cada mensagem e curta,
cita o campo, o layout (enum fixo) e os numeros dos limites, e NUNCA o valor que o modelo
escreveu (titulo, tema, id de foto).
"""

from collections.abc import Mapping
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from marketing_agent.images.layouts import MAX_TITLE_CHARS, MAX_TITLE_LINES, LayoutName, fit_title
from marketing_agent.images.prompts import MAX_THEME_CHARS, Estilo, InvalidTheme, neutralize_theme
from marketing_agent.media.library import PhotoRef

MAX_ALT_TEXT_CHARS = 250
MAX_JUSTIFICATION_CHARS = 600
_PHOTO_LAYOUTS = ("foto_inteira", "foto_titulo")
_TITLE_LAYOUTS = ("foto_titulo", "fundo_ia_titulo")


class RascunhoVisual(BaseModel):
    """Escolha visual de uma publicacao.

    Os campos que dependem do layout (`foto_id`, `titulo`, `tema_fundo`, `estilo`) sao
    obrigatorios mas anulaveis, como `Alegacao.kb_ref`: o esquema JSON enviado ao modelo
    obriga-o a decidir cada um. As regras por layout verificam-se em `validate_visual`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    layout: LayoutName
    foto_id: str | None
    titulo: Annotated[str, Field(min_length=1, max_length=MAX_TITLE_CHARS)] | None
    tema_fundo: Annotated[str, Field(min_length=1, max_length=MAX_THEME_CHARS)] | None
    estilo: Estilo | None
    alt_text: str = Field(min_length=1, max_length=MAX_ALT_TEXT_CHARS)
    justificacao: str = Field(min_length=1, max_length=MAX_JUSTIFICATION_CHARS)


def validate_visual(draft: RascunhoVisual, *, photos: Mapping[str, PhotoRef]) -> list[str]:
    """Devolve as violacoes do rascunho (lista vazia = valido); puro e determinista.

    `photos` mapeia id -> foto e deve conter so as fotos ATIVAS da marca: aqui so se testa a
    pertenca e a autorizacao de pessoas. Uma foto que mostra pessoas sem autorizacao e recusada.
    """
    return [
        *_check_photo(draft, photos),
        *_check_background(draft),
        *_check_title(draft),
        *_check_alt_text(draft),
    ]


def _check_photo(draft: RascunhoVisual, photos: Mapping[str, PhotoRef]) -> list[str]:
    if draft.layout not in _PHOTO_LAYOUTS:
        return ["foto_id nao se usa em fundo_ia_titulo"] if draft.foto_id is not None else []
    if draft.foto_id is None:
        return [f"foto_id obrigatorio em {draft.layout}"]
    photo = photos.get(draft.foto_id)
    if photo is None:
        return ["foto_id desconhecido, inativo ou de outra marca"]
    if photo.mostra == "pessoas" and not photo.tem_autorizacao_pessoas:
        return ["foto_id mostra pessoas sem autorizacao"]
    return []


def _check_background(draft: RascunhoVisual) -> list[str]:
    if draft.layout in _PHOTO_LAYOUTS:
        return [
            f"{name} nao se usa em {draft.layout}"
            for name, value in (("tema_fundo", draft.tema_fundo), ("estilo", draft.estilo))
            if value is not None
        ]
    violations = [] if draft.estilo is not None else ["estilo obrigatorio em fundo_ia_titulo"]
    if draft.tema_fundo is None:
        return ["tema_fundo obrigatorio em fundo_ia_titulo", *violations]
    try:
        neutralize_theme(draft.tema_fundo)
    except InvalidTheme as error:
        # Mensagem fixa por motivo: `InvalidTheme` nunca repete o tema.
        violations.insert(0, str(error))
    return violations


def _check_title(draft: RascunhoVisual) -> list[str]:
    if draft.layout not in _TITLE_LAYOUTS:
        return ["foto_inteira nao leva titulo"] if draft.titulo is not None else []
    if draft.titulo is None:
        return [f"titulo obrigatorio em {draft.layout}"]
    # `fit_title` levanta para `foto_inteira`, que e tratado acima: nunca lhe chega.
    if fit_title(draft.titulo, draft.layout) is None:
        return [
            f"titulo nao cabe em {draft.layout} "
            f"(ate {MAX_TITLE_CHARS} caracteres, {MAX_TITLE_LINES} linhas)"
        ]
    return []


def _check_alt_text(draft: RascunhoVisual) -> list[str]:
    if not draft.alt_text.strip():
        return ["alt_text nao pode estar vazio"]
    if draft.alt_text != draft.alt_text.strip():
        return ["alt_text nao pode ter espacos nas pontas"]
    return []
