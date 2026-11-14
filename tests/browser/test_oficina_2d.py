"""Teclado, sondagem, falhas de ligacao e dados hostis na oficina."""

import json

import pytest
from playwright.sync_api import expect
from team_seed import TeamSeed

pytestmark = pytest.mark.e2e_browser


def open_office(live_panel, page):
    live = live_panel([])
    # install() mantem o tempo real a correr; congelar antes dos intervalos exatos.
    page.clock.install(time="2026-11-14T12:00:00Z")
    page.clock.pause_at("2026-11-14T12:00:01Z")
    page.goto(live.base_url + "/oficina")
    return live


def test_keyboard_arrows_and_enter_open_each_agent(live_panel, page):
    open_office(live_panel, page)
    first = page.locator(".avatar").first
    first.focus()
    for role in ("copywriter", "designer", "revisor", "diretor"):
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        expect(page.locator("#agente-trabalho")).to_have_attribute("data-papel", role)
        expect(page.locator('.avatar[aria-current="true"]')).to_have_attribute("data-papel", role)
    page.keyboard.press("End")
    expect(page.locator(".avatar").last).to_be_focused()
    page.keyboard.press("Home")
    expect(first).to_be_focused()


def test_polling_every_5s_with_if_none_match(live_panel, page):
    requests = []
    page.on("request", lambda r: requests.append(r) if r.url.endswith("/oficina/estado") else None)
    open_office(live_panel, page)
    page.wait_for_function("() => document.querySelector('#oficina').dataset.ligacao === 'ok'")
    before = page.locator(".avatares").text_content()
    with page.expect_response("**/oficina/estado") as response:
        page.clock.run_for(5000)
    assert response.value.status == 304 and requests[-1].headers.get("if-none-match")
    assert page.locator(".avatares").text_content() == before


def test_state_change_updates_text_and_panel(live_panel, page):
    live = open_office(live_panel, page)
    page.locator('.avatar[data-papel="copywriter"]').click()
    expect(page.locator("#agente-trabalho")).to_have_attribute("data-papel", "copywriter")
    page.wait_for_function("() => document.querySelector('#oficina').dataset.ligacao === 'ok'")
    TeamSeed(live.rig.factory, live.rig.clock, live.rig.brand_id).seed_run("copywriter", "running")
    page.clock.run_for(5000)
    expect(page.locator('.avatar[data-papel="copywriter"] .estado-texto')).to_have_text(
        "A trabalhar"
    )
    expect(page.locator("#agente-trabalho .estado-texto")).to_have_text("A trabalhar")


def hide(page, hidden):
    page.evaluate(
        """hidden => {
      Object.defineProperty(document,'hidden',{configurable:true,get:()=>hidden});
      document.dispatchEvent(new Event('visibilitychange'));
    }""",
        hidden,
    )


def test_polling_paused_when_hidden_and_resumes(live_panel, page):
    requests = []
    page.on("request", lambda r: requests.append(r) if r.url.endswith("/oficina/estado") else None)
    open_office(live_panel, page)
    page.wait_for_function("() => document.querySelector('#oficina').dataset.ligacao === 'ok'")
    hide(page, True)
    count = len(requests)
    page.clock.run_for(30000)
    assert len(requests) == count
    with page.expect_request("**/oficina/estado"):
        hide(page, False)


def test_polling_single_inflight_and_backoff_on_errors(live_panel, page):
    pending = []

    def hold(route):
        pending.append(route)
        page.evaluate("n => document.documentElement.dataset.retidos = n", len(pending))

    page.route("**/oficina/estado", hold)
    open_office(live_panel, page)
    expect(page.locator(".avatar")).to_have_count(4)
    page.wait_for_function("() => document.querySelector('#oficina').dataset.ligacao === 'a_ligar'")
    page.clock.run_for(30000)
    assert len(pending) == 1
    for interval in (10000, 20000, 40000, 60000, 60000):
        count = len(pending)
        pending[-1].fulfill(status=500, body="falha")
        page.wait_for_function(
            '(n)=>Number(document.querySelector("#oficina").dataset.falhas)===n', arg=count
        )
        page.clock.run_for(interval - 1)
        assert len(pending) == count
        with page.expect_request("**/oficina/estado"):
            page.clock.run_for(1)
        page.wait_for_function(
            "n => Number(document.documentElement.dataset.retidos) === n", arg=count + 1
        )
    pending[-1].fulfill(status=500)


def test_polling_failures_mark_column_stale(live_panel, page):
    live = live_panel([])
    TeamSeed(live.rig.factory, live.rig.clock, live.rig.brand_id).seed_run("copywriter", "running")
    calls = []

    def fail(route):
        calls.append(1)
        route.fulfill(status=500)

    page.route("**/oficina/estado", fail)
    # install() mantem o tempo real a correr; congelar antes dos intervalos exatos.
    page.clock.install(time="2026-11-14T12:00:00Z")
    page.clock.pause_at("2026-11-14T12:00:01Z")
    page.goto(live.base_url + "/oficina")
    for count, delay in ((1, 10000), (2, 20000)):
        page.wait_for_function(
            '(n)=>Number(document.querySelector("#oficina").dataset.falhas)===n', arg=count
        )
        page.clock.run_for(delay)
    expect(page.locator(".avatar .estado-texto").first).to_have_text("Estado por confirmar")
    expect(page.locator(".avatar .estado-texto").filter(has_text="A trabalhar")).to_have_count(0)
    page.unroute("**/oficina/estado", fail)
    page.clock.run_for(40000)
    expect(page.locator('.avatar[data-papel="copywriter"] .estado-texto')).to_have_text(
        "A trabalhar"
    )


def test_session_expired_stops_polling(live_panel, page):
    calls = []

    def expire(route):
        calls.append(1)
        route.fulfill(status=403)

    page.route("**/oficina/estado", expire)
    open_office(live_panel, page)
    expect(page.locator("#estado")).to_contain_text("sessão")
    page.clock.run_for(120000)
    assert len(calls) == 1


def test_hostile_titles_render_as_text(live_panel, page):
    live = live_panel([])
    seed = TeamSeed(live.rig.factory, live.rig.clock, live.rig.brand_id)
    post = seed.seed_post(tema="<img src=x onerror=alert(1)>")
    seed.seed_revision(post.id)
    page.goto(live.base_url + "/oficina?papel=copywriter")
    expect(page.locator(".entregas")).to_contain_text("<img src=x onerror=alert(1)>")
    expect(page.locator("#oficina img")).to_have_count(0)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//outro.example", "/\\outro.example"])
def test_json_urls_validated_before_use(live_panel, page, url):
    live = live_panel([])
    data = live.rig.get("/oficina/estado").json()
    data["revisao_semanal"]["url"] = url
    page.route(
        "**/oficina/estado",
        lambda route: route.fulfill(content_type="application/json", body=json.dumps(data)),
    )
    page.goto(live.base_url + "/oficina")
    page.wait_for_function("() => document.querySelector('#oficina').dataset.ligacao === 'ok'")
    assert page.locator(".revisao-semanal").get_attribute("href").startswith("/lote/")


def test_hung_request_marks_states_unknown_after_30s(live_panel, page):
    pending = []
    page.route("**/oficina/estado", lambda route: pending.append(route))
    open_office(live_panel, page)
    page.clock.run_for(30000)
    expect(page.locator(".avatar .estado-texto").first).to_have_text("Estado por confirmar")
    for route in pending:
        route.fulfill(status=500)


def test_304_recovery_restores_selected_panel(live_panel, page):
    open_office(live_panel, page)
    page.wait_for_function("() => document.querySelector('#oficina').dataset.ligacao === 'ok'")
    expect(page.locator("#agente-trabalho .estado-texto")).to_have_text("Sem trabalho")

    def fail(route):
        route.fulfill(status=500)

    page.route("**/oficina/estado", fail)
    for count, delay in ((1, 5000), (2, 10000), (3, 20000)):
        page.clock.run_for(delay)
        page.wait_for_function(
            'n => Number(document.querySelector("#oficina").dataset.falhas) === n', arg=count
        )
    expect(page.locator("#agente-trabalho .estado-texto")).to_have_text("Estado por confirmar")
    page.unroute("**/oficina/estado", fail)
    with page.expect_response("**/oficina/estado") as response:
        page.clock.run_for(40000)
    assert response.value.status == 304
    expect(page.locator("#agente-trabalho .estado-texto")).to_have_text("Sem trabalho")


def test_transition_detector_ignores_initial_and_seen_reviews(live_panel, page):
    open_office(live_panel, page)
    result = page.evaluate("""async () => {
      const {detetarTransicoes} = await import('/static/oficina/estado.js');
      const state = ids => ({agentes:[{papel:'revisor',
        entregas:ids.map(id=>({tipo:'parecer',id}))}]});
      return [detetarTransicoes(null,state(['a'])),
        detetarTransicoes(state(['a']),state(['a','b']))];
    }""")
    assert result == [
        [],
        [{"tipo": "entrega_pasta", "de": "copywriter", "para": "revisor", "id": "b"}],
    ]
