"""Worker unico por base de dados: lock de sessao do Postgres numa ligacao dedicada.

O lock (`pg_try_advisory_lock`) vive enquanto a ligacao viver: se o processo morre, o servidor
liberta-o sozinho, e e isso que permite ao worker seguinte tratar os jobs `running` que
encontra como orfaos (o painel nunca reclama jobs). Os locks consultivos sao por BASE DE DADOS,
nao por servidor: bases diferentes no mesmo servidor nao se bloqueiam.

A ligacao e propria (sem pool) e fica em autocommit: nao tem transacao aberta, por isso nao
segura nenhum snapshot nem bloqueia migracoes ou limpezas.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

# Chave arbitraria e fixa do lock do worker (bigint positivo); todos os workers usam a mesma.
WORKER_LOCK_KEY = 0x4D41_574B_5200_0001
ALREADY_RUNNING_MESSAGE = "ja existe um worker a correr nesta base de dados"
LOCK_LOST_MESSAGE = (
    "a ligacao que guarda o lock do worker perdeu-se: outro worker pode arrancar; a terminar"
)
# Uma ligacao morta sem FIN (particao de rede silenciosa) tem de falhar em dezenas de segundos:
# `tcp_user_timeout` (ms) limita dados nao confirmados (o `SELECT 1` nao fica preso) e
# `connect_timeout` (s) a abertura da ligacao; os keepalives apanham a ligacao parada.
_KEEPALIVES = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "tcp_user_timeout": 30_000,
    "connect_timeout": 10,
}


class WorkerAlreadyRunning(RuntimeError):
    """Outro processo ja detem o lock do worker nesta base de dados."""

    def __init__(self) -> None:
        """Mensagem fixa: nada do servidor ou da base entra no texto."""
        super().__init__(ALREADY_RUNNING_MESSAGE)


class WorkerLockLost(RuntimeError):
    """A ligacao que guardava o lock perdeu-se: o worker deixa de poder reclamar jobs."""

    def __init__(self) -> None:
        """Mensagem fixa: nada do servidor ou da base entra no texto."""
        super().__init__(LOCK_LOST_MESSAGE)


class WorkerLock:
    """Lock de sessao do worker: ligacao dedicada aberta por `acquire`, fechada por `release`."""

    def __init__(self, database_url: str, key: int = WORKER_LOCK_KEY) -> None:
        """Prepara o lock sobre `database_url`; `key` so muda nos testes."""
        self._engine = create_engine(
            database_url, poolclass=NullPool, connect_args=dict(_KEEPALIVES)
        )
        self._key = key
        self._connection: Connection | None = None

    def acquire(self) -> bool:
        """Tenta tomar o lock sem esperar; `False` (sem ligacao aberta) se outro o detem."""
        if self._connection is not None:
            raise RuntimeError("o lock do worker ja foi tomado por este objeto")
        connection = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            held = connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": self._key}
            ).scalar_one()
        except BaseException:
            connection.close()
            raise
        if not held:
            connection.close()
            return False
        self._connection = connection
        return True

    def is_alive(self) -> bool:
        """A ligacao ainda responde? (`SELECT 1`, barato); se nao, descarta-a e devolve `False`."""
        if self._connection is None:
            return False
        try:
            self._connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            logger.error("lock do worker: ligacao perdida (%s)", type(exc).__name__)
            self._discard()
            return False
        return True

    def release(self) -> None:
        """Fecha a ligacao (o servidor liberta o lock) e o motor; seguro de repetir."""
        self._discard()
        self._engine.dispose()

    def _discard(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            connection.close()
        except SQLAlchemyError as exc:  # a ligacao ja estava morta: o lock ja nao existe
            logger.warning("lock do worker: fecho falhou (%s)", type(exc).__name__)


@contextmanager
def hold_worker_lock(database_url: str, key: int = WORKER_LOCK_KEY) -> Iterator[WorkerLock]:
    """Toma o lock do worker durante o bloco e liberta-o ao sair.

    Raises:
        WorkerAlreadyRunning: outro processo ja detem o lock nesta base de dados.
    """
    lock = WorkerLock(database_url, key)
    try:
        if not lock.acquire():
            raise WorkerAlreadyRunning
        yield lock
    finally:
        lock.release()
