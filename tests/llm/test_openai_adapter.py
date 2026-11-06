"""Testes do adaptador OpenAI: mapeamento da resposta, erros de transporte e pedido ao SDK."""

import base64
import json
import os
from pathlib import Path

import httpx2
import openai
import pytest
from openai.types.chat import ChatCompletion

from marketing_agent.budget.pricing import Usage
from marketing_agent.config import Capability
from marketing_agent.llm.openai_adapter import (
    OpenAiClient,
    classify_transport_error,
    to_llm_response,
)
from marketing_agent.llm.types import (
    ImagePart,
    LlmNetworkError,
    LlmRequest,
    LlmUnknownOutcome,
    Message,
    StopReason,
    TextPart,
)
from marketing_agent.secrets import SecretFileError

FIXTURES = Path(__file__).parent / "fixtures"
_CAPS: frozenset[Capability] = frozenset({"text", "json_schema"})
_SEGREDO = "sk-openai-segredo-de-teste"


def _raw(name: str) -> dict[str, object]:
    loaded: dict[str, object] = json.loads((FIXTURES / f"openai_{name}.json").read_text("utf-8"))
    return loaded


class _FakeSdkResponse:
    def __init__(self, raw: dict[str, object]) -> None:
        self._raw = raw

    def to_dict(self) -> dict[str, object]:
        return self._raw


class _FakeSdkCompletions:
    def __init__(self, outcome: dict[str, object] | Exception) -> None:
        self._outcome = outcome
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> _FakeSdkResponse:
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return _FakeSdkResponse(self._outcome)


class _FakeSdkChat:
    def __init__(self, outcome: dict[str, object] | Exception) -> None:
        self.completions = _FakeSdkCompletions(outcome)


class _FakeSdk:
    def __init__(self, outcome: dict[str, object] | Exception) -> None:
        self.chat = _FakeSdkChat(outcome)


def _key_file(tmp_path: Path) -> Path:
    path = tmp_path / "openai.key"
    path.write_text(_SEGREDO + "\n", encoding="utf-8")
    return path


def _client(
    tmp_path: Path, outcome: dict[str, object] | Exception
) -> tuple[OpenAiClient, _FakeSdk]:
    fake = _FakeSdk(outcome)
    client = OpenAiClient(_key_file(tmp_path), _CAPS, sdk_client=fake)  # type: ignore[arg-type]
    return client, fake


def _request(
    parts: list[TextPart | ImagePart] | None = None,
    json_schema: dict[str, object] | None = None,
) -> LlmRequest:
    content = list(parts) if parts is not None else [TextPart("ola")]
    return LlmRequest(
        model_id="gpt-5.4-mini",
        system="sistema",
        messages=[Message(role="user", content=list(content))],
        max_output_tokens=321,
        json_schema=json_schema,
    )


def _httpx_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")


def _connection_error(cause: Exception) -> openai.APIConnectionError:
    error = openai.APIConnectionError(request=_httpx_request())
    error.__cause__ = cause
    return error


def _timeout_error(cause: Exception) -> openai.APITimeoutError:
    error = openai.APITimeoutError(request=_httpx_request())
    error.__cause__ = cause
    return error


def _status_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    response = httpx2.Response(status, request=_httpx_request())
    return cls("erro de teste", response=response, body=None)


# --- to_llm_response ---------------------------------------------------------------------


def test_to_llm_response_maps_text_usage_stop() -> None:
    response = to_llm_response(_raw("single_text"))
    assert response.text == "Ola, mundo."
    assert response.usage == Usage(input_tokens=12, output_tokens=5, reasoning_tokens=None)
    assert response.provider_request_id == "chatcmpl-Single"
    assert response.resolved_model == "gpt-5.4-mini-2026-03-17"
    assert response.stop_reason is StopReason.end


def test_reasoning_tokens_mapped() -> None:
    # `completion_tokens` ja inclui o raciocinio; `reasoning_tokens` e so o detalhe.
    usage = to_llm_response(_raw("reasoning_usage")).usage
    assert usage == Usage(input_tokens=40, output_tokens=300, reasoning_tokens=256)


def test_length_maps_to_max_tokens() -> None:
    response = to_llm_response(_raw("length"))
    assert response.stop_reason is StopReason.max_tokens
    assert response.text == "Texto cortado a meio da fra"


def test_refusal_field_maps_to_refusal() -> None:
    response = to_llm_response(_raw("refusal"))
    assert response.stop_reason is StopReason.refusal
    assert response.text == ""


def test_content_filter_maps_to_refusal() -> None:
    assert to_llm_response(_raw("content_filter")).stop_reason is StopReason.refusal


def test_null_content_becomes_empty_text() -> None:
    assert to_llm_response(_raw("null_content")).text == ""


@pytest.mark.parametrize("reason", ["tool_calls", "function_call", "nova", None])
def test_other_finish_reasons_map_to_other(reason: str | None) -> None:
    raw = _raw("single_text")
    raw["choices"][0]["finish_reason"] = reason  # type: ignore[index]
    assert to_llm_response(raw).stop_reason is StopReason.other


def test_real_sdk_completion_round_trip() -> None:
    # O que o cliente passa a `to_llm_response` e `ChatCompletion.to_dict()`, nao o JSON cru.
    completion = ChatCompletion.model_validate(_raw("reasoning_usage"))
    response = to_llm_response(completion.to_dict())
    assert response.usage.reasoning_tokens == 256
    assert response.text == "Resposta curta."


@pytest.mark.parametrize(
    ("path", "value", "field"),
    [
        (("choices",), [], "choices"),
        (("choices",), "x", "choices"),
        (("choices", 0, "message"), None, "message"),
        (("choices", 0, "message", "content"), 3, "content"),
        (("choices", 0, "message", "refusal"), 3, "refusal"),
        (("choices", 0, "finish_reason"), 3, "finish_reason"),
        (("usage",), None, "usage"),
        (("usage", "prompt_tokens"), "1", "prompt_tokens"),
        (("usage", "completion_tokens"), None, "completion_tokens"),
        (("usage", "completion_tokens_details"), 3, "completion_tokens_details"),
        (("id",), 3, "id"),
        (("model",), None, "model"),
    ],
)
def test_invalid_raw_names_the_field(
    path: tuple[str | int, ...], value: object, field: str
) -> None:
    raw = _raw("reasoning_usage")
    node: object = raw
    for key in path[:-1]:
        node = node[key]  # type: ignore[index]
    node[path[-1]] = value  # type: ignore[index]
    with pytest.raises(ValueError, match=field):
        to_llm_response(raw)


# --- classify_transport_error ------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_connection_error(httpx2.ConnectError("recusada")), LlmNetworkError),
        (_timeout_error(httpx2.ConnectTimeout("lento")), LlmNetworkError),
        (_timeout_error(httpx2.ReadTimeout("sem resposta")), LlmUnknownOutcome),
        (_timeout_error(httpx2.WriteTimeout("x")), LlmUnknownOutcome),
        (_connection_error(httpx2.RemoteProtocolError("cortada")), LlmUnknownOutcome),
        (_connection_error(OSError("x")), LlmUnknownOutcome),
        (_status_error(openai.RateLimitError, 429), LlmNetworkError),
        (_status_error(openai.InternalServerError, 503), LlmNetworkError),
        (_status_error(openai.InternalServerError, 529), LlmNetworkError),
        (_status_error(openai.InternalServerError, 500), LlmUnknownOutcome),
        (_status_error(openai.InternalServerError, 504), LlmUnknownOutcome),
    ],
)
def test_classified_transport_errors(error: Exception, expected: type[Exception]) -> None:
    assert classify_transport_error(error) is expected


@pytest.mark.parametrize(
    ("cls", "status"),
    [
        (openai.BadRequestError, 400),
        (openai.AuthenticationError, 401),
        (openai.PermissionDeniedError, 403),
        (openai.NotFoundError, 404),
        (openai.UnprocessableEntityError, 422),
    ],
)
def test_other_4xx_are_not_classified(cls: type[openai.APIStatusError], status: int) -> None:
    assert classify_transport_error(_status_error(cls, status)) is None


def test_unrelated_exception_is_not_classified() -> None:
    assert classify_transport_error(RuntimeError("x")) is None


# --- OpenAiClient --------------------------------------------------------------------------


def test_key_read_from_file(tmp_path: Path) -> None:
    client = OpenAiClient(_key_file(tmp_path), _CAPS, timeout_s=7.0)
    assert client._sdk.api_key == _SEGREDO
    assert client._sdk.max_retries == 0
    assert client._sdk.timeout == 7.0
    assert client.capabilities == _CAPS


def test_default_timeout_is_120s(tmp_path: Path) -> None:
    assert OpenAiClient(_key_file(tmp_path), _CAPS)._sdk.timeout == 120.0


def test_base_url_passed_to_sdk(tmp_path: Path) -> None:
    client = OpenAiClient(_key_file(tmp_path), _CAPS, base_url="http://127.0.0.1:11434/v1")
    assert str(client._sdk.base_url).startswith("http://127.0.0.1:11434/v1")


def test_key_not_in_repr_of_client(tmp_path: Path) -> None:
    client = OpenAiClient(_key_file(tmp_path), _CAPS)
    assert _SEGREDO not in repr(client)
    assert _SEGREDO not in repr(vars(client).keys())


@pytest.mark.parametrize("content", [None, "  \n"])
def test_missing_or_empty_key_file_raises_without_leaking(
    tmp_path: Path, content: str | None
) -> None:
    path = tmp_path / "k.key"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(SecretFileError) as info:
        OpenAiClient(path, _CAPS)
    assert _SEGREDO not in str(info.value)


def test_complete_maps_response(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, _raw("reasoning_usage"))
    response = client.complete(_request())
    assert response.text == "Resposta curta."
    assert response.usage.reasoning_tokens == 256


def test_complete_builds_sdk_call(tmp_path: Path) -> None:
    client, fake = _client(tmp_path, _raw("single_text"))
    client.complete(_request())
    (call,) = fake.chat.completions.calls
    assert call["model"] == "gpt-5.4-mini"
    assert call["max_completion_tokens"] == 321
    assert call["messages"] == [
        {"role": "developer", "content": "sistema"},
        {"role": "user", "content": [{"type": "text", "text": "ola"}]},
    ]
    # Modelos de raciocinio rejeitam sampling e `max_tokens`; nada disto pode ir no pedido.
    for forbidden in ("temperature", "top_p", "max_tokens", "seed", "reasoning_effort", "n"):
        assert forbidden not in call
    assert call["response_format"] is openai.omit


def test_complete_system_role_option_changes_system_message_role(tmp_path: Path) -> None:
    # Servidores compativeis (Ollama, LM Studio) ignoram `developer`: exigem `system`.
    fake = _FakeSdk(_raw("single_text"))
    client = OpenAiClient(
        _key_file(tmp_path),
        _CAPS,
        system_role="system",
        sdk_client=fake,  # type: ignore[arg-type]
    )
    client.complete(_request())
    (call,) = fake.chat.completions.calls
    assert call["messages"] == [
        {"role": "system", "content": "sistema"},
        {"role": "user", "content": [{"type": "text", "text": "ola"}]},
    ]


def test_complete_sends_assistant_turns_as_text(tmp_path: Path) -> None:
    client, fake = _client(tmp_path, _raw("single_text"))
    request = _request()
    turns = [*request.messages, Message(role="assistant", content=[TextPart("")])]
    client.complete(LlmRequest("m", "s", turns, 10))
    assert fake.chat.completions.calls[0]["messages"][2] == {  # type: ignore[index]
        "role": "assistant",
        "content": [{"type": "text", "text": "(sem resposta)"}],
    }


def test_complete_sends_images_as_data_url(tmp_path: Path) -> None:
    client, fake = _client(tmp_path, _raw("single_text"))
    image = ImagePart(media_type="image/png", data=b"\x89PNG-bytes")
    client.complete(_request([TextPart("olha"), image]))
    content = fake.chat.completions.calls[0]["messages"][1]["content"]  # type: ignore[index]
    encoded = base64.b64encode(b"\x89PNG-bytes").decode("ascii")
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }


def test_unsupported_image_type_rejected(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, _raw("single_text"))
    with pytest.raises(ValueError, match="image/bmp"):
        client.complete(_request([ImagePart(media_type="image/bmp", data=b"x")]))


def test_complete_never_sends_empty_text_part(tmp_path: Path) -> None:
    client, fake = _client(tmp_path, _raw("single_text"))
    client.complete(_request([TextPart("")]))
    content = fake.chat.completions.calls[0]["messages"][1]["content"]  # type: ignore[index]
    assert content == [{"type": "text", "text": "(sem resposta)"}]


def test_complete_passes_json_schema_as_response_format(tmp_path: Path) -> None:
    client, fake = _client(tmp_path, _raw("single_text"))
    schema: dict[str, object] = {"type": "object", "properties": {"a": {"type": "string"}}}
    client.complete(_request(json_schema=schema))
    # strict=False: nao garantimos que o esquema cumpre o subconjunto exigido pelo modo estrito.
    assert fake.chat.completions.calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "resposta", "schema": schema, "strict": False},
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_connection_error(httpx2.ConnectError("x")), LlmNetworkError),
        (_timeout_error(httpx2.ReadTimeout("x")), LlmUnknownOutcome),
        (_status_error(openai.RateLimitError, 429), LlmNetworkError),
        (_status_error(openai.InternalServerError, 500), LlmUnknownOutcome),
    ],
)
def test_complete_wraps_classified_errors(
    tmp_path: Path, error: Exception, expected: type[Exception]
) -> None:
    client, _ = _client(tmp_path, error)
    with pytest.raises(expected) as info:
        client.complete(_request())
    assert info.value.__cause__ is error
    assert type(error).__name__ in str(info.value)
    assert _SEGREDO not in str(info.value)
    assert "ola" not in str(info.value)


def test_complete_lets_unclassified_error_propagate(tmp_path: Path) -> None:
    error = _status_error(openai.BadRequestError, 400)
    client, _ = _client(tmp_path, error)
    with pytest.raises(openai.BadRequestError) as info:
        client.complete(_request())
    assert info.value is error


# --- chamada real (so a mao, com OPENAI_API_KEY_FILE) --------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    "OPENAI_API_KEY_FILE" not in os.environ, reason="OPENAI_API_KEY_FILE nao definido"
)
def test_live_call() -> None:
    # Configuravel por ambiente para poder correr contra um emulador compativel (ex.: Ollama).
    # Com `OPENAI_BASE_URL` o papel do prompt de sistema e `system`, como faz a fabrica.
    base_url = os.environ.get("OPENAI_BASE_URL")
    client = OpenAiClient(
        Path(os.environ["OPENAI_API_KEY_FILE"]),
        _CAPS,
        base_url=base_url,
        system_role="developer" if base_url is None else "system",
    )
    request = LlmRequest(
        model_id=os.environ.get("OPENAI_LIVE_MODEL", "gpt-5.4-mini"),
        system="Responde sempre apenas com a palavra BANANA.",
        messages=[Message(role="user", content=[TextPart("Qual e a capital de Portugal?")])],
        max_output_tokens=200,
    )
    response = client.complete(request)
    # O prompt de sistema tem de ser obedecido (um servidor que o ignore responderia "Lisboa").
    assert "BANANA" in response.text.upper()
    assert response.usage.output_tokens > 0
    assert response.stop_reason is StopReason.end
