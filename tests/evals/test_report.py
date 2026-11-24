"""Relatorio da corrida revelada: contagens pelo lado, medidas por percurso, limitacoes, decisao."""

from datetime import timedelta
from pathlib import Path

import pytest
from eval_rig import EvalRig, build_eval_rig
from sqlalchemy import select

from marketing_agent.cli_eval import write_report
from marketing_agent.clock import FixedClock
from marketing_agent.db.models import AuditLog, RoleDecision
from marketing_agent.evals.blind import side_a_path
from marketing_agent.evals.judging import JudgementInput
from marketing_agent.evals.report import (
    LIMITACOES,
    PERGUNTA_D35,
    InvalidDecision,
    ReportService,
    RevealPending,
)
from marketing_agent.evals.report_markdown import render_markdown
from marketing_agent.llm import fake_scenarios
from marketing_agent.startup import StartupError


@pytest.fixture
def rig(session_factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> EvalRig:  # type: ignore[no-untyped-def]
    built = build_eval_rig(
        session_factory, clock, brand_id, tmp_path / "m", text_price_per_million="1"
    )
    built.add_price_item()
    built.llm.before = lambda role: clock.advance(timedelta(seconds=1))
    built.llm.scripts["revisor"] += fake_scenarios.review(
        "rever", ["voz_incoerente"], "Mais simples.", imagem_verificada=True
    )
    return built


def specialized_side(rig: EvalRig, run_id: str, key: str) -> str:
    return "a" if side_a_path(rig.run(run_id).seed, key) == "especializado" else "b"


def judge_all(rig: EvalRig, run_id: str, *, special_wins: bool, voice_error: bool) -> None:
    """Par 1: o especializado ganha (ou perde); par 2: empate. Marca `voz_errada` onde o
    Revisor assinalou `voz_incoerente`, se pedido."""
    keys = ["pao_do_dia", "preco_pao"]
    flagged = {
        item.brief_key
        for item in rig.items(run_id)
        if item.path == "especializado"
        and "voz_incoerente" in (item.revision_snapshot or {})["review"]["all_violations"]  # type: ignore[index, operator]
    }
    for index, key in enumerate(keys, start=1):
        side = specialized_side(rig, run_id, key)
        other = "b" if side == "a" else "a"
        better = (side if special_wins else other) if index == 1 else "empate"
        errors = {f"errors_{side}": ["voz_errada"]} if voice_error and key in flagged else {}
        answer = JudgementInput(better=better, edit_a="0", edit_b="mais_5", **errors)  # type: ignore[arg-type]
        rig.judging.record(
            rig.brand_id, run_id, index, answer, seconds_spent=20 * index, replaces_id=None,
            actor="ana",
        )  # fmt: skip


def revealed(rig: EvalRig, *, special_wins: bool = True, voice_error: bool = True) -> str:
    run_id = rig.start()
    rig.drain()
    judge_all(rig, run_id, special_wins=special_wins, voice_error=voice_error)
    rig.judging.reveal(rig.brand_id, run_id, "ana")
    return run_id


def reports(rig: EvalRig) -> ReportService:
    return ReportService(rig.factory, rig.clock)


def test_report_requires_reveal(rig: EvalRig) -> None:
    run_id = rig.start()
    rig.drain()
    with pytest.raises(RevealPending):
        reports(rig).build(rig.brand_id, run_id)
    with pytest.raises(RevealPending):
        reports(rig).record_role_decision(rig.brand_id, run_id, "revisor", "manter", "x", "ana")


def test_report_counts_wins_losses_ties_by_side_a_path(rig: EvalRig) -> None:
    run_id = revealed(rig)
    report = reports(rig).build(rig.brand_id, run_id)
    assert (report.pairs, report.wins, report.losses, report.ties) == (2, 1, 0, 1)
    assert report.interval is not None and report.interval[1] == 1.0
    assert report.seconds_per_pair_mean == 30
    lost = revealed_loss(rig)
    assert (lost.wins, lost.losses) == (0, 1)


def revealed_loss(rig: EvalRig):  # type: ignore[no-untyped-def]
    """Uma segunda corrida, onde o especializado perde o par 1."""
    run_id = revealed(rig, special_wins=False, voice_error=False)
    return reports(rig).build(rig.brand_id, run_id)


def test_report_errors_minutes_cost_latency_by_path(rig: EvalRig) -> None:
    run_id = revealed(rig)
    report = reports(rig).build(rig.brand_id, run_id)
    paths = {p.path: p for p in report.paths}
    assert set(paths) == {"simples", "especializado"}
    assert paths["especializado"].errors["voz_errada"] == 1
    assert paths["simples"].errors["voz_errada"] == 0
    minutes = paths["simples"].edit_minutes["0"] + paths["simples"].edit_minutes["mais_5"]
    assert minutes == 2
    assert paths["especializado"].review_rounds == 1
    assert all(p.cost_mean_usd > 0 and p.latency_mean_ms >= 1000 for p in report.paths)
    assert paths["especializado"].latency_max_ms > paths["simples"].latency_max_ms


def test_reviewer_agreement_uses_all_rounds(rig: EvalRig) -> None:
    run_id = revealed(rig)
    assert reports(rig).build(rig.brand_id, run_id).reviewer_agreement == (1, 1)


def test_report_shows_estimate_vs_actual(rig: EvalRig) -> None:
    run_id = revealed(rig)
    report = reports(rig).build(rig.brand_id, run_id)
    assert report.estimate_usd == rig.run(run_id).estimate_usd
    assert 0 < report.actual_usd < report.estimate_usd
    text = render_markdown(report, "agora")
    assert "estimativa máxima 6.0000 USD" in text and f"real {report.actual_usd:.4f} USD" in text


@pytest.mark.parametrize("special_wins", [True, False])
def test_limitations_always_present(rig: EvalRig, special_wins: bool) -> None:
    run_id = revealed(rig, special_wins=special_wins)
    report = reports(rig).build(rig.brand_id, run_id)
    text = render_markdown(report, "agora")
    for item in LIMITACOES:
        assert item in text
    assert report.limitations == LIMITACOES


def test_report_ends_with_d35_question_and_no_automatic_decision(rig: EvalRig) -> None:
    run_id = revealed(rig)
    report = reports(rig).build(rig.brand_id, run_id)
    assert report.decisions == ()
    text = render_markdown(report, "agora")
    assert PERGUNTA_D35 in text and "Ainda sem decisões registadas." in text
    for word in ("recomend", "deve retirar", "deve manter"):
        assert word not in text.lower()


def test_role_decision_append_only_and_latest_wins(rig: EvalRig) -> None:
    run_id = revealed(rig)
    service = reports(rig)
    service.record_role_decision(rig.brand_id, run_id, "revisor", "manter", "Viu a voz.", "ana")
    rig.clock.advance(timedelta(minutes=1))
    service.record_role_decision(rig.brand_id, run_id, "revisor", "simplificar", "Caro.", "ana")
    [decision] = service.build(rig.brand_id, run_id).decisions
    assert (decision.role, decision.decision) == ("revisor", "simplificar")
    with rig.factory() as session:
        assert len(session.scalars(select(RoleDecision)).all()) == 2
        actions = session.scalars(select(AuditLog.action)).all()
    assert actions.count("role_decision") == 2
    for bad in (
        ("analista", "manter", "x"),
        ("revisor", "talvez", "x"),
        ("revisor", "manter", " "),
    ):
        with pytest.raises(InvalidDecision):
            service.record_role_decision(rig.brand_id, run_id, *bad, "ana")


def test_cli_eval_report_writes_markdown_to_docs(rig: EvalRig, tmp_path: Path) -> None:
    run_id = revealed(rig)
    out = tmp_path / "docs"
    out.mkdir()
    path = write_report(reports(rig), rig.factory, run_id, out, rig.clock)
    assert path.parent == out and path.name.endswith("_relatorio_avaliacao_briefs_teste.md")
    assert "# Relatório da avaliação cega: briefs_teste v3" in path.read_text(encoding="utf-8")
    pending = rig.start()
    with pytest.raises(StartupError, match="revelada"):
        write_report(reports(rig), rig.factory, pending, out, rig.clock)
