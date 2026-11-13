import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from marketing_agent.config import IMAGE_ROUTE, Settings, get_settings

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


def test_plan_limits_default_to_five_per_week_and_one_per_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PLAN_MAX_POSTS_PER_WEEK", raising=False)
    monkeypatch.delenv("PLAN_MAX_POSTS_PER_DAY", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (settings.plan_max_posts_per_week, settings.plan_max_posts_per_day) == (5, 1)


@pytest.mark.parametrize("name", ["PLAN_MAX_POSTS_PER_WEEK", "PLAN_MAX_POSTS_PER_DAY"])
def test_plan_limits_must_be_positive(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    assert _first_error_loc(monkeypatch, name, "0") == (name.lower(),)


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


@pytest.mark.parametrize(
    "name", ["ANTHROPIC_API_KEY_FILE", "OPENAI_API_KEY_FILE", "OPENAI_BASE_URL"]
)
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_optional_secret_settings_become_none(
    monkeypatch: pytest.MonkeyPatch, name: str, blank: str
) -> None:
    monkeypatch.setenv(name, blank)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert getattr(settings, name.lower()) is None


def test_non_blank_optional_secret_settings_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", "/run/a.key")
    monkeypatch.setenv("OPENAI_API_KEY_FILE", "/run/o.key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.anthropic_api_key_file == Path("/run/a.key")
    assert settings.openai_api_key_file == Path("/run/o.key")
    assert settings.openai_base_url == "http://127.0.0.1:11434/v1"


def test_panel_csrf_secret_file_defaults_to_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PANEL_CSRF_SECRET_FILE", raising=False)
    assert Settings(_env_file=None).panel_csrf_secret_file is None  # type: ignore[call-arg]


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_panel_csrf_secret_file_means_unset(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("PANEL_CSRF_SECRET_FILE", raw)
    assert Settings(_env_file=None).panel_csrf_secret_file is None  # type: ignore[call-arg]


def test_panel_csrf_secret_file_is_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANEL_CSRF_SECRET_FILE", "AgentDATA/secrets/panel_csrf_secret")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.panel_csrf_secret_file == Path("AgentDATA/secrets/panel_csrf_secret")


@pytest.mark.parametrize("name", [".env.example", ".env.local.example"])
def test_example_env_files_are_valid_settings(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")
    path = Path(__file__).resolve().parents[1] / name
    settings = Settings(_env_file=path)  # type: ignore[call-arg]
    assert settings.panel_csrf_secret_file == Path("AgentDATA/secrets/panel_csrf_secret")
    assert "diretor" in settings.role_routes
    assert (settings.plan_max_posts_per_week, settings.plan_max_posts_per_day) == (5, 1)


def test_local_example_uses_ollama_route_and_local_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL")
    path = Path(__file__).resolve().parents[1] / ".env.local.example"
    settings = Settings(_env_file=path)  # type: ignore[call-arg]
    route = settings.role_routes["diretor"]
    assert (route.provider, route.model_id) == ("openai", "qwen2.5:14b")
    assert settings.openai_base_url == "http://127.0.0.1:11434/v1"
    assert settings.model_prices_path == Path("config/model_prices.local.yaml")
    assert settings.panel_allowed_logins == ["dono@local"]
    assert ":5434/" in settings.database_url


# --- rota de imagem (`designer_imagem`) -----------------------------------------------------


def _routes_env(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]) -> None:
    monkeypatch.setenv("ROLE_ROUTES", json.dumps(routes))


def _route(provider: str, capabilities: list[str], model_id: str = "m") -> dict[str, Any]:
    return {"provider": provider, "model_id": model_id, "capabilities": capabilities}


def test_image_route_requires_openai_and_image_output(monkeypatch: pytest.MonkeyPatch) -> None:
    assert IMAGE_ROUTE == "designer_imagem"
    _routes_env(monkeypatch, {IMAGE_ROUTE: _route("openai", ["image_output"], "img-1")})
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.role_routes[IMAGE_ROUTE].capabilities == frozenset({"image_output"})
    bad_routes = [
        _route("anthropic", ["image_output"]),
        _route("fake", ["image_output"]),
        _route("openai", ["text"]),
        _route("openai", []),
    ]
    for bad in bad_routes:
        _routes_env(monkeypatch, {IMAGE_ROUTE: bad})
        with pytest.raises(ValidationError, match=IMAGE_ROUTE):
            Settings(_env_file=None)  # type: ignore[call-arg]


def test_no_other_route_may_declare_image_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _routes_env(monkeypatch, {"diretor": _route("openai", ["text", "image_output"])})
    with pytest.raises(ValidationError, match="image_output"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_image_settings_defaults_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.image_quality == "medium"
    assert settings.openai_images_api_key_file is None
    monkeypatch.setenv("OPENAI_IMAGES_API_KEY_FILE", "AgentDATA/secrets/openai_images_api_key")
    monkeypatch.setenv("IMAGE_QUALITY", "high")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.openai_images_api_key_file == Path("AgentDATA/secrets/openai_images_api_key")
    assert settings.image_quality == "high"
    monkeypatch.setenv("IMAGE_QUALITY", "ultra")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_blank_images_key_file_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_IMAGES_API_KEY_FILE", "  ")
    assert Settings(_env_file=None).openai_images_api_key_file is None  # type: ignore[call-arg]


def test_image_route_error_hides_input(monkeypatch: pytest.MonkeyPatch) -> None:
    _routes_env(monkeypatch, {IMAGE_ROUTE: _route("anthropic", ["image_output"], "modelo-secreto")})
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert "modelo-secreto" not in str(excinfo.value)


@pytest.mark.parametrize("value", [str(n) for n in range(1, 11)])
def test_time_multiplier_accepts_one_to_ten(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("LLM_TIME_MULTIPLIER", value)
    assert Settings(_env_file=None).llm_time_multiplier == int(value)  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["0", "11", "-1", "1.5", "true", "abc"])
def test_time_multiplier_rejects_values_outside_one_to_ten_or_not_integers(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    assert _first_error_loc(monkeypatch, "LLM_TIME_MULTIPLIER", value) == ("llm_time_multiplier",)


def test_time_multiplier_defaults_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_TIME_MULTIPLIER", raising=False)
    assert Settings(_env_file=None).llm_time_multiplier == 1  # type: ignore[call-arg]
