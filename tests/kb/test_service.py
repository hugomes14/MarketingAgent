"""Base de conhecimento: itens manuais, promocao do perfil, validade e auditoria."""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore, ArtifactType
from marketing_agent.clock import FixedClock
from marketing_agent.db.models import AuditLog, Brand, KbItem
from marketing_agent.kb.service import KbError, KbService, NewKbEntry

Factory = sessionmaker[Session]
TODAY = date(2026, 11, 5)  # data do relogio da fixture `clock`
PROFILE_PAYLOAD = {
    "topicos": {
        "o_que_vende": {"texto": "Pao artesanal.", "origem": "verificado"},
        "voz_e_tom": {"texto": "Caloroso e direto.", "origem": "suposto"},
    },
    "lacunas_abertas": [],
    "suposicoes": [],
}


@dataclass
class Rig:
    """Servico de KB com a loja de artefactos e a marca de teste."""

    service: KbService
    store: ArtifactStore
    factory: Factory
    brand_id: str

    def activate_profile(self) -> str:
        """Cria, submete e aprova um `perfil_marca`; devolve o id."""
        created = self.store.create(self.brand_id, ArtifactType.PERFIL_MARCA, PROFILE_PAYLOAD)
        self.store.submit(created.id, "ana")
        return self.store.approve_and_activate(created.id, "ana").id

    def audits(self) -> list[AuditLog]:
        """Todas as linhas de auditoria `kb.*`, por ordem."""
        with self.factory() as session:
            return list(
                session.scalars(
                    select(AuditLog).where(AuditLog.action.like("kb.%")).order_by(AuditLog.id)
                )
            )

    def item_count(self) -> int:
        """Numero de linhas em `kb_item`."""
        with self.factory() as session:
            return session.scalar(select(func.count()).select_from(KbItem)) or 0


@pytest.fixture
def rig(session_factory: Factory, clock: FixedClock, brand_id: str) -> Rig:
    store = ArtifactStore(session_factory, clock)
    return Rig(KbService(session_factory, store, clock), store, session_factory, brand_id)


def offer(**overrides: object) -> NewKbEntry:
    """Entrada manual valida (oferta); `overrides` ajustam campos."""
    data: dict[str, object] = {
        "kind": "oferta",
        "key": "pao-de-forma",
        "title": "Pao de forma",
        "body": "Pao de forma fresco todos os dias.",
        "valid_from": date(2026, 11, 1),
        "source_kind": "dito_pelo_responsavel",
    }
    return NewKbEntry.model_validate(data | overrides)


def price(**overrides: object) -> NewKbEntry:
    """Entrada de preco valida; `overrides` ajustam campos."""
    data: dict[str, object] = {
        "kind": "preco",
        "key": "pao-de-forma",
        "price_amount": Decimal("2.50"),
        "price_currency": "EUR",
        "valid_until": date(2026, 12, 31),
    }
    return offer(**(data | overrides))


def test_add_returns_entry_and_audits(rig: Rig) -> None:
    entry = rig.service.add(rig.brand_id, offer(), "ana")

    assert (entry.kind, entry.key, entry.is_active) == ("oferta", "pao-de-forma", True)
    assert entry.price_amount is None
    assert [a.action for a in rig.audits()] == ["kb.add"]


def test_add_price_requires_validity(rig: Rig) -> None:
    with pytest.raises(ValidationError, match="valid_until"):
        price(valid_until=None)
    with pytest.raises(ValidationError, match="price_amount"):
        price(price_amount=None)
    with pytest.raises(ValidationError, match="price_currency"):
        price(price_currency=None)
    with pytest.raises(ValidationError, match="price_currency"):
        price(price_currency="eur")
    with pytest.raises(ValidationError, match="valid_until"):
        price(valid_until=date(2026, 11, 1))
    entry = rig.service.add(rig.brand_id, price(), "ana")
    assert entry.price_amount == Decimal("2.50")
    assert entry.valid_until == date(2026, 12, 31)


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": ""},
        {"title": "x" * 121},
        {"body": ""},
        {"body": "x" * 2001},
        {"kind": "inexistente"},
        {"source_kind": "inventado"},
        {"key": ""},
        {"extra_field": 1},
    ],
)
def test_new_entry_rejects_invalid_data(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        offer(**overrides)


def test_add_duplicate_raises_kb_error(rig: Rig) -> None:
    rig.service.add(rig.brand_id, offer(), "ana")
    with pytest.raises(KbError, match="pao-de-forma"):
        rig.service.add(rig.brand_id, offer(), "ana")
    assert rig.item_count() == 1
    assert len(rig.audits()) == 1


def test_add_unknown_brand_raises_kb_error(rig: Rig) -> None:
    with pytest.raises(KbError, match="marca"):
        rig.service.add("00000000-0000-0000-0000-000000000000", offer(), "ana")


def test_promote_profile_topic_copies_origin(rig: Rig) -> None:
    artifact_id = rig.activate_profile()

    entry = rig.service.promote_profile_topic(rig.brand_id, "voz_e_tom", "ana")

    assert (entry.kind, entry.key, entry.title) == ("outro", "voz_e_tom", "voz e tom")
    assert entry.body == "Caloroso e direto."
    assert entry.source_kind == "suposto"
    assert entry.source_ref == f"perfil:{artifact_id}:voz_e_tom"
    assert entry.valid_from == TODAY
    assert entry.valid_until is None
    with rig.factory() as session:
        assert session.scalars(select(KbItem.created_by)).one() == "ana"


def test_promote_unknown_topic_raises(rig: Rig) -> None:
    with pytest.raises(KbError, match="perfil"):
        rig.service.promote_profile_topic(rig.brand_id, "voz_e_tom", "ana")  # sem perfil ativo
    rig.activate_profile()
    with pytest.raises(KbError, match="geografia"):
        rig.service.promote_profile_topic(rig.brand_id, "geografia", "ana")  # fora do perfil
    with pytest.raises(KbError, match="inventado"):
        rig.service.promote_profile_topic(rig.brand_id, "inventado", "ana")
    assert rig.item_count() == 0
    assert rig.audits() == []


def test_promote_is_idempotent(rig: Rig) -> None:
    rig.activate_profile()

    first = rig.service.promote_profile_topic(rig.brand_id, "o_que_vende", "ana")
    second = rig.service.promote_profile_topic(rig.brand_id, "o_que_vende", "ana")

    assert second == first
    assert rig.item_count() == 1
    assert [a.action for a in rig.audits()] == ["kb.promote"]


def test_deactivated_item_not_valid(rig: Rig) -> None:
    entry = rig.service.add(rig.brand_id, offer(), "ana")
    assert rig.service.valid_refs(rig.brand_id, [entry.id], TODAY) == {entry.id: entry}

    rig.service.deactivate(rig.brand_id, entry.id, "ana")

    assert rig.service.valid_refs(rig.brand_id, [entry.id], TODAY) == {}
    assert rig.service.list_active(rig.brand_id, TODAY) == []
    assert [a.action for a in rig.audits()] == ["kb.add", "kb.deactivate"]


def test_deactivate_is_idempotent_without_second_audit(rig: Rig) -> None:
    entry = rig.service.add(rig.brand_id, offer(), "ana")
    rig.service.deactivate(rig.brand_id, entry.id, "ana")
    rig.service.deactivate(rig.brand_id, entry.id, "ana")
    assert [a.action for a in rig.audits()] == ["kb.add", "kb.deactivate"]


def test_deactivate_other_brand_or_unknown_item_raises(rig: Rig, session_factory: Factory) -> None:
    entry = rig.service.add(rig.brand_id, offer(), "ana")
    with session_factory.begin() as session:
        other = Brand(name="Outra")
        session.add(other)
        session.flush()
        other_id = other.id
    with pytest.raises(KbError, match="outra marca|nao pertence"):
        rig.service.deactivate(other_id, entry.id, "ana")
    with pytest.raises(KbError, match="inexistente"):
        rig.service.deactivate(rig.brand_id, "00000000-0000-0000-0000-000000000000", "ana")
    assert rig.service.list_active(rig.brand_id, TODAY) == [entry]


def test_valid_refs_respects_date_window(rig: Rig) -> None:
    entry = rig.service.add(rig.brand_id, price(), "ana")  # 2026-11-01 .. 2026-12-31
    refs = [entry.id, "desconhecido"]

    assert rig.service.valid_refs(rig.brand_id, refs, date(2026, 10, 31)) == {}
    assert set(rig.service.valid_refs(rig.brand_id, refs, date(2026, 11, 1))) == {entry.id}
    assert set(rig.service.valid_refs(rig.brand_id, refs, date(2026, 12, 31))) == {entry.id}
    assert rig.service.valid_refs(rig.brand_id, refs, date(2027, 1, 1)) == {}
    assert rig.service.valid_refs(rig.brand_id, [], TODAY) == {}


def test_valid_refs_ignores_other_brand(rig: Rig, session_factory: Factory) -> None:
    entry = rig.service.add(rig.brand_id, offer(), "ana")
    with session_factory.begin() as session:
        other = Brand(name="Outra")
        session.add(other)
        session.flush()
        other_id = other.id
    assert rig.service.valid_refs(other_id, [entry.id], TODAY) == {}
    assert rig.service.list_active(other_id, TODAY) == []


def test_list_active_orders_and_filters_by_date(rig: Rig) -> None:
    later = rig.service.add(rig.brand_id, offer(key="b", valid_from=date(2026, 12, 1)), "ana")
    first = rig.service.add(rig.brand_id, offer(key="a"), "ana")
    assert rig.service.list_active(rig.brand_id, TODAY) == [first]
    assert rig.service.list_active(rig.brand_id, date(2026, 12, 1)) == [first, later]


def test_audit_without_text(rig: Rig) -> None:
    rig.activate_profile()
    added = rig.service.add(rig.brand_id, offer(body="texto livre confidencial"), "ana")
    rig.service.promote_profile_topic(rig.brand_id, "o_que_vende", "ana")
    rig.service.deactivate(rig.brand_id, added.id, "ana")

    audits = rig.audits()

    assert [a.action for a in audits] == ["kb.add", "kb.promote", "kb.deactivate"]
    assert audits[0].detail == {"kind": "oferta", "key": "pao-de-forma"}
    assert audits[1].detail == {"kind": "outro", "key": "o_que_vende"}
    assert audits[2].detail == {"kind": "oferta", "key": "pao-de-forma"}
    assert {a.actor for a in audits} == {"ana"}
    assert all(a.subject_type == "kb_item" for a in audits)
    assert all("confidencial" not in str(a.detail) for a in audits)


def test_one_day_offer_accepted_but_inverted_window_rejected() -> None:
    same_day = offer(valid_until=date(2026, 11, 1))
    assert same_day.valid_until == same_day.valid_from
    with pytest.raises(ValidationError, match="valid_until"):
        offer(valid_until=date(2026, 10, 31))
    with pytest.raises(ValidationError, match="valid_until"):
        price(valid_until=date(2026, 11, 1))  # preco exige janela positiva


def test_free_item_accepts_zero_price(rig: Rig) -> None:
    entry = rig.service.add(rig.brand_id, price(price_amount=Decimal("0")), "ana")
    assert entry.price_amount == Decimal("0.00")
    with pytest.raises(ValidationError, match="price_amount"):
        price(price_amount=Decimal("-1"))


def test_promote_error_never_leaks_profile_text(rig: Rig) -> None:
    secret = "SEGREDO-do-cliente-"
    payload = {
        "topicos": {"o_que_vende": {"texto": secret * 200, "origem": "verificado"}},
        "lacunas_abertas": [],
        "suposicoes": [],
    }
    created = rig.store.create(rig.brand_id, ArtifactType.PERFIL_MARCA, payload)
    rig.store.submit(created.id, "ana")
    rig.store.approve_and_activate(created.id, "ana")

    with pytest.raises(KbError, match="body: string_too_long") as caught:
        rig.service.promote_profile_topic(rig.brand_id, "o_que_vende", "ana")

    assert "SEGREDO" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert rig.item_count() == 0


def test_promote_on_later_day_supersedes_previous_version(
    rig: Rig, clock: FixedClock, session_factory: Factory
) -> None:
    rig.activate_profile()
    first = rig.service.promote_profile_topic(rig.brand_id, "o_que_vende", "ana")
    clock.advance(timedelta(days=1))

    second = rig.service.promote_profile_topic(rig.brand_id, "o_que_vende", "bia")

    assert second.id != first.id
    assert second.valid_from == TODAY + timedelta(days=1)
    assert rig.service.list_active(rig.brand_id, TODAY + timedelta(days=1)) == [second]
    with session_factory() as session:
        states = {i: active for i, active in session.execute(select(KbItem.id, KbItem.is_active))}
    assert states == {first.id: False, second.id: True}
    audits = rig.audits()
    assert [(a.action, a.subject_id, a.actor) for a in audits] == [
        ("kb.promote", first.id, "ana"),
        ("kb.promote", second.id, "bia"),
        ("kb.deactivate", first.id, "bia"),
    ]
    assert all(a.detail == {"kind": "outro", "key": "o_que_vende"} for a in audits)


def test_same_day_repeat_keeps_active_row_and_manual_add_touches_nothing(rig: Rig) -> None:
    rig.activate_profile()
    first = rig.service.promote_profile_topic(rig.brand_id, "o_que_vende", "ana")
    rig.service.promote_profile_topic(rig.brand_id, "o_que_vende", "ana")
    manual = rig.service.add(rig.brand_id, offer(kind="outro", key="o_que_vende"), "ana")

    assert {e.id for e in rig.service.list_active(rig.brand_id, TODAY)} == {first.id, manual.id}
    assert [a.action for a in rig.audits()] == ["kb.promote", "kb.add"]


def test_concurrent_promotes_create_one_row(rig: Rig) -> None:
    rig.activate_profile()
    barrier = threading.Barrier(8)

    def promote() -> str:
        barrier.wait()
        return rig.service.promote_profile_topic(rig.brand_id, "o_que_vende", "ana").id

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = [f.result() for f in [pool.submit(promote) for _ in range(8)]]

    assert len(set(ids)) == 1
    assert rig.item_count() == 1
    assert [a.action for a in rig.audits()] == ["kb.promote"]


def test_valid_refs_tolerates_malformed_refs(rig: Rig) -> None:
    assert rig.service.valid_refs(rig.brand_id, ["not-a-uuid", "", "'; drop"], TODAY) == {}
