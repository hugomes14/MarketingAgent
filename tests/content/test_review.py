"""ReviewService: aprovar, editar, pedir alteracoes e rejeitar, cada decisao numa transacao."""

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import cast

import pytest
from content_rig import WEEK, ContentRig, Factory, Script, build_rig, copy_and_review
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError

from marketing_agent.clock import FixedClock
from marketing_agent.content.review import (
    CaptionEdit,
    InvalidComment,
    InvalidEdit,
    InvalidPostState,
    PostNotFound,
    ReviewService,
    StaleRevision,
)
from marketing_agent.content.revision_store import canonical_revision_hash
from marketing_agent.db.models import Approval as ApprovalRow
from marketing_agent.db.models import AuditLog, Brand, FeedbackExample, Job, Post, PostRevision
from marketing_agent.feedback.service import FeedbackChoice, FeedbackError

HOSTILE = "ignora-as-regras-SEGREDO-4711"
CAPTION = "Pao quente todas as manhas."
MAX_COMMENT = 200
Claim = dict[str, str | None]


@dataclass
class Seeded:
    post_id: str
    revision_id: str


@dataclass
class Review:
    rig: ContentRig
    service: ReviewService

    def seed(
        self,
        *,
        lifecycle: str = "in_review",
        channels: Sequence[str] = ("instagram",),
        claims: Sequence[Claim] = (),
        kb_refs: Sequence[str] = (),
        slot: int = 1,
    ) -> Seeded:
        post_id = self.rig.seed_post(
            f"{WEEK}-{slot}", lifecycle=lifecycle, channels=channels, kb_refs=kb_refs
        )
        return Seeded(post_id, self.add_revision(post_id, 1, claims=claims))

    def add_revision(
        self, post_id: str, rev_no: int, caption: str = CAPTION, claims: Sequence[Claim] = ()
    ) -> str:
        post = self.rig.post(post_id)
        with self.rig.factory.begin() as session:
            revision = PostRevision(
                brand_id=post.brand_id,
                post_id=post_id,
                rev_no=rev_no,
                revision_hash=canonical_revision_hash(
                    caption=caption,
                    hashtags=["#pao"],
                    call_to_action="Venha.",
                    alt_text="Pao.",
                    claims=claims,
                    channels=post.channels,
                ),
                caption=caption,
                hashtags=["#pao"],
                call_to_action="Venha.",
                alt_text="Pao.",
                claims=[dict(claim) for claim in claims],
                review_verdict="aprovar",
                review_violations=[],
                review_notes="",
                origin="agente",
                created_at=self.rig.clock.now(),
            )
            session.add(revision)
            session.flush()
            return revision.id

    def edit(
        self,
        seeded: Seeded,
        *,
        legenda: str = "Pao novo, sempre quente.",
        hashtags: Sequence[str] = ("#pao",),
        apelo: str = "Venha.",
        alt_text: str = "Pao.",
        feedback: FeedbackChoice = "nao_usar",
        note: str | None = None,
        brand_id: str | None = None,
    ) -> str:
        changes = CaptionEdit(
            legenda=legenda, hashtags=list(hashtags), apelo_a_acao=apelo, alt_text=alt_text
        )
        return self.service.edit(
            brand_id or self.rig.brand_id,
            seeded.post_id,
            seeded.revision_id,
            changes,
            feedback,
            note,
            None,
            "ana",
        )

    def approvals(self, post_id: str) -> list[ApprovalRow]:
        with self.rig.factory() as session:
            statement = select(ApprovalRow).where(ApprovalRow.post_id == post_id)
            return list(session.scalars(statement.order_by(ApprovalRow.decided_at, ApprovalRow.id)))

    def audits(self, prefix: str = "post.") -> list[AuditLog]:
        with self.rig.factory() as session:
            statement = select(AuditLog).where(AuditLog.action.startswith(prefix))
            return list(session.scalars(statement.order_by(AuditLog.id)))

    def feedback(self) -> list[FeedbackExample]:
        with self.rig.factory() as session:
            return list(session.scalars(select(FeedbackExample)))

    def jobs(self) -> list[Job]:
        with self.rig.factory() as session:
            return list(session.scalars(select(Job).order_by(Job.created_at)))


MakeReview = Callable[..., Review]


@pytest.fixture
def make_review(session_factory: Factory, clock: FixedClock, brand_id: str) -> MakeReview:
    def make(script: Script | None = None) -> Review:
        rig = build_rig(session_factory, clock, brand_id, script or [])
        service = ReviewService(
            rig.factory, rig.feedback, rig.queue, rig.kb, rig.clock, max_comment_chars=MAX_COMMENT
        )
        return Review(rig, service)

    return make


@pytest.fixture
def review(make_review: MakeReview) -> Review:
    return make_review()


def race(*calls: Callable[[], object]) -> list[object]:
    """Corre as chamadas em paralelo a partir de uma Barrier; devolve resultado ou excecao."""
    barrier = threading.Barrier(len(calls), timeout=15)

    def run(call: Callable[[], object]) -> object:
        barrier.wait()
        try:
            return call()
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(len(calls)) as pool:
        futures = [pool.submit(run, call) for call in calls]
        return [future.result(timeout=60) for future in futures]


# --- aprovar -------------------------------------------------------------------------------


def test_approve_only_latest_revision(review: Review) -> None:
    seeded = review.seed()
    newer = review.add_revision(seeded.post_id, 2, caption="Segunda versao.")
    with pytest.raises(StaleRevision):
        review.service.approve(review.rig.brand_id, seeded.post_id, seeded.revision_id, "ana")
    assert review.rig.post(seeded.post_id).lifecycle == "in_review"
    assert review.approvals(seeded.post_id) == []

    review.service.approve(review.rig.brand_id, seeded.post_id, newer, "ana")

    [approval] = review.approvals(seeded.post_id)
    assert (approval.decision, approval.revision_id, approval.decided_by) == (
        "approved",
        newer,
        "ana",
    )
    assert approval.revision_hash == review.rig.revisions(seeded.post_id)[1].revision_hash
    assert review.rig.post(seeded.post_id).lifecycle == "approved"


def test_approve_unknown_revision_is_stale(review: Review) -> None:
    seeded = review.seed()
    with pytest.raises(StaleRevision):
        review.service.approve(review.rig.brand_id, seeded.post_id, "nao-existe", "ana")


@pytest.mark.parametrize("lifecycle", ["planned", "drafting", "approved", "rejected", "cancelled"])
def test_decisions_require_in_review(review: Review, lifecycle: str) -> None:
    seeded = review.seed(lifecycle=lifecycle)
    brand, post, rev = review.rig.brand_id, seeded.post_id, seeded.revision_id
    calls: list[Callable[[], object]] = [
        lambda: review.service.approve(brand, post, rev, "ana"),
        lambda: review.service.reject(brand, post, rev, "nao_usar", None, "ana"),
        lambda: review.service.request_changes(brand, post, rev, "mais curto", "ana"),
    ]
    for call in calls:
        with pytest.raises(InvalidPostState):
            call()
    assert review.rig.post(post).lifecycle == lifecycle
    assert review.approvals(post) == [] and review.jobs() == []


def test_approve_cancels_a_queued_generation_job(review: Review) -> None:
    seeded = review.seed()
    job_id = review.rig.enqueue(seeded.post_id)
    review.service.approve(review.rig.brand_id, seeded.post_id, seeded.revision_id, "ana")
    assert review.rig.job(job_id).status == "cancelled"


# --- editar --------------------------------------------------------------------------------


def test_edit_creates_revision_and_invalidates_approval(review: Review) -> None:
    claims: list[Claim] = [{"texto": "Forno a lenha", "kb_ref": None}]
    seeded = review.seed(claims=claims)
    old = ApprovalRow(
        brand_id=review.rig.brand_id,
        post_id=seeded.post_id,
        revision_id=seeded.revision_id,
        revision_hash=review.rig.revisions(seeded.post_id)[0].revision_hash,
        decision="approved",
        comment=None,
        decided_by="ana",
        decided_at=review.rig.clock.now(),
    )
    with review.rig.factory.begin() as session:
        session.add(old)

    new_id = review.edit(seeded, legenda="Pao novo, sempre quente.", hashtags=["#novo"])

    first, second = review.rig.revisions(seeded.post_id)
    assert (second.id, second.rev_no, second.origin) == (new_id, 2, "edicao_responsavel")
    assert (second.caption, second.hashtags) == ("Pao novo, sempre quente.", ["#novo"])
    assert second.claims == claims  # as alegacoes mantem-se da revisao base
    assert (second.review_verdict, second.review_violations, second.review_notes) == (
        None,
        [],
        None,
    )
    assert second.revision_hash == canonical_revision_hash(
        caption=second.caption,
        hashtags=["#novo"],
        call_to_action="Venha.",
        alt_text="Pao.",
        claims=claims,
        channels=["instagram"],
    )
    assert second.revision_hash != first.revision_hash
    post = review.rig.post(seeded.post_id)
    assert (post.lifecycle, post.last_error) == ("in_review", None)
    # A aprovacao antiga fica no historico mas aponta a revisao 1: so vale a da mais recente.
    [stored] = review.approvals(seeded.post_id)
    assert stored.revision_id != new_id
    with pytest.raises(StaleRevision):
        review.service.approve(review.rig.brand_id, seeded.post_id, seeded.revision_id, "ana")


def test_edit_on_stale_base_raises(review: Review) -> None:
    seeded = review.seed()
    review.add_revision(seeded.post_id, 2, caption="Outra.")
    with pytest.raises(StaleRevision):
        review.edit(seeded)
    assert len(review.rig.revisions(seeded.post_id)) == 2


def test_edit_twice_from_same_base_second_is_stale(review: Review) -> None:
    seeded = review.seed()
    review.edit(seeded, legenda="Primeira edicao.")
    with pytest.raises(StaleRevision):
        review.edit(seeded, legenda="Segunda edicao.")
    assert [r.rev_no for r in review.rig.revisions(seeded.post_id)] == [1, 2]


def test_edit_validates_limits(review: Review) -> None:
    seeded = review.seed(channels=("instagram", "facebook"))
    cases = {
        "caption": {"legenda": "x" * 2201},
        "hashtags": {"hashtags": [f"#t{i}" for i in range(31)]},
        "alt": {"alt_text": "   "},
        "hashtag_format": {"hashtags": ["semcardinal", "#com espaco"]},
        "blank": {"legenda": "   "},
    }
    messages = {}
    for name, fields in cases.items():
        with pytest.raises(InvalidEdit) as caught:
            review.edit(seeded, **fields)  # type: ignore[arg-type]
        messages[name] = str(caught.value)
    assert "2201" in messages["caption"] and "2200" in messages["caption"]
    assert "31 hashtags" in messages["hashtags"] and "30" in messages["hashtags"]
    assert "alt_text" in messages["alt"]
    assert (
        "hashtags[0]" in messages["hashtag_format"] and "hashtags[1]" in messages["hashtag_format"]
    )
    assert "legenda" in messages["blank"]
    assert len(review.rig.revisions(seeded.post_id)) == 1
    assert review.rig.post(seeded.post_id).lifecycle == "in_review"


def test_edit_violation_message_never_echoes_owner_text(review: Review) -> None:
    seeded = review.seed()
    with pytest.raises(InvalidEdit) as caught:
        review.edit(seeded, legenda=HOSTILE + "x" * 2300, hashtags=[HOSTILE])
    assert HOSTILE not in str(caught.value) and HOSTILE not in repr(caught.value)
    assert all(HOSTILE not in violation for violation in caught.value.violations)


def test_edit_message_is_bounded_with_many_violations(review: Review) -> None:
    seeded = review.seed()
    with pytest.raises(InvalidEdit) as caught:
        review.edit(seeded, hashtags=["x"] * 100)
    assert len(str(caught.value)) <= 600 and len(caught.value.violations) >= 30


def test_edit_price_rule_uses_claims_carried_over_from_the_base(review: Review) -> None:
    price_id = review.rig.add_price_item()
    with_claim = review.seed(
        claims=[{"texto": "12,50 EUR", "kb_ref": price_id}], kb_refs=[price_id]
    )
    review.edit(with_claim, legenda="Pao a 12,50 EUR hoje.")
    without = review.seed(slot=2, kb_refs=[price_id])
    with pytest.raises(InvalidEdit, match="valor monetario"):
        review.edit(without, legenda="Pao a 12,50 EUR hoje.")


def test_edit_rejects_claims_whose_kb_item_is_no_longer_valid(review: Review) -> None:
    seeded = review.seed(claims=[{"texto": "Preco", "kb_ref": "item-fora-do-brief"}])
    with pytest.raises(InvalidEdit, match=r"alegacoes\[0\]"):
        review.edit(seeded)


def test_edit_without_changes_is_refused(review: Review) -> None:
    seeded = review.seed()
    with pytest.raises(InvalidEdit, match="sem alteracoes"):
        review.edit(seeded, legenda=CAPTION)
    assert len(review.rig.revisions(seeded.post_id)) == 1


def test_edit_cancels_a_queued_generation_job(review: Review) -> None:
    seeded = review.seed()
    job_id = review.rig.enqueue(seeded.post_id, "changes", "comentario")
    review.edit(seeded)
    assert review.rig.job(job_id).status == "cancelled"
    assert review.rig.queue.claim("w1") is None


def test_edit_makes_a_running_job_end_stale(make_review: MakeReview) -> None:
    review = make_review(copy_and_review())
    seeded = review.seed()
    job = review.rig.claimed(seeded.post_id, "changes", "comentario")  # worker com o job
    new_id = review.edit(seeded)

    assert review.rig.pipeline.generate(job) == "stale"

    assert [r.id for r in review.rig.revisions(seeded.post_id)][1] == new_id
    assert len(review.rig.revisions(seeded.post_id)) == 2
    assert review.rig.post(seeded.post_id).lifecycle == "in_review"
    assert review.rig.llm.requests == []


def test_edit_is_refused_while_a_job_runs_on_a_drafting_post(make_review: MakeReview) -> None:
    review = make_review(copy_and_review())
    seeded = review.seed()
    review.service.request_changes(
        review.rig.brand_id, seeded.post_id, seeded.revision_id, "x", "a"
    )
    job = review.rig.queue.claim("w1")
    assert job is not None
    outcomes: list[str] = []

    def hook() -> None:
        with pytest.raises(InvalidPostState):
            review.edit(seeded)
        outcomes.append("refused")

    review.rig.llm.hooks[1] = hook
    assert review.rig.pipeline.generate(job) == "in_review"
    assert outcomes == ["refused"]
    assert [r.rev_no for r in review.rig.revisions(seeded.post_id)] == [1, 2]


def test_edit_fixes_a_failed_generation_by_hand(review: Review) -> None:
    seeded = review.seed(lifecycle="drafting")
    with review.rig.factory.begin() as session:
        session.get_one(Post, seeded.post_id).last_error = "LeaseExpired: lease expirada"
    review.edit(seeded)
    post = review.rig.post(seeded.post_id)
    assert (post.lifecycle, post.last_error) == ("in_review", None)


def test_edit_in_drafting_needs_a_recorded_failure_and_no_active_job(review: Review) -> None:
    quiet = review.seed(lifecycle="drafting")
    with pytest.raises(InvalidPostState):
        review.edit(quiet)  # a gerar de verdade: sem last_error
    failed = review.seed(lifecycle="drafting", slot=2)
    with review.rig.factory.begin() as session:
        session.get_one(Post, failed.post_id).last_error = "RunLimitExceeded: x"
    review.rig.enqueue(failed.post_id)
    with pytest.raises(InvalidPostState):
        review.edit(failed)
    assert (
        len(review.rig.revisions(quiet.post_id)) == len(review.rig.revisions(failed.post_id)) == 1
    )


@pytest.mark.parametrize("lifecycle", ["planned", "approved", "rejected", "cancelled"])
def test_edit_refused_in_other_states(review: Review, lifecycle: str) -> None:
    seeded = review.seed(lifecycle=lifecycle)
    with pytest.raises(InvalidPostState):
        review.edit(seeded)


def test_edit_records_style_feedback_in_the_same_transaction(review: Review) -> None:
    seeded = review.seed()
    new_id = review.edit(seeded, legenda="Nova legenda.", feedback="estilo", note="Mais direto")
    [example] = review.feedback()
    assert (example.kind, example.before_text, example.after_text) == (
        "estilo",
        CAPTION,
        "Nova legenda.",
    )
    assert (example.note, example.revision_id, example.created_by) == ("Mais direto", new_id, "ana")


def test_edit_with_nao_usar_records_nothing(review: Review) -> None:
    review.edit(review.seed(), feedback="nao_usar", note="x")
    assert review.feedback() == []


def test_edit_feedback_failure_rolls_back_the_revision(review: Review) -> None:
    seeded = review.seed()
    with pytest.raises(FeedbackError):
        review.edit(seeded, feedback="temporario")  # sem expires_at
    assert len(review.rig.revisions(seeded.post_id)) == 1
    assert review.audits() == []


def test_edit_temporary_feedback_keeps_expiry(review: Review) -> None:
    seeded = review.seed()
    expires = review.rig.clock.now().replace(year=2027)
    changes = CaptionEdit(legenda="Nova.", hashtags=["#a"], apelo_a_acao="", alt_text="Pao.")
    review.service.edit(
        review.rig.brand_id, seeded.post_id, seeded.revision_id, changes, "temporario", None,
        expires, "ana",
    )  # fmt: skip
    [example] = review.feedback()
    assert example.expires_at == expires


SECRET = "SEGREDO-do-responsavel-9137"


@pytest.mark.parametrize("fields", [
    {"hashtags": ["#" + SECRET + "x" * 100]},
    {"apelo_a_acao": SECRET + "x" * 300},
    {"alt_text": SECRET + "x" * 500},
    {"legenda": SECRET + "x" * 10_000},
])  # fmt: skip
def test_from_form_never_leaks_the_input(fields: dict[str, object]) -> None:
    form: dict[str, object] = {"legenda": "a", "hashtags": [], "apelo_a_acao": "", "alt_text": ""}
    form.update(fields)
    with pytest.raises(InvalidEdit) as caught:
        CaptionEdit.from_form(**form)  # type: ignore[arg-type]
    exc = caught.value
    assert exc.__cause__ is None and exc.__context__ is None
    assert exc.violations and len(str(exc)) <= 600
    for text in (str(exc), repr(exc), *exc.violations):
        assert SECRET not in text and "xxx" not in text
    field = next(iter(fields))
    assert any(field in violation for violation in exc.violations)


def test_from_form_builds_a_valid_edit() -> None:
    changes = CaptionEdit.from_form("Texto", ["#a"], "Venha.", "Pao.")
    assert changes == CaptionEdit(
        legenda="Texto", hashtags=["#a"], apelo_a_acao="Venha.", alt_text="Pao."
    )


@pytest.mark.parametrize("choice", ["estilo", "negativo"])
def test_reject_and_edit_accept_a_long_facebook_caption(
    review: Review, choice: FeedbackChoice
) -> None:
    long_caption = "Pao quente. " * 250  # 3000 caracteres
    seeded = review.seed(channels=("facebook",))
    other = review.seed(channels=("facebook",), slot=2)
    review.add_revision(other.post_id, 2, caption=long_caption)
    other = Seeded(other.post_id, review.rig.revisions(other.post_id)[1].id)

    review.service.reject(review.rig.brand_id, other.post_id, other.revision_id, choice, "n", "ana")
    new_id = review.edit(seeded, legenda=long_caption, feedback=choice, note="n")
    edited = Seeded(seeded.post_id, new_id)
    new_id = review.edit(edited, legenda=long_caption + "Fim.", feedback=choice, note="n")

    examples = review.feedback()
    assert len(examples) == 3 and all(e.kind == choice for e in examples)
    assert examples[0].before_text == long_caption and examples[0].after_text is None
    assert (
        examples[2].before_text == long_caption and examples[2].after_text == long_caption + "Fim."
    )


def test_caption_edit_is_frozen_strict_and_bounded() -> None:
    changes = CaptionEdit(legenda="a", hashtags=[], apelo_a_acao="", alt_text="")
    with pytest.raises(ValueError):
        changes.legenda = "b"  # type: ignore[misc]
    with pytest.raises(ValueError):
        CaptionEdit.model_validate(
            {"legenda": "a", "hashtags": [], "apelo_a_acao": "", "alt_text": "", "extra": 1}
        )
    with pytest.raises(ValueError):
        CaptionEdit(legenda="x" * 10_001, hashtags=[], apelo_a_acao="", alt_text="")
    with pytest.raises(ValueError):
        CaptionEdit(legenda="a", hashtags=["#a"] * 101, apelo_a_acao="", alt_text="")
    with pytest.raises(ValueError):
        CaptionEdit(legenda="a", hashtags=[], apelo_a_acao="x" * 301, alt_text="")
    with pytest.raises(ValueError):
        CaptionEdit(legenda="a", hashtags=[], apelo_a_acao="", alt_text="x" * 501)


# --- pedir alteracoes ----------------------------------------------------------------------


def test_request_changes_enqueues_with_comment(review: Review) -> None:
    seeded = review.seed()
    with review.rig.factory.begin() as session:
        session.get_one(Post, seeded.post_id).last_error = "antigo"
    review.service.request_changes(
        review.rig.brand_id, seeded.post_id, seeded.revision_id, "  Mais curto, por favor.  ", "ana"
    )
    post = review.rig.post(seeded.post_id)
    assert (post.lifecycle, post.last_error) == ("drafting", None)
    [job] = review.jobs()
    assert job.type == "generate_post" and job.status == "queued"
    assert job.payload == {
        "post_id": seeded.post_id,
        "owner_comment": "Mais curto, por favor.",  # sem espacos nas pontas
        "reason": "changes",
    }
    assert job.dedupe_key == f"generate_post:{seeded.post_id}:changes:{seeded.revision_id}"
    [approval] = review.approvals(seeded.post_id)
    assert (approval.decision, approval.comment) == ("changes_requested", "Mais curto, por favor.")
    assert approval.revision_hash == review.rig.revisions(seeded.post_id)[0].revision_hash


def test_request_changes_then_worker_regenerates_with_the_comment(make_review: MakeReview) -> None:
    review = make_review(copy_and_review(legenda="Versao dois."))
    seeded = review.seed()
    review.service.request_changes(
        review.rig.brand_id, seeded.post_id, seeded.revision_id, "Tom mais informal", "ana"
    )
    job = review.rig.queue.claim("w1")
    assert job is not None
    assert review.rig.pipeline.generate(job) == "in_review"
    assert "Tom mais informal" in review.rig.llm.request_text(1)
    assert [r.rev_no for r in review.rig.revisions(seeded.post_id)] == [1, 2]
    assert review.rig.post(seeded.post_id).lifecycle == "in_review"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_request_changes_refused_when_a_job_is_active(review: Review, status: str) -> None:
    seeded = review.seed()
    job_id = review.rig.enqueue(seeded.post_id)
    if status == "running":
        assert review.rig.queue.claim("w1") is not None
    with pytest.raises(InvalidPostState):
        review.service.request_changes(
            review.rig.brand_id, seeded.post_id, seeded.revision_id, "x", "ana"
        )
    assert review.rig.post(seeded.post_id).lifecycle == "in_review"
    assert review.approvals(seeded.post_id) == []
    assert [job.id for job in review.jobs()] == [job_id]


def test_request_changes_on_non_latest_revision_is_stale(review: Review) -> None:
    seeded = review.seed()
    review.add_revision(seeded.post_id, 2)
    with pytest.raises(StaleRevision):
        review.service.request_changes(
            review.rig.brand_id, seeded.post_id, seeded.revision_id, "x", "ana"
        )
    assert review.jobs() == []


@pytest.mark.parametrize("comment", ["", "   \n\t ", "x" * (MAX_COMMENT + 1)])
def test_request_changes_comment_bounds(review: Review, comment: str) -> None:
    seeded = review.seed()
    with pytest.raises(InvalidComment):
        review.service.request_changes(
            review.rig.brand_id, seeded.post_id, seeded.revision_id, comment, "ana"
        )
    assert review.jobs() == [] and review.rig.post(seeded.post_id).lifecycle == "in_review"


def test_request_changes_accepts_the_maximum_length(review: Review) -> None:
    seeded = review.seed()
    comment = "x" * MAX_COMMENT
    review.service.request_changes(
        review.rig.brand_id, seeded.post_id, seeded.revision_id, comment, "ana"
    )
    assert review.jobs()[0].payload["owner_comment"] == comment


# --- rejeitar ------------------------------------------------------------------------------


def test_reject_records_feedback(review: Review) -> None:
    seeded = review.seed()
    job_id = review.rig.enqueue(seeded.post_id)
    review.service.reject(
        review.rig.brand_id, seeded.post_id, seeded.revision_id, "negativo", "Tom errado", "ana"
    )
    assert review.rig.post(seeded.post_id).lifecycle == "rejected"
    assert review.rig.job(job_id).status == "cancelled"
    [approval] = review.approvals(seeded.post_id)
    assert (approval.decision, approval.revision_id) == ("rejected", seeded.revision_id)
    [example] = review.feedback()
    assert (example.kind, example.before_text, example.after_text) == ("negativo", CAPTION, None)
    assert (example.note, example.revision_id) == ("Tom errado", seeded.revision_id)


def test_reject_with_nao_usar_records_no_feedback(review: Review) -> None:
    seeded = review.seed()
    review.service.reject(
        review.rig.brand_id, seeded.post_id, seeded.revision_id, "nao_usar", None, "ana"
    )
    assert review.feedback() == []
    assert review.rig.post(seeded.post_id).lifecycle == "rejected"


def test_reject_on_non_latest_revision_is_stale(review: Review) -> None:
    seeded = review.seed()
    review.add_revision(seeded.post_id, 2)
    with pytest.raises(StaleRevision):
        review.service.reject(
            review.rig.brand_id, seeded.post_id, seeded.revision_id, "nao_usar", None, "ana"
        )
    assert review.rig.post(seeded.post_id).lifecycle == "in_review"


def test_feedback_choice_is_checked_at_runtime_before_any_insert(review: Review) -> None:
    seeded = review.seed()
    bad = cast(FeedbackChoice, HOSTILE)
    with pytest.raises(FeedbackError) as rejected:
        review.service.reject(
            review.rig.brand_id, seeded.post_id, seeded.revision_id, bad, HOSTILE, "ana"
        )
    with pytest.raises(FeedbackError) as edited:
        review.edit(seeded, feedback=bad, note=HOSTILE)
    for caught in (rejected, edited):
        assert HOSTILE not in str(caught.value) and caught.value.__cause__ is None
    assert review.rig.post(seeded.post_id).lifecycle == "in_review"
    assert review.approvals(seeded.post_id) == [] and review.feedback() == []
    assert len(review.rig.revisions(seeded.post_id)) == 1


# --- marca, auditoria e segredos -----------------------------------------------------------


def other_brand(factory: Factory) -> str:
    with factory.begin() as session:
        brand = Brand(name="Outra marca")
        session.add(brand)
        session.flush()
        return brand.id


def test_other_brand_post_not_found(review: Review) -> None:
    seeded = review.seed()
    stranger = other_brand(review.rig.factory)
    post, rev = seeded.post_id, seeded.revision_id
    calls: list[Callable[[str, str], object]] = [
        lambda brand, post_id: review.service.approve(brand, post_id, rev, HOSTILE),
        lambda brand, post_id: review.service.reject(brand, post_id, rev, "negativo", HOSTILE, "a"),
        lambda brand, post_id: review.service.request_changes(brand, post_id, rev, HOSTILE, "a"),
        lambda brand, post_id: review.edit(Seeded(post_id, rev), legenda=HOSTILE, brand_id=brand),
    ]
    for call in calls:
        with pytest.raises(PostNotFound) as foreign:
            call(stranger, post)
        with pytest.raises(PostNotFound) as unknown:
            call(review.rig.brand_id, "id-que-nao-existe")
        assert str(foreign.value) == str(unknown.value)
        for text in (post, rev, stranger, HOSTILE, "id-que-nao-existe"):
            assert text not in str(foreign.value)
    assert review.rig.post(post).lifecycle == "in_review"
    assert review.approvals(post) == [] and review.jobs() == [] and review.feedback() == []
    assert len(review.rig.revisions(post)) == 1


def test_approval_composite_foreign_key_rejects_a_foreign_brand(review: Review) -> None:
    seeded = review.seed()
    stranger = other_brand(review.rig.factory)
    row = ApprovalRow(
        brand_id=stranger,
        post_id=seeded.post_id,
        revision_id=seeded.revision_id,
        revision_hash="0" * 64,
        decision="approved",
        comment=None,
        decided_by="x",
        decided_at=review.rig.clock.now(),
    )
    with pytest.raises(IntegrityError), review.rig.factory.begin() as session:
        session.add(row)


def test_decisions_audited_with_actor(review: Review) -> None:
    first = review.seed()
    second = review.seed(slot=2)
    third = review.seed(slot=3)
    fourth = review.seed(slot=4)
    brand = review.rig.brand_id
    review.service.approve(brand, first.post_id, first.revision_id, "ana")
    new_id = review.edit(second, feedback="estilo", note="nota")
    review.service.request_changes(brand, third.post_id, third.revision_id, "comentario", "rui")
    review.service.reject(brand, fourth.post_id, fourth.revision_id, "negativo", "motivo", "eva")

    rows = {row.action: row for row in review.audits("post.")}
    assert set(rows) == {"post.approve", "post.edit", "post.request_changes", "post.reject"}
    assert rows["post.approve"].actor == "ana" and rows["post.approve"].subject_id == first.post_id
    assert rows["post.approve"].subject_type == "post"
    assert rows["post.approve"].detail == {"revision_id": first.revision_id, "rev_no": 1}
    assert rows["post.edit"].detail == {
        "base_revision_id": second.revision_id,
        "revision_id": new_id,
        "rev_no": 2,
        "feedback": "estilo",
    }
    assert rows["post.request_changes"].actor == "rui"
    assert rows["post.request_changes"].detail == {
        "revision_id": third.revision_id,
        "rev_no": 1,
        "comment_chars": len("comentario"),
    }
    assert (rows["post.reject"].actor, rows["post.reject"].detail["feedback"]) == (
        "eva",
        "negativo",
    )


def test_hostile_text_never_reaches_audit_log_or_errors(review: Review) -> None:
    seeded = review.seed()
    third = review.seed(slot=3)
    brand = review.rig.brand_id
    review.edit(seeded, legenda=HOSTILE, feedback="estilo", note=HOSTILE)
    review.service.reject(brand, third.post_id, third.revision_id, "negativo", HOSTILE, "ana")
    fresh = review.seed(slot=4)
    review.service.request_changes(brand, fresh.post_id, fresh.revision_id, HOSTILE, "ana")
    with pytest.raises(InvalidComment) as too_long:
        review.service.request_changes(brand, fresh.post_id, "x", HOSTILE * 20, "ana")
    assert HOSTILE not in str(too_long.value)
    for row in review.audits(""):
        assert HOSTILE not in repr((row.actor, row.action, row.subject_id, row.detail))


# --- concorrencia --------------------------------------------------------------------------


def test_approve_and_edit_race_has_exactly_one_winner(review: Review) -> None:
    brand = review.rig.brand_id
    for slot in range(1, 7):
        seeded = review.seed(slot=slot)
        results = race(
            lambda s=seeded: review.service.approve(brand, s.post_id, s.revision_id, "ana"),
            lambda s=seeded, n=slot: review.edit(s, legenda=f"Edicao {n}."),
        )
        approved, edited = results
        revisions = review.rig.revisions(seeded.post_id)
        post = review.rig.post(seeded.post_id)
        assert not any(isinstance(r, DBAPIError) for r in results)
        if approved is None:  # aprovar ganhou: a edicao ja encontra o post aprovado
            assert isinstance(edited, InvalidPostState)
            assert (post.lifecycle, len(revisions), len(review.approvals(seeded.post_id))) == (
                "approved", 1, 1,
            )  # fmt: skip
        else:  # editar ganhou: a aprovacao da revisao antiga e obsoleta
            assert isinstance(approved, StaleRevision) and isinstance(edited, str)
            assert (post.lifecycle, len(revisions), len(review.approvals(seeded.post_id))) == (
                "in_review", 2, 0,
            )  # fmt: skip


def test_request_changes_twice_concurrently_enqueues_one_job(review: Review) -> None:
    seeded = review.seed()
    brand = review.rig.brand_id
    results = race(
        lambda: review.service.request_changes(brand, seeded.post_id, seeded.revision_id, "a", "x"),
        lambda: review.service.request_changes(brand, seeded.post_id, seeded.revision_id, "b", "y"),
        lambda: review.service.request_changes(brand, seeded.post_id, seeded.revision_id, "c", "z"),
    )
    assert sorted(r is None for r in results) == [False, False, True]
    assert all(isinstance(r, InvalidPostState) for r in results if r is not None)
    assert len(review.jobs()) == 1 and len(review.approvals(seeded.post_id)) == 1
    assert review.rig.post(seeded.post_id).lifecycle == "drafting"


def test_decision_and_plan_reapproval_race_do_not_deadlock(review: Review) -> None:
    from marketing_agent.planning.posts import PostReconciler
    from marketing_agent.planning.schemas import PlanoSemanal

    reconciler = PostReconciler(review.rig.queue, review.rig.clock)
    plan_record = review.rig.store.get(review.rig.plan_id)
    empty_plan = PlanoSemanal.model_construct(semana=WEEK, slots=[])
    brand = review.rig.brand_id

    def reapprove() -> object:
        with review.rig.factory.begin() as session:
            return reconciler.apply(session, plan_record, empty_plan, review.rig.strategy_id)

    for slot in range(1, 7):
        seeded = review.seed(slot=slot)
        review.rig.enqueue(seeded.post_id)
        results = race(
            reapprove,
            lambda s=seeded: review.service.approve(brand, s.post_id, s.revision_id, "ana"),
        )
        assert not any(isinstance(r, DBAPIError) for r in results), results
        assert not isinstance(results[0], Exception), results
        assert results[1] is None or isinstance(results[1], InvalidPostState)
        assert review.rig.post(seeded.post_id).lifecycle == "cancelled"
        assert [j.status for j in review.jobs() if j.payload["post_id"] == seeded.post_id] == [
            "cancelled"
        ]
