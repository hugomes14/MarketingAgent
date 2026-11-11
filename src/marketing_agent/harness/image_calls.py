"""Geracao de imagem no motor: reserva e acerto por imagem, sem texto do fornecedor nos registos.

`RunContext.generate_image` so delega aqui. Ordem de cada tentativa: limites e quota -> passo ->
reserva (limite de custo) -> gerador (fora de qualquer transacao) -> acerto -> validacao dos
bytes. Mapeamento de falhas do gerador, seguro para dinheiro:

- `LlmNetworkError` (o pedido nunca saiu): liberta a reserva e repete com passo e reserva novos;
- `ImageRefused` (recusa sem faturacao): liberta a reserva e pede ajuda humana;
- qualquer outra coisa (`LlmUnknownOutcome`, `ImageResponseInvalid`, `ImageProviderError`, erro
  inesperado): o fornecedor pode ter faturado, logo a reserva fica `unknown_outcome` (continua a
  contar) e pede-se ajuda humana.

Nenhuma mensagem, `repr` ou cadeia de causas de uma excecao do gerador chega ao passo, a execucao,
ao registo ou ao `NeedsHuman`: so o nome da classe e, em `ImageProviderError`, o `status_code`.
"""

import hashlib
import logging
import re
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from threading import Lock
from typing import Final, get_args

from marketing_agent.budget.controller import (
    BudgetController,
    ImageReservationRequest,
    Reservation,
)
from marketing_agent.budget.pricing import ImageUsage
from marketing_agent.harness.run_errors import NeedsHuman, RunLimitExceeded, StepError
from marketing_agent.images.codec import (
    ImageFormat,
    detect_format,
    open_bounded,
)
from marketing_agent.images.factory import ImageBackend
from marketing_agent.images.generation import (
    MAX_IMAGE_BYTES,
    ImageQuality,
    ImageRefused,
    ImageRequest,
    ImageResult,
    ImageSize,
)
from marketing_agent.images.prompts import PromptFixo
from marketing_agent.llm.types import LlmNetworkError

logger = logging.getLogger(__name__)

MIN_GENERATED_SIDE: Final = 1024
_ALLOWED_FORMATS: Final[frozenset[ImageFormat]] = frozenset({"png", "jpeg", "webp"})
_MEDIA_TYPES: Final[dict[ImageFormat, str]] = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}
_SAFE_TOKEN: Final = re.compile(r"[A-Za-z0-9x._-]{1,24}")

# Mensagens fixas: o que e mostrado ao responsavel nunca depende de texto do fornecedor.
REFUSED_MESSAGE: Final = "geração recusada pelo fornecedor"
UNKNOWN_MESSAGE: Final = "geração de imagem com desfecho desconhecido; verificar antes de repetir"
SETTLE_FAILED_MESSAGE: Final = "custo da imagem gerada por acertar; verificar antes de repetir"
INVALID_IMAGE_MESSAGE: Final = "imagem gerada inválida; o custo ficou registado"
UNAVAILABLE_MESSAGE: Final = "geração de imagem indisponível nesta execução"
EXHAUSTED_MESSAGE: Final = "limite de imagens geradas por pedido atingido"


class ImageLimitExceeded(RunLimitExceeded):
    """Sem quota de imagens (ou sem gerador) nesta execucao; o motor converte em BLOCKED_LIMIT.

    Construivel sem argumentos: `describe_stop` instancia a classe do limite para a descrever.
    """

    def __init__(self, message: str = EXHAUSTED_MESSAGE) -> None:
        super().__init__(message)


class ImageAllowance:
    """Quota de imagens geradas de um pedido, partilhada pelas execucoes do mesmo job.

    `consume` toma um lugar de forma atomica ANTES de chamar o fornecedor (duas threads nunca
    passam o limite) e `refund` devolve-o quando nada foi faturado (reserva libertada). Um lugar
    nunca devolvido corresponde a uma imagem que o fornecedor pode ter faturado.
    """

    def __init__(self, limit: int = 2) -> None:
        if limit < 0:
            raise ValueError("o limite de imagens nao pode ser negativo")
        self._limit = limit
        self._taken = 0
        self._lock = Lock()

    @property
    def remaining(self) -> int:
        """Lugares ainda livres (tomados e ainda nao devolvidos ja contam como usados)."""
        with self._lock:
            return self._limit - self._taken

    def consume(self) -> None:
        """Toma um lugar; `ImageLimitExceeded` se a quota estiver esgotada."""
        with self._lock:
            if self._taken >= self._limit:
                raise ImageLimitExceeded(f"limite de {self._limit} imagens por pedido atingido")
            self._taken += 1

    def refund(self) -> None:
        """Devolve um lugar tomado por `consume` quando a imagem nao chegou a ser faturada."""
        with self._lock:
            if self._taken == 0:
                raise ValueError("nenhum lugar de imagem para devolver")
            self._taken -= 1


@dataclass(frozen=True)
class GeneratedImage:
    """Imagem gerada e acertada. `step_id` e o `<pedido>` da proveniencia `gerada:<pedido>`.

    `size` e `quality` sao os reservados e faturados; `media_type` vem do formato descodificado.
    """

    data: bytes
    media_type: str
    step_id: str
    provider: str
    model_id: str
    size: str
    quality: str
    cost_usd: Decimal
    prompt_sha256: str


@dataclass(frozen=True)
class ImageStepOps:
    """Operacoes de passo do motor de que a chamada de imagem precisa."""

    check_limits: Callable[[], None]
    open_step: Callable[[str, str, str], str]  # (input_summary, kind, name) -> step_id
    close_step: Callable[[str, StepError | None, str | None], None]
    close_step_quietly: Callable[[str, StepError], None]
    unknown_if_unsettled: Callable[[str, str], AbstractContextManager[None]]


@dataclass(frozen=True)
class _Sent:
    """Imagem devolvida pelo gerador, com o passo e a reserva ja acertados."""

    step_id: str
    result: ImageResult
    cost_usd: Decimal


@dataclass(frozen=True)
class ImageCaller:
    """Uma chamada de imagem de uma execucao; sem estado proprio alem das dependencias."""

    run_id: str
    brand_id: str
    role: str
    max_cost_usd: Decimal
    backend: ImageBackend | None
    allowance: ImageAllowance | None
    budget: BudgetController
    steps: ImageStepOps
    sleep: Callable[[float], None]
    backoff_s: Sequence[float]

    def generate(
        self, prompt_fixo: PromptFixo, size: ImageSize, quality: ImageQuality
    ) -> GeneratedImage:
        """Gera, acerta e valida uma imagem; ver a documentacao do modulo para as falhas."""
        _verify_prompt(prompt_fixo)
        _verify_options(size, quality)
        if self.backend is None or self.allowance is None:
            raise ImageLimitExceeded(UNAVAILABLE_MESSAGE)
        request = ImageRequest(self.backend.model_id, prompt_fixo.text, size, quality)
        sent = self._send_with_retries(self.backend, self.allowance, request)
        summary = _output_summary(prompt_fixo.sha256, sent.result, size, quality)
        image_format = self._validate_or_close(sent, summary)
        self.steps.close_step(sent.step_id, None, summary)
        return GeneratedImage(
            data=sent.result.data,
            media_type=_MEDIA_TYPES[image_format],
            step_id=sent.step_id,
            provider=self.backend.provider,
            model_id=self.backend.model_id,
            size=size,
            quality=quality,
            cost_usd=sent.cost_usd,
            prompt_sha256=prompt_fixo.sha256,
        )

    def _send_with_retries(
        self, backend: ImageBackend, allowance: ImageAllowance, request: ImageRequest
    ) -> _Sent:
        """Repete so falhas de rede (cada uma com passo e reserva novos), como `call_llm`."""
        attempt = 0
        while True:
            sent = self._attempt(backend, allowance, request)
            if sent is not None:
                return sent
            if attempt >= len(self.backoff_s):
                raise NeedsHuman(f"rede indisponivel apos {attempt + 1} tentativas")
            self.sleep(self.backoff_s[attempt])
            attempt += 1

    def _attempt(
        self, backend: ImageBackend, allowance: ImageAllowance, request: ImageRequest
    ) -> _Sent | None:
        """Uma tentativa completa; `None` quando a rede falhou e a reserva foi libertada."""
        self.steps.check_limits()
        allowance.consume()
        try:
            step_id = self.steps.open_step(
                f"tamanho {request.size}, qualidade {request.quality}", "tool", "generate_image"
            )
            reservation = self._reserve(step_id, backend, request)
        except BaseException:
            allowance.refund()  # nada foi enviado: o lugar volta
            raise
        result = self._call_generator(backend, allowance, step_id, reservation.id, request)
        if result is None:
            return None
        cost = self._settle(step_id, reservation.id, request, result)
        return _Sent(step_id, result, cost)

    def _reserve(self, step_id: str, backend: ImageBackend, request: ImageRequest) -> Reservation:
        reservation_request = ImageReservationRequest(
            brand_id=self.brand_id,
            role=self.role,
            run_id=self.run_id,
            step_id=step_id,
            provider=backend.provider,
            model_id=backend.model_id,
            size=request.size,
            quality=request.quality,
        )
        try:
            return self.budget.reserve_image(reservation_request, self.max_cost_usd)
        except Exception:
            # Nada foi enviado: o limite de custo (ou um preco em falta) bloqueou a chamada.
            self.steps.close_step_quietly(step_id, "limit")
            raise

    def _call_generator(
        self,
        backend: ImageBackend,
        allowance: ImageAllowance,
        step_id: str,
        reservation_id: str,
        request: ImageRequest,
    ) -> ImageResult | None:
        """Chama o fornecedor (sem transacao aberta) e aplica o mapeamento de falhas."""
        try:
            return backend.generator.generate(request)
        except LlmNetworkError:
            self._release(allowance, step_id, reservation_id, "network")
            return None
        except ImageRefused:
            self._release(allowance, step_id, reservation_id, "refusal")
            raise NeedsHuman(REFUSED_MESSAGE) from None
        except BaseException as exc:
            self._record_unknown(step_id, reservation_id, exc)
            if isinstance(exc, Exception):
                raise NeedsHuman(f"{UNKNOWN_MESSAGE} ({_describe(exc)})") from None
            raise

    def _release(
        self, allowance: ImageAllowance, step_id: str, reservation_id: str, error: StepError
    ) -> None:
        """Nada foi faturado: liberta a reserva, devolve o lugar e fecha o passo."""
        try:
            self.budget.release(reservation_id)
        except Exception:
            self.steps.close_step_quietly(step_id, error)
            raise
        allowance.refund()
        self.steps.close_step(step_id, error, None)

    def _record_unknown(self, step_id: str, reservation_id: str, exc: BaseException) -> None:
        """Pode ter sido faturado: a reserva fica `unknown_outcome` e o lugar fica gasto."""
        logger.warning(
            "execucao %s: geracao de imagem sem desfecho (%s)", self.run_id, _describe(exc)
        )
        try:
            self.budget.mark_unknown(reservation_id)
        except Exception as mark_error:
            logger.error(
                "execucao %s: mark_unknown falhou (%s)", self.run_id, type(mark_error).__name__
            )
        self.steps.close_step_quietly(step_id, "unknown_outcome")

    def _settle(
        self, step_id: str, reservation_id: str, request: ImageRequest, result: ImageResult
    ) -> Decimal:
        """Acerta com o par RESERVADO (nao o devolvido); qualquer falha deixa `unknown_outcome`."""
        usage = ImageUsage(1, request.size, request.quality)
        try:
            with self.steps.unknown_if_unsettled(step_id, reservation_id):
                return self.budget.settle_image(reservation_id, usage, result.provider_request_id)
        except Exception as exc:
            logger.error(
                "execucao %s: acerto da imagem falhou (%s)", self.run_id, type(exc).__name__
            )
            raise NeedsHuman(SETTLE_FAILED_MESSAGE) from None

    def _validate_or_close(self, sent: _Sent, summary: str) -> ImageFormat:
        """Valida os bytes DEPOIS do acerto: o custo fica lancado mesmo com imagem invalida."""
        try:
            return _validated_format(sent.result.data)
        except Exception:
            self.steps.close_step(sent.step_id, "invalid_output", summary)
            raise NeedsHuman(INVALID_IMAGE_MESSAGE) from None


def _verify_prompt(prompt_fixo: PromptFixo) -> None:
    if not isinstance(prompt_fixo, PromptFixo):
        raise TypeError("generate_image exige um PromptFixo, nunca texto livre")
    digest = hashlib.sha256(prompt_fixo.text.encode("utf-8")).hexdigest()
    if digest != prompt_fixo.sha256:
        raise ValueError("o sha256 do PromptFixo nao corresponde ao seu texto")


def _verify_options(size: str, quality: str) -> None:
    if size not in get_args(ImageSize):
        raise ValueError("tamanho de imagem nao suportado")
    if quality not in get_args(ImageQuality):
        raise ValueError("qualidade de imagem nao suportada")


def _validated_format(data: bytes) -> ImageFormat:
    """Formato da imagem se for PNG/JPEG/WebP estatico dentro dos limites; senao levanta."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("imagem acima do tamanho maximo")
    with open_bounded(data, _ALLOWED_FORMATS) as image:
        if min(image.size) < MIN_GENERATED_SIDE:
            raise ValueError("imagem com o lado menor abaixo do minimo")
    detected = detect_format(data)
    if detected is None:  # inalcancavel: open_bounded ja recusou
        raise ValueError("formato de imagem desconhecido")
    return detected


def _describe(exc: BaseException) -> str:
    """Nome da classe e, em `ImageProviderError`, o `status_code`: nunca texto do fornecedor."""
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    return f"{name}, HTTP {status}" if isinstance(status, int) else name


def _safe_token(value: object) -> str:
    return value if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value) else "?"


def _output_summary(prompt_sha256: str, result: ImageResult, size: str, quality: str) -> str:
    """Resumo do passo: sha256 do prompt (nunca o texto) e a divergencia devolvida, se houver."""
    parts = [f"prompt_sha256={prompt_sha256}"]
    if (result.size, result.quality) != (size, quality):
        parts.append(
            f"fornecedor devolveu size={_safe_token(result.size)} "
            f"quality={_safe_token(result.quality)}"
        )
    return "; ".join(parts)
