"""Revisao do responsavel: aprovar, editar, pedir alteracoes ou rejeitar uma publicacao.

Cada acao e UMA transacao que bloqueia o post primeiro (`lock_post`) e so depois toca em
trabalhos e revisoes: e a ordem do pipeline e da aprovacao do plano, a unica que evita impasses.
Dentro do bloqueio, a revisao sobre a qual se decide tem de ser a mais recente do post; e isso
que torna obsoletas as aprovacoes anteriores: uma aprovacao (ou pedido de alteracoes) so vale
para a revisao a que aponta, por isso uma edicao ou uma geracao nova nao precisa de a apagar.
Auditoria e erros levam atores, ids e contagens, nunca o comentario, a nota nem a legenda.
"""

from datetime import datetime
from typing import get_args

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import Clock
from marketing_agent.content.manual_image import ManualImage, normalize_manual
from marketing_agent.content.review_edit import CaptionEdit, build_edit_revision, check_edit
from marketing_agent.content.review_errors import (
    InvalidComment,
    InvalidEdit,
    InvalidPostState,
    PostNotFound,
    ReviewError,
    StaleRevision,
)
from marketing_agent.content.review_image import recompose_photo
from marketing_agent.content.review_records import (
    AuditDetail,
    Decision,
    audit_edit,
    audit_post,
    record_decision,
)
from marketing_agent.content.review_retry import retry_generation_in
from marketing_agent.content.revision_store import kb_ref_ids, latest_revision, lock_post
from marketing_agent.db.models import MediaAsset, Post, PostRevision
from marketing_agent.feedback.service import FeedbackChoice, FeedbackError, FeedbackService
from marketing_agent.jobs.queue import GENERATE_POST_JOB, REVIEW_POST_JOB, JobQueue
from marketing_agent.kb.service import KbService
from marketing_agent.media.brand_kit import BrandKitService
from marketing_agent.media.library import MediaLibrary

__all__ = [
    "CaptionEdit",
    "InvalidComment",
    "InvalidEdit",
    "InvalidPostState",
    "PostNotFound",
    "ReviewError",
    "ReviewService",
    "StaleRevision",
]

IN_REVIEW = "in_review"
DRAFTING = "drafting"
_EDITABLE = frozenset({IN_REVIEW, DRAFTING})
_FEEDBACK_CHOICES = frozenset(get_args(FeedbackChoice))


def _base_image_hash(session: Session, brand_id: str, base: PostRevision) -> str | None:
    """SHA-256 da imagem final da revisao base (entra no hash da edicao); `None` sem imagem."""
    if base.final_asset_id is None:
        return None
    return session.scalar(
        select(MediaAsset.sha256).where(
            MediaAsset.brand_id == brand_id, MediaAsset.id == base.final_asset_id
        )
    )


def _check_choice(choice: str) -> None:
    """Recusa um tipo de feedback fora do enum, com mensagem fixa (nunca o valor recebido).

    O `Literal` so protege o tipo estatico; em runtime um valor invalido bateria no CHECK da
    base e o `IntegrityError` podia embutir texto do responsavel.
    """
    if choice not in _FEEDBACK_CHOICES:
        raise FeedbackError("feedback: tipo invalido")


class ReviewService:
    """Decisoes do responsavel sobre a revisao mais recente de uma publicacao."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        feedback: FeedbackService,
        queue: JobQueue,
        kb: KbService,
        clock: Clock,
        *,
        max_comment_chars: int,
        library: MediaLibrary | None = None,
        kits: BrandKitService | None = None,
    ) -> None:
        """`max_comment_chars` (`settings.max_message_chars`) limita o pedido de alteracoes."""
        self._session_factory = session_factory
        self._feedback = feedback
        self._queue = queue
        self._kb = kb
        self._clock = clock
        self._max_comment_chars = max_comment_chars
        self._library = library
        self._kits = kits

    def approve(self, brand_id: str, post_id: str, revision_id: str, actor: str) -> None:
        """Aprova a revisao (tem de ser a mais recente) e cancela trabalhos de geracao ativos.

        `PostNotFound`, `InvalidPostState` (nao esta `in_review`) ou `StaleRevision`.
        """
        with self._session_factory.begin() as session:
            post, revision = self._open(session, brand_id, post_id, revision_id)
            self._queue.cancel_for_post_in(session, post_id)
            self._decide(session, post, revision, "approved", actor)
            post.lifecycle = "approved"

    def reject(
        self,
        brand_id: str,
        post_id: str,
        revision_id: str,
        feedback: FeedbackChoice,
        note: str | None,
        actor: str,
    ) -> None:
        """Rejeita a revisao; o feedback guarda a legenda rejeitada como `before_text`.

        Erros de `approve` e `FeedbackError` (tipo ou nota invalidos: nada e gravado).
        """
        _check_choice(feedback)
        with self._session_factory.begin() as session:
            post, revision = self._open(session, brand_id, post_id, revision_id)
            self._queue.cancel_for_post_in(session, post_id)
            self._record_feedback(
                session,
                revision,
                feedback,
                before_text=revision.caption,
                after_text=None,
                note=note,
                expires_at=None,
                actor=actor,
            )
            self._decide(session, post, revision, "rejected", actor, {"feedback": feedback})
            post.lifecycle = "rejected"

    def request_changes(
        self, brand_id: str, post_id: str, revision_id: str, comment: str, actor: str
    ) -> None:
        """Poe o post a `drafting` e enfileira uma geracao nova com o comentario.

        O comentario perde os espacos das pontas e tem de ficar com 1..`max_comment_chars`
        caracteres (`InvalidComment`). Recusa (`InvalidPostState`) se ja ha um trabalho de
        geracao ativo, para nunca haver duas geracoes sobre o mesmo post.
        """
        text = self._clean_comment(comment)
        with self._session_factory.begin() as session:
            post, revision = self._open(session, brand_id, post_id, revision_id)
            if self._queue.has_active_for_post_in(session, post_id):
                raise InvalidPostState("ha uma geracao em curso para a publicacao")
            payload = {"post_id": post_id, "owner_comment": text, "reason": "changes"}
            dedupe_key = f"{GENERATE_POST_JOB}:{post_id}:changes:{revision_id}"
            if self._queue.enqueue_in(session, GENERATE_POST_JOB, payload, dedupe_key) is None:
                raise InvalidPostState("ja existe um pedido de alteracoes para esta revisao")
            post.lifecycle = DRAFTING
            post.last_error = None
            self._decide(
                session,
                post,
                revision,
                "changes_requested",
                actor,
                {"comment_chars": len(text)},
                text,
            )

    def edit(
        self,
        brand_id: str,
        post_id: str,
        base_revision_id: str,
        changes: CaptionEdit,
        feedback: FeedbackChoice,
        note: str | None,
        expires_at: datetime | None,
        actor: str,
    ) -> str:
        """Cria a revisao `edicao_responsavel` a partir da mais recente e devolve o seu id.

        O post fica `in_review`. Aceita-se `in_review` e tambem `drafting` com `last_error` e
        sem trabalho ativo (geracao falhada que o responsavel corrige a mao); sem revisao base
        nao ha o que editar (`StaleRevision`). Um texto igual ao da base e recusado
        (`InvalidEdit`), tal como violacoes de limites, hashtags, alt_text, alegacoes e preco.
        O trabalho de geracao ativo e cancelado: nao pode escrever por cima da edicao.
        """
        _check_choice(feedback)
        with self._session_factory.begin() as session:
            post, base = self._open(session, brand_id, post_id, base_revision_id, _EDITABLE)
            self._require_editable(session, post)
            valid_kb = self._kb.valid_refs(brand_id, kb_ref_ids(post.brief), post.scheduled_date)
            image_hash = _base_image_hash(session, brand_id, base)
            check_edit(base, changes, post.channels, valid_kb, image_hash)
            self._queue.cancel_for_post_in(session, post_id)
            revision = build_edit_revision(post, base, changes, self._clock.now(), image_hash)
            session.add(revision)
            session.flush()
            post.lifecycle = IN_REVIEW
            post.last_error = None
            self._record_edit(session, base, revision, changes, feedback, note, expires_at, actor)
            return revision.id

    def _record_edit(
        self,
        session: Session,
        base: PostRevision,
        revision: PostRevision,
        changes: CaptionEdit,
        feedback: FeedbackChoice,
        note: str | None,
        expires_at: datetime | None,
        actor: str,
    ) -> None:
        """Feedback e auditoria da edicao, na transacao do chamador."""
        self._record_feedback(
            session,
            revision,
            feedback,
            before_text=base.caption,
            after_text=changes.legenda,
            note=note,
            expires_at=expires_at,
            actor=actor,
        )
        audit_edit(
            session,
            actor=actor,
            base=base,
            revision=revision,
            feedback=feedback,
            now=self._clock.now(),
        )

    def attach_manual_image(
        self,
        brand_id: str,
        post_id: str,
        base_revision_id: str,
        data: bytes,
        meta: ManualImage,
        actor: str,
    ) -> str:
        """Guarda a imagem e enfileira uma revisao visual atomica; exige a base atual.

        So `in_review`, ou `drafting` com `last_error` e sem trabalho ativo (como `edit`):
        publicacoes aprovadas, rejeitadas ou canceladas nao se reabrem (`InvalidPostState`).
        """
        if self._library is None:
            raise InvalidEdit("servicos de imagem indisponiveis", ())
        image = normalize_manual(data)
        with self._session_factory.begin() as session:
            post, base = self._open(session, brand_id, post_id, base_revision_id, _EDITABLE)
            self._require_editable(session, post)
            if self._queue.has_active_for_post_in(session, post_id):
                raise InvalidPostState("ha uma geracao em curso")
            asset_id = self._library.store_asset_in(
                session,
                brand_id,
                image,
                kind="final",
                origem="gerada" if meta.gerada_por_ia else meta.origem,
                actor=actor,
            )
            job_id = self._enqueue_manual_review(session, post_id, base, asset_id, meta)
            post.lifecycle, post.last_error = DRAFTING, None
            detail = {"base_revision_id": base.id, "asset_id": asset_id}
            audit_post(session, actor, "post.manual_image", post_id, detail, self._clock.now())
            return job_id

    def _enqueue_manual_review(
        self, session: Session, post_id: str, base: PostRevision, asset_id: str, meta: ManualImage
    ) -> str:
        """Enfileira o `review_post` do upload; `InvalidPostState` se ja ha um igual ativo."""
        payload = {
            "post_id": post_id,
            "base_revision_id": base.id,
            "asset_id": asset_id,
            "meta": meta.model_dump(mode="json"),
        }
        dedupe_key = f"{REVIEW_POST_JOB}:{post_id}:{asset_id}"
        job_id = self._queue.enqueue_in(session, REVIEW_POST_JOB, payload, dedupe_key)
        if job_id is None:
            raise InvalidPostState("revisao visual ja enfileirada")
        return job_id

    def swap_photo(
        self,
        brand_id: str,
        post_id: str,
        base_revision_id: str,
        photo_id: str,
        alt_text: str,
        actor: str,
    ) -> str:
        """Troca a foto da revisao mais recente; sem LLM e com nova aprovacao obrigatoria.

        Mesmas condicoes de `attach_manual_image`: so `in_review` ou `drafting` falhado sem
        trabalho ativo. Trocar para a mesma foto com o mesmo texto e recusado (`InvalidEdit`).
        """
        if self._library is None or self._kits is None:
            raise InvalidEdit("servicos de imagem indisponiveis", ())
        with self._session_factory.begin() as session:
            post, base = self._open(session, brand_id, post_id, base_revision_id, _EDITABLE)
            self._require_editable(session, post)
            revision = recompose_photo(
                session,
                post,
                base,
                photo_id,
                alt_text,
                actor,
                self._clock.now(),
                self._library,
                self._kits,
            )
            self._queue.cancel_for_post_in(session, post_id)
            session.add(revision)
            session.flush()
            post.lifecycle, post.last_error = IN_REVIEW, None
            # Ids e nunca o texto alternativo: a auditoria nao leva texto do responsavel.
            detail = {"revision_id": revision.id, "base_revision_id": base.id, "photo_id": photo_id}
            audit_post(session, actor, "post.swap_photo", post_id, detail, self._clock.now())
            return revision.id

    def retry_generation(self, brand_id: str, post_id: str, actor: str) -> None:
        """Repete a geracao falhada (ou perdida) de uma publicacao, num so trabalho novo.

        `PostNotFound` ou `InvalidPostState` (ver `retry_generation_in`); nada e gravado se recusa.
        """
        with self._session_factory.begin() as session:
            post = lock_post(session, post_id)
            if post is None or post.brand_id != brand_id:
                raise PostNotFound
            retry_generation_in(session, self._queue, post, actor, self._clock.now())

    def _open(
        self,
        session: Session,
        brand_id: str,
        post_id: str,
        revision_id: str,
        states: frozenset[str] = frozenset({IN_REVIEW}),
    ) -> tuple[Post, PostRevision]:
        """Bloqueia o post e devolve-o com a revisao mais recente, que tem de ser `revision_id`."""
        post = lock_post(session, post_id)
        if post is None or post.brand_id != brand_id:
            raise PostNotFound
        if post.lifecycle not in states:
            raise InvalidPostState(f"a publicacao esta em '{post.lifecycle}'; acao nao permitida")
        latest = latest_revision(session, post_id)
        if latest is None or latest.id != revision_id:
            raise StaleRevision
        return post, latest

    def _require_editable(self, session: Session, post: Post) -> None:
        """Em `drafting` so se edita se a geracao falhou (`last_error`) e nada a vai refazer."""
        if post.lifecycle != DRAFTING:
            return
        if post.last_error is None or self._queue.has_active_for_post_in(session, post.id):
            raise InvalidPostState("a publicacao esta em geracao; aguarde ou peca alteracoes")

    def _clean_comment(self, comment: str) -> str:
        text = comment.strip()
        if not 1 <= len(text) <= self._max_comment_chars:
            raise InvalidComment(
                f"o comentario deve ter entre 1 e {self._max_comment_chars} caracteres"
            )
        return text

    def _record_feedback(
        self,
        session: Session,
        revision: PostRevision,
        choice: FeedbackChoice,
        *,
        before_text: str,
        after_text: str | None,
        note: str | None,
        expires_at: datetime | None,
        actor: str,
    ) -> None:
        """Feedback na transacao da decisao, ligado a `revision` (`nao_usar` nao grava nada)."""
        self._feedback.record_in(
            session,
            brand_id=revision.brand_id,
            post_id=revision.post_id,
            revision_id=revision.id,
            choice=choice,
            before_text=before_text,
            after_text=after_text,
            note=note,
            expires_at=expires_at,
            actor=actor,
        )

    def _decide(
        self,
        session: Session,
        post: Post,
        revision: PostRevision,
        decision: Decision,
        actor: str,
        extra: AuditDetail | None = None,
        comment: str | None = None,
    ) -> None:
        record_decision(
            session,
            post=post,
            revision=revision,
            decision=decision,
            actor=actor,
            now=self._clock.now(),
            extra=extra,
            comment=comment,
        )
