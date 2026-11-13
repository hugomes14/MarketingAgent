"""Fila de trabalhos em Postgres: reclamacao exclusiva, lease, tentativas e cancelamento.

A fila nao conhece o conteudo dos payloads (exceto o `post_id` dos jobs de um post, em
`has_active_for_post_in` e `cancel_for_post_in`). Cada operacao e uma transacao curta; nenhum
lock e mantido enquanto o worker trabalha. A posse de um job prova-se pelo `lease_token`: quem
o perde (lease expirada, job cancelado) nao consegue concluir nem falhar o job.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import ColumnElement, case, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import Clock
from marketing_agent.db.base import new_id
from marketing_agent.db.models import ACTIVE_JOB_PREDICATE_SQL, ACTIVE_JOB_STATUSES, Job, Post

GENERATE_POST_JOB = "generate_post"
REVIEW_POST_JOB = "review_post"
# Jobs que escrevem uma revisao de um post: ambos bloqueiam e sao cancelados pelas acoes do dono.
POST_JOB_TYPES = (GENERATE_POST_JOB, REVIEW_POST_JOB)
# Pacote da comparacao (Plano 2C): NAO tem post. Fica fora de `POST_JOB_TYPES` (as acoes do dono
# nao o veem nem o cancelam), cede a vez aos jobs de post em `claim` e e recuperado como eles.
EVAL_PACKAGE_JOB = "eval_package"
RECOVERABLE_JOB_TYPES = (*POST_JOB_TYPES, EVAL_PACKAGE_JOB)
RETRY_BACKOFF = timedelta(seconds=30)  # espera = RETRY_BACKOFF x tentativas ja feitas
MAX_ERROR_CHARS = 200
_LEASE_EXPIRED_ERROR = "LeaseExpired: lease expirada sem conclusao"
_ORPHANED_ERROR = "WorkerLost: o worker anterior terminou sem concluir o job"
_ACTIVE_FILTER = text(ACTIVE_JOB_PREDICATE_SQL)
# Campos limpos sempre que o job deixa de estar em posse de um worker.
_RELEASED_LOCK = {"locked_by": None, "locked_until": None, "lease_token": None}


@dataclass(frozen=True)
class ClaimedJob:
    """Job reclamado por um worker; `lease_token` prova a posse nas operacoes seguintes."""

    id: str
    type: str
    payload: Mapping[str, object]
    attempts: int
    lease_token: str


def truncate_error(message: str) -> str:
    """Limita `message` a `MAX_ERROR_CHARS` caracteres, numa so linha."""
    return " ".join(message.split())[:MAX_ERROR_CHARS]


def _active_post_jobs(post_id: str) -> tuple[ColumnElement[bool], ...]:
    """Condicoes dos jobs ativos de `post_id` (`POST_JOB_TYPES`: geracao e revisao visual)."""
    return (
        Job.type.in_(POST_JOB_TYPES),
        Job.status.in_(ACTIVE_JOB_STATUSES),
        Job.payload["post_id"].astext == post_id,
    )


JobFailure = Literal["queued", "dead", "failed"]


def _status_after_failure(job: Job, retry: bool) -> JobFailure:
    """`queued` se ainda ha tentativas; `dead` se esgotou; `failed` se a falha nao e repetivel."""
    if not retry:
        return "failed"
    return "queued" if job.attempts < job.max_attempts else "dead"


class JobQueue:
    """Fila de trabalhos; o estado vive todo na tabela `job`."""

    def __init__(
        self, session_factory: sessionmaker[Session], clock: Clock, lease: timedelta
    ) -> None:
        """`lease` e a duracao da posse de um job reclamado; quem chama decide o valor."""
        self._session_factory = session_factory
        self._clock = clock
        self._lease = lease

    def enqueue_in(
        self, session: Session, type: str, payload: Mapping[str, object], dedupe_key: str
    ) -> str | None:
        """Cria um job `queued` na transacao do chamador; `None` se a chave ja tem job ativo."""
        now = self._clock.now()
        statement = (
            insert(Job)
            .values(
                id=new_id(),
                type=type,
                payload=dict(payload),
                status="queued",
                run_at=now,
                attempts=0,
                max_attempts=3,
                dedupe_key=dedupe_key,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"], index_where=_ACTIVE_FILTER)
            .returning(Job.id)
        )
        return session.execute(statement).scalar_one_or_none()

    def claim(self, worker_id: str) -> ClaimedJob | None:
        """Reclama o job `queued` mais antigo com `run_at` vencido; `None` se nao houver.

        Os jobs de avaliacao so passam a frente se nao ha nenhum outro pronto: um lote semanal
        nunca espera por uma corrida da comparacao (o job em curso, esse, termina primeiro).
        """
        now = self._clock.now()
        statement = (
            select(Job)
            .where(Job.status == "queued", Job.run_at <= now)
            .order_by(case((Job.type == EVAL_PACKAGE_JOB, 1), else_=0), Job.run_at, Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        with self._session_factory.begin() as session:
            job = session.execute(statement).scalar_one_or_none()
            if job is None:
                return None
            job.status = "running"
            job.locked_by = worker_id
            job.locked_until = now + self._lease
            job.lease_token = new_id()
            job.attempts += 1
            job.updated_at = now
            return ClaimedJob(job.id, job.type, job.payload, job.attempts, job.lease_token)

    def complete_in(self, session: Session, job: ClaimedJob) -> bool:
        """Marca `succeeded` na transacao do chamador; `False` se o worker perdeu a lease."""
        statement = (
            update(Job)
            .where(Job.id == job.id, Job.lease_token == job.lease_token, Job.status == "running")
            .values(status="succeeded", updated_at=self._clock.now(), **_RELEASED_LOCK)
            .returning(Job.id)
        )
        return session.execute(statement).first() is not None

    def holds_lease_in(self, session: Session, job: ClaimedJob) -> bool:
        """O job continua `running` com o `lease_token` deste worker? Leitura sem lock."""
        statement = select(Job.id).where(
            Job.id == job.id, Job.lease_token == job.lease_token, Job.status == "running"
        )
        return session.execute(statement).first() is not None

    def fail(self, job: ClaimedJob, error: str, retry: bool) -> bool:
        """Regista a falha numa transacao propria; `False` se o worker perdeu a lease."""
        with self._session_factory.begin() as session:
            return self.fail_in(session, job, error, retry) is not None

    def fail_in(
        self, session: Session, job: ClaimedJob, error: str, retry: bool
    ) -> JobFailure | None:
        """Regista a falha na transacao do chamador e devolve o estado resultante.

        `queued` (nova tentativa), `dead` (tentativas esgotadas) ou `failed` (sem `retry`).
        Devolve `None`, sem alterar nada, se o worker perdeu a lease (nao e erro: o job ja
        pertence a outro worker ou foi cancelado). Quem tambem escreve no post deve bloquear o
        post ANTES de chamar (ordem posts -> jobs, como a aprovacao do plano).
        """
        now = self._clock.now()
        statement = (
            select(Job)
            .where(Job.id == job.id, Job.lease_token == job.lease_token, Job.status == "running")
            .with_for_update()
        )
        row = session.execute(statement).scalar_one_or_none()
        if row is None:
            return None
        status = _status_after_failure(row, retry)
        row.status = status
        if status == "queued":
            row.run_at = now + RETRY_BACKOFF * row.attempts
        row.locked_by = None
        row.locked_until = None
        row.lease_token = None
        row.last_error = truncate_error(error)
        row.updated_at = now
        return status

    def requeue_expired(self) -> int:
        """Devolve a `queued` (ou `dead`, se esgotou) os `running` com lease vencida; conta-os.

        Os posts dos que ficam `dead` e ainda estao `drafting` recebem `last_error` fixo (senao
        ficavam em geracao para sempre). Ordem dos locks: posts primeiro, depois os jobs. Um item
        de avaliacao cujo job fica `dead` e fechado pelo `reconcile` do tratador de avaliacao.
        """
        return self._requeue_running(expired_only=True, error=_LEASE_EXPIRED_ERROR, grace=None)

    def requeue_orphans(self, *, grace: timedelta) -> int:
        """Devolve a `queued` (ou `dead`) TODOS os jobs de post e de avaliacao `running`.

        So e correto com o lock do worker unico tomado (`jobs.singleton`): o painel nunca
        reclama jobs, por isso um `running` e de um worker que morreu. Mantem `attempts` (a
        tentativa foi gasta na reclamacao) e trata posts e locks como `requeue_expired`. NAO toca
        nas reservas de custo: as do worker morto continuam a contar (a quota de imagens deriva
        da base) ate ao limiar de recuperacao, para nunca libertar dinheiro de uma chamada que
        pode ter sido faturada.

        Os que voltam a `queued` so podem ser reclamados depois de `grace` (ver
        `startup.orphan_grace`, que a deriva do multiplicador de tempos): se o worker
        anterior ainda esta vivo (perdeu o lock mas nao morreu), a chamada paga em voo tem de
        terminar antes de outro comecar o mesmo job.
        """
        return self._requeue_running(expired_only=False, error=_ORPHANED_ERROR, grace=grace)

    def _requeue_running(self, *, expired_only: bool, error: str, grace: timedelta | None) -> int:
        """Recoloca os `running` candidatos; devolve quantos mudaram de estado."""
        now = self._clock.now()
        with self._session_factory.begin() as session:
            candidates = self._running_candidates(session, now, expired_only)
            if not candidates:
                return 0
            self._lock_posts(session, {post_id for _, post_id in candidates if post_id})
            updated = self._requeue(
                session, [job_id for job_id, _ in candidates], now, error, expired_only, grace
            )
            self._mark_posts_failed(
                session,
                {
                    post_id
                    for job_id, post_id in candidates
                    if post_id and updated.get(job_id) == "dead"
                },
                error,
            )
            return len(updated)

    @staticmethod
    def _running_candidates(
        session: Session, now: datetime, expired_only: bool
    ) -> list[tuple[str, str | None]]:
        """`running` com lease vencida (qualquer tipo) ou, sem `expired_only`, de tipo recuperavel.

        Os jobs de avaliacao nao tem `post_id` (a segunda coluna vem `None`): nao ha post a
        bloquear, por isso a ordem posts -> jobs dos jobs de post mantem-se.
        """
        statement = select(Job.id, Job.payload["post_id"].astext).where(Job.status == "running")
        if expired_only:
            statement = statement.where(Job.locked_until < now)
        else:
            statement = statement.where(Job.type.in_(RECOVERABLE_JOB_TYPES))
        return [(job_id, post_id) for job_id, post_id in session.execute(statement)]

    @staticmethod
    def _lock_posts(session: Session, post_ids: set[str]) -> None:
        """Bloqueia os posts por ordem de id (sem ciclos entre transacoes iguais).

        Sem posts (jobs de avaliacao, ou de outros tipos) nem sequer se consulta a tabela.
        """
        if not post_ids:
            return
        statement = select(Post.id).where(Post.id.in_(sorted(post_ids))).order_by(Post.id)
        session.execute(statement.with_for_update()).all()

    @staticmethod
    def _requeue(
        session: Session,
        job_ids: list[str],
        now: datetime,
        error: str,
        expired_only: bool,
        grace: timedelta | None,
    ) -> dict[str, str]:
        """Aplica a recolocacao aos candidatos (ainda `running` e, se pedido, vencidos).

        Primeiro bloqueia as linhas dos jobs por ordem de id (depois dos posts), para duas
        transacoes iguais nunca se esperarem em ciclo; so depois atualiza.
        """
        conditions = [Job.id.in_(job_ids), Job.status == "running"]
        if expired_only:
            conditions.append(Job.locked_until < now)
        locked = session.execute(
            select(Job.id).where(*conditions).order_by(Job.id).with_for_update()
        ).scalars()
        exhausted = Job.attempts >= Job.max_attempts
        values: dict[str, object] = {
            "status": case((exhausted, "dead"), else_="queued"),
            "last_error": error,
            "updated_at": now,
            **_RELEASED_LOCK,
        }
        if grace is not None:
            values["run_at"] = now + grace  # `claim` so reclama com `run_at` vencido
        statement = (
            update(Job)
            .where(Job.id.in_(list(locked)))
            .values(**values)
            .returning(Job.id, Job.status)
        )
        return {job_id: status for job_id, status in session.execute(statement)}

    @staticmethod
    def _mark_posts_failed(session: Session, post_ids: set[str], error: str) -> None:
        """`last_error` fixo nos posts ainda `drafting` (os outros ja seguiram a sua vida)."""
        if not post_ids:
            return
        statement = (
            update(Post)
            .where(Post.id.in_(post_ids), Post.lifecycle == "drafting")
            .values(last_error=error)
        )
        session.execute(statement)

    def has_active_for_post_in(self, session: Session, post_id: str) -> bool:
        """Ha um `generate_post` ou `review_post` ativo (`queued`/`running`) do post? Sem lock."""
        statement = select(Job.id).where(*_active_post_jobs(post_id)).limit(1)
        return session.execute(statement).first() is not None

    def latest_generate_payload_in(self, session: Session, post_id: str) -> Mapping[str, object]:
        """Payload do `generate_post` mais recente do post (em qualquer estado); vazio se nao ha.

        Serve para repetir uma geracao falhada com o mesmo pedido. Chamar com o post bloqueado.
        """
        statement = (
            select(Job.payload)
            .where(Job.type == GENERATE_POST_JOB, Job.payload["post_id"].astext == post_id)
            .order_by(Job.created_at.desc(), Job.updated_at.desc())
            .limit(1)
        )
        return session.execute(statement).scalar_one_or_none() or {}

    def cancel_for_post_in(self, session: Session, post_id: str) -> int:
        """Cancela os `generate_post` e `review_post` ativos do post; devolve quantos.

        Limpar o `lease_token` impede que um worker a meio do job o conclua depois.
        """
        statement = (
            update(Job)
            .where(*_active_post_jobs(post_id))
            .values(status="cancelled", updated_at=self._clock.now(), **_RELEASED_LOCK)
            .returning(Job.id)
        )
        return len(session.execute(statement).all())
