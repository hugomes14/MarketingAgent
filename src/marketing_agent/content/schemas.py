"""Esquemas de saida do Copywriter (`RascunhoTexto`) e do Revisor (`Revisao`).

Os limites daqui so protegem contra saidas absurdas; os limites por canal (que dependem do
contexto da chamada) verificam-se em `content.validation`.
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from marketing_agent.planning.schemas import KbRef

# Nove violacoes possiveis do parecer (spec 2A, secao 5); `instrucao_suspeita` so informa.
Violacao = Literal[
    "alegacao_sem_apoio",
    "preco_sem_validade",
    "voz_incoerente",
    "fora_do_tema",
    "limite_canal",
    "promessa_indevida",
    "dados_pessoais",
    "instrucao_suspeita",
    "outro",
]
Veredicto = Literal["aprovar", "rever", "rejeitar"]

# Teto de seguranca do esquema, acima do maior limite de canal (Facebook, 5000).
MAX_SCHEMA_CAPTION_CHARS = 10_000
MAX_SCHEMA_HASHTAGS = 100
# Cada hashtag limita-se a 100 caracteres: o formato (`#`, sem espacos) e do validador.
Hashtag = Annotated[str, Field(max_length=100)]
MAX_CALL_TO_ACTION_CHARS = 300
MAX_ALT_TEXT_CHARS = 500


def _check_caption_length(value: str) -> str:
    # Validador em codigo e nao `max_length`: um `maxLength` de milhares no esquema JSON enviado
    # ao modelo faz o runner do Ollama (llama.cpp) cair com um 500 ao montar a gramatica (com
    # 1500 funciona, com 2200 nao). Os limites por canal sao verificados em `validate_draft`.
    if len(value) > MAX_SCHEMA_CAPTION_CHARS:
        raise ValueError("legenda acima do teto do esquema")
    return value


class Alegacao(BaseModel):
    """Afirmacao factual da legenda e o item da base que a apoia (`None` = sem apoio)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    texto: str = Field(min_length=1, max_length=500)
    kb_ref: KbRef | None


class RascunhoTexto(BaseModel):
    """Texto de uma publicacao; `alt_text` pode vir vazio aqui e e recusado pelo validador."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    legenda: Annotated[str, Field(min_length=1), AfterValidator(_check_caption_length)]
    hashtags: list[Hashtag] = Field(max_length=MAX_SCHEMA_HASHTAGS)
    apelo_a_acao: str = Field(max_length=MAX_CALL_TO_ACTION_CHARS)
    alt_text: str = Field(max_length=MAX_ALT_TEXT_CHARS)
    alegacoes: list[Alegacao] = Field(max_length=30)


class Revisao(BaseModel):
    """Parecer do Revisor: veredicto, violacoes do enum e notas curtas para o Copywriter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    veredicto: Veredicto
    violacoes: list[Violacao] = Field(max_length=9)
    notas: str = Field(max_length=1500)
