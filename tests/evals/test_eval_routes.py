"""Painel da avaliacao: estimativa, inicio, progresso agregado, pares cegos e revelacao."""

import re
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from eval_rig import EvalRig, build_eval_rig
from panel_rig import PanelRig, Script
from sqlalchemy import select

from marketing_agent.clock import FixedClock
from marketing_agent.db.models import AgentRun, EvalItem, EvalJudgement, MediaAsset
from marketing_agent.evals.blind import side_a_path

MakePanel = Callable[[Script], PanelRig]
FORBIDDEN = ("simples", "especializado", "revisor", "copywriter", "designer", "custo")
ANSWER = {"melhor": "a", "edicao_a": "0", "edicao_b": "2_5", "erro_b_voz_errada": "1"}


@pytest.fixture
def evals(session_factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> EvalRig:  # type: ignore[no-untyped-def]
    built = build_eval_rig(session_factory, clock, brand_id, tmp_path / "media")
    built.add_price_item()
    return built


def ready_run(evals: EvalRig) -> str:
    run_id = evals.start()
    evals.drain()
    return run_id


def pair_form(rig: PanelRig, run_id: str, index: int) -> dict[str, str]:
    """Campos escondidos (`inicio`, `substitui`) da pagina do par."""
    html = rig.get(f"/avaliacao/{run_id}/par/{index}").text
    fields = dict(re.findall(r'name="(inicio|substitui)" value="([^"]*)"', html))
    assert "inicio" in fields
    return fields


def answer(rig: PanelRig, run_id: str, index: int, **extra: str) -> int:
    data = {**pair_form(rig, run_id, index), **ANSWER, **extra}
    return rig.post(f"/avaliacao/{run_id}/par/{index}", data).status_code


def test_estimate_shown_before_start(make_panel: MakePanel, evals: EvalRig) -> None:
    rig = make_panel([])
    page = rig.get("/avaliacao/nova?conjunto=briefs_teste")
    assert page.status_code == 200
    assert "6.00 USD" in page.text and 'name="majorante" value="6.00"' in page.text
    assert rig.get("/avaliacao/nova?conjunto=..%2Fx").status_code == 422


def test_start_from_the_panel_and_refuse_changed_estimate(
    make_panel: MakePanel, evals: EvalRig
) -> None:
    rig = make_panel([])
    stale = rig.post("/avaliacao", {"conjunto": "briefs_teste", "majorante": "5.00"})
    assert stale.status_code == 409 and "custo máximo mudou" in stale.text
    started = rig.post("/avaliacao", {"conjunto": "briefs_teste", "majorante": "6.00"})
    assert started.status_code == 303
    run_id = started.headers["location"].rsplit("/", 1)[-1]
    assert evals.run(run_id).set_name == "briefs_teste"
    again = rig.post("/avaliacao", {"conjunto": "briefs_teste", "majorante": "6.00"})
    assert again.status_code == 409


def test_progress_shows_only_aggregate_counts(make_panel: MakePanel, evals: EvalRig) -> None:
    run_id = evals.start()
    assert evals.worker.run_once()  # um pacote pronto, os outros por fazer
    rig = make_panel([])
    for path in (f"/avaliacao/{run_id}", f"/avaliacao/{run_id}/estado", "/avaliacao"):
        html = rig.get(path).text.lower()
        # O custo total da corrida pode aparecer; o percurso e o brief de cada pacote nao.
        for word in ("simples", "especializado", "revisor", "pao_do_dia", "preco_pao"):
            assert word not in html, (path, word)
        for item in evals.items(run_id):
            assert item.id not in html
    assert "1 de 4" in rig.get(f"/avaliacao/{run_id}").text


def test_polling_only_while_running(make_panel: MakePanel, evals: EvalRig) -> None:
    run_id = evals.start()
    rig = make_panel([])
    assert "every 5s" in rig.get(f"/avaliacao/{run_id}/estado").text
    evals.drain()
    assert "hx-trigger" not in rig.get(f"/avaliacao/{run_id}/estado").text


def test_pair_html_never_reveals_path(make_panel: MakePanel, evals: EvalRig) -> None:
    run_id = ready_run(evals)
    rig = make_panel([])
    with evals.factory() as session:
        secrets = [
            *session.scalars(select(MediaAsset.id)),
            *session.scalars(select(MediaAsset.storage_key)),
            *session.scalars(select(MediaAsset.sha256)),
            *session.scalars(select(AgentRun.id)),
        ]
    for index in (1, 2):
        html = rig.get(f"/avaliacao/{run_id}/par/{index}").text
        lowered = html.lower()
        for word in FORBIDDEN:
            assert word not in lowered, (index, word)
        for secret in secrets:
            assert secret not in html
        side_a = re.search(r'<article class="([^"]+)" id="lado-a">', html)
        side_b = re.search(r'<article class="([^"]+)" id="lado-b">', html)
        assert side_a and side_b
        assert side_a.group(1).replace("lado-a", "") == side_b.group(1).replace("lado-b", "")
        assert "data-" not in html.split('id="lado-a"')[1].split("</form>")[0]


def test_pair_image_response_has_no_identifying_headers(
    make_panel: MakePanel, evals: EvalRig
) -> None:
    run_id = ready_run(evals)
    rig = make_panel([])
    response = rig.get(f"/avaliacao/{run_id}/par/1/imagem/b")
    assert response.status_code == 200 and response.content.startswith(b"\xff\xd8")
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    for header in ("content-disposition", "etag", "last-modified"):
        assert header not in response.headers
    assert rig.get(f"/avaliacao/{run_id}/par/1/imagem/c").status_code == 422
    assert rig.get(f"/avaliacao/{run_id}/par/9/imagem/a").status_code == 404


def test_seconds_spent_measured_by_signed_token(make_panel: MakePanel, evals: EvalRig) -> None:
    run_id = ready_run(evals)
    rig = make_panel([])
    fields = pair_form(rig, run_id, 1)
    rig.clock.advance(timedelta(seconds=42))
    response = rig.post(f"/avaliacao/{run_id}/par/1", {**fields, **ANSWER})
    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/avaliacao/{run_id}/par/2")
    with evals.factory() as session:
        row = session.scalars(select(EvalJudgement)).one()
    assert row.seconds_spent == 42 and row.errors_b == ["voz_errada"]
    assert row.side_a_path == side_a_path(evals.run(run_id).seed, "pao_do_dia")
    assert row.decided_by == "owner@example.com"


def test_forged_timer_token_is_422(make_panel: MakePanel, evals: EvalRig) -> None:
    run_id = ready_run(evals)
    rig = make_panel([])
    fields = pair_form(rig, run_id, 1)
    stamp, signature = fields["inicio"].split(".")
    forged = {**fields, "inicio": f"{int(stamp) - 3000}.{signature}"}
    assert rig.post(f"/avaliacao/{run_id}/par/1", {**forged, **ANSWER}).status_code == 422
    other_pair = pair_form(rig, run_id, 2)
    moved = {**fields, "inicio": other_pair["inicio"]}
    assert rig.post(f"/avaliacao/{run_id}/par/1", {**moved, **ANSWER}).status_code == 422


def test_judgement_form_requires_choices(make_panel: MakePanel, evals: EvalRig) -> None:
    run_id = ready_run(evals)
    rig = make_panel([])
    fields = pair_form(rig, run_id, 1)
    missing = {**fields, "edicao_a": "0", "edicao_b": "1"}
    assert rig.post(f"/avaliacao/{run_id}/par/1", missing).status_code == 422
    bad = {**fields, **ANSWER, "melhor": "c"}
    assert rig.post(f"/avaliacao/{run_id}/par/1", bad).status_code == 422
    html = rig.get(f"/avaliacao/{run_id}/par/1").text
    assert " checked" not in html  # sem pre-selecao


def test_correction_from_the_panel_and_stale_tab(make_panel: MakePanel, evals: EvalRig) -> None:
    run_id = ready_run(evals)
    rig = make_panel([])
    old_tab = pair_form(rig, run_id, 1)
    assert answer(rig, run_id, 1) == 303
    corrected = {**pair_form(rig, run_id, 1), **ANSWER, "melhor": "empate"}
    assert corrected["substitui"]
    assert rig.post(f"/avaliacao/{run_id}/par/1", corrected).status_code == 303
    stale = rig.post(f"/avaliacao/{run_id}/par/1", {**old_tab, **ANSWER})
    assert stale.status_code == 409
    with evals.factory() as session:
        assert len(session.scalars(select(EvalJudgement)).all()) == 2


def test_reveal_button_blocked_until_last_pair(make_panel: MakePanel, evals: EvalRig) -> None:
    run_id = ready_run(evals)
    rig = make_panel([])
    assert "disabled" in rig.get(f"/avaliacao/{run_id}").text
    assert answer(rig, run_id, 1) == 303
    assert rig.post(f"/avaliacao/{run_id}/revelar").status_code == 409
    assert answer(rig, run_id, 2) == 303
    page = rig.get(f"/avaliacao/{run_id}").text
    assert "Revelar" in page and "disabled" not in page
    assert rig.post(f"/avaliacao/{run_id}/revelar").status_code == 303
    assert evals.run(run_id).status == "revealed"
    assert answer(rig, run_id, 1) == 409


def test_model_text_escaped_on_pair_page(make_panel: MakePanel, evals: EvalRig) -> None:
    from marketing_agent.llm import fake_scenarios

    hostile = '<script>alert(1)</script>"><img src=x>'
    evals.llm.scripts["simples"] += fake_scenarios.simple_package({"legenda": hostile})
    evals.llm.scripts["simples"] += fake_scenarios.simple_package({"legenda": hostile})
    run_id = ready_run(evals)
    rig = make_panel([])
    html = "".join(rig.get(f"/avaliacao/{run_id}/par/{i}").text for i in (1, 2))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_unknown_run_is_404_and_running_pairs_409(make_panel: MakePanel, evals: EvalRig) -> None:
    rig = make_panel([])
    unknown = "00000000-0000-0000-0000-000000000000"
    assert rig.get(f"/avaliacao/{unknown}").status_code == 404
    run_id = evals.start()
    assert rig.get(f"/avaliacao/{run_id}/par/1").status_code == 409
    assert len(evals.items(run_id)) == 4
    assert all(isinstance(item, EvalItem) for item in evals.items(run_id))


def test_report_page_and_decision_from_the_panel(make_panel: MakePanel, evals: EvalRig) -> None:
    run_id = ready_run(evals)
    rig = make_panel([])
    assert rig.get(f"/avaliacao/{run_id}/relatorio").status_code == 409
    early = rig.post(f"/avaliacao/{run_id}/decisao", {"papel": "revisor", "decisao": "manter"})
    assert early.status_code == 409
    for index in (1, 2):
        assert answer(rig, run_id, index) == 303
    assert rig.post(f"/avaliacao/{run_id}/revelar").status_code == 303
    page = rig.get(f"/avaliacao/{run_id}/relatorio")
    assert page.status_code == 200
    assert "Intervalo de Wilson a 95 %" in page.text and "Um só avaliador" in page.text
    assert "manter, simplificar ou retirar" in page.text
    decided = rig.post(
        f"/avaliacao/{run_id}/decisao",
        {"papel": "designer", "decisao": "simplificar", "justificacao": "<b>Pouco ganho</b>"},
    )
    assert decided.status_code == 303
    html = rig.get(f"/avaliacao/{run_id}/relatorio").text
    assert "&lt;b&gt;Pouco ganho&lt;/b&gt;" in html and "<b>Pouco ganho</b>" not in html
    bad = rig.post(f"/avaliacao/{run_id}/decisao", {"papel": "designer", "decisao": "talvez"})
    assert bad.status_code == 422


def test_non_finite_majorante_from_the_panel_is_422(make_panel: MakePanel, evals: EvalRig) -> None:
    rig = make_panel([])
    for value in ("sNaN", "NaN", "Infinity"):
        bad = rig.post("/avaliacao", {"conjunto": "briefs_teste", "majorante": value})
        assert bad.status_code == 422, value


def test_progress_poll_refreshes_the_page_when_the_run_is_ready(
    make_panel: MakePanel, evals: EvalRig
) -> None:
    run_id = evals.start()
    rig = make_panel([])
    assert "hx-refresh" not in rig.get(f"/avaliacao/{run_id}/estado").headers
    evals.drain()
    assert rig.get(f"/avaliacao/{run_id}/estado").headers["hx-refresh"] == "true"
