"""Verificacao do cabecalho `Host` (defesa contra DNS rebinding) antes de qualquer rota.

Sem esta verificacao, um site malicioso cujo nome DNS passe a apontar para 127.0.0.1 leria o
painel e o token CSRF como "mesma origem" e submeteria formularios com `Origin == Host`. A
lista `PANEL_ALLOWED_HOSTS` nomeia os unicos hostnames (sem porta) que o painel serve; tudo o
resto recebe 400 generico, sem chamar a aplicacao, em todos os metodos e caminhos.
"""

import ipaddress
import re
from collections.abc import Iterable

from starlette.types import ASGIApp, Receive, Scope, Send

DENIED_MESSAGE = "Pedido recusado."
_HOSTNAME = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")
_BRACKETED = re.compile(r"\[([0-9a-f:.]+)\](?::[0-9]{1,5})?")
_WITH_PORT = re.compile(r"([a-z0-9.-]+)(?::[0-9]{1,5})?")


def _normalize_name(name: str) -> str | None:
    """Minusculas, sem ponto final; so hostnames DNS validos ou IPv4 literais."""
    clean = name.lower().removesuffix(".")
    return clean if _HOSTNAME.fullmatch(clean) else None


def _normalize_ipv6(text: str) -> str | None:
    try:
        return str(ipaddress.IPv6Address(text))
    except ValueError:
        return None


def parse_host_header(value: str) -> str | None:
    """Hostname normalizado de um `Host` (`nome`, `nome:porta`, `[ipv6]:porta`), ou `None`.

    Recusa tudo o que nao seja exatamente host[:porta]: espacos, controlo, `@`, `/`, `?`, `#`.
    """
    lowered = value.lower()
    bracketed = _BRACKETED.fullmatch(lowered)
    if bracketed:
        return _normalize_ipv6(bracketed.group(1))
    named = _WITH_PORT.fullmatch(lowered)
    return _normalize_name(named.group(1)) if named else None


def normalize_allowed_hosts(entries: Iterable[str]) -> frozenset[str]:
    """Valida `PANEL_ALLOWED_HOSTS`: hostnames sem porta (IPv6 com ou sem parenteses retos).

    Raises:
        ValueError: se a lista estiver vazia, tiver `*`, entradas vazias ou com porta/formato
            invalido. A mensagem nomeia a definicao.
    """
    hosts: set[str] = set()
    for entry in entries:
        text = entry.strip().lower()
        name = _normalize_ipv6(text.strip("[]")) if ":" in text else _normalize_name(text)
        if name is None:
            raise ValueError(f"PANEL_ALLOWED_HOSTS invalido: {entry!r} (use hostnames sem porta)")
        hosts.add(name)
    if not hosts:
        raise ValueError("PANEL_ALLOWED_HOSTS esta vazio: nenhum host poderia aceder ao painel")
    return frozenset(hosts)


def _host_values(scope: Scope) -> list[bytes]:
    return [value for name, value in scope["headers"] if name == b"host"]


class AllowedHostsMiddleware:
    """Middleware ASGI puro: 400 antes da aplicacao se o `Host` nao estiver na lista."""

    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str]) -> None:
        """Guarda a aplicacao e a lista validada (`ValueError` se for invalida)."""
        self._app = app
        self._allowed = normalize_allowed_hosts(allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Deixa passar `lifespan`; em HTTP e WebSocket exige um unico `Host` permitido."""
        if scope["type"] == "lifespan" or self._is_allowed(scope):
            await self._app(scope, receive, send)
        elif scope["type"] == "http":
            await self._deny(send)
        else:
            await send({"type": "websocket.close", "code": 1008})

    def _is_allowed(self, scope: Scope) -> bool:
        values = _host_values(scope)
        if len(values) != 1:
            return False
        hostname = parse_host_header(values[0].decode("latin-1"))
        return hostname is not None and hostname in self._allowed

    @staticmethod
    async def _deny(send: Send) -> None:
        body = DENIED_MESSAGE.encode()
        headers = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ]
        await send({"type": "http.response.start", "status": 400, "headers": headers})
        await send({"type": "http.response.body", "body": body})
