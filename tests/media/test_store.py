"""Contrato do armazenamento imutavel; usa apenas ficheiros temporarios, sem Postgres."""

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, cast

import pytest

from marketing_agent.media.store import LocalMediaStore, MediaStore, MediaStoreError

ABC_SHA = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
ABC_KEY = f"marca/{ABC_SHA[:2]}/{ABC_SHA}.jpg"


def test_put_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store: MediaStore = LocalMediaStore(tmp_path / "media")
    first = store.put("marca", b"abc", "jpg")
    path = tmp_path / "media" / first.key
    inode = path.stat().st_ino
    second = store.put("marca", b"abc", "jpg")
    assert first == second
    assert (first.key, first.sha256, first.size) == (ABC_KEY, ABC_SHA, 3)
    assert store.read(first.key) == b"abc"
    assert path.stat().st_ino == inode
    assert list(path.parent.iterdir()) == [path]


def test_brands_and_extensions_have_separate_keys(tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path)
    objects = [
        store.put(brand, b"abc", ext)
        for brand, ext in (("marca", "jpg"), ("outra", "jpg"), ("marca", "png"))
    ]
    assert len({item.key for item in objects}) == 3
    assert all(store.read(item.key) == b"abc" for item in objects)


def test_put_never_overwrites_different_bytes(tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path)
    path = tmp_path / ABC_KEY
    path.parent.mkdir(parents=True)
    path.write_bytes(b"conteudo anterior")
    with pytest.raises(MediaStoreError):
        store.put("marca", b"abc", "jpg")
    assert path.read_bytes() == b"conteudo anterior"
    assert list(path.parent.iterdir()) == [path]


def test_created_directories_and_files_are_private(tmp_path: Path) -> None:
    root = tmp_path / "media"
    item = LocalMediaStore(root).put("marca", b"abc", "jpg")
    path = root / item.key
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for directory in (root, path.parent.parent, path.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "key",
    [
        "/etc/passwd",
        "../fora",
        "marca/../../fora",
        "",
        ".",
        "marca\\..\\fora",
        "marca/ba/invalid.jpg",
        "a\x00b",
    ],
)
def test_read_rejects_keys_outside_root(tmp_path: Path, key: str) -> None:
    with pytest.raises(MediaStoreError):
        LocalMediaStore(tmp_path).read(key)


@pytest.mark.parametrize("brand", ["", ".", "..", "../fora", "/tmp/fora", "a/b", "a\\b", "a\x00b"])
def test_put_rejects_unsafe_brand(tmp_path: Path, brand: str) -> None:
    with pytest.raises(MediaStoreError):
        LocalMediaStore(tmp_path).put(brand, b"abc", "jpg")
    assert not list(tmp_path.iterdir())


def test_put_rejects_unknown_extension(tmp_path: Path) -> None:
    extension = cast(Literal["jpg", "png"], "../jpg")
    with pytest.raises(MediaStoreError):
        LocalMediaStore(tmp_path).put("marca", b"abc", extension)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("component", ["marca", "prefixo", "ficheiro"])
def test_symlinks_cannot_escape_root(tmp_path: Path, component: str) -> None:
    root = tmp_path / "media"
    outside = tmp_path / "fora"
    outside.mkdir()
    secret = outside / "privado"
    secret.write_bytes(b"fora do armazenamento")
    store = LocalMediaStore(root)
    target = root / ABC_KEY
    if component == "marca":
        (root / "marca").symlink_to(outside, target_is_directory=True)
    elif component == "prefixo":
        (root / "marca").mkdir()
        target.parent.symlink_to(outside, target_is_directory=True)
    else:
        target.parent.mkdir(parents=True)
        target.symlink_to(secret)
    with pytest.raises(MediaStoreError):
        store.read(ABC_KEY)
    with pytest.raises(MediaStoreError):
        store.put("marca", b"abc", "jpg")
    assert secret.read_bytes() == b"fora do armazenamento"
    assert list(outside.iterdir()) == [secret]


def test_missing_object_has_context_without_content(tmp_path: Path) -> None:
    with pytest.raises(MediaStoreError) as error:
        LocalMediaStore(tmp_path).read(ABC_KEY)
    assert error.value.__cause__ is not None
    assert "ler" in str(error.value).lower()


def test_concurrent_puts_publish_one_complete_object(tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: store.put("marca", b"abc", "jpg"), range(24)))
    assert all(result == results[0] for result in results)
    assert store.read(results[0].key) == b"abc"
    assert list((tmp_path / ABC_KEY).parent.iterdir()) == [tmp_path / ABC_KEY]


def test_write_failure_cleans_temporary_and_preserves_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_link(*args: str, **kwargs: int | bool) -> None:
        raise OSError("falha de disco simulada")

    monkeypatch.setattr(os, "link", fail_link)
    with pytest.raises(MediaStoreError) as error:
        LocalMediaStore(tmp_path).put("marca", b"abc", "jpg")
    assert isinstance(error.value.__cause__, OSError)
    assert list((tmp_path / ABC_KEY).parent.iterdir()) == []


@pytest.mark.parametrize("kind", ["pasta", "fifo"])
def test_read_rejects_non_regular_objects(tmp_path: Path, kind: str) -> None:
    path = tmp_path / ABC_KEY
    path.parent.mkdir(parents=True)
    if kind == "pasta":
        path.mkdir()
    else:
        os.mkfifo(path)
    with pytest.raises(MediaStoreError):
        LocalMediaStore(tmp_path).read(ABC_KEY)


def test_root_symlink_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "fora"
    outside.mkdir()
    root = tmp_path / "media"
    root.symlink_to(outside, target_is_directory=True)
    store = LocalMediaStore(root)
    with pytest.raises(MediaStoreError):
        store.put("marca", b"abc", "jpg")
    with pytest.raises(MediaStoreError):
        store.read(ABC_KEY)
    assert list(outside.iterdir()) == []


def test_read_rejects_inconsistent_hash_prefix(tmp_path: Path) -> None:
    path = tmp_path / "marca" / "00" / f"{ABC_SHA}.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"abc")
    with pytest.raises(MediaStoreError):
        LocalMediaStore(tmp_path).read(f"marca/00/{ABC_SHA}.jpg")
