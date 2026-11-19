"""Corridas de avaliacao: estimativa, inicio atomico e recusas (spec 2C, secao 4)."""

from decimal import Decimal
from pathlib import Path

import pytest
from eval_rig import ACTOR, FIXTURES, EvalRig, build_eval_rig
from sqlalchemy import func, select

from marketing_agent.clock import FixedClock
from marketing_agent.db.models import AuditLog, EvalItem, Job
from marketing_agent.evals.runs import EstimateChanged, EvalSetMismatch, RunInProgress
from marketing_agent.evals.sets import EvalSetError
from marketing_agent.jobs.queue import EVAL_PACKAGE_JOB


@pytest.fixture
def rig(session_factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> EvalRig:  # type: ignore[no-untyped-def]
    built = build_eval_rig(session_factory, clock, brand_id, tmp_path / "m")
    built.add_price_item()
    return built


def test_estimate_is_packages_times_ceiling(rig: EvalRig) -> None:
    estimate = rig.runs.estimate(rig.brand_id, "briefs_teste")
    assert (estimate.set_version, estimate.briefs, estimate.packages) == (3, 2, 4)
    assert estimate.upper_bound_usd == Decimal("6.00")
    assert estimate.month_reference_usd == Decimal(20)


def test_start_run_creates_items_and_jobs_atomically(rig: EvalRig) -> None:
    run_id = rig.start()
    run = rig.run(run_id)
    assert (run.status, run.set_name, run.set_version) == ("running", "briefs_teste", 3)
    assert run.estimate_usd == Decimal("6.00") and 0 <= run.seed < 2**63
    priced = next(b for b in run.briefs if b["key"] == "preco_pao")
    assert len(priced["kb_ids"]) == 1  # `preco:pao` resolvido para o id do item
    items = rig.items(run_id)
    assert [(i.brief_key, i.path, i.status) for i in items] == [
        ("pao_do_dia", "especializado", "queued"),
        ("pao_do_dia", "simples", "queued"),
        ("preco_pao", "especializado", "queued"),
        ("preco_pao", "simples", "queued"),
    ]
    for item in items:
        job = rig.job(item.job_id or "")
        assert job.type == EVAL_PACKAGE_JOB and job.status == "queued"
        assert job.payload == {"eval_run_id": run_id, "eval_item_id": item.id}
    with rig.factory() as session:
        audit = session.scalar(select(AuditLog).where(AuditLog.subject_id == run_id))
    assert audit is not None and audit.actor == ACTOR and audit.action == "eval_run_started"


def test_start_refuses_changed_estimate(rig: EvalRig) -> None:
    with pytest.raises(EstimateChanged):
        rig.runs.start_run(rig.brand_id, "briefs_teste", Decimal("5.99"), ACTOR)
    with rig.factory() as session:
        assert session.scalar(select(func.count()).select_from(EvalItem)) == 0


def test_second_running_run_refused(rig: EvalRig) -> None:
    rig.start()
    with pytest.raises(RunInProgress):
        rig.start()
    with rig.factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 4


def _custom_set(tmp_path: Path, body: str) -> Path:
    folder = tmp_path / "sets"
    folder.mkdir()
    (folder / "custom.yaml").write_text(body, encoding="utf-8")
    return folder


SET_BODY = """
name: custom
version: 1
briefs:
  - key: a
    tema: Tema
    pilar: {pilar}
    objetivo: Objetivo
    canais: [instagram]
    kb_refs: [{ref}]
"""


@pytest.mark.parametrize(
    ("pilar", "ref", "message"),
    [
        ("Pilar inventado", '"preco:pao"', "pilar fora da estrategia"),
        ("Proximidade", '"preco:inexistente"', "preco:inexistente"),
    ],
)
def test_start_run_rejects_unknown_pilar_or_kb_ref(
    session_factory, clock: FixedClock, brand_id: str, tmp_path: Path,  # type: ignore[no-untyped-def]
    pilar: str, ref: str, message: str,
) -> None:  # fmt: skip
    folder = _custom_set(tmp_path, SET_BODY.format(pilar=pilar, ref=ref))
    rig = build_eval_rig(session_factory, clock, brand_id, tmp_path / "m", evals_dir=folder)
    rig.add_price_item()
    with pytest.raises(EvalSetMismatch, match=message):
        rig.start("custom")
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_unknown_set_is_a_set_error(rig: EvalRig) -> None:
    with pytest.raises(EvalSetError):
        rig.runs.estimate(rig.brand_id, "nao_existe")
    assert FIXTURES.is_dir()
