"""Montagem partilhada pelos testes do pipeline e do worker: motor falso, base e servicos reais.

O `HookedLlm` deixa o teste intervir a meio de uma geracao (por exemplo, gravar uma revisao do
responsavel entre a chamada ao Copywriter e a gravacao), que e onde moram as corridas.
"""

import itertools
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore, ArtifactType
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
from marketing_agent.content.pipeline import ContentPipeline
from marketing_agent.db.base import new_id
from marketing_agent.db.models import Job, Post, PostRevision
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.harness.engine import AgentEngine
from marketing_agent.jobs.queue import GENERATE_POST_JOB, ClaimedJob, JobQueue
from marketing_agent.kb.service import KbService, NewKbEntry
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.router import LlmRouter
from marketing_agent.llm.types import LlmRequest, LlmResponse, TextPart
from marketing_agent.startup import content_job_lease
from marketing_agent.strategy.service import StrategyService

Factory = sessionmaker[Session]
Script = list[LlmResponse | Exception]
WEEK = "2026-W46"  # segunda 2026-11-09; o relogio dos testes e quinta 2026-11-05
POST_DATE = date(2026, 11, 9)
VALID_FROM = datetime(2026, 11, 1, tzinfo=UTC)
PROFILE_PAYLOAD = {
    "topicos": {"o_que_vende": {"texto": "Pao artesanal.", "origem": "dito_pelo_responsavel"}},
    "lacunas_abertas": ["concorrentes"],
    "suposicoes": [],
}
_request_ids = itertools.count(1)  # o ledger exige provider_request_id unico


def unique(script: Script) -> Script:
    """Cenario com `provider_request_id` unicos, como o ledger exige."""
    return [
        item
        if isinstance(item, Exception)
        else LlmResponse(
            text=item.text,
            usage=Usage(input_tokens=100, output_tokens=50),
            provider_request_id=f"req-{next(_request_ids)}",
            resolved_model="fake-1",
            stop_reason=item.stop_reason,
        )
        for item in script
    ]


class HookedLlm(FakeLlmClient):
    """Cliente falso que executa um gancho antes de servir o pedido de numero N (1-based)."""

    def __init__(self, responses: Sequence[LlmResponse | Exception]) -> None:
        super().__init__(responses)
        self.hooks: dict[int, Callable[[], None]] = {}

    def complete(self, request: LlmRequest) -> LlmResponse:
        hook = self.hooks.pop(len(self.requests) + 1, None)
        if hook is not None:
            hook()
        return super().complete(request)

    def load(self, script: Script) -> None:
        """Substitui o guiao pendente (para scripts que dependem de ids criados no teste)."""
        self._script[:] = unique(script)

    def request_text(self, number: int) -> str:
        """Texto da primeira mensagem do pedido `number` (1-based)."""
        part = self.requests[number - 1].messages[0].content[0]
        assert isinstance(part, TextPart)
        return part.text


@dataclass
class ContentRig:
    """Pipeline real sobre cliente falso, base de teste e relogio fixo."""

    pipeline: ContentPipeline
    engine: AgentEngine
    llm: HookedLlm
    store: ArtifactStore
    strategy: StrategyService
    kb: KbService
    feedback: FeedbackService
    queue: JobQueue
    clock: FixedClock
    factory: Factory
    brand_id: str
    plan_id: str
    strategy_id: str

    def seed_post(
        self,
        slot_key: str = f"{WEEK}-1",
        *,
        lifecycle: str = "planned",
        channels: Sequence[str] = ("instagram",),
        kb_refs: Sequence[str] = (),
    ) -> str:
        post = Post(
            brand_id=self.brand_id,
            plan_artifact_id=self.plan_id,
            slot_key=slot_key,
            strategy_artifact_id=self.strategy_id,
            channels=list(channels),
            scheduled_date=POST_DATE,
            brief={
                "tema": "Pao do dia",
                "pilar": "Producao artesanal",
                "objetivo": "Mensagens",
                "kb_refs": list(kb_refs),
            },
            lifecycle=lifecycle,
            created_at=self.clock.now(),
        )
        with self.factory.begin() as session:
            session.add(post)
            session.flush()
            return post.id

    def enqueue(self, post_id: str, reason: str = "plan", owner_comment: str | None = None) -> str:
        payload = {"post_id": post_id, "owner_comment": owner_comment, "reason": reason}
        with self.factory.begin() as session:
            job_id = self.queue.enqueue_in(
                session, GENERATE_POST_JOB, payload, f"{GENERATE_POST_JOB}:{post_id}:{new_id()}"
            )
        assert job_id is not None
        return job_id

    def claimed(
        self, post_id: str, reason: str = "plan", owner_comment: str | None = None
    ) -> ClaimedJob:
        """Enfileira e reclama o job do post (worker `w1`)."""
        self.enqueue(post_id, reason, owner_comment)
        job = self.queue.claim("w1")
        assert job is not None
        return job

    def add_owner_revision(
        self, post_id: str, rev_no: int = 1, origin: str = "edicao_responsavel"
    ) -> str:
        """Revisao gravada diretamente (como o painel faria), fora do pipeline."""
        with self.factory.begin() as session:
            post = session.get_one(Post, post_id)
            revision = PostRevision(
                brand_id=post.brand_id,
                post_id=post_id,
                rev_no=rev_no,
                revision_hash="0" * 64,
                caption="Texto do responsavel.",
                hashtags=["#x"],
                call_to_action=None,
                alt_text="Alt.",
                claims=[],
                review_verdict=None,
                review_violations=[],
                review_notes=None,
                origin=origin,
                created_at=self.clock.now(),
            )
            session.add(revision)
            session.flush()
            return revision.id

    def add_price_item(self) -> str:
        entry = NewKbEntry(
            kind="preco",
            key="pao",
            title="Pao",
            body="Pao de forno a lenha.",
            price_amount=Decimal("12.50"),
            price_currency="EUR",
            valid_from=date(2026, 11, 1),
            valid_until=date(2026, 12, 1),
            source_kind="dito_pelo_responsavel",
        )
        return self.kb.add(self.brand_id, entry, "ana").id

    def revisions(self, post_id: str) -> list[PostRevision]:
        with self.factory() as session:
            statement = select(PostRevision).where(PostRevision.post_id == post_id)
            return list(session.scalars(statement.order_by(PostRevision.rev_no)))

    def post(self, post_id: str) -> Post:
        with self.factory() as session:
            return session.get_one(Post, post_id)

    def job(self, job_id: str) -> Job:
        with self.factory() as session:
            return session.get_one(Job, job_id)

    def expire_leases(self) -> None:
        """Avanca o relogio para depois de qualquer lease."""
        self.clock.advance(content_job_lease() + timedelta(seconds=1))


def copy_and_review(
    verdict: str = "aprovar",
    violations: Sequence[str] = (),
    notes: str = "",
    legenda: str = "Pao quente todas as manhas, feito com receitas de familia.",
) -> Script:
    """Uma passagem Copywriter + Revisor."""
    return [
        *fake_scenarios.caption_draft(legenda),
        *fake_scenarios.review(verdict, violations, notes),
    ]


def build_rig(
    session_factory: Factory, clock: FixedClock, brand_id: str, script: Script
) -> ContentRig:
    free = PriceTier(0, Decimal(0), Decimal(0))
    sync_model_prices(
        session_factory, [ModelPrice("fake", "fake-1", "v1", (free,), valid_from=VALID_FROM)]
    )
    llm = HookedLlm(unique(script))
    route = RoleRoute(
        provider="fake", model_id="fake-1", capabilities=frozenset({"text", "json_schema"})
    )
    budget = BudgetController(
        session_factory, PriceBook(session_factory), clock, Decimal(20), (50, 80, 100)
    )
    store = ArtifactStore(session_factory, clock)
    engine = AgentEngine(
        session_factory,
        LlmRouter({"copywriter": route, "revisor": route}, {"fake": llm}),
        budget,
        store,
        clock,
        sleep=lambda seconds: None,
    )
    strategy = StrategyService(session_factory, engine, store, clock)
    kb = KbService(session_factory, store, clock)
    feedback = FeedbackService(session_factory, clock)
    queue = JobQueue(session_factory, clock, content_job_lease())
    pipeline = ContentPipeline(session_factory, engine, kb, feedback, queue, clock, store, strategy)
    strategy_id = _activate_strategy(store, strategy, brand_id)
    plan = store.create(brand_id, ArtifactType.PLANO, {"semana": WEEK, "slots": []})
    return ContentRig(
        pipeline, engine, llm, store, strategy, kb, feedback, queue, clock,
        session_factory, brand_id, plan.id, strategy_id,
    )  # fmt: skip


def _activate_strategy(store: ArtifactStore, strategy: StrategyService, brand_id: str) -> str:
    profile = store.create(brand_id, ArtifactType.PERFIL_MARCA, PROFILE_PAYLOAD)
    store.submit(profile.id, "ana")
    store.approve_and_activate(profile.id, "ana")
    _, scenario = fake_scenarios.diagnosis_and_strategy()
    assert not isinstance(scenario, Exception)
    created = store.create(brand_id, ArtifactType.ESTRATEGIA, json.loads(scenario.text))
    store.submit(created.id, "ana")
    strategy.approve_strategy(created.id, "ana", {0: True, 1: False})
    return created.id
