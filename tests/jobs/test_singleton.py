"""Lock de sessao do worker: exclusividade, libertacao ao fechar e deteccao de ligacao perdida.

Os locks consultivos sao por BASE DE DADOS: estes testes usam chaves proprias (pequenas, para
a procura em `pg_locks` ser direta: classid 0, objid = chave) na base de teste, por isso nao
colidem com a chave de producao nem com o worker de um fumo noutra base do mesmo servidor.
"""

import threading
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from marketing_agent.db.base import make_engine
from marketing_agent.jobs.singleton import WORKER_LOCK_KEY, WorkerLock

TEST_KEY = 71_000_001
WAIT_S = 10.0


@pytest.fixture
def admin(pg_url: str) -> Iterator[Engine]:
    engine = make_engine(pg_url)
    yield engine
    engine.dispose()


@pytest.fixture
def locks(pg_url: str) -> Iterator[list[WorkerLock]]:
    created: list[WorkerLock] = []
    yield created
    for lock in created:
        lock.release()


def make_lock(pg_url: str, locks: list[WorkerLock], key: int = TEST_KEY) -> WorkerLock:
    lock = WorkerLock(pg_url, key)
    locks.append(lock)
    return lock


def holder_pid(admin: Engine, key: int = TEST_KEY) -> int | None:
    with admin.connect() as connection:
        return connection.execute(
            text(
                "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted "
                "AND classid = 0 AND objid = :key AND database = "
                "(SELECT oid FROM pg_database WHERE datname = current_database())"
            ),
            {"key": key},
        ).scalar_one_or_none()


def test_production_key_is_a_positive_bigint() -> None:
    assert 0 < WORKER_LOCK_KEY < 2**63


def test_second_lock_is_refused_while_the_first_holds_it(
    pg_url: str, locks: list[WorkerLock]
) -> None:
    first, second = make_lock(pg_url, locks), make_lock(pg_url, locks)
    assert first.acquire() is True
    assert second.acquire() is False
    assert second.is_alive() is False  # sem ligacao aberta depois de recusado
    assert first.is_alive() is True


def test_two_threads_racing_for_the_lock_have_exactly_one_winner(
    pg_url: str, locks: list[WorkerLock]
) -> None:
    contenders = [make_lock(pg_url, locks), make_lock(pg_url, locks)]
    barrier = threading.Barrier(2, timeout=WAIT_S)
    results: list[bool] = []

    def attempt(lock: WorkerLock) -> None:
        barrier.wait()
        results.append(lock.acquire())

    threads = [threading.Thread(target=attempt, args=(lock,), daemon=True) for lock in contenders]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=WAIT_S)
        assert not thread.is_alive()
    assert sorted(results) == [False, True]


def test_lock_is_released_when_the_holder_closes_its_connection(
    pg_url: str, locks: list[WorkerLock], admin: Engine
) -> None:
    first, second = make_lock(pg_url, locks), make_lock(pg_url, locks)
    assert first.acquire() is True
    assert holder_pid(admin) is not None
    first.release()
    assert holder_pid(admin) is None
    assert second.acquire() is True
    first.release()  # repetir e inofensivo


def test_holding_the_lock_leaves_no_open_transaction(
    pg_url: str, locks: list[WorkerLock], admin: Engine
) -> None:
    lock = make_lock(pg_url, locks)
    assert lock.acquire() is True
    assert lock.is_alive() is True
    pid = holder_pid(admin)
    with admin.connect() as connection:
        state = connection.execute(
            text("SELECT state FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}
        ).scalar_one()
    assert state == "idle"  # nunca "idle in transaction": nao segura snapshots


def test_a_terminated_connection_is_detected_and_frees_the_lock(
    pg_url: str, locks: list[WorkerLock], admin: Engine
) -> None:
    first, second = make_lock(pg_url, locks), make_lock(pg_url, locks)
    assert first.acquire() is True
    with admin.connect() as connection:
        connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": holder_pid(admin)})
        connection.commit()
    assert first.is_alive() is False
    assert first.is_alive() is False  # ja descartada: continua a ser False, sem excecao
    assert second.acquire() is True  # a base ja aceita outro worker


def test_acquiring_twice_with_the_same_object_is_refused(
    pg_url: str, locks: list[WorkerLock]
) -> None:
    lock = make_lock(pg_url, locks)
    assert lock.acquire() is True
    with pytest.raises(RuntimeError, match="ja foi tomado"):
        lock.acquire()
