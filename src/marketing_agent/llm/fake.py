"""Cliente LLM falso, guiado por um guiao de respostas e excecoes, para testes."""

from collections.abc import Sequence

from marketing_agent.config import Capability
from marketing_agent.llm.types import LlmRequest, LlmResponse


class ScriptExhausted(Exception):
    """O cliente falso recebeu mais pedidos do que respostas no guiao."""


class FakeLlmClient:
    """Serve o guiao por ordem; itens que sao excecoes sao levantados em vez de devolvidos."""

    def __init__(
        self,
        responses: Sequence[LlmResponse | Exception],
        capabilities: frozenset[Capability] = frozenset({"text", "json_schema"}),
    ) -> None:
        self._script: list[LlmResponse | Exception] = list(
            responses
        )  # copia: nao altera o original
        self.capabilities = capabilities
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        """Regista o pedido e devolve (ou levanta) o proximo item do guiao."""
        self.requests.append(request)
        if not self._script:
            raise ScriptExhausted(f"guiao esgotado no pedido {len(self.requests)}")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
