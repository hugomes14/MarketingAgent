"""Percurso completo do Diretor pelo painel: entrevista, perfil, diagnostico e estrategia."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from panel_rig import LOGIN, PanelRig, Script
from sqlalchemy import select

from marketing_agent.db.models import AgentRun, AuditLog, CostEntry
from marketing_agent.llm import fake_scenarios

MakePanel = Callable[..., PanelRig]
DECISION_ACTIONS = ("artifact.approve", "strategy.decision")


def director_script() -> Script:
    return [*fake_scenarios.interview_full(3), *fake_scenarios.diagnosis_and_strategy()]


def test_interview_to_active_strategy(make_panel: MakePanel) -> None:
    rig = make_panel(director_script())
    rig.confirm_profile(3)
    rig.approve_diagnosis()
    strategy_id = rig.post_id("/estrategia")
    approved = rig.post(
        f"/estrategia/{strategy_id}/aprovar", {"decisao_0": "aceitar", "decisao_1": "recusar"}
    )
    assert approved.status_code == 303

    active = rig.strategy.active_strategy(rig.brand_id)
    assert active is not None and len(active.propostas_aceites) == 1
    assert "Cabaz semanal" in rig.get("/").text

    with rig.factory() as session:
        runs = session.scalars(select(AgentRun).where(AgentRun.brand_id == rig.brand_id)).all()
        assert len(runs) == 7  # 1a pergunta, 3 respostas, perfil, diagnostico, estrategia
        assert {run.status for run in runs} == {"succeeded"}
        for run in runs:
            entries = session.scalars(select(CostEntry).where(CostEntry.run_id == run.id)).all()
            assert entries, f"execucao {run.id} sem cost_entry"
        decisions = session.scalars(
            select(AuditLog).where(AuditLog.action.in_(DECISION_ACTIONS))
        ).all()
    # perfil + diagnostico + estrategia aprovados e uma decisao por proposta: 5 registos
    assert len(decisions) == 5
    assert {entry.actor for entry in decisions} == {LOGIN}


WARNING_TEXT = "Gasto acima da referência mensal"


def test_over_reference_next_month_resets(make_panel: MakePanel) -> None:
    rig = make_panel(director_script())
    rig.add_cost(Decimal("25"))  # referencia mensal: 20 USD
    assert rig.budget.status(rig.brand_id).over_reference

    session_id = rig.start_session()  # a referencia informa, nao bloqueia
    answered = rig.answer(session_id, "Resposta.", "k0")
    assert answered.status_code == 200
    assert WARNING_TEXT in rig.get("/").text
    assert rig.budget.status(rig.brand_id).spent_usd >= Decimal("25")

    rig.clock.advance(datetime(2026, 12, 2, 12, tzinfo=UTC) - rig.clock.now())
    status = rig.budget.status(rig.brand_id)
    assert status.spent_usd == 0 and not status.over_reference
    assert WARNING_TEXT not in rig.get("/").text
