"""Biblioteca de fotografias e acesso autenticado aos bytes imutaveis da marca."""

from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from marketing_agent.db.models import PHOTO_SUBJECTS
from marketing_agent.media.library import MediaError, PhotoSubject, PhotoUpload
from marketing_agent.panel.context import MUTATION_GUARD, CurrentUser, Panel, RecordId, see_other
from marketing_agent.panel.forms import FormFields
from marketing_agent.panel.uploads import read_upload_form

router = APIRouter()
_SUBJECT_LABELS = {
    "produto": "Produto",
    "instalacao": "Instalação",
    "pessoas": "Pessoas",
    "ambiente": "Ambiente",
    "outro": "Outro",
}


@router.get("/biblioteca", response_class=HTMLResponse)
def library(panel: Panel, user: CurrentUser, mostra: str | None = None) -> HTMLResponse:
    """Lista apenas fotos ativas da marca, com filtro de assunto validado."""
    if mostra is not None and mostra not in PHOTO_SUBJECTS:
        raise MediaError()
    photos = panel.media.library.list_photos(panel.brand_id, cast(PhotoSubject | None, mostra))
    return panel.renderer.page(
        "library.html",
        user,
        {
            "photos": photos,
            "subjects": _SUBJECT_LABELS,
            "selected": mostra,
        },
    )


@router.post("/biblioteca", dependencies=[MUTATION_GUARD])
async def upload_photo(request: Request, panel: Panel, user: CurrentUser) -> RedirectResponse:
    """Le o multipart so depois da autenticacao e do CSRF e guarda a foto normalizada."""
    form = await read_upload_form(request)
    if form.file is None:
        raise MediaError()
    meta = _metadata(form.fields)
    # Descodificacao, disco e SQL sao sincronos; nao bloquear o event loop do painel.
    await run_in_threadpool(
        panel.media.library.upload_photo, panel.brand_id, form.file.data, meta, user.login
    )
    return see_other("/biblioteca")


@router.post("/biblioteca/{asset_id}/desativar", dependencies=[MUTATION_GUARD])
def deactivate_photo(asset_id: RecordId, panel: Panel, user: CurrentUser) -> RedirectResponse:
    """Retira a foto das escolhas futuras, preservando revisoes antigas."""
    panel.media.library.deactivate(panel.brand_id, asset_id, user.login)
    return see_other("/biblioteca")


@router.get("/media/{asset_id}")
def get_media(asset_id: RecordId, panel: Panel, user: CurrentUser) -> Response:
    """Serve so media da marca e com MIME validado, nunca o tipo declarado no upload."""
    asset = panel.media.library.read_asset(panel.brand_id, asset_id)
    if asset.mime not in ("image/jpeg", "image/png"):
        raise MediaError()
    return Response(
        asset.data,
        media_type=asset.mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private",
            "Content-Disposition": "inline",
        },
    )


def _metadata(fields: FormFields) -> PhotoUpload:
    try:
        return PhotoUpload.model_validate(
            {
                "mostra": fields.get("mostra"),
                "descricao": fields.get("descricao"),
                "origem": fields.get("origem"),
                "licenca_nota": fields.get("licenca_nota") or None,
                "tem_autorizacao_pessoas": fields.get("tem_autorizacao_pessoas", "false"),
            }
        )
    except ValidationError as error:
        raise MediaError() from error
