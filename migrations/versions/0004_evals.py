"""Comparacao cega: corridas, pacotes, respostas imutaveis e decisoes por papel.

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
# Literais da migracao: congelam o contrato desta revisao, ao contrario das constantes de
# `models`, que evoluem. O teste `test_migration_matches_models` nao compara CHECKs, por isso
# os testes de `tests/db/test_eval_constraints.py` fixam os valores aceites.
EVAL_RUN_STATUSES = "'running', 'ready', 'revealed'"
EVAL_PATHS = "'simples', 'especializado'"
EVAL_ITEM_STATUSES = "'queued', 'running', 'succeeded', 'failed'"
EVAL_BETTER = "'a', 'b', 'empate'"
EVAL_ERROR_KINDS = (
    "'facto_errado', 'alegacao_sem_apoio', 'voz_errada', 'imagem_inadequada', 'outro'"
)
EVAL_ERROR_KINDS_COUNT = 5
EDIT_MINUTES = "'0', '1', '2_5', 'mais_5'"
DECIDABLE_ROLES = "'copywriter', 'designer', 'revisor'"
ROLE_DECISIONS = "'manter', 'simplificar', 'retirar'"
KEY_REGEX = "^[a-z0-9_]{1,40}$"

# Uma resposta nunca muda nem desaparece; corrigir e acrescentar uma linha com `supersedes_id`.
JUDGEMENT_IMMUTABLE_FUNCTION = """
CREATE FUNCTION eval_judgement_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'eval_judgement %: respostas sao imutaveis', OLD.id
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql
"""

# O registo das decisoes so se acrescenta.
DECISION_IMMUTABLE_FUNCTION = """
CREATE FUNCTION role_decision_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'role_decision %: decisoes so se acrescentam', OLD.id
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql
"""


def id_col() -> sa.Column[str]:
    return sa.Column("id", sa.String(36), primary_key=True)


def brand_col() -> sa.Column[str]:
    return sa.Column("brand_id", sa.String(36), sa.ForeignKey("brand.id"), nullable=False)


def upgrade() -> None:
    """Cria as quatro tabelas e os gatilhos que tornam respostas e decisoes definitivas."""
    _create_eval_run()
    _create_eval_item()
    _create_eval_judgement()
    _create_role_decision()


def _create_eval_run() -> None:
    op.create_table(
        "eval_run",
        id_col(),
        brand_col(),
        sa.Column("set_name", sa.Text, nullable=False),
        sa.Column("set_version", sa.Integer, nullable=False),
        sa.Column("seed", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("strategy_artifact_id", sa.String(36), nullable=False),
        sa.Column("briefs", JSONB, nullable=False),
        sa.Column("context_snapshot", JSONB, nullable=False),
        sa.Column("estimate_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("revealed_at", TS),
        sa.UniqueConstraint("brand_id", "id"),
        sa.ForeignKeyConstraint(
            ["brand_id", "strategy_artifact_id"], ["artifact.brand_id", "artifact.id"]
        ),
        sa.CheckConstraint(f"status IN ({EVAL_RUN_STATUSES})", name="status"),
        sa.CheckConstraint(f"set_name ~ '{KEY_REGEX}'", name="set_name"),
        sa.CheckConstraint("set_version > 0", name="positive_set_version"),
        sa.CheckConstraint("estimate_usd >= 0", name="non_negative_estimate"),
        sa.CheckConstraint(
            "(status = 'revealed') = (revealed_at IS NOT NULL)", name="revealed_at_matches_status"
        ),
    )
    op.create_index(
        "uq_eval_run_one_running_per_brand",
        "eval_run",
        ["brand_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def _create_eval_item() -> None:
    op.create_table(
        "eval_item",
        id_col(),
        brand_col(),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("brief_key", sa.Text, nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("revision_snapshot", JSONB),
        sa.Column("final_asset_id", sa.String(36)),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("invalid_outputs", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("review_rounds", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("job_id", sa.String(36)),
        sa.Column("last_error", sa.Text),
        sa.UniqueConstraint("run_id", "brief_key", "path"),
        sa.UniqueConstraint("job_id"),
        sa.ForeignKeyConstraint(["brand_id", "run_id"], ["eval_run.brand_id", "eval_run.id"]),
        sa.ForeignKeyConstraint(
            ["brand_id", "final_asset_id"], ["media_asset.brand_id", "media_asset.id"]
        ),
        sa.CheckConstraint(f"path IN ({EVAL_PATHS})", name="path"),
        sa.CheckConstraint(f"status IN ({EVAL_ITEM_STATUSES})", name="status"),
        sa.CheckConstraint(f"brief_key ~ '{KEY_REGEX}'", name="brief_key"),
        sa.CheckConstraint(
            "cost_usd >= 0 AND invalid_outputs >= 0 AND review_rounds >= 0 "
            "AND (latency_ms IS NULL OR latency_ms >= 0)",
            name="non_negative_measures",
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR revision_snapshot IS NOT NULL",
            name="succeeded_requires_snapshot",
        ),
    )


def _known_errors(column: str) -> str:
    """Todos os elementos conhecidos e no maximo um de cada tipo possivel."""
    return (
        f"{column} <@ ARRAY[{EVAL_ERROR_KINDS}]::text[] "
        f"AND cardinality({column}) <= {EVAL_ERROR_KINDS_COUNT}"
    )


def _create_eval_judgement() -> None:
    op.create_table(
        "eval_judgement",
        id_col(),
        brand_col(),
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
        sa.UniqueConstraint("supersedes_id"),
        sa.UniqueConstraint("run_id", "brief_key", "id"),
        sa.ForeignKeyConstraint(["brand_id", "run_id"], ["eval_run.brand_id", "eval_run.id"]),
        sa.ForeignKeyConstraint(
            ["run_id", "brief_key", "supersedes_id"],
            ["eval_judgement.run_id", "eval_judgement.brief_key", "eval_judgement.id"],
            name="fk_eval_judgement_supersedes",
        ),
        sa.CheckConstraint(f"side_a_path IN ({EVAL_PATHS})", name="side_a_path"),
        sa.CheckConstraint(f"better IN ({EVAL_BETTER})", name="better"),
        sa.CheckConstraint(f"edit_a IN ({EDIT_MINUTES})", name="edit_a"),
        sa.CheckConstraint(f"edit_b IN ({EDIT_MINUTES})", name="edit_b"),
        sa.CheckConstraint(_known_errors("errors_a"), name="errors_a_known_kinds"),
        sa.CheckConstraint(_known_errors("errors_b"), name="errors_b_known_kinds"),
        sa.CheckConstraint(f"brief_key ~ '{KEY_REGEX}'", name="brief_key"),
        sa.CheckConstraint("seconds_spent BETWEEN 0 AND 3600", name="seconds_spent_range"),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 2000", name="note_length"),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
    )
    op.create_index(
        "uq_eval_judgement_first_answer",
        "eval_judgement",
        ["run_id", "brief_key"],
        unique=True,
        postgresql_where=sa.text("supersedes_id IS NULL"),
    )
    op.execute(JUDGEMENT_IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER eval_judgement_immutable BEFORE UPDATE OR DELETE ON eval_judgement "
        "FOR EACH ROW EXECUTE FUNCTION eval_judgement_immutable()"
    )


def _create_role_decision() -> None:
    op.create_table(
        "role_decision",
        id_col(),
        brand_col(),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("justificacao", sa.Text, nullable=False),
        sa.Column("decided_by", sa.Text, nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["brand_id", "run_id"], ["eval_run.brand_id", "eval_run.id"]),
        sa.CheckConstraint(f"role IN ({DECIDABLE_ROLES})", name="role"),
        sa.CheckConstraint(f"decision IN ({ROLE_DECISIONS})", name="decision"),
        sa.CheckConstraint(
            "char_length(justificacao) BETWEEN 1 AND 2000", name="justificacao_length"
        ),
    )
    op.execute(DECISION_IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER role_decision_immutable BEFORE UPDATE OR DELETE ON role_decision "
        "FOR EACH ROW EXECUTE FUNCTION role_decision_immutable()"
    )


def downgrade() -> None:
    """Remove gatilhos, funcoes e tabelas por ordem de dependencia (descarta os dados)."""
    op.execute("DROP TRIGGER role_decision_immutable ON role_decision")
    op.execute("DROP TRIGGER eval_judgement_immutable ON eval_judgement")
    op.execute("DROP FUNCTION role_decision_immutable()")
    op.execute("DROP FUNCTION eval_judgement_immutable()")
    for tabela in ("role_decision", "eval_judgement", "eval_item", "eval_run"):
        op.drop_table(tabela)
