"""Precos versionados: custo exato em Decimal, escaloes, sincronizacao e leitura."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.budget.pricing import (
    ModelPrice,
    PriceBook,
    PriceTier,
    UnknownPrice,
    Usage,
    compute_cost_usd,
    load_model_prices,
    sync_model_prices,
)
from marketing_agent.db.models import ModelPriceRow

Factory = sessionmaker[Session]
REPO_PRICES = Path(__file__).resolve().parents[2] / "config" / "model_prices.yaml"
VALID_FROM = datetime(2026, 11, 5, tzinfo=UTC)


def make_price(*tiers: PriceTier, version: str = "2026-11-05") -> ModelPrice:
    return ModelPrice("fake", "m1", version, VALID_FROM, tiers)


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
    new = ModelPrice("fake", "m1", "2026-11-05", datetime(2026, 11, 5, tzinfo=UTC), new_tiers)
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
