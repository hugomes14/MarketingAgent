"""Validacao em codigo do plano semanal: semana ISO, chaves, tetos, experiencias e base."""

from datetime import date

import pytest

from marketing_agent.planning.schemas import PlanoSemanal
from marketing_agent.planning.validation import PlanLimits, validate_plan, week_range

WEEK = "2026-W46"  # segunda 2026-11-09, domingo 2026-11-15
LIMITS = PlanLimits(max_per_week=5, max_per_day=1)


def slot(n: int, day: str, **overrides: object) -> dict[str, object]:
    return {
        "slot_key": f"{WEEK}-{n}",
        "data": day,
        "canais": ["instagram"],
        "tema": f"Tema {n}",
        "pilar": "Producao artesanal",
        "objetivo": "Gerar encomendas",
        "experiment_index": None,
        "kb_refs": [],
        "justificacao": "Porque sim.",
        **overrides,
    }


def make_plan(*slots: dict[str, object], semana: str = WEEK) -> PlanoSemanal:
    return PlanoSemanal.model_validate({"semana": semana, "slots": list(slots), "suposicoes": []})


def check(
    plan: PlanoSemanal,
    *,
    limits: PlanLimits = LIMITS,
    experiment_count: int = 1,
    valid_kb_refs: frozenset[str] = frozenset(),
) -> list[str]:
    return validate_plan(
        plan,
        week=WEEK,
        limits=limits,
        experiment_count=experiment_count,
        valid_kb_refs=valid_kb_refs,
    )


def test_week_range_is_monday_to_sunday() -> None:
    assert week_range(WEEK) == (date(2026, 11, 9), date(2026, 11, 15))
    assert week_range("2026-W53") == (date(2026, 12, 28), date(2027, 1, 3))


@pytest.mark.parametrize("week", ["2026-W00", "2026-W54", "2027-W53", "2026-46", "abc"])
def test_week_range_rejects_non_iso_weeks(week: str) -> None:
    with pytest.raises(ValueError, match="semana"):
        week_range(week)


def test_valid_plan_has_no_violations() -> None:
    plan = make_plan(
        slot(1, "2026-11-09", experiment_index=0, kb_refs=["kb-1"]),
        slot(2, "2026-11-15", canais=["facebook", "instagram"]),
    )
    assert check(plan, valid_kb_refs=frozenset({"kb-1"})) == []


def test_requested_week_must_match() -> None:
    plan = make_plan(slot(1, "2026-11-16", slot_key="2026-W47-1"), semana="2026-W47")
    violations = check(plan)
    assert any("semana" in v and "2026-W46" in v for v in violations)


def test_slot_keys_must_follow_week_and_sequence() -> None:
    plan = make_plan(
        slot(1, "2026-11-09"),
        slot(3, "2026-11-10"),  # fora de ordem
        slot(2, "2026-11-11"),
    )
    violations = check(plan)
    assert len(violations) == 2
    assert "slots[1]" in violations[0] and "2026-W46-2" in violations[0]
    assert "slots[2]" in violations[1] and "2026-W46-3" in violations[1]


def test_duplicate_slot_key_rejected() -> None:
    plan = make_plan(slot(1, "2026-11-09"), slot(1, "2026-11-10"))
    [violation] = check(plan)
    assert "slots[1]" in violation and "2026-W46-2" in violation


def test_slot_key_of_another_week_rejected() -> None:
    plan = make_plan(slot(1, "2026-11-09", slot_key="2026-W45-1"))
    [violation] = check(plan)
    assert "slots[0]" in violation and "2026-W46-1" in violation


@pytest.mark.parametrize("day", ["2026-11-08", "2026-11-16", "2025-11-10"])
def test_dates_outside_week_rejected(day: str) -> None:
    [violation] = check(make_plan(slot(1, day)))
    assert "slots[0]" in violation and day in violation and "2026-11-09" in violation


def test_week_and_day_limits() -> None:
    plan = make_plan(
        slot(1, "2026-11-09"),
        slot(2, "2026-11-09"),
        slot(3, "2026-11-10"),
    )
    violations = check(plan, limits=PlanLimits(max_per_week=2, max_per_day=1))
    assert len(violations) == 2
    assert any("3 slots" in v and "2 por semana" in v for v in violations)
    assert any("2026-11-09" in v and "2 slots" in v and "1 por dia" in v for v in violations)


def test_limits_at_exact_boundary_are_allowed() -> None:
    plan = make_plan(slot(1, "2026-11-09"), slot(2, "2026-11-09"), slot(3, "2026-11-10"))
    assert check(plan, limits=PlanLimits(max_per_week=3, max_per_day=2)) == []


@pytest.mark.parametrize("index", [1, 5, -1])
def test_unknown_experiment_index_rejected(index: int) -> None:
    [violation] = check(make_plan(slot(1, "2026-11-09", experiment_index=index)))
    assert "slots[0]" in violation and "experiment_index" in violation


def test_experiment_index_without_experiments_rejected() -> None:
    [violation] = check(make_plan(slot(1, "2026-11-09", experiment_index=0)), experiment_count=0)
    assert "experiment_index" in violation


def test_unknown_kb_ref_rejected() -> None:
    plan = make_plan(slot(1, "2026-11-09", kb_refs=["kb-1", "kb-x"]))
    [violation] = check(plan, valid_kb_refs=frozenset({"kb-1"}))
    assert "slots[0]" in violation and "kb-x" in violation and "kb-1" not in violation


def test_violations_are_collected_not_short_circuited() -> None:
    plan = make_plan(slot(2, "2026-11-20", experiment_index=7, kb_refs=["kb-x"]))
    assert len(check(plan)) == 4
