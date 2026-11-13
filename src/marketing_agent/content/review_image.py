"""Recomposicao de fotografia sem chamadas LLM, na transacao do responsavel."""

from datetime import datetime
from typing import Final, cast

from sqlalchemy.orm import Session

from marketing_agent.content.review_edit import CaptionEdit, build_edit_revision
from marketing_agent.content.review_errors import InvalidEdit
from marketing_agent.db.models import JsonObject, Post, PostRevision
from marketing_agent.images.layouts import LayoutInput, LayoutName, RenderedImage, render_layout
from marketing_agent.media.brand_kit import BrandKitService, BrandKitView
from marketing_agent.media.ingest import NormalizedImage
from marketing_agent.media.library import AssetNotFound, MediaLibrary

MAX_ALT_CHARS: Final = 250
UNCHANGED_SWAP_MESSAGE: Final = "troca sem alteracoes"
_RECOMPOSABLE: Final = frozenset({"foto_inteira", "foto_titulo", "fundo_ia_titulo"})


def _recomposable_layout(base: PostRevision) -> LayoutName:
    """Layout da revisao nova (uma foto substitui o fundo gerado); recusa o que nao recompoe."""
    if base.final_asset_id is None or base.layout not in _RECOMPOSABLE:
        raise InvalidEdit("a revisao nao tem um layout recomponivel", ())
    return cast(LayoutName, "foto_titulo" if base.layout == "fundo_ia_titulo" else base.layout)


def _clean_alt_text(alt_text: str) -> str:
    """Texto alternativo sem espacos nas pontas, com 1..`MAX_ALT_CHARS` caracteres."""
    cleaned = alt_text.strip()
    if not 1 <= len(cleaned) <= MAX_ALT_CHARS:
        raise InvalidEdit("texto alternativo obrigatorio, ate 250 caracteres", ())
    return cleaned


def _require_usable_photo(library: MediaLibrary, brand_id: str, photo_id: str) -> None:
    """A foto tem de ser ativa da marca e, se mostra pessoas, ter autorizacao."""
    photo = library.active_photo(brand_id, photo_id)
    if photo is None:
        raise AssetNotFound()
    if photo.mostra == "pessoas" and not photo.tem_autorizacao_pessoas:
        raise InvalidEdit("foto de pessoas sem autorizacao", ())


def _original_title(base: PostRevision, layout: LayoutName) -> str | None:
    """Titulo da revisao base; `foto_inteira` nao o usa e pacotes antigos podem nao o ter."""
    title = next(
        (part.get("titulo") for part in base.components if part.get("tipo") == "texto"), None
    )
    if layout != "foto_inteira" and not isinstance(title, str):
        # Pacotes antigos nao guardavam o titulo: nunca inventar nem extrair por OCR.
        raise InvalidEdit("titulo original indisponivel; gere de novo o pacote", ())
    return title if isinstance(title, str) else None


def _render(
    post: Post,
    layout: LayoutName,
    title: str | None,
    photo_id: str,
    library: MediaLibrary,
    kits: BrandKitService,
) -> tuple[RenderedImage, BrandKitView]:
    """Compoe a fotografia com o kit ativo; `LayoutError` e `ImageRejected` sobem ao chamador."""
    kit = kits.active(post.brand_id)
    if kit is None:
        raise InvalidEdit("falta o kit de marca", ())
    final = render_layout(
        LayoutInput(
            layout,
            library.read_asset(post.brand_id, photo_id).data,
            title,
            kit.cor_primaria,
            kit.cor_secundaria,
            kits.logo_png(post.brand_id, kit),
        )
    )
    return final, kit


def _photo_components(
    layout: LayoutName, photo_id: str, kit: BrandKitView, title: str | None
) -> list[JsonObject]:
    """Proveniencia da imagem recomposta: foto e, fora de `foto_inteira`, logotipo e titulo."""
    components: list[JsonObject] = [{"tipo": "fotografia", "ref": photo_id, "asset_id": photo_id}]
    if layout != "foto_inteira":
        components.append(
            {"tipo": "logotipo", "ref": kit.logo_asset_id, "asset_id": kit.logo_asset_id}
        )
        components.append({"tipo": "texto", "ref": None, "titulo": title})
    return components


def _swap_revision(
    post: Post, base: PostRevision, alt: str, final: RenderedImage, now: datetime
) -> PostRevision:
    """Revisao da troca (mesmo texto, nova imagem); `InvalidEdit` se o hash nao muda."""
    changes = CaptionEdit(
        legenda=base.caption,
        hashtags=base.hashtags,
        apelo_a_acao=base.call_to_action or "",
        alt_text=alt,
    )
    revision = build_edit_revision(post, base, changes, now, final.sha256)
    if revision.revision_hash == base.revision_hash:
        raise InvalidEdit(UNCHANGED_SWAP_MESSAGE, ())
    return revision


def recompose_photo(
    session: Session,
    post: Post,
    base: PostRevision,
    photo_id: str,
    alt_text: str,
    actor: str,
    now: datetime,
    library: MediaLibrary,
    kits: BrandKitService,
) -> PostRevision:
    """Compoe e cria uma revisao sem parecer; nao altera nem aprova a anterior.

    `InvalidEdit` se a troca nao muda nada (mesma imagem e mesmo texto alternativo: o hash
    seria igual e limparia o parecer e a aprovacao sem motivo). Isso verifica-se ANTES de
    guardar o ativo, para uma troca recusada nao deixar ficheiros nem linhas novas.
    """
    layout = _recomposable_layout(base)
    alt = _clean_alt_text(alt_text)
    _require_usable_photo(library, post.brand_id, photo_id)
    title = _original_title(base, layout)
    final, kit = _render(post, layout, title, photo_id, library, kits)
    revision = _swap_revision(post, base, alt, final, now)
    revision.final_asset_id = library.store_asset_in(
        session,
        post.brand_id,
        NormalizedImage(final.data, "image/jpeg", final.width, final.height, final.sha256),
        kind="final",
        origem="composta",
        actor=actor,
    )
    revision.layout, revision.layout_version = layout, final.layout_version
    revision.is_ai_generated = False
    revision.components = _photo_components(layout, photo_id, kit, title)
    return revision
