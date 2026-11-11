"""Pipeline de geracao de um post: Copywriter -> Designer -> Revisor (ate 2 voltas) -> revisao.

Um job `generate_post` demora minutos (ate 3 passagens do pacote texto e imagem) e nenhum lock
e mantido enquanto o modelo trabalha. So a gravacao final e transacional: bloqueia o post,
verifica que nada mudou (`content.revision_store`) e grava os ativos de imagem, a revisao, o
estado do post e a conclusao do job na MESMA transacao. Ordem dos locks: post antes do job,
como a aprovacao do plano.

Falha da parte de imagem (Designer sem imagem, falta de kit, composicao): grava-se a revisao so
de texto (sem parecer nem imagem), o post fica `drafting` com `last_error = "imagem: <razao>"`
e o job falha sem nova tentativa, tudo na mesma transacao. A quota de imagens de cada tentativa
vem da base (`content.package_costs`), para um job reclamado nunca faturar mais de 2 imagens.

Mensagens de falha: so `describe_error` chega a `job.last_error` e `post.last_error`; o texto de
`RunResult.message` (que pode citar o modelo) nunca e copiado.
"""

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore, ArtifactType
from marketing_agent.clock import Clock
from marketing_agent.content.image_failure import image_error
from marketing_agent.content.loops import CopyPayload
from marketing_agent.content.package_assets import store_package_image
from marketing_agent.content.package_costs import job_costs_in, remaining_images
from marketing_agent.content.package_media import DesignMedia
from marketing_agent.content.revision_store import (
    DRAFTING,
    PostSnapshot,
    RevisionCosts,
    kb_ref_ids,
    lock_post,
    save_generated,
    stale_reason,
    take_snapshot,
)
from marketing_agent.content.rounds import (
    GeneratedPackage,
    ImageFailure,
    PackageRounds,
    describe_stop,
)
from marketing_agent.content.specs import load_content_specs
from marketing_agent.db.models import Post
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.harness.engine import AgentEngine, RunResult
from marketing_agent.harness.image_calls import ImageAllowance
from marketing_agent.images.generation import ImageQuality
from marketing_agent.jobs.errors import describe_error
from marketing_agent.jobs.queue import ClaimedJob, JobFailure, JobQueue
from marketing_agent.kb.service import KbService
from marketing_agent.media.brand_kit import BrandKitService
from marketing_agent.media.library import MediaLibrary
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


class ContentPipeline:
    """Gera a `post_revision` (texto e imagem) de um post a partir de um job `generate_post`."""

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
        library: MediaLibrary,
        kits: BrandKitService,
        image_quality: ImageQuality,
    ) -> None:
        """Guarda os colaboradores; `store` e `strategy` dao o perfil e as propostas aceites.

        `library` e `kits` dao ao Designer as fotos e o kit de marca e guardam os ativos;
        `image_quality` e a qualidade dos fundos gerados.
        """
        self._session_factory = session_factory
        self._kb = kb
        self._feedback = feedback
        self._queue = queue
        self._clock = clock
        self._store = store
        self._strategy = strategy
        self._library = library
        self._rounds = PackageRounds(
            engine, load_content_specs(), DesignMedia(library, kits), image_quality
        )

    def generate(self, job: ClaimedJob) -> GenerationOutcome:
        """Corre o job e fecha-o (conclui ou falha); erros inesperados sobem ao worker.

        `in_review`: revisao do pacote gravada. `stale`: nada foi gravado (post cancelado, ja
        decidido, alterado durante a geracao, ou lease perdida) e o job foi concluido sem escrever.
        `drafting_failed`: `needs_human`, limite, pre-requisito em falta, payload invalido ou
        falha da imagem; o job falhou sem nova tentativa e, se foi possivel, o post fica
        `drafting` com o motivo (numa falha da imagem, com a revisao so de texto gravada).
        """
        try:
            request = GenerationRequest.model_validate(job.payload)
        except ValidationError as exc:
            return self._fail(job, describe_error(exc))
        started = self._begin(job, request)
        if not isinstance(started, PostSnapshot):
            return started
        try:
            payload = self._load_payload(started)
        except MissingPrerequisite as exc:
            return self._fail(job, describe_error(exc))
        allowance = ImageAllowance(
            remaining_images(self._session_factory, started.brand_id, job.id)
        )
        result = self._rounds.run(
            job.id, started.brand_id, payload, request.owner_comment, allowance
        )
        if isinstance(result, RunResult):
            return self._fail(job, describe_stop(result))
        if isinstance(result, ImageFailure):
            return self._save_image_failure(job, started, result)
        return self._save(job, started, result)

    def _begin(
        self, job: ClaimedJob, request: GenerationRequest
    ) -> PostSnapshot | Literal["stale"]:
        """Bloqueia o post, decide se ha trabalho a fazer e passa-o a `drafting`."""
        with self._session_factory.begin() as session:
            post = lock_post(session, request.post_id)
            if not self._queue.holds_lease_in(session, job):
                return "stale"  # cancelado ou reclamado por outro: nao tocar no post
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
            brand_id, kb_ref_ids(snapshot.brief), snapshot.scheduled_date
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
        self, job: ClaimedJob, snapshot: PostSnapshot, package: GeneratedPackage
    ) -> GenerationOutcome:
        """Grava ativos, revisao, estado do post e conclusao do job numa transacao (post primeiro).

        Os ficheiros e as linhas de media so se criam depois de a verificacao de obsoletos e da
        lease passarem; se algo falhar a seguir a transacao desfaz as linhas.
        """
        try:
            with self._session_factory.begin() as session:
                post = lock_post(session, snapshot.post_id)
                if post is None or stale_reason(session, post, snapshot) is not None:
                    self._queue.complete_in(session, job)
                    return "stale"
                if not self._queue.holds_lease_in(session, job):
                    raise _LeaseLost  # nao escrever ficheiros para um job que ja nao e nosso
                image = store_package_image(session, self._library, snapshot.brand_id, package)
                costs = self._costs(session, snapshot, job)
                save_generated(
                    session,
                    post,
                    package.text,
                    snapshot,
                    self._clock.now(),
                    image=image,
                    costs=costs,
                )
                post.lifecycle = "in_review"
                post.last_error = None
                if not self._queue.complete_in(session, job):
                    raise _LeaseLost  # desfaz a revisao: este worker ja nao e o dono do job
                return "in_review"
        except _LeaseLost:
            logger.warning("job %s: lease perdida antes de gravar; nada foi gravado", job.id)
            return "stale"

    def _save_image_failure(
        self, job: ClaimedJob, snapshot: PostSnapshot, failure: ImageFailure
    ) -> GenerationOutcome:
        """Grava a revisao so de texto e falha o job sem repeticao, na mesma transacao.

        O post continua `drafting`, com `last_error = "imagem: <razao fixa>"`. Os custos de
        imagem ja faturados ficam registados na revisao: o fundo gerado perde-se, a despesa nao.
        """
        message = image_error(failure.reason)
        try:
            with self._session_factory.begin() as session:
                post = lock_post(session, snapshot.post_id)
                if post is None or stale_reason(session, post, snapshot) is not None:
                    self._queue.complete_in(session, job)
                    return "stale"
                costs = self._costs(session, snapshot, job)
                save_generated(
                    session, post, failure.text, snapshot, self._clock.now(), costs=costs
                )
                if self._queue.fail_in(session, job, message, retry=False) is None:
                    raise _LeaseLost
                post.last_error = message
                return "drafting_failed"
        except _LeaseLost:
            logger.warning("job %s: lease perdida antes de gravar; nada foi gravado", job.id)
            return "stale"

    @staticmethod
    def _costs(session: Session, snapshot: PostSnapshot, job: ClaimedJob) -> RevisionCosts:
        totals = job_costs_in(session, snapshot.brand_id, job.id)
        return RevisionCosts(totals.image_usd, totals.package_usd)

    def _fail(self, job: ClaimedJob, message: str) -> GenerationOutcome:
        """Falha o job sem nova tentativa; so com a lease valida e que o post recebe o motivo."""
        if self.fail_job(job, message, retry=False) is None:
            return "stale"
        return "drafting_failed"

    def fail_job(self, job: ClaimedJob, message: str, retry: bool) -> JobFailure | None:
        """Falha o job; se ficou `failed` ou `dead`, o post `drafting` recebe `message` como motivo.

        Uma so transacao, post primeiro e job depois (como a aprovacao do plano): nao ha estado
        em que o job falhou e o post continua mudo, e quem perdeu a lease (`None`) nao escreve
        nada, nem no post de um job novo. `message` vem sempre de `describe_error`.
        """
        post_id = job.payload.get("post_id")
        with self._session_factory.begin() as session:
            post = lock_post(session, post_id) if isinstance(post_id, str) else None
            status = self._queue.fail_in(session, job, message, retry)
            is_terminal = status in ("failed", "dead")
            if is_terminal and post is not None and post.lifecycle == DRAFTING:
                post.last_error = message
            return status
