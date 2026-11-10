"""Adaptador Anthropic: converte `LlmRequest` para o SDK e a resposta/erros para o contrato LLM.

O SDK `anthropic` 2.x assenta em `httpx2` (nao `httpx`): e a ele que se vao buscar os tipos das
causas dos erros de ligacao. O SDK e criado sem repeticoes automaticas (`max_retries=0`), porque
quem decide repetir e o motor, que precisa de contar cada tentativa no orcamento.
"""

import base64
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

import anthropic
from anthropic.types import ImageBlockParam, MessageParam, OutputConfigParam, TextBlockParam

from marketing_agent.budget.pricing import Usage
from marketing_agent.config import Capability
from marketing_agent.llm.transport_errors import classify_connection_cause, classify_http_status
from marketing_agent.llm.types import (
    LLM_CALL_TIMEOUT_S,
    ContentPart,
    ImagePart,
    LlmRequest,
    LlmResponse,
    Message,
    StopReason,
)
from marketing_agent.secrets import read_secret_file

# A API rejeita blocos de texto vazios; este marcador ocupa o lugar de uma parte sem texto.
EMPTY_TEXT_PLACEHOLDER = "(sem resposta)"

_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_STOP_REASONS = {
    "end_turn": StopReason.end,
    "max_tokens": StopReason.max_tokens,
    "refusal": StopReason.refusal,
}


def _require_str(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str):
        raise ValueError(f"resposta Anthropic: campo '{field}' em falta ou nao e texto")
    return value


def _token_count(usage: Mapping[str, object], field: str, *, is_required: bool) -> int:
    value = usage.get(field)
    if value is None and not is_required:
        return 0
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"resposta Anthropic: usage.{field} em falta ou nao e inteiro")
    return value


def _join_text_blocks(content: object) -> str:
    """Junta os blocos `text` por ordem, sem separador; `thinking` e restantes sao ignorados."""
    if not isinstance(content, list):
        raise ValueError("resposta Anthropic: campo 'content' em falta ou nao e uma lista")
    texts: list[str] = []
    for block in content:
        if not isinstance(block, Mapping):
            raise ValueError("resposta Anthropic: 'content' tem um bloco que nao e um objeto")
        if block.get("type") == "text":
            texts.append(_require_str(block, "text"))
    return "".join(texts)


def _map_usage(raw_usage: object) -> Usage:
    if not isinstance(raw_usage, Mapping):
        raise ValueError("resposta Anthropic: campo 'usage' em falta ou nao e um objeto")
    # Entrada = input + escrita + leitura de cache; `output_tokens` ja inclui o raciocinio.
    input_tokens = (
        _token_count(raw_usage, "input_tokens", is_required=True)
        + _token_count(raw_usage, "cache_creation_input_tokens", is_required=False)
        + _token_count(raw_usage, "cache_read_input_tokens", is_required=False)
    )
    output_tokens = _token_count(raw_usage, "output_tokens", is_required=True)
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens, reasoning_tokens=None)


def _map_stop_reason(raw_reason: object) -> StopReason:
    if raw_reason is not None and not isinstance(raw_reason, str):
        raise ValueError("resposta Anthropic: campo 'stop_reason' nao e texto")
    return _STOP_REASONS.get(raw_reason or "", StopReason.other)


def to_llm_response(raw: Mapping[str, object]) -> LlmResponse:
    """Converte a mensagem crua da API (`Message.to_dict()`) numa `LlmResponse`.

    Raises:
        ValueError: se `content`, `usage`, `stop_reason`, `id` ou `model` tiverem tipo invalido.
    """
    return LlmResponse(
        text=_join_text_blocks(raw.get("content")),
        usage=_map_usage(raw.get("usage")),
        provider_request_id=_require_str(raw, "id"),
        resolved_model=_require_str(raw, "model"),
        stop_reason=_map_stop_reason(raw.get("stop_reason")),
    )


def classify_transport_error(exc: Exception) -> type[Exception] | None:
    """Diz se o pedido chegou ao fornecedor: `LlmNetworkError`, `LlmUnknownOutcome` ou `None`.

    `None` significa "nao classificado" (4xx como 400/401/403/404 e qualquer outra excecao): o
    chamador deve deixar a excecao original propagar, e o motor trata-a como resultado desconhecido.
    """
    if isinstance(exc, anthropic.APIConnectionError):
        # APITimeoutError e subclasse: a causa (httpx2) separa a fase de ligacao da de leitura.
        return classify_connection_cause(exc.__cause__)
    if isinstance(exc, anthropic.APIStatusError):
        return classify_http_status(exc.status_code)
    return None


def _to_content_block(part: ContentPart) -> TextBlockParam | ImageBlockParam:
    if isinstance(part, ImagePart):
        if part.media_type not in _IMAGE_MEDIA_TYPES:
            raise ValueError(f"tipo de imagem nao suportado pela Anthropic: {part.media_type}")
        media_type = cast(
            Literal["image/jpeg", "image/png", "image/gif", "image/webp"], part.media_type
        )
        data = base64.b64encode(part.data).decode("ascii")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    return {"type": "text", "text": part.text or EMPTY_TEXT_PLACEHOLDER}


def _to_sdk_message(message: Message) -> MessageParam:
    blocks = [_to_content_block(part) for part in message.content]
    if message.role == "user":
        return {"role": "user", "content": blocks}
    return {"role": "assistant", "content": blocks}


class AnthropicClient:
    """Cliente `LlmClient` para a API Messages da Anthropic."""

    def __init__(
        self,
        api_key_file: Path,
        capabilities: frozenset[Capability],
        timeout_s: float = LLM_CALL_TIMEOUT_S,
        base_url: str | None = None,
        sdk_client: anthropic.Anthropic | None = None,
    ) -> None:
        """Le a chave de `api_key_file` ja na construcao (nunca aparece em erros ou logs).

        `base_url` aponta para um servidor compativel com a API Messages (ex.: emulador local);
        `None` usa o endereco oficial. `sdk_client` existe so para os testes injetarem um SDK falso.
        """
        self.capabilities = capabilities
        key = read_secret_file(api_key_file)
        self._sdk = sdk_client or _build_sdk(key, timeout_s, base_url)

    def complete(self, request: LlmRequest) -> LlmResponse:
        """Envia o pedido; falhas classificaveis viram `LlmNetworkError`/`LlmUnknownOutcome`.

        Erros nao classificados (ex.: 400/401) propagam inalterados.
        """
        try:
            message = self._sdk.messages.create(
                model=request.model_id,
                system=request.system,
                messages=[_to_sdk_message(message) for message in request.messages],
                max_tokens=request.max_output_tokens,
                output_config=_output_config(request),
            )
        except Exception as exc:
            error_type = classify_transport_error(exc)
            if error_type is None:
                raise
            # So o nome da classe: a mensagem do SDK pode ecoar partes do pedido.
            raise error_type(f"falha de transporte Anthropic: {type(exc).__name__}") from exc
        return to_llm_response(message.to_dict())


def _output_config(request: LlmRequest) -> OutputConfigParam | anthropic.Omit:
    if request.json_schema is None:
        return anthropic.omit
    return {"format": {"type": "json_schema", "schema": dict(request.json_schema)}}


def _build_sdk(api_key: str, timeout_s: float, base_url: str | None) -> anthropic.Anthropic:
    # Sem `base_url` o SDK usa o endereco oficial (e a variavel ANTHROPIC_BASE_URL, se existir).
    if base_url is None:
        return anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=timeout_s)
    return anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=timeout_s, base_url=base_url)
