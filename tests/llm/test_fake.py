"""Testes do cliente falso e da estrutura dos cenarios (sem validar esquemas JSON)."""

import json

import pytest

from marketing_agent.budget.pricing import Usage
from marketing_agent.llm import fake_scenarios as scenarios
from marketing_agent.llm.fake import FakeLlmClient, ScriptExhausted
from marketing_agent.llm.types import (
    LlmNetworkError,
    LlmRequest,
    LlmResponse,
    Message,
    StopReason,
    TextPart,
)


def _request(text: str = "ola") -> LlmRequest:
    return LlmRequest(
        model_id="fake-1",
        system="sistema",
        messages=[Message(role="user", content=[TextPart(text)])],
        max_output_tokens=100,
    )


def _response(text: str) -> LlmResponse:
    return LlmResponse(
        text=text,
        usage=Usage(input_tokens=1, output_tokens=1),
        provider_request_id=None,
        resolved_model="fake-1",
        stop_reason=StopReason.end,
    )


def test_fake_returns_in_order_and_records() -> None:
    client = FakeLlmClient([_response("a"), _response("b")])
    first, second = _request("1"), _request("2")
    assert client.complete(first).text == "a"
    assert client.complete(second).text == "b"
    assert client.requests == [first, second]


def test_fake_raises_scripted_exception() -> None:
    client = FakeLlmClient([LlmNetworkError("sem rede"), _response("ok")])
    with pytest.raises(LlmNetworkError):
        client.complete(_request())
    assert client.complete(_request()).text == "ok"
    assert len(client.requests) == 2


def test_fake_exhausted_raises() -> None:
    client = FakeLlmClient([])
    with pytest.raises(ScriptExhausted):
        client.complete(_request())


def test_fake_default_capabilities() -> None:
    assert FakeLlmClient([]).capabilities == frozenset({"text", "json_schema"})


def test_interview_full_structure() -> None:
    items = scenarios.interview_full(turns=3)
    assert len(items) == 5
    bodies = [json.loads(item.text) for item in items if isinstance(item, LlmResponse)]
    assert bodies[0]["next_question"] and bodies[0]["ready_for_profile"] is False
    assert bodies[3]["next_question"] is None and bodies[3]["ready_for_profile"] is True
    for body in bodies[1:3]:
        assert 0 < len(body["next_question"]) < 300 and "\n" not in body["next_question"]
        assert body["summary"]
    assert len(bodies[4]["topicos"]) >= 3
    assert {"lacunas_abertas", "suposicoes"} <= bodies[4].keys()


def test_diagnosis_and_strategy_structure() -> None:
    diagnosis_item, strategy_item = scenarios.diagnosis_and_strategy()
    assert isinstance(diagnosis_item, LlmResponse) and isinstance(strategy_item, LlmResponse)
    diagnosis, strategy = json.loads(diagnosis_item.text), json.loads(strategy_item.text)
    kinds = {a["tipo"] for a in diagnosis["achados"]}
    assert kinds == {"facto", "hipotese"}
    assert all(a["evidencia"] and a["origem"] for a in diagnosis["achados"] if a["tipo"] == "facto")
    assert len(strategy["propostas_oferta"]) >= 2
    assert all(1 <= e["prazo_dias"] <= 90 for e in strategy["experiencias"])


def test_invalid_scenarios_structure() -> None:
    first, second = scenarios.invalid_then_valid('{"a": 1}')
    assert isinstance(first, LlmResponse) and isinstance(second, LlmResponse)
    with pytest.raises(json.JSONDecodeError):
        json.loads(first.text)
    assert second.text == '{"a": 1}'
    always = scenarios.always_invalid()
    assert len(always) == 3
    for item in always:
        assert isinstance(item, LlmResponse)
        with pytest.raises(json.JSONDecodeError):
            json.loads(item.text)


def test_refusal_truncated_and_network_structure() -> None:
    (refused,) = scenarios.refusal()
    assert isinstance(refused, LlmResponse) and refused.stop_reason is StopReason.refusal
    (cut,) = scenarios.truncated()
    assert isinstance(cut, LlmResponse) and cut.stop_reason is StopReason.max_tokens
    with pytest.raises(json.JSONDecodeError):
        json.loads(cut.text)
    error, ok = scenarios.network_error_then('{"a": 1}')
    assert isinstance(error, LlmNetworkError)
    assert isinstance(ok, LlmResponse) and ok.text == '{"a": 1}'
    assert ok.provider_request_id is None and ok.resolved_model == "fake-1"
