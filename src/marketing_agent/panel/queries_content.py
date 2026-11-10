"""Leituras do painel sobre a base de conhecimento, o plano semanal e as publicacoes.

Funcoes puras de leitura sobre uma `Session` aberta pelo chamador (`PanelQueries`); todas
filtram pela marca do painel, por isso um id de outra marca e tratado como inexistente.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from marketing_agent.artifacts.store import ArtifactRecord, ArtifactState, ArtifactType
from marketing_agent.db.models import KB_KINDS, Artifact, KbItem, Post
from marketing_agent.planning.schemas import PlanoSemanal

IN_REVIEW: Final = "in_review"
# Planos que interessam a pagina inicial: o que aguarda decisao e o que esta em vigor.
_LISTED_PLAN_STATES: Final = (ArtifactState.SUBMITTED.value, ArtifactState.APPROVED.value)


@dataclass(frozen=True)
class KbItemView:
    """Item ativo da base, para a lista; o preco ja vem formatado (`12.50 EUR`)."""

    id: str
    key: str
    title: str
    body: str
    price: str | None
    valid_from: date
    valid_until: date | None
    source_kind: str


@dataclass(frozen=True)
class KbGroup:
    """Itens ativos de um tipo, por chave e inicio de validade."""

    kind: str
    items: list[KbItemView]


@dataclass(frozen=True)
class PlanView:
    """Plano para revisao: conteudo validado, titulos da base citados e posts ja criados."""

    record: ArtifactRecord
    plano: PlanoSemanal
    kb_titles: dict[str, str]
    post_count: int


@dataclass(frozen=True)
class PlanLink:
    """Plano mais relevante da marca para a pagina inicial."""

    artifact_id: str
    week: str
    state: str


def _price_text(amount: Decimal | None, currency: str | None) -> str | None:
    return None if amount is None else f"{amount:.2f} {currency}"


def kb_groups(session: Session, brand_id: str) -> list[KbGroup]:
    """Itens ativos (qualquer janela de validade) agrupados por tipo, na ordem de `KB_KINDS`."""
    rows = session.scalars(
        select(KbItem)
        .where(KbItem.brand_id == brand_id, KbItem.is_active.is_(True))
        .order_by(KbItem.key, KbItem.valid_from, KbItem.id)
    )
    by_kind: dict[str, list[KbItemView]] = {kind: [] for kind in KB_KINDS}
    for row in rows:
        price = _price_text(row.price_amount, row.price_currency)
        by_kind[row.kind].append(
            KbItemView(
                id=row.id,
                key=row.key,
                title=row.title,
                body=row.body,
                price=price,
                valid_from=row.valid_from,
                valid_until=row.valid_until,
                source_kind=row.source_kind,
            )
        )
    return [KbGroup(kind, items) for kind, items in by_kind.items() if items]


def kb_titles(session: Session, brand_id: str, item_ids: Collection[str]) -> dict[str, str]:
    """Titulo de cada item da base citado (ativo ou nao); ids de outra marca ficam de fora."""
    if not item_ids:
        return {}
    rows = session.execute(
        select(KbItem.id, KbItem.title).where(
            KbItem.brand_id == brand_id, KbItem.id.in_(list(item_ids))
        )
    )
    return {item_id: title for item_id, title in rows}


def count_plan_posts(session: Session, plan_id: str) -> int:
    """Publicacoes que apontam para o plano (criadas ou repontadas ao aprova-lo)."""
    statement = select(func.count()).select_from(Post).where(Post.plan_artifact_id == plan_id)
    return session.scalar(statement) or 0


def latest_plan_link(session: Session, brand_id: str) -> PlanLink | None:
    """Plano da semana mais recente; na mesma semana, um `submitted` passa a frente do aprovado."""
    week = Artifact.payload["semana"].astext
    submitted_first = case((Artifact.state == ArtifactState.SUBMITTED.value, 0), else_=1)
    row = session.execute(
        select(Artifact.id, week, Artifact.state)
        .where(
            Artifact.brand_id == brand_id,
            Artifact.type == ArtifactType.PLANO.value,
            Artifact.state.in_(_LISTED_PLAN_STATES),
        )
        .order_by(week.desc(), submitted_first, Artifact.id)
        .limit(1)
    ).first()
    return None if row is None else PlanLink(*row)


def count_in_review(session: Session, brand_id: str) -> int:
    """Publicacoes a aguardar a decisao do responsavel."""
    statement = (
        select(func.count())
        .select_from(Post)
        .where(Post.brand_id == brand_id, Post.lifecycle == IN_REVIEW)
    )
    return session.scalar(statement) or 0


def earliest_review_week(session: Session, brand_id: str) -> str | None:
    """Semana ISO mais antiga com publicacoes por rever (as `slot_key` ordenam por semana)."""
    slot_key = session.scalar(
        select(func.min(Post.slot_key)).where(
            Post.brand_id == brand_id, Post.lifecycle == IN_REVIEW
        )
    )
    return None if slot_key is None else slot_key.rsplit("-", 1)[0]
