"""Personagens configuraveis, validadas no arranque; nomes sao dados, nunca HTML."""

import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from marketing_agent.oficina.papeis import PAPEIS, Papel

EstiloCabelo = Literal["curto", "comprido", "bone", "capa", "careca"]
ESTILO_REFERENCIA: Final[Mapping[EstiloCabelo, str]] = {
    "curto": "pelo",
    "comprido": "pelo-largo",
    "bone": "gorra",
    "capa": "capa",
    "careca": "ninguno",
}
MAX_CONFIG_BYTES = 64 * 1024
_SAFE_FIELDS = frozenset({"versao", "personagens", "nome", "cabelo", "roupa", "estilo", *PAPEIS})


class OficinaConfigError(ValueError):
    """Configuracao invalida: nome do ficheiro e campo, sem repetir valores fornecidos."""


class Personagem(BaseModel):
    """Aparencia de um papel, sem controlo de comportamento ou autonomia."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    nome: str = Field(min_length=1, max_length=40)
    cabelo: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    roupa: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    estilo: EstiloCabelo

    @field_validator("nome", mode="before")
    @classmethod
    def clean_name(cls, value: str) -> str:
        """Recusa controlos antes de aparar espacos, incluindo controlos bidi invisiveis."""
        if not isinstance(value, str):
            raise ValueError("nome deve ser texto")
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("nome com caracteres de controlo")
        return value.strip()


class OficinaConfig(BaseModel):
    """Versao1 com exatamente uma personagem por papel existente."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    versao: Literal[1]
    personagens: dict[Papel, Personagem]

    @field_validator("personagens")
    @classmethod
    def exact_roles(cls, value: dict[Papel, Personagem]) -> dict[Papel, Personagem]:
        """Nem papeis futuros nem omissoes silenciosas de personagens."""
        if set(value) != set(PAPEIS):
            raise ValueError("exige exatamente os quatro papeis")
        return value


def load_oficina_config(path: Path) -> OficinaConfig:
    """Le YAML local limitado em tamanho e devolve um contrato tipado de aparencia."""
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise OficinaConfigError(f"{path.name}: ficheiro demasiado grande")
        return OficinaConfig.model_validate(yaml.safe_load(raw.decode("utf-8")))
    except ValidationError as exc:
        location = exc.errors(include_input=False)[0]["loc"]
        field = ".".join(str(part) for part in location if part in _SAFE_FIELDS) or "configuracao"
        raise OficinaConfigError(f"{path.name}: campo {field} invalido") from None
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        raise OficinaConfigError(f"{path.name}: nao foi possivel ler YAML valido") from None
