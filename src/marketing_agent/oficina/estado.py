"""Estados derivados, entregas publicas e serializacao estavel da equipa da oficina."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

from marketing_agent.oficina.papeis import Papel

if TYPE_CHECKING:
    from marketing_agent.db.models import JsonObject

EstadoPapel = Literal["a_trabalhar", "erro", "desatualizado", "em_espera", "inativo"]
TipoEntrega = Literal["perfil", "diagnostico", "estrategia", "plano", "texto", "imagem", "parecer"]
ESTADO_PRIORIDADE: Final[tuple[EstadoPapel, ...]] = (
    "a_trabalhar",
    "erro",
    "desatualizado",
    "em_espera",
    "inativo",
)
ESTADO_TEXTO: Final[Mapping[EstadoPapel, str]] = {
    "a_trabalhar": "A trabalhar",
    "erro": "Erro por resolver",
    "desatualizado": "Sem sinal: o processo pode ter parado",
    "em_espera": "À espera de si",
    "inativo": "Sem trabalho",
}


@dataclass(frozen=True)
class RoleSignals:
    """Sinais de leitura, sem semantica de animacao ou acoes sobre agentes."""

    is_working: bool
    has_failure: bool
    is_stale: bool
    pending: int


def derive_state(signals: RoleSignals) -> EstadoPapel:
    """Prioridade fixa: trabalho, erro, falta de sinal, pendencias, inatividade."""
    if signals.is_working:
        return "a_trabalhar"
    if signals.has_failure:
        return "erro"
    if signals.is_stale:
        return "desatualizado"
    return "em_espera" if signals.pending > 0 else "inativo"


@dataclass(frozen=True)
class Entrega:
    """Ligacao a uma entrega existente; nunca transporta conteudo completo ou comandos."""

    tipo: TipoEntrega
    id: str
    titulo: str
    url: str


@dataclass(frozen=True)
class RevisaoSemanal:
    """Entrada para o lote existente mais antigo com pacotes por decidir."""

    semana: str
    pendentes: int
    url: str


@dataclass(frozen=True)
class EstadoAgente:
    """Estado publico de um papel: texto legivel, contagem e ate cinco entregas."""

    papel: Papel
    nome: str
    estado: EstadoPapel
    estado_texto: str
    pendencias: int
    ultima_atividade_em: datetime | None
    entregas: tuple[Entrega, ...]


@dataclass(frozen=True)
class EstadoEquipa:
    """Snapshot dos quatro papeis e da entrada para revisao semanal."""

    agentes: tuple[EstadoAgente, ...]
    revisao_semanal: RevisaoSemanal


def truncate_title(text: str, limit: int = 60) -> str:
    """Normaliza espacos e limita o titulo com reticencias incluidas no limite."""
    if limit < 1:
        raise ValueError("o limite do titulo deve ser positivo")
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _utc_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ultima atividade exige fuso horario")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _agent_json(agent: EstadoAgente) -> JsonObject:
    # Lista explicita: novos campos internos nunca passam para o JSON por acidente.
    return {
        "papel": agent.papel,
        "nome": agent.nome,
        "estado": agent.estado,
        "estado_texto": agent.estado_texto,
        "pendencias": agent.pendencias,
        "ultima_atividade_em": _utc_time(agent.ultima_atividade_em),
        "entregas": [
            {"tipo": e.tipo, "id": e.id, "titulo": e.titulo, "url": e.url} for e in agent.entregas
        ],
    }


def team_state_json(state: EstadoEquipa) -> bytes:
    """JSON UTF-8 canonico e minimo; sem legendas, falhas, notas ou dados de autenticacao."""
    review = state.revisao_semanal
    payload: JsonObject = {
        "agentes": [_agent_json(agent) for agent in state.agentes],
        "revisao_semanal": {
            "semana": review.semana,
            "pendentes": review.pendentes,
            "url": review.url,
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def etag_for(body: bytes) -> str:
    """ETag forte dos bytes exatos do JSON (32 digitos SHA-256 entre aspas)."""
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """Comparacao fraca de If-None-Match para GET: listas, W/ e asterisco."""
    for candidate in (if_none_match or "").split(","):
        value = candidate.strip()
        if value == "*" or value.removeprefix("W/").strip() == etag:
            return True
    return False
