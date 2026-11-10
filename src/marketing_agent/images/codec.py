"""Fronteira de descodificacao limitada e codificacao sem metadados."""

import warnings
from hashlib import sha256
from io import BytesIO
from typing import Literal

from PIL import Image, ImageCms, ImageFile

MAX_IMAGE_PIXELS = 64_000_000
JPEG_QUALITY = 90
ImageFormat = Literal["jpeg", "png", "webp"]
UNSUPPORTED_DEPTH = "Profundidade de cor da imagem não suportada."

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
        # warnings.catch_warnings() nao e thread-safe (altera o estado global dos avisos):
        # a defesa real e a verificacao explicita de width*height antes de load(); o aviso
        # de bomba de descompressao do Pillow e apenas uma segunda barreira.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=[detected.upper()]) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ImageRejected("A imagem excede o limite de píxeis.")
                _require_single_frame(image, detected)
                if detected == "png":
                    _verify_png(data)
                image.load()
                return image
    except ImageRejected:
        raise
    # A fronteira Pillow inclui erros especificos dos plugins; nunca os mostrar no painel.
    except Exception as error:
        raise ImageRejected("Não foi possível descodificar a imagem com segurança.") from error


def _require_single_frame(image: Image.Image, detected: ImageFormat) -> None:
    # JPEG multi-imagem (MPO) de telemovel traz miniaturas ou profundidade em fotogramas
    # extra; nao e animacao. Usa-se o fotograma primario e o re-encode descarta o resto.
    # So PNG (APNG) e WebP podem ser animados.
    if detected == "jpeg":
        image.seek(0)
    elif getattr(image, "n_frames", 1) > 1:
        raise ImageRejected("Imagens animadas não são permitidas.")


def _verify_png(data: bytes) -> None:
    with BytesIO(data) as stream, Image.open(stream, formats=["PNG"]) as image:
        image.verify()
        # Pillow para antes do CRC de IEND; exigir os quatro bytes finais desse bloco.
        if stream.read(4) != b"\xaeB`\x82":
            raise ImageRejected("O ficheiro PNG está incompleto ou corrompido.")


def convert_to_srgb(image: Image.Image) -> tuple[Image.Image, Image.Image | None]:
    """Devolve a cor em sRGB (modo RGB) e o canal alfa, ou None se a imagem for opaca.

    Pode devolver a propria imagem quando ja e RGB opaca sem perfil, para nao duplicar
    64 MP em memoria; o chamador nao deve assumir que e uma copia.
    """
    try:
        eight_bit = _to_eight_bit(image)
        alpha, color = _split_alpha(eight_bit)
        return _apply_profile(color, eight_bit.info.get("icc_profile")), alpha
    except ImageRejected:
        raise
    except Exception as error:
        raise ImageRejected("Não foi possível converter as cores da imagem.") from error


def to_srgb_rgb(image: Image.Image) -> Image.Image:
    """Converte o perfil para sRGB e compoe o alfa sobre branco."""
    color, alpha = convert_to_srgb(image)
    if alpha is None:
        return color
    background = Image.new("RGB", color.size, "white")
    background.paste(color, mask=alpha)
    return background


def _to_eight_bit(image: Image.Image) -> Image.Image:
    # convert("RGB") satura os 16 bits (32768 vira 255), por isso escala-se 16 -> 8 bits antes.
    if image.mode == "I;16":
        # O tRNS de 16 bits nao tem equivalente exacto em 8 bits depois de escalar.
        if "transparency" in image.info:
            raise ImageRejected(UNSUPPORTED_DEPTH)
        # point() mantem o modo I;16; convert("L") e exacto porque os valores ja cabem em 0..255.
        scaled = image.point(lambda value: value / 256).convert("L")
        scaled.info.update(image.info)
        return scaled
    # I e F nao tem escala conhecida; nenhum formato permitido os produz hoje.
    if image.mode in ("I", "F") or image.mode.startswith("I;"):
        raise ImageRejected(UNSUPPORTED_DEPTH)
    return image


def _split_alpha(image: Image.Image) -> tuple[Image.Image | None, Image.Image]:
    has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
    if not has_alpha:
        return None, image.convert("RGB") if image.mode == "P" else image
    rgba = image.convert("RGBA")
    # A transformacao ICC recebe apenas a cor; o alfa conserva-se para compor no fim.
    # Cinzento mantem-se em L para aplicar perfis cinzentos antes de passar a RGB.
    color = image.getchannel("L") if image.mode in ("L", "LA") else rgba.convert("RGB")
    return rgba.getchannel("A"), color


def _apply_profile(color: Image.Image, profile: bytes | None) -> Image.Image:
    if not profile:
        return color if color.mode == "RGB" else color.convert("RGB")
    transform = ImageCms.buildTransform(
        ImageCms.ImageCmsProfile(BytesIO(profile)),
        ImageCms.createProfile("sRGB"),
        color.mode,
        "RGB",
    )
    return transform.apply(color)


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
