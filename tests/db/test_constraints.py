"""Restricoes de integridade: marca, enums, imutabilidade e unicidades."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.db.models import (
    AgentRun,
    Artifact,
    ArtifactHead,
    Brand,
    CostEntry,
    CostReservation,
    InterviewSession,
    InterviewSubmission,
    InterviewTurn,
    StrategyDecision,
)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
Factory = sessionmaker[Session]


def make_artifact(brand_id: str, state: str = "approved") -> Artifact:
    return Artifact(
        brand_id=brand_id, type="perfil_marca", schema_version=1, state=state, payload={"a": 1}
    )


def make_run(brand_id: str) -> AgentRun:
    return AgentRun(
        brand_id=brand_id,
        role="diretor",
        spec_version=1,
        trigger="test",
        status="running",
        started_at=NOW,
    )


def make_reservation(brand_id: str, run_id: str) -> CostReservation:
    return CostReservation(
        brand_id=brand_id,
        run_id=run_id,
        role="diretor",
        provider="fake",
        model_id="m",
        price_version="v1",
        reserved_usd=Decimal("0.5"),
        status="open",
        created_at=NOW,
    )


def make_entry(brand_id: str, run_id: str, reservation_id: str) -> CostEntry:
    return CostEntry(
        brand_id=brand_id,
        reservation_id=reservation_id,
        role="diretor",
        run_id=run_id,
        provider="fake",
        model_id="m",
        price_version="v1",
        tokens_in=1,
        tokens_out=1,
        units={},
        cost_usd=Decimal("0.1"),
        kind="measured",
        at=NOW,
    )


def test_cross_brand_head_rejected(session_factory: Factory, brand_id: str) -> None:
    with session_factory.begin() as session:
        other = Brand(name="B")
        artifact_a = make_artifact(brand_id)
        session.add_all([other, artifact_a])
        session.flush()
        other_id, artifact_id = other.id, artifact_a.id
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(
            ArtifactHead(
                brand_id=other_id,
                type="perfil_marca",
                scope="brand",
                active_artifact_id=artifact_id,
                activated_by="t",
                activated_at=NOW,
            )
        )


def test_artifact_state_check(session_factory: Factory, brand_id: str) -> None:
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(make_artifact(brand_id, state="superseded"))


def test_payload_update_blocked_by_trigger(session_factory: Factory, brand_id: str) -> None:
    with session_factory.begin() as session:
        artifact = make_artifact(brand_id)
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id
    with pytest.raises(DBAPIError), session_factory.begin() as session:
        session.execute(
            text("UPDATE artifact SET payload = '{\"a\": 2}'::jsonb WHERE id = :id"),
            {"id": artifact_id},
        )


def test_state_update_allowed_by_trigger(session_factory: Factory, brand_id: str) -> None:
    with session_factory.begin() as session:
        artifact = make_artifact(brand_id, state="draft")
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE artifact SET state = 'submitted' WHERE id = :id"), {"id": artifact_id}
        )
        state = session.execute(
            text("SELECT state FROM artifact WHERE id = :id"), {"id": artifact_id}
        ).scalar_one()
    assert state == "submitted"


def test_cost_entry_unique_per_reservation(session_factory: Factory, brand_id: str) -> None:
    with session_factory.begin() as session:
        run = make_run(brand_id)
        session.add(run)
        session.flush()
        reservation = make_reservation(brand_id, run.id)
        session.add(reservation)
        session.flush()
        run_id, reservation_id = run.id, reservation.id
        session.add(make_entry(brand_id, run_id, reservation_id))
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(make_entry(brand_id, run_id, reservation_id))


def test_turn_seq_unique_per_session(session_factory: Factory, brand_id: str) -> None:
    with session_factory.begin() as session:
        interview = InterviewSession(brand_id=brand_id, state="open", gap_map={})
        session.add(interview)
        session.flush()
        session_id = interview.id
        session.add(InterviewTurn(session_id=session_id, seq=1, role="user", content="x"))
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(InterviewTurn(session_id=session_id, seq=1, role="director", content="y"))


def test_submission_key_unique_per_session(session_factory: Factory, brand_id: str) -> None:
    def submission(session_id: str) -> InterviewSubmission:
        return InterviewSubmission(
            session_id=session_id, idempotency_key="k", text_sha256="h", status="reserved"
        )

    with session_factory.begin() as session:
        interview = InterviewSession(brand_id=brand_id, state="open", gap_map={})
        session.add(interview)
        session.flush()
        session_id = interview.id
        session.add(submission(session_id))
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(submission(session_id))


def test_strategy_decision_unique_per_proposal(session_factory: Factory, brand_id: str) -> None:
    def decision(artifact_id: str) -> StrategyDecision:
        return StrategyDecision(
            brand_id=brand_id,
            strategy_artifact_id=artifact_id,
            proposal_index=0,
            accepted=True,
            decided_by="t",
            decided_at=NOW,
        )

    with session_factory.begin() as session:
        artifact = make_artifact(brand_id)
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id
        session.add(decision(artifact_id))
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(decision(artifact_id))
