"""Leitura consistente e limitada do trabalho da equipa; nenhuma chamada ou escrita."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased, sessionmaker
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery

from marketing_agent.clock import Clock
from marketing_agent.db.models import AgentRun, Artifact, AuditLog, Post
from marketing_agent.oficina.config import OficinaConfig
from marketing_agent.oficina.estado import (
    ESTADO_TEXTO,
    Entrega,
    EstadoAgente,
    EstadoEquipa,
    RevisaoSemanal,
    RoleSignals,
    TipoEntrega,
    derive_state,
    truncate_title,
)
from marketing_agent.oficina.papeis import PAPEIS, Papel
from marketing_agent.oficina.sql import JOB_ROLES, POST_JOBS, job_snapshot, latest_revision
from marketing_agent.planning.service import next_week
from marketing_agent.startup import content_job_lease

__all__ = ["JOB_ROLES", "TeamStateQueries", "PainelAgente", "FalhaPost"]
_ARTIFACTS: Final[Mapping[str, tuple[TipoEntrega, str, str]]] = {
    "perfil_marca": ("perfil", "Perfil de marca", "/perfil/"),
    "diagnostico": ("diagnostico", "Diagnóstico", "/diagnostico/"),
    "estrategia": ("estrategia", "Estratégia", "/estrategia/"),
    "plano": ("plano", "Plano semanal", "/plano/"),
}
_CONTENT: Final[Mapping[Papel, TipoEntrega]] = {
    "copywriter": "texto",
    "designer": "imagem",
    "revisor": "parecer",
}


@dataclass(frozen=True)
class FalhaPost:
    """Falha resumida para HTML autenticado, deliberadamente ausente do JSON publico."""

    post_id: str
    titulo: str
    last_error: str
    url: str


@dataclass(frozen=True)
class PainelAgente:
    """O mesmo estado publico mais as falhas que o responsavel pode consultar no lote."""

    agente: EstadoAgente
    falhas: tuple[FalhaPost, ...]


def _week(value: str | None) -> str | None:
    candidate = (value or "")[:8]
    if not re.fullmatch(r"\d{4}-W\d{2}", candidate):
        return None
    try:
        date.fromisocalendar(int(candidate[:4]), int(candidate[6:8]), 1)
    except ValueError:
        return None
    return candidate


def _post_url(slot: str, identifier: str, today: date) -> str:
    return f"/lote/{_week(slot) or next_week(today)}#post-{identifier}"


def _unresolved(jobs: Subquery) -> ColumnElement[bool]:
    return and_(
        jobs.c.type.in_(POST_JOBS),
        jobs.c.ordem_post == 1,
        jobs.c.status.in_(("failed", "dead")),
        jobs.c.lifecycle.not_in(("approved", "rejected", "cancelled")),
    )


class TeamStateQueries:
    """Snapshot com nove consultas fixas, qualquer que seja o numero de posts ou execucoes."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        clock: Clock,
        max_durations: Mapping[Papel, int],
        config: OficinaConfig,
        *,
        job_lease: timedelta | None = None,
    ) -> None:
        """Limites ja escalados pelo processo; lease injetada para respeitar o worker real."""
        self._factory = session_factory
        self._clock = clock
        self._max_s = dict(max_durations)
        self._config = config
        self._lease = job_lease if job_lease is not None else content_job_lease(time_multiplier=1)

    def team_state(self, brand_id: str) -> EstadoEquipa:
        """Le apenas metadados necessarios para a oficina, sem invocar qualquer servico ativo."""
        state, _ = self._read(brand_id)
        return state

    def agent_panel(self, brand_id: str, papel: Papel) -> PainelAgente:
        """Estado e ate cinco falhas do papel; os links apontam para os pacotes existentes."""
        state, failures = self._read(brand_id)
        agent = next(a for a in state.agentes if a.papel == papel)
        return PainelAgente(agent, failures.get(papel, ()))

    def _read(self, brand_id: str) -> tuple[EstadoEquipa, dict[Papel, tuple[FalhaPost, ...]]]:
        now = self._clock.now()
        with self._factory() as session:
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            runs = self._runs(session, brand_id)
            director_count, director_deliveries = self._director(session, brand_id)
            latest = latest_revision(brand_id)
            pending = self._pending(session, brand_id, latest, director_count)
            deliveries = {
                "diretor": director_deliveries,
                **{
                    role: self._deliveries(session, brand_id, latest, role, now.date())
                    for role in _CONTENT
                },
            }
            review = self._review(session, brand_id, now.date())
            jobs = job_snapshot(brand_id, now, self._lease)
            job_signals = self._job_signals(session, jobs, now)
            failures = self._failures(session, jobs, now.date())
            agents = tuple(
                self._agent(
                    p,
                    pending[p],
                    deliveries[p],
                    runs.get(p),
                    job_signals.get(p, (False, False, False)),
                    now,
                )
                for p in PAPEIS
            )
        return EstadoEquipa(agents, review), failures

    @staticmethod
    def _runs(session: Session, brand_id: str) -> dict[Papel, tuple[AgentRun, datetime]]:
        # So a execucao mais recente do papel conta: um orfao antigo nao persiste para sempre.
        ranked = (
            select(
                AgentRun,
                func.max(func.coalesce(AgentRun.ended_at, AgentRun.started_at))
                .over(partition_by=AgentRun.role)
                .label("atividade"),
                func.row_number()
                .over(
                    partition_by=AgentRun.role,
                    order_by=(AgentRun.started_at.desc(), AgentRun.id.desc()),
                )
                .label("ordem"),
            )
            .where(AgentRun.brand_id == brand_id, AgentRun.role.in_(PAPEIS))
            .subquery()
        )
        run = aliased(AgentRun, ranked)
        rows = session.execute(select(run, ranked.c.atividade).where(ranked.c.ordem == 1))
        return {cast(Papel, r.role): (r, activity) for r, activity in rows}

    @staticmethod
    def _director(session: Session, brand_id: str) -> tuple[int, tuple[Entrega, ...]]:
        # Artifact nao tem created_at: a primeira transicao ou o run dao recencia observavel.
        first_transition = (
            select(func.min(AuditLog.at))
            .where(
                AuditLog.subject_type == "artifact",
                AuditLog.subject_id == Artifact.id,
            )
            .correlate(Artifact)
            .scalar_subquery()
        )
        activity = func.coalesce(first_transition, AgentRun.ended_at, AgentRun.started_at)
        rows = session.execute(
            select(
                Artifact.id,
                Artifact.type,
                Artifact.payload["semana"].astext.label("semana"),
                func.count().filter(Artifact.state == "submitted").over().label("pendentes"),
            )
            .outerjoin(AgentRun, AgentRun.id == Artifact.produced_by_run_id)
            .where(Artifact.brand_id == brand_id, Artifact.type.in_(tuple(_ARTIFACTS)))
            .order_by(activity.desc().nulls_last(), Artifact.id.desc())
            .limit(5)
        ).all()
        deliveries = []
        for row in rows:
            kind, title, route = _ARTIFACTS[row.type]
            week = _week(row.semana) if kind == "plano" else None
            deliveries.append(
                Entrega(kind, row.id, f"{title} · {week}" if week else title, route + row.id)
            )
        return (int(rows[0].pendentes) if rows else 0), tuple(deliveries)

    @staticmethod
    def _pending(
        session: Session, brand_id: str, latest: Subquery, director: int
    ) -> dict[Papel, int]:
        waiting = Post.lifecycle == "in_review"
        row = session.execute(
            select(
                func.count().filter(waiting),
                func.count().filter(and_(waiting, latest.c.final_asset_id.is_not(None))),
                # NULL nao e um parecer devolvido pelo Revisor.
                func.count().filter(
                    and_(
                        waiting,
                        Post.review_rounds >= 2,
                        latest.c.review_verdict.in_(("rever", "rejeitar")),
                    )
                ),
            )
            .select_from(Post)
            .outerjoin(latest, latest.c.post_id == Post.id)
            .where(Post.brand_id == brand_id)
        ).one()
        return {"diretor": director, "copywriter": row[0], "designer": row[1], "revisor": row[2]}

    @staticmethod
    def _deliveries(
        session: Session, brand_id: str, latest: Subquery, papel: Papel, today: date
    ) -> tuple[Entrega, ...]:
        query = select(Post.id, Post.slot_key, Post.brief["tema"].astext.label("tema"))
        query = query.join(latest, latest.c.post_id == Post.id).where(Post.brand_id == brand_id)
        if papel == "designer":
            query = query.where(latest.c.final_asset_id.is_not(None))
        elif papel == "revisor":
            query = query.where(latest.c.review_verdict.is_not(None))
        rows = session.execute(query.order_by(latest.c.created_at.desc(), Post.id.desc()).limit(5))
        return tuple(
            Entrega(
                _CONTENT[papel],
                r.id,
                truncate_title(r.tema or "Publicação"),
                _post_url(r.slot_key, r.id, today),
            )
            for r in rows
        )

    @staticmethod
    def _review(session: Session, brand_id: str, today: date) -> RevisaoSemanal:
        week = func.substr(Post.slot_key, 1, 8)
        row = session.execute(
            select(week, func.count())
            .where(
                Post.brand_id == brand_id,
                Post.lifecycle == "in_review",
            )
            .group_by(week)
            .order_by(week)
            .limit(1)
        ).first()
        selected = _week(row[0]) if row else None
        selected = selected or next_week(today)
        return RevisaoSemanal(selected, int(row[1]) if row else 0, f"/lote/{selected}")

    @staticmethod
    def _job_signals(
        session: Session, jobs: Subquery, now: datetime
    ) -> dict[Papel, tuple[bool, bool, bool]]:
        running = jobs.c.status == "running"
        rows = session.execute(
            select(
                jobs.c.papel,
                func.bool_or(and_(running, jobs.c.locked_until > now)),
                func.bool_or(
                    and_(running, or_(jobs.c.locked_until <= now, jobs.c.locked_until.is_(None)))
                ),
                func.bool_or(_unresolved(jobs)),
            ).group_by(jobs.c.papel)
        )
        return {
            cast(Papel, role): (bool(live), bool(stale), bool(failed))
            for role, live, stale, failed in rows
        }

    @staticmethod
    def _failures(
        session: Session, jobs: Subquery, today: date
    ) -> dict[Papel, tuple[FalhaPost, ...]]:
        ranked = (
            select(
                jobs,
                func.row_number()
                .over(
                    partition_by=jobs.c.papel, order_by=(jobs.c.updated_at.desc(), jobs.c.id.desc())
                )
                .label("ordem_papel"),
            )
            .where(_unresolved(jobs))
            .subquery()
        )
        rows = session.execute(
            select(ranked)
            .where(ranked.c.ordem_papel <= 5)
            .order_by(ranked.c.papel, ranked.c.ordem_papel)
        )
        failures: dict[Papel, list[FalhaPost]] = {}
        for row in rows:
            failures.setdefault(cast(Papel, row.papel), []).append(
                FalhaPost(
                    row.post_id,
                    truncate_title(row.tema or "Publicação"),
                    truncate_title(row.erro, 300),
                    _post_url(row.slot_key, row.post_id, today),
                )
            )
        return {role: tuple(items) for role, items in failures.items()}

    def _agent(
        self,
        papel: Papel,
        pending: int,
        deliveries: tuple[Entrega, ...],
        run_data: tuple[AgentRun, datetime] | None,
        jobs: tuple[bool, bool, bool],
        now: datetime,
    ) -> EstadoAgente:
        run, activity = run_data if run_data else (None, None)
        running = run is not None and run.status == "running"
        age = (now - run.started_at).total_seconds() if run is not None else 0
        signals = RoleSignals(
            (running and age < self._max_s[papel]) or jobs[0],
            (run is not None and run.status in ("failed", "needs_human")) or jobs[2],
            (running and age >= self._max_s[papel]) or jobs[1],
            pending,
        )
        state = derive_state(signals)
        return EstadoAgente(
            papel,
            self._config.personagens[papel].nome,
            state,
            ESTADO_TEXTO[state],
            pending,
            activity,
            deliveries,
        )
