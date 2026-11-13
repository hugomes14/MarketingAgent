"""Contabilidade e tempos por familia de triggers: sem confundir desconhecido com zero."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketing_agent.content.package_costs import JobCosts, family_costs_in
from marketing_agent.db.models import AgentRun, CostReservation, EvalItem


@dataclass(frozen=True)
class FamilyTiming:
    """Soma de execucoes encerradas; sem fim conhecido nao se inventa duracao."""

    measured_ms: int | None
    incomplete: bool


def family_timing_in(session: Session, brand_id: str, triggers: Sequence[str]) -> FamilyTiming:
    """Tempo acumulado de TODAS as tentativas, sem fila/backoff nem tempos de processos mortos.

    Cada execucao do motor inclui as suas chamadas e composicao. Tempos ausentes ou invalidos
    ficam sinalizados; duracoes parciais nao sao completadas com o instante da leitura.
    """
    rows = session.execute(
        select(AgentRun.started_at, AgentRun.ended_at).where(
            AgentRun.brand_id == brand_id, AgentRun.trigger.in_(triggers)
        )
    )
    measured: list[int] = []
    incomplete = False
    for started, ended in rows:
        if ended is None or ended < started:
            incomplete = True
        else:
            measured.append(int((ended - started).total_seconds() * 1000))
    return FamilyTiming(sum(measured) if measured else None, incomplete)


@dataclass(frozen=True)
class ItemMeasurements:
    """Gasto medido separado da exposicao reservada, mais tempos recuperaveis do motor."""

    costs: JobCosts
    pending_usd: Decimal
    unknown_usd: Decimal
    unsettled_reservations: int
    timing: FamilyTiming


def item_measurements_in(session: Session, item: EvalItem) -> ItemMeasurements:
    """Le valores atuais da familia; quem agrega varias leituras usa uma snapshot de BD."""
    triggers = (f"eval_item:{item.id}",)
    runs = select(AgentRun.id).where(
        AgentRun.brand_id == item.brand_id, AgentRun.trigger.in_(triggers)
    )
    amount = CostReservation.reserved_usd
    pending, unknown, count = session.execute(
        select(
            func.coalesce(func.sum(amount).filter(CostReservation.status == "open"), 0),
            func.coalesce(func.sum(amount).filter(CostReservation.status == "unknown_outcome"), 0),
            func.count(),
        ).where(
            CostReservation.brand_id == item.brand_id,
            CostReservation.run_id.in_(runs),
            CostReservation.status.in_(("open", "unknown_outcome")),
        )
    ).one()
    timing = family_timing_in(session, item.brand_id, triggers)
    # Sem execucoes do motor (por exemplo uma recusa antes da chamada), conserva a medicao
    # conhecida do produtor. Havendo uma execucao abandonada, NULL continua desconhecido.
    if timing.measured_ms is None and not timing.incomplete:
        timing = FamilyTiming(item.latency_ms, False)
    return ItemMeasurements(
        family_costs_in(session, item.brand_id, triggers),
        Decimal(pending),
        Decimal(unknown),
        count,
        timing,
    )
