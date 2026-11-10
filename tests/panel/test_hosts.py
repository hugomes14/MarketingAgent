"""Filtro de `Host` (DNS rebinding): 400 antes da autenticacao, da rota e do CSRF."""

import asyncio
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from panel_rig import LOGIN, PanelRig, build_settings
from starlette.types import Message, Scope

from marketing_agent.panel.app import create_app
from marketing_agent.panel.hosts import (
    AllowedHostsMiddleware,
    normalize_allowed_hosts,
    parse_host_header,
)
from marketing_agent.panel.security import CONTENT_SECURITY_POLICY

MakePanel = Callable[..., PanelRig]
ALLOWED = ["127.0.0.1", "localhost", "painel.tail1234.ts.net", "::1"]


def call(
    headers: list[bytes], *, kind: str = "http", method: str = "GET"
) -> tuple[list[Message], int]:
    """Corre o middleware sobre uma app que conta chamadas; devolve (mensagens, chamadas)."""
    calls: list[int] = []

    async def inner(scope: Scope, receive: object, send: object) -> None:
        calls.append(1)

    scope: Scope = {
        "type": kind,
        "method": method,
        "path": "/",
        "headers": [(b"host", value) for value in headers],
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(AllowedHostsMiddleware(inner, ALLOWED)(scope, receive, send))  # type: ignore[arg-type]
    return sent, len(calls)


@pytest.mark.parametrize(
    "host",
    [
        b"127.0.0.1",
        b"127.0.0.1:8001",
        b"LOCALHOST",
        b"LocalHost:8000",
        b"localhost.",
        b"localhost.:80",
        b"[::1]",
        b"[::1]:8001",
        b"Painel.Tail1234.TS.net:443",
    ],
)
def test_allowed_hosts_reach_the_app(host: bytes) -> None:
    sent, calls = call([host])
    assert calls == 1 and sent == []


@pytest.mark.parametrize(
    "host",
    [
        b"evil.com",
        b"evil.com:8001",
        b"127.0.0.1.evil.com",
        b"localhost.evil.com",
        b"user@127.0.0.1",
        b"127.0.0.1@evil.com",
        b"evil.com#@127.0.0.1",
        b"127.0.0.1/",
        b"127.0.0.1:abc",
        b"127.0.0.1:",
        b"",
        b" ",
        b"127.0.0.1 evil.com",
        b"[::1]evil",
        b"[::2]",
        b"\xff.example",
        b"*",
    ],
)
def test_other_hosts_get_400_without_calling_the_app(host: bytes) -> None:
    sent, calls = call([host])
    assert calls == 0
    assert sent[0]["status"] == 400
    assert sent[1]["body"] == b"Pedido recusado."


def test_missing_and_duplicate_host_get_400() -> None:
    assert call([])[1] == 0 and call([])[0][0]["status"] == 400
    sent, calls = call([b"127.0.0.1", b"127.0.0.1"])
    assert calls == 0 and sent[0]["status"] == 400


def test_websocket_with_foreign_host_is_closed() -> None:
    sent, calls = call([b"evil.com"], kind="websocket")
    assert calls == 0 and sent == [{"type": "websocket.close", "code": 1008}]


def test_lifespan_is_not_filtered() -> None:
    assert call([], kind="lifespan")[1] == 1


def test_parse_host_header_normalizes() -> None:
    assert parse_host_header("Example.COM.:80") == "example.com"
    assert parse_host_header("[::1]:80") == "::1"
    assert parse_host_header("a b") is None


@pytest.mark.parametrize("entries", [[], [""], [" "], ["*"], ["127.0.0.1:8000"], ["a b"], ["a@b"]])
def test_invalid_allow_lists_are_refused(entries: list[str]) -> None:
    with pytest.raises(ValueError, match="PANEL_ALLOWED_HOSTS"):
        normalize_allowed_hosts(entries)


def test_allow_list_accepts_ipv6_with_or_without_brackets() -> None:
    assert normalize_allowed_hosts(["[::1]", "::1", "LocalHost."]) == {"::1", "localhost"}


def test_create_app_refuses_wildcard_hosts(make_panel: MakePanel) -> None:
    rig = make_panel([])
    with pytest.raises(ValueError, match="PANEL_ALLOWED_HOSTS"):
        create_app(
            build_settings(panel_allowed_hosts=["*"]),
            rig.interview,
            rig.strategy,
            rig.budget,
            b"c" * 32,
            brand_id=rig.brand_id,
            queries=rig.app.state.panel.queries,
            content=rig.content,
        )


def test_default_allow_list_is_loopback_only() -> None:
    settings = build_settings()
    assert build_settings(panel_allowed_hosts=[]).panel_allowed_hosts == []
    assert type(settings).model_fields["panel_allowed_hosts"].default == ["127.0.0.1", "localhost"]


def client_with_host(rig: PanelRig, host: str) -> TestClient:
    return TestClient(rig.app, client=("127.0.0.1", 5000), base_url=f"http://{host}")


def test_panel_serves_allowed_host_variants(make_panel: MakePanel) -> None:
    rig = make_panel([])
    rig.app = create_app(
        build_settings(panel_allowed_hosts=["127.0.0.1", "localhost"]),
        rig.interview,
        rig.strategy,
        rig.budget,
        b"c" * 32,
        brand_id=rig.brand_id,
        queries=rig.app.state.panel.queries,
        content=rig.content,
    )
    for host in ("127.0.0.1", "127.0.0.1:8001", "LOCALHOST:8000", "localhost."):
        response = client_with_host(rig, host).get("/", headers=rig.headers())
        assert response.status_code == 200, host


@pytest.mark.parametrize(
    "host",
    ["evil.com", "evil.com:8001", "127.0.0.1.evil.com", "localhost.evil.com", "user@127.0.0.1"],
)
def test_foreign_host_is_400_everywhere_with_csp(make_panel: MakePanel, host: str) -> None:
    rig = make_panel([])
    client = rig.client
    headers = rig.headers({"Host": host})
    for path in ("/", "/static/htmx.min.js", "/entrevista/inexistente", "/nao-existe"):
        response = client.get(path, headers=headers)
        assert response.status_code == 400, path
        assert response.text == "Pedido recusado."
        assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
        assert response.headers["X-Content-Type-Options"] == "nosniff"
    for method in ("HEAD", "OPTIONS", "DELETE"):
        assert client.request(method, "/", headers=headers).status_code == 400


def test_post_with_valid_token_and_matching_origin_is_still_400(make_panel: MakePanel) -> None:
    rig = make_panel([])
    before = len(rig.llm.requests)
    response = rig.client.post(
        "/entrevista",
        data={"csrf_token": rig.token()},
        headers=rig.headers({"Host": "evil.com", "Origin": "http://evil.com"}),
        follow_redirects=False,
    )
    assert response.status_code == 400 and response.text == "Pedido recusado."
    assert len(rig.llm.requests) == before


def test_host_check_runs_before_authentication(make_panel: MakePanel) -> None:
    rig = make_panel([])
    anonymous = rig.client.get("/", headers={"Host": "evil.com"})  # sem login nem proxy valido
    assert anonymous.status_code == 400
    untrusted = TestClient(rig.app, client=("203.0.113.9", 1), base_url="http://evil.com")
    assert untrusted.get("/", headers={"Tailscale-User-Login": LOGIN}).status_code == 400


def test_duplicate_host_header_is_400(make_panel: MakePanel) -> None:
    rig = make_panel([])
    headers = [("host", "testserver"), ("host", "testserver"), ("Tailscale-User-Login", LOGIN)]
    assert rig.client.get("/", headers=headers).status_code == 400


def test_testserver_host_passes_with_rig_settings(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert rig.get("/").status_code == 200
    assert isinstance(rig.app, FastAPI)
