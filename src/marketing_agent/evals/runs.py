"""Corridas de avaliacao: estimativa, inicio atomico pela fila e reconciliacao (spec 2C, secao 4).

Uma corrida cria, numa so transacao, a `eval_run` (semente, briefs e contexto fixados), um
`eval_item` por brief e percurso e um job `eval_package` por item. Os pacotes nunca viram posts.

Custo: a estimativa mostrada antes de comecar e um MAJORANTE, nao uma previsao: pacotes x teto de
custo por pacote, que o `PackageCostGuard` impoe em cada pacote. O inicio recusa-se se o
majorante mudou desde que foi mostrado (`EstimateChanged`), para o responsavel nunca confirmar
um valor e gastar outro.
"""

import secrets
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore, ArtifactType
from marketing_agent.budget.controller import BudgetController
from marketing_agent.clock import Clock
from marketing_agent.db.models import (
    EVAL_PATHS,
    AuditLog,
    EvalItem,
    EvalRun,
    Job,
    KbItem,
)
from marketing_agent.evals.context import EvalContext, FixedBrief
from marketing_agent.evals.sets import EvalBrief, EvalSet, load_eval_set
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.jobs.queue import EVAL_PACKAGE_JOB, JobQueue
from marketing_agent.kb.service import KbEntry, KbService
from marketing_agent.media.brand_kit import BrandKitService
from marketing_agent.media.library import MediaLibrary
from marketing_agent.proposals import MissingPrerequisite, require_active
from marketing_agent.strategy.service import StrategyService

TERMINAL_ITEM_STATUSES: Final = ("succeeded", "failed")
_FAILED_JOB_STATUSES: Final = ("failed", "dead", "cancelled")
# Motivo fixo de um item cujo job terminou sem o fechar (worker morto, job cancelado).
JOB_ENDED_ERROR: Final = "o trabalho terminou sem concluir o pacote"


class EvalSetMismatch(ValueError):
    """O conjunto nao serve a marca: pilar fora da estrategia ativa ou item da base em falta."""


class EstimateChanged(ValueError):
    """O majorante confirmado ja nao e o atual (conjunto, teto ou numero de pacotes mudaram)."""


class RunInProgress(ValueError):
    """Ja ha uma corrida `running` nesta marca."""


@dataclass(frozen=True)
class RunEstimate:
    """O que o responsavel ve antes de comecar: pacotes, majorante e o mes ate agora."""

    set_name: str
    set_version: int
    briefs: int
    packages: int
    package_max_cost_usd: Decimal
    upper_bound_usd: Decimal
    month_spent_usd: Decimal
    month_reference_usd: Decimal


@dataclass(frozen=True)
class EvalRunDeps:
    """Colaboradores do servico de corridas (agrupados para o construtor ficar curto)."""

    store: ArtifactStore
    strategy: StrategyService
    kb: KbService
    feedback: FeedbackService
    library: MediaLibrary
    kits: BrandKitService
    budget: BudgetController
    queue: JobQueue


class EvalRunService:
    """Estima, inicia e reconcilia corridas; nunca chama o modelo."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        deps: EvalRunDeps,
        clock: Clock,
        *,
        evals_dir: Path,
        package_max_cost_usd: Decimal,
    ) -> None:
        """`evals_dir` e a pasta dos conjuntos; `package_max_cost_usd` o teto por pacote."""
        self._session_factory = session_factory
        self._deps = deps
        self._clock = clock
        self._evals_dir = evals_dir
        self._ceiling = package_max_cost_usd

    def estimate(self, brand_id: str, set_name: str) -> RunEstimate:
        """Majorante da corrida e gasto do mes; `EvalSetError` se o conjunto e invalido."""
        eval_set = load_eval_set(set_name, self._evals_dir)
        return self._estimate(brand_id, eval_set)

    def _estimate(self, brand_id: str, eval_set: EvalSet) -> RunEstimate:
        packages = len(eval_set.briefs) * len(EVAL_PATHS)
        status = self._deps.budget.status(brand_id)
        return RunEstimate(
            set_name=eval_set.name,
            set_version=eval_set.version,
            briefs=len(eval_set.briefs),
            packages=packages,
            package_max_cost_usd=self._ceiling,
            upper_bound_usd=self._ceiling * packages,
            month_spent_usd=status.spent_usd,
            month_reference_usd=status.reference_usd,
        )

    def start_run(
        self, brand_id: str, set_name: str, confirmed_upper_bound_usd: Decimal, actor: str
    ) -> str:
        """Cria a corrida, os itens e os jobs numa transacao; devolve o id da corrida.

        Raises:
            EvalSetError: conjunto inexistente ou invalido.
            EstimateChanged: o majorante confirmado nao e o atual.
            MissingPrerequisite: falta perfil, estrategia ativa ou kit de marca.
            EvalSetMismatch: pilar fora da estrategia ativa ou item da base em falta.
            RunInProgress: ja ha uma corrida `running` na marca.
        """
        eval_set = load_eval_set(set_name, self._evals_dir)
        estimate = self._estimate(brand_id, eval_set)
        if confirmed_upper_bound_usd != estimate.upper_bound_usd:
            raise EstimateChanged(
                f"o majorante mudou: confirmado {confirmed_upper_bound_usd}, "
                f"atual {estimate.upper_bound_usd} USD"
            )
        if self.running_run_id(brand_id) is not None:
            raise RunInProgress("ja ha uma corrida de avaliacao em curso nesta marca")
        briefs, context = self._fix_context(brand_id, eval_set)
        try:
            return self._create(brand_id, eval_set, estimate, briefs, context, actor)
        except IntegrityError:
            # Corrida concorrente: o indice parcial so admite uma `running` por marca.
            raise RunInProgress("ja ha uma corrida de avaliacao em curso nesta marca") from None

    def running_run_id(self, brand_id: str) -> str | None:
        """Id da corrida `running` da marca, se houver."""
        statement = select(EvalRun.id).where(
            EvalRun.brand_id == brand_id, EvalRun.status == "running"
        )
        with self._session_factory() as session:
            return session.scalar(statement)

    def _fix_context(
        self, brand_id: str, eval_set: EvalSet
    ) -> tuple[list[FixedBrief], EvalContext]:
        """Valida o conjunto contra a marca e copia o contexto que os percursos vao ler."""
        deps = self._deps
        profile = require_active(deps.store, brand_id, ArtifactType.PERFIL_MARCA, "avaliacao")
        active = deps.strategy.active_strategy(brand_id)
        if active is None:
            raise MissingPrerequisite(f"avaliacao: falta uma estrategia ativa na marca {brand_id}")
        kit = deps.kits.active(brand_id)
        if kit is None:
            raise MissingPrerequisite(f"avaliacao: falta o kit de marca na marca {brand_id}")
        pilares = set(active.estrategia.pilares)
        unknown = sorted({b.key for b in eval_set.briefs if b.pilar not in pilares})
        if unknown:
            raise EvalSetMismatch(f"briefs com pilar fora da estrategia ativa: {unknown}")
        kb = self._resolve_kb(brand_id, eval_set.briefs)
        briefs = [_fixed_brief(brief, kb) for brief in eval_set.briefs]
        styles, negatives = deps.feedback.prompt_examples(brand_id)
        context = EvalContext(
            profile_artifact_id=profile.id,
            perfil_marca=profile.payload,
            strategy_artifact_id=active.artifact_id,
            pilares=tuple(active.estrategia.pilares),
            propostas_aceites=tuple(active.propostas_aceites),
            kb={entry.id: entry for entry in kb.values()},
            photos=deps.library.photo_refs(brand_id),
            kit_id=kit.id,
            style_examples=tuple(styles),
            negative_examples=tuple(negatives),
        )
        return briefs, context

    def _resolve_kb(self, brand_id: str, briefs: list[EvalBrief]) -> dict[str, KbEntry]:
        """`kind:key` -> item ativo e valido hoje (o mais recente); `EvalSetMismatch` se falta."""
        wanted = sorted({ref for brief in briefs for ref in brief.kb_refs})
        if not wanted:
            return {}
        today = self._clock.now().date()
        with self._session_factory() as session:
            rows = session.scalars(
                select(KbItem.id)
                .where(KbItem.brand_id == brand_id, KbItem.is_active.is_(True))
                .order_by(KbItem.valid_from.desc())
            ).all()
        valid = self._deps.kb.valid_refs(brand_id, list(rows), today)
        resolved: dict[str, KbEntry] = {}
        for entry in sorted(valid.values(), key=lambda e: e.valid_from, reverse=True):
            resolved.setdefault(f"{entry.kind}:{entry.key}", entry)
        missing = [ref for ref in wanted if ref not in resolved]
        if missing:
            raise EvalSetMismatch(f"itens da base em falta ou fora da validade: {missing}")
        return {ref: resolved[ref] for ref in wanted}

    def _create(
        self,
        brand_id: str,
        eval_set: EvalSet,
        estimate: RunEstimate,
        briefs: list[FixedBrief],
        context: EvalContext,
        actor: str,
    ) -> str:
        now = self._clock.now()
        with self._session_factory.begin() as session:
            run = EvalRun(
                brand_id=brand_id,
                set_name=eval_set.name,
                set_version=eval_set.version,
                seed=secrets.randbits(63),
                status="running",
                strategy_artifact_id=context.strategy_artifact_id,
                briefs=[brief.to_json() for brief in briefs],
                context_snapshot=context.to_json(),
                estimate_usd=estimate.upper_bound_usd,
                created_by=actor,
                created_at=now,
            )
            session.add(run)
            session.flush()
            for brief in briefs:
                for path in EVAL_PATHS:
                    self._add_item(session, run, brief.key, path)
            session.add(
                AuditLog(
                    actor=actor,
                    action="eval_run_started",
                    subject_type="eval_run",
                    subject_id=run.id,
                    detail={
                        "set_name": eval_set.name,
                        "set_version": eval_set.version,
                        "packages": estimate.packages,
                        "upper_bound_usd": str(estimate.upper_bound_usd),
                    },
                    at=now,
                )
            )
            return run.id

    def _add_item(self, session: Session, run: EvalRun, brief_key: str, path: str) -> None:
        item = EvalItem(
            brand_id=run.brand_id,
            run_id=run.id,
            brief_key=brief_key,
            path=path,
            status="queued",
            created_at=run.created_at,
        )
        session.add(item)
        session.flush()
        payload = {"eval_run_id": run.id, "eval_item_id": item.id}
        job_id = self._deps.queue.enqueue_in(
            session, EVAL_PACKAGE_JOB, payload, f"{EVAL_PACKAGE_JOB}:{item.id}"
        )
        item.job_id = job_id

    def reconcile(self, brand_id: str | None = None) -> int:
        """Itens por terminar cujo job ja terminou sem os fechar passam a `failed`; conta-os.

        Corre no arranque do worker: um item cujo job ficou `dead`, `failed` ou `cancelled` (por
        exemplo, um worker morto antes de o fechar) nunca deixa a corrida a meio para sempre.
        """
        now = self._clock.now()
        with self._session_factory.begin() as session:
            statement = (
                select(EvalItem)
                .join(Job, Job.id == EvalItem.job_id)
                .where(
                    EvalItem.status.in_(("queued", "running")),
                    Job.status.in_(_FAILED_JOB_STATUSES),
                )
                .order_by(EvalItem.id)
                .with_for_update(of=EvalItem)
            )
            if brand_id is not None:
                statement = statement.where(EvalItem.brand_id == brand_id)
            items = list(session.scalars(statement))
            for item in items:
                item.status = "failed"
                item.last_error = JOB_ENDED_ERROR
                item.finished_at = now
            for run_id in sorted({item.run_id for item in items}):
                close_run_if_done(session, run_id)
            return len(items)


def close_run_if_done(session: Session, run_id: str) -> bool:
    """Com a corrida bloqueada, passa-a a `ready` se todos os itens sao terminais."""
    run = session.scalar(select(EvalRun).where(EvalRun.id == run_id).with_for_update())
    if run is None or run.status != "running":
        return False
    pending = session.scalar(
        select(EvalItem.id)
        .where(EvalItem.run_id == run_id, EvalItem.status.not_in(TERMINAL_ITEM_STATUSES))
        .limit(1)
    )
    if pending is not None:
        return False
    run.status = "ready"
    return True


def _fixed_brief(brief: EvalBrief, kb: dict[str, KbEntry]) -> FixedBrief:
    return FixedBrief(
        key=brief.key,
        tema=brief.tema,
        pilar=brief.pilar,
        objetivo=brief.objetivo,
        canais=tuple(brief.canais),
        kb_ids=tuple(kb[ref].id for ref in brief.kb_refs),
        tags=tuple(brief.tags),
    )
