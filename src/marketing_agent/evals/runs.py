"""Servico das corridas de avaliacao: estimativa, inicio atomico e itens na fila.

`start_run` e uma so transacao: a `eval_run` (semente, estimativa, copia dos briefs e contexto
fixado), um `eval_item` por brief e percurso e um job `eval_package` por item, mais a linha de
auditoria. Ou nasce tudo ou nao nasce nada. A fila so recebe os jobs depois de a corrida
estar validada: conjunto, pilares, itens da base, estrategia, perfil, kit e rota `simples`.

Cada item e um pacote, e o `trigger` `eval_item:<id>` e a unica ligacao entre o item e o custo
(ver `evals.handler`). Mensagens de erro fixas: nunca repetem valores do conjunto.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.budget.controller import BudgetController
from marketing_agent.clock import Clock
from marketing_agent.db.base import new_id
from marketing_agent.db.models import EVAL_PATHS, AuditLog, EvalItem, EvalRun
from marketing_agent.evals.context import (
    ContextSnapshot,
    EvalContext,
    EvalPrerequisiteMissing,
    EvalSetInvalid,
)
from marketing_agent.evals.sets import EvalSet, EvalSetError, load_eval_set
from marketing_agent.jobs.queue import EVAL_PACKAGE_JOB, JobQueue
from marketing_agent.startup import StartupError

ONE_RUNNING_INDEX: Final = "uq_eval_run_one_running_per_brand"
START_ACTION: Final = "eval.start_run"
SEED_BITS: Final = 63  # cabe em `bigint` com sinal (`eval_run.seed`)


class EvalSetMismatch(Exception):
    """O conjunto mudou de versao desde que o responsavel viu a estimativa."""

    def __init__(self) -> None:
        """Mensagem fixa."""
        super().__init__("o conjunto de avaliacao mudou de versao desde a estimativa")


class EstimateChanged(Exception):
    """O limite superior confirmado ja nao e o de agora (conjunto ou teto mudaram)."""

    def __init__(self) -> None:
        """Mensagem fixa."""
        super().__init__("a estimativa mudou desde a confirmacao; confirme de novo")


class RunInProgress(Exception):
    """Ja ha uma corrida a decorrer para a marca."""

    def __init__(self) -> None:
        """Mensagem fixa."""
        super().__init__("ja existe uma corrida de avaliacao a decorrer para esta marca")


# Tudo o que o inicio de uma corrida levanta por motivos esperados: mensagens fixas, seguras
# para o terminal e para o painel.
EVAL_START_ERRORS: Final = (
    EvalSetError,
    EvalSetInvalid,
    EvalPrerequisiteMissing,
    EvalSetMismatch,
    EstimateChanged,
    RunInProgress,
)


@dataclass(frozen=True)
class RunEstimate:
    """O que o responsavel confirma antes de gastar: pacotes e limite superior do custo.

    `upper_bound_usd = packages x package_max_cost_usd`: um limite, nao uma previsao (a maioria
    dos pacotes custa uma fraccao do teto). `month_*` situam o custo face a referencia mensal.
    """

    set_name: str
    set_version: int
    packages: int
    upper_bound_usd: Decimal
    month_spent_usd: Decimal
    month_reference_usd: Decimal


@dataclass(frozen=True)
class _Prepared:
    eval_set: EvalSet
    snapshot: ContextSnapshot


class EvalRunService:
    """Estima e inicia corridas de avaliacao."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        context: EvalContext,
        budget: BudgetController,
        queue: JobQueue,
        clock: Clock,
        evals_dir: Path,
        package_max_cost_usd: Decimal,
        check_routes: Callable[[], None],
        new_seed: Callable[[], int] = lambda: secrets.randbits(SEED_BITS),
    ) -> None:
        """Guarda os colaboradores.

        `check_routes` levanta `StartupError` se a rota `simples` nao serve (ver
        `startup.check_simple_route`); `new_seed` sorteia a semente dos lados A e B.
        """
        self._session_factory = session_factory
        self._context = context
        self._budget = budget
        self._queue = queue
        self._clock = clock
        self._evals_dir = evals_dir
        self._ceiling = package_max_cost_usd
        self._check_routes = check_routes
        self._new_seed = new_seed

    def estimate(self, brand_id: str, set_name: str) -> RunEstimate:
        """Estimativa da corrida; valida tudo o que `start_run` valida, sem escrever nada."""
        return self._estimate_of(brand_id, self._prepare(brand_id, set_name))

    def start_run(
        self,
        brand_id: str,
        set_name: str,
        confirmed_upper_bound_usd: Decimal,
        actor: str,
        *,
        expected_set_version: int | None = None,
    ) -> str:
        """Cria a corrida, os itens e os jobs numa transacao; devolve o id da corrida.

        Raises:
            EvalSetMismatch: `expected_set_version` ja nao e a versao do conjunto.
            EstimateChanged: `confirmed_upper_bound_usd` ja nao e o limite de agora.
            RunInProgress: a marca ja tem uma corrida `running`.
            EvalSetError, EvalSetInvalid, EvalPrerequisiteMissing: ver `estimate`.
        """
        prepared = self._prepare(brand_id, set_name)
        version = prepared.eval_set.version
        if expected_set_version is not None and expected_set_version != version:
            raise EvalSetMismatch
        estimate = self._estimate_of(brand_id, prepared)
        if confirmed_upper_bound_usd != estimate.upper_bound_usd:
            raise EstimateChanged
        try:
            with self._session_factory.begin() as session:
                return self._create(session, brand_id, prepared, estimate, actor)
        except IntegrityError as exc:
            if _violated_constraint(exc) == ONE_RUNNING_INDEX:
                raise RunInProgress from None
            raise

    def _prepare(self, brand_id: str, set_name: str) -> _Prepared:
        try:
            self._check_routes()
        except StartupError as exc:
            raise EvalPrerequisiteMissing(str(exc)) from None
        eval_set = load_eval_set(set_name, self._evals_dir)
        snapshot = self._context.capture(brand_id, eval_set.briefs, self._clock.now().date())
        return _Prepared(eval_set, snapshot)

    def _estimate_of(self, brand_id: str, prepared: _Prepared) -> RunEstimate:
        packages = len(prepared.eval_set.briefs) * len(EVAL_PATHS)
        status = self._budget.status(brand_id)
        return RunEstimate(
            set_name=prepared.eval_set.name,
            set_version=prepared.eval_set.version,
            packages=packages,
            upper_bound_usd=self._ceiling * packages,
            month_spent_usd=status.spent_usd,
            month_reference_usd=status.reference_usd,
        )

    def _create(
        self,
        session: Session,
        brand_id: str,
        prepared: _Prepared,
        estimate: RunEstimate,
        actor: str,
    ) -> str:
        """Corpo da transacao de `start_run`: corrida, itens, jobs e auditoria."""
        if _has_running_run(session, brand_id):
            raise RunInProgress
        now = self._clock.now()
        briefs = prepared.eval_set.briefs
        run = EvalRun(
            id=new_id(),
            brand_id=brand_id,
            set_name=estimate.set_name,
            set_version=estimate.set_version,
            seed=self._new_seed(),
            status="running",
            strategy_artifact_id=prepared.snapshot.strategy_artifact_id,
            briefs=[brief.model_dump(mode="json") for brief in briefs],
            context_snapshot=prepared.snapshot.model_dump(mode="json"),
            estimate_usd=estimate.upper_bound_usd,
            created_by=actor,
            created_at=now,
        )
        session.add(run)
        session.flush()  # a corrida existe antes dos itens (FK composta) e do indice parcial
        for brief in briefs:
            for path in EVAL_PATHS:
                self._add_item(session, run, brief.key, path)
        session.add(
            AuditLog(
                actor=actor,
                action=START_ACTION,
                subject_type="eval_run",
                subject_id=run.id,
                # So ids e contagens: nada do texto dos briefs.
                detail={
                    "brand_id": brand_id,
                    "set_name": estimate.set_name,
                    "set_version": estimate.set_version,
                    "packages": estimate.packages,
                },
                at=now,
            )
        )
        return run.id

    def _add_item(self, session: Session, run: EvalRun, brief_key: str, path: str) -> None:
        """Um item e o seu job `eval_package`, ligados pelo id do item."""
        item = EvalItem(
            id=new_id(),
            brand_id=run.brand_id,
            run_id=run.id,
            brief_key=brief_key,
            path=path,
            status="queued",
        )
        session.add(item)
        session.flush()
        payload = {"eval_run_id": run.id, "eval_item_id": item.id}
        job_id = self._queue.enqueue_in(session, EVAL_PACKAGE_JOB, payload, _dedupe_key(item.id))
        if job_id is None:  # chave nova por item: so acontece se a fila estiver incoerente
            raise RuntimeError("o job do item de avaliacao nao foi criado")
        item.job_id = job_id


def _dedupe_key(item_id: str) -> str:
    return f"{EVAL_PACKAGE_JOB}:{item_id}"


def _has_running_run(session: Session, brand_id: str) -> bool:
    statement = select(EvalRun.id).where(EvalRun.brand_id == brand_id, EvalRun.status == "running")
    return session.execute(statement.limit(1)).first() is not None


def _violated_constraint(error: IntegrityError) -> str | None:
    """Nome da restricao violada (do diagnostico do Postgres), sem usar o texto do erro."""
    diag = getattr(error.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None
