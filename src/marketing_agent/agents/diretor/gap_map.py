"""Mapa de lacunas da entrevista: um item por topico, do desconhecido ao confirmado."""

from collections.abc import Mapping
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

# Cobrem as entradas do diagnostico (F02). A ordem e a de apresentacao.
GAP_TOPICS: Final[tuple[str, ...]] = (
    "o_que_vende",
    "cliente_prioritario",
    "geografia",
    "origem_clientes_atual",
    "proposta_de_valor",
    "motivos_compra_e_perda",
    "capacidade_entrega",
    "provas_disponiveis",
    "percurso_contacto",
    "voz_e_tom",
    "concorrentes",
    "objetivos",
    "canais_e_idioma",
    "material_existente",
)

Origin = Literal["dito_pelo_responsavel", "verificado", "suposto"]
GapStatus = Literal["desconhecido", "suposicao", "confirmado"]


class GapItem(BaseModel):
    """Estado de um topico: o que se sabe, com que estado e de onde veio."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: str
    status: GapStatus
    note: str
    origin: Origin | None


class _StoredGapMap(BaseModel):
    """Forma guardada em `interview_session.gap_map` (a coluna e um objeto JSON)."""

    model_config = ConfigDict(extra="forbid")

    items: list[GapItem]


def initial_gap_map() -> list[GapItem]:
    """Todos os topicos como `desconhecido`, sem nota nem origem."""
    return [GapItem(topic=t, status="desconhecido", note="", origin=None) for t in GAP_TOPICS]


def _is_owner_confirmed(item: GapItem) -> bool:
    return item.status == "confirmado" and item.origin == "dito_pelo_responsavel"


def merge_gap_map(current: list[GapItem], updates: list[GapItem]) -> list[GapItem]:
    """Aplica `updates` sobre `current`, mantendo a ordem de `current`.

    Ignora topicos desconhecidos e nunca altera um item confirmado pelo responsavel:
    o que o dono da marca disse nao pode ser rebaixado pela saida do LLM.
    """
    by_topic = {item.topic: item for item in current}
    for update in updates:
        existing = by_topic.get(update.topic)
        if existing is not None and not _is_owner_confirmed(existing):
            by_topic[update.topic] = update
    return [by_topic[item.topic] for item in current]


def confirm_by_owner(current: list[GapItem], topic: str, note: str) -> list[GapItem]:
    """Correcao explicita do responsavel: unico caminho que substitui um item dele.

    Levanta `ValueError` para um topico fora de `GAP_TOPICS`.
    """
    if topic not in GAP_TOPICS:
        raise ValueError(f"topico inexistente no mapa de lacunas: {topic!r}")
    confirmed = GapItem(topic=topic, status="confirmado", note=note, origin="dito_pelo_responsavel")
    return [confirmed if item.topic == topic else item for item in current]


def serialize_gap_map(items: list[GapItem]) -> dict[str, object]:
    """Converte o mapa no objeto JSON guardado na sessao."""
    return _StoredGapMap(items=items).model_dump(mode="json")


def parse_gap_map(data: Mapping[str, object]) -> list[GapItem]:
    """Le o objeto JSON guardado na sessao; `ValidationError` se estiver corrompido."""
    return _StoredGapMap.model_validate(data).items
