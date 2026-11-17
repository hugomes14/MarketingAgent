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
    EVAL_PACKAGE_JOB,
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


class EvalJobHandler(Protocol):
    """O que o worker precisa de quem processa os jobs `eval_package` da comparacao (Plano 2C)."""

    def handle(self, job: ClaimedJob) -> None:
        """Produz o pacote do item e fecha o job; erros inesperados sobem ao worker."""
        ...

    def fail_job(self, job: ClaimedJob, message: str, retry: bool) -> JobFailure | None:
        """Falha o job (e, se ficou terminal, o item); `None` se a lease se perdeu."""
        ...

    def reconcile(self, brand_id: str | None = None) -> int:
        """Fecha os itens cujo job acabou sem concluir; devolve quantos fechou (idempotente)."""
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
        eval_handler: EvalJobHandler | None = None,
    ) -> None:
        """`sleep` substitui a espera de fila vazia (testes); por omissao `stop.wait`, que acorda
        logo que `stop` e ativado. `recover_reservations` corre no arranque e devolve quantas
        reservas orfas marcou (o chamador conhece o orcamento e o limiar).

        `lock` e o lock de worker unico JA TOMADO por quem chama: o worker para
        (`WorkerLockLost`) se a ligacao que o guarda se perder, porque outro worker poderia
        arrancar. `recover_orphans` corre no arranque e devolve quantos jobs `running` recolocou
        (so e correto com o lock tomado: sao orfaos de um worker morto). Sem eles (testes) so as
        leases vencidas sao recuperadas.

        `eval_handler` trata os jobs `eval_package`; sem ele esses jobs falham sem repeticao
        (`UnknownJobType`) e os itens ficam para o proximo arranque com handler. Com ele, o
        arranque e cada iteracao ociosa fecham os itens cujo job morreu (`reconcile`).
        """
        self._queue = queue
        self._pipeline = pipeline
        self._worker_id = worker_id
        self._sleep = sleep
        self._recover_reservations = recover_reservations
        self._lock = lock
        self._recover_orphans = recover_orphans
        self._eval_handler = eval_handler

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
            outcome = self._dispatch(job)
        except Exception as exc:
            logger.error("job %s falhou: %s", job.id, type(exc).__name__)
            # `None` = lease perdida: o job ja e de outro worker e nao se altera nada.
            self._fail_job(job, describe_error(exc), retry=True)
        else:
            if outcome is not None:
                logger.info("job %s: %s", job.id, outcome)
        return True

    def _dispatch(self, job: ClaimedJob) -> str | None:
        """Entrega o job ao tratador do seu tipo; `None` se nao ha resultado a registar."""
        if job.type == GENERATE_POST_JOB:
            return self._pipeline.generate(job)
        if job.type == REVIEW_POST_JOB:
            return self._pipeline.review_manual(job)
        if job.type == EVAL_PACKAGE_JOB and self._eval_handler is not None:
            self._eval_handler.handle(job)
            return "avaliacao"
        self._pipeline.fail_job(job, "UnknownJobType", retry=False)
        return None

    def _fail_job(self, job: ClaimedJob, message: str, *, retry: bool) -> None:
        """Falha o job pelo tratador do seu tipo (o de avaliacao fecha tambem o item)."""
        if job.type == EVAL_PACKAGE_JOB and self._eval_handler is not None:
            self._eval_handler.fail_job(job, message, retry)
        else:
            self._pipeline.fail_job(job, message, retry)

    def recover(self) -> None:
        """Arranque: devolve a fila os jobs de workers mortos e marca as reservas orfas.

        Raises:
            WorkerLockLost: o lock ja se perdeu; sem ele nao se recolocam jobs de ninguem.
        """
        self._ensure_lock_alive()
        orphans = self._recover_orphans() if self._recover_orphans is not None else 0
        requeued = orphans + self._queue.requeue_expired()
        reservations = self._recover_reservations() if self._recover_reservations else 0
        closed = self._eval_handler.reconcile() if self._eval_handler is not None else 0
        logger.info(
            "arranque: %d job(s) recolocado(s) (%d orfao(s)), %d reserva(s) orfa(s), "
            "%d item(ns) de avaliacao fechado(s)",
            requeued,
            orphans,
            reservations,
            closed,
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
        """Fila vazia: espera e, se continua a correr, recupera jobs de workers que sumiram.

        Um job de avaliacao esgotado (`dead`) por esta recuperacao fecha aqui o seu item e, se
        for o ultimo, a corrida (`reconcile`): e o mecanismo para jobs que morrem fora do worker.
        """
        if self._sleep is None:
            stop.wait(IDLE_WAIT_S)
        else:
            self._sleep(IDLE_WAIT_S)
        if stop.is_set():
            return
        self._ensure_lock_alive()
        try:
            requeued = self._queue.requeue_expired()
            closed = self._eval_handler.reconcile() if self._eval_handler is not None else 0
        except Exception as exc:
            logger.error(
                "worker %s: recolocar jobs falhou: %s", self._worker_id, type(exc).__name__
            )
        else:
            if requeued or closed:
                logger.warning(
                    "worker %s: %d job(s) de lease vencida recolocado(s), %d item(ns) fechado(s)",
                    self._worker_id,
                    requeued,
                    closed,
                )
