"""Acoes de imagem pelo HTTP: trocar foto e carregar imagem a mao, so em revisao."""

from collections.abc import Callable
from dataclasses import dataclass, replace

import pytest
from httpx import Response
from image_factory import make_jpeg, make_logo_png
from panel_rig import LOGIN, PanelRig, Script
from sqlalchemy import select
from test_batch_routes import CAPTION, lote, seed_post

from marketing_agent.content.revision_store import canonical_revision_hash
from marketing_agent.db.models import AuditLog, Job, MediaAsset, Post, PostRevision
from marketing_agent.images.layouts import LayoutInput, render_layout
from marketing_agent.jobs.queue import REVIEW_POST_JOB
from marketing_agent.media.brand_kit import BrandKitInput
from marketing_agent.media.ingest import NormalizedImage
from marketing_agent.media.library import PhotoUpload
from marketing_agent.panel.routes_batch import AI_DECLARATION_MESSAGE

MakePanel = Callable[[Script], PanelRig]
FREE_TEXT = "TEXTO-LIVRE-DO-RESPONSAVEL"
TITLE = "Pao do dia"


@dataclass(frozen=True)
class ImagePost:
    """Publicacao com uma imagem `foto_titulo` e as fotos da biblioteca."""

    post_id: str
    revision_id: str
    photo_ids: tuple[str, str]


def seed_image_post(
    rig: PanelRig, lifecycle: str = "in_review", stored_title: str = TITLE, **seed: object
) -> ImagePost:
    """Duas fotos, kit e um post cuja unica revisao tem a imagem composta com a 1.a foto."""
    library, kits = rig.media.library, rig.media.kits
    photos = [
        library.upload_photo(
            rig.brand_id,
            make_jpeg(1080 + number * 100, 1350),
            PhotoUpload(
                mostra="produto",
                descricao=f"Foto {number}",
                origem="propria",
                tem_autorizacao_pessoas=False,
            ),
            LOGIN,
        ).id
        for number in range(2)
    ]
    kit = kits.save(
        rig.brand_id, BrandKitInput(cor_primaria="#000000", cor_secundaria="#FFFFFF"),
        make_logo_png(), LOGIN,
    )  # fmt: skip
    seeded = seed_post(rig, lifecycle=lifecycle, with_revision=False, **seed)  # type: ignore[arg-type]
    final = render_layout(
        LayoutInput(
            "foto_titulo",
            library.read_asset(rig.brand_id, photos[0]).data,
            TITLE,
            kit.cor_primaria,
            kit.cor_secundaria,
            kits.logo_png(rig.brand_id, kit),
        )
    )
    hashtags = ["#pao"]
    with rig.factory.begin() as session:
        asset_id = library.store_asset_in(
            session,
            rig.brand_id,
            NormalizedImage(final.data, "image/jpeg", final.width, final.height, final.sha256),
            kind="final",
            origem="composta",
            actor=LOGIN,
        )
        revision = PostRevision(
            brand_id=rig.brand_id,
            post_id=seeded.post_id,
            rev_no=1,
            revision_hash=canonical_revision_hash(
                caption=CAPTION,
                hashtags=hashtags,
                call_to_action="Passe pela loja.",
                alt_text="Pao artesanal.",
                claims=[],
                channels=["instagram"],
                final_image_sha256=final.sha256,
            ),
            caption=CAPTION,
            hashtags=hashtags,
            call_to_action="Passe pela loja.",
            alt_text="Pao artesanal.",
            claims=[],
            review_verdict="aprovar",
            review_violations=[],
            review_notes="Parecer automatico.",
            final_asset_id=asset_id,
            layout="foto_titulo",
            layout_version=final.layout_version,
            components=[
                {"tipo": "fotografia", "ref": photos[0], "asset_id": photos[0]},
                {"tipo": "logotipo", "ref": kit.logo_asset_id, "asset_id": kit.logo_asset_id},
                {"tipo": "texto", "ref": None, "titulo": stored_title},
            ],
            origin="agente",
            created_at=rig.clock.now(),
        )
        session.add(revision)
        session.flush()
        return ImagePost(seeded.post_id, revision.id, (photos[0], photos[1]))


def swap_form(base: ImagePost, **extra: str) -> dict[str, str]:
    return {
        "revisao_id": base.revision_id,
        "foto_id": base.photo_ids[1],
        "alt_text": FREE_TEXT,
        **extra,
    }


def upload(
    rig: PanelRig,
    base: ImagePost,
    *,
    data: bytes | None = None,
    field: str = "ficheiro",
    **fields: str,
) -> Response:
    form = {
        "csrf_token": rig.token(),
        "revisao_id": base.revision_id,
        "alt_text": FREE_TEXT,
        "gerada_por_ia": "nao",
        "origem": "propria",
        "mostra": "produto",
        **fields,
    }
    form = {name: value for name, value in form.items() if value != "<omitir>"}
    return rig.client.post(
        f"/post/{base.post_id}/imagem",
        headers=rig.headers({"Origin": "http://testserver"}),
        follow_redirects=False,
        data=form,
        files={field: ("imagem.jpg", data or make_jpeg(1080, 1350), "image/jpeg")},
    )


def revisions(rig: PanelRig, post_id: str) -> list[PostRevision]:
    with rig.factory() as session:
        stmt = select(PostRevision).where(PostRevision.post_id == post_id)
        return list(session.scalars(stmt.order_by(PostRevision.rev_no)))


def audits(rig: PanelRig, action: str) -> list[AuditLog]:
    with rig.factory() as session:
        return list(session.scalars(select(AuditLog).where(AuditLog.action == action)))


def count_state(rig: PanelRig) -> tuple[int, int, int]:
    with rig.factory() as session:
        return (
            len(list(session.scalars(select(PostRevision.id)))),
            len(list(session.scalars(select(MediaAsset.id)))),
            len(list(session.scalars(select(Job.id)))),
        )


# --- fluxos felizes ---------------------------------------------------------------------------


def test_swap_photo_flow(make_panel: MakePanel) -> None:
    rig = make_panel([])
    base = seed_image_post(rig)
    response = rig.post(f"/post/{base.post_id}/trocar-foto", swap_form(base))
    assert response.status_code == 303

    first, swapped = revisions(rig, base.post_id)
    assert (swapped.rev_no, swapped.origin) == (2, "edicao_responsavel")
    assert (swapped.layout, swapped.is_ai_generated) == ("foto_titulo", False)
    assert (swapped.review_verdict, swapped.review_image_checked) == (None, False)
    assert (swapped.image_cost_usd, swapped.package_cost_usd) == (0, 0)
    assert swapped.alt_text == FREE_TEXT and swapped.caption == CAPTION
    assert swapped.components[0]["ref"] == base.photo_ids[1]
    with rig.factory() as session:
        final = session.get_one(MediaAsset, swapped.final_asset_id)
        post = session.get_one(Post, base.post_id)
    assert (final.kind, final.origem) == ("final", "composta")
    assert swapped.final_asset_id != first.final_asset_id
    assert swapped.revision_hash == canonical_revision_hash(
        caption=swapped.caption,
        hashtags=swapped.hashtags,
        call_to_action=swapped.call_to_action,
        alt_text=swapped.alt_text,
        claims=swapped.claims,
        channels=["instagram"],
        final_image_sha256=final.sha256,
    )
    assert swapped.revision_hash != first.revision_hash
    assert (post.lifecycle, post.last_error) == ("in_review", None)
    [audit] = audits(rig, "post.swap_photo")
    assert audit.actor == LOGIN and FREE_TEXT not in str(audit.detail)
    assert audit.detail == {
        "revision_id": swapped.id,
        "base_revision_id": first.id,
        "photo_id": base.photo_ids[1],
    }
    # A mesma troca outra vez (mesma foto e mesmo texto) nao cria revisao.
    again = {**swap_form(base), "revisao_id": swapped.id}
    assert rig.post(f"/post/{base.post_id}/trocar-foto", again).status_code == 422
    assert len(revisions(rig, base.post_id)) == 2


@pytest.mark.parametrize("is_ai", ["sim", "nao"])
def test_upload_manual_image_flow(make_panel: MakePanel, is_ai: str) -> None:
    rig = make_panel([])
    base = seed_image_post(rig)
    response = upload(rig, base, gerada_por_ia=is_ai, licenca_nota=FREE_TEXT)
    assert response.status_code == 303

    assert len(revisions(rig, base.post_id)) == 1  # a revisao nasce no worker, depois do parecer
    with rig.factory() as session:
        job = session.scalars(select(Job).where(Job.type == REVIEW_POST_JOB)).one()
        asset = session.get_one(MediaAsset, job.payload["asset_id"])
        post = session.get_one(Post, base.post_id)
    assert job.status == "queued" and job.last_error is None
    assert job.dedupe_key == f"{REVIEW_POST_JOB}:{base.post_id}:{asset.id}"
    assert job.payload["post_id"] == base.post_id
    assert job.payload["base_revision_id"] == base.revision_id
    assert job.payload["meta"]["gerada_por_ia"] is (is_ai == "sim")  # type: ignore[index,call-overload]
    assert (asset.kind, asset.origem) == ("final", "gerada" if is_ai == "sim" else "propria")
    assert (post.lifecycle, post.last_error) == ("drafting", None)
    [audit] = audits(rig, "post.manual_image")
    assert audit.actor == LOGIN
    assert audit.detail == {"base_revision_id": base.revision_id, "asset_id": asset.id}
    assert FREE_TEXT not in str(audit.detail)


# --- so em revisao ----------------------------------------------------------------------------


@pytest.mark.parametrize("lifecycle", ["approved", "rejected", "cancelled"])
def test_swap_and_upload_are_refused_and_hidden_on_decided_posts(
    make_panel: MakePanel, lifecycle: str
) -> None:
    rig = make_panel([])
    base = seed_image_post(rig, lifecycle)
    html = rig.get(lote()).text
    assert CAPTION in html and "<form" not in html
    assert f"/post/{base.post_id}/trocar-foto" not in html
    assert f"/post/{base.post_id}/imagem" not in html
    before = count_state(rig)
    assert rig.post(f"/post/{base.post_id}/trocar-foto", swap_form(base)).status_code == 409
    assert upload(rig, base).status_code == 409
    assert count_state(rig) == before
    with rig.factory() as session:
        assert session.get_one(Post, base.post_id).lifecycle == lifecycle


def test_actions_are_offered_in_review_and_on_a_failed_drafting_post(make_panel: MakePanel) -> None:
    rig = make_panel([])
    in_review = seed_image_post(rig, slot=1)  # type: ignore[call-arg]
    failed = seed_post(rig, 2, lifecycle="drafting", last_error="NeedsHuman")
    html = rig.get(lote()).text
    for action in ("trocar-foto", "imagem"):
        assert f"/post/{in_review.post_id}/{action}" in html
    assert f"/post/{failed.post_id}/imagem" in html  # sem imagem final: so o upload
    assert f"/post/{failed.post_id}/trocar-foto" not in html


# --- upload: declaracoes e fronteiras ---------------------------------------------------------


def test_upload_without_the_ai_declaration_is_refused(make_panel: MakePanel) -> None:
    rig = make_panel([])
    base = seed_image_post(rig)
    before = count_state(rig)
    response = upload(rig, base, gerada_por_ia="<omitir>")
    assert response.status_code == 422
    assert AI_DECLARATION_MESSAGE in response.text
    assert count_state(rig) == before


def test_fake_image_bytes_with_a_valid_declaration_are_rejected(make_panel: MakePanel) -> None:
    rig = make_panel([])
    base = seed_image_post(rig)
    before = count_state(rig)
    response = upload(rig, base, data=b"isto nao e uma imagem")
    assert response.status_code == 422
    # A razao fixa do `ImageRejected`, nao a mensagem generica de media nem texto do pedido.
    assert "Formato de imagem não permitido." in response.text
    assert count_state(rig) == before


def test_upload_must_use_its_own_file_field(make_panel: MakePanel) -> None:
    rig = make_panel([])
    base = seed_image_post(rig)
    before = count_state(rig)
    response = upload(rig, base, field="logotipo")
    assert response.status_code == 422
    assert count_state(rig) == before


def test_upload_of_people_without_authorization_is_refused(make_panel: MakePanel) -> None:
    rig = make_panel([])
    base = seed_image_post(rig)
    before = count_state(rig)
    response = upload(rig, base, mostra="pessoas")  # sem a caixa de autorizacao
    assert response.status_code == 422
    assert count_state(rig) == before
    allowed = upload(rig, base, mostra="pessoas", tem_autorizacao_pessoas="true")
    assert allowed.status_code == 303


def test_swap_to_people_without_authorization_is_refused(
    make_panel: MakePanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A base impede a foto (CHECK); o servico recusa-a se a biblioteca a devolver."""
    rig = make_panel([])
    base = seed_image_post(rig)
    library = rig.content.review._library  # noqa: SLF001
    assert library is not None
    photo = library.photo_refs(rig.brand_id)[base.photo_ids[1]]
    bad = replace(photo, mostra="pessoas", tem_autorizacao_pessoas=False)
    monkeypatch.setattr(library, "active_photo", lambda brand_id, asset_id: bad)
    before = count_state(rig)
    response = rig.post(f"/post/{base.post_id}/trocar-foto", swap_form(base))
    assert response.status_code == 422
    assert count_state(rig) == before


def test_layout_failure_during_swap_is_a_fixed_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    base = seed_image_post(rig, stored_title="x" * 500)  # titulo acima do limite do layout
    before = count_state(rig)
    response = rig.post(f"/post/{base.post_id}/trocar-foto", swap_form(base))
    assert response.status_code == 422
    assert "Não foi possível compor a imagem" in response.text
    assert "x" * 50 not in response.text
    assert count_state(rig) == before
