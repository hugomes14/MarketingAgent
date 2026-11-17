"""Worker: reclama um job de cada vez e entrega-o ao pipeline; nunca morre por um job mau.

Concorrencia 1, garantida entre processos pelo lock de sessao de `jobs.singleton`: com ele
tomado, um job `running` no arranque e orfao de um worker morto e volta logo a fila. Falhas
inesperadas de um job viram `pipeline.fail_job(..., retry=True)` com `describe_error` (nunca
texto de excecao); o log leva so o nome da classe e o id do job.
"""

import logging
import threading
from collections.abc import Callable
from typing import Protocol

from marketing_agent.content.pipeline import GenerationOutcome
from marketing_agent.jobs.errors import describe_error
from marketing_agent.jobs.queue import (
    GENERATE_POST_JOB,
    REVIEW_POST_JOB,
    ClaimedJob,
    JobFailure,
    JobQueue,
)
from marketing_agent.jobs.singleton import WorkerLockLost

logger = logging.getLogger(__name__)

IDLE_WAIT_S = 2.0


class JobHandler(Protocol):
    """O que o worker precisa de quem processa um job (o `ContentPipeline`)."""

    def generate(self, job: ClaimedJob) -> GenerationOutcome:
        """Processa e fecha o job; erros inesperados sobem ao worker."""
        ...

    def review_manual(self, job: ClaimedJob) -> GenerationOutcome:
        """Reve um upload manual e fecha o job."""
        ...

    def fail_job(self, job: ClaimedJob, message: str, retry: bool) -> JobFailure | None:
        """Falha o job (e, se ficou terminal, anota o motivo no que ele produzia)."""
        ...


class LockGuard(Protocol):
    """O lock de worker unico: o worker so precisa de saber se a ligacao que o guarda vive."""

    def is_alive(self) -> bool:
        """`True` se a ligacao que guarda o lock ainda responde."""
        ...


def require_lock_alive(lock: LockGuard) -> None:
    """Levanta `WorkerLockLost` se a ligacao que guarda o lock deixou de responder.

    Serve de `abort_check` do motor: sem o lock outro worker pode estar a reclamar o job.
    """
    if not lock.is_alive():
        raise WorkerLockLost


class Worker:
    """Consome a fila de `generate_post` ate lhe pedirem para parar."""

    def __init__(
        self,
        queue: JobQueue,
        pipeline: JobHandler,
        worker_id: str,
        sleep: Callable[[float], None] | None = None,
        *,
        recover_reservations: Callable[[], int] | None = None,
        lock: LockGuard | None = None,
        recover_orphans: Callable[[], int] | None = None,
    ) -> None:
        """`sleep` substitui a espera de fila vazia (testes); por omissao `stop.wait`, que acorda
        logo que `stop` e ativado. `recover_reservations` corre no arranque e devolve quantas
        reservas orfas marcou (o chamador conhece o orcamento e o limiar).

        `lock` e o lock de worker unico JA TOMADO por quem chama: o worker para
        (`WorkerLockLost`) se a ligacao que o guarda se perder, porque outro worker poderia
        arrancar. `recover_orphans` corre no arranque e devolve quantos jobs `running` recolocou
        (so e correto com o lock tomado: sao orfaos de um worker morto). Sem eles (testes) so as
        leases vencidas sao recuperadas.
        """
        self._queue = queue
        self._pipeline = pipeline
        self._worker_id = worker_id
        self._sleep = sleep
        self._recover_reservations = recover_reservations
        self._lock = lock
        self._recover_orphans = recover_orphans

    def _ensure_lock_alive(self) -> None:
        """Sem o lock nao se reclama nada: levanta `WorkerLockLost` se a ligacao se perdeu."""
        if self._lock is not None:
            require_lock_alive(self._lock)

    def run_once(self) -> bool:
        """Processa no maximo um job; `False` se a fila estava vazia.

        Raises:
            WorkerLockLost: o lock de worker unico perdeu-se (verificado antes de reclamar).
        """
        self._ensure_lock_alive()
        job = self._queue.claim(self._worker_id)
        if job is None:
            return False
        try:
            if job.type == GENERATE_POST_JOB:
                outcome = self._pipeline.generate(job)
            elif job.type == REVIEW_POST_JOB:
                outcome = self._pipeline.review_manual(job)
            else:
                self._pipeline.fail_job(job, "UnknownJobType", retry=False)
                return True
        except Exception as exc:
            logger.error("job %s falhou: %s", job.id, type(exc).__name__)
            # `None` = lease perdida: o job ja e de outro worker e nao se altera nada.
            self._pipeline.fail_job(job, describe_error(exc), retry=True)
        else:
            logger.info("job %s: %s", job.id, outcome)
        return True

    def recover(self) -> None:
        """Arranque: devolve a fila os jobs de workers mortos e marca as reservas orfas.

        Raises:
            WorkerLockLost: o lock ja se perdeu; sem ele nao se recolocam jobs de ninguem.
        """
        self._ensure_lock_alive()
        orphans = self._recover_orphans() if self._recover_orphans is not None else 0
        requeued = orphans + self._queue.requeue_expired()
        reservations = self._recover_reservations() if self._recover_reservations else 0
        logger.info(
            "arranque: %d job(s) recolocado(s) (%d orfao(s)), %d reserva(s) orfa(s)",
            requeued,
            orphans,
            reservations,
        )

    def run_forever(self, stop: threading.Event) -> None:
        """Recupera o que um worker morto deixou e processa jobs ate `stop` ser ativado.

        `stop` so e verificado entre jobs: o job em curso termina (a lease cobre a duracao maxima).

        Raises:
            WorkerLockLost: o lock de worker unico perdeu-se; nao se reclama mais nenhum job.
        """
        self.recover()
        while not stop.is_set():
            if not self._step():
                self._idle(stop)

    def _step(self) -> bool:
        """Um `run_once` a prova de falhas da infraestrutura (base em baixo, por exemplo)."""
        try:
            return self.run_once()
        except WorkerLockLost:
            raise
        except Exception as exc:
            logger.error("worker %s: erro fora do job: %s", self._worker_id, type(exc).__name__)
            return False

    def _idle(self, stop: threading.Event) -> None:
        """Fila vazia: espera e, se continua a correr, recupera jobs de workers que sumiram."""
        if self._sleep is None:
            stop.wait(IDLE_WAIT_S)
        else:
            self._sleep(IDLE_WAIT_S)
        if stop.is_set():
            return
        self._ensure_lock_alive()
        try:
            requeued = self._queue.requeue_expired()
        except Exception as exc:
            logger.error(
                "worker %s: recolocar jobs falhou: %s", self._worker_id, type(exc).__name__
            )
        else:
            if requeued:
                logger.warning(
                    "worker %s: %d job(s) de lease vencida recolocado(s)", self._worker_id, requeued
                )
