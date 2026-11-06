"""Tabelas do nucleo com integridade por marca.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

MONEY = sa.Numeric(12, 6)
TS = sa.DateTime(timezone=True)
ARTIFACT_STATES = "'draft', 'submitted', 'approved', 'rejected', 'withdrawn'"
RESERVATION_STATUSES = "'open', 'settled', 'released', 'unknown_outcome'"
RUN_STATUSES = "'running', 'succeeded', 'blocked_limit', 'needs_human', 'failed'"
STEP_ERRORS = "'invalid_output', 'refusal', 'truncated', 'network', 'limit', 'unknown_outcome'"

# Rejeita alteracoes ao conteudo de um artefacto; so `state` pode mudar.
IMMUTABLE_FUNCTION = """
CREATE FUNCTION artifact_payload_immutable() RETURNS trigger AS $$
BEGIN
    IF NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.type IS DISTINCT FROM OLD.type
       OR NEW.brand_id IS DISTINCT FROM OLD.brand_id
       OR NEW.parent_id IS DISTINCT FROM OLD.parent_id THEN
        RAISE EXCEPTION 'artifact %: payload, type, brand_id e parent_id sao imutaveis', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def id_col() -> sa.Column[str]:
    return sa.Column("id", sa.String(36), nullable=False)


def upgrade() -> None:
    _create_basics()
    _create_runs_and_costs()
    _create_artifacts()
    _create_interviews()


def _create_basics() -> None:
    op.create_table(
        "brand",
        id_col(),
        sa.Column("name", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_brand"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("subject_type", sa.Text, nullable=False),
        sa.Column("subject_id", sa.Text, nullable=False),
        sa.Column("detail", JSONB, nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.Column("correlation_id", sa.Text),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_table(
        "system_flag",
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("set_by", sa.Text, nullable=False),
        sa.Column("set_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_system_flag"),
    )
    op.create_table(
        "model_price",
        id_col(),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column("price_version", sa.Text, nullable=False),
        sa.Column("valid_from", TS, nullable=False),
        sa.Column("input_per_million", MONEY, nullable=False),
        sa.Column("output_per_million", MONEY, nullable=False),
        sa.Column("conditions", JSONB, nullable=False),
        sa.CheckConstraint(
            "input_per_million >= 0 AND output_per_million >= 0",
            name="ck_model_price_non_negative_prices",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_price"),
        sa.UniqueConstraint(
            "provider",
            "model_id",
            "price_version",
            name="uq_model_price_provider_model_id_price_version",
        ),
    )


def _create_runs_and_costs() -> None:
    op.create_table(
        "agent_run",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("spec_version", sa.Integer, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("job_id", sa.Text),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("cost_usd", MONEY, nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("ended_at", TS),
        sa.CheckConstraint(f"status IN ({RUN_STATUSES})", name="ck_agent_run_status"),
        sa.ForeignKeyConstraint(["brand_id"], ["brand.id"], name="fk_agent_run_brand_id_brand"),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run"),
        sa.UniqueConstraint("job_id", name="uq_agent_run_job_id"),
        sa.UniqueConstraint("brand_id", "id", name="uq_agent_run_brand_id_id"),
    )
    op.create_table(
        "agent_step",
        id_col(),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("input_summary", sa.Text),
        sa.Column("output_ref", sa.Text),
        sa.Column("error_class", sa.Text),
        sa.CheckConstraint("kind IN ('llm', 'tool')", name="ck_agent_step_kind"),
        sa.CheckConstraint(
            f"error_class IS NULL OR error_class IN ({STEP_ERRORS})",
            name="ck_agent_step_error_class",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_run.id"], name="fk_agent_step_run_id_agent_run"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_step"),
        sa.UniqueConstraint("run_id", "seq", name="uq_agent_step_run_id_seq"),
    )
    op.create_table(
        "cost_reservation",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("step_id", sa.String(36)),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column("price_version", sa.Text, nullable=False),
        sa.Column("reserved_usd", MONEY, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("settled_at", TS),
        sa.CheckConstraint(
            f"status IN ({RESERVATION_STATUSES})", name="ck_cost_reservation_status"
        ),
        sa.CheckConstraint("reserved_usd >= 0", name="ck_cost_reservation_non_negative_reserved"),
        sa.ForeignKeyConstraint(
            ["brand_id", "run_id"],
            ["agent_run.brand_id", "agent_run.id"],
            name="fk_cost_reservation_brand_id_run_id_agent_run",
        ),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brand.id"], name="fk_cost_reservation_brand_id_brand"
        ),
        sa.ForeignKeyConstraint(
            ["step_id"], ["agent_step.id"], name="fk_cost_reservation_step_id_agent_step"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cost_reservation"),
        sa.UniqueConstraint("brand_id", "id", name="uq_cost_reservation_brand_id_id"),
    )
    op.create_table(
        "cost_entry",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("reservation_id", sa.String(36), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("step_id", sa.String(36)),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column("provider_request_id", sa.Text),
        sa.Column("price_version", sa.Text, nullable=False),
        sa.Column("region", sa.Text),
        sa.Column("tokens_in", sa.Integer, nullable=False),
        sa.Column("tokens_out", sa.Integer, nullable=False),
        sa.Column("tokens_reasoning", sa.Integer),
        sa.Column("units", JSONB, nullable=False),
        sa.Column("cost_usd", MONEY, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.CheckConstraint("kind IN ('measured', 'estimated')", name="ck_cost_entry_kind"),
        sa.CheckConstraint("cost_usd >= 0", name="ck_cost_entry_non_negative_cost"),
        sa.ForeignKeyConstraint(["brand_id"], ["brand.id"], name="fk_cost_entry_brand_id_brand"),
        sa.ForeignKeyConstraint(
            ["brand_id", "reservation_id"],
            ["cost_reservation.brand_id", "cost_reservation.id"],
            name="fk_cost_entry_brand_id_reservation_id_cost_reservation",
        ),
        sa.ForeignKeyConstraint(
            ["brand_id", "run_id"],
            ["agent_run.brand_id", "agent_run.id"],
            name="fk_cost_entry_brand_id_run_id_agent_run",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"], ["agent_step.id"], name="fk_cost_entry_step_id_agent_step"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cost_entry"),
        sa.UniqueConstraint("reservation_id", name="uq_cost_entry_reservation_id"),
        sa.UniqueConstraint("provider_request_id", name="uq_cost_entry_provider_request_id"),
    )


def _create_artifacts() -> None:
    op.create_table(
        "artifact",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("parent_id", sa.String(36)),
        sa.Column("produced_by_run_id", sa.String(36)),
        sa.CheckConstraint(f"state IN ({ARTIFACT_STATES})", name="ck_artifact_state"),
        sa.ForeignKeyConstraint(["brand_id"], ["brand.id"], name="fk_artifact_brand_id_brand"),
        sa.PrimaryKeyConstraint("id", name="pk_artifact"),
        sa.UniqueConstraint("brand_id", "id", name="uq_artifact_brand_id_id"),
    )
    # FKs que apontam a propria tabela ou a agent_run so entram depois do UNIQUE (brand_id, id).
    op.create_foreign_key(
        "fk_artifact_brand_id_parent_id_artifact",
        "artifact",
        "artifact",
        ["brand_id", "parent_id"],
        ["brand_id", "id"],
    )
    op.create_foreign_key(
        "fk_artifact_brand_id_produced_by_run_id_agent_run",
        "artifact",
        "agent_run",
        ["brand_id", "produced_by_run_id"],
        ["brand_id", "id"],
    )
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER artifact_payload_immutable BEFORE UPDATE ON artifact "
        "FOR EACH ROW EXECUTE FUNCTION artifact_payload_immutable()"
    )
    _create_artifact_dependents()


def _create_artifact_dependents() -> None:
    op.create_table(
        "artifact_head",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("active_artifact_id", sa.String(36), nullable=False),
        sa.Column("previous_artifact_id", sa.String(36)),
        sa.Column("activated_by", sa.Text, nullable=False),
        sa.Column("activated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["brand_id"], ["brand.id"], name="fk_artifact_head_brand_id_brand"),
        sa.ForeignKeyConstraint(
            ["brand_id", "active_artifact_id"],
            ["artifact.brand_id", "artifact.id"],
            name="fk_artifact_head_brand_id_active_artifact_id_artifact",
        ),
        sa.ForeignKeyConstraint(
            ["brand_id", "previous_artifact_id"],
            ["artifact.brand_id", "artifact.id"],
            name="fk_artifact_head_brand_id_previous_artifact_id_artifact",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifact_head"),
        sa.UniqueConstraint(
            "brand_id", "type", "scope", name="uq_artifact_head_brand_id_type_scope"
        ),
    )
    op.create_table(
        "brand_profile",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("artifact_id", sa.String(36), nullable=False),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["brand_id"], ["brand.id"], name="fk_brand_profile_brand_id_brand"),
        sa.ForeignKeyConstraint(
            ["brand_id", "artifact_id"],
            ["artifact.brand_id", "artifact.id"],
            name="fk_brand_profile_brand_id_artifact_id_artifact",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand_profile"),
        sa.UniqueConstraint("brand_id", name="uq_brand_profile_brand_id"),
    )
    op.create_table(
        "strategy_decision",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("strategy_artifact_id", sa.String(36), nullable=False),
        sa.Column("proposal_index", sa.Integer, nullable=False),
        sa.Column("accepted", sa.Boolean, nullable=False),
        sa.Column("decided_by", sa.Text, nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brand.id"], name="fk_strategy_decision_brand_id_brand"
        ),
        sa.ForeignKeyConstraint(
            ["brand_id", "strategy_artifact_id"],
            ["artifact.brand_id", "artifact.id"],
            name="fk_strategy_decision_brand_id_strategy_artifact_id_artifact",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_strategy_decision"),
        sa.UniqueConstraint(
            "strategy_artifact_id",
            "proposal_index",
            name="uq_strategy_decision_strategy_artifact_id_proposal_index",
        ),
    )


def _create_interviews() -> None:
    op.create_table(
        "interview_session",
        id_col(),
        sa.Column("brand_id", sa.String(36), nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("gap_map", JSONB, nullable=False),
        sa.Column("busy_submission_id", sa.String(36)),
        sa.Column("busy_since", TS),
        sa.CheckConstraint(
            "state IN ('open', 'profile_requested', 'closed')", name="ck_interview_session_state"
        ),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brand.id"], name="fk_interview_session_brand_id_brand"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interview_session"),
        sa.UniqueConstraint("brand_id", "id", name="uq_interview_session_brand_id_id"),
    )
    op.create_table(
        "interview_submission",
        id_col(),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("result", JSONB),
        sa.CheckConstraint(
            "status IN ('reserved', 'completed', 'failed')", name="ck_interview_submission_status"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["interview_session.id"],
            name="fk_interview_submission_session_id_interview_session",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interview_submission"),
        sa.UniqueConstraint(
            "session_id",
            "idempotency_key",
            name="uq_interview_submission_session_id_idempotency_key",
        ),
    )
    op.create_table(
        "interview_turn",
        id_col(),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("submission_id", sa.String(36)),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("run_id", sa.String(36)),
        sa.CheckConstraint("role IN ('user', 'director')", name="ck_interview_turn_role"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["interview_session.id"],
            name="fk_interview_turn_session_id_interview_session",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["interview_submission.id"],
            name="fk_interview_turn_submission_id_interview_submission",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_run.id"], name="fk_interview_turn_run_id_agent_run"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interview_turn"),
        sa.UniqueConstraint("session_id", "seq", name="uq_interview_turn_session_id_seq"),
    )


def downgrade() -> None:
    for table in (
        "interview_turn",
        "interview_submission",
        "interview_session",
        "strategy_decision",
        "brand_profile",
        "artifact_head",
        "artifact",
        "cost_entry",
        "cost_reservation",
        "agent_step",
        "agent_run",
        "model_price",
        "system_flag",
        "audit_log",
        "brand",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION artifact_payload_immutable()")
