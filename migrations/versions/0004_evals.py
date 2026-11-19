"""Avaliacao cega do 2C: corridas, pacotes, respostas e decisoes por papel.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(12, 6)
PATHS = "'simples', 'especializado'"
ERROR_KINDS = "'facto_errado', 'alegacao_sem_apoio', 'voz_errada', 'imagem_inadequada', 'outro'"
EDIT_MINUTES = "'0', '1', '2_5', 'mais_5'"

# Respostas e decisoes so se acrescentam: uma correcao e uma linha nova (`supersedes_id`).
APPEND_ONLY_FUNCTION = """
CREATE FUNCTION eval_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '%: linhas imutaveis (corrigir com uma linha nova)', TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql
"""
APPEND_ONLY_TABLES = {
    "eval_judgement": "eval_judgement_immutable",
    "role_decision": "role_decision_immutable",
}


def upgrade() -> None:
    """Cria as quatro tabelas da avaliacao e os gatilhos de imutabilidade."""
    criar_corrida()
    criar_item()
    criar_resposta()
    criar_decisao()
    op.execute(APPEND_ONLY_FUNCTION)
    for tabela, gatilho in APPEND_ONLY_TABLES.items():
        op.execute(
            f"CREATE TRIGGER {gatilho} BEFORE UPDATE OR DELETE ON {tabela} "
            "FOR EACH ROW EXECUTE FUNCTION eval_append_only()"
        )


def criar_corrida() -> None:
    """Corrida com semente, briefs e contexto fixados; uma `running` por marca."""
    op.create_table(
        "eval_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("brand_id", sa.String(36), sa.ForeignKey("brand.id"), nullable=False),
        sa.Column("set_name", sa.Text, nullable=False),
        sa.Column("set_version", sa.Integer, nullable=False),
        sa.Column("seed", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("strategy_artifact_id", sa.String(36), nullable=False),
        sa.Column("briefs", JSONB, nullable=False),
        sa.Column("context_snapshot", JSONB, nullable=False),
        sa.Column("estimate_usd", MONEY, nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("revealed_at", TS),
        sa.UniqueConstraint("brand_id", "id"),
        sa.ForeignKeyConstraint(
            ["brand_id", "strategy_artifact_id"], ["artifact.brand_id", "artifact.id"]
        ),
        sa.CheckConstraint("status IN ('running', 'ready', 'revealed')", name="status"),
        sa.CheckConstraint("set_name ~ '^[a-z0-9_]{1,40}$'", name="set_name"),
        sa.CheckConstraint("set_version > 0", name="positive_set_version"),
        sa.CheckConstraint("estimate_usd >= 0", name="non_negative_estimate"),
    )
    op.create_index(
        "uq_eval_run_one_running_per_brand",
        "eval_run",
        ["brand_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def criar_item() -> None:
    """Pacote por brief e percurso, com as medidas derivadas das execucoes."""
    op.create_table(
        "eval_item",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("brand_id", sa.String(36), sa.ForeignKey("brand.id"), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("brief_key", sa.Text, nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("job_id", sa.String(36)),
        sa.Column("revision_snapshot", JSONB),
        sa.Column("final_asset_id", sa.String(36)),
        sa.Column("cost_usd", MONEY, nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("invalid_outputs", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("review_rounds", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("finished_at", TS),
        sa.UniqueConstraint("brand_id", "id"),
        sa.UniqueConstraint("run_id", "brief_key", "path"),
        sa.ForeignKeyConstraint(["brand_id", "run_id"], ["eval_run.brand_id", "eval_run.id"]),
        sa.ForeignKeyConstraint(
            ["brand_id", "final_asset_id"], ["media_asset.brand_id", "media_asset.id"]
        ),
        sa.CheckConstraint(f"path IN ({PATHS})", name="path"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="status"),
        sa.CheckConstraint(
            "cost_usd >= 0 AND invalid_outputs >= 0 AND review_rounds >= 0 "
            "AND (latency_ms IS NULL OR latency_ms >= 0)",
            name="non_negative_measures",
        ),
    )


def criar_resposta() -> None:
    """Respostas cegas imutaveis; so a primeira de cada par tem `supersedes_id` nulo."""
    op.create_table(
        "eval_judgement",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("brand_id", sa.String(36), sa.ForeignKey("brand.id"), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("brief_key", sa.Text, nullable=False),
        sa.Column("side_a_path", sa.Text, nullable=False),
        sa.Column("better", sa.Text, nullable=False),
        sa.Column("errors_a", ARRAY(sa.Text), nullable=False),
        sa.Column("errors_b", ARRAY(sa.Text), nullable=False),
        sa.Column("edit_a", sa.Text, nullable=False),
        sa.Column("edit_b", sa.Text, nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("seconds_spent", sa.Integer, nullable=False),
        sa.Column("decided_by", sa.Text, nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.Column("supersedes_id", sa.String(36)),
        sa.UniqueConstraint("brand_id", "id"),
        sa.UniqueConstraint("supersedes_id"),
        sa.ForeignKeyConstraint(["brand_id", "run_id"], ["eval_run.brand_id", "eval_run.id"]),
        sa.ForeignKeyConstraint(
            ["brand_id", "supersedes_id"], ["eval_judgement.brand_id", "eval_judgement.id"]
        ),
        sa.CheckConstraint(f"side_a_path IN ({PATHS})", name="side_a_path"),
        sa.CheckConstraint("better IN ('a', 'b', 'empate')", name="better"),
        sa.CheckConstraint(f"edit_a IN ({EDIT_MINUTES})", name="edit_a"),
        sa.CheckConstraint(f"edit_b IN ({EDIT_MINUTES})", name="edit_b"),
        sa.CheckConstraint(f"errors_a <@ ARRAY[{ERROR_KINDS}]::text[]", name="errors_a"),
        sa.CheckConstraint(f"errors_b <@ ARRAY[{ERROR_KINDS}]::text[]", name="errors_b"),
        sa.CheckConstraint("seconds_spent BETWEEN 0 AND 3600", name="seconds_spent"),
    )
    op.create_index(
        "uq_eval_judgement_first_answer",
        "eval_judgement",
        ["run_id", "brief_key"],
        unique=True,
        postgresql_where=sa.text("supersedes_id IS NULL"),
    )


def criar_decisao() -> None:
    """Decisao por papel (manter, simplificar, retirar) ligada a uma corrida."""
    op.create_table(
        "role_decision",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("brand_id", sa.String(36), sa.ForeignKey("brand.id"), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("justificacao", sa.Text, nullable=False),
        sa.Column("decided_by", sa.Text, nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["brand_id", "run_id"], ["eval_run.brand_id", "eval_run.id"]),
        sa.CheckConstraint("role IN ('copywriter', 'designer', 'revisor')", name="role"),
        sa.CheckConstraint("decision IN ('manter', 'simplificar', 'retirar')", name="decision"),
    )


def downgrade() -> None:
    """Remove gatilhos, funcao e tabelas; o esquema volta exatamente ao da 0003."""
    for tabela, gatilho in APPEND_ONLY_TABLES.items():
        op.execute(f"DROP TRIGGER {gatilho} ON {tabela}")
    op.execute("DROP FUNCTION eval_append_only()")
    for tabela in ("role_decision", "eval_judgement", "eval_item", "eval_run"):
        op.drop_table(tabela)
