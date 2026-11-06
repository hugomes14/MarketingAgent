"""Motor de execucao de agentes: passo e reserva por chamada, limites e erros classificados.

O motor nunca decide estado de artefactos pela saida do LLM: so o codigo do Loop cria
artefactos (sempre em `draft`). Cada chamada ao fornecedor corre fora de qualquer transacao;
passo, reserva e acerto sao transacoes curtas e proprias.
"""

import logging
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactRecord, ArtifactStore, ArtifactType
from marketing_agent.budget.controller import (
    BudgetController,
    ReservationRequest,
    RunCostLimitExceeded,
)
from marketing_agent.clock import Clock
from marketing_agent.config import RoleRoute
from marketing_agent.db.models import AgentRun, AgentStep, CostEntry
from marketing_agent.harness.spec import AgentSpec, assert_can_write
from marketing_agent.harness.validators import VALIDATORS
from marketing_agent.llm.router import LlmRouter, MissingCapability, RouteNotConfigured
from marketing_agent.llm.types import (
    LlmClient,
    LlmNetworkError,
    LlmRequest,
    LlmResponse,
    LlmUnknownOutcome,
    Message,
    StopReason,
    TextPart,
)

logger = logging.getLogger(__name__)

MAX_INVALID_ATTEMPTS = 3  # 1 tentativa + 2 repeticoes
NETWORK_BACKOFF_S = (1.0, 2.0)  # uma repeticao por espera
MAX_ERROR_SUMMARY_CHARS = 500
CHARS_PER_TOKEN = 3  # estimativa conservadora de tokens de entrada

StepError = Literal["invalid_output", "refusal", "truncated", "network", "limit", "unknown_outcome"]


class RunStatus(StrEnum):
    """Estado final de uma execucao."""

    SUCCEEDED = "succeeded"
    BLOCKED_LIMIT = "blocked_limit"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


@dataclass(frozen=True)
class RunResult:
    """Resultado de `AgentEngine.run`; `run_id` e vazio se a execucao nem chegou a ser criada."""

    status: RunStatus
    run_id: str
    output: object | None
    message: str | None


class RunLimitExceeded(Exception):
    """Um limite cumulativo da execucao (passos, tokens ou duracao) foi atingido."""


class NeedsHuman(Exception):
    """A execucao nao pode continuar sozinha e precisa de decisao humana."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class _Services:
    """Colaboradores partilhados por uma execucao."""

    client: LlmClient
    route: RoleRoute
    session_factory: sessionmaker[Session]
    budget: BudgetController
    store: ArtifactStore
    clock: Clock
    sleep: Callable[[float], None]


@dataclass(frozen=True)
class _Retry:
    """Tentativa falhada mas repetivel; `text` e a resposta invalida, vazia em falha de rede."""

    kind: Literal["invalid_output", "network"]
    errors: str
    text: str


@dataclass
class _RetryState:
    """Contadores independentes de repeticoes de uma chamada a `call_llm`."""

    invalid: int = 0
    network: int = 0


@dataclass(frozen=True)
class _Verdict[M: BaseModel]:
    """Classificacao de uma resposta: `error_class` None quando `parsed` e valido."""

    error_class: StepError | None
    errors: str
    parsed: M | None


def _estimate_input_tokens(system: str, messages: list[Message]) -> int:
    """Teto grosseiro: caracteres de `system` e de todas as partes de texto, por 3 por token."""
    chars = len(system)
    for message in messages:
        chars += sum(len(part.text) for part in message.content if isinstance(part, TextPart))
    return -(-chars // CHARS_PER_TOKEN)  # divisao com arredondamento para cima


def _summarize_validation(exc: ValidationError) -> str:
    """Resume os erros por campo, sem ecoar a entrada (`include_input=False`)."""
    parts = [
        f"{'.'.join(str(p) for p in err['loc']) or '(raiz)'}: {err['msg']}"
        for err in exc.errors(include_input=False)
    ]
    return "; ".join(parts)[:MAX_ERROR_SUMMARY_CHARS]


class RunContext:
    """Contexto de uma execucao entregue ao Loop: chamadas ao LLM e criacao de artefactos."""

    def __init__(
        self,
        *,
        run_id: str,
        brand_id: str,
        spec: AgentSpec,
        started_at: datetime,
        services: _Services,
    ) -> None:
        self.run_id = run_id
        self.brand_id = brand_id
        self.spec = spec
        self._started_at = started_at
        self._s = services
        self._steps_done = 0
        self._tokens_used = 0

    def call_llm[M: BaseModel](self, system: str, messages: list[Message], schema: type[M]) -> M:
        """Chama o LLM e devolve a saida validada por `schema` e pelos `spec.validators`.

        Repete saidas invalidas (ate 3 tentativas) e falhas de rede (ate 2 repeticoes);
        recusa, truncagem e desfecho desconhecido levantam `NeedsHuman` sem repetir.
        """
        state = _RetryState()
        history = list(messages)
        while True:
            result = self._attempt(system, history, schema)
            if not isinstance(result, _Retry):
                return result
            history = self._retry_policy(result, state, history)

    def create_artifact(
        self,
        type: ArtifactType,  # noqa: A002
        payload: Mapping[str, object],
        parent_id: str | None = None,
    ) -> ArtifactRecord:
        """Cria um artefacto em `draft` da marca; `PermissionDenied` se o spec nao o permite."""
        assert_can_write(self.spec, type)
        return self._s.store.create(
            self.brand_id, type, payload, parent_id=parent_id, produced_by_run_id=self.run_id
        )

    def _attempt[M: BaseModel](
        self, system: str, messages: list[Message], schema: type[M]
    ) -> M | _Retry:
        self._check_limits()
        estimate = _estimate_input_tokens(system, messages)
        request = LlmRequest(
            model_id=self._s.route.model_id,
            system=system,
            messages=messages,
            max_output_tokens=self.spec.max_output_tokens,
            json_schema=schema.model_json_schema(),
        )
        step_id = self._open_step(f"{len(messages)} mensagens, ~{estimate} tokens de entrada")
        reservation_id = self._reserve(step_id, estimate)
        response = self._send(step_id, reservation_id, request)
        if isinstance(response, _Retry):
            return response
        with self._unknown_if_unsettled(step_id, reservation_id):
            self._settle(reservation_id, response)
        verdict = self._classify_or_close(step_id, response, schema)
        self._close_step(step_id, verdict.error_class)
        return self._outcome(verdict, response)

    def _classify[M: BaseModel](self, response: LlmResponse, schema: type[M]) -> _Verdict[M]:
        if response.stop_reason is StopReason.refusal:
            return _Verdict("refusal", "o modelo recusou responder", None)
        if response.stop_reason is StopReason.max_tokens:
            return _Verdict("truncated", "resposta truncada por max_tokens", None)
        try:
            parsed = schema.model_validate_json(response.text)
        except ValidationError as exc:
            return _Verdict("invalid_output", _summarize_validation(exc), None)
        violations = [v for name in self.spec.validators for v in VALIDATORS[name](parsed)]
        if violations:
            return _Verdict("invalid_output", "; ".join(violations), None)
        return _Verdict(None, "", parsed)

    @staticmethod
    def _outcome[M: BaseModel](verdict: _Verdict[M], response: LlmResponse) -> M | _Retry:
        if verdict.parsed is not None:
            return verdict.parsed
        if verdict.error_class == "invalid_output":
            return _Retry("invalid_output", verdict.errors, response.text)
        raise NeedsHuman(verdict.errors)

    def _retry_policy(
        self, retry: _Retry, state: _RetryState, history: list[Message]
    ) -> list[Message]:
        """Decide se repete: devolve a conversa da proxima tentativa ou levanta `NeedsHuman`."""
        if retry.kind == "network":
            if state.network >= len(NETWORK_BACKOFF_S):
                raise NeedsHuman(f"rede indisponivel apos {state.network + 1} tentativas")
            self._s.sleep(NETWORK_BACKOFF_S[state.network])
            state.network += 1
            return history
        state.invalid += 1
        if state.invalid >= MAX_INVALID_ATTEMPTS:
            raise NeedsHuman(
                f"saida invalida apos {MAX_INVALID_ATTEMPTS} tentativas: {retry.errors}"
            )
        feedback = (
            f"A resposta anterior foi invalida: {retry.errors}. Responde apenas com JSON valido."
        )
        return [
            *history,
            Message("assistant", [TextPart(retry.text)]),
            Message("user", [TextPart(feedback)]),
        ]

    def _check_limits(self) -> None:
        limits = self.spec.limits
        if self._steps_done >= limits.max_steps:
            raise RunLimitExceeded(f"limite de {limits.max_steps} passos atingido")
        if self._tokens_used >= limits.max_tokens:
            raise RunLimitExceeded(
                f"limite de {limits.max_tokens} tokens atingido ({self._tokens_used} usados)"
            )
        elapsed = self._s.clock.now() - self._started_at
        if elapsed > timedelta(seconds=limits.max_duration_s):
            raise RunLimitExceeded(
                f"limite de {limits.max_duration_s} s de duracao excedido ({elapsed})"
            )

    def _open_step(self, input_summary: str) -> str:
        seq = self._steps_done + 1
        step = AgentStep(
            run_id=self.run_id, seq=seq, kind="llm", name="call_llm", input_summary=input_summary
        )
        with self._s.session_factory.begin() as session:
            session.add(step)
            session.flush()
            step_id = step.id
        self._steps_done = seq
        return step_id

    def _close_step(self, step_id: str, error_class: StepError | None) -> None:
        with self._s.session_factory.begin() as session:
            session.get_one(AgentStep, step_id).error_class = error_class

    def _reserve(self, step_id: str, input_tokens_estimate: int) -> str:
        request = ReservationRequest(
            brand_id=self.brand_id,
            role=self.spec.role,
            run_id=self.run_id,
            step_id=step_id,
            provider=self._s.route.provider,
            model_id=self._s.route.model_id,
            input_tokens_estimate=input_tokens_estimate,
            max_output_tokens=self.spec.max_output_tokens,
        )
        try:
            return self._s.budget.reserve(request, self.spec.limits.max_cost_usd).id
        except RunCostLimitExceeded:
            self._close_step(step_id, "limit")
            raise

    def _send(self, step_id: str, reservation_id: str, request: LlmRequest) -> LlmResponse | _Retry:
        """Chama o fornecedor (sem transacao aberta); falha de rede liberta a reserva."""
        try:
            with self._unknown_if_unsettled(step_id, reservation_id):
                return self._s.client.complete(request)
        except LlmNetworkError as exc:
            self._s.budget.release(reservation_id)
            self._close_step(step_id, "network")
            return _Retry("network", str(exc), "")
        except LlmUnknownOutcome as exc:
            self._s.budget.mark_unknown(reservation_id)
            self._close_step(step_id, "unknown_outcome")
            raise NeedsHuman(f"desfecho desconhecido da chamada ao LLM: {exc}") from exc

    @contextmanager
    def _unknown_if_unsettled(self, step_id: str, reservation_id: str) -> Iterator[None]:
        """Qualquer falha entre a reserva e o acerto deixa o desfecho desconhecido.

        Conservador: a chamada pode ter sido faturada, entao nao se liberta a reserva.
        Os erros de transporte do contrato tem tratamento proprio em `_send`. A excecao
        original e sempre a que sobe, mesmo que o registo da falha tambem falhe.
        """
        try:
            yield
        except (LlmNetworkError, LlmUnknownOutcome):
            raise
        except BaseException:
            self._record_unknown(step_id, reservation_id)
            raise

    def _record_unknown(self, step_id: str, reservation_id: str) -> None:
        try:
            self._s.budget.mark_unknown(reservation_id)
        except Exception as exc:
            logger.error("execucao %s: mark_unknown falhou (%s)", self.run_id, type(exc).__name__)
        self._close_step_quietly(step_id, "unknown_outcome")

    def _close_step_quietly(self, step_id: str, error_class: StepError) -> None:
        """Fecha o passo sem deixar uma falha secundaria mascarar a excecao original."""
        try:
            self._close_step(step_id, error_class)
        except Exception as exc:
            logger.error("execucao %s: fecho do passo falhou (%s)", self.run_id, type(exc).__name__)

    def _classify_or_close[M: BaseModel](
        self, step_id: str, response: LlmResponse, schema: type[M]
    ) -> _Verdict[M]:
        """Classifica; se o codigo de validacao levantar, a reserva ja foi acertada."""
        try:
            return self._classify(response, schema)
        except Exception:
            self._close_step_quietly(step_id, "invalid_output")
            raise

    def _settle(self, reservation_id: str, response: LlmResponse) -> None:
        self._s.budget.settle(reservation_id, response.usage, response.provider_request_id)
        self._tokens_used += response.usage.input_tokens + response.usage.output_tokens


class Loop[TIn, TOut](Protocol):
    """Ciclo de um agente: recebe o contexto e a entrada, devolve a saida do papel."""

    def run(self, ctx: RunContext, payload: TIn) -> TOut:
        """Executa o ciclo; so ele cria artefactos, via `ctx.create_artifact`."""
        ...


class AgentEngine:
    """Executa um Loop sob um `AgentSpec`, registando a execucao, os passos e os custos."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        router: LlmRouter,
        budget: BudgetController,
        store: ArtifactStore,
        clock: Clock,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._router = router
        self._budget = budget
        self._store = store
        self._clock = clock
        self._sleep = sleep

    def run[TIn, TOut](
        self, spec: AgentSpec, brand_id: str, trigger: str, loop: Loop[TIn, TOut], payload: TIn
    ) -> RunResult:
        """Corre o Loop e fecha sempre o `agent_run` com o estado final e o custo somado."""
        try:
            client, route = self._router.resolve(spec)
        except (RouteNotConfigured, MissingCapability) as exc:
            return RunResult(RunStatus.FAILED, "", None, f"{type(exc).__name__}: {exc}")
        started_at = self._clock.now()
        run_id = self._open_run(spec, brand_id, trigger, started_at)
        services = _Services(
            client,
            route,
            self._session_factory,
            self._budget,
            self._store,
            self._clock,
            self._sleep,
        )
        ctx = RunContext(
            run_id=run_id, brand_id=brand_id, spec=spec, started_at=started_at, services=services
        )
        status = RunStatus.FAILED  # vale se uma BaseException interromper o Loop
        try:
            result = self._execute(loop, ctx, payload)
            status = result.status
            return result
        finally:
            self._close_run(run_id, status)

    @staticmethod
    def _execute[TIn, TOut](loop: Loop[TIn, TOut], ctx: RunContext, payload: TIn) -> RunResult:
        run_id = ctx.run_id
        try:
            output = loop.run(ctx, payload)
        except (RunLimitExceeded, RunCostLimitExceeded) as exc:
            return RunResult(RunStatus.BLOCKED_LIMIT, run_id, None, str(exc))
        except NeedsHuman as exc:
            return RunResult(RunStatus.NEEDS_HUMAN, run_id, None, exc.reason)
        except Exception as exc:
            logger.exception("execucao %s falhou", run_id)
            return RunResult(RunStatus.FAILED, run_id, None, f"{type(exc).__name__}: {exc}")
        return RunResult(RunStatus.SUCCEEDED, run_id, output, None)

    def _open_run(self, spec: AgentSpec, brand_id: str, trigger: str, started_at: datetime) -> str:
        run = AgentRun(
            brand_id=brand_id,
            role=spec.role,
            spec_version=spec.version,
            trigger=trigger,
            status="running",
            started_at=started_at,
        )
        with self._session_factory.begin() as session:
            session.add(run)
            session.flush()
            return run.id

    def _close_run(self, run_id: str, status: RunStatus) -> None:
        with self._session_factory.begin() as session:
            total = session.scalar(
                select(func.coalesce(func.sum(CostEntry.cost_usd), Decimal(0))).where(
                    CostEntry.run_id == run_id
                )
            )
            run = session.get_one(AgentRun, run_id)
            run.status = status.value
            run.ended_at = self._clock.now()
            run.cost_usd = Decimal(total) if total is not None else Decimal(0)
