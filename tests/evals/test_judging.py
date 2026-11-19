"""Respostas cegas: pares comparaveis, imutabilidade com rasto, prova de versao e revelacao."""

from pathlib import Path

import pytest
from eval_rig import EvalRig, build_eval_rig
from sqlalchemy import select, update

from marketing_agent.clock import FixedClock
from marketing_agent.db.models import EvalItem, EvalJudgement
from marketing_agent.evals.blind import side_a_path
from marketing_agent.evals.judging import (
    AlreadyRevealed,
    JudgementInput,
    PairNotFound,
    RevealBlocked,
    RunNotReady,
    StaleJudgement,
)

ANSWER = JudgementInput(better="a", errors_b=["voz_errada"], edit_a="0", edit_b="2_5")


@pytest.fixture
def rig(session_factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> EvalRig:  # type: ignore[no-untyped-def]
    built = build_eval_rig(session_factory, clock, brand_id, tmp_path / "m")
    built.add_price_item()
    return built


@pytest.fixture
def ready(rig: EvalRig) -> str:
    run_id = rig.start()
    rig.drain()
    return run_id


def record(rig: EvalRig, run_id: str, index: int, replaces: str | None = None) -> str:
    return rig.judging.record(
        rig.brand_id, run_id, index, ANSWER, seconds_spent=30, replaces_id=replaces, actor="ana"
    )


def test_pairs_need_a_finished_run(rig: EvalRig) -> None:
    run_id = rig.start()
    with pytest.raises(RunNotReady):
        rig.judging.pair(rig.brand_id, run_id, 1)


def test_pair_follows_the_seed_and_hides_the_review(rig: EvalRig, ready: str) -> None:
    pair = rig.judging.pair(rig.brand_id, ready, 1)
    assert (pair.index, pair.total, pair.brief.tema) == (1, 2, "Pao do dia")
    run = rig.run(ready)
    first = side_a_path(run.seed, "pao_do_dia")
    items = {item.path: item for item in rig.items(ready) if item.brief_key == "pao_do_dia"}
    a_snapshot = items[first].revision_snapshot or {}
    assert pair.a.legenda == a_snapshot["texto"]["legenda"]  # type: ignore[index]
    assert pair.current is None
    with pytest.raises(PairNotFound):
        rig.judging.pair(rig.brand_id, ready, 3)


def test_pairs_with_failed_item_excluded(rig: EvalRig, ready: str) -> None:
    with rig.factory.begin() as session:
        session.execute(
            update(EvalItem)
            .where(EvalItem.run_id == ready, EvalItem.brief_key == "pao_do_dia")
            .values(status="failed")
        )
    pair = rig.judging.pair(rig.brand_id, ready, 1)
    assert pair.total == 1 and pair.brief.tema != "Pao do dia"


def test_record_stores_side_a_path_and_seconds(rig: EvalRig, ready: str) -> None:
    judgement_id = record(rig, ready, 1)
    with rig.factory() as session:
        row = session.get_one(EvalJudgement, judgement_id)
    assert row.side_a_path == side_a_path(rig.run(ready).seed, "pao_do_dia")
    assert (row.better, row.errors_b, row.edit_b, row.seconds_spent) == (
        "a",
        ["voz_errada"],
        "2_5",
        30,
    )


def test_correction_keeps_both_rows_with_supersedes(rig: EvalRig, ready: str) -> None:
    first = record(rig, ready, 1)
    second = record(rig, ready, 1, replaces=first)
    with rig.factory() as session:
        rows = session.scalars(select(EvalJudgement).order_by(EvalJudgement.decided_at)).all()
    assert {row.id for row in rows} == {first, second}
    assert next(r for r in rows if r.id == second).supersedes_id == first
    current = rig.judging.pair(rig.brand_id, ready, 1).current
    assert current is not None and current.id == second


def test_stale_replaces_id_raises(rig: EvalRig, ready: str) -> None:
    first = record(rig, ready, 1)
    record(rig, ready, 1, replaces=first)
    with pytest.raises(StaleJudgement):
        record(rig, ready, 1, replaces=first)  # outra aba ainda via a primeira
    with pytest.raises(StaleJudgement):
        record(rig, ready, 1)  # "primeira resposta" quando ja ha uma


def test_reveal_blocked_until_all_pairs_judged(rig: EvalRig, ready: str) -> None:
    record(rig, ready, 1)
    with pytest.raises(RevealBlocked) as blocked:
        rig.judging.reveal(rig.brand_id, ready, "ana")
    assert blocked.value.missing == (2,)
    record(rig, ready, 2)
    assert rig.judging.progress(rig.brand_id, ready).judged == 2
    rig.judging.reveal(rig.brand_id, ready, "ana")
    run = rig.run(ready)
    assert run.status == "revealed" and run.revealed_at is not None
    rig.judging.reveal(rig.brand_id, ready, "ana")  # idempotente


def test_judging_after_reveal_refused(rig: EvalRig, ready: str) -> None:
    for index in (1, 2):
        record(rig, ready, index)
    rig.judging.reveal(rig.brand_id, ready, "ana")
    current = rig.judging.pair(rig.brand_id, ready, 1).current
    with pytest.raises(AlreadyRevealed):
        record(rig, ready, 1, replaces=current.id if current else None)


def test_other_brand_cannot_see_the_run(rig: EvalRig, ready: str) -> None:
    with pytest.raises(RunNotReady):
        rig.judging.pair("outra-marca", ready, 1)


def test_judgement_input_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        JudgementInput(better="c", edit_a="0", edit_b="0")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        JudgementInput(better="a", errors_a=["outro", "outro"], edit_a="0", edit_b="0")
    assert JudgementInput(better="a", edit_a="0", edit_b="0", note="  ").note is None
