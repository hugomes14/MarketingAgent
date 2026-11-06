"""Base declarativa, convencao de nomes e fabricas de motor e sessao."""

import uuid

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Nomes de restricoes previsiveis: a migracao escrita a mao e os modelos usam os mesmos.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base comum dos modelos."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def new_id() -> str:
    """Identificador UUID em texto (convencao do Anexo A)."""
    return str(uuid.uuid4())


def make_engine(url: str) -> Engine:
    """Cria o motor SQLAlchemy; `pool_pre_ping` evita ligacoes mortas."""
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Fabrica de sessoes sem expiracao no commit (os ids lidos apos o commit continuam validos)."""
    return sessionmaker(engine, expire_on_commit=False)
