"""Feedback: tipos, validade, limites no prompt, desativacao e auditoria sem texto livre."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import FixedClock
from marketing_agent.db.models import (
    Artifact,
    AuditLog,
    Brand,
    FeedbackExample,
    Post,
    PostRevision,
)
from marketing_agent.feedback.service import FeedbackError, FeedbackService

Factory = sessionmaker[Session]
SECRET_TEXT = "texto-livre-do-responsavel"


@dataclass
class Rig:
    """Servico de feedback com a marca, um post e uma revisao desse post."""

    service: FeedbackService
    factory: Factory
    clock: FixedClock
    brand_id: str
    post_id: str
    revision_id: str

    def record(
        self,
        choice: str = "estilo",
        *,
        before_text: str = "antes",
        after_text: str | None = "depois",
        note: str | None = None,
        expires_at: datetime | None = None,
        brand_id: str | None = None,
        revision_id: str | None = None,
    ) -> str | None:
        """Grava um exemplo numa transacao propria; `kwargs` ajustam campos."""
        with self.factory.begin() as session:
            return self.service.record_in(
                session,
                brand_id=brand_id or self.brand_id,
                post_id=self.post_id,
                revision_id=revision_id or self.revision_id,
                choice=choice,  # type: ignore[arg-type]
                before_text=before_text,
                after_text=after_text,
                note=note,
                expires_at=expires_at,
                actor="ana",
            )

    def record_many(self, choice: str, count: int) -> list[str]:
        """Grava `count` exemplos, avancando o relogio um minuto entre cada um."""
        ids: list[str] = []
        for index in range(count):
            self.clock.advance(timedelta(minutes=1))
            new_id = self.record(choice, before_text=f"{choice}-{index}")
            assert new_id is not None
            ids.append(new_id)
        return ids

    def row_count(self) -> int:
        """Numero de linhas em `feedback_example`."""
        with self.factory() as session:
            return session.scalar(select(func.count()).select_from(FeedbackExample)) or 0

    def audits(self) -> list[AuditLog]:
        """Todas as linhas de auditoria `feedback.*`, por ordem."""
        with self.factory() as session:
            return list(
                session.scalars(
                    select(AuditLog).where(AuditLog.action.like("feedback.%")).order_by(AuditLog.id)
                )
            )


def seed_post_with_revision(factory: Factory, brand_id: str, slot_key: str) -> tuple[str, str]:
    """Cria artefacto, post e revisao da marca; devolve `(post_id, revision_id)`."""
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    with factory.begin() as session:
        artifact = Artifact(
            brand_id=brand_id, type="plano", schema_version=1, state="approved", payload={}
        )
        session.add(artifact)
        session.flush()
        post = Post(
            brand_id=brand_id,
            plan_artifact_id=artifact.id,
            strategy_artifact_id=artifact.id,
            slot_key=slot_key,
            channels=["instagram"],
            scheduled_date=date(2026, 11, 10),
            brief={"tema": "t", "kb_refs": []},
            lifecycle="in_review",
            created_at=now,
        )
        session.add(post)
        session.flush()
        revision = PostRevision(
            brand_id=brand_id,
            post_id=post.id,
            rev_no=1,
            revision_hash="a" * 64,
            caption="Legenda",
            hashtags=[],
            claims=[],
            review_violations=[],
            origin="agente",
            created_at=now,
        )
        session.add(revision)
        session.flush()
        return post.id, revision.id


@pytest.fixture
def rig(session_factory: Factory, clock: FixedClock, brand_id: str) -> Rig:
    post_id, revision_id = seed_post_with_revision(session_factory, brand_id, "2026-W46-1")
    return Rig(
        FeedbackService(session_factory, clock),
        session_factory,
        clock,
        brand_id,
        post_id,
        revision_id,
    )


def test_nao_usar_records_nothing(rig: Rig) -> None:
    assert rig.record("nao_usar") is None
    assert rig.row_count() == 0
    assert rig.audits() == []


def test_nao_usar_skips_validation(rig: Rig) -> None:
    # Escolher "nao usar" nunca falha: nao ha nada a validar nem a gravar.
    assert rig.record("nao_usar", before_text="", after_text="x" * 9999) is None
    assert rig.row_count() == 0


def test_estilo_is_stored_with_audit_without_text(rig: Rig) -> None:
    example_id = rig.record("estilo", before_text=SECRET_TEXT, note=SECRET_TEXT)
    assert example_id is not None
    [view] = rig.service.list_examples(rig.brand_id)
    assert (view.id, view.kind, view.before_text, view.after_text) == (
        example_id,
        "estilo",
        SECRET_TEXT,
        "depois",
    )
    assert view.is_active is True
    [audit] = rig.audits()
    assert (audit.actor, audit.action, audit.subject_type, audit.subject_id) == (
        "ana",
        "feedback.record",
        "feedback_example",
        example_id,
    )
    assert audit.detail == {"kind": "estilo", "post_id": rig.post_id}
    assert SECRET_TEXT not in str(audit.detail)


def test_record_in_does_not_commit(rig: Rig) -> None:
    with pytest.raises(RuntimeError), rig.factory.begin() as session:
        rig.service.record_in(
            session,
            brand_id=rig.brand_id,
            post_id=rig.post_id,
            revision_id=rig.revision_id,
            choice="estilo",
            before_text="a",
            after_text=None,
            note=None,
            expires_at=None,
            actor="ana",
        )
        raise RuntimeError("falha do chamador depois do feedback")
    assert rig.row_count() == 0
    assert rig.audits() == []


def test_temporario_requires_future_expiry(rig: Rig) -> None:
    now = rig.clock.now()
    for expires_at in (None, now, now - timedelta(days=1)):
        with pytest.raises(FeedbackError, match="expires_at"):
            rig.record("temporario", expires_at=expires_at)
    assert rig.row_count() == 0
    assert rig.record("temporario", expires_at=now + timedelta(days=7)) is not None
    assert rig.row_count() == 1


@pytest.mark.parametrize("choice", ["estilo", "correcao_factual", "negativo"])
def test_expires_at_only_accepted_for_temporario(rig: Rig, choice: str) -> None:
    with pytest.raises(FeedbackError, match="expires_at"):
        rig.record(choice, expires_at=rig.clock.now() + timedelta(days=1))
    assert rig.row_count() == 0


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"before_text": ""}, "before_text"),
        ({"before_text": "x" * 2501}, "before_text"),
        ({"after_text": ""}, "after_text"),
        ({"after_text": "x" * 2501}, "after_text"),
        ({"note": ""}, "note"),
        ({"note": "x" * 501}, "note"),
    ],
)
def test_text_limits_rejected_without_leaking_text(
    rig: Rig, overrides: dict[str, str], field: str
) -> None:
    with pytest.raises(FeedbackError, match=field) as raised:
        rig.record(**overrides)  # type: ignore[arg-type]
    assert "xxx" not in str(raised.value)
    assert rig.row_count() == 0


def test_text_limits_accept_boundaries(rig: Rig) -> None:
    assert rig.record(before_text="x" * 2500, after_text="y" * 2500, note="n" * 500) is not None
    assert rig.record(before_text="x", after_text=None, note=None) is not None


def test_revision_of_another_post_or_brand_is_rejected(rig: Rig, session_factory: Factory) -> None:
    with session_factory.begin() as session:
        other = Brand(name="Outra marca")
        session.add(other)
        session.flush()
        other_id = other.id
    _, other_revision = seed_post_with_revision(session_factory, other_id, "2026-W46-1")
    with pytest.raises(FeedbackError, match="revisao"):
        rig.record(revision_id=other_revision)
    with pytest.raises(FeedbackError, match="revisao"):
        rig.record(brand_id=other_id)
    with pytest.raises(FeedbackError, match="revisao"):
        rig.record(revision_id="inexistente")
    assert rig.row_count() == 0


def test_prompt_examples_limits_and_order(rig: Rig) -> None:
    styles = rig.record_many("estilo", 7)
    negatives = rig.record_many("negativo", 5)
    estilo, negativo = rig.service.prompt_examples(rig.brand_id)
    assert [view.id for view in estilo] == list(reversed(styles))[:5]
    assert [view.id for view in negativo] == list(reversed(negatives))[:3]
    assert {view.kind for view in estilo} == {"estilo"}
    assert {view.kind for view in negativo} == {"negativo"}


def test_prompt_examples_same_instant_ordering_is_deterministic(rig: Rig) -> None:
    ids = [rig.record("estilo", before_text=f"t{n}") for n in range(3)]
    first = rig.service.prompt_examples(rig.brand_id)
    assert first == rig.service.prompt_examples(rig.brand_id)
    assert sorted(view.id for view in first[0]) == sorted(i for i in ids if i)


def test_expired_and_inactive_excluded(rig: Rig) -> None:
    kept = rig.record("estilo")
    expiring = rig.record("estilo")
    inactive_style = rig.record("estilo")
    inactive_negative = rig.record("negativo")
    assert kept and expiring and inactive_style and inactive_negative
    # `record_in` so aceita validade em `temporario`; um estilo com validade (dado legado
    # ou futuro) cria-se na base para provar que a expiracao tambem o exclui do prompt.
    with rig.factory.begin() as session:
        row = session.get(FeedbackExample, expiring)
        assert row is not None
        row.expires_at = rig.clock.now() + timedelta(days=1)
    rig.service.deactivate(rig.brand_id, inactive_style, "ana")
    rig.service.deactivate(rig.brand_id, inactive_negative, "ana")
    estilo, negativo = rig.service.prompt_examples(rig.brand_id)
    assert {view.id for view in estilo} == {kept, expiring}
    assert negativo == []
    rig.clock.advance(timedelta(days=2))
    estilo, _ = rig.service.prompt_examples(rig.brand_id)
    assert [view.id for view in estilo] == [kept]


def test_correcao_factual_not_in_prompt_examples(rig: Rig) -> None:
    rig.record("correcao_factual")
    rig.record("temporario", expires_at=rig.clock.now() + timedelta(days=3))
    assert rig.service.prompt_examples(rig.brand_id) == ([], [])
    assert {view.kind for view in rig.service.list_examples(rig.brand_id)} == {
        "correcao_factual",
        "temporario",
    }


def test_prompt_examples_are_brand_scoped(rig: Rig, session_factory: Factory) -> None:
    rig.record("estilo")
    with session_factory.begin() as session:
        other = Brand(name="Outra marca")
        session.add(other)
        session.flush()
        other_id = other.id
    assert rig.service.prompt_examples(other_id) == ([], [])
    assert rig.service.list_examples(other_id) == []


def test_list_examples_returns_everything_newest_first(rig: Rig) -> None:
    ids = rig.record_many("estilo", 2) + rig.record_many("negativo", 1)
    rig.service.deactivate(rig.brand_id, ids[0], "ana")
    views = rig.service.list_examples(rig.brand_id)
    assert [view.id for view in views] == list(reversed(ids))
    assert [view.is_active for view in views] == [True, True, False]


def test_deactivate_is_idempotent_and_audited_once(rig: Rig) -> None:
    example_id = rig.record("negativo", before_text=SECRET_TEXT)
    assert example_id is not None
    rig.service.deactivate(rig.brand_id, example_id, "bia")
    rig.service.deactivate(rig.brand_id, example_id, "bia")
    [view] = rig.service.list_examples(rig.brand_id)
    assert view.is_active is False
    deactivations = [a for a in rig.audits() if a.action == "feedback.deactivate"]
    assert len(deactivations) == 1
    assert deactivations[0].actor == "bia"
    assert deactivations[0].subject_id == example_id
    assert deactivations[0].detail == {"kind": "negativo"}


def test_deactivate_other_brand_or_unknown_fails_without_change(
    rig: Rig, session_factory: Factory
) -> None:
    example_id = rig.record("estilo")
    assert example_id is not None
    with session_factory.begin() as session:
        other = Brand(name="Outra marca")
        session.add(other)
        session.flush()
        other_id = other.id
    with pytest.raises(FeedbackError):
        rig.service.deactivate(other_id, example_id, "ana")
    with pytest.raises(FeedbackError):
        rig.service.deactivate(rig.brand_id, "inexistente", "ana")
    [view] = rig.service.list_examples(rig.brand_id)
    assert view.is_active is True
    assert [a.action for a in rig.audits()] == ["feedback.record"]
