"""Base de conhecimento, posts, revisoes, aprovacoes, feedback e fila de jobs.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
KB_KINDS = "'oferta', 'preco', 'horario', 'contacto', 'prova', 'politica', 'outro'"
SOURCE_KINDS = "'dito_pelo_responsavel', 'verificado', 'suposto'"
POST_LIFECYCLES = "'planned', 'drafting', 'in_review', 'approved', 'rejected', 'cancelled'"
REVIEW_VERDICTS = "'aprovar', 'rever', 'rejeitar'"
REVISION_ORIGINS = "'agente', 'edicao_responsavel'"
APPROVAL_DECISIONS = "'approved', 'rejected', 'changes_requested'"
FEEDBACK_KINDS = "'estilo', 'correcao_factual', 'temporario', 'negativo'"
JOB_STATUSES = "'queued', 'running', 'succeeded', 'failed', 'dead', 'cancelled'"

# Uma revisao nunca muda depois de criada; uma correcao e uma revisao nova.
IMMUTABLE_FUNCTION = """
CREATE FUNCTION post_revision_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'post_revision %: revisoes sao imutaveis', OLD.id
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql
"""


def id_col() -> sa.Column[str]:
    return sa.Column("id", sa.String(36), nullable=False)


def upgrade() -> None:
    _create_kb_item()
    _create_post()
    _create_post_revision()
    _create_review_tables()
    _create_job()


def _create_kb_item() -> None:
    op.create_table(
        "kb_item",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("price_amount", sa.Numeric(12, 2)),
        sa.Column("price_currency", sa.Text),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_until", sa.Date),
        sa.Column("source_kind", sa.Text, nullable=False),
        sa.Column("source_ref", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint(f"kind IN ({KB_KINDS})", name="ck_kb_item_kind"),
        sa.CheckConstraint(f"source_kind IN ({SOURCE_KINDS})", name="ck_kb_item_source_kind"),
        sa.CheckConstraint(
            "kind <> 'preco' OR (price_amount IS NOT NULL "
            "AND price_currency IS NOT NULL AND valid_until IS NOT NULL)",
            name="ck_kb_item_price_requires_amount_currency_validity",
        ),
        sa.ForeignKeyConstraint(["brand_id"], ["brand.id"], name="fk_kb_item_brand_id_brand"),
        sa.PrimaryKeyConstraint("id", name="pk_kb_item"),
        sa.UniqueConstraint(
            "brand_id", "kind", "key", "valid_from", name="uq_kb_item_brand_id_kind_key_valid_from"
        ),
    )


def _create_post() -> None:
    op.create_table(
        "post",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("plan_artifact_id", sa.String(36), nullable=False),
        sa.Column("slot_key", sa.Text, nullable=False),
        sa.Column("strategy_artifact_id", sa.String(36), nullable=False),
        sa.Column("experiment_index", sa.Integer),
        sa.Column("channels", ARRAY(sa.Text), nullable=False),
        sa.Column("scheduled_date", sa.Date, nullable=False),
        sa.Column("brief", JSONB, nullable=False),
        sa.Column("lifecycle", sa.Text, nullable=False),
        sa.Column("review_rounds", sa.Integer, nullable=False),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint(f"lifecycle IN ({POST_LIFECYCLES})", name="ck_post_lifecycle"),
        sa.CheckConstraint(
            "cardinality(channels) >= 1 AND channels <@ ARRAY['facebook', 'instagram']::text[]",
            name="ck_post_channels",
        ),
        sa.CheckConstraint("review_rounds >= 0", name="ck_post_non_negative_review_rounds"),
        sa.ForeignKeyConstraint(["brand_id"], ["brand.id"], name="fk_post_brand_id_brand"),
        sa.ForeignKeyConstraint(
            ["brand_id", "plan_artifact_id"],
            ["artifact.brand_id", "artifact.id"],
            name="fk_post_brand_id_plan_artifact_id_artifact",
        ),
        sa.ForeignKeyConstraint(
            ["brand_id", "strategy_artifact_id"],
            ["artifact.brand_id", "artifact.id"],
            name="fk_post_brand_id_strategy_artifact_id_artifact",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_post"),
        sa.UniqueConstraint("brand_id", "id", name="uq_post_brand_id_id"),
        sa.UniqueConstraint("brand_id", "slot_key", name="uq_post_brand_id_slot_key"),
    )


def _create_post_revision() -> None:
    op.create_table(
        "post_revision",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("post_id", sa.String(36), nullable=False),
        sa.Column("rev_no", sa.Integer, nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("caption", sa.Text, nullable=False),
        sa.Column("hashtags", ARRAY(sa.Text), nullable=False),
        sa.Column("call_to_action", sa.Text),
        sa.Column("alt_text", sa.Text),
        sa.Column("claims", JSONB, nullable=False),
        sa.Column("review_verdict", sa.Text),
        sa.Column("review_violations", ARRAY(sa.Text), nullable=False),
        sa.Column("review_notes", sa.Text),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("produced_by_run_id", sa.String(36)),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint(
            f"review_verdict IN ({REVIEW_VERDICTS})", name="ck_post_revision_review_verdict"
        ),
        sa.CheckConstraint(f"origin IN ({REVISION_ORIGINS})", name="ck_post_revision_origin"),
        sa.ForeignKeyConstraint(["brand_id"], ["brand.id"], name="fk_post_revision_brand_id_brand"),
        sa.ForeignKeyConstraint(
            ["brand_id", "post_id"],
            ["post.brand_id", "post.id"],
            name="fk_post_revision_brand_id_post_id_post",
        ),
        sa.ForeignKeyConstraint(
            ["brand_id", "produced_by_run_id"],
            ["agent_run.brand_id", "agent_run.id"],
            name="fk_post_revision_brand_id_produced_by_run_id_agent_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_post_revision"),
        sa.UniqueConstraint("post_id", "rev_no", name="uq_post_revision_post_id_rev_no"),
        sa.UniqueConstraint(
            "brand_id", "post_id", "id", name="uq_post_revision_brand_id_post_id_id"
        ),
    )
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER post_revision_immutable BEFORE UPDATE ON post_revision "
        "FOR EACH ROW EXECUTE FUNCTION post_revision_immutable()"
    )


def _create_review_tables() -> None:
    op.create_table(
        "approval",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("post_id", sa.String(36), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("decided_by", sa.Text, nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.CheckConstraint(f"decision IN ({APPROVAL_DECISIONS})", name="ck_approval_decision"),
        sa.ForeignKeyConstraint(["brand_id"], ["brand.id"], name="fk_approval_brand_id_brand"),
        sa.ForeignKeyConstraint(
            ["brand_id", "post_id", "revision_id"],
            ["post_revision.brand_id", "post_revision.post_id", "post_revision.id"],
            name="fk_approval_brand_id_post_id_revision_id_post_revision",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval"),
    )
    op.create_table(
        "feedback_example",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("post_id", sa.String(36), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("before_text", sa.Text, nullable=False),
        sa.Column("after_text", sa.Text),
        sa.Column("note", sa.Text),
        sa.Column("expires_at", TS),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint(f"kind IN ({FEEDBACK_KINDS})", name="ck_feedback_example_kind"),
        sa.CheckConstraint(
            "kind <> 'temporario' OR expires_at IS NOT NULL",
            name="ck_feedback_example_temporario_requires_expiry",
        ),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brand.id"], name="fk_feedback_example_brand_id_brand"
        ),
        sa.ForeignKeyConstraint(
            ["brand_id", "post_id", "revision_id"],
            ["post_revision.brand_id", "post_revision.post_id", "post_revision.id"],
            name="fk_feedback_example_brand_id_post_id_revision_id_post_revision",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_feedback_example"),
    )


def _create_job() -> None:
    op.create_table(
        "job",
        id_col(),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("run_at", TS, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False),
        sa.Column("locked_by", sa.Text),
        sa.Column("locked_until", TS),
        sa.Column("lease_token", sa.Text),
        sa.Column("dedupe_key", sa.Text),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint(f"status IN ({JOB_STATUSES})", name="ck_job_status"),
        sa.PrimaryKeyConstraint("id", name="pk_job"),
    )
    # Unica so enquanto o job esta ativo: um pedido repetido depois de concluido e legitimo.
    op.create_index(
        "uq_job_dedupe_key_active",
        "job",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    for table in ("job", "feedback_example", "approval", "post_revision", "post", "kb_item"):
        op.drop_table(table)
    op.execute("DROP FUNCTION post_revision_immutable()")
