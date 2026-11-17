"""Escolha do cliente e do modelo por papel, validando as capacidades exigidas."""

from collections.abc import Mapping
from typing import Final

from marketing_agent.config import RoleRoute
from marketing_agent.harness.spec import AgentSpec
from marketing_agent.llm.types import LlmClient

# Papeis sem rota propria que usam a de outro papel (so quando nao ha rota explicita): o percurso
# simples da comparacao (Plano 2C) corre com o modelo do Copywriter. Um papel desconhecido
# continua a falhar com `RouteNotConfigured`.
ROUTE_FALLBACKS: Final[Mapping[str, str]] = {"simples": "copywriter"}


class RouteNotConfigured(Exception):
    """Falta a rota do papel ou o cliente do fornecedor dessa rota."""


class MissingCapability(Exception):
    """A rota ou o cliente nao cobrem as capacidades que o papel exige."""


class LlmRouter:
    """Resolve, para cada papel, o cliente e a rota (modelo) a usar."""

    def __init__(self, routes: Mapping[str, RoleRoute], clients: Mapping[str, LlmClient]) -> None:
        self._routes = routes
        self._clients = clients

    def resolve(self, spec: AgentSpec) -> tuple[LlmClient, RoleRoute]:
        """Devolve (cliente, rota) do papel; levanta `RouteNotConfigured` ou `MissingCapability`.

        Sem rota explicita, os papeis de `ROUTE_FALLBACKS` usam a rota do papel indicado; as
        capacidades exigidas verificam-se sempre contra a rota que acaba por ser usada.
        """
        route = self._routes.get(spec.role)
        if route is None and spec.role in ROUTE_FALLBACKS:
            route = self._routes.get(ROUTE_FALLBACKS[spec.role])
        if route is None:
            raise RouteNotConfigured(f"papel {spec.role!r} sem rota configurada")
        client = self._clients.get(route.provider)
        if client is None:
            raise RouteNotConfigured(
                f"papel {spec.role!r}: sem cliente para o fornecedor {route.provider!r}"
            )
        missing = spec.required_capabilities - (route.capabilities & client.capabilities)
        if missing:
            raise MissingCapability(
                f"papel {spec.role!r} exige capacidades em falta: {sorted(missing)}"
            )
        return client, route
