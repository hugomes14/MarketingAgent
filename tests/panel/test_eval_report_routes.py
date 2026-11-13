"""Relatorio restrito a revelacao, limitacoes visiveis e decisoes humanas com CSRF."""

from content_rig import package_pass
from eval_panel_fixtures import evaluation

from marketing_agent.evals.blind import JudgementInput
from marketing_agent.llm.fake_scenarios import simple_package

__all__ = ["evaluation"]


def test_report_shows_estimate_vs_actual_and_limits(evaluation):
    panel, rig = evaluation
    run_id = rig.start()
    path = f"/avaliacao/{run_id}"
    assert panel.get(path + "/relatorio").status_code == 409
    judging = panel.app.state.panel.evals.judging
    for item in rig.items(run_id):
        rig.process(item, simple_package() if item.path == "simples" else package_pass())
    for index in (1, 2):
        judging.record(
            rig.brand_id,
            run_id,
            index,
            JudgementInput(better="empate", errors_a=[], errors_b=[], edit_a="0", edit_b="1"),
            1,
            None,
            "ana",
        )
    judging.reveal(rig.brand_id, run_id, "ana")
    page = panel.get(path + "/relatorio")
    assert page.status_code == 200
    for text in (
        "6.0000",
        "0.0000",
        "Poucos briefs",
        "Um só avaliador",
        "Sem causalidade",
        "0 pares decisivos",
    ):
        assert text in page.text
    assert (
        panel.post(
            path + "/decisao",
            {"role": "revisor", "decision": "manter", "justificacao": "<script>texto</script>"},
        ).status_code
        == 303
    )
    page = panel.get(path + "/relatorio")
    assert "&lt;script&gt;texto" in page.text and "<script>texto" not in page.text
    assert panel.post(path + "/decisao", with_token=False).status_code == 403
    assert panel.client.get(path + "/relatorio").status_code in (401, 403)
