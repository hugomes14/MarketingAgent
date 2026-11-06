"""Cabecalhos de seguranca em todas as respostas do painel (incluindo estaticos e erros)."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'"
)


def apply_security_headers(headers: MutableHeaders) -> None:
    """Define CSP, `nosniff`, `Referrer-Policy` e, em HTML, `Cache-Control: no-store`.

    Paginas HTML nunca ficam em cache: contem dados da marca e tokens CSRF. Os estaticos
    (JS e CSS fixos) podem ser guardados pelo browser.
    """
    headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Referrer-Policy"] = "no-referrer"
    if headers.get("content-type", "").startswith("text/html"):
        headers["Cache-Control"] = "no-store"


class SecurityHeadersMiddleware:
    """Middleware ASGI que aplica `apply_security_headers` ao inicio de cada resposta HTTP."""

    def __init__(self, app: ASGIApp) -> None:
        """Envolve a aplicacao ASGI."""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Passa o pedido adiante e acrescenta os cabecalhos a resposta."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                apply_security_headers(MutableHeaders(scope=message))
            await send(message)

        await self._app(scope, receive, send_with_headers)
