"""Contrato das personagens da oficina e dos tempos reais de cada papel."""

from pathlib import Path

import pytest
import yaml

from marketing_agent.config import Settings
from marketing_agent.oficina.config import OficinaConfigError, load_oficina_config
from marketing_agent.oficina.papeis import PAPEIS
from marketing_agent.startup import director_max_duration_s, role_max_durations

REPO = Path(__file__).resolve().parents[2]


def document():
    return yaml.safe_load((REPO / "config/oficina.yaml").read_text())


def write(tmp_path, data):
    path = tmp_path / "oficina.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_repo_oficina_yaml_is_valid():
    config = load_oficina_config(REPO / "config/oficina.yaml")
    assert config.versao == 1 and set(config.personagens) == set(PAPEIS)
    assert len({p.roupa for p in config.personagens.values()}) == 4


@pytest.mark.parametrize("change", ["missing", "unknown"])
def test_exact_roles_required(tmp_path, change):
    data = document()
    data["personagens"]["pesquisador" if change == "unknown" else "revisor"] = data["personagens"][
        "diretor"
    ]
    if change == "missing":
        del data["personagens"]["revisor"]
    with pytest.raises(OficinaConfigError):
        load_oficina_config(write(tmp_path, data))


@pytest.mark.parametrize("color", ["#fff", "red", "#12345g", "#1234567", 42])
def test_bad_color_rejected_and_error_names_file_and_field(tmp_path, color):
    data = document()
    data["personagens"]["revisor"]["cabelo"] = color
    with pytest.raises(OficinaConfigError) as caught:
        load_oficina_config(write(tmp_path, data))
    assert "oficina.yaml" in str(caught.value) and "personagens.revisor.cabelo" in str(caught.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("estilo", "casco"),
        ("estilo", "inexistente"),
        ("extra", "segredo"),
        ("nome", ""),
        ("nome", "x" * 41),
        ("nome", "Ana\nSilva"),
        ("nome", "Ana\x00"),
        ("nome", "\tAna"),
        ("nome", "Ana\u202e"),
    ],
)
def test_bad_style_extra_key_and_control_characters(tmp_path, field, value):
    data = document()
    data["personagens"]["revisor"][field] = value
    with pytest.raises(OficinaConfigError):
        load_oficina_config(write(tmp_path, data))


def test_names_trim_spaces_but_html_remains_data(tmp_path):
    data = document()
    data["personagens"]["diretor"]["nome"] = "  <b>Ana</b>  "
    config = load_oficina_config(write(tmp_path, data))
    assert config.personagens["diretor"].nome == "<b>Ana</b>"


def test_bad_yaml_missing_file_and_version_rejected(tmp_path):
    with pytest.raises(OficinaConfigError, match="oficina.yaml"):
        load_oficina_config(tmp_path / "oficina.yaml")
    path = tmp_path / "oficina.yaml"
    for source in ("[bad", "42", "versao: 2\npersonagens: {}", "!!python/object:os.system {}"):
        path.write_text(source)
        with pytest.raises(OficinaConfigError):
            load_oficina_config(path)


def test_role_max_durations_reads_each_role_specs():
    from marketing_agent.agents.diretor import DIRETOR_DIR
    from marketing_agent.content.specs import load_content_specs
    from marketing_agent.harness.spec import load_agent_spec
    from marketing_agent.startup import _DIRECTOR_SCHEMAS

    specs = load_content_specs()
    expected = {
        "diretor": max(
            load_agent_spec(p, _DIRECTOR_SCHEMAS).limits.max_duration_s
            for p in DIRETOR_DIR.glob("spec_*.yaml")
        ),
        "copywriter": specs.copywriter.limits.max_duration_s,
        "designer": specs.designer.limits.max_duration_s,
        "revisor": specs.reviewer.limits.max_duration_s,
    }
    assert role_max_durations() == expected
    assert director_max_duration_s() == expected["diretor"]


def test_oficina_config_path_from_settings(monkeypatch):
    monkeypatch.setenv("OFICINA_CONFIG_FILE", "/tmp/test-oficina.yaml")
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost/test")
    assert settings.oficina_config_file == Path("/tmp/test-oficina.yaml")
