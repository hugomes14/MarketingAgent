"""Achados da revisao independente do PR #20: cada teste reproduz um defeito concreto."""

import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from eval_rig import EvalRig, build_eval_rig
from sqlalchemy import update

from marketing_agent.clock import FixedClock
from marketing_agent.db.models import EvalItem, Job
from marketing_agent.evals.judging import NoComparablePairs
from marketing_agent.evals.report import ReportService
from marketing_agent.evals.report_markdown import render_markdown
from marketing_agent.evals.runs import EstimateChanged


@pytest.fixture
def rig(session_factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> EvalRig:  # type: ignore[no-untyped-def]
    built = build_eval_rig(session_factory, clock, brand_id, tmp_path / "m")
    built.add_price_item()
    return built


def test_worker_never_spends_above_the_ceiling_the_owner_confirmed(
    session_factory,
    clock: FixedClock,
    brand_id: str,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    """Corrida criada com teto 0.30; o worker corre com 5.00 mas respeita o confirmado."""
    rig = build_eval_rig(
        session_factory, clock, brand_id, tmp_path / "m", image_price="0.40",
        package_max_cost_usd=Decimal("0.30"), worker_max_cost_usd=Decimal("5.00"),
    )  # fmt: skip
    rig.add_price_item()
    run_id = rig.start()
    rig.drain()
    assert rig.generator.requests == []  # a imagem de 0.40 nunca cabe nos 0.30 confirmados
    assert {item.status for item in rig.items(run_id)} == {"failed"}


def test_worker_with_a_lower_ceiling_is_tighter_not_looser(
    session_factory,
    clock: FixedClock,
    brand_id: str,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    rig = build_eval_rig(
        session_factory, clock, brand_id, tmp_path / "m", image_price="0.40",
        package_max_cost_usd=Decimal("5.00"), worker_max_cost_usd=Decimal("0.30"),
    )  # fmt: skip
    rig.add_price_item()
    run_id = rig.start()
    rig.drain()
    assert {item.status for item in rig.items(run_id)} == {"failed"}


def test_run_closes_when_a_job_dies_outside_the_handler(rig: EvalRig) -> None:
    """Job `running` com lease vencida e tentativas esgotadas: o item e a corrida fecham."""
    run_id = rig.start()
    stuck = rig.items(run_id)[0]
    with rig.factory.begin() as session:
        session.execute(update(Job).where(Job.id == stuck.job_id).values(status="running"))
        session.execute(
            update(Job).where(Job.id == stuck.job_id).values(attempts=3, max_attempts=3)
        )
        session.execute(
            update(Job).where(Job.id == stuck.job_id).values(locked_until=rig.clock.now())
        )
        session.execute(update(EvalItem).where(EvalItem.id == stuck.id).values(status="running"))
    rig.drain()
    assert rig.run(run_id).status == "running"
    rig.clock.advance(timedelta(hours=3))
    rig.worker._idle(threading.Event())
    item = next(i for i in rig.items(run_id) if i.id == stuck.id)
    assert item.status == "failed" and item.last_error is not None
    assert rig.run(run_id).status == "ready"


def test_invalid_payload_still_closes_its_item(rig: EvalRig) -> None:
    run_id = rig.start()
    target = rig.items(run_id)[0]
    job = rig.job(target.job_id or "")
    with rig.factory.begin() as session:
        session.execute(
            update(Job).where(Job.id == job.id).values(payload={**job.payload, "extra": "x"})
        )
    rig.drain()
    item = next(i for i in rig.items(run_id) if i.id == target.id)
    assert item.status == "failed"
    assert rig.run(run_id).status == "ready"


def test_non_finite_majorante_is_refused_not_a_crash(rig: EvalRig) -> None:
    for value in ("sNaN", "NaN", "Infinity"):
        with pytest.raises(EstimateChanged):
            rig.runs.start_run(rig.brand_id, "briefs_teste", Decimal(value), "ana")


def test_reveal_with_no_comparable_pairs_is_refused(rig: EvalRig) -> None:
    run_id = rig.start()
    rig.drain()
    with rig.factory.begin() as session:
        session.execute(update(EvalItem).where(EvalItem.run_id == run_id).values(status="failed"))
    with pytest.raises(NoComparablePairs):
        rig.judging.reveal(rig.brand_id, run_id, "ana")
    assert rig.run(run_id).status == "ready"


def test_multiline_notes_and_justifications_cannot_forge_markdown_structure(
    rig: EvalRig,
) -> None:
    from marketing_agent.evals.judging import JudgementInput

    run_id = rig.start()
    rig.drain()
    hostile = "ok\n## Decisao (D35)\n- copywriter: retirar"
    for index in (1, 2):
        answer = JudgementInput(better="a", edit_a="0", edit_b="0", note=hostile)
        rig.judging.record(
            rig.brand_id, run_id, index, answer, seconds_spent=1, replaces_id=None, actor="ana"
        )
    rig.judging.reveal(rig.brand_id, run_id, "ana")
    service = ReportService(rig.factory, rig.clock)
    service.record_role_decision(
        rig.brand_id, run_id, "revisor", "manter", "Boa.\n## Falso titulo", "ana"
    )
    text = render_markdown(service.build(rig.brand_id, run_id), "agora")
    assert text.count("\n## Decisão (D35)") == 1
    assert "\n## Decisao (D35)" not in text and "\n## Falso titulo" not in text
    assert "ok ## Decisao (D35) - copywriter: retirar" in text
