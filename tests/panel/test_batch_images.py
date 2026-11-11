"""Imagem do pacote no lote: apresentacao, proveniencia, custos e escape."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from panel_rig import PanelRig, Script
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import FixedClock
from marketing_agent.db.models import PostRevision
from marketing_agent.panel.queries_batch import _image_view
from marketing_agent.panel.rendering import build_environment


def revision(**fields: object) -> PostRevision:
    """Revisao em memoria: o teste nao precisa de gerar bytes ou chamar modelos."""
    values = {
        "final_asset_id": "asset-final",
        "layout": "fundo_ia_titulo",
        "components": [{"tipo": "gerada", "ref": "step-1", "modelo": "image-model"}],
        "is_ai_generated": True,
        "review_image_checked": True,
        "image_cost_usd": Decimal("0.04"),
        "package_cost_usd": Decimal("0.12"),
        **fields,
    }
    return PostRevision(**values)


def render(stored: PostRevision | None, error: str | None = None) -> str:
    image = _image_view(stored, error, {"photo-1": '<script>alert("photo")</script>'})
    return (
        build_environment()
        .get_template("_post_image.html")
        .render(
            card=SimpleNamespace(image=image, lifecycle="cancelled", can_edit=False),
            rev=SimpleNamespace(alt_text='" onerror="alert(1)') if stored else None,
        )
    )


def test_card_shows_image_layout_and_components() -> None:
    html = render(revision())
    assert 'src="/media/asset-final"' in html
    assert 'width="1080" height="1350"' in html
    assert "Fundo gerado por IA com título" in html
    assert "image-model" in html
    assert "Imagem verificada pelo Revisor: sim" in html


def test_ai_badge_always_visible_when_generated() -> None:
    html = render(revision())
    assert ">Gerada por IA</strong>" in html
    assert "<details" not in html
    assert ">Gerada por IA</strong>" not in render(revision(is_ai_generated=False))


def test_card_shows_image_cost_and_package_cost() -> None:
    assert "Custo do pacote: 0.1200 USD (imagem: 0.0400 USD)" in render(revision())


def test_missing_image_shows_reason() -> None:
    html = render(revision(final_asset_id=None, layout=None), "imagem: geração recusada")
    assert "Sem imagem: imagem: geração recusada" in html
    assert "<img" not in html
    assert "Sem imagem: ainda por gerar" in render(None)


def test_image_unchecked_flagged() -> None:
    html = render(revision(review_image_checked=False))
    assert 'class="alerta">Imagem verificada pelo Revisor: não' in html


def test_alt_text_and_descriptions_escaped() -> None:
    html = render(revision(components=[{"tipo": "fotografia", "ref": "photo-1"}]))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'alt="&#34; onerror=&#34;alert(1)"' in html
    html = render(revision(components=[{"tipo": "gerada", "modelo": "<svg onload=x>"}]))
    assert "<svg" not in html and "&lt;svg" in html


@pytest.mark.parametrize("layout", ["foto_inteira", "foto_titulo", "manual"])
def test_all_layouts_have_labels(layout: str) -> None:
    html = render(revision(layout=layout))
    assert f"Layout: {layout}" not in html


def test_complete_card_accepts_image_view() -> None:
    """O fragmento efetivamente incluido no lote recebe a revisao e a imagem."""
    stored = revision()
    rev = SimpleNamespace(
        id="revision-1",
        rev_no=1,
        origin="agente",
        caption="Legenda",
        hashtags=[],
        call_to_action=None,
        alt_text="Imagem",
        claims=[],
        verdict="rever",
        violations=["pessoa_ou_produto_gerado"],
        notes="Parecer",
        created_at=datetime(2026, 11, 11, tzinfo=UTC),
    )
    card = SimpleNamespace(
        id="post-1",
        scheduled_date=datetime(2026, 11, 11).date(),
        theme="Tema",
        channels=["instagram"],
        lifecycle="cancelled",
        review_rounds=1,
        revision=rev,
        image=_image_view(stored, None, {}),
        decisions=[],
        last_error=None,
        can_retry=False,
        can_edit=False,
        can_decide=False,
        can_request_changes=False,
    )
    html = build_environment().get_template("_post_card.html").render(card=card, csrf_token="x")
    assert 'src="/media/asset-final"' in html
    assert "pessoa ou produto gerado por IA" in html


def test_batch_image_fields_loaded_from_database(
    make_panel: Callable[[Script], PanelRig],
) -> None:
    from test_batch_routes import lote, seed_post

    rig = make_panel([])
    seeded = seed_post(rig, with_revision=False)
    with rig.factory.begin() as session:
        stored = PostRevision(
            brand_id=rig.brand_id,
            post_id=seeded.post_id,
            rev_no=1,
            revision_hash="a" * 64,
            caption="Legenda",
            hashtags=[],
            claims=[],
            review_violations=[],
            origin="agente",
            created_at=rig.clock.now(),
        )
        session.add(stored)
        stored.image_cost_usd = Decimal("0.04")
        stored.package_cost_usd = Decimal("0.12")
        stored.review_image_checked = False
        stored.is_ai_generated = True
        stored.components = [{"tipo": "gerada", "ref": "step-1", "modelo": "image-model"}]
    html = rig.get(lote()).text
    assert "Custo do pacote: 0.1200 USD (imagem: 0.0400 USD)" in html
    assert ">Gerada por IA</strong>" in html
    assert "Sem imagem:" in html


def test_photo_descriptions_are_brand_scoped_and_include_inactive_assets(
    session_factory: sessionmaker[Session], brand_id: str, clock: FixedClock
) -> None:
    from marketing_agent.db.models import Brand, MediaAsset
    from marketing_agent.panel.queries_batch import _asset_descriptions

    with session_factory.begin() as session:
        other = Brand(name="Outra marca")
        session.add(other)
        session.flush()
        ids = []
        for number, brand in enumerate((brand_id, other.id)):
            asset = MediaAsset(
                brand_id=brand,
                kind="foto",
                storage_backend="local",
                storage_key=f"photo-{number}",
                mime="image/jpeg",
                width=1080,
                height=1350,
                bytes=100,
                sha256=str(number) * 64,
                mostra="produto",
                descricao="Foto desativada" if number == 0 else "DESCRICAO-ALHEIA",
                origem="banco_licenciado",
                is_active=False,
                created_by="dono",
                created_at=clock.now(),
            )
            session.add(asset)
            session.flush()
            ids.append(asset.id)
        stored = revision(components=[{"tipo": "fotografia", "ref": ref} for ref in ids])
        descriptions = _asset_descriptions(session, brand_id, [stored])
        assert descriptions == {ids[0]: "Banco licenciado: Foto desativada"}
        assert "DESCRICAO-ALHEIA" not in str(_image_view(stored, None, descriptions))


def test_image_actions_require_auth_csrf_and_explicit_ai(
    make_panel: Callable[[Script], PanelRig],
) -> None:
    from test_batch_routes import seed_post

    rig = make_panel([])
    base = seed_post(rig)
    for action in ("trocar-foto", "imagem"):
        path = f"/post/{base.post_id}/{action}"
        assert rig.client.post(path).status_code == 401
        assert rig.client.post(path, headers=rig.headers()).status_code == 403
    headers = rig.headers({"Origin": "http://testserver"})
    response = rig.client.post(
        f"/post/{base.post_id}/imagem",
        headers=headers,
        data={"csrf_token": rig.token(), "revisao_id": base.revision_id},
        files={"ficheiro": ("foto.jpg", b"invalido", "image/jpeg")},
    )
    assert response.status_code == 422
