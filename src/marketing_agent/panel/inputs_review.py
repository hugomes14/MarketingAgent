"""Entradas dos formularios da revisao de publicacoes (fronteira com o responsavel).

As mensagens de `InvalidForm` sao fixas. O texto da edicao so passa pelo construtor seguro
`CaptionEdit.from_form`; nunca se constroi `CaptionEdit(...)` aqui, porque a `ValidationError`
do pydantic traz o valor recebido.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Final, cast, get_args

from marketing_agent.content.review_edit import CaptionEdit
from marketing_agent.feedback.service import FeedbackChoice
from marketing_agent.panel.context import UUID_PATTERN
from marketing_agent.panel.errors import InvalidForm
from marketing_agent.panel.forms import FormFields

FEEDBACK_CHOICES: Final[tuple[str, ...]] = get_args(FeedbackChoice)
TEMPORARY: Final = "temporario"
MAX_NOTE_CHARS: Final = 500
_ID_PATTERN: Final = re.compile(UUID_PATTERN)
_DATE_PATTERN: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_HASHTAG_SEPARATORS: Final = re.compile(r"[\s,]+")
_END_OF_DAY: Final = time(23, 59, 59)


@dataclass(frozen=True)
class FeedbackInput:
    """Tipo de feedback escolhido, nota opcional e validade (so no tipo `temporario`)."""

    choice: FeedbackChoice
    note: str | None
    expires_at: datetime | None


def read_revision_id(fields: FormFields, name: str = "revision_id") -> str:
    """Id da revisao que a pagina mostrava; sem ele a acao nao pode ser verificada (422)."""
    value = fields.get(name, "")
    if not _ID_PATTERN.fullmatch(value):
        raise InvalidForm("Falta a revisão a que a ação se refere. Recarregue a página.")
    return value


def read_feedback(fields: FormFields) -> FeedbackInput:
    """Tipo de feedback (sem valor por omissao), nota e validade; invalido e `InvalidForm`.

    `temporario` exige a data `expira_em` (AAAA-MM-DD), que vale ate ao fim desse dia em UTC;
    que seja futura verifica-o o `FeedbackService`. Nos outros tipos a data e ignorada.
    """
    choice = fields.get("feedback", "")
    if choice not in FEEDBACK_CHOICES:
        raise InvalidForm("Escolha o tipo de feedback.")
    note = fields.get("nota", "").strip()
    if len(note) > MAX_NOTE_CHARS:
        raise InvalidForm(f"A nota tem de ter no máximo {MAX_NOTE_CHARS} caracteres.")
    expires_at = _read_expiry(fields) if choice == TEMPORARY else None
    # `choice` ja foi verificado contra os valores do `Literal`, por isso o `cast` e seguro.
    return FeedbackInput(cast(FeedbackChoice, choice), note or None, expires_at)


def read_caption_edit(fields: FormFields) -> CaptionEdit:
    """Texto editado a partir do formulario; limites violados dao `InvalidEdit` (422)."""
    hashtags = [tag for tag in _HASHTAG_SEPARATORS.split(fields.get("hashtags", "")) if tag]
    return CaptionEdit.from_form(
        _field_text(fields, "legenda"),
        hashtags,
        _field_text(fields, "apelo_a_acao"),
        _field_text(fields, "alt_text"),
    )


def _field_text(fields: FormFields, name: str) -> str:
    # Os browsers enviam quebras de linha como CRLF; a legenda guardada usa LF.
    return fields.get(name, "").replace("\r\n", "\n").strip()


def _read_expiry(fields: FormFields) -> datetime:
    text = fields.get("expira_em", "").strip()
    if not _DATE_PATTERN.fullmatch(text):
        raise InvalidForm("Indique até que dia o feedback temporário vale (AAAA-MM-DD).")
    try:
        day = date.fromisoformat(text)
    except ValueError:
        raise InvalidForm("Indique até que dia o feedback temporário vale (AAAA-MM-DD).") from None
    return datetime.combine(day, _END_OF_DAY, tzinfo=UTC)
