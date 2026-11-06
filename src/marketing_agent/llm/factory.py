"""Construcao do `LlmRouter` a partir das definicoes: so os fornecedores referidos nas rotas."""

from collections.abc import Mapping
from pathlib import Path

from marketing_agent.config import Capability, RoleRoute, Settings
from marketing_agent.llm.anthropic_adapter import AnthropicClient
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.openai_adapter import OpenAiClient
from marketing_agent.llm.router import LlmRouter
from marketing_agent.llm.types import LlmClient
from marketing_agent.secrets import SecretFileError


def _capabilities_by_provider(
    routes: Mapping[str, RoleRoute],
) -> dict[str, frozenset[Capability]]:
    """Uniao das capacidades das rotas de cada fornecedor (a rota restringe cada papel)."""
    union: dict[str, frozenset[Capability]] = {}
    for route in routes.values():
        union[route.provider] = union.get(route.provider, frozenset()) | route.capabilities
    return union


def _require_key_file(key_file: Path | None, variable: str) -> Path:
    if key_file is None:
        raise ValueError(f"{variable} nao definido: indique o ficheiro com a chave de API")
    return key_file


def _build_anthropic(settings: Settings, capabilities: frozenset[Capability]) -> LlmClient:
    variable = "ANTHROPIC_API_KEY_FILE"
    key_file = _require_key_file(settings.anthropic_api_key_file, variable)
    try:
        return AnthropicClient(key_file, capabilities)
    except SecretFileError as exc:
        raise ValueError(f"{variable} invalido: {exc}") from exc


def _build_openai(settings: Settings, capabilities: frozenset[Capability]) -> LlmClient:
    variable = "OPENAI_API_KEY_FILE"
    key_file = _require_key_file(settings.openai_api_key_file, variable)
    try:
        return OpenAiClient(key_file, capabilities, base_url=settings.openai_base_url)
    except SecretFileError as exc:
        raise ValueError(f"{variable} invalido: {exc}") from exc


def build_llm_router(settings: Settings, fake: FakeLlmClient | None = None) -> LlmRouter:
    """Constroi o router com um cliente por fornecedor referido em `settings.role_routes`.

    As capacidades de cada cliente sao a uniao das das rotas que o usam.

    Raises:
        ValueError: se faltar a chave de um fornecedor (a mensagem nomeia a variavel `*_FILE`,
            nunca o conteudo) ou se uma rota `fake` for usada sem o argumento `fake`.
    """
    clients: dict[str, LlmClient] = {}
    for provider, capabilities in _capabilities_by_provider(settings.role_routes).items():
        if provider == "anthropic":
            clients[provider] = _build_anthropic(settings, capabilities)
        elif provider == "openai":
            clients[provider] = _build_openai(settings, capabilities)
        elif fake is None:
            raise ValueError("o fornecedor 'fake' exige o argumento 'fake'")
        else:
            clients[provider] = fake
    return LlmRouter(settings.role_routes, clients)
