"""Fixtures partilhadas: base Postgres de teste, relogio fixo e marca criada."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from panel_fixtures import make_panel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import FixedClock
from marketing_agent.db.base import make_engine, make_session_factory
from marketing_agent.db.models import Brand

__all__ = ["make_panel"]  # fixture partilhada pelos testes do painel e dos ponta a ponta

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def pg_url() -> str:
    """URL da base de teste; falha (nao salta) quando em falta."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL em falta: arrancar compose.test.yaml")
    return url


def make_alembic_config() -> Config:
    """Configuracao do Alembic com caminho absoluto, independente do cwd."""
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return config


@pytest.fixture(scope="session")
def engine(pg_url: str) -> Iterator[Engine]:
    """Motor ligado a uma base recriada do zero e migrada ate `head`."""
    eng = make_engine(pg_url)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    # A migracao le Settings.database_url; os testes apontam-no a base de teste.
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_url
    try:
        command.upgrade(make_alembic_config(), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Fabrica de sessoes sobre a base migrada, com todas as tabelas vazias."""
    with engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT quote_ident(tablename) FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).scalars()
        names = ", ".join(tables)
        if names:
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield make_session_factory(engine)


@pytest.fixture
def clock() -> FixedClock:
    """Relogio fixo em 2026-11-05T12:00:00Z."""
    return FixedClock(datetime(2026, 11, 5, 12, 0, tzinfo=UTC))


@pytest.fixture
def brand_id(session_factory: sessionmaker[Session]) -> str:
    """Identificador de uma marca criada na base."""
    with session_factory.begin() as session:
        brand = Brand(name="Marca de teste")
        session.add(brand)
        session.flush()
        return brand.id
