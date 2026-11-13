"""Julgamentos cegos serializados com a revelacao e conservados como respostas imutaveis."""

import pytest
from content_rig import package_pass
from eval_rig import brief
from sqlalchemy import select

from marketing_agent.db.models import EvalJudgement
from marketing_agent.evals.judging import (
    AlreadyRevealed,
    JudgementInput,
    JudgingService,
    RevealBlocked,
    RunNotReady,
    StaleJudgement,
)
from marketing_agent.evals.views import EvalNotFound
from marketing_agent.llm.fake_scenarios import simple_package

ANSWER = {"better": "a", "errors_a": [], "errors_b": ["voz_errada"], "edit_a": "0", "edit_b": "2_5"}


def ready(rig):
    run_id = rig.start()
    for item in rig.items(run_id):
        rig.process(item, simple_package() if item.path == "simples" else package_pass())
    return run_id


def service(rig):
    return JudgingService(rig.factory, rig.content.clock)


def test_pairs_with_failed_item_excluded(make_eval_rig):
    rig = make_eval_rig()
    run_id = rig.start()
    with pytest.raises(RunNotReady):
        service(rig).pair(rig.brand_id, run_id, 1)
    for item in rig.items(run_id):
        if item.brief_key == "b1":
            rig.process(item, simple_package() if item.path == "simples" else package_pass())
        else:
            claimed = rig.claim(item)
            rig.handler.fail_job(claimed, "teste", retry=False)
    judging = service(rig)
    assert judging.pair(rig.brand_id, run_id, 1).brief.tema == "Tema b1"
    with pytest.raises(EvalNotFound):
        judging.pair(rig.brand_id, run_id, 2)
    assert judging.summary(rig.brand_id, run_id).excluded == 1


def test_reveal_blocked_until_all_pairs_judged(make_eval_rig):
    rig = make_eval_rig()
    run_id = ready(rig)
    judging = service(rig)
    with pytest.raises(RevealBlocked):
        judging.reveal(rig.brand_id, run_id, "ana")
    for index in (1, 2):
        judging.record(rig.brand_id, run_id, index, JudgementInput(**ANSWER), 12, None, "ana")
    judging.reveal(rig.brand_id, run_id, "ana")
    run = rig.run(run_id)
    assert run.status == "revealed" and run.revealed_at == rig.content.clock.now()


def test_correction_keeps_both_rows_with_supersedes(make_eval_rig):
    rig = make_eval_rig(briefs=[brief("b1")])
    run_id = ready(rig)
    judging = service(rig)
    first = judging.record(rig.brand_id, run_id, 1, JudgementInput(**ANSWER), 3, None, "ana")
    answer = JudgementInput(**{**ANSWER, "better": "empate"})
    second = judging.record(rig.brand_id, run_id, 1, answer, 10, first, "ana")
    with rig.factory() as session:
        rows = list(session.scalars(select(EvalJudgement).order_by(EvalJudgement.decided_at)))
    assert len(rows) == 2
    assert {row.supersedes_id for row in rows} == {None, first}
    assert judging.pair(rig.brand_id, run_id, 1).current.id == second
    assert judging.pair(rig.brand_id, run_id, 1).current.answer.better == "empate"


def test_stale_replaces_id_raises(make_eval_rig):
    rig = make_eval_rig()
    run_id = ready(rig)
    judging = service(rig)
    answer = JudgementInput(**ANSWER)
    first = judging.record(rig.brand_id, run_id, 1, answer, 0, None, "ana")
    for index, replaces in ((1, None), (1, "bad"), (2, first)):
        with pytest.raises(StaleJudgement):
            judging.record(rig.brand_id, run_id, index, answer, 0, replaces, "ana")


def test_judging_after_reveal_refused(make_eval_rig):
    rig = make_eval_rig(briefs=[brief("b1")])
    run_id = ready(rig)
    judging = service(rig)
    answer = JudgementInput(**ANSWER)
    first = judging.record(rig.brand_id, run_id, 1, answer, 1, None, "ana")
    judging.reveal(rig.brand_id, run_id, "ana")
    with pytest.raises(AlreadyRevealed):
        judging.record(rig.brand_id, run_id, 1, answer, 1, first, "ana")
    with pytest.raises(EvalNotFound):
        judging.pair("outra-marca", run_id, 1)
    with pytest.raises(EvalNotFound):
        judging.pair_image(rig.brand_id, run_id, 0, "a")
