"""Modelos do nucleo (Plano 01, Anexo A.2).

Regras transversais:
- `id` e UUID em texto; datas em `timestamptz` (sempre UTC); dinheiro em `Numeric(12, 6)`.
- Tabelas com dados de marca levam `brand_id` e referenciam-se por FKs compostas
  `(brand_id, id)`, para uma linha nunca apontar para dados de outra marca. Por isso cada
  tabela referenciada declara `UNIQUE (brand_id, id)`.
- Enums sao `CHECK`s; nao usamos o tipo ENUM do Postgres, para as migracoes serem simples.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marketing_agent.db.base import Base, new_id

Money = Numeric(12, 6)
Timestamp = DateTime(timezone=True)

ARTIFACT_STATES = ("draft", "submitted", "approved", "rejected", "withdrawn")
RESERVATION_STATUSES = ("open", "settled", "released", "unknown_outcome")
RUN_STATUSES = ("running", "succeeded", "blocked_limit", "needs_human", "failed")
STEP_ERROR_CLASSES = (
    "invalid_output",
    "refusal",
    "truncated",
    "network",
    "limit",
    "unknown_outcome",
)
KB_KINDS = ("oferta", "preco", "horario", "contacto", "prova", "politica", "outro")
SOURCE_KINDS = ("dito_pelo_responsavel", "verificado", "suposto")
POST_LIFECYCLES = ("planned", "drafting", "in_review", "approved", "rejected", "cancelled")
CHANNELS = ("facebook", "instagram")
REVIEW_VERDICTS = ("aprovar", "rever", "rejeitar")
REVISION_ORIGINS = ("agente", "edicao_responsavel")
APPROVAL_DECISIONS = ("approved", "rejected", "changes_requested")
FEEDBACK_KINDS = ("estilo", "correcao_factual", "temporario", "negativo")
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "dead", "cancelled")
# Estados em que um job ainda conta para a deduplicacao (ver o indice parcial de `Job`).
ACTIVE_JOB_STATUSES = ("queued", "running")

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


def enum_check(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    """`CHECK` que restringe `column` ao conjunto `values`."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


def id_column() -> Mapped[str]:
    """Chave primaria UUID em texto."""
    return mapped_column(String(36), primary_key=True, default=new_id)


def brand_fk() -> Mapped[str]:
    """Coluna `brand_id` obrigatoria."""
    return mapped_column(String(36), ForeignKey("brand.id"), nullable=False)


class Brand(Base):
    """Marca. Hoje existe uma so."""

    __tablename__ = "brand"

    id: Mapped[str] = id_column()
    name: Mapped[str] = mapped_column(Text, nullable=False)


class AuditLog(Base):
    """Registo de auditoria que so se acrescenta. `detail` nunca leva segredos."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(Text)


class SystemFlag(Base):
    """Interruptor global (ex.: `publishing_paused_global`, `restore_in_progress`)."""

    __tablename__ = "system_flag"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    set_by: Mapped[str] = mapped_column(Text, nullable=False)
    set_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)


class ModelPriceRow(Base):
    """Preco versionado de um modelo; uma rota sem preco conhecido e recusada."""

    __tablename__ = "model_price"
    __table_args__ = (
        UniqueConstraint("provider", "model_id", "price_version"),
        CheckConstraint(
            "input_per_million >= 0 AND output_per_million >= 0", name="non_negative_prices"
        ),
    )

    id: Mapped[str] = id_column()
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    price_version: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(Timestamp, nullable=False)
    input_per_million: Mapped[Decimal] = mapped_column(Money, nullable=False)
    output_per_million: Mapped[Decimal] = mapped_column(Money, nullable=False)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AgentRun(Base):
    """Execucao de um agente sob um `AgentSpec`."""

    __tablename__ = "agent_run"
    __table_args__ = (
        UniqueConstraint("brand_id", "id"),
        enum_check("status", RUN_STATUSES, "status"),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    role: Mapped[str] = mapped_column(Text, nullable=False)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    job_id: Mapped[str | None] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal(0))
    started_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(Timestamp)


class AgentStep(Base):
    """Passo de uma execucao (chamada ao LLM ou a uma ferramenta)."""

    __tablename__ = "agent_step"
    __table_args__ = (
        UniqueConstraint("run_id", "seq"),
        enum_check("kind", ("llm", "tool"), "kind"),
        CheckConstraint(
            "error_class IS NULL OR error_class IN ("
            + ", ".join(f"'{value}'" for value in STEP_ERROR_CLASSES)
            + ")",
            name="error_class",
        ),
    )

    id: Mapped[str] = id_column()
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_run.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_ref: Mapped[str | None] = mapped_column(Text)
    error_class: Mapped[str | None] = mapped_column(Text)


class CostReservation(Base):
    """Reserva de custo criada antes de cada chamada paga."""

    __tablename__ = "cost_reservation"
    __table_args__ = (
        UniqueConstraint("brand_id", "id"),
        ForeignKeyConstraint(["brand_id", "run_id"], ["agent_run.brand_id", "agent_run.id"]),
        enum_check("status", RESERVATION_STATUSES, "status"),
        CheckConstraint("reserved_usd >= 0", name="non_negative_reserved"),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("agent_step.id"))
    role: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    price_version: Mapped[str] = mapped_column(Text, nullable=False)
    reserved_usd: Mapped[Decimal] = mapped_column(Money, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(Timestamp)


class CostEntry(Base):
    """Lancamento de custo: um por reserva, para repetir o commit nao duplicar."""

    __tablename__ = "cost_entry"
    __table_args__ = (
        ForeignKeyConstraint(
            ["brand_id", "reservation_id"], ["cost_reservation.brand_id", "cost_reservation.id"]
        ),
        ForeignKeyConstraint(["brand_id", "run_id"], ["agent_run.brand_id", "agent_run.id"]),
        enum_check("kind", ("measured", "estimated"), "kind"),
        CheckConstraint("cost_usd >= 0", name="non_negative_cost"),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    reservation_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("agent_step.id"))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(Text, unique=True)
    price_version: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)
    # Informativo: ja contido em `tokens_out` quando o fornecedor assim o reporta.
    tokens_reasoning: Mapped[int | None] = mapped_column(Integer)
    units: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    cost_usd: Mapped[Decimal] = mapped_column(Money, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)


class Artifact(Base):
    """Artefacto imutavel e versionado; so `state` avanca (ver o trigger na migracao)."""

    __tablename__ = "artifact"
    __table_args__ = (
        UniqueConstraint("brand_id", "id"),
        ForeignKeyConstraint(["brand_id", "parent_id"], ["artifact.brand_id", "artifact.id"]),
        ForeignKeyConstraint(
            ["brand_id", "produced_by_run_id"], ["agent_run.brand_id", "agent_run.id"]
        ),
        enum_check("state", ARTIFACT_STATES, "state"),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(36))
    produced_by_run_id: Mapped[str | None] = mapped_column(String(36))


class ArtifactHead(Base):
    """Versao ativa por marca, tipo e ambito; so muda ao aprovar uma nova versao."""

    __tablename__ = "artifact_head"
    __table_args__ = (
        UniqueConstraint("brand_id", "type", "scope"),
        ForeignKeyConstraint(
            ["brand_id", "active_artifact_id"], ["artifact.brand_id", "artifact.id"]
        ),
        ForeignKeyConstraint(
            ["brand_id", "previous_artifact_id"], ["artifact.brand_id", "artifact.id"]
        ),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    type: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    active_artifact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    previous_artifact_id: Mapped[str | None] = mapped_column(String(36))
    activated_by: Mapped[str] = mapped_column(Text, nullable=False)
    activated_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)


class BrandProfile(Base):
    """Projecao do `perfil_marca` ativo, atualizada na transacao de ativacao."""

    __tablename__ = "brand_profile"
    __table_args__ = (
        ForeignKeyConstraint(["brand_id", "artifact_id"], ["artifact.brand_id", "artifact.id"]),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), nullable=False, unique=True)
    artifact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)


class StrategyDecision(Base):
    """Decisao do responsavel sobre uma proposta de oferta de uma estrategia."""

    __tablename__ = "strategy_decision"
    __table_args__ = (
        UniqueConstraint("strategy_artifact_id", "proposal_index"),
        ForeignKeyConstraint(
            ["brand_id", "strategy_artifact_id"], ["artifact.brand_id", "artifact.id"]
        ),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    strategy_artifact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    proposal_index: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)


class InterviewSession(Base):
    """Sessao de entrevista do Diretor. `busy_*` marcam uma submissao em curso."""

    __tablename__ = "interview_session"
    __table_args__ = (
        UniqueConstraint("brand_id", "id"),
        enum_check("state", ("open", "profile_requested", "closed"), "state"),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    state: Mapped[str] = mapped_column(Text, nullable=False)
    gap_map: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    busy_submission_id: Mapped[str | None] = mapped_column(String(36))
    busy_since: Mapped[datetime | None] = mapped_column(Timestamp)


class InterviewSubmission(Base):
    """Submissao idempotente: a mesma chave com outro `text_sha256` e conflito (409)."""

    __tablename__ = "interview_submission"
    __table_args__ = (
        UniqueConstraint("session_id", "idempotency_key"),
        enum_check("status", ("reserved", "completed", "failed"), "status"),
    )

    id: Mapped[str] = id_column()
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_session.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class InterviewTurn(Base):
    """Turno da entrevista; uma submissao produz um turno `user` e um `director`."""

    __tablename__ = "interview_turn"
    __table_args__ = (
        UniqueConstraint("session_id", "seq"),
        enum_check("role", ("user", "director"), "role"),
    )

    id: Mapped[str] = id_column()
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_session.id"), nullable=False)
    submission_id: Mapped[str | None] = mapped_column(ForeignKey("interview_submission.id"))
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_run.id"))


class KbItem(Base):
    """Facto da base de conhecimento da marca (oferta, preco, horario...) com origem e validade."""

    __tablename__ = "kb_item"
    __table_args__ = (
        UniqueConstraint("brand_id", "kind", "key", "valid_from"),
        enum_check("kind", KB_KINDS, "kind"),
        enum_check("source_kind", SOURCE_KINDS, "source_kind"),
        CheckConstraint(
            "kind <> 'preco' OR (price_amount IS NOT NULL "
            "AND price_currency IS NOT NULL AND valid_until IS NOT NULL)",
            name="price_requires_amount_currency_validity",
        ),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_currency: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    # Por exemplo `perfil:<artifact_id>:<topico>`.
    source_ref: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)


class Post(Base):
    """Slot do plano semanal; o texto vive nas `PostRevision`."""

    __tablename__ = "post"
    __table_args__ = (
        UniqueConstraint("brand_id", "id"),
        UniqueConstraint("brand_id", "slot_key"),
        ForeignKeyConstraint(
            ["brand_id", "plan_artifact_id"], ["artifact.brand_id", "artifact.id"]
        ),
        ForeignKeyConstraint(
            ["brand_id", "strategy_artifact_id"], ["artifact.brand_id", "artifact.id"]
        ),
        enum_check("lifecycle", POST_LIFECYCLES, "lifecycle"),
        CheckConstraint(
            "cardinality(channels) >= 1 AND channels <@ ARRAY['facebook', 'instagram']::text[]",
            name="channels",
        ),
        CheckConstraint("review_rounds >= 0", name="non_negative_review_rounds"),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    plan_artifact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # `AAAA-Www-<n>`; estavel dentro da semana (um novo plano substitui por `slot_key`).
    slot_key: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_artifact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    experiment_index: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    # tema, pilar, objetivo e `kb_refs`.
    brief: Mapped[JsonObject] = mapped_column(JSONB, nullable=False)
    lifecycle: Mapped[str] = mapped_column(Text, nullable=False)
    review_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Motivo curto da ultima falha; nunca conteudo produzido pelo modelo.
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)


class PostRevision(Base):
    """Versao do texto de um post. Imutavel (ver o trigger na migracao)."""

    __tablename__ = "post_revision"
    __table_args__ = (
        UniqueConstraint("post_id", "rev_no"),
        # Permite as aprovacoes e o feedback provarem que revisao e post sao da mesma marca.
        UniqueConstraint("brand_id", "post_id", "id"),
        ForeignKeyConstraint(["brand_id", "post_id"], ["post.brand_id", "post.id"]),
        ForeignKeyConstraint(
            ["brand_id", "produced_by_run_id"], ["agent_run.brand_id", "agent_run.id"]
        ),
        enum_check("review_verdict", REVIEW_VERDICTS, "review_verdict"),
        enum_check("origin", REVISION_ORIGINS, "origin"),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    post_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rev_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # SHA-256 canonico do texto e dos campos por canal.
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    call_to_action: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(Text)
    # Lista de `{texto, kb_ref}`.
    claims: Mapped[list[dict[str, str | None]]] = mapped_column(JSONB, nullable=False)
    review_verdict: Mapped[str | None] = mapped_column(Text)
    review_violations: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    review_notes: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    produced_by_run_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)


class Approval(Base):
    """Decisao do responsavel sobre uma revisao; so a mais recente da revisao mais recente vale."""

    __tablename__ = "approval"
    __table_args__ = (
        ForeignKeyConstraint(
            ["brand_id", "post_id", "revision_id"],
            ["post_revision.brand_id", "post_revision.post_id", "post_revision.id"],
        ),
        enum_check("decision", APPROVAL_DECISIONS, "decision"),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    post_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)


class FeedbackExample(Base):
    """Correcao do responsavel guardada como exemplo para o Copywriter."""

    __tablename__ = "feedback_example"
    __table_args__ = (
        ForeignKeyConstraint(
            ["brand_id", "post_id", "revision_id"],
            ["post_revision.brand_id", "post_revision.post_id", "post_revision.id"],
        ),
        enum_check("kind", FEEDBACK_KINDS, "kind"),
        CheckConstraint(
            "kind <> 'temporario' OR expires_at IS NOT NULL", name="temporario_requires_expiry"
        ),
    )

    id: Mapped[str] = id_column()
    brand_id: Mapped[str] = brand_fk()
    post_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    before_text: Mapped[str] = mapped_column(Text, nullable=False)
    after_text: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(Timestamp)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)


class Job(Base):
    """Trabalho da fila global (sem marca). `dedupe_key` so e unica enquanto o job esta ativo."""

    __tablename__ = "job"
    __table_args__ = (
        enum_check("status", JOB_STATUSES, "status"),
        Index(
            "uq_job_dedupe_key_active",
            "dedupe_key",
            unique=True,
            postgresql_where=text(
                "status IN (" + ", ".join(f"'{v}'" for v in ACTIVE_JOB_STATUSES) + ")"
            ),
        ),
    )

    id: Mapped[str] = id_column()
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[JsonObject] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    run_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_until: Mapped[datetime | None] = mapped_column(Timestamp)
    lease_token: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False)
