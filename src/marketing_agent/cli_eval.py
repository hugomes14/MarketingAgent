"""Subcomandos `marketing-agent eval ...` e a montagem dos servicos da avaliacao (2C).

`eval run --set NOME` mostra o majorante de custo da corrida e pede confirmacao antes de criar os
pacotes na fila (o worker processa-os). Fora de um terminal interativo, sem `--yes`, recusa
(codigo 2): uma corrida gasta dinheiro e nunca comeca sem confirmacao explicita.
"""

import argparse
import sys
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.brands import resolve_single_brand
from marketing_agent.budget.controller import BudgetController
from marketing_agent.clock import Clock
from marketing_agent.config import Settings
from marketing_agent.content.specs import load_content_specs
from marketing_agent.evals.handler import EvalHandlerDeps, EvalPackageHandler
from marketing_agent.evals.runs import (
    EstimateChanged,
    EvalRunDeps,
    EvalRunService,
    EvalSetMismatch,
    RunEstimate,
    RunInProgress,
)
from marketing_agent.evals.sets import EvalSetError
from marketing_agent.evals.simple_path import load_simple_spec
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.harness.engine import AgentEngine
from marketing_agent.jobs.queue import JobQueue
from marketing_agent.kb.service import KbService
from marketing_agent.media.brand_kit import BrandKitService
from marketing_agent.media.library import MediaLibrary
from marketing_agent.proposals import MissingPrerequisite
from marketing_agent.startup import StartupError
from marketing_agent.strategy.service import StrategyService

EVAL_ACTOR = "cli"
# Erros de uso da corrida: a mensagem so cita nomes de conjunto, briefs, campos e valores do codigo.
_USAGE_ERRORS = (EvalSetError, EstimateChanged, EvalSetMismatch, RunInProgress, MissingPrerequisite)


def build_eval_runs(
    settings: Settings,
    session_factory: sessionmaker[Session],
    engine: AgentEngine,
    store: ArtifactStore,
    budget: BudgetController,
    queue: JobQueue,
    library: MediaLibrary,
    clock: Clock,
) -> EvalRunService:
    """Servico das corridas sobre os mesmos colaboradores do worker."""
    deps = EvalRunDeps(
        store=store,
        strategy=StrategyService(session_factory, engine, store, clock),
        kb=KbService(session_factory, store, clock),
        feedback=FeedbackService(session_factory, clock),
        library=library,
        kits=BrandKitService(session_factory, library, clock),
        budget=budget,
        queue=queue,
    )
    return EvalRunService(
        session_factory,
        deps,
        clock,
        evals_dir=settings.evals_dir,
        package_max_cost_usd=settings.package_max_cost_usd,
    )


def build_eval_handler(
    settings: Settings,
    session_factory: sessionmaker[Session],
    engine: AgentEngine,
    queue: JobQueue,
    library: MediaLibrary,
    clock: Clock,
) -> EvalPackageHandler:
    """Handler dos jobs `eval_package` do worker."""
    deps = EvalHandlerDeps(
        engine=engine,
        queue=queue,
        library=library,
        kits=BrandKitService(session_factory, library, clock),
        specs=load_content_specs(),
        simple_spec=load_simple_spec(),
    )
    return EvalPackageHandler(
        session_factory,
        deps,
        clock,
        image_quality=settings.image_quality,
        package_max_cost_usd=settings.package_max_cost_usd,
    )


def format_estimate(estimate: RunEstimate) -> str:
    """Texto da estimativa mostrado antes da confirmacao."""
    return "\n".join(
        [
            f"conjunto {estimate.set_name} v{estimate.set_version}: {estimate.briefs} briefs, "
            f"{estimate.packages} pacotes (simples e especializado)",
            f"custo maximo: {estimate.upper_bound_usd} USD "
            f"({estimate.package_max_cost_usd} USD por pacote, teto imposto a cada pacote)",
            f"gasto do mes ate agora: {estimate.month_spent_usd} USD "
            f"(referencia {estimate.month_reference_usd} USD)",
        ]
    )


def confirm_start(
    *, assume_yes: bool, is_interactive: bool, ask: Callable[[str], str] = input
) -> bool:
    """Confirmacao explicita; fora de um terminal, so com `--yes` (`StartupError` senao)."""
    if assume_yes:
        return True
    if not is_interactive:
        raise StartupError("confirmacao em falta: corra num terminal ou acrescente --yes")
    return ask("Comecar a corrida? [s/N] ").strip().lower() in ("s", "sim")


def run_eval(
    runs: EvalRunService,
    session_factory: sessionmaker[Session],
    set_name: str,
    *,
    assume_yes: bool,
    is_interactive: bool,
    ask: Callable[[str], str] = input,
) -> int:
    """Mostra a estimativa, pede confirmacao e cria a corrida; devolve o codigo de saida."""
    brand_id = resolve_single_brand(session_factory)
    try:
        estimate = runs.estimate(brand_id, set_name)
        print(format_estimate(estimate))
        if not confirm_start(assume_yes=assume_yes, is_interactive=is_interactive, ask=ask):
            print("corrida cancelada", file=sys.stderr)
            return 0
        run_id = runs.start_run(brand_id, set_name, estimate.upper_bound_usd, EVAL_ACTOR)
    except _USAGE_ERRORS as exc:
        raise StartupError(str(exc)) from exc
    print(run_id)
    print("corrida criada: o worker processa os pacotes pela fila", file=sys.stderr)
    return 0


def add_eval_parser(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Subcomando `eval` com `run` (e, mais tarde, `report`)."""
    evaluation = sub.add_parser("eval", help="Avaliação cega: percurso simples vs. especializado.")
    actions = evaluation.add_subparsers(dest="eval_command", required=True, metavar="ACAO")
    run = actions.add_parser("run", help="Mostra o custo máximo e cria uma corrida na fila.")
    run.add_argument("--set", required=True, dest="set_name", metavar="NOME")
    run.add_argument("--yes", action="store_true", help="Confirma sem perguntar.")
