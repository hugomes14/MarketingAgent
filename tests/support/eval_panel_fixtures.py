"""Painel e geracao falsos sobre os servicos reais de avaliacao, para testes ponta a ponta."""

from dataclasses import replace

import pytest
from content_rig import build_rig
from eval_rig import build_eval_rig

from marketing_agent.evals.judging import JudgingService
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
        panel.app.state.panel,
        evals=EvalServices(
            rig.service, EvalViews(panel.factory), JudgingService(panel.factory, panel.clock)
        ),
    )
    return panel, rig
