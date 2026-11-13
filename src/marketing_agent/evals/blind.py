"""Atribuicao cega reproduzivel e valores que podem atravessar a fronteira do painel."""

import random
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from marketing_agent.evals.sets import EvalBrief

EvalPath = Literal["simples", "especializado"]
Side = Literal["a", "b"]
Better = Literal["a", "b", "empate"]
ErrorKind = Literal[
    "facto_errado", "alegacao_sem_apoio", "voz_errada", "imagem_inadequada", "outro"
]
EditMinutes = Literal["0", "1", "2_5", "mais_5"]


def side_a_path(seed: int, brief_key: str) -> EvalPath:
    """Sorteio por par independente da ordem de execucao, sem o hash aleatorio do processo."""
    return random.Random(f"{seed}:{brief_key}").choice(("simples", "especializado"))


class JudgementInput(BaseModel):
    """Resposta explicita do responsavel; ausencia de escolha nunca implica empate."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    better: Better
    errors_a: list[ErrorKind] = Field(max_length=5)
    errors_b: list[ErrorKind] = Field(max_length=5)
    edit_a: EditMinutes
    edit_b: EditMinutes
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("errors_a", "errors_b")
    @classmethod
    def unique_errors(cls, value: list[ErrorKind]) -> list[ErrorKind]:
        """Um tipo de erro conta uma vez por pacote, mesmo se houver varias ocorrencias."""
        if len(value) != len(set(value)):
            raise ValueError("tipos de erro repetidos")
        return value


@dataclass(frozen=True)
class BlindSide:
    """Apenas conteudo comparavel; sem ids, nomes de ficheiro ou metadados de execucao."""

    legenda: str
    hashtags: tuple[str, ...]
    apelo_a_acao: str
    alt_text: str
    alegacoes: tuple[str, ...]
    is_ai_generated: bool


@dataclass(frozen=True)
class CurrentJudgement:
    """A resposta atual do par, sem a chave que identifica o percurso A."""

    id: str
    answer: JudgementInput


@dataclass(frozen=True)
class BlindPair:
    """Um par elegivel na ordem do conjunto, com os lados ja anonimizados."""

    index: int
    brief: EvalBrief
    a: BlindSide
    b: BlindSide
    current: CurrentJudgement | None


@dataclass(frozen=True)
class JudgingSummary:
    """Contagens e indices que permitem navegar sem expor a identidade dos percursos."""

    pairs: int
    judged: int
    excluded: int
    missing: tuple[int, ...]
