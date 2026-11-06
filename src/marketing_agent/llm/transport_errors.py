"""Classificacao de falhas de transporte partilhada pelos adaptadores (dinheiro em jogo).

Os SDKs `anthropic` e `openai` assentam ambos em `httpx2`, por isso as causas e os estados HTTP
tem o mesmo significado nos dois. A pergunta e sempre a mesma: o pedido chegou ao fornecedor?
`LlmNetworkError` = nao chegou (ou foi rejeitado sem processar), pode repetir-se sem custo;
`LlmUnknownOutcome` = pode ter sido processado e faturado; `None` = nao classificado.
"""

import httpx2

from marketing_agent.llm.types import LlmNetworkError, LlmUnknownOutcome

# Rejeitados antes de qualquer processamento: nada foi gerado nem faturado.
_REJECTED_STATUSES = frozenset({429, 503, 529})
# Falhas em que o pedido nunca saiu: a ligacao nao chegou a ser estabelecida.
_NEVER_SENT_CAUSES = (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout)


def classify_connection_cause(cause: BaseException | None) -> type[Exception]:
    """Classifica um erro de ligacao do SDK pela causa `httpx2` (`exc.__cause__`).

    Falha antes de enviar -> `LlmNetworkError`; qualquer outra (leitura, escrita, ligacao
    cortada, causa desconhecida) -> `LlmUnknownOutcome`, porque o pedido pode ter sido recebido.
    """
    return LlmNetworkError if isinstance(cause, _NEVER_SENT_CAUSES) else LlmUnknownOutcome


def classify_http_status(status_code: int) -> type[Exception] | None:
    """Classifica uma resposta HTTP de erro; `None` para 4xx nao classificados (ex.: 400/401)."""
    if status_code in _REJECTED_STATUSES:
        return LlmNetworkError
    return LlmUnknownOutcome if status_code >= 500 else None
