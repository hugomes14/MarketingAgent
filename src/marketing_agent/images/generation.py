"""Contrato de geracao de imagem e o gerador falso (predefinido localmente, custo 0)."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Final, Literal, Protocol

from marketing_agent.images.layouts import gradient_background

ImageSize = Literal["1024x1536"]
ImageQuality = Literal["low", "medium", "high"]

FAKE_IMAGE_MODEL: Final = "fake-image-1"
# Teto da imagem descodificada que o fornecedor pode devolver (a mesma ordem de grandeza do
# limite de upload): protege a memoria contra uma resposta anormal.
MAX_IMAGE_BYTES: Final = 20 * 1024 * 1024


@dataclass(frozen=True)
class ImageRequest:
    """Pedido de uma imagem: o `prompt` vem sempre de `PromptFixo.text`."""

    model_id: str
    prompt: str
    size: ImageSize
    quality: ImageQuality


@dataclass(frozen=True)
class ImageResult:
    """Imagem PNG gerada. `size` e `quality` sao os devolvidos pelo fornecedor, quando vem."""

    data: bytes
    provider_request_id: str | None
    resolved_model: str
    size: str
    quality: str


class ImageRefused(Exception):
    """O fornecedor recusou o conteudo (moderacao) sem faturar: a reserva pode ser libertada."""


class ImageResponseInvalid(Exception):
    """O fornecedor respondeu, mas sem uma imagem PNG utilizavel.

    Nao e falha de transporte: o pedido foi processado e pode ter sido faturado. O chamador
    trata-a como "faturado, valor desconhecido" (`mark_unknown`), nunca como `release`.
    A mensagem e fixa e nunca inclui o conteudo recebido.
    """


class ImageProviderError(Exception):
    """O fornecedor rejeitou o pedido com um erro que nao e recusa nem falha de transporte.

    Cobre os 4xx sem classificacao (400 que nao e moderacao, 401, 403, 404, 422...) e qualquer
    outro erro da API. Leva SO `status_code` (None se nao houve resposta HTTP) e uma mensagem
    fixa com o nome da classe do erro do SDK: o SDK guarda a chave no cabecalho do pedido e o
    fornecedor repete-a por vezes na mensagem, por isso nada disso e retido. O chamador (Tarefa 10)
    trata-a como resultado desconhecido (`mark_unknown`) e pede intervencao humana.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ImageGenerator(Protocol):
    """Gerador de imagens de um fornecedor."""

    provider: str

    def generate(self, request: ImageRequest) -> ImageResult:
        """Gera uma imagem.

        Raises:
            LlmNetworkError: o pedido nunca saiu (nada faturado).
            LlmUnknownOutcome: o pedido pode ter sido processado e faturado.
            ImageRefused: recusa de conteudo sem faturacao.
            ImageResponseInvalid: resposta sem PNG utilizavel (pode ter sido faturada).
            ImageProviderError: outro erro da API (4xx nao classificado); tratar como desconhecido.
        """
        ...


class FakeImageGenerator:
    """Gerador determinista sem rede: gradiente a partir do SHA-256 do prompt.

    `failures` e o guiao de falhas: cada chamada levanta a seguinte, por ordem; esgotado o guiao,
    as chamadas sucedem. `requests` regista todos os pedidos, incluindo os que falharam.
    """

    provider = "fake"

    def __init__(self, failures: Sequence[Exception] = ()) -> None:
        self.requests: list[ImageRequest] = []
        self._pending_failures = list(failures)
        # Protege a lista e o guiao quando o motor e os testes chamam de varias threads.
        self._lock = Lock()

    def generate(self, request: ImageRequest) -> ImageResult:
        """Devolve o gradiente do prompt ou levanta a proxima falha do guiao."""
        with self._lock:
            self.requests.append(request)
            failure = self._pending_failures.pop(0) if self._pending_failures else None
        if failure is not None:
            raise failure
        seed = hashlib.sha256(request.prompt.encode("utf-8")).digest()
        return ImageResult(
            data=gradient_background(seed),
            # Sem id de pedido: o `cost_entry` exige ids unicos e o mesmo prompt repete-se.
            provider_request_id=None,
            resolved_model=FAKE_IMAGE_MODEL,
            size=request.size,
            quality=request.quality,
        )
