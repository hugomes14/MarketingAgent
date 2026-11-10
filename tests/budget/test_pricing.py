"""Precos versionados: custo exato em Decimal, escaloes, sincronizacao e leitura."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.budget.pricing import (
    ImagePrice,
    ImageUsage,
    ModelPrice,
    PriceBook,
    PriceTier,
    UnknownPrice,
    Usage,
    compute_cost_usd,
    compute_image_cost_usd,
    load_model_prices,
    sync_model_prices,
)
from marketing_agent.db.models import ModelPriceRow

Factory = sessionmaker[Session]
REPO_PRICES = Path(__file__).resolve().parents[2] / "config" / "model_prices.yaml"
VALID_FROM = datetime(2026, 11, 5, tzinfo=UTC)


def make_price(*tiers: PriceTier, version: str = "2026-11-05") -> ModelPrice:
    return ModelPrice("fake", "m1", version, tiers, valid_from=VALID_FROM)


def test_cost_exact() -> None:
    price = make_price(PriceTier(0, Decimal("2"), Decimal("10")))
    assert compute_cost_usd(price, Usage(6000, 1500)) == Decimal("0.027")


def test_tier_selected_by_prompt_size() -> None:
    price = make_price(
        PriceTier(0, Decimal("1"), Decimal("5")),
        PriceTier(100000, Decimal("2"), Decimal("10")),
    )
    assert compute_cost_usd(price, Usage(150000, 0)) == Decimal("0.3")
    assert compute_cost_usd(price, Usage(99999, 0)) == Decimal("0.099999")


def test_reasoning_not_double_counted() -> None:
    price = make_price(PriceTier(0, Decimal("2"), Decimal("10")))
    with_reasoning = compute_cost_usd(price, Usage(1000, 500, reasoning_tokens=400))
    assert with_reasoning == compute_cost_usd(price, Usage(1000, 500))


def test_negative_price_rejected() -> None:
    with pytest.raises(ValueError, match="negativ"):
        make_price(PriceTier(0, Decimal("-1"), Decimal("1")))


def test_sync_is_idempotent(session_factory: Factory) -> None:
    prices = [make_price(PriceTier(0, Decimal("2"), Decimal("10")))]
    sync_model_prices(session_factory, prices)
    sync_model_prices(session_factory, prices)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelPriceRow)) == 1
    changed = [make_price(PriceTier(0, Decimal("3"), Decimal("10")))]
    with pytest.raises(ValueError, match="fake/m1/2026-11-05"):
        sync_model_prices(session_factory, changed)


def test_unknown_price_raises(session_factory: Factory) -> None:
    with pytest.raises(UnknownPrice, match="fake/nada"):
        PriceBook(session_factory).get("fake", "nada")


def test_price_book_returns_latest_with_all_tiers(session_factory: Factory) -> None:
    old = make_price(PriceTier(0, Decimal("1"), Decimal("1")), version="2026-01-01")
    new_tiers = (
        PriceTier(0, Decimal("2"), Decimal("4")),
        PriceTier(100001, Decimal("5"), Decimal("8")),
    )
    new = ModelPrice("fake", "m1", "2026-11-05", new_tiers, valid_from=VALID_FROM)
    sync_model_prices(session_factory, [new, old])
    assert PriceBook(session_factory).get("fake", "m1") == new


def test_repo_price_file_loads() -> None:
    prices = load_model_prices(REPO_PRICES)
    by_key = {(p.provider, p.model_id): p for p in prices}
    assert ("fake", "fake-1") in by_key
    assert len(by_key[("anthropic", "claude-haiku-5-5")].tiers) == 2


def test_loader_rejects_numeric_prices(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(
        "models:\n- provider: x\n  model_id: y\n  price_version: v\n"
        "  valid_from: '2026-11-05T00:00:00+00:00'\n"
        "  tiers:\n  - {min_prompt_tokens: 0, input_per_million: 0.1, output_per_million: '1'}\n"
    )
    with pytest.raises(ValueError, match=r"p\.yaml.*x/y"):
        load_model_prices(path)


def test_positional_construction_without_valid_from(session_factory: Factory) -> None:
    price = ModelPrice("p", "m", "v", (PriceTier(0, Decimal("1"), Decimal("2")),))
    assert price.valid_from is None
    with pytest.raises(ValueError, match="p/m/v"):
        sync_model_prices(session_factory, [price])


ZERO_TIER = PriceTier(0, Decimal("0"), Decimal("0"))
IMAGE_PRICES = (
    ImagePrice("medium", "1024x1536", Decimal("0.05")),
    ImagePrice("low", "1024x1536", Decimal("0.011")),
    ImagePrice("high", "1024x1536", Decimal("0.2")),
    ImagePrice("high", "1024x1024", Decimal("0.133")),
)


def make_image_price(
    per_image: tuple[ImagePrice, ...] = IMAGE_PRICES, version: str = "2026-11-09"
) -> ModelPrice:
    return ModelPrice(
        "fake", "img-1", version, (ZERO_TIER,), valid_from=VALID_FROM, per_image=per_image
    )


def write_image_yaml(tmp_path: Path, per_image_lines: str) -> Path:
    path = tmp_path / "img.yaml"
    path.write_text(
        "models:\n- provider: fake\n  model_id: img-1\n  price_version: v\n"
        "  valid_from: '2026-11-05T00:00:00+00:00'\n"
        "  tiers:\n  - {min_prompt_tokens: 0, input_per_million: '0', output_per_million: '0'}\n"
        f"  per_image:\n{per_image_lines}"
    )
    return path


def test_load_per_image_prices_money_as_text(tmp_path: Path) -> None:
    path = write_image_yaml(
        tmp_path,
        "  - {quality: medium, size: '1024x1536', usd: '0.05'}\n"
        "  - {quality: low, size: '1024x1536', usd: '0.011'}\n",
    )
    (price,) = load_model_prices(path)
    assert price.per_image == (
        ImagePrice("low", "1024x1536", Decimal("0.011")),
        ImagePrice("medium", "1024x1536", Decimal("0.05")),
    )


@pytest.mark.parametrize("usd", ["0.05", "5"])  # float e inteiro sem aspas
def test_loader_rejects_non_text_per_image_money(tmp_path: Path, usd: str) -> None:
    path = write_image_yaml(tmp_path, f"  - {{quality: low, size: '1024x1536', usd: {usd}}}\n")
    with pytest.raises(ValueError, match=r"img\.yaml.*fake/img-1"):
        load_model_prices(path)


def test_loader_rejects_malformed_per_image(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="per_image"):
        load_model_prices(write_image_yaml(tmp_path, "  - {quality: low, usd: '1'}\n"))
    path = tmp_path / "lista.yaml"
    path.write_text(
        "models:\n- provider: fake\n  model_id: img-1\n  price_version: v\n"
        "  valid_from: '2026-11-05T00:00:00+00:00'\n"
        "  tiers:\n  - {min_prompt_tokens: 0, input_per_million: '0', output_per_million: '0'}\n"
        "  per_image: 3\n"
    )
    with pytest.raises(ValueError, match="per_image"):
        load_model_prices(path)


def test_per_image_negative_price_rejected() -> None:
    with pytest.raises(ValueError, match="negativ"):
        make_image_price((ImagePrice("low", "1024x1536", Decimal("-0.01")),))


def test_per_image_duplicate_pair_rejected() -> None:
    dup = (
        ImagePrice("low", "1024x1536", Decimal("1")),
        ImagePrice("low", "1024x1536", Decimal("2")),
    )
    with pytest.raises(ValueError, match="repetido") as error:
        make_image_price(dup)
    assert "1024x1536" not in str(error.value)


def test_per_image_order_is_canonical() -> None:
    assert make_image_price(IMAGE_PRICES) == make_image_price(tuple(reversed(IMAGE_PRICES)))
    pairs = [(i.quality, i.size) for i in make_image_price().per_image]
    assert pairs == sorted(pairs)


def test_per_image_round_trip_in_conditions(session_factory: Factory) -> None:
    price = make_image_price()
    sync_model_prices(session_factory, [price])
    with session_factory() as session:
        row = session.scalars(select(ModelPriceRow)).one()
        assert {"tiers", "per_image"} == set(row.conditions)
        assert row.conditions["per_image"][0] == {
            "quality": "high",
            "size": "1024x1024",
            "usd": "0.133",
        }
        assert all(isinstance(i["usd"], str) for i in row.conditions["per_image"])
    assert PriceBook(session_factory).get("fake", "img-1") == price


@pytest.mark.parametrize(
    ("quality", "size", "expected"),
    [
        ("low", "1024x1536", Decimal("0.011")),
        ("medium", "1024x1536", Decimal("0.05")),
        ("high", "1024x1536", Decimal("0.2")),
        ("high", "1024x1024", Decimal("0.133")),
    ],
)
def test_compute_image_cost_by_quality_and_size(quality: str, size: str, expected: Decimal) -> None:
    price = make_image_price()
    assert compute_image_cost_usd(price, ImageUsage(1, size, quality)) == expected
    assert compute_image_cost_usd(price, ImageUsage(3, size, quality)) == expected * 3


@pytest.mark.parametrize(("quality", "size"), [("low", "1024x1024"), ("ultra", "1024x1536")])
def test_unknown_quality_size_raises_unknown_price(quality: str, size: str) -> None:
    with pytest.raises(UnknownPrice, match="fake/img-1"):
        compute_image_cost_usd(make_image_price(), ImageUsage(1, size, quality))
    with pytest.raises(UnknownPrice):
        compute_image_cost_usd(make_price(ZERO_TIER), ImageUsage(1, "1024x1536", "low"))


def test_compute_image_cost_rejects_non_positive_images() -> None:
    with pytest.raises(ValueError, match="images"):
        compute_image_cost_usd(make_image_price(), ImageUsage(0, "1024x1536", "low"))


def test_sync_refuses_changed_per_image_same_version(session_factory: Factory) -> None:
    sync_model_prices(session_factory, [make_image_price()])
    sync_model_prices(session_factory, [make_image_price(tuple(reversed(IMAGE_PRICES)))])
    changed = (
        ImagePrice("low", "1024x1536", Decimal("0.012")),
        *IMAGE_PRICES[:1],
        *IMAGE_PRICES[2:],
    )
    with pytest.raises(ValueError, match="fake/img-1/2026-11-09"):
        sync_model_prices(session_factory, [make_image_price(changed)])
    with pytest.raises(ValueError, match="fake/img-1/2026-11-09"):
        sync_model_prices(session_factory, [make_image_price(IMAGE_PRICES[:2])])


def test_sync_old_version_without_per_image_stays_equal(session_factory: Factory) -> None:
    price = make_price(PriceTier(0, Decimal("2"), Decimal("10")))
    with session_factory.begin() as session:  # linha gravada antes do preco por imagem
        session.add(
            ModelPriceRow(
                provider="fake",
                model_id="m1",
                price_version="2026-11-05",
                valid_from=VALID_FROM,
                input_per_million=Decimal("2"),
                output_per_million=Decimal("10"),
                conditions={
                    "tiers": [
                        {
                            "min_prompt_tokens": 0,
                            "input_per_million": "2",
                            "output_per_million": "10",
                        }
                    ]
                },
            )
        )
    sync_model_prices(session_factory, [price])  # no-op, nao e visto como alteracao
    assert PriceBook(session_factory).get("fake", "m1") == price
    with pytest.raises(ValueError, match="imutavel"):
        sync_model_prices(
            session_factory,
            [
                ModelPrice(
                    "fake",
                    "m1",
                    "2026-11-05",
                    price.tiers,
                    valid_from=VALID_FROM,
                    per_image=(ImagePrice("low", "1024x1536", Decimal("0")),),
                )
            ],
        )


def test_repo_price_files_define_fake_image_prices() -> None:
    config = REPO_PRICES.parent
    for name in ("model_prices.yaml", "model_prices.local.yaml"):
        by_key = {(p.provider, p.model_id): p for p in load_model_prices(config / name)}
        price = by_key[("fake", "fake-image-1")]
        assert price.tiers == (ZERO_TIER,)
        assert {(i.quality, i.size, i.usd) for i in price.per_image} == {
            (q, "1024x1536", Decimal("0")) for q in ("low", "medium", "high")
        }
