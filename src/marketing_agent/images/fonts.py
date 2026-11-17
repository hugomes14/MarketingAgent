"""Inter Bold estatico 4.1, com SIL OFL original, vendorizado do arquivo oficial.

Origem: https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip
Membro: extras/ttf/Inter-Bold.ttf; SHA-256:
288316099b1e0a47a4716d159098005eef7c0066921f34e3200393dbdb01947f.
LICENSE.txt foi copiado sem alteracoes para assets/fonts/OFL.txt; SHA-256:
262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache, lru_cache
from hashlib import sha256
from importlib.resources import files
from io import BytesIO
from threading import local
from types import MappingProxyType

from PIL import ImageFont


class FontIntegrityError(ValueError):
    """O recurso vendorizado nao pode ser lido ou nao corresponde ao hash oficial."""


@dataclass(frozen=True)
class FontFile:
    """Nome publico, caminho relativo no pacote e resumo da fonte oficial."""

    name: str
    resource: str
    sha256: str


BRAND_FONTS: Mapping[str, FontFile] = MappingProxyType(
    {
        "inter-bold": FontFile(
            "inter-bold",
            "fonts/Inter-Bold.ttf",
            "288316099b1e0a47a4716d159098005eef7c0066921f34e3200393dbdb01947f",
        )
    }
)


@cache
def _font_bytes(name: str) -> bytes:
    font = BRAND_FONTS.get(name)
    if font is None:
        raise ValueError("Tipo de letra desconhecido.")
    try:
        data = files("marketing_agent.assets").joinpath(font.resource).read_bytes()
    except OSError as error:
        raise FontIntegrityError("Não foi possível verificar a integridade da fonte.") from error
    if sha256(data).hexdigest() != font.sha256:
        raise FontIntegrityError("Falha de integridade da fonte vendorizada.")
    # O decorador so guarda retornos bem sucedidos, nunca bytes ainda por validar.
    return data


def _build_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    if type(size) is not int or size <= 0:
        raise ValueError("O tamanho da fonte deve ser um inteiro positivo.")
    return ImageFont.truetype(
        BytesIO(_font_bytes(name)), size=size, layout_engine=ImageFont.Layout.BASIC
    )


@lru_cache(maxsize=128, typed=True)
def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Carrega por tamanho com bytes verificados e layout BASIC, sem libraqm do sistema.

    O objeto em cache e partilhado e as faces FreeType nao sao seguras entre threads:
    quem renderiza a partir de threads deve usar `load_thread_font`.
    """
    return _build_font(name, size)


_thread_fonts = local()


def load_thread_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Como `load_font`, mas com uma face FreeType propria de cada thread.

    Os bytes continuam verificados e em cache global (`_font_bytes`); so o objeto
    `FreeTypeFont`, que tem estado interno mutavel, nunca atravessa threads.
    """
    cache: dict[tuple[str, int], ImageFont.FreeTypeFont] | None = getattr(
        _thread_fonts, "cache", None
    )
    if cache is None:
        cache = _thread_fonts.cache = {}
    key = (name, size)
    font = cache.get(key)
    if font is None:
        font = cache[key] = _build_font(name, size)
    return font
