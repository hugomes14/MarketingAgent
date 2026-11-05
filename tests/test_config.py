import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from marketing_agent.config import Settings, get_settings


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
