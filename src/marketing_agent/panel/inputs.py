"""Validacao das entradas dos formularios do painel (fronteira com o utilizador).

Os valores aqui lidos sao dados: so se verifica forma e tamanho, nunca se interpretam como
instrucoes. As mensagens de `InvalidForm` sao fixas e seguras para mostrar.
"""

import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final

from fastapi import Request
from pydantic import ValidationError

from marketing_agent.agents.diretor.gap_map import GAP_TOPICS
from marketing_agent.kb.service import NewKbEntry
from marketing_agent.panel.errors import InvalidForm
from marketing_agent.panel.forms import FormFields
from marketing_agent.planning.validation import week_range

IDEMPOTENCY_HEADER: Final = "Idempotency-Key"
MAX_KEY_CHARS: Final = 100
MAX_REASON_CHARS: Final = 500
MAX_TOPIC_CHARS: Final = 100
DEFAULT_REASON: Final = "sem motivo indicado"
CONFIRM_FIELD: Final = "confirm_cancel"
CONFIRM_VALUE: Final = "1"
ACCEPT: Final = "aceitar"
REFUSE: Final = "recusar"
_KEY_PATTERN: Final = re.compile(r"[\x21-\x7e]+")  # ASCII visivel, sem espacos nem controlo
_DECISION_FIELD: Final = re.compile(r"decisao_(\d{1,4})")
DEFAULT_SOURCE_KIND: Final = "dito_pelo_responsavel"  # o responsavel escreve o item no painel
# Campos do formulario da base que o pydantic pode recusar, com a etiqueta mostrada ao utilizador.
_KB_FIELD_LABELS: Final = {
    "kind": "tipo",
    "key": "chave",
    "title": "título",
    "body": "texto",
    "price_amount": "preço",
    "price_currency": "moeda",
    "valid_from": "início da validade",
    "valid_until": "fim da validade",
    "source_kind": "origem",
}
_KB_WINDOW_MESSAGE: Final = (
    "Verifique o preço, a moeda e a validade: um preço exige montante, moeda de 3 letras, "
    "e um fim de validade posterior ao início."
)


def read_idempotency_key(request: Request) -> str:
    """Chave do cabecalho `Idempotency-Key`; nunca e gerada no servidor (422 se faltar)."""
    key = request.headers.get(IDEMPOTENCY_HEADER, "")
    if len(key) > MAX_KEY_CHARS or not _KEY_PATTERN.fullmatch(key):
        raise InvalidForm("Falta a chave do formulário. Recarregue a página e tente de novo.")
    return key


def read_topic(fields: FormFields) -> str:
    """Topico do mapa de lacunas escolhido no formulario de correcao."""
    topic = fields.get("topico", "")
    if topic not in GAP_TOPICS:
        raise InvalidForm("Escolha um tópico da lista.")
    return topic


def read_reason(fields: FormFields) -> str:
    """Motivo da rejeicao: opcional, ate `MAX_REASON_CHARS` caracteres."""
    reason = fields.get("motivo", "").strip()
    if len(reason) > MAX_REASON_CHARS:
        raise InvalidForm(f"O motivo tem de ter no máximo {MAX_REASON_CHARS} caracteres.")
    return reason or DEFAULT_REASON


def read_decisions(fields: FormFields) -> Mapping[int, bool]:
    """Decisoes `decisao_{i}` -> aceite/recusada; valores desconhecidos sao recusados (422).

    Nao adivinha: um campo em falta fica em falta e o servico levanta `IncompleteDecisions`.
    """
    decisions: dict[int, bool] = {}
    for name, value in fields.items():
        match = _DECISION_FIELD.fullmatch(name)
        if match is None:
            continue
        if value not in (ACCEPT, REFUSE):
            raise InvalidForm("Escolha aceitar ou recusar em cada proposta.")
        decisions[int(match.group(1))] = value == ACCEPT
    return decisions


def read_week(fields: FormFields) -> str | None:
    """Semana ISO `AAAA-Www` pedida para o plano; `None` (campo vazio ou ausente) = a seguinte."""
    week = fields.get("semana", "")
    if not week:
        return None
    try:
        week_range(week)
    except ValueError:
        raise InvalidForm("Indique a semana no formato AAAA-Www, por exemplo 2026-W46.") from None
    return week


def read_confirm_cancel(fields: FormFields) -> bool:
    """A caixa `confirm_cancel` do formulario de aprovar: so o valor exato `1` confirma.

    Ausente ou com outro valor e `False`: o servico exige a confirmacao quando o plano cancelaria
    publicacoes aprovadas ou em revisao, por isso um valor estranho nunca confirma por engano.
    """
    return fields.get(CONFIRM_FIELD, "") == CONFIRM_VALUE


def read_profile_topic(fields: FormFields) -> str:
    """Nome do topico do perfil a promover; a pertenca ao perfil ativo verifica-se na rota."""
    topic = fields.get("topic", "")
    if not topic or len(topic) > MAX_TOPIC_CHARS:
        raise InvalidForm("Escolha um tópico do perfil.")
    return topic


def _read_optional_date(fields: FormFields, name: str, label: str) -> date | None:
    text = fields.get(name, "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise InvalidForm(f"Data inválida em «{label}»: use AAAA-MM-DD.") from None


def _read_amount(fields: FormFields) -> Decimal | None:
    text = fields.get("price_amount", "").strip().replace(",", ".")
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise InvalidForm("Preço inválido: use um número, por exemplo 12.50.") from None
    if not amount.is_finite():
        raise InvalidForm("Preço inválido: use um número, por exemplo 12.50.")
    return amount


def read_kb_entry(fields: FormFields) -> NewKbEntry:
    """Item novo da base a partir do formulario; qualquer valor invalido e `InvalidForm` (422).

    Campos opcionais vazios (preco, moeda, fim) ficam `None`. A mensagem so nomeia campos: o
    `ValidationError` do pydantic traz o valor recebido e nunca chega ao utilizador.
    """
    text = {name: fields.get(name, "").strip() for name in ("kind", "key", "title", "body")}
    currency = fields.get("price_currency", "").strip().upper() or None
    valid_from = _read_optional_date(fields, "valid_from", "início da validade")
    if valid_from is None:
        raise InvalidForm("Indique o início da validade.")
    valid_until = _read_optional_date(fields, "valid_until", "fim da validade")
    source_kind = fields.get("source_kind", "").strip() or DEFAULT_SOURCE_KIND
    invalid_fields: list[str] = []
    try:
        return NewKbEntry(
            **text,
            price_amount=_read_amount(fields),
            price_currency=currency,
            valid_from=valid_from,
            valid_until=valid_until,
            source_kind=source_kind,
        )
    except ValidationError as exc:
        invalid_fields = [str(error["loc"][0]) if error["loc"] else "" for error in exc.errors()]
    # Fora do `except`: a excecao do pydantic nao fica ligada a esta como causa nem contexto.
    raise InvalidForm(_kb_message(invalid_fields))


def _kb_message(invalid_fields: list[str]) -> str:
    names = sorted({_KB_FIELD_LABELS.get(name, "") for name in invalid_fields} - {""})
    if names:
        return "Dados inválidos na base de conhecimento. Reveja: " + ", ".join(names) + "."
    return _KB_WINDOW_MESSAGE
