"""Pipeline de geracao de um post: Copywriter -> Revisor (ate 2 voltas) -> `post_revision`.

Um job `generate_post` demora minutos (ate 3 passagens Copywriter+Revisor) e nenhum lock e
mantido enquanto o modelo trabalha. So a gravacao final e transacional: bloqueia o post, verifica
que nada mudou (`content.revision_store`) e grava a revisao, o estado do post e a conclusao do
job na MESMA transacao. Ordem dos locks: post antes do job, como a aprovacao do plano.

Mensagens de falha: so `describe_error` chega a `job.last_error` e `post.last_error`; o texto de
`RunResult.message` (que pode citar o modelo) nunca e copiado.
"""

import logging
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore, ArtifactType
from marketing_agent.clock import Clock
from marketing_agent.content.loops import CopyPayload
from marketing_agent.content.revision_store import (
    DRAFTING,
    GeneratedText,
    PostSnapshot,
    lock_post,
    save_generated,
    stale_reason,
    take_snapshot,
)
from marketing_agent.content.rounds import ReviewRounds, describe_stop
from marketing_agent.content.specs import load_content_specs
from marketing_agent.db.models import Post
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.harness.engine import AgentEngine, RunResult
from marketing_agent.jobs.errors import describe_error
from marketing_agent.jobs.queue import ClaimedJob, JobQueue
from marketing_agent.kb.service import KbService
from marketing_agent.proposals import MissingPrerequisite, require_active
from marketing_agent.strategy.service import StrategyService

logger = logging.getLogger(__name__)

GenerationOutcome = Literal["in_review", "drafting_failed", "stale"]
_GENERATABLE = frozenset({"planned", DRAFTING})


class _LeaseLost(Exception):
    """Interna: o worker perdeu a lease, por isso a transacao de gravacao e desfeita."""


class GenerationRequest(BaseModel):
    """Payload de um job `generate_post` (ruling R1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    post_id: str = Field(min_length=1)
    owner_comment: str | None
    reason: Literal["plan", "changes"]


def _kb_ref_ids(brief: Mapping[str, object]) -> list[str]:
    refs = brief.get("kb_refs")
    return [ref for ref in refs if isinstance(ref, str)] if isinstance(refs, list) else []


class ContentPipeline:
    """Gera a `post_revision` de um post a partir de um job `generate_post`."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: AgentEngine,
        kb: KbService,
        feedback: FeedbackService,
        queue: JobQueue,
        clock: Clock,
        store: ArtifactStore,
        strategy: StrategyService,
    ) -> None:
        """Guarda os colaboradores; `store` e `strategy` dao o perfil e as propostas aceites."""
        self._session_factory = session_factory
        self._kb = kb
        self._feedback = feedback
        self._queue = queue
        self._clock = clock
        self._store = store
        self._strategy = strategy
        self._rounds = ReviewRounds(engine, load_content_specs())

    def generate(self, job: ClaimedJob) -> GenerationOutcome:
        """Corre o job e fecha-o (conclui ou falha); erros inesperados sobem ao worker.

        `in_review`: revisao gravada. `stale`: nada foi gravado (post cancelado, ja decidido,
        alterado durante a geracao, ou lease perdida) e o job foi concluido sem escrever.
        `drafting_failed`: `needs_human`, limite, pre-requisito em falta ou payload invalido;
        o job falhou sem nova tentativa e, se foi possivel, o post fica `drafting` com o motivo.
        """
        try:
            request = GenerationRequest.model_validate(job.payload)
        except ValidationError as exc:
            return self._fail(job, None, describe_error(exc))
        started = self._begin(job, request)
        if not isinstance(started, PostSnapshot):
            return started
        try:
            payload = self._load_payload(started)
        except MissingPrerequisite as exc:
            return self._fail(job, started, describe_error(exc))
        result = self._rounds.run(started.brand_id, payload, request.owner_comment)
        if isinstance(result, RunResult):
            return self._fail(job, started, describe_stop(result))
        return self._save(job, started, result)

    def _begin(
        self, job: ClaimedJob, request: GenerationRequest
    ) -> PostSnapshot | Literal["stale"]:
        """Bloqueia o post, decide se ha trabalho a fazer e passa-o a `drafting`."""
        with self._session_factory.begin() as session:
            post = lock_post(session, request.post_id)
            if post is None or not self._can_generate(post, request.reason):
                self._queue.complete_in(session, job)
                return "stale"
            post.lifecycle = DRAFTING
            post.last_error = None
            return take_snapshot(session, post)

    @staticmethod
    def _can_generate(post: Post, reason: str) -> bool:
        """`planned`/`drafting` geram; `in_review` so a pedido de alteracoes do responsavel."""
        return post.lifecycle in _GENERATABLE or (
            post.lifecycle == "in_review" and reason == "changes"
        )

    def _load_payload(self, snapshot: PostSnapshot) -> CopyPayload:
        brand_id = snapshot.brand_id
        profile = require_active(self._store, brand_id, ArtifactType.PERFIL_MARCA, "geracao")
        active = self._strategy.active_strategy(brand_id)
        if active is None:
            raise MissingPrerequisite(
                f"geracao: falta uma estrategia ativa para a marca {brand_id}"
            )
        kb_entries = self._kb.valid_refs(
            brand_id, _kb_ref_ids(snapshot.brief), snapshot.scheduled_date
        )
        styles, negatives = self._feedback.prompt_examples(brand_id)
        return CopyPayload(
            brief=snapshot.brief,
            channels=snapshot.channels,
            perfil_marca=profile.payload,
            pilares=active.estrategia.pilares,
            accepted_proposals=active.propostas_aceites,
            kb_entries=kb_entries,
            style_examples=styles,
            negative_examples=negatives,
            reviewer_notes=None,
            owner_comment=None,
        )

    def _save(
        self, job: ClaimedJob, snapshot: PostSnapshot, generated: GeneratedText
    ) -> GenerationOutcome:
        """Grava revisao, estado do post e conclusao do job numa so transacao (post primeiro)."""
        try:
            with self._session_factory.begin() as session:
                post = lock_post(session, snapshot.post_id)
                if post is None or stale_reason(session, post, snapshot) is not None:
                    self._queue.complete_in(session, job)
                    return "stale"
                save_generated(session, post, generated, snapshot, self._clock.now())
                if not self._queue.complete_in(session, job):
                    raise _LeaseLost  # desfaz a revisao: este worker ja nao e o dono do job
                return "in_review"
        except _LeaseLost:
            logger.warning("job %s: lease perdida antes de gravar; nada foi gravado", job.id)
            return "stale"

    def _fail(
        self, job: ClaimedJob, snapshot: PostSnapshot | None, message: str
    ) -> GenerationOutcome:
        """Falha o job sem nova tentativa; so com a lease valida e que o post recebe o motivo."""
        if not self._queue.fail(job, message, retry=False):
            return "stale"
        if snapshot is not None:
            self._record_post_error(snapshot, message)
        return "drafting_failed"

    def _record_post_error(self, snapshot: PostSnapshot, message: str) -> None:
        with self._session_factory.begin() as session:
            post = lock_post(session, snapshot.post_id)
            if post is not None and stale_reason(session, post, snapshot) is None:
                post.last_error = message
