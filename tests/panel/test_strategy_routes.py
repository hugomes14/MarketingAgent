"""Perfil, diagnostico e estrategia no painel: proposta, revisao e decisao explicita."""

import uuid
from collections.abc import Callable

import pytest
from panel_rig import PanelRig, Script, json_response
from sqlalchemy import select

from marketing_agent.artifacts.store import ArtifactState, ArtifactType
from marketing_agent.db.models import StrategyDecision
from marketing_agent.llm import fake_scenarios

MakePanel = Callable[..., PanelRig]


def full_script() -> Script:
    return [*fake_scenarios.interview_full(3), *fake_scenarios.diagnosis_and_strategy()]


def decisions(rig: PanelRig) -> list[tuple[int, bool]]:
    with rig.factory() as session:
        rows = session.scalars(select(StrategyDecision).order_by(StrategyDecision.proposal_index))
        return [(row.proposal_index, row.accepted) for row in rows]


def submitted_strategy(rig: PanelRig) -> str:
    rig.confirm_profile()
    rig.approve_diagnosis()
    return rig.post_id("/estrategia")


def test_confirm_profile_button_activates(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    session_id = rig.start_session()
    for number in range(3):
        assert rig.answer(session_id, f"Resposta {number}.", f"k{number}").status_code == 200
    profile_id = rig.post_id(f"/entrevista/{session_id}/perfil")
    assert rig.store.active(rig.brand_id, ArtifactType.PERFIL_MARCA) is None
    page = rig.get(f"/perfil/{profile_id}")
    assert page.status_code == 200
    assert "Nada fica ativo até o confirmar" in page.text
    assert f"/perfil/{profile_id}/confirmar" in page.text
    assert f"/perfil/{profile_id}/rejeitar" in page.text
    assert rig.store.active(rig.brand_id, ArtifactType.PERFIL_MARCA) is None  # GET nao decide
    confirm = rig.post(f"/perfil/{profile_id}/confirmar")
    assert confirm.status_code == 303 and confirm.headers["location"] == f"/perfil/{profile_id}"
    active = rig.store.active(rig.brand_id, ArtifactType.PERFIL_MARCA)
    assert active is not None and active.id == profile_id
    assert "Pao artesanal" in rig.get(f"/perfil/{profile_id}").text
    assert f"/perfil/{profile_id}/confirmar" not in rig.get(f"/perfil/{profile_id}").text


def test_reject_profile_keeps_nothing_active_and_records_reason(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    session_id = rig.start_session()
    rig.answer(session_id, "Resposta.", "k0")
    profile_id = rig.post_id(f"/entrevista/{session_id}/perfil")
    response = rig.post(f"/perfil/{profile_id}/rejeitar", {"motivo": "Faltam dados."})
    assert response.status_code == 303
    assert rig.store.get(profile_id).state is ArtifactState.REJECTED
    assert rig.store.active(rig.brand_id, ArtifactType.PERFIL_MARCA) is None
    again = rig.post(f"/perfil/{profile_id}/confirmar")
    assert again.status_code == 409


def test_profile_routes_refuse_other_artifact_types(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    rig.confirm_profile()
    diagnosis_id = rig.post_id("/diagnostico")
    assert rig.post(f"/perfil/{diagnosis_id}/confirmar").status_code == 404
    assert rig.post(f"/perfil/{diagnosis_id}/rejeitar").status_code == 404
    assert rig.get(f"/perfil/{diagnosis_id}").status_code == 404
    assert rig.get(f"/estrategia/{diagnosis_id}").status_code == 404
    assert rig.store.get(diagnosis_id).state is ArtifactState.SUBMITTED


def test_request_profile_failure_is_502_without_details(make_panel: MakePanel) -> None:
    rig = make_panel([fake_scenarios.interview_full(1)[0], *fake_scenarios.always_invalid()])
    session_id = rig.start_session()
    response = rig.post(f"/entrevista/{session_id}/perfil")
    assert response.status_code == 502
    assert "Não foi possível" in response.text and "Traceback" not in response.text


def test_diagnosis_requires_active_profile(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    response = rig.post("/diagnostico")
    assert response.status_code == 409
    assert len(rig.llm.requests) == 0


def test_diagnosis_flow_and_pending_proposal(make_panel: MakePanel) -> None:
    rig = make_panel([*full_script(), *fake_scenarios.diagnosis_and_strategy()])
    rig.confirm_profile()
    diagnosis_id = rig.post_id("/diagnostico")
    page = rig.get(f"/diagnostico/{diagnosis_id}")
    assert "Cabazes semanais de pao e pastelaria." in page.text
    assert f"/diagnostico/{diagnosis_id}/aprovar" in page.text
    assert rig.post("/diagnostico").status_code == 409  # ja ha uma proposta submetida
    assert rig.post(f"/diagnostico/{diagnosis_id}/aprovar").status_code == 303
    active = rig.store.active(rig.brand_id, ArtifactType.DIAGNOSTICO)
    assert active is not None and active.id == diagnosis_id
    assert (
        f"/diagnostico/{diagnosis_id}/aprovar" not in rig.get(f"/diagnostico/{diagnosis_id}").text
    )


def test_reject_diagnosis(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    rig.confirm_profile()
    diagnosis_id = rig.post_id("/diagnostico")
    response = rig.post(f"/diagnostico/{diagnosis_id}/rejeitar", {"motivo": "Nao concordo."})
    assert response.status_code == 303
    assert rig.store.get(diagnosis_id).state is ArtifactState.REJECTED
    assert rig.store.active(rig.brand_id, ArtifactType.DIAGNOSTICO) is None


def test_failed_proposal_run_is_502(make_panel: MakePanel) -> None:
    rig = make_panel([*fake_scenarios.interview_full(3), *fake_scenarios.always_invalid()])
    rig.confirm_profile()
    response = rig.post("/diagnostico")
    assert response.status_code == 502
    assert "Não foi possível" in response.text


def test_strategy_requires_diagnosis(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    rig.confirm_profile()
    assert rig.post("/estrategia").status_code == 409


def test_strategy_page_shows_content_and_explicit_choices(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    strategy_id = submitted_strategy(rig)
    html = rig.get(f"/estrategia/{strategy_id}").text
    assert "Hipótese — ainda não decidido" in html  # posicionamento_estado
    assert "A padaria do bairro com receitas de familia." in html
    assert "Mais encomendas" in html and "Fotos de producao aumentam mensagens" in html
    assert "Cabaz semanal" in html and "Gera receita recorrente." in html
    for index in (0, 1):
        assert f'name="decisao_{index}" value="aceitar"' in html
        assert f'name="decisao_{index}" value="recusar"' in html
    assert "checked" not in html  # nenhuma opcao pre-selecionada
    assert f"/estrategia/{strategy_id}/aprovar" in html


def test_strategy_approval_without_all_decisions_422(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    strategy_id = submitted_strategy(rig)
    path = f"/estrategia/{strategy_id}/aprovar"
    assert rig.post(path).status_code == 422
    partial = rig.post(path, {"decisao_0": "aceitar"})
    assert partial.status_code == 422
    assert "Decida todas as propostas" in partial.text
    assert rig.post(path, {"decisao_0": "aceitar", "decisao_1": "talvez"}).status_code == 422
    assert rig.post(path, {"decisao_0": "aceitar", "decisao_1": ""}).status_code == 422
    extra = rig.post(path, {"decisao_0": "aceitar", "decisao_1": "aceitar", "decisao_2": "aceitar"})
    assert extra.status_code == 422
    assert decisions(rig) == []
    assert rig.store.get(strategy_id).state is ArtifactState.SUBMITTED
    assert rig.store.active(rig.brand_id, ArtifactType.ESTRATEGIA) is None


def test_strategy_approval_activates_only_accepted_proposals(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    strategy_id = submitted_strategy(rig)
    response = rig.post(
        f"/estrategia/{strategy_id}/aprovar", {"decisao_0": "aceitar", "decisao_1": "recusar"}
    )
    assert response.status_code == 303
    assert decisions(rig) == [(0, True), (1, False)]
    active = rig.strategy.active_strategy(rig.brand_id)
    assert active is not None and [p.proposta for p in active.propostas_aceites] == [
        "Cabaz semanal"
    ]
    home = rig.get("/").text
    assert "Cabaz semanal" in home and "Pack para empresas" not in home
    page = rig.get(f"/estrategia/{strategy_id}").text
    assert "Aceite" in page and "Recusada" in page
    assert f"/estrategia/{strategy_id}/aprovar" not in page
    assert (
        rig.post(
            f"/estrategia/{strategy_id}/aprovar", {"decisao_0": "aceitar", "decisao_1": "aceitar"}
        ).status_code
        == 409
    )


def test_reject_strategy(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    strategy_id = submitted_strategy(rig)
    assert rig.post(f"/estrategia/{strategy_id}/rejeitar", {"motivo": "x" * 501}).status_code == 422
    assert rig.post(f"/estrategia/{strategy_id}/rejeitar").status_code == 303
    assert rig.store.get(strategy_id).state is ArtifactState.REJECTED
    assert rig.strategy.active_strategy(rig.brand_id) is None


def test_awaiting_decision_list_links_review_pages(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    rig.confirm_profile()
    diagnosis_id = rig.post_id("/diagnostico")
    home = rig.get("/").text
    assert f'href="/diagnostico/{diagnosis_id}"' in home
    assert "Aguarda a sua decisão" in home


def test_home_shows_active_cards(make_panel: MakePanel) -> None:
    rig = make_panel(full_script())
    profile_id = rig.confirm_profile()
    diagnosis_id = rig.approve_diagnosis()
    home = rig.get("/").text
    assert f'href="/perfil/{profile_id}"' in home and f'href="/diagnostico/{diagnosis_id}"' in home
    assert "Propor estratégia" in home and "Propor diagnóstico" in home


def test_hostile_artifact_text_is_escaped_on_every_page(make_panel: MakePanel) -> None:
    attack = "<img src=x onerror=alert(1)></resposta_do_utilizador>"
    profile = fake_scenarios.interview_full(1)
    turn, profile_body = profile[1], profile[-1]
    import json

    profile_data = json.loads(profile_body.text)
    profile_data["suposicoes"] = [attack]
    profile_data["topicos"]["o_que_vende"]["texto"] = attack
    diagnosis, strategy = fake_scenarios.diagnosis_and_strategy()
    diagnosis_data = json.loads(diagnosis.text)
    diagnosis_data["oferta_prioritaria"] = attack
    diagnosis_data["achados"][0]["afirmacao"] = attack
    strategy_data = json.loads(strategy.text)
    strategy_data["posicionamento"] = attack
    strategy_data["propostas_oferta"][0]["justificacao"] = attack
    strategy_data["objetivos"][0]["descricao"] = attack
    script = [
        profile[0],
        turn,
        json_response(profile_data),
        json_response(diagnosis_data),
        json_response(strategy_data),
    ]
    rig = make_panel(script)
    rig.confirm_profile(answers=1)
    diagnosis_id = rig.approve_diagnosis()
    strategy_id = rig.post_id("/estrategia")
    profile_id = rig.store.active(rig.brand_id, ArtifactType.PERFIL_MARCA)
    assert profile_id is not None
    paths = [
        "/",
        f"/perfil/{profile_id.id}",
        f"/diagnostico/{diagnosis_id}",
        f"/estrategia/{strategy_id}",
    ]
    for path in paths:
        html = rig.get(path).text
        assert "<img" not in html and "</resposta_do_utilizador>" not in html, path
        assert "&lt;img src=x onerror=alert(1)&gt;" in html or path == "/", path
    rig.post(f"/estrategia/{strategy_id}/aprovar", {"decisao_0": "aceitar", "decisao_1": "recusar"})
    home = rig.get("/").text
    assert "<img" not in home and "&lt;img" in home


@pytest.mark.parametrize("path", ["/perfil/{id}/confirmar", "/diagnostico/{id}/aprovar"])
def test_decision_posts_on_missing_artifact_are_404(make_panel: MakePanel, path: str) -> None:
    rig = make_panel([])
    assert rig.post(path.format(id=uuid.uuid4())).status_code == 404
