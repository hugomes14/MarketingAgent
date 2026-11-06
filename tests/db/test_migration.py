"""A migracao inicial cria as tabelas do nucleo e coincide com os modelos."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from marketing_agent.db import models  # noqa: F401  (regista as tabelas em Base.metadata)
from marketing_agent.db.base import Base

CORE_TABLES = {
    "brand",
    "audit_log",
    "system_flag",
    "model_price",
    "cost_reservation",
    "cost_entry",
    "agent_run",
    "agent_step",
    "artifact",
    "artifact_head",
    "brand_profile",
    "strategy_decision",
    "interview_session",
    "interview_submission",
    "interview_turn",
}


def test_upgrade_head_creates_core_tables(engine: Engine) -> None:
    existing = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert existing == CORE_TABLES


def test_migration_matches_models(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
