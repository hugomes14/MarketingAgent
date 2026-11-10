"""Biblioteca real: autenticacao, CSRF, metadados, media privada e fronteira de marca."""

from collections.abc import Callable
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.routing import iter_route_contexts
from httpx import Response
from image_factory import make_jpeg, make_logo_png
from panel_rig import LOGIN, PanelRig

from marketing_agent.media.ingest import normalize_logo
from marketing_agent.media.library import PhotoUpload
from marketing_agent.panel import csrf, routes_media

MakePanel = Callable[..., PanelRig]


def upload(rig: PanelRig, **fields: str) -> Response:
    """Upload autentico do browser: token no campo e imagem JPEG sintetica."""
    return rig.client.post(
        "/biblioteca",
        headers=rig.headers({"Origin": "http://testserver"}),
        data={
            "csrf_token": rig.token(),
            "mostra": "produto",
            "descricao": "Foto teste",
            "origem": "propria",
            **fields,
        },
        files={"ficheiro": ("foto.jpg", make_jpeg(1080, 1080), "image/jpeg")},
        follow_redirects=False,
    )


def test_every_new_route_requires_auth_and_csrf(make_panel: MakePanel) -> None:
    rig = make_panel([])
    routes = [
        route
        for route in iter_route_contexts(rig.app.routes)
        if route.path and route.path.startswith(("/biblioteca", "/media"))
    ]
    # A contagem vem do proprio router: uma rota nova entra na verificacao sem editar o teste.
    assert len(routes) == len(routes_media.router.routes) > 0
    for route in routes:
        assert route.path and route.methods
        path = route.path.replace("{asset_id}", str(uuid4()))
        for method in route.methods:
            assert rig.client.request(method, path).status_code == 401
            assert (
                rig.client.request(
                    method,
                    path,
                    headers={
                        "Tailscale-User-Login": "intruso@example.com",
                    },
                ).status_code
                == 403
            )
            if method == "POST":
                assert rig.client.post(path, headers=rig.headers()).status_code == 403


def test_upload_requires_auth_before_parsing(
    make_panel: MakePanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = make_panel([])
    spy = AsyncMock(side_effect=AssertionError("Leitura antes da autenticacao"))
    monkeypatch.setattr(csrf, "read_upload_form", spy)
    monkeypatch.setattr(routes_media, "read_upload_form", spy)
    response = rig.client.post("/biblioteca", files={"ficheiro": ("f.jpg", b"invalid")})
    assert response.status_code == 401
    spy.assert_not_called()


def test_multipart_csrf_token_from_field(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert upload(rig).status_code == 303
    assert upload(rig, csrf_token="errado").status_code == 403
    assert upload(rig, csrf_token="").status_code == 403


def test_upload_photo_and_see_it_in_library(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert upload(rig, descricao='<script>alert("x")</script>').status_code == 303
    photos = rig.media.library.list_photos(rig.brand_id)
    assert len(photos) == 1
    response = rig.get("/biblioteca")
    assert response.status_code == 200
    assert f"/media/{photos[0].id}" in response.text
    assert 'loading="lazy"' in response.text
    assert "<script>alert" not in response.text and "&lt;script&gt;" in response.text
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="tem_autorizacao_pessoas"' in response.text and "checked" not in response.text
    assert 'href="/biblioteca"' in rig.get("/").text


def test_fake_extension_upload_returns_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    response = rig.client.post(
        "/biblioteca",
        headers=rig.headers(),
        data={
            "csrf_token": rig.token(),
            "mostra": "produto",
            "descricao": "Teste",
            "origem": "propria",
        },
        files={"ficheiro": ("foto.jpg", b"PRIVATE_NOT_AN_IMAGE", "image/jpeg")},
    )
    assert response.status_code == 422
    assert "Formato de imagem não permitido." in response.text
    assert "PRIVATE_NOT_AN_IMAGE" not in response.text


def test_people_without_authorization_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert upload(rig, mostra="pessoas").status_code == 422
    assert upload(rig, mostra="pessoas", tem_autorizacao_pessoas="on").status_code == 303


@pytest.mark.parametrize(
    "fields",
    [
        {"mostra": "inventado"},
        {"origem": "roubada"},
        {"descricao": ""},
        {"descricao": "x" * 301},
        {"licenca_nota": "x" * 301},
        {"tem_autorizacao_pessoas": "talvez"},
    ],
)
def test_invalid_metadata_is_422(make_panel: MakePanel, fields: dict[str, str]) -> None:
    assert upload(make_panel([]), **fields).status_code == 422


def test_library_filters_by_subject(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert upload(rig).status_code == 303
    assert "Foto teste" in rig.get("/biblioteca?mostra=produto").text
    assert "Foto teste" not in rig.get("/biblioteca?mostra=pessoas").text
    assert rig.get("/biblioteca?mostra=inventado").status_code == 422


def test_deactivate_photo(make_panel: MakePanel) -> None:
    rig = make_panel([])
    upload(rig)
    photo = rig.media.library.list_photos(rig.brand_id)[0]
    assert rig.post(f"/biblioteca/{photo.id}/desativar").status_code == 303
    assert rig.media.library.list_photos(rig.brand_id) == []
    assert f"/media/{photo.id}" not in rig.get("/biblioteca").text
    assert rig.get(f"/media/{photo.id}").status_code == 200


def test_reupload_of_deactivated_photo_makes_it_reappear_in_library(make_panel: MakePanel) -> None:
    rig = make_panel([])
    upload(rig)
    photo = rig.media.library.list_photos(rig.brand_id)[0]
    rig.post(f"/biblioteca/{photo.id}/desativar")
    assert f"/media/{photo.id}" not in rig.get("/biblioteca").text
    assert upload(rig, descricao="Outra descricao").status_code == 303
    assert f"/media/{photo.id}" in rig.get("/biblioteca").text
    assert [item.descricao for item in rig.media.library.list_photos(rig.brand_id)] == [
        "Foto teste"
    ]


def test_get_media_requires_auth(make_panel: MakePanel) -> None:
    rig = make_panel([])
    upload(rig)
    photo = rig.media.library.list_photos(rig.brand_id)[0]
    assert rig.client.get(f"/media/{photo.id}").status_code == 401


@pytest.mark.parametrize("mime", ["image/jpeg", "image/png"])
def test_get_media_fixed_content_type_and_headers(make_panel: MakePanel, mime: str) -> None:
    rig = make_panel([])
    if mime == "image/jpeg":
        upload(rig)
        asset_id = rig.media.library.list_photos(rig.brand_id)[0].id
    else:
        with rig.factory.begin() as session:
            asset_id = rig.media.library.store_asset_in(
                session,
                rig.brand_id,
                normalize_logo(make_logo_png()),
                kind="logotipo",
                origem="propria",
                actor=LOGIN,
            )
    response = rig.get(f"/media/{asset_id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == mime
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private"
    assert response.headers["content-disposition"] == "inline"
    assert response.content == rig.media.library.read_asset(rig.brand_id, asset_id).data


def test_get_media_other_brand_404(make_panel: MakePanel) -> None:
    rig = make_panel([])
    photo = rig.media.library.upload_photo(
        rig.new_brand(),
        make_jpeg(1080, 1080),
        PhotoUpload(
            mostra="produto",
            descricao="Segredo",
            origem="propria",
            tem_autorizacao_pessoas=False,
        ),
        LOGIN,
    )
    assert rig.get(f"/media/{photo.id}").status_code == 404
    assert rig.post(f"/biblioteca/{photo.id}/desativar").status_code == 404
    assert "Segredo" not in rig.get("/biblioteca").text
    assert rig.get(f"/media/{uuid4()}").status_code == 404


def test_multipart_header_token_and_origin_rules(make_panel: MakePanel) -> None:
    rig = make_panel([])
    arguments = {
        "data": {
            "mostra": "produto",
            "descricao": "Foto",
            "origem": "propria",
            "csrf_token": "bad",
        },
        "files": {"ficheiro": ("foto.html", make_jpeg(1080, 1080), "text/html")},
    }
    headers = rig.headers({"X-CSRF-Token": rig.token()})
    assert (
        rig.client.post(
            "/biblioteca", headers=headers, follow_redirects=False, **arguments
        ).status_code
        == 303
    )
    assert (
        rig.client.post(
            "/biblioteca", headers={**headers, "Origin": "https://evil.test"}, **arguments
        ).status_code
        == 403
    )
    assert (
        rig.client.post(
            "/biblioteca", headers={**headers, "Sec-Fetch-Site": "cross-site"}, **arguments
        ).status_code
        == 403
    )
    assert (
        rig.client.post(
            "/biblioteca", headers={**headers, "X-CSRF-Token": "bad"}, **arguments
        ).status_code
        == 403
    )


def test_upload_needs_file_and_named_file_field(make_panel: MakePanel) -> None:
    rig = make_panel([])
    headers = rig.headers({"X-CSRF-Token": rig.token()})
    assert (
        rig.client.post(
            "/biblioteca", headers=headers, files={"descricao": (None, "Foto")}
        ).status_code
        == 422
    )
    assert (
        rig.client.post(
            "/biblioteca", headers=headers, files={"errado": ("f.jpg", b"x")}
        ).status_code
        == 400
    )
    assert (
        rig.client.post("/biblioteca", headers=headers, data={"descricao": "Foto"}).status_code
        == 415
    )
