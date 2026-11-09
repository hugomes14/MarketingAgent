"""Biblioteca real: contratos, armazenamento privado e transacoes Postgres."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import Barrier

import pytest
from image_factory import make_jpeg, make_logo_png, make_png
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import FixedClock
from marketing_agent.db.models import AuditLog, Brand, MediaAsset
from marketing_agent.images.codec import ImageRejected
from marketing_agent.media.ingest import normalize_logo, normalize_photo
from marketing_agent.media.library import AssetNotFound, MediaError, MediaLibrary, PhotoUpload
from marketing_agent.media.store import LocalMediaStore


@pytest.fixture
def library(
    session_factory: sessionmaker[Session], tmp_path: Path, clock: FixedClock
) -> MediaLibrary:
    return MediaLibrary(session_factory, LocalMediaStore(tmp_path / "media"), clock)


@pytest.fixture
def photo_meta() -> PhotoUpload:
    return PhotoUpload(
        mostra="produto",
        descricao="Produto privado",
        origem="propria",
        licenca_nota="Licenca privada",
        tem_autorizacao_pessoas=False,
    )


def test_upload_photo_stores_normalized_jpeg_and_row(
    library: MediaLibrary,
    session_factory: sessionmaker[Session],
    brand_id: str,
    photo_meta: PhotoUpload,
    clock: FixedClock,
) -> None:
    original = make_jpeg(1200, 1080, gps=True, orientation=6, icc=True)
    photo = library.upload_photo(brand_id, original, photo_meta, "owner")
    result = library.read_asset(brand_id, photo.id)
    assert result.mime == "image/jpeg"
    assert result.data != original
    with Image.open(BytesIO(result.data)) as image:
        assert image.format == "JPEG"
        assert image.size == (1080, 1200)
        assert not image.getexif()
        assert not {"icc_profile", "comment", "exif"} & image.info.keys()
    assert (photo.width, photo.height, photo.created_at) == (1080, 1200, clock.now())
    assert photo.descricao == "Produto privado"
    assert photo.origem == "propria"
    assert photo.licenca_nota == "Licenca privada"
    assert photo.mostra == "produto"
    assert not photo.tem_autorizacao_pessoas
    assert photo.is_active
    with session_factory() as session:
        row = session.get_one(MediaAsset, photo.id)
        assert row.sha256 == sha256(result.data).hexdigest()
        assert row.bytes == len(result.data)
        assert row.storage_backend == "local"
        assert row.storage_key == f"{brand_id}/{row.sha256[:2]}/{row.sha256}.jpg"
        assert (row.created_by, row.kind) == ("owner", "foto")
    with pytest.raises(FrozenInstanceError):
        photo.descricao = "alterada"
    with pytest.raises(FrozenInstanceError):
        result.data = b"alterados"


def test_same_file_twice_is_deduplicated(
    library: MediaLibrary,
    session_factory: sessionmaker[Session],
    brand_id: str,
    photo_meta: PhotoUpload,
    clock: FixedClock,
) -> None:
    data = make_png(1080, 1080)
    first = library.upload_photo(brand_id, data, photo_meta, "owner")
    library.deactivate(brand_id, first.id, "owner")
    clock.advance(timedelta(days=1))
    changed = photo_meta.model_copy(update={"descricao": "Outra", "origem": "cliente_autorizado"})
    second = library.upload_photo(brand_id, data, changed, "other")
    assert second.id == first.id
    assert second.descricao == first.descricao
    assert second.origem == first.origem
    assert second.created_at == first.created_at
    assert not second.is_active
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 2


def test_deduplication_is_per_brand(
    library: MediaLibrary,
    session_factory: sessionmaker[Session],
    brand_id: str,
    photo_meta: PhotoUpload,
) -> None:
    with session_factory.begin() as session:
        brand = Brand(name="Outra marca")
        session.add(brand)
        session.flush()
        other = brand.id
    data = make_png(1080, 1080)
    first = library.upload_photo(brand_id, data, photo_meta, "owner")
    second = library.upload_photo(other, data, photo_meta, "owner")
    assert first.id != second.id
    assert library.read_asset(brand_id, first.id) == library.read_asset(other, second.id)


def test_people_without_authorization_refused(photo_meta: PhotoUpload) -> None:
    values = photo_meta.model_dump() | {"mostra": "pessoas"}
    with pytest.raises(ValidationError):
        PhotoUpload.model_validate(values)
    accepted = PhotoUpload.model_validate(values | {"tem_autorizacao_pessoas": True})
    assert accepted.tem_autorizacao_pessoas


@pytest.mark.parametrize("description", ["", "   ", "x" * 301])
def test_description_limits(description: str, photo_meta: PhotoUpload) -> None:
    with pytest.raises(ValidationError):
        PhotoUpload.model_validate(photo_meta.model_dump() | {"descricao": description})


def test_description_is_stripped_and_maximum_accepted(photo_meta: PhotoUpload) -> None:
    meta = PhotoUpload.model_validate(
        photo_meta.model_dump() | {"descricao": "  " + "x" * 300 + "  "}
    )
    assert meta.descricao == "x" * 300


@pytest.mark.parametrize(
    "extra",
    [
        {"licenca_nota": "x" * 301},
        {"origem": "gerada"},
        {"mostra": "desconhecida"},
        {"extra": "privado"},
    ],
)
def test_photo_upload_rejects_invalid_metadata(
    extra: dict[str, str],
    photo_meta: PhotoUpload,
) -> None:
    with pytest.raises(ValidationError):
        PhotoUpload.model_validate(photo_meta.model_dump() | extra)


def test_deactivate_hides_from_refs_but_keeps_bytes(
    library: MediaLibrary,
    brand_id: str,
    photo_meta: PhotoUpload,
) -> None:
    photo = library.upload_photo(brand_id, make_png(1080, 1080), photo_meta, "owner")
    original = library.read_asset(brand_id, photo.id)
    ref = library.photo_refs(brand_id)[photo.id]
    assert (ref.mostra, ref.descricao, ref.tem_autorizacao_pessoas) == (
        "produto",
        "Produto privado",
        False,
    )
    with pytest.raises(FrozenInstanceError):
        ref.descricao = "alterada"
    library.deactivate(brand_id, photo.id, "owner")
    library.deactivate(brand_id, photo.id, "owner")
    assert library.photo_refs(brand_id) == {}
    assert library.list_photos(brand_id) == []
    assert library.list_photos(brand_id, active_only=False)[0].is_active is False
    assert library.read_asset(brand_id, photo.id) == original


def test_list_filters_by_subject(
    library: MediaLibrary,
    brand_id: str,
    photo_meta: PhotoUpload,
    clock: FixedClock,
) -> None:
    first = library.upload_photo(brand_id, make_png(1080, 1080), photo_meta, "owner")
    clock.advance(timedelta(seconds=1))
    other_meta = photo_meta.model_copy(update={"mostra": "ambiente"})
    second = library.upload_photo(brand_id, make_png(1081, 1080), other_meta, "owner")
    assert [photo.id for photo in library.list_photos(brand_id)] == [second.id, first.id]
    assert [photo.id for photo in library.list_photos(brand_id, "produto")] == [first.id]
    assert list(library.photo_refs(brand_id, limit=1)) == [second.id]
    assert library.photo_refs(brand_id, limit=0) == {}
    with pytest.raises(MediaError):
        library.photo_refs(brand_id, limit=-1)


@pytest.mark.parametrize("operation", ["read", "deactivate"])
def test_other_brand_asset_not_found(
    library: MediaLibrary,
    brand_id: str,
    photo_meta: PhotoUpload,
    operation: str,
) -> None:
    photo = library.upload_photo(brand_id, make_png(1080, 1080), photo_meta, "owner")
    for brand, asset in [("other-brand", photo.id), (brand_id, "missing")]:
        with pytest.raises(AssetNotFound):
            if operation == "read":
                library.read_asset(brand, asset)
            else:
                library.deactivate(brand, asset, "owner")
    assert library.list_photos(brand_id)[0].is_active
    assert library.list_photos("other-brand") == []
    assert library.photo_refs("other-brand") == {}


def test_store_asset_in_is_get_or_create(
    library: MediaLibrary,
    session_factory: sessionmaker[Session],
    brand_id: str,
) -> None:
    image = normalize_logo(make_logo_png())
    with session_factory.begin() as session:
        first = library.store_asset_in(
            session,
            brand_id,
            image,
            kind="logotipo",
            origem="propria",
            actor="owner",
        )
        second = library.store_asset_in(
            session,
            brand_id,
            image,
            kind="final",
            origem="composta",
            actor="other",
        )
        assert first == second
        assert session.get_one(MediaAsset, first).kind == "logotipo"
    assert library.list_photos(brand_id) == []
    assert library.photo_refs(brand_id) == {}
    assert library.read_asset(brand_id, first).mime == "image/png"
    assert library.read_asset(brand_id, first).data == image.data


def test_store_asset_in_does_not_commit_caller_transaction(
    library: MediaLibrary,
    session_factory: sessionmaker[Session],
    brand_id: str,
    tmp_path: Path,
) -> None:
    image = normalize_logo(make_logo_png())
    store = LocalMediaStore(tmp_path / "media")
    with session_factory() as session:
        asset_id = library.store_asset_in(
            session,
            brand_id,
            image,
            kind="logotipo",
            origem="propria",
            actor="owner",
        )
        key = session.get_one(MediaAsset, asset_id).storage_key
        assert store.read(key) == image.data
        session.rollback()
    assert store.read(key) == image.data
    with session_factory() as session:
        assert session.get(MediaAsset, asset_id) is None
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_unrelated_integrity_error_is_not_swallowed(
    library: MediaLibrary,
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        library.store_asset_in(
            session,
            "missing-brand",
            normalize_logo(make_logo_png()),
            kind="logotipo",
            origem="propria",
            actor="owner",
        )


def test_audit_without_text(
    library: MediaLibrary,
    session_factory: sessionmaker[Session],
    brand_id: str,
    photo_meta: PhotoUpload,
    clock: FixedClock,
) -> None:
    photo = library.upload_photo(brand_id, make_png(1080, 1080), photo_meta, "owner")
    library.deactivate(brand_id, photo.id, "owner")
    with session_factory() as session:
        rows = session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
        digest = session.get_one(MediaAsset, photo.id).sha256
        assert [row.action for row in rows] == ["media.upload", "media.deactivate"]
        for row in rows:
            assert row.detail == {"kind": "foto", "mostra": "produto", "sha256": digest[:12]}
            assert (row.actor, row.subject_id, row.subject_type, row.at) == (
                "owner",
                photo.id,
                "media_asset",
                clock.now(),
            )


def test_invalid_photo_keeps_database_empty(
    library: MediaLibrary,
    session_factory: sessionmaker[Session],
    brand_id: str,
    photo_meta: PhotoUpload,
) -> None:
    with pytest.raises(ImageRejected):
        library.upload_photo(brand_id, b"private corrupt input", photo_meta, "owner")
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_concurrent_duplicate_uploads_preserve_first_metadata(
    library: MediaLibrary,
    session_factory: sessionmaker[Session],
    brand_id: str,
    photo_meta: PhotoUpload,
) -> None:
    barrier = Barrier(2)
    data = make_png(1080, 1080)

    def upload(index: int) -> str:
        barrier.wait(timeout=10)
        meta = photo_meta.model_copy(update={"descricao": f"original-{index}"})
        return library.upload_photo(brand_id, data, meta, "owner").id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(upload, range(2)))
    assert ids[0] == ids[1]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1
        assert session.get_one(MediaAsset, ids[0]).descricao in {"original-0", "original-1"}


def test_photo_refs_never_exceeds_fifty(
    library: MediaLibrary,
    brand_id: str,
    photo_meta: PhotoUpload,
    clock: FixedClock,
) -> None:
    for index in range(52):
        meta = photo_meta.model_copy(update={"descricao": str(index)})
        library.upload_photo(brand_id, make_png(1080 + index, 1080), meta, "owner")
        clock.advance(timedelta(seconds=1))
    refs = library.photo_refs(brand_id, limit=100)
    assert len(refs) == 50
    assert [ref.descricao for ref in refs.values()] == [str(index) for index in range(51, 1, -1)]


def test_upload_deduplicates_normalized_bytes_not_source_metadata(
    library: MediaLibrary,
    brand_id: str,
    photo_meta: PhotoUpload,
) -> None:
    first = library.upload_photo(brand_id, make_jpeg(1080, 1080), photo_meta, "owner")
    second = library.upload_photo(brand_id, make_jpeg(1080, 1080, gps=True), photo_meta, "owner")
    assert second.id == first.id


def test_non_photo_collision_is_refused_without_metadata_change(
    library: MediaLibrary,
    session_factory: sessionmaker[Session],
    brand_id: str,
    photo_meta: PhotoUpload,
) -> None:
    source = make_png(1080, 1080)
    with session_factory.begin() as session:
        asset_id = library.store_asset_in(
            session,
            brand_id,
            normalize_photo(source),
            kind="final",
            origem="composta",
            actor="owner",
        )
    with pytest.raises(MediaError):
        library.upload_photo(brand_id, source, photo_meta, "owner")
    with pytest.raises(AssetNotFound):
        library.deactivate(brand_id, asset_id, "owner")
    assert library.read_asset(brand_id, asset_id).mime == "image/jpeg"
    with session_factory() as session:
        row = session.get_one(MediaAsset, asset_id)
        assert (row.kind, row.descricao, row.is_active) == ("final", None, True)
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_store_asset_in_reuses_existing_photo_but_refuses_new_metadata_less_photo(
    library: MediaLibrary,
    session_factory: sessionmaker[Session],
    brand_id: str,
    photo_meta: PhotoUpload,
) -> None:
    source = make_png(1080, 1080)
    photo = library.upload_photo(brand_id, source, photo_meta, "owner")
    with session_factory.begin() as session:
        reused = library.store_asset_in(
            session,
            brand_id,
            normalize_photo(source),
            kind="foto",
            origem="propria",
            actor="other",
        )
        assert reused == photo.id
        with pytest.raises(MediaError):
            library.store_asset_in(
                session,
                brand_id,
                normalize_photo(make_png(1081, 1080)),
                kind="foto",
                origem="propria",
                actor="other",
            )
        assert session.get_one(MediaAsset, photo.id).descricao == "Produto privado"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1
