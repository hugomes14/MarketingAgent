"""Tipos do contrato LLM: pedido, resposta, cliente e erros de transporte."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

from marketing_agent.budget.pricing import Usage
from marketing_agent.config import Capability

# Timeout de UMA chamada ao fornecedor (adaptadores). O motor so verifica `max_duration_s` ANTES
# de cada passo, por isso uma execucao pode ultrapassa-lo ate este valor: a lease dos jobs e o
# limiar de reservas orfas somam-no (ver `startup`).
LLM_CALL_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class TextPart:
    """Trecho de texto de uma mensagem."""

    text: str


@dataclass(frozen=True)
class ImagePart:
    """Imagem de entrada, em bytes, com o tipo MIME (ex.: image/png)."""

    media_type: str
    # Fora do `repr`: um pedido registado por engano nao deve despejar a imagem.
    data: bytes = field(repr=False)


ContentPart = TextPart | ImagePart


@dataclass(frozen=True)
class Message:
    """Mensagem de uma conversa, composta por uma ou mais partes."""

    role: Literal["user", "assistant"]
    content: list[ContentPart]


@dataclass(frozen=True)
class LlmRequest:
    """Pedido a um modelo; `json_schema`, se dado, pede saida estruturada."""

    model_id: str
    system: str
    messages: list[Message]
    max_output_tokens: int
    json_schema: Mapping[str, object] | None = None


class StopReason(StrEnum):
    """Motivo pelo qual o modelo parou de gerar."""

    end = "end"
    max_tokens = "max_tokens"
    refusal = "refusal"
    other = "other"


@dataclass(frozen=True)
class LlmResponse:
    """Resposta de um modelo, com o consumo de tokens para o orcamento."""

    text: str
    usage: Usage
    provider_request_id: str | None
    resolved_model: str
    stop_reason: StopReason


class LlmClient(Protocol):
    """Cliente de um fornecedor de modelos."""

    capabilities: frozenset[Capability]

    def complete(self, request: LlmRequest) -> LlmResponse:
        """Envia o pedido e devolve a resposta; falhas de transporte levantam erros proprios."""
        ...


class LlmNetworkError(Exception):
    """O pedido nao chegou ao fornecedor: nao houve processamento nem faturacao."""


class LlmUnknownOutcome(Exception):
    """Resultado desconhecido: o pedido pode ter sido processado e faturado."""
