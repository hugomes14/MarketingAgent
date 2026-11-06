"""Reservas de custo: limite por execucao, acerto idempotente e estado do mes."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.budget.controller import (
    BudgetController,
    ReservationRequest,
    RunCostLimitExceeded,
)
from marketing_agent.budget.pricing import (
    ModelPrice,
    PriceBook,
    PriceTier,
    Usage,
    sync_model_prices,
)
from marketing_agent.clock import FixedClock
from marketing_agent.db.models import AgentRun, AgentStep, CostEntry, CostReservation

Factory = sessionmaker[Session]
VALID_FROM = datetime(2026, 11, 1, tzinfo=UTC)
REFERENCE = Decimal(20)
RUN_LIMIT = Decimal(1)


@pytest.fixture
def controller(session_factory: Factory, clock: FixedClock) -> BudgetController:
    # Entrada 1 USD/M, saida 10 USD/M: 100000 de entrada + 50000 de saida custam 0,6 USD.
    price = ModelPrice(
        "fake", "m1", "v1", (PriceTier(0, Decimal(1), Decimal(10)),), valid_from=VALID_FROM
    )
    sync_model_prices(session_factory, [price])

    return BudgetController(
        session_factory, PriceBook(session_factory), clock, REFERENCE, (50, 80, 100)
    )


def make_run(session_factory: Factory, brand_id: str) -> tuple[str, str]:
    """Cria uma execucao com um passo e devolve (run_id, step_id)."""
    with session_factory.begin() as session:
        run = AgentRun(
            brand_id=brand_id,
            role="director",
            spec_version=1,
            trigger="test",
            status="running",
            started_at=VALID_FROM,
        )
        session.add(run)
        session.flush()
        step = AgentStep(run_id=run.id, seq=1, kind="llm", name="call")
        session.add(step)
        session.flush()
        return run.id, step.id


def request(
    brand_id: str, run_id: str, step_id: str, out_tokens: int = 50000
) -> ReservationRequest:
    return ReservationRequest(
        brand_id, "director", run_id, step_id, "fake", "m1", 100000, out_tokens
    )


def add_settled_entry(
    session_factory: Factory, brand_id: str, run_id: str, cost: Decimal, at: datetime
) -> None:
    """Grava uma reserva acertada e o respetivo lancamento."""
    with session_factory.begin() as session:
        reservation = CostReservation(
            brand_id=brand_id,
            run_id=run_id,
            role="director",
            provider="fake",
            model_id="m1",
            price_version="v1",
            reserved_usd=cost,
            status="settled",
            created_at=at,
            settled_at=at,
        )
        session.add(reservation)
        session.flush()
        session.add(
            CostEntry(
                brand_id=brand_id,
                reservation_id=reservation.id,
                role="director",
                run_id=run_id,
                provider="fake",
                model_id="m1",
                price_version="v1",
                tokens_in=0,
                tokens_out=0,
                cost_usd=cost,
                kind="measured",
                at=at,
            )
        )


def test_reserve_then_settle_records_measured_entry(
    controller: BudgetController, session_factory: Factory, brand_id: str, clock: FixedClock
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    reservation = controller.reserve(request(brand_id, run_id, step_id), RUN_LIMIT)
    assert reservation.reserved_usd == Decimal("0.6")
    cost = controller.settle(reservation.id, Usage(100000, 20000, 5000), "req-1")
    assert cost == Decimal("0.3")
    with session_factory() as session:
        entry = session.scalars(select(CostEntry)).one()
        row = session.get(CostReservation, reservation.id)
        assert row is not None
        assert row.status == "settled"
        assert row.settled_at == clock.now()
    assert (entry.kind, entry.price_version, entry.provider_request_id) == (
        "measured",
        "v1",
        "req-1",
    )
    assert (entry.tokens_in, entry.tokens_out, entry.tokens_reasoning) == (100000, 20000, 5000)
    assert (entry.cost_usd, entry.step_id, entry.at) == (Decimal("0.3"), step_id, clock.now())


def test_settle_twice_single_entry(
    controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    reservation = controller.reserve(request(brand_id, run_id, step_id), RUN_LIMIT)
    first = controller.settle(reservation.id, Usage(100000, 20000), None)
    second = controller.settle(reservation.id, Usage(1, 1), None)
    assert first == second == Decimal("0.3")
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CostEntry)) == 1


def test_concurrent_reservations_respect_run_limit(
    controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    for _ in range(5):  # repete para o intercalamento sem lock aparecer
        run_id, step_id = make_run(session_factory, brand_id)
        barrier = threading.Barrier(2)

        def attempt(
            run_id: str = run_id, step_id: str = step_id, barrier: threading.Barrier = barrier
        ) -> bool:
            barrier.wait()
            try:
                controller.reserve(request(brand_id, run_id, step_id), RUN_LIMIT)
            except RunCostLimitExceeded:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result() for f in [pool.submit(attempt), pool.submit(attempt)]]
        assert sorted(results) == [False, True]
        with session_factory() as session:
            count = session.scalar(
                select(func.count())
                .select_from(CostReservation)
                .where(CostReservation.run_id == run_id)
            )
        assert count == 1


def test_reference_exceeded_does_not_block(
    controller: BudgetController, session_factory: Factory, brand_id: str, clock: FixedClock
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    add_settled_entry(session_factory, brand_id, run_id, Decimal(25), clock.now())
    controller.reserve(request(brand_id, run_id, step_id), Decimal(100))
    status = controller.status(brand_id)
    assert status.over_reference is True
    assert status.spent_usd == Decimal(25)
    assert status.reserved_usd == Decimal("0.6")


def test_warn_percents_count_pending_reservations(
    controller: BudgetController, session_factory: Factory, brand_id: str, clock: FixedClock
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    add_settled_entry(session_factory, brand_id, run_id, Decimal(9), clock.now())
    assert controller.status(brand_id).warn_percent is None
    controller.reserve(request(brand_id, run_id, step_id, out_tokens=100000), Decimal(100))
    status = controller.status(brand_id)
    assert status.reserved_usd == Decimal(1) + Decimal("0.1")
    # 9 + 1,1 = 10,1 de 20 USD: 50,5 % -> limiar 50.
    assert status.warn_percent == 50
    assert status.over_reference is False


def test_month_boundary_utc(
    controller: BudgetController, session_factory: Factory, brand_id: str, clock: FixedClock
) -> None:
    run_id, _ = make_run(session_factory, brand_id)
    add_settled_entry(
        session_factory,
        brand_id,
        run_id,
        Decimal(7),
        datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC),
    )
    add_settled_entry(
        session_factory, brand_id, run_id, Decimal(3), datetime(2026, 11, 1, tzinfo=UTC)
    )
    status = controller.status(brand_id)
    assert status.spent_usd == Decimal(3)
    # Dia 5 de 30: 3 * 30 / 5.
    assert status.projected_usd == Decimal(18)


def test_recover_stale_marks_unknown_and_counts_in_status(
    controller: BudgetController, session_factory: Factory, brand_id: str, clock: FixedClock
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    controller.reserve(request(brand_id, run_id, step_id), RUN_LIMIT)
    assert controller.recover_stale(timedelta(hours=1)) == 0
    clock.advance(timedelta(hours=2))
    assert controller.recover_stale(timedelta(hours=1)) == 1
    assert controller.recover_stale(timedelta(hours=1)) == 0
    status = controller.status(brand_id)
    assert (status.reserved_usd, status.unknown_usd) == (Decimal(0), Decimal("0.6"))


def test_amounts_rounded_to_six_decimals_consistently(
    controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    fine = ModelPrice(
        "fake", "m2", "v1", (PriceTier(0, Decimal("0.15"), Decimal("0.60")),), valid_from=VALID_FROM
    )
    sync_model_prices(session_factory, [fine])
    run_id, step_id = make_run(session_factory, brand_id)
    pedido = ReservationRequest(brand_id, "director", run_id, step_id, "fake", "m2", 1234, 0)
    reservation = controller.reserve(pedido, RUN_LIMIT)
    # 1234 * 0.15 / 1e6 = 0.0001851: a reserva arredonda para cima, o custo para o mais proximo.
    assert reservation.reserved_usd == Decimal("0.000186")
    first = controller.settle(reservation.id, Usage(1234, 0), None)
    second = controller.settle(reservation.id, Usage(1234, 0), None)
    with session_factory() as session:
        entry = session.scalars(select(CostEntry)).one()
        row = session.get(CostReservation, reservation.id)
        assert row is not None
        assert row.reserved_usd == reservation.reserved_usd
    assert first == second == entry.cost_usd == Decimal("0.000185")
