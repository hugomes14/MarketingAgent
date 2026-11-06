"""Precos versionados por modelo, com escaloes pelo tamanho do prompt, e calculo do custo.

Aritmetica sempre em `Decimal`, sem arredondar. Uma `price_version` e imutavel: depois de
gravada em `model_price`, so pode ser repetida com valores identicos.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.db.base import new_id
from marketing_agent.db.models import ModelPriceRow

MILLION = Decimal(1_000_000)


class UnknownPrice(Exception):
    """Nao ha preco conhecido para o par fornecedor/modelo."""


@dataclass(frozen=True)
class PriceTier:
    """Taxas por milhao de tokens aplicaveis a pedidos com prompt >= `min_prompt_tokens`."""

    min_prompt_tokens: int
    input_per_million: Decimal
    output_per_million: Decimal


@dataclass(frozen=True)
class ModelPrice:
    """Preco de um modelo numa versao; o primeiro escalao comeca em 0 tokens."""

    provider: str
    model_id: str
    price_version: str
    valid_from: datetime
    tiers: tuple[PriceTier, ...]

    def __post_init__(self) -> None:
        key = f"{self.provider}/{self.model_id}/{self.price_version}"
        if not self.tiers or self.tiers[0].min_prompt_tokens != 0:
            raise ValueError(f"{key}: o primeiro escalao deve comecar em 0 tokens")
        minimos = [tier.min_prompt_tokens for tier in self.tiers]
        if any(a >= b for a, b in zip(minimos, minimos[1:], strict=False)):
            raise ValueError(f"{key}: escaloes devem ser estritamente crescentes")
        if any(t.input_per_million < 0 or t.output_per_million < 0 for t in self.tiers):
            raise ValueError(f"{key}: precos negativos nao sao permitidos")


@dataclass(frozen=True)
class Usage:
    """Tokens de um pedido; `reasoning_tokens` ja esta contido em `output_tokens`."""

    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None = None


def compute_cost_usd(price: ModelPrice, usage: Usage) -> Decimal:
    """Custo em USD; o escalao e o mais alto com `min_prompt_tokens <= input_tokens`."""
    tier = max(
        (t for t in price.tiers if t.min_prompt_tokens <= usage.input_tokens),
        key=lambda t: t.min_prompt_tokens,
    )
    total = (
        usage.input_tokens * tier.input_per_million + usage.output_tokens * tier.output_per_million
    )
    return total / MILLION


def _parse_money(value: object, where: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{where}: valor monetario deve ser texto entre aspas, nao {value!r}")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{where}: valor monetario invalido {value!r}") from None


def _parse_tier(raw: object, where: str) -> PriceTier:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: escalao deve ser um mapa")
    minimo = raw.get("min_prompt_tokens")
    if not isinstance(minimo, int) or isinstance(minimo, bool):
        raise ValueError(f"{where}: min_prompt_tokens deve ser inteiro")
    return PriceTier(
        minimo,
        _parse_money(raw.get("input_per_million"), where),
        _parse_money(raw.get("output_per_million"), where),
    )


def _parse_model(raw: object, path: Path, index: int) -> ModelPrice:
    where = f"{path.name}: entrada {index}"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: deve ser um mapa")
    fields = {k: raw.get(k) for k in ("provider", "model_id", "price_version", "valid_from")}
    if not all(isinstance(v, str) and v for v in fields.values()):
        raise ValueError(f"{where}: provider, model_id, price_version e valid_from sao texto")
    where = f"{path.name}: {fields['provider']}/{fields['model_id']}"
    tiers = raw.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise ValueError(f"{where}: tiers deve ser uma lista nao vazia")
    try:
        valid_from = datetime.fromisoformat(cast(str, fields["valid_from"]))
    except ValueError:
        raise ValueError(f"{where}: valid_from invalido {fields['valid_from']!r}") from None
    if valid_from.tzinfo is None:
        raise ValueError(f"{where}: valid_from precisa de fuso horario")
    return ModelPrice(
        cast(str, fields["provider"]),
        cast(str, fields["model_id"]),
        cast(str, fields["price_version"]),
        valid_from,
        tuple(_parse_tier(t, where) for t in tiers),
    )


def load_model_prices(path: Path) -> list[ModelPrice]:
    """Le e valida o YAML de precos; recusa valores monetarios que nao sejam texto."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise ValueError(f"{path.name}: falta a lista 'models'")
    return [_parse_model(raw, path, i) for i, raw in enumerate(models)]


def _tiers_as_json(price: ModelPrice) -> list[dict[str, int | str]]:
    return [
        {
            "min_prompt_tokens": t.min_prompt_tokens,
            "input_per_million": str(t.input_per_million),
            "output_per_million": str(t.output_per_million),
        }
        for t in price.tiers
    ]


def _tiers_from_json(raw: object) -> tuple[PriceTier, ...]:
    items = cast(list[dict[str, int | str]], raw)
    return tuple(
        PriceTier(
            int(i["min_prompt_tokens"]),
            Decimal(str(i["input_per_million"])),
            Decimal(str(i["output_per_million"])),
        )
        for i in items
    )


def _to_model_price(row: ModelPriceRow) -> ModelPrice:
    return ModelPrice(
        row.provider, row.model_id, row.price_version, row.valid_from,
        _tiers_from_json(row.conditions["tiers"]),
    )  # fmt: skip


def _find_row(session: Session, price: ModelPrice) -> ModelPriceRow | None:
    return session.scalar(
        select(ModelPriceRow).where(
            ModelPriceRow.provider == price.provider,
            ModelPriceRow.model_id == price.model_id,
            ModelPriceRow.price_version == price.price_version,
        )
    )


def sync_model_prices(session_factory: sessionmaker[Session], prices: Sequence[ModelPrice]) -> None:
    """Grava precos novos numa transacao; repetir igual e no-op, valores diferentes falham."""
    with session_factory.begin() as session:
        for price in prices:
            existing = _find_row(session, price)
            if existing is None:
                session.add(_to_row(price))
            elif _to_model_price(existing) != price:
                key = f"{price.provider}/{price.model_id}/{price.price_version}"
                raise ValueError(f"preco {key} ja existe com valores diferentes (versao imutavel)")


def _to_row(price: ModelPrice) -> ModelPriceRow:
    first = price.tiers[0]
    return ModelPriceRow(
        id=new_id(),
        provider=price.provider,
        model_id=price.model_id,
        price_version=price.price_version,
        valid_from=price.valid_from,
        input_per_million=first.input_per_million,
        output_per_million=first.output_per_million,
        conditions={"tiers": _tiers_as_json(price)},
    )


class PriceBook:
    """Consulta o preco mais recente de um modelo na base."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self, provider: str, model_id: str) -> ModelPrice:
        """Preco mais recente por (valid_from, price_version); `UnknownPrice` se nao houver."""
        stmt = (
            select(ModelPriceRow)
            .where(ModelPriceRow.provider == provider, ModelPriceRow.model_id == model_id)
            .order_by(ModelPriceRow.valid_from.desc(), ModelPriceRow.price_version.desc())
            .limit(1)
        )
        with self._session_factory() as session:
            row = session.scalar(stmt)
            if row is None:
                raise UnknownPrice(f"sem preco para {provider}/{model_id}")
            return _to_model_price(row)
