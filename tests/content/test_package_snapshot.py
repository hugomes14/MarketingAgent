"""Contexto de desenho fixado (`DesignSnapshot`): fotos e versao do kit congeladas no pedido.

Sem snapshot o pacote le a media ao vivo (percurso habitual, inalterado); com snapshot so
oferece as fotos fixadas (mesmo desativadas depois) e usa exatamente a versao fixada do kit.
"""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from content_rig import ContentRig, Factory, Script, build_rig, package_pass
from image_factory import make_jpeg, make_png
from test_package_core import MakeRig, copy_payload, specialized

from marketing_agent.clock import FixedClock
from marketing_agent.content.package import PackageContext, PackageRequest, PackageResult
from marketing_agent.content.package_costs import PackageTriggers
from marketing_agent.content.package_media import (
    SNAPSHOT_INVALID_REASON,
    DesignMedia,
    DesignSnapshot,
    ImageUnavailable,
    capture_design_snapshot,
)
from marketing_agent.db.models import Brand
from marketing_agent.llm import fake_scenarios
from marketing_agent.media.brand_kit import BrandKitInput
from marketing_agent.media.library import PhotoUpload


@pytest.fixture
def make_rig(session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> MakeRig:
    def make(script: Script | None = None, **options: object) -> ContentRig:
        return build_rig(
            session_factory,
            clock,
            brand_id,
            script or [],
            media_dir=tmp_path / "media",
            **options,  # type: ignore[arg-type]
        )

    return make


META = PhotoUpload(
    mostra="produto", descricao="Foto nova", origem="propria", tem_autorizacao_pessoas=False
)
NEW_KIT = BrandKitInput(cor_primaria="#AA0000", cor_secundaria="#FFFFFF", fonte="inter-bold")
TRIGGERS = PackageTriggers.single("eval_item:E1")


def capture(rig: ContentRig) -> DesignSnapshot:
    return capture_design_snapshot(rig.factory, rig.brand_id)


def media(rig: ContentRig) -> DesignMedia:
    return DesignMedia(rig.library, rig.kits)


def produce(rig: ContentRig, snapshot: DesignSnapshot | None) -> PackageResult:
    context = PackageContext(copy_payload(), None, snapshot)
    return specialized(rig).produce(PackageRequest(rig.brand_id, TRIGGERS, context))


def upload_new_photo(rig: ContentRig) -> str:
    return rig.library.upload_photo(rig.brand_id, make_jpeg(1100, 1400), META, "ana").id


def designer_request_text(rig: ContentRig) -> str:
    return rig.llm.request_text(2)  # 1.o pedido: Copywriter; 2.o: Designer


def test_capture_pins_the_active_photos_and_the_latest_kit_version(make_rig: MakeRig) -> None:
    rig = make_rig()
    snapshot = capture(rig)
    assert snapshot.photo_ids == frozenset(rig.library.photo_refs(rig.brand_id))
    assert snapshot.photo_ids == {rig.photo_id}
    assert snapshot.kit_version == rig.kits.active(rig.brand_id).version == 1  # type: ignore[union-attr]
    with pytest.raises(FrozenInstanceError):
        snapshot.kit_version = 2  # type: ignore[misc]


def test_capture_without_a_kit_pins_no_kit(make_rig: MakeRig) -> None:
    rig = make_rig(with_kit=False)
    assert capture(rig).kit_version is None


def test_capture_is_scoped_to_the_brand(make_rig: MakeRig, session_factory: Factory) -> None:
    rig = make_rig()
    with session_factory.begin() as session:
        other = Brand(name="Outra marca")
        session.add(other)
        session.flush()
        other_id = other.id
    assert capture_design_snapshot(rig.factory, other_id) == DesignSnapshot(frozenset(), None)


def test_without_a_snapshot_the_media_is_read_live_as_before(make_rig: MakeRig) -> None:
    rig = make_rig()
    new_id = upload_new_photo(rig)
    live = media(rig).load(rig.brand_id)
    assert set(live.photos) == {rig.photo_id, new_id}
    assert live.kit.version == 1


def test_photo_uploaded_after_capture_is_not_offered_to_the_designer(make_rig: MakeRig) -> None:
    rig = make_rig(package_pass())
    snapshot = capture(rig)
    new_id = upload_new_photo(rig)

    result = produce(rig, snapshot)

    assert result.outcome == "ok"
    text = designer_request_text(rig)
    assert rig.photo_id in text and new_id not in text
    assert set(media(rig).load(rig.brand_id, snapshot).photos) == {rig.photo_id}


def test_kit_saved_after_capture_is_not_used(make_rig: MakeRig) -> None:
    rig = make_rig(package_pass())
    snapshot = capture(rig)
    rig.kits.save(rig.brand_id, NEW_KIT, None, "ana")
    assert media(rig).load(rig.brand_id).kit.version == 2  # ao vivo: a nova versao

    result = produce(rig, snapshot)

    assert result.outcome == "ok"
    text = designer_request_text(rig)
    assert "#1F3A5F" in text and "#AA0000" not in text
    pinned = media(rig).load(rig.brand_id, snapshot)
    assert (pinned.kit.version, pinned.kit.cor_primaria) == (1, "#1F3A5F")
    assert pinned.logo_png[:8] == b"\x89PNG\r\n\x1a\n"


def test_photo_deactivated_after_capture_is_still_offered_and_readable(
    make_rig: MakeRig,
) -> None:
    rig = make_rig()
    visual = fake_scenarios.visual_draft(
        layout="foto_titulo", foto_id=rig.photo_id, tema_fundo=None, estilo=None
    )
    rig.llm.load(package_pass(visual=visual))
    snapshot = capture(rig)
    rig.library.deactivate(rig.brand_id, rig.photo_id, "ana")
    assert media(rig).load(rig.brand_id).photos == {}  # ao vivo: desapareceu

    pinned = media(rig).load(rig.brand_id, snapshot)
    assert set(pinned.photos) == {rig.photo_id}
    assert pinned.photo_bytes(rig.photo_id)[:3] == b"\xff\xd8\xff"

    result = produce(rig, snapshot)
    assert result.outcome == "ok" and rig.photo_id in designer_request_text(rig)
    assert [component.tipo for component in result.components][0] == "fotografia"


def test_pinned_photo_bytes_refuse_ids_outside_the_snapshot(make_rig: MakeRig) -> None:
    rig = make_rig()
    snapshot = capture(rig)
    new_id = upload_new_photo(rig)
    pinned = media(rig).load(rig.brand_id, snapshot)
    with pytest.raises(Exception) as caught:
        pinned.photo_bytes(new_id)
    assert new_id not in str(caught.value)


def test_snapshot_with_another_brands_photo_is_rejected(
    make_rig: MakeRig, session_factory: Factory
) -> None:
    rig = make_rig(package_pass())
    with session_factory.begin() as session:
        other = Brand(name="Outra marca")
        session.add(other)
        session.flush()
        other_id = other.id
    foreign = rig.library.upload_photo(other_id, make_jpeg(1150, 1450), META, "ana").id
    snapshot = replace(capture(rig), photo_ids=frozenset({rig.photo_id, foreign}))

    with pytest.raises(ImageUnavailable) as caught:
        media(rig).load(rig.brand_id, snapshot)
    assert caught.value.reason == SNAPSHOT_INVALID_REASON and foreign not in str(caught.value)
    result = produce(rig, snapshot)
    assert (result.outcome, result.reason) == ("failed", SNAPSHOT_INVALID_REASON)
    assert rig.generated_requests() == 0


def test_snapshot_pointing_at_a_missing_kit_version_fails_with_a_fixed_message(
    make_rig: MakeRig,
) -> None:
    rig = make_rig()
    snapshot = replace(capture(rig), kit_version=99)
    with pytest.raises(ImageUnavailable) as caught:
        media(rig).load(rig.brand_id, snapshot)
    assert "99" not in str(caught.value)


def test_snapshot_without_a_kit_never_picks_up_a_later_kit(
    make_rig: MakeRig, tmp_path: Path
) -> None:
    rig = make_rig(with_kit=False)
    snapshot = capture(rig)
    rig.kits.save(rig.brand_id, NEW_KIT, make_png(240, 240, alpha=True), "ana")
    with pytest.raises(ImageUnavailable) as caught:
        media(rig).load(rig.brand_id, snapshot)
    assert caught.value.reason == "Falta o kit de marca."


def test_brand_kit_get_version_returns_only_that_brand_and_version(make_rig: MakeRig) -> None:
    rig = make_rig()
    rig.kits.save(rig.brand_id, NEW_KIT, None, "ana")
    first = rig.kits.get_version(rig.brand_id, 1)
    assert first is not None and first.cor_primaria == "#1F3A5F"
    assert rig.kits.get_version(rig.brand_id, 2) == rig.kits.active(rig.brand_id)
    assert rig.kits.get_version(rig.brand_id, 3) is None
    assert rig.kits.get_version("outra-marca", 1) is None
