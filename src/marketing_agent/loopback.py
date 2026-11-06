"""Enderecos de loopback: o painel e o proxy de desenvolvimento nunca saem desta maquina."""

import ipaddress

MAX_PORT = 65535


def is_loopback_host(host: str) -> bool:
    """True para `localhost` e para IPs literais de loopback (IPv4 `127.0.0.0/8` e `::1`).

    Nomes de dominio nao sao resolvidos: um nome so conta se for `localhost`.
    """
    name = host.strip().removeprefix("[").removesuffix("]")
    if name.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def parse_loopback_endpoint(text: str, option: str) -> tuple[str, int]:
    """Converte `host:porta` (ou `[::1]:porta`) num par (host, porta) de loopback.

    `localhost` passa a `127.0.0.1` para o endereco ser inequivoco.

    Raises:
        ValueError: com o nome de `option`, se o formato for invalido ou o host nao for loopback.
    """
    host, separator, port_text = text.rpartition(":")
    if not separator or not host or not port_text.isdigit():
        raise ValueError(f"{option}: esperado HOST:PORTA, recebido {text!r}")
    port = int(port_text)
    if not 1 <= port <= MAX_PORT:
        raise ValueError(f"{option}: porta fora do intervalo 1-{MAX_PORT}: {port}")
    if not is_loopback_host(host):
        raise ValueError(f"{option}: so enderecos de loopback sao permitidos, recebido {host!r}")
    clean = host.removeprefix("[").removesuffix("]")
    return ("127.0.0.1" if clean.lower() == "localhost" else clean), port
