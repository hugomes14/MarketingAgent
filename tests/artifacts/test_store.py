"""Ciclo de vida dos artefactos: imutabilidade, versao ativa, projecao e auditoria."""

from collections.abc import Mapping

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import (
    ArtifactNotFound,
    ArtifactState,
    ArtifactStore,
    ArtifactType,
    InvalidTransition,
)
from marketing_agent.clock import FixedClock
from marketing_agent.db.models import ArtifactHead, AuditLog, BrandProfile

Factory = sessionmaker[Session]
PERFIL_V1: Mapping[str, object] = {"nome": "Marca", "tom": "calmo", "itens": [1, 2]}
PERFIL_V2: Mapping[str, object] = {"nome": "Marca", "tom": "ousado"}


@pytest.fixture
def store(session_factory: Factory, clock: FixedClock) -> ArtifactStore:
    return ArtifactStore(session_factory, clock)


def activate_profile(store: ArtifactStore, brand_id: str, payload: Mapping[str, object]) -> str:
    created = store.create(brand_id, ArtifactType.PERFIL_MARCA, payload)
    store.submit(created.id, "ana")
    return store.approve_and_activate(created.id, "ana").id


def test_create_is_draft(store: ArtifactStore, brand_id: str) -> None:
    record = store.create(brand_id, ArtifactType.PERFIL_MARCA, PERFIL_V1)
    assert record.state is ArtifactState.DRAFT
    assert record.payload == PERFIL_V1
    assert store.get(record.id) == record
    assert store.active(brand_id, ArtifactType.PERFIL_MARCA) is None


def test_new_draft_does_not_change_active(store: ArtifactStore, brand_id: str) -> None:
    v1 = activate_profile(store, brand_id, PERFIL_V1)
    v2 = store.create(brand_id, ArtifactType.PERFIL_MARCA, PERFIL_V2, parent_id=v1)
    store.submit(v2.id, "ana")
    active = store.active(brand_id, ArtifactType.PERFIL_MARCA)
    assert active is not None and active.id == v1


def test_rejected_draft_keeps_active(store: ArtifactStore, brand_id: str) -> None:
    v1 = activate_profile(store, brand_id, PERFIL_V1)
    v2 = store.create(brand_id, ArtifactType.PERFIL_MARCA, PERFIL_V2, parent_id=v1)
    store.submit(v2.id, "ana")
    assert store.reject(v2.id, "ana", "tom errado").state is ArtifactState.REJECTED
    active = store.active(brand_id, ArtifactType.PERFIL_MARCA)
    assert active is not None and active.id == v1


def test_activation_records_previous(
    store: ArtifactStore, session_factory: Factory, brand_id: str
) -> None:
    v1 = activate_profile(store, brand_id, PERFIL_V1)
    v2 = activate_profile(store, brand_id, PERFIL_V2)
    active = store.active(brand_id, ArtifactType.PERFIL_MARCA)
    assert active is not None and active.id == v2
    assert store.get(v1).state is ArtifactState.APPROVED
    with session_factory() as session:
        head = session.scalars(select(ArtifactHead)).one()
        assert head.previous_artifact_id == v1
        assert head.activated_by == "ana"


def test_profile_activation_updates_projection(
    store: ArtifactStore, session_factory: Factory, brand_id: str
) -> None:
    activate_profile(store, brand_id, PERFIL_V1)
    v2 = activate_profile(store, brand_id, PERFIL_V2)
    with session_factory() as session:
        profile = session.scalars(select(BrandProfile)).one()
        assert profile.artifact_id == v2
        assert profile.data == PERFIL_V2


def test_illegal_transition_raises(store: ArtifactStore, brand_id: str) -> None:
    draft = store.create(brand_id, ArtifactType.PERFIL_MARCA, PERFIL_V1)
    with pytest.raises(InvalidTransition):
        store.approve_and_activate(draft.id, "ana")
    assert store.get(draft.id).state is ArtifactState.DRAFT


def test_unknown_artifact_raises(store: ArtifactStore) -> None:
    with pytest.raises(ArtifactNotFound):
        store.get("00000000-0000-0000-0000-000000000000")
    with pytest.raises(ArtifactNotFound):
        store.submit("00000000-0000-0000-0000-000000000000", "ana")


def test_transitions_write_audit_with_actor(
    clock: FixedClock, store: ArtifactStore, session_factory: Factory, brand_id: str
) -> None:
    created = store.create(brand_id, ArtifactType.DIAGNOSTICO, {"k": 1})
    store.submit(created.id, "ana")
    store.reject(created.id, "rui", "incompleto")
    with session_factory() as session:
        rows = session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [(r.actor, r.action, r.subject_id) for r in rows] == [
        ("ana", "artifact.submit", created.id),
        ("rui", "artifact.reject", created.id),
    ]
    assert rows[1].detail["reason"] == "incompleto"
    assert rows[0].at == clock.now()


def test_payload_preserved_in_storage(
    store: ArtifactStore, session_factory: Factory, brand_id: str
) -> None:
    v1 = activate_profile(store, brand_id, PERFIL_V1)
    other = store.create(brand_id, ArtifactType.PERFIL_MARCA, PERFIL_V2)
    store.submit(other.id, "ana")
    store.withdraw(other.id, "ana")
    with session_factory() as session:
        stored = session.execute(
            text("SELECT payload FROM artifact WHERE id = :id"), {"id": v1}
        ).scalar_one()
    assert stored == PERFIL_V1
