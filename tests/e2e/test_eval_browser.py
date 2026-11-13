"""Avaliacao real no Chromium, com conteudo sintetico e servicos reais."""

import socket
import threading
import time
from contextlib import contextmanager

import pytest
import uvicorn
from content_rig import package_pass
from eval_panel_fixtures import evaluation
from panel_rig import SECRET
from playwright.sync_api import expect, sync_playwright

from marketing_agent.llm.fake_scenarios import simple_package
from marketing_agent.panel.app import create_app

__all__ = ["evaluation"]
pytestmark = pytest.mark.browser


@contextmanager
def server(panel):
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
        evals=panel.app.state.panel.evals,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        address = f"http://127.0.0.1:{sock.getsockname()[1]}"
        runner = uvicorn.Server(uvicorn.Config(app, proxy_headers=False, log_level="error"))
        thread = threading.Thread(target=runner.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if runner.started:
                    break
                if not thread.is_alive():
                    raise RuntimeError("painel terminou antes de arrancar")
                time.sleep(0.05)
            assert runner.started
            yield address
        finally:
            runner.should_exit = True
            thread.join(timeout=10)


def test_blind_evaluation_in_chromium(evaluation):
    panel, rig = evaluation
    run_id = rig.start()
    for item in rig.items(run_id):
        rig.process(item, simple_package() if item.path == "simples" else package_pass())
    with server(panel) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            extra_http_headers=panel.headers(), viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{url}/avaliacao/{run_id}")
        expect(page.get_by_role("button", name="Revelar e abrir relatório")).to_be_disabled()
        for index in (1, 2):
            page.goto(f"{url}/avaliacao/{run_id}/par/{index}")
            expect(page.locator(".lado-avaliacao")).to_have_count(2)
            for image in page.locator(".imagem-avaliacao").all():
                expect(image).to_be_visible()
                assert image.evaluate("img => img.complete && img.naturalWidth === 1080")
                assert image.evaluate("img => img.clientWidth <= img.parentElement.clientWidth")
            assert all(
                word not in page.content().lower()
                for word in (
                    "simples",
                    "especializado",
                    "copywriter",
                    "designer",
                    "revisor",
                    "sha256",
                )
            )
            for name, value in (("edit_a", "0"), ("edit_b", "1"), ("better", "empate")):
                page.locator(f'input[name="{name}"][value="{value}"]').check()
            page.get_by_role("button", name="Guardar avaliação").click()
            page.wait_for_url(
                f"**/avaliacao/{run_id}" if index == 2 else f"**/avaliacao/{run_id}/par/2"
            )
        page.get_by_role("button", name="Revelar e abrir relatório").click()
        page.wait_for_url("**/relatorio")
        expect(page.get_by_role("heading", name="Limitações", exact=True)).to_be_visible()
        page.locator('select[name="role"]').select_option("revisor")
        page.locator('select[name="decision"]').select_option("manter")
        page.locator('textarea[name="justificacao"]').fill("Decisao sintetica no browser.")
        page.get_by_role("button", name="Registar decisão").click()
        expect(page.get_by_text("revisor: manter", exact=False)).to_be_visible()
        assert not errors
        browser.close()
