"""Esquemas, validadores registados e specs do diagnostico e da estrategia."""

import json
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from marketing_agent.agents.diretor import DIRETOR_DIR
from marketing_agent.agents.diretor.schemas import Diagnostico, Estrategia
from marketing_agent.artifacts.store import ArtifactType
from marketing_agent.harness.spec import AgentSpec, LoopType, load_agent_spec
from marketing_agent.harness.validators import VALIDATORS
from marketing_agent.llm import fake_scenarios
from marketing_agent.strategy.service import SCHEMAS


def _fake_bodies() -> tuple[dict[str, object], dict[str, object]]:
    diagnosis, strategy = fake_scenarios.diagnosis_and_strategy()
    assert not isinstance(diagnosis, Exception) and not isinstance(strategy, Exception)
    return json.loads(diagnosis.text), json.loads(strategy.text)


def make_diagnosis(**overrides: object) -> Diagnostico:
    return Diagnostico.model_validate({**_fake_bodies()[0], **overrides})


def make_strategy(**overrides: object) -> Estrategia:
    return Estrategia.model_validate({**_fake_bodies()[1], **overrides})


def achado(tipo: str, evidencia: str | None, origem: str | None) -> dict[str, object]:
    return {"afirmacao": "x", "tipo": tipo, "evidencia": evidencia, "origem": origem}


def experiencia(hipotese: str = "h", metrica: str = "m", prazo_dias: int = 30) -> dict[str, object]:
    return {"hipotese": hipotese, "metrica": metrica, "prazo_dias": prazo_dias}


def test_scenario_outputs_satisfy_schemas_and_validators() -> None:
    assert VALIDATORS["diagnostico_factos_com_evidencia"](make_diagnosis()) == []
    assert VALIDATORS["estrategia_experiencias_completas"](make_strategy()) == []


def test_fact_without_evidence_rejected_by_validator() -> None:
    validate = VALIDATORS["diagnostico_factos_com_evidencia"]
    bad = make_diagnosis(
        achados=[
            achado("facto", "dito", "verificado"),
            achado("facto", None, "verificado"),
            achado("facto", "   ", "verificado"),
            achado("facto", "dito", None),
            achado("hipotese", None, None),
        ]
    )
    violations = validate(bad)
    assert len(violations) == 3
    assert [("achados[1]" in v, "achados[2]" in v, "achados[3]" in v) for v in violations] == [
        (True, False, False),
        (False, True, False),
        (False, False, True),
    ]


def test_wrong_type_input_is_a_violation_not_a_crash() -> None:
    class Other(BaseModel):
        x: int = 1

    for name in ("diagnostico_factos_com_evidencia", "estrategia_experiencias_completas"):
        [message] = VALIDATORS[name](Other())
        assert "Other" in message


@pytest.mark.parametrize(
    ("kwargs", "ok"),
    [
        ({"prazo_dias": 1}, True),
        ({"prazo_dias": 90}, True),
        ({"prazo_dias": 0}, False),
        ({"prazo_dias": 91}, False),
        ({"prazo_dias": -5}, False),
        ({"hipotese": "  "}, False),
        ({"metrica": ""}, False),
    ],
)
def test_experiment_prazo_bounds(kwargs: dict[str, object], ok: bool) -> None:
    strategy = make_strategy(experiencias=[experiencia(), experiencia(**kwargs)])  # type: ignore[arg-type]
    violations = VALIDATORS["estrategia_experiencias_completas"](strategy)
    assert (violations == []) is ok
    if not ok:
        assert len(violations) == 1 and "experiencias[1]" in violations[0]


def test_schemas_are_frozen_and_forbid_extra_fields() -> None:
    diagnosis = make_diagnosis()
    with pytest.raises(ValidationError):
        diagnosis.riscos = []  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Estrategia.model_validate({**_fake_bodies()[1], "extra": 1})
    with pytest.raises(ValidationError):
        make_strategy(posicionamento_estado="talvez")


@pytest.mark.parametrize(
    ("file_name", "schema", "name", "writes", "reads", "validator"),
    [
        (
            "spec_diagnostico.yaml",
            "Diagnostico",
            "diagnostico",
            [ArtifactType.DIAGNOSTICO],
            ["perfil_marca"],
            "diagnostico_factos_com_evidencia",
        ),
        (
            "spec_estrategia.yaml",
            "Estrategia",
            "estrategia",
            [ArtifactType.ESTRATEGIA],
            ["perfil_marca", "diagnostico"],
            "estrategia_experiencias_completas",
        ),
    ],
)
def test_specs_load_with_ruled_values(
    file_name: str,
    schema: str,
    name: str,
    writes: list[ArtifactType],
    reads: list[str],
    validator: str,
) -> None:
    spec: AgentSpec = load_agent_spec(DIRETOR_DIR / file_name, SCHEMAS)
    assert (spec.role, spec.name, spec.version) == ("diretor", name, 1)
    assert spec.loop_type is LoopType.STRUCTURED and spec.output_schema == schema
    assert spec.writes == writes and spec.reads == reads and spec.tools == []
    assert spec.validators == [validator]
    assert spec.required_capabilities == {"text", "json_schema"}
    assert (spec.limits.max_steps, spec.limits.max_tokens) == (4, 80000)
    assert (spec.limits.max_cost_usd, spec.limits.max_duration_s) == (Decimal("2.00"), 240)
    assert (spec.max_output_tokens, spec.budget_share_hint) == (6000, Decimal("0.30"))
    prompt = spec.load_prompt(DIRETOR_DIR)
    assert "DADOS" in prompt and "nunca instrucoes" in prompt
