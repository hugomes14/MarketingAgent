"""Fila de trabalhos: deduplicacao, reclamacao exclusiva, lease, tentativas e cancelamento."""

import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import FixedClock
from marketing_agent.db.models import ACTIVE_JOB_PREDICATE_SQL, Job
from marketing_agent.jobs.queue import (
    GENERATE_POST_JOB,
    MAX_ERROR_CHARS,
    ClaimedJob,
    JobQueue,
)

Factory = sessionmaker[Session]
LEASE = timedelta(minutes=10)
BARRIER_TIMEOUT_S = 5


@pytest.fixture
def queue(session_factory: Factory, clock: FixedClock) -> JobQueue:
    return JobQueue(session_factory, clock, LEASE)


def enqueue(
    queue: JobQueue,
    session_factory: Factory,
    dedupe_key: str,
    job_type: str = GENERATE_POST_JOB,
    post_id: str = "post-1",
) -> str | None:
    with session_factory.begin() as session:
        return queue.enqueue_in(session, job_type, {"post_id": post_id}, dedupe_key)


def load(session_factory: Factory, job_id: str) -> Job:
    with session_factory() as session:
        return session.scalars(select(Job).where(Job.id == job_id)).one()


def claim_one(queue: JobQueue, session_factory: Factory, key: str = "k1") -> ClaimedJob:
    """Enfileira um job e reclama-o; falha se nao o conseguir."""
    assert enqueue(queue, session_factory, key) is not None
    claimed = queue.claim("w1")
    assert claimed is not None
    return claimed


def test_enqueue_dedupes_active(queue: JobQueue, session_factory: Factory) -> None:
    first = enqueue(queue, session_factory, "generate_post:p1:plan")
    assert first is not None
    assert enqueue(queue, session_factory, "generate_post:p1:plan") is None
    claimed = queue.claim("w1")  # running continua ativo
    assert claimed is not None
    assert enqueue(queue, session_factory, "generate_post:p1:plan") is None
    with session_factory.begin() as session:
        assert queue.complete_in(session, claimed) is True
    # Terminado, a chave fica livre para um novo pedido.
    assert enqueue(queue, session_factory, "generate_post:p1:plan") is not None


def test_claim_marks_running_with_lease(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    job_id = enqueue(queue, session_factory, "k1")
    claimed = queue.claim("worker-a")
    assert claimed is not None
    assert (claimed.id, claimed.type, claimed.attempts) == (job_id, GENERATE_POST_JOB, 1)
    assert claimed.payload == {"post_id": "post-1"}
    row = load(session_factory, claimed.id)
    assert row.status == "running"
    assert row.locked_by == "worker-a"
    assert row.locked_until == clock.now() + LEASE
    assert row.lease_token == claimed.lease_token
    assert queue.claim("worker-b") is None


def test_claim_orders_by_run_at_then_created_at(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    late = enqueue(queue, session_factory, "late")
    clock.advance(timedelta(seconds=1))
    later = enqueue(queue, session_factory, "later")
    assert late is not None and later is not None
    with session_factory.begin() as session:
        session.get_one(Job, later).run_at = clock.now() - timedelta(hours=1)
    order = [queue.claim("w1"), queue.claim("w1")]
    assert [c.id for c in order if c is not None] == [later, late]


def test_claim_respects_run_at(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    job_id = enqueue(queue, session_factory, "k1")
    with session_factory.begin() as session:
        session.get_one(Job, job_id).run_at = clock.now() + timedelta(minutes=5)
    assert queue.claim("w1") is None
    clock.advance(timedelta(minutes=5))
    claimed = queue.claim("w1")
    assert claimed is not None and claimed.id == job_id


def test_claim_two_threads_one_wins(queue: JobQueue, session_factory: Factory) -> None:
    for repetition in range(5):  # repete para o intercalamento sem lock aparecer
        job_id = enqueue(queue, session_factory, f"race-{repetition}")
        barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT_S)

        def attempt(name: str, barrier: threading.Barrier = barrier) -> ClaimedJob | None:
            barrier.wait()
            return queue.claim(name)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt, "w1"), pool.submit(attempt, "w2")]
            results = [f.result() for f in futures]
        winners = [r for r in results if r is not None]
        assert [w.id for w in winners] == [job_id]
        assert winners[0].attempts == 1
        with session_factory.begin() as session:  # liberta a chave para a repeticao seguinte
            assert queue.complete_in(session, winners[0]) is True


def test_complete_with_stale_token_returns_false(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    claimed = claim_one(queue, session_factory)
    clock.advance(LEASE + timedelta(seconds=1))
    assert queue.requeue_expired() == 1
    second = queue.claim("w2")
    assert second is not None and second.lease_token != claimed.lease_token
    with session_factory.begin() as session:
        assert queue.complete_in(session, claimed) is False
    assert load(session_factory, claimed.id).status == "running"
    with session_factory.begin() as session:
        assert queue.complete_in(session, second) is True
    done = load(session_factory, claimed.id)
    assert (done.status, done.lease_token, done.locked_by, done.locked_until) == (
        "succeeded",
        None,
        None,
        None,
    )


def test_fail_retries_then_dead(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    enqueue(queue, session_factory, "k1")
    for attempt in (1, 2, 3):
        claimed = queue.claim("w1")
        assert claimed is not None and claimed.attempts == attempt
        assert queue.fail(claimed, "ValueError: falhou", retry=True) is True
        row = load(session_factory, claimed.id)
        if attempt == 3:
            assert row.status == "dead"
            assert row.last_error == "ValueError: falhou"
            break
        assert row.status == "queued"
        assert row.run_at == clock.now() + timedelta(seconds=30 * attempt)
        assert (row.lease_token, row.locked_by, row.locked_until) == (None, None, None)
        assert queue.claim("w1") is None  # ainda em espera
        clock.advance(timedelta(seconds=30 * attempt))
    assert enqueue(queue, session_factory, "k1") is not None  # dead liberta a chave


def test_fail_without_retry_is_failed(queue: JobQueue, session_factory: Factory) -> None:
    claimed = claim_one(queue, session_factory)
    assert queue.fail(claimed, "NeedsHuman: sem base", retry=False) is True
    row = load(session_factory, claimed.id)
    assert (row.status, row.lease_token, row.last_error) == ("failed", None, "NeedsHuman: sem base")
    assert queue.claim("w1") is None


def test_fail_with_stale_token_changes_nothing(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    claimed = claim_one(queue, session_factory)
    clock.advance(LEASE + timedelta(seconds=1))
    queue.requeue_expired()
    second = queue.claim("w2")
    assert second is not None
    assert queue.fail(claimed, "Erro: velho", retry=False) is False
    row = load(session_factory, claimed.id)
    assert (row.status, row.lease_token, row.last_error) == (
        "running",
        second.lease_token,
        "LeaseExpired: lease expirada sem conclusao",
    )


def test_fail_truncates_error(queue: JobQueue, session_factory: Factory) -> None:
    claimed = claim_one(queue, session_factory)
    assert queue.fail(claimed, "X" * 500, retry=False) is True
    error = load(session_factory, claimed.id).last_error
    assert error is not None and len(error) == MAX_ERROR_CHARS


def test_requeue_expired_lease(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    claimed = claim_one(queue, session_factory)
    clock.advance(LEASE)  # locked_until == agora: ainda valida
    assert queue.requeue_expired() == 0
    clock.advance(timedelta(seconds=1))
    assert queue.requeue_expired() == 1
    row = load(session_factory, claimed.id)
    assert (row.status, row.attempts, row.lease_token, row.locked_by) == ("queued", 1, None, None)
    again = queue.claim("w2")
    assert again is not None and again.attempts == 2


def test_requeue_expired_exhausted_attempts_goes_dead(
    queue: JobQueue, session_factory: Factory, clock: FixedClock
) -> None:
    claimed = claim_one(queue, session_factory)
    with session_factory.begin() as session:
        session.get_one(Job, claimed.id).attempts = 3
    clock.advance(LEASE + timedelta(seconds=1))
    assert queue.requeue_expired() == 1
    assert load(session_factory, claimed.id).status == "dead"


def test_cancel_for_post(queue: JobQueue, session_factory: Factory) -> None:
    running = claim_one(queue, session_factory, "r")  # post-1, a correr
    queued = enqueue(queue, session_factory, "a", post_id="post-1")
    other_post = enqueue(queue, session_factory, "b", post_id="post-2")
    other_type = enqueue(queue, session_factory, "c", job_type="outro", post_id="post-1")
    assert queued is not None and other_post is not None and other_type is not None
    with session_factory.begin() as session:
        assert queue.cancel_for_post_in(session, "post-1") == 2
    assert load(session_factory, queued).status == "cancelled"
    assert load(session_factory, running.id).status == "cancelled"
    assert load(session_factory, other_post).status == "queued"
    assert load(session_factory, other_type).status == "queued"
    with session_factory.begin() as session:  # o worker que perdeu o job nao o conclui
        assert queue.complete_in(session, running) is False
        assert queue.cancel_for_post_in(session, "post-1") == 0


JOBS_IN_STRESS = 50
THREADS_IN_STRESS = 8


def drain(queue: JobQueue, session_factory: Factory, barrier: threading.Barrier) -> list[str]:
    """Reclama e conclui jobs ate a fila esvaziar; devolve os ids reclamados."""
    barrier.wait()
    claimed_ids: list[str] = []
    while (claimed := queue.claim(threading.current_thread().name)) is not None:
        claimed_ids.append(claimed.id)
        with session_factory.begin() as session:
            assert queue.complete_in(session, claimed) is True
    return claimed_ids


def test_claim_many_threads_claims_each_job_exactly_once(
    queue: JobQueue, session_factory: Factory
) -> None:
    for round_number in range(3):
        enqueued = {
            enqueue(queue, session_factory, f"stress-{round_number}-{n}")
            for n in range(JOBS_IN_STRESS)
        }
        barrier = threading.Barrier(THREADS_IN_STRESS, timeout=BARRIER_TIMEOUT_S)
        with ThreadPoolExecutor(max_workers=THREADS_IN_STRESS) as pool:
            futures = [
                pool.submit(drain, queue, session_factory, barrier)
                for _ in range(THREADS_IN_STRESS)
            ]
            claimed_ids = [job_id for f in futures for job_id in f.result(timeout=60)]
        assert len(claimed_ids) == JOBS_IN_STRESS  # nenhum reclamado duas vezes
        assert set(claimed_ids) == enqueued
        with session_factory() as session:
            rows = session.scalars(select(Job).where(Job.id.in_(enqueued))).all()
        assert {(row.status, row.attempts) for row in rows} == {("succeeded", 1)}


def index_predicate(session: Session, index_name: str) -> str | None:
    """Predicado normalizado pelo Postgres de um indice parcial."""
    return session.scalar(
        text(
            "SELECT pg_get_expr(i.indpred, i.indrelid) FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
        ),
        {"name": index_name},
    )


def test_active_predicate_matches_migrated_index(session_factory: Factory) -> None:
    """O predicado da migracao 0002 (literal congelado) tem de coincidir com a constante."""
    with session_factory() as session:
        migrated = index_predicate(session, "uq_job_dedupe_key_active")
        # O Postgres normaliza o predicado; compara-se a forma normalizada de ambos.
        session.execute(text("CREATE TEMP TABLE predicate_probe (status text)"))
        session.execute(
            text(
                "CREATE INDEX predicate_probe_idx ON predicate_probe (status) "
                f"WHERE {ACTIVE_JOB_PREDICATE_SQL}"
            )
        )
        expected = index_predicate(session, "predicate_probe_idx")
        session.rollback()
    assert migrated is not None
    assert migrated == expected


def test_queue_module_has_no_domain_imports() -> None:
    """A fila nao importa dominio (evita ciclos quando strategy/planning a importarem)."""
    code = (
        "import sys; import marketing_agent.jobs.queue; "
        "bad = [m for m in sys.modules if m.startswith(("
        "'marketing_agent.harness', 'marketing_agent.kb', "
        "'marketing_agent.strategy', 'marketing_agent.budget'))]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
