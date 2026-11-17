"""Gerador falso: gradiente determinista a partir do sha256 do prompt, sem id de pedido."""

import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from marketing_agent.images.generation import (
    FAKE_IMAGE_MODEL,
    FakeImageGenerator,
    ImageGenerator,
    ImageRequest,
    ImageResult,
)
from marketing_agent.images.layouts import gradient_background
from marketing_agent.llm.types import LlmNetworkError, LlmUnknownOutcome


def _request(prompt: str = "um prompt", quality: str = "medium") -> ImageRequest:
    return ImageRequest(
        model_id=FAKE_IMAGE_MODEL,
        prompt=prompt,
        size="1024x1536",
        quality=quality,  # type: ignore[arg-type]
    )


def test_fake_generator_deterministic_from_prompt_sha() -> None:
    generator = FakeImageGenerator()
    first = generator.generate(_request("alfa"))
    again = generator.generate(_request("alfa"))
    other = generator.generate(_request("omega"))
    expected = gradient_background(hashlib.sha256(b"alfa").digest())
    assert first.data == again.data == expected
    assert other.data != first.data
    assert FakeImageGenerator().generate(_request("alfa")).data == expected


def test_fake_generator_has_no_request_id() -> None:
    result = FakeImageGenerator().generate(_request())
    assert result.provider_request_id is None


def test_fake_result_echoes_request() -> None:
    result = FakeImageGenerator().generate(_request(quality="high"))
    assert isinstance(result, ImageResult)
    assert (result.resolved_model, result.size, result.quality) == (
        FAKE_IMAGE_MODEL,
        "1024x1536",
        "high",
    )
    assert result.data.startswith(b"\x89PNG\r\n\x1a\n")


def test_fake_generator_is_an_image_generator() -> None:
    generator: ImageGenerator = FakeImageGenerator()
    assert generator.provider == "fake"
    assert FAKE_IMAGE_MODEL == "fake-image-1"


def test_fake_records_requests_in_order() -> None:
    generator = FakeImageGenerator()
    generator.generate(_request("a"))
    generator.generate(_request("b"))
    assert [r.prompt for r in generator.requests] == ["a", "b"]


def test_fake_raises_scripted_failures_in_order_then_succeeds() -> None:
    network, unknown = LlmNetworkError("x"), LlmUnknownOutcome("y")
    generator = FakeImageGenerator(failures=[network, unknown])
    with pytest.raises(LlmNetworkError):
        generator.generate(_request("a"))
    with pytest.raises(LlmUnknownOutcome):
        generator.generate(_request("b"))
    assert generator.generate(_request("c")).data
    assert len(generator.requests) == 3


def test_fake_failure_script_is_consumed_exactly_once_under_threads() -> None:
    generator = FakeImageGenerator(failures=[LlmNetworkError("x")] * 5)

    def call(_: int) -> bool:
        try:
            generator.generate(_request())
        except LlmNetworkError:
            return True
        return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        failed = list(pool.map(call, range(20)))
    assert sum(failed) == 5
    assert len(generator.requests) == 20


def test_requests_are_frozen() -> None:
    request = _request()
    with pytest.raises(AttributeError):
        request.prompt = "outro"  # type: ignore[misc]
