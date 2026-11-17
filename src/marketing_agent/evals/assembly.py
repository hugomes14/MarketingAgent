"""Montagem partilhada da avaliacao no painel, CLI e worker (sem chamadas ao modelo)."""

from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.budget.controller import BudgetController
from marketing_agent.budget.pricing import PriceBook
from marketing_agent.clock import Clock
from marketing_agent.config import Settings
from marketing_agent.evals.context import EvalContext
from marketing_agent.evals.runs import EvalRunService
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.jobs.queue import JobQueue
from marketing_agent.kb.service import KbService
from marketing_agent.startup import check_content_routes, check_simple_route, content_job_lease
from marketing_agent.strategy.service import StrategyService


def build_eval_context(
    factory: sessionmaker[Session], store: ArtifactStore, strategy: StrategyService, clock: Clock
) -> EvalContext:
    """Contexto sobre os mesmos artefactos, base de conhecimento e feedback da marca."""
    return EvalContext(
        factory, store, strategy, KbService(factory, store, clock), FeedbackService(factory, clock)
    )


def build_eval_runs(
    settings: Settings,
    factory: sessionmaker[Session],
    context: EvalContext,
    budget: BudgetController,
    clock: Clock,
) -> EvalRunService:
    """Corridas com validacao de TODAS as rotas antes de confirmar e enfileirar custos."""
    prices = PriceBook(factory)

    def check_routes() -> None:
        check_content_routes(settings, prices)
        check_simple_route(settings, prices)

    queue = JobQueue(
        factory, clock, content_job_lease(time_multiplier=settings.llm_time_multiplier)
    )
    return EvalRunService(
        factory,
        context,
        budget,
        queue,
        clock,
        settings.evals_dir,
        settings.package_max_cost_usd,
        check_routes,
    )
