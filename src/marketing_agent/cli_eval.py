"""Comandos da avaliacao: estimar primeiro, enfileirar so com confirmacao explicita."""

import argparse
import sys

from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.brands import resolve_single_brand
from marketing_agent.clock import SystemClock
from marketing_agent.config import Settings
from marketing_agent.evals.assembly import build_eval_context, build_eval_runs
from marketing_agent.evals.runs import EVAL_START_ERRORS, EvalRunService
from marketing_agent.harness.engine import AgentEngine
from marketing_agent.llm.factory import build_llm_router
from marketing_agent.startup import (
    StartupError,
    build_budget_controller,
    load_settings,
    open_database,
)
from marketing_agent.strategy.service import StrategyService


def build_cli_runs(settings: Settings, factory: sessionmaker[Session]) -> EvalRunService:
    """Monta os servicos para leitura do contexto; nao executa agentes nem gera imagens."""
    clock = SystemClock()
    budget = build_budget_controller(settings, factory, clock)
    store = ArtifactStore(factory, clock)
    engine = AgentEngine(factory, build_llm_router(settings), budget, store, clock)
    strategy = StrategyService(factory, engine, store, clock)
    context = build_eval_context(factory, store, strategy, clock)
    return build_eval_runs(settings, factory, context, budget, clock)


def cmd_eval(args: argparse.Namespace) -> int:
    """Imprime estimativa; sem TTY exige `--yes` antes de criar os jobs da corrida."""
    settings = load_settings()
    try:
        with open_database(settings) as factory:
            brand_id = resolve_single_brand(factory)
            runs = build_cli_runs(settings, factory)
            estimate = runs.estimate(brand_id, args.set_name)
            print(
                f"{estimate.packages} pacotes; teto USD {estimate.upper_bound_usd:.2f}; "
                f"gasto do mes USD {estimate.month_spent_usd:.2f}; "
                f"referencia USD {estimate.month_reference_usd:.2f}"
            )
            if not args.yes:
                if not sys.stdin.isatty():
                    raise StartupError("fora de um TTY use --yes para confirmar a estimativa")
                print("Iniciar a corrida? [sim/nao] ", end="", flush=True)
                if sys.stdin.readline().strip().casefold() != "sim":
                    print("Corrida cancelada.")
                    return 0
            run_id = runs.start_run(
                brand_id,
                args.set_name,
                estimate.upper_bound_usd,
                "cli",
                expected_set_version=estimate.set_version,
            )
    except EVAL_START_ERRORS as exc:
        raise StartupError(str(exc)) from None
    print(f"Corrida criada: {run_id}")
    return 0
