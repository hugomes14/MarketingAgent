"""Montagem dos testes do plano semanal: servico real, cliente LLM falso e relogio fixo."""

import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import (
    ArtifactRecord,
    ArtifactStore,
    ArtifactType,
)
from marketing_agent.budget.controller import BudgetController
from marketing_agent.budget.pricing import (
    ModelPrice,
    PriceBook,
    PriceTier,
    Usage,
    sync_model_prices,
)
from marketing_agent.clock import FixedClock
from marketing_agent.config import RoleRoute
from marketing_agent.db.models import (
    ArtifactHead,
    AuditLog,
    Job,
    Post,
    SystemFlag,
)
from marketing_agent.harness.engine import AgentEngine, RunStatus
from marketing_agent.jobs.queue import JobQueue
from marketing_agent.kb.service import KbService, NewKbEntry
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.router import LlmRouter
from marketing_agent.llm.types import LlmResponse
from marketing_agent.planning.service import PlanningService
from marketing_agent.planning.validation import PlanLimits
from marketing_agent.strategy.service import StrategyService

Factory = sessionmaker[Session]
Script = list[LlmResponse | Exception]
Edit = Callable[[list[dict[str, object]]], None]
WEEK = "2026-W46"  # segunda 2026-11-09; o relogio dos testes e quinta 2026-11-05 (semana W45)
LIMITS = PlanLimits(max_per_week=5, max_per_day=1)
VALID_FROM = datetime(2026, 11, 1, tzinfo=UTC)
_request_ids = itertools.count(1)  # o ledger exige provider_request_id unico
PROFILE_PAYLOAD = {
    "topicos": {"o_que_vende": {"texto": "Pao artesanal.", "origem": "dito_pelo_responsavel"}},
    "lacunas_abertas": ["concorrentes"],
    "suposicoes": [],
}


def plan_response(week: str, slots: int, edit: Edit | None = None) -> LlmResponse:
    """Resposta de plano do cenario, com id de pedido unico e, opcionalmente, slots editados."""
    [base] = fake_scenarios.weekly_plan(week, slots)
    assert not isinstance(base, Exception)
    body = json.loads(base.text)
    if edit is not None:
        edit(body["slots"])
    return LlmResponse(
        text=json.dumps(body),
        usage=Usage(input_tokens=100, output_tokens=50),
        provider_request_id=f"req-{next(_request_ids)}",
        resolved_model="fake-1",
        stop_reason=base.stop_reason,
    )


def change_theme(index: int, theme: str) -> Edit:
    def edit(slots: list[dict[str, object]]) -> None:
        slots[index]["tema"] = theme

    return edit


@dataclass
class Rig:
    """Servico de plano montado sobre cliente falso, base de teste e relogio fixo."""

    service: PlanningService
    engine: AgentEngine
    store: ArtifactStore
    strategy: StrategyService
    kb: KbService
    queue: JobQueue
    llm: FakeLlmClient
    clock: FixedClock
    factory: Factory
    brand_id: str

    def activate_profile(self) -> None:
        created = self.store.create(self.brand_id, ArtifactType.PERFIL_MARCA, PROFILE_PAYLOAD)
        self.store.submit(created.id, "ana")
        self.store.approve_and_activate(created.id, "ana")

    def activate_strategy(self) -> str:
        """Perfil ativo e estrategia aprovada (so a 1a proposta aceite); devolve o id."""
        self.activate_profile()
        _, strategy = fake_scenarios.diagnosis_and_strategy()
        assert not isinstance(strategy, Exception)
        created = self.store.create(
            self.brand_id, ArtifactType.ESTRATEGIA, json.loads(strategy.text)
        )
        self.store.submit(created.id, "ana")
        self.strategy.approve_strategy(created.id, "ana", {0: True, 1: False})
        return created.id

    def propose(self, week: str | None = None) -> str:
        """Propoe o plano e devolve o id do artefacto `submitted`."""
        result = self.service.propose_plan(self.brand_id, week)
        assert result.status is RunStatus.SUCCEEDED and isinstance(result.output, ArtifactRecord)
        return result.output.id

    def propose_and_approve(
        self, week: str | None = None, *, confirm_cancel: bool = False
    ) -> tuple[str, list[str]]:
        plan_id = self.propose(week)
        return plan_id, self.service.approve_plan(plan_id, "ana", confirm_cancel=confirm_cancel)

    def posts(self) -> list[Post]:
        with self.factory() as session:
            return list(session.scalars(select(Post).order_by(Post.slot_key)))

    def post_by_key(self) -> dict[str, Post]:
        return {post.slot_key: post for post in self.posts()}

    def jobs(self) -> list[Job]:
        with self.factory() as session:
            return list(session.scalars(select(Job).order_by(Job.created_at, Job.dedupe_key)))

    def count(self, model: type[Post] | type[Job] | type[ArtifactHead]) -> int:
        with self.factory() as session:
            return session.scalar(select(func.count()).select_from(model)) or 0

    def audits(self, action: str) -> list[AuditLog]:
        with self.factory() as session:
            return list(
                session.scalars(
                    select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
                )
            )

    def markers(self) -> list[SystemFlag]:
        with self.factory() as session:
            return list(
                session.scalars(select(SystemFlag).where(SystemFlag.key.like("proposal:%")))
            )

    def submit_plan(self, week: str, slots: int, label: str) -> str:
        """Plano `submitted` criado pela loja, com temas proprios de `label`."""
        [base] = fake_scenarios.weekly_plan(week, slots)
        assert not isinstance(base, Exception)
        body = json.loads(base.text)
        for slot in body["slots"]:
            slot["tema"] = f"Tema {label} {slot['slot_key']}"
        created = self.store.create(self.brand_id, ArtifactType.PLANO, body)
        self.store.submit(created.id, "diretor")
        return created.id

    def set_job_status(self, post_id: str, status: str) -> None:
        with self.factory.begin() as session:
            session.execute(
                update(Job).where(Job.payload["post_id"].astext == post_id).values(status=status)
            )

    def set_post(self, slot_key: str, **values: object) -> None:
        with self.factory.begin() as session:
            session.execute(update(Post).where(Post.slot_key == slot_key).values(**values))

    def jobs_of(self, post_id: str) -> list[Job]:
        return [job for job in self.jobs() if job.payload["post_id"] == post_id]

    def set_lifecycle(self, slot_key: str, lifecycle: str) -> None:
        with self.factory.begin() as session:
            session.execute(
                update(Post).where(Post.slot_key == slot_key).values(lifecycle=lifecycle)
            )


@pytest.fixture
def make_rig(session_factory: Factory, clock: FixedClock, brand_id: str) -> Callable[[Script], Rig]:
    free = PriceTier(0, Decimal(0), Decimal(0))
    sync_model_prices(
        session_factory, [ModelPrice("fake", "fake-1", "v1", (free,), valid_from=VALID_FROM)]
    )

    def make(script: Script) -> Rig:
        llm = FakeLlmClient(script)
        route = RoleRoute(
            provider="fake", model_id="fake-1", capabilities=frozenset({"text", "json_schema"})
        )
        budget = BudgetController(
            session_factory, PriceBook(session_factory), clock, Decimal(20), (50, 80, 100)
        )
        store = ArtifactStore(session_factory, clock)
        engine = AgentEngine(
            session_factory,
            LlmRouter({"diretor": route}, {"fake": llm}),
            budget,
            store,
            clock,
            sleep=lambda seconds: None,
        )
        strategy = StrategyService(session_factory, engine, store, clock)
        kb = KbService(session_factory, store, clock)
        queue = JobQueue(session_factory, clock, timedelta(minutes=5))
        service = PlanningService(
            session_factory, engine, store, strategy, kb, queue, clock, LIMITS
        )
        return Rig(
            service, engine, store, strategy, kb, queue, llm, clock, session_factory, brand_id
        )

    return make


def plans(*responses: LlmResponse) -> Script:
    return list(responses)


def add_kb(rig: Rig, key: str, valid_until: date | None = None) -> str:
    entry = NewKbEntry(
        kind="oferta",
        key=key,
        title=f"Oferta {key}",
        body="Texto da oferta.",
        valid_from=date(2026, 11, 1),
        valid_until=valid_until,
        source_kind="dito_pelo_responsavel",
    )
    return rig.kb.add(rig.brand_id, entry, "ana").id
