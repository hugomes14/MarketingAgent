"""Esquemas de saida do Diretor: turno de entrevista e perfil de marca."""

from typing import Final

from pydantic import BaseModel, ConfigDict, field_validator

from marketing_agent.agents.diretor.gap_map import GAP_TOPICS, GapItem, Origin

MAX_QUESTION_CHARS: Final = 300


class InterviewTurnOutput(BaseModel):
    """Saida de um turno: proxima pergunta, resumo e atualizacoes ao mapa de lacunas."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    next_question: str | None
    summary: str
    gap_map_updates: list[GapItem]
    ready_for_profile: bool
    injection_suspected: bool

    @field_validator("next_question")
    @classmethod
    def _question_is_short_single_line(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > MAX_QUESTION_CHARS:
            raise ValueError(f"a pergunta excede {MAX_QUESTION_CHARS} caracteres")
        if "\n" in value or "\r" in value:
            raise ValueError("a pergunta nao pode ter quebras de linha")
        return value


class PerfilTopico(BaseModel):
    """O que o perfil afirma sobre um topico e de onde vem a afirmacao."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    texto: str
    origem: Origin


class PerfilMarca(BaseModel):
    """Perfil da marca derivado da entrevista, com lacunas e suposicoes explicitas."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    topicos: dict[str, PerfilTopico]
    lacunas_abertas: list[str]
    suposicoes: list[str]

    @field_validator("topicos")
    @classmethod
    def _topics_belong_to_gap_map(cls, value: dict[str, PerfilTopico]) -> dict[str, PerfilTopico]:
        unknown = sorted(set(value) - set(GAP_TOPICS))
        if unknown:
            raise ValueError(f"topicos inexistentes no mapa de lacunas: {unknown}")
        return value
