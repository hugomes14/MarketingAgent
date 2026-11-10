"""Validacao em codigo do plano semanal: o que o esquema sozinho nao consegue garantir.

As violacoes voltam ao LLM como `extra_validator` do motor (contam como `invalid_output`),
por isso cada mensagem e curta, cita o slot e diz o valor esperado.
"""

import re
from collections import Counter
from collections.abc import Set
from dataclasses import dataclass
from datetime import date, timedelta

from marketing_agent.planning.schemas import WEEK_PATTERN, PlanoSemanal, PlanSlot

_WEEK_RE = re.compile(WEEK_PATTERN)


@dataclass(frozen=True)
class PlanLimits:
    """Tetos de publicacoes: por semana e por dia (cada slot conta como uma publicacao)."""

    max_per_week: int
    max_per_day: int


def week_range(week: str) -> tuple[date, date]:
    """Segunda e domingo da semana ISO `AAAA-Www`; `ValueError` se a semana nao existe."""
    if not _WEEK_RE.fullmatch(week):
        raise ValueError(f"semana {week!r} fora do formato AAAA-Www")
    try:
        monday = date.fromisocalendar(int(week[:4]), int(week[6:]), 1)
    except ValueError as exc:
        raise ValueError(f"semana {week!r} nao existe no calendario ISO") from exc
    return monday, monday + timedelta(days=6)


def validate_plan(
    plan: PlanoSemanal,
    *,
    week: str,
    limits: PlanLimits,
    experiment_count: int,
    valid_kb_refs: Set[str],
) -> list[str]:
    """Devolve as violacoes do plano (lista vazia = valido); nunca levanta por causa do plano.

    `valid_kb_refs` e a uniao dos ids da base que estao ativos e validos em TODOS os dias da
    semana pedida: o chamador exclui um item que nao seja valido em todas as datas, porque
    aqui so se testa a pertenca ao conjunto e nao a data de cada slot. Uma `week` invalida e
    erro do chamador e levanta `ValueError`.
    """
    first_day, last_day = week_range(week)
    violations = []
    if plan.semana != week:
        violations.append(f"semana deve ser {week!r}, recebeu {plan.semana!r}")
    violations += _check_counts(plan, limits)
    for index, slot in enumerate(plan.slots):
        violations += [
            *_check_slot_key(slot, week, index),
            *_check_date(slot, index, first_day, last_day),
            *_check_experiment(slot, index, experiment_count),
            *_check_kb_refs(slot, index, valid_kb_refs),
        ]
    return violations


def _check_counts(plan: PlanoSemanal, limits: PlanLimits) -> list[str]:
    violations = []
    if len(plan.slots) > limits.max_per_week:
        violations.append(
            f"{len(plan.slots)} slots excedem o maximo de {limits.max_per_week} por semana"
        )
    per_day = Counter(slot.data for slot in plan.slots)
    for day, count in sorted(per_day.items()):
        if count > limits.max_per_day:
            violations.append(
                f"{count} slots em {day.isoformat()} excedem o maximo de "
                f"{limits.max_per_day} por dia"
            )
    return violations


def _check_slot_key(slot: PlanSlot, week: str, index: int) -> list[str]:
    # A chave usa a semana PEDIDA: se o plano trouxe outra, a unica violacao de semana ja o diz.
    expected = f"{week}-{index + 1}"  # sequencia 1..N garante tambem a unicidade
    if slot.slot_key == expected:
        return []
    return [f"slots[{index}]: slot_key deve ser {expected!r}, recebeu {slot.slot_key!r}"]


def _check_date(slot: PlanSlot, index: int, first_day: date, last_day: date) -> list[str]:
    if first_day <= slot.data <= last_day:
        return []
    return [
        f"slots[{index}]: data {slot.data.isoformat()} fora da semana "
        f"({first_day.isoformat()} a {last_day.isoformat()})"
    ]


def _check_experiment(slot: PlanSlot, index: int, experiment_count: int) -> list[str]:
    chosen = slot.experiment_index
    if chosen is None or 0 <= chosen < experiment_count:
        return []
    if experiment_count == 0:
        return [f"slots[{index}]: experiment_index {chosen} invalido: sem experiencias"]
    return [f"slots[{index}]: experiment_index {chosen} fora de 0..{experiment_count - 1}"]


def _check_kb_refs(slot: PlanSlot, index: int, valid_kb_refs: Set[str]) -> list[str]:
    """Cita a posicao do ref invalido, nunca o valor: o texto vem do modelo."""
    return [
        f"slots[{index}].kb_refs[{position}] desconhecido ou sem validade na semana"
        for position, ref in enumerate(slot.kb_refs)
        if ref not in valid_kb_refs
    ]
