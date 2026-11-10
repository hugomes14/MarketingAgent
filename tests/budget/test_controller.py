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
    ImageReservationRequest,
    ReservationRequest,
    RunCostLimitExceeded,
)
from marketing_agent.budget.pricing import (
    ImagePrice,
    ImageUsage,
    ModelPrice,
    PriceBook,
    PriceTier,
    UnknownPrice,
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


SIZE = "1024x1536"
UNIT = Decimal("0.2")  # preco da imagem "high": cinco imagens somam exatamente RUN_LIMIT


@pytest.fixture
def image_controller(controller: BudgetController, session_factory: Factory) -> BudgetController:
    price = ModelPrice(
        "fake",
        "img-1",
        "v1",
        (PriceTier(0, Decimal(0), Decimal(0)),),
        valid_from=VALID_FROM,
        per_image=(
            ImagePrice("low", SIZE, Decimal("0.011")),
            ImagePrice("medium", SIZE, Decimal("0.05")),
            ImagePrice("high", SIZE, UNIT),
        ),
    )
    sync_model_prices(session_factory, [price])
    return controller


def image_request(
    brand_id: str, run_id: str, step_id: str, quality: str = "high", model_id: str = "img-1"
) -> ImageReservationRequest:
    return ImageReservationRequest(
        brand_id, "designer", run_id, step_id, "fake", model_id, SIZE, quality
    )


def count_rows(session_factory: Factory, model: type[CostReservation] | type[CostEntry]) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_reserve_image_holds_the_price_book_amount(
    image_controller: BudgetController, session_factory: Factory, brand_id: str, clock: FixedClock
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    reservation = image_controller.reserve_image(
        image_request(brand_id, run_id, step_id, "medium"), RUN_LIMIT
    )
    assert reservation.reserved_usd == Decimal("0.05")
    with session_factory() as session:
        row = session.get(CostReservation, reservation.id)
    assert row is not None
    assert (row.status, row.reserved_usd, row.price_version) == ("open", Decimal("0.05"), "v1")
    assert (row.brand_id, row.role, row.run_id, row.step_id) == (
        brand_id,
        "designer",
        run_id,
        step_id,
    )
    assert (row.provider, row.model_id, row.created_at) == ("fake", "img-1", clock.now())


def test_reserve_image_respects_run_limit(
    image_controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    for _ in range(5):  # 5 x 0,2 = 1,0: exatamente igual ao limite e permitido
        image_controller.reserve_image(image_request(brand_id, run_id, step_id), RUN_LIMIT)
    with pytest.raises(RunCostLimitExceeded, match=f"execucao {run_id}"):
        image_controller.reserve_image(image_request(brand_id, run_id, step_id, "low"), RUN_LIMIT)
    assert count_rows(session_factory, CostReservation) == 5


def test_reserve_image_refuses_one_cent_over_the_limit(
    image_controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    limit = Decimal("0.99")
    for _ in range(4):
        image_controller.reserve_image(image_request(brand_id, run_id, step_id), limit)
    with pytest.raises(RunCostLimitExceeded):
        image_controller.reserve_image(image_request(brand_id, run_id, step_id), limit)
    assert count_rows(session_factory, CostReservation) == 4


def test_reserve_image_counts_text_reservations_and_settled_entries(
    image_controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    text = image_controller.reserve(request(brand_id, run_id, step_id), RUN_LIMIT)  # 0,6 abertos
    first = image_controller.reserve_image(image_request(brand_id, run_id, step_id), RUN_LIMIT)
    image_controller.settle_image(first.id, ImageUsage(1, SIZE, "high"), "img-req-1")
    image_controller.reserve_image(image_request(brand_id, run_id, step_id), RUN_LIMIT)  # = 1,0
    with pytest.raises(RunCostLimitExceeded):
        image_controller.reserve_image(image_request(brand_id, run_id, step_id, "low"), RUN_LIMIT)
    image_controller.release(text.id)  # libertar o texto volta a deixar espaco
    image_controller.reserve_image(image_request(brand_id, run_id, step_id, "low"), RUN_LIMIT)


def test_reserve_image_does_not_enforce_monthly_reference(
    session_factory: Factory, clock: FixedClock, brand_id: str, controller: BudgetController
) -> None:
    # Como `reserve`: a referencia mensal so informa (BudgetStatus.over_reference).
    tiny = BudgetController(
        session_factory, PriceBook(session_factory), clock, Decimal("0.01"), (50, 100)
    )
    price = ModelPrice(
        "fake",
        "img-1",
        "v1",
        (PriceTier(0, Decimal(0), Decimal(0)),),
        valid_from=VALID_FROM,
        per_image=(ImagePrice("high", SIZE, UNIT),),
    )
    sync_model_prices(session_factory, [price])
    run_id, step_id = make_run(session_factory, brand_id)
    tiny.reserve_image(image_request(brand_id, run_id, step_id), RUN_LIMIT)
    assert tiny.status(brand_id).over_reference is True


def test_reserve_image_unknown_price_writes_nothing(
    image_controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    with pytest.raises(UnknownPrice, match="quality=ultra"):
        image_controller.reserve_image(image_request(brand_id, run_id, step_id, "ultra"), RUN_LIMIT)
    with pytest.raises(UnknownPrice):  # modelo sem nenhum preco
        image_controller.reserve_image(
            image_request(brand_id, run_id, step_id, model_id="nada"), RUN_LIMIT
        )
    with pytest.raises(UnknownPrice):  # modelo de texto, sem precos por imagem
        image_controller.reserve_image(
            image_request(brand_id, run_id, step_id, model_id="m1"), RUN_LIMIT
        )
    assert count_rows(session_factory, CostReservation) == 0


def test_reserve_image_rolls_back_when_the_transaction_fails(
    image_controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    run_id, _ = make_run(session_factory, brand_id)
    with pytest.raises(Exception, match="agent_step"):  # FK do passo falha no commit
        image_controller.reserve_image(
            image_request(brand_id, run_id, "00000000-0000-0000-0000-000000000000"), RUN_LIMIT
        )
    assert count_rows(session_factory, CostReservation) == 0


def test_reserve_image_unknown_run_raises_lookup_error(
    image_controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    with pytest.raises(LookupError, match="nao existe"):
        image_controller.reserve_image(image_request(brand_id, "sem-run", "sem-step"), RUN_LIMIT)
    assert count_rows(session_factory, CostReservation) == 0


def test_settle_image_writes_units_and_is_idempotent(
    image_controller: BudgetController, session_factory: Factory, brand_id: str, clock: FixedClock
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    reservation = image_controller.reserve_image(
        image_request(brand_id, run_id, step_id, "medium"), RUN_LIMIT
    )
    usage = ImageUsage(1, SIZE, "medium")
    first = image_controller.settle_image(reservation.id, usage, "img-req-1")
    second = image_controller.settle_image(reservation.id, usage, "img-req-2")
    assert first == second == Decimal("0.05")
    with session_factory() as session:
        entry = session.scalars(select(CostEntry)).one()
        row = session.get(CostReservation, reservation.id)
    assert row is not None
    assert (row.status, row.settled_at) == ("settled", clock.now())
    assert entry.units == {"images": 1, "size": SIZE, "quality": "medium"}
    assert (entry.tokens_in, entry.tokens_out, entry.tokens_reasoning) == (0, 0, None)
    assert (entry.cost_usd, entry.kind, entry.price_version) == (Decimal("0.05"), "measured", "v1")
    assert (entry.provider_request_id, entry.reservation_id) == ("img-req-1", reservation.id)
    assert (entry.provider, entry.model_id, entry.role) == ("fake", "img-1", "designer")
    assert (entry.brand_id, entry.run_id, entry.step_id) == (brand_id, run_id, step_id)
    assert entry.at == clock.now()


def test_settle_image_concurrent_double_call_writes_one_entry(
    image_controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    reservation = image_controller.reserve_image(
        image_request(brand_id, run_id, step_id), RUN_LIMIT
    )
    barrier = threading.Barrier(2, timeout=10)

    def attempt() -> Decimal:
        barrier.wait()
        return image_controller.settle_image(reservation.id, ImageUsage(1, SIZE, "high"), "rq")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=30) for f in [pool.submit(attempt), pool.submit(attempt)]]
    assert results == [UNIT, UNIT]
    assert count_rows(session_factory, CostEntry) == 1


def test_settle_image_rejects_usage_that_differs_from_the_reservation(
    image_controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    reservation = image_controller.reserve_image(
        image_request(brand_id, run_id, step_id, "medium"), RUN_LIMIT
    )
    for bad in (ImageUsage(1, SIZE, "high"), ImageUsage(2, SIZE, "medium")):
        with pytest.raises(ValueError, match="difere da reserva") as error:
            image_controller.settle_image(reservation.id, bad, "rq")
        assert "quality" in str(error.value)
    unknown = ImageUsage(1, "512x512", "medium")
    with pytest.raises(UnknownPrice):
        image_controller.settle_image(reservation.id, unknown, "rq")
    assert count_rows(session_factory, CostEntry) == 0
    with session_factory() as session:
        row = session.get(CostReservation, reservation.id)
    assert row is not None and row.status == "open"
    image_controller.settle_image(reservation.id, ImageUsage(1, SIZE, "medium"), "rq")  # ainda ok


def test_settle_image_refuses_a_released_reservation(
    image_controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    reservation = image_controller.reserve_image(
        image_request(brand_id, run_id, step_id), RUN_LIMIT
    )
    image_controller.release(reservation.id)
    with pytest.raises(ValueError, match="libertada"):
        image_controller.settle_image(reservation.id, ImageUsage(1, SIZE, "high"), None)
    assert count_rows(session_factory, CostEntry) == 0


def test_release_image_reservation(
    image_controller: BudgetController, session_factory: Factory, brand_id: str, clock: FixedClock
) -> None:
    run_id, step_id = make_run(session_factory, brand_id)
    reservation = image_controller.reserve_image(
        image_request(brand_id, run_id, step_id), RUN_LIMIT
    )
    assert image_controller.status(brand_id).reserved_usd == UNIT
    image_controller.release(reservation.id)
    image_controller.release(reservation.id)  # idempotente
    with session_factory() as session:
        row = session.get(CostReservation, reservation.id)
    assert row is not None
    assert (row.status, row.settled_at) == ("released", clock.now())
    assert image_controller.status(brand_id).reserved_usd == 0
    for _ in range(5):  # a execucao volta a ter o limite todo
        image_controller.reserve_image(image_request(brand_id, run_id, step_id), RUN_LIMIT)


def test_concurrent_image_reservations_respect_run_limit(
    image_controller: BudgetController, session_factory: Factory, brand_id: str
) -> None:
    limit = Decimal("0.3")  # cabe uma imagem de 0,2; a segunda passaria para 0,4
    for _ in range(5):  # repete para o intercalamento sem lock aparecer
        run_id, step_id = make_run(session_factory, brand_id)
        barrier = threading.Barrier(2, timeout=10)

        def attempt(
            run_id: str = run_id, step_id: str = step_id, barrier: threading.Barrier = barrier
        ) -> bool:
            barrier.wait()
            try:
                image_controller.reserve_image(image_request(brand_id, run_id, step_id), limit)
            except RunCostLimitExceeded:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result(timeout=30) for f in [pool.submit(attempt), pool.submit(attempt)]]
        assert sorted(results) == [False, True]
        with session_factory() as session:
            count = session.scalar(
                select(func.count())
                .select_from(CostReservation)
                .where(CostReservation.run_id == run_id)
            )
        assert count == 1
