"""Seguranca de descodificacao, cor e metadados com bytes reais."""

import struct
from io import BytesIO
from unittest.mock import patch

import pytest
from image_factory import make_animated_webp, make_jpeg, make_png, make_png_bomb
from PIL import Image, ImageCms, ImageFile

from marketing_agent.images import codec
from marketing_agent.images.codec import (
    ImageRejected,
    detect_format,
    encode_jpeg,
    open_bounded,
    sha256_hex,
    to_srgb_rgb,
)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"\xff\xd8\xff", "jpeg"),
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"RIFF\x00\x01\x02\x03WEBP", "webp"),
        (b"RIFF\x00\x01\x02\x03WAVE", None),
        (b"PNG", None),
        (b"", None),
        (b"texto privado.jpg", None),
    ],
)
def test_detect_format_checks_complete_signature(data: bytes, expected: str | None) -> None:
    assert detect_format(data) == expected


def test_detects_type_by_bytes_not_extension() -> None:
    uploads = {"falso.jpg": b"texto privado", "foto.jpg": make_png(10, 8)}
    with pytest.raises(ImageRejected):
        open_bounded(uploads["falso.jpg"], frozenset({"jpeg", "png"}))
    with open_bounded(uploads["foto.jpg"], frozenset({"jpeg", "png"})) as image:
        assert image.format == "PNG"
        assert image.size == (10, 8)
    with pytest.raises(ImageRejected):
        open_bounded(uploads["foto.jpg"], frozenset({"jpeg"}))


@pytest.mark.parametrize("size", [(20000, 20000), (8000, 8001)])
def test_decompression_bomb_rejected_before_decode(size: tuple[int, int]) -> None:
    data = make_png_bomb(*size)
    with (
        patch.object(Image.Image, "load", side_effect=AssertionError("descodificou bomba")) as load,
        pytest.raises(ImageRejected),
    ):
        open_bounded(data, frozenset({"png"}))
    load.assert_not_called()


def test_pixel_limit_includes_exact_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codec, "MAX_IMAGE_PIXELS", 64)
    with open_bounded(make_png(8, 8), frozenset({"png"})) as image:
        assert image.size == (8, 8)
    with pytest.raises(ImageRejected):
        open_bounded(make_png(8, 9), frozenset({"png"}))


def test_decompression_warning_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 32)
    with pytest.raises(ImageRejected):
        open_bounded(make_png(8, 8), frozenset({"png"}))


@pytest.mark.parametrize("data", [make_jpeg(50, 40)[:-20], make_png(50, 40)[:50]])
def test_truncated_image_rejected(data: bytes) -> None:
    assert ImageFile.LOAD_TRUNCATED_IMAGES is False
    with pytest.raises(ImageRejected):
        open_bounded(data, frozenset({"jpeg", "png"}))


def test_animated_webp_rejected() -> None:
    with pytest.raises(ImageRejected):
        open_bounded(make_animated_webp(), frozenset({"webp"}))


def test_animated_png_rejected() -> None:
    output = BytesIO()
    Image.new("RGBA", (8, 8), "red").save(
        output, "PNG", save_all=True, append_images=[Image.new("RGBA", (8, 8), "blue")]
    )
    with pytest.raises(ImageRejected):
        open_bounded(output.getvalue(), frozenset({"png"}))


def test_static_webp_is_decoded() -> None:
    output = BytesIO()
    Image.new("RGB", (8, 8), "red").save(output, "WEBP", lossless=True)
    with open_bounded(output.getvalue(), frozenset({"webp"})) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)


def test_invalid_pillow_data_has_safe_reason_and_preserves_cause() -> None:
    with pytest.raises(ImageRejected) as error:
        open_bounded(b"\xff\xd8\xffsegredo privado", frozenset({"jpeg"}))
    assert error.value.reason == str(error.value)
    assert "segredo" not in str(error.value)
    assert error.value.__cause__ is not None


def _linear_rgb_profile() -> bytes:
    profile = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    count = struct.unpack_from(">I", profile, 128)[0]
    for index in range(count):
        tag, offset, _ = struct.unpack_from(">4sII", profile, 132 + 12 * index)
        if tag in (b"rTRC", b"gTRC", b"bTRC"):
            # Curva parametrica de tipo 0 e gama 1: RGB linear em vez de sRGB.
            struct.pack_into(">H", profile, offset + 8, 0)
            struct.pack_into(">i", profile, offset + 12, 65536)
    return bytes(profile)


def test_icc_profile_converted_to_srgb_and_dropped() -> None:
    image = Image.new("RGB", (4, 4), (128, 128, 128))
    image.info["icc_profile"] = _linear_rgb_profile()
    converted = to_srgb_rgb(image)
    assert converted.mode == "RGB"
    # 128/255 linear convertido pela curva sRGB fica perto de 188/255.
    assert converted.getpixel((0, 0)) == pytest.approx((188, 188, 188), abs=1)
    with Image.open(BytesIO(encode_jpeg(converted))) as saved:
        assert "icc_profile" not in saved.info
        assert saved.getpixel((0, 0)) == pytest.approx((188, 188, 188), abs=2)


@pytest.mark.parametrize("mode", ["RGBA", "LA", "P"])
def test_transparency_is_composited_over_white(mode: str) -> None:
    if mode == "P":
        image = Image.new(mode, (1, 1), 0)
        image.putpalette([255, 0, 0] + [0] * 765)
        image.info["transparency"] = b"\x80"
    else:
        image = Image.new(mode, (1, 1), (255, 0, 0, 128) if mode == "RGBA" else (0, 128))
    expected = (255, 127, 127) if mode != "LA" else (127, 127, 127)
    assert to_srgb_rgb(image).getpixel((0, 0)) == expected


def test_icc_conversion_preserves_alpha_until_white_composition() -> None:
    image = Image.new("RGBA", (1, 1), (128, 128, 128, 128))
    image.info["icc_profile"] = _linear_rgb_profile()
    assert to_srgb_rgb(image).getpixel((0, 0)) == pytest.approx((221, 221, 221), abs=1)


def test_cmyk_and_opaque_palette_are_converted_to_rgb() -> None:
    assert to_srgb_rgb(Image.new("CMYK", (1, 1), (0, 255, 255, 0))).getpixel((0, 0)) == (255, 0, 0)
    image = Image.new("P", (1, 1), 0)
    image.putpalette([20, 40, 60] + [0] * 765)
    assert to_srgb_rgb(image).getpixel((0, 0)) == (20, 40, 60)


def test_invalid_icc_profile_is_rejected_without_echoing_profile() -> None:
    image = Image.new("RGB", (1, 1))
    image.info["icc_profile"] = b"perfil privado invalido"
    with pytest.raises(ImageRejected) as error:
        to_srgb_rgb(image)
    assert "privado" not in str(error.value)
    assert error.value.__cause__ is not None


def test_jpeg_encoding_drops_metadata_and_uses_full_chroma() -> None:
    with Image.open(BytesIO(make_jpeg(16, 8, gps=True, orientation=6, icc=True))) as image:
        data = encode_jpeg(image)
    with Image.open(BytesIO(data)) as saved:
        assert not saved.getexif()
        assert (
            not {"exif", "icc_profile", "comment", "progressive", "progression"} & saved.info.keys()
        )
        assert saved.layer == [(1, 1, 1, 0), (2, 1, 1, 1), (3, 1, 1, 1)]
        # Tabela luminancia do JPEG de qualidade 90 (primeira entrada).
        assert saved.quantization[0][0] == 3


def test_sha256_has_known_answer() -> None:
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.mark.parametrize("missing", [1, 4, 12])
def test_png_with_truncated_iend_is_rejected(missing: int) -> None:
    with pytest.raises(ImageRejected):
        open_bounded(make_png(8, 8)[:-missing], frozenset({"png"}))


def test_png_with_invalid_idat_checksum_is_rejected() -> None:
    data = bytearray(make_png(8, 8))
    offset = data.index(b"IDAT")
    length = struct.unpack_from(">I", data, offset - 4)[0]
    data[offset + 4 + length] ^= 1
    with pytest.raises(ImageRejected):
        open_bounded(bytes(data), frozenset({"png"}))


def test_grayscale_icc_is_converted_before_alpha_composition() -> None:
    profile = bytearray(_linear_rgb_profile())
    profile[16:20] = b"GRAY"
    count = struct.unpack_from(">I", profile, 128)[0]
    for index in range(count):
        offset = 132 + 12 * index
        if profile[offset : offset + 4] == b"rTRC":
            profile[offset : offset + 4] = b"kTRC"
    image = Image.new("LA", (1, 1), (128, 128))
    image.info["icc_profile"] = bytes(profile)
    assert to_srgb_rgb(image).getpixel((0, 0)) == pytest.approx((221, 221, 221), abs=1)
