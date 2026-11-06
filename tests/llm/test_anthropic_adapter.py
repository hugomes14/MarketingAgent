"""Testes do adaptador Anthropic: mapeamento da resposta, erros de transporte e pedido ao SDK."""

import base64
import json
import os
from pathlib import Path

import anthropic
import httpx2
import pytest

from marketing_agent.budget.pricing import Usage
from marketing_agent.config import Capability
from marketing_agent.llm.anthropic_adapter import (
    AnthropicClient,
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
_SEGREDO = "sk-ant-segredo-de-teste"


def _raw(name: str) -> dict[str, object]:
    loaded: dict[str, object] = json.loads((FIXTURES / f"anthropic_{name}.json").read_text("utf-8"))
    return loaded


class _FakeSdkResponse:
    def __init__(self, raw: dict[str, object]) -> None:
        self._raw = raw

    def to_dict(self) -> dict[str, object]:
        return self._raw


class _FakeSdkMessages:
    def __init__(self, outcome: dict[str, object] | Exception) -> None:
        self._outcome = outcome
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> _FakeSdkResponse:
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return _FakeSdkResponse(self._outcome)


class _FakeSdk:
    def __init__(self, outcome: dict[str, object] | Exception) -> None:
        self.messages = _FakeSdkMessages(outcome)


def _key_file(tmp_path: Path) -> Path:
    path = tmp_path / "anthropic.key"
    path.write_text(_SEGREDO + "\n", encoding="utf-8")
    return path


def _client(
    tmp_path: Path, outcome: dict[str, object] | Exception
) -> tuple[AnthropicClient, _FakeSdk]:
    fake = _FakeSdk(outcome)
    client = AnthropicClient(_key_file(tmp_path), _CAPS, sdk_client=fake)  # type: ignore[arg-type]
    return client, fake


def _request(
    parts: list[TextPart | ImagePart] | None = None,
    json_schema: dict[str, object] | None = None,
) -> LlmRequest:
    content = list(parts) if parts is not None else [TextPart("ola")]
    return LlmRequest(
        model_id="claude-haiku-5-5",
        system="sistema",
        messages=[Message(role="user", content=list(content))],
        max_output_tokens=321,
        json_schema=json_schema,
    )


def _httpx_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _connection_error(cause: Exception) -> anthropic.APIConnectionError:
    error = anthropic.APIConnectionError(request=_httpx_request())
    error.__cause__ = cause
    return error


def _timeout_error(cause: Exception) -> anthropic.APITimeoutError:
    error = anthropic.APITimeoutError(request=_httpx_request())
    error.__cause__ = cause
    return error


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    response = httpx2.Response(status, request=_httpx_request())
    return cls("erro de teste", response=response, body=None)


# --- to_llm_response ---------------------------------------------------------------------


def test_single_text_block_mapped() -> None:
    response = to_llm_response(_raw("single_text"))
    assert response.text == "Ola, mundo."
    assert response.provider_request_id == "msg_01SingleText"
    assert response.resolved_model == "claude-haiku-5-5"
    assert response.stop_reason is StopReason.end


def test_multiple_text_blocks_joined() -> None:
    response = to_llm_response(_raw("multi_text_thinking"))
    # Os blocos `thinking` sao ignorados; os de texto juntam-se por ordem, sem separador.
    assert response.text == "Primeira parte. Segunda parte."


def test_usage_mapped() -> None:
    response = to_llm_response(_raw("usage_cache"))
    # Entrada = input + criacao de cache + leitura de cache; `output_tokens` ja inclui o raciocinio.
    assert response.usage == Usage(input_tokens=3210, output_tokens=7, reasoning_tokens=None)


def test_usage_without_cache_fields() -> None:
    assert to_llm_response(_raw("single_text")).usage == Usage(input_tokens=12, output_tokens=5)


def test_usage_cache_fields_null_count_as_zero() -> None:
    raw = _raw("single_text")
    raw["usage"] = {"input_tokens": 4, "output_tokens": 2, "cache_read_input_tokens": None}
    assert to_llm_response(raw).usage == Usage(input_tokens=4, output_tokens=2)


def test_refusal_mapped() -> None:
    response = to_llm_response(_raw("refusal"))
    assert response.stop_reason is StopReason.refusal
    assert response.text == ""


def test_max_tokens_mapped() -> None:
    response = to_llm_response(_raw("max_tokens"))
    assert response.stop_reason is StopReason.max_tokens
    assert response.text == "Texto cortado a meio da fra"


@pytest.mark.parametrize("reason", ["stop_sequence", "tool_use", "pause_turn", "nova", None])
def test_other_stop_reasons_map_to_other(reason: str | None) -> None:
    raw = _raw("single_text")
    raw["stop_reason"] = reason
    assert to_llm_response(raw).stop_reason is StopReason.other


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content", "texto"),
        ("content", [1]),
        ("usage", None),
        ("usage", {"input_tokens": "1", "output_tokens": 1}),
        ("stop_reason", 3),
        ("id", 3),
        ("model", None),
    ],
)
def test_invalid_raw_names_the_field(field: str, value: object) -> None:
    raw = _raw("single_text")
    raw[field] = value
    with pytest.raises(ValueError, match=field):
        to_llm_response(raw)


def test_missing_field_names_the_field() -> None:
    raw = _raw("single_text")
    del raw["usage"]
    with pytest.raises(ValueError, match="usage"):
        to_llm_response(raw)


# --- classify_transport_error ------------------------------------------------------------


def test_connect_error_is_network_error() -> None:
    error = _connection_error(httpx2.ConnectError("recusada"))
    assert classify_transport_error(error) is LlmNetworkError


def test_connect_timeout_is_network_error() -> None:
    error = _timeout_error(httpx2.ConnectTimeout("lento"))
    assert classify_transport_error(error) is LlmNetworkError


def test_read_timeout_is_unknown_outcome() -> None:
    error = _timeout_error(httpx2.ReadTimeout("sem resposta"))
    assert classify_transport_error(error) is LlmUnknownOutcome


def test_write_timeout_is_unknown_outcome() -> None:
    assert classify_transport_error(_timeout_error(httpx2.WriteTimeout("x"))) is LlmUnknownOutcome


def test_dropped_connection_is_unknown_outcome() -> None:
    error = _connection_error(httpx2.RemoteProtocolError("cortada"))
    assert classify_transport_error(error) is LlmUnknownOutcome


def test_connection_error_without_known_cause_is_unknown_outcome() -> None:
    assert classify_transport_error(_connection_error(OSError("x"))) is LlmUnknownOutcome


@pytest.mark.parametrize(
    ("cls", "status"),
    [
        (anthropic.RateLimitError, 429),
        (anthropic.ServiceUnavailableError, 503),
        (anthropic.OverloadedError, 529),
    ],
)
def test_rejected_statuses_are_network_error(
    cls: type[anthropic.APIStatusError], status: int
) -> None:
    assert classify_transport_error(_status_error(cls, status)) is LlmNetworkError


@pytest.mark.parametrize(
    ("cls", "status"),
    [
        (anthropic.InternalServerError, 500),
        (anthropic.InternalServerError, 502),
        (anthropic.GatewayTimeoutError, 504),
    ],
)
def test_other_5xx_are_unknown_outcome(cls: type[anthropic.APIStatusError], status: int) -> None:
    assert classify_transport_error(_status_error(cls, status)) is LlmUnknownOutcome


@pytest.mark.parametrize(
    ("cls", "status"),
    [
        (anthropic.BadRequestError, 400),
        (anthropic.AuthenticationError, 401),
        (anthropic.PermissionDeniedError, 403),
        (anthropic.NotFoundError, 404),
    ],
)
def test_other_4xx_are_not_classified(cls: type[anthropic.APIStatusError], status: int) -> None:
    assert classify_transport_error(_status_error(cls, status)) is None


def test_unrelated_exception_is_not_classified() -> None:
    assert classify_transport_error(RuntimeError("x")) is None


# --- AnthropicClient -----------------------------------------------------------------------


def test_key_read_from_file(tmp_path: Path) -> None:
    client = AnthropicClient(_key_file(tmp_path), _CAPS)
    assert client._sdk.api_key == _SEGREDO
    assert client._sdk.max_retries == 0
    assert client.capabilities == _CAPS


def test_base_url_passed_to_sdk(tmp_path: Path) -> None:
    client = AnthropicClient(_key_file(tmp_path), _CAPS, base_url="http://127.0.0.1:11434")
    assert str(client._sdk.base_url).startswith("http://127.0.0.1:11434")


@pytest.mark.parametrize("content", [None, "  \n"])
def test_missing_or_empty_key_file_raises_without_leaking(
    tmp_path: Path, content: str | None
) -> None:
    path = tmp_path / "k.key"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(SecretFileError) as info:
        AnthropicClient(path, _CAPS)
    assert _SEGREDO not in str(info.value)


def test_complete_maps_response(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, _raw("single_text"))
    assert client.complete(_request()).text == "Ola, mundo."


def test_complete_builds_sdk_call(tmp_path: Path) -> None:
    client, fake = _client(tmp_path, _raw("single_text"))
    client.complete(_request())
    (call,) = fake.messages.calls
    assert call["model"] == "claude-haiku-5-5"
    assert call["system"] == "sistema"
    assert call["max_tokens"] == 321
    assert call["messages"] == [{"role": "user", "content": [{"type": "text", "text": "ola"}]}]
    # Estes modelos rejeitam sampling/prefill e nao deixam desligar o raciocinio.
    for forbidden in ("temperature", "top_p", "top_k", "thinking"):
        assert forbidden not in call
    assert call["output_config"] is anthropic.omit


def test_complete_sends_images_as_base64(tmp_path: Path) -> None:
    client, fake = _client(tmp_path, _raw("single_text"))
    image = ImagePart(media_type="image/png", data=b"\x89PNG-bytes")
    client.complete(_request([TextPart("olha"), image]))
    content = fake.messages.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert content[1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(b"\x89PNG-bytes").decode("ascii"),
        },
    }


def test_complete_never_sends_empty_text_block(tmp_path: Path) -> None:
    client, fake = _client(tmp_path, _raw("single_text"))
    client.complete(_request([TextPart("")]))
    content = fake.messages.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert content == [{"type": "text", "text": "(sem resposta)"}]


def test_complete_passes_json_schema_as_output_format(tmp_path: Path) -> None:
    client, fake = _client(tmp_path, _raw("single_text"))
    schema: dict[str, object] = {"type": "object", "properties": {"a": {"type": "string"}}}
    client.complete(_request(json_schema=schema))
    assert fake.messages.calls[0]["output_config"] == {
        "format": {"type": "json_schema", "schema": schema}
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_connection_error(httpx2.ConnectError("x")), LlmNetworkError),
        (_timeout_error(httpx2.ReadTimeout("x")), LlmUnknownOutcome),
        (_status_error(anthropic.RateLimitError, 429), LlmNetworkError),
        (_status_error(anthropic.InternalServerError, 500), LlmUnknownOutcome),
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
    error = _status_error(anthropic.AuthenticationError, 401)
    client, _ = _client(tmp_path, error)
    with pytest.raises(anthropic.AuthenticationError) as info:
        client.complete(_request())
    assert info.value is error


# --- chamada real (so a mao, com ANTHROPIC_API_KEY_FILE) -------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    "ANTHROPIC_API_KEY_FILE" not in os.environ, reason="ANTHROPIC_API_KEY_FILE nao definido"
)
def test_live_call() -> None:
    # Configuravel por ambiente para poder correr contra um emulador compativel (ex.: Ollama).
    client = AnthropicClient(
        Path(os.environ["ANTHROPIC_API_KEY_FILE"]),
        _CAPS,
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )
    request = LlmRequest(
        model_id=os.environ.get("ANTHROPIC_LIVE_MODEL", "claude-haiku-5-5"),
        system="Responde em portugues, numa frase curta.",
        messages=[Message(role="user", content=[TextPart("Diz ola.")])],
        max_output_tokens=200,
    )
    response = client.complete(request)
    assert response.text.strip()
    assert response.usage.output_tokens > 0
    assert response.stop_reason is StopReason.end
