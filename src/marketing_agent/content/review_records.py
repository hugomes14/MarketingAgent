"""Registos de uma decisao do responsavel: a `Approval` e a linha de auditoria.

Funcoes sem estado, na sessao (e transacao) do chamador. A auditoria leva ator, acao, ids e
contagens; o comentario so fica em `approval.comment`, nunca em `audit_log`.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from marketing_agent.db.models import Approval, AuditLog, Post, PostRevision

Decision = Literal["approved", "rejected", "changes_requested"]
AuditDetail = Mapping[str, str | int]
_ACTIONS: Mapping[Decision, str] = {
    "approved": "post.approve",
    "rejected": "post.reject",
    "changes_requested": "post.request_changes",
}


def record_decision(
    session: Session,
    *,
    post: Post,
    revision: PostRevision,
    decision: Decision,
    actor: str,
    now: datetime,
    extra: AuditDetail | None = None,
    comment: str | None = None,
) -> None:
    """Grava a `Approval` (com o hash da revisao decidida) e a auditoria da decisao."""
    session.add(
        Approval(
            brand_id=post.brand_id,
            post_id=post.id,
            revision_id=revision.id,
            revision_hash=revision.revision_hash,
            decision=decision,
            comment=comment,
            decided_by=actor,
            decided_at=now,
        )
    )
    detail: dict[str, str | int] = {
        "revision_id": revision.id,
        "rev_no": revision.rev_no,
        **(extra or {}),
    }
    audit_post(session, actor, _ACTIONS[decision], post.id, detail, now)


def audit_edit(
    session: Session,
    *,
    actor: str,
    base: PostRevision,
    revision: PostRevision,
    feedback: str,
    now: datetime,
) -> None:
    """Auditoria de uma edicao: a revisao base, a nova e o tipo de feedback."""
    detail: dict[str, str | int] = {
        "base_revision_id": base.id,
        "revision_id": revision.id,
        "rev_no": revision.rev_no,
        "feedback": feedback,
    }
    audit_post(session, actor, "post.edit", revision.post_id, detail, now)


def audit_post(
    session: Session, actor: str, action: str, post_id: str, detail: AuditDetail, now: datetime
) -> None:
    """Linha de auditoria sobre um post."""
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            subject_type="post",
            subject_id=post_id,
            detail=dict(detail),
            at=now,
        )
    )
