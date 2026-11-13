"""Pagina de comparacao: anonimato, media, temporizador e respostas explicitas."""

import re
from datetime import timedelta

import pytest
from content_rig import package_pass
from eval_panel_fixtures import evaluation
from sqlalchemy import select

from marketing_agent.db.models import EvalJudgement
from marketing_agent.llm.fake_scenarios import simple_package
from marketing_agent.panel.pair_timer import PairTimer

__all__ = ["evaluation"]


def ready(panel, rig):
    run_id = rig.start()
    for item in rig.items(run_id):
        rig.process(item, simple_package() if item.path == "simples" else package_pass())
    return run_id


def fields(page):
    return {
        key: re.search(rf'name="{key}" value="([^"]*)"', page.text).group(1)
        for key in ("inicio", "substitui")
    }


def answer(panel, path, **changes):
    data = {**fields(panel.get(path)), "better": "a", "edit_a": "0", "edit_b": "1"}
    return panel.post(path, {**data, **changes})


def test_pair_html_never_reveals_path(evaluation):
    panel, rig = evaluation
    run_id = ready(panel, rig)
    for n in (1, 2):
        page = panel.get(f"/avaliacao/{run_id}/par/{n}")
        assert page.status_code == 200
        forbidden = (
            "simples",
            "especializado",
            "revisor",
            "copywriter",
            "designer",
            "custo",
            "storage_key",
            "sha256",
        )
        assert all(word not in page.text.lower() for word in forbidden)
        for item in rig.items(run_id):
            assert item.id not in page.text and item.final_asset_id not in page.text
        assert page.text.count('class="lado-avaliacao"') == 2
        assert " checked" not in page.text  # nenhuma escolha de qualidade ou minutos implicita


def test_pair_image_response_has_no_identifying_headers(evaluation):
    panel, rig = evaluation
    run_id = ready(panel, rig)
    for side in ("a", "b"):
        image = panel.get(f"/avaliacao/{run_id}/par/1/imagem/{side}")
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
        assert image.headers["cache-control"] == "no-store"
        assert image.headers["x-content-type-options"] == "nosniff"
        assert "etag" not in image.headers and "content-disposition" not in image.headers


def test_seconds_spent_measured_by_signed_token(evaluation):
    panel, rig = evaluation
    run_id = ready(panel, rig)
    path = f"/avaliacao/{run_id}/par/1"
    data = {**fields(panel.get(path)), "better": "b", "edit_a": "2_5", "edit_b": "0"}
    rig.content.clock.advance(timedelta(seconds=37))
    assert panel.post(path, data).status_code == 303
    with rig.factory() as session:
        row = session.scalar(select(EvalJudgement))
        assert row.seconds_spent == 37


def test_forged_timer_token_is_422(evaluation):
    panel, rig = evaluation
    run_id = ready(panel, rig)
    assert answer(panel, f"/avaliacao/{run_id}/par/1", inicio="falso").status_code == 422
    token = fields(panel.get(f"/avaliacao/{run_id}/par/1"))["inicio"]
    assert answer(panel, f"/avaliacao/{run_id}/par/2", inicio=token).status_code == 422


def test_judgement_form_requires_choices(evaluation):
    panel, rig = evaluation
    run_id = ready(panel, rig)
    path = f"/avaliacao/{run_id}/par/1"
    for key in ("better", "edit_a", "edit_b"):
        assert answer(panel, path, **{key: ""}).status_code == 422


def test_reveal_button_blocked_until_last_pair(evaluation):
    panel, rig = evaluation
    run_id = ready(panel, rig)
    path = f"/avaliacao/{run_id}"
    assert panel.post(path + "/revelar").status_code == 409
    assert "disabled" in panel.get(path).text
    for n in (1, 2):
        assert answer(panel, f"{path}/par/{n}").status_code == 303
    assert panel.post(path + "/revelar").status_code == 303
    assert answer(panel, f"{path}/par/1").status_code == 409


def test_model_text_escaped_on_pair_page(evaluation):
    panel, rig = evaluation
    run_id = rig.start()
    for item in rig.items(run_id):
        script = (
            simple_package(legenda='<img src=x onerror="alert(1)">')
            if item.path == "simples"
            else package_pass()
        )
        rig.process(item, script)
    page = panel.get(f"/avaliacao/{run_id}/par/1")
    assert "&lt;img" in page.text and '<img src=x onerror="alert(1)">' not in page.text


def test_every_pair_route_requires_auth_and_csrf(evaluation):
    panel, rig = evaluation
    run_id = ready(panel, rig)
    path = f"/avaliacao/{run_id}/par/1"
    for url in (path, path + "/imagem/a"):
        assert panel.client.get(url).status_code in (401, 403)
    for url in (path, f"/avaliacao/{run_id}/revelar"):
        assert panel.post(url, with_token=False).status_code == 403
    assert panel.get(f"/avaliacao/{run_id}/par/0").status_code == 404
    assert panel.get(path + "/imagem/c").status_code == 404


def test_timer_rejects_future_and_wrong_pair_and_caps_duration(clock):
    timer = PairTimer(b"c" * 32)
    token = timer.issue("run", 1, clock.now())
    assert timer.seconds(token, "run", 1, clock.now() + timedelta(hours=2)) == 3600
    for run_id, index, now in (
        ("other", 1, clock.now()),
        ("run", 2, clock.now()),
        ("run", 1, clock.now() - timedelta(seconds=1)),
    ):
        with pytest.raises(ValueError):
            timer.seconds(token, run_id, index, now)
