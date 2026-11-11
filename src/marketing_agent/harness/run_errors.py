"""Erros e classes de passo do motor, partilhados por `engine` e `image_calls`.

Vivem aqui para `image_calls` os poder importar sem ciclo com `engine`; o motor continua a
reexporta-los, por isso os importadores existentes nao mudam.
"""

from typing import Literal

StepError = Literal["invalid_output", "refusal", "truncated", "network", "limit", "unknown_outcome"]


class RunLimitExceeded(Exception):
    """Um limite cumulativo da execucao (passos, tokens ou duracao) foi atingido."""


class NeedsHuman(Exception):
    """A execucao nao pode continuar sozinha e precisa de decisao humana."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
