"""Reservas de custo por chamada, acerto idempotente e estado do mes.

So o limite de custo por execucao recusa chamadas (lock na linha do `agent_run`); a referencia
mensal apenas informa (`BudgetStatus.over_reference`). Nenhum lock e mantido durante chamadas
ao fornecedor: cada operacao e uma transacao curta.
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.orm import InstrumentedAttribute, Session, sessionmaker

from marketing_agent.budget.pricing import PriceBook, Usage, compute_cost_usd
from marketing_agent.clock import Clock
from marketing_agent.db.base import new_id
from marketing_agent.db.models import AgentRun, CostEntry, CostReservation

PENDING_STATUSES = ("open", "unknown_outcome")
CENT_MILLI = Decimal("0.000001")  # escala das colunas Numeric(12, 6)


class RunCostLimitExceeded(Exception):
    """A nova reserva ultrapassaria o limite de custo da execucao."""


class ReservationNotFound(LookupError):
    """Nao existe reserva com o identificador pedido."""


@dataclass(frozen=True)
class ReservationRequest:
    """Pedido de reserva: estimativa de entrada e teto de saida da chamada."""

    brand_id: str
    role: str
    run_id: str
    step_id: str
    provider: str
    model_id: str
    input_tokens_estimate: int
    max_output_tokens: int


@dataclass(frozen=True)
class Reservation:
    """Reserva criada, com o valor retido."""

    id: str
    reserved_usd: Decimal


@dataclass(frozen=True)
class BudgetStatus:
    """Estado do mes civil UTC; `unknown_usd` conta chamadas de desfecho desconhecido."""

    spent_usd: Decimal
    reserved_usd: Decimal
    unknown_usd: Decimal
    projected_usd: Decimal
    reference_usd: Decimal
    warn_percent: int | None
    over_reference: bool


def _money(value: Decimal, rounding: str) -> Decimal:
    """Quantiza a 6 casas, como a base guarda, para valor devolvido e gravado coincidirem."""
    return value.quantize(CENT_MILLI, rounding=rounding)


def _month_window(now: datetime) -> tuple[datetime, datetime]:
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    end = datetime(now.year + now.month // 12, now.month % 12 + 1, 1, tzinfo=UTC)
    return start, end


def _sum_money(
    session: Session, column: InstrumentedAttribute[Decimal], *conditions: ColumnElement[bool]
) -> Decimal:
    total = session.scalar(select(func.coalesce(func.sum(column), Decimal(0))).where(*conditions))
    return Decimal(total) if total is not None else Decimal(0)


class BudgetController:
    """Reserva, acerta e consulta custos; o estado vive todo na base."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        price_book: PriceBook,
        clock: Clock,
        reference_usd: Decimal,
        warn_percents: Sequence[int],
    ) -> None:
        self._session_factory = session_factory
        self._price_book = price_book
        self._clock = clock
        self._reference_usd = reference_usd
        self._warn_percents = tuple(warn_percents)

    def reserve(self, request: ReservationRequest, run_cost_limit_usd: Decimal) -> Reservation:
        """Reserva o custo maximo da chamada; recusa se a execucao passar `run_cost_limit_usd`."""
        price = self._price_book.get(request.provider, request.model_id)
        estimate = Usage(request.input_tokens_estimate, request.max_output_tokens)
        # Teto: a retencao nunca fica abaixo da estimativa exata.
        amount = _money(compute_cost_usd(price, estimate), ROUND_CEILING)
        with self._session_factory.begin() as session:
            self._lock_run(session, request)
            committed = self._run_committed_usd(session, request.run_id)
            if committed + amount > run_cost_limit_usd:
                raise RunCostLimitExceeded(
                    f"execucao {request.run_id}: limite {run_cost_limit_usd} USD, "
                    f"total com a nova reserva {committed + amount} USD"
                )
            row = CostReservation(
                id=new_id(),
                brand_id=request.brand_id,
                run_id=request.run_id,
                step_id=request.step_id,
                role=request.role,
                provider=request.provider,
                model_id=request.model_id,
                price_version=price.price_version,
                reserved_usd=amount,
                status="open",
                created_at=self._clock.now(),
            )
            session.add(row)
            return Reservation(row.id, amount)

    def settle(self, reservation_id: str, usage: Usage, provider_request_id: str | None) -> Decimal:
        """Lanca o custo medido e fecha a reserva; repetir devolve o mesmo custo."""
        with self._session_factory.begin() as session:
            row = self._lock_reservation(session, reservation_id)
            if row.status == "released":
                raise ValueError(f"reserva {reservation_id} ja libertada; nao pode ser acertada")
            if row.status == "settled":
                return self._entry_cost(session, reservation_id)
            price = self._price_book.get(row.provider, row.model_id)
            cost = _money(compute_cost_usd(price, usage), ROUND_HALF_UP)
            now = self._clock.now()
            session.add(
                CostEntry(
                    id=new_id(),
                    brand_id=row.brand_id,
                    reservation_id=row.id,
                    role=row.role,
                    run_id=row.run_id,
                    step_id=row.step_id,
                    provider=row.provider,
                    model_id=row.model_id,
                    provider_request_id=provider_request_id,
                    price_version=price.price_version,
                    tokens_in=usage.input_tokens,
                    tokens_out=usage.output_tokens,
                    tokens_reasoning=usage.reasoning_tokens,
                    units={},
                    cost_usd=cost,
                    kind="measured",
                    at=now,
                )
            )
            row.status = "settled"
            row.settled_at = now
            return cost

    def release(self, reservation_id: str) -> None:
        """Liberta uma reserva aberta (chamada nunca enviada); idempotente."""
        with self._session_factory.begin() as session:
            row = self._lock_reservation(session, reservation_id)
            if row.status == "released":
                return
            self._require_open(row, "libertada")
            row.status = "released"
            row.settled_at = self._clock.now()

    def mark_unknown(self, reservation_id: str) -> None:
        """Marca como desfecho desconhecido uma reserva aberta; idempotente."""
        with self._session_factory.begin() as session:
            row = self._lock_reservation(session, reservation_id)
            if row.status == "unknown_outcome":
                return
            self._require_open(row, "marcada como desconhecida")
            row.status = "unknown_outcome"

    def recover_stale(self, older_than: timedelta) -> int:
        """Passa a `unknown_outcome` as reservas abertas mais antigas que `older_than`."""
        cutoff = self._clock.now() - older_than
        stmt = (
            update(CostReservation)
            .where(CostReservation.status == "open", CostReservation.created_at < cutoff)
            .values(status="unknown_outcome")
            .returning(CostReservation.id)
        )
        with self._session_factory.begin() as session:
            return len(session.execute(stmt).all())

    def status(self, brand_id: str) -> BudgetStatus:
        """Gasto, reservas e projecao do mes civil UTC de `clock.now()`."""
        now = self._clock.now()
        start, end = _month_window(now)
        with self._session_factory() as session:
            spent = _sum_money(
                session,
                CostEntry.cost_usd,
                CostEntry.brand_id == brand_id,
                CostEntry.at >= start,
                CostEntry.at < end,
            )
            reserved = self._pending_usd(session, brand_id, "open", start, end)
            unknown = self._pending_usd(session, brand_id, "unknown_outcome", start, end)
        total = spent + reserved + unknown
        days = calendar.monthrange(now.year, now.month)[1]
        return BudgetStatus(
            spent_usd=spent,
            reserved_usd=reserved,
            unknown_usd=unknown,
            projected_usd=total * days / now.day,
            reference_usd=self._reference_usd,
            warn_percent=self._warn_percent(total),
            over_reference=total > self._reference_usd,
        )

    def _warn_percent(self, total: Decimal) -> int | None:
        # Compara sem dividir: limiar <= total / referencia * 100.
        reached = [
            p for p in self._warn_percents if Decimal(p) * self._reference_usd <= total * 100
        ]
        return max(reached, default=None)

    @staticmethod
    def _pending_usd(
        session: Session, brand_id: str, status: str, start: datetime, end: datetime
    ) -> Decimal:
        return _sum_money(
            session,
            CostReservation.reserved_usd,
            CostReservation.brand_id == brand_id,
            CostReservation.status == status,
            CostReservation.created_at >= start,
            CostReservation.created_at < end,
        )

    @staticmethod
    def _lock_run(session: Session, request: ReservationRequest) -> None:
        # Serializa reservas da mesma execucao: o 2o pedido so le a soma apos o commit do 1o.
        locked = session.scalar(
            select(AgentRun.id)
            .where(AgentRun.brand_id == request.brand_id, AgentRun.id == request.run_id)
            .with_for_update()
        )
        if locked is None:
            raise LookupError(f"execucao {request.run_id} da marca {request.brand_id} nao existe")

    @staticmethod
    def _run_committed_usd(session: Session, run_id: str) -> Decimal:
        pending = _sum_money(
            session,
            CostReservation.reserved_usd,
            CostReservation.run_id == run_id,
            CostReservation.status.in_(PENDING_STATUSES),
        )
        entries = _sum_money(session, CostEntry.cost_usd, CostEntry.run_id == run_id)
        return pending + entries

    @staticmethod
    def _lock_reservation(session: Session, reservation_id: str) -> CostReservation:
        row = session.scalar(
            select(CostReservation).where(CostReservation.id == reservation_id).with_for_update()
        )
        if row is None:
            raise ReservationNotFound(f"reserva {reservation_id} nao existe")
        return row

    @staticmethod
    def _require_open(row: CostReservation, action: str) -> None:
        if row.status != "open":
            raise ValueError(
                f"reserva {row.id} em estado {row.status}; so 'open' pode ser {action}"
            )

    @staticmethod
    def _entry_cost(session: Session, reservation_id: str) -> Decimal:
        cost = session.scalar(
            select(CostEntry.cost_usd).where(CostEntry.reservation_id == reservation_id)
        )
        if cost is None:
            raise LookupError(f"reserva {reservation_id} acertada sem lancamento de custo")
        return cost
