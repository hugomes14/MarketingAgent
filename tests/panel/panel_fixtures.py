"""Fixture `make_panel`: o painel sobre servicos reais, base de teste e cliente LLM falso.

Vive num modulo proprio (e nao num conftest) para tambem servir os testes ponta a ponta; o
`tests/conftest.py` regista-a e o `pythonpath` do pytest expoe esta pasta.
"""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from llm_capabilities import VISION_CAPABILITIES
from panel_rig import (
    PEER,
    SECRET,
    VALID_FROM,
    Factory,
    PanelRig,
    Script,
    build_settings,
    scripted,
)

from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.budget.controller import BudgetController
from marketing_agent.budget.pricing import ModelPrice, PriceBook, PriceTier, sync_model_prices
from marketing_agent.clock import FixedClock
from marketing_agent.config import RoleRoute
from marketing_agent.harness.engine import AgentEngine
from marketing_agent.interview.service import InterviewService
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.router import LlmRouter
from marketing_agent.panel.app import create_app
from marketing_agent.panel.assembly import build_content_services, build_media_services
from marketing_agent.panel.queries import PanelQueries
from marketing_agent.strategy.service import StrategyService


@pytest.fixture
def make_panel(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> Callable[[Script], PanelRig]:
    free = PriceTier(0, Decimal(0), Decimal(0))
    sync_model_prices(
        session_factory, [ModelPrice("fake", "fake-1", "v1", (free,), valid_from=VALID_FROM)]
    )

    def make(script: Script, *, peer: str = PEER) -> PanelRig:
        settings = build_settings(media_dir=tmp_path / "media")
        media = build_media_services(settings, session_factory, clock)
        llm = FakeLlmClient(scripted(script), VISION_CAPABILITIES)
        route = RoleRoute(provider="fake", model_id="fake-1", capabilities=VISION_CAPABILITIES)
        budget = BudgetController(
            session_factory, PriceBook(session_factory), clock, Decimal(20), (50, 80, 100)
        )
        store = ArtifactStore(session_factory, clock)
        engine = AgentEngine(
            session_factory,
            LlmRouter({"diretor": route, "copywriter": route, "revisor": route}, {"fake": llm}),
            budget,
            store,
            clock,
            sleep=lambda seconds: None,
        )
        interview = InterviewService(session_factory, engine, store, settings, clock)
        strategy = StrategyService(session_factory, engine, store, clock)
        content = build_content_services(settings, session_factory, engine, store, strategy, clock)
        app = create_app(
            settings,
            interview,
            strategy,
            budget,
            SECRET,
            brand_id=brand_id,
            queries=PanelQueries(session_factory, store),
            content=content,
            media=media,
        )
        client = TestClient(app, client=(peer, 5000))
        return PanelRig(
            app,
            client,
            session_factory,
            store,
            interview,
            strategy,
            budget,
            llm,
            clock,
            brand_id,
            settings,
            content,
            engine,
            media,
        )

    return make
