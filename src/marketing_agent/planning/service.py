"""Servico do plano semanal: propor, aprovar (criando posts e trabalhos) e rejeitar.

Como no servico de estrategia, a chamada ao motor corre fora de qualquer transacao e nenhuma
mudanca de estado depende da saida do LLM: o motor so cria um `draft`, o servico submete-o e
so `approve_plan` / `reject_plan` decidem. Aprovar e uma unica transacao: ativar o plano da
semana, reconciliar os posts por `slot_key`, enfileirar os trabalhos e auditar; qualquer falha
reverte tudo e o plano continua `submitted`.
"""

from collections.abc import Mapping
from datetime import date, timedelta

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.agents.diretor import DIRETOR_DIR
from marketing_agent.artifacts.store import ArtifactStore, ArtifactType
from marketing_agent.clock import Clock
from marketing_agent.db.models import Artifact, AuditLog
from marketing_agent.harness.engine import AgentEngine, RunResult
from marketing_agent.harness.spec import load_agent_spec
from marketing_agent.jobs.queue import JobQueue
from marketing_agent.kb.service import KbEntry, KbService
from marketing_agent.planning.loop import PlanLoop, PlanPayload
from marketing_agent.planning.posts import PostReconciler
from marketing_agent.planning.schemas import PlanoSemanal
from marketing_agent.planning.validation import PlanLimits, week_range
from marketing_agent.proposals import (
    MissingPrerequisite,
    ProposalRunner,
    require_active,
    require_type,
)
from marketing_agent.strategy.service import ActiveStrategy, StrategyService

PLAN_SCHEMAS: Mapping[str, type[BaseModel]] = {"PlanoSemanal": PlanoSemanal}


def next_week(today: date) -> str:
    """Semana ISO (`AAAA-Www`) a seguir a que contem `today`."""
    year, week, _ = (today + timedelta(days=7)).isocalendar()
    return f"{year}-W{week:02d}"


def _week_scope(week: str) -> str:
    return f"week:{week}"


class PlanningService:
    """Propoe o plano semanal e regista a decisao do responsavel."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: AgentEngine,
        store: ArtifactStore,
        strategy: StrategyService,
        kb: KbService,
        queue: JobQueue,
        clock: Clock,
        limits: PlanLimits,
    ) -> None:
        """Guarda os colaboradores, os tetos do plano e carrega o spec do plano."""
        self._session_factory = session_factory
        self._store = store
        self._strategy = strategy
        self._kb = kb
        self._clock = clock
        self._limits = limits
        self._runner = ProposalRunner(session_factory, engine, store, clock)
        self._reconciler = PostReconciler(queue, clock)
        self._spec = load_agent_spec(DIRETOR_DIR / "spec_plano.yaml", PLAN_SCHEMAS)

    def propose_plan(self, brand_id: str, week: str | None = None) -> RunResult:
        """Gera um plano `submitted` para `week` (por omissao, a semana seguinte).

        Exige `perfil_marca` e estrategia ativos; recusa com `PendingProposal` se a semana ja
        tem um plano `submitted`. `ValueError` se `week` nao e uma semana ISO valida.
        """
        semana = week if week is not None else next_week(self._clock.now().date())
        week_range(semana)
        payload = self._build_payload(brand_id, semana)
        return self._runner.propose(
            brand_id,
            ArtifactType.PLANO,
            self._spec,
            "plan.weekly",
            PlanLoop(),
            payload,
            scope=semana,
            only_pending_where=[Artifact.payload["semana"].astext == semana],
        )

    def approve_plan(self, artifact_id: str, actor: str) -> list[str]:
        """Aprova o plano e cria/atualiza os posts da semana e os seus trabalhos, numa transacao.

        Devolve os ids dos posts do plano, por ordem de slot (criados, atualizados ou mantidos).
        Um plano que ja nao esta `submitted` levanta `InvalidTransition` sem alterar nada.
        """
        record = self._store.get(artifact_id)
        require_type(record, ArtifactType.PLANO)
        plan = PlanoSemanal.model_validate(record.payload)
        strategy_id = self._require_strategy(record.brand_id).artifact_id
        with self._session_factory.begin() as session:
            self._store.approve_and_activate_in(
                session, artifact_id, actor, scope=_week_scope(plan.semana)
            )
            result = self._reconciler.apply(session, record, plan, strategy_id)
            session.add(
                AuditLog(
                    actor=actor,
                    action="plan.approve",
                    subject_type="artifact",
                    subject_id=artifact_id,
                    detail={
                        "semana": plan.semana,
                        "posts": len(result.post_ids),
                        "criados": result.created,
                        "atualizados": result.updated,
                        "mantidos": result.kept,
                        "cancelados": result.cancelled,
                        "jobs": result.jobs,
                    },
                    at=self._clock.now(),
                )
            )
        return result.post_ids

    def reject_plan(self, artifact_id: str, actor: str, reason: str) -> None:
        """Rejeita o plano submetido; nao cria posts nem trabalhos e o plano ativo nao muda."""
        require_type(self._store.get(artifact_id), ArtifactType.PLANO)
        self._store.reject(artifact_id, actor, reason)

    def _require_strategy(self, brand_id: str) -> ActiveStrategy:
        active = self._strategy.active_strategy(brand_id)
        if active is None:
            raise MissingPrerequisite(
                f"plano: falta um artefacto estrategia ativo para a marca {brand_id}"
            )
        return active

    def _build_payload(self, brand_id: str, week: str) -> PlanPayload:
        profile = require_active(self._store, brand_id, ArtifactType.PERFIL_MARCA, "plano")
        strategy = self._require_strategy(brand_id)
        parent = self._store.active(brand_id, ArtifactType.PLANO, scope=_week_scope(week))
        return PlanPayload(
            week=week,
            limits=self._limits,
            perfil_marca=profile.payload,
            estrategia=strategy.estrategia,
            accepted_proposals=strategy.propostas_aceites,
            kb_entries=self._kb_valid_all_week(brand_id, week),
            parent_id=parent.id if parent else None,
        )

    def _kb_valid_all_week(self, brand_id: str, week: str) -> list[KbEntry]:
        """Itens da base ativos e validos em TODOS os dias da semana (interseccao dos 7 dias)."""
        first_day, _ = week_range(week)
        days = [first_day + timedelta(days=offset) for offset in range(7)]
        per_day = [self._kb.list_active(brand_id, on=day) for day in days]
        valid_every_day = set.intersection(*({entry.id for entry in day} for day in per_day))
        return [entry for entry in per_day[0] if entry.id in valid_every_day]
