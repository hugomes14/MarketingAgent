"""Fabrica `create_app_from_env`: configuracao do ambiente, segredos, marca e arranque."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.db.models import AgentRun, Brand, CostReservation
from marketing_agent.panel.main import create_app_from_env
from marketing_agent.startup import StartupError, director_max_duration_s

Factory = sessionmaker[Session]
LOGIN = "dono@local"
CSRF_SECRET = "s" * 48
KEY_SECRET = "chave-de-api-super-secreta"
DB_PASSWORD = "senha-da-base-xyz"
ROUTES = {
    "diretor": {
        "provider": "openai",
        "model_id": "qwen2.5:14b",
        "capabilities": ["text", "json_schema"],
    }
}


@pytest.fixture
def environment(
    monkeypatch: pytest.MonkeyPatch, pg_url: str, tmp_path: Path, session_factory: Factory
) -> Path:
    """Ambiente minimo de uma instalacao local (cwd vazio: nenhum `.env`)."""
    monkeypatch.chdir(tmp_path)
    key_file = tmp_path / "ollama_key"
    key_file.write_text(KEY_SECRET, encoding="utf-8")
    csrf_file = tmp_path / "csrf"
    csrf_file.write_text(CSRF_SECRET + "\n", encoding="utf-8")
    values = {
        "DATABASE_URL": pg_url,
        "ROLE_ROUTES": json.dumps(ROUTES),
        "OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
        "OPENAI_API_KEY_FILE": str(key_file),
        "PANEL_ALLOWED_LOGINS": json.dumps([LOGIN]),
        "PANEL_TRUSTED_PROXY": json.dumps(["127.0.0.1"]),
        "PANEL_CSRF_SECRET_FILE": str(csrf_file),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return tmp_path


def add_brand(factory: Factory, name: str = "Marca") -> str:
    with factory.begin() as session:
        brand = Brand(name=name)
        session.add(brand)
        session.flush()
        return brand.id


def error_text(exc_info: pytest.ExceptionInfo[StartupError]) -> str:
    return str(exc_info.value)


def test_app_answers_401_without_header_and_200_with_it(
    environment: Path, session_factory: Factory
) -> None:
    add_brand(session_factory)
    client = TestClient(create_app_from_env(), client=("127.0.0.1", 5000))
    assert client.get("/").status_code == 401
    assert client.get("/", headers={"Tailscale-User-Login": LOGIN}).status_code == 200
    assert client.get("/", headers={"Tailscale-User-Login": "outro@x"}).status_code == 403


def test_untrusted_peer_is_denied_even_with_forged_forwarding(
    environment: Path, session_factory: Factory
) -> None:
    add_brand(session_factory)
    client = TestClient(create_app_from_env(), client=("203.0.113.9", 5000))
    headers = {"Tailscale-User-Login": LOGIN, "X-Forwarded-For": "127.0.0.1"}
    assert client.get("/", headers=headers).status_code == 403


def test_missing_csrf_secret_file_names_the_variable(
    environment: Path, monkeypatch: pytest.MonkeyPatch, session_factory: Factory
) -> None:
    add_brand(session_factory)
    monkeypatch.delenv("PANEL_CSRF_SECRET_FILE")
    with pytest.raises(StartupError, match="PANEL_CSRF_SECRET_FILE") as exc_info:
        create_app_from_env()
    assert "Traceback" not in error_text(exc_info)


def test_unreadable_csrf_secret_file_names_the_variable(
    environment: Path, monkeypatch: pytest.MonkeyPatch, session_factory: Factory
) -> None:
    add_brand(session_factory)
    monkeypatch.setenv("PANEL_CSRF_SECRET_FILE", str(environment / "nao_existe"))
    with pytest.raises(StartupError, match="PANEL_CSRF_SECRET_FILE"):
        create_app_from_env()


def test_short_csrf_secret_is_refused_without_echoing_it(
    environment: Path, monkeypatch: pytest.MonkeyPatch, session_factory: Factory
) -> None:
    add_brand(session_factory)
    short = environment / "curto"
    short.write_text("curto-mas-secreto-1234567", encoding="utf-8")  # 25 caracteres
    monkeypatch.setenv("PANEL_CSRF_SECRET_FILE", str(short))
    with pytest.raises(StartupError, match="32") as exc_info:
        create_app_from_env()
    assert "curto-mas-secreto" not in error_text(exc_info)
    assert "PANEL_CSRF_SECRET_FILE" in error_text(exc_info)


def test_zero_brands_tells_how_to_create_one(environment: Path) -> None:
    with pytest.raises(StartupError, match="create-brand"):
        create_app_from_env()


def test_several_brands_are_refused(environment: Path, session_factory: Factory) -> None:
    add_brand(session_factory, "A")
    add_brand(session_factory, "B")
    with pytest.raises(StartupError, match="uma so marca"):
        create_app_from_env()


def test_fake_provider_route_is_rejected(
    environment: Path, monkeypatch: pytest.MonkeyPatch, session_factory: Factory
) -> None:
    add_brand(session_factory)
    fake_route = {"diretor": {**ROUTES["diretor"], "provider": "fake"}}
    monkeypatch.setenv("ROLE_ROUTES", json.dumps(fake_route))
    with pytest.raises(StartupError, match="fake") as exc_info:
        create_app_from_env()
    assert "ROLE_ROUTES" in error_text(exc_info)


def test_missing_provider_key_file_names_the_variable(
    environment: Path, monkeypatch: pytest.MonkeyPatch, session_factory: Factory
) -> None:
    add_brand(session_factory)
    monkeypatch.delenv("OPENAI_API_KEY_FILE")
    with pytest.raises(StartupError, match="OPENAI_API_KEY_FILE"):
        create_app_from_env()


def test_invalid_settings_name_variable_not_value(
    environment: Path, monkeypatch: pytest.MonkeyPatch, session_factory: Factory
) -> None:
    add_brand(session_factory)
    monkeypatch.setenv("ROLE_ROUTES", '{"diretor": {"provider": "segredo-xyz"}}')
    with pytest.raises(StartupError, match="ROLE_ROUTES") as exc_info:
        create_app_from_env()
    assert "segredo-xyz" not in error_text(exc_info)


def test_malformed_json_setting_names_variable(
    environment: Path, monkeypatch: pytest.MonkeyPatch, session_factory: Factory
) -> None:
    monkeypatch.setenv("PANEL_ALLOWED_LOGINS", "dono@local-sem-json")
    with pytest.raises(StartupError, match="PANEL_ALLOWED_LOGINS") as exc_info:
        create_app_from_env()
    assert "dono@local-sem-json" not in error_text(exc_info)


def test_empty_allowed_logins_fail_at_startup(
    environment: Path, monkeypatch: pytest.MonkeyPatch, session_factory: Factory
) -> None:
    add_brand(session_factory)
    monkeypatch.setenv("PANEL_ALLOWED_LOGINS", "[]")
    with pytest.raises(StartupError, match="PANEL_ALLOWED_LOGINS"):
        create_app_from_env()


def test_unreachable_database_does_not_leak_the_password(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"postgresql+psycopg://marketing_agent:{DB_PASSWORD}@127.0.0.1:1/x"
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(StartupError, match="base de dados") as exc_info:
        create_app_from_env()
    assert DB_PASSWORD not in error_text(exc_info)


def test_no_error_message_contains_secret_contents(
    environment: Path, monkeypatch: pytest.MonkeyPatch, session_factory: Factory
) -> None:
    monkeypatch.setenv("PANEL_ALLOWED_LOGINS", "[]")
    add_brand(session_factory)
    with pytest.raises(StartupError) as exc_info:
        create_app_from_env()
    assert KEY_SECRET not in error_text(exc_info) and CSRF_SECRET not in error_text(exc_info)


def test_startup_runs_recover_stale_with_largest_spec_duration(
    environment: Path, session_factory: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    brand_id = add_brand(session_factory)
    now = datetime.now(UTC)
    ids = {
        "old": seed_reservation(session_factory, brand_id, now - timedelta(seconds=241)),
        "recent": seed_reservation(session_factory, brand_id, now - timedelta(seconds=200)),
    }
    with caplog.at_level("INFO"):
        create_app_from_env()
    with session_factory() as session:
        assert session.get_one(CostReservation, ids["old"]).status == "unknown_outcome"
        assert session.get_one(CostReservation, ids["recent"]).status == "open"
    assert any("1 reserva" in record.getMessage() for record in caplog.records)
    assert KEY_SECRET not in caplog.text and CSRF_SECRET not in caplog.text


def seed_reservation(factory: Factory, brand_id: str, created_at: datetime) -> str:
    with factory.begin() as session:
        run = AgentRun(
            brand_id=brand_id,
            role="diretor",
            spec_version=1,
            trigger="test",
            status="running",
            started_at=created_at,
        )
        session.add(run)
        session.flush()
        reservation = CostReservation(
            brand_id=brand_id,
            run_id=run.id,
            role="diretor",
            provider="openai",
            model_id="qwen2.5:14b",
            price_version="v1",
            reserved_usd=Decimal("0.1"),
            status="open",
            created_at=created_at,
        )
        session.add(reservation)
        session.flush()
        return reservation.id


def test_director_duration_is_derived_from_specs() -> None:
    assert director_max_duration_s() == 240
