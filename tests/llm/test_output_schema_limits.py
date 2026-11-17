"""Esquemas de saida enviados ao modelo: o que o Ollama (llama.cpp) nao aguenta na gramatica.

O teste de fumo com o `qwen2.5:14b` mostrou dois casos em que o runner do Ollama cai (HTTP 500
"model runner has unexpectedly stopped") ao converter o `json_schema` em gramatica:

- `pattern` com `\\d{4}` (o `semana` do plano);
- uma repeticao grande, como `maxLength: 10000` na legenda (com 1500 funciona, com 2200 ja nao).

Os limites reais ficam em codigo (validadores); o esquema enviado ao modelo so leva os pequenos.
"""

import json

import pytest
from pydantic import BaseModel, ValidationError

from marketing_agent.agents.diretor.schemas import (
    Diagnostico,
    Estrategia,
    InterviewTurnOutput,
    PerfilMarca,
)
from marketing_agent.content.schemas import MAX_SCHEMA_CAPTION_CHARS, RascunhoTexto, Revisao
from marketing_agent.content.visual import RascunhoVisual
from marketing_agent.evals.simple_schema import PacoteSimples
from marketing_agent.planning.schemas import PlanoSemanal

SCHEMAS = [
    PlanoSemanal,
    RascunhoTexto,
    Revisao,
    InterviewTurnOutput,
    PerfilMarca,
    Diagnostico,
    Estrategia,
    RascunhoVisual,
    PacoteSimples,
]
# Maior limite de repeticao verificado a funcionar no Ollama com o `qwen2.5:14b`.
MAX_SAFE_BOUND = 1500
BOUND_KEYS = ("maxLength", "maxItems")


def collect(node: object, key: str) -> list[object]:
    """Todos os valores de `key` no esquema, a qualquer profundidade."""
    found: list[object] = []
    if isinstance(node, dict):
        found += [node[key]] if key in node else []
        for value in node.values():
            found += collect(value, key)
    elif isinstance(node, list):
        for item in node:
            found += collect(item, key)
    return found


@pytest.mark.parametrize("schema", SCHEMAS)
def test_schemas_have_no_regex_pattern(schema: type[BaseModel]) -> None:
    assert collect(schema.model_json_schema(), "pattern") == []


@pytest.mark.parametrize("schema", SCHEMAS)
def test_schemas_have_no_large_repetition_bounds(schema: type[BaseModel]) -> None:
    json_schema = schema.model_json_schema()
    for key in BOUND_KEYS:
        bounds = [value for value in collect(json_schema, key) if isinstance(value, int)]
        assert all(value <= MAX_SAFE_BOUND for value in bounds), (schema.__name__, key, bounds)


def test_caption_limit_is_still_enforced_in_code() -> None:
    body = {"legenda": "x", "hashtags": [], "apelo_a_acao": "", "alt_text": "", "alegacoes": []}
    RascunhoTexto.model_validate({**body, "legenda": "x" * MAX_SCHEMA_CAPTION_CHARS})
    with pytest.raises(ValidationError):
        RascunhoTexto.model_validate({**body, "legenda": "x" * (MAX_SCHEMA_CAPTION_CHARS + 1)})
    with pytest.raises(ValidationError):
        RascunhoTexto.model_validate({**body, "legenda": ""})
    assert "maxLength" not in json.dumps(RascunhoTexto.model_json_schema()["properties"]["legenda"])
