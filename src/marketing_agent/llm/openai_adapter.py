"""Adaptador OpenAI: converte `LlmRequest` para a API Chat Completions e a resposta para o contrato.

O SDK `openai` assenta em `httpx2`, tal como o da Anthropic, por isso a classificacao das falhas
de transporte e partilhada (`transport_errors`). O SDK e criado sem repeticoes automaticas
(`max_retries=0`): quem decide repetir e o motor, que precisa de contar cada tentativa no orcamento.
"""

import base64
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import openai
from openai.types.chat import (
    ChatCompletionContentPartParam,
    ChatCompletionContentPartTextParam,
    ChatCompletionMessageParam,
)
from openai.types.shared_params import ResponseFormatJSONSchema

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

# A API rejeita partes de texto vazias; este marcador ocupa o lugar de uma parte sem texto.
EMPTY_TEXT_PLACEHOLDER = "(sem resposta)"

_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_STOP_REASONS = {"stop": StopReason.end, "length": StopReason.max_tokens}


def _label(field: str) -> str:
    return f"resposta OpenAI: campo '{field}'"


def _require_str(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{_label(field)} em falta ou nao e texto")
    return value


def _optional_str(raw: Mapping[str, object], field: str) -> str | None:
    value = raw.get(field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{_label(field)} nao e texto")
    return value


def _require_mapping(raw: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = raw.get(field)
    if not isinstance(value, Mapping):
        raise ValueError(f"{_label(field)} em falta ou nao e um objeto")
    return value


def _token_count(usage: Mapping[str, object], field: str) -> int:
    value = usage.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{_label('usage.' + field)} em falta ou nao e inteiro")
    return value


def _reasoning_tokens(raw_usage: Mapping[str, object]) -> int | None:
    """Le `completion_tokens_details.reasoning_tokens`; ausente (ou nulo) significa desconhecido."""
    if raw_usage.get("completion_tokens_details") is None:
        return None
    details = _require_mapping(raw_usage, "completion_tokens_details")
    if details.get("reasoning_tokens") is None:
        return None
    return _token_count(details, "reasoning_tokens")


def _map_usage(raw_usage: Mapping[str, object]) -> Usage:
    # `completion_tokens` ja inclui o raciocinio; `reasoning_tokens` e so o detalhe (ver `Usage`).
    return Usage(
        input_tokens=_token_count(raw_usage, "prompt_tokens"),
        output_tokens=_token_count(raw_usage, "completion_tokens"),
        reasoning_tokens=_reasoning_tokens(raw_usage),
    )


def _map_stop_reason(message: Mapping[str, object], finish_reason: str | None) -> StopReason:
    # Recusa do modelo (campo `refusal`) ou filtro de conteudo: nao ha resposta utilizavel.
    if _optional_str(message, "refusal") or finish_reason == "content_filter":
        return StopReason.refusal
    return _STOP_REASONS.get(finish_reason or "", StopReason.other)


def _first_choice(raw: Mapping[str, object]) -> Mapping[str, object]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"{_label('choices')} em falta, vazio ou nao e uma lista")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ValueError(f"{_label('choices')} tem um item que nao e um objeto")
    return first


def to_llm_response(raw: Mapping[str, object]) -> LlmResponse:
    """Converte o `ChatCompletion` cru (`to_dict()`) numa `LlmResponse`.

    Raises:
        ValueError: se `choices`, `message`, `usage`, `id` ou `model` tiverem tipo invalido.
    """
    choice = _first_choice(raw)
    message = _require_mapping(choice, "message")
    return LlmResponse(
        text=_optional_str(message, "content") or "",
        usage=_map_usage(_require_mapping(raw, "usage")),
        provider_request_id=_require_str(raw, "id"),
        resolved_model=_require_str(raw, "model"),
        stop_reason=_map_stop_reason(message, _optional_str(choice, "finish_reason")),
    )


def classify_transport_error(exc: Exception) -> type[Exception] | None:
    """Diz se o pedido chegou ao fornecedor: `LlmNetworkError`, `LlmUnknownOutcome` ou `None`.

    `None` significa "nao classificado" (4xx como 400/401/403/404 e qualquer outra excecao): o
    chamador deve deixar a excecao original propagar, e o motor trata-a como resultado desconhecido.
    """
    if isinstance(exc, openai.APIConnectionError):
        # APITimeoutError e subclasse: a causa (httpx2) separa a fase de ligacao da de leitura.
        return classify_connection_cause(exc.__cause__)
    if isinstance(exc, openai.APIStatusError):
        return classify_http_status(exc.status_code)
    return None


def _to_content_part(part: ContentPart) -> ChatCompletionContentPartParam:
    if isinstance(part, ImagePart):
        if part.media_type not in _IMAGE_MEDIA_TYPES:
            raise ValueError(f"tipo de imagem nao suportado pela OpenAI: {part.media_type}")
        data = base64.b64encode(part.data).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{part.media_type};base64,{data}"}}
    return {"type": "text", "text": part.text or EMPTY_TEXT_PLACEHOLDER}


def _to_text_part(part: ContentPart) -> ChatCompletionContentPartTextParam:
    if isinstance(part, ImagePart):
        raise ValueError("mensagens do assistente nao podem conter imagens")
    return {"type": "text", "text": part.text or EMPTY_TEXT_PLACEHOLDER}


def _to_sdk_message(message: Message) -> ChatCompletionMessageParam:
    if message.role == "user":
        return {"role": "user", "content": [_to_content_part(part) for part in message.content]}
    return {"role": "assistant", "content": [_to_text_part(part) for part in message.content]}


def _to_system_message(
    system: str, role: Literal["developer", "system"]
) -> ChatCompletionMessageParam:
    # Dois ramos explicitos: o tipo da mensagem depende do papel (TypedDict discriminado).
    if role == "system":
        return {"role": "system", "content": system}
    return {"role": "developer", "content": system}


def _response_format(request: LlmRequest) -> ResponseFormatJSONSchema | openai.Omit:
    if request.json_schema is None:
        return openai.omit
    # strict=False: nao garantimos que o esquema cumpre o subconjunto do modo estrito (todas as
    # propriedades em `required`, `additionalProperties: false`); estrito rejeitaria o pedido.
    return {
        "type": "json_schema",
        "json_schema": {"name": "resposta", "schema": dict(request.json_schema), "strict": False},
    }


class OpenAiClient:
    """Cliente `LlmClient` para a API Chat Completions da OpenAI (ou compativel)."""

    def __init__(
        self,
        api_key_file: Path,
        capabilities: frozenset[Capability],
        base_url: str | None = None,
        timeout_s: float = LLM_CALL_TIMEOUT_S,
        sdk_client: openai.OpenAI | None = None,
        system_role: Literal["developer", "system"] = "developer",
    ) -> None:
        """Le a chave de `api_key_file` ja na construcao (nunca aparece em erros ou logs).

        `base_url` aponta para um servidor compativel com a API (ex.: emulador local); `None` usa
        o endereco oficial. `sdk_client` existe so para os testes injetarem um SDK falso.
        `system_role` e o papel da mensagem do prompt de sistema: `developer` na API oficial,
        `system` em servidores compativeis (Ollama, LM Studio), que ignoram `developer`.
        """
        self.capabilities = capabilities
        self._system_role = system_role
        key = read_secret_file(api_key_file)
        self._sdk = sdk_client or _build_sdk(key, timeout_s, base_url)
        effective_url = base_url or str(getattr(self._sdk, "base_url", "https://api.openai.com/v1"))
        host = urlsplit(effective_url).hostname
        self._namespace_response_ids = host is not None and not (
            host == "api.openai.com" or host.endswith(".api.openai.com")
        )

    def complete(self, request: LlmRequest) -> LlmResponse:
        """Envia o pedido; falhas classificaveis viram `LlmNetworkError`/`LlmUnknownOutcome`.

        Erros nao classificados (ex.: 400/401) propagam inalterados.
        """
        # Sem sampling (temperature/top_p): os modelos de raciocinio rejeitam-nos. O limite e
        # `max_completion_tokens` (`max_tokens` esta obsoleto e e rejeitado por esses modelos).
        # O prompt de sistema vai como `developer` na API oficial (o SDK indica-o em vez de
        # `system` desde o o1); os servidores compativeis exigem `system` (ver `system_role`).
        messages: list[ChatCompletionMessageParam] = [
            _to_system_message(request.system, self._system_role),
            *(_to_sdk_message(message) for message in request.messages),
        ]
        try:
            completion = self._sdk.chat.completions.create(
                model=request.model_id,
                messages=messages,
                max_completion_tokens=request.max_output_tokens,
                response_format=_response_format(request),
            )
        except Exception as exc:
            error_type = classify_transport_error(exc)
            if error_type is None:
                raise
            # So o nome da classe: a mensagem do SDK pode ecoar partes do pedido.
            raise error_type(f"falha de transporte OpenAI: {type(exc).__name__}") from exc
        response = to_llm_response(completion.to_dict())
        if self._namespace_response_ids:
            # Ollama pode repetir ids curtos entre chamadas/modelos. A reserva continua a
            # garantir idempotencia; o id local distingue chamadas realmente distintas e
            # preserva o id original como sufixo para correlacao, sem relaxar a UNIQUE da base.
            response = replace(
                response, provider_request_id=f"compat:{uuid4().hex}:{response.provider_request_id}"
            )
        return response


def _build_sdk(api_key: str, timeout_s: float, base_url: str | None) -> openai.OpenAI:
    # Sem `base_url` o SDK usa o endereco oficial (e a variavel OPENAI_BASE_URL, se existir).
    if base_url is None:
        return openai.OpenAI(api_key=api_key, max_retries=0, timeout=timeout_s)
    return openai.OpenAI(api_key=api_key, max_retries=0, timeout=timeout_s, base_url=base_url)
