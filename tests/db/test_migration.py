"""A migracao inicial cria as tabelas do nucleo e coincide com os modelos."""

from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

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


MEDIA_TABLES = {"media_asset", "brand_kit"}


CONTENT_TABLES = {"kb_item", "post", "post_revision", "approval", "feedback_example", "job"}


def test_upgrade_head_creates_core_tables(engine: Engine) -> None:
    existing = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert existing >= CORE_TABLES


def test_upgrade_head_creates_content_tables(engine: Engine) -> None:
    existing = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert existing >= CORE_TABLES | CONTENT_TABLES


def test_migration_matches_models(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_upgrade_head_creates_media_tables(engine: Engine) -> None:
    existentes = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert existentes == CORE_TABLES | CONTENT_TABLES | MEDIA_TABLES


def test_downgrade_0003_restores_0002(
    engine: Engine, pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", pg_url)
    raiz = Path(__file__).resolve().parents[2]
    configuracao = Config(str(raiz / "alembic.ini"))
    configuracao.set_main_option("script_location", str(raiz / "migrations"))
    with engine.begin() as ligacao:
        tabelas = inspect(ligacao).get_table_names()
        nomes = ", ".join(f'"{nome}"' for nome in tabelas if nome != "alembic_version")
        ligacao.execute(text(f"TRUNCATE {nomes} CASCADE"))
    try:
        command.downgrade(configuracao, "0002")
        verificar_esquema_0002(engine)
        gravar_revisao_0002(engine)
    finally:
        command.upgrade(configuracao, "head")
    with engine.connect() as ligacao:
        revisao = ligacao.execute(
            text(
                "SELECT caption, final_asset_id, layout, layout_version, components, "
                "is_ai_generated, review_image_checked, image_cost_usd, package_cost_usd "
                "FROM post_revision WHERE id = 'revisao_0002'"
            )
        ).one()
        assert revisao == (
            "Texto existente",
            None,
            None,
            None,
            [],
            False,
            False,
            Decimal(0),
            Decimal(0),
        )


def verificar_esquema_0002(engine: Engine) -> None:
    """Confirma que a reversao remove imagem e preserva o contrato do 2A."""
    inspetor = inspect(engine)
    existentes = set(inspetor.get_table_names()) - {"alembic_version"}
    assert existentes == CORE_TABLES | CONTENT_TABLES
    colunas = {coluna["name"] for coluna in inspetor.get_columns("post_revision")}
    novas = {
        "final_asset_id",
        "layout",
        "layout_version",
        "components",
        "is_ai_generated",
        "review_image_checked",
        "image_cost_usd",
        "package_cost_usd",
    }
    assert colunas.isdisjoint(novas)
    with engine.connect() as ligacao:
        origem = ligacao.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_post_revision_ck_post_revision_origin'"
            )
        )
        assert "upload" not in origem
        gatilhos = ligacao.scalar(
            text("SELECT count(*) FROM pg_trigger WHERE tgname = 'post_revision_immutable'")
        )
        assert gatilhos == 1


def gravar_revisao_0002(engine: Engine) -> None:
    """Insere uma revisao do 2A para provar o backfill sem passar pelo ORM novo."""
    comandos = (
        "INSERT INTO brand (id, name) VALUES ('marca_0002', 'Marca anterior')",
        "INSERT INTO artifact (id, brand_id, type, schema_version, state, payload) "
        "VALUES ('artefacto_0002', 'marca_0002', 'plano', 1, 'approved', '{}')",
        "INSERT INTO post (id, brand_id, plan_artifact_id, strategy_artifact_id, slot_key, "
        "channels, scheduled_date, brief, lifecycle, review_rounds, created_at) "
        "VALUES ('post_0002', 'marca_0002', 'artefacto_0002', 'artefacto_0002', '2026-W46-1', "
        "ARRAY['instagram'], '2026-11-10', '{}', 'planned', 0, now())",
        "INSERT INTO post_revision (id, brand_id, post_id, rev_no, revision_hash, caption, "
        "hashtags, claims, review_violations, origin, created_at) "
        "VALUES ('revisao_0002', 'marca_0002', 'post_0002', 1, repeat('a', 64), "
        "'Texto existente', ARRAY[]::text[], '[]', ARRAY[]::text[], 'agente', now())",
    )
    with engine.begin() as ligacao:
        for comando in comandos:
            ligacao.execute(text(comando))
