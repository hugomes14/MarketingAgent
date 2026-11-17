"""Pecas de arranque partilhadas: duracoes dos specs e a lease dos trabalhos de conteudo."""

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent import startup
from marketing_agent.budget.pricing import (
    ImagePrice,
    ModelPrice,
    PriceBook,
    PriceTier,
    sync_model_prices,
)
from marketing_agent.config import RoleRoute, Settings
from marketing_agent.content.specs import ContentSpecs, load_content_specs
from marketing_agent.harness.engine import NETWORK_BACKOFF_S
from marketing_agent.harness.image_calls import MAX_IMAGES_PER_REQUEST
from marketing_agent.images.openai_images import OpenAiImageGenerator
from marketing_agent.llm.anthropic_adapter import AnthropicClient
from marketing_agent.llm.openai_adapter import OpenAiClient
from marketing_agent.llm.types import IMAGE_CALL_TIMEOUT_S, LLM_CALL_TIMEOUT_S
from marketing_agent.startup import (
    StartupError,
    check_content_routes,
    content_job_lease,
    director_max_duration_s,
    max_spec_duration_s,
    orphan_grace,
    worker_stale_after,
)


def test_adapters_use_the_shared_call_timeout() -> None:
    for adapter in (AnthropicClient, OpenAiClient):
        default = inspect.signature(adapter).parameters["timeout_s"].default
        assert default == LLM_CALL_TIMEOUT_S == 120.0


def package_per_pass(specs: ContentSpecs) -> float:
    """Uma passagem do pacote no pior caso, componente a componente (ver `content_job_lease`)."""
    run_limits = sum(
        spec.limits.max_duration_s for spec in (specs.copywriter, specs.designer, specs.reviewer)
    )
    # O motor so verifica a duracao ANTES de cada passo: cada papel pode exceder o limite em
    # uma chamada (timeout do adaptador de texto).
    overrun = 3 * LLM_CALL_TIMEOUT_S
    # Cada geracao possivel pode ser tentada 1 + repeticoes de rede vezes, ate ao timeout de imagem.
    images = MAX_IMAGES_PER_REQUEST * (1 + len(NETWORK_BACKOFF_S)) * IMAGE_CALL_TIMEOUT_S
    return run_limits + overrun + images


def test_lease_covers_three_package_rounds() -> None:
    specs = load_content_specs()
    assert (specs.copywriter.limits.max_duration_s, specs.designer.limits.max_duration_s) == (
        180,
        300,
    )
    assert specs.reviewer.limits.max_duration_s == 180
    assert (MAX_IMAGES_PER_REQUEST, len(NETWORK_BACKOFF_S)) == (2, 2)
    expected = timedelta(seconds=3 * package_per_pass(specs) + 60)
    assert content_job_lease(time_multiplier=1) == expected
    # 3 x (180 + 300 + 180 + 3 x 120 + 2 x 3 x 180) + 60 = 6360 s
    assert content_job_lease(time_multiplier=1) == timedelta(seconds=6360)


def test_lease_grows_with_the_designer_and_with_each_image_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = load_content_specs()
    base = content_job_lease(time_multiplier=1)

    def with_designer_duration(seconds: int) -> ContentSpecs:
        limits = specs.designer.limits.model_copy(update={"max_duration_s": seconds})
        return replace(specs, designer=specs.designer.model_copy(update={"limits": limits}))

    monkeypatch.setattr(startup, "load_content_specs", lambda: with_designer_duration(400))
    assert content_job_lease(time_multiplier=1) == base + timedelta(
        seconds=3 * 100
    )  # as 3 passagens
    monkeypatch.setattr(startup, "MAX_IMAGES_PER_REQUEST", 3)
    assert content_job_lease(time_multiplier=1) > base + timedelta(seconds=3 * 100)
    # Sem o Designer na conta (a regra do 2A) a lease seria menor: nunca cobriria o pacote.
    assert base > timedelta(seconds=3 * (180 + 180 + 240) + 60)


def test_image_timeout_is_one_shared_constant_used_by_the_images_adapter() -> None:
    default = inspect.signature(OpenAiImageGenerator).parameters["timeout_s"].default
    assert default == IMAGE_CALL_TIMEOUT_S == 180.0 > LLM_CALL_TIMEOUT_S


def test_content_job_lease_is_longer_than_the_director_rule_it_replaces() -> None:
    assert (
        content_job_lease(time_multiplier=1) > timedelta(seconds=director_max_duration_s() + 60) * 3
    )


def test_max_spec_duration_is_the_longest_of_all_specs() -> None:
    specs = load_content_specs()
    longest = max(
        director_max_duration_s(),
        specs.copywriter.limits.max_duration_s,
        specs.designer.limits.max_duration_s,
        specs.reviewer.limits.max_duration_s,
    )
    assert max_spec_duration_s() == longest == 300  # o Designer e o mais longo


def test_worker_stale_reservation_threshold_outlives_a_live_image_reservation() -> None:
    # Uma reserva de imagem fica aberta durante a chamada (ate IMAGE_CALL_TIMEOUT_S): o limiar
    # do arranque do painel nunca a pode dar como orfa enquanto o worker a espera.
    expected = timedelta(seconds=max_spec_duration_s() + IMAGE_CALL_TIMEOUT_S)
    assert worker_stale_after(time_multiplier=1) == expected == timedelta(seconds=480)
    assert worker_stale_after(time_multiplier=1) > timedelta(
        seconds=IMAGE_CALL_TIMEOUT_S + 60
    )  # com margem
    assert worker_stale_after(time_multiplier=1) < content_job_lease(
        time_multiplier=1
    )  # uma execucao cabe numa lease


# --- rotas do worker: capacidades e precos ----------------------------------------------------

Factory = sessionmaker[Session]
VALID_FROM = datetime(2026, 11, 1, tzinfo=UTC)
FREE = (PriceTier(0, Decimal(0), Decimal(0)),)
TEXT = ["text", "json_schema"]
VISION = ["text", "json_schema", "image_input"]
SIZE = "1024x1536"


def route(model: str, capabilities: list[str], provider: str = "openai") -> RoleRoute:
    return RoleRoute.model_validate(
        {"provider": provider, "model_id": model, "capabilities": capabilities}
    )


def settings_with(routes: dict[str, RoleRoute], **extra: object) -> Settings:
    return Settings(  # type: ignore[arg-type]
        _env_file=None, database_url="postgresql+psycopg://u:p@127.0.0.1:5433/db",
        role_routes=routes, **extra,
    )  # fmt: skip


def seed_text_prices(factory: Factory, *models: str) -> None:
    sync_model_prices(
        factory, [ModelPrice("openai", m, "v1", FREE, valid_from=VALID_FROM) for m in models]
    )


def good_routes() -> dict[str, RoleRoute]:
    return {"copywriter": route("qwen2.5:14b", TEXT), "revisor": route("qwen2.5vl:7b", VISION)}


def test_worker_routes_with_vision_revisor_and_prices_pass(session_factory: Factory) -> None:
    seed_text_prices(session_factory, "qwen2.5:14b", "qwen2.5vl:7b")
    check_content_routes(settings_with(good_routes()), PriceBook(session_factory))


def test_text_only_revisor_route_is_refused_at_startup(session_factory: Factory) -> None:
    seed_text_prices(session_factory, "qwen2.5:14b", "llama3.1:8b")
    routes = {**good_routes(), "revisor": route("llama3.1:8b", TEXT)}
    with pytest.raises(StartupError) as caught:
        check_content_routes(settings_with(routes), PriceBook(session_factory))
    assert str(caught.value) == (
        "ROLE_ROUTES: a rota 'revisor' precisa das capacidades ['image_input']; "
        "ver .env.local.example"
    )


def test_missing_capabilities_are_listed_sorted(session_factory: Factory) -> None:
    routes = {"copywriter": route("m", ["text"]), "revisor": route("v", ["text"])}
    with pytest.raises(StartupError) as caught:
        check_content_routes(settings_with(routes), PriceBook(session_factory))
    assert "'copywriter' precisa das capacidades ['json_schema']" in str(caught.value)
    routes = {"copywriter": route("m", TEXT), "revisor": route("v", ["text"])}
    with pytest.raises(StartupError) as caught:
        check_content_routes(settings_with(routes), PriceBook(session_factory))
    assert "'revisor' precisa das capacidades ['image_input', 'json_schema']" in str(caught.value)


def test_route_without_price_points_to_sync_prices(session_factory: Factory) -> None:
    seed_text_prices(session_factory, "qwen2.5:14b")  # falta o preco do modelo de visao
    with pytest.raises(StartupError) as caught:
        check_content_routes(settings_with(good_routes()), PriceBook(session_factory))
    assert str(caught.value) == (
        "ROLE_ROUTES: a rota 'revisor' (openai/qwen2.5vl:7b) nao tem preco; "
        "correr 'marketing-agent sync-prices'"
    )


def test_designer_route_is_checked_when_present(session_factory: Factory) -> None:
    seed_text_prices(session_factory, "qwen2.5:14b", "qwen2.5vl:7b")
    routes = {**good_routes(), "designer": route("sem-preco", TEXT)}
    with pytest.raises(StartupError, match="'designer' .*sem-preco.* nao tem preco"):
        check_content_routes(settings_with(routes), PriceBook(session_factory))
    routes = {**good_routes(), "designer": route("qwen2.5:14b", ["text"])}
    with pytest.raises(StartupError, match="'designer' precisa das capacidades"):
        check_content_routes(settings_with(routes), PriceBook(session_factory))


def image_settings(tmp_path: Path, **extra: object) -> Settings:
    key = tmp_path / "images.key"
    key.write_text("chave-de-imagens")
    image_route = route("gpt-image-1", ["image_output"])
    return settings_with(
        {**good_routes(), "designer_imagem": image_route},
        openai_images_api_key_file=key, **extra,
    )  # fmt: skip


def seed_image_price(factory: Factory, *qualities: str) -> None:
    per_image = tuple(ImagePrice(q, SIZE, Decimal("0.04")) for q in qualities)
    sync_model_prices(
        factory,
        [
            ModelPrice(
                "openai", "gpt-image-1", "v1", FREE, valid_from=VALID_FROM, per_image=per_image
            )
        ],
    )


def test_image_route_with_key_and_per_image_price_passes(
    session_factory: Factory, tmp_path: Path
) -> None:
    seed_text_prices(session_factory, "qwen2.5:14b", "qwen2.5vl:7b")
    seed_image_price(session_factory, "medium")
    check_content_routes(image_settings(tmp_path), PriceBook(session_factory))


def test_image_route_without_key_file_is_refused(session_factory: Factory) -> None:
    seed_text_prices(session_factory, "qwen2.5:14b", "qwen2.5vl:7b")
    image_route = route("gpt-image-1", ["image_output"])
    settings = settings_with({**good_routes(), "designer_imagem": image_route})
    with pytest.raises(StartupError, match="OPENAI_IMAGES_API_KEY_FILE"):
        check_content_routes(settings, PriceBook(session_factory))


def test_image_route_without_price_for_the_configured_quality_is_refused(
    session_factory: Factory, tmp_path: Path
) -> None:
    seed_text_prices(session_factory, "qwen2.5:14b", "qwen2.5vl:7b")
    seed_image_price(session_factory, "low")  # a qualidade configurada e `medium`
    with pytest.raises(StartupError) as caught:
        check_content_routes(image_settings(tmp_path), PriceBook(session_factory))
    assert str(caught.value) == (
        "ROLE_ROUTES: a rota 'designer_imagem' (openai/gpt-image-1) nao tem preco por imagem "
        "para quality=medium, size=1024x1536; correr 'marketing-agent sync-prices'"
    )


def test_image_route_without_any_price_is_refused(session_factory: Factory, tmp_path: Path) -> None:
    seed_text_prices(session_factory, "qwen2.5:14b", "qwen2.5vl:7b")
    with pytest.raises(StartupError, match="'designer_imagem' .* nao tem preco"):
        check_content_routes(image_settings(tmp_path), PriceBook(session_factory))


def test_slow_local_times_extend_lease_and_reservation_recovery() -> None:
    multiplier = 5
    # Os limites de imagem nao mudam; os tres papeis e a sua chamada em excesso escalam.
    specs = load_content_specs()
    durations = sum(
        s.limits.max_duration_s
        for s in (
            specs.copywriter,
            specs.designer,
            specs.reviewer,
        )
    )
    increase = timedelta(seconds=3 * (multiplier - 1) * (durations + 3 * LLM_CALL_TIMEOUT_S))
    assert (
        content_job_lease(time_multiplier=multiplier)
        == content_job_lease(time_multiplier=1) + increase
    )
    assert worker_stale_after(time_multiplier=multiplier) == timedelta(
        seconds=max_spec_duration_s() * multiplier + LLM_CALL_TIMEOUT_S * multiplier
    )
    # Valores documentados: reservas 300 x 5 + 600 = 2100 s (35 min); lease 18600 s (310 min).
    assert worker_stale_after(time_multiplier=multiplier) == timedelta(seconds=2100)
    assert content_job_lease(time_multiplier=multiplier) == timedelta(seconds=18600)


@pytest.mark.parametrize(("multiplier", "seconds"), [(1, 243), (5, 663), (10, 1263)])
def test_orphan_grace_covers_one_in_flight_call_plus_the_fixed_margin(
    multiplier: int, seconds: int
) -> None:
    # max(120 x m, 180) + 60 + (1 + 2): a chamada paga mais lenta ainda em voo no worker anterior.
    assert orphan_grace(time_multiplier=multiplier) == timedelta(seconds=seconds)
