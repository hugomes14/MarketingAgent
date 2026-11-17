"""Projecoes SQL da oficina: revisao atual e atribuicao de jobs sem N+1."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery

from marketing_agent.db.models import AgentRun, EvalRun, Job, Post, PostRevision
from marketing_agent.jobs.queue import EVAL_PACKAGE_JOB, GENERATE_POST_JOB, REVIEW_POST_JOB
from marketing_agent.oficina.papeis import Papel

JOB_ROLES: Final[Mapping[str, tuple[Papel, ...]]] = {
    GENERATE_POST_JOB: ("copywriter", "designer", "revisor"),
    EVAL_PACKAGE_JOB: ("copywriter", "designer", "revisor"),
    REVIEW_POST_JOB: ("revisor",),
}
POST_JOBS = (GENERATE_POST_JOB, REVIEW_POST_JOB)


def latest_revision(brand_id: str) -> Subquery:
    """Uma so revisao por post, sem ler legendas, notas ou outros conteudos privados."""
    return (
        select(
            PostRevision.id,
            PostRevision.post_id,
            PostRevision.final_asset_id,
            PostRevision.review_verdict,
            PostRevision.created_at,
        )
        .where(PostRevision.brand_id == brand_id)
        .ext(distinct_on(PostRevision.post_id))
        .order_by(PostRevision.post_id, PostRevision.rev_no.desc())
        .subquery("oficina_revisao_atual")
    )


def _job_role(brand_id: str, now: datetime, lease: timedelta) -> ColumnElement[str]:
    start = case(
        (Job.status == "running", func.greatest(Job.updated_at, Job.locked_until - lease)),
        else_=Job.created_at,
    )
    end = case((Job.status == "running", now), else_=Job.updated_at)
    eligible_role = or_(
        and_(Job.type == REVIEW_POST_JOB, AgentRun.role == "revisor"),
        and_(
            Job.type.in_((GENERATE_POST_JOB, EVAL_PACKAGE_JOB)),
            AgentRun.role.in_(("copywriter", "designer", "revisor")),
        ),
    )
    failed_first = case(
        (
            and_(
                Job.status.in_(("failed", "dead")), AgentRun.status.in_(("failed", "needs_human"))
            ),
            1,
        ),
        else_=0,
    )
    role = (
        select(AgentRun.role)
        .where(
            AgentRun.brand_id == brand_id, eligible_role, AgentRun.started_at.between(start, end)
        )
        .order_by(failed_first.desc(), AgentRun.started_at.desc(), AgentRun.id.desc())
        .limit(1)
        .correlate(Job)
        .scalar_subquery()
    )
    fallback = case((Job.type == REVIEW_POST_JOB, "revisor"), else_="copywriter")
    return func.coalesce(role, fallback)


def job_snapshot(brand_id: str, now: datetime, lease: timedelta) -> Subquery:
    """Jobs da marca com papel inferido pela ultima execucao na janela da tentativa.

    Depende do worker unico imposto por PostgreSQL: numa janela so ha um pipeline ativo.
    `claim` legado nao atualiza updated_at; a lease da a fronteira inferior real. Um job de
    avaliacao nunca cria uma falha de post, mesmo que o payload esteja malformado.
    """
    return (
        select(
            Job.id,
            Job.type,
            Job.status,
            Job.locked_until,
            Job.updated_at,
            _job_role(brand_id, now, lease).label("papel"),
            Post.id.label("post_id"),
            Post.lifecycle,
            Post.brief["tema"].astext.label("tema"),
            Post.slot_key,
            func.coalesce(Post.last_error, Job.last_error, "Falha no trabalho").label("erro"),
            func.row_number()
            .over(
                partition_by=Post.id,
                order_by=(Job.created_at.desc(), Job.updated_at.desc(), Job.id.desc()),
            )
            .label("ordem_post"),
        )
        .outerjoin(Post, and_(Job.type.in_(POST_JOBS), Post.id == Job.payload["post_id"].astext))
        .outerjoin(
            EvalRun,
            and_(Job.type == EVAL_PACKAGE_JOB, EvalRun.id == Job.payload["eval_run_id"].astext),
        )
        .where(
            Job.type.in_(tuple(JOB_ROLES)),
            or_(Post.brand_id == brand_id, EvalRun.brand_id == brand_id),
        )
        .subquery("oficina_jobs")
    )
