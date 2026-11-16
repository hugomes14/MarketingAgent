"""Percurso completo a partir da oficina e persistencia da vista entre paginas."""

import pytest
from playwright.sync_api import expect
from team_seed import TeamSeed

pytestmark = pytest.mark.e2e_browser


def pending_button(page):
    return page.locator(".revisao-semanal span")


def test_weekly_review_from_oficina_to_approval(live_panel, page, page_errors):
    live = live_panel([])  # guiao vazio: aprovar nao chama o LLM (a fixture confirma)
    seed = TeamSeed(live.rig.factory, live.rig.clock, live.rig.brand_id)
    post = seed.seed_post()
    seed.seed_revision(post.id, verdict="aprovar")
    page.clock.install(time="2026-11-14T12:00:00Z")
    page.clock.pause_at("2026-11-14T12:00:01Z")
    # Segunda janela: fica na oficina e so conhece a aprovacao pela sondagem.
    witness = page.context.new_page()
    witness.goto(live.base_url + "/")
    page.goto(live.base_url + "/")
    page.wait_for_function("() => document.querySelector('#oficina').dataset.ligacao === 'ok'")
    expect(pending_button(page)).to_have_text("1")

    page.locator('.avatar[data-papel="revisor"]').click()
    delivery = page.locator("#agente-trabalho .entregas a").first
    expect(delivery).to_have_attribute("href", f"/lote/2026-W46#post-{post.id}")
    delivery.click()
    page.wait_for_url(f"**/lote/2026-W46#post-{post.id}")
    with page.expect_navigation():
        page.locator(f"#post-{post.id}").get_by_role("button", name="Aprovar").click()
    assert live.rig.llm.requests == []

    page.get_by_role("navigation", name="Principal").get_by_role("link", name="Oficina").click()
    page.wait_for_url(live.base_url + "/")
    expect(page.locator("#oficina")).to_be_visible()
    expect(pending_button(page)).to_have_text("0")

    witness.wait_for_function("() => document.querySelector('#oficina').dataset.ligacao === 'ok'")
    expect(pending_button(witness)).to_have_text("1")
    page.clock.run_for(5000)
    expect(pending_button(witness)).to_have_text("0")
    assert page_errors == []


def test_view_choice_persists_across_navigation(live_panel, page, page_errors):
    live = live_panel([])
    page.emulate_media(reduced_motion="no-preference")
    page.goto(live.base_url + "/")
    scene = page.locator("#oficina-cena")
    expect(scene).to_have_attribute("data-pronto", "1", timeout=60000)
    toggle = page.locator("#vista-alternar")
    nav = page.get_by_role("navigation", name="Principal")

    toggle.click()
    expect(page.locator("#oficina")).to_have_attribute("data-vista", "2d")
    nav.get_by_role("link", name="Resumo").click()
    page.wait_for_url("**/resumo")
    nav.get_by_role("link", name="Oficina").click()
    expect(page.locator("#oficina")).to_have_attribute("data-vista", "2d")
    expect(page.locator("#oficina-cena canvas")).to_have_count(0)

    page.locator("#vista-alternar").click()
    expect(page.locator("#oficina-cena")).to_have_attribute("data-pronto", "1", timeout=60000)
    nav.get_by_role("link", name="Resumo").click()
    page.wait_for_url("**/resumo")
    nav.get_by_role("link", name="Oficina").click()
    expect(page.locator("#oficina")).to_have_attribute("data-vista", "3d")
    expect(page.locator("#oficina-cena")).to_have_attribute("data-pronto", "1", timeout=60000)
    assert page_errors == []
