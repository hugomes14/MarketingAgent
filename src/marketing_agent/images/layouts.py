"""Tres layouts 1080x1350 deterministas: recorte 4:5, titulo ajustado e logotipo contido.

Determinismo: as mesmas entradas dao os mesmos bytes nesta maquina. Fica fixo o que pode
variar: codificacao JPEG (`encode_jpeg`: qualidade, subamostragem, sem optimize/progressivo,
sem EXIF/ICC/comentario), reamostragem LANCZOS, `GaussianBlur(6)`, fonte com layout BASIC e
ancoras de texto explicitas; nada usa relogio, locale nem ambiente. Os ficheiros de ouro em
`tests/images/golden/` dependem do Pillow e do libjpeg fixados em `uv.lock`: subir o Pillow
obriga a regravar os ouros; so se sobe a versao em `LAYOUT_VERSIONS` quando o visual mudou
de proposito. Mudar qualquer constante de geometria abaixo muda os bytes e exige subir a
versao do layout e regravar o ouro correspondente.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from types import MappingProxyType
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from marketing_agent.images.codec import (
    ImageFormat,
    encode_jpeg,
    open_bounded,
    sha256_hex,
    to_srgb_rgb,
)
from marketing_agent.images.fonts import load_thread_font

type LayoutName = Literal["foto_inteira", "foto_titulo", "fundo_ia_titulo"]
type Rgb = tuple[int, int, int]
type Rect = tuple[int, int, int, int]

LAYOUT_VERSIONS: Mapping[LayoutName, int] = MappingProxyType(
    {"foto_inteira": 1, "foto_titulo": 1, "fundo_ia_titulo": 1}
)
CANVAS = (1080, 1350)
MAX_TITLE_CHARS = 40
MAX_TITLE_LINES = 2

FONT_NAME = "inter-bold"
TITLE_SIZE_STEP = 4
LINE_SPACING_PERCENT = 115
OUTER_MARGIN = 64
BAND_HEIGHT = 324
BAND_TOP = CANVAS[1] - BAND_HEIGHT
SHADOW_OFFSET = (4, 4)
SHADOW_BLUR_RADIUS = 6
LOGO_BOX_SIDE: Mapping[LayoutName, int] = MappingProxyType(
    {"foto_titulo": 180, "fundo_ia_titulo": 160}
)
GRADIENT_SIZE = (1024, 1536)
GRADIENT_SEED_BYTES = 6
PNG_COMPRESS_LEVEL = 6

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_PHOTO_FORMATS: frozenset[ImageFormat] = frozenset({"jpeg", "png", "webp"})
_LOGO_FORMATS: frozenset[ImageFormat] = frozenset({"png"})


@dataclass(frozen=True)
class TitleBox:
    """Largura util e tamanhos maximo e minimo (px) do titulo de um layout."""

    width: int
    max_size: int
    min_size: int


TITLE_BOXES: Mapping[LayoutName, TitleBox] = MappingProxyType(
    {
        "foto_titulo": TitleBox(width=732, max_size=88, min_size=48),
        "fundo_ia_titulo": TitleBox(width=888, max_size=112, min_size=64),
    }
)


class LayoutError(ValueError):
    """Erro de layout com mensagem fixa: nunca inclui o titulo nem bytes de imagens."""


class InvalidLayoutInput(LayoutError):
    """Entrada do layout invalida (nome, cor, titulo em falta ou imagem em modo errado)."""


class TitleTooLong(LayoutError):
    """Titulo com mais de MAX_TITLE_CHARS caracteres."""

    def __init__(self) -> None:
        """Mensagem fixa, sem o texto do titulo."""
        super().__init__(f"O título tem mais de {MAX_TITLE_CHARS} caracteres.")


class TitleDoesNotFit(LayoutError):
    """O titulo nao cabe no layout; so ocorre se o chamador nao validou com `fit_title`."""

    def __init__(self) -> None:
        """Mensagem fixa, sem o texto do titulo."""
        super().__init__("O título não cabe no layout ou tem caracteres sem glifo.")


@dataclass(frozen=True)
class TitleFit:
    """Tamanho da letra (px) e linhas do titulo ajustado."""

    size: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class RenderedImage:
    """JPEG final com a proveniencia necessaria para o registo da revisao."""

    data: bytes
    sha256: str
    layout: LayoutName
    layout_version: int
    width: int
    height: int


def normalize_title(titulo: str) -> str:
    """Colapsa espacos e normaliza para NFC (acentos compostos), antes de medir e desenhar."""
    return unicodedata.normalize("NFC", " ".join(titulo.split()))


def _parse_color(value: str) -> Rgb:
    if not isinstance(value, str) or _HEX_COLOR.fullmatch(value) is None:
        raise InvalidLayoutInput("A cor deve usar o formato #RRGGBB.")
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


@dataclass(frozen=True)
class LayoutInput:
    """Entradas validadas de um layout.

    `foto_inteira` nao desenha texto nem logotipo: ignora `titulo` e `logo_png`, para que
    o mesmo pedido sirva os tres layouts (por exemplo na pre-visualizacao do kit).
    """

    layout: LayoutName
    base_image: bytes
    titulo: str | None
    cor_primaria: str
    cor_secundaria: str
    logo_png: bytes | None

    def __post_init__(self) -> None:
        """Valida a fronteira: layout, cores, bytes e titulo (obrigatorio nos layouts com texto)."""
        if self.layout not in LAYOUT_VERSIONS:
            raise InvalidLayoutInput("Layout desconhecido.")
        _parse_color(self.cor_primaria)
        _parse_color(self.cor_secundaria)
        if not isinstance(self.base_image, bytes) or not (
            self.logo_png is None or isinstance(self.logo_png, bytes)
        ):
            raise InvalidLayoutInput("As imagens devem ser bytes.")
        if self.titulo is not None and not isinstance(self.titulo, str):
            raise InvalidLayoutInput("O título deve ser texto.")
        text = normalize_title(self.titulo or "")
        if len(text) > MAX_TITLE_CHARS:
            raise TitleTooLong
        if self.layout in TITLE_BOXES and not text:
            raise InvalidLayoutInput("Este layout exige um título.")


def wrap_words(words: list[str], font: ImageFont.FreeTypeFont, width: int) -> list[str] | None:
    """Quebra gulosa por palavras; devolve None se alguma palavra sozinha excede `width`."""
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if font.getlength(candidate) <= width:
            current = candidate
            continue
        if not current:
            return None
        lines.append(current)
        if font.getlength(word) > width:
            return None
        current = word
    if current:
        lines.append(current)
    return lines


def _signature(font: ImageFont.FreeTypeFont, char: str) -> bytes:
    side = round(font.size)
    canvas = Image.new("L", (side * 3, side * 2))
    ImageDraw.Draw(canvas).text((side // 2, side // 2), char, font=font, fill=255)
    return canvas.tobytes()


def _has_all_glyphs(text: str, font: ImageFont.FreeTypeFont) -> bool:
    # O Inter nao tem emoji. Em vez de desenhar o quadrado de glifo em falta (.notdef), recusa-se
    # o titulo. U+10FFFF e um nao-carater garantidamente sem glifo, logo desenha o .notdef.
    missing = _signature(font, "\U0010ffff")
    return all(_signature(font, char) != missing for char in set(text) - {" "})


def _title_box(layout: LayoutName) -> TitleBox:
    box = TITLE_BOXES.get(layout)
    if box is None:
        raise InvalidLayoutInput("Este layout não tem título.")
    return box


def fit_title(titulo: str, layout: LayoutName) -> TitleFit | None:
    """Maior tamanho que cabe em MAX_TITLE_LINES linhas; None se nao couber, sem cortar texto.

    O tamanho decresce em TITLE_SIZE_STEP: aceita-se o primeiro que cabe em duas linhas, por
    isso um tamanho maior com duas linhas vence um menor com uma. Titulos vazios, com mais de
    MAX_TITLE_CHARS, com caracteres nao imprimiveis ou sem glifo na fonte devolvem None.
    """
    box = _title_box(layout)
    text = normalize_title(titulo)
    if not text or len(text) > MAX_TITLE_CHARS or not text.isprintable():
        return None
    if not _has_all_glyphs(text, load_thread_font(FONT_NAME, box.min_size)):
        return None
    words = text.split()
    for size in range(box.max_size, box.min_size - 1, -TITLE_SIZE_STEP):
        lines = wrap_words(words, load_thread_font(FONT_NAME, size), box.width)
        if lines is not None and len(lines) <= MAX_TITLE_LINES:
            return TitleFit(size, tuple(lines))
    return None


def crop_to_portrait(image: Image.Image) -> Image.Image:
    """Recorte central 4:5 e LANCZOS para 1080x1350; devolve sempre uma imagem NOVA.

    Exige RGB ja normalizado (`to_srgb_rgb`). Nunca altera nem fecha `image`.
    """
    if image.mode != "RGB":
        raise InvalidLayoutInput("A imagem deve estar em RGB.")
    width, height = image.size
    crop_width = min(width, max(1, height * 4 // 5))
    crop_height = min(height, max(1, width * 5 // 4))
    left = (width - crop_width) // 2
    top = (height - crop_height) // 2
    # resize(box=...) recorta e reamostra num so passo e, mesmo com o mesmo tamanho, copia.
    return image.resize(
        CANVAS, Image.Resampling.LANCZOS, box=(left, top, left + crop_width, top + crop_height)
    )


def logo_box(layout: LayoutName) -> Rect:
    """Caixa (x0, y0, x1, y1) do logotipo: a direita da faixa ou no canto inferior direito."""
    side = LOGO_BOX_SIDE[layout]
    right = CANVAS[0] - OUTER_MARGIN
    if layout == "foto_titulo":
        top = BAND_TOP + (BAND_HEIGHT - side) // 2
    else:
        top = CANVAS[1] - OUTER_MARGIN - side
    return right - side, top, right, top + side


def _paste_logo(canvas: Image.Image, logo_png: bytes, box: Rect) -> None:
    logo = open_bounded(logo_png, _LOGO_FORMATS).convert("RGBA")
    box_width, box_height = box[2] - box[0], box[3] - box[1]
    # Nunca se amplia: um logotipo pequeno fica no tamanho original, centrado na caixa.
    scale = min(1.0, box_width / logo.width, box_height / logo.height)
    if scale < 1.0:
        size = (max(1, round(logo.width * scale)), max(1, round(logo.height * scale)))
        logo = logo.resize(size, Image.Resampling.LANCZOS)
    left = box[0] + (box_width - logo.width) // 2
    top = box[1] + (box_height - logo.height) // 2
    canvas.paste(logo, (left, top), logo.getchannel("A"))


def _line_centers(fit: TitleFit, center_y: int) -> list[int]:
    pitch = fit.size * LINE_SPACING_PERCENT // 100
    first = center_y - pitch * (len(fit.lines) - 1) // 2
    return [first + index * pitch for index in range(len(fit.lines))]


def _draw_band_title(canvas: Image.Image, fit: TitleFit, primary: Rgb, secondary: Rgb) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, BAND_TOP, CANVAS[0] - 1, CANVAS[1] - 1), fill=primary)
    font = load_thread_font(FONT_NAME, fit.size)
    centers = _line_centers(fit, BAND_TOP + BAND_HEIGHT // 2)
    for line, center_y in zip(fit.lines, centers, strict=True):
        # Ancora "lm": esquerda e meio vertical da fonte, igual em todas as linhas.
        draw.text((OUTER_MARGIN, center_y), line, font=font, fill=secondary, anchor="lm")


def _draw_shadow_title(canvas: Image.Image, fit: TitleFit, primary: Rgb, secondary: Rgb) -> None:
    font = load_thread_font(FONT_NAME, fit.size)
    centers = _line_centers(fit, CANVAS[1] // 2)
    middle = CANVAS[0] // 2
    shadow = Image.new("L", CANVAS, 0)
    shadow_draw = ImageDraw.Draw(shadow)
    for line, center_y in zip(fit.lines, centers, strict=True):
        position = (middle + SHADOW_OFFSET[0], center_y + SHADOW_OFFSET[1])
        shadow_draw.text(position, line, font=font, fill=255, anchor="mm")
    canvas.paste(primary, mask=shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR_RADIUS)))
    draw = ImageDraw.Draw(canvas)
    for line, center_y in zip(fit.lines, centers, strict=True):
        draw.text((middle, center_y), line, font=font, fill=secondary, anchor="mm")


def _load_base(data: bytes) -> Image.Image:
    # open_bounded limita os pixeis antes de descodificar; to_srgb_rgb pode devolver a imagem
    # que abrimos (sem copia), por isso so crop_to_portrait, que cria outra, a consome.
    source = to_srgb_rgb(open_bounded(data, _PHOTO_FORMATS))
    try:
        return crop_to_portrait(source)
    finally:
        source.close()


def render_layout(spec: LayoutInput) -> RenderedImage:
    """Compoe o layout e codifica o JPEG final.

    Levanta `TitleDoesNotFit` se o titulo nao couber e `ImageRejected` se a imagem de base
    ou o logotipo forem recusados por `open_bounded`.
    """
    canvas = _load_base(spec.base_image)
    if spec.layout != "foto_inteira":
        fit = fit_title(spec.titulo or "", spec.layout)
        if fit is None:
            raise TitleDoesNotFit
        primary, secondary = _parse_color(spec.cor_primaria), _parse_color(spec.cor_secundaria)
        draw_title = _draw_band_title if spec.layout == "foto_titulo" else _draw_shadow_title
        draw_title(canvas, fit, primary, secondary)
        if spec.logo_png is not None:
            _paste_logo(canvas, spec.logo_png, logo_box(spec.layout))
    data = encode_jpeg(canvas)
    return RenderedImage(
        data=data,
        sha256=sha256_hex(data),
        layout=spec.layout,
        layout_version=LAYOUT_VERSIONS[spec.layout],
        width=canvas.width,
        height=canvas.height,
    )


def gradient_background(seed: bytes) -> bytes:
    """PNG 1024x1536 com gradiente vertical entre duas cores tiradas de `seed`.

    Bytes 0-2 sao a cor do topo e bytes 3-5 a do fundo (R, G, B); bytes a mais sao ignorados.
    Puro e determinista: serve o ouro do `fundo_ia_titulo`, a pre-visualizacao e o gerador falso.
    """
    if not isinstance(seed, bytes) or len(seed) < GRADIENT_SEED_BYTES:
        raise ValueError(f"A semente deve ter pelo menos {GRADIENT_SEED_BYTES} bytes.")
    top, bottom = seed[0:3], seed[3:6]
    width, height = GRADIENT_SIZE
    rows = [
        tuple(
            (top[i] * (height - 1 - y) + bottom[i] * y + (height - 1) // 2) // (height - 1)
            for i in range(3)
        )
        for y in range(height)
    ]
    column = Image.new("RGB", (1, height))
    column.putdata(rows)
    output = BytesIO()
    # compress_level explicito: o valor por omissao poderia mudar entre versoes do Pillow.
    column.resize(GRADIENT_SIZE, Image.Resampling.NEAREST).save(
        output, "PNG", compress_level=PNG_COMPRESS_LEVEL
    )
    return output.getvalue()
