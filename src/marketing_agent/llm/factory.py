"""Construcao do `LlmRouter` a partir das definicoes: so os fornecedores referidos nas rotas."""

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from marketing_agent.config import IMAGE_ROUTE, Capability, RoleRoute, Settings
from marketing_agent.llm.anthropic_adapter import AnthropicClient
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.openai_adapter import OpenAiClient
from marketing_agent.llm.router import LlmRouter
from marketing_agent.llm.types import LLM_CALL_TIMEOUT_S, LlmClient
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
        return AnthropicClient(
            key_file, capabilities, timeout_s=LLM_CALL_TIMEOUT_S * settings.llm_time_multiplier
        )
    except SecretFileError as exc:
        raise ValueError(f"{variable} invalido: {exc}") from exc


def _build_openai(settings: Settings, capabilities: frozenset[Capability]) -> LlmClient:
    variable = "OPENAI_API_KEY_FILE"
    key_file = _require_key_file(settings.openai_api_key_file, variable)
    # Endereco personalizado = servidor compativel (Ollama, LM Studio): ignora o papel `developer`
    # (o prompt de sistema desaparece), por isso usa `system`. A API oficial mantem `developer`.
    system_role: Literal["developer", "system"] = (
        "developer" if settings.openai_base_url is None else "system"
    )
    try:
        return OpenAiClient(
            key_file,
            capabilities,
            base_url=settings.openai_base_url,
            system_role=system_role,
            timeout_s=LLM_CALL_TIMEOUT_S * settings.llm_time_multiplier,
        )
    except SecretFileError as exc:
        raise ValueError(f"{variable} invalido: {exc}") from exc


def build_llm_router(settings: Settings, fake: FakeLlmClient | None = None) -> LlmRouter:
    """Constroi o router com um cliente por fornecedor referido em `settings.role_routes`.

    As capacidades de cada cliente sao a uniao das das rotas que o usam. A rota `designer_imagem`
    e ignorada: o router de texto nunca a ve (ver `images.factory.build_image_backend`).

    Raises:
        ValueError: se faltar a chave de um fornecedor (a mensagem nomeia a variavel `*_FILE`,
            nunca o conteudo) ou se uma rota `fake` for usada sem o argumento `fake`.
    """
    # A rota de imagem e do gerador de imagens: aqui nao cria cliente nem exige chave de texto.
    text_routes = {
        role: route for role, route in settings.role_routes.items() if role != IMAGE_ROUTE
    }
    clients: dict[str, LlmClient] = {}
    for provider, capabilities in _capabilities_by_provider(text_routes).items():
        if provider == "anthropic":
            clients[provider] = _build_anthropic(settings, capabilities)
        elif provider == "openai":
            clients[provider] = _build_openai(settings, capabilities)
        elif fake is None:
            raise ValueError("o fornecedor 'fake' exige o argumento 'fake'")
        else:
            clients[provider] = fake
    return LlmRouter(text_routes, clients)
