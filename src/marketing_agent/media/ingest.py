"""Normalizacao de fotos e logotipos antes do armazenamento imutavel."""

import threading
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image, ImageOps

from marketing_agent.images.codec import (
    ImageRejected,
    convert_to_srgb,
    encode_jpeg,
    open_bounded,
    sha256_hex,
    to_srgb_rgb,
)

MAX_PHOTO_BYTES = 15 * 1024 * 1024
MIN_PHOTO_SIDE = 1080
MAX_STORED_SIDE = 4096
MAX_LOGO_BYTES = 2 * 1024 * 1024
MIN_LOGO_SIDE = 64
MAX_LOGO_SIDE = 4096
LOGO_SIDE_MESSAGE = "O logótipo precisa de ter entre 64 e 4096 píxeis em cada lado."

# Uma normalizacao de cada vez no processo: uma foto de 64 MP chega a ~1 GB de pico, e
# uploads simultaneos multiplicariam esse pico. A espera e curta e o painel e de uso restrito.
_NORMALIZATION_SLOT = threading.Semaphore(1)


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
        with _NORMALIZATION_SLOT:
            return _normalize_photo(data)
    except ImageRejected:
        raise
    except Exception as error:
        raise ImageRejected("Não foi possível normalizar a fotografia.") from error


def _normalize_photo(data: bytes) -> NormalizedImage:
    with open_bounded(data, frozenset({"jpeg", "png", "webp"})) as image:
        # A orientacao precisa do EXIF original e deve preceder qualquer remocao. In place
        # evita mais uma copia completa da imagem (ate 64 MP) em memoria.
        ImageOps.exif_transpose(image, in_place=True)
        if min(image.size) < MIN_PHOTO_SIDE:
            raise ImageRejected("A fotografia precisa de pelo menos 1080 píxeis em cada lado.")
        rgb = to_srgb_rgb(image)
        if max(rgb.size) > MAX_STORED_SIDE:
            rgb.thumbnail((MAX_STORED_SIDE, MAX_STORED_SIDE), Image.Resampling.LANCZOS)
        encoded = encode_jpeg(rgb)
        return NormalizedImage(encoded, "image/jpeg", rgb.width, rgb.height, sha256_hex(encoded))


def normalize_logo(data: bytes) -> NormalizedImage:
    """Valida PNG com alfa e produz RGBA sRGB sem blocos auxiliares."""
    if len(data) > MAX_LOGO_BYTES:
        raise ImageRejected("O logótipo excede o limite de 2 MB.")
    try:
        with _NORMALIZATION_SLOT:
            return _normalize_logo(data)
    except ImageRejected:
        raise
    except Exception as error:
        raise ImageRejected("Não foi possível normalizar o logótipo.") from error


def _normalize_logo(data: bytes) -> NormalizedImage:
    with open_bounded(data, frozenset({"png"})) as image:
        if image.mode not in ("RGBA", "LA") and not (
            image.mode == "P" and "transparency" in image.info
        ):
            raise ImageRejected("O logótipo precisa de ser PNG com transparência.")
        if not min(image.size) >= MIN_LOGO_SIDE or max(image.size) > MAX_LOGO_SIDE:
            raise ImageRejected(LOGO_SIDE_MESSAGE)
        color, alpha = convert_to_srgb(image)
        if alpha is None:
            raise ImageRejected("O logótipo precisa de ser PNG com transparência.")
        # Uma imagem nova impede que info e EXIF acompanhem a copia do Pillow.
        clean = Image.new("RGBA", image.size)
        clean.paste(color)
        clean.putalpha(alpha)
        output = BytesIO()
        clean.save(output, "PNG")
        encoded = output.getvalue()
        return NormalizedImage(encoded, "image/png", clean.width, clean.height, sha256_hex(encoded))
