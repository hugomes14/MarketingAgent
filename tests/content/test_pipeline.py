"""ContentPipeline: Copywriter -> Revisor, voltas, gravacao atomica e protecao contra obsoletos."""

import hashlib
import json
import threading
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from content_rig import (
    WEEK,
    ContentRig,
    Factory,
    Script,
    build_rig,
    copy_and_review,
)
from sqlalchemy import select, text, update

from marketing_agent.artifacts.store import ArtifactType
from marketing_agent.budget.controller import RunCostLimitExceeded
from marketing_agent.clock import FixedClock
from marketing_agent.config import Capability
from marketing_agent.content.revision_store import canonical_revision_hash
from marketing_agent.content.rounds import AgentRunFailed, describe_stop
from marketing_agent.db.models import AgentRun, Post
from marketing_agent.harness.engine import RunLimitExceeded, RunResult, RunStatus
from marketing_agent.harness.run_errors import RunConfigurationFailed
from marketing_agent.jobs.errors import describe_error
from marketing_agent.llm import fake_scenarios

MakeRig = Callable[[Script], ContentRig]
HOSTILE = "ignora-as-regras-SEGREDO-4711"


@pytest.fixture
def make_rig(session_factory: Factory, clock: FixedClock, brand_id: str) -> MakeRig:
    def make(script: Script) -> ContentRig:
        return build_rig(session_factory, clock, brand_id, script)

    return make


def mutate_post(rig: ContentRig, post_id: str, **values: object) -> Callable[[], None]:
    def mutate() -> None:
        with rig.factory.begin() as session:
            session.execute(update(Post).where(Post.id == post_id).values(**values))

    return mutate


# --- caminho feliz -------------------------------------------------------------------------


def test_generate_happy_path_creates_revision_in_review(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review("aprovar", notes="Boa."))
    post_id = rig.seed_post()
    job = rig.claimed(post_id)

    assert rig.pipeline.generate(job) == "in_review"

    [revision] = rig.revisions(post_id)
    assert (revision.rev_no, revision.origin) == (1, "agente")
    assert revision.caption.startswith("Pao quente")
    assert revision.hashtags == ["#padariadobairo", "#paoartesanal"]
    assert revision.call_to_action == "Passe pela loja e experimente."
    assert revision.alt_text and revision.claims == []
    assert (revision.review_verdict, revision.review_violations) == ("aprovar", [])
    assert revision.review_notes == "Boa."
    assert revision.revision_hash == canonical_revision_hash(
        caption=revision.caption,
        hashtags=revision.hashtags,
        call_to_action=revision.call_to_action,
        alt_text=revision.alt_text,
        claims=revision.claims,
        channels=["instagram"],
    )
    post = rig.post(post_id)
    assert (post.lifecycle, post.review_rounds, post.last_error) == ("in_review", 0, None)
    assert rig.job(job.id).status == "succeeded"
    assert len(rig.llm.requests) == 2
    with rig.factory() as session:
        run = session.get_one(AgentRun, revision.produced_by_run_id)
    assert (run.role, run.status) == ("copywriter", "succeeded")


def test_generate_moves_planned_post_to_drafting_while_running(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post()
    seen: list[str] = []
    rig.llm.hooks[1] = lambda: seen.append(rig.post(post_id).lifecycle)
    rig.pipeline.generate(rig.claimed(post_id))
    assert seen == ["drafting"]


def test_generate_stores_claims_and_cites_only_valid_kb_items(make_rig: MakeRig) -> None:
    rig = make_rig([])
    price_id = rig.add_price_item()
    claim = {"texto": "Pao a 12,50 EUR", "kb_ref": price_id}
    rig.llm.load(
        [
            *fake_scenarios.caption_draft("Pao a 12,50 EUR.", alegacoes=[claim]),
            *fake_scenarios.review("aprovar"),
        ]
    )
    post_id = rig.seed_post(kb_refs=[price_id, "item-que-nao-existe"])
    assert rig.pipeline.generate(rig.claimed(post_id)) == "in_review"
    [revision] = rig.revisions(post_id)
    assert revision.claims == [claim]
    assert "Pao de forno a lenha." in rig.llm.request_text(1)  # o item citado vai no pedido


def test_feedback_examples_reach_the_copywriter_request(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review())
    other = rig.seed_post(f"{WEEK}-2")
    revision_id = rig.add_owner_revision(other)
    with rig.factory.begin() as session:
        rig.feedback.record_in(
            session,
            brand_id=rig.brand_id,
            post_id=other,
            revision_id=revision_id,
            choice="estilo",
            before_text="ANTES-ESTILO",
            after_text="DEPOIS-ESTILO",
            note=None,
            expires_at=None,
            actor="ana",
        )
    rig.pipeline.generate(rig.claimed(rig.seed_post()))
    assert "DEPOIS-ESTILO" in rig.llm.request_text(1)


def test_owner_comment_and_changes_reason_reach_the_copywriter(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post(lifecycle="in_review")
    rig.add_owner_revision(post_id, 1, "agente")
    job = rig.claimed(post_id, "changes", "Mais curto, por favor.")
    assert rig.pipeline.generate(job) == "in_review"
    assert "Mais curto, por favor." in rig.llm.request_text(1)
    assert [r.rev_no for r in rig.revisions(post_id)] == [1, 2]
    assert rig.post(post_id).lifecycle == "in_review"


# --- voltas do Revisor ---------------------------------------------------------------------


def test_reviewer_rever_twice_then_goes_to_owner_with_notes(make_rig: MakeRig) -> None:
    script = (
        copy_and_review("rever", ["voz_incoerente"], "NOTA-UM", legenda="Rascunho um")
        + copy_and_review("rever", ["fora_do_tema"], "NOTA-DOIS", legenda="Rascunho dois")
        + copy_and_review("rever", ["limite_canal"], "NOTA-TRES", legenda="Rascunho tres")
    )
    rig = make_rig(script)
    post_id = rig.seed_post()
    job = rig.claimed(post_id)

    assert rig.pipeline.generate(job) == "in_review"

    assert len(rig.llm.requests) == 6  # 3 rascunhos, 2 voltas: nunca uma quarta passagem
    [revision] = rig.revisions(post_id)
    assert revision.caption == "Rascunho tres"
    assert (revision.review_verdict, revision.review_violations) == ("rever", ["limite_canal"])
    assert revision.review_notes == "NOTA-TRES"
    assert rig.post(post_id).review_rounds == 2
    # Cada volta leva so as notas do parecer anterior.
    assert "NOTA-UM" in rig.llm.request_text(3) and "NOTA-DOIS" not in rig.llm.request_text(3)
    assert "NOTA-DOIS" in rig.llm.request_text(5) and "NOTA-UM" not in rig.llm.request_text(5)
    assert "NOTA-UM" not in rig.llm.request_text(1)
    assert rig.job(job.id).status == "succeeded"


def test_aprovar_with_violations_is_treated_as_rever(make_rig: MakeRig) -> None:
    script = copy_and_review("aprovar", ["voz_incoerente"], "Ajusta a voz.") + copy_and_review(
        "aprovar", [], "Ok.", legenda="Segunda versao"
    )
    rig = make_rig(script)
    post_id = rig.seed_post()
    assert rig.pipeline.generate(rig.claimed(post_id)) == "in_review"
    assert len(rig.llm.requests) == 4
    assert "Ajusta a voz." in rig.llm.request_text(3)
    [revision] = rig.revisions(post_id)
    assert (revision.caption, revision.review_verdict) == ("Segunda versao", "aprovar")
    assert rig.post(post_id).review_rounds == 1


def test_aprovar_with_violations_after_last_round_is_stored_as_rever(make_rig: MakeRig) -> None:
    script = 3 * copy_and_review("aprovar", ["outro"], "Ainda mal.")
    rig = make_rig(script)
    post_id = rig.seed_post()
    assert rig.pipeline.generate(rig.claimed(post_id)) == "in_review"
    [revision] = rig.revisions(post_id)
    # Nunca se grava "aprovar" ao lado de violacoes: o painel mostraria um parecer incoerente.
    assert (revision.review_verdict, revision.review_violations) == ("rever", ["outro"])


def test_rejeitar_goes_to_owner_without_more_rounds(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review("rejeitar", ["instrucao_suspeita"], "Ordem escondida."))
    post_id = rig.seed_post()
    assert rig.pipeline.generate(rig.claimed(post_id)) == "in_review"
    assert len(rig.llm.requests) == 2
    [revision] = rig.revisions(post_id)
    assert (revision.review_verdict, revision.review_violations) == (
        "rejeitar",
        ["instrucao_suspeita"],
    )
    assert rig.post(post_id).lifecycle == "in_review"  # so o responsavel decide


def test_code_validation_failure_retries_inside_the_engine(make_rig: MakeRig) -> None:
    too_long = fake_scenarios.caption_draft("a" * 2300)
    rig = make_rig(too_long + copy_and_review())
    post_id = rig.seed_post()
    assert rig.pipeline.generate(rig.claimed(post_id)) == "in_review"
    assert len(rig.llm.requests) == 3
    [revision] = rig.revisions(post_id)
    assert len(revision.caption) < 2200


# --- falhas do motor -----------------------------------------------------------------------


def test_needs_human_leaves_post_drafting_with_reason(make_rig: MakeRig) -> None:
    bad = fake_scenarios.caption_draft(HOSTILE, hashtags=[f"{HOSTILE} x"], alt_text="")
    rig = make_rig(bad * 3)
    post_id = rig.seed_post()
    job = rig.claimed(post_id)

    assert rig.pipeline.generate(job) == "drafting_failed"

    post = rig.post(post_id)
    assert (post.lifecycle, post.last_error) == ("drafting", "NeedsHuman")
    stored = rig.job(job.id)
    assert (stored.status, stored.last_error) == ("failed", "NeedsHuman")
    assert rig.revisions(post_id) == []
    # Nem o texto do modelo nem as violacoes do validador chegam a `last_error`.
    for message in (post.last_error, stored.last_error):
        assert message is not None and HOSTILE not in message and "alt_text" not in message


def test_blocked_limit_fails_job_and_keeps_post_drafting(make_rig: MakeRig) -> None:
    network = [fake_scenarios.network_error_then("{}")[0]] * 2
    bad = fake_scenarios.caption_draft(alt_text="")
    rig = make_rig(bad + bad + network)  # 4 passos gastos sem saida valida: limite de passos
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    assert rig.pipeline.generate(job) == "drafting_failed"
    stored, post = rig.job(job.id), rig.post(post_id)
    assert stored.status == "failed" and stored.last_error is not None
    assert stored.last_error.startswith("RunLimitExceeded")
    assert (post.lifecycle, post.last_error) == ("drafting", stored.last_error)


def test_missing_capability_is_discoverable_in_the_job_error(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    text_only: frozenset[Capability] = frozenset({"text", "json_schema"})
    rig = build_rig(
        session_factory, clock, brand_id, copy_and_review(), reviewer_capabilities=text_only
    )
    post_id = rig.seed_post()
    with pytest.raises(RunConfigurationFailed) as caught:
        rig.pipeline.generate(rig.claimed(post_id))
    assert describe_error(caught.value) == (
        "RunConfigurationFailed: revisor: MissingCapability: papel 'revisor' exige "
        "capacidades em falta: ['image_input']"
    )


def test_unexpected_engine_failure_raises_a_fixed_message_error(make_rig: MakeRig) -> None:
    rig = make_rig([RuntimeError(HOSTILE)])
    post_id = rig.seed_post()
    with pytest.raises(AgentRunFailed) as caught:
        rig.pipeline.generate(rig.claimed(post_id))
    assert HOSTILE not in str(caught.value) and HOSTILE not in repr(caught.value)
    assert describe_error(caught.value) == "AgentRunFailed"
    assert rig.revisions(post_id) == [] and rig.post(post_id).last_error is None


def test_missing_profile_fails_job_without_retry(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post()
    with rig.factory.begin() as session:
        session.execute(text("DELETE FROM artifact_head WHERE type = 'perfil_marca'"))
    job = rig.claimed(post_id)
    assert rig.pipeline.generate(job) == "drafting_failed"
    stored = rig.job(job.id)
    assert stored.status == "failed" and (stored.last_error or "").startswith("MissingPrerequisite")
    assert rig.llm.requests == []


def test_malformed_payload_fails_job_without_retry(make_rig: MakeRig) -> None:
    rig = make_rig([])
    post_id = rig.seed_post()
    with rig.factory.begin() as session:
        job_id = rig.queue.enqueue_in(
            session, "generate_post", {"post_id": post_id, "reason": HOSTILE}, "k-malformed"
        )
    job = rig.queue.claim("w1")
    assert job is not None and job.id == job_id
    assert rig.pipeline.generate(job) == "drafting_failed"
    stored = rig.job(job.id)
    assert stored.status == "failed" and HOSTILE not in (stored.last_error or "")


# --- obsoletos (Foco 1) --------------------------------------------------------------------


def test_owner_revision_during_job_is_not_overwritten(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    rig.llm.hooks[2] = lambda: rig.add_owner_revision(post_id)  # o responsavel edita a meio

    assert rig.pipeline.generate(job) == "stale"

    [revision] = rig.revisions(post_id)
    assert (revision.origin, revision.caption) == ("edicao_responsavel", "Texto do responsavel.")
    assert rig.post(post_id).lifecycle == "drafting"  # nao foi para in_review por este job
    assert rig.job(job.id).status == "succeeded"  # concluido sem gravar


def test_cancelled_post_job_completes_without_writing(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post(lifecycle="cancelled")
    job = rig.claimed(post_id)
    assert rig.pipeline.generate(job) == "stale"
    assert rig.llm.requests == [] and rig.revisions(post_id) == []
    assert rig.job(job.id).status == "succeeded"
    assert rig.post(post_id).lifecycle == "cancelled"


def test_post_cancelled_during_job_is_not_written(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post()
    job = rig.claimed(post_id)

    def cancel() -> None:  # o que a aprovacao de um plano sem este slot faz
        with rig.factory.begin() as session:
            session.get_one(Post, post_id).lifecycle = "cancelled"
            rig.queue.cancel_for_post_in(session, post_id)

    rig.llm.hooks[2] = cancel
    assert rig.pipeline.generate(job) == "stale"
    assert rig.revisions(post_id) == []
    assert (rig.post(post_id).lifecycle, rig.job(job.id).status) == ("cancelled", "cancelled")


@pytest.mark.parametrize("lifecycle", ["approved", "rejected"])
def test_finished_post_is_not_generated_again(make_rig: MakeRig, lifecycle: str) -> None:
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post(lifecycle=lifecycle)
    assert rig.pipeline.generate(rig.claimed(post_id)) == "stale"
    assert rig.llm.requests == [] and rig.post(post_id).lifecycle == lifecycle


def test_in_review_post_is_only_regenerated_on_changes_request(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post(lifecycle="in_review")
    assert rig.pipeline.generate(rig.claimed(post_id, "plan")) == "stale"
    assert rig.llm.requests == [] and rig.revisions(post_id) == []


def test_missing_post_completes_job_as_stale(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review())
    job = rig.claimed("00000000-0000-0000-0000-000000000000")
    assert rig.pipeline.generate(job) == "stale"
    assert rig.job(job.id).status == "succeeded"


def stale_mutations(rig: ContentRig, post_id: str) -> dict[str, Callable[[], None]]:
    return {
        "revisao_do_responsavel": lambda: rig.add_owner_revision(post_id),
        "revisao_de_outro_job": lambda: rig.add_owner_revision(post_id, 1, "agente"),
        "brief": mutate_post(rig, post_id, brief={"tema": "Outro tema", "kb_refs": []}),
        "canais": mutate_post(rig, post_id, channels=["facebook"]),
        "data": mutate_post(rig, post_id, scheduled_date=date(2026, 11, 10)),
        "aprovado": mutate_post(rig, post_id, lifecycle="approved"),
        "planeado_de_novo": mutate_post(rig, post_id, lifecycle="planned"),
    }


@pytest.mark.parametrize(
    "case",
    ["revisao_do_responsavel", "revisao_de_outro_job", "brief", "canais", "data", "aprovado",
     "planeado_de_novo"],
)  # fmt: skip
def test_stale_detection_cases(make_rig: MakeRig, case: str) -> None:
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    rig.llm.hooks[2] = stale_mutations(rig, post_id)[case]
    assert rig.pipeline.generate(job) == "stale"
    revisions = rig.revisions(post_id)  # nenhuma gravacao do job obsoleto
    assert [r.caption for r in revisions] in ([], ["Texto do responsavel."])
    assert rig.job(job.id).status == "succeeded"


def test_repointed_artifact_pointers_are_not_stale(make_rig: MakeRig) -> None:
    """Aprovar um plano identico repoe `plan_artifact_id`/`strategy_artifact_id` dos posts."""
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    other_plan = rig.store.create(rig.brand_id, ArtifactType.PLANO, {"semana": WEEK, "slots": []})
    rig.llm.hooks[2] = mutate_post(
        rig, post_id, plan_artifact_id=other_plan.id, strategy_artifact_id=rig.plan_id
    )
    assert rig.pipeline.generate(job) == "in_review"
    assert len(rig.revisions(post_id)) == 1
    assert rig.post(post_id).plan_artifact_id == other_plan.id  # o reposicionamento sobrevive


# --- lease perdida -------------------------------------------------------------------------


def steal_lease(rig: ContentRig) -> Callable[[], None]:
    """Outro worker reclama o job depois de a lease expirar."""

    def steal() -> None:
        rig.expire_leases()
        assert rig.queue.requeue_expired() == 1
        assert rig.queue.claim("w2") is not None

    return steal


def test_lost_lease_rolls_back_the_whole_write(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    rig.llm.hooks[2] = steal_lease(rig)

    assert rig.pipeline.generate(job) == "stale"  # sem erro

    assert rig.revisions(post_id) == []
    assert rig.post(post_id).lifecycle == "drafting"
    stored = rig.job(job.id)
    assert (stored.status, stored.locked_by) == ("running", "w2")  # intacto, do outro worker


def test_queue_fail_false_path_writes_nothing_else(make_rig: MakeRig) -> None:
    bad = fake_scenarios.caption_draft(alt_text="")
    rig = make_rig(bad * 3)
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    rig.llm.hooks[1] = steal_lease(rig)

    assert rig.pipeline.generate(job) == "stale"  # nao levanta

    assert rig.post(post_id).last_error is None
    stored = rig.job(job.id)
    assert (stored.status, stored.locked_by) == ("running", "w2")
    assert stored.last_error == "LeaseExpired: lease expirada sem conclusao"


def test_slow_executor_after_lease_loss_does_not_duplicate_the_revision(make_rig: MakeRig) -> None:
    # Pedidos: 1 copia A | 2 copia B | 3 revisao B | 4 revisao A (a sua gravacao e obsoleta).
    rig = make_rig(
        [
            *fake_scenarios.caption_draft("Executor A"),
            *fake_scenarios.caption_draft("Executor B"),
            *fake_scenarios.review("aprovar"),
            *fake_scenarios.review("aprovar"),
        ]
    )
    post_id = rig.seed_post()
    job_a = rig.claimed(post_id)
    outcomes: list[str] = []

    def second_executor() -> None:
        rig.expire_leases()
        assert rig.queue.requeue_expired() == 1
        job_b = rig.queue.claim("w2")
        assert job_b is not None
        outcomes.append(rig.pipeline.generate(job_b))

    rig.llm.hooks[2] = second_executor
    outcomes.append(rig.pipeline.generate(job_a))

    assert outcomes == ["in_review", "stale"]
    [revision] = rig.revisions(post_id)
    assert (revision.rev_no, revision.caption) == (1, "Executor B")
    assert rig.job(job_a.id).status == "succeeded"


# --- ordem dos locks -----------------------------------------------------------------------


def wait_for_lock_waiter(factory: Factory, timeout_s: float = 10.0) -> None:
    """Espera ate haver uma sessao bloqueada num lock de linha (o pipeline a esperar o post)."""
    deadline = threading.Event()
    with factory() as session:
        for _ in range(int(timeout_s * 20)):
            waiting = session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
            if waiting:
                return
            deadline.wait(0.05)
            session.rollback()
    raise AssertionError("nenhuma sessao ficou a esperar por um lock")


def test_plan_reapproval_during_save_does_not_deadlock(make_rig: MakeRig) -> None:
    """A aprovacao bloqueia posts -> jobs; o pipeline tem de fazer o mesmo (nunca job -> post)."""
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    reached, approval_holds_post = threading.Event(), threading.Event()

    def pause_before_save() -> None:
        reached.set()
        assert approval_holds_post.wait(10)

    rig.llm.hooks[2] = pause_before_save
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rig.pipeline.generate, job)
        assert reached.wait(10)
        with rig.factory.begin() as session:  # a transacao de aprovacao do plano
            session.scalars(select(Post).where(Post.id == post_id).with_for_update()).one()
            approval_holds_post.set()
            wait_for_lock_waiter(rig.factory)  # o pipeline fica a espera do post, sem tocar no job
            rig.queue.cancel_for_post_in(session, post_id)  # segundo lock: o job
            session.execute(
                update(Post)
                .where(Post.id == post_id)
                .values(brief={"tema": "Novo tema", "kb_refs": []}, lifecycle="planned")
            )
        outcome = future.result(timeout=20)  # um impasse apareceria aqui como DeadlockDetected

    assert outcome == "stale"
    assert rig.revisions(post_id) == []
    assert rig.job(job.id).status == "cancelled"


# --- hash canonico -------------------------------------------------------------------------


def test_canonical_hash_is_sha256_of_sorted_json() -> None:
    claims = [{"texto": "t", "kb_ref": None}]
    expected = hashlib.sha256(
        json.dumps(
            {
                "alegacoes": claims,
                "alt_text": "alt",
                "apelo": "apelo",
                "canais": ["facebook", "instagram"],
                "hashtags": ["#a"],
                "legenda": "legenda ç",
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    fields = {
        "caption": "legenda ç",
        "hashtags": ["#a"],
        "call_to_action": "apelo",
        "alt_text": "alt",
        "claims": claims,
    }
    assert canonical_revision_hash(**fields, channels=["instagram", "facebook"]) == expected
    assert canonical_revision_hash(**fields, channels=["facebook", "instagram"]) == expected


@pytest.mark.parametrize(
    "change",
    [
        {"caption": "outra"},
        {"hashtags": ["#b"]},
        {"call_to_action": None},
        {"alt_text": "outro"},
        {"claims": []},
        {"channels": ["instagram"]},
    ],
)
def test_canonical_hash_changes_with_each_field(change: dict[str, object]) -> None:
    base: dict[str, object] = {
        "caption": "legenda",
        "hashtags": ["#a"],
        "call_to_action": "apelo",
        "alt_text": "alt",
        "claims": [{"texto": "t", "kb_ref": None}],
        "channels": ["facebook", "instagram"],
    }
    assert canonical_revision_hash(**{**base, **change}) != canonical_revision_hash(**base)  # type: ignore[arg-type]


def test_two_jobs_in_a_row_each_write_their_own_revision(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review() + copy_and_review())
    first, second = rig.seed_post(f"{WEEK}-1"), rig.seed_post(f"{WEEK}-2")
    assert rig.pipeline.generate(rig.claimed(first)) == "in_review"
    assert rig.pipeline.generate(rig.claimed(second)) == "in_review"
    assert [len(rig.revisions(p)) for p in (first, second)] == [1, 1]


def test_canonical_hash_ignores_unicode_normalization_form() -> None:
    fields = {"hashtags": ["#a"], "call_to_action": "x", "alt_text": "y", "claims": []}
    composed = canonical_revision_hash(caption="Pão açorda", channels=["instagram"], **fields)
    decomposed = canonical_revision_hash(
        caption=unicodedata.normalize("NFD", "Pão açorda"), channels=["instagram"], **fields
    )
    assert unicodedata.normalize("NFD", "Pão açorda") != "Pão açorda"
    assert composed == decomposed


def test_describe_stop_keeps_cost_and_step_limits_apart_without_messages() -> None:
    cost = RunResult(RunStatus.BLOCKED_LIMIT, "r", None, HOSTILE, RunCostLimitExceeded)
    steps = RunResult(RunStatus.BLOCKED_LIMIT, "r", None, HOSTILE, RunLimitExceeded)
    human = RunResult(RunStatus.NEEDS_HUMAN, "r", None, HOSTILE)
    assert describe_stop(cost) == "RunCostLimitExceeded"
    assert describe_stop(steps) == "RunLimitExceeded"
    assert describe_stop(human) == "NeedsHuman"


def test_job_cancelled_before_begin_leaves_post_and_llm_alone(make_rig: MakeRig) -> None:
    rig = make_rig(copy_and_review())
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    with rig.factory.begin() as session:  # a aprovacao de um plano cancela o job entretanto
        rig.queue.cancel_for_post_in(session, post_id)
    assert rig.pipeline.generate(job) == "stale"
    assert rig.post(post_id).lifecycle == "planned"
    assert rig.llm.requests == [] and rig.revisions(post_id) == []
    assert rig.job(job.id).status == "cancelled"


def test_fail_job_with_lost_lease_does_not_write_the_post(make_rig: MakeRig) -> None:
    rig = make_rig([])
    post_id = rig.seed_post(lifecycle="drafting")
    job = rig.claimed(post_id)
    steal_lease(rig)()  # o job passa a outro worker (e ao seu post, no futuro)
    assert rig.pipeline.fail_job(job, "NeedsHuman", retry=False) is None
    assert rig.post(post_id).last_error is None
    assert rig.job(job.id).locked_by == "w2"


@pytest.mark.parametrize("retry", [True, False])
def test_fail_job_writes_post_error_only_when_terminal_and_drafting(
    make_rig: MakeRig, retry: bool
) -> None:
    rig = make_rig([])
    drafting = rig.seed_post(f"{WEEK}-1", lifecycle="drafting")
    in_review = rig.seed_post(f"{WEEK}-2", lifecycle="in_review")
    for post_id in (drafting, in_review):
        job = rig.claimed(post_id)
        status = rig.pipeline.fail_job(job, "NeedsHuman", retry=retry)
        assert status == ("queued" if retry else "failed")
    assert rig.post(drafting).last_error == (None if retry else "NeedsHuman")
    assert rig.post(in_review).last_error is None
