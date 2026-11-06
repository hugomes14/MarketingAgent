"""Autenticacao do painel por proxy confiavel (cabecalhos do `tailscale serve`).

A identidade vem do cabecalho `Tailscale-User-Login`, que so merece confianca quando a
ligacao TCP vem do proxy (o Serve remove os cabecalhos de identidade do cliente). Por isso a
decisao e tomada por esta ordem: 1) origem da ligacao; 2) cabecalho de login; 3) lista de
logins. `X-Forwarded-*`, `Forwarded` e `X-Real-IP` nunca sao lidos: qualquer cliente os forja.

Todas as negacoes 403 devolvem o mesmo texto, para a resposta nao servir de oraculo sobre a
lista de logins, os proxies confiaveis ou a validade do cabecalho.

Requisito do servidor ASGI: a seguranca desta dependencia exige que o servidor corra com a
reescrita de cabecalhos de proxy DESLIGADA (uvicorn `proxy_headers=False`, ou seja
`--no-proxy-headers`, e `FORWARDED_ALLOW_IPS` nao definido). Caso contrario, o `request.client`
pode ser substituido pelo valor de `X-Forwarded-For` e um peer nao confiavel passa a parecer o
proxy. O lancador do painel (Tarefa 15) tem de impor isto.
"""

import ipaddress
import logging
from dataclasses import dataclass
from functools import lru_cache

from fastapi import HTTPException, Request

from marketing_agent.config import Settings

logger = logging.getLogger(__name__)

LOGIN_HEADER = "Tailscale-User-Login"
NAME_HEADER = "Tailscale-User-Name"
MAX_LOGIN_CHARS = 254
MAX_NAME_CHARS = 100

UNAUTHENTICATED_MESSAGE = "Autenticação necessária."
DENIED_MESSAGE = "Acesso negado."

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class TailscaleUser:
    """Utilizador autenticado pelo proxy. `name` serve so para apresentacao."""

    login: str
    name: str | None


def require_tailscale_user(request: Request) -> TailscaleUser:
    """Dependencia FastAPI: devolve o utilizador do proxy ou levanta 401/403.

    Le as definicoes de `request.app.state.settings`. Uma configuracao invalida em
    `panel_trusted_proxy` levanta `ValueError` (falha fechada, nunca ignorada).
    """
    settings = _settings_of(request)
    networks = _parse_networks(tuple(settings.panel_trusted_proxy))
    peer_host = request.client.host if request.client else None
    if not _is_trusted_peer(peer_host, networks):
        raise _deny("untrusted_peer", peer_host)
    login = _read_login(request, peer_host)
    allowed = {entry.strip().casefold() for entry in settings.panel_allowed_logins}
    if login.casefold() not in allowed:
        raise _deny("login_not_allowed", peer_host)
    return TailscaleUser(login=login, name=_read_name(request))


def _settings_of(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise RuntimeError(
            "app.state.settings nao esta definido: create_app deve guardar as Settings "
            "antes de usar require_tailscale_user"
        )
    return settings


@lru_cache(maxsize=16)
def _parse_networks(entries: tuple[str, ...]) -> tuple[IpNetwork, ...]:
    """Converte IPs ou CIDRs em redes; a cache e chaveada pelas proprias entradas."""
    networks: list[IpNetwork] = []
    for entry in entries:
        try:
            network = ipaddress.ip_network(entry.strip(), strict=False)
        except ValueError as error:
            raise ValueError(f"PANEL_TRUSTED_PROXY invalido: {entry!r}") from error
        if network.prefixlen == 0:
            # 0.0.0.0/0 e ::/0 confiam em todos os peers e desligam a verificacao de origem.
            raise ValueError(f"PANEL_TRUSTED_PROXY demasiado largo (confia em todos): {entry!r}")
        networks.append(network)
    return tuple(networks)


def _is_trusted_peer(peer_host: str | None, networks: tuple[IpNetwork, ...]) -> bool:
    address = _peer_address(peer_host)
    return address is not None and any(address in network for network in networks)


def _peer_address(peer_host: str | None) -> IpAddress | None:
    """Interpreta o peer; `::ffff:a.b.c.d` vira IPv4 e um nome que nao e IP e nao confiavel."""
    if peer_host is None:
        return None
    try:
        address = ipaddress.ip_address(peer_host)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _read_login(request: Request, peer_host: str | None) -> str:
    values = request.headers.getlist(LOGIN_HEADER)
    if not values:
        logger.warning(
            "painel: acesso sem cabecalho de login (motivo=missing_login) peer=%s", peer_host
        )
        raise HTTPException(status_code=401, detail=UNAUTHENTICATED_MESSAGE)
    if len(values) > 1:
        raise _deny("ambiguous_login_header", peer_host)
    raw = values[0]
    login = raw.strip()
    # isprintable() rejeita caracteres de controlo (incluindo NUL e ESC) no valor bruto.
    if not login or len(login) > MAX_LOGIN_CHARS or not raw.isprintable():
        raise _deny("invalid_login_header", peer_host)
    return login


def _read_name(request: Request) -> str | None:
    name = request.headers.get(NAME_HEADER, "").strip()[:MAX_NAME_CHARS].strip()
    return name or None


def _deny(reason: str, peer_host: str | None) -> HTTPException:
    """Regista o motivo e o peer (nunca login, cabecalhos ou lista) e devolve o 403 generico."""
    logger.warning("painel: acesso negado (motivo=%s) peer=%s", reason, peer_host)
    return HTTPException(status_code=403, detail=DENIED_MESSAGE)
