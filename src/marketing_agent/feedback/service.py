"""Feedback: o tipo escolhido pelo responsavel decide se e como o exemplo volta ao prompt.

So `estilo` e `negativo` entram no prompt do Copywriter (ate 5 e 3, ativos e nao expirados).
`correcao_factual` fica guardado para o painel sugerir um item da base; `temporario` tem
validade e serve para nao repetir um facto desatualizado. `nao_usar` nao grava nada.

`record_in` corre na sessao do chamador (sem commit), para o exemplo nascer na mesma
transacao da decisao que o motivou. O `detail` da auditoria so leva `kind` e `post_id`, e as
mensagens de erro so nomeiam campo e motivo: o texto livre fica em `feedback_example`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import Clock
from marketing_agent.db.models import AuditLog, FeedbackExample, PostRevision

FeedbackChoice = Literal["estilo", "correcao_factual", "temporario", "negativo", "nao_usar"]

_STYLE_LIMIT = 5
_NEGATIVE_LIMIT = 3
# 5000 e a legenda maxima de canal (Facebook); sem truncar, para o par antes/depois ficar fiel.
_TEXT_MAX = 5000
_NOTE_MAX = 500
_TEMPORARY_KIND = "temporario"


class FeedbackError(ValueError):
    """Feedback invalido, revisao fora do post/marca ou exemplo inexistente. Sem texto livre."""


@dataclass(frozen=True)
class FeedbackExampleView:
    """Vista imutavel de um exemplo de feedback."""

    id: str
    kind: str
    before_text: str
    after_text: str | None
    note: str | None
    expires_at: datetime | None
    created_at: datetime
    is_active: bool


def _to_view(row: FeedbackExample) -> FeedbackExampleView:
    return FeedbackExampleView(
        id=row.id,
        kind=row.kind,
        before_text=row.before_text,
        after_text=row.after_text,
        note=row.note,
        expires_at=row.expires_at,
        created_at=row.created_at,
        is_active=row.is_active,
    )


def _check_length(field: str, value: str | None, maximum: int, *, is_required: bool) -> None:
    """Valida 1..`maximum` caracteres; a mensagem so nomeia o campo."""
    if value is None:
        if is_required:
            raise FeedbackError(f"{field}: obrigatorio")
        return
    if not 1 <= len(value) <= maximum:
        raise FeedbackError(f"{field}: tem de ter entre 1 e {maximum} caracteres")


def _newest_first(statement: Select[FeedbackExample]) -> Select[FeedbackExample]:
    # O `id` desempata exemplos do mesmo instante, para a ordem ser determinista.
    return statement.order_by(FeedbackExample.created_at.desc(), FeedbackExample.id.desc())


class FeedbackService:
    """Regista, lista e desativa exemplos de feedback e escolhe os que entram no prompt."""

    def __init__(self, session_factory: sessionmaker[Session], clock: Clock) -> None:
        """Guarda a fabrica de sessoes e o relogio (UTC) usado nas validades."""
        self._session_factory = session_factory
        self._clock = clock

    def record_in(
        self,
        session: Session,
        *,
        brand_id: str,
        post_id: str,
        revision_id: str,
        choice: FeedbackChoice,
        before_text: str,
        after_text: str | None,
        note: str | None,
        expires_at: datetime | None,
        actor: str,
    ) -> str | None:
        """Grava o exemplo na sessao do chamador (sem commit) e devolve o id.

        `nao_usar` devolve `None` sem escrever nada. `FeedbackError` se os textos excedem os
        limites, se `expires_at` nao e futuro em `temporario` (ou aparece noutro tipo) ou se a
        revisao nao pertence ao post e a marca.
        """
        if choice == "nao_usar":
            return None
        _check_length("before_text", before_text, _TEXT_MAX, is_required=True)
        _check_length("after_text", after_text, _TEXT_MAX, is_required=False)
        _check_length("note", note, _NOTE_MAX, is_required=False)
        self._check_expiry(choice, expires_at)
        self._check_revision(session, brand_id, post_id, revision_id)
        now = self._clock.now()
        row = FeedbackExample(
            brand_id=brand_id,
            post_id=post_id,
            revision_id=revision_id,
            kind=choice,
            before_text=before_text,
            after_text=after_text,
            note=note,
            expires_at=expires_at,
            created_by=actor,
            created_at=now,
        )
        session.add(row)
        session.flush()
        self._audit(session, actor, "feedback.record", row, {"kind": choice, "post_id": post_id})
        return row.id

    def prompt_examples(
        self, brand_id: str
    ) -> tuple[list[FeedbackExampleView], list[FeedbackExampleView]]:
        """Ate 5 `estilo` e 3 `negativo` ativos e nao expirados, dos mais recentes."""
        with self._session_factory() as session:
            styles = self._usable(session, brand_id, "estilo", _STYLE_LIMIT)
            negatives = self._usable(session, brand_id, "negativo", _NEGATIVE_LIMIT)
        return styles, negatives

    def list_examples(self, brand_id: str, limit: int | None = None) -> list[FeedbackExampleView]:
        """Exemplos da marca (ativos ou nao, de qualquer tipo), dos mais recentes.

        `limit` (opcional) corta a lista nos mais recentes; sem ele devolve todos.
        """
        statement = _newest_first(
            select(FeedbackExample).where(FeedbackExample.brand_id == brand_id)
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self._session_factory() as session:
            return [_to_view(row) for row in session.scalars(statement)]

    def deactivate(self, brand_id: str, example_id: str, actor: str) -> None:
        """Desativa o exemplo (idempotente); `FeedbackError` se nao existe ou e de outra marca."""
        with self._session_factory.begin() as session:
            row = session.get(FeedbackExample, example_id)
            if row is None or row.brand_id != brand_id:
                # Mesma mensagem nos dois casos: nao revela ids de outra marca.
                raise FeedbackError("exemplo inexistente para a marca")
            if row.is_active:
                row.is_active = False
                self._audit(session, actor, "feedback.deactivate", row, {"kind": row.kind})

    def _check_expiry(self, choice: str, expires_at: datetime | None) -> None:
        if choice != _TEMPORARY_KIND:
            if expires_at is not None:
                raise FeedbackError(f"expires_at: so se aceita no tipo {_TEMPORARY_KIND!r}")
            return
        if expires_at is None:
            raise FeedbackError("expires_at: obrigatorio no tipo 'temporario'")
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise FeedbackError("expires_at: exige data com fuso")
        if expires_at <= self._clock.now():
            raise FeedbackError("expires_at: tem de ser futuro")

    @staticmethod
    def _check_revision(session: Session, brand_id: str, post_id: str, revision_id: str) -> None:
        """Garante a revisao no par marca+post; evita um `IntegrityError` da FK composta."""
        found = session.scalar(
            select(PostRevision.id).where(
                PostRevision.id == revision_id,
                PostRevision.brand_id == brand_id,
                PostRevision.post_id == post_id,
            )
        )
        if found is None:
            raise FeedbackError("revisao nao pertence ao post/marca")

    def _usable(
        self, session: Session, brand_id: str, kind: str, limit: int
    ) -> list[FeedbackExampleView]:
        now = self._clock.now()
        statement = _newest_first(
            select(FeedbackExample).where(
                FeedbackExample.brand_id == brand_id,
                FeedbackExample.kind == kind,
                FeedbackExample.is_active.is_(True),
                or_(FeedbackExample.expires_at.is_(None), FeedbackExample.expires_at > now),
            )
        ).limit(limit)
        return [_to_view(row) for row in session.scalars(statement)]

    def _audit(
        self,
        session: Session,
        actor: str,
        action: str,
        row: FeedbackExample,
        detail: dict[str, str],
    ) -> None:
        session.add(
            AuditLog(
                actor=actor,
                action=action,
                subject_type="feedback_example",
                subject_id=row.id,
                detail=detail,
                at=self._clock.now(),
            )
        )
