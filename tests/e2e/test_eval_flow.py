"""Avaliacao cega de ponta a ponta: painel, worker, pares, correcao, revelacao, relatorio, decisao.

Corre com LLM falso por papel e gerador de imagens falso, sobre a base de teste. Prova o que o
Plano 2C promete: a corrida nasce no painel com o custo maximo confirmado, o worker gera os
pacotes pela fila sem tocar no lote semanal, os pares sao cegos e as respostas imutaveis, e a
decisao por papel fica registada depois da revelacao.
"""

import re
from collections.abc import Callable
from pathlib import Path

from eval_rig import build_eval_rig
from panel_rig import PanelRig, Script
from sqlalchemy import func, select

from marketing_agent.clock import FixedClock
from marketing_agent.content.package_costs import request_run_ids
from marketing_agent.db.models import (
    CostEntry,
    EvalJudgement,
    Post,
    PostRevision,
    RoleDecision,
)

MakePanel = Callable[[Script], PanelRig]
ANSWER = {"melhor": "b", "edicao_a": "1", "edicao_b": "0", "erro_a_outro": "1"}


def hidden(html: str) -> dict[str, str]:
    return dict(re.findall(r'name="(inicio|substitui)" value="([^"]*)"', html))


def test_eval_flow(
    make_panel: MakePanel,
    session_factory,
    clock: FixedClock,
    brand_id: str,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    evals = build_eval_rig(session_factory, clock, brand_id, tmp_path / "media")
    evals.add_price_item()
    panel = make_panel([])

    estimate = panel.get("/avaliacao/nova?conjunto=briefs_teste")
    upper = re.search(r'name="majorante" value="([^"]+)"', estimate.text)
    assert upper is not None
    started = panel.post("/avaliacao", {"conjunto": "briefs_teste", "majorante": upper.group(1)})
    assert started.status_code == 303
    run_id = started.headers["location"].rsplit("/", 1)[-1]

    assert evals.drain() == 4
    assert "4 de 4" in panel.get(f"/avaliacao/{run_id}").text

    for index in (1, 2):
        page = panel.get(f"/avaliacao/{run_id}/par/{index}")
        assert page.status_code == 200
        posted = panel.post(f"/avaliacao/{run_id}/par/{index}", {**hidden(page.text), **ANSWER})
        assert posted.status_code == 303
    correction = hidden(panel.get(f"/avaliacao/{run_id}/par/1").text)
    fixed = panel.post(f"/avaliacao/{run_id}/par/1", {**correction, **ANSWER, "melhor": "empate"})
    assert fixed.status_code == 303

    assert panel.post(f"/avaliacao/{run_id}/revelar").status_code == 303
    report = panel.get(f"/avaliacao/{run_id}/relatorio")
    assert report.status_code == 200 and "em 2 pares" in report.text
    decision = {"papel": "revisor", "decisao": "manter", "justificacao": "Primeira corrida."}
    assert panel.post(f"/avaliacao/{run_id}/decisao", decision).status_code == 303

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Post)) == 0
        assert session.scalar(select(func.count()).select_from(PostRevision)) == 0
        assert session.scalar(select(func.count()).select_from(EvalJudgement)) == 3
        assert session.scalar(select(func.count()).select_from(RoleDecision)) == 1
        for item in evals.items(run_id):
            runs = request_run_ids(brand_id, item.id, "eval")
            entries = session.scalar(
                select(func.count()).select_from(CostEntry).where(CostEntry.run_id.in_(runs))
            )
            assert entries and entries >= 1, item.path
