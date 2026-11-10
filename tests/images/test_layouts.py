"""Layouts deterministas: ficheiros de ouro, geometria, titulos e entradas hostis.

Os ficheiros de ouro dependem do Pillow e do libjpeg fixados em uv.lock. Subir o Pillow
obriga a regravar os tres (`UPDATE_GOLDEN=1 uv run pytest tests/images/test_layouts.py`) e
a inspecionar o resultado; so se sobe `LAYOUT_VERSIONS` quando o visual mudou de proposito.
Sem `UPDATE_GOLDEN`, o teste nunca escreve.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from io import BytesIO
from pathlib import Path
from threading import Barrier

import pytest
from PIL import Image, ImageChops
from tests.support.image_factory import make_jpeg, make_logo_png, make_png

from marketing_agent.images import layouts
from marketing_agent.images.codec import ImageRejected, sha256_hex
from marketing_agent.images.layouts import (
    CANVAS,
    LAYOUT_VERSIONS,
    MAX_TITLE_CHARS,
    MAX_TITLE_LINES,
    InvalidLayoutInput,
    LayoutInput,
    LayoutName,
    RenderedImage,
    TitleDoesNotFit,
    TitleFit,
    TitleTooLong,
    crop_to_portrait,
    fit_title,
    gradient_background,
    render_layout,
)

GOLDEN_DIR = Path(__file__).parent / "golden"
PRIMARY = "#1F3A5F"
SECONDARY = "#F4E9D8"
LAYOUT_NAMES: tuple[LayoutName, ...] = ("foto_inteira", "foto_titulo", "fundo_ia_titulo")
GOLDEN_TITLES: dict[LayoutName, str | None] = {
    "foto_inteira": None,
    "foto_titulo": "Pão quente todos os dias às 7h",
    "fundo_ia_titulo": "Promoção de verão até domingo",
}
TITLE_LAYOUTS: tuple[LayoutName, ...] = ("foto_titulo", "fundo_ia_titulo")


def spec_for(
    layout: LayoutName,
    *,
    base: bytes | None = None,
    titulo: str | None = "Pão quente às 7h",
    logo: bytes | None = None,
) -> LayoutInput:
    return LayoutInput(
        layout=layout,
        base_image=base if base is not None else make_jpeg(1600, 1200),
        titulo=titulo,
        cor_primaria=PRIMARY,
        cor_secundaria=SECONDARY,
        logo_png=logo,
    )


def golden_spec(layout: LayoutName) -> LayoutInput:
    base = gradient_background(b"ouro-v1") if layout == "fundo_ia_titulo" else make_jpeg(1600, 1200)
    return spec_for(layout, base=base, titulo=GOLDEN_TITLES[layout], logo=make_logo_png())


def decode(data: bytes) -> Image.Image:
    image = Image.open(BytesIO(data))
    image.load()
    return image


@pytest.mark.parametrize("layout", LAYOUT_NAMES)
def test_layouts_match_golden_bytes(layout: LayoutName) -> None:
    rendered = render_layout(golden_spec(layout))
    path = GOLDEN_DIR / f"{layout}_v{LAYOUT_VERSIONS[layout]}.jpg"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(rendered.data)
    assert path.is_file(), f"Ficheiro de ouro em falta: {path.name}. Gerar com UPDATE_GOLDEN=1."
    expected = path.read_bytes()
    # Nunca imprimir bytes: so tamanhos e resumos, para a falha ser legivel.
    assert rendered.data == expected, (
        f"{path.name} difere: obtido {len(rendered.data)} B sha256={sha256_hex(rendered.data)}, "
        f"esperado {len(expected)} B sha256={sha256_hex(expected)}. Mudanca visual intencional "
        "exige subir LAYOUT_VERSIONS e regravar com UPDATE_GOLDEN=1."
    )


def test_layout_versions_are_all_one() -> None:
    assert dict(LAYOUT_VERSIONS) == dict.fromkeys(LAYOUT_NAMES, 1)
    with pytest.raises(TypeError):
        LAYOUT_VERSIONS["foto_inteira"] = 2  # type: ignore[index]


@pytest.mark.parametrize("layout", LAYOUT_NAMES)
def test_render_twice_same_bytes(layout: LayoutName) -> None:
    first = render_layout(golden_spec(layout))
    second = render_layout(golden_spec(layout))
    assert first == second
    assert first.data == second.data


@pytest.mark.parametrize("layout", LAYOUT_NAMES)
def test_output_is_1080x1350_jpeg_without_metadata(layout: LayoutName) -> None:
    rendered = render_layout(golden_spec(layout))
    image = decode(rendered.data)
    assert image.format == "JPEG"
    assert image.size == CANVAS == (1080, 1350)
    assert image.mode == "RGB"
    assert not image.info.get("exif")
    assert not image.info.get("icc_profile")
    assert "comment" not in image.info
    assert image.getexif() == {}
    assert not image.info.get("progressive")


@pytest.mark.parametrize("layout", LAYOUT_NAMES)
def test_rendered_image_provenance(layout: LayoutName) -> None:
    rendered = render_layout(golden_spec(layout))
    assert isinstance(rendered, RenderedImage)
    assert rendered.sha256 == sha256_hex(rendered.data)
    assert (rendered.width, rendered.height) == CANVAS
    assert rendered.layout == layout
    assert rendered.layout_version == LAYOUT_VERSIONS[layout] == 1
    with pytest.raises(FrozenInstanceError):
        rendered.width = 1  # type: ignore[misc]


def test_crop_to_portrait_centers_landscape_and_portrait() -> None:
    landscape = Image.new("RGB", (1600, 1000), (0, 0, 0))
    landscape.paste((255, 0, 0), (0, 0, 400, 1000))
    landscape.paste((0, 255, 0), (600, 0, 1000, 1000))
    landscape.paste((0, 0, 255), (1200, 0, 1600, 1000))
    result = crop_to_portrait(landscape)
    assert result.size == CANVAS
    # 4:5 de 1000 px de altura sao 800 px, de x=400 a x=1200: so o verde fica visivel.
    assert result.getpixel((540, 675)) == (0, 255, 0)
    assert result.getpixel((10, 10)) == (0, 0, 0)

    portrait = Image.new("RGB", (800, 2000), (0, 0, 0))
    portrait.paste((255, 0, 0), (0, 0, 800, 375))
    portrait.paste((0, 255, 0), (0, 875, 800, 1125))
    portrait.paste((0, 0, 255), (0, 1625, 800, 2000))
    result = crop_to_portrait(portrait)
    assert result.size == CANVAS
    # 4:5 de 800 px de largura sao 1000 px, de y=500 a y=1500.
    assert result.getpixel((540, 675)) == (0, 255, 0)
    assert result.getpixel((540, 10)) == (0, 0, 0)

    generated = crop_to_portrait(Image.new("RGB", (1024, 1536), (7, 8, 9)))
    assert generated.size == CANVAS


@pytest.mark.parametrize("size", [(1080, 1350), (1, 1), (20, 25), (4000, 100), (100, 4000)])
def test_crop_to_portrait_returns_new_image_for_any_shape(size: tuple[int, int]) -> None:
    source = Image.new("RGB", size, (10, 20, 30))
    before = source.tobytes()
    result = crop_to_portrait(source)
    assert result is not source
    assert result.size == CANVAS
    assert result.mode == "RGB"
    result.paste((255, 255, 255), (0, 0, 10, 10))
    assert source.tobytes() == before
    assert source.size == size


def test_crop_to_portrait_requires_rgb() -> None:
    with pytest.raises(InvalidLayoutInput):
        crop_to_portrait(Image.new("RGBA", (100, 100)))


@pytest.mark.parametrize("size", [(4000, 300), (300, 4000), (30, 20), (2, 2)])
@pytest.mark.parametrize("layout", LAYOUT_NAMES)
def test_render_accepts_non_portrait_and_tiny_inputs(
    layout: LayoutName, size: tuple[int, int]
) -> None:
    rendered = render_layout(spec_for(layout, base=make_jpeg(*size), logo=make_logo_png()))
    assert (rendered.width, rendered.height) == CANVAS
    assert decode(rendered.data).size == CANVAS


def test_render_normalizes_cmyk_and_alpha_bases() -> None:
    cmyk = BytesIO()
    Image.new("CMYK", (1100, 1400), (0, 255, 255, 0)).save(cmyk, "JPEG")
    red = decode(render_layout(spec_for("foto_inteira", base=cmyk.getvalue())).data)
    # Dois pixels longe de qualquer texto: vermelho, nao ciano nem cores invertidas.
    assert red.getpixel((540, 600)) == pytest.approx((255, 0, 0), abs=12)
    transparent = BytesIO()
    Image.new("RGBA", (1100, 1400), (255, 0, 0, 0)).save(transparent, "PNG")
    white = decode(render_layout(spec_for("foto_inteira", base=transparent.getvalue())).data)
    # O alfa compoe-se sobre branco, nao sobre a cor escondida pelo alfa.
    assert white.getpixel((540, 600)) == pytest.approx((255, 255, 255), abs=3)


def test_fit_title_prefers_large_size_with_two_lines_over_small_single_line() -> None:
    one_line = fit_title("Pão quente às 7h", "foto_titulo")
    assert one_line == TitleFit(88, ("Pão quente às 7h",))
    # Cabe numa linha a 48 px, mas o ajuste aceita o maior tamanho que cabe em <= 2 linhas.
    two_lines = fit_title("Pão quente hoje às 7h", "foto_titulo")
    assert two_lines == TitleFit(88, ("Pão quente hoje", "às 7h"))


def test_fit_title_shrinks_then_wraps_two_lines() -> None:
    title = "Promoção de verão até domingo"
    words = title.split()
    fitted = fit_title(title, "foto_titulo")
    assert fitted is not None
    assert 48 <= fitted.size < 88
    assert (fitted.size - 48) % 4 == 0
    assert len(fitted.lines) <= MAX_TITLE_LINES
    assert " ".join(fitted.lines) == title
    font = layouts.load_thread_font("inter-bold", fitted.size)
    assert all(font.getlength(line) <= 732 for line in fitted.lines)
    # E o maior tamanho possivel: um passo acima ja nao cabe em duas linhas.
    bigger = layouts.load_thread_font("inter-bold", fitted.size + 4)
    wrapped = layouts.wrap_words(words, bigger, 732)
    assert wrapped is None or len(wrapped) > MAX_TITLE_LINES


@pytest.mark.parametrize("layout", TITLE_LAYOUTS)
def test_fit_title_returns_none_when_it_does_not_fit(layout: LayoutName) -> None:
    assert fit_title("Supercalifragilisticexpialidocioso", layout) is None
    assert fit_title("Palavras compridas demais para duas linhas aqui", layout) is None
    assert fit_title("W" * 40, layout) is None


@pytest.mark.parametrize("layout", TITLE_LAYOUTS)
def test_fit_title_handles_exactly_forty_chars(layout: LayoutName) -> None:
    title = "Pao quente todos os dias as 7h da manha!"
    assert len(title) == MAX_TITLE_CHARS == 40
    fitted = fit_title(title, layout)
    assert fitted is not None
    assert " ".join(fitted.lines) == title
    rendered = render_layout(spec_for(layout, titulo=title))
    assert (rendered.width, rendered.height) == CANVAS


@pytest.mark.parametrize("layout", TITLE_LAYOUTS)
def test_title_over_40_chars_refused(layout: LayoutName) -> None:
    title = "x" * 20 + " segredo " + "y" * 12
    assert len(title) == 41
    with pytest.raises(TitleTooLong) as error:
        spec_for(layout, titulo=title)
    assert "segredo" not in str(error.value)
    assert isinstance(error.value, ValueError)
    assert fit_title(title, layout) is None


def test_title_length_counts_normalized_text() -> None:
    padded = "  " + "Pão quente às 7h" + "   \n"
    assert fit_title(padded, "foto_titulo") == fit_title("Pão quente às 7h", "foto_titulo")
    decomposed = "Pão quente"  # a + til combinante vira "ã" (NFC) antes de medir.
    assert fit_title(decomposed, "foto_titulo") == fit_title("Pão quente", "foto_titulo")


@pytest.mark.parametrize("layout", TITLE_LAYOUTS)
def test_unsupported_glyphs_and_controls_do_not_fit(layout: LayoutName) -> None:
    # O Inter nao tem emoji: recusa-se em vez de desenhar o quadrado de glifo em falta.
    assert fit_title("Pão quente 😀", layout) is None
    assert fit_title("Bom​dia", layout) is None
    with pytest.raises(TitleDoesNotFit):
        render_layout(spec_for(layout, titulo="Pão 😀"))


@pytest.mark.parametrize("layout", TITLE_LAYOUTS)
def test_accented_title_renders(layout: LayoutName) -> None:
    with_text = render_layout(spec_for(layout, titulo="Pão quente às 7h"))
    other_text = render_layout(spec_for(layout, titulo="Pao quente as 7h"))
    assert with_text.data != other_text.data
    assert decode(with_text.data).size == CANVAS


@pytest.mark.parametrize("layout", TITLE_LAYOUTS)
def test_title_that_cannot_fit_raises_without_echoing_it(layout: LayoutName) -> None:
    with pytest.raises(TitleDoesNotFit) as error:
        render_layout(spec_for(layout, titulo="Supercalifragilisticexpialidocioso"))
    assert "Supercali" not in str(error.value)


def test_foto_inteira_has_no_text_or_logo() -> None:
    bare = render_layout(spec_for("foto_inteira", titulo=None, logo=None))
    decorated = render_layout(spec_for("foto_inteira", titulo="Pão quente", logo=make_logo_png()))
    assert bare.data == decorated.data
    assert bare.sha256 == decorated.sha256


@pytest.mark.parametrize("layout", TITLE_LAYOUTS)
@pytest.mark.parametrize("titulo", [None, "", "   \n\t"])
def test_title_layouts_require_title(layout: LayoutName, titulo: str | None) -> None:
    with pytest.raises(InvalidLayoutInput) as error:
        render_layout(spec_for(layout, titulo=titulo))
    assert "exige" in str(error.value)


@pytest.mark.parametrize("color", ["red", "#FFF", "#GGGGGG", "#12345678", "#1F3A5F\n", " #1F3A5F"])
def test_invalid_colors_refused_with_fixed_message(color: str) -> None:
    for field in ("cor_primaria", "cor_secundaria"):
        with pytest.raises(InvalidLayoutInput) as error:
            replace(spec_for("foto_titulo"), **{field: color})
        assert color.strip() not in str(error.value) or color.strip() == ""


def test_lowercase_colors_render_like_uppercase() -> None:
    upper = render_layout(spec_for("foto_titulo"))
    lower = render_layout(
        replace(
            spec_for("foto_titulo"), cor_primaria=PRIMARY.lower(), cor_secundaria=SECONDARY.lower()
        )
    )
    assert upper.data == lower.data


def test_unknown_layout_refused() -> None:
    with pytest.raises(InvalidLayoutInput):
        LayoutInput("carrossel", b"x", None, PRIMARY, SECONDARY, None)  # type: ignore[arg-type]
    with pytest.raises(InvalidLayoutInput):
        fit_title("Titulo", "foto_inteira")
    with pytest.raises(InvalidLayoutInput):
        fit_title("Titulo", "carrossel")  # type: ignore[arg-type]


def test_hostile_images_are_refused_by_the_bounded_decoder() -> None:
    with pytest.raises(ImageRejected):
        render_layout(spec_for("foto_inteira", base=b"isto nao e uma imagem"))
    bomb = make_png(2, 2)
    huge = bomb.replace(b"\x00\x00\x00\x02\x00\x00\x00\x02", b"\x00\x00\x4e\x20\x00\x00\x4e\x20", 1)
    with pytest.raises(ImageRejected):
        render_layout(spec_for("foto_inteira", base=huge))
    with pytest.raises(ImageRejected):
        render_layout(spec_for("foto_titulo", logo=b"\x89PNG\r\n\x1a\nlixo"))
    with pytest.raises(ImageRejected):
        render_layout(spec_for("foto_titulo", logo=make_jpeg(100, 100)))


def logo_png(size: tuple[int, int], color: tuple[int, int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGBA", size, color).save(output, "PNG")
    return output.getvalue()


def changed_region(a: Image.Image, b: Image.Image) -> tuple[int, int, int, int] | None:
    return ImageChops.difference(a, b).getbbox()


@pytest.mark.parametrize(
    ("layout", "box"),
    [("foto_titulo", (836, 1098, 1016, 1278)), ("fundo_ia_titulo", (856, 1126, 1016, 1286))],
)
@pytest.mark.parametrize("size", [(96, 64), (64, 500), (700, 100), (1000, 1000), (64, 64)])
def test_logo_stays_inside_its_box(
    layout: LayoutName, box: tuple[int, int, int, int], size: tuple[int, int]
) -> None:
    base = gradient_background(b"fundo-logo")
    plain = decode(render_layout(spec_for(layout, base=base)).data)
    with_logo = decode(
        render_layout(spec_for(layout, base=base, logo=logo_png(size, (255, 0, 255, 255)))).data
    )
    bbox = changed_region(plain, with_logo)
    assert bbox is not None
    # A margem de 8 px cobre o espalhamento do bloco DCT 8x8 do JPEG sem subamostragem.
    assert bbox[0] >= box[0] - 8 and bbox[1] >= box[1] - 8
    assert bbox[2] <= box[2] + 8 and bbox[3] <= box[3] + 8
    assert bbox[2] <= CANVAS[0] - 64 + 8 and bbox[3] <= CANVAS[1]


def test_logo_is_never_upscaled_and_keeps_aspect_ratio() -> None:
    base = gradient_background(b"fundo-logo")
    plain = decode(render_layout(spec_for("fundo_ia_titulo", base=base)).data)
    small = decode(
        render_layout(
            spec_for("fundo_ia_titulo", base=base, logo=logo_png((64, 64), (255, 0, 255, 255)))
        ).data
    )
    wide = decode(
        render_layout(
            spec_for("fundo_ia_titulo", base=base, logo=logo_png((800, 100), (255, 0, 255, 255)))
        ).data
    )
    small_box = changed_region(plain, small)
    wide_box = changed_region(plain, wide)
    assert small_box is not None and wide_box is not None
    assert small_box[2] - small_box[0] <= 64 + 16  # nao ampliado para os 160 px da caixa.
    assert 160 - 16 <= wide_box[2] - wide_box[0] <= 160 + 16  # reduzido para a largura da caixa.
    assert wide_box[3] - wide_box[1] <= 20 + 16  # 800x100 numa caixa de 160 px tem 20 px de altura.


def test_logo_alpha_is_composited() -> None:
    base = gradient_background(b"fundo-logo")
    opaque = decode(
        render_layout(
            spec_for("fundo_ia_titulo", base=base, logo=logo_png((160, 160), (255, 0, 255, 255)))
        ).data
    )
    clear = decode(
        render_layout(
            spec_for("fundo_ia_titulo", base=base, logo=logo_png((160, 160), (255, 0, 255, 0)))
        ).data
    )
    plain = decode(render_layout(spec_for("fundo_ia_titulo", base=base)).data)
    assert opaque.getpixel((936, 1206)) == pytest.approx((255, 0, 255), abs=12)
    assert changed_region(plain, clear) is None


def test_title_and_logo_boxes_do_not_overlap() -> None:
    title_right = layouts.OUTER_MARGIN + layouts.TITLE_BOXES["foto_titulo"].width
    logo = layouts.logo_box("foto_titulo")
    assert title_right <= logo[0]
    assert logo[0] >= 0 and logo[1] >= layouts.BAND_TOP
    assert logo[2] <= CANVAS[0] and logo[3] <= CANVAS[1]
    corner = layouts.logo_box("fundo_ia_titulo")
    assert corner[2:] == (CANVAS[0] - 64, CANVAS[1] - 64)
    # O titulo centrado a 112 px (2 linhas) termina bem acima do logotipo do canto.
    assert corner[1] > CANVAS[1] // 2 + 2 * 112 * 115 // 100


def test_band_is_bottom_324_px_in_primary_color() -> None:
    image = decode(render_layout(spec_for("foto_titulo", base=make_jpeg(1600, 1200))).data)
    assert image.getpixel((540, 1030)) == pytest.approx((0x1F, 0x3A, 0x5F), abs=6)
    assert image.getpixel((5, 1349)) == pytest.approx((0x1F, 0x3A, 0x5F), abs=6)
    assert image.getpixel((540, 1020)) != pytest.approx((0x1F, 0x3A, 0x5F), abs=6)


def test_gradient_background_is_pure_png_1024x1536() -> None:
    seed = bytes([10, 20, 30, 200, 210, 220])
    first = gradient_background(seed)
    assert first == gradient_background(seed)
    assert first != gradient_background(bytes([10, 20, 30, 200, 210, 221]))
    image = decode(first)
    assert image.format == "PNG"
    assert image.size == (1024, 1536)
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (10, 20, 30)  # bytes 0-2: topo.
    assert image.getpixel((1023, 1535)) == (200, 210, 220)  # bytes 3-5: fundo.
    assert image.getpixel((512, 0)) == image.getpixel((0, 0))
    assert image.getpixel((0, 768)) == pytest.approx((105, 115, 125), abs=1)
    assert not image.info.get("icc_profile")


def test_gradient_background_ignores_extra_seed_bytes() -> None:
    assert gradient_background(b"abcdef") == gradient_background(b"abcdef-resto-ignorado")
    assert gradient_background(b"abcdef") != gradient_background(b"abcdeg")


@pytest.mark.parametrize("seed", [b"", b"12345"])
def test_gradient_background_refuses_short_seed(seed: bytes) -> None:
    with pytest.raises(ValueError, match="6 bytes") as error:
        gradient_background(seed)
    assert seed.decode() not in str(error.value) or seed == b""


def test_gradient_background_refuses_non_bytes_seed() -> None:
    with pytest.raises(ValueError):
        gradient_background("abcdefgh")  # type: ignore[arg-type]


def test_render_does_not_change_its_inputs() -> None:
    spec = golden_spec("foto_titulo")
    snapshot = (spec.base_image, spec.logo_png, spec.titulo)
    render_layout(spec)
    assert (spec.base_image, spec.logo_png, spec.titulo) == snapshot
    with pytest.raises(FrozenInstanceError):
        spec.titulo = "outro"  # type: ignore[misc]


def render_in_threads(spec: LayoutInput, workers: int) -> list[RenderedImage]:
    barrier = Barrier(workers, timeout=30)

    def work() -> RenderedImage:
        barrier.wait()
        return render_layout(spec)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work) for _ in range(workers * 2)]
        return [future.result(timeout=120) for future in futures]


@pytest.mark.parametrize("layout", TITLE_LAYOUTS)
def test_concurrent_renders_are_byte_identical(layout: LayoutName) -> None:
    spec = golden_spec(layout)
    expected = render_layout(spec)
    results = render_in_threads(spec, workers=4)
    assert len(results) == 8
    assert all(result == expected for result in results)


def test_concurrent_renders_of_different_titles_do_not_mix() -> None:
    specs = [spec_for("foto_titulo", titulo=title) for title in ("Pão quente", "Bolo de ontem")]
    expected = [render_layout(spec) for spec in specs]
    barrier = Barrier(4, timeout=30)

    def work(index: int) -> RenderedImage:
        barrier.wait()
        return render_layout(specs[index % 2])

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(work, index) for index in range(8)]
        results = [future.result(timeout=120) for future in futures]
    assert all(result == expected[index % 2] for index, result in enumerate(results))


def test_layout_names_and_constants_are_consistent() -> None:
    assert set(LAYOUT_VERSIONS) == set(LAYOUT_NAMES)
    assert layouts.TITLE_BOXES["foto_titulo"].width == 732
    assert layouts.TITLE_BOXES["fundo_ia_titulo"].width == 888
