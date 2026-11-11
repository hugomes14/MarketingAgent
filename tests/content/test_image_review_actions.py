"""Acoes de imagem: hash, revisoes, obsolescencia e despacho do worker."""

from pathlib import Path

import pytest
from content_rig import ContentRig, Factory, build_rig, package_pass
from image_factory import make_jpeg

from marketing_agent.clock import FixedClock
from marketing_agent.content.manual_image import ManualImage
from marketing_agent.content.review import CaptionEdit, InvalidEdit, ReviewService, StaleRevision
from marketing_agent.content.revision_store import canonical_revision_hash
from marketing_agent.db.models import PostRevision
from marketing_agent.jobs.worker import Worker
from marketing_agent.llm import fake_scenarios

type Ready = tuple[ContentRig, ReviewService, str, PostRevision]


@pytest.fixture
def ready(session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> Ready:
    rig = build_rig(session_factory, clock, brand_id, package_pass(), media_dir=tmp_path)
    post_id = rig.seed_post()
    rig.enqueue(post_id)
    assert Worker(rig.queue, rig.pipeline, "w").run_once()
    [base] = rig.revisions(post_id)
    service = ReviewService(
        rig.factory,
        rig.feedback,
        rig.queue,
        rig.kb,
        rig.clock,
        max_comment_chars=200,
        library=rig.library,
        kits=rig.kits,
    )
    return rig, service, post_id, base


def test_edit_keeps_image_and_hash_and_rejects_unchanged(ready: Ready) -> None:
    rig, service, post_id, base = ready
    unchanged = CaptionEdit(
        legenda=base.caption,
        hashtags=base.hashtags,
        apelo_a_acao=base.call_to_action or "",
        alt_text=base.alt_text,
    )
    with pytest.raises(InvalidEdit):
        service.edit(rig.brand_id, post_id, base.id, unchanged, "nao_usar", None, None, "dono")
    edited = unchanged.model_copy(update={"legenda": "Pao fresco na loja."})
    service.edit(rig.brand_id, post_id, base.id, edited, "nao_usar", None, None, "dono")
    latest = rig.revisions(post_id)[-1]
    assert latest.final_asset_id == base.final_asset_id
    assert latest.components == base.components
    assert latest.is_ai_generated and not latest.review_image_checked
    asset = next(a for a in rig.assets("final") if a.id == latest.final_asset_id)
    assert latest.revision_hash == canonical_revision_hash(
        caption=latest.caption,
        hashtags=latest.hashtags,
        call_to_action=latest.call_to_action,
        alt_text=latest.alt_text,
        claims=latest.claims,
        channels=["instagram"],
        final_image_sha256=asset.sha256,
    )


def test_swap_photo_without_llm_and_stale_base_refused(ready: Ready) -> None:
    rig, service, post_id, base = ready
    calls = len(rig.llm.requests)
    service.swap_photo(rig.brand_id, post_id, base.id, rig.photo_id, "Fotografia de pao.", "dono")
    latest = rig.revisions(post_id)[-1]
    assert len(rig.llm.requests) == calls
    assert latest.layout == "foto_titulo" and not latest.is_ai_generated
    assert latest.final_asset_id != base.final_asset_id
    assert latest.review_verdict is None and not latest.review_image_checked
    assert latest.components[-1]["titulo"] == base.components[-1]["titulo"]
    with pytest.raises(StaleRevision):
        service.swap_photo(rig.brand_id, post_id, base.id, rig.photo_id, "Outra foto.", "dono")


@pytest.mark.parametrize("failed", [False, True])
def test_manual_upload_worker_dispatch_and_review_failure(ready: Ready, failed: bool) -> None:
    rig, service, post_id, base = ready
    meta = ManualImage(
        alt_text="Imagem manual.",
        gerada_por_ia=True,
        origem="propria",
        mostra="outro",
        tem_autorizacao_pessoas=False,
    )
    job_id = service.attach_manual_image(
        rig.brand_id, post_id, base.id, make_jpeg(1080, 1350), meta, "dono"
    )
    rig.llm.load(
        [RuntimeError("SEGREDO")]
        if failed
        else fake_scenarios.review("aprovar", imagem_verificada=True)
    )
    calls = len(rig.llm.requests)
    assert Worker(rig.queue, rig.pipeline, "w").run_once()
    latest = rig.revisions(post_id)[-1]
    assert rig.job(job_id).status == "succeeded"
    assert latest.origin == "upload" and latest.layout == "manual"
    assert latest.is_ai_generated and latest.caption == base.caption
    assert rig.post(post_id).lifecycle == "in_review"
    if failed:
        assert latest.review_verdict is None and "SEGREDO" not in latest.review_notes
    else:
        assert latest.review_image_checked and latest.review_verdict == "aprovar"
    assert len(rig.llm.requests) == calls + 1


def test_manual_review_does_not_overwrite_cancelled_post(ready: Ready) -> None:
    rig, service, post_id, base = ready
    meta = ManualImage(
        alt_text="Imagem manual.",
        gerada_por_ia=False,
        origem="propria",
        mostra="produto",
        tem_autorizacao_pessoas=False,
    )
    job_id = service.attach_manual_image(
        rig.brand_id, post_id, base.id, make_jpeg(1080, 1350), meta, "dono"
    )

    def cancel_during_review() -> None:
        from marketing_agent.db.models import Post

        with rig.factory.begin() as session:
            session.get_one(Post, post_id).lifecycle = "cancelled"

    rig.llm.load(fake_scenarios.review("aprovar", imagem_verificada=True))
    rig.llm.hooks[len(rig.llm.requests) + 1] = cancel_during_review
    Worker(rig.queue, rig.pipeline, "w").run_once()
    assert len(rig.revisions(post_id)) == 1
    assert rig.post(post_id).lifecycle == "cancelled"
    assert rig.job(job_id).status == "succeeded"


def test_unknown_worker_job_fails_without_retry(ready: Ready) -> None:
    rig, _, post_id, _ = ready
    with rig.factory.begin() as session:
        job_id = rig.queue.enqueue_in(session, "unknown", {"post_id": post_id}, "unknown")
    assert job_id is not None
    assert Worker(rig.queue, rig.pipeline, "w").run_once()
    assert rig.job(job_id).status == "failed"
