"""Testes do AgentSpec: carregamento do YAML, confinamento do prompt e permissoes."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from marketing_agent.artifacts.store import ArtifactType
from marketing_agent.harness import validators
from marketing_agent.harness.spec import (
    LoopType,
    PermissionDenied,
    SpecLoadError,
    assert_can_write,
    load_agent_spec,
)


class DummyOutput(BaseModel):
    """Esquema de saida minimo para os testes."""

    text: str


SCHEMAS: dict[str, type[BaseModel]] = {"DummyOutput": DummyOutput}

BASE_SPEC = """\
role: estrategista
name: Estrategista
version: 1
loop_type: structured
prompt_file: prompt.md
output_schema: DummyOutput
required_capabilities: [text, json_schema]
reads: [perfil_marca]
writes: [estrategia]
limits:
  max_steps: 5
  max_tokens: 8000
  max_cost_usd: "0.50"
  max_duration_s: 120
max_output_tokens: 2000
budget_share_hint: "0.25"
escalation: diretor
"""


def write_spec(tmp_path: Path, text: str = BASE_SPEC) -> Path:
    (tmp_path / "prompt.md").write_text("Ola, papel.", encoding="utf-8")
    path = tmp_path / "estrategista.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_valid_spec(tmp_path: Path) -> None:
    spec = load_agent_spec(write_spec(tmp_path), SCHEMAS)
    assert spec.role == "estrategista"
    assert spec.loop_type is LoopType.STRUCTURED
    assert spec.required_capabilities == frozenset({"text", "json_schema"})
    assert spec.writes == [ArtifactType.ESTRATEGIA]
    assert spec.limits.max_cost_usd == Decimal("0.50")
    assert spec.tools == []
    assert spec.load_prompt(tmp_path) == "Ola, papel."


def test_prompt_outside_dir_rejected(tmp_path: Path) -> None:
    pasta = tmp_path / "specs"
    pasta.mkdir()
    (tmp_path / "x.md").write_text("fora", encoding="utf-8")
    path = pasta / "a.yaml"
    path.write_text(BASE_SPEC.replace("prompt.md", "../x.md"), encoding="utf-8")
    with pytest.raises(SpecLoadError, match="a.yaml"):
        load_agent_spec(path, SCHEMAS)
    spec = load_agent_spec(write_spec(tmp_path), SCHEMAS)
    with pytest.raises(SpecLoadError):
        spec.load_prompt(pasta)


def test_unknown_schema_rejected(tmp_path: Path) -> None:
    with pytest.raises(SpecLoadError, match="output_schema"):
        load_agent_spec(write_spec(tmp_path), {})


def test_unknown_validator_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = BASE_SPEC + "validators: [check_x]\n"
    path = write_spec(tmp_path, text)
    with pytest.raises(SpecLoadError, match="check_x"):
        load_agent_spec(path, SCHEMAS)
    monkeypatch.setitem(validators.VALIDATORS, "check_x", lambda _out: [])
    assert load_agent_spec(path, SCHEMAS).validators == ["check_x"]


def test_tools_not_allowed_in_plan_01(tmp_path: Path) -> None:
    path = write_spec(tmp_path, BASE_SPEC + "tools: [web_search]\n")
    with pytest.raises(SpecLoadError, match="no tools in Plan 01"):
        load_agent_spec(path, SCHEMAS)


def test_assert_can_write_denies_unlisted_type(tmp_path: Path) -> None:
    spec = load_agent_spec(write_spec(tmp_path), SCHEMAS)
    assert_can_write(spec, ArtifactType.ESTRATEGIA)
    with pytest.raises(PermissionDenied, match="estrategista.*plano"):
        assert_can_write(spec, ArtifactType.PLANO)


def test_limits_must_be_positive(tmp_path: Path) -> None:
    path = write_spec(tmp_path, BASE_SPEC.replace("max_steps: 5", "max_steps: 0"))
    with pytest.raises(SpecLoadError, match="max_steps"):
        load_agent_spec(path, SCHEMAS)
