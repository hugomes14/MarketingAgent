"""Ciclos de LLM da entrevista: um turno de pergunta e a geracao do perfil de marca.

O texto do utilizador entra nos prompts so como dado, dentro de `<resposta_do_utilizador>`;
nenhum ciclo muda o estado de artefactos: o de perfil cria um `draft` e mais nada.
"""

import re
from dataclasses import dataclass
from typing import Literal

from marketing_agent.agents.diretor import DIRETOR_DIR
from marketing_agent.agents.diretor.gap_map import GapItem
from marketing_agent.agents.diretor.schemas import InterviewTurnOutput, PerfilMarca
from marketing_agent.artifacts.store import ArtifactRecord, ArtifactType
from marketing_agent.harness.engine import RunContext
from marketing_agent.llm.types import Message, TextPart

# Etiquetas de delimitacao: qualquer ocorrencia no texto do utilizador e neutralizada.
_DELIMITER_PATTERN = re.compile(r"<\s*/?\s*(resposta_do_utilizador|mapa_de_lacunas)\s*>", re.I)
_NEUTRALIZED = "[etiqueta removida]"

# Mensagens fixas (nossas, nao do utilizador): a conversa tem de comecar por `user`.
OPENING_MESSAGE = "Inicia a entrevista de descoberta."
PROFILE_REQUEST_MESSAGE = "Produz agora o perfil de marca com base na entrevista."


@dataclass(frozen=True)
class TurnLine:
    """Um turno guardado da conversa; `role` e `user` ou `director`."""

    role: Literal["user", "director"]
    content: str


@dataclass(frozen=True)
class InterviewPayload:
    """Entrada dos ciclos: historico guardado, mapa atual e (so no turno) a nova resposta."""

    history: tuple[TurnLine, ...]
    gap_map: list[GapItem]
    new_answer: str | None = None


def neutralize(text: str) -> str:
    """Remove etiquetas de delimitacao do texto, para que ele nao saia do bloco de dados."""
    return _DELIMITER_PATTERN.sub(_NEUTRALIZED, text)


def wrap_answer(text: str) -> str:
    """Delimita a resposta do utilizador como dado."""
    return f"<resposta_do_utilizador>{neutralize(text)}</resposta_do_utilizador>"


def render_gap_map(items: list[GapItem]) -> str:
    """Mapa de lacunas em texto, uma linha por topico, dentro do bloco de dados."""
    lines = []
    for item in items:
        note = neutralize(" ".join(item.note.split()))
        lines.append(f"- {item.topic} [{item.status}] ({item.origin or 'sem origem'}): {note}")
    return "<mapa_de_lacunas>\n" + "\n".join(lines) + "\n</mapa_de_lacunas>"


def _system_prompt(ctx: RunContext, gap_map: list[GapItem]) -> str:
    return f"{ctx.spec.load_prompt(DIRETOR_DIR)}\n\n{render_gap_map(gap_map)}"


def _history_messages(history: tuple[TurnLine, ...]) -> list[Message]:
    """Abertura fixa mais o historico; o Diretor fala como `assistant`."""
    messages = [Message("user", [TextPart(OPENING_MESSAGE)])]
    for line in history:
        if line.role == "user":
            messages.append(Message("user", [TextPart(wrap_answer(line.content))]))
        else:
            messages.append(Message("assistant", [TextPart(line.content)]))
    return messages


class InterviewTurnLoop:
    """Um turno: devolve a proxima pergunta e as atualizacoes do mapa de lacunas."""

    def run(self, ctx: RunContext, payload: InterviewPayload) -> InterviewTurnOutput:
        """Chama o LLM com o historico e a nova resposta (se houver) no fim."""
        messages = _history_messages(payload.history)
        if payload.new_answer is not None:
            messages.append(Message("user", [TextPart(wrap_answer(payload.new_answer))]))
        system = _system_prompt(ctx, payload.gap_map)
        return ctx.call_llm(system, messages, InterviewTurnOutput)


class ProfileLoop:
    """Gera o perfil da marca e cria o artefacto `perfil_marca` em `draft`."""

    def run(self, ctx: RunContext, payload: InterviewPayload) -> ArtifactRecord:
        """Chama o LLM e guarda a saida como rascunho; a submissao e decisao do servico."""
        messages = _history_messages(payload.history)
        messages.append(Message("user", [TextPart(PROFILE_REQUEST_MESSAGE)]))
        system = _system_prompt(ctx, payload.gap_map)
        profile = ctx.call_llm(system, messages, PerfilMarca)
        return ctx.create_artifact(ArtifactType.PERFIL_MARCA, profile.model_dump(mode="json"))
