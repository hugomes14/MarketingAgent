"""Cabecalhos de seguranca, HTML sem scripts externos ou inline, HTMX fixo e arranque seguro."""

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from panel_rig import SECRET, PanelRig, build_settings
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.agents.diretor.gap_map import GAP_TOPICS
from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.budget.controller import BudgetController
from marketing_agent.llm import fake_scenarios
from marketing_agent.panel.app import create_app
from marketing_agent.panel.queries import PanelQueries
from marketing_agent.panel.rendering import TEMPLATES_DIR, build_environment

CSP = "default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "marketing_agent" / "panel" / "static"
MakePanel = Callable[..., PanelRig]


def all_pages(rig: PanelRig) -> dict[str, str]:
    """Percorre o fluxo inteiro e devolve o HTML de cada pagina renderizavel."""
    profile_id = rig.confirm_profile()
    session_id = re.search(r"/entrevista/([0-9a-f-]{36})", rig.get("/").text)
    assert session_id
    diagnosis_id = rig.post_id("/diagnostico")
    paths = {
        "inicio": "/",
        "entrevista": f"/entrevista/{session_id.group(1)}",
        "perfil": f"/perfil/{profile_id}",
        "diagnostico": f"/diagnostico/{diagnosis_id}",
    }
    pages = {name: rig.get(path).text for name, path in paths.items()}
    rig.post(f"/diagnostico/{diagnosis_id}/aprovar")
    strategy_id = rig.post_id("/estrategia")
    pages["estrategia"] = rig.get(f"/estrategia/{strategy_id}").text
    rig.post(f"/estrategia/{strategy_id}/aprovar", {"decisao_0": "aceitar", "decisao_1": "recusar"})
    pages["estrategia_decidida"] = rig.get(f"/estrategia/{strategy_id}").text
    pages["inicio_final"] = rig.get("/").text
    pages["erro"] = rig.get(f"/entrevista/{uuid.uuid4()}").text
    return pages


@pytest.fixture
def pages(make_panel: MakePanel) -> dict[str, str]:
    rig = make_panel([*fake_scenarios.interview_full(3), *fake_scenarios.diagnosis_and_strategy()])
    return all_pages(rig)


def test_csp_header_present(make_panel: MakePanel) -> None:
    rig = make_panel([])
    ok = rig.get("/")
    assert ok.headers["Content-Security-Policy"] == CSP
    for response in (
        rig.client.get("/"),  # 401
        rig.get("/entrevista/nao-existe"),  # 404 / 422
        rig.get("/static/panel.css"),
        rig.get("/static/htmx.min.js"),
        rig.post("/entrevista", with_token=False),  # 403
    ):
        assert response.headers["Content-Security-Policy"] == CSP, response.status_code


def test_security_headers_on_html(make_panel: MakePanel) -> None:
    rig = make_panel([])
    response = rig.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["content-type"].startswith("text/html")
    assert "no-store" not in rig.get("/static/panel.css").headers.get("Cache-Control", "")


def test_no_external_scripts(pages: dict[str, str]) -> None:
    for name, html in pages.items():
        sources = re.findall(r"<script\b[^>]*\bsrc=[\"']([^\"']*)[\"']", html)
        assert all(src.startswith("/static/") and "//" not in src for src in sources), name
        assert len(re.findall(r"<script\b", html)) == len(sources), f"script inline em {name}"


def test_no_inline_handlers_styles_or_external_resources(pages: dict[str, str]) -> None:
    for name, html in pages.items():
        assert not re.search(r"<[^>]*\son\w+\s*=", html), name
        assert not re.search(r"<style\b|\sstyle\s*=", html), name
        assert not re.search(r"(?:src|href|action)=[\"']https?:", html), name
        assert "javascript:" not in html.lower(), name


def test_pages_load_htmx_and_configure_it_safely(pages: dict[str, str]) -> None:
    html = pages["inicio"]
    assert '<script src="/static/htmx.min.js"></script>' in html
    match = re.search(r"<meta name=\"htmx-config\" content='([^']*)'", html)
    assert match
    config = json.loads(match.group(1).replace("&#34;", '"'))
    assert config["allowEval"] is False and config["allowScriptTags"] is False
    assert config["includeIndicatorStyles"] is False
    assert config["selfRequestsOnly"] is True and config["historyCacheSize"] == 0
    assert {"code": "[45]..", "swap": True, "error": True} in config["responseHandling"]
    assert '<meta name="csrf-token"' in html


def test_every_form_has_csrf_token_and_post_method(pages: dict[str, str]) -> None:
    for name, html in pages.items():
        for form in re.findall(r"<form\b.*?</form>", html, flags=re.S):
            if re.search(r"method=\"post\"|hx-post=", form):
                assert 'name="csrf_token"' in form, name
            assert not re.search(r"method=\"get\"", form), name


def test_pages_escape_user_login_and_name(make_panel: MakePanel) -> None:
    rig = make_panel([])
    response = rig.get("/", {"Tailscale-User-Name": "<b>Dono</b>"})
    assert "<b>Dono</b>" not in response.text and "&lt;b&gt;Dono&lt;/b&gt;" in response.text


def test_htmx_file_matches_recorded_sha256() -> None:
    readme = (STATIC_DIR / "README.md").read_text(encoding="utf-8")
    recorded = re.search(r"local:\s*([0-9a-f]{64})", readme, flags=re.I)
    assert recorded, "sha256 em falta no README"
    digest = hashlib.sha256((STATIC_DIR / "htmx.min.js").read_bytes()).hexdigest()
    assert digest == recorded.group(1).lower()
    assert re.search(r"\b2\.\d+\.\d+\b", readme) and "sha512-" in readme and "0BSD" in readme


def test_static_files_are_served_and_traversal_is_refused(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert rig.get("/static/htmx.min.js").status_code == 200
    assert rig.get("/static/../auth.py").status_code in (404, 400)
    assert rig.get("/static/%2e%2e/auth.py").status_code == 404


def test_docs_and_openapi_are_not_exposed(make_panel: MakePanel) -> None:
    rig = make_panel([])
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert rig.get(path).status_code == 404


def build_with(
    factory: sessionmaker[Session], rig: PanelRig, secret: bytes = SECRET, **overrides: object
) -> Callable[[], object]:
    settings = build_settings(**overrides)
    store = ArtifactStore(factory, rig.clock)

    def build() -> object:
        return create_app(
            settings,
            rig.interview,
            rig.strategy,
            rig.budget,
            secret,
            brand_id=rig.brand_id,
            queries=PanelQueries(factory, store),
            content=rig.content,
        )

    return build


def test_create_app_rejects_invalid_proxy_entry(make_panel: MakePanel) -> None:
    rig = make_panel([])
    with pytest.raises(ValueError, match="not-an-ip"):
        build_with(rig.factory, rig, panel_trusted_proxy=["127.0.0.1", "not-an-ip"])()
    with pytest.raises(ValueError, match="0.0.0.0/0"):
        build_with(rig.factory, rig, panel_trusted_proxy=["0.0.0.0/0"])()


def test_create_app_rejects_empty_allow_list_and_weak_secret(make_panel: MakePanel) -> None:
    rig = make_panel([])
    with pytest.raises(ValueError, match="PANEL_ALLOWED_LOGINS"):
        build_with(rig.factory, rig, panel_allowed_logins=[])()
    with pytest.raises(ValueError, match="segredo"):
        build_with(rig.factory, rig, secret=b"curto")()
    assert build_with(rig.factory, rig)() is not None


def test_unhandled_error_is_generic_and_keeps_headers(
    make_panel: MakePanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = make_panel([])

    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("segredo interno 1234")

    monkeypatch.setattr(BudgetController, "status", boom)
    client = TestClient(rig.app, client=("127.0.0.1", 5000), raise_server_exceptions=False)
    response = client.get("/", headers=rig.headers())
    assert response.status_code == 500
    assert "segredo interno" not in response.text
    assert response.headers["Content-Security-Policy"] == CSP
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_every_gap_topic_has_a_portuguese_label() -> None:
    label = build_environment().filters["topic_label"]
    for topic in GAP_TOPICS:
        assert "_" not in label(topic) and label(topic)[0].isupper(), topic


def test_templates_never_mark_content_safe() -> None:
    for template in TEMPLATES_DIR.glob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert not re.search(r"\|\s*safe\b|Markup|autoescape\s+false|\{%\s*raw", text), (
            template.name
        )


def test_referrer_policy_is_same_origin_on_html_and_error_responses(make_panel: MakePanel) -> None:
    # `no-referrer` faria o browser enviar `Origin: null` nos formularios simples (ver security.py).
    rig = make_panel([])
    responses = [
        rig.get("/"),
        rig.get(f"/entrevista/{uuid.uuid4()}"),  # pagina de erro 404
        rig.client.get("/"),  # 401 JSON
        rig.post("/entrevista", with_token=False),  # 403
        rig.get("/static/panel.css"),
    ]
    for response in responses:
        assert response.headers["Referrer-Policy"] == "same-origin", response.status_code


BROWSER_FORM_HEADERS = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def browser_post(rig: PanelRig, path: str, data: dict[str, str] | None = None) -> int:
    return rig.post(path, data, BROWSER_FORM_HEADERS).status_code


def test_plain_form_posts_with_browser_headers_succeed(make_panel: MakePanel) -> None:
    rig = make_panel([*fake_scenarios.interview_full(3), *fake_scenarios.diagnosis_and_strategy()])
    profile_id = rig.confirm_profile()
    assert (
        browser_post(rig, f"/perfil/{profile_id}/rejeitar") == 409
    )  # ja confirmado: chegou ao servico
    diagnosis_id = rig.post_id("/diagnostico")
    assert browser_post(rig, f"/diagnostico/{diagnosis_id}/aprovar") == 303
    strategy_id = rig.post_id("/estrategia")
    approval = {"decisao_0": "aceitar", "decisao_1": "recusar"}
    assert browser_post(rig, f"/estrategia/{strategy_id}/aprovar", approval) == 303


def test_plain_form_new_interview_with_browser_headers(make_panel: MakePanel) -> None:
    rig = make_panel(fake_scenarios.interview_full(1))
    assert browser_post(rig, "/entrevista") == 303


def test_plain_form_profile_confirm_with_browser_headers(make_panel: MakePanel) -> None:
    rig = make_panel(fake_scenarios.interview_full(1))
    session_id = rig.start_session()
    rig.answer(session_id, "Resposta.", "k0")
    profile_id = rig.post_id(f"/entrevista/{session_id}/perfil")
    assert browser_post(rig, f"/perfil/{profile_id}/confirmar") == 303


@pytest.mark.parametrize("path", ["/entrevista", "/diagnostico", "/perfil/x/confirmar"])
def test_opaque_origin_null_is_still_refused(make_panel: MakePanel, path: str) -> None:
    rig = make_panel([])
    response = rig.post(path, headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert rig.post(path, headers={"Origin": "null"}).status_code == 403


def test_panel_js_is_served_with_js_type_and_security_headers(make_panel: MakePanel) -> None:
    rig = make_panel([])
    response = rig.get("/static/panel.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.headers["Content-Security-Policy"] == CSP
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "submitted" in response.text and "pageshow" in response.text


def test_pages_with_plain_post_forms_load_panel_js_after_htmx(pages: dict[str, str]) -> None:
    plain_form = re.compile(r"<form\b(?![^>]*\bhx-post=)[^>]*method=\"post\"")
    checked = 0
    for name, html in pages.items():
        if plain_form.search(html):
            checked += 1
            tag = '<script src="/static/panel.js" defer></script>'
            assert tag in html, name
            assert html.index("/static/htmx.min.js") < html.index(tag), name
    assert checked >= 5
