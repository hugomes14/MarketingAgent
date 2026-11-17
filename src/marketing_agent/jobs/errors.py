"""Resumo seguro de excecoes para `job.last_error`.

Fica fora de `jobs/queue.py` para a fila nao importar dominio (evita ciclos): so o worker
importa este modulo.
"""

from pydantic import ValidationError

from marketing_agent.budget.controller import RunCostLimitExceeded
from marketing_agent.harness.engine import RunLimitExceeded
from marketing_agent.harness.run_errors import RunConfigurationFailed
from marketing_agent.jobs.queue import truncate_error
from marketing_agent.strategy.service import (
    MissingPrerequisite,
    PendingProposal,
    ProposalInProgress,
)

# Lista fechada de excecoes cuja mensagem e montada so com numeros, valores de enumeracao, ids e
# frases fixas do nosso codigo (`RunConfigurationFailed`: so nomes de papel, modelo e capacidades).
# `NeedsHuman` e `KbError` ficam de fora de proposito: carregam texto escolhido pelo modelo ou
# pelo utilizador (o detalhe da execucao ja esta em agent_run/agent_step).
# Comparacao por tipo EXATO: uma subclasse futura nao herda o armazenamento da mensagem.
SAFE_MESSAGE_ERRORS: tuple[type[Exception], ...] = (
    RunLimitExceeded,
    RunCostLimitExceeded,
    MissingPrerequisite,
    PendingProposal,
    ProposalInProgress,
    RunConfigurationFailed,
)


def describe_error(error: BaseException) -> str:
    """Resume `error` para `last_error` sem nunca copiar texto de modelo ou de utilizador.

    Por omissao so a classe. Mensagem curta apenas para `SAFE_MESSAGE_ERRORS` (tipo exato); para
    um `ValidationError` do pydantic, so o numero e os tipos dos erros (nunca `input` nem `msg`).
    """
    name = type(error).__name__
    if isinstance(error, ValidationError):
        kinds = ", ".join(sorted({str(item["type"]) for item in error.errors(include_input=False)}))
        return truncate_error(f"{name}: {error.error_count()} erros ({kinds})")
    message = str(error)
    if type(error) in SAFE_MESSAGE_ERRORS and message:
        return truncate_error(f"{name}: {message}")
    return name
