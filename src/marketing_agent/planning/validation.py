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
    if not _WEEK_RE.match(week):
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
        problems = [
            *_check_slot_key(slot, plan.semana, index),
            *_check_date(slot, first_day, last_day),
            *_check_experiment(slot, experiment_count),
            *_check_kb_refs(slot, valid_kb_refs),
        ]
        violations += [f"slots[{index}]: {problem}" for problem in problems]
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


def _check_slot_key(slot: PlanSlot, semana: str, index: int) -> list[str]:
    expected = f"{semana}-{index + 1}"  # sequencia 1..N garante tambem a unicidade
    if slot.slot_key == expected:
        return []
    return [f"slot_key deve ser {expected!r}, recebeu {slot.slot_key!r}"]


def _check_date(slot: PlanSlot, first_day: date, last_day: date) -> list[str]:
    if first_day <= slot.data <= last_day:
        return []
    return [
        f"data {slot.data.isoformat()} fora da semana "
        f"({first_day.isoformat()} a {last_day.isoformat()})"
    ]


def _check_experiment(slot: PlanSlot, experiment_count: int) -> list[str]:
    index = slot.experiment_index
    if index is None or 0 <= index < experiment_count:
        return []
    if experiment_count == 0:
        return [f"experiment_index {index} invalido: a estrategia nao tem experiencias"]
    return [f"experiment_index {index} fora de 0..{experiment_count - 1}"]


def _check_kb_refs(slot: PlanSlot, valid_kb_refs: Set[str]) -> list[str]:
    unknown = [ref for ref in slot.kb_refs if ref not in valid_kb_refs]
    if not unknown:
        return []
    return [f"kb_refs inexistentes ou sem validade na semana: {unknown}"]
