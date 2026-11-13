"""Os quatro papeis que existem no pipeline, pela ordem estavel da interface."""

from collections.abc import Mapping
from typing import Final, Literal

Papel = Literal["diretor", "copywriter", "designer", "revisor"]
PAPEIS: Final[tuple[Papel, ...]] = ("diretor", "copywriter", "designer", "revisor")
PAPEL_TEXTO: Final[Mapping[Papel, str]] = {
    "diretor": "Diretor",
    "copywriter": "Copywriter",
    "designer": "Designer",
    "revisor": "Revisor",
}
