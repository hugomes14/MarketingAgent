"""PlanningService: propor, aprovar e substituir o plano semanal, com posts e trabalhos atomicos."""

import itertools
import json
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.agents.diretor import DIRETOR_DIR
from marketing_agent.artifacts.store import (
    ArtifactRecord,
    ArtifactState,
    ArtifactStore,
    ArtifactType,
    InvalidTransition,
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
from marketing_agent.harness.spec import load_agent_spec
from marketing_agent.jobs.queue import JobQueue
from marketing_agent.kb.service import KbService, NewKbEntry
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.router import LlmRouter
from marketing_agent.llm.types import LlmResponse, TextPart
from marketing_agent.planning.loop import PlanLoop, PlanPayload
from marketing_agent.planning.schemas import PlanoSemanal
from marketing_agent.planning.service import PlanningService, next_week
from marketing_agent.planning.validation import PlanLimits
from marketing_agent.proposals import MissingPrerequisite, PendingProposal, ProposalInProgress
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

    def propose_and_approve(self, week: str | None = None) -> tuple[str, list[str]]:
        plan_id = self.propose(week)
        return plan_id, self.service.approve_plan(plan_id, "ana")

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


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 5), "2026-W46"),  # quinta
        (date(2026, 11, 8), "2026-W46"),  # domingo da W45
        (date(2026, 11, 9), "2026-W47"),  # segunda da W46
        (date(2025, 12, 29), "2026-W02"),  # segunda da 2026-W01 (ano ISO difere do civil)
        (date(2026, 12, 28), "2027-W01"),  # 2026 tem 53 semanas ISO: W53 -> W01
        (date(2026, 12, 21), "2026-W53"),
    ],
)
def test_next_week_is_the_following_iso_week(today: date, expected: str) -> None:
    assert next_week(today) == expected


def test_propose_requires_active_strategy(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3)))
    with pytest.raises(MissingPrerequisite, match="perfil_marca"):
        rig.service.propose_plan(rig.brand_id)
    rig.activate_profile()
    with pytest.raises(MissingPrerequisite, match="estrategia"):
        rig.service.propose_plan(rig.brand_id)
    assert rig.llm.requests == [] and rig.markers() == []


def test_propose_creates_submitted_plan_for_next_week(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3)))
    rig.activate_strategy()
    plan_id = rig.propose()
    record = rig.store.get(plan_id)
    assert record.state is ArtifactState.SUBMITTED and record.parent_id is None
    assert record.payload["semana"] == WEEK
    assert rig.store.active(rig.brand_id, ArtifactType.PLANO, scope=f"week:{WEEK}") is None
    [request] = rig.llm.requests
    part = request.messages[0].content[0]
    assert isinstance(part, TextPart) and WEEK in part.text and "<estrategia>" in part.text
    assert rig.markers() == [] and rig.count(Post) == 0 and rig.count(Job) == 0


def test_propose_submits_with_diretor_after_creating_a_draft(
    make_rig: Callable[[Script], Rig],
) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3)))
    rig.activate_strategy()
    plan_id = rig.propose()
    [submit] = [a for a in rig.audits("artifact.submit") if a.subject_id == plan_id]
    assert submit.actor == "diretor" and submit.detail == {"from": "draft", "to": "submitted"}


def test_plan_loop_through_real_engine_creates_draft(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3)))
    strategy_id = rig.activate_strategy()
    active = rig.strategy.active_strategy(rig.brand_id)
    assert active is not None and active.artifact_id == strategy_id
    spec = load_agent_spec(DIRETOR_DIR / "spec_plano.yaml", {"PlanoSemanal": PlanoSemanal})
    payload = PlanPayload(
        week=WEEK,
        limits=LIMITS,
        perfil_marca=PROFILE_PAYLOAD,
        estrategia=active.estrategia,
        accepted_proposals=active.propostas_aceites,
        kb_entries=[],
        parent_id=None,
    )
    run = rig.engine.run(spec, rig.brand_id, "plan.weekly", PlanLoop(), payload)
    assert run.status is RunStatus.SUCCEEDED and isinstance(run.output, ArtifactRecord)
    assert run.output.state is ArtifactState.DRAFT  # o ciclo nunca submete
    assert rig.store.get(run.output.id).state is ArtifactState.DRAFT
    assert run.output.produced_by_run_id == run.run_id


def test_invalid_plan_from_model_is_retried_by_the_code_validator(
    make_rig: Callable[[Script], Rig],
) -> None:
    too_many = plan_response(WEEK, 6)  # 6 slots excedem o teto de 5 por semana
    rig = make_rig(plans(too_many, plan_response(WEEK, 3)))
    rig.activate_strategy()
    plan_id = rig.propose()
    assert len(rig.llm.requests) == 2
    assert len(rig.store.get(plan_id).payload["slots"]) == 3  # type: ignore[arg-type]


def test_propose_refuses_pending_plan_same_week(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3), plan_response("2026-W47", 2)))
    rig.activate_strategy()
    rig.propose()
    with pytest.raises(PendingProposal):
        rig.service.propose_plan(rig.brand_id, WEEK)
    assert len(rig.llm.requests) == 1 and rig.markers() == []
    other = rig.service.propose_plan(rig.brand_id, "2026-W47")  # outra semana: livre
    assert other.status is RunStatus.SUCCEEDED


def test_propose_week_pending_refusal_ends_after_decision(
    make_rig: Callable[[Script], Rig],
) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3), plan_response(WEEK, 2)))
    rig.activate_strategy()
    first = rig.propose()
    rig.service.reject_plan(first, "ana", "mudar os temas")
    assert rig.propose() != first


def test_propose_rejects_invalid_week_before_any_call(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3)))
    rig.activate_strategy()
    with pytest.raises(ValueError, match="semana"):
        rig.service.propose_plan(rig.brand_id, "2026-W60")
    assert rig.llm.requests == [] and rig.markers() == []


def test_fresh_marker_for_the_week_blocks_and_other_week_is_free(
    make_rig: Callable[[Script], Rig],
) -> None:
    rig = make_rig(plans(plan_response("2026-W47", 2)))
    rig.activate_strategy()
    key = f"proposal:{rig.brand_id}:plano:{WEEK}"
    with rig.factory.begin() as session:
        session.add(SystemFlag(key=key, value="outro", set_by="diretor", set_at=rig.clock.now()))
    with pytest.raises(ProposalInProgress):
        rig.service.propose_plan(rig.brand_id, WEEK)
    assert rig.llm.requests == []
    assert rig.service.propose_plan(rig.brand_id, "2026-W47").status is RunStatus.SUCCEEDED


def test_propose_offers_only_kb_valid_on_every_day_of_the_week(
    make_rig: Callable[[Script], Rig],
) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3)))
    rig.activate_strategy()
    whole_week = add_kb(rig, "sempre")
    ends_midweek = add_kb(rig, "termina", valid_until=date(2026, 11, 12))
    ends_sunday = add_kb(rig, "domingo", valid_until=date(2026, 11, 15))
    rig.propose()
    [request] = rig.llm.requests
    part = request.messages[0].content[0]
    assert isinstance(part, TextPart)
    assert whole_week in part.text and ends_sunday in part.text
    assert ends_midweek not in part.text


def test_propose_parent_is_the_active_plan_of_that_week(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3), plan_response(WEEK, 2)))
    rig.activate_strategy()
    first, _ = rig.propose_and_approve()
    assert rig.store.get(rig.propose()).parent_id == first


def test_approve_creates_posts_and_jobs_atomically(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3)))
    strategy_id = rig.activate_strategy()
    plan_id, post_ids = rig.propose_and_approve()

    posts = rig.post_by_key()
    assert list(posts) == [f"{WEEK}-1", f"{WEEK}-2", f"{WEEK}-3"]
    assert [posts[f"{WEEK}-{n}"].id for n in (1, 2, 3)] == post_ids  # ordem dos slots
    first = posts[f"{WEEK}-1"]
    assert first.lifecycle == "planned" and first.channels == ["instagram"]
    assert first.scheduled_date == date(2026, 11, 9) and first.experiment_index == 0
    assert first.plan_artifact_id == plan_id and first.strategy_artifact_id == strategy_id
    assert first.brief == {
        "tema": "Pao do dia saido do forno",
        "pilar": "Producao artesanal",
        "objetivo": "Gerar mensagens e encomendas.",
        "justificacao": "Reforca o pilar producao artesanal com um tema concreto.",
        "kb_refs": [],
        "experiment_index": 0,
    }
    assert rig.store.get(plan_id).state is ArtifactState.APPROVED
    active = rig.store.active(rig.brand_id, ArtifactType.PLANO, scope=f"week:{WEEK}")
    assert active is not None and active.id == plan_id

    jobs = rig.jobs()
    assert {job.dedupe_key for job in jobs} == {
        f"generate_post:{post.id}:plan:{plan_id}" for post in posts.values()
    }
    assert all(job.type == "generate_post" and job.status == "queued" for job in jobs)
    assert {json.dumps(job.payload, sort_keys=True) for job in jobs} == {
        json.dumps({"post_id": pid, "owner_comment": None, "reason": "plan"}, sort_keys=True)
        for pid in post_ids
    }
    [audit] = rig.audits("plan.approve")
    assert audit.actor == "ana" and audit.subject_id == plan_id
    assert audit.detail == {
        "semana": WEEK,
        "posts": 3,
        "criados": 3,
        "atualizados": 0,
        "mantidos": 0,
        "cancelados": 0,
        "jobs": 3,
    }


def test_approve_twice_no_duplicates(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3)))
    rig.activate_strategy()
    plan_id, post_ids = rig.propose_and_approve()
    before = [(p.id, p.lifecycle) for p in rig.posts()]
    with pytest.raises(InvalidTransition):
        rig.service.approve_plan(plan_id, "ana")
    assert [(p.id, p.lifecycle) for p in rig.posts()] == before
    assert len(post_ids) == 3 and rig.count(Post) == 3 and rig.count(Job) == 3
    assert len(rig.audits("plan.approve")) == 1


def test_approve_wrong_type_names_expected_type(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans())
    strategy_id = rig.activate_strategy()
    with pytest.raises(ValueError, match="esperava plano"):
        rig.service.approve_plan(strategy_id, "ana")
    with pytest.raises(ValueError, match="esperava plano"):
        rig.service.reject_plan(strategy_id, "ana", "x")
    assert rig.count(Post) == 0


def test_approve_requires_active_strategy_and_changes_nothing(
    make_rig: Callable[[Script], Rig],
) -> None:
    rig = make_rig(plans())
    [response] = fake_scenarios.weekly_plan(WEEK, 2)
    assert not isinstance(response, Exception)
    plan = rig.store.create(rig.brand_id, ArtifactType.PLANO, json.loads(response.text))
    rig.store.submit(plan.id, "diretor")
    with pytest.raises(MissingPrerequisite, match="estrategia"):
        rig.service.approve_plan(plan.id, "ana")
    assert rig.store.get(plan.id).state is ArtifactState.SUBMITTED
    assert rig.count(Post) == 0 and rig.count(ArtifactHead) == 0


def test_reject_creates_nothing(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3)))
    rig.activate_strategy()
    plan_id = rig.propose()
    heads = rig.count(ArtifactHead)
    rig.service.reject_plan(plan_id, "ana", "temas fracos")
    assert rig.store.get(plan_id).state is ArtifactState.REJECTED
    assert rig.count(Post) == 0 and rig.count(Job) == 0 and rig.count(ArtifactHead) == heads
    assert rig.audits("plan.approve") == []
    [rejected] = [a for a in rig.audits("artifact.reject") if a.subject_id == plan_id]
    assert rejected.detail["reason"] == "temas fracos"


def test_approve_failure_rolls_back_posts_and_jobs(
    make_rig: Callable[[Script], Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3)))
    rig.activate_strategy()
    plan_id = rig.propose()
    heads = rig.count(ArtifactHead)
    real = rig.queue.enqueue_in
    calls: list[str] = []

    def flaky(
        session: Session, type: str, payload: Mapping[str, object], dedupe_key: str
    ) -> str | None:
        calls.append(dedupe_key)
        if len(calls) == 3:
            raise RuntimeError("fila avariada")
        return real(session, type, payload, dedupe_key)

    monkeypatch.setattr(rig.queue, "enqueue_in", flaky)
    with pytest.raises(RuntimeError, match="fila avariada"):
        rig.service.approve_plan(plan_id, "ana")
    assert len(calls) == 3
    assert rig.count(Post) == 0 and rig.count(Job) == 0
    assert rig.store.get(plan_id).state is ArtifactState.SUBMITTED
    assert rig.count(ArtifactHead) == heads and rig.audits("plan.approve") == []
    monkeypatch.undo()
    assert len(rig.service.approve_plan(plan_id, "ana")) == 3  # nada ficou meio feito


def test_new_plan_replaces_by_slot_key(make_rig: Callable[[Script], Rig]) -> None:
    second = plan_response(WEEK, 2, change_theme(1, "Tema novo do slot 2"))
    rig = make_rig(plans(plan_response(WEEK, 3), second))
    rig.activate_strategy()
    first_id, first_posts = rig.propose_and_approve()
    rig.set_lifecycle(f"{WEEK}-1", "approved")  # slot 1 mantido, ja aprovado pelo responsavel
    old_jobs = {job.dedupe_key: job for job in rig.jobs()}

    second_id, second_posts = rig.propose_and_approve()

    posts = rig.post_by_key()
    assert second_posts == first_posts[:2]  # mesmos posts, em ordem de slot
    assert posts[f"{WEEK}-1"].lifecycle == "approved"  # conservado intacto
    assert posts[f"{WEEK}-1"].plan_artifact_id == first_id
    assert posts[f"{WEEK}-2"].lifecycle == "planned"
    assert posts[f"{WEEK}-2"].brief["tema"] == "Tema novo do slot 2"
    assert posts[f"{WEEK}-2"].plan_artifact_id == second_id
    assert posts[f"{WEEK}-3"].lifecycle == "cancelled"
    assert rig.count(Post) == 3

    by_key = {job.dedupe_key: job for job in rig.jobs()}
    status = {key.split(":")[1]: job.status for key, job in by_key.items() if first_id in key}
    assert status[first_posts[0]] == "queued"  # slot mantido: o job continua
    assert status[first_posts[1]] == "cancelled"  # slot alterado: job antigo substituido
    assert status[first_posts[2]] == "cancelled"  # slot removido
    new_jobs = [job for key, job in by_key.items() if second_id in key]
    assert [job.payload["post_id"] for job in new_jobs] == [first_posts[1]]
    assert new_jobs[0].status == "queued" and old_jobs.keys() <= by_key.keys()
    [_, audit] = rig.audits("plan.approve")
    assert audit.detail == {
        "semana": WEEK,
        "posts": 2,
        "criados": 0,
        "atualizados": 1,
        "mantidos": 1,
        "cancelados": 1,
        "jobs": 1,
    }
    active = rig.store.active(rig.brand_id, ArtifactType.PLANO, scope=f"week:{WEEK}")
    assert active is not None and active.id == second_id


def test_identical_replacement_keeps_posts_and_creates_no_new_jobs(
    make_rig: Callable[[Script], Rig],
) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3), plan_response(WEEK, 3)))
    rig.activate_strategy()
    _, first_posts = rig.propose_and_approve()
    second_id, second_posts = rig.propose_and_approve()
    assert second_posts == first_posts
    assert rig.count(Job) == 3 and {j.status for j in rig.jobs()} == {"queued"}
    assert {p.plan_artifact_id for p in rig.posts()} == {second_id}  # aponta ao plano ativo
    assert all(p.lifecycle == "planned" for p in rig.posts())


def test_removed_slots_are_cancelled_except_rejected(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 4), plan_response(WEEK, 1)))
    rig.activate_strategy()
    rig.propose_and_approve()
    rig.set_lifecycle(f"{WEEK}-2", "in_review")
    rig.set_lifecycle(f"{WEEK}-3", "approved")
    rig.set_lifecycle(f"{WEEK}-4", "rejected")
    rig.propose_and_approve()
    lifecycles = {key: post.lifecycle for key, post in rig.post_by_key().items()}
    assert lifecycles == {
        f"{WEEK}-1": "planned",
        f"{WEEK}-2": "cancelled",
        f"{WEEK}-3": "cancelled",
        f"{WEEK}-4": "rejected",
    }
    [audit] = rig.audits("plan.approve")[-1:]
    assert audit.detail["cancelados"] == 2


def test_cancelled_slot_is_revived_with_a_new_job(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3), plan_response(WEEK, 2), plan_response(WEEK, 3)))
    rig.activate_strategy()
    _, first_posts = rig.propose_and_approve()
    rig.propose_and_approve()
    assert rig.post_by_key()[f"{WEEK}-3"].lifecycle == "cancelled"
    third_id, third_posts = rig.propose_and_approve()
    assert third_posts == first_posts
    revived = rig.post_by_key()[f"{WEEK}-3"]
    assert revived.lifecycle == "planned" and revived.plan_artifact_id == third_id
    [job] = [j for j in rig.jobs() if j.dedupe_key == f"generate_post:{revived.id}:plan:{third_id}"]
    assert job.status == "queued" and rig.count(Post) == 3


def test_posts_of_other_weeks_are_never_touched(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response("2026-W47", 2), plan_response(WEEK, 1)))
    rig.activate_strategy()
    rig.propose_and_approve("2026-W47")
    rig.propose_and_approve(WEEK)
    lifecycles = {key: post.lifecycle for key, post in rig.post_by_key().items()}
    assert lifecycles == {"2026-W47-1": "planned", "2026-W47-2": "planned", f"{WEEK}-1": "planned"}
    assert all(job.status == "queued" for job in rig.jobs())


def test_changed_draft_post_gets_new_brief_and_job(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 2), plan_response(WEEK, 2, change_theme(0, "Outro"))))
    rig.activate_strategy()
    rig.propose_and_approve()
    rig.set_lifecycle(f"{WEEK}-1", "drafting")
    second_id, _ = rig.propose_and_approve()
    first = rig.post_by_key()[f"{WEEK}-1"]
    assert first.lifecycle == "planned" and first.brief["tema"] == "Outro"
    statuses = {j.dedupe_key: j.status for j in rig.jobs()}
    assert statuses[f"generate_post:{first.id}:plan:{second_id}"] == "queued"
    assert sum(status == "cancelled" for status in statuses.values()) == 1


SLOT_1 = f"{WEEK}-1"


def reapprove_identical(rig: Rig, status: str, lifecycle: str = "planned") -> tuple[str, str]:
    """Aprova o plano, estraga o trabalho do slot 1 e reaprova um plano identico."""
    rig_post_ids = rig.propose_and_approve()[1]
    first = rig_post_ids[0]
    rig.set_job_status(first, status)
    rig.set_post(SLOT_1, last_error="falhou", lifecycle=lifecycle)
    second_id, _ = rig.propose_and_approve()
    return first, second_id


@pytest.mark.parametrize("status", ["failed", "dead"])
@pytest.mark.parametrize("lifecycle", ["planned", "drafting"])
def test_identical_reapproval_regenerates_post_whose_job_ended(
    make_rig: Callable[[Script], Rig], status: str, lifecycle: str
) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3), plan_response(WEEK, 3)))
    rig.activate_strategy()
    post_id, second_id = reapprove_identical(rig, status, lifecycle)
    [new_job] = [j for j in rig.jobs_of(post_id) if j.status == "queued"]
    assert new_job.dedupe_key == f"generate_post:{post_id}:plan:{second_id}"
    assert new_job.payload == {"post_id": post_id, "owner_comment": None, "reason": "plan"}
    post = rig.post_by_key()[SLOT_1]
    assert post.last_error is None and post.lifecycle == lifecycle  # drafting nao volta a planned
    assert post.plan_artifact_id == second_id
    assert rig.count(Job) == 4  # os outros dois posts mantem o trabalho que tinham
    assert rig.audits("plan.approve")[-1].detail["jobs"] == 1


@pytest.mark.parametrize("status", ["queued", "running"])
def test_identical_reapproval_with_active_job_adds_no_job(
    make_rig: Callable[[Script], Rig], status: str
) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3), plan_response(WEEK, 3)))
    rig.activate_strategy()
    post_id, _ = reapprove_identical(rig, status)
    assert rig.count(Job) == 3
    assert [j.status for j in rig.jobs_of(post_id)] == [status]
    assert rig.post_by_key()[SLOT_1].last_error == "falhou"  # nada a regenerar: nao se mexe
    assert rig.audits("plan.approve")[-1].detail["jobs"] == 0


@pytest.mark.parametrize("lifecycle", ["in_review", "approved", "rejected"])
def test_identical_reapproval_leaves_reviewed_posts_alone(
    make_rig: Callable[[Script], Rig], lifecycle: str
) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3), plan_response(WEEK, 3)))
    rig.activate_strategy()
    post_id, _ = reapprove_identical(rig, "failed", lifecycle)
    assert rig.count(Job) == 3 and [j.status for j in rig.jobs_of(post_id)] == ["failed"]
    assert rig.post_by_key()[SLOT_1].lifecycle == lifecycle


def test_rewritten_post_restarts_review_rounds(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 2), plan_response(WEEK, 2, change_theme(0, "Outro"))))
    rig.activate_strategy()
    rig.propose_and_approve()
    rig.set_post(SLOT_1, review_rounds=2, lifecycle="drafting")
    rig.set_post(f"{WEEK}-2", review_rounds=2)
    rig.propose_and_approve()
    posts = rig.post_by_key()
    assert posts[SLOT_1].review_rounds == 0  # brief novo: nova geracao
    assert posts[f"{WEEK}-2"].review_rounds == 2  # igual: conserva


def test_revived_post_restarts_review_rounds(make_rig: Callable[[Script], Rig]) -> None:
    rig = make_rig(plans(plan_response(WEEK, 2), plan_response(WEEK, 1), plan_response(WEEK, 2)))
    rig.activate_strategy()
    rig.propose_and_approve()
    rig.set_post(f"{WEEK}-2", review_rounds=3)
    rig.propose_and_approve()
    assert rig.post_by_key()[f"{WEEK}-2"].review_rounds == 3  # cancelado conserva o historico
    rig.propose_and_approve()
    assert rig.post_by_key()[f"{WEEK}-2"].review_rounds == 0


BARRIER_TIMEOUT_S = 5
RESULT_TIMEOUT_S = 30


def approve_concurrently(rig: Rig, plan_ids: list[str]) -> list[object]:
    """Aprova cada plano numa thread propria, largando-as juntas; devolve resultado ou erro."""
    barrier = threading.Barrier(len(plan_ids), timeout=BARRIER_TIMEOUT_S)

    def attempt(plan_id: str) -> object:
        barrier.wait()
        try:
            return rig.service.approve_plan(plan_id, "ana")
        except Exception as exc:  # o teste classifica o erro
            return exc

    with ThreadPoolExecutor(max_workers=len(plan_ids)) as pool:
        futures = [pool.submit(attempt, plan_id) for plan_id in plan_ids]
        return [future.result(timeout=RESULT_TIMEOUT_S) for future in futures]


WEEKS = ["2026-W46", "2026-W47", "2026-W48"]  # uma semana por repeticao: a mesma marca serve


def test_same_plan_approved_from_two_threads_succeeds_once(
    make_rig: Callable[[Script], Rig],
) -> None:
    rig = make_rig(plans(*(plan_response(week, 3) for week in WEEKS)))
    rig.activate_strategy()
    for repetition, week in enumerate(WEEKS):  # repete para o intercalamento aparecer
        plan_id = rig.propose(week)
        results = approve_concurrently(rig, [plan_id, plan_id])
        failures = [r for r in results if isinstance(r, Exception)]
        assert len(failures) == 1 and isinstance(failures[0], InvalidTransition), repetition
        assert len(rig.audits("plan.approve")) == repetition + 1
        assert rig.count(Post) == 3 * (repetition + 1) and rig.count(Job) == 3 * (repetition + 1)


def test_two_plans_of_the_same_week_approved_concurrently_agree_with_the_head(
    make_rig: Callable[[Script], Rig],
) -> None:
    rig = make_rig(plans())
    rig.activate_strategy()
    for repetition, week in enumerate(WEEKS):
        ids = [rig.submit_plan(week, 3, label) for label in ("A", "B")]
        results = approve_concurrently(rig, ids)
        assert not [r for r in results if isinstance(r, Exception)], (repetition, results)
        head = rig.store.active(rig.brand_id, ArtifactType.PLANO, scope=f"week:{week}")
        assert head is not None and head.id in ids
        slots = head.payload["slots"]
        assert isinstance(slots, list)
        posts = [p for key, p in rig.post_by_key().items() if key.startswith(f"{week}-")]
        assert len(posts) == 3
        for post, slot in zip(posts, slots, strict=True):
            assert isinstance(slot, dict)
            assert post.plan_artifact_id == head.id and post.brief["tema"] == slot["tema"]
            assert [j.status for j in rig.jobs_of(post.id)].count("queued") == 1
