"""Conjuntos de avaliacao versionados (`evals/<nome>.yaml`, spec 2C, secao 4).

Um conjunto e um ficheiro YAML editado pelo responsavel: nome, versao e de 1 a 20 briefs. A
versao fica gravada em cada corrida, por isso mudar um brief exige subir a versao.

Os itens da base citados por um brief sao `kind:key` (por exemplo `preco:pao`) e nao ids: os ids
mudam de base para base e o ficheiro tem de sobreviver a isso. A corrida resolve-os, no inicio,
para os itens ativos e validos nesse dia (`evals.runs`).

O nome do conjunto passa pela regra `^[a-z0-9_]{1,40}$` ANTES de tocar no sistema de ficheiros:
um nome como `../x` nunca chega a formar um caminho.
"""

import re
from pathlib import Path
from typing import Annotated, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from marketing_agent.planning.schemas import Canal

SET_NAME_RE: Final = re.compile(r"^[a-z0-9_]{1,40}$")
BRIEF_KEY_PATTERN: Final = r"^[a-z0-9_]{1,40}$"
# `kind:key` de um item da base; o `kind` e um dos tipos da base (`db.models.KB_KINDS`).
KB_KEY_PATTERN: Final = r"^[a-z_]{1,20}:[A-Za-z0-9_.-]{1,80}$"
MAX_BRIEFS: Final = 20

type BriefTag = Literal["com_preco", "sem_foto_adequada", "promessa_exagerada"]


class EvalSetError(ValueError):
    """Conjunto inexistente, invalido ou com nome recusado; a mensagem cita so campos e tipos."""


class EvalBrief(BaseModel):
    """Um brief do conjunto: o mesmo que um lugar do plano semanal, mais as etiquetas do caso."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: Annotated[str, Field(pattern=BRIEF_KEY_PATTERN)]
    tema: str = Field(min_length=1, max_length=200)
    pilar: str = Field(min_length=1, max_length=200)
    objetivo: str = Field(min_length=1, max_length=300)
    canais: list[Canal] = Field(min_length=1, max_length=2)
    kb_refs: list[Annotated[str, Field(pattern=KB_KEY_PATTERN)]] = Field(
        default_factory=list, max_length=10
    )
    tags: list[BriefTag] = Field(default_factory=list)

    @field_validator("canais", "kb_refs", "tags")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("valores repetidos")
        return value


class EvalSet(BaseModel):
    """Conjunto versionado de briefs com chaves unicas."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")]
    version: int = Field(ge=1)
    briefs: list[EvalBrief] = Field(min_length=1, max_length=MAX_BRIEFS)

    @field_validator("briefs")
    @classmethod
    def _unique_keys(cls, value: list[EvalBrief]) -> list[EvalBrief]:
        keys = [brief.key for brief in value]
        if len(set(keys)) != len(keys):
            raise ValueError("chaves de brief repetidas")
        return value


def check_set_name(name: str) -> str:
    """Devolve `name` se cumpre `^[a-z0-9_]{1,40}$`; `EvalSetError` (sem ecoar o nome) senao."""
    if not SET_NAME_RE.fullmatch(name):
        raise EvalSetError("nome de conjunto invalido: so [a-z0-9_], ate 40 caracteres")
    return name


def load_eval_set(name: str, root: Path) -> EvalSet:
    """Le e valida `root/<name>.yaml`; o `name` do ficheiro tem de coincidir com o pedido."""
    path = root / f"{check_set_name(name)}.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EvalSetError(f"conjunto {name} inexistente em {root}") from None
    except yaml.YAMLError:
        raise EvalSetError(f"conjunto {name}: YAML invalido") from None
    try:
        eval_set = EvalSet.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'raiz'}: {e['type']}"
            for e in exc.errors(include_input=False)
        )
        raise EvalSetError(f"conjunto {name} invalido: {details}") from None
    if eval_set.name != name:
        raise EvalSetError(f"conjunto {name}: o campo name do ficheiro nao coincide")
    return eval_set
