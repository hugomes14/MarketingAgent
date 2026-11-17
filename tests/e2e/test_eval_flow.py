"""Percurso completo de avaliacao com modelos falsos; nao cria publicacoes semanais."""

import re
from datetime import timedelta

from content_rig import package_pass
from eval_panel_fixtures import evaluation
from sqlalchemy import func, select

from marketing_agent.db.models import AgentRun, CostEntry, EvalJudgement, Job, Post, PostRevision
from marketing_agent.jobs.worker import Worker
from marketing_agent.llm.fake_scenarios import simple_package

__all__ = ["evaluation"]


def form_fields(page):
    return {
        key: re.search(rf'name="{key}" value="([^"]*)"', page.text).group(1)
        for key in ("inicio", "substitui")
    }


def test_eval_flow(evaluation):
    panel, rig = evaluation
    with rig.factory() as session:
        before = [session.scalar(select(func.count()).select_from(t)) for t in (Post, PostRevision)]
    estimate = panel.get("/avaliacao/nova?conjunto=conj")
    assert "4 pacotes" in estimate.text
    response = panel.post("/avaliacao", {"conjunto": "conj", "teto": "6", "versao": "1"})
    assert response.status_code == 303
    path = response.headers["location"]
    run_id = path.rsplit("/", 1)[-1]
    items = rig.items(run_id)
    # Datas diferentes tornam a ordem dos guioes falsos deterministica, sem trocar o claim real.
    with rig.factory.begin() as session:
        for order, item in enumerate(items):
            session.get_one(Job, item.job_id).run_at = rig.content.clock.now() + timedelta(
                seconds=order
            )
    rig.content.clock.advance(timedelta(minutes=1))
    worker = Worker(rig.content.queue, rig.content.pipeline, "e2e", eval_handler=rig.handler)
    for item in items:
        rig.content.llm.load(simple_package() if item.path == "simples" else package_pass())
        assert worker.run_once()
    assert worker.run_once() is False
    assert rig.run(run_id).status == "ready" and all(
        i.status == "succeeded" for i in rig.items(run_id)
    )
    assert panel.post(path + "/revelar").status_code == 409
    for index in (1, 2):
        pair_path = f"{path}/par/{index}"
        data = {**form_fields(panel.get(pair_path)), "better": "a", "edit_a": "0", "edit_b": "1"}
        rig.content.clock.advance(timedelta(seconds=10))
        assert panel.post(pair_path, data).status_code == 303
    # Corrigir preserva o registo inicial e liga a nova resposta pelo supersedes_id.
    data = {
        **form_fields(panel.get(path + "/par/1")),
        "better": "empate",
        "edit_a": "1",
        "edit_b": "2_5",
    }
    assert panel.post(path + "/par/1", data).status_code == 303
    assert panel.post(path + "/revelar").headers["location"] == path + "/relatorio"
    report = panel.get(path + "/relatorio")
    assert (
        report.status_code == 200
        and "1 empates" in report.text
        and "Sem causalidade" in report.text
    )
    for role in ("copywriter", "designer", "revisor"):
        assert (
            panel.post(
                path + "/decisao",
                {"role": role, "decision": "manter", "justificacao": "Decisao sintetica do teste."},
            ).status_code
            == 303
        )
    with rig.factory() as session:
        assert [
            session.scalar(select(func.count()).select_from(t)) for t in (Post, PostRevision)
        ] == before
        assert session.scalar(select(func.count()).select_from(EvalJudgement)) == 3
        for item in items:
            costs = session.scalar(
                select(func.count())
                .select_from(CostEntry)
                .join(AgentRun, AgentRun.id == CostEntry.run_id)
                .where(AgentRun.trigger == f"eval_item:{item.id}")
            )
            assert costs > 0
