"""Percursos do painel num browser HTTP real, sem fornecedores externos."""

import json

import pytest
from panel_rig import turn_json
from playwright.sync_api import expect
from sqlalchemy import func, select

from marketing_agent.db.models import InterviewSession

pytestmark = pytest.mark.e2e_browser


def test_home_loads_without_console_errors_or_csp_violations(live_panel, page, page_errors):
    live = live_panel([])
    page.goto(live.base_url + "/")
    expect(page.get_by_role("heading", name="Início", exact=True)).to_be_visible()
    assert page_errors == []


def test_plain_form_double_click_creates_one_session(live_panel, page, page_errors):
    live = live_panel([turn_json("Primeira pergunta?")])
    page.goto(live.base_url + "/")
    with page.expect_navigation():
        page.get_by_role("button", name="Nova entrevista").evaluate(
            "(button) => {button.click(); button.click();}"
        )
    expect(page.get_by_text("Primeira pergunta?", exact=True)).to_be_visible()
    with live.rig.factory() as session:
        assert session.scalar(select(func.count()).select_from(InterviewSession)) == 1
    assert len(live.rig.llm.requests) == 1
    assert page_errors == []


def test_back_navigation_reenables_plain_forms(live_panel, page, page_errors):
    live = live_panel([])
    page.goto(live.base_url + "/")
    button = page.get_by_role("button", name="Nova entrevista")
    button.evaluate("""button => {
        button.form.dataset.submitted = '1'; button.disabled = true;
        window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}));
    }""")
    expect(button).to_be_enabled()
    assert button.evaluate("button => !button.form.dataset.submitted")
    assert page_errors == []


def test_htmx_answer_form_swaps_turn_and_sends_idempotency_key(live_panel, page, page_errors):
    live = live_panel([turn_json("O que vende?"), turn_json("E para quem?")])
    identifier = live.rig.start_session()
    page.goto(live.base_url + "/entrevista/" + identifier)
    key = json.loads(page.locator("#form-resposta").get_attribute("hx-headers"))["Idempotency-Key"]
    page.locator("#resposta").fill("Pão artesanal.")
    with page.expect_request("**/resposta") as sent:
        page.get_by_role("button", name="Enviar resposta").click()
    expect(page.locator("#turnos")).to_contain_text("E para quem?")
    assert sent.value.headers["idempotency-key"] == key
    assert sent.value.headers["x-csrf-token"] == live.rig.token()
    assert sent.value.all_headers()["origin"] == live.base_url
    assert len(live.rig.llm.requests) == 2
    assert page_errors == []


def test_htmx_double_submit_makes_one_llm_call(live_panel, page, page_errors):
    live = live_panel([turn_json("O que vende?"), turn_json("Próxima pergunta?")])
    identifier = live.rig.start_session()
    page.goto(live.base_url + "/entrevista/" + identifier)
    page.locator("#resposta").fill("Pão.")
    page.get_by_role("button", name="Enviar resposta").evaluate(
        "(button) => {button.click(); button.click();}"
    )
    expect(page.locator("#turnos")).to_contain_text("Próxima pergunta?")
    assert len(live.rig.llm.requests) == 2  # primeira pergunta + uma resposta
    assert page_errors == []


def test_htmx_error_retargets_to_status_region(live_panel, page, page_errors):
    live = live_panel([turn_json("O que vende?")])
    identifier = live.rig.start_session()
    live.rig.mark_in_progress(identifier, "busy-test", "Em curso.")
    page.goto(live.base_url + "/entrevista/" + identifier)
    page.locator("#resposta").fill("Outra resposta.")
    page.get_by_role("button", name="Enviar resposta").click()
    expect(page.locator("#estado")).to_contain_text("Há uma resposta em curso.")
    expect(page.locator("#form-resposta")).to_be_visible()
    # O HTMX comunica intencionalmente o erro HTTP; nenhum erro JS/CSP e permitido.
    assert all(
        "409" in error and ("Response Status Error" in error or "Failed to load resource" in error)
        for error in page_errors
    ), page_errors


def test_oficina_2d_page_loads_clean(live_panel, page, page_errors):
    live = live_panel([])
    requests = []
    page.on("request", lambda request: requests.append(request))
    page.goto(live.base_url + "/oficina")
    expect(page.locator(".avatar")).to_have_count(4)
    page.locator('.avatar[data-papel="revisor"]').click()
    expect(page.locator("#agente-trabalho")).to_have_attribute("data-papel", "revisor")
    assert all(
        r.method == "GET"
        and r.url.startswith((live.base_url + "/oficina", live.base_url + "/static/"))
        for r in requests
    )
    assert page_errors == []
