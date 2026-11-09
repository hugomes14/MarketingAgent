"""Esquema do plano semanal, spec do plano e cenario do cliente falso."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from marketing_agent.agents.diretor import DIRETOR_DIR
from marketing_agent.artifacts.store import ArtifactType
from marketing_agent.harness.spec import AgentSpec, LoopType, load_agent_spec
from marketing_agent.llm import fake_scenarios
from marketing_agent.planning.schemas import PlanoSemanal, PlanSlot
from marketing_agent.planning.validation import PlanLimits, validate_plan

SLOT = {
    "slot_key": "2026-W46-1",
    "data": "2026-11-09",
    "canais": ["instagram"],
    "tema": "Pao do dia",
    "pilar": "Producao artesanal",
    "objetivo": "Gerar encomendas",
    "experiment_index": None,
    "kb_refs": [],
    "justificacao": "Mostra o forno.",
}


def make_slot(**overrides: object) -> PlanSlot:
    return PlanSlot.model_validate({**SLOT, **overrides})


def make_plan(**overrides: object) -> dict[str, object]:
    return {"semana": "2026-W46", "slots": [SLOT], "suposicoes": [], **overrides}


def test_valid_slot_and_plan_parse() -> None:
    plan = PlanoSemanal.model_validate(make_plan())
    assert plan.slots[0].canais == ["instagram"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"canais": []},
        {"canais": ["facebook", "instagram", "facebook"]},
        {"canais": ["facebook", "facebook"]},
        {"canais": ["tiktok"]},
        {"tema": ""},
        {"tema": "x" * 201},
        {"pilar": ""},
        {"pilar": "x" * 201},
        {"objetivo": ""},
        {"objetivo": "x" * 301},
        {"justificacao": ""},
        {"justificacao": "x" * 601},
        {"data": "nao-e-data"},
        {"extra": 1},
    ],
)
def test_slot_bounds_are_enforced(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_slot(**overrides)


def test_both_channels_are_accepted() -> None:
    assert make_slot(canais=["facebook", "instagram"]).canais == ["facebook", "instagram"]


@pytest.mark.parametrize("semana", ["2026-46", "2026-W4", "26-W46", "2026-W46x", "x2026-W46"])
def test_week_pattern_is_enforced(semana: str) -> None:
    with pytest.raises(ValidationError):
        PlanoSemanal.model_validate(make_plan(semana=semana))


@pytest.mark.parametrize("count", [0, 15])
def test_slot_count_bounds_are_enforced(count: int) -> None:
    with pytest.raises(ValidationError):
        PlanoSemanal.model_validate(make_plan(slots=[SLOT] * count))


def test_plan_models_are_frozen_and_forbid_extra_fields() -> None:
    plan = PlanoSemanal.model_validate(make_plan())
    with pytest.raises(ValidationError):
        plan.semana = "2026-W47"
    with pytest.raises(ValidationError):
        PlanoSemanal.model_validate(make_plan(extra=1))


def test_weekly_plan_scenario_is_valid() -> None:
    [response] = fake_scenarios.weekly_plan("2026-W46", slots=3)
    assert not isinstance(response, Exception)
    plan = PlanoSemanal.model_validate_json(response.text)
    violations = validate_plan(
        plan,
        week="2026-W46",
        limits=PlanLimits(max_per_week=5, max_per_day=1),
        experiment_count=1,  # a estrategia do cenario falso tem uma experiencia
        valid_kb_refs=set(),
    )
    assert violations == []
    assert len(plan.slots) == 3


@pytest.mark.parametrize("slots", [1, 5, 7])
def test_weekly_plan_scenario_respects_one_per_day(slots: int) -> None:
    [response] = fake_scenarios.weekly_plan("2026-W46", slots=slots)
    assert not isinstance(response, Exception)
    violations = validate_plan(
        PlanoSemanal.model_validate_json(response.text),
        week="2026-W46",
        limits=PlanLimits(max_per_week=7, max_per_day=1),
        experiment_count=1,
        valid_kb_refs=set(),
    )
    assert violations == []


def test_plan_spec_loads_with_ruled_values() -> None:
    spec: AgentSpec = load_agent_spec(
        DIRETOR_DIR / "spec_plano.yaml", {"PlanoSemanal": PlanoSemanal}
    )
    assert (spec.role, spec.name, spec.loop_type) == ("diretor", "plano", LoopType.STRUCTURED)
    assert spec.writes == [ArtifactType.PLANO]
    assert spec.reads == ["perfil_marca", "estrategia"]
    assert spec.required_capabilities == frozenset({"text", "json_schema"})
    assert spec.validators == []
    assert (spec.limits.max_steps, spec.limits.max_tokens) == (4, 60000)
    assert spec.limits.max_cost_usd == Decimal("1.00") and spec.limits.max_duration_s == 240
    assert spec.max_output_tokens == 4000 and spec.budget_share_hint == Decimal("0.15")
    assert spec.output_schema == "PlanoSemanal"
    prompt = spec.load_prompt(DIRETOR_DIR)
    for tag in ("perfil_marca", "estrategia", "propostas_aceites", "base_conhecimento"):
        assert f"<{tag}>" in prompt
