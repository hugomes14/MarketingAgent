"""Esquema de saida do plano semanal: slots de publicacao com tema, pilar e justificacao."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Canal = Literal["facebook", "instagram"]
WEEK_PATTERN = r"^\d{4}-W\d{2}$"
# Id de item da base (UUID de 36 caracteres); o teto limita o que um erro pode ecoar de volta.
KbRef = Annotated[str, Field(min_length=1, max_length=64)]


class PlanSlot(BaseModel):
    """Uma publicacao planeada: dia, canais, tema e a ligacao a pilar, experiencia e base."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot_key: str = Field(min_length=1, max_length=20)
    data: date
    canais: list[Canal] = Field(min_length=1, max_length=2)
    tema: str = Field(min_length=1, max_length=200)
    pilar: str = Field(min_length=1, max_length=200)
    objetivo: str = Field(min_length=1, max_length=300)
    experiment_index: int | None
    kb_refs: list[KbRef]
    justificacao: str = Field(min_length=1, max_length=600)

    @field_validator("canais")
    @classmethod
    def _channels_are_unique(cls, value: list[Canal]) -> list[Canal]:
        if len(set(value)) != len(value):
            raise ValueError("canais repetidos")
        return value


class PlanoSemanal(BaseModel):
    """Plano de uma semana ISO; a validacao contra datas, tetos e base fica em `validation`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    semana: str = Field(pattern=WEEK_PATTERN)
    slots: list[PlanSlot] = Field(min_length=1, max_length=14)
    suposicoes: list[str]
