"""Fonte oficial, integridade antes do cache e composicao deterministica."""

from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from hashlib import sha256
from importlib.resources import files
from pathlib import Path

import pytest
from PIL import ImageFont

from marketing_agent.images import fonts
from marketing_agent.images.fonts import BRAND_FONTS, FontIntegrityError, load_font

FONT_HASH = "288316099b1e0a47a4716d159098005eef7c0066921f34e3200393dbdb01947f"
LICENSE_HASH = "262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a"


@pytest.fixture(autouse=True)
def empty_font_caches() -> Iterator[None]:
    load_font.cache_clear()
    fonts._font_bytes.cache_clear()
    yield
    load_font.cache_clear()
    fonts._font_bytes.cache_clear()


def test_vendored_font_matches_recorded_hash() -> None:
    font = BRAND_FONTS["inter-bold"]
    assert font.name == "inter-bold"
    assert font.sha256 == FONT_HASH
    data = files("marketing_agent.assets").joinpath(font.resource).read_bytes()
    assert sha256(data).hexdigest() == FONT_HASH
    with pytest.raises(FrozenInstanceError):
        font.sha256 = "alterado"
    with pytest.raises(TypeError):
        BRAND_FONTS["other"] = font


def test_font_license_shipped() -> None:
    license_data = files("marketing_agent.assets").joinpath("fonts/OFL.txt").read_bytes()
    assert sha256(license_data).hexdigest() == LICENSE_HASH
    assert b"SIL OPEN FONT LICENSE Version 1.1" in license_data


def test_tampered_font_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resource = BRAND_FONTS["inter-bold"].resource
    original = files("marketing_agent.assets").joinpath(resource).read_bytes()
    local = tmp_path / resource
    local.parent.mkdir(parents=True)
    local.write_bytes(b"private tampered font bytes")
    monkeypatch.setattr(fonts, "files", lambda package: tmp_path)
    for size in (32, 64):
        with pytest.raises(FontIntegrityError, match="integridade") as error:
            load_font("inter-bold", size)
        assert "private" not in str(error.value)
    # Falhar nunca coloca bytes no cache: corrigir o recurso permite tentar de novo.
    local.write_bytes(original)
    first = load_font("inter-bold", 32)
    assert first.layout_engine == ImageFont.Layout.BASIC
    local.unlink()
    # Depois da validacao, tamanhos novos usam os mesmos bytes ja verificados.
    assert load_font("inter-bold", 32) is first
    assert load_font("inter-bold", 64).size == 64


def test_font_sizes_use_basic_layout() -> None:
    first = load_font("inter-bold", 28)
    second = load_font("inter-bold", 42)
    assert first is load_font("inter-bold", 28)
    assert first is not second
    assert first.layout_engine == second.layout_engine == ImageFont.Layout.BASIC
    assert first.getbbox("Marketing Ágil") == load_font("inter-bold", 28).getbbox("Marketing Ágil")


@pytest.mark.parametrize("size", [0, -1, 1.5, True])
def test_invalid_font_size_refused(size: int) -> None:
    load_font("inter-bold", 1)
    with pytest.raises(ValueError):
        load_font("inter-bold", size)


def test_unknown_font_refused() -> None:
    with pytest.raises(ValueError):
        load_font("../../private", 32)
