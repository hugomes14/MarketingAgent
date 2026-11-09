"""Normalizacao de fotos e logotipos antes do armazenamento imutavel."""

from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image, ImageOps

from marketing_agent.images.codec import (
    ImageRejected,
    encode_jpeg,
    open_bounded,
    sha256_hex,
    to_srgb_rgb,
)

MAX_PHOTO_BYTES = 15 * 1024 * 1024
MIN_PHOTO_SIDE = 1080
MAX_STORED_SIDE = 4096
MAX_LOGO_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class NormalizedImage:
    """Bytes normalizados e metadados derivados desses mesmos bytes."""

    data: bytes
    mime: Literal["image/jpeg", "image/png"]
    width: int
    height: int
    sha256: str


def normalize_photo(data: bytes) -> NormalizedImage:
    """Valida uma fotografia e produz JPEG sRGB sem metadados."""
    if len(data) > MAX_PHOTO_BYTES:
        raise ImageRejected("A fotografia excede o limite de 15 MB.")
    try:
        with open_bounded(data, frozenset({"jpeg", "png", "webp"})) as image:
            # A orientacao precisa do EXIF original e deve preceder qualquer remocao.
            oriented = ImageOps.exif_transpose(image)
            if min(oriented.size) < MIN_PHOTO_SIDE:
                raise ImageRejected("A fotografia precisa de pelo menos 1080 píxeis em cada lado.")
            rgb = to_srgb_rgb(oriented)
            if max(rgb.size) > MAX_STORED_SIDE:
                rgb.thumbnail((MAX_STORED_SIDE, MAX_STORED_SIDE), Image.Resampling.LANCZOS)
            encoded = encode_jpeg(rgb)
            return NormalizedImage(
                encoded, "image/jpeg", rgb.width, rgb.height, sha256_hex(encoded)
            )
    except ImageRejected:
        raise
    except Exception as error:
        raise ImageRejected("Não foi possível normalizar a fotografia.") from error


def normalize_logo(data: bytes) -> NormalizedImage:
    """Valida PNG com alfa e produz RGBA sem blocos auxiliares."""
    if len(data) > MAX_LOGO_BYTES:
        raise ImageRejected("O logótipo excede o limite de 2 MB.")
    try:
        with open_bounded(data, frozenset({"png"})) as image:
            if image.mode not in ("RGBA", "LA") and not (
                image.mode == "P" and "transparency" in image.info
            ):
                raise ImageRejected("O logótipo precisa de ser PNG com transparência.")
            # Converter nao basta: info e EXIF podem acompanhar a copia do Pillow.
            clean = Image.new("RGBA", image.size)
            clean.paste(image.convert("RGBA"))
            output = BytesIO()
            clean.save(output, "PNG")
            encoded = output.getvalue()
            return NormalizedImage(
                encoded, "image/png", clean.width, clean.height, sha256_hex(encoded)
            )
    except ImageRejected:
        raise
    except Exception as error:
        raise ImageRejected("Não foi possível normalizar o logótipo.") from error
