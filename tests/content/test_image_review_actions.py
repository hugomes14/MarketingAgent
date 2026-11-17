"""Acoes de imagem: hash, revisoes, obsolescencia e despacho do worker."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from content_rig import ContentRig, Factory, build_rig, package_pass
from image_factory import make_jpeg
from pydantic import ValidationError
from sqlalchemy import func, select, update

from marketing_agent.clock import FixedClock
from marketing_agent.content.manual_image import ManualImage
from marketing_agent.content.package_costs import job_costs_in
from marketing_agent.content.review import (
    CaptionEdit,
    InvalidEdit,
    InvalidPostState,
    ReviewService,
    StaleRevision,
)
from marketing_agent.content.revision_store import canonical_revision_hash
from marketing_agent.db.models import AuditLog, Brand, Job, MediaAsset, Post, PostRevision
from marketing_agent.jobs.queue import REVIEW_POST_JOB
from marketing_agent.jobs.worker import Worker
from marketing_agent.llm import fake_scenarios

type Ready = tuple[ContentRig, ReviewService, str, PostRevision]


def make_ready(
    session_factory: Factory,
    clock: FixedClock,
    brand_id: str,
    tmp_path: Path,
    image_price: str = "0",
) -> Ready:
    """Pacote gerado e em revisao, com o servico de revisao ligado a media do rig."""
    rig = build_rig(
        session_factory,
        clock,
        brand_id,
        package_pass(),
        media_dir=tmp_path,
        image_price=image_price,
    )
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


@pytest.fixture
def ready(session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> Ready:
    return make_ready(session_factory, clock, brand_id, tmp_path)


@pytest.fixture
def priced(session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> Ready:
    """Como `ready`, mas a imagem gerada custou 0.04 USD (a revisao base tem custos)."""
    return make_ready(session_factory, clock, brand_id, tmp_path, "0.04")


def manual_meta(**fields: object) -> ManualImage:
    values: dict[str, object] = {
        "alt_text": "Imagem manual.",
        "gerada_por_ia": False,
        "origem": "propria",
        "mostra": "produto",
        "tem_autorizacao_pessoas": False,
        **fields,
    }
    return ManualImage.model_validate(values)


def force_lifecycle(rig: ContentRig, post_id: str, lifecycle: str, last_error: str | None) -> None:
    with rig.factory.begin() as session:
        session.execute(
            update(Post)
            .where(Post.id == post_id)
            .values(lifecycle=lifecycle, last_error=last_error)
        )


def count_rows(rig: ContentRig) -> tuple[int, int, int]:
    """(revisoes, ativos, trabalhos): nada disto pode crescer numa acao recusada."""
    with rig.factory() as session:
        return tuple(  # type: ignore[return-value]
            session.scalar(select(func.count()).select_from(model)) or 0
            for model in (PostRevision, MediaAsset, Job)
        )


def queue_review_job(rig: ContentRig, post_id: str, key: str = "k") -> str:
    with rig.factory.begin() as session:
        job_id = rig.queue.enqueue_in(session, REVIEW_POST_JOB, {"post_id": post_id}, key)
    assert job_id is not None
    return job_id


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


def test_asset_of_another_brand_fails_the_review_job_without_retry(ready: Ready) -> None:
    rig, _, post_id, base = ready
    with rig.factory.begin() as session:
        other = Brand(name="Outra marca")
        session.add(other)
        session.flush()
        foreign = MediaAsset(
            brand_id=other.id,
            kind="foto",
            storage_backend="local",
            storage_key="foreign.jpg",
            mime="image/jpeg",
            width=1080,
            height=1350,
            bytes=100,
            sha256="f" * 64,
            mostra="produto",
            descricao="Foto da outra marca",
            origem="propria",
            created_by="dono",
            created_at=rig.clock.now(),
        )
        session.add(foreign)
        session.flush()
        foreign_id = foreign.id
    force_lifecycle(rig, post_id, "drafting", None)
    payload = {
        "post_id": post_id,
        "base_revision_id": base.id,
        "asset_id": foreign_id,
        "meta": manual_meta().model_dump(),
    }
    with rig.factory.begin() as session:
        job_id = rig.queue.enqueue_in(session, REVIEW_POST_JOB, payload, "foreign")
    assert job_id is not None
    calls, revisions = len(rig.llm.requests), len(rig.revisions(post_id))

    assert Worker(rig.queue, rig.pipeline, "w").run_once()

    job = rig.job(job_id)
    assert (job.status, job.attempts, job.last_error) == ("failed", 1, "ForeignAssetError")
    assert rig.post(post_id).last_error == "ForeignAssetError"
    assert len(rig.llm.requests) == calls and len(rig.revisions(post_id)) == revisions


def test_unknown_worker_job_fails_without_retry(ready: Ready) -> None:
    rig, _, post_id, _ = ready
    with rig.factory.begin() as session:
        job_id = rig.queue.enqueue_in(session, "unknown", {"post_id": post_id}, "unknown")
    assert job_id is not None
    assert Worker(rig.queue, rig.pipeline, "w").run_once()
    assert rig.job(job_id).status == "failed"


# --- so em revisao: swap e upload nunca reabrem posts decididos --------------------------------


def decide(
    rig: ContentRig, service: ReviewService, post_id: str, base: PostRevision, how: str
) -> None:
    if how == "approved":
        service.approve(rig.brand_id, post_id, base.id, "dono")
    elif how == "rejected":
        service.reject(rig.brand_id, post_id, base.id, "nao_usar", None, "dono")
    else:
        force_lifecycle(rig, post_id, "cancelled", None)


@pytest.mark.parametrize("how", ["approved", "rejected", "cancelled"])
def test_swap_and_upload_refused_on_decided_posts(ready: Ready, how: str) -> None:
    rig, service, post_id, base = ready
    decide(rig, service, post_id, base, how)
    before = count_rows(rig)
    with pytest.raises(InvalidPostState):
        service.swap_photo(rig.brand_id, post_id, base.id, rig.photo_id, "Outra foto.", "dono")
    with pytest.raises(InvalidPostState):
        service.attach_manual_image(
            rig.brand_id, post_id, base.id, make_jpeg(1080, 1350), manual_meta(), "dono"
        )
    assert count_rows(rig) == before and rig.post(post_id).lifecycle == how


def test_swap_and_upload_allowed_on_a_failed_drafting_post_without_job(ready: Ready) -> None:
    rig, service, post_id, base = ready
    force_lifecycle(rig, post_id, "drafting", "NeedsHuman")
    service.swap_photo(rig.brand_id, post_id, base.id, rig.photo_id, "Fotografia de pao.", "dono")
    swapped = rig.revisions(post_id)[-1]
    assert rig.post(post_id).lifecycle == "in_review" and swapped.rev_no == 2
    force_lifecycle(rig, post_id, "drafting", "NeedsHuman")
    service.attach_manual_image(
        rig.brand_id, post_id, swapped.id, make_jpeg(1080, 1350), manual_meta(), "dono"
    )
    assert rig.post(post_id).lifecycle == "drafting" and rig.post(post_id).last_error is None


@pytest.mark.parametrize("last_error", [None, "NeedsHuman"])
def test_active_review_job_blocks_every_owner_action(ready: Ready, last_error: str | None) -> None:
    rig, service, post_id, base = ready
    force_lifecycle(rig, post_id, "drafting", last_error)
    job_id = queue_review_job(rig, post_id)
    before = count_rows(rig)
    edited = CaptionEdit(
        legenda="Outra legenda.", hashtags=[], apelo_a_acao="", alt_text=base.alt_text
    )
    attempts = (
        lambda: service.approve(rig.brand_id, post_id, base.id, "dono"),
        lambda: service.reject(rig.brand_id, post_id, base.id, "nao_usar", None, "dono"),
        lambda: service.request_changes(rig.brand_id, post_id, base.id, "Mais curto.", "dono"),
        lambda: service.edit(
            rig.brand_id, post_id, base.id, edited, "nao_usar", None, None, "dono"
        ),
        lambda: service.retry_generation(rig.brand_id, post_id, "dono"),
        lambda: service.swap_photo(rig.brand_id, post_id, base.id, rig.photo_id, "Foto.", "dono"),
        lambda: service.attach_manual_image(
            rig.brand_id, post_id, base.id, make_jpeg(1080, 1350), manual_meta(), "dono"
        ),
    )
    for attempt in attempts:
        with pytest.raises(InvalidPostState):
            attempt()
    assert count_rows(rig) == before and rig.job(job_id).status == "queued"


def test_owner_actions_cancel_an_active_review_job(ready: Ready) -> None:
    rig, service, post_id, base = ready
    edited = CaptionEdit(
        legenda="Outra legenda.", hashtags=[], apelo_a_acao="", alt_text=base.alt_text
    )
    steps = (
        lambda rev: service.edit(
            rig.brand_id, post_id, rev, edited, "nao_usar", None, None, "dono"
        ),
        lambda rev: service.swap_photo(
            rig.brand_id, post_id, rev, rig.photo_id, "Fotografia de pao.", "dono"
        ),
        lambda rev: service.approve(rig.brand_id, post_id, rev, "dono"),
    )
    for number, step in enumerate(steps):
        job_id = queue_review_job(rig, post_id, f"cancel-{number}")
        step(rig.revisions(post_id)[-1].id)
        assert rig.job(job_id).status == "cancelled"
    force_lifecycle(rig, post_id, "in_review", None)
    job_id = queue_review_job(rig, post_id, "cancel-reject")
    service.reject(rig.brand_id, post_id, rig.revisions(post_id)[-1].id, "nao_usar", None, "dono")
    assert rig.job(job_id).status == "cancelled"


# --- troca sem alteracoes -------------------------------------------------------------------


def test_swap_to_the_same_photo_and_alt_is_refused(ready: Ready) -> None:
    rig, service, post_id, base = ready
    service.swap_photo(rig.brand_id, post_id, base.id, rig.photo_id, "Fotografia de pao.", "dono")
    swapped = rig.revisions(post_id)[-1]
    before = count_rows(rig)
    with pytest.raises(InvalidEdit, match="troca sem alteracoes"):
        service.swap_photo(
            rig.brand_id, post_id, swapped.id, rig.photo_id, "  Fotografia de pao.  ", "dono"
        )
    assert count_rows(rig) == before and rig.post(post_id).lifecycle == "in_review"


# --- custos: pertencem a geracao que os produziu --------------------------------------------


def test_derived_revisions_carry_no_generation_cost(priced: Ready) -> None:
    rig, service, post_id, base = priced
    assert (base.image_cost_usd, base.package_cost_usd) == (Decimal("0.04"),) * 2
    edited = CaptionEdit(
        legenda="Outra legenda.", hashtags=[], apelo_a_acao="", alt_text=base.alt_text
    )
    service.edit(rig.brand_id, post_id, base.id, edited, "nao_usar", None, None, "dono")
    edit = rig.revisions(post_id)[-1]
    service.swap_photo(rig.brand_id, post_id, edit.id, rig.photo_id, "Fotografia de pao.", "dono")
    swap = rig.revisions(post_id)[-1]
    for derived in (edit, swap):
        assert (derived.image_cost_usd, derived.package_cost_usd) == (Decimal(0), Decimal(0))


def test_manual_revision_costs_only_its_own_review_job(priced: Ready) -> None:
    rig, service, post_id, base = priced
    job_id = service.attach_manual_image(
        rig.brand_id, post_id, base.id, make_jpeg(1080, 1350), manual_meta(), "dono"
    )
    rig.llm.load(fake_scenarios.review("aprovar", imagem_verificada=True))
    assert Worker(rig.queue, rig.pipeline, "w").run_once()
    manual = rig.revisions(post_id)[-1]
    with rig.factory() as session:
        own = job_costs_in(session, rig.brand_id, job_id)
    assert manual.origin == "upload"
    assert (manual.image_cost_usd, manual.package_cost_usd) == (Decimal(0), own.package_usd)
    assert manual.package_cost_usd != base.package_cost_usd


# --- declaracoes do upload --------------------------------------------------------------------


def test_manual_alt_text_is_stripped_before_the_length_check() -> None:
    assert manual_meta(alt_text="  Pao  ").alt_text == "Pao"
    assert len(manual_meta(alt_text=" " + "a" * 250 + " ").alt_text) == 250
    with pytest.raises(ValidationError):
        manual_meta(alt_text="a" * 251)
    with pytest.raises(ValidationError):
        manual_meta(alt_text="   ")


def test_manual_upload_of_people_without_authorization_is_refused() -> None:
    with pytest.raises(ValidationError):
        manual_meta(mostra="pessoas", tem_autorizacao_pessoas=False)


def test_swap_to_a_people_photo_without_authorization_is_refused(
    ready: Ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A base ja o impede (CHECK); o servico recusa mesmo assim se a biblioteca o devolver."""
    rig, service, post_id, base = ready
    photo = rig.library.photo_refs(rig.brand_id)[rig.photo_id]
    unauthorized = replace(photo, mostra="pessoas", tem_autorizacao_pessoas=False)
    monkeypatch.setattr(rig.library, "active_photo", lambda brand_id, asset_id: unauthorized)
    before = count_rows(rig)
    with pytest.raises(InvalidEdit, match="sem autorizacao"):
        service.swap_photo(rig.brand_id, post_id, base.id, rig.photo_id, "Foto.", "dono")
    assert count_rows(rig) == before


def test_swap_audit_records_the_photo_id_and_no_free_text(ready: Ready) -> None:
    rig, service, post_id, base = ready
    service.swap_photo(rig.brand_id, post_id, base.id, rig.photo_id, "TEXTO-LIVRE-ALT", "dono")
    with rig.factory() as session:
        audit = session.scalars(select(AuditLog).where(AuditLog.action == "post.swap_photo")).one()
    assert audit.detail == {
        "revision_id": rig.revisions(post_id)[-1].id,
        "base_revision_id": base.id,
        "photo_id": rig.photo_id,
    }
