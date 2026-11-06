"""Protecao CSRF das mutacoes do painel: mesma origem e token HMAC por utilizador.

O token e o HMAC-SHA256 do login (em minusculas) com um segredo do servidor; nao e guardado
em sessao porque o painel nao tem sessoes: a identidade vem do proxy a cada pedido. Todas as
recusas devolvem o mesmo 403 generico, para a resposta nao revelar qual verificacao falhou.
"""

import hashlib
import hmac
import logging
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request

from marketing_agent.panel.auth import TailscaleUser, require_tailscale_user
from marketing_agent.panel.forms import read_form

logger = logging.getLogger(__name__)

CSRF_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
DENIED_MESSAGE = "Pedido recusado."
MIN_SECRET_BYTES = 16


def csrf_token_for(user: TailscaleUser, secret: bytes) -> str:
    """Token CSRF do utilizador: HMAC-SHA256 hexadecimal do login em minusculas."""
    return hmac.new(secret, user.login.casefold().encode(), hashlib.sha256).hexdigest()


async def require_same_origin_and_csrf(
    request: Request,
    user: Annotated[TailscaleUser, Depends(require_tailscale_user)],
) -> None:
    """Dependencia das mutacoes: 403 se o pedido nao for da mesma origem ou o token falhar.

    Ordem: autenticacao (401/403 do proxy), `Sec-Fetch-Site`, `Origin`, e por fim o token do
    cabecalho `X-CSRF-Token` ou, se o cabecalho faltar, do campo de formulario `csrf_token`.
    """
    _check_fetch_site(request)
    _check_origin(request)
    supplied = await _supplied_token(request)
    expected = csrf_token_for(user, _secret_of(request))
    if supplied is None or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise _deny(request, "invalid_token")


def _check_fetch_site(request: Request) -> None:
    site = request.headers.get("sec-fetch-site")
    if site is not None and site != "same-origin":
        raise _deny(request, "cross_site")


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return
    host = request.headers.get("host", "").strip().lower()
    # Compara so o netloc: atras do proxy o esquema visto pela app pode diferir do do browser.
    # `Origin: null` fica com netloc vazio e nunca coincide com um Host real.
    if not host or urlsplit(origin).netloc.lower() != host:
        raise _deny(request, "origin_mismatch")


async def _supplied_token(request: Request) -> str | None:
    header = request.headers.get(CSRF_HEADER)
    if header is not None:
        return header
    try:
        fields = await read_form(request)
    except HTTPException:
        return None  # corpo ilegivel nao e um token valido: cai no 403 generico
    return fields.get(CSRF_FIELD)


def _secret_of(request: Request) -> bytes:
    secret = getattr(request.app.state, "csrf_secret", None)
    if not isinstance(secret, bytes) or len(secret) < MIN_SECRET_BYTES:
        raise RuntimeError("app.state.csrf_secret nao esta definido: create_app deve guarda-lo")
    return secret


def _deny(request: Request, reason: str) -> HTTPException:
    """Regista o motivo e o peer (nunca o token nem o corpo) e devolve o 403 generico."""
    peer = request.client.host if request.client else None
    logger.warning("painel: pedido recusado (motivo=%s) peer=%s", reason, peer)
    return HTTPException(status_code=403, detail=DENIED_MESSAGE)
