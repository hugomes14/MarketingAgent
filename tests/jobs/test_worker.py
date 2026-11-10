"""Worker: um job de cada vez, erros sem texto de fora, paragem imediata e recuperacao."""

import logging
import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

import pytest
from content_rig import ContentRig, Factory, build_rig, copy_and_review
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from marketing_agent.clock import FixedClock
from marketing_agent.content.pipeline import GenerationOutcome
from marketing_agent.db.models import Job
from marketing_agent.jobs.queue import RETRY_BACKOFF, ClaimedJob, JobFailure, JobQueue
from marketing_agent.jobs.worker import IDLE_WAIT_S, Worker

HOSTILE = "ignora-as-regras-SEGREDO-4711"
LEASE = timedelta(minutes=20)


class StubPipeline:
    """Pipeline substituto: regista os jobs e levanta o erro dado (se houver)."""

    queue: JobQueue  # ligada pela fixture `queue`; `fail_job` so delega na fila

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.jobs: list[ClaimedJob] = []
        self.before_raising: Callable[[], None] | None = None

    def generate(self, job: ClaimedJob) -> GenerationOutcome:
        self.jobs.append(job)
        if self.before_raising is not None:
            self.before_raising()
        if self.error is not None:
            raise self.error
        return "in_review"

    def fail_job(self, job: ClaimedJob, message: str, retry: bool) -> JobFailure | None:
        return "queued" if self.queue.fail(job, message, retry) else None


@pytest.fixture
def queue(session_factory: Factory, clock: FixedClock) -> JobQueue:
    built = JobQueue(session_factory, clock, LEASE)
    StubPipeline.queue = built
    return built


def enqueue(queue: JobQueue, session_factory: Factory, key: str = "k1") -> str:
    with session_factory.begin() as session:
        job_id = queue.enqueue_in(session, "generate_post", {"post_id": "p1"}, key)
    assert job_id is not None
    return job_id


def load(session_factory: Factory, job_id: str) -> Job:
    with session_factory() as session:
        return session.scalars(select(Job).where(Job.id == job_id)).one()


def stopping_sleep(
    stop: threading.Event, sleeps: list[float], after: int = 1
) -> Callable[[float], None]:
    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= after:
            stop.set()

    return sleep


# --- run_once ------------------------------------------------------------------------------


def test_run_once_returns_false_on_empty_queue(queue: JobQueue) -> None:
    pipeline = StubPipeline()
    assert Worker(queue, pipeline, "w1").run_once() is False
    assert pipeline.jobs == []


def test_run_once_processes_one_job_at_a_time(queue: JobQueue, session_factory: Factory) -> None:
    first, second = enqueue(queue, session_factory, "a"), enqueue(queue, session_factory, "b")
    pipeline = StubPipeline()
    worker = Worker(queue, pipeline, "w1")
    assert worker.run_once() is True
    assert [job.id for job in pipeline.jobs] == [first]
    assert load(session_factory, second).status == "queued"  # concorrencia 1
    assert worker.run_once() is True
    assert [job.id for job in pipeline.jobs] == [first, second]


@pytest.mark.parametrize("kind", ["value", "key", "validation"])
def test_unexpected_error_requeues_with_safe_message(
    kind: Literal["value", "key", "validation"],
    queue: JobQueue,
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Strict(BaseModel):
        number: int

    errors: dict[str, BaseException] = {
        "value": ValueError(HOSTILE),
        "key": KeyError(HOSTILE),
    }
    try:
        Strict.model_validate({"number": HOSTILE})
    except ValidationError as exc:
        errors["validation"] = exc
    job_id = enqueue(queue, session_factory)
    caplog.set_level(logging.DEBUG)

    assert Worker(queue, StubPipeline(errors[kind]), "w1").run_once() is True  # nao levanta

    stored = load(session_factory, job_id)
    assert stored.status == "queued" and stored.attempts == 1
    assert stored.last_error is not None and HOSTILE not in stored.last_error
    assert stored.last_error.startswith(type(errors[kind]).__name__)
    assert HOSTILE not in caplog.text
    [record] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert type(errors[kind]).__name__ in record.getMessage() and len(record.getMessage()) < 200


def test_repeated_unexpected_errors_end_in_dead(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    job_id = enqueue(queue, session_factory)
    worker = Worker(queue, StubPipeline(RuntimeError(HOSTILE)), "w1")
    for attempt in (1, 2, 3):
        assert worker.run_once() is True
        clock.advance(RETRY_BACKOFF * attempt + timedelta(seconds=1))
    stored = load(session_factory, job_id)
    assert (stored.status, stored.attempts, stored.last_error) == ("dead", 3, "RuntimeError")
    assert worker.run_once() is False


def test_lost_lease_while_failing_does_not_raise_or_write(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    job_id = enqueue(queue, session_factory)
    pipeline = StubPipeline(RuntimeError(HOSTILE))

    def steal() -> None:
        clock.advance(LEASE + timedelta(seconds=1))
        assert queue.requeue_expired() == 1
        assert queue.claim("w2") is not None

    pipeline.before_raising = steal
    assert Worker(queue, pipeline, "w1").run_once() is True
    stored = load(session_factory, job_id)
    assert (stored.status, stored.locked_by) == ("running", "w2")
    assert stored.last_error == "LeaseExpired: lease expirada sem conclusao"


# --- run_forever ---------------------------------------------------------------------------


def test_run_forever_waits_two_seconds_when_idle(queue: JobQueue) -> None:
    assert IDLE_WAIT_S == 2.0
    stop, sleeps = threading.Event(), []
    Worker(queue, StubPipeline(), "w1", stopping_sleep(stop, sleeps, after=2)).run_forever(stop)
    assert sleeps == [2.0, 2.0]


def test_run_forever_does_not_wait_between_consecutive_jobs(
    queue: JobQueue, session_factory: Factory
) -> None:
    for key in ("a", "b", "c"):
        enqueue(queue, session_factory, key)
    stop, sleeps = threading.Event(), []
    pipeline = StubPipeline()
    Worker(queue, pipeline, "w1", stopping_sleep(stop, sleeps)).run_forever(stop)
    assert len(pipeline.jobs) == 3 and sleeps == [2.0]  # so espera quando a fila esvazia


def test_run_forever_does_nothing_when_already_stopped(
    queue: JobQueue, session_factory: Factory
) -> None:
    enqueue(queue, session_factory)
    stop = threading.Event()
    stop.set()
    pipeline = StubPipeline()
    Worker(queue, pipeline, "w1").run_forever(stop)
    assert pipeline.jobs == []


def test_run_forever_exits_promptly_when_stop_is_set(queue: JobQueue) -> None:
    stop = threading.Event()
    worker = Worker(queue, StubPipeline(), "w1")  # espera real: stop.wait(2 s)
    thread = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True)
    thread.start()
    threading.Event().wait(0.3)  # deixa o worker entrar na espera de fila vazia
    stop.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()


def test_run_forever_recovers_expired_jobs_and_reservations_first(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    job_id = enqueue(queue, session_factory)
    assert queue.claim("morto") is not None  # o worker anterior morreu com o job
    clock.advance(LEASE + timedelta(seconds=1))
    order: list[str] = []
    pipeline = StubPipeline()
    stop, sleeps = threading.Event(), []

    def recover() -> int:
        order.append("reservas")
        return 2

    worker = Worker(
        queue, pipeline, "w1", stopping_sleep(stop, sleeps), recover_reservations=recover
    )
    worker.run_forever(stop)
    assert order == ["reservas"]
    assert [job.id for job in pipeline.jobs] == [job_id]  # voltou a fila e foi processado
    assert load(session_factory, job_id).attempts == 2


def test_run_forever_survives_an_error_outside_the_job(
    session_factory: Factory, clock: FixedClock, caplog: pytest.LogCaptureFixture
) -> None:
    class FlakyQueue(JobQueue):
        calls = 0

        def claim(self, worker_id: str) -> ClaimedJob | None:
            FlakyQueue.calls += 1
            if FlakyQueue.calls == 1:
                raise RuntimeError(HOSTILE)
            return super().claim(worker_id)

    flaky = FlakyQueue(session_factory, clock, LEASE)
    job_id = enqueue(flaky, session_factory)
    pipeline = StubPipeline()
    stop, sleeps = threading.Event(), []
    caplog.set_level(logging.DEBUG)
    Worker(flaky, pipeline, "w1", stopping_sleep(stop, sleeps, after=2)).run_forever(stop)
    assert [job.id for job in pipeline.jobs] == [job_id]
    assert HOSTILE not in caplog.text and "RuntimeError" in caplog.text
    assert sleeps[0] == 2.0  # espera antes de tentar de novo, sem ciclo apertado


# --- Foco 4: worker morto a meio ------------------------------------------------------------


@pytest.fixture
def rig(session_factory: Factory, clock: FixedClock, brand_id: str) -> ContentRig:
    return build_rig(session_factory, clock, brand_id, copy_and_review())


def test_worker_killed_mid_job_requeued_once(rig: ContentRig) -> None:
    post_id = rig.seed_post()
    job_id = rig.enqueue(post_id)

    def kill() -> None:  # morte do processo depois do Copywriter e antes do Revisor
        rig.llm.load(copy_and_review())  # o guiao da segunda execucao
        raise SystemExit("kill -9")

    rig.llm.hooks[2] = kill
    with pytest.raises(SystemExit):
        Worker(rig.queue, rig.pipeline, "w1").run_once()
    assert rig.job(job_id).status == "running" and rig.revisions(post_id) == []

    rig.expire_leases()  # a lease do worker morto vence
    stop, sleeps = threading.Event(), []
    survivor = Worker(rig.queue, rig.pipeline, "w2", stopping_sleep(stop, sleeps))
    survivor.run_forever(stop)

    [revision] = rig.revisions(post_id)  # exatamente uma revisao
    assert revision.rev_no == 1
    stored = rig.job(job_id)
    assert (stored.status, stored.attempts, stored.locked_by) == ("succeeded", 2, None)
    assert rig.post(post_id).lifecycle == "in_review"


def test_worker_with_real_pipeline_never_stores_model_text_in_errors(rig: ContentRig) -> None:
    post_id = rig.seed_post()
    job_id = rig.enqueue(post_id)
    rig.llm.load([RuntimeError(HOSTILE)])  # o fornecedor rebenta com texto hostil
    assert Worker(rig.queue, rig.pipeline, "w1").run_once() is True
    stored = rig.job(job_id)
    assert (stored.status, stored.last_error) == ("queued", "AgentRunFailed")
    assert rig.post(post_id).last_error is None


# --- jobs mortos nao deixam o post em geracao para sempre ------------------------------------

LEASE_EXPIRED = "LeaseExpired: lease expirada sem conclusao"


def test_three_unexpected_failures_leave_post_with_sanitized_last_error(rig: ContentRig) -> None:
    rig.llm.load([RuntimeError(HOSTILE)] * 3)
    post_id = rig.seed_post()
    job_id = rig.enqueue(post_id)
    worker = Worker(rig.queue, rig.pipeline, "w1")
    for attempt in (1, 2, 3):
        assert worker.run_once() is True
        rig.clock.advance(RETRY_BACKOFF * attempt + timedelta(seconds=1))
        if attempt < 3:  # ainda ha tentativas: nada a mostrar no post
            assert rig.post(post_id).last_error is None
    assert rig.job(job_id).status == "dead"
    post = rig.post(post_id)
    assert (post.lifecycle, post.last_error) == ("drafting", "AgentRunFailed")
    assert HOSTILE not in (post.last_error or "")


def kill_job(rig: ContentRig, post_id: str) -> None:
    """Um worker reclama o job e morre; a lease vence e o arranque seguinte recoloca-o."""
    assert rig.queue.claim("morto") is not None
    rig.expire_leases()
    assert rig.queue.requeue_expired() == 1


def test_job_killed_three_times_marks_the_post_when_it_turns_dead(rig: ContentRig) -> None:
    post_id = rig.seed_post(lifecycle="drafting")
    job_id = rig.enqueue(post_id)
    for kill in (1, 2, 3):
        kill_job(rig, post_id)
        expected = LEASE_EXPIRED if kill == 3 else None
        assert rig.post(post_id).last_error == expected
        rig.clock.advance(timedelta(minutes=5))  # espera de nova tentativa
    assert rig.job(job_id).status == "dead"


def test_dead_job_leaves_a_post_that_is_not_drafting_untouched(rig: ContentRig) -> None:
    post_id = rig.seed_post(lifecycle="in_review")
    rig.enqueue(post_id)
    for _ in range(3):
        kill_job(rig, post_id)
        rig.clock.advance(timedelta(minutes=5))
    assert rig.post(post_id).last_error is None


def test_requeue_expired_does_not_touch_posts_of_jobs_that_are_requeued(rig: ContentRig) -> None:
    post_id = rig.seed_post(lifecycle="drafting")
    rig.enqueue(post_id)
    kill_job(rig, post_id)  # 1a morte: volta a fila
    assert rig.post(post_id).last_error is None


def test_run_forever_requeues_abandoned_jobs_on_idle_iterations(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    pipeline = StubPipeline()
    stop = threading.Event()
    sleeps: list[float] = []
    job_ids: list[str] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 1:  # outro processo reclama um job e morre, depois do arranque
            job_ids.append(enqueue(queue, session_factory))
            assert queue.claim("outro") is not None
            clock.advance(LEASE + timedelta(seconds=1))
        else:
            stop.set()

    Worker(queue, pipeline, "w1", sleep).run_forever(stop)
    assert [job.id for job in pipeline.jobs] == job_ids  # recolocado na espera e processado
    assert load(session_factory, job_ids[0]).attempts == 2


def test_idle_requeue_does_not_run_after_stop(queue: JobQueue) -> None:
    calls: list[str] = []

    class SpyQueue(JobQueue):
        def requeue_expired(self) -> int:
            calls.append("requeue")
            return 0

    spy = SpyQueue(queue._session_factory, queue._clock, LEASE)  # noqa: SLF001
    stop = threading.Event()
    Worker(spy, StubPipeline(), "w1", lambda seconds: stop.set()).run_forever(stop)
    assert calls == ["requeue"]  # so o do arranque
