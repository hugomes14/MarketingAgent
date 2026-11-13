"""Relatorio apos revelacao; custos de todas as tentativas e decisoes humanas sem automatismo."""

from decimal import Decimal

import pytest
from content_rig import package_pass
from eval_accounting import add_exposure
from eval_rig import brief

from marketing_agent.evals.blind import JudgementInput, side_a_path
from marketing_agent.evals.judging import JudgingService
from marketing_agent.evals.report import (
    LIMITACOES,
    PERGUNTA_D35,
    ReportService,
    RevealPending,
    render_markdown,
)
from marketing_agent.llm.fake_scenarios import simple_package


def prepare(rig, winners=("especializado", "empate"), *, record_timings=False):
    run_id = rig.start()
    for item in rig.items(run_id):
        if record_timings:
            from datetime import timedelta

            duration = timedelta(seconds=1 if item.path == "simples" else 3)
            rig.content.llm.hooks[len(rig.content.llm.requests) + 1] = lambda elapsed=duration: (
                rig.content.clock.advance(elapsed)
            )
        script = (
            simple_package()
            if item.path == "simples"
            else [*package_pass("rever", ["voz_incoerente"], checked=True), *package_pass()]
        )
        rig.process(item, script)
    judging = JudgingService(rig.factory, rig.content.clock)
    run = rig.run(run_id)
    for index, winner in enumerate(winners, 1):
        path_a = side_a_path(run.seed, f"b{index}")
        better = "empate" if winner == "empate" else ("a" if path_a == winner else "b")
        errors = ["voz_errada", "facto_errado"]
        answer = JudgementInput(
            better=better,
            errors_a=errors if path_a == "especializado" else [],
            errors_b=errors if path_a != "especializado" else [],
            edit_a="2_5",
            edit_b="1",
        )
        judging.record(rig.brand_id, run_id, index, answer, 10, None, "ana")
    return run_id, judging


def test_report_counts_wins_losses_ties_by_side_a_path(make_eval_rig):
    rig = make_eval_rig(briefs=[brief(f"b{i}") for i in (1, 2, 3)])
    run_id, judging = prepare(rig, ("especializado", "simples", "empate"))
    # Corrigir a primeira vitoria para empate; so conta a folha da cadeia.
    first = judging.pair(rig.brand_id, run_id, 1).current
    judging.record(
        rig.brand_id,
        run_id,
        1,
        first.answer.model_copy(update={"better": "empate"}),
        2,
        first.id,
        "ana",
    )
    judging.reveal(rig.brand_id, run_id, "ana")
    report = ReportService(rig.factory, rig.content.clock).build(rig.brand_id, run_id)
    assert (report.wins, report.losses, report.ties, report.pairs) == (0, 1, 2, 3)
    assert report.interval is not None


def test_report_errors_minutes_cost_latency_by_path(make_eval_rig):
    rig = make_eval_rig(image_price="0.04")
    run_id, judging = prepare(rig, record_timings=True)
    judging.reveal(rig.brand_id, run_id, "ana")
    report = ReportService(rig.factory, rig.content.clock).build(rig.brand_id, run_id)
    paths = {p.path: p for p in report.paths}
    specialized, simple = paths["especializado"], paths["simples"]
    assert specialized.errors["voz_errada"] == 2 and simple.errors["voz_errada"] == 0
    assert sum(specialized.edit_minutes.values()) == 2
    assert specialized.cost_mean_usd == specialized.cost_max_usd == Decimal("0.04")
    assert specialized.image_mean_usd == Decimal("0.04") and specialized.text_mean_usd == 0
    assert (simple.latency_mean_ms, specialized.latency_max_ms) == (1000, 3000)
    assert specialized.review_rounds == 2 and simple.review_rounds == 0
    assert report.actual_usd == Decimal("0.16") and report.estimate_usd == Decimal("6")
    lines = render_markdown(report).splitlines()
    table_rows = [index for index, line in enumerate(lines) if line.startswith("|")]
    assert table_rows == list(range(table_rows[0], table_rows[0] + 4))


def test_reviewer_agreement_uses_all_rounds(make_eval_rig):
    rig = make_eval_rig()
    run_id, judging = prepare(rig)
    judging.reveal(rig.brand_id, run_id, "ana")
    report = ReportService(rig.factory, rig.content.clock).build(rig.brand_id, run_id)
    assert (report.reviewer_agreement.matched, report.reviewer_agreement.total) == (2, 4)
    assert report.reviewer_agreement.ratio == Decimal("0.5")


@pytest.mark.parametrize("winner", ["empate", "especializado"])
def test_limitations_always_present_and_report_ends_with_question(make_eval_rig, winner):
    rig = make_eval_rig(briefs=[brief(f"b{i}") for i in range(1, 9)])
    run_id, judging = prepare(rig, (winner,) * 8)
    judging.reveal(rig.brand_id, run_id, "ana")
    report = ReportService(rig.factory, rig.content.clock).build(rig.brand_id, run_id)
    text = render_markdown(report)
    assert all(limitation in text for limitation in LIMITACOES)
    assert text.rstrip().endswith(PERGUNTA_D35)
    assert report.decisions == ()
    assert (report.interval is None) == (winner == "empate")


def test_role_decision_before_reveal_refused_and_after_append_only(make_eval_rig):
    rig = make_eval_rig()
    run_id, judging = prepare(rig)
    reports = ReportService(rig.factory, rig.content.clock)
    with pytest.raises(RevealPending):
        reports.build(rig.brand_id, run_id)
    with pytest.raises(RevealPending):
        reports.record_role_decision(rig.brand_id, run_id, "revisor", "manter", "Ajuda", "ana")
    judging.reveal(rig.brand_id, run_id, "ana")
    for decision in ("manter", "simplificar"):
        reports.record_role_decision(
            rig.brand_id, run_id, "revisor", decision, "Decisao humana", "ana"
        )
    assert len(reports.build(rig.brand_id, run_id).decisions) == 2
    with pytest.raises(ValueError):
        reports.record_role_decision(rig.brand_id, run_id, "diretor", "manter", "Errado", "ana")


def test_report_separates_pending_unknown_exposure_and_late_settlement(make_eval_rig):
    from marketing_agent.budget.pricing import ImageUsage

    rig = make_eval_rig(image_price="0.04")
    run_id, judging = prepare(rig)
    judging.reveal(rig.brand_id, run_id, "ana")
    item = next(i for i in rig.items(run_id) if i.path == "especializado")
    unknown = add_exposure(rig, item, "0.04", "unknown_outcome")
    add_exposure(rig, item, "0.02", "open")
    reports = ReportService(rig.factory, rig.content.clock)
    report = reports.build(rig.brand_id, run_id)
    assert (report.actual_usd, report.pending_usd, report.unknown_usd) == (
        Decimal("0.16"),
        Decimal("0.02"),
        Decimal("0.04"),
    )
    assert report.accounting_incomplete
    text = render_markdown(report)
    assert "Contabilidade incompleta" in text and "desfecho desconhecido" in text
    # Acerto tardio depois do fecho do item: total/imagem/texto avancam juntos.
    rig.budget.settle_image(unknown, ImageUsage(1, "1024x1536", "medium"), None)
    report = reports.build(rig.brand_id, run_id)
    assert report.actual_usd == Decimal("0.20") and report.unknown_usd == 0
    path = next(p for p in report.paths if p.path == "especializado")
    assert path.cost_mean_usd == path.image_mean_usd == Decimal("0.06")
    assert path.text_mean_usd == path.text_max_usd == 0


def test_report_flags_abandoned_timing_instead_of_zero(make_eval_rig):
    from datetime import timedelta

    from marketing_agent.db.models import AgentRun

    rig = make_eval_rig(briefs=[brief("b1")])
    run_id = rig.start()
    items = rig.items(run_id)
    for item in items:
        rig.handler.fail_job(rig.claim(item), "worker morto", retry=False)
    with rig.factory.begin() as session:
        session.add(
            AgentRun(
                brand_id=rig.brand_id,
                role="simples",
                spec_version=1,
                trigger=f"eval_item:{next(i.id for i in items if i.path == 'simples')}",
                status="running",
                started_at=rig.content.clock.now() - timedelta(minutes=4),
            )
        )
    JudgingService(rig.factory, rig.content.clock).reveal(rig.brand_id, run_id, "ana")
    report = ReportService(rig.factory, rig.content.clock).build(rig.brand_id, run_id)
    path = next(p for p in report.paths if p.path == "simples")
    assert path.latency_mean_ms is None and path.latency_max_ms is None
    assert path.timing_incomplete_packages == 1 and path.latency_measured_packages == 0
    assert "Tempos incompletos" in render_markdown(report)
