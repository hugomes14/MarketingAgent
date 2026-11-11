"""Recomposicao de fotografia sem chamadas LLM, na transacao do responsavel."""

from datetime import datetime
from typing import cast

from sqlalchemy.orm import Session

from marketing_agent.content.review_edit import CaptionEdit, build_edit_revision
from marketing_agent.content.review_errors import InvalidEdit
from marketing_agent.db.models import Post, PostRevision
from marketing_agent.images.layouts import LayoutInput, LayoutName, render_layout
from marketing_agent.media.brand_kit import BrandKitService
from marketing_agent.media.ingest import NormalizedImage
from marketing_agent.media.library import AssetNotFound, MediaLibrary


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
    """Compoe e cria uma revisao sem parecer; nao altera nem aprova a anterior."""
    if base.final_asset_id is None or base.layout not in (
        "foto_inteira",
        "foto_titulo",
        "fundo_ia_titulo",
    ):
        raise InvalidEdit("a revisao nao tem um layout recomponivel", ())
    if not 1 <= len(alt_text.strip()) <= 250:
        raise InvalidEdit("texto alternativo obrigatorio, ate 250 caracteres", ())
    photo = library.active_photo(post.brand_id, photo_id)
    if photo is None:
        raise AssetNotFound()
    if photo.mostra == "pessoas" and not photo.tem_autorizacao_pessoas:
        raise InvalidEdit("foto de pessoas sem autorizacao", ())
    title = next(
        (part.get("titulo") for part in base.components if part.get("tipo") == "texto"), None
    )
    layout = cast(LayoutName, "foto_titulo" if base.layout == "fundo_ia_titulo" else base.layout)
    if layout != "foto_inteira" and not isinstance(title, str):
        # Pacotes antigos nao guardavam o titulo: nunca inventar nem extrair por OCR.
        raise InvalidEdit("titulo original indisponivel; gere de novo o pacote", ())
    kit = kits.active(post.brand_id)
    if kit is None:
        raise InvalidEdit("falta o kit de marca", ())
    final = render_layout(
        LayoutInput(
            layout,
            library.read_asset(post.brand_id, photo_id).data,
            title if isinstance(title, str) else None,
            kit.cor_primaria,
            kit.cor_secundaria,
            kits.logo_png(post.brand_id, kit),
        )
    )
    asset = library.store_asset_in(
        session,
        post.brand_id,
        NormalizedImage(final.data, "image/jpeg", final.width, final.height, final.sha256),
        kind="final",
        origem="composta",
        actor=actor,
    )
    changes = CaptionEdit(
        legenda=base.caption,
        hashtags=base.hashtags,
        apelo_a_acao=base.call_to_action or "",
        alt_text=alt_text.strip(),
    )
    revision = build_edit_revision(post, base, changes, now, final.sha256)
    revision.final_asset_id, revision.layout = asset, layout
    revision.layout_version, revision.is_ai_generated = final.layout_version, False
    revision.components = [{"tipo": "fotografia", "ref": photo_id, "asset_id": photo_id}]
    if layout != "foto_inteira":
        revision.components.extend(
            [
                {"tipo": "logotipo", "ref": kit.logo_asset_id, "asset_id": kit.logo_asset_id},
                {"tipo": "texto", "ref": None, "titulo": title},
            ]
        )
    return revision
