"""Erros e classes de passo do motor, partilhados por `engine` e `image_calls`.

Vivem aqui para `image_calls` os poder importar sem ciclo com `engine`; o motor continua a
reexporta-los, por isso os importadores existentes nao mudam.
"""

from typing import Literal

StepError = Literal["invalid_output", "refusal", "truncated", "network", "limit", "unknown_outcome"]


class RunLimitExceeded(Exception):
    """Um limite cumulativo da execucao (passos, tokens ou duracao) foi atingido."""


class RunConfigurationFailed(Exception):
    """A execucao nao arrancou (ou parou) por rota, capacidade ou preco em falta.

    A mensagem e montada so com nomes de papel, fornecedor, modelo e capacidades do nosso
    codigo e da configuracao, por isso pode chegar a `job.last_error` (ver `jobs.errors`).
    """


class NeedsHuman(Exception):
    """A execucao nao pode continuar sozinha e precisa de decisao humana."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
