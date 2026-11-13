"""Estimativa e progresso sem fuga da atribuicao cega dos percursos."""

from dataclasses import replace

import pytest
from content_rig import build_rig
from eval_rig import build_eval_rig

from marketing_agent.db.models import EvalItem, EvalRun
from marketing_agent.evals.views import EvalViews
from marketing_agent.panel.context import EvalServices


@pytest.fixture
def evaluation(make_panel, tmp_path):
    panel = make_panel([])
    content = build_rig(
        panel.factory, panel.clock, panel.brand_id, [], media_dir=panel.settings.media_dir
    )
    rig = build_eval_rig(content, tmp_path / "evals")
    panel.app.state.panel = replace(
        panel.app.state.panel, evals=EvalServices(rig.service, EvalViews(panel.factory))
    )
    return panel, rig


def test_estimate_shown_before_start(evaluation):
    panel, rig = evaluation
    page = panel.get("/avaliacao/nova?conjunto=conj")
    assert page.status_code == 200 and "6.00" in page.text and "4 pacotes" in page.text
    assert 'name="teto"' in page.text and 'name="versao"' in page.text
    response = panel.post("/avaliacao", {"conjunto": "conj", "teto": "6", "versao": "1"})
    assert response.status_code == 303
    assert panel.get(response.headers["location"]).status_code == 200


def test_start_refuses_changed_estimate(evaluation):
    panel, rig = evaluation
    response = panel.post("/avaliacao", {"conjunto": "conj", "teto": "3", "versao": "1"})
    assert response.status_code == 409
    assert (
        panel.post("/avaliacao", {"conjunto": "conj", "teto": "NaN", "versao": "1"}).status_code
        == 422
    )
    assert rig.content.llm.requests == []


def test_progress_shows_only_aggregate_counts(evaluation):
    panel, rig = evaluation
    run_id = rig.start()
    item = rig.items(run_id)[0]
    with rig.factory.begin() as session:
        session.get_one(EvalItem, item.id).status = "failed"
    page = panel.get(f"/avaliacao/{run_id}/estado")
    assert page.status_code == 200
    assert all(
        value not in page.text for value in ("simples", "especializado", "brief_key", item.id)
    )
    assert "1 falhados" in page.text and "every 5s" in page.text


def test_polling_only_while_running(evaluation):
    panel, rig = evaluation
    run_id = rig.start()
    assert "every 5s" in panel.get(f"/avaliacao/{run_id}").text
    with rig.factory.begin() as session:
        session.get_one(EvalRun, run_id).status = "ready"
    assert "every 5s" not in panel.get(f"/avaliacao/{run_id}/estado").text
    assert panel.get("/avaliacao").status_code == 200


def test_every_eval_route_requires_auth_and_csrf(evaluation):
    panel, rig = evaluation
    run_id = rig.start()
    for path in (
        "/avaliacao",
        "/avaliacao/nova",
        f"/avaliacao/{run_id}",
        f"/avaliacao/{run_id}/estado",
    ):
        assert panel.client.get(path).status_code in (401, 403)
    assert panel.post("/avaliacao", with_token=False).status_code == 403
    assert panel.get(f"/avaliacao/{'0' * 36}").status_code == 404
    assert panel.get("/avaliacao/nova?conjunto=../../a").status_code == 422
