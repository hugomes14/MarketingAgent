"""Teto de custo por pacote: soma todas as tentativas do pedido e limita cada execucao."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from content_rig import Factory, build_rig, package_pass
from sqlalchemy import select

from marketing_agent.clock import FixedClock
from marketing_agent.content.package_costs import run_trigger
from marketing_agent.content.package_guard import (
    PACKAGE_COST_MESSAGE,
    PackageCostExceeded,
    PackageCostGuard,
    run_capped,
)
from marketing_agent.content.specs import load_content_specs
from marketing_agent.db.models import AgentRun, CostEntry, CostReservation, PostRevision
from marketing_agent.harness.engine import RunResult, RunStatus

AT = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def add_run(factory: Factory, brand_id: str, trigger: str) -> str:
    with factory.begin() as session:
        run = AgentRun(
            brand_id=brand_id,
            role="copywriter",
            spec_version=1,
            trigger=trigger,
            status="succeeded",
            started_at=AT,
        )
        session.add(run)
        session.flush()
        return run.id


def add_reservation(factory: Factory, brand_id: str, run_id: str, usd: str, status: str) -> str:
    with factory.begin() as session:
        reservation = CostReservation(
            brand_id=brand_id,
            run_id=run_id,
            role="copywriter",
            provider="fake",
            model_id="fake-1",
            price_version="v1",
            reserved_usd=Decimal(usd),
            status=status,
            created_at=AT,
        )
        session.add(reservation)
        session.flush()
        return reservation.id


def add_cost(factory: Factory, brand_id: str, run_id: str, usd: str) -> None:
    reservation_id = add_reservation(factory, brand_id, run_id, usd, "settled")
    with factory.begin() as session:
        session.add(
            CostEntry(
                brand_id=brand_id,
                reservation_id=reservation_id,
                role="copywriter",
                run_id=run_id,
                provider="fake",
                model_id="fake-1",
                price_version="v1",
                tokens_in=10,
                tokens_out=10,
                cost_usd=Decimal(usd),
                kind="measured",
                at=AT,
            )
        )


def test_guard_counts_entries_and_pending_reservations_of_the_request_only(
    session_factory: Factory, brand_id: str
) -> None:
    mine = add_run(session_factory, brand_id, run_trigger("copy", "job-1"))
    retry = add_run(session_factory, brand_id, run_trigger("review", "job-1"))
    other = add_run(session_factory, brand_id, run_trigger("copy", "job-2"))
    evaluated = add_run(session_factory, brand_id, run_trigger("copy", "job-1", "eval"))
    add_cost(session_factory, brand_id, mine, "0.40")
    add_reservation(session_factory, brand_id, retry, "0.20", "open")
    add_reservation(session_factory, brand_id, retry, "0.10", "unknown_outcome")
    add_reservation(session_factory, brand_id, retry, "0.30", "released")
    add_cost(session_factory, brand_id, other, "5")
    add_cost(session_factory, brand_id, evaluated, "5")
    guard = PackageCostGuard(session_factory, brand_id, "job-1", Decimal("1.50"))
    assert guard.spent_usd() == Decimal("0.70")
    eval_guard = PackageCostGuard(session_factory, brand_id, "job-1", Decimal("9"), "eval")
    assert eval_guard.spent_usd() == Decimal("5")


def test_guard_caps_the_spec_to_what_remains(session_factory: Factory, brand_id: str) -> None:
    run_id = add_run(session_factory, brand_id, run_trigger("copy", "job-1"))
    spec = load_content_specs().copywriter
    guard = PackageCostGuard(session_factory, brand_id, "job-1", Decimal("1.50"))
    assert guard.cap(spec) is spec  # sobra mais do que o limite do proprio spec
    add_cost(session_factory, brand_id, run_id, "1.45")
    capped = guard.cap(spec)
    assert capped.limits.max_cost_usd == Decimal("0.05")
    assert capped.limits.max_steps == spec.limits.max_steps
    add_cost(session_factory, brand_id, run_id, "0.05")
    with pytest.raises(PackageCostExceeded):
        guard.cap(spec)


def test_guard_rejects_non_positive_ceiling(session_factory: Factory, brand_id: str) -> None:
    with pytest.raises(ValueError, match="positivo"):
        PackageCostGuard(session_factory, brand_id, "job-1", Decimal(0))


def test_run_capped_without_budget_returns_a_limit_and_never_runs(
    session_factory: Factory, brand_id: str
) -> None:
    run_id = add_run(session_factory, brand_id, run_trigger("copy", "job-1"))
    add_cost(session_factory, brand_id, run_id, "2")
    guard = PackageCostGuard(session_factory, brand_id, "job-1", Decimal("1.50"))
    calls: list[object] = []

    def run(spec: object) -> RunResult:
        calls.append(spec)
        return RunResult(RunStatus.SUCCEEDED, "x", None, None)

    result = run_capped(guard, load_content_specs().copywriter, run)
    assert (result.status, result.run_id, result.limit) == (
        RunStatus.BLOCKED_LIMIT,
        "",
        PackageCostExceeded,
    )
    assert result.message == PACKAGE_COST_MESSAGE and calls == []
    assert run_capped(None, load_content_specs().copywriter, run).status is RunStatus.SUCCEEDED


def test_guard_caps_package_total_across_attempts(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    """Uma tentativa anterior do mesmo job ja gastou o teto: a nova nao chama o modelo."""
    rig = build_rig(
        session_factory, clock, brand_id, package_pass(), media_dir=tmp_path / "m",
        package_max_cost_usd=Decimal("0.50"),
    )  # fmt: skip
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    earlier = add_run(session_factory, brand_id, run_trigger("design", job.id))
    add_cost(session_factory, brand_id, earlier, "0.50")
    assert rig.pipeline.generate(job) == "drafting_failed"
    assert rig.llm.requests == []
    assert rig.post(post_id).last_error == "PackageCostExceeded"
    with session_factory() as session:
        assert session.scalars(select(PostRevision)).all() == []


def test_paid_background_stops_at_the_package_ceiling(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    """O Revisor recusa o fundo pago: o segundo fundo nao cabe no teto e segue o 1.o pacote."""
    script = [
        *package_pass("rever", ["imagem_inadequada"], "Fundo com pessoas."),
        *package_pass()[1:],  # o Designer volta (sem Copywriter): a geracao nao cabe
    ]
    rig = build_rig(
        session_factory, clock, brand_id, script, media_dir=tmp_path / "m",
        image_price="0.40", package_max_cost_usd=Decimal("0.60"),
    )  # fmt: skip
    post_id = rig.seed_post()
    assert rig.pipeline.generate(rig.claimed(post_id)) == "in_review"
    assert rig.generated_requests() == 1
    assert sum(entry.cost_usd for entry in rig.entries()) <= Decimal("0.60")
    [revision] = rig.revisions(post_id)
    assert revision.review_verdict == "rever"
    assert revision.review_notes is not None and "indisponivel" in revision.review_notes
