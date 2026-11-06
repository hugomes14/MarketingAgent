"""Ambiente do Alembic: o URL vem de `Settings.database_url`."""

from alembic import context
from sqlalchemy import create_engine

from marketing_agent.config import get_settings
from marketing_agent.db import models  # noqa: F401  (regista as tabelas em Base.metadata)
from marketing_agent.db.base import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Gera SQL sem ligar a base."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Aplica as migracoes numa ligacao real, numa transacao."""
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
