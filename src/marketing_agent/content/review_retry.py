"""Repetir a geracao falhada de uma publicacao: regras e escrita, na transacao do chamador.

O ciclo de vida do post nao muda: o pipeline aceita `planned` e `drafting` e passa a `drafting`
ao arrancar o trabalho, por isso basta limpar o motivo da falha e enfileirar um trabalho novo.
Um pedido de alteracoes que falhou e repetido com o mesmo comentario (esta no payload do trabalho
falhado); em qualquer outro caso a razao e `plan`.
"""

from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from marketing_agent.content.review_errors import InvalidPostState
from marketing_agent.content.review_records import audit_post
from marketing_agent.db.base import new_id
from marketing_agent.db.models import Post
from marketing_agent.jobs.queue import GENERATE_POST_JOB, JobQueue

RETRY_ACTION = "post.retry_generation"
_PLANNED = "planned"
_DRAFTING = "drafting"
Reason = Literal["plan", "changes"]


def retry_generation_in(
    session: Session, queue: JobQueue, post: Post, actor: str, now: datetime
) -> None:
    """Enfileira uma geracao nova do `post` (bloqueado pelo chamador) e audita.

    `InvalidPostState` se o post nao esta `planned`, nem `drafting` com `last_error`, ou se ja
    ha um `generate_post` ativo (assim dois cliques seguidos geram um so trabalho).
    """
    _require_retryable(session, queue, post)
    reason, comment = _previous_request(session, queue, post.id)
    payload = {"post_id": post.id, "owner_comment": comment, "reason": reason}
    # Chave nova a cada repeticao: o pedido de alteracoes original ja usou a sua.
    dedupe_key = f"{GENERATE_POST_JOB}:{post.id}:retry:{new_id()}"
    if queue.enqueue_in(session, GENERATE_POST_JOB, payload, dedupe_key) is None:
        raise InvalidPostState("ja existe um pedido de geracao para a publicacao")
    post.last_error = None
    audit_post(session, actor, RETRY_ACTION, post.id, {"reason": reason}, now)


def _require_retryable(session: Session, queue: JobQueue, post: Post) -> None:
    failed_drafting = post.lifecycle == _DRAFTING and post.last_error is not None
    if post.lifecycle != _PLANNED and not failed_drafting:
        raise InvalidPostState(f"a publicacao esta em '{post.lifecycle}'; acao nao permitida")
    if queue.has_active_for_post_in(session, post.id):
        raise InvalidPostState("ha uma geracao em curso para a publicacao")


def _previous_request(session: Session, queue: JobQueue, post_id: str) -> tuple[Reason, str | None]:
    """Razao e comentario do ultimo trabalho de geracao; so um comentario de texto e reutilizado."""
    previous = queue.latest_generate_payload_in(session, post_id)
    comment = previous.get("owner_comment")
    if previous.get("reason") == "changes" and isinstance(comment, str) and comment:
        return "changes", comment
    return "plan", None
