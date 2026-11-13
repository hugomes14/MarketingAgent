"""Base de conhecimento: factos da marca com origem e validade que os textos podem citar.

Cada operacao e uma transacao curta com a sua linha de `AuditLog`. O `detail` da auditoria so
leva `kind` e `key`: o texto livre dos itens fica na tabela `kb_item`, nunca no registo.
Todas as datas vem do `Clock` injetado (UTC).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import Select, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.agents.diretor.schemas import PerfilMarca
from marketing_agent.artifacts.store import ArtifactStore, ArtifactType
from marketing_agent.clock import Clock
from marketing_agent.db.models import KB_KINDS, SOURCE_KINDS, AuditLog, Brand, KbItem

_PRICE_KIND = "preco"
_PROMOTED_KIND = "outro"


class KbError(ValueError):
    """Topico inexistente, item de outra marca, duplicado ou dados invalidos."""


class NewKbEntry(BaseModel):
    """Entrada nova da base de conhecimento, validada antes de tocar na base."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=2000)
    price_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    price_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    valid_from: date
    valid_until: date | None = None
    source_kind: str

    @field_validator("kind")
    @classmethod
    def _kind_is_known(cls, value: str) -> str:
        if value not in KB_KINDS:
            raise ValueError(f"kind desconhecido: {value!r}; esperava um de {KB_KINDS}")
        return value

    @field_validator("source_kind")
    @classmethod
    def _source_kind_is_known(cls, value: str) -> str:
        if value not in SOURCE_KINDS:
            raise ValueError(f"source_kind desconhecido: {value!r}; esperava um de {SOURCE_KINDS}")
        return value

    @model_validator(mode="after")
    def _price_and_validity_are_consistent(self) -> Self:
        if (self.price_amount is None) != (self.price_currency is None):
            raise ValueError("price_amount e price_currency vem sempre juntos")
        if self.kind == _PRICE_KIND and (self.price_amount is None or self.valid_until is None):
            raise ValueError("kind 'preco' exige price_amount, price_currency e valid_until")
        if self.valid_until is not None:
            # Preco exige janela positiva (`>`); os outros tipos aceitam um so dia (`>=`).
            is_inverted = (
                self.valid_until <= self.valid_from
                if self.kind == _PRICE_KIND
                else self.valid_until < self.valid_from
            )
            if is_inverted:
                raise ValueError(
                    "valid_until nao pode ser anterior a valid_from (preco: posterior)"
                )
        return self


@dataclass(frozen=True)
class KbEntry:
    """Vista imutavel de um item da base de conhecimento."""

    id: str
    kind: str
    key: str
    title: str
    body: str
    price_amount: Decimal | None
    price_currency: str | None
    valid_from: date
    valid_until: date | None
    source_kind: str
    source_ref: str | None
    is_active: bool


def _describe(exc: ValidationError) -> str:
    """Resume o erro so com campo e tipo (ex.: `body: string_too_long`), sem valores."""
    errors = exc.errors(include_input=False)
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'entrada'}: {e['type']}" for e in errors)


def _to_entry(row: KbItem) -> KbEntry:
    return KbEntry(
        id=row.id,
        kind=row.kind,
        key=row.key,
        title=row.title,
        body=row.body,
        price_amount=row.price_amount,
        price_currency=row.price_currency,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        source_kind=row.source_kind,
        source_ref=row.source_ref,
        is_active=row.is_active,
    )


class KbService:
    """Adiciona, promove, desativa e consulta factos da marca."""

    def __init__(
        self, session_factory: sessionmaker[Session], store: ArtifactStore, clock: Clock
    ) -> None:
        """Guarda a fabrica de sessoes, a loja de artefactos (perfil ativo) e o relogio."""
        self._session_factory = session_factory
        self._store = store
        self._clock = clock

    def add(self, brand_id: str, entry: NewKbEntry, actor: str) -> KbEntry:
        """Grava um item manual; `KbError` se a marca nao existe ou o item ja existe."""
        with self._session_factory.begin() as session:
            row = self._insert(session, brand_id, entry, actor, "kb.add", None)
            if row is None:
                raise KbError(
                    f"ja existe o item {entry.kind}/{entry.key} desde {entry.valid_from} "
                    f"para a marca {brand_id}"
                )
            return _to_entry(row)

    def promote_profile_topic(self, brand_id: str, topic: str, actor: str) -> KbEntry:
        """Copia um topico do `perfil_marca` ativo para a base, com a origem do topico.

        Idempotente por (marca, kind, key, valid_from): repetir no mesmo dia devolve o item
        existente, sem segunda linha nem segunda auditoria.
        """
        profile = self._store.active(brand_id, ArtifactType.PERFIL_MARCA)
        if profile is None:
            raise KbError(f"a marca {brand_id} nao tem perfil_marca ativo")
        topics = PerfilMarca.model_validate(profile.payload).topicos
        if topic not in topics:
            raise KbError(f"o perfil ativo nao tem o topico {topic!r}")
        found = topics[topic]
        entry = self._build_promoted(topic, found.texto, found.origem)
        source_ref = f"perfil:{profile.id}:{topic}"
        with self._session_factory.begin() as session:
            row = self._insert(session, brand_id, entry, actor, "kb.promote", source_ref)
            if row is None:
                row = session.scalars(self._same_slot(brand_id, entry)).one()
            else:
                self._deactivate_superseded(session, row, actor)
            return _to_entry(row)

    def deactivate(self, brand_id: str, item_id: str, actor: str) -> None:
        """Desativa o item (idempotente); `KbError` se nao existe ou e de outra marca."""
        with self._session_factory.begin() as session:
            row = session.get(KbItem, item_id)
            if row is None:
                raise KbError(f"item inexistente: {item_id}")
            if row.brand_id != brand_id:
                raise KbError(f"o item {item_id} nao pertence a marca {brand_id}")
            if row.is_active:
                row.is_active = False
                self._audit(session, actor, "kb.deactivate", row.id, row.kind, row.key)

    def list_active(self, brand_id: str, on: date) -> list[KbEntry]:
        """Itens ativos e validos em `on`, por tipo e chave."""
        statement = self._valid_on(brand_id, on).order_by(
            KbItem.kind, KbItem.key, KbItem.valid_from
        )
        with self._session_factory() as session:
            return [_to_entry(row) for row in session.scalars(statement)]

    def valid_refs(self, brand_id: str, refs: Sequence[str], on: date) -> dict[str, KbEntry]:
        """Dos ids em `refs`, so os ativos e validos em `on` (extremos inclusivos)."""
        if not refs:
            return {}
        statement = self._valid_on(brand_id, on).where(KbItem.id.in_(refs))
        with self._session_factory() as session:
            return {row.id: _to_entry(row) for row in session.scalars(statement)}

    def entries_by_ids(self, brand_id: str, ids: Sequence[str]) -> dict[str, KbEntry]:
        """Itens da marca com estes ids, ativos ou nao, validos ou nao, na ordem de `ids`.

        Serve a quem fixou ids num instante (corrida de avaliacao) e tem de reler exatamente
        esses itens depois, mesmo desativados ou expirados entretanto. Ids de outra marca ou
        inexistentes ficam de fora.
        """
        if not ids:
            return {}
        statement = select(KbItem).where(KbItem.brand_id == brand_id, KbItem.id.in_(ids))
        with self._session_factory() as session:
            found = {row.id: _to_entry(row) for row in session.scalars(statement)}
        return {item_id: found[item_id] for item_id in ids if item_id in found}

    def _build_promoted(self, topic: str, text: str, origin: str) -> NewKbEntry:
        """Entrada `outro` valida para o topico, com inicio de validade na data do relogio."""
        try:
            return NewKbEntry(
                kind=_PROMOTED_KIND,
                key=topic,
                title=topic.replace("_", " "),
                body=text,
                valid_from=self._clock.now().date(),
                source_kind=origin,
            )
        except ValidationError as exc:
            # Nunca `str(exc)`: o pydantic inclui `input_value` e o texto do perfil e livre.
            raise KbError(
                f"topico {topic!r} nao cabe na base de conhecimento: {_describe(exc)}"
            ) from None

    def _insert(
        self,
        session: Session,
        brand_id: str,
        entry: NewKbEntry,
        actor: str,
        action: str,
        source_ref: str | None,
    ) -> KbItem | None:
        """Insere o item e audita; devolve `None` (sem auditar) se o slot ja estava ocupado."""
        if session.get(Brand, brand_id) is None:
            raise KbError(f"marca inexistente: {brand_id}")
        now = self._clock.now()
        statement = (
            insert(KbItem)
            .values(
                **entry.model_dump(),
                brand_id=brand_id,
                source_ref=source_ref,
                created_by=actor,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["brand_id", "kind", "key", "valid_from"])
            .returning(KbItem)
        )
        row = session.scalars(statement, execution_options={"populate_existing": True}).first()
        if row is not None:
            self._audit(session, actor, action, row.id, row.kind, row.key)
        return row

    def _deactivate_superseded(self, session: Session, promoted: KbItem, actor: str) -> None:
        """Desativa as versoes anteriores ativas do mesmo `(marca, kind, key)`; a nova fica."""
        older = session.scalars(
            select(KbItem).where(
                KbItem.brand_id == promoted.brand_id,
                KbItem.kind == promoted.kind,
                KbItem.key == promoted.key,
                KbItem.is_active.is_(True),
                KbItem.id != promoted.id,
            )
        )
        for row in older:
            row.is_active = False
            self._audit(session, actor, "kb.deactivate", row.id, row.kind, row.key)

    def _audit(
        self, session: Session, actor: str, action: str, item_id: str, kind: str, key: str
    ) -> None:
        session.add(
            AuditLog(
                actor=actor,
                action=action,
                subject_type="kb_item",
                subject_id=item_id,
                detail={"kind": kind, "key": key},
                at=self._clock.now(),
            )
        )

    @staticmethod
    def _same_slot(brand_id: str, entry: NewKbEntry) -> Select[KbItem]:
        return select(KbItem).where(
            KbItem.brand_id == brand_id,
            KbItem.kind == entry.kind,
            KbItem.key == entry.key,
            KbItem.valid_from == entry.valid_from,
        )

    @staticmethod
    def _valid_on(brand_id: str, on: date) -> Select[KbItem]:
        return select(KbItem).where(
            KbItem.brand_id == brand_id,
            KbItem.is_active.is_(True),
            KbItem.valid_from <= on,
            or_(KbItem.valid_until.is_(None), KbItem.valid_until >= on),
        )
