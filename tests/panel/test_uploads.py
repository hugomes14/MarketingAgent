"""Fronteira multipart: limites durante receive e fecho dos temporarios reais."""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from tempfile import SpooledTemporaryFile
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from panel_rig import PanelRig
from starlette.types import Message, Scope

from marketing_agent.panel.uploads import (
    MAX_FIELD_BYTES,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_FIELDS,
    MULTIPART_OVERHEAD_BYTES,
    read_upload_form,
)

MakePanel = Callable[..., PanelRig]
CAP = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES


def multipart(fields: list[tuple[str, str]], files: int = 1, size: int = 5) -> httpx.Request:
    """Pedido codificado pelo cliente HTTP, sem depender do parser do servidor."""
    parts = [(name, (None, value)) for name, value in fields]
    parts.extend(("ficheiro", ("foto.jpg", b"x" * size, "image/jpeg")) for _ in range(files))
    request = httpx.Request("POST", "http://testserver/biblioteca", files=parts)
    request.read()
    return request


def app() -> FastAPI:
    application = FastAPI()

    @application.post("/biblioteca")
    async def upload(request: Request) -> dict[str, str | int | None]:
        first = await read_upload_form(request)
        assert first is await read_upload_form(request)
        return {
            "filename": first.file.filename if first.file else None,
            "size": len(first.file.data) if first.file else 0,
            **first.fields,
        }

    return application


def call_stream(
    application: FastAPI, chunks: list[bytes], headers: dict[str, str]
) -> tuple[int, int]:
    """Entrega mensagens ASGI distintas e mede quantas o servidor consumiu."""
    received = 0
    sent: list[Message] = []
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/biblioteca",
        "raw_path": b"/biblioteca",
        "query_string": b"",
        "root_path": "",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 5000),
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
    }

    async def receive() -> Message:
        nonlocal received
        assert received < len(chunks), "A aplicacao leu depois do fim do corpo"
        chunk = chunks[received]
        received += 1
        return {"type": "http.request", "body": chunk, "more_body": received < len(chunks)}

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(application(scope, receive, send))
    return sent[0]["status"], received


@pytest.fixture
def spools() -> Iterator[list[SpooledTemporaryFile[bytes]]]:
    opened: list[SpooledTemporaryFile[bytes]] = []

    with ExitStack() as cleanup:

        def tracked(max_size: int) -> SpooledTemporaryFile[bytes]:
            stream = cleanup.enter_context(SpooledTemporaryFile(max_size=max_size))
            opened.append(stream)
            return stream

        with patch("starlette.formparsers.SpooledTemporaryFile", tracked):
            yield opened
        # Verificar antes de o ExitStack limpar eventual falha do codigo testado.
        assert all(stream.closed for stream in opened), "Temporario multipart ficou aberto"


def test_upload_is_cached_and_file_closed(spools: list[SpooledTemporaryFile[bytes]]) -> None:
    request = multipart([("descricao", "Olá")])
    response = TestClient(app()).post(
        "/biblioteca", content=request.content, headers=request.headers
    )
    assert response.status_code == 200
    assert response.json() == {"filename": "foto.jpg", "size": 5, "descricao": "Olá"}
    assert len(spools) == 1 and spools[0].closed


@pytest.mark.parametrize("declared", [None, "1"])
def test_upload_over_15mb_rejected_while_streaming(
    make_panel: MakePanel, spools: list[SpooledTemporaryFile[bytes]], declared: str | None
) -> None:
    rig = make_panel([])
    prefix = (
        b'--cut\r\nContent-Disposition: form-data; name="ficheiro"; filename="foto.jpg"\r\n'
        b"Content-Type: image/jpeg\r\n\r\n"
    )
    chunks = [prefix, *([b"x" * (1024 * 1024)] * 20), b"\r\n--cut--\r\n"]
    headers = rig.headers(
        {"host": "testserver", "content-type": "multipart/form-data; boundary=cut"}
    )
    if declared is not None:
        headers["content-length"] = declared
    status, consumed = call_stream(rig.app, chunks, headers)
    # Sem token no cabecalho: o proprio CSRF tem de preservar o erro de tamanho.
    assert status == 413
    assert 2 < consumed < len(chunks)
    assert len(spools) == 1 and spools[0].closed
    assert spools[0]._rolled  # O limite foi atingido ja com um temporario real no disco.


def test_declared_overflow_rejected_before_receive() -> None:
    status, consumed = call_stream(
        app(),
        [b"never"],
        {
            "content-type": "multipart/form-data; boundary=cut",
            "content-length": str(CAP + 1),
        },
    )
    assert (status, consumed) == (413, 0)


def test_file_limit_independent_of_multipart_overhead(
    spools: list[SpooledTemporaryFile[bytes]],
) -> None:
    request = multipart([], size=MAX_UPLOAD_BYTES + 1)
    assert len(request.content) < CAP
    response = TestClient(app()).post(
        "/biblioteca", content=request.content, headers=request.headers
    )
    assert response.status_code == 413
    assert spools and all(stream.closed for stream in spools)


@pytest.mark.parametrize(
    "fields,files",
    [
        ([("descricao", "x" * (MAX_FIELD_BYTES + 1))], 1),
        ([(f"campo{i}", "x") for i in range(MAX_UPLOAD_FIELDS + 1)], 1),
        ([("descricao", "a\x00b")], 1),
        ([("descricao", "a"), ("descricao", "b")], 1),
        ([], 2),
    ],
)
def test_invalid_parts_are_400_and_close_files(
    fields: list[tuple[str, str]],
    files: int,
    spools: list[SpooledTemporaryFile[bytes]],
) -> None:
    request = multipart(fields, files)
    response = TestClient(app()).post(
        "/biblioteca", content=request.content, headers=request.headers
    )
    assert response.status_code == 400


def test_non_multipart_is_415() -> None:
    assert TestClient(app()).post("/biblioteca", data={"a": "b"}).status_code == 415


@pytest.mark.parametrize("body", [b"not multipart", b"--cut\r\ninvalid header\r\n\r\nx"])
def test_malformed_multipart_is_400(body: bytes) -> None:
    response = TestClient(app()).post(
        "/biblioteca",
        content=body,
        headers={
            "Content-Type": "multipart/form-data; boundary=cut",
        },
    )
    assert response.status_code == 400


def test_nul_field_name_on_wire_is_400(spools: list[SpooledTemporaryFile[bytes]]) -> None:
    request = multipart([("descricao", "x")])
    response = TestClient(app()).post(
        "/biblioteca",
        content=request.content.replace(b'name="descricao"', b'name="des\x00cricao"'),
        headers={"content-type": request.headers["content-type"]},
    )
    assert response.status_code == 400


def test_malformed_after_opening_file_closes_spool(
    spools: list[SpooledTemporaryFile[bytes]],
) -> None:
    body = (
        b'--cut\r\nContent-Disposition: form-data; name="ficheiro"; filename="f.jpg"\r\n\r\n'
        b"x\r\n--cut\r\nInvalid header\r\n\r\nx\r\n--cut--\r\n"
    )
    response = TestClient(app()).post(
        "/biblioteca", content=body, headers={"content-type": "multipart/form-data; boundary=cut"}
    )
    assert response.status_code == 400
    assert len(spools) == 1 and spools[0].closed


@pytest.mark.parametrize(
    "headers,expected", [({}, 401), ({"Tailscale-User-Login": "intruso"}, 403)]
)
def test_denied_upload_does_not_receive_body(
    make_panel: MakePanel,
    headers: dict[str, str],
    expected: int,
) -> None:
    rig = make_panel([])
    status, consumed = call_stream(
        rig.app,
        [b"unread"],
        {
            "host": "testserver",
            "content-type": "multipart/form-data; boundary=cut",
            **headers,
        },
    )
    assert (status, consumed) == (expected, 0)


def test_exact_file_limit_is_accepted(spools: list[SpooledTemporaryFile[bytes]]) -> None:
    request = multipart([], size=MAX_UPLOAD_BYTES)
    response = TestClient(app()).post(
        "/biblioteca", content=request.content, headers=request.headers
    )
    assert response.status_code == 200 and response.json()["size"] == MAX_UPLOAD_BYTES


def test_truncated_file_closes_spool(spools: list[SpooledTemporaryFile[bytes]]) -> None:
    body = (
        b'--cut\r\nContent-Disposition: form-data; name="ficheiro"; filename="f.jpg"\r\n\r\n'
        + b"x" * (2 * 1024 * 1024)
    )
    status, consumed = call_stream(
        app(),
        [body, b"truncated ending"],
        {
            "content-type": "multipart/form-data; boundary=cut",
        },
    )
    assert status == 400
    assert len(spools) == 1 and spools[0].closed and spools[0]._rolled


@pytest.mark.parametrize("suffix", [b"", b"\r\n"])
@pytest.mark.parametrize("empty", [False, True])
def test_terminal_boundary_with_optional_crlf(suffix: bytes, empty: bool) -> None:
    body = (
        b"--cut--"
        if empty
        else b'--cut\r\nContent-Disposition: form-data; name="a"\r\n\r\nb\r\n--cut--'
    )
    response = TestClient(app()).post(
        "/biblioteca",
        content=body + suffix,
        headers={"content-type": "multipart/form-data; boundary=cut"},
    )
    assert response.status_code == 200
    assert response.json()["filename"] is None


@pytest.mark.parametrize("ending", [b"--cut--", b"\r\n--cut--\r\nepilogue"])
def test_missing_crlf_or_epilogue_is_rejected(ending: bytes) -> None:
    body = b'--cut\r\nContent-Disposition: form-data; name="a"\r\n\r\nb' + ending
    response = TestClient(app()).post(
        "/biblioteca", content=body, headers={"content-type": "multipart/form-data; boundary=cut"}
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "content_type",
    [
        "multipart/form-data",
        "multipart/form-data; boundary=",
        'multipart/form-data; boundary="bad "',
        'multipart/form-data; boundary="bad@boundary"',
        "multipart/form-data; boundary=" + "x" * 71,
    ],
)
def test_invalid_boundary_is_rejected_before_receive(content_type: str) -> None:
    assert call_stream(app(), [b"unread"], {"content-type": content_type}) == (400, 0)
