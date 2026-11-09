"""Testes do AgentEngine: reserva por chamada, limites, erros classificados e fecho da execucao."""

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactState, ArtifactStore, ArtifactType
from marketing_agent.budget.controller import BudgetController
from marketing_agent.budget.pricing import (
    ModelPrice,
    PriceBook,
    PriceTier,
    Usage,
    sync_model_prices,
)
from marketing_agent.clock import FixedClock
from marketing_agent.config import Capability, RoleRoute
from marketing_agent.db.models import AgentRun, AgentStep, CostEntry, CostReservation
from marketing_agent.harness.engine import (
    AgentEngine,
    Loop,
    RunContext,
    RunResult,
    RunStatus,
)
from marketing_agent.harness.spec import AgentLimits, AgentSpec, LoopType
from marketing_agent.harness.validators import VALIDATORS
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.router import LlmRouter
from marketing_agent.llm.types import (
    LlmNetworkError,
    LlmRequest,
    LlmResponse,
    LlmUnknownOutcome,
    Message,
    StopReason,
    TextPart,
)

Factory = sessionmaker[Session]
Script = list[LlmResponse | Exception]
VALID_FROM = datetime(2026, 11, 1, tzinfo=UTC)
GOOD = '{"answer": "ola"}'
BAD = '{"wrong": 1}'
_request_ids = itertools.count(1)  # o ledger exige provider_request_id unico


class Out(BaseModel):
    """Saida estruturada minima dos testes."""

    answer: str


def response(
    text: str = GOOD, stop_reason: StopReason = StopReason.end, usage: Usage | None = None
) -> LlmResponse:
    return LlmResponse(
        text=text,
        usage=usage or Usage(input_tokens=100, output_tokens=50),
        provider_request_id=f"req-{next(_request_ids)}",
        resolved_model="fake-1",
        stop_reason=stop_reason,
    )


def make_spec(
    max_steps: int = 10,
    max_tokens: int = 100000,
    max_cost_usd: str = "1",
    max_duration_s: int = 60,
    validators: list[str] | None = None,
) -> AgentSpec:
    return AgentSpec(
        role="estrategista",
        name="Estrategista",
        version=3,
        loop_type=LoopType.STRUCTURED,
        prompt_file="p.md",
        output_schema="Out",
        required_capabilities=frozenset({"text", "json_schema"}),
        reads=[],
        writes=[ArtifactType.ESTRATEGIA],
        validators=validators or [],
        limits=AgentLimits(
            max_steps=max_steps,
            max_tokens=max_tokens,
            max_cost_usd=Decimal(max_cost_usd),
            max_duration_s=max_duration_s,
        ),
        max_output_tokens=1000,
        budget_share_hint=Decimal("0.1"),
        escalation="diretor",
    )


def ask(ctx: RunContext, payload: str) -> Out:
    return ctx.call_llm("sistema", [Message("user", [TextPart(payload)])], Out)


class OneCallLoop:
    """Uma chamada ao LLM; devolve a resposta tipada."""

    def run(self, ctx: RunContext, payload: str) -> Out:
        return ask(ctx, payload)


class ExtraValidatorLoop:
    """Uma chamada com `extra_validator`; `rounds` da as violacoes de cada validacao seguida."""

    def __init__(self, rounds: list[list[str]]) -> None:
        self._rounds = iter(rounds)
        self.seen: list[Out] = []

    def run(self, ctx: RunContext, payload: str) -> Out:
        def extra_validator(output: Out) -> list[str]:
            self.seen.append(output)
            return next(self._rounds)

        return ctx.call_llm(
            "sistema", [Message("user", [TextPart(payload)])], Out, extra_validator=extra_validator
        )


class TwoCallLoop:
    """Duas chamadas ao LLM em sequencia."""

    def run(self, ctx: RunContext, payload: str) -> Out:
        ask(ctx, payload)
        return ask(ctx, payload)


class SlowLoop:
    """Deixa passar `seconds` no relogio antes de chamar o LLM."""

    def __init__(self, clock: FixedClock, seconds: int) -> None:
        self._clock = clock
        self._seconds = seconds

    def run(self, ctx: RunContext, payload: str) -> Out:
        self._clock.advance(timedelta(seconds=self._seconds))
        return ask(ctx, payload)


class ArtifactLoop:
    """Cria um artefacto do tipo dado."""

    def __init__(self, type: ArtifactType) -> None:  # noqa: A002
        self._type = type

    def run(self, ctx: RunContext, payload: str) -> str:
        return ctx.create_artifact(self._type, {"texto": payload}).id


@dataclass
class Rig:
    """Motor montado sobre cliente falso, base de teste e relogio fixo."""

    engine: AgentEngine
    llm: FakeLlmClient
    clock: FixedClock
    sleeps: list[float]
    factory: Factory
    brand_id: str
    store: ArtifactStore

    def run[TOut](self, loop: Loop[str, TOut], spec: AgentSpec | None = None) -> RunResult:
        return self.engine.run(spec or make_spec(), self.brand_id, "test", loop, "pedido")

    def steps(self) -> list[AgentStep]:
        with self.factory() as session:
            return list(session.scalars(select(AgentStep).order_by(AgentStep.seq)))

    def step_errors(self) -> list[str | None]:
        return [step.error_class for step in self.steps()]

    def reservations(self) -> list[CostReservation]:
        # Relogio fixo: created_at empata, entao ordena-se pelo passo a que a reserva pertence.
        with self.factory() as session:
            stmt = (
                select(CostReservation)
                .join(AgentStep, CostReservation.step_id == AgentStep.id)
                .order_by(AgentStep.seq)
            )
            return list(session.scalars(stmt))

    def entries(self) -> list[CostEntry]:
        with self.factory() as session:
            return list(session.scalars(select(CostEntry)))

    def run_row(self, run_id: str) -> AgentRun:
        with self.factory() as session:
            return session.get_one(AgentRun, run_id)


class RigFactory(Protocol):
    def __call__(self, script: Script, output_per_million: str = "0") -> Rig: ...


@pytest.fixture
def make_rig(session_factory: Factory, clock: FixedClock, brand_id: str) -> RigFactory:
    def make(script: Script, output_per_million: str = "0") -> Rig:
        # Entrada custa um decimo da saida; "0" torna todas as chamadas gratuitas.
        out_price = Decimal(output_per_million)
        tier = PriceTier(0, out_price / 10, out_price)
        price = ModelPrice("fake", "fake-1", "v1", (tier,), valid_from=VALID_FROM)
        sync_model_prices(session_factory, [price])
        llm = FakeLlmClient(script)
        route = RoleRoute(
            provider="fake", model_id="fake-1", capabilities=frozenset({"text", "json_schema"})
        )
        budget = BudgetController(
            session_factory, PriceBook(session_factory), clock, Decimal(20), (50, 80, 100)
        )
        store = ArtifactStore(session_factory, clock)
        sleeps: list[float] = []
        engine = AgentEngine(
            session_factory,
            LlmRouter({"estrategista": route}, {"fake": llm}),
            budget,
            store,
            clock,
            sleep=sleeps.append,
        )
        return Rig(engine, llm, clock, sleeps, session_factory, brand_id, store)

    return make


def last_user_text(rig: Rig, request_index: int) -> str:
    part = rig.llm.requests[request_index].messages[-1].content[0]
    assert isinstance(part, TextPart)
    return part.text


def test_success_records_run_step_reservation_and_entry(make_rig: RigFactory) -> None:
    rig = make_rig([response()])
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.SUCCEEDED
    assert result.output == Out(answer="ola")
    [step] = rig.steps()
    assert (step.run_id, step.seq, step.kind, step.error_class) == (result.run_id, 1, "llm", None)
    [reservation] = rig.reservations()
    assert (reservation.status, reservation.step_id) == ("settled", step.id)
    [entry] = rig.entries()
    assert (entry.step_id, entry.tokens_in, entry.tokens_out) == (step.id, 100, 50)
    [request] = rig.llm.requests
    assert request.model_id == "fake-1"
    assert request.max_output_tokens == 1000
    assert request.json_schema == Out.model_json_schema()


def test_successful_run_is_closed_with_summed_cost(make_rig: RigFactory) -> None:
    # Saida a 10 USD/M e entrada a 1 USD/M: (100 * 1 + 50 * 10) / 1e6 = 0,0006 por chamada.
    rig = make_rig([response(), response()], output_per_million="10")
    result = rig.run(TwoCallLoop())
    row = rig.run_row(result.run_id)
    assert (row.status, row.spec_version, row.trigger) == ("succeeded", 3, "test")
    assert row.ended_at == rig.clock.now()
    assert row.cost_usd == Decimal("0.001200")


def test_invalid_three_times_needs_human(make_rig: RigFactory) -> None:
    rig = make_rig([response(BAD), response(BAD), response(BAD)])
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.NEEDS_HUMAN
    assert len(rig.llm.requests) == 3
    assert len(rig.entries()) == 3
    assert [r.status for r in rig.reservations()] == ["settled"] * 3
    assert rig.step_errors() == ["invalid_output"] * 3
    assert "invalida" in last_user_text(rig, 2)
    assert len(rig.llm.requests[2].messages) == 5
    assert rig.run_row(result.run_id).status == "needs_human"


def test_invalid_then_valid_succeeds(make_rig: RigFactory) -> None:
    rig = make_rig([response(BAD), response()])
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.SUCCEEDED
    assert rig.step_errors() == ["invalid_output", None]
    assert len(rig.entries()) == 2


def test_invalid_retry_carries_previous_text_and_errors(make_rig: RigFactory) -> None:
    rig = make_rig([response(BAD), response()])
    rig.run(OneCallLoop())
    first, second = rig.llm.requests
    assert len(first.messages) == 1
    assistant, feedback = second.messages[1:]
    assert assistant.role == "assistant"
    assert assistant.content == [TextPart(BAD)]
    assert feedback.role == "user"
    text = last_user_text(rig, 1)
    assert "answer" in text and "Field required" in text
    assert text.endswith("Responde apenas com JSON valido.")


def test_validator_violation_counts_as_invalid(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(VALIDATORS, "sem_ola", lambda model: ["nao pode dizer ola"])
    rig = make_rig([response(), response(), response()])
    result = rig.run(OneCallLoop(), make_spec(validators=["sem_ola"]))
    assert result.status is RunStatus.NEEDS_HUMAN
    assert rig.step_errors() == ["invalid_output"] * 3
    assert "nao pode dizer ola" in last_user_text(rig, 1)


def test_extra_validator_violation_retries_with_message(make_rig: RigFactory) -> None:
    rig = make_rig([response(), response()])
    loop = ExtraValidatorLoop([["slot fora da semana"], []])
    result = rig.run(loop)
    assert result.status is RunStatus.SUCCEEDED
    assert rig.step_errors() == ["invalid_output", None]
    text = last_user_text(rig, 1)
    assert "slot fora da semana" in text
    assert text.endswith("Responde apenas com JSON valido.")
    assert len(loop.seen) == 2


def test_extra_validator_three_failures_needs_human(make_rig: RigFactory) -> None:
    rig = make_rig([response(), response(), response()])
    loop = ExtraValidatorLoop([["slot fora da semana"]] * 3)
    result = rig.run(loop)
    assert result.status is RunStatus.NEEDS_HUMAN
    assert result.message is not None and "slot fora da semana" in result.message
    assert rig.step_errors() == ["invalid_output"] * 3


def test_extra_validator_violations_join_registered_ones(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(VALIDATORS, "sem_ola", lambda model: ["nao pode dizer ola"])
    rig = make_rig([response() for _ in range(3)])
    loop = ExtraValidatorLoop([["slot fora da semana"]] * 3)
    result = rig.run(loop, make_spec(validators=["sem_ola"]))
    assert result.status is RunStatus.NEEDS_HUMAN  # o validador registado reprova sempre
    text = last_user_text(rig, 1)
    assert "nao pode dizer ola; slot fora da semana" in text


def test_extra_validator_not_called_for_unparseable_output(make_rig: RigFactory) -> None:
    rig = make_rig([response(BAD), response()])
    loop = ExtraValidatorLoop([[]])
    result = rig.run(loop)
    assert result.status is RunStatus.SUCCEEDED
    assert len(loop.seen) == 1


def test_refusal_no_retry(make_rig: RigFactory) -> None:
    rig = make_rig([response("", StopReason.refusal)])
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.NEEDS_HUMAN
    assert len(rig.llm.requests) == 1
    assert rig.step_errors() == ["refusal"]
    assert [r.status for r in rig.reservations()] == ["settled"]
    assert len(rig.entries()) == 1
    assert rig.run_row(result.run_id).status == "needs_human"


def test_truncated_no_retry(make_rig: RigFactory) -> None:
    rig = make_rig([response('{"answer": "cort', StopReason.max_tokens)])
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.NEEDS_HUMAN
    assert len(rig.llm.requests) == 1
    assert rig.step_errors() == ["truncated"]
    assert len(rig.entries()) == 1


def test_network_error_retries_and_releases_reservation(make_rig: RigFactory) -> None:
    rig = make_rig([LlmNetworkError("sem rede"), response()])
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.SUCCEEDED
    assert [r.status for r in rig.reservations()] == ["released", "settled"]
    assert len(rig.entries()) == 1
    assert rig.step_errors() == ["network", None]
    assert rig.sleeps == [1.0]


def test_two_network_errors_sleep_one_then_two_seconds(make_rig: RigFactory) -> None:
    rig = make_rig([LlmNetworkError("a"), LlmNetworkError("b"), response()])
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.SUCCEEDED
    assert rig.sleeps == [1.0, 2.0]


def test_three_network_errors_need_human(make_rig: RigFactory) -> None:
    rig = make_rig([LlmNetworkError("a"), LlmNetworkError("b"), LlmNetworkError("c")])
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.NEEDS_HUMAN
    assert rig.sleeps == [1.0, 2.0]
    assert [r.status for r in rig.reservations()] == ["released"] * 3
    assert rig.entries() == []


def test_unknown_outcome_marks_reservation(make_rig: RigFactory) -> None:
    rig = make_rig([LlmUnknownOutcome("timeout a meio")])
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.NEEDS_HUMAN
    assert [r.status for r in rig.reservations()] == ["unknown_outcome"]
    assert rig.entries() == []
    assert rig.step_errors() == ["unknown_outcome"]
    assert len(rig.llm.requests) == 1


def test_run_cost_limit_blocks_before_call(make_rig: RigFactory) -> None:
    # Reserva maxima: 1000 tokens de saida a 10000 USD/M = 10 USD, acima do limite de 1 USD.
    rig = make_rig([response()], output_per_million="10000")
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.BLOCKED_LIMIT
    assert rig.llm.requests == []
    assert rig.step_errors() == ["limit"]
    assert rig.reservations() == []
    assert rig.run_row(result.run_id).status == "blocked_limit"


def test_max_steps_blocks(make_rig: RigFactory) -> None:
    # Cada tentativa conta como passo: a repeticao apos a saida invalida esbarra no limite.
    rig = make_rig([response(BAD), response()])
    result = rig.run(OneCallLoop(), make_spec(max_steps=1))
    assert result.status is RunStatus.BLOCKED_LIMIT
    assert len(rig.llm.requests) == 1
    assert len(rig.steps()) == 1


def test_max_tokens_blocks(make_rig: RigFactory) -> None:
    rig = make_rig([response(), response()])
    result = rig.run(TwoCallLoop(), make_spec(max_tokens=150))  # 100 + 50 acertados
    assert result.status is RunStatus.BLOCKED_LIMIT
    assert len(rig.llm.requests) == 1


def test_duration_limit_uses_clock(make_rig: RigFactory, clock: FixedClock) -> None:
    rig = make_rig([response()])
    result = rig.run(SlowLoop(clock, 61), make_spec(max_duration_s=60))
    assert result.status is RunStatus.BLOCKED_LIMIT
    assert rig.llm.requests == []
    assert rig.run_row(result.run_id).status == "blocked_limit"


def test_duration_at_exact_limit_is_allowed(make_rig: RigFactory, clock: FixedClock) -> None:
    rig = make_rig([response()])
    result = rig.run(SlowLoop(clock, 60), make_spec(max_duration_s=60))
    assert result.status is RunStatus.SUCCEEDED


def test_create_artifact_outside_writes_denied(make_rig: RigFactory) -> None:
    rig = make_rig([])
    result = rig.run(ArtifactLoop(ArtifactType.PLANO))
    assert result.status is RunStatus.FAILED
    assert result.message is not None
    assert "PermissionDenied" in result.message
    assert rig.run_row(result.run_id).status == "failed"


def test_create_artifact_allowed_is_draft_and_traced(make_rig: RigFactory) -> None:
    rig = make_rig([])
    result = rig.run(ArtifactLoop(ArtifactType.ESTRATEGIA))
    assert result.status is RunStatus.SUCCEEDED
    assert isinstance(result.output, str)
    record = rig.store.get(result.output)
    assert record.state is ArtifactState.DRAFT
    assert record.produced_by_run_id == result.run_id


def test_unexpected_error_fails_run_and_closes_it(make_rig: RigFactory) -> None:
    class Boom:
        def run(self, ctx: RunContext, payload: str) -> str:
            raise ValueError("explodiu")

    result = make_rig([]).run(Boom())
    assert result.status is RunStatus.FAILED
    assert result.message == "ValueError: explodiu"


def test_base_exception_propagates_but_run_is_closed(make_rig: RigFactory) -> None:
    class Interrupted:
        def run(self, ctx: RunContext, payload: str) -> str:
            raise KeyboardInterrupt

    rig = make_rig([])
    with pytest.raises(KeyboardInterrupt):
        rig.run(Interrupted())
    with rig.factory() as session:
        row = session.scalars(select(AgentRun)).one()
    assert row.status == "failed"
    assert row.ended_at is not None


def test_missing_route_fails_without_run_row(make_rig: RigFactory) -> None:
    rig = make_rig([])
    spec = make_spec().model_copy(update={"role": "sem_rota"})
    result = rig.run(OneCallLoop(), spec)
    assert result.status is RunStatus.FAILED
    assert result.message is not None and "sem_rota" in result.message
    with rig.factory() as session:
        assert session.scalars(select(AgentRun)).all() == []


class RaisingClient:
    """Cliente cujo `complete` levanta a excecao dada (nao e erro de transporte do contrato)."""

    capabilities: frozenset[Capability] = frozenset({"text", "json_schema"})

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def complete(self, request: LlmRequest) -> LlmResponse:
        raise self._error


def install_client(rig: Rig, error: BaseException) -> None:
    """Troca o cliente do router por um que levanta `error`."""
    route = RoleRoute(
        provider="fake", model_id="fake-1", capabilities=frozenset({"text", "json_schema"})
    )
    rig.engine._router = LlmRouter({"estrategista": route}, {"fake": RaisingClient(error)})


def test_client_unexpected_error_marks_reservation_unknown(make_rig: RigFactory) -> None:
    rig = make_rig([])
    install_client(rig, RuntimeError("bug no sdk"))
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.FAILED
    assert result.message == "RuntimeError: bug no sdk"
    assert [r.status for r in rig.reservations()] == ["unknown_outcome"]
    assert rig.step_errors() == ["unknown_outcome"]
    assert rig.entries() == []
    assert rig.run_row(result.run_id).status == "failed"


def test_settle_failure_marks_reservation_unknown(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_settle(*args: object, **kwargs: object) -> Decimal:
        raise RuntimeError("base caiu no acerto")

    monkeypatch.setattr(BudgetController, "settle", broken_settle)
    rig = make_rig([response()])
    result = rig.run(OneCallLoop())
    assert result.status is RunStatus.FAILED
    assert result.message == "RuntimeError: base caiu no acerto"
    assert [r.status for r in rig.reservations()] == ["unknown_outcome"]
    assert rig.step_errors() == ["unknown_outcome"]
    assert rig.entries() == []


def test_raising_validator_closes_step_as_invalid_output(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_validator(model: BaseModel) -> list[str]:
        raise KeyError("campo em falta")

    monkeypatch.setitem(VALIDATORS, "quebrado", broken_validator)
    rig = make_rig([response()])
    result = rig.run(OneCallLoop(), make_spec(validators=["quebrado"]))
    assert result.status is RunStatus.FAILED
    assert result.message is not None and result.message.startswith("KeyError")
    assert rig.step_errors() == ["invalid_output"]
    assert [r.status for r in rig.reservations()] == ["settled"]
    assert len(rig.entries()) == 1


def test_keyboard_interrupt_from_client_marks_unknown_and_closes_run(make_rig: RigFactory) -> None:
    rig = make_rig([])
    install_client(rig, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        rig.run(OneCallLoop())
    assert [r.status for r in rig.reservations()] == ["unknown_outcome"]
    assert rig.step_errors() == ["unknown_outcome"]
    with rig.factory() as session:
        row = session.scalars(select(AgentRun)).one()
    assert (row.status, row.ended_at is not None) == ("failed", True)


def test_secondary_failure_in_mark_unknown_keeps_original_error(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_mark(*args: object, **kwargs: object) -> None:
        raise ValueError("falha secundaria")

    monkeypatch.setattr(BudgetController, "mark_unknown", broken_mark)
    rig = make_rig([])
    install_client(rig, RuntimeError("original"))
    result = rig.run(OneCallLoop())
    assert result.message == "RuntimeError: original"
    assert rig.step_errors() == ["unknown_outcome"]
