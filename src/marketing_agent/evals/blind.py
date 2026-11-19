"""Atribuicao cega dos lados A e B e a vista de um par sem nada que identifique o percurso.

O lado de cada percurso decide-se por brief a partir da semente gravada na corrida, com um
gerador local (`random.Random(f"{seed}:{brief_key}")`): e reproduzivel entre processos (nao
depende de `PYTHONHASHSEED`) e independente da ordem em que os pares sao vistos.

`BlindSide` so leva o que o responsavel julga: texto, alegacoes (com ou sem apoio na base) e se
a imagem tem fundo gerado por IA (o aviso e obrigatorio). Nunca leva percurso, custo, voltas,
pareceres do Revisor, ids de ativos nem de execucoes.
"""

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, cast

type EvalPath = Literal["simples", "especializado"]
type Side = Literal["a", "b"]
SIDES: Final[tuple[Side, Side]] = ("a", "b")


def side_a_path(seed: int, brief_key: str) -> EvalPath:
    """Percurso mostrado no lado A deste brief (deterministico pela semente)."""
    draw = random.Random(f"{seed}:{brief_key}").random()
    return "simples" if draw < 0.5 else "especializado"


def path_for_side(seed: int, brief_key: str, side: Side) -> EvalPath:
    """Percurso de um lado do par."""
    first = side_a_path(seed, brief_key)
    if side == "a":
        return first
    return "especializado" if first == "simples" else "simples"


@dataclass(frozen=True)
class BlindClaim:
    """Afirmacao do texto e se tem apoio na base (sem o id do item)."""

    texto: str
    has_support: bool


@dataclass(frozen=True)
class BlindSide:
    """Um lado do par como o responsavel o ve."""

    legenda: str
    hashtags: tuple[str, ...]
    apelo_a_acao: str
    alegacoes: tuple[BlindClaim, ...]
    alt_text: str
    is_ai_generated: bool


def blind_side(snapshot: Mapping[str, object]) -> BlindSide:
    """Constroi o lado a partir do `revision_snapshot` do item, deixando de fora o parecer."""
    texto = cast(Mapping[str, object], snapshot["texto"])
    claims = cast(list[Mapping[str, object]], texto["alegacoes"])
    return BlindSide(
        legenda=str(texto["legenda"]),
        hashtags=tuple(cast(list[str], texto["hashtags"])),
        apelo_a_acao=str(texto["apelo_a_acao"]),
        alegacoes=tuple(
            BlindClaim(str(claim["texto"]), claim.get("kb_ref") is not None) for claim in claims
        ),
        alt_text=str(snapshot["alt_text"]),
        is_ai_generated=bool(snapshot["is_ai_generated"]),
    )


@dataclass(frozen=True)
class BlindBrief:
    """O brief do par, como foi pedido aos dois percursos."""

    tema: str
    pilar: str
    objetivo: str
    canais: tuple[str, ...]


@dataclass(frozen=True)
class CurrentAnswer:
    """Resposta ativa do par (para corrigir): o id serve so de prova de versao no formulario."""

    id: str
    better: str
    errors_a: tuple[str, ...]
    errors_b: tuple[str, ...]
    edit_a: str
    edit_b: str
    note: str | None


@dataclass(frozen=True)
class BlindPair:
    """Par `index` (1..`total`, pela ordem do conjunto) de uma corrida."""

    index: int
    total: int
    brief: BlindBrief
    a: BlindSide
    b: BlindSide
    current: CurrentAnswer | None
