"""Previsao e confirmacao ao reaprovar o plano de uma semana que ja tem publicacoes decididas.

A previsao e a aplicacao partilham a mesma regra de decisao (`planning.slot_rules`): a tabela
abaixo corre as duas sobre os mesmos casos e exige que concordem.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import pytest
from planning_rig import WEEK, Rig, Script, change_theme, plan_response, plans
from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection

from marketing_agent.artifacts.store import ArtifactNotFound, ArtifactState, ArtifactType
from marketing_agent.db.models import AuditLog, Job, Post
from marketing_agent.planning.errors import ConfirmationRequired
from marketing_agent.planning.slot_rules import SlotOutcome

MakeRig = Callable[[Script], Rig]
NEW, REWRITTEN, UNCHANGED, RESTARTED = (
    SlotOutcome.NEW,
    SlotOutcome.REWRITTEN,
    SlotOutcome.UNCHANGED,
    SlotOutcome.RESTARTED,
)
DECIDED_KEPT, DECIDED_DIFFERS, CANCELLED = (
    SlotOutcome.DECIDED_KEPT,
    SlotOutcome.DECIDED_DIFFERS,
    SlotOutcome.CANCELLED,
)


def key(slot: int) -> str:
    return f"{WEEK}-{slot}"


@dataclass(frozen=True)
class Case:
    """Plano A (4 slots) aprovado; os posts ficam nos estados dados; depois o plano B."""

    name: str
    lifecycles: Mapping[int, str]
    b_slots: int
    b_themes: Mapping[int, str] = field(default_factory=dict)  # indice do slot (0-based) -> tema
    dead_job_slots: tuple[int, ...] = ()
    expected: Mapping[int, SlotOutcome] = field(default_factory=dict)
    needs_confirmation: bool = False


CASES = [
    Case("identical", {}, 4, expected={1: UNCHANGED, 2: UNCHANGED, 3: UNCHANGED, 4: UNCHANGED}),
    Case(
        "mixed_states_theme_change",
        {2: "drafting", 3: "in_review", 4: "approved"},
        4,
        b_themes={0: "Outro 1", 1: "Outro 2", 2: "Outro 3", 3: "Outro 4"},
        expected={1: REWRITTEN, 2: REWRITTEN, 3: DECIDED_DIFFERS, 4: DECIDED_DIFFERS},
    ),
    Case(
        "decided_same_theme",
        {1: "in_review", 2: "approved", 3: "rejected"},
        4,
        expected={1: DECIDED_KEPT, 2: DECIDED_KEPT, 3: DECIDED_KEPT, 4: UNCHANGED},
    ),
    Case(
        "removed_in_review_and_approved",
        {2: "in_review", 3: "approved", 4: "rejected"},
        1,
        expected={1: UNCHANGED, 2: CANCELLED, 3: CANCELLED},  # o rejected removido nao entra
        needs_confirmation=True,
    ),
    Case(
        "removed_approved_only",
        {4: "approved"},
        3,
        expected={1: UNCHANGED, 2: UNCHANGED, 3: UNCHANGED, 4: CANCELLED},
        needs_confirmation=True,
    ),
    Case(
        "removed_only_undecided",
        {3: "drafting"},
        2,
        expected={1: UNCHANGED, 2: UNCHANGED, 3: CANCELLED, 4: CANCELLED},
    ),
    Case(
        "more_slots",
        {},
        5,
        expected={1: UNCHANGED, 2: UNCHANGED, 3: UNCHANGED, 4: UNCHANGED, 5: NEW},
    ),
    Case(
        "dead_job_restarts",
        {2: "drafting"},
        4,
        dead_job_slots=(1, 2),
        expected={1: RESTARTED, 2: RESTARTED, 3: UNCHANGED, 4: UNCHANGED},
    ),
    Case(
        "cancelled_is_revived",
        {2: "cancelled"},
        4,
        expected={1: UNCHANGED, 2: REWRITTEN, 3: UNCHANGED, 4: UNCHANGED},
    ),
    Case(
        "removed_in_review_and_rewritten_planned",
        {3: "in_review"},
        2,
        b_themes={0: "Novo tema"},
        expected={1: REWRITTEN, 2: UNCHANGED, 3: CANCELLED, 4: CANCELLED},
        needs_confirmation=True,
    ),
]


def observe(rig: Rig, before: dict[str, Post], jobs_before: set[str]) -> dict[str, str]:
    """Classe observada por slot: new, rewritten, unchanged, restarted, decided ou cancelled."""
    after = rig.post_by_key()
    new_job_posts = {str(job.payload["post_id"]) for job in rig.jobs() if job.id not in jobs_before}
    result: dict[str, str] = {}
    for slot_key, post in after.items():
        old = before.get(slot_key)
        if old is None:
            result[slot_key] = "new"
        elif old.lifecycle in ("in_review", "approved", "rejected"):
            result[slot_key] = "cancelled" if post.lifecycle == "cancelled" else "decided"
        elif post.lifecycle == "cancelled" and old.lifecycle != "cancelled":
            result[slot_key] = "cancelled"
        elif dict(post.brief) != dict(old.brief) or old.lifecycle == "cancelled":
            result[slot_key] = "rewritten"
        else:
            result[slot_key] = "restarted" if post.id in new_job_posts else "unchanged"
    return result


GROUP = {
    NEW: "new",
    REWRITTEN: "rewritten",
    UNCHANGED: "unchanged",
    RESTARTED: "restarted",
    DECIDED_KEPT: "decided",
    DECIDED_DIFFERS: "decided",
    CANCELLED: "cancelled",
}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_preview_and_apply_agree(make_rig: MakeRig, case: Case) -> None:
    edit = None
    if case.b_themes:
        themes = case.b_themes

        def edit(slots: list[dict[str, object]]) -> None:
            for index, theme in themes.items():
                slots[index]["tema"] = theme

    rig = make_rig(plans(plan_response(WEEK, 4), plan_response(WEEK, case.b_slots, edit)))
    rig.activate_strategy()
    _, post_ids = rig.propose_and_approve()
    for slot, lifecycle in case.lifecycles.items():
        rig.set_lifecycle(key(slot), lifecycle)
    for slot in case.dead_job_slots:
        rig.set_job_status(post_ids[slot - 1], "failed")
    plan_b = rig.propose()

    preview = rig.service.preview_approval(rig.brand_id, plan_b)

    assert {e.slot_key: e.outcome for e in preview.entries} == {
        key(slot): outcome for slot, outcome in case.expected.items()
    }
    assert bool(preview.cancels_decided) is case.needs_confirmation
    before, jobs_before = rig.post_by_key(), {job.id for job in rig.jobs()}
    if case.needs_confirmation:
        with pytest.raises(ConfirmationRequired):
            rig.service.approve_plan(plan_b, "ana")
        assert rig.post_by_key().keys() == before.keys()  # nada escrito
    rig.service.approve_plan(plan_b, "ana", confirm_cancel=True)
    observed = observe(rig, before, jobs_before)
    for entry in preview.entries:
        assert observed[entry.slot_key] == GROUP[entry.outcome], entry


def test_preview_counts_and_entry_details(make_rig: MakeRig) -> None:
    edit = change_theme(2, "Tema novo 3")
    rig = make_rig(plans(plan_response(WEEK, 4), plan_response(WEEK, 3, edit)))
    rig.activate_strategy()
    rig.propose_and_approve()
    rig.set_lifecycle(key(3), "in_review")
    rig.set_lifecycle(key(4), "approved")
    old_themes = {k: p.brief["tema"] for k, p in rig.post_by_key().items()}
    plan_b = rig.propose()

    preview = rig.service.preview_approval(rig.brand_id, plan_b)

    assert (preview.created, preview.rewritten, preview.kept, preview.kept_differs) == (0, 0, 2, 1)
    assert preview.cancelled == 1 and preview.has_existing_posts
    by_key = {e.slot_key: e for e in preview.entries}
    differs = by_key[key(3)]
    assert differs.outcome is DECIDED_DIFFERS and differs.lifecycle == "in_review"
    assert (differs.old_theme, differs.new_theme) == (old_themes[key(3)], "Tema novo 3")
    removed = by_key[key(4)]
    assert removed.outcome is CANCELLED and removed.lifecycle == "approved"
    assert (removed.old_theme, removed.new_theme) == (old_themes[key(4)], None)
    assert removed.scheduled_date == rig.post_by_key()[key(4)].scheduled_date
    assert [e.slot_key for e in preview.cancels_decided] == [key(4)]
    assert [e.slot_key for e in preview.entries] == [key(1), key(2), key(3), key(4)]


def test_preview_of_a_week_without_posts_is_all_new(make_rig: MakeRig) -> None:
    rig = make_rig(plans(plan_response(WEEK, 3)))
    rig.activate_strategy()
    preview = rig.service.preview_approval(rig.brand_id, rig.propose())
    assert preview.created == 3 and not preview.has_existing_posts
    assert not preview.cancels_decided


def test_preview_writes_nothing_and_takes_no_locks(make_rig: MakeRig) -> None:
    rig = make_rig(plans(plan_response(WEEK, 4), plan_response(WEEK, 2)))
    rig.activate_strategy()
    rig.propose_and_approve()
    rig.set_lifecycle(key(3), "approved")
    plan_b = rig.propose()
    posts_before = [(p.slot_key, p.lifecycle, p.plan_artifact_id) for p in rig.posts()]
    counts_before = (rig.count(Job), rig.count(Post))
    statements: list[str] = []

    def capture(conn: Connection, cursor: object, statement: str, *rest: object) -> None:
        statements.append(statement)

    event.listen(rig.factory.kw["bind"], "before_cursor_execute", capture)
    try:
        rig.service.preview_approval(rig.brand_id, plan_b)
    finally:
        event.remove(rig.factory.kw["bind"], "before_cursor_execute", capture)

    assert statements, "a previsao tem de ler a base"
    forbidden = re.compile(r"^\s*(INSERT|UPDATE|DELETE)|FOR UPDATE", re.IGNORECASE)
    assert not [s for s in statements if forbidden.search(s)]
    assert [(p.slot_key, p.lifecycle, p.plan_artifact_id) for p in rig.posts()] == posts_before
    assert (rig.count(Job), rig.count(Post)) == counts_before
    assert rig.store.get(plan_b).state is ArtifactState.SUBMITTED


def test_preview_of_unknown_other_brand_or_wrong_type_plan_fails(make_rig: MakeRig) -> None:
    rig = make_rig(plans(plan_response(WEEK, 2)))
    strategy_id = rig.activate_strategy()
    plan_id = rig.propose()
    with pytest.raises(ArtifactNotFound):
        rig.service.preview_approval(rig.brand_id, "nao-existe")
    with pytest.raises(ArtifactNotFound):
        rig.service.preview_approval("outra-marca", plan_id)
    with pytest.raises(ValueError, match="tipo"):
        rig.service.preview_approval(rig.brand_id, strategy_id)


def test_confirmation_is_required_only_for_approved_or_in_review_posts(make_rig: MakeRig) -> None:
    rig = make_rig(plans(plan_response(WEEK, 4), plan_response(WEEK, 1)))
    rig.activate_strategy()
    rig.propose_and_approve()
    rig.set_lifecycle(key(2), "drafting")
    rig.set_lifecycle(key(3), "rejected")
    rig.set_lifecycle(key(4), "cancelled")
    plan_b = rig.propose()
    assert not rig.service.preview_approval(rig.brand_id, plan_b).cancels_decided
    rig.service.approve_plan(plan_b, "ana")  # sem confirmacao: nada decido e cancelado
    lifecycles = {k: p.lifecycle for k, p in rig.post_by_key().items()}
    assert lifecycles == {
        key(1): "planned",
        key(2): "cancelled",
        key(3): "rejected",
        key(4): "cancelled",
    }


@pytest.mark.parametrize("lifecycle", ["approved", "in_review"])
def test_decided_post_is_cancelled_only_with_confirmation(
    make_rig: MakeRig, lifecycle: str
) -> None:
    rig = make_rig(plans(plan_response(WEEK, 2), plan_response(WEEK, 1)))
    rig.activate_strategy()
    _, post_ids = rig.propose_and_approve()
    rig.set_lifecycle(key(2), lifecycle)
    plan_b = rig.propose()
    jobs_before = [(j.id, j.status) for j in rig.jobs()]
    audits_before = len(rig.audits("plan.approve"))

    with pytest.raises(ConfirmationRequired) as refused:
        rig.service.approve_plan(plan_b, "ana")

    message = str(refused.value)
    assert all(text not in message for text in (post_ids[1], plan_b, lifecycle, rig.brand_id))
    assert rig.store.get(plan_b).state is ArtifactState.SUBMITTED  # a decisao foi desfeita
    head = rig.store.active(rig.brand_id, ArtifactType.PLANO, scope=f"week:{WEEK}")
    assert head is not None and head.id != plan_b
    assert rig.post_by_key()[key(2)].lifecycle == lifecycle
    assert [(j.id, j.status) for j in rig.jobs()] == jobs_before
    assert len(rig.audits("plan.approve")) == audits_before

    rig.service.approve_plan(plan_b, "ana", confirm_cancel=True)

    assert rig.post_by_key()[key(2)].lifecycle == "cancelled"
    assert rig.store.get(plan_b).state is ArtifactState.APPROVED
    assert rig.audits("plan.approve")[-1].detail["cancelados"] == 1


def test_confirmation_is_recomputed_at_apply_time(make_rig: MakeRig) -> None:
    """A previsao nao prende nada: se o post foi aprovado entretanto, o servico volta a exigir."""
    rig = make_rig(plans(plan_response(WEEK, 2), plan_response(WEEK, 1)))
    rig.activate_strategy()
    rig.propose_and_approve()
    plan_b = rig.propose()
    assert not rig.service.preview_approval(rig.brand_id, plan_b).cancels_decided
    rig.set_lifecycle(key(2), "approved")  # o responsavel aprova noutra aba
    with pytest.raises(ConfirmationRequired):
        rig.service.approve_plan(plan_b, "ana")
    assert rig.post_by_key()[key(2)].lifecycle == "approved"


def test_confirmation_is_not_a_blank_cheque_for_other_weeks(make_rig: MakeRig) -> None:
    rig = make_rig(plans(plan_response("2026-W47", 2), plan_response(WEEK, 2)))
    rig.activate_strategy()
    rig.propose_and_approve("2026-W47")
    rig.set_lifecycle("2026-W47-2", "approved")
    rig.propose_and_approve(WEEK, confirm_cancel=True)  # confirmar nao toca noutra semana
    assert rig.post_by_key()["2026-W47-2"].lifecycle == "approved"


def test_apply_and_preview_lock_the_week_in_the_documented_order(make_rig: MakeRig) -> None:
    """O servico bloqueia posts so ao aplicar; o SELECT bloqueante continua ordenado por id."""
    rig = make_rig(plans(plan_response(WEEK, 2), plan_response(WEEK, 2)))
    rig.activate_strategy()
    rig.propose_and_approve()
    plan_b = rig.propose()
    statements: list[str] = []

    def capture(conn: Connection, cursor: object, statement: str, *rest: object) -> None:
        statements.append(statement)

    bind = rig.factory.kw["bind"]
    event.listen(bind, "before_cursor_execute", capture)
    try:
        rig.service.approve_plan(plan_b, "ana")
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    [locking] = [s for s in statements if "FROM post" in s and "FOR UPDATE" in s]
    assert re.search(r"ORDER BY post\.id\s+FOR UPDATE", locking)


def test_audit_is_unchanged_when_nothing_decided_is_involved(make_rig: MakeRig) -> None:
    rig = make_rig(plans(plan_response(WEEK, 2)))
    rig.activate_strategy()
    rig.propose_and_approve()
    with rig.factory() as session:
        details = session.scalars(select(AuditLog.detail).where(AuditLog.action == "plan.approve"))
        assert list(details) == [
            {"semana": WEEK, "posts": 2, "criados": 2, "atualizados": 0, "mantidos": 0,
             "cancelados": 0, "jobs": 2}
        ]  # fmt: skip
        assert session.scalar(select(func.count()).select_from(Post)) == 2
