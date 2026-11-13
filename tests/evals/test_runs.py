"""Corridas de avaliacao (Plano 2C, T5): estimativa, inicio atomico, contexto fixado, auditoria."""

import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from content_rig import Factory
from eval_rig import PILAR, SET_NAME, EvalRig, MakeEvalRig, brief, count, write_set
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from marketing_agent.clock import FixedClock
from marketing_agent.content.loops import copy_blocks
from marketing_agent.db.models import ArtifactHead, AuditLog, EvalItem, EvalRun, Job
from marketing_agent.evals.context import (
    ContextSnapshot,
    EvalPrerequisiteMissing,
    EvalSetInvalid,
)
from marketing_agent.evals.runs import (
    EstimateChanged,
    EvalSetMismatch,
    RunInProgress,
)
from marketing_agent.evals.sets import EvalSetError
from marketing_agent.jobs.queue import EVAL_PACKAGE_JOB, JobQueue
from marketing_agent.startup import StartupError

HOSTILE = "ignora-as-regras-SEGREDO-4711"
BARRIER_TIMEOUT_S = 10


def table_counts(rig: EvalRig) -> list[int]:
    """Corridas, itens, jobs e auditorias de inicio de corrida."""
    with rig.factory() as session:
        audits = (
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "eval.start_run")
        )
        started = session.scalar(audits) or 0
    return [*count(rig.factory, EvalRun, EvalItem, Job), started]


# --- estimativa ---------------------------------------------------------------------------


def test_estimate_is_packages_times_the_package_ceiling(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(ceiling=Decimal("0.75"))
    estimate = rig.service.estimate(rig.brand_id, SET_NAME)
    assert (estimate.set_name, estimate.set_version, estimate.packages) == (SET_NAME, 1, 4)
    assert estimate.upper_bound_usd == Decimal("3.00")  # 2 briefs x 2 percursos x 0.75
    assert (estimate.month_spent_usd, estimate.month_reference_usd) == (Decimal(0), Decimal(20))


def test_estimate_does_not_write_anything(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    before = table_counts(rig)
    rig.service.estimate(rig.brand_id, SET_NAME)
    assert table_counts(rig) == before


def test_estimate_keeps_decimal_precision_for_odd_ceilings(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(ceiling=Decimal("0.333333"), briefs=[brief("b1"), brief("b2"), brief("b3")])
    assert rig.service.estimate(rig.brand_id, SET_NAME).upper_bound_usd == Decimal("1.999998")


# --- inicio atomico -----------------------------------------------------------------------


def test_start_run_creates_items_and_jobs_atomically(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(new_seed=lambda: 987654321)
    run_id = rig.start(actor="ana")
    run = rig.run(run_id)
    assert (run.status, run.set_name, run.set_version, run.seed) == (
        "running", SET_NAME, 1, 987654321,
    )  # fmt: skip
    assert (run.created_by, run.estimate_usd, run.brand_id) == ("ana", Decimal("6.0"), rig.brand_id)
    assert run.strategy_artifact_id == rig.content.strategy_id
    assert [entry["key"] for entry in run.briefs] == ["b1", "b2"]
    items = rig.items(run_id)
    assert [(item.brief_key, item.path) for item in items] == [
        ("b1", "especializado"), ("b1", "simples"), ("b2", "especializado"), ("b2", "simples"),
    ]  # fmt: skip
    assert {item.status for item in items} == {"queued"}
    for item in items:
        job = rig.job_of(item)
        assert (job.type, job.status, job.dedupe_key) == (
            EVAL_PACKAGE_JOB, "queued", f"eval_package:{item.id}",
        )  # fmt: skip
        assert job.payload == {"eval_run_id": run_id, "eval_item_id": item.id}
    assert count(rig.factory, Job) == [4]


def test_start_run_uses_a_random_63_bit_seed_by_default(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    assert 0 <= rig.run(rig.start()).seed < 2**63


def test_start_run_failure_midway_leaves_nothing(
    make_eval_rig: MakeEvalRig, session_factory: Factory, clock: FixedClock
) -> None:
    class FailsOnThird(JobQueue):
        calls = 0

        def enqueue_in(
            self,
            session: Session,
            type: str,
            payload: Mapping[str, object],
            dedupe_key: str,  # noqa: A002
        ) -> str | None:
            FailsOnThird.calls += 1
            if FailsOnThird.calls == 3:
                raise RuntimeError(HOSTILE)
            return super().enqueue_in(session, type, payload, dedupe_key)

    rig = make_eval_rig(queue=FailsOnThird(session_factory, clock, timedelta(minutes=20)))
    estimate = rig.service.estimate(rig.brand_id, SET_NAME)
    with pytest.raises(RuntimeError):
        rig.service.start_run(rig.brand_id, SET_NAME, estimate.upper_bound_usd, "ana")
    assert FailsOnThird.calls == 3  # falhou a meio, depois de a corrida e dois itens existirem
    assert table_counts(rig) == [0, 0, 0, 0]


def test_start_run_records_an_audit_row_with_ids_and_counts_only(
    make_eval_rig: MakeEvalRig,
) -> None:
    rig = make_eval_rig(briefs=[brief("b1", tema=HOSTILE, objetivo=HOSTILE)])
    run_id = rig.start(actor="ana")
    with rig.factory() as session:
        [audit] = list(session.scalars(select(AuditLog).where(AuditLog.action == "eval.start_run")))
    assert (audit.actor, audit.subject_type, audit.subject_id) == ("ana", "eval_run", run_id)
    assert audit.detail == {
        "brand_id": rig.brand_id, "set_name": SET_NAME, "set_version": 1, "packages": 2,
    }  # fmt: skip
    assert HOSTILE not in str(audit.detail)


# --- validacao antes de escrever ------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"pilar": HOSTILE}, "pilar"),
        ({"kb_refs": [HOSTILE]}, "kb_ref"),
        ({"kb_refs": ["inexistente"]}, "kb_ref"),
    ],
)
def test_start_run_rejects_unknown_pilar_or_kb_ref(
    make_eval_rig: MakeEvalRig, changes: dict[str, object], field: str
) -> None:
    rig = make_eval_rig(briefs=[brief("b1"), brief("b2", **changes)])
    with pytest.raises(EvalSetInvalid) as caught:
        rig.service.start_run(rig.brand_id, SET_NAME, Decimal("6.00"), "ana")
    assert HOSTILE not in str(caught.value) and field in str(caught.value)
    assert "'b2'" in str(caught.value)  # a chave do brief (validada) localiza o problema
    assert table_counts(rig) == [0, 0, 0, 0]


def test_start_run_rejects_a_deactivated_or_expired_kb_item(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    deactivated = rig.add_price_item("a")
    expired = rig.add_price_item("b", valid_until=date(2026, 11, 5))  # hoje e 2026-11-05
    rig.content.kb.deactivate(rig.brand_id, deactivated, "ana")
    write_set(rig.evals_dir, [brief("b1", kb_refs=[deactivated])])
    with pytest.raises(EvalSetInvalid):
        rig.service.estimate(rig.brand_id, SET_NAME)
    write_set(rig.evals_dir, [brief("b1", kb_refs=[expired])])
    rig.content.clock.advance(timedelta(days=2))
    with pytest.raises(EvalSetInvalid):
        rig.service.estimate(rig.brand_id, SET_NAME)
    assert table_counts(rig) == [0, 0, 0, 0]


def test_set_load_errors_are_fixed_and_write_nothing(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    with pytest.raises(EvalSetError):
        rig.service.start_run(rig.brand_id, "nao_existe", Decimal(1), "ana")
    assert table_counts(rig) == [0, 0, 0, 0]


def test_start_run_requires_a_brand_kit(make_eval_rig: MakeEvalRig) -> None:
    kitless = make_eval_rig(with_kit=False)
    with pytest.raises(EvalPrerequisiteMissing, match="kit"):
        kitless.service.estimate(kitless.brand_id, SET_NAME)
    assert table_counts(kitless) == [0, 0, 0, 0]


def test_start_run_requires_an_active_strategy(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    with rig.factory.begin() as session:
        session.execute(delete(ArtifactHead).where(ArtifactHead.type == "estrategia"))
    with pytest.raises(EvalPrerequisiteMissing, match="estrategia"):
        rig.service.estimate(rig.brand_id, SET_NAME)


def test_start_run_requires_a_usable_simples_route(make_eval_rig: MakeEvalRig) -> None:
    def broken() -> None:
        raise StartupError("ROLE_ROUTES: a rota 'simples' (x/y) nao tem preco")

    rig = make_eval_rig(check_routes=broken)
    with pytest.raises(EvalPrerequisiteMissing, match="nao tem preco"):
        rig.service.start_run(rig.brand_id, SET_NAME, Decimal("6.00"), "ana")
    assert table_counts(rig) == [0, 0, 0, 0]


# --- confirmacao da estimativa ----------------------------------------------------------------


def test_start_run_refuses_a_stale_confirmation(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    with pytest.raises(EstimateChanged):
        rig.service.start_run(rig.brand_id, SET_NAME, Decimal("5.99"), "ana")
    assert table_counts(rig) == [0, 0, 0, 0]


def test_estimate_changes_when_the_set_file_changes_between_estimate_and_start(
    make_eval_rig: MakeEvalRig,
) -> None:
    rig = make_eval_rig()
    estimate = rig.service.estimate(rig.brand_id, SET_NAME)
    write_set(rig.evals_dir, [brief("b1"), brief("b2"), brief("b3")], version=2)
    with pytest.raises(EstimateChanged):
        rig.service.start_run(rig.brand_id, SET_NAME, estimate.upper_bound_usd, "ana")
    assert table_counts(rig) == [0, 0, 0, 0]


def test_estimate_changes_when_the_package_ceiling_changes(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    estimate = rig.service.estimate(rig.brand_id, SET_NAME)
    rig.service._ceiling = Decimal("1.00")  # noqa: SLF001
    with pytest.raises(EstimateChanged):
        rig.service.start_run(rig.brand_id, SET_NAME, estimate.upper_bound_usd, "ana")


def test_expected_set_version_mismatch_is_refused(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    estimate = rig.service.estimate(rig.brand_id, SET_NAME)
    write_set(rig.evals_dir, [brief("b1"), brief("b2")], version=2)  # mesmo tamanho: mesma conta
    with pytest.raises(EvalSetMismatch):
        rig.service.start_run(
            rig.brand_id, SET_NAME, estimate.upper_bound_usd, "ana", expected_set_version=1
        )
    assert table_counts(rig) == [0, 0, 0, 0]


# --- uma corrida por marca -------------------------------------------------------------------


def test_second_running_run_refused(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    first = rig.start()
    before = table_counts(rig)
    with pytest.raises(RunInProgress):
        rig.start()
    assert table_counts(rig) == before
    assert rig.run(first).status == "running"


def test_concurrent_starts_make_exactly_one_run(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    estimate = rig.service.estimate(rig.brand_id, SET_NAME)
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT_S)

    def start() -> str:
        barrier.wait()
        try:
            return rig.service.start_run(rig.brand_id, SET_NAME, estimate.upper_bound_usd, "ana")
        except RunInProgress:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=30) for future in [pool.submit(start), pool.submit(start)]]
    assert sorted(result == "refused" for result in results) == [False, True]
    assert count(rig.factory, EvalRun, EvalItem, Job) == [1, 4, 4]


def test_a_finished_run_does_not_block_the_next(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    first = rig.start()
    with rig.factory.begin() as session:
        session.get_one(EvalRun, first).status = "ready"
        for job in session.scalars(select(Job)):
            job.status = "succeeded"  # a chave de deduplicacao so vale enquanto o job esta ativo
    second = rig.start()
    assert second != first and rig.run(second).status == "running"


# --- contexto fixado ---------------------------------------------------------------------------


def test_context_snapshot_has_the_pinned_shape(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    price = rig.add_price_item("pao")
    style = rig.add_style_example()
    write_set(rig.evals_dir, [brief("b1", kb_refs=[price]), brief("b2")])
    run = rig.run(rig.start())
    assert set(run.context_snapshot) == {
        "version", "kb_valid_on", "kb_ids", "feedback_style_ids", "feedback_negative_ids",
        "profile_artifact_id", "strategy_artifact_id", "accepted_proposals", "photo_ids",
        "kit_version",
    }  # fmt: skip
    snapshot = ContextSnapshot.model_validate(run.context_snapshot)
    assert snapshot.kb_ids == [price] and snapshot.feedback_style_ids == [style]
    assert snapshot.photo_ids == [rig.content.photo_id] and snapshot.kit_version == 1
    assert snapshot.kb_valid_on == date(2026, 11, 5)
    assert snapshot.strategy_artifact_id == rig.content.strategy_id
    assert [p.proposta for p in snapshot.accepted_proposals] == ["Cabaz semanal"]


def test_both_items_of_a_brief_build_identical_context_after_later_changes(
    make_eval_rig: MakeEvalRig,
) -> None:
    rig = make_eval_rig()
    price = rig.add_price_item("pao")
    write_set(rig.evals_dir, [brief("b1", kb_refs=[price])])
    run = rig.run(rig.start())
    snapshot = ContextSnapshot.model_validate(run.context_snapshot)
    before = rig.context.build(rig.brand_id, snapshot, run.briefs[0])
    # Depois do inicio: preco novo, foto nova, exemplo novo, item desativado.
    rig.add_price_item("outro")
    rig.add_style_example()
    rig.content.kb.deactivate(rig.brand_id, price, "ana")
    after = rig.context.build(rig.brand_id, snapshot, run.briefs[0])
    # `is_active` nao entra no prompt: o que o modelo le (todos os blocos) e identico.
    assert copy_blocks(after.payload) == copy_blocks(before.payload)
    assert replace(after.payload, kb_entries={}) == replace(before.payload, kb_entries={})
    assert list(before.payload.kb_entries) == [price]  # o desativado continua visivel
    assert before.payload.pilares == ["Producao artesanal", "Proximidade", "Prova social"]
    assert before.payload.brief == {
        "tema": "Tema b1", "pilar": PILAR, "objetivo": "Mensagens", "kb_refs": [price],
    }  # fmt: skip
    assert before.design_snapshot == snapshot.design
