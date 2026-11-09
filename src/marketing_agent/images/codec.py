"""Fronteira de descodificacao limitada e codificacao sem metadados."""

import warnings
from hashlib import sha256
from io import BytesIO
from typing import Literal

from PIL import Image, ImageCms, ImageFile

MAX_IMAGE_PIXELS = 64_000_000
JPEG_QUALITY = 90
ImageFormat = Literal["jpeg", "png", "webp"]

# Limites globais ao processo: nao permitir bombas nem descodificacao parcial de uploads.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
ImageFile.LOAD_TRUNCATED_IMAGES = False


class ImageRejected(ValueError):
    """Imagem recusada com motivo seguro para apresentar no painel."""

    def __init__(self, reason: str) -> None:
        """Guarda um motivo fixo e legivel, sem incluir dados do upload."""
        self.reason = reason
        super().__init__(reason)


def detect_format(data: bytes) -> ImageFormat | None:
    """Identifica apenas assinaturas JPEG, PNG e WebP."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return None


def open_bounded(data: bytes, allowed: frozenset[ImageFormat]) -> Image.Image:
    """Descodifica uma imagem estatica dentro do limite de pixeis."""
    detected = detect_format(data)
    if detected is None or detected not in allowed:
        raise ImageRejected("Formato de imagem não permitido.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=[detected.upper()]) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ImageRejected("A imagem excede o limite de píxeis.")
                if getattr(image, "n_frames", 1) > 1:
                    raise ImageRejected("Imagens animadas não são permitidas.")
                if detected == "png":
                    _verify_png(data)
                image.load()
                return image
    except ImageRejected:
        raise
    # A fronteira Pillow inclui erros especificos dos plugins; nunca os mostrar no painel.
    except Exception as error:
        raise ImageRejected("Não foi possível descodificar a imagem com segurança.") from error


def _verify_png(data: bytes) -> None:
    with BytesIO(data) as stream, Image.open(stream, formats=["PNG"]) as image:
        image.verify()
        # Pillow para antes do CRC de IEND; exigir os quatro bytes finais desse bloco.
        if stream.read(4) != b"\xaeB`\x82":
            raise ImageRejected("O ficheiro PNG está incompleto ou corrompido.")


def to_srgb_rgb(image: Image.Image) -> Image.Image:
    """Converte o perfil para sRGB e compoe o alfa sobre branco."""
    try:
        has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
        alpha = image.convert("RGBA").getchannel("A") if has_alpha else None
        # A transformacao ICC recebe apenas a cor; o alfa conserva-se para compor no fim.
        color = image.convert("RGBA").convert("RGB") if has_alpha else image
        if image.mode == "LA":
            color = image.getchannel("L")
        if color.mode == "P":
            color = color.convert("RGB")
        profile = image.info.get("icc_profile")
        if profile:
            transform = ImageCms.buildTransform(
                ImageCms.ImageCmsProfile(BytesIO(profile)),
                ImageCms.createProfile("sRGB"),
                color.mode,
                "RGB",
            )
            color = transform.apply(color)
        else:
            color = color.convert("RGB")
        if alpha is not None:
            background = Image.new("RGB", image.size, "white")
            background.paste(color, mask=alpha)
            color = background
        return color
    except Exception as error:
        raise ImageRejected("Não foi possível converter as cores da imagem.") from error


def encode_jpeg(image: Image.Image) -> bytes:
    """Codifica RGB em JPEG deterministico sem metadados pessoais."""
    try:
        # Uma imagem nova impede a heranca implicita de EXIF, ICC e comentarios pelo encoder.
        clean = Image.new("RGB", image.size)
        clean.paste(image)
        output = BytesIO()
        clean.save(
            output,
            "JPEG",
            quality=JPEG_QUALITY,
            subsampling=0,
            optimize=False,
            progressive=False,
        )
        return output.getvalue()
    except Exception as error:
        raise ImageRejected("Não foi possível codificar a imagem.") from error


def sha256_hex(data: bytes) -> str:
    """Calcula o resumo hexadecimal SHA-256 dos bytes."""
    return sha256(data).hexdigest()
