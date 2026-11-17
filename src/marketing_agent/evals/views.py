"""Leituras agregadas para o painel: nunca incluem percurso, item nem ordem de conclusao."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.db.models import EvalItem, EvalRun


class EvalNotFound(Exception):
    """A corrida ou o par nao existe nesta marca."""


def require_run(session: Session, brand_id: str, run_id: str, *, lock: bool = False) -> EvalRun:
    """Le a corrida da marca; opcionalmente serializa mutacoes de julgamento e revelacao."""
    query = select(EvalRun).where(EvalRun.id == run_id, EvalRun.brand_id == brand_id)
    if lock:
        query = query.with_for_update()
    run = session.scalar(query)
    if run is None:
        raise EvalNotFound
    return run


@dataclass(frozen=True)
class RunProgress:
    """Dados seguros para a pagina e para o fragmento de progresso anterior a revelacao."""

    id: str
    set_name: str
    set_version: int
    status: str
    created_at: datetime
    total: int
    finished: int
    failed: int
    cost_usd: Decimal


class EvalViews:
    """Leituras de corridas limitadas a marca autenticada."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        """Guarda a fabrica de sessoes; cada leitura devolve valores sem entidades ORM."""
        self._factory = factory

    def list_runs(self, brand_id: str) -> list[RunProgress]:
        """Corridas mais recentes primeiro, sem medidas individuais dos percursos."""
        with self._factory() as session:
            runs = session.scalars(
                select(EvalRun)
                .where(EvalRun.brand_id == brand_id)
                .order_by(EvalRun.created_at.desc())
            )
            return [self._progress(session, run) for run in runs]

    def progress(self, brand_id: str, run_id: str) -> RunProgress:
        """Contagens e custo total dos itens, sem revelar a sua identidade."""
        with self._factory() as session:
            return self._progress(session, require_run(session, brand_id, run_id))

    @staticmethod
    def _progress(session: Session, run: EvalRun) -> RunProgress:
        total, finished, failed, cost = session.execute(
            select(
                func.count(),
                func.count().filter(EvalItem.status.in_(("succeeded", "failed"))),
                func.count().filter(EvalItem.status == "failed"),
                func.coalesce(func.sum(EvalItem.cost_usd), 0),
            ).where(EvalItem.run_id == run.id)
        ).one()
        return RunProgress(
            run.id,
            run.set_name,
            run.set_version,
            run.status,
            run.created_at,
            total,
            finished,
            failed,
            cost,
        )
