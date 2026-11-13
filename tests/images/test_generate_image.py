"""`RunContext.generate_image`: reserva e acerto por imagem, quota por pedido e limite de custo."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier

from image_engine_rig import (
    SIZE,
    ImageRig,
    make_image_rig,
    make_prompt,
    make_spec,
)
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.budget.controller import RunCostLimitExceeded
from marketing_agent.clock import FixedClock
from marketing_agent.config import IMAGE_ROUTE, RoleRoute, Settings
from marketing_agent.harness.engine import RunContext, RunStatus
from marketing_agent.harness.image_calls import GeneratedImage, ImageAllowance, ImageLimitExceeded
from marketing_agent.images.factory import build_image_backend
from marketing_agent.images.generation import (
    FakeImageGenerator,
    ImageQuality,
    ImageRequest,
    ImageResult,
)
from marketing_agent.llm.types import LlmNetworkError

Factory = sessionmaker[Session]


def generate(ctx: RunContext, quality: ImageQuality = "medium", count: int = 1) -> object:
    return [ctx.generate_image(make_prompt(f"tema {i}"), SIZE, quality) for i in range(count)]


def assert_steps_closed(rig: ImageRig) -> None:
    """Todo passo termina com uma classe de erro ou com o resumo de saida."""
    assert all(step.error_class or step.output_ref for step in rig.steps())


def test_generate_image_reserves_and_settles_per_image(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    rig = make_image_rig(session_factory, clock, brand_id, FakeImageGenerator(), price="0.04")
    prompt = make_prompt()
    result = rig.run(lambda ctx: ctx.generate_image(prompt, SIZE, "medium"), ImageAllowance(2))
    assert result.status is RunStatus.SUCCEEDED
    [step] = rig.steps()
    assert (step.kind, step.name, step.error_class) == ("tool", "generate_image", None)
    assert step.input_summary == f"tamanho {SIZE}, qualidade medium"
    assert step.output_ref == f"prompt_sha256={prompt.sha256}"
    [reservation] = rig.reservations()
    assert (reservation.status, reservation.step_id) == ("settled", step.id)
    [entry] = rig.entries()
    assert entry.units == {"images": 1, "size": "1024x1536", "quality": "medium"}
    assert (entry.provider, entry.model_id, entry.step_id) == ("fake", "fake-image-1", step.id)
    assert entry.provider_request_id is None
    assert entry.cost_usd == Decimal("0.04")
    image = result.output
    assert isinstance(image, GeneratedImage)
    assert (image.step_id, image.provider, image.model_id) == (step.id, "fake", "fake-image-1")
    assert (image.size, image.quality, image.media_type) == (SIZE, "medium", "image/png")
    assert (image.cost_usd, image.prompt_sha256) == (Decimal("0.04"), prompt.sha256)
    assert image.data.startswith(b"\x89PNG")
    assert rig.run_row(result.run_id).cost_usd == Decimal("0.04")
    assert prompt.text not in (step.input_summary or "") + (step.output_ref or "")
    assert_steps_closed(rig)


def test_generate_image_uses_designer_imagem_model(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    key = tmp_path / "images.key"
    key.write_text("sk-chave-de-teste", encoding="utf-8")
    route = RoleRoute.model_validate(
        {"provider": "openai", "model_id": "modelo-da-rota", "capabilities": {"image_output"}}
    )
    settings = Settings(
        _env_file=None,
        database_url="postgresql://x",
        role_routes={IMAGE_ROUTE: route},
        openai_images_api_key_file=key,
    )  # type: ignore[arg-type]
    backend = build_image_backend(settings)
    generator = FakeImageGenerator()
    rig = make_image_rig(
        session_factory,
        clock,
        brand_id,
        generator,
        price="0.04",
        provider=backend.provider,
        model_id=backend.model_id,
    )
    result = rig.run(lambda ctx: generate(ctx), ImageAllowance(2))
    assert result.status is RunStatus.SUCCEEDED
    [request] = generator.requests
    assert request.model_id == "modelo-da-rota"
    [entry] = rig.entries()
    assert (entry.provider, entry.model_id) == ("openai", "modelo-da-rota")
    [reservation] = rig.reservations()
    assert (reservation.provider, reservation.model_id) == ("openai", "modelo-da-rota")


def test_generate_image_request_is_built_from_the_fixed_prompt(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator()
    rig = make_image_rig(session_factory, clock, brand_id, generator)
    prompt = make_prompt()
    rig.run(lambda ctx: ctx.generate_image(prompt, SIZE, "high"), ImageAllowance(2))
    [request] = generator.requests
    assert (request.prompt, request.size, request.quality) == (prompt.text, SIZE, "high")


def test_network_error_releases_and_retries_with_backoff(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    failures = [LlmNetworkError("sem rede"), LlmNetworkError("sem rede")]
    generator = FakeImageGenerator(failures)
    rig = make_image_rig(session_factory, clock, brand_id, generator, price="0.04")
    allowance = ImageAllowance(2)
    result = rig.run(lambda ctx: generate(ctx), allowance)
    assert result.status is RunStatus.SUCCEEDED
    assert rig.sleeps == [1.0, 2.0]
    assert [step.error_class for step in rig.steps()] == ["network", "network", None]
    assert [step.seq for step in rig.steps()] == [1, 2, 3]
    reservations = rig.reservations()
    assert [r.status for r in reservations] == ["released", "released", "settled"]
    assert len({r.id for r in reservations}) == 3
    assert len(rig.entries()) == 1
    assert allowance.remaining == 1
    assert_steps_closed(rig)


def test_network_error_exhausted_needs_human_and_refunds_the_slot(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator([LlmNetworkError("a")] * 3)
    rig = make_image_rig(session_factory, clock, brand_id, generator, price="0.04")
    allowance = ImageAllowance(2)
    result = rig.run(lambda ctx: generate(ctx), allowance)
    assert result.status is RunStatus.NEEDS_HUMAN
    assert result.message == "rede indisponivel apos 3 tentativas"
    assert len(generator.requests) == 3
    assert rig.sleeps == [1.0, 2.0]
    assert [step.error_class for step in rig.steps()] == ["network"] * 3
    assert [r.status for r in rig.reservations()] == ["released"] * 3
    assert rig.entries() == []
    assert allowance.remaining == 2
    assert_steps_closed(rig)


def test_third_image_in_same_request_blocked(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator()
    rig = make_image_rig(session_factory, clock, brand_id, generator, price="0.04")
    allowance = ImageAllowance(2)
    first = rig.run(lambda ctx: generate(ctx), allowance)
    second = rig.run(lambda ctx: generate(ctx), allowance)
    third = rig.run(lambda ctx: generate(ctx), allowance)
    assert (first.status, second.status) == (RunStatus.SUCCEEDED, RunStatus.SUCCEEDED)
    assert third.status is RunStatus.BLOCKED_LIMIT
    assert third.limit is ImageLimitExceeded
    assert len(generator.requests) == 2
    assert len(rig.entries()) == 2
    assert allowance.remaining == 0
    assert [step.run_id for step in rig.steps()] == [first.run_id, second.run_id]
    assert rig.run_row(third.run_id).status == "blocked_limit"


def test_generation_over_run_cost_limit_blocked_without_call(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator()
    rig = make_image_rig(session_factory, clock, brand_id, generator, price="0.60")
    allowance = ImageAllowance(2)
    result = rig.run(lambda ctx: generate(ctx), allowance, make_spec(max_cost_usd="0.50"))
    assert result.status is RunStatus.BLOCKED_LIMIT
    assert result.limit is RunCostLimitExceeded
    assert generator.requests == []
    assert rig.entries() == []
    assert rig.reservations() == []
    assert [step.error_class for step in rig.steps()] == ["limit"]
    assert allowance.remaining == 2


def test_run_without_allowance_cannot_generate(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator()
    rig = make_image_rig(session_factory, clock, brand_id, generator)
    result = rig.run(lambda ctx: generate(ctx), None)
    assert result.status is RunStatus.BLOCKED_LIMIT
    assert result.limit is ImageLimitExceeded
    assert generator.requests == []
    assert rig.steps() == []


def test_engine_without_image_backend_cannot_generate(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator()
    rig = make_image_rig(session_factory, clock, brand_id, generator, with_backend=False)
    allowance = ImageAllowance(2)
    result = rig.run(lambda ctx: generate(ctx), allowance)
    assert (result.status, result.limit) == (RunStatus.BLOCKED_LIMIT, ImageLimitExceeded)
    assert generator.requests == []
    assert allowance.remaining == 2


def test_step_limit_blocks_before_taking_a_slot(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator()
    rig = make_image_rig(session_factory, clock, brand_id, generator)
    allowance = ImageAllowance(2)
    result = rig.run(lambda ctx: generate(ctx, count=2), allowance, make_spec(max_steps=1))
    assert result.status is RunStatus.BLOCKED_LIMIT
    assert len(generator.requests) == 1
    assert allowance.remaining == 1


def test_two_fake_images_in_one_run_have_null_request_ids(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    rig = make_image_rig(session_factory, clock, brand_id, FakeImageGenerator(), price="0.02")
    result = rig.run(lambda ctx: generate(ctx, count=2), ImageAllowance(2))
    assert result.status is RunStatus.SUCCEEDED
    entries = rig.entries()
    assert len(entries) == 2
    assert [entry.provider_request_id for entry in entries] == [None, None]
    assert len({entry.step_id for entry in entries}) == 2
    assert rig.run_row(result.run_id).cost_usd == Decimal("0.04")
    assert_steps_closed(rig)


class LockLost(Exception):
    """Sinal de aborto dos testes (o motor nao conhece o tipo concreto)."""


def test_abort_check_stops_an_image_before_consuming_the_allowance_or_reserving(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    def abort() -> None:
        raise LockLost

    generator = FakeImageGenerator()
    rig = make_image_rig(
        session_factory, clock, brand_id, generator, price="0.04", abort_check=abort
    )
    allowance = ImageAllowance(2)
    result = rig.run(lambda ctx: generate(ctx), allowance)
    assert result.status is RunStatus.FAILED
    assert allowance.remaining == 2  # a quota nao foi tocada
    assert generator.requests == [] and rig.reservations() == [] and rig.steps() == []


def test_allowance_is_never_exceeded_by_concurrent_runs(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    generator = FakeImageGenerator()
    rig = make_image_rig(session_factory, clock, brand_id, generator, price="0.02")
    allowance = ImageAllowance(1)
    start = Barrier(4, timeout=10)

    def attempt() -> RunStatus:
        start.wait()
        return rig.run(lambda ctx: generate(ctx), allowance).status

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(attempt) for _ in range(4)]
        statuses = [future.result(timeout=60) for future in futures]
    assert sorted(statuses) == [RunStatus.BLOCKED_LIMIT] * 3 + [RunStatus.SUCCEEDED]
    assert len(generator.requests) == 1
    assert len(rig.entries()) == 1
    assert allowance.remaining == 0


def test_generator_is_not_called_inside_a_transaction(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    """A reserva ja esta confirmada (visivel a outra sessao) quando o gerador e chamado."""
    seen: list[list[str]] = []
    holder: dict[str, ImageRig] = {}

    class Probe(FakeImageGenerator):
        def generate(self, request: ImageRequest) -> ImageResult:
            seen.append([r.status for r in holder["rig"].reservations()])
            return super().generate(request)

    rig = make_image_rig(session_factory, clock, brand_id, Probe(), price="0.04")
    holder["rig"] = rig
    rig.run(lambda ctx: generate(ctx), ImageAllowance(2))
    assert seen == [["open"]]
