"""Imagens sinteticas deterministicas; nunca dependem de ficheiros pessoais."""

import struct
import zlib
from io import BytesIO

from PIL import Image, ImageCms


def _srgb_profile() -> bytes:
    profile = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    # LittleCMS usa a hora atual no cabecalho; fixar a data e remover o ID derivado dela.
    profile[24:36] = struct.pack(">6H", 2026, 1, 1, 0, 0, 0)
    profile[84:100] = bytes(16)
    return bytes(profile)


def make_jpeg(
    width: int,
    height: int,
    *,
    gps: bool = False,
    orientation: int | None = None,
    icc: bool = False,
) -> bytes:
    """Cria JPEG com quadrantes distintos e metadados opcionais para testar ingestao."""
    image = Image.new("RGB", (width, height), (220, 30, 20))
    image.paste((20, 40, 220), (width // 2, 0, width, height))
    exif = Image.Exif()
    if gps:
        exif[34853] = {1: "N", 2: (38.0, 43.0, 0.0), 3: "W", 4: (9.0, 8.0, 0.0)}
    if orientation is not None:
        exif[274] = orientation
    profile = _srgb_profile() if icc else b""
    output = BytesIO()
    image.save(output, "JPEG", quality=95, exif=exif, icc_profile=profile, comment=b"privado")
    return output.getvalue()


def make_png(width: int, height: int, *, alpha: bool = False) -> bytes:
    """Cria PNG RGB ou RGBA com cor fixa e alfa parcial quando pedido."""
    image = Image.new("RGBA" if alpha else "RGB", (width, height), (40, 80, 120, 128))
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload))
    )


def make_png_bomb(width: int, height: int) -> bytes:
    """Cria IHDR grande com IDAT minimo, sem alocar os pixeis anunciados."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + _png_chunk(b"IEND", b"")
    )


def make_animated_webp() -> bytes:
    """Cria WebP com dois fotogramas visivelmente diferentes."""
    output = BytesIO()
    Image.new("RGB", (1080, 1080), "red").save(
        output,
        "WEBP",
        save_all=True,
        append_images=[Image.new("RGB", (1080, 1080), "blue")],
        duration=100,
        loop=0,
    )
    return output.getvalue()


def make_logo_png() -> bytes:
    """Cria logotipo RGBA pequeno, incluindo transparencia parcial."""
    return make_png(32, 24, alpha=True)
