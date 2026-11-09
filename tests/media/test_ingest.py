"""Contrato de ingestao: limites, orientacao, privacidade e bytes finais."""

import struct
from dataclasses import FrozenInstanceError
from hashlib import sha256
from io import BytesIO
from unittest.mock import patch

import pytest
from image_factory import make_jpeg, make_logo_png, make_png
from PIL import Image, ImageCms, PngImagePlugin

from marketing_agent.images.codec import ImageRejected
from marketing_agent.media.ingest import normalize_logo, normalize_photo


def test_gps_and_exif_removed() -> None:
    original = make_jpeg(1200, 1080, gps=True, icc=True)
    with Image.open(BytesIO(original)) as source:
        assert source.getexif().get_ifd(34853)
        assert source.info["icc_profile"]
        assert source.info["comment"]
    normalized = normalize_photo(original)
    with Image.open(BytesIO(normalized.data)) as saved:
        assert saved.format == "JPEG"
        assert saved.mode == "RGB"
        assert not saved.getexif()
        assert not {"exif", "icc_profile", "comment"} & saved.info.keys()


def test_exif_orientation_applied_before_strip() -> None:
    normalized = normalize_photo(make_jpeg(1600, 1200, orientation=6))
    assert (normalized.width, normalized.height) == (1200, 1600)
    with Image.open(BytesIO(normalized.data)) as image:
        assert not image.getexif()
        assert image.getpixel((600, 200)) == pytest.approx((220, 30, 20), abs=3)
        assert image.getpixel((600, 1400)) == pytest.approx((20, 40, 220), abs=3)


@pytest.mark.parametrize("size", [(1079, 1080), (1080, 1079)])
def test_small_side_under_1080_rejected(size: tuple[int, int]) -> None:
    with pytest.raises(ImageRejected):
        normalize_photo(make_png(*size))


def test_minimum_side_is_inclusive() -> None:
    normalized = normalize_photo(make_png(1080, 1080))
    assert (normalized.width, normalized.height, normalized.mime) == (1080, 1080, "image/jpeg")


def test_over_15_mb_rejected_before_decode() -> None:
    with (
        patch.object(
            Image, "open", side_effect=AssertionError("abriu upload demasiado grande")
        ) as opened,
        pytest.raises(ImageRejected),
    ):
        normalize_photo(b"x" * (15 * 1024 * 1024 + 1))
    opened.assert_not_called()


def test_exactly_15_mb_is_accepted() -> None:
    data = make_png(1080, 1080)
    normalized = normalize_photo(data + b"\x00" * (15 * 1024 * 1024 - len(data)))
    assert normalized.mime == "image/jpeg"


@pytest.mark.parametrize(
    ("size", "expected"),
    [((5000, 1250), (4096, 1024)), ((1250, 5000), (1024, 4096)), ((4096, 1080), (4096, 1080))],
)
def test_large_photo_downscaled_to_4096(size: tuple[int, int], expected: tuple[int, int]) -> None:
    normalized = normalize_photo(make_png(*size))
    assert (normalized.width, normalized.height) == expected
    with Image.open(BytesIO(normalized.data)) as image:
        assert image.size == expected


def test_output_is_deterministic_and_sha_stable() -> None:
    data = make_jpeg(1200, 1080, gps=True, orientation=6, icc=True)
    first = normalize_photo(data)
    second = normalize_photo(data)
    assert first == second
    assert first.sha256 == sha256(first.data).hexdigest()
    assert len(first.sha256) == 64
    with pytest.raises(FrozenInstanceError):
        first.width = 1


def test_photo_alpha_is_composited_on_white() -> None:
    result = normalize_photo(make_png(1080, 1080, alpha=True))
    with Image.open(BytesIO(result.data)) as image:
        assert image.getpixel((500, 500)) == pytest.approx((147, 167, 187), abs=2)


def test_cmyk_jpeg_is_normalized_to_rgb() -> None:
    output = BytesIO()
    Image.new("CMYK", (1080, 1080), (0, 255, 255, 0)).save(output, "JPEG")
    normalized = normalize_photo(output.getvalue())
    with Image.open(BytesIO(normalized.data)) as image:
        assert image.mode == "RGB"
        assert image.getpixel((500, 500)) == pytest.approx((255, 0, 0), abs=2)


def test_static_webp_photo_is_accepted() -> None:
    output = BytesIO()
    Image.new("RGB", (1080, 1080), "blue").save(output, "WEBP", lossless=True)
    normalized = normalize_photo(output.getvalue())
    assert normalized.mime == "image/jpeg"
    with Image.open(BytesIO(normalized.data)) as image:
        assert image.getpixel((500, 500)) == pytest.approx((0, 0, 255), abs=2)


@pytest.mark.parametrize("data", [make_jpeg(32, 24), make_png(32, 24), b"privado"])
def test_logo_requires_png_with_alpha(data: bytes) -> None:
    with pytest.raises(ImageRejected):
        normalize_logo(data)


def test_logo_over_2_mb_rejected_before_decode() -> None:
    with (
        patch.object(
            Image, "open", side_effect=AssertionError("abriu logotipo demasiado grande")
        ) as opened,
        pytest.raises(ImageRejected),
    ):
        normalize_logo(b"x" * (2 * 1024 * 1024 + 1))
    opened.assert_not_called()


def test_logo_exactly_2_mb_is_accepted() -> None:
    data = make_logo_png()
    normalized = normalize_logo(data + b"\x00" * (2 * 1024 * 1024 - len(data)))
    assert normalized.mime == "image/png"


def _png_chunk_names(data: bytes) -> list[bytes]:
    offset = 8
    result = []
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        result.append(data[offset + 4 : offset + 8])
        offset += 12 + length
    return result


def test_logo_is_rgba_preserves_alpha_and_strips_all_auxiliary_chunks() -> None:
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Description", "dados privados")
    metadata.add_text("XML:com.adobe.xmp", "localizacao privada", zip=True)
    metadata.add(b"gAMA", struct.pack(">I", 45455))
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    image = Image.new("RGBA", (32, 24), (40, 80, 120, 128))
    exif = Image.Exif()
    exif[270] = "descricao privada"
    image.save(output, "PNG", pnginfo=metadata, icc_profile=profile, exif=exif, dpi=(72, 72))
    source = output.getvalue()
    assert {b"tEXt", b"zTXt", b"iCCP", b"eXIf", b"gAMA", b"pHYs"} <= set(_png_chunk_names(source))
    normalized = normalize_logo(source)
    assert (normalized.width, normalized.height, normalized.mime) == (32, 24, "image/png")
    assert set(_png_chunk_names(normalized.data)) == {b"IHDR", b"IDAT", b"IEND"}
    assert normalized.sha256 == sha256(normalized.data).hexdigest()
    assert normalized == normalize_logo(source)
    with Image.open(BytesIO(normalized.data)) as saved:
        assert saved.mode == "RGBA"
        assert saved.getpixel((0, 0)) == (40, 80, 120, 128)
        assert not saved.info
        assert not saved.getexif()


@pytest.mark.parametrize("mode", ["LA", "P"])
def test_logo_accepts_grayscale_alpha_and_palette_transparency(mode: str) -> None:
    output = BytesIO()
    if mode == "LA":
        image = Image.new(mode, (4, 3), (60, 128))
    else:
        image = Image.new(mode, (4, 3), 0)
        image.putpalette([20, 40, 60] + [0] * 765)
        image.info["transparency"] = 0
    image.save(output, "PNG")
    normalized = normalize_logo(output.getvalue())
    expected = (60, 60, 60, 128) if mode == "LA" else (20, 40, 60, 0)
    with Image.open(BytesIO(normalized.data)) as saved:
        assert saved.mode == "RGBA"
        assert saved.getpixel((0, 0)) == expected
        assert set(_png_chunk_names(normalized.data)) == {b"IHDR", b"IDAT", b"IEND"}
