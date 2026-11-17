"""Kit de marca: ver, guardar uma versao nova, verificar o contraste e pre-visualizar os layouts.

A pre-visualizacao usa o mesmo `render_layout` da producao (nenhum renderizador paralelo), com
o kit ativo, uma fotografia ativa da marca e um titulo fixo. So le: nao escreve na base, nao
audita e nunca chama o motor nem um fornecedor (o fundo do `fundo_ia_titulo` e um gradiente
local). O autor de cada versao e sempre o utilizador da sessao.
"""

import threading
from dataclasses import dataclass
from typing import Annotated, Literal, cast, get_args

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from marketing_agent.images.layouts import (
    LayoutInput,
    LayoutName,
    gradient_background,
    render_layout,
)
from marketing_agent.media.brand_kit import (
    INVALID_COLOR_MESSAGE,
    MIN_TITLE_CONTRAST,
    BrandKitInput,
    BrandKitView,
    KitError,
    contrast_ratio,
    display_ratio,
)
from marketing_agent.media.library import AssetNotFound, PhotoRef
from marketing_agent.panel.context import (
    MUTATION_GUARD,
    UUID_PATTERN,
    CurrentUser,
    Panel,
    PanelContext,
    see_other,
)
from marketing_agent.panel.forms import FormFields
from marketing_agent.panel.rendering import Context
from marketing_agent.panel.uploads import read_upload_form

router = APIRouter()
KIT_URL = "/kit"
PREVIEW_TITLE = "Exemplo de título da marca"
PREVIEW_BACKGROUND_SEED = b"previa"
MAX_HISTORY_VERSIONS = 20
MAX_SELECTOR_PHOTOS = 20
# Sugestao do formulario sem kit: texto claro sobre faixa escura (contraste bem acima de 4.5).
DEFAULT_PRIMARY = "#1F2937"
DEFAULT_SECONDARY = "#FFFFFF"
# `LayoutName` e um alias PEP 695: os valores estao em `__value__`, nao em `get_args(alias)`.
LAYOUT_NAMES: tuple[LayoutName, ...] = get_args(LayoutName.__value__)
_LAYOUT_LABELS: dict[LayoutName, str] = {
    "foto_inteira": "Foto inteira",
    "foto_titulo": "Foto com faixa e título",
    "fundo_ia_titulo": "Fundo gerado com título",
}
LayoutPath = Annotated[str, Path(pattern=f"^({'|'.join(LAYOUT_NAMES)})$")]
PhotoQuery = Annotated[str | None, Query(pattern=UUID_PATTERN)]
# Cada render decodifica uma foto de ate 4096 px; a pagina pede os tres layouts de uma vez.
# Dois de cada vez limitam o pico de memoria sem serializar a pagina por completo.
_RENDER_SLOTS = threading.BoundedSemaphore(2)


@dataclass(frozen=True)
class ContrastCheck:
    """Resultado da verificacao de contraste, pronto para o fragmento `_contrast.html`."""

    state: Literal["empty", "invalid", "ok"]
    ratio: str
    complies: bool
    threshold: float


@dataclass(frozen=True)
class KitRow:
    """Versao do kit com a razao de contraste ja formatada (truncada) para a pagina."""

    kit: BrandKitView
    ratio: str


def check_contrast(primary: str | None, secondary: str | None) -> ContrastCheck:
    """Razao WCAG das duas cores; cores em falta ou invalidas dao `invalid`, sem erro."""
    try:
        ratio = contrast_ratio(primary or "", secondary or "")
    except KitError:
        return ContrastCheck("invalid", "", False, MIN_TITLE_CONTRAST)
    return ContrastCheck(
        "ok", display_ratio(ratio), ratio >= MIN_TITLE_CONTRAST, MIN_TITLE_CONTRAST
    )


@router.get("/kit", response_class=HTMLResponse)
def show_kit(user: CurrentUser, panel: Panel, foto: PhotoQuery = None) -> HTMLResponse:
    """Kit ativo, historico limitado, formulario, contraste e as tres pre-visualizacoes.

    `?foto=` escolhe a fotografia das pre-visualizacoes; uma que nao seja ativa e da marca da 404.
    """
    media, brand_id = panel.media, panel.brand_id
    kit = media.kits.active(brand_id)
    options = media.library.photo_refs(brand_id, MAX_SELECTOR_PHOTOS)
    chosen = _require_photo(panel, foto) if foto is not None else None
    if chosen is not None and chosen.id not in options:
        options = {chosen.id: chosen, **options}
    primary = kit.cor_primaria if kit else DEFAULT_PRIMARY
    secondary = kit.cor_secundaria if kit else DEFAULT_SECONDARY
    context: Context = {
        "kit": kit,
        "history": [
            KitRow(item, display_ratio(item.contrast))
            for item in media.kits.history(brand_id, MAX_HISTORY_VERSIONS)
        ],
        "photos": list(options.values()),
        "selected": chosen.id if chosen else None,
        "can_preview": kit is not None and bool(options),
        "layouts": [(name, _LAYOUT_LABELS[name]) for name in LAYOUT_NAMES],
        "primary": primary,
        "secondary": secondary,
        "contrast": check_contrast(primary, secondary),
    }
    return panel.renderer.page("kit.html", user, context)


@router.post("/kit", dependencies=[MUTATION_GUARD])
async def save_kit(request: Request, panel: Panel, user: CurrentUser) -> RedirectResponse:
    """Le o multipart so depois da autenticacao e do CSRF e acrescenta uma versao do kit.

    O logotipo e opcional (o browser envia uma parte vazia quando nao se escolhe ficheiro); a
    primeira versao exige-o. Contraste ou cor invalidos dao 422 sem escrever nada.
    """
    form = await read_upload_form(request)
    kit_input = _kit_input(form.fields)
    logo = form.file.data if form.file is not None and form.file.data else None
    # Normalizacao do logotipo, disco e SQL sao sincronos; nao bloquear o event loop.
    await run_in_threadpool(panel.media.kits.save, panel.brand_id, kit_input, logo, user.login)
    return see_other(KIT_URL)


@router.get("/kit/contraste", response_class=HTMLResponse)
def contrast_fragment(
    user: CurrentUser,
    panel: Panel,
    cor_primaria: str | None = None,
    cor_secundaria: str | None = None,
) -> HTMLResponse:
    """Fragmento HTMX com a razao e "cumpre"/"não cumpre"; nao toca na base nem muda estado.

    Cores invalidas ou a meio da escrita dao 200 com um aviso no proprio alvo: nao e um erro
    do pedido, e o estado normal de quem ainda esta a escrever o hexadecimal.
    """
    check = check_contrast(cor_primaria, cor_secundaria)
    return panel.renderer.partial("_contrast.html", {"contrast": check})


@router.get("/kit/previa/{layout}")
def preview_layout(
    layout: LayoutPath, user: CurrentUser, panel: Panel, foto: PhotoQuery = None
) -> Response:
    """JPEG 1080x1350 do layout com o kit ativo; 404 sem kit, sem foto ou com foto alheia.

    E uma rota `def`: o render (CPU) corre no pool de threads e nunca no event loop.
    """
    name = cast(LayoutName, layout)
    spec = _preview_spec(panel, name, foto)
    with _RENDER_SLOTS:
        rendered = render_layout(spec)
    return Response(
        rendered.data,
        media_type="image/jpeg",
        # O kit e as fotos mudam: nunca reutilizar uma previa antiga.
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


def _preview_spec(panel: PanelContext, layout: LayoutName, foto: str | None) -> LayoutInput:
    media, brand_id = panel.media, panel.brand_id
    kit = media.kits.active(brand_id)
    photo = _require_photo(panel, foto) if foto is not None else _latest_photo(panel)
    if kit is None or photo is None:
        raise AssetNotFound()
    if layout == "fundo_ia_titulo":
        base = gradient_background(PREVIEW_BACKGROUND_SEED)
    else:
        base = media.library.read_asset(brand_id, photo.id).data
    return LayoutInput(
        layout=layout,
        base_image=base,
        titulo=PREVIEW_TITLE,
        cor_primaria=kit.cor_primaria,
        cor_secundaria=kit.cor_secundaria,
        logo_png=media.kits.logo_png(brand_id, kit),
    )


def _require_photo(panel: PanelContext, photo_id: str) -> PhotoRef:
    # Inexistente, desativada, de outra marca ou de outro tipo: a mesma resposta (sem fuga).
    photo = panel.media.library.active_photo(panel.brand_id, photo_id)
    if photo is None:
        raise AssetNotFound()
    return photo


def _latest_photo(panel: PanelContext) -> PhotoRef | None:
    return next(iter(panel.media.library.photo_refs(panel.brand_id, 1).values()), None)


def _kit_input(fields: FormFields) -> BrandKitInput:
    """Valida as duas cores com as regras do servico; so estes campos entram no kit."""
    try:
        return BrandKitInput.model_validate(
            {
                "cor_primaria": fields.get("cor_primaria"),
                "cor_secundaria": fields.get("cor_secundaria"),
            }
        )
    except ValidationError as error:
        raise _kit_error(error) from error


def _kit_error(error: ValidationError) -> KitError:
    # Contraste insuficiente chega como KitError dentro do ValidationError (mensagem do
    # servico, com a razao truncada). Qualquer outra falha e uma cor mal formada ou em falta;
    # o valor submetido nunca vai para a mensagem.
    for detail in error.errors():
        cause = detail.get("ctx", {}).get("error")
        if isinstance(cause, KitError):
            return cause
    return KitError(INVALID_COLOR_MESSAGE)
