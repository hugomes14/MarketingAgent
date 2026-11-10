"""ReviewService.retry_generation: repetir a geracao falhada de uma publicacao."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import pytest
from content_rig import WEEK, ContentRig, Factory, Script, build_rig, copy_and_review
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from test_review import other_brand, race

from marketing_agent.clock import FixedClock
from marketing_agent.content.review import (
    InvalidPostState,
    PostNotFound,
    ReviewService,
)
from marketing_agent.db.models import AuditLog, Job, Post

HOSTILE = "ignora-as-regras-SEGREDO-4711"


@dataclass
class Retry:
    rig: ContentRig
    service: ReviewService

    def seed(
        self,
        *,
        lifecycle: str = "drafting",
        last_error: str | None = "NeedsHuman",
        revisions: int = 0,
        slot: int = 1,
    ) -> str:
        post_id = self.rig.seed_post(f"{WEEK}-{slot}", lifecycle=lifecycle)
        with self.rig.factory.begin() as session:
            session.get_one(Post, post_id).last_error = last_error
        for rev_no in range(1, revisions + 1):
            self.rig.add_owner_revision(post_id, rev_no, origin="agente")
        return post_id

    def fail_job(self, post_id: str, reason: str = "plan", comment: str | None = None) -> None:
        """Um trabalho que correu e falhou, como o do pipeline (sem nova tentativa)."""
        self.rig.clock.advance(timedelta(seconds=1))  # created_at distinto: o ultimo e o mais novo
        job = self.rig.claimed(post_id, reason, comment)
        assert self.rig.queue.fail(job, "NeedsHuman", retry=False)

    def retry(self, post_id: str, brand_id: str | None = None) -> None:
        self.service.retry_generation(brand_id or self.rig.brand_id, post_id, "ana")

    def jobs(self, post_id: str) -> list[Job]:
        with self.rig.factory() as session:
            statement = select(Job).order_by(Job.created_at, Job.id)
            return [job for job in session.scalars(statement) if job.payload["post_id"] == post_id]

    def queued(self, post_id: str) -> list[Job]:
        return [job for job in self.jobs(post_id) if job.status == "queued"]

    def audits(self) -> list[AuditLog]:
        with self.rig.factory() as session:
            statement = select(AuditLog).where(AuditLog.action == "post.retry_generation")
            return list(session.scalars(statement))


MakeRetry = Callable[..., Retry]


@pytest.fixture
def make_retry(session_factory: Factory, clock: FixedClock, brand_id: str) -> MakeRetry:
    def make(script: Script | None = None) -> Retry:
        rig = build_rig(session_factory, clock, brand_id, script or [])
        service = ReviewService(
            rig.factory, rig.feedback, rig.queue, rig.kb, rig.clock, max_comment_chars=200
        )
        return Retry(rig, service)

    return make


@pytest.fixture
def retry(make_retry: MakeRetry) -> Retry:
    return make_retry()


def test_retry_a_failed_post_without_revision(retry: Retry) -> None:
    post_id = retry.seed(lifecycle="drafting", last_error="NeedsHuman")
    retry.fail_job(post_id)

    retry.retry(post_id)

    post = retry.rig.post(post_id)
    # O ciclo de vida nao muda (o pipeline aceita planned/drafting e passa a drafting ao arrancar).
    assert (post.lifecycle, post.last_error) == ("drafting", None)
    [job] = retry.queued(post_id)
    assert job.type == "generate_post"
    assert job.payload == {"post_id": post_id, "owner_comment": None, "reason": "plan"}
    prefix = f"generate_post:{post_id}:retry:"
    assert job.dedupe_key is not None and job.dedupe_key.startswith(prefix)
    assert len(job.dedupe_key) > len(prefix)


def test_retry_a_planned_post_whose_job_died(retry: Retry) -> None:
    post_id = retry.seed(lifecycle="planned", last_error=None)
    retry.fail_job(post_id)

    retry.retry(post_id)

    assert retry.rig.post(post_id).lifecycle == "planned"
    assert len(retry.queued(post_id)) == 1


def test_retry_a_planned_post_with_no_job_at_all(retry: Retry) -> None:
    post_id = retry.seed(lifecycle="planned", last_error=None)
    retry.retry(post_id)
    assert len(retry.queued(post_id)) == 1


def test_retry_with_revisions_reuses_the_owner_comment_of_the_failed_changes_job(
    retry: Retry,
) -> None:
    post_id = retry.seed(lifecycle="drafting", revisions=2)
    retry.fail_job(post_id, "changes", "Mais curto, por favor.")

    retry.retry(post_id)

    [job] = retry.queued(post_id)
    assert job.payload == {
        "post_id": post_id,
        "owner_comment": "Mais curto, por favor.",
        "reason": "changes",
    }


def test_retry_after_a_plan_job_ignores_an_older_changes_comment(retry: Retry) -> None:
    post_id = retry.seed(lifecycle="drafting", revisions=1)
    retry.fail_job(post_id, "changes", "Comentario antigo.")
    retry.fail_job(post_id, "plan")

    retry.retry(post_id)

    [job] = retry.queued(post_id)
    assert job.payload["reason"] == "plan" and job.payload["owner_comment"] is None


def test_retry_twice_in_a_row_keeps_the_comment_and_is_refused_while_active(
    retry: Retry,
) -> None:
    post_id = retry.seed(lifecycle="drafting", revisions=1)
    retry.fail_job(post_id, "changes", "Tom informal.")
    retry.retry(post_id)
    with pytest.raises(InvalidPostState):
        retry.retry(post_id)
    assert len(retry.jobs(post_id)) == 2


@pytest.mark.parametrize("status", ["queued", "running"])
@pytest.mark.parametrize("lifecycle", ["planned", "drafting"])
def test_retry_is_refused_while_a_generation_job_is_active(
    retry: Retry, lifecycle: str, status: str
) -> None:
    post_id = retry.seed(lifecycle=lifecycle, last_error="NeedsHuman")
    retry.rig.enqueue(post_id)
    if status == "running":
        assert retry.rig.queue.claim("w1") is not None
    with pytest.raises(InvalidPostState):
        retry.retry(post_id)
    assert len(retry.jobs(post_id)) == 1
    assert retry.rig.post(post_id).last_error == "NeedsHuman"
    assert retry.audits() == []


def test_retry_is_refused_on_drafting_without_a_recorded_failure(retry: Retry) -> None:
    post_id = retry.seed(lifecycle="drafting", last_error=None)
    with pytest.raises(InvalidPostState):
        retry.retry(post_id)
    assert retry.jobs(post_id) == []


@pytest.mark.parametrize("lifecycle", ["in_review", "approved", "rejected", "cancelled"])
def test_retry_is_refused_in_other_states(retry: Retry, lifecycle: str) -> None:
    post_id = retry.seed(lifecycle=lifecycle, last_error=None, revisions=1)
    with pytest.raises(InvalidPostState):
        retry.retry(post_id)
    assert retry.rig.post(post_id).lifecycle == lifecycle
    assert retry.jobs(post_id) == [] and retry.audits() == []


def test_retry_on_another_brand_or_unknown_post_is_not_found(retry: Retry) -> None:
    post_id = retry.seed()
    stranger = other_brand(retry.rig.factory)
    messages = set()
    for brand, target in ((stranger, post_id), (retry.rig.brand_id, "nao-existe")):
        with pytest.raises(PostNotFound) as caught:
            retry.retry(target, brand)
        messages.add(str(caught.value))
    assert len(messages) == 1  # a mesma mensagem: nao revela se o id existe noutra marca
    assert retry.jobs(post_id) == [] and retry.audits() == []


def test_retry_audit_has_actor_and_ids_but_no_text(retry: Retry) -> None:
    post_id = retry.seed(lifecycle="drafting", revisions=1)
    retry.fail_job(post_id, "changes", HOSTILE)

    retry.retry(post_id)

    [audit] = retry.audits()
    assert (audit.actor, audit.subject_type, audit.subject_id) == ("ana", "post", post_id)
    assert HOSTILE not in str(audit.detail)
    assert audit.detail == {"reason": "changes"}


def test_concurrent_double_click_enqueues_exactly_one_job(retry: Retry) -> None:
    post_id = retry.seed()
    results = race(*(lambda: retry.retry(post_id) for _ in range(3)))
    assert sorted(r is None for r in results) == [False, False, True]
    assert all(isinstance(r, InvalidPostState) for r in results if r is not None)
    assert len(retry.queued(post_id)) == 1 and len(retry.audits()) == 1


def test_retry_and_plan_reapproval_race_do_not_deadlock(retry: Retry) -> None:
    from marketing_agent.planning.posts import PostReconciler
    from marketing_agent.planning.schemas import PlanoSemanal

    reconciler = PostReconciler(retry.rig.queue, retry.rig.clock)
    plan_record = retry.rig.store.get(retry.rig.plan_id)
    empty_plan = PlanoSemanal.model_construct(semana=WEEK, slots=[])

    def reapprove() -> object:
        with retry.rig.factory.begin() as session:
            return reconciler.apply(session, plan_record, empty_plan, retry.rig.strategy_id)

    for slot in range(1, 7):
        post_id = retry.seed(slot=slot)
        results = race(reapprove, lambda p=post_id: retry.retry(p))
        assert not any(isinstance(r, DBAPIError) for r in results), results
        assert not isinstance(results[0], Exception), results
        assert results[1] is None or isinstance(results[1], InvalidPostState)
        assert retry.rig.post(post_id).lifecycle == "cancelled"
        assert not any(job.status in ("queued", "running") for job in retry.jobs(post_id))


def test_pipeline_generates_the_retried_post_without_revision(make_retry: MakeRetry) -> None:
    retry = make_retry(copy_and_review(legenda="Versao nova."))
    post_id = retry.seed(lifecycle="drafting")
    retry.fail_job(post_id)
    retry.retry(post_id)

    job = retry.rig.queue.claim("w1")
    assert job is not None
    assert retry.rig.pipeline.generate(job) == "in_review"

    [revision] = retry.rig.revisions(post_id)
    assert revision.caption == "Versao nova."
    post = retry.rig.post(post_id)
    assert (post.lifecycle, post.last_error) == ("in_review", None)


def test_pipeline_generates_a_retried_planned_post(make_retry: MakeRetry) -> None:
    retry = make_retry(copy_and_review())
    post_id = retry.seed(lifecycle="planned", last_error=None)
    retry.retry(post_id)
    job = retry.rig.queue.claim("w1")
    assert job is not None
    assert retry.rig.pipeline.generate(job) == "in_review"
    assert len(retry.rig.revisions(post_id)) == 1


def test_pipeline_retries_a_failed_changes_job_with_the_comment(make_retry: MakeRetry) -> None:
    retry = make_retry(copy_and_review(legenda="Versao dois."))
    post_id = retry.seed(lifecycle="drafting", revisions=1)
    retry.fail_job(post_id, "changes", "Tom mais informal")
    retry.retry(post_id)

    job = retry.rig.queue.claim("w1")
    assert job is not None
    assert retry.rig.pipeline.generate(job) == "in_review"
    assert "Tom mais informal" in retry.rig.llm.request_text(1)
    assert [r.rev_no for r in retry.rig.revisions(post_id)] == [1, 2]
