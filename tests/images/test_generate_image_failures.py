"""`generate_image`: mapeamento de falhas seguro para dinheiro, sem fuga de texto do fornecedor."""

import logging
import traceback
from dataclasses import replace
from io import BytesIO

import pytest
from image_engine_rig import (
    SIZE,
    ImageRig,
    ScriptedImageGenerator,
    make_image_rig,
    make_prompt,
)
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.budget.controller import BudgetController
from marketing_agent.clock import FixedClock
from marketing_agent.harness.engine import RunContext, RunStatus
from marketing_agent.harness.image_calls import (
    INVALID_IMAGE_MESSAGE,
    REFUSED_MESSAGE,
    SETTLE_FAILED_MESSAGE,
    ImageAllowance,
    ImageLimitExceeded,
)
from marketing_agent.harness.run_errors import NeedsHuman
from marketing_agent.images.generation import (
    FakeImageGenerator,
    ImageProviderError,
    ImageRefused,
    ImageResponseInvalid,
    ImageResult,
)
from marketing_agent.llm.types import LlmNetworkError, LlmUnknownOutcome

Factory = sessionmaker[Session]
HOSTILE = "SEGREDO-HOSTIL sk-abc123 Bearer xyz"


def generate(ctx: RunContext) -> object:
    return ctx.generate_image(make_prompt(), SIZE, "medium")


def png_result(width: int, height: int, **overrides: str) -> ImageResult:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, "PNG")
    base = ImageResult(buffer.getvalue(), None, "fake-image-1", SIZE, "medium")
    return replace(base, **overrides)


def everything_stored(rig: ImageRig, run_id: str) -> str:
    """Texto de todas as linhas que a execucao deixou na base."""
    run = rig.run_row(run_id)
    parts = [repr(vars(run))]
    parts += [repr(vars(step)) for step in rig.steps()]
    parts += [repr(vars(r)) for r in rig.reservations()]
    parts += [repr(vars(e)) for e in rig.entries()]
    return " ".join(parts)


def assert_steps_closed(rig: ImageRig) -> None:
    assert all(step.error_class or step.output_ref for step in rig.steps())


def test_refusal_releases_and_needs_human(
    session_factory: Factory, clock: FixedClock, brand_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    generator = FakeImageGenerator([ImageRefused(HOSTILE)])
    rig = make_image_rig(session_factory, clock, brand_id, generator, price="0.04")
    allowance = ImageAllowance(2)
    result = rig.run(generate, allowance)
    assert (result.status, result.message) == (RunStatus.NEEDS_HUMAN, REFUSED_MESSAGE)
    assert REFUSED_MESSAGE == "geração recusada pelo fornecedor"
    assert [step.error_class for step in rig.steps()] == ["refusal"]
    assert [r.status for r in rig.reservations()] == ["released"]
    assert rig.entries() == []
    assert allowance.remaining == 2
    assert len(generator.requests) == 1
    assert "SEGREDO" not in everything_stored(rig, result.run_id) + caplog.text
    assert_steps_closed(rig)


def test_unknown_outcome_marks_reservation_and_needs_human(
    session_factory: Factory, clock: FixedClock, brand_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    generator = FakeImageGenerator([LlmUnknownOutcome(HOSTILE)])
    rig = make_image_rig(session_factory, clock, brand_id, generator, price="0.04")
    allowance = ImageAllowance(2)
    with caplog.at_level(logging.DEBUG):
        result = rig.run(generate, allowance)
    assert result.status is RunStatus.NEEDS_HUMAN
    assert result.message is not None and "LlmUnknownOutcome" in result.message
    assert [step.error_class for step in rig.steps()] == ["unknown_outcome"]
    [reservation] = rig.reservations()
    assert reservation.status == "unknown_outcome"
    assert rig.entries() == []
    assert allowance.remaining == 1  # o fornecedor pode ter faturado: o lugar fica gasto
    assert len(generator.requests) == 1  # nunca repete um desfecho desconhecido
    assert "SEGREDO" not in (result.message or "") + everything_stored(rig, result.run_id)
    assert "SEGREDO" not in caplog.text
    assert_steps_closed(rig)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (ImageProviderError(HOSTILE, status_code=401), "ImageProviderError, HTTP 401"),
        (ImageProviderError(HOSTILE), "ImageProviderError"),
        (ImageResponseInvalid(HOSTILE), "ImageResponseInvalid"),
        (RuntimeError(HOSTILE), "RuntimeError"),
    ],
)
def test_other_generator_errors_are_unknown_without_leaking_text(
    session_factory: Factory,
    clock: FixedClock,
    brand_id: str,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
    expected: str,
) -> None:
    rig = make_image_rig(
        session_factory, clock, brand_id, FakeImageGenerator([failure]), price="0.04"
    )
    allowance = ImageAllowance(2)
    with caplog.at_level(logging.DEBUG):
        result = rig.run(generate, allowance)
    assert result.status is RunStatus.NEEDS_HUMAN
    assert result.message is not None and result.message.endswith(f"({expected})")
    assert [r.status for r in rig.reservations()] == ["unknown_outcome"]
    assert [step.error_class for step in rig.steps()] == ["unknown_outcome"]
    assert allowance.remaining == 1
    assert "SEGREDO" not in (result.message or "") + everything_stored(rig, result.run_id)
    assert "SEGREDO" not in caplog.text
    assert_steps_closed(rig)


def test_base_exception_from_generator_marks_unknown_and_propagates(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator([KeyboardInterrupt()])
    rig = make_image_rig(session_factory, clock, brand_id, generator, price="0.04")
    allowance = ImageAllowance(2)
    with pytest.raises(KeyboardInterrupt):
        rig.run(generate, allowance)
    assert [r.status for r in rig.reservations()] == ["unknown_outcome"]
    assert [step.error_class for step in rig.steps()] == ["unknown_outcome"]
    assert allowance.remaining == 1  # o fornecedor pode ter faturado: o lugar fica gasto


def test_settle_failure_leaves_reservation_unknown(
    session_factory: Factory,
    clock: FixedClock,
    brand_id: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError(HOSTILE)

    rig = make_image_rig(session_factory, clock, brand_id, FakeImageGenerator(), price="0.04")
    monkeypatch.setattr(BudgetController, "settle_image", broken)
    allowance = ImageAllowance(2)
    with caplog.at_level(logging.DEBUG):
        result = rig.run(generate, allowance)
    assert (result.status, result.message) == (RunStatus.NEEDS_HUMAN, SETTLE_FAILED_MESSAGE)
    assert [r.status for r in rig.reservations()] == ["unknown_outcome"]
    assert [step.error_class for step in rig.steps()] == ["unknown_outcome"]
    assert allowance.remaining == 1
    assert "SEGREDO" not in everything_stored(rig, result.run_id) + caplog.text
    assert_steps_closed(rig)


def test_settle_uses_the_reserved_pair_and_records_the_returned_one(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    returned = png_result(1024, 1536, size="1024x1024", quality="high")
    rig = make_image_rig(
        session_factory, clock, brand_id, ScriptedImageGenerator([returned]), price="0.04"
    )
    result = rig.run(generate, ImageAllowance(2))
    assert result.status is RunStatus.SUCCEEDED
    [entry] = rig.entries()
    assert entry.units == {"images": 1, "size": SIZE, "quality": "medium"}
    [step] = rig.steps()
    assert step.output_ref is not None
    assert step.output_ref.endswith("; fornecedor devolveu size=1024x1024 quality=high")
    image = result.output
    assert (image.size, image.quality) == (SIZE, "medium")


def test_returned_pair_with_hostile_text_is_not_recorded(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    returned = png_result(1024, 1536, size="<script>" + HOSTILE, quality="x" * 500)
    rig = make_image_rig(
        session_factory, clock, brand_id, ScriptedImageGenerator([returned]), price="0.04"
    )
    result = rig.run(generate, ImageAllowance(2))
    assert result.status is RunStatus.SUCCEEDED
    [step] = rig.steps()
    assert step.output_ref is not None
    assert step.output_ref.endswith("size=? quality=?")
    assert "SEGREDO" not in step.output_ref


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(ImageResult(b"nao e uma imagem", None, "m", SIZE, "medium"), id="not-image"),
        pytest.param(
            ImageResult(b"\x89PNG\r\n\x1a\n" + b"lixo", None, "m", SIZE, "medium"), id="truncated"
        ),
        pytest.param(png_result(1000, 1536), id="side-below-1024"),
        pytest.param(png_result(1024, 600), id="short-side"),
        pytest.param(
            ImageResult(b"\xff\xd8\xff" + b"0" * (21 * 1024 * 1024), None, "m", SIZE, "medium"),
            id="oversized",
        ),
    ],
)
def test_invalid_image_bytes_after_billing_needs_human_and_keeps_cost(
    session_factory: Factory, clock: FixedClock, brand_id: str, bad: ImageResult
) -> None:
    rig = make_image_rig(
        session_factory, clock, brand_id, ScriptedImageGenerator([bad]), price="0.04"
    )
    allowance = ImageAllowance(2)
    result = rig.run(generate, allowance)
    assert (result.status, result.message) == (RunStatus.NEEDS_HUMAN, INVALID_IMAGE_MESSAGE)
    [step] = rig.steps()
    assert step.error_class == "invalid_output"
    assert step.output_ref is not None and step.output_ref.startswith("prompt_sha256=")
    [reservation] = rig.reservations()
    assert reservation.status == "settled"
    [entry] = rig.entries()
    assert str(entry.cost_usd) == "0.040000"
    assert rig.run_row(result.run_id).cost_usd == entry.cost_usd
    assert allowance.remaining == 1
    assert_steps_closed(rig)


def test_pixel_bomb_is_rejected_after_billing(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    buffer = BytesIO()
    Image.new("1", (9000, 9000)).save(buffer, "PNG")  # 81 MP declarados, poucos bytes
    bomb = ImageResult(buffer.getvalue(), None, "m", SIZE, "medium")
    rig = make_image_rig(
        session_factory, clock, brand_id, ScriptedImageGenerator([bomb]), price="0.04"
    )
    result = rig.run(generate, ImageAllowance(2))
    assert result.message == INVALID_IMAGE_MESSAGE
    assert [step.error_class for step in rig.steps()] == ["invalid_output"]
    assert len(rig.entries()) == 1


def test_plain_string_prompt_is_rejected(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator()
    rig = make_image_rig(session_factory, clock, brand_id, generator)
    allowance = ImageAllowance(2)
    result = rig.run(lambda ctx: ctx.generate_image("texto livre", SIZE, "medium"), allowance)  # type: ignore[arg-type]
    assert result.status is RunStatus.FAILED
    assert result.message == "TypeError: generate_image exige um PromptFixo, nunca texto livre"
    assert generator.requests == [] and rig.steps() == []
    assert allowance.remaining == 2


def test_prompt_with_wrong_sha_is_rejected(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator()
    rig = make_image_rig(session_factory, clock, brand_id, generator)
    prompt = make_prompt()
    object.__setattr__(prompt, "sha256", "0" * 64)  # adulteracao posterior a construcao
    result = rig.run(lambda ctx: ctx.generate_image(prompt, SIZE, "medium"), ImageAllowance(2))
    assert result.status is RunStatus.FAILED
    assert generator.requests == [] and rig.steps() == []


@pytest.mark.parametrize(("size", "quality"), [("1x1", "medium"), (SIZE, "ultra")])
def test_unsupported_size_or_quality_is_rejected_before_any_step(
    session_factory: Factory, clock: FixedClock, brand_id: str, size: str, quality: str
) -> None:
    generator = FakeImageGenerator()
    rig = make_image_rig(session_factory, clock, brand_id, generator)
    result = rig.run(
        lambda ctx: ctx.generate_image(make_prompt(), size, quality),  # type: ignore[arg-type]
        ImageAllowance(2),
    )
    assert result.status is RunStatus.FAILED
    assert generator.requests == [] and rig.steps() == []


def test_missing_image_price_closes_step_and_returns_the_slot(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator()
    rig = make_image_rig(session_factory, clock, brand_id, generator, with_image_prices=False)
    allowance = ImageAllowance(2)
    result = rig.run(generate, allowance)
    assert result.status is RunStatus.FAILED
    assert generator.requests == []
    assert [step.error_class for step in rig.steps()] == ["limit"]
    assert rig.reservations() == [] and allowance.remaining == 2


def test_allowance_contract() -> None:
    allowance = ImageAllowance()
    assert allowance.remaining == 2
    allowance.consume()
    allowance.consume()
    with pytest.raises(ImageLimitExceeded):
        allowance.consume()
    allowance.refund()
    assert allowance.remaining == 1
    with pytest.raises(ValueError):
        ImageAllowance(-1)
    with pytest.raises(ValueError):
        ImageAllowance(1).refund()
    assert str(ImageLimitExceeded()) != ""  # `describe_stop` constroi-a sem argumentos


def png_bytes(width: int, height: int, image_format: str) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, image_format)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("width", "height"), [(1023, 1536), (1536, 1023), (1023, 1023), (1000, 4000)]
)
def test_shorter_side_1023_is_rejected(
    session_factory: Factory, clock: FixedClock, brand_id: str, width: int, height: int
) -> None:
    rig = make_image_rig(
        session_factory,
        clock,
        brand_id,
        ScriptedImageGenerator([png_result(width, height)]),
        price="0.04",
    )
    result = rig.run(generate, ImageAllowance(2))
    assert (result.status, result.message) == (RunStatus.NEEDS_HUMAN, INVALID_IMAGE_MESSAGE)
    assert [step.error_class for step in rig.steps()] == ["invalid_output"]
    assert len(rig.entries()) == 1  # o custo fica lancado


@pytest.mark.parametrize(("width", "height"), [(1024, 1024), (1024, 1536), (1536, 1024)])
def test_shorter_side_1024_is_accepted(
    session_factory: Factory, clock: FixedClock, brand_id: str, width: int, height: int
) -> None:
    rig = make_image_rig(
        session_factory,
        clock,
        brand_id,
        ScriptedImageGenerator([png_result(width, height)]),
        price="0.04",
    )
    assert rig.run(generate, ImageAllowance(2)).status is RunStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("image_format", "media_type"),
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_media_type_follows_the_decoded_format(
    session_factory: Factory, clock: FixedClock, brand_id: str, image_format: str, media_type: str
) -> None:
    data = ImageResult(png_bytes(1024, 1536, image_format), None, "m", SIZE, "medium")
    rig = make_image_rig(
        session_factory, clock, brand_id, ScriptedImageGenerator([data]), price="0.04"
    )
    result = rig.run(generate, ImageAllowance(2))
    assert result.status is RunStatus.SUCCEEDED
    assert result.output.media_type == media_type  # type: ignore[attr-defined]


def chain_text(error: BaseException) -> str:
    """str, repr e traceback completo de `error` e de toda a cadeia __cause__/__context__."""
    parts: list[str] = []
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        parts += [str(current), repr(current), "".join(traceback.format_exception(current))]
        pending += [current.__cause__, current.__context__]
    return " ".join(parts)


def capture(errors: list[BaseException], ctx: RunContext) -> object:
    """Corre a geracao e guarda a excecao tal como saiu, para inspecionar a cadeia."""
    try:
        return generate(ctx)
    except BaseException as exc:
        errors.append(exc)
        raise


@pytest.mark.parametrize("patched", ["release", "close_step"])
@pytest.mark.parametrize("failure", [ImageRefused(HOSTILE), LlmNetworkError(HOSTILE)])
def test_bookkeeping_failure_never_chains_the_provider_exception(
    session_factory: Factory,
    clock: FixedClock,
    brand_id: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    patched: str,
    failure: Exception,
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("falha de registo")

    rig = make_image_rig(
        session_factory, clock, brand_id, FakeImageGenerator([failure]), price="0.04"
    )
    target = BudgetController if patched == "release" else RunContext
    monkeypatch.setattr(target, "release" if patched == "release" else "_close_step", broken)
    errors: list[BaseException] = []
    with caplog.at_level(logging.DEBUG):
        result = rig.run(lambda ctx: capture(errors, ctx), ImageAllowance(2))
    [error] = errors
    assert isinstance(error, RuntimeError)
    assert "SEGREDO" not in chain_text(error)
    assert "SEGREDO" not in everything_stored(rig, result.run_id)
    assert any(record.exc_info for record in caplog.records)  # o traceback esta no registo
    assert "SEGREDO" not in caplog.text


@pytest.mark.parametrize(
    "failure", [ImageRefused(HOSTILE), RuntimeError(HOSTILE), LlmUnknownOutcome(HOSTILE)]
)
def test_needs_human_from_a_generator_failure_has_no_chain(
    session_factory: Factory, clock: FixedClock, brand_id: str, failure: Exception
) -> None:
    rig = make_image_rig(
        session_factory, clock, brand_id, FakeImageGenerator([failure]), price="0.04"
    )
    errors: list[BaseException] = []
    rig.run(lambda ctx: capture(errors, ctx), ImageAllowance(2))
    [error] = errors
    assert isinstance(error, NeedsHuman)
    assert error.__cause__ is None and error.__context__ is None
    assert "SEGREDO" not in chain_text(error)
