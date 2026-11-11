"""Gerador OpenAI com SDK falso: parametros reais, erros de transporte e resposta invalida.

Nunca ha rede nem chave real: o SDK e um objeto falso que regista as chamadas.
"""

import base64
import traceback
from pathlib import Path
from types import SimpleNamespace

import httpx2
import openai
import pytest

from marketing_agent.images.generation import (
    MAX_IMAGE_BYTES,
    ImageRefused,
    ImageRequest,
    ImageResponseInvalid,
    ImageResult,
)
from marketing_agent.images.openai_images import OFFICIAL_BASE_URL, OpenAiImageGenerator
from marketing_agent.llm.types import LlmNetworkError, LlmUnknownOutcome
from marketing_agent.secrets import SecretFileError

_SEGREDO = "sk-imagens-segredo-de-teste"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_BYTES = PNG_SIGNATURE + b"corpo-de-teste"


class _FakeRaw:
    def __init__(self, parsed: object, headers: dict[str, str]) -> None:
        self._parsed = parsed
        self.headers = httpx2.Headers(headers)

    def parse(self) -> object:
        if isinstance(self._parsed, Exception):
            raise self._parsed
        return self._parsed


class _FakeRawResponseApi:
    def __init__(self, outcome: _FakeRaw | Exception) -> None:
        self._outcome = outcome
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> _FakeRaw:
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _FakeImages:
    def __init__(self, outcome: _FakeRaw | Exception) -> None:
        self.with_raw_response = _FakeRawResponseApi(outcome)


class _FakeSdk:
    def __init__(self, outcome: _FakeRaw | Exception) -> None:
        self.images = _FakeImages(outcome)


def _body(b64: str | None, size: str | None = None, quality: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        data=[SimpleNamespace(b64_json=b64)], size=size, quality=quality, created=1
    )


def _ok(png: bytes = PNG_BYTES, **kwargs: str | None) -> _FakeRaw:
    encoded = base64.b64encode(png).decode("ascii")
    return _FakeRaw(_body(encoded, **kwargs), {"x-request-id": "req_123"})


def _key_file(tmp_path: Path) -> Path:
    path = tmp_path / "images.key"
    path.write_text(_SEGREDO + "\n", encoding="utf-8")
    return path


def _generator(
    tmp_path: Path, outcome: _FakeRaw | Exception
) -> tuple[OpenAiImageGenerator, _FakeSdk]:
    sdk = _FakeSdk(outcome)
    generator = OpenAiImageGenerator(_key_file(tmp_path), sdk_client=sdk)  # type: ignore[arg-type]
    return generator, sdk


def _request() -> ImageRequest:
    return ImageRequest(
        model_id="modelo-da-rota", prompt="um prompt", size="1024x1536", quality="medium"
    )


def _httpx_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.openai.com/v1/images/generations")


def _connection_error(cause: Exception) -> openai.APIConnectionError:
    error = openai.APIConnectionError(request=_httpx_request())
    error.__cause__ = cause
    return error


def _timeout_error(cause: Exception) -> openai.APITimeoutError:
    error = openai.APITimeoutError(request=_httpx_request())
    error.__cause__ = cause
    return error


def _status_error(
    cls: type[openai.APIStatusError], status: int, body: object = None
) -> openai.APIStatusError:
    response = httpx2.Response(status, request=_httpx_request())
    return cls("texto-do-sdk", response=response, body=body)


def test_generate_uses_route_model_and_real_params(tmp_path: Path) -> None:
    generator, sdk = _generator(tmp_path, _ok())
    result = generator.generate(_request())
    assert sdk.images.with_raw_response.calls == [
        {
            "model": "modelo-da-rota",
            "prompt": "um prompt",
            "size": "1024x1536",
            "quality": "medium",
            "n": 1,
            "output_format": "png",
            "background": "opaque",
            "moderation": "auto",
        }
    ]
    assert isinstance(result, ImageResult)
    assert result.data == PNG_BYTES
    assert (result.resolved_model, result.size, result.quality) == (
        "modelo-da-rota",
        "1024x1536",
        "medium",
    )
    assert generator.provider == "openai"


def test_response_size_and_quality_replace_the_requested_ones(tmp_path: Path) -> None:
    generator, _ = _generator(tmp_path, _ok(size="1024x1024", quality="high"))
    result = generator.generate(_request())
    assert (result.size, result.quality) == ("1024x1024", "high")


def test_request_id_from_raw_headers(tmp_path: Path) -> None:
    generator, _ = _generator(tmp_path, _ok())
    assert generator.generate(_request()).provider_request_id == "req_123"
    no_header = _FakeRaw(_body(base64.b64encode(PNG_BYTES).decode()), {})
    generator, _ = _generator(tmp_path, no_header)
    assert generator.generate(_request()).provider_request_id is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_connection_error(httpx2.ConnectError("recusada")), LlmNetworkError),
        (_timeout_error(httpx2.ConnectTimeout("lento")), LlmNetworkError),
        (_status_error(openai.RateLimitError, 429), LlmNetworkError),
    ],
)
def test_connect_error_is_network(
    tmp_path: Path, error: Exception, expected: type[Exception]
) -> None:
    generator, _ = _generator(tmp_path, error)
    with pytest.raises(expected) as info:
        generator.generate(_request())
    assert type(info.value) is expected
    assert str(info.value).endswith(type(error).__name__)


@pytest.mark.parametrize(
    "error",
    [
        _timeout_error(httpx2.ReadTimeout("sem resposta")),
        _timeout_error(httpx2.WriteTimeout("x")),
        _connection_error(httpx2.RemoteProtocolError("cortada")),
        _status_error(openai.InternalServerError, 500),
        _status_error(openai.InternalServerError, 502),
    ],
)
def test_read_timeout_is_unknown_outcome(tmp_path: Path, error: Exception) -> None:
    generator, _ = _generator(tmp_path, error)
    with pytest.raises(LlmUnknownOutcome) as info:
        generator.generate(_request())
    assert str(info.value).endswith(type(error).__name__)
    assert _SEGREDO not in str(info.value)


def test_moderation_blocked_is_refused(tmp_path: Path) -> None:
    error = _status_error(openai.BadRequestError, 400, {"code": "moderation_blocked"})
    generator, _ = _generator(tmp_path, error)
    with pytest.raises(ImageRefused) as info:
        generator.generate(_request())
    assert "texto-do-sdk" not in str(info.value)
    assert "um prompt" not in str(info.value)


@pytest.mark.parametrize(
    ("cls", "status", "body"),
    [
        (openai.BadRequestError, 400, {"code": "invalid_value"}),
        (openai.BadRequestError, 400, None),
        (openai.AuthenticationError, 401, None),
        (openai.PermissionDeniedError, 403, None),
        (openai.NotFoundError, 404, None),
        (openai.UnprocessableEntityError, 422, None),
    ],
)
def test_other_4xx_propagate_unchanged(
    tmp_path: Path, cls: type[openai.APIStatusError], status: int, body: object
) -> None:
    error = _status_error(cls, status, body)
    generator, _ = _generator(tmp_path, error)
    with pytest.raises(cls) as info:
        generator.generate(_request())
    assert info.value is error


def test_unrelated_exception_propagates_unchanged(tmp_path: Path) -> None:
    error = RuntimeError("falha local")
    generator, _ = _generator(tmp_path, error)
    with pytest.raises(RuntimeError) as info:
        generator.generate(_request())
    assert info.value is error


@pytest.mark.parametrize(
    "body",
    [
        _body(None),
        _body(""),
        SimpleNamespace(data=None, size=None, quality=None),
        SimpleNamespace(data=[], size=None, quality=None),
    ],
)
def test_missing_b64_raises(tmp_path: Path, body: object) -> None:
    generator, _ = _generator(tmp_path, _FakeRaw(body, {"x-request-id": "req_1"}))
    with pytest.raises(ImageResponseInvalid):
        generator.generate(_request())


def test_unparseable_body_is_invalid_response_not_unknown_outcome(tmp_path: Path) -> None:
    generator, _ = _generator(tmp_path, _FakeRaw(ValueError("json quebrado"), {}))
    with pytest.raises(ImageResponseInvalid) as info:
        generator.generate(_request())
    assert not isinstance(info.value, LlmUnknownOutcome)


@pytest.mark.parametrize("b64", ["isto nao e base64!!", "AAA", "====", "QUJD\nREVG"])
def test_invalid_base64_is_rejected(tmp_path: Path, b64: str) -> None:
    generator, _ = _generator(tmp_path, _FakeRaw(_body(b64), {}))
    with pytest.raises(ImageResponseInvalid):
        generator.generate(_request())


def test_non_png_bytes_are_rejected(tmp_path: Path) -> None:
    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 50
    generator, _ = _generator(tmp_path, _ok(jpeg))
    with pytest.raises(ImageResponseInvalid, match="PNG"):
        generator.generate(_request())


def test_image_at_the_limit_is_accepted_and_above_it_rejected(tmp_path: Path) -> None:
    at_limit = PNG_SIGNATURE + b"x" * (MAX_IMAGE_BYTES - len(PNG_SIGNATURE))
    generator, _ = _generator(tmp_path, _ok(at_limit))
    assert len(generator.generate(_request()).data) == MAX_IMAGE_BYTES
    generator, _ = _generator(tmp_path, _ok(at_limit + b"x"))
    with pytest.raises(ImageResponseInvalid, match="limite"):
        generator.generate(_request())


def test_oversized_base64_is_rejected_before_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("nao deve descodificar")

    monkeypatch.setattr(base64, "b64decode", explode)
    huge = "A" * (MAX_IMAGE_BYTES * 4 // 3 + 100)
    generator, _ = _generator(tmp_path, _FakeRaw(_body(huge), {}))
    with pytest.raises(ImageResponseInvalid, match="limite"):
        generator.generate(_request())


def test_invalid_response_is_not_a_transport_error() -> None:
    assert not issubclass(ImageResponseInvalid, (LlmNetworkError, LlmUnknownOutcome))
    assert not issubclass(ImageRefused, (LlmNetworkError, LlmUnknownOutcome))


def _all_failures() -> list[object]:
    return [
        _connection_error(httpx2.ConnectError("x")),
        _timeout_error(httpx2.ReadTimeout("x")),
        _status_error(openai.BadRequestError, 400, {"code": "moderation_blocked"}),
        _FakeRaw(_body(None), {}),
        _FakeRaw(_body("nao-e-base64!"), {}),
        _ok(b"\xff\xd8not-a-png"),
    ]


def test_key_never_in_errors(tmp_path: Path) -> None:
    for outcome in _all_failures():
        generator, _ = _generator(tmp_path, outcome)  # type: ignore[arg-type]
        with pytest.raises(Exception) as info:
            generator.generate(_request())
        # Cadeia completa (causa incluida), repr da excecao e do gerador: a chave nunca aparece.
        rendered = "".join(traceback.format_exception(info.value))
        for text in (rendered, str(info.value), repr(info.value), repr(generator)):
            assert _SEGREDO not in text
        assert _SEGREDO not in repr(vars(generator))


def test_our_error_messages_never_carry_sdk_text(tmp_path: Path) -> None:
    error = _status_error(openai.InternalServerError, 500)
    generator, _ = _generator(tmp_path, error)
    with pytest.raises(LlmUnknownOutcome) as info:
        generator.generate(_request())
    assert "texto-do-sdk" not in str(info.value)
    assert "texto-do-sdk" not in repr(info.value)


def test_missing_or_empty_key_file_error_names_path_not_content(tmp_path: Path) -> None:
    empty = tmp_path / "vazio.key"
    empty.write_text("  \n", encoding="utf-8")
    for path in (tmp_path / "nao-existe.key", empty):
        with pytest.raises(SecretFileError) as info:
            OpenAiImageGenerator(path, sdk_client=_FakeSdk(_ok()))  # type: ignore[arg-type]
        assert _SEGREDO not in str(info.value)


def test_real_sdk_uses_official_address_no_retries_and_given_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # OPENAI_BASE_URL (ex.: Ollama para o texto) nunca pode redirecionar a chave das imagens.
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    generator = OpenAiImageGenerator(_key_file(tmp_path), timeout_s=42.0)
    sdk = generator._sdk  # noqa: SLF001 - verificar a configuracao do SDK real
    assert str(sdk.base_url) == OFFICIAL_BASE_URL + "/"
    assert OFFICIAL_BASE_URL == "https://api.openai.com/v1"
    assert sdk.max_retries == 0
    assert sdk.timeout == 42.0
    assert _SEGREDO not in repr(generator)


def test_default_timeout_is_180_seconds(tmp_path: Path) -> None:
    generator = OpenAiImageGenerator(_key_file(tmp_path))
    assert generator._sdk.timeout == 180.0  # noqa: SLF001
