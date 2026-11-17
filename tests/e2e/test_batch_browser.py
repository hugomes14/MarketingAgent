"""Lote real no Chromium: imagens, formularios, CSRF e nova aprovacao apos edicao."""

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import uvicorn
from image_factory import make_jpeg
from panel_rig import SECRET, scripted
from playwright.sync_api import expect, sync_playwright
from tests.e2e.test_weekly_text_flow import MakeWeekly, WeeklyRig, make_weekly

from marketing_agent.llm import fake_scenarios
from marketing_agent.media.library import PhotoUpload
from marketing_agent.panel.app import create_app

__all__ = ["make_weekly"]
pytestmark = pytest.mark.browser


@contextmanager
def browser_server(rig: WeeklyRig) -> Iterator[str]:
    """Painel real em porta efemera; apenas loopback e sem proxy_headers."""
    panel = rig.panel
    settings = panel.settings.model_copy(update={"panel_allowed_hosts": ["127.0.0.1"]})
    app = create_app(
        settings,
        panel.interview,
        panel.strategy,
        panel.budget,
        SECRET,
        brand_id=panel.brand_id,
        queries=panel.app.state.panel.queries,
        content=panel.content,
        media=panel.media,
        team=panel.app.state.panel.team,
        oficina=panel.app.state.panel.oficina,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, proxy_headers=False, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                if not thread.is_alive():
                    raise RuntimeError("painel terminou antes do arranque")
                time.sleep(0.05)
            if not server.started:
                raise RuntimeError("painel nao arrancou")
            yield url
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def test_batch_image_actions_in_chromium(make_weekly: MakeWeekly) -> None:
    rig = make_weekly(3)
    rig.propose_and_approve_plan()
    assert rig.drain() == 3
    photo = rig.panel.media.library.upload_photo(
        rig.panel.brand_id,
        make_jpeg(1080, 1350),
        PhotoUpload(
            mostra="produto",
            descricao="Fotografia para trocar",
            origem="propria",
            tem_autorizacao_pessoas=False,
        ),
        "browser-test",
    )
    post = rig.posts()[0]
    with browser_server(rig) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        anonymous = browser.new_page()
        response = anonymous.goto(f"{url}/lote/2026-W46")
        assert response is not None and response.status == 401
        anonymous.close()
        context = browser.new_context(extra_http_headers=rig.panel.headers())
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{url}/lote/2026-W46")
        card = page.locator(f"#post-{post.id}")
        image = card.locator("img.imagem-publicacao")
        expect(image).to_be_visible()
        assert image.evaluate("img => img.complete && img.naturalWidth === 1080")
        expect(card.get_by_text("Gerada por IA", exact=True)).to_be_visible()
        card.get_by_text("Trocar foto", exact=True).click()
        swap = card.locator('form[action$="/trocar-foto"]')
        swap.locator(f'[name="foto_id"][value="{photo.id}"]').check()
        swap.locator('[name="alt_text"]').fill("Fotografia real da padaria.")
        swap.get_by_role("button", name="Trocar fotografia").click()
        page.wait_for_load_state("networkidle")
        expect(card.get_by_text("Gerada por IA", exact=True)).to_have_count(0)
        expect(card.get_by_text("Imagem verificada pelo Revisor: não", exact=True)).to_be_visible()
        card.get_by_text("Carregar imagem à mão", exact=True).click()
        upload = card.locator('form[action$="/imagem"]')
        upload.locator('[name="ficheiro"]').set_input_files(
            {"name": "manual.jpg", "mimeType": "image/jpeg", "buffer": make_jpeg(1200, 1350)}
        )
        upload.locator('[name="alt_text"]').fill("Fotografia de pão carregada à mão.")
        upload.locator('[name="gerada_por_ia"][value="nao"]').check()
        upload.locator('[name="origem"]').select_option("propria")
        upload.locator('[name="mostra"]').select_option("produto")
        upload.get_by_role("button", name="Carregar e pedir revisão").click()
        page.wait_for_load_state("networkidle")
        rig.panel.llm._script.extend(
            scripted(fake_scenarios.review("aprovar", imagem_verificada=True))
        )
        assert rig.drain() == 1
        page.reload()
        expect(card.get_by_text("Imagem verificada pelo Revisor: sim", exact=True)).to_be_visible()
        expect(card.get_by_text("Gerada por IA", exact=True)).to_have_count(0)
        card.locator("summary").filter(has_text="Editar").click()
        edit = card.locator('form[action$="/editar"]')
        edit.locator('[name="legenda"]').fill("Pão artesanal a 12,50 EUR, disponível na loja.")
        edit.locator('[name="feedback"][value="nao_usar"]').check()
        edit.get_by_role("button", name="Guardar edição").click()
        page.wait_for_load_state("networkidle")
        expect(card.get_by_text("Imagem verificada pelo Revisor: não", exact=True)).to_be_visible()
        expect(card.locator("img.imagem-publicacao")).to_be_visible()
        card.get_by_role("button", name="Aprovar", exact=True).click()
        page.wait_for_load_state("networkidle")
        expect(card.get_by_text("Aprovada", exact=True)).to_be_visible()
        expect(card.get_by_text("Carregar imagem à mão", exact=True)).to_have_count(0)
        assert not errors
        browser.close()
