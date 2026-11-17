"""Fumo operacional de um par na base descartavel do script, sem julgamentos simulados."""

import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.clock import Clock
from marketing_agent.config import Settings
from marketing_agent.db.models import EvalItem, EvalRun
from marketing_agent.evals.assembly import build_eval_context, build_eval_runs
from marketing_agent.evals.snapshot import PackageSnapshot
from marketing_agent.startup import build_budget_controller
from marketing_agent.strategy.service import StrategyService


@dataclass(frozen=True)
class EvalSmokeItem:
    """Resultado tecnico de um percurso, sem avaliar a qualidade em nome do responsavel."""

    path: str
    status: str
    has_image: bool
    latency_ms: int | None
    review_rounds: int
    invalid_outputs: int
    cost_usd: Decimal = Decimal(0)
    image_checked: bool = False


def _outcomes(factory: sessionmaker[Session], run_id: str) -> list[EvalSmokeItem]:
    with factory() as session:
        items = session.scalars(select(EvalItem).where(EvalItem.run_id == run_id))
        outcomes = []
        for item in items:
            snapshot = (
                PackageSnapshot.model_validate(item.revision_snapshot)
                if item.revision_snapshot
                else None
            )
            outcomes.append(
                EvalSmokeItem(
                    item.path,
                    item.status,
                    item.final_asset_id is not None,
                    item.latency_ms,
                    item.review_rounds,
                    item.invalid_outputs,
                    item.cost_usd,
                    snapshot.imagem.verificada is True if snapshot else False,
                )
            )
        return outcomes


def run_eval_pair(
    settings: Settings,
    factory: sessionmaker[Session],
    store: ArtifactStore,
    strategy: StrategyService,
    clock: Clock,
    brand_id: str,
) -> list[EvalSmokeItem]:
    """Gera um par sobre perfil/estrategia/kit reais; a base descartavel e garantida pelo script.

    O conjunto temporario usa um pilar ativo, sem promessas ou referencias inventadas. O worker
    real produz os dois pacotes; nao se gravam respostas cegas nem decisoes de papeis.
    """
    from marketing_agent.cli import build_worker

    active = strategy.active_strategy(brand_id)
    if active is None or not active.estrategia.pilares:
        raise ValueError("o fumo de avaliacao exige estrategia com pilares")
    with tempfile.TemporaryDirectory(prefix="marketing-eval-smoke-") as directory:
        root = Path(directory)
        document = {
            "name": "smoke_eval",
            "version": 1,
            "briefs": [
                {
                    "key": "b1",
                    "tema": "Mostrar os bastidores do trabalho artesanal",
                    "pilar": active.estrategia.pilares[0],
                    "objetivo": "Iniciar conversas com clientes",
                    "canais": ["instagram"],
                    "kb_refs": [],
                    "tags": [],
                }
            ],
        }
        (root / "smoke_eval.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
        configured = settings.model_copy(update={"evals_dir": root})
        budget = build_budget_controller(configured, factory, clock)
        context = build_eval_context(factory, store, strategy, clock)
        runs = build_eval_runs(configured, factory, context, budget, clock)
        estimate = runs.estimate(brand_id, "smoke_eval")
        print(
            f"Avaliacao: {estimate.packages} pacotes, teto {estimate.upper_bound_usd} USD",
            flush=True,
        )
        run_id = runs.start_run(brand_id, "smoke_eval", estimate.upper_bound_usd, "smoke@local")
        worker = build_worker(configured, factory, lock=None)
        for _ in range(6):  # dois pacotes; trava tambem falhas que voltam imediatamente a fila
            if not worker.run_once():
                break
        worker.recover()
        with factory() as session:
            print(f"Corrida de fumo: {session.get_one(EvalRun, run_id).status}", flush=True)
        return _outcomes(factory, run_id)
