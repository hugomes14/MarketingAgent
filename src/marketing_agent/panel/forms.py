"""Leitura de formularios `application/x-www-form-urlencoded` do painel.

Os formularios HTML e pedidos HTMX urlencoded continuam aqui; o multipart vive em `uploads`.
O resultado fica em cache no pedido: a dependencia de CSRF e a rota leem o mesmo corpo
uma so vez, com limite de tamanho.
"""

from collections.abc import Mapping
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request

FormFields = Mapping[str, str]

MAX_FORM_BYTES = 256 * 1024
MAX_FORM_FIELDS = 100
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
INVALID_REQUEST_MESSAGE = "Pedido inválido."
_CACHE_ATTRIBUTE = "panel_form_fields"


async def read_form(request: Request) -> FormFields:
    """Devolve os campos do formulario (primeiro valor de cada nome), lidos uma so vez.

    Levanta 413 se o corpo excede `MAX_FORM_BYTES`, 415 se o corpo nao e urlencoded e 400 se
    o corpo e invalido (bytes que nao sao UTF-8, campos a mais, ou NUL num valor).
    """
    cached: FormFields | None = getattr(request.state, _CACHE_ATTRIBUTE, None)
    if cached is not None:
        return cached
    body = await _read_limited_body(request)
    fields = _parse_fields(body, request.headers.get("content-type", "")) if body else {}
    setattr(request.state, _CACHE_ATTRIBUTE, fields)
    return fields


async def _read_limited_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_FORM_BYTES:
        raise HTTPException(status_code=413, detail=INVALID_REQUEST_MESSAGE)
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > MAX_FORM_BYTES:
            raise HTTPException(status_code=413, detail=INVALID_REQUEST_MESSAGE)
    return bytes(received)


def _parse_fields(body: bytes, content_type: str) -> dict[str, str]:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != FORM_CONTENT_TYPE:
        raise HTTPException(status_code=415, detail=INVALID_REQUEST_MESSAGE)
    try:
        pairs = parse_qsl(
            body.decode("utf-8"), keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS
        )
    except ValueError as error:  # UnicodeDecodeError e "demasiados campos" herdam de ValueError
        raise HTTPException(status_code=400, detail=INVALID_REQUEST_MESSAGE) from error
    fields: dict[str, str] = {}
    for name, value in pairs:
        if "\x00" in name or "\x00" in value:
            # O Postgres recusa NUL em texto; falhar aqui evita um erro 500 mais abaixo.
            raise HTTPException(status_code=400, detail=INVALID_REQUEST_MESSAGE)
        fields.setdefault(name, value)
    return fields
