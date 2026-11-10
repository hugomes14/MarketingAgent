"""Fabrica do painel a partir do ambiente: o alvo `--factory` do uvicorn.

Monta, uma vez, as definicoes, a base, o orcamento, o router de LLM, o motor e os servicos reais.
Todo erro de configuracao esperado e um `StartupError` curto que nomeia a variavel e nunca o
seu valor (segredos, chaves de API e a palavra-passe da base nao saem nas mensagens).
O servidor tem de correr sem reescrita de cabecalhos de proxy (ver `panel.auth`); o comando
`marketing-agent serve` impoe isso.
"""

import logging
from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.brands import resolve_single_brand
from marketing_agent.budget.controller import BudgetController
from marketing_agent.clock import SystemClock
from marketing_agent.config import Settings
from marketing_agent.db.base import make_engine, make_session_factory
from marketing_agent.harness.engine import AgentEngine
from marketing_agent.interview.service import InterviewService
from marketing_agent.llm.factory import build_llm_router
from marketing_agent.llm.router import LlmRouter
from marketing_agent.media.library import MediaLibrary
from marketing_agent.media.store import LocalMediaStore
from marketing_agent.panel.app import create_app
from marketing_agent.panel.context import MediaServices, PanelContext
from marketing_agent.panel.queries import PanelQueries
from marketing_agent.secrets import SecretFileError, read_secret_file
from marketing_agent.startup import (
    StartupError,
    build_budget_controller,
    database_error_message,
    director_max_duration_s,
    load_settings,
)
from marketing_agent.strategy.service import StrategyService

logger = logging.getLogger(__name__)

MIN_CSRF_SECRET_CHARS = 32
CSRF_VARIABLE = "PANEL_CSRF_SECRET_FILE"


def create_app_from_env() -> FastAPI:
    """Constroi o painel com a configuracao do ambiente (alvo de `uvicorn --factory`).

    Raises:
        StartupError: configuracao invalida, segredo em falta ou curto, rota `fake`, base
            inacessivel ou sem migracoes, ou nao haver exatamente uma marca.
    """
    settings = load_settings()
    csrf_secret = _read_csrf_secret(settings)
    router = _build_router(settings)
    session_factory = _open_session_factory(settings)
    brand_id = _single_brand(session_factory)
    app = _assemble(settings, csrf_secret, router, session_factory, brand_id)
    panel: PanelContext = app.state.panel
    _recover_stale_reservations(panel.budget)
    return app


def _assemble(
    settings: Settings,
    csrf_secret: bytes,
    router: LlmRouter,
    session_factory: sessionmaker[Session],
    brand_id: str,
) -> FastAPI:
    """Liga o motor aos servicos reais e monta o painel (`create_app` valida a seguranca)."""
    clock = SystemClock()
    budget = build_budget_controller(settings, session_factory, clock)
    store = ArtifactStore(session_factory, clock)
    engine = AgentEngine(session_factory, router, budget, store, clock)
    interview = InterviewService(session_factory, engine, store, settings, clock)
    strategy = StrategyService(session_factory, engine, store, clock)
    try:
        return create_app(
            settings,
            interview,
            strategy,
            budget,
            csrf_secret,
            brand_id=brand_id,
            queries=PanelQueries(session_factory, store),
            media=MediaServices(
                MediaLibrary(session_factory, LocalMediaStore(settings.media_dir), clock)
            ),
        )
    except ValueError as exc:
        raise StartupError(str(exc)) from exc


def _read_csrf_secret(settings: Settings) -> bytes:
    path = settings.panel_csrf_secret_file
    if path is None:
        raise StartupError(f"{CSRF_VARIABLE} nao definido: indique o ficheiro com o segredo CSRF")
    try:
        secret = read_secret_file(path)
    except SecretFileError as exc:
        raise StartupError(f"{CSRF_VARIABLE} invalido: {exc}") from exc
    if len(secret) < MIN_CSRF_SECRET_CHARS:
        raise StartupError(
            f"{CSRF_VARIABLE}: o segredo deve ter pelo menos {MIN_CSRF_SECRET_CHARS} caracteres"
        )
    return secret.encode()


def _build_router(settings: Settings) -> LlmRouter:
    if any(route.provider == "fake" for route in settings.role_routes.values()):
        raise StartupError(
            "ROLE_ROUTES: o fornecedor 'fake' so existe nos testes; use 'anthropic' ou 'openai'"
        )
    try:
        return build_llm_router(settings)
    except ValueError as exc:
        raise StartupError(str(exc)) from exc


def _open_session_factory(settings: Settings) -> sessionmaker[Session]:
    try:
        return make_session_factory(make_engine(settings.database_url))
    except SQLAlchemyError as exc:
        raise StartupError(database_error_message(exc)) from None


def _single_brand(session_factory: sessionmaker[Session]) -> str:
    try:
        return resolve_single_brand(session_factory)
    except SQLAlchemyError as exc:
        raise StartupError(database_error_message(exc)) from None


def _recover_stale_reservations(budget: BudgetController) -> None:
    """Reservas abertas ha mais do que o maior `max_duration_s` pertencem a um processo morto."""
    older_than = timedelta(seconds=director_max_duration_s())
    count = budget.recover_stale(older_than)
    logger.info("arranque: %d reserva(s) aberta(s) marcada(s) como unknown_outcome", count)
