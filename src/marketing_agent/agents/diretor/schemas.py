"""Esquemas de saida do Diretor: entrevista, perfil de marca, diagnostico e estrategia."""

from typing import Final, Literal

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


class Achado(BaseModel):
    """Afirmacao do diagnostico: `facto` exige evidencia e origem, `hipotese` nao."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    afirmacao: str
    tipo: Literal["facto", "hipotese"]
    evidencia: str | None
    origem: Origin | None


class Diagnostico(BaseModel):
    """Diagnostico da marca: onde esta, o que se sabe e o que ainda e hipotese."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    oferta_prioritaria: str
    cliente_prioritario: str
    geografia: str
    motivos_compra: str
    motivos_perda: str
    capacidade_entrega: str
    provas_disponiveis: str
    percurso_contacto: str
    adequacao_canais: str
    achados: list[Achado]
    riscos: list[str]
    alternativas: list[str]


class Objetivo(BaseModel):
    """Objetivo da estrategia e o indicador que o mede."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    descricao: str
    indicador: str
    alvo: str | None


class PropostaOferta(BaseModel):
    """Proposta de oferta; so entra na vista ativa se o responsavel a aceitar."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposta: str
    justificacao: str


class Experiencia(BaseModel):
    """Experiencia a testar: hipotese, metrica e prazo (validado em 1..90 dias)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hipotese: str
    metrica: str
    prazo_dias: int


class Estrategia(BaseModel):
    """Estrategia proposta; o posicionamento e `hipotese` ate o responsavel o decidir."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objetivos: list[Objetivo]
    posicionamento: str
    posicionamento_estado: Literal["hipotese", "decidido"]
    pilares: list[str]
    propostas_oferta: list[PropostaOferta]
    experiencias: list[Experiencia]
    indicadores_minimos: list[str]
