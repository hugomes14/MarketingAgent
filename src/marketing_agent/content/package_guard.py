"""Teto de custo por pacote, somado sobre TODAS as tentativas de um pedido (spec 2C, secao 3).

Os limites de cada spec valem por execucao. Um pacote corre ate 3 passagens de 3 papeis (ou uma
chamada, no percurso simples) e um job pode repetir depois de um worker morrer; sem um teto do
pacote, o custo total nao tem limite conhecido e a estimativa de uma corrida de avaliacao
(pacotes x teto) deixaria de ser um majorante. O gasto vem da base, nunca da memoria:

- os `cost_entry` das execucoes do pedido (acertados);
- as `cost_reservation` ainda `open` ou `unknown_outcome` dessas execucoes (o fornecedor pode
  ter faturado; uma reserva `settled` ja esta no lancamento e uma `released` nao faturou).

Antes de cada execucao, `cap` devolve o spec com `max_cost_usd = min(spec, teto - gasto)`: o
motor impoe esse limite por reserva, por isso a execucao nunca passa o que resta do teto.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.content.package_costs import TriggerScope, request_run_ids
from marketing_agent.db.models import CostEntry, CostReservation
from marketing_agent.harness.engine import RunResult, RunStatus
from marketing_agent.harness.run_errors import RunLimitExceeded
from marketing_agent.harness.spec import AgentSpec

DEFAULT_PACKAGE_MAX_COST_USD: Final = Decimal("1.50")
_UNSETTLED_STATUSES: Final = ("open", "unknown_outcome")
# Mensagem fixa: so numeros do codigo, nunca texto do modelo.
PACKAGE_COST_MESSAGE: Final = "teto de custo do pacote atingido"


class PackageCostExceeded(RunLimitExceeded):
    """Nada resta do teto do pacote: a execucao seguinte nem chega a ser criada."""


class PackageCostGuard:
    """Teto de custo de um pedido (`scope`, `request_id`), partilhado por todas as execucoes."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        brand_id: str,
        request_id: str,
        ceiling_usd: Decimal,
        scope: TriggerScope = "post",
    ) -> None:
        """`ceiling_usd` tem de ser positivo; `ValueError` caso contrario."""
        if ceiling_usd <= 0:
            raise ValueError("o teto do pacote tem de ser positivo")
        self._session_factory = session_factory
        self._brand_id = brand_id
        self._request_id = request_id
        self._scope: TriggerScope = scope
        self.ceiling_usd = ceiling_usd

    def spent_usd(self) -> Decimal:
        """Custo ja lancado mais o reservado por acertar, de todas as tentativas do pedido."""
        runs = request_run_ids(self._brand_id, self._request_id, self._scope)
        entries = select(func.coalesce(func.sum(CostEntry.cost_usd), Decimal(0))).where(
            CostEntry.brand_id == self._brand_id, CostEntry.run_id.in_(runs)
        )
        pending = select(func.coalesce(func.sum(CostReservation.reserved_usd), Decimal(0))).where(
            CostReservation.brand_id == self._brand_id,
            CostReservation.run_id.in_(runs),
            CostReservation.status.in_(_UNSETTLED_STATUSES),
        )
        with self._session_factory() as session:
            return Decimal(session.scalar(entries) or 0) + Decimal(session.scalar(pending) or 0)

    def cap(self, spec: AgentSpec) -> AgentSpec:
        """Spec com `max_cost_usd` limitado ao que resta; `PackageCostExceeded` se nada resta."""
        remaining = self.ceiling_usd - self.spent_usd()
        if remaining <= 0:
            raise PackageCostExceeded(PACKAGE_COST_MESSAGE)
        if remaining >= spec.limits.max_cost_usd:
            return spec
        limits = spec.limits.model_copy(update={"max_cost_usd": remaining})
        return spec.model_copy(update={"limits": limits})


def run_capped(
    guard: PackageCostGuard | None, spec: AgentSpec, run: Callable[[AgentSpec], RunResult]
) -> RunResult:
    """Corre `run` com o spec limitado pelo teto; sem teto restante devolve `BLOCKED_LIMIT`.

    O resultado sintetico nao tem execucao (`run_id` vazio) e leva `PackageCostExceeded` em
    `limit`: quem o recebe trata-o como qualquer outro limite (paragem com motivo fixo).
    """
    if guard is None:
        return run(spec)
    try:
        capped = guard.cap(spec)
    except PackageCostExceeded:
        return RunResult(
            RunStatus.BLOCKED_LIMIT, "", None, PACKAGE_COST_MESSAGE, PackageCostExceeded
        )
    return run(capped)
