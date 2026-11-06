"""Validacao das entradas dos formularios do painel (fronteira com o utilizador).

Os valores aqui lidos sao dados: so se verifica forma e tamanho, nunca se interpretam como
instrucoes. As mensagens de `InvalidForm` sao fixas e seguras para mostrar.
"""

import re
from collections.abc import Mapping
from typing import Final

from fastapi import Request

from marketing_agent.agents.diretor.gap_map import GAP_TOPICS
from marketing_agent.panel.errors import InvalidForm
from marketing_agent.panel.forms import FormFields

IDEMPOTENCY_HEADER: Final = "Idempotency-Key"
MAX_KEY_CHARS: Final = 100
MAX_REASON_CHARS: Final = 500
DEFAULT_REASON: Final = "sem motivo indicado"
ACCEPT: Final = "aceitar"
REFUSE: Final = "recusar"
_KEY_PATTERN: Final = re.compile(r"[\x21-\x7e]+")  # ASCII visivel, sem espacos nem controlo
_DECISION_FIELD: Final = re.compile(r"decisao_(\d{1,4})")


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
