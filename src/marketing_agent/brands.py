"""Marca do sistema: criacao idempotente por nome e resolucao da marca unica do painel."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.db.models import Brand
from marketing_agent.startup import StartupError

MAX_NAME_CHARS = 200
# Chave do lock consultivo que serializa a criacao da marca (valor arbitrario, fixo).
BRAND_LOCK_KEY = 7_150_001


def normalize_brand_name(name: str) -> str:
    """Nome sem espacos nas pontas; recusa vazio ou acima de `MAX_NAME_CHARS`."""
    clean = name.strip()
    if not clean or len(clean) > MAX_NAME_CHARS:
        raise StartupError(f"o nome da marca deve ter entre 1 e {MAX_NAME_CHARS} caracteres")
    return clean


def ensure_brand(session_factory: sessionmaker[Session], name: str) -> tuple[str, bool]:
    """Devolve (id, criada): reutiliza a marca com o mesmo nome em vez de duplicar.

    A instalacao tem uma so marca: se ja existe outra com nome diferente levanta `StartupError`.
    O lock consultivo (de transacao, chave constante) serializa verificacao e insercao: sem ele,
    duas execucoes concorrentes em READ COMMITTED poderiam inserir ambas, e `brand.name` nao tem
    UNIQUE.
    """
    clean = normalize_brand_name(name)
    with session_factory.begin() as session:
        session.execute(select(func.pg_advisory_xact_lock(BRAND_LOCK_KEY)))
        existing = list(session.scalars(select(Brand).order_by(Brand.id)))
        for brand in existing:
            if brand.name == clean:
                return brand.id, False
        if existing:
            raise StartupError(f"instalacao de marca unica: ja existe a marca {existing[0].name!r}")
        brand = Brand(name=clean)
        session.add(brand)
        session.flush()
        return brand.id, True


def resolve_single_brand(session_factory: sessionmaker[Session]) -> str:
    """Id da unica marca da instalacao; sem marca ou com varias levanta `StartupError`."""
    with session_factory() as session:
        ids = list(session.scalars(select(Brand.id).order_by(Brand.id).limit(2)))
    if not ids:
        raise StartupError(
            "nenhuma marca criada: execute `marketing-agent create-brand NOME` antes de servir"
        )
    if len(ids) > 1:
        raise StartupError(
            "existe mais de uma marca: esta versao serve uma so marca por instalacao"
        )
    return ids[0]
