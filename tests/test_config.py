import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from marketing_agent.config import Settings, get_settings

TEST_DATABASE_URL = "postgresql+psycopg://u:p@127.0.0.1:5433/db"


@pytest.fixture(autouse=True)
def database_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL e obrigatorio: os testes que constroem Settings definem-no."""
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MONTHLY_BUDGET_REFERENCE_USD", "BUDGET_WARN_PERCENTS", "ROLE_ROUTES"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.monthly_budget_reference_usd == Decimal("20")
    assert settings.budget_warn_percents == (50, 80, 100)


def test_role_routes_from_json(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {
        "diretor": {
            "provider": "fake",
            "model_id": "fake-1",
            "capabilities": ["text", "json_schema"],
        }
    }
    monkeypatch.setenv("ROLE_ROUTES", json.dumps(routes))
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.role_routes["diretor"].provider == "fake"
    assert settings.role_routes["diretor"].capabilities == frozenset({"text", "json_schema"})


def test_unknown_provider_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {"diretor": {"provider": "x", "model_id": "m", "capabilities": ["text"]}}
    monkeypatch.setenv("ROLE_ROUTES", json.dumps(routes))
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_get_settings_returns_settings() -> None:
    assert isinstance(get_settings(), Settings)


ROUTE_OK: dict[str, Any] = {"provider": "fake", "model_id": "fake-1", "capabilities": ["text"]}


def _first_error_loc(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> tuple[int | str, ...]:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # type: ignore[call-arg]
    return excinfo.value.errors()[0]["loc"]


@pytest.mark.parametrize("value", ["0", "-5"])
def test_budget_reference_must_be_positive(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    loc = _first_error_loc(monkeypatch, "MONTHLY_BUDGET_REFERENCE_USD", value)
    assert loc == ("monthly_budget_reference_usd",)


def test_max_message_chars_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _first_error_loc(monkeypatch, "MAX_MESSAGE_CHARS", "0") == ("max_message_chars",)


@pytest.mark.parametrize("value", ["[]", "[0]", "[101]", "[100,50,-1]"])
def test_warn_percents_must_be_1_to_100_and_non_empty(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    assert _first_error_loc(monkeypatch, "BUDGET_WARN_PERCENTS", value)[0] == "budget_warn_percents"


def test_empty_model_id_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {"diretor": {**ROUTE_OK, "model_id": ""}}
    loc = _first_error_loc(monkeypatch, "ROLE_ROUTES", json.dumps(routes))
    assert loc == ("role_routes", "diretor", "model_id")


def test_route_with_extra_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {"diretor": {**ROUTE_OK, "temperature": 0.2}}
    loc = _first_error_loc(monkeypatch, "ROLE_ROUTES", json.dumps(routes))
    assert loc == ("role_routes", "diretor", "temperature")


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert excinfo.value.errors()[0]["loc"] == ("database_url",)


def test_repr_hides_database_url() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url="postgresql://u:SECRETPW@h/db"
    )
    assert "SECRETPW" not in repr(settings)
    assert "SECRETPW" not in str(settings)


def _write_env(tmp_path: Path, extra_line: str) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL={TEST_DATABASE_URL}\n{extra_line}\n", encoding="utf-8")
    return env_file


def test_unknown_env_key_error_hides_value(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path, "ANTHROPIC_API_KEY=sk-ant-api03-SECRETVALUE0123456789")
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert "SECRETVALUE" not in str(excinfo.value)
    assert "SECRETVALUE" not in repr(excinfo.value)
    assert "input_value" not in str(excinfo.value)


def test_unknown_env_url_error_hides_password(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path, "TEST_DATABASE_URL=postgresql://u:pw-SECRET@h/db")
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert "pw-SECRET" not in str(excinfo.value)
