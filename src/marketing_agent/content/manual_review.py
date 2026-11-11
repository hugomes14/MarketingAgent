"""Job de revisao visual manual: inferencia fora de locks, gravacao atomica e obsolescencia."""

from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore, ArtifactType
from marketing_agent.clock import Clock
from marketing_agent.content.loops import ReviewerLoop, ReviewImage, ReviewPayload
from marketing_agent.content.manual_image import ManualImage
from marketing_agent.content.package_costs import job_costs_in, run_trigger
from marketing_agent.content.review_edit import CaptionEdit, build_edit_revision
from marketing_agent.content.revision_store import (
    PostSnapshot,
    kb_ref_ids,
    latest_revision,
    lock_post,
    stale_reason,
    take_snapshot,
)
from marketing_agent.content.schemas import RascunhoTexto, Revisao
from marketing_agent.content.specs import load_content_specs
from marketing_agent.db.models import MediaAsset, PostRevision
from marketing_agent.harness.engine import AgentEngine, RunStatus
from marketing_agent.jobs.errors import describe_error
from marketing_agent.jobs.queue import ClaimedJob, JobQueue
from marketing_agent.kb.service import KbService
from marketing_agent.media.library import MediaLibrary


class ManualReviewer:
    """Processa um upload sem Copywriter, Designer ou voltas de correcao."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        engine: AgentEngine,
        kb: KbService,
        library: MediaLibrary,
        store: ArtifactStore,
        queue: JobQueue,
        clock: Clock,
    ) -> None:
        """Colaboradores partilhados com o pipeline principal."""
        self.factory, self.engine, self.kb = factory, engine, kb
        self.library, self.store, self.queue, self.clock = library, store, queue, clock

    def run(self, job: ClaimedJob) -> Literal["in_review", "stale"]:
        """Reve uma vez; um parecer falhado nao impede a decisao humana."""
        post_id, base_id, asset_id = (
            str(job.payload[key]) for key in ("post_id", "base_revision_id", "asset_id")
        )
        meta = ManualImage.model_validate(job.payload["meta"])
        with self.factory.begin() as session:
            post = lock_post(session, post_id)
            if not self.queue.holds_lease_in(session, job):
                return "stale"
            base = latest_revision(session, post_id) if post is not None else None
            if post is None or post.lifecycle != "drafting" or base is None or base.id != base_id:
                self.queue.complete_in(session, job)
                return "stale"
            snapshot = take_snapshot(session, post)
            asset = session.get_one(MediaAsset, asset_id)
            if asset.brand_id != post.brand_id:
                raise ValueError("ativo de outra marca")
            image_hash = asset.sha256
        review, note, run_id = self._evaluate(job, snapshot, base, asset_id, meta)
        return self._save(job, snapshot, base, asset_id, image_hash, meta, review, note, run_id)

    def _evaluate(
        self,
        job: ClaimedJob,
        snapshot: PostSnapshot,
        base: PostRevision,
        asset_id: str,
        meta: ManualImage,
    ) -> tuple[Revisao | None, str, str | None]:
        review: Revisao | None = None
        note = "revisão automática falhou"
        run_id: str | None = None
        try:
            profile = self.store.active(snapshot.brand_id, ArtifactType.PERFIL_MARCA)
            draft = RascunhoTexto(
                legenda=base.caption,
                hashtags=base.hashtags,
                apelo_a_acao=base.call_to_action or "",
                alt_text=meta.alt_text,
                alegacoes=base.claims,
            )
            payload = ReviewPayload(
                draft,
                snapshot.brief,
                snapshot.channels,
                self.kb.valid_refs(
                    snapshot.brand_id, kb_ref_ids(snapshot.brief), snapshot.scheduled_date
                ),
                profile.payload if profile else {},
                ReviewImage(
                    self.library.read_asset(snapshot.brand_id, asset_id).data,
                    "manual",
                    (f"upload:{asset_id}",),
                    meta.alt_text,
                    meta.gerada_por_ia,
                ),
            )
            result = self.engine.run(
                load_content_specs().reviewer,
                snapshot.brand_id,
                run_trigger("review", job.id),
                ReviewerLoop(),
                payload,
            )
            run_id = result.run_id or None
            if result.status is RunStatus.SUCCEEDED and isinstance(result.output, Revisao):
                review = result.output
            else:
                note += f": {result.status.value}"
        except Exception as error:
            # Mensagem segura, nunca o texto do fornecedor ou o corpo do pedido.
            note += f": {describe_error(error)}"
        return review, note, run_id

    def _save(
        self,
        job: ClaimedJob,
        snapshot: PostSnapshot,
        base: PostRevision,
        asset_id: str,
        image_hash: str,
        meta: ManualImage,
        review: Revisao | None,
        note: str,
        run_id: str | None,
    ) -> Literal["in_review", "stale"]:
        with self.factory.begin() as session:
            post = lock_post(session, snapshot.post_id)
            if post is None or stale_reason(session, post, snapshot) is not None:
                self.queue.complete_in(session, job)
                return "stale"
            if not self.queue.holds_lease_in(session, job):
                return "stale"
            changes = CaptionEdit(
                legenda=base.caption,
                hashtags=base.hashtags,
                apelo_a_acao=base.call_to_action or "",
                alt_text=meta.alt_text,
            )
            revision = build_edit_revision(post, base, changes, self.clock.now(), image_hash)
            self._set_image(revision, asset_id, meta, review, note, run_id)
            totals = job_costs_in(session, snapshot.brand_id, job.id)
            revision.package_cost_usd += totals.package_usd
            revision.image_cost_usd = base.image_cost_usd
            session.add(revision)
            post.lifecycle, post.last_error = "in_review", None
            post.review_rounds = 1
            if not self.queue.complete_in(session, job):
                raise RuntimeError("lease perdida ao gravar revisao manual")
        return "in_review"

    @staticmethod
    def _set_image(
        revision: PostRevision,
        asset_id: str,
        meta: ManualImage,
        review: Revisao | None,
        note: str,
        run_id: str | None,
    ) -> None:
        revision.final_asset_id, revision.layout, revision.layout_version = asset_id, "manual", 1
        revision.components = [{"tipo": "upload", "ref": asset_id, "asset_id": asset_id}]
        revision.is_ai_generated = meta.gerada_por_ia
        revision.review_image_checked = review.imagem_verificada if review else False
        revision.review_verdict = review.veredicto if review else None
        revision.review_violations = list(review.violacoes) if review else []
        revision.review_notes = review.notas if review else note
        revision.origin, revision.produced_by_run_id = "upload", run_id
