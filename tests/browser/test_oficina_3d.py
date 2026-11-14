"""Cena real com WebGL por software, sem relaxar CSP."""

import io

import pytest
from PIL import Image
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e_browser


@pytest.fixture
def scene_page(live_panel, page):
    live = live_panel([])
    page.emulate_media(reduced_motion="no-preference")
    page.goto(live.base_url + "/oficina")
    expect(page.locator("#oficina-cena")).to_have_attribute("data-pronto", "1", timeout=60000)
    return page


def test_3d_loads_without_console_errors_or_csp_violations(scene_page, page_errors):
    expect(scene_page.locator("#oficina-cena canvas")).to_have_count(1)
    assert page_errors == []


def test_canvas_is_painted(scene_page):
    image = Image.open(io.BytesIO(scene_page.locator("#oficina-cena canvas").screenshot()))
    assert len(set(image.convert("RGB").get_flattened_data())) > 50


def test_canvas_has_aria_label_and_is_focusable(scene_page):
    canvas = scene_page.locator("#oficina-cena canvas")
    expect(canvas).to_have_attribute("tabindex", "0")
    expect(canvas).to_have_attribute("aria-label", "Oficina 3D da equipa")
    canvas.focus()
    expect(canvas).to_be_focused()


def test_arrow_keys_in_avatar_list_do_not_move_camera(scene_page):
    scene_page.locator(".avatar").first.focus()
    before = scene_page.evaluate(
        "async () => (await import('/static/oficina/main.js')).oficina.estatisticas().camara"
    )
    for key in ("ArrowDown", "ArrowDown", "ArrowUp", "Home", "End"):
        scene_page.keyboard.press(key)
    after = scene_page.evaluate(
        "async () => (await import('/static/oficina/main.js')).oficina.estatisticas().camara"
    )
    assert before == after


def test_scene_lifecycle_pauses_and_disposes(scene_page):
    result = scene_page.evaluate("""async () => {
      const {oficina} = await import('/static/oficina/main.js');
      oficina.cena.setQuality('baixa');
      const low = {ratio:oficina.estatisticas().pixelRatio, composer:oficina.cena.composer};
      oficina.cena.pause();
      const frames=oficina.estatisticas().quadros;
      await new Promise(resolve=>requestAnimationFrame(resolve));
      const paused=oficina.estatisticas().quadros===frames && oficina.cena.raf===null;
      const memoria=oficina.desmontar();
      return {low,paused,memoria,canvases:document.querySelectorAll('#oficina-cena canvas').length};
    }""")
    assert result["low"] == {"ratio": 1, "composer": None}
    assert result["paused"] and result["canvases"] == 0
    assert result["memoria"]["geometries"] == 0 and result["memoria"]["textures"] == 0, result
