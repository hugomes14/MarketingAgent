"""Restricoes da base de conhecimento, dos posts, das revisoes, do feedback e da fila."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.db.models import (
    Artifact,
    Brand,
    FeedbackExample,
    Job,
    KbItem,
    Post,
    PostRevision,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
Factory = sessionmaker[Session]


def make_kb_item(brand_id: str, kind: str = "oferta", key: str = "k1") -> KbItem:
    return KbItem(
        brand_id=brand_id,
        kind=kind,
        key=key,
        title="Titulo",
        body="Corpo",
        valid_from=date(2026, 11, 1),
        source_kind="verificado",
        created_by="t",
        created_at=NOW,
    )


def make_artifact(brand_id: str) -> Artifact:
    return Artifact(
        brand_id=brand_id, type="plano", schema_version=1, state="approved", payload={"a": 1}
    )


def make_post(
    brand_id: str, artifact_id: str, slot_key: str = "2026-W46-1", channels: list[str] | None = None
) -> Post:
    return Post(
        brand_id=brand_id,
        plan_artifact_id=artifact_id,
        strategy_artifact_id=artifact_id,
        slot_key=slot_key,
        channels=["instagram"] if channels is None else channels,
        scheduled_date=date(2026, 11, 10),
        brief={"tema": "t", "kb_refs": []},
        lifecycle="planned",
        created_at=NOW,
    )


def make_revision(brand_id: str, post_id: str, rev_no: int = 1) -> PostRevision:
    return PostRevision(
        brand_id=brand_id,
        post_id=post_id,
        rev_no=rev_no,
        revision_hash="a" * 64,
        caption="Legenda",
        hashtags=["#um"],
        claims=[{"texto": "x", "kb_ref": None}],
        review_violations=[],
        origin="agente",
        created_at=NOW,
    )


def make_job(status: str = "queued", dedupe_key: str | None = "k") -> Job:
    return Job(
        type="generate_post",
        payload={"post_id": "p"},
        status=status,
        run_at=NOW,
        dedupe_key=dedupe_key,
        created_at=NOW,
        updated_at=NOW,
    )


def seed_post(factory: Factory, brand_id: str) -> tuple[str, str]:
    """Cria artefacto e post; devolve os respetivos ids."""
    with factory.begin() as session:
        artifact = make_artifact(brand_id)
        session.add(artifact)
        session.flush()
        post = make_post(brand_id, artifact.id)
        session.add(post)
        session.flush()
        return artifact.id, post.id


def test_kb_price_requires_amount_currency_and_validity(
    session_factory: Factory, brand_id: str
) -> None:
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        item = make_kb_item(brand_id, kind="preco")
        item.price_amount = Decimal("9.90")
        item.price_currency = "EUR"
        session.add(item)  # sem valid_until
    with session_factory.begin() as session:
        item = make_kb_item(brand_id, kind="preco")
        item.price_amount = Decimal("9.90")
        item.price_currency = "EUR"
        item.valid_until = date(2026, 12, 1)
        session.add(item)


def test_kb_item_unique_per_kind_key_and_start(session_factory: Factory, brand_id: str) -> None:
    with session_factory.begin() as session:
        session.add(make_kb_item(brand_id))
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(make_kb_item(brand_id))


def test_post_slot_key_unique_per_brand(session_factory: Factory, brand_id: str) -> None:
    artifact_id, _ = seed_post(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(make_post(brand_id, artifact_id))
    with session_factory.begin() as session:
        other = Brand(name="B")
        session.add(other)
        session.flush()
        other_artifact = make_artifact(other.id)
        session.add(other_artifact)
        session.flush()
        session.add(make_post(other.id, other_artifact.id))  # mesma slot_key, outra marca


@pytest.mark.parametrize("channels", [[], ["tiktok"], ["facebook", "tiktok"]])
def test_post_channels_must_be_known_and_not_empty(
    session_factory: Factory, brand_id: str, channels: list[str]
) -> None:
    with session_factory.begin() as session:
        artifact = make_artifact(brand_id)
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(make_post(brand_id, artifact_id, channels=channels))
    with session_factory.begin() as session:
        session.add(make_post(brand_id, artifact_id, channels=["facebook", "instagram"]))


def test_post_lifecycle_check(session_factory: Factory, brand_id: str) -> None:
    artifact_id, _ = seed_post(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        post = make_post(brand_id, artifact_id, slot_key="2026-W46-2")
        post.lifecycle = "published"
        session.add(post)


def test_post_revision_rev_no_unique_per_post(session_factory: Factory, brand_id: str) -> None:
    _, post_id = seed_post(session_factory, brand_id)
    with session_factory.begin() as session:
        session.add(make_revision(brand_id, post_id))
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(make_revision(brand_id, post_id))
    with session_factory.begin() as session:
        session.add(make_revision(brand_id, post_id, rev_no=2))


def test_cross_brand_post_revision_rejected(session_factory: Factory, brand_id: str) -> None:
    _, post_id = seed_post(session_factory, brand_id)
    with session_factory.begin() as session:
        other = Brand(name="B")
        session.add(other)
        session.flush()
        other_id = other.id
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(make_revision(other_id, post_id))


def test_post_revision_is_immutable(session_factory: Factory, brand_id: str) -> None:
    _, post_id = seed_post(session_factory, brand_id)
    with session_factory.begin() as session:
        revision = make_revision(brand_id, post_id)
        session.add(revision)
        session.flush()
        revision_id = revision.id
    with pytest.raises(DBAPIError), session_factory.begin() as session:
        session.execute(
            text("UPDATE post_revision SET caption = 'outra' WHERE id = :id"), {"id": revision_id}
        )


def make_feedback(brand_id: str, post_id: str, revision_id: str, kind: str) -> FeedbackExample:
    return FeedbackExample(
        brand_id=brand_id,
        post_id=post_id,
        revision_id=revision_id,
        kind=kind,
        before_text="antes",
        created_by="t",
        created_at=NOW,
    )


def test_feedback_temporario_requires_expiry(session_factory: Factory, brand_id: str) -> None:
    _, post_id = seed_post(session_factory, brand_id)
    with session_factory.begin() as session:
        revision = make_revision(brand_id, post_id)
        session.add(revision)
        session.flush()
        revision_id = revision.id
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(make_feedback(brand_id, post_id, revision_id, "temporario"))
    with session_factory.begin() as session:
        feedback = make_feedback(brand_id, post_id, revision_id, "temporario")
        feedback.expires_at = NOW
        session.add(feedback)
        plain = make_feedback(brand_id, post_id, revision_id, "estilo")
        session.add(plain)
        session.flush()
        assert plain.is_active is True


def test_job_dedupe_key_unique_only_while_active(session_factory: Factory) -> None:
    with session_factory.begin() as session:
        session.add(make_job("queued"))
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(make_job("queued"))
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(make_job("running"))
    with session_factory.begin() as session:
        session.add(make_job("succeeded"))
        session.add(make_job("succeeded"))  # inativos nao colidem entre si
        session.add(make_job("queued", dedupe_key=None))
        session.add(make_job("queued", dedupe_key=None))  # sem chave nunca colide


def test_job_status_check(session_factory: Factory) -> None:
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(make_job("paused"))
