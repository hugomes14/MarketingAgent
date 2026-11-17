"""Kit de marca no painel: formulario, contraste, pre-visualizacao e fronteiras de seguranca."""

import re
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from threading import Barrier
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from httpx import Response
from image_factory import make_jpeg, make_logo_png, make_png
from panel_rig import LOGIN, PEER, PanelRig
from PIL import Image
from sqlalchemy import Engine, event, func, select
from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext

from marketing_agent.db.models import (
    AgentRun,
    AuditLog,
    BrandKit,
    CostReservation,
    MediaAsset,
)
from marketing_agent.images.layouts import (
    LAYOUT_VERSIONS,
    LayoutInput,
    LayoutName,
    fit_title,
    gradient_background,
    render_layout,
)
from marketing_agent.media.brand_kit import MIN_TITLE_CONTRAST, BrandKitInput
from marketing_agent.media.library import PhotoUpload
from marketing_agent.panel import csrf, routes_kit

MakePanel = Callable[..., PanelRig]
LAYOUTS: tuple[LayoutName, ...] = ("foto_inteira", "foto_titulo", "fundo_ia_titulo")
TITLE_LAYOUTS: tuple[LayoutName, ...] = ("foto_titulo", "fundo_ia_titulo")
DARK, LIGHT = "#102A43", "#F0F4F8"


def kit_post(rig: PanelRig, *, logo: bytes | None = None, **fields: str) -> Response:
    """POST /kit como o browser: token no campo, cores e logotipo opcional em multipart."""
    data = {"csrf_token": rig.token(), "cor_primaria": DARK, "cor_secundaria": LIGHT, **fields}
    files = {"logotipo": ("logo.png", logo, "image/png")} if logo is not None else None
    return rig.client.post(
        "/kit",
        headers=rig.headers({"Origin": "http://testserver"}),
        data=data,
        files=files or {"logotipo": ("", b"", "application/octet-stream")},
        follow_redirects=False,
    )


def add_photo(rig: PanelRig, description: str = "Foto de exemplo", *, side: int = 1080) -> str:
    meta = PhotoUpload(
        mostra="produto", descricao=description, origem="propria", tem_autorizacao_pessoas=False
    )
    return rig.media.library.upload_photo(rig.brand_id, make_jpeg(side, side), meta, LOGIN).id


def add_kit(rig: PanelRig, primary: str = DARK, secondary: str = LIGHT) -> None:
    kit = BrandKitInput(cor_primaria=primary, cor_secundaria=secondary)
    rig.media.kits.save(rig.brand_id, kit, make_logo_png(), LOGIN)


def ready_rig(make_panel: MakePanel) -> PanelRig:
    rig = make_panel([])
    add_kit(rig)
    add_photo(rig)
    return rig


def count(rig: PanelRig, model: type) -> int:
    with rig.factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


@contextmanager
def captured_sql(rig: PanelRig) -> Iterator[list[str]]:
    """Todas as instrucoes SQL executadas no bloco (o motor e o da fabrica de sessoes)."""
    engine = rig.factory.kw["bind"]
    assert isinstance(engine, Engine)
    statements: list[str] = []

    def record(
        connection: object,
        cursor: DBAPICursor,
        statement: str,
        parameters: object,
        context: ExecutionContext | None,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def decode(data: bytes) -> Image.Image:
    image = Image.open(BytesIO(data))
    image.load()
    return image


# ---------------------------------------------------------------- autenticacao e CSRF


def test_every_new_route_requires_auth_and_csrf(make_panel: MakePanel) -> None:
    rig = make_panel([])
    routes = [
        route
        for route in iter_route_contexts(rig.app.routes)
        if route.path and route.path.startswith("/kit")
    ]
    # A contagem vem do proprio router: uma rota nova entra na verificacao sem editar o teste.
    assert len(routes) == len(routes_kit.router.routes) == 4
    for route in routes:
        assert route.path and route.methods
        path = route.path.replace("{layout}", "foto_titulo")
        for method in route.methods:
            assert rig.client.request(method, path).status_code == 401
            intruder = {"Tailscale-User-Login": "intruso@example.com"}
            assert rig.client.request(method, path, headers=intruder).status_code == 403
            if method == "POST":
                assert rig.client.post(path, headers=rig.headers()).status_code == 403
                bad = {"csrf_token": "x" * 64}
                assert rig.client.post(path, headers=rig.headers(), data=bad).status_code == 403


def test_kit_upload_requires_auth_before_parsing(
    make_panel: MakePanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = make_panel([])
    spy = AsyncMock(side_effect=AssertionError("Leitura antes da autenticacao"))
    monkeypatch.setattr(csrf, "read_upload_form", spy)
    monkeypatch.setattr(routes_kit, "read_upload_form", spy)
    response = rig.client.post("/kit", files={"logotipo": ("l.png", b"invalid")})
    assert response.status_code == 401
    spy.assert_not_called()


def test_kit_csrf_token_from_field_or_header(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert kit_post(rig, logo=make_logo_png()).status_code == 303
    assert kit_post(rig, csrf_token="errado").status_code == 403
    assert kit_post(rig, csrf_token="").status_code == 403
    headers = rig.headers({"X-CSRF-Token": rig.token()})
    response = rig.client.post(
        "/kit",
        headers=headers,
        data={"cor_primaria": DARK, "cor_secundaria": LIGHT},
        files={"logotipo": ("", b"", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    cross = rig.client.post("/kit", headers={**headers, "Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403


# ---------------------------------------------------------------- guardar o kit


def test_save_kit_with_logo_and_colors(make_panel: MakePanel) -> None:
    rig = make_panel([])
    response = kit_post(rig, logo=make_logo_png(), cor_primaria="#102a43")
    assert response.status_code == 303 and response.headers["location"] == "/kit"
    with rig.factory() as session:
        kit = session.scalars(select(BrandKit)).one()
        logo = session.get_one(MediaAsset, kit.logo_asset_id)
        audit = session.scalars(select(AuditLog).where(AuditLog.action == "brand_kit.save")).one()
    assert (kit.version, kit.cor_primaria, kit.cor_secundaria) == (1, DARK, LIGHT)
    assert kit.created_by == LOGIN and logo.kind == "logotipo" and logo.mime == "image/png"
    assert audit.actor == LOGIN and audit.subject_id == kit.id
    assert set(audit.detail) == {"version", "cor_primaria", "cor_secundaria"}
    page = rig.get("/kit").text
    assert f"/media/{logo.id}" in page and DARK in page and LIGHT in page
    assert "versão 1" in page.lower()


def test_new_version_without_logo_keeps_the_previous_logo(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert kit_post(rig, logo=make_logo_png()).status_code == 303
    assert kit_post(rig, cor_primaria="#003366").status_code == 303
    with rig.factory() as session:
        kits = list(session.scalars(select(BrandKit).order_by(BrandKit.version)))
    assert [kit.version for kit in kits] == [1, 2]
    assert kits[0].logo_asset_id == kits[1].logo_asset_id
    assert (kits[0].cor_primaria, kits[1].cor_primaria) == (DARK, "#003366")


def test_first_kit_without_logo_is_422_and_creates_nothing(make_panel: MakePanel) -> None:
    rig = make_panel([])
    response = kit_post(rig)
    assert response.status_code == 422
    assert "O primeiro kit exige um logótipo." in response.text
    assert count(rig, BrandKit) == count(rig, MediaAsset) == 0


def test_actor_comes_from_the_session_not_from_the_form(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert (
        kit_post(rig, logo=make_logo_png(), actor="intruso", created_by="intruso").status_code
        == 303
    )
    with rig.factory() as session:
        assert session.scalars(select(BrandKit)).one().created_by == LOGIN


def test_low_contrast_returns_422_with_ratio(make_panel: MakePanel) -> None:
    rig = make_panel([])
    response = kit_post(rig, logo=make_logo_png(), cor_primaria="#777777", cor_secundaria="#FFFFFF")
    assert response.status_code == 422
    # 4.478 trunca para 4.47 (nunca 4.48 nem 4.5), ao lado do minimo exigido.
    assert "Contraste 4.47: o título exige pelo menos 4.5." in response.text
    assert count(rig, BrandKit) == 0 and count(rig, MediaAsset) == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"cor_primaria": "vermelho"},
        {"cor_primaria": "#12345"},
        {"cor_secundaria": '"><script>alert(1)</script>'},
        {"cor_primaria": "#GGGGGG"},
        {"cor_secundaria": "#FFFFFF\n"},
    ],
)
def test_invalid_color_is_422_without_echo(make_panel: MakePanel, fields: dict[str, str]) -> None:
    rig = make_panel([])
    response = kit_post(rig, logo=make_logo_png(), **fields)
    assert response.status_code == 422
    assert "A cor deve usar o formato #RRGGBB." in response.text
    assert "<script>alert" not in response.text and "vermelho" not in response.text
    assert count(rig, BrandKit) == 0 and count(rig, MediaAsset) == 0


def test_missing_color_field_is_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    response = rig.client.post(
        "/kit",
        headers=rig.headers(),
        data={"csrf_token": rig.token(), "cor_primaria": DARK},
        files={"logotipo": ("l.png", make_logo_png(), "image/png")},
    )
    assert response.status_code == 422 and "A cor deve usar o formato #RRGGBB." in response.text
    assert count(rig, BrandKit) == 0


@pytest.mark.parametrize(
    ("logo", "message"),
    [
        (b"PRIVATE_NOT_AN_IMAGE", "Formato de imagem não permitido."),
        (make_jpeg(200, 200), "Formato de imagem não permitido."),
        (make_png(96, 64), "O logótipo precisa de ser PNG com transparência."),
        (make_png(32, 32, alpha=True), "O logótipo precisa de ter entre 64 e 4096 píxeis"),
        (b"\x89PNG\r\n\x1a\n" + bytes(2 * 1024 * 1024), "O logótipo excede o limite de 2 MB."),
    ],
)
def test_invalid_logo_is_422_with_fixed_message_and_no_partial_kit(
    make_panel: MakePanel, logo: bytes, message: str
) -> None:
    rig = make_panel([])
    response = kit_post(rig, logo=logo)
    assert response.status_code == 422
    assert message in response.text and "PRIVATE_NOT_AN_IMAGE" not in response.text
    assert count(rig, BrandKit) == 0 and count(rig, MediaAsset) == 0
    assert count(rig, AuditLog) == 0


def test_invalid_logo_keeps_the_existing_kit(make_panel: MakePanel) -> None:
    rig = ready_rig(make_panel)
    assert kit_post(rig, logo=b"nao e imagem").status_code == 422
    assert count(rig, BrandKit) == 1 and count(rig, MediaAsset) == 2  # logotipo e foto


def test_kit_form_without_multipart_is_415(make_panel: MakePanel) -> None:
    rig = make_panel([])
    response = rig.post("/kit", {"cor_primaria": DARK, "cor_secundaria": LIGHT})
    assert response.status_code == 415
    assert count(rig, BrandKit) == 0


def test_two_simultaneous_saves_make_two_consecutive_versions(make_panel: MakePanel) -> None:
    rig = make_panel([])
    barrier = Barrier(2, timeout=10)
    logo = make_logo_png()

    def save(primary: str) -> int:
        # Um cliente por thread: o TestClient partilhado nao garante pedidos concorrentes.
        client = TestClient(rig.app, client=(PEER, 5000))
        barrier.wait()
        response = client.post(
            "/kit",
            headers=rig.headers({"X-CSRF-Token": rig.token()}),
            data={"cor_primaria": primary, "cor_secundaria": LIGHT},
            files={"logotipo": ("logo.png", logo, "image/png")},
            follow_redirects=False,
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, color) for color in ("#102A43", "#003366")]
        statuses = [future.result(timeout=60) for future in futures]
    assert statuses == [303, 303]
    with rig.factory() as session:
        versions = list(session.scalars(select(BrandKit.version).order_by(BrandKit.version)))
        assets = session.scalar(
            select(func.count()).select_from(MediaAsset).where(MediaAsset.kind == "logotipo")
        )
    assert versions == [1, 2] and assets == 1


# ---------------------------------------------------------------- pagina /kit


def test_kit_page_without_kit_explains_what_is_missing(make_panel: MakePanel) -> None:
    rig = make_panel([])
    page = rig.get("/kit").text
    assert "Ainda não há kit de marca" in page
    assert "/kit/previa/" not in page
    assert 'enctype="multipart/form-data"' in page and 'name="logotipo"' in page
    assert 'name="csrf_token"' in page


def test_kit_page_with_kit_but_no_photo_explains_the_photo(make_panel: MakePanel) -> None:
    rig = make_panel([])
    add_kit(rig)
    page = rig.get("/kit").text
    assert "Falta uma fotografia ativa" in page and 'href="/biblioteca"' in page
    assert "/kit/previa/" not in page
    assert "Ainda não há kit de marca" not in page


def test_kit_page_with_photo_but_no_kit_explains_the_kit(make_panel: MakePanel) -> None:
    rig = make_panel([])
    add_photo(rig)
    page = rig.get("/kit").text
    assert "Ainda não há kit de marca" in page and "/kit/previa/" not in page


def test_kit_page_lists_previews_form_and_contrast_fragment(make_panel: MakePanel) -> None:
    rig = ready_rig(make_panel)
    page = rig.get("/kit").text
    for layout in LAYOUTS:
        assert f'src="/kit/previa/{layout}"' in page
    assert page.count('type="color"') == 2
    assert 'name="cor_primaria"' in page and 'name="cor_secundaria"' in page
    assert 'hx-get="/kit/contraste"' in page and 'hx-trigger="change"' in page
    assert 'hx-target="#contraste"' in page and 'id="contraste"' in page
    assert "Cumpre" in page or "cumpre" in page  # contraste do kit ativo
    assert "<script" not in page.replace('<script src="/static/', "")
    assert "style=" not in page and "onclick" not in page


def test_kit_page_history_is_bounded_to_the_last_twenty(make_panel: MakePanel) -> None:
    rig = make_panel([])
    add_kit(rig)
    for index in range(24):
        color = f"#{index:02X}2A43"
        rig.media.kits.save(
            rig.brand_id, BrandKitInput(cor_primaria=color, cor_secundaria=LIGHT), None, LOGIN
        )
    page = rig.get("/kit").text
    assert page.count('class="versao-kit"') == 20
    assert "Versão 25" in page and "Versão 6" in page and "Versão 5<" not in page


def test_photo_selector_escapes_descriptions_and_is_bounded(make_panel: MakePanel) -> None:
    rig = make_panel([])
    add_kit(rig)
    evil = add_photo(rig, '<script>alert("x")</script>', side=1080)
    page = rig.get("/kit").text
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert f'value="{evil}"' in page and 'name="foto"' in page


def test_photo_selector_lists_at_most_twenty_photos(make_panel: MakePanel) -> None:
    rig = make_panel([])
    add_kit(rig)
    for index in range(22):
        add_photo(rig, f"Foto numero {index}", side=1080 + index)
    page = rig.get("/kit").text
    assert len(re.findall(r'<option value="[0-9a-f-]{36}"', page)) == 20


def test_kit_page_selected_photo_goes_to_the_previews(make_panel: MakePanel) -> None:
    rig = ready_rig(make_panel)
    chosen = add_photo(rig, "Escolhida", side=1100)
    page = rig.get(f"/kit?foto={chosen}").text
    for layout in LAYOUTS:
        assert f"/kit/previa/{layout}?foto={chosen}" in page
    assert "selected" in page


def test_kit_page_unknown_or_foreign_photo_is_404(make_panel: MakePanel) -> None:
    rig = ready_rig(make_panel)
    other = rig.new_brand()
    foreign = rig.media.library.upload_photo(
        other,
        make_jpeg(1080, 1080),
        PhotoUpload(
            mostra="produto", descricao="Segredo", origem="propria", tem_autorizacao_pessoas=False
        ),
        LOGIN,
    )
    assert rig.get(f"/kit?foto={foreign.id}").status_code == 404
    assert rig.get(f"/kit?foto={uuid4()}").status_code == 404
    assert rig.get("/kit?foto=nao-e-um-id").status_code == 422
    assert "Segredo" not in rig.get("/kit").text


# ---------------------------------------------------------------- contraste


def test_contrast_fragment(make_panel: MakePanel) -> None:
    rig = make_panel([])
    ok = rig.get("/kit/contraste?cor_primaria=%23000000&cor_secundaria=%23FFFFFF")
    assert ok.status_code == 200 and "21.00" in ok.text and "cumpre" in ok.text.lower()
    assert "não cumpre" not in ok.text and str(MIN_TITLE_CONTRAST) in ok.text
    low = rig.get("/kit/contraste?cor_primaria=%23777777&cor_secundaria=%23ffffff")
    assert low.status_code == 200 and "4.47" in low.text and "não cumpre" in low.text
    assert "<html" not in ok.text  # fragmento, nao pagina
    assert ok.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "query",
    [
        "cor_primaria=vermelho&cor_secundaria=%23FFFFFF",
        "cor_primaria=%23FFF&cor_secundaria=%23FFFFFF",
        "cor_primaria=%23000000",
        "",
        "cor_primaria=%3Cscript%3Ealert(1)%3C/script%3E&cor_secundaria=%23FFFFFF",
        "cor_primaria=%23000000%0A&cor_secundaria=%23FFFFFF",
    ],
)
def test_contrast_fragment_invalid_colors(make_panel: MakePanel, query: str) -> None:
    rig = make_panel([])
    response = rig.get(f"/kit/contraste?{query}")
    # 200 com aviso no proprio alvo: um valor a meio da escrita nao e um erro do pedido.
    assert response.status_code == 200
    assert "Cor inválida" in response.text and "#RRGGBB" in response.text
    assert "<script>" not in response.text and "vermelho" not in response.text
    assert "cumpre" not in response.text.lower().replace("cor inválida", "")


def test_contrast_fragment_does_not_touch_the_database(make_panel: MakePanel) -> None:
    rig = make_panel([])
    audit_before = count(rig, AuditLog)
    with captured_sql(rig) as statements:
        rig.get("/kit/contraste?cor_primaria=%23000000&cor_secundaria=%23FFFFFF")
        rig.get("/kit/contraste?cor_primaria=x&cor_secundaria=y")
    assert statements == []
    assert count(rig, AuditLog) == audit_before


def test_contrast_threshold_is_the_services(make_panel: MakePanel) -> None:
    rig = make_panel([])
    # #767676 sobre branco e o primeiro cinzento que passa; #777777 o ultimo que falha.
    assert (
        "não cumpre"
        not in rig.get("/kit/contraste?cor_primaria=%23767676&cor_secundaria=%23FFFFFF").text
    )
    assert (
        "não cumpre"
        in rig.get("/kit/contraste?cor_primaria=%23777777&cor_secundaria=%23FFFFFF").text
    )


# ---------------------------------------------------------------- pre-visualizacao


def test_preview_title_fits_every_title_layout() -> None:
    for layout in TITLE_LAYOUTS:
        assert fit_title(routes_kit.PREVIEW_TITLE, layout) is not None, layout


@pytest.mark.parametrize("layout", LAYOUTS)
def test_preview_three_layouts_are_jpeg_1080x1350(make_panel: MakePanel, layout: str) -> None:
    rig = ready_rig(make_panel)
    response = rig.get(f"/kit/previa/{layout}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    image = decode(response.content)
    assert image.format == "JPEG" and image.size == (1080, 1350)


def test_preview_is_produced_by_the_production_renderer(make_panel: MakePanel) -> None:
    rig = ready_rig(make_panel)
    kit = rig.media.kits.active(rig.brand_id)
    assert kit is not None
    [photo_id] = rig.media.library.photo_refs(rig.brand_id)
    photo = rig.media.library.read_asset(rig.brand_id, photo_id).data
    logo = rig.media.kits.logo_png(rig.brand_id, kit)
    for layout in LAYOUTS:
        base = gradient_background(b"previa") if layout == "fundo_ia_titulo" else photo
        expected = render_layout(
            LayoutInput(
                layout, base, routes_kit.PREVIEW_TITLE, kit.cor_primaria, kit.cor_secundaria, logo
            )
        )
        assert rig.get(f"/kit/previa/{layout}").content == expected.data, layout
    assert routes_kit.PREVIEW_TITLE == "Exemplo de título da marca"


def test_preview_uses_the_requested_photo_else_the_latest(make_panel: MakePanel) -> None:
    rig = make_panel([])
    add_kit(rig)
    older = add_photo(rig, "Antiga", side=1080)
    rig.clock.advance(timedelta(minutes=1))
    newer = add_photo(rig, "Nova", side=1200)
    by_default = rig.get("/kit/previa/foto_inteira").content
    assert by_default == rig.get(f"/kit/previa/foto_inteira?foto={newer}").content
    assert by_default != rig.get(f"/kit/previa/foto_inteira?foto={older}").content


def test_ai_background_preview_ignores_the_photo(make_panel: MakePanel) -> None:
    rig = make_panel([])
    add_kit(rig)
    first, second = add_photo(rig, "A", side=1080), add_photo(rig, "B", side=1200)
    url = "/kit/previa/fundo_ia_titulo"
    assert rig.get(f"{url}?foto={first}").content == rig.get(f"{url}?foto={second}").content


def test_preview_without_kit_or_photo_explains(make_panel: MakePanel) -> None:
    rig = make_panel([])
    for layout in LAYOUTS:
        assert rig.get(f"/kit/previa/{layout}").status_code == 404
    assert "Ainda não há kit de marca" in rig.get("/kit").text
    add_kit(rig)
    response = rig.get("/kit/previa/foto_titulo")
    assert response.status_code == 404 and "Não encontrado." in response.text
    assert "Falta uma fotografia ativa" in rig.get("/kit").text
    add_photo(rig)
    assert rig.get("/kit/previa/foto_titulo").status_code == 200


def test_preview_with_photo_but_without_kit_is_404(make_panel: MakePanel) -> None:
    rig = make_panel([])
    add_photo(rig)
    assert rig.get("/kit/previa/foto_titulo").status_code == 404


def test_preview_other_brand_unknown_inactive_and_malformed_photo(make_panel: MakePanel) -> None:
    rig = ready_rig(make_panel)
    foreign = rig.media.library.upload_photo(
        rig.new_brand(),
        make_jpeg(1080, 1080),
        PhotoUpload(
            mostra="produto", descricao="Segredo", origem="propria", tem_autorizacao_pessoas=False
        ),
        LOGIN,
    )
    unknown = rig.get(f"/kit/previa/foto_titulo?foto={uuid4()}")
    other = rig.get(f"/kit/previa/foto_titulo?foto={foreign.id}")
    # Mesma resposta para "nao existe" e "e de outra marca": sem fuga de informacao.
    assert (unknown.status_code, other.status_code) == (404, 404)
    assert unknown.text == other.text
    inactive = add_photo(rig, "Retirada", side=1300)
    assert rig.post(f"/biblioteca/{inactive}/desativar").status_code == 303
    assert rig.get(f"/kit/previa/foto_titulo?foto={inactive}").status_code == 404
    logo_id = rig.media.kits.active(rig.brand_id).logo_asset_id  # type: ignore[union-attr]
    assert rig.get(f"/kit/previa/foto_titulo?foto={logo_id}").status_code == 404
    assert rig.get("/kit/previa/foto_titulo?foto=../../etc/passwd").status_code == 422


@pytest.mark.parametrize("layout", ["manual", "FOTO_TITULO", "foto_titulo.jpg", "inventado", "%20"])
def test_preview_unknown_layout_is_422_with_the_fixed_message(
    make_panel: MakePanel, layout: str
) -> None:
    rig = ready_rig(make_panel)
    response = rig.get(f"/kit/previa/{layout}")
    assert response.status_code == 422 and "Pedido inválido." in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_preview_creates_no_run_or_reservation(make_panel: MakePanel) -> None:
    rig = ready_rig(make_panel)
    models = (AgentRun, CostReservation, AuditLog, BrandKit, MediaAsset)
    before = [count(rig, model) for model in models]
    with captured_sql(rig) as statements:
        for layout in LAYOUTS:
            assert rig.get(f"/kit/previa/{layout}").status_code == 200
        assert rig.get("/kit/previa/foto_titulo?foto=" + str(uuid4())).status_code == 404
        assert rig.get("/kit").status_code == 200
        rig.get("/kit/contraste?cor_primaria=%23000000&cor_secundaria=%23FFFFFF")
    assert [count(rig, model) for model in models] == before
    writes = [
        s
        for s in statements
        if not s.lstrip().upper().startswith(("SELECT", "BEGIN", "ROLLBACK", "COMMIT"))
    ]
    assert writes == []


def test_unexpected_render_failure_leaks_nothing_and_frees_the_slot(
    make_panel: MakePanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = ready_rig(make_panel)
    quiet = TestClient(rig.app, client=(PEER, 5000), raise_server_exceptions=False)
    real_render = routes_kit.render_layout

    def explode(spec: LayoutInput) -> object:
        raise RuntimeError("SEGREDO-INTERNO")

    monkeypatch.setattr(routes_kit, "render_layout", explode)
    for _ in range(3):  # mais falhas do que ranhuras: uma ranhura presa bloquearia a terceira
        response = quiet.get("/kit/previa/foto_titulo", headers=rig.headers())
        assert response.status_code == 500 and "SEGREDO-INTERNO" not in response.text
    monkeypatch.setattr(routes_kit, "render_layout", real_render)
    assert rig.get("/kit/previa/foto_titulo").status_code == 200


def test_layout_names_match_the_renderer() -> None:
    assert set(LAYOUTS) == set(LAYOUT_VERSIONS)
    assert set(routes_kit.LAYOUT_NAMES) == set(LAYOUTS)


# ---------------------------------------------------------------- inicio e navegacao


def test_home_shows_kit_status(make_panel: MakePanel) -> None:
    rig = make_panel([])
    home = rig.get("/").text
    assert "sem kit" in home.lower() and 'href="/kit"' in home and 'href="/biblioteca"' in home
    add_kit(rig)
    home = rig.get("/").text
    assert "versão 1" in home.lower() and "2026-11-05" in home
    assert "sem kit" not in home.lower()
    add_kit(rig, "#003366")
    assert "versão 2" in rig.get("/").text.lower()


def test_navigation_links_the_kit_and_library(make_panel: MakePanel) -> None:
    rig = make_panel([])
    for path in ("/", "/kit", "/biblioteca"):
        page = rig.get(path).text
        assert 'href="/kit"' in page and 'href="/biblioteca"' in page, path


def test_kit_page_has_no_inline_script_or_style(make_panel: MakePanel) -> None:
    rig = ready_rig(make_panel)
    for path in ("/kit", "/kit/contraste?cor_primaria=%23000000&cor_secundaria=%23FFFFFF"):
        text = rig.get(path).text
        assert not re.search(r"<script(?![^>]*\ssrc=)", text), path
        assert "<style" not in text and " style=" not in text and " onchange=" not in text, path
    csp = rig.get("/kit").headers["content-security-policy"]
    assert "script-src 'self'" in csp
