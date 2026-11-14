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


def test_avatar_click_selects_character_and_opens_panel(scene_page):
    scene_page.locator('.avatar[data-papel="designer"]').click()
    expect(scene_page.locator("#agente-trabalho")).to_have_attribute("data-papel", "designer")
    expect(scene_page.locator("#oficina-cena")).to_have_attribute("data-selecionado", "designer")


def test_canvas_click_selects_avatar_and_opens_panel(scene_page):
    point = scene_page.evaluate(
        "async () => (await import('/static/oficina/oficina3d.js')).projetarPapel('revisor')"
    )
    scene_page.mouse.click(point["x"], point["y"])
    expect(scene_page.locator('.avatar[aria-current="true"]')).to_have_attribute(
        "data-papel", "revisor"
    )
    expect(scene_page.locator("#agente-trabalho")).to_have_attribute("data-papel", "revisor")


def test_typing_only_when_a_trabalhar(live_panel, page):
    from team_seed import TeamSeed

    from marketing_agent.db.models import AgentRun

    live = live_panel([])
    seed = TeamSeed(live.rig.factory, live.rig.clock, live.rig.brand_id)
    page.emulate_media(reduced_motion="no-preference")
    page.goto(live.base_url + "/oficina")
    scene = page.locator("#oficina-cena")
    expect(scene).to_have_attribute("data-atividade-copywriter", "relax", timeout=60000)
    page.wait_for_timeout(5100)
    expect(scene).to_have_attribute("data-atividade-copywriter", "relax")
    run = seed.seed_run("copywriter", "running")
    expect(scene).to_have_attribute("data-atividade-copywriter", "typing", timeout=15000)
    with live.rig.factory.begin() as session:
        row = session.get_one(AgentRun, run.id)
        row.status = "succeeded"
        row.ended_at = live.rig.clock.now()
    expect(scene).to_have_attribute("data-atividade-copywriter", "relax", timeout=15000)


def test_folder_handover_on_new_reviewed_post(live_panel, page):
    from team_seed import TeamSeed

    live = live_panel([])
    page.emulate_media(reduced_motion="no-preference")
    page.goto(live.base_url + "/oficina")
    expect(page.locator("#oficina-cena")).to_have_attribute("data-pronto", "1", timeout=60000)
    seed = TeamSeed(live.rig.factory, live.rig.clock, live.rig.brand_id)
    post = seed.seed_post()
    seed.seed_revision(post.id)
    expect(page.locator("#oficina-cena")).to_have_attribute(
        "data-ultima-transicao", "entrega_pasta:" + post.id, timeout=15000
    )
    expect(page.locator("#oficina-cena")).to_have_attribute("data-atividade-copywriter", "carry")


def test_first_load_does_not_replay_transitions(live_panel, page):
    from team_seed import TeamSeed

    live = live_panel([])
    seed = TeamSeed(live.rig.factory, live.rig.clock, live.rig.brand_id)
    seed.seed_revision(seed.seed_post().id)
    page.emulate_media(reduced_motion="no-preference")
    page.goto(live.base_url + "/oficina")
    expect(page.locator("#oficina-cena")).to_have_attribute("data-pronto", "1", timeout=60000)
    assert page.locator("#oficina-cena").get_attribute("data-ultima-transicao") is None


def test_characters_use_config_colors(scene_page):
    looks = scene_page.evaluate(
        """async () => {
          const {oficina}=await import('/static/oficina/main.js');
          return [...oficina.cena.chars].map(([id,c])=>[id,JSON.parse(c.lookKey)]);
        }"""
    )
    assert len(looks) == 4
    for role, look in looks:
        avatar = scene_page.locator(f'.avatar[data-papel="{role}"]')
        assert look["hair"] == avatar.get_attribute("data-cabelo")
        assert look["torso"] == look["arms"] == avatar.get_attribute("data-roupa")
        assert look["style"] == avatar.get_attribute("data-estilo")


def test_avatar_faces_captured_and_cached_in_session(scene_page):
    expect(scene_page.locator("#oficina")).to_have_attribute(
        "data-caras-prontas", "1", timeout=60000
    )
    pixels = scene_page.locator(".avatar-cara").first.evaluate(
        'canvas => new Set(canvas.getContext("2d").getImageData(0,0,96,96).data).size'
    )
    assert pixels > 5
    assert (
        scene_page.evaluate(
            "Object.keys(sessionStorage).filter(k=>k.startsWith('oficina.cara.')).length"
        )
        == 4
    )
    scene_page.reload()
    expect(scene_page.locator("#oficina")).to_have_attribute(
        "data-caras-prontas", "1", timeout=60000
    )
    expect(scene_page.locator('.avatar-cara[data-origem-cara="cache"]')).to_have_count(4)
    scene_page.emulate_media(reduced_motion="reduce")
    scene_page.reload()
    expect(scene_page.locator('.avatar-cara[data-origem-cara="cache"]')).to_have_count(4)
    expect(scene_page.locator("#oficina-cena")).to_be_hidden()


def test_hostile_names_in_scene_labels_are_text(live_panel, page):
    live = live_panel([])
    config = live.rig.app.state.panel.oficina
    config.personagens["diretor"] = config.personagens["diretor"].model_copy(
        update={"nome": "<b>Ana</b>"}
    )
    page.emulate_media(reduced_motion="no-preference")
    page.goto(live.base_url + "/oficina")
    expect(page.locator(".office-label").filter(has_text="<b>Ana</b>")).to_have_count(
        1, timeout=60000
    )
    expect(page.locator(".office-label b")).to_have_count(0)


def test_oficina_only_issues_get_requests(live_panel, page):
    import json

    from sqlalchemy import text

    from marketing_agent.db.base import Base

    live = live_panel([])

    def snapshot():
        with live.rig.factory() as session:
            return {
                name: sorted(
                    json.dumps(v, sort_keys=True)
                    for v in session.scalars(text(f'SELECT to_jsonb(t) FROM "{name}" t'))
                )
                for name in Base.metadata.tables
            }

    before = snapshot()
    requests = []
    page.on("request", lambda request: requests.append(request))
    page.emulate_media(reduced_motion="no-preference")
    page.goto(live.base_url + "/oficina")
    expect(page.locator("#oficina")).to_have_attribute("data-caras-prontas", "1", timeout=60000)
    page.close()
    assert requests and all(
        r.method == "GET"
        and r.url.startswith((live.base_url + "/oficina", live.base_url + "/static/"))
        for r in requests
    )
    assert snapshot() == before
    assert live.rig.llm.requests == []


def test_scene_stops_typing_on_disconnect(scene_page):
    scene_page.evaluate("""async () => {
      const {oficina,coluna,cliente}=await import('/static/oficina/main.js');
      // Isolar a falha visual sem receber uma recuperacao real da API durante o teste.
      cliente.parar();
      const estado=structuredClone(cliente.estado);
      estado.agentes.forEach(a=>{a.estado='a_trabalhar';});
      oficina.aplicarEstado(estado,[]);
      coluna.marcarSemLigacao(new Date());
    }""")
    for role in ("diretor", "copywriter", "designer", "revisor"):
        expect(scene_page.locator("#oficina-cena")).to_have_attribute(
            "data-atividade-" + role, "paused"
        )


def test_idle_characters_rest_in_garden_and_remain_visible(scene_page):
    people = scene_page.evaluate("""async () => {
      const {oficina,cliente}=await import('/static/oficina/main.js');
      const estado=structuredClone(cliente.estado);
      estado.agentes.forEach(a=>{a.estado='inativo';});
      oficina.aplicarEstado(estado,[]);
      return [...oficina.cena.chars].map(([id,c])=>({
        id,place:c.place.type,activity:c.activity,visible:c.root.visible}));
    }""")
    assert len(people) == 4
    assert all(p["place"] == "garden" and p["activity"] == "relax" and p["visible"] for p in people)
