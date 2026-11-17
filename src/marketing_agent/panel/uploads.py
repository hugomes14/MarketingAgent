"""Multipart limitado durante a rececao; cache so de bytes e campos, nunca de temporarios."""

from dataclasses import dataclass
from types import MappingProxyType

from fastapi import HTTPException, Request
from python_multipart.multipart import parse_options_header
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.types import Message

from marketing_agent.panel.forms import INVALID_REQUEST_MESSAGE, FormFields

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_UPLOAD_FIELDS = 20
MAX_FIELD_BYTES = 8 * 1024
MULTIPART_CONTENT_TYPE = "multipart/form-data"
# Nomes aceites para o unico ficheiro: `ficheiro` (fotos) e `logotipo` (kit de marca). O
# recetor so devolve bytes; cada rota decide o que o ficheiro significa. Qualquer outro e 400.
FILE_FIELD_NAMES = frozenset({"ficheiro", "logotipo"})
_CACHE_ATTRIBUTE = "panel_upload_form"
_MAX_BODY_BYTES = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES


@dataclass(frozen=True)
class UploadedFile:
    """Copia limitada de um ficheiro; filename e dado nao confiavel, nunca um caminho."""

    filename: str | None
    field_name: str
    data: bytes


@dataclass(frozen=True)
class UploadForm:
    """Campos e ficheiro ja desligados dos temporarios do parser."""

    fields: FormFields
    file: UploadedFile | None


async def read_upload_form(request: Request) -> UploadForm:
    """Le uma vez: 415 para outro tipo, 413 para excesso e 400 para multipart invalido."""
    cached: UploadForm | None = getattr(request.state, _CACHE_ATTRIBUTE, None)
    if cached is not None:
        return cached
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != MULTIPART_CONTENT_TYPE:
        raise _invalid(415)
    _check_declared_length(request)
    result = await _parse_upload(request)
    setattr(request.state, _CACHE_ATTRIBUTE, result)
    return result


def _check_declared_length(request: Request) -> None:
    declared = request.headers.get("content-length")
    if declared is None:
        return
    if not declared.isascii() or not declared.isdecimal():
        raise _invalid(400)
    significant = declared.lstrip("0") or "0"
    if len(significant) > len(str(_MAX_BODY_BYTES)) or int(significant) > _MAX_BODY_BYTES:
        raise _invalid(413)


async def _parse_upload(request: Request) -> UploadForm:
    received = 0
    overflow = False
    closing = _closing_delimiter(request)
    tail = b""

    async def limited_receive() -> Message:
        nonlocal received, overflow, tail
        message = await request.receive()
        if message["type"] == "http.request":
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > _MAX_BODY_BYTES:
                overflow = True
                # MultiPartException aciona o fecho dos spools mesmo a meio de um ficheiro.
                raise MultiPartException(INVALID_REQUEST_MESSAGE)
            tail = (tail + chunk)[-len(closing) - 2 :]
            if not message.get("more_body", False):
                _check_ending(tail, closing, received)
        return message

    limited = Request(request.scope, receive=limited_receive)
    try:
        async with limited.form(
            max_files=1, max_fields=MAX_UPLOAD_FIELDS, max_part_size=MAX_FIELD_BYTES
        ) as form:
            return await _copy_form(form)
    except (MultiPartException, StarletteHTTPException) as error:
        # Request.form converte MultiPartException em HTTP400 se scope tiver uma app.
        # O sinal local preserva 413 sem expor mensagens internas do parser.
        if overflow:
            raise _invalid(413) from error
        if isinstance(error, StarletteHTTPException) and error.status_code == 413:
            raise
        raise _invalid(400) from error


def _closing_delimiter(request: Request) -> bytes:
    _, params = parse_options_header(request.headers["content-type"])
    boundary = params.get(b"boundary", b"")
    allowed = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'()+_,-./:=? "
    if not 1 <= len(boundary) <= 70 or boundary.endswith(b" "):
        raise _invalid(400)
    if any(byte not in allowed for byte in boundary):
        raise _invalid(400)
    return b"\r\n--" + boundary + b"--"


def _check_ending(tail: bytes, closing: bytes, received: int) -> None:
    # python-multipart.finalize nao valida EOF: um ficheiro parcial fica fora do FormData
    # e de form.close. Recusar antes do EOF aciona o fecho de TODOS os spools no parser.
    # Formularios do browser nao usam epilogo: aceita-se so delimitador final e CRLF opcional.
    ending = tail[:-2] if tail.endswith(b"\r\n") else tail
    is_empty_form = received <= len(closing) and ending == closing[2:]
    if not ending.endswith(closing) and not is_empty_form:
        raise MultiPartException(INVALID_REQUEST_MESSAGE)


async def _copy_form(form: FormData) -> UploadForm:
    fields: dict[str, str] = {}
    uploaded = None
    names: set[str] = set()
    for name, value in form.multi_items():
        if name in names or "\x00" in name or len(name.encode()) > MAX_FIELD_BYTES:
            raise _invalid(400)
        names.add(name)
        if isinstance(value, UploadFile):
            if name not in FILE_FIELD_NAMES or (value.filename and "\x00" in value.filename):
                raise _invalid(400)
            data = await value.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise _invalid(413)
            uploaded = UploadedFile(value.filename, name, data)
        else:
            if "\x00" in value:
                raise _invalid(400)
            fields[name] = value
    return UploadForm(MappingProxyType(fields), uploaded)


def _invalid(status: int) -> HTTPException:
    return HTTPException(status_code=status, detail=INVALID_REQUEST_MESSAGE)
