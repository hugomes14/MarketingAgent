"""Chromium e painel isolados; nunca usam a app ou modelos em execucao."""

from contextlib import ExitStack

import pytest
from panel_rig import LOGIN
from playwright.sync_api import sync_playwright
from tests.browser.live_server import LiveServer, run_live_server


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(
            headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
        )
        yield instance
        instance.close()


@pytest.fixture(scope="session")
def browser_no_webgl():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(
            headless=True, args=["--disable-webgl", "--disable-3d-apis"]
        )
        yield instance
        instance.close()


@pytest.fixture
def live_panel(make_panel):
    panels = []
    with ExitStack() as stack:

        def make(script, **overrides):
            rig = make_panel(
                script,
                settings_overrides={
                    "panel_allowed_hosts": ["127.0.0.1", "testserver"],
                    **overrides,
                },
            )
            base_url = stack.enter_context(run_live_server(rig.app))
            panels.append((rig, len(script)))
            return LiveServer(base_url, rig)

        yield make
    for rig, expected in panels:
        assert not rig.llm._script, "guiao LLM nao consumido"
        assert len(rig.llm.requests) == expected, "chamadas LLM fora do guiao"


@pytest.fixture
def page_errors():
    return []


def collect_errors(page, errors):
    """Recolhe erros JS/HTTP e recusas CSP sem mascarar falhas do browser."""
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda msg: (
            errors.append(msg.text) if msg.type == "error" or "Refused to " in msg.text else None
        ),
    )


@pytest.fixture
def page(browser, live_panel, page_errors):
    context = browser.new_context(
        extra_http_headers={"Tailscale-User-Login": LOGIN}, viewport={"width": 1280, "height": 900}
    )
    page = context.new_page()
    collect_errors(page, page_errors)
    yield page
    context.close()
