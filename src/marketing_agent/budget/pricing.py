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
class ImagePrice:
    """Valor fixo em USD de uma imagem gerada com `quality` e `size`."""

    quality: str
    size: str
    usd: Decimal


@dataclass(frozen=True)
class ModelPrice:
    """Preco de um modelo numa versao; o primeiro escalao comeca em 0 tokens."""

    provider: str
    model_id: str
    price_version: str
    tiers: tuple[PriceTier, ...]
    # Opcional para quem so calcula custo; obrigatorio para gravar (sync_model_prices).
    valid_from: datetime | None = None
    # Precos por imagem (modelos de imagem). Guardados ordenados por (quality, size): a forma
    # canonica torna a comparacao de imutabilidade e a gravacao independentes da ordem do YAML.
    per_image: tuple[ImagePrice, ...] = ()

    def __post_init__(self) -> None:
        key = f"{self.provider}/{self.model_id}/{self.price_version}"
        if not self.tiers or self.tiers[0].min_prompt_tokens != 0:
            raise ValueError(f"{key}: o primeiro escalao deve comecar em 0 tokens")
        minimos = [tier.min_prompt_tokens for tier in self.tiers]
        if any(a >= b for a, b in zip(minimos, minimos[1:], strict=False)):
            raise ValueError(f"{key}: escaloes devem ser estritamente crescentes")
        if any(t.input_per_million < 0 or t.output_per_million < 0 for t in self.tiers):
            raise ValueError(f"{key}: precos negativos nao sao permitidos")
        if any(image.usd < 0 for image in self.per_image):
            raise ValueError(f"{key}: precos por imagem negativos nao sao permitidos")
        pares = [(image.quality, image.size) for image in self.per_image]
        if len(set(pares)) != len(pares):
            raise ValueError(f"{key}: par quality/size repetido em per_image")
        object.__setattr__(
            self, "per_image", tuple(sorted(self.per_image, key=lambda i: (i.quality, i.size)))
        )


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


@dataclass(frozen=True)
class ImageUsage:
    """Imagens geradas num pedido, com a qualidade e o tamanho pedidos."""

    images: int
    size: str
    quality: str


def compute_image_cost_usd(price: ModelPrice, usage: ImageUsage) -> Decimal:
    """Custo em USD de `usage.images` imagens; `UnknownPrice` sem linha para (quality, size).

    Funcao pura: o preco por imagem e fixo para cada par, sem arredondar.
    """
    if usage.images < 1:
        raise ValueError("images deve ser pelo menos 1")
    for image in price.per_image:
        if image.quality == usage.quality and image.size == usage.size:
            return image.usd * usage.images
    raise UnknownPrice(
        f"sem preco por imagem para {price.provider}/{price.model_id} "
        f"(quality={usage.quality}, size={usage.size})"
    )


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


def _parse_image_price(raw: object, where: str) -> ImagePrice:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: per_image deve conter mapas")
    quality, size = raw.get("quality"), raw.get("size")
    if not (isinstance(quality, str) and quality and isinstance(size, str) and size):
        raise ValueError(f"{where}: per_image exige quality e size em texto")
    return ImagePrice(quality, size, _parse_money(raw.get("usd"), where))


def _parse_per_image(raw: object, where: str) -> tuple[ImagePrice, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{where}: per_image deve ser uma lista")
    return tuple(_parse_image_price(item, where) for item in raw)


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
        tuple(_parse_tier(t, where) for t in tiers),
        valid_from=valid_from,
        per_image=_parse_per_image(raw.get("per_image"), where),
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


def _per_image_as_json(price: ModelPrice) -> list[dict[str, str]]:
    # `price.per_image` ja esta na forma canonica (ordenado), logo a ordem aqui e estavel.
    return [{"quality": i.quality, "size": i.size, "usd": str(i.usd)} for i in price.per_image]


def _per_image_from_json(raw: object) -> tuple[ImagePrice, ...]:
    # Versoes gravadas antes do preco por imagem nao tem a chave: equivalem a "sem per_image".
    items = cast(list[dict[str, str]], raw or [])
    return tuple(ImagePrice(i["quality"], i["size"], Decimal(i["usd"])) for i in items)


def _to_model_price(row: ModelPriceRow) -> ModelPrice:
    return ModelPrice(
        row.provider,
        row.model_id,
        row.price_version,
        _tiers_from_json(row.conditions["tiers"]),
        valid_from=row.valid_from,
        per_image=_per_image_from_json(row.conditions.get("per_image")),
    )


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
            if price.valid_from is None:
                key = f"{price.provider}/{price.model_id}/{price.price_version}"
                raise ValueError(f"preco {key} sem valid_from nao pode ser gravado")
            existing = _find_row(session, price)
            if existing is None:
                session.add(_to_row(price, price.valid_from))
            elif _to_model_price(existing) != price:
                key = f"{price.provider}/{price.model_id}/{price.price_version}"
                raise ValueError(f"preco {key} ja existe com valores diferentes (versao imutavel)")


def _to_row(price: ModelPrice, valid_from: datetime) -> ModelPriceRow:
    first = price.tiers[0]
    return ModelPriceRow(
        id=new_id(),
        provider=price.provider,
        model_id=price.model_id,
        price_version=price.price_version,
        valid_from=valid_from,
        input_per_million=first.input_per_million,
        output_per_million=first.output_per_million,
        conditions={"tiers": _tiers_as_json(price), "per_image": _per_image_as_json(price)},
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
