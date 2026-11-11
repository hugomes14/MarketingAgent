"""Gerador de imagens da OpenAI (API Images, modelos GPT Image).

Dinheiro em jogo: cada chamada fatura. Por isso o SDK e criado sem repeticoes (`max_retries=0`;
uma repeticao automatica poderia faturar duas vezes) e as falhas de transporte sao classificadas
como no texto (`classify_transport_error`): so a que nunca saiu e segura para libertar a reserva.

Higiene da chave: as excecoes do SDK guardam o pedido (cabecalho `Authorization` com a chave
inteira), a resposta e o corpo. As excecoes que daqui saem sao levantadas FORA do `except`, com
mensagens fixas, para nao reterem a excecao do SDK nem como `__cause__` nem como `__context__`.

Ambiente: `base_url` e `api_key` sao explicitos, mas o cliente continua a herdar do ambiente do
operador OPENAI_ORG_ID, OPENAI_PROJECT_ID, OPENAI_CUSTOM_HEADERS e HTTPS_PROXY (controlados por
quem opera o servico). A unica limitacao de tamanho da resposta e o teto de 20 MB da imagem
descodificada (o SDK le o corpo inteiro antes de o analisar); os 180 s de timeout valem por fase
(ligacao, escrita, leitura), nao para a chamada toda.
"""

import base64
import binascii
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Protocol

import openai
from openai.types import ImagesResponse

from marketing_agent.images.generation import (
    MAX_IMAGE_BYTES,
    ImageProviderError,
    ImageRefused,
    ImageRequest,
    ImageResponseInvalid,
    ImageResult,
)
from marketing_agent.llm.openai_adapter import classify_transport_error
from marketing_agent.llm.types import IMAGE_CALL_TIMEOUT_S
from marketing_agent.secrets import read_secret_file

OFFICIAL_BASE_URL: Final = "https://api.openai.com/v1"
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_MODERATION_BLOCKED: Final = "moderation_blocked"
# Comprimento maximo do base64 de MAX_IMAGE_BYTES (4 caracteres por 3 bytes, com preenchimento).
_MAX_B64_CHARS: Final = (MAX_IMAGE_BYTES + 2) // 3 * 4


class _RawImages(Protocol):
    """A parte da resposta crua do SDK (`with_raw_response`) que este modulo usa."""

    @property
    def headers(self) -> Mapping[str, str]: ...

    def parse(self) -> ImagesResponse: ...


def _failure_for(exc: Exception) -> Exception | None:
    """Traduz uma excecao do SDK numa nossa, sem a reter; `None` se nao for do SDK.

    O chamador levanta o resultado FORA do `except` (ver o docstring do modulo).
    """
    if isinstance(exc, openai.BadRequestError) and exc.code == _MODERATION_BLOCKED:
        return ImageRefused("o fornecedor recusou o conteudo da imagem")
    name = type(exc).__name__
    error_type = classify_transport_error(exc)
    if error_type is not None:
        # So o nome da classe: a mensagem do SDK pode ecoar partes do pedido.
        return error_type(f"falha de transporte OpenAI Images: {name}")
    if isinstance(exc, openai.APIError):
        status = exc.status_code if isinstance(exc, openai.APIStatusError) else None
        return ImageProviderError(f"erro do fornecedor de imagens: {name}", status)
    return None


def _parse_body(raw: _RawImages) -> ImagesResponse:
    """Le o corpo 2xx; se for ilegivel o fornecedor respondeu (possivelmente faturado)."""
    try:
        return raw.parse()
    except Exception:
        pass
    # Fora do `except`: nao reter a excecao de leitura como causa nem contexto.
    raise ImageResponseInvalid("resposta de imagem ilegivel")


def _decode_png(b64_json: str | None) -> bytes:
    """Descodifica e valida o PNG da resposta; as mensagens sao fixas (nunca o conteudo)."""
    if not b64_json:
        raise ImageResponseInvalid("resposta de imagem sem b64_json")
    # Recusa antes de descodificar: o comprimento do base64 ja diz o tamanho final.
    if len(b64_json) > _MAX_B64_CHARS:
        raise ImageResponseInvalid("imagem acima do limite de 20 MB")
    try:
        data = base64.b64decode(b64_json, validate=True)
    except (binascii.Error, ValueError):
        raise ImageResponseInvalid("b64_json de imagem invalido") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageResponseInvalid("imagem acima do limite de 20 MB")
    if not data.startswith(_PNG_SIGNATURE):
        raise ImageResponseInvalid("a imagem devolvida nao e PNG")
    return data


class OpenAiImageGenerator:
    """`ImageGenerator` sobre `images.generate` da OpenAI, sempre no endereco oficial."""

    provider = "openai"

    def __init__(
        self,
        api_key_file: Path,
        timeout_s: float = IMAGE_CALL_TIMEOUT_S,
        sdk_client: openai.OpenAI | None = None,
    ) -> None:
        """Le a chave de `api_key_file` ja na construcao (nunca aparece em erros ou logs).

        `sdk_client` existe so para os testes injetarem um SDK falso.

        Raises:
            SecretFileError: se o ficheiro da chave faltar, estiver vazio ou for invalido.
        """
        key = read_secret_file(api_key_file)
        # `base_url` explicito: sem ele o SDK obedece a OPENAI_BASE_URL (ex.: Ollama usado para
        # o texto) e enviaria a chave paga das imagens para outro servidor.
        self._sdk = sdk_client or openai.OpenAI(
            api_key=key, max_retries=0, timeout=timeout_s, base_url=OFFICIAL_BASE_URL
        )

    def generate(self, request: ImageRequest) -> ImageResult:
        """Gera uma imagem PNG opaca (uma por chamada).

        Raises:
            LlmNetworkError: o pedido nunca saiu (ligacao recusada) ou foi rejeitado sem
                processar (429/503/529); nada faturado.
            LlmUnknownOutcome: timeout de leitura, ligacao cortada ou 5xx; pode ter sido faturado.
            ImageRefused: moderacao (`moderation_blocked`); nao e faturada.
            ImageResponseInvalid: resposta sem PNG utilizavel; pode ter sido faturada.
            ImageProviderError: os restantes erros da API (4xx como 400/401/403/404, sem a
                excecao do SDK); o chamador trata-os como resultado desconhecido.
        """
        raw = self._call_sdk(request)
        body = _parse_body(raw)
        first = body.data[0] if body.data else None
        data = _decode_png(first.b64_json if first is not None else None)
        return ImageResult(
            data=data,
            provider_request_id=raw.headers.get("x-request-id"),
            resolved_model=request.model_id,
            size=body.size or request.size,
            quality=body.quality or request.quality,
        )

    def _call_sdk(self, request: ImageRequest) -> _RawImages:
        failure: Exception | None = None
        try:
            # Fundo opaco: os layouts compoem sobre uma imagem plana, sem canal alfa.
            return self._sdk.images.with_raw_response.generate(
                model=request.model_id,
                prompt=request.prompt,
                size=request.size,
                quality=request.quality,
                n=1,
                output_format="png",
                background="opaque",
                moderation="auto",
            )
        except Exception as exc:
            failure = _failure_for(exc)
            if failure is None:
                raise
        # Fora do `except`: assim a excecao do SDK (com a chave) nao fica em `__context__`.
        raise failure
