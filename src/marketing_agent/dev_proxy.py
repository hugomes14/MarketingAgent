"""Proxy reverso minimo para DESENVOLVIMENTO: faz o papel do `tailscale serve` na maquina local.

Quem consegue ligar-se a porta de escuta passa a agir como o login configurado: por isso so
escuta em loopback (sem opcao para mudar) e nunca deve correr fora de uma maquina pessoal.
Em producao a identidade vem do `tailscale serve`, que remove os cabecalhos de identidade que
o cliente envia; este proxy faz o mesmo, descartando `Tailscale-*`, `X-Forwarded-*`, `Forwarded`
e `X-Real-IP` do cliente antes de juntar o seu `Tailscale-User-Login`.
So usa a biblioteca padrao. Nao segue redirecionamentos: devolve-os ao browser tal como vieram.
"""

import http.client
import logging
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from marketing_agent.loopback import parse_loopback_endpoint

logger = logging.getLogger(__name__)

LOGIN_HEADER = "Tailscale-User-Login"
NAME_HEADER = "Tailscale-User-Name"
MAX_LOGIN_CHARS = 254
MAX_NAME_CHARS = 100
MAX_BODY_BYTES = 1024 * 1024
UPSTREAM_TIMEOUT_S = 130  # acima do maior max_duration_s dos agentes (240 s so no diagnostico)
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)
FORGEABLE_PREFIXES = ("tailscale-", "x-forwarded-")
FORGEABLE_NAMES = frozenset({"forwarded", "x-real-ip"})

Headers = list[tuple[str, str]]


def validate_login(login: str) -> str:
    """Login (e-mail) em ASCII imprimivel, de 1 a 254 caracteres; senao `ValueError`."""
    if not 1 <= len(login) <= MAX_LOGIN_CHARS or not login.isascii() or not login.isprintable():
        raise ValueError(
            f"login invalido: use 1 a {MAX_LOGIN_CHARS} caracteres ASCII imprimiveis, sem controlo"
        )
    return login


def validate_name(name: str) -> str:
    """Nome de apresentacao em ASCII imprimivel, ate 100 caracteres; senao `ValueError`."""
    if len(name) > MAX_NAME_CHARS or not name.isascii() or not name.isprintable():
        raise ValueError(
            f"nome invalido: use ate {MAX_NAME_CHARS} caracteres ASCII imprimiveis, sem controlo"
        )
    return name


def _is_forgeable(name: str) -> bool:
    lowered = name.lower()
    return lowered in FORGEABLE_NAMES or lowered.startswith(FORGEABLE_PREFIXES)


def _connection_tokens(headers: Iterable[tuple[str, str]]) -> set[str]:
    """Cabecalhos que o cliente declarou como salto-a-salto em `Connection`."""
    tokens: set[str] = set()
    for name, value in headers:
        if name.lower() == "connection":
            tokens.update(token.strip().lower() for token in value.split(","))
    return tokens


def forwarded_request_headers(
    incoming: Iterable[tuple[str, str]], login: str, name: str | None
) -> Headers:
    """Cabecalhos para o destino: sem identidade forjada nem salto-a-salto, com o login fixo."""
    pairs = list(incoming)
    declared = _connection_tokens(pairs)
    kept = [
        (key, value)
        for key, value in pairs
        if key.lower() not in HOP_BY_HOP and key.lower() not in declared and not _is_forgeable(key)
    ]
    kept.append((LOGIN_HEADER, login))
    if name is not None:
        kept.append((NAME_HEADER, name))
    kept.append(("Connection", "close"))
    return kept


class DevProxyServer(ThreadingHTTPServer):
    """Servidor com o destino e a identidade que o manipulador injeta."""

    daemon_threads = True

    def __init__(
        self,
        listen: tuple[str, int],
        upstream: tuple[str, int],
        login: str,
        name: str | None,
    ) -> None:
        """Escuta em `listen` e reencaminha para `upstream` como `login`."""
        self.upstream = upstream
        self.login = login
        self.name = name
        super().__init__(listen, _ProxyHandler)


class _ProxyHandler(BaseHTTPRequestHandler):
    server: DevProxyServer
    protocol_version = "HTTP/1.1"

    def _forward(self) -> None:
        body = self._read_body()
        if body is None:
            return
        headers = forwarded_request_headers(
            self.headers.items(), self.server.login, self.server.name
        )
        host, port = self.server.upstream
        connection = http.client.HTTPConnection(host, port, timeout=UPSTREAM_TIMEOUT_S)
        try:
            self._send_upstream(connection, headers, body)
            self._relay(connection.getresponse())
        except (OSError, http.client.HTTPException) as exc:
            logger.warning("dev-proxy: destino indisponivel (%s)", type(exc).__name__)
            self._reply_text(502, "Bad Gateway: o painel nao respondeu.")
        finally:
            connection.close()

    def _send_upstream(
        self, connection: http.client.HTTPConnection, headers: Headers, body: bytes
    ) -> None:
        """Envia o pedido tal como recebido; `putheader` mantem cabecalhos repetidos."""
        connection.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
        for key, value in headers:
            connection.putheader(key, value)
        connection.endheaders(body or None)

    def _read_body(self) -> bytes | None:
        """Corpo completo (Content-Length); `None` quando ja foi enviada uma resposta de erro."""
        if "transfer-encoding" in {key.lower() for key in self.headers}:
            self._reply_text(411, "Length Required: use Content-Length.")
            return None
        length = self.headers.get("Content-Length", "0")
        if not length.isdigit():
            self._reply_text(400, "Bad Request: Content-Length invalido.")
            return None
        if int(length) > MAX_BODY_BYTES:
            self._reply_text(413, "Payload Too Large.")
            return None
        return self.rfile.read(int(length)) if int(length) else b""

    def _relay(self, response: http.client.HTTPResponse) -> None:
        body = response.read() if self.command != "HEAD" else b""
        self.send_response_only(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() not in HOP_BY_HOP and key.lower() != "content-length":
                self.send_header(key, value)
        length = response.getheader("Content-Length") if self.command == "HEAD" else len(body)
        self.send_header("Content-Length", str(length or 0))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _reply_text(self, status: int, message: str) -> None:
        data = message.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Regista cada pedido (metodo, caminho, estado) no logger do modulo."""
        logger.info("dev-proxy: %s", format % args)


def build_dev_proxy(
    listen: tuple[str, int], upstream: tuple[str, int], login: str, name: str | None
) -> DevProxyServer:
    """Cria o proxy (ainda sem servir); recusa enderecos que nao sejam de loopback.

    Raises:
        ValueError: se `listen` ou `upstream` nao forem loopback, ou o login/nome forem invalidos.
    """
    for option, (host, port) in (("--listen", listen), ("--upstream", upstream)):
        parse_loopback_endpoint(f"{host}:{port or 1}", option)
    validate_login(login)
    if name is not None:
        validate_name(name)
    return DevProxyServer(listen, upstream, login, name)
