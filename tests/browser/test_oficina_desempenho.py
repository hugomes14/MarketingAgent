"""Pausa, qualidade adaptativa, movimento reduzido e interruptor 3D/2D da oficina."""

import pytest
from panel_rig import LOGIN
from playwright.sync_api import expect
from team_seed import TeamSeed

pytestmark = pytest.mark.e2e_browser

CENA = "#oficina-cena"
CANVAS = "#oficina-cena canvas"
MAIN = "/static/oficina/main.js"
THREE = "/static/vendor/three/"
HEADERS = {"Tailscale-User-Login": LOGIN}


def open_3d(live, page, path="/oficina"):
    """Abre a oficina com movimento normal e espera pelo primeiro frame da cena."""
    page.emulate_media(reduced_motion="no-preference")
    page.goto(live.base_url + path)
    expect(page.locator(CENA)).to_have_attribute("data-pronto", "1", timeout=60000)


def stats(page):
    return page.evaluate(
        f"async () => (await import('{MAIN}')).oficina.estatisticas()",
    )


def set_hidden(page, hidden):
    """Simula o separador oculto; o headless nao dispara visibilitychange sozinho."""
    page.evaluate(
        """hidden => {
          Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
          Object.defineProperty(document, 'visibilityState',
            {configurable: true, get: () => (hidden ? 'hidden' : 'visible')});
          document.dispatchEvent(new Event('visibilitychange'));
        }""",
        hidden,
    )


def wait_until(page, condition, timeout_ms):
    """Espera limitada por uma condicao Python; falha com mensagem em vez de bloquear."""
    for _ in range(timeout_ms // 100):
        if condition():
            return
        page.wait_for_timeout(100)
    assert condition(), "condicao nao cumprida no prazo"


def test_hidden_tab_stops_render_and_polling(live_panel, page):
    live = live_panel([])
    polls = []
    page.on("request", lambda r: polls.append(r.url) if r.url.endswith("/oficina/estado") else None)
    open_3d(live, page)
    set_hidden(page, True)
    frames, requests = stats(page)["quadros"], len(polls)
    # Mais que o intervalo de sondagem (5 s): um temporizador esquecido dispararia aqui.
    page.wait_for_timeout(5600)
    assert stats(page)["quadros"] == frames and len(polls) == requests
    set_hidden(page, False)
    page.wait_for_function(
        f"async () => (await import('{MAIN}')).oficina.estatisticas().quadros > {frames}"
    )
    wait_until(page, lambda: len(polls) > requests, 5000)


def test_pixel_ratio_capped_at_two(browser, live_panel):
    live = live_panel([])
    context = browser.new_context(
        reduced_motion="no-preference",
        device_scale_factor=3,
        extra_http_headers=HEADERS,
        viewport={"width": 1280, "height": 900},
    )
    try:
        page = context.new_page()
        open_3d(live, page)
        assert page.evaluate("window.devicePixelRatio") == 3
        assert stats(page)["pixelRatio"] == 2
    finally:
        context.close()


def test_quality_controller_drops_on_sustained_slow_frames_only(live_panel, page):
    live = live_panel([])
    page.goto(live.base_url + "/oficina")
    result = page.evaluate("""async () => {
      const {ControloQualidade} = await import('/static/oficina/qualidade.js');
      const controlo = new ControloQualidade({limiteMs: 33, janela: 90});
      const alimentar = (n, ms) => {
        let nivel;
        for (let i = 0; i < n; i++) nivel = controlo.registar(ms);
        return nivel;
      };
      const niveis = [];
      for (let i = 0; i < 270; i++) niveis.push(controlo.registar(i % 10 === 0 ? 120 : 16));
      return {
        picos: [...new Set(niveis)],
        meio: alimentar(89, 40),
        baixa: alimentar(1, 40),
        entre: alimentar(90, 30),
        insuficiente: alimentar(90, 40),
        sobe1: alimentar(90, 16),
        sobe2: alimentar(90, 16),
      };
    }""")
    assert result == {
        "picos": ["alta"],
        "meio": "alta",
        "baixa": "baixa",
        "entre": "baixa",
        "insuficiente": "insuficiente",
        "sobe1": "baixa",
        "sobe2": "alta",
    }


def test_slow_frames_lower_quality_then_offer_2d(live_panel, page):
    live = live_panel([])
    open_3d(live, page)
    client = page.context.new_cdp_session(page)
    client.send("Emulation.setCPUThrottlingRate", {"rate": 20})
    try:
        page.wait_for_function(
            f"async () => (await import('{MAIN}')).oficina.estatisticas().qualidade !== 'alta'",
            timeout=90000,
        )
        notice = page.locator("#oficina-lenta")
        expect(notice).to_be_visible(timeout=120000)
        expect(notice).to_contain_text("A oficina está lenta neste dispositivo")
    finally:
        client.send("Emulation.setCPUThrottlingRate", {"rate": 1})
    button = notice.get_by_role("button", name="Mudar para 2D")
    button.focus()
    expect(button).to_be_focused()
    button.click()
    expect(page.locator(CANVAS)).to_have_count(0)
    expect(notice).to_be_hidden()
    assert page.evaluate("localStorage.getItem('oficina.vista')") == "2d"


def test_switch_to_2d_disposes_and_persists(live_panel, page):
    live = live_panel([])
    open_3d(live, page)
    toggle = page.locator("#vista-alternar")
    expect(toggle).to_have_attribute("aria-pressed", "true")
    page.evaluate(f"async () => {{ globalThis.guardada = (await import('{MAIN}')).oficina; }}")
    toggle.click()
    expect(page.locator(CANVAS)).to_have_count(0)
    expect(toggle).to_have_attribute("aria-pressed", "false")
    final = page.evaluate("""() => {
      const o = globalThis.guardada;
      return {memoria: o.estatisticas().memoria, raf: o.cena.raf, ativo: o.estatisticas().ativo};
    }""")
    assert final["memoria"]["geometries"] == 0 and final["memoria"]["textures"] == 0, final
    assert final["raf"] is None and final["ativo"] is False
    assert page.evaluate("localStorage.getItem('oficina.vista')") == "2d"
    page.reload()
    expect(page.locator("#oficina-cena")).to_be_hidden()
    expect(page.locator("#vista-alternar")).to_have_attribute("aria-pressed", "false")
    expect(page.locator(CANVAS)).to_have_count(0)
    page.locator("#vista-alternar").click()
    expect(page.locator(CENA)).to_have_attribute("data-pronto", "1", timeout=60000)
    assert page.evaluate("localStorage.getItem('oficina.vista')") == "3d"


def test_no_webgl_opens_2d_with_same_actions(browser_no_webgl, live_panel, page_errors):
    from tests.browser.conftest import collect_errors

    live = live_panel([])
    context = browser_no_webgl.new_context(
        reduced_motion="no-preference", extra_http_headers=HEADERS
    )
    try:
        page = context.new_page()
        collect_errors(page, page_errors)
        urls = []
        page.on("request", lambda r: urls.append(r.url))
        page.goto(live.base_url + "/oficina")
        toggle = page.locator("#vista-alternar")
        expect(toggle).to_be_visible()
        expect(toggle).to_be_disabled()
        expect(toggle).to_have_text("3D indisponível neste browser")
        expect(page.locator(CANVAS)).to_have_count(0)
        page.locator(".avatar").first.focus()
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        expect(page.locator("#agente-trabalho")).to_have_attribute("data-papel", "copywriter")
        page.wait_for_function("() => document.querySelector('#oficina').dataset.ligacao === 'ok'")
        assert not [u for u in urls if THREE in u or u.endswith("/oficina3d.js")]
    finally:
        context.close()
    assert page_errors == []


def test_reduced_motion_defaults_to_2d_and_3d_on_request_has_no_walking(live_panel, page):
    live = live_panel([])
    seed = TeamSeed(live.rig.factory, live.rig.clock, live.rig.brand_id)
    page.goto(live.base_url + "/oficina")  # a fixture `page` ja pede reduced_motion="reduce"
    toggle = page.locator("#vista-alternar")
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(page.locator(CANVAS)).to_have_count(0)
    assert page.evaluate("localStorage.getItem('oficina.vista')") is None
    toggle.click()
    expect(page.locator(CENA)).to_have_attribute("data-pronto", "1", timeout=60000)
    # Com o estado aplicado as personagens ja estao no sitio final (jardim) e nao se mexem mais.
    expect(page.locator(CENA)).to_have_attribute(
        "data-atividade-copywriter", "relax", timeout=15000
    )
    reading = f"""async () => {{
      const {{oficina}} = await import('{MAIN}');
      const walkers = [];
      for (let frame = 0; frame < 20; frame++) {{
        await new Promise(resolve => requestAnimationFrame(resolve));
        for (const [id, c] of oficina.cena.chars) {{
          const arrived = c.root.position.x === c.spot.x && c.root.position.z === c.spot.z;
          if (c.route.length || !arrived) walkers.push(id);
        }}
      }}
      return {{walkers, camara: oficina.estatisticas().camara}};
    }}"""
    before = page.evaluate(reading)
    page.locator('.avatar[data-papel="designer"]').click()
    expect(page.locator(CENA)).to_have_attribute("data-selecionado", "designer")
    post = seed.seed_post()
    seed.seed_revision(post.id)
    expect(page.locator(CENA)).to_have_attribute(
        "data-ultima-transicao", "entrega_pasta:" + post.id, timeout=15000
    )
    expect(page.locator(CENA)).to_have_attribute(
        "data-atividade-copywriter", "carry", timeout=15000
    )
    after = page.evaluate(reading)
    # Deslocar-se seria rota ativa ou posicao diferente do destino em algum frame.
    assert before["walkers"] == [] and after["walkers"] == []
    assert after["camara"] == before["camara"]


def test_2d_view_never_downloads_three(live_panel, page):
    live = live_panel([])
    urls = []
    page.on("request", lambda r: urls.append(r.url))
    page.goto(live.base_url + "/oficina")  # movimento reduzido: 2D por omissao
    page.wait_for_function("() => document.querySelector('#oficina').dataset.ligacao === 'ok'")
    page.add_init_script("localStorage.setItem('oficina.vista', '2d')")
    page.emulate_media(reduced_motion="no-preference")  # escolha guardada ganha ao 3D por omissao
    page.reload()
    page.wait_for_function("() => document.querySelector('#oficina').dataset.ligacao === 'ok'")
    expect(page.locator(CANVAS)).to_have_count(0)
    forbidden = [u for u in urls if THREE in u or u.endswith(("/oficina3d.js", "/scene.js"))]
    assert forbidden == []


def test_focus_visible_on_avatars_toggle_and_canvas(live_panel, page):
    live = live_panel([])
    open_3d(live, page)
    seen = {}
    for _ in range(24):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => {
          const el = document.activeElement;
          const kind = el.matches('.avatar') ? 'avatar' : el.id === 'vista-alternar' ? 'toggle'
            : el.matches('#oficina-cena canvas') ? 'canvas' : null;
          const css = getComputedStyle(el);
          return {kind, style: css.outlineStyle, width: css.outlineWidth,
            visible: el.matches(':focus-visible')};
        }""")
        if info["kind"]:
            seen[info["kind"]] = info
    assert set(seen) == {"avatar", "toggle", "canvas"}, seen
    for kind, info in seen.items():
        assert info["visible"] and info["style"] != "none" and info["width"] != "0px", kind


def test_measurement_overlay_only_with_query(live_panel, page):
    live = live_panel([])
    open_3d(live, page)
    expect(page.locator("#oficina-medidas")).to_have_count(0)
    open_3d(live, page, "/oficina?medir=1")
    panel = page.locator("#oficina-medidas")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("FPS")
    expect(panel).to_contain_text("Memória")
    expect(panel).to_contain_text("Primeiro frame")
    expect(panel).to_contain_text("Qualidade")
    expect(panel).to_contain_text("alta", timeout=15000)
    assert page.locator("#oficina-medidas *").evaluate_all(
        "els => els.every(e => e.tagName === 'P')"
    )
