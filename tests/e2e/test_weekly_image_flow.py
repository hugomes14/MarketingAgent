"""Pacote de imagem pelo painel: biblioteca, kit, plano, worker e decisoes."""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from image_factory import make_jpeg, make_logo_png
from panel_rig import LOGIN, PanelRig, Script, scripted
from PIL import Image
from sqlalchemy import select
from tests.e2e.test_weekly_text_flow import WeeklyRig

from marketing_agent.budget.pricing import ImagePrice, ModelPrice, PriceTier, sync_model_prices
from marketing_agent.db.models import AuditLog, CostEntry, Post
from marketing_agent.llm import fake_scenarios


def test_weekly_image_flow(make_panel: Callable[[Script], PanelRig]) -> None:
    panel = make_panel(
        [
            *fake_scenarios.interview_full(3),
            *fake_scenarios.diagnosis_and_strategy(),
        ]
    )
    panel.approve_strategy()
    origin = panel.headers({"Origin": "http://testserver"})
    for number, subject in enumerate(("produto", "pessoas")):
        response = panel.client.post(
            "/biblioteca",
            headers=origin,
            follow_redirects=False,
            data={
                "csrf_token": panel.token(),
                "descricao": f"Foto {number}",
                "mostra": subject,
                "origem": "propria",
                "tem_autorizacao_pessoas": "true",
            },
            files={
                "ficheiro": (
                    "foto.jpg",
                    make_jpeg(1080 + number * 100, 1350),
                    "image/jpeg",
                )
            },
        )
        assert response.status_code == 303
    photos = panel.media.library.list_photos(panel.brand_id)
    assert len(photos) == 2
    assert (
        panel.client.post(
            "/kit",
            headers=origin,
            follow_redirects=False,
            data={
                "csrf_token": panel.token(),
                "cor_primaria": "#000000",
                "cor_secundaria": "#FFFFFF",
            },
            files={"logotipo": ("logo.png", make_logo_png(), "image/png")},
        ).status_code
        == 303
    )
    script = [*fake_scenarios.weekly_plan("2026-W46", 3)]
    for number in range(3):
        script.extend(fake_scenarios.caption_draft())
        script.extend(
            fake_scenarios.visual_draft(
                **(
                    {
                        "layout": "foto_titulo",
                        "foto_id": photos[0].id,
                        "tema_fundo": None,
                        "estilo": None,
                    }
                    if number == 0
                    else {}
                )
            )
        )
        if number == 2:
            script.extend(
                fake_scenarios.review("rever", ["pessoa_ou_produto_gerado"], imagem_verificada=True)
            )
            script.extend(fake_scenarios.caption_draft())
            script.extend(fake_scenarios.visual_draft())
        script.extend(fake_scenarios.review("aprovar", imagem_verificada=True))
    panel.llm._script.extend(scripted(script))
    sync_model_prices(
        panel.factory,
        [
            ModelPrice(
                "fake",
                "fake-image-1",
                "images-e2e",
                (PriceTier(0, Decimal(0), Decimal(0)),),
                panel.clock.now() - timedelta(days=1),
                tuple(
                    ImagePrice(quality, "1024x1536", Decimal("0.04"))
                    for quality in ("low", "medium", "high")
                ),
            )
        ],
    )
    rig = WeeklyRig(panel)
    rig.propose_and_approve_plan()
    assert rig.drain() == 3
    posts = rig.posts()
    revisions = [rig.revisions(post.id)[-1] for post in posts]
    assert sorted(rev.layout for rev in revisions) == [
        "foto_titulo",
        "fundo_ia_titulo",
        "fundo_ia_titulo",
    ]
    assert sorted(rev.image_cost_usd for rev in revisions) == [
        Decimal(0),
        Decimal("0.04"),
        Decimal("0.08"),
    ]
    with panel.factory() as session:
        image_entries = [
            entry for entry in session.scalars(select(CostEntry)) if "images" in entry.units
        ]
        assert len(image_entries) == 3
        assert all(entry.cost_usd == Decimal("0.04") for entry in image_entries)
    for revision in revisions:
        response = panel.get(f"/media/{revision.final_asset_id}")
        assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
        with Image.open(BytesIO(response.content)) as image:
            assert image.size == (1080, 1350) and not image.getexif()
    base = revisions[0]
    assert (
        panel.post(
            f"/post/{base.post_id}/trocar-foto",
            {
                "revisao_id": base.id,
                "foto_id": photos[1].id,
                "alt_text": "Outra fotografia.",
            },
        ).status_code
        == 303
    )
    swapped = rig.revisions(base.post_id)[-1]
    assert not swapped.is_ai_generated and not swapped.review_image_checked
    target = revisions[1]
    assert (
        panel.client.post(
            f"/post/{target.post_id}/imagem",
            headers=origin,
            follow_redirects=False,
            data={
                "csrf_token": panel.token(),
                "revisao_id": target.id,
                "alt_text": "Imagem manual.",
                "gerada_por_ia": "nao",
                "origem": "propria",
                "mostra": "produto",
            },
            files={"ficheiro": ("manual.jpg", make_jpeg(1200, 1350), "image/jpeg")},
        ).status_code
        == 303
    )
    panel.llm._script.extend(scripted(fake_scenarios.review("aprovar", imagem_verificada=True)))
    assert rig.drain() == 1
    manual = rig.revisions(target.post_id)[-1]
    assert manual.origin == "upload" and manual.review_image_checked and not manual.is_ai_generated
    assert (
        panel.post(f"/post/{target.post_id}/aprovar", {"revision_id": manual.id}).status_code == 303
    )
    with panel.factory() as session:
        assert session.get_one(Post, target.post_id).lifecycle == "approved"
        actions = list(session.scalars(select(AuditLog.action).where(AuditLog.actor == LOGIN)))
    assert "post.swap_photo" in actions and "post.manual_image" in actions
    assert "Gerada por IA" in rig.batch()
