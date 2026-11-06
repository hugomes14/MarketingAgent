"""Servico de entrevista: submissoes idempotentes, sessao exclusiva e pedido de perfil.

Cada passo de BD e uma transacao curta; a chamada ao motor (e ao fornecedor) corre sempre
fora de qualquer transacao ou lock. So `request_profile`, `confirm_profile` e
`reject_profile` mexem em artefactos, e so por chamada explicita: nem o texto do
utilizador nem a saida do LLM decidem estado de artefacto.
"""

import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.agents.diretor import DIRETOR_DIR
from marketing_agent.agents.diretor.gap_map import (
    GapItem,
    confirm_by_owner,
    initial_gap_map,
    merge_gap_map,
    parse_gap_map,
    serialize_gap_map,
)
from marketing_agent.agents.diretor.schemas import InterviewTurnOutput, PerfilMarca
from marketing_agent.artifacts.store import ArtifactRecord, ArtifactStore
from marketing_agent.clock import Clock
from marketing_agent.config import Settings
from marketing_agent.db.base import new_id
from marketing_agent.db.models import (
    AuditLog,
    InterviewSession,
    InterviewSubmission,
    InterviewTurn,
)
from marketing_agent.harness.engine import AgentEngine, RunResult, RunStatus
from marketing_agent.harness.spec import AgentSpec, load_agent_spec
from marketing_agent.interview.loop import (
    InterviewPayload,
    InterviewTurnLoop,
    ProfileLoop,
    TurnLine,
)

logger = logging.getLogger(__name__)

BLOCKED_MESSAGE = (
    "Limite desta execução atingido. A entrevista fica guardada e pode continuar depois."
)
INTERNAL_ERROR_MESSAGE = "Ocorreu um erro interno ao processar a resposta. Tente novamente."
IN_PROGRESS_MESSAGE = "Esta resposta já está a ser processada. Aguarde e consulte o resultado."
INTERRUPTED_MESSAGE = "Esta submissão foi interrompida. Envie a resposta de novo."
_SCHEMAS: Mapping[str, type[BaseModel]] = {
    "InterviewTurnOutput": InterviewTurnOutput,
    "PerfilMarca": PerfilMarca,
}


class TurnStatus(StrEnum):
    """Estado do resultado de uma resposta submetida."""

    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"


class TurnResult(BaseModel):
    """Resultado de `submit_answer`; e o que fica guardado em `interview_submission.result`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: TurnStatus
    question: str | None
    summary: str
    gap_map: list[GapItem]
    message: str | None
    ready_for_profile: bool
    injection_suspected: bool


@dataclass(frozen=True)
class StartResult:
    """Sessao criada e a primeira pergunta do Diretor."""

    session_id: str
    question: str | None


class SubmissionConflict(Exception):
    """A mesma chave de idempotencia foi usada com outro texto."""


class SessionBusy(Exception):
    """Outra submissao esta a ser processada nesta sessao."""


class InvalidAnswer(ValueError):
    """O texto esta vazio ou excede `max_message_chars`."""


class SessionNotFound(LookupError):
    """Nao existe sessao de entrevista com o identificador pedido."""


class InterviewStartFailed(Exception):
    """A primeira pergunta nao foi obtida; a mensagem e segura para mostrar."""


class InvalidSessionState(Exception):
    """A sessao esta num estado que nao permite a acao pedida."""


class ProfileRequestFailed(Exception):
    """O perfil nao foi gerado; a mensagem e segura para mostrar."""


@dataclass(frozen=True)
class _Reservation:
    """Submissao reservada para processar, ou o resultado a devolver sem processar."""

    submission_id: str
    replay: TurnResult | None


def _load_spec(file_name: str) -> AgentSpec:
    return load_agent_spec(DIRETOR_DIR / file_name, _SCHEMAS)


def _turn_output(run: RunResult) -> InterviewTurnOutput | None:
    if run.status is RunStatus.SUCCEEDED and isinstance(run.output, InterviewTurnOutput):
        return run.output
    return None


def _director_content(output: InterviewTurnOutput) -> str:
    return output.next_question if output.next_question is not None else output.summary


def _describe_failure(run: RunResult) -> tuple[TurnStatus, str]:
    """Estado e mensagem segura de uma execucao sem saida; o detalhe so vai para o log."""
    if run.status is RunStatus.BLOCKED_LIMIT:
        return TurnStatus.BLOCKED, BLOCKED_MESSAGE
    if run.status is RunStatus.NEEDS_HUMAN:
        return TurnStatus.NEEDS_HUMAN, run.message or "A entrevista precisa de decisão humana."
    error_class = (run.message or "desconhecido").split(":")[0]
    logger.error("entrevista: execucao %s terminou em %s (%s)", run.run_id, run.status, error_class)
    return TurnStatus.NEEDS_HUMAN, INTERNAL_ERROR_MESSAGE


def _non_completed(status: TurnStatus, message: str, gap_map: list[GapItem]) -> TurnResult:
    return TurnResult(
        status=status,
        question=None,
        summary="",
        gap_map=gap_map,
        message=message,
        ready_for_profile=False,
        injection_suspected=False,
    )


class InterviewService:
    """Conduz a entrevista do Diretor com estado guardado e respostas idempotentes."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: AgentEngine,
        store: ArtifactStore,
        settings: Settings,
        clock: Clock,
    ) -> None:
        """Guarda os colaboradores e carrega os specs de entrevista e de perfil."""
        self._session_factory = session_factory
        self._engine = engine
        self._store = store
        self._settings = settings
        self._clock = clock
        self._interview_spec = _load_spec("spec_interview.yaml")
        self._profile_spec = _load_spec("spec_profile.yaml")

    def start(self, brand_id: str) -> StartResult:
        """Obtem a primeira pergunta e so entao cria a sessao (sem sessoes orfas)."""
        gap_map = initial_gap_map()
        payload = InterviewPayload((), gap_map)
        loop = InterviewTurnLoop()
        run = self._engine.run(self._interview_spec, brand_id, "interview.start", loop, payload)
        output = _turn_output(run)
        if output is None:
            raise InterviewStartFailed(_describe_failure(run)[1])
        merged = merge_gap_map(gap_map, output.gap_map_updates)
        with self._session_factory.begin() as session:
            row = InterviewSession(
                brand_id=brand_id, state="open", gap_map=serialize_gap_map(merged)
            )
            session.add(row)
            session.flush()
            session.add(self._turn(row.id, 1, "director", _director_content(output), None, run))
            return StartResult(row.id, output.next_question)

    def submit_answer(self, session_id: str, text: str, idempotency_key: str) -> TurnResult:
        """Processa uma resposta uma unica vez por `idempotency_key`.

        Uma chave repetida nunca volta a chamar o motor: devolve o resultado guardado
        (`completed` ou `failed`), `in_progress` se a reclamacao ainda e valida, ou
        `needs_human` se a submissao ficou abandonada. Para tentar de novo usa-se outra chave.
        """
        self._validate_text(text)
        reservation = self._reserve_and_claim(session_id, text, idempotency_key)
        if reservation.replay is not None:
            return reservation.replay
        submission_id = reservation.submission_id
        try:
            return self._process(session_id, submission_id, text)
        except BaseException:
            self._best_effort(
                "interrupcao da submissao",
                submission_id,
                lambda: self._interrupt(session_id, submission_id),
            )
            raise
        finally:
            # Sempre liberta; um processo morto fica a cargo da recuperacao por max_duration_s.
            self._best_effort(
                "libertacao da sessao",
                submission_id,
                lambda: self._release_session(session_id, submission_id),
            )

    def correct_topic(self, session_id: str, topic: str, text: str, actor: str) -> list[GapItem]:
        """Correcao explicita do responsavel: confirma o topico e audita (so o topico)."""
        self._validate_text(text)
        with self._session_factory.begin() as session:
            row = self._get_session(session, session_id, lock=True)
            items = confirm_by_owner(parse_gap_map(row.gap_map), topic, text)
            row.gap_map = serialize_gap_map(items)
            session.add(
                AuditLog(
                    actor=actor,
                    action="interview.correct_topic",
                    subject_type="interview_session",
                    subject_id=session_id,
                    detail={"topic": topic},
                    at=self._clock.now(),
                )
            )
            return items

    def request_profile(self, session_id: str) -> str:
        """Acao explicita: gera o perfil, submete-o para revisao e devolve o id do artefacto.

        Reclama a sessao com um marcador proprio; um segundo pedido em simultaneo levanta
        `SessionBusy` sem chamar o motor. Recusa sessoes `closed` (`InvalidSessionState`).
        """
        marker = new_id()
        brand_id = self._claim_for_profile(session_id, marker)
        try:
            return self._generate_profile(session_id, brand_id)
        finally:
            self._best_effort(
                "libertacao da sessao", marker, lambda: self._release_session(session_id, marker)
            )

    def confirm_profile(self, artifact_id: str, actor: str) -> None:
        """Acao explicita do painel: aprova e ativa o perfil."""
        self._store.approve_and_activate(artifact_id, actor)

    def reject_profile(self, artifact_id: str, actor: str, reason: str) -> None:
        """Acao explicita do painel: rejeita o perfil, mantendo o ativo."""
        self._store.reject(artifact_id, actor, reason)

    def _validate_text(self, text: str) -> None:
        if not 1 <= len(text) <= self._settings.max_message_chars:
            raise InvalidAnswer(
                f"o texto deve ter entre 1 e {self._settings.max_message_chars} caracteres"
            )

    def _process(self, session_id: str, submission_id: str, text: str) -> TurnResult:
        """Corre o motor fora de transacao e grava o desfecho."""
        brand_id, payload = self._load_payload(session_id, text)
        loop = InterviewTurnLoop()
        run = self._engine.run(self._interview_spec, brand_id, "interview.turn", loop, payload)
        output = _turn_output(run)
        if output is None:
            status, message = _describe_failure(run)
            result = _non_completed(status, message, payload.gap_map)
            self._fail_submission(submission_id, result)
            return result
        return self._record_turns(session_id, submission_id, text, run, output)

    def _generate_profile(self, session_id: str, brand_id: str) -> str:
        _, payload = self._load_payload(session_id, None)
        spec, loop = self._profile_spec, ProfileLoop()
        run = self._engine.run(spec, brand_id, "interview.profile", loop, payload)
        if run.status is not RunStatus.SUCCEEDED or not isinstance(run.output, ArtifactRecord):
            raise ProfileRequestFailed(_describe_failure(run)[1])
        self._store.submit(run.output.id, actor="diretor")
        with self._session_factory.begin() as session:
            self._get_session(session, session_id, lock=True).state = "profile_requested"
        return run.output.id

    def _stale_before(self, now: datetime) -> datetime:
        return now - timedelta(seconds=self._interview_spec.limits.max_duration_s)

    def _is_claim_valid(self, row: InterviewSession, holder: str, now: datetime) -> bool:
        """A reclamacao e de `holder` e ainda nao passou `max_duration_s`."""
        return (
            row.busy_submission_id == holder
            and row.busy_since is not None
            and row.busy_since >= self._stale_before(now)
        )

    def _try_claim(
        self, session: Session, row: InterviewSession, marker: str, now: datetime
    ) -> bool:
        """Reclama a sessao (linha ja bloqueada); uma ocupacao vencida e retomada.

        O antigo titular, se ainda `reserved`, fica `failed` com o resultado "interrompida":
        a sua chave deixa de poder voltar a correr.
        """
        previous = row.busy_submission_id
        if (
            previous is not None
            and row.busy_since is not None
            and (row.busy_since >= self._stale_before(now))
        ):
            return False
        row.busy_submission_id = marker
        row.busy_since = now
        if previous is not None:
            interrupted = _non_completed(
                TurnStatus.NEEDS_HUMAN, INTERRUPTED_MESSAGE, parse_gap_map(row.gap_map)
            )
            self._mark_failed(session, previous, interrupted)
        return True

    def _claim_for_profile(self, session_id: str, marker: str) -> str:
        with self._session_factory.begin() as session:
            row = self._get_session(session, session_id, lock=True)
            if row.state == "closed":
                raise InvalidSessionState(f"sessao {session_id} esta fechada")
            if not self._try_claim(session, row, marker, self._clock.now()):
                raise SessionBusy(f"sessao {session_id} ocupada por outra operacao")
            return row.brand_id

    def _reserve_and_claim(self, session_id: str, text: str, idempotency_key: str) -> _Reservation:
        """Numa so transacao curta: reserva a submissao e reclama a sessao, ou resolve a chave.

        Se a sessao estiver ocupada levanta `SessionBusy` e a transacao desfaz-se: nao fica
        linha de submissao e a mesma chave pode ser tentada outra vez. Uma submissao
        `reserved` e portanto sempre dona da reclamacao da sessao. A linha da sessao e
        bloqueada primeiro, o que serializa chamadas concorrentes com a mesma chave.
        """
        digest = hashlib.sha256(text.encode()).hexdigest()
        now = self._clock.now()
        with self._session_factory.begin() as session:
            row = self._get_session(session, session_id, lock=True)
            created = session.execute(
                insert(InterviewSubmission)
                .values(
                    id=new_id(),
                    session_id=session_id,
                    idempotency_key=idempotency_key,
                    text_sha256=digest,
                    status="reserved",
                )
                .on_conflict_do_nothing(index_elements=["session_id", "idempotency_key"])
                .returning(InterviewSubmission.id)
            ).scalar_one_or_none()
            if created is None:
                return self._resolve_existing(session, row, idempotency_key, digest, now)
            if not self._try_claim(session, row, created, now):
                raise SessionBusy(f"sessao {session_id} ocupada por outra submissao")
            return _Reservation(created, None)

    def _resolve_existing(
        self, session: Session, row: InterviewSession, key: str, digest: str, now: datetime
    ) -> _Reservation:
        """Chave ja usada: devolve sempre um resultado, nunca manda reprocessar."""
        existing = session.scalars(
            select(InterviewSubmission)
            .where(
                InterviewSubmission.session_id == row.id, InterviewSubmission.idempotency_key == key
            )
            .with_for_update()
        ).one()
        if existing.text_sha256 != digest:
            raise SubmissionConflict(f"chave {key!r} ja usada com outro texto")
        gap_map = parse_gap_map(row.gap_map)
        if existing.status == "reserved":
            return _Reservation(
                existing.id, self._resolve_reserved(session, row, existing, gap_map, now)
            )
        stored = TurnResult.model_validate(existing.result) if existing.result else None
        return _Reservation(
            existing.id,
            stored or _non_completed(TurnStatus.NEEDS_HUMAN, INTERRUPTED_MESSAGE, gap_map),
        )

    def _resolve_reserved(
        self,
        session: Session,
        row: InterviewSession,
        existing: InterviewSubmission,
        gap_map: list[GapItem],
        now: datetime,
    ) -> TurnResult:
        """`reserved` repetida: `in_progress` se a reclamacao e valida, senao abandonada."""
        if self._is_claim_valid(row, existing.id, now):
            return _non_completed(TurnStatus.IN_PROGRESS, IN_PROGRESS_MESSAGE, gap_map)
        interrupted = _non_completed(TurnStatus.NEEDS_HUMAN, INTERRUPTED_MESSAGE, gap_map)
        self._mark_failed(session, existing.id, interrupted)
        if row.busy_submission_id == existing.id:  # so liberta a reclamacao se for dele
            row.busy_submission_id = None
            row.busy_since = None
        return interrupted

    def _release_session(self, session_id: str, marker: str) -> None:
        """Liberta a sessao so se a ocupacao ainda for de `marker`."""
        with self._session_factory.begin() as session:
            session.execute(
                update(InterviewSession)
                .where(
                    InterviewSession.id == session_id,
                    InterviewSession.busy_submission_id == marker,
                )
                .values(busy_submission_id=None, busy_since=None)
            )

    def _record_turns(
        self,
        session_id: str,
        submission_id: str,
        text: str,
        run: RunResult,
        output: InterviewTurnOutput,
    ) -> TurnResult:
        """Numa transacao: turnos, mapa fundido e submissao fechada, se ainda for a dona.

        Se a submissao ja nao esta `reserved` ou perdeu a reclamacao (foi retomada por
        outra), nao escreve nada e devolve `needs_human`: so uma execucao pode concluir.
        """
        with self._session_factory.begin() as session:
            row = self._get_session(session, session_id, lock=True)
            submission = session.get_one(InterviewSubmission, submission_id, with_for_update=True)
            if submission.status != "reserved" or row.busy_submission_id != submission_id:
                gap_map = parse_gap_map(row.gap_map)
                return _non_completed(TurnStatus.NEEDS_HUMAN, INTERRUPTED_MESSAGE, gap_map)
            merged = merge_gap_map(parse_gap_map(row.gap_map), output.gap_map_updates)
            row.gap_map = serialize_gap_map(merged)
            self._add_turns(session, session_id, submission_id, text, run, output)
            result = TurnResult(
                status=TurnStatus.COMPLETED,
                question=output.next_question,
                summary=output.summary,
                gap_map=merged,
                message=None,
                ready_for_profile=output.ready_for_profile,
                injection_suspected=output.injection_suspected,
            )
            submission.status = "completed"
            submission.result = result.model_dump(mode="json")
            return result

    def _add_turns(
        self,
        session: Session,
        session_id: str,
        submission_id: str,
        text: str,
        run: RunResult,
        output: InterviewTurnOutput,
    ) -> None:
        last_seq = session.scalar(
            select(func.coalesce(func.max(InterviewTurn.seq), 0)).where(
                InterviewTurn.session_id == session_id
            )
        )
        seq = (last_seq or 0) + 1
        session.add(self._turn(session_id, seq, "user", text, submission_id, None))
        content = _director_content(output)
        session.add(self._turn(session_id, seq + 1, "director", content, submission_id, run))

    @staticmethod
    def _turn(
        session_id: str,
        seq: int,
        role: str,
        content: str,
        submission_id: str | None,
        run: RunResult | None,
    ) -> InterviewTurn:
        return InterviewTurn(
            session_id=session_id,
            submission_id=submission_id,
            seq=seq,
            role=role,
            content=content,
            run_id=run.run_id if run is not None else None,
        )

    def _load_payload(
        self, session_id: str, new_answer: str | None
    ) -> tuple[str, InterviewPayload]:
        with self._session_factory() as session:
            row = self._get_session(session, session_id)
            turns = session.scalars(
                select(InterviewTurn)
                .where(InterviewTurn.session_id == session_id)
                .order_by(InterviewTurn.seq)
            )
            history = tuple(
                TurnLine("user" if t.role == "user" else "director", t.content) for t in turns
            )
            return row.brand_id, InterviewPayload(history, parse_gap_map(row.gap_map), new_answer)

    def _fail_submission(self, submission_id: str, result: TurnResult) -> None:
        with self._session_factory.begin() as session:
            self._mark_failed(session, submission_id, result)

    def _interrupt(self, session_id: str, submission_id: str) -> None:
        """Fecha uma submissao ainda `reserved` como interrompida, com resultado guardado."""
        with self._session_factory.begin() as session:
            gap_map = parse_gap_map(self._get_session(session, session_id).gap_map)
            interrupted = _non_completed(TurnStatus.NEEDS_HUMAN, INTERRUPTED_MESSAGE, gap_map)
            self._mark_failed(session, submission_id, interrupted)

    @staticmethod
    def _mark_failed(session: Session, submission_id: str, result: TurnResult) -> None:
        """`reserved -> failed` guardando o resultado devolvido; nunca toca noutros estados."""
        session.execute(
            update(InterviewSubmission)
            .where(
                InterviewSubmission.id == submission_id, InterviewSubmission.status == "reserved"
            )
            .values(status="failed", result=result.model_dump(mode="json"))
        )

    @staticmethod
    def _best_effort(what: str, ref: str, action: Callable[[], None]) -> None:
        """Passo de limpeza: uma falha e registada (classe e id) e nao mascara a original."""
        try:
            action()
        except Exception as exc:
            logger.error("entrevista: %s falhou (%s) para %s", what, type(exc).__name__, ref)

    @staticmethod
    def _get_session(session: Session, session_id: str, lock: bool = False) -> InterviewSession:
        row = session.get(InterviewSession, session_id, with_for_update=lock)
        if row is None:
            raise SessionNotFound(f"sessao de entrevista inexistente: {session_id}")
        return row
