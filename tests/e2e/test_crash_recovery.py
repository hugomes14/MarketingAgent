"""Morte do processo depois da chamada ao LLM e antes do acerto (SIGKILL ou SystemExit)."""

import hashlib
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

import pytest
from panel_rig import PanelRig
from sqlalchemy import select

from marketing_agent.db.models import (
    AgentRun,
    AgentStep,
    CostEntry,
    CostReservation,
    InterviewSession,
    InterviewSubmission,
)
from marketing_agent.interview.service import INTERRUPTED_MESSAGE, TurnStatus
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.types import LlmRequest, LlmResponse
from marketing_agent.startup import worker_stale_after

MakePanel = Callable[..., PanelRig]
ANSWER = "Resposta que ficou a meio."
KEY = "chave-do-formulario-1"
RESERVED = Decimal("0.5")


def seed_killed_turn(rig: PanelRig, session_id: str, age: timedelta) -> str:
    """Estado deixado por um SIGKILL: execucao, passo e reserva abertos e submissao reclamada."""
    started = rig.clock.now() - age
    with rig.factory.begin() as session:
        run = AgentRun(
            brand_id=rig.brand_id,
            role="diretor",
            spec_version=1,
            trigger="interview.turn",
            status="running",
            started_at=started,
        )
        session.add(run)
        session.flush()
        step = AgentStep(run_id=run.id, seq=1, kind="llm", name="turno")
        session.add(step)
        session.flush()
        reservation = CostReservation(
            brand_id=rig.brand_id,
            run_id=run.id,
            step_id=step.id,
            role="diretor",
            provider="fake",
            model_id="fake-1",
            price_version="v1",
            reserved_usd=RESERVED,
            status="open",
            created_at=started,
        )
        submission = InterviewSubmission(
            session_id=session_id,
            idempotency_key=KEY,
            text_sha256=hashlib.sha256(ANSWER.encode()).hexdigest(),
            status="reserved",
        )
        session.add_all([reservation, submission])
        session.flush()
        owner = session.get_one(InterviewSession, session_id)
        owner.busy_submission_id = submission.id
        owner.busy_since = started
        return reservation.id


def reservation_status(rig: PanelRig, reservation_id: str) -> str:
    with rig.factory() as session:
        return session.get_one(CostReservation, reservation_id).status


def test_kill_after_llm_call_before_commit(make_panel: MakePanel) -> None:
    """(a) SIGKILL simulado por estado na base; a recuperacao nunca repete a chamada."""
    rig = make_panel(fake_scenarios.interview_full(3))
    session_id = rig.start_session()
    requests_before = len(rig.llm.requests)
    age = worker_stale_after(time_multiplier=1) + timedelta(seconds=60)
    reservation_id = seed_killed_turn(rig, session_id, age)
    assert reservation_status(rig, reservation_id) == "open"
    assert rig.budget.status(rig.brand_id).reserved_usd == RESERVED

    recovered = rig.budget.recover_stale(worker_stale_after(time_multiplier=1))

    assert recovered == 1
    assert reservation_status(rig, reservation_id) == "unknown_outcome"
    status = rig.budget.status(rig.brand_id)
    assert status.unknown_usd == RESERVED and status.reserved_usd == 0

    page = rig.answer(session_id, ANSWER, KEY)  # a mesma chave, pelo painel
    assert page.status_code == 200 and INTERRUPTED_MESSAGE in page.text
    again = rig.interview.submit_answer(session_id, ANSWER, KEY)  # e pelo servico
    assert again.status is TurnStatus.NEEDS_HUMAN
    assert len(rig.llm.requests) == requests_before  # nenhum pedido novo ao LLM
    with rig.factory() as session:
        row = session.scalars(
            select(InterviewSubmission).where(InterviewSubmission.idempotency_key == KEY)
        ).one()
        assert row.status == "failed"
        owner = session.get_one(InterviewSession, session_id)
        assert owner.busy_submission_id is None


class SystemExitLlm:
    """Cliente cuja chamada morre como se o processo terminasse (SystemExit)."""

    def __init__(self, rig: PanelRig) -> None:
        self._rig = rig

    def __call__(self, request: LlmRequest) -> LlmResponse:
        self._rig.llm.requests.append(request)
        raise SystemExit(137)


def test_system_exit_during_llm_call_leaves_unknown_outcome(
    make_panel: MakePanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b) SystemExit entre a chamada e o acerto: reserva desconhecida e execucao falhada.

    Desde a Tarefa 8 o motor marca `unknown_outcome` (nao deixa `open`) antes de deixar a
    excecao subir; o que ficaria `open` seria um SIGKILL, coberto no teste (a).
    """
    rig = make_panel(fake_scenarios.interview_full(3))
    session_id = rig.start_session()
    monkeypatch.setattr(rig.llm, "complete", SystemExitLlm(rig))
    requests_before = len(rig.llm.requests)

    with pytest.raises(SystemExit):
        rig.interview.submit_answer(session_id, ANSWER, KEY)

    assert len(rig.llm.requests) == requests_before + 1
    with rig.factory() as session:
        killed = session.scalars(
            select(CostReservation).where(CostReservation.status == "unknown_outcome")
        ).one()
        run = session.get_one(AgentRun, killed.run_id)
        assert run.status == "failed" and run.ended_at is not None
        step = session.get_one(AgentStep, killed.step_id)
        assert step.error_class == "unknown_outcome"
        assert session.scalars(select(CostEntry).where(CostEntry.run_id == run.id)).all() == []
    assert rig.budget.status(rig.brand_id).unknown_usd == killed.reserved_usd  # preco 0 no aparelho

    repeated = rig.interview.submit_answer(session_id, ANSWER, KEY)
    assert repeated.status is TurnStatus.NEEDS_HUMAN
    assert len(rig.llm.requests) == requests_before + 1  # a chave repetida nao chama o LLM
