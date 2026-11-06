import logging
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.types import ASGIApp
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from marketing_agent.config import Settings
from marketing_agent.panel.auth import TailscaleUser, require_tailscale_user

DATABASE_URL = "postgresql+psycopg://u:p@127.0.0.1:5433/db"
TRUSTED_PEER = "172.18.0.5"
UNTRUSTED_PEER = "172.19.0.5"
LOGIN = "Owner@Example.com"
GENERIC_DENIAL = "Acesso negado."


def build_settings(logins: list[str] | None = None, proxies: list[str] | None = None) -> Settings:
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        panel_allowed_logins=[LOGIN] if logins is None else logins,
        panel_trusted_proxy=[TRUSTED_PEER] if proxies is None else proxies,
    )


def build_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings or build_settings()

    @app.get("/whoami")
    def whoami(
        user: Annotated[TailscaleUser, Depends(require_tailscale_user)],
    ) -> dict[str, str | None]:
        return {"login": user.login, "name": user.name}

    return app


def client_for(app: ASGIApp, peer: str = TRUSTED_PEER) -> TestClient:
    return TestClient(app, client=(peer, 5000), raise_server_exceptions=False)


def raw_request(
    app: FastAPI, headers: list[tuple[bytes, bytes]], client: tuple[str, int] | None
) -> Request:
    scope = {"type": "http", "app": app, "headers": headers, "client": client}
    return Request(scope)


def test_missing_header_401() -> None:
    response = client_for(build_app()).get("/whoami")
    assert response.status_code == 401
    assert response.json() == {"detail": "Autenticação necessária."}


def test_untrusted_peer_403_even_with_header() -> None:
    client = client_for(build_app(), UNTRUSTED_PEER)
    response = client.get("/whoami", headers={"Tailscale-User-Login": LOGIN})
    assert response.status_code == 403
    assert response.json() == {"detail": GENERIC_DENIAL}


def test_untrusted_peer_without_header_is_403_not_401() -> None:
    response = client_for(build_app(), UNTRUSTED_PEER).get("/whoami")
    assert response.status_code == 403


def test_forwarded_for_ignored() -> None:
    client = client_for(build_app(), UNTRUSTED_PEER)
    response = client.get(
        "/whoami",
        headers={"Tailscale-User-Login": LOGIN, "X-Forwarded-For": "127.0.0.1"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "extra",
    [
        {"X-Forwarded-For": "8.8.8.8"},
        {"X-Forwarded-Host": "evil.example"},
        {"X-Real-IP": "8.8.8.8"},
        {"Forwarded": "for=8.8.8.8"},
    ],
)
def test_forwarding_headers_never_change_outcome(extra: dict[str, str]) -> None:
    headers = {"Tailscale-User-Login": LOGIN, **extra}
    trusted = client_for(build_app(), TRUSTED_PEER).get("/whoami", headers=headers)
    assert trusted.status_code == 200
    untrusted = client_for(build_app(), UNTRUSTED_PEER).get("/whoami", headers=headers)
    assert untrusted.status_code == 403
    spoofed = {**extra, "X-Forwarded-For": TRUSTED_PEER, "X-Real-IP": TRUSTED_PEER}
    assert (
        client_for(build_app(), UNTRUSTED_PEER).get("/whoami", headers=spoofed).status_code == 403
    )


def test_login_not_allowed_403() -> None:
    response = client_for(build_app()).get(
        "/whoami", headers={"Tailscale-User-Login": "intruder@example.com"}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": GENERIC_DENIAL}


def test_allowed_login_returns_user() -> None:
    response = client_for(build_app()).get(
        "/whoami",
        headers={"Tailscale-User-Login": f"  {LOGIN} ", "Tailscale-User-Name": "Dono"},
    )
    assert response.status_code == 200
    assert response.json() == {"login": LOGIN, "name": "Dono"}


def test_login_compared_case_insensitively_keeps_header_case() -> None:
    response = client_for(build_app()).get(
        "/whoami", headers={"Tailscale-User-Login": "OWNER@EXAMPLE.COM"}
    )
    assert response.status_code == 200
    assert response.json()["login"] == "OWNER@EXAMPLE.COM"


def test_cidr_entry_trusts_network_only() -> None:
    app = build_app(build_settings(proxies=["172.18.0.0/16"]))
    headers = {"Tailscale-User-Login": LOGIN}
    assert client_for(app, "172.18.0.5").get("/whoami", headers=headers).status_code == 200
    assert client_for(app, "172.19.0.5").get("/whoami", headers=headers).status_code == 403


def test_ipv4_mapped_ipv6_peer_is_normalized() -> None:
    headers = {"Tailscale-User-Login": LOGIN}
    mapped = client_for(build_app(), "::ffff:172.18.0.5")
    assert mapped.get("/whoami", headers=headers).status_code == 200
    other = client_for(build_app(), "::ffff:172.19.0.5")
    assert other.get("/whoami", headers=headers).status_code == 403


def test_ipv6_proxy_entry_is_trusted() -> None:
    app = build_app(build_settings(proxies=["::1"]))
    response = client_for(app, "::1").get("/whoami", headers={"Tailscale-User-Login": LOGIN})
    assert response.status_code == 200


def test_non_ip_peer_is_untrusted() -> None:
    client = client_for(build_app(), "testclient")
    assert client.get("/whoami", headers={"Tailscale-User-Login": LOGIN}).status_code == 403


def test_missing_client_is_403() -> None:
    request = raw_request(build_app(), [(b"tailscale-user-login", LOGIN.encode())], None)
    with pytest.raises(HTTPException) as excinfo:
        require_tailscale_user(request)
    assert excinfo.value.status_code == 403


def test_duplicate_login_headers_403() -> None:
    response = client_for(build_app()).get(
        "/whoami", headers=[("Tailscale-User-Login", LOGIN), ("Tailscale-User-Login", LOGIN)]
    )
    assert response.status_code == 403
    assert response.json() == {"detail": GENERIC_DENIAL}


@pytest.mark.parametrize("value", [b"", b"   ", b"own\x00er@example.com", b"own\x1ber@example.com"])
def test_blank_or_control_char_login_403(value: bytes) -> None:
    app = build_app(build_settings(logins=[value.decode("latin-1"), LOGIN]))
    request = raw_request(app, [(b"tailscale-user-login", value)], (TRUSTED_PEER, 5000))
    with pytest.raises(HTTPException) as excinfo:
        require_tailscale_user(request)
    assert excinfo.value.status_code == 403


def test_overlong_login_403() -> None:
    long_login = "a" * 243 + "@example.com"  # 255 caracteres
    assert len(long_login) == 255
    app = build_app(build_settings(logins=[long_login]))
    response = client_for(app).get("/whoami", headers={"Tailscale-User-Login": long_login})
    assert response.status_code == 403


def test_login_at_length_limit_is_accepted() -> None:
    login = "a" * 242 + "@example.com"
    assert len(login) == 254
    app = build_app(build_settings(logins=[login]))
    response = client_for(app).get("/whoami", headers={"Tailscale-User-Login": login})
    assert response.status_code == 200


def test_empty_allow_list_denies_everyone() -> None:
    app = build_app(build_settings(logins=[]))
    response = client_for(app).get("/whoami", headers={"Tailscale-User-Login": LOGIN})
    assert response.status_code == 403


def test_denials_share_one_body_and_reveal_nothing() -> None:
    allowed = build_app()
    headers = {"Tailscale-User-Login": LOGIN}
    bodies = {
        client_for(allowed, UNTRUSTED_PEER).get("/whoami", headers=headers).text,
        client_for(allowed).get("/whoami", headers={"Tailscale-User-Login": "x@y.z"}).text,
        client_for(allowed)
        .get("/whoami", headers=[("Tailscale-User-Login", "a"), ("Tailscale-User-Login", "b")])
        .text,
    }
    assert len(bodies) == 1
    body = bodies.pop()
    assert LOGIN.lower() not in body.lower()
    assert TRUSTED_PEER not in body


def test_name_is_truncated_and_empty_name_is_none() -> None:
    client = client_for(build_app())
    long_name = client.get(
        "/whoami", headers={"Tailscale-User-Login": LOGIN, "Tailscale-User-Name": "n" * 150}
    )
    assert long_name.json()["name"] == "n" * 100
    blank = client.get(
        "/whoami", headers={"Tailscale-User-Login": LOGIN, "Tailscale-User-Name": "   "}
    )
    assert blank.json()["name"] is None
    absent = client.get("/whoami", headers={"Tailscale-User-Login": LOGIN})
    assert absent.json()["name"] is None


def test_name_never_influences_decision() -> None:
    response = client_for(build_app()).get(
        "/whoami",
        headers={"Tailscale-User-Login": "x@y.z", "Tailscale-User-Name": LOGIN},
    )
    assert response.status_code == 403


def test_invalid_proxy_entry_raises_value_error_naming_it() -> None:
    app = build_app(build_settings(proxies=[TRUSTED_PEER, "not-an-ip"]))
    request = raw_request(app, [(b"tailscale-user-login", LOGIN.encode())], (TRUSTED_PEER, 1))
    with pytest.raises(ValueError, match="not-an-ip"):
        require_tailscale_user(request)
    response = client_for(app).get("/whoami", headers={"Tailscale-User-Login": LOGIN})
    assert response.status_code == 500


def test_denial_log_has_no_login(caplog: pytest.LogCaptureFixture) -> None:
    secret_login = "intruder-secret@example.com"
    with caplog.at_level(logging.WARNING, logger="marketing_agent.panel.auth"):
        client_for(build_app()).get("/whoami", headers={"Tailscale-User-Login": secret_login})
        client_for(build_app(), UNTRUSTED_PEER).get(
            "/whoami", headers={"Tailscale-User-Login": LOGIN}
        )
    assert caplog.records
    text = caplog.text.lower()
    assert secret_login not in text
    assert LOGIN.lower() not in text
    assert UNTRUSTED_PEER in caplog.text


def test_each_settings_instance_is_evaluated_on_its_own() -> None:
    headers = {"Tailscale-User-Login": "other@example.com"}
    first = build_app(build_settings(logins=["other@example.com"]))
    second = build_app(build_settings(logins=[LOGIN]))
    assert client_for(first).get("/whoami", headers=headers).status_code == 200
    assert client_for(second).get("/whoami", headers=headers).status_code == 403
    other_proxy = build_app(build_settings(proxies=["10.0.0.1"]))
    assert client_for(other_proxy).get("/whoami", headers=headers).status_code == 403


def test_missing_app_settings_is_runtime_error() -> None:
    app = FastAPI()
    request = raw_request(app, [], (TRUSTED_PEER, 1))
    with pytest.raises(RuntimeError, match="settings"):
        require_tailscale_user(request)


@pytest.mark.parametrize("entry", ["0.0.0.0/0", "::/0"])
def test_match_all_proxy_entry_raises_value_error(entry: str) -> None:
    app = build_app(build_settings(proxies=[TRUSTED_PEER, entry]))
    request = raw_request(app, [(b"tailscale-user-login", LOGIN.encode())], (TRUSTED_PEER, 1))
    with pytest.raises(ValueError, match=entry):
        require_tailscale_user(request)
    response = client_for(app).get("/whoami", headers={"Tailscale-User-Login": LOGIN})
    assert response.status_code == 500


@pytest.mark.parametrize("entry", ["0.0.0.0/1", "10.0.0.0/8"])
def test_wide_but_not_match_all_proxy_entry_is_accepted(entry: str) -> None:
    app = build_app(build_settings(proxies=[entry]))
    peer = "10.1.2.3"
    response = client_for(app, peer).get("/whoami", headers={"Tailscale-User-Login": LOGIN})
    assert response.status_code == 200


SPOOFING_HEADERS = {"Tailscale-User-Login": LOGIN, "X-Forwarded-For": TRUSTED_PEER}


def test_PROXY_HEADERS_MIDDLEWARE_BREAKS_THE_PEER_CHECK() -> None:
    """Documenta a armadilha: com a reescrita de proxy do uvicorn ativa, o peer e forjavel.

    O ProxyHeadersMiddleware substitui `request.client` pelo `X-Forwarded-For`, e um peer
    nao confiavel passa a parecer o proxy. Por isso o lancador do painel tem de correr o
    uvicorn com `proxy_headers=False` e sem `FORWARDED_ALLOW_IPS`.
    """
    wrapped: ASGIApp = ProxyHeadersMiddleware(build_app(), trusted_hosts="*")  # type: ignore[arg-type,assignment]
    response = client_for(wrapped, UNTRUSTED_PEER).get("/whoami", headers=SPOOFING_HEADERS)
    assert response.status_code == 200


def test_same_spoof_without_proxy_headers_middleware_is_403() -> None:
    response = client_for(build_app(), UNTRUSTED_PEER).get("/whoami", headers=SPOOFING_HEADERS)
    assert response.status_code == 403
