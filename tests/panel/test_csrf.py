"""CSRF do painel: mesma origem e token HMAC por utilizador, por formulario ou cabecalho."""

import hmac
from hashlib import sha256
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from marketing_agent.config import Settings
from marketing_agent.panel.auth import TailscaleUser
from marketing_agent.panel.csrf import csrf_token_for, require_same_origin_and_csrf
from marketing_agent.panel.forms import FormFields, read_form

SECRET = b"s" * 32
LOGIN = "Owner@Example.com"
OTHER_LOGIN = "second@example.com"
PEER = "127.0.0.1"
DENIED = "Pedido recusado."


def build_app() -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@127.0.0.1:5433/db",
        panel_allowed_logins=[LOGIN, OTHER_LOGIN],
        panel_trusted_proxy=[PEER],
    )
    app.state.csrf_secret = SECRET

    @app.post("/mutate", dependencies=[Depends(require_same_origin_and_csrf)])
    def mutate(fields: Annotated[FormFields, Depends(read_form)]) -> dict[str, str]:
        return dict(fields)

    return app


def client() -> TestClient:
    return TestClient(build_app(), client=(PEER, 5000))


def token(login: str = LOGIN) -> str:
    return csrf_token_for(TailscaleUser(login=login, name=None), SECRET)


def login_header(login: str = LOGIN) -> dict[str, str]:
    return {"Tailscale-User-Login": login}


def test_token_is_hex_hmac_of_lowercase_login() -> None:
    expected = hmac.new(SECRET, LOGIN.lower().encode(), sha256).hexdigest()
    assert token(LOGIN) == expected == token(LOGIN.upper())
    assert token(LOGIN) != token(OTHER_LOGIN)
    assert token(LOGIN) != csrf_token_for(TailscaleUser(login=LOGIN, name=None), b"t" * 32)


def test_cross_site_post_403() -> None:
    headers = {**login_header(), "Sec-Fetch-Site": "cross-site", "X-CSRF-Token": token()}
    response = client().post("/mutate", headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": DENIED}


@pytest.mark.parametrize("site", ["same-site", "none", "cross-site", ""])
def test_sec_fetch_site_other_than_same_origin_403(site: str) -> None:
    headers = {**login_header(), "Sec-Fetch-Site": site, "X-CSRF-Token": token()}
    assert client().post("/mutate", headers=headers).status_code == 403


def test_same_origin_fetch_metadata_passes() -> None:
    headers = {**login_header(), "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": token()}
    assert client().post("/mutate", headers=headers).status_code == 200


def test_missing_csrf_token_403() -> None:
    response = client().post("/mutate", headers=login_header(), data={"a": "b"})
    assert response.status_code == 403
    assert response.json() == {"detail": DENIED}


def test_valid_csrf_passes() -> None:
    response = client().post(
        "/mutate", headers=login_header(), data={"csrf_token": token(), "a": "b"}
    )
    assert response.status_code == 200
    assert response.json() == {"csrf_token": token(), "a": "b"}


def test_header_token_passes_without_form_body() -> None:
    response = client().post("/mutate", headers={**login_header(), "X-CSRF-Token": token()})
    assert response.status_code == 200
    assert response.json() == {}


def test_header_token_passes_and_body_is_still_readable_by_the_route() -> None:
    headers = {**login_header(), "X-CSRF-Token": token()}
    response = client().post("/mutate", headers=headers, data={"text": "ola"})
    assert response.json() == {"text": "ola"}


def test_wrong_token_in_form_or_header_403() -> None:
    http = client()
    assert (
        http.post("/mutate", headers=login_header(), data={"csrf_token": "x" * 64}).status_code
        == 403
    )
    wrong = {**login_header(), "X-CSRF-Token": "x" * 64}
    assert http.post("/mutate", headers=wrong).status_code == 403
    # Um cabecalho errado nao e salvo por um campo certo: o cabecalho presente decide.
    mixed = http.post("/mutate", headers=wrong, data={"csrf_token": token()})
    assert mixed.status_code == 403


def test_token_of_other_user_is_rejected() -> None:
    headers = {**login_header(OTHER_LOGIN), "X-CSRF-Token": token(LOGIN)}
    assert client().post("/mutate", headers=headers).status_code == 403
    headers = {**login_header(OTHER_LOGIN), "X-CSRF-Token": token(OTHER_LOGIN)}
    assert client().post("/mutate", headers=headers).status_code == 200


def test_origin_must_match_host() -> None:
    http = client()
    base = {**login_header(), "X-CSRF-Token": token()}
    same = http.post("/mutate", headers={**base, "Origin": "http://testserver"})
    assert same.status_code == 200
    other = http.post("/mutate", headers={**base, "Origin": "https://evil.example"})
    assert other.status_code == 403
    port = http.post("/mutate", headers={**base, "Origin": "http://testserver:8443"})
    assert port.status_code == 403


def test_origin_null_403() -> None:
    headers = {**login_header(), "X-CSRF-Token": token(), "Origin": "null"}
    assert client().post("/mutate", headers=headers).status_code == 403


def test_origin_check_ignores_scheme() -> None:
    headers = {**login_header(), "X-CSRF-Token": token(), "Origin": "https://testserver"}
    assert client().post("/mutate", headers=headers).status_code == 200


def test_unauthenticated_is_401_before_csrf() -> None:
    assert client().post("/mutate").status_code == 401
    assert client().post("/mutate", headers={"Tailscale-User-Login": "x@y.z"}).status_code == 403


def test_denials_share_one_body() -> None:
    http = client()
    bodies = {
        http.post("/mutate", headers={**login_header(), "Sec-Fetch-Site": "cross-site"}).text,
        http.post("/mutate", headers={**login_header(), "Origin": "null"}).text,
        http.post("/mutate", headers=login_header()).text,
        http.post("/mutate", headers={**login_header(), "X-CSRF-Token": "bad"}).text,
    }
    assert len(bodies) == 1
    assert token() not in bodies.pop()


def test_non_form_body_is_rejected_after_csrf() -> None:
    headers = {**login_header(), "X-CSRF-Token": token()}
    response = client().post("/mutate", headers=headers, json={"a": "b"})
    assert response.status_code == 415


def test_oversized_form_is_rejected() -> None:
    headers = {**login_header(), "X-CSRF-Token": token()}
    response = client().post("/mutate", headers=headers, data={"text": "a" * 300_000})
    assert response.status_code == 413
