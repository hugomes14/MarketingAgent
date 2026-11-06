"""Proxy de desenvolvimento: injeta a identidade e nunca confia em cabecalhos do cliente."""

import http.client
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from marketing_agent.dev_proxy import (
    build_dev_proxy,
    forwarded_request_headers,
    validate_login,
    validate_name,
)

LOGIN = "dono@local"


@dataclass
class Seen:
    """O que o servidor de destino recebeu."""

    method: str
    path: str
    headers: dict[str, list[str]]
    body: bytes


@dataclass
class Rig:
    proxy_port: int
    upstream_port: int
    seen: list[Seen] = field(default_factory=list)


class UpstreamHandler(BaseHTTPRequestHandler):
    """Destino descartavel: regista o pedido e responde conforme o caminho."""

    rig: ClassVar[Rig]

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        headers: dict[str, list[str]] = {}
        for name, value in self.headers.items():
            headers.setdefault(name.lower(), []).append(value)
        self.rig.seen.append(Seen(self.command, self.path, headers, self.rfile.read(length)))
        if self.path == "/redirect":
            self.send_response(303)
            self.send_header("Location", "/estrategia/abc")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"<html>ola</html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", "a=1")
        self.send_header("Set-Cookie", "b=2")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_POST = do_HEAD = _handle

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silencia o registo de acessos nos testes."""


def _serve(server: ThreadingHTTPServer) -> None:
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()


@pytest.fixture
def rig() -> Iterator[Rig]:
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    state = Rig(proxy_port=0, upstream_port=upstream.server_address[1])
    UpstreamHandler.rig = state
    _serve(upstream)
    proxy = build_dev_proxy(("127.0.0.1", 0), ("127.0.0.1", state.upstream_port), LOGIN, "Dono")
    state.proxy_port = proxy.server_address[1]
    _serve(proxy)
    yield state
    proxy.shutdown()
    proxy.server_close()
    upstream.shutdown()
    upstream.server_close()


def request(
    port: int,
    method: str = "GET",
    path: str = "/",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[http.client.HTTPResponse, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response, data


def test_injects_login_and_name(rig: Rig) -> None:
    response, body = request(rig.proxy_port)
    assert response.status == 200 and body == b"<html>ola</html>"
    assert rig.seen[0].headers["tailscale-user-login"] == [LOGIN]
    assert rig.seen[0].headers["tailscale-user-name"] == ["Dono"]


def test_forged_identity_and_forwarding_headers_are_replaced_or_stripped(rig: Rig) -> None:
    forged = {
        "Tailscale-User-Login": "intruso@evil",
        "Tailscale-User-Name": "Intruso",
        "Tailscale-Foo": "x",
        "X-Forwarded-For": "10.0.0.1",
        "X-Forwarded-Host": "evil",
        "X-Forwarded-Proto": "https",
        "Forwarded": "for=10.0.0.1",
        "X-Real-IP": "10.0.0.1",
    }
    request(rig.proxy_port, headers=forged)
    seen = rig.seen[0].headers
    assert seen["tailscale-user-login"] == [LOGIN]
    assert seen["tailscale-user-name"] == ["Dono"]
    for name in ("tailscale-foo", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto"):
        assert name not in seen
    assert "forwarded" not in seen and "x-real-ip" not in seen


def test_host_origin_cookie_and_content_type_are_preserved(rig: Rig) -> None:
    headers = {
        "Origin": f"http://127.0.0.1:{rig.proxy_port}",
        "Cookie": "s=1",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Language": "pt-PT",
        "Idempotency-Key": "k1",
    }
    request(rig.proxy_port, "POST", "/x", headers, b"a=b")
    seen = rig.seen[0].headers
    assert seen["host"] == [f"127.0.0.1:{rig.proxy_port}"]
    assert seen["origin"] == [f"http://127.0.0.1:{rig.proxy_port}"]
    assert seen["cookie"] == ["s=1"]
    assert seen["content-type"] == ["application/x-www-form-urlencoded"]
    assert seen["accept-language"] == ["pt-PT"]
    assert seen["idempotency-key"] == ["k1"]


def test_post_body_is_relayed(rig: Rig) -> None:
    request(rig.proxy_port, "POST", "/entrevista", body=b"csrf_token=abc&text=ola")
    assert rig.seen[0].method == "POST" and rig.seen[0].path == "/entrevista"
    assert rig.seen[0].body == b"csrf_token=abc&text=ola"


def test_redirect_passes_through_untouched(rig: Rig) -> None:
    response, _ = request(rig.proxy_port, "POST", "/redirect", body=b"")
    assert response.status == 303
    assert response.getheader("Location") == "/estrategia/abc"
    assert len(rig.seen) == 1  # o proxy nao seguiu o redirecionamento


def test_response_headers_are_relayed_including_repeated_cookies(rig: Rig) -> None:
    response, _ = request(rig.proxy_port)
    assert response.getheader("Content-Type") == "text/html; charset=utf-8"
    assert response.headers.get_all("Set-Cookie") == ["a=1", "b=2"]
    assert response.getheader("Content-Length") == str(len(b"<html>ola</html>"))


def test_head_has_no_body(rig: Rig) -> None:
    response, body = request(rig.proxy_port, "HEAD")
    assert response.status == 200 and body == b""


def test_hop_by_hop_request_headers_are_not_forwarded(rig: Rig) -> None:
    request(rig.proxy_port, headers={"Connection": "close, X-Custom-Hop", "X-Custom-Hop": "1"})
    assert "x-custom-hop" not in rig.seen[0].headers
    assert rig.seen[0].headers.get("connection") == ["close"]


def test_unreachable_upstream_is_502_with_short_message() -> None:
    probe = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    dead_port = probe.server_address[1]
    probe.server_close()
    proxy = build_dev_proxy(("127.0.0.1", 0), ("127.0.0.1", dead_port), LOGIN, None)
    _serve(proxy)
    try:
        response, body = request(proxy.server_address[1])
    finally:
        proxy.shutdown()
        proxy.server_close()
    assert response.status == 502
    assert len(body) < 200 and b"Traceback" not in body


def test_oversized_request_body_is_413(rig: Rig) -> None:
    response, _ = request(rig.proxy_port, "POST", "/", {"Content-Length": str(50 * 1024 * 1024)})
    assert response.status == 413
    assert rig.seen == []


def test_build_refuses_non_loopback_listen() -> None:
    with pytest.raises(ValueError, match="loopback"):
        build_dev_proxy(("0.0.0.0", 0), ("127.0.0.1", 8000), LOGIN, None)


def test_build_refuses_non_loopback_upstream() -> None:
    with pytest.raises(ValueError, match="loopback"):
        build_dev_proxy(("127.0.0.1", 0), ("192.168.1.5", 8000), LOGIN, None)


@pytest.mark.parametrize("login", ["", "a\nb@x", "a\x00@x", "x" * 255, "joão@x", "a\tb"])
def test_invalid_logins_are_refused(login: str) -> None:
    with pytest.raises(ValueError, match="login"):
        validate_login(login)


def test_valid_login_and_name() -> None:
    assert validate_login("dono@local") == "dono@local"
    assert validate_name("Dono Local") == "Dono Local"
    with pytest.raises(ValueError, match="nome"):
        validate_name("a\r\nX-Evil: 1")


def test_forwarded_request_headers_is_case_insensitive() -> None:
    result = forwarded_request_headers(
        [("tailscale-user-login", "x"), ("X-FORWARDED-FOR", "1"), ("Accept", "*/*")], LOGIN, None
    )
    assert ("Tailscale-User-Login", LOGIN) in result
    assert ("Accept", "*/*") in result
    assert all(name.lower() != "x-forwarded-for" for name, _ in result)
    assert sum(name.lower() == "tailscale-user-login" for name, _ in result) == 1
