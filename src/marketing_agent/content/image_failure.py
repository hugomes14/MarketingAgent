"""Razao curta e fixa de uma falha da parte de imagem do pacote (`last_error = "imagem: ..."`).

O motivo que o motor devolve (`RunResult.message`) pode citar o modelo ou o fornecedor, por
isso nunca e copiado: so se reconhece, pelo PREFIXO que o codigo escreve antes de qualquer texto
externo, de que falha se trata, e a razao publicada vem de uma tabela fixa.
"""

from typing import Final

from marketing_agent.budget.controller import RunCostLimitExceeded
from marketing_agent.content.design import COMPOSE_FAILED_MESSAGE
from marketing_agent.content.package_media import PHOTO_UNAVAILABLE_MESSAGE
from marketing_agent.harness.engine import RunResult, RunStatus
from marketing_agent.harness.image_calls import (
    INVALID_IMAGE_MESSAGE,
    REFUSED_MESSAGE,
    SETTLE_FAILED_MESSAGE,
    UNKNOWN_MESSAGE,
    ImageLimitExceeded,
)

IMAGE_ERROR_PREFIX: Final = "imagem: "
# O motor escreve `f"{type(exc).__name__}: {exc}"` nas falhas inesperadas do ciclo.
_PHOTO_FAILED_PREFIX: Final = "PhotoUnavailable: "
_NETWORK_PREFIX: Final = "rede indisponivel"

_NEEDS_HUMAN_REASONS: Final[tuple[tuple[str, str], ...]] = (
    (COMPOSE_FAILED_MESSAGE, "a composição da imagem falhou"),
    (REFUSED_MESSAGE, "a geração foi recusada pelo fornecedor"),
    (UNKNOWN_MESSAGE, "a geração ficou com desfecho desconhecido"),
    (SETTLE_FAILED_MESSAGE, "o custo da imagem ficou por acertar"),
    (INVALID_IMAGE_MESSAGE, "a imagem gerada é inválida"),
    (_NETWORK_PREFIX, "a rede está indisponível"),
)
DEFAULT_NEEDS_HUMAN: Final = "o designer pediu ajuda humana"
PHOTO_UNAVAILABLE_REASON: Final = "a foto escolhida já não está disponível"
DESIGNER_FAILED_REASON: Final = "o designer falhou"


def image_error(reason: str) -> str:
    """`last_error` de uma falha de imagem a partir de uma razao fixa."""
    return f"{IMAGE_ERROR_PREFIX}{reason}"


def describe_image_stop(result: RunResult) -> str:
    """Razao fixa de uma execucao do Designer que nao produziu imagem."""
    message = result.message or ""
    if result.status is RunStatus.BLOCKED_LIMIT:
        return _limit_reason(result)
    if result.status is RunStatus.NEEDS_HUMAN:
        for prefix, reason in _NEEDS_HUMAN_REASONS:
            if message.startswith(prefix):
                return reason
        return DEFAULT_NEEDS_HUMAN
    if message.startswith(_PHOTO_FAILED_PREFIX) and PHOTO_UNAVAILABLE_MESSAGE in message:
        return PHOTO_UNAVAILABLE_REASON
    return DESIGNER_FAILED_REASON


def is_image_limit(result: RunResult) -> bool:
    """A execucao parou por falta de quota de imagens (decide-se pela classe, nao pelo texto)."""
    return (
        result.status is RunStatus.BLOCKED_LIMIT
        and result.limit is not None
        and issubclass(result.limit, ImageLimitExceeded)
    )


def _limit_reason(result: RunResult) -> str:
    if is_image_limit(result):
        return "limite de imagens geradas atingido"
    if result.limit is not None and issubclass(result.limit, RunCostLimitExceeded):
        return "limite de custo do designer atingido"
    return "limite de passos ou de tempo do designer atingido"
