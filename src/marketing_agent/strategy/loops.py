"""Ciclos de LLM do diagnostico e da estrategia, e as etiquetas de dados dos prompts do Diretor.

Os artefactos ativos entram no pedido so como dado, dentro de etiquetas de delimitacao;
nenhum ciclo muda estado de artefactos: criam um `draft` e mais nada.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from marketing_agent.agents.diretor import DIRETOR_DIR
from marketing_agent.agents.diretor.schemas import Diagnostico, Estrategia
from marketing_agent.artifacts.store import ArtifactRecord, ArtifactType
from marketing_agent.harness.engine import RunContext
from marketing_agent.llm.types import Message, TextPart

# Unica lista de etiquetas de dados dos prompts do Diretor, do Copywriter e do Revisor: o padrao
# de neutralizacao nasce dela e `wrap_data` recusa etiquetas que nao estejam nela.
DATA_TAGS: Final = (
    "perfil_marca",
    "diagnostico",
    "estrategia",
    "propostas_aceites",
    "base_conhecimento",
    "brief",
    "exemplos_estilo",
    "exemplos_negativos",
    "rascunho",
    "comentario_responsavel",
    "notas_revisor",
)

# Tolera espacos e as quebras de linha ja escapadas pelo JSON (`\n` literal). Depois do nome
# aceita uma cauda sem `<` nem `>` (atributos, `/`), para apanhar `</tag x="1">` e `<tag/>`;
# `\b` evita apanhar nomes mais longos como `<estrategia_extra>`.
_SPACE = r"(?:\s|\\[nrt])*"
_TAG_NAMES = "|".join(map(re.escape, DATA_TAGS))
_DELIMITER_PATTERN = re.compile(rf"<{_SPACE}/?{_SPACE}(?:{_TAG_NAMES})\b[^<>]*>", re.IGNORECASE)
_NEUTRALIZED = "[etiqueta removida]"

DIAGNOSIS_REQUEST = "Produz agora o diagnostico com base nos dados abaixo."
STRATEGY_REQUEST = "Produz agora a estrategia com base nos dados abaixo."


@dataclass(frozen=True)
class DiagnosisPayload:
    """Entrada do diagnostico: perfil ativo (de confianca) e id do diagnostico ativo (pai)."""

    perfil_marca: Mapping[str, object]
    parent_id: str | None


@dataclass(frozen=True)
class StrategyPayload:
    """Entrada da estrategia: perfil e diagnostico ativos e a estrategia ativa (pai)."""

    perfil_marca: Mapping[str, object]
    diagnostico: Mapping[str, object]
    parent_id: str | None


def neutralize(text: str) -> str:
    """Remove etiquetas de delimitacao do texto, para que ele nao saia do bloco de dados."""
    return _DELIMITER_PATTERN.sub(_NEUTRALIZED, text)


def wrap_data(tag: str, data: Mapping[str, object]) -> str:
    """Serializa `data` como JSON e delimita-o com `tag`, neutralizando etiquetas la dentro.

    `ValueError` se `tag` nao esta em `DATA_TAGS`: sem isso o fecho dela nao seria neutralizado.
    """
    if tag not in DATA_TAGS:
        raise ValueError(f"etiqueta {tag!r} fora de DATA_TAGS; acrescentar la antes de usar")
    body = neutralize(json.dumps(data, ensure_ascii=False, indent=2))
    return f"<{tag}>\n{body}\n</{tag}>"


def build_request(instruction: str, *blocks: str) -> list[Message]:
    """Mensagem unica do utilizador: a instrucao seguida dos blocos de dados delimitados."""
    return [Message("user", [TextPart("\n\n".join([instruction, *blocks]))])]


class DiagnosisLoop:
    """Gera o diagnostico e cria o artefacto `diagnostico` em `draft`."""

    def run(self, ctx: RunContext, payload: DiagnosisPayload) -> ArtifactRecord:
        """Chama o LLM com o perfil ativo; a submissao e decisao do servico."""
        messages = build_request(DIAGNOSIS_REQUEST, wrap_data("perfil_marca", payload.perfil_marca))
        output = ctx.call_llm(ctx.spec.load_prompt(DIRETOR_DIR), messages, Diagnostico)
        return ctx.create_artifact(
            ArtifactType.DIAGNOSTICO, output.model_dump(mode="json"), parent_id=payload.parent_id
        )


class StrategyLoop:
    """Gera a estrategia e cria o artefacto `estrategia` em `draft`."""

    def run(self, ctx: RunContext, payload: StrategyPayload) -> ArtifactRecord:
        """Chama o LLM com perfil e diagnostico ativos; a submissao e decisao do servico."""
        messages = build_request(
            STRATEGY_REQUEST,
            wrap_data("perfil_marca", payload.perfil_marca),
            wrap_data("diagnostico", payload.diagnostico),
        )
        output = ctx.call_llm(ctx.spec.load_prompt(DIRETOR_DIR), messages, Estrategia)
        return ctx.create_artifact(
            ArtifactType.ESTRATEGIA, output.model_dump(mode="json"), parent_id=payload.parent_id
        )
