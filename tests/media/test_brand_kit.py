"""Kits imutaveis com contraste real, isolamento e serializacao Postgres."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from math import floor
from pathlib import Path
from threading import Event
from time import monotonic

import pytest
from image_factory import make_logo_png, make_png
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import FixedClock
from marketing_agent.db.models import AuditLog, Brand, BrandKit, MediaAsset
from marketing_agent.images.codec import ImageRejected
from marketing_agent.media.brand_kit import (
    MIN_TITLE_CONTRAST,
    BrandKitInput,
    BrandKitService,
    KitError,
    contrast_ratio,
)
from marketing_agent.media.ingest import normalize_logo
from marketing_agent.media.library import MediaLibrary
from marketing_agent.media.store import LocalMediaStore


@pytest.fixture
def library(
    session_factory: sessionmaker[Session], tmp_path: Path, clock: FixedClock
) -> MediaLibrary:
    return MediaLibrary(session_factory, LocalMediaStore(tmp_path / "media"), clock)


@pytest.fixture
def service(
    session_factory: sessionmaker[Session], library: MediaLibrary, clock: FixedClock
) -> BrandKitService:
    return BrandKitService(session_factory, library, clock)


@pytest.fixture
def kit_input() -> BrandKitInput:
    return BrandKitInput(cor_primaria="#000000", cor_secundaria="#ffffff")


def test_contrast_known_pairs() -> None:
    assert MIN_TITLE_CONTRAST == 4.5
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21)
    assert contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21)
    assert contrast_ratio("#777777", "#FFFFFF") == pytest.approx(4.478089, abs=0.000001)
    assert contrast_ratio("#aBcDeF", "#ABCDEF") == 1
    assert contrast_ratio("#767676", "#FFFFFF") > MIN_TITLE_CONTRAST


@pytest.mark.parametrize("color", ["#777777", "#FFFFFF", "#808080"])
def test_kit_refuses_low_contrast(color: str) -> None:
    ratio = contrast_ratio(color, "#FFFFFF")
    # O valor apresentado trunca (nunca arredonda para cima, ao lado do minimo de 4.5).
    with pytest.raises(ValidationError, match=f"{floor(ratio * 100) / 100:.2f}"):
        BrandKitInput(cor_primaria=color, cor_secundaria="#FFFFFF")


def test_contrast_just_below_minimum_is_not_displayed_as_the_minimum() -> None:
    assert 4.49 < contrast_ratio("#767776", "#FFFFFF") < MIN_TITLE_CONTRAST
    with pytest.raises(ValidationError) as error:
        BrandKitInput(cor_primaria="#767776", cor_secundaria="#FFFFFF")
    assert "Contraste 4.49:" in str(error.value)
    assert "4.50" not in str(error.value)


def test_kit_colors_normalized_uppercase() -> None:
    kit = BrandKitInput(cor_primaria="#abcdef", cor_secundaria="#010a0b")
    assert (kit.cor_primaria, kit.cor_secundaria, kit.fonte) == ("#ABCDEF", "#010A0B", "inter-bold")
    with pytest.raises(ValidationError):
        kit.cor_primaria = "#FFFFFF"


@pytest.mark.parametrize(
    "color", ["#000", "#GG0000", "ffffff", "#1234567", " #000000", "#000000\n"]
)
def test_invalid_colors_refused(color: str) -> None:
    with pytest.raises(ValidationError):
        BrandKitInput(cor_primaria=color, cor_secundaria="#FFFFFF")
    with pytest.raises(ValueError):
        contrast_ratio(color, "#FFFFFF")


def test_unregistered_font_and_extra_fields_refused(kit_input: BrandKitInput) -> None:
    for extra in ({"fonte": "system-font"}, {"private": "value"}):
        with pytest.raises(ValidationError):
            BrandKitInput.model_validate(kit_input.model_dump() | extra)


def test_first_kit_requires_logo(
    service: BrandKitService,
    kit_input: BrandKitInput,
    brand_id: str,
    session_factory: sessionmaker[Session],
) -> None:
    assert service.active(brand_id) is None
    assert service.history(brand_id) == []
    with pytest.raises(KitError, match="logótipo"):
        service.save(brand_id, kit_input, None, "owner")
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(BrandKit)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_new_version_reuses_logo_when_absent(
    service: BrandKitService,
    kit_input: BrandKitInput,
    brand_id: str,
    session_factory: sessionmaker[Session],
    clock: FixedClock,
) -> None:
    first = service.save(brand_id, kit_input, make_logo_png(), "owner")
    changed = BrandKitInput(cor_primaria="#abcdef", cor_secundaria="#010a0b")
    second = service.save(brand_id, changed, None, "other")
    assert first.id != second.id
    assert (first.version, second.version) == (1, 2)
    assert first.logo_asset_id == second.logo_asset_id
    assert (first.cor_primaria, second.cor_primaria) == ("#000000", "#ABCDEF")
    assert first.contrast == 21
    assert second.contrast == contrast_ratio("#ABCDEF", "#010A0B")
    assert first.created_at == second.created_at == clock.now()
    assert first.fonte == second.fonte == "inter-bold"
    with pytest.raises(FrozenInstanceError):
        first.version = 99
    with session_factory() as session:
        assert session.get_one(BrandKit, first.id).cor_primaria == "#000000"
        assert session.get_one(BrandKit, second.id).created_by == "other"
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 1


def test_active_is_highest_version(
    service: BrandKitService,
    kit_input: BrandKitInput,
    brand_id: str,
) -> None:
    first = service.save(brand_id, kit_input, make_logo_png(), "owner")
    second = service.save(brand_id, kit_input, None, "owner")
    assert service.active(brand_id) == second
    assert service.history(brand_id) == [second, first]
    assert service.active("missing") is None
    assert service.history("missing") == []


def test_new_logo_preserves_previous_bytes(
    service: BrandKitService,
    kit_input: BrandKitInput,
    brand_id: str,
) -> None:
    first = service.save(brand_id, kit_input, make_logo_png(), "owner")
    second = service.save(brand_id, kit_input, make_png(80, 80, alpha=True), "owner")
    assert first.logo_asset_id != second.logo_asset_id
    assert service.logo_png(brand_id, first) == normalize_logo(make_logo_png()).data
    assert service.logo_png(brand_id, second) == normalize_logo(make_png(80, 80, alpha=True)).data


def test_missing_or_other_brand_kit_is_safe(
    service: BrandKitService,
    kit_input: BrandKitInput,
    brand_id: str,
) -> None:
    with pytest.raises(KitError):
        service.save("missing-private-brand", kit_input, make_logo_png(), "owner")
    kit = service.save(brand_id, kit_input, make_logo_png(), "owner")
    errors = []
    for brand, view in (("other-private-brand", kit), (brand_id, replace(kit, id="missing"))):
        with pytest.raises(KitError) as error:
            service.logo_png(brand, view)
        errors.append(str(error.value))
    assert errors[0] == errors[1]
    assert "private" not in errors[0]
    # A vista recebida nao pode redirecionar a leitura a um ativo arbitrario.
    assert service.logo_png(brand_id, replace(kit, logo_asset_id="missing")) == service.logo_png(
        brand_id, kit
    )


def test_brand_scoped_logo_and_history(
    service: BrandKitService,
    kit_input: BrandKitInput,
    brand_id: str,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        other = Brand(name="Other")
        session.add(other)
        session.flush()
        other_id = other.id
    first = service.save(brand_id, kit_input, make_logo_png(), "owner")
    second = service.save(other_id, kit_input, make_png(80, 80, alpha=True), "owner")
    assert first.version == second.version == 1
    assert service.history(brand_id) == [first]
    assert service.history(other_id) == [second]
    with pytest.raises(KitError):
        service.logo_png(brand_id, second)


def test_audit_contains_only_kit_version_and_colors(
    service: BrandKitService,
    kit_input: BrandKitInput,
    brand_id: str,
    session_factory: sessionmaker[Session],
    clock: FixedClock,
) -> None:
    kit = service.save(brand_id, kit_input, make_logo_png(), "owner")
    with session_factory() as session:
        audit = session.scalars(select(AuditLog).where(AuditLog.action == "brand_kit.save")).one()
        assert audit.detail == {
            "version": 1,
            "cor_primaria": "#000000",
            "cor_secundaria": "#FFFFFF",
        }
        assert (audit.actor, audit.subject_type, audit.subject_id, audit.at) == (
            "owner",
            "brand_kit",
            kit.id,
            clock.now(),
        )


def test_invalid_logo_is_atomic(
    service: BrandKitService,
    kit_input: BrandKitInput,
    brand_id: str,
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(ImageRejected):
        service.save(brand_id, kit_input, b"private corrupt logo", "owner")
    with session_factory() as session:
        for model in (BrandKit, MediaAsset, AuditLog):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_logo_bytes_matching_a_non_logo_asset_are_refused_without_changes(
    service: BrandKitService,
    library: MediaLibrary,
    kit_input: BrandKitInput,
    brand_id: str,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        asset_id = library.store_asset_in(
            session,
            brand_id,
            normalize_logo(make_logo_png()),
            kind="final",
            origem="composta",
            actor="original",
        )
    with pytest.raises(KitError) as error:
        service.save(brand_id, kit_input, make_logo_png(), "other")
    assert str(error.value) == "O ativo escolhido não é um logótipo da marca."
    with session_factory() as session:
        asset = session.get_one(MediaAsset, asset_id)
        assert (asset.kind, asset.origem, asset.created_by) == ("final", "composta", "original")
        assert session.scalar(select(func.count()).select_from(BrandKit)) == 0
        # So a auditoria do ativo preparado: a tentativa recusada nao deixou rasto.
        assert session.scalars(select(AuditLog.action)).all() == ["media.upload"]


def test_inherited_logo_must_still_be_a_brand_logo(
    service: BrandKitService,
    kit_input: BrandKitInput,
    brand_id: str,
    session_factory: sessionmaker[Session],
) -> None:
    first = service.save(brand_id, kit_input, make_logo_png(), "owner")
    with session_factory.begin() as session:
        # Referencia corrompida: o servico nao pode propagar um ativo que nao seja logotipo.
        session.execute(
            text("UPDATE media_asset SET kind = 'final' WHERE id = :id"),
            {"id": first.logo_asset_id},
        )
    with pytest.raises(KitError, match="não é um logótipo"):
        service.save(brand_id, kit_input, None, "owner")
    assert [kit.version for kit in service.history(brand_id)] == [1]


@pytest.mark.parametrize("has_previous", [False, True])
def test_concurrent_saves_get_distinct_versions(
    service: BrandKitService,
    kit_input: BrandKitInput,
    brand_id: str,
    session_factory: sessionmaker[Session],
    has_previous: bool,
) -> None:
    if has_previous:
        service.save(brand_id, kit_input, make_logo_png(), "owner")
    with ThreadPoolExecutor(max_workers=2) as pool:
        with session_factory.begin() as locker:
            locker.scalar(select(Brand).where(Brand.id == brand_id).with_for_update())
            futures = [
                pool.submit(service.save, brand_id, kit_input, make_logo_png(), "owner")
                for _ in range(2)
            ]
            deadline = monotonic() + 10
            waiting = 0
            # As duas transacoes tem de estar bloqueadas no Postgres antes de libertar
            # a linha: uma barreira antes da normalizacao nao demonstra sobreposicao SQL.
            while waiting < 2 and monotonic() < deadline:
                # pg_stat_activity preserva a fotografia por transacao ate a limpar.
                locker.execute(text("SELECT pg_stat_clear_snapshot()"))
                waiting = locker.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                        "AND query LIKE '%FOR UPDATE%' AND pid <> pg_backend_pid()"
                    )
                )
                if waiting < 2:
                    Event().wait(0.01)
            assert waiting == 2, "As duas gravacoes nao chegaram ao bloqueio por marca"
        results = [future.result(timeout=10) for future in futures]
    offset = 1 if has_previous else 0
    assert sorted(kit.version for kit in results) == [1 + offset, 2 + offset]
    assert [kit.version for kit in service.history(brand_id)] == list(range(2 + offset, 0, -1))
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(BrandKit)) == 2 + offset
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "brand_kit.save")
            )
            == 2 + offset
        )
