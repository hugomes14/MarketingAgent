"""Esquemas de saida do Diretor e coerencia com os guioes do cliente falso."""

import json

import pytest
from pydantic import ValidationError

from marketing_agent.agents.diretor.gap_map import GAP_TOPICS
from marketing_agent.agents.diretor.schemas import InterviewTurnOutput, PerfilMarca, PerfilTopico
from marketing_agent.llm import fake_scenarios


def turn(next_question: str | None) -> dict[str, object]:
    return {
        "next_question": next_question,
        "summary": "resumo",
        "gap_map_updates": [],
        "ready_for_profile": False,
        "injection_suspected": False,
    }


def test_question_with_newline_rejected() -> None:
    with pytest.raises(ValidationError, match="quebras de linha"):
        InterviewTurnOutput.model_validate(turn("primeira\nsegunda"))
    with pytest.raises(ValidationError, match="quebras de linha"):
        InterviewTurnOutput.model_validate(turn("primeira\rsegunda"))


def test_question_length_limit_is_300() -> None:
    assert InterviewTurnOutput.model_validate(turn("a" * 300)).next_question == "a" * 300
    with pytest.raises(ValidationError, match="300"):
        InterviewTurnOutput.model_validate(turn("a" * 301))


def test_question_may_be_none_and_output_is_frozen() -> None:
    output = InterviewTurnOutput.model_validate(turn(None))
    assert output.next_question is None
    with pytest.raises(ValidationError):
        output.summary = "outro"  # type: ignore[misc]


def test_profile_rejects_unknown_topic() -> None:
    topico = PerfilTopico(texto="x", origem="suposto")
    with pytest.raises(ValidationError, match="inexistente"):
        PerfilMarca(topicos={"inexistente": topico}, lacunas_abertas=[], suposicoes=[])


def test_profile_accepts_known_topics() -> None:
    topico = PerfilTopico(texto="x", origem="suposto")
    perfil = PerfilMarca(topicos={GAP_TOPICS[0]: topico}, lacunas_abertas=["a"], suposicoes=[])
    assert perfil.topicos[GAP_TOPICS[0]] == topico


def test_scenarios_topics_equal_gap_topics() -> None:
    assert fake_scenarios._GAP_TOPICS == GAP_TOPICS


def test_scenarios_produce_valid_shapes() -> None:
    *turns, profile = (
        item for item in fake_scenarios.interview_full(3) if not isinstance(item, Exception)
    )
    for item in turns:
        InterviewTurnOutput.model_validate_json(item.text)
    perfil = PerfilMarca.model_validate_json(profile.text)
    assert set(perfil.topicos) <= set(GAP_TOPICS)
    assert json.loads(profile.text)["lacunas_abertas"]
