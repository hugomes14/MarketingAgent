"""Mapeamento de erros de dominio para respostas do painel.

Nenhuma mensagem de excecao chega ao utilizador (podem citar ids ou chaves) e nenhum log leva
texto do utilizador ou de modelos: regista-se so a classe da excecao, o estado e o caminho.
Pedidos HTMX recebem um fragmento redirecionado para `#estado`; os restantes, uma pagina.
"""

import logging
from typing import Final

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from starlette.exceptions import HTTPException

from marketing_agent.artifacts.store import ArtifactNotFound, InvalidTransition
from marketing_agent.interview.service import (
    InterviewStartFailed,
    InvalidAnswer,
    InvalidSessionState,
    ProfileRequestFailed,
    SessionBusy,
    SessionNotFound,
    SubmissionConflict,
)
from marketing_agent.kb.service import KbError
from marketing_agent.panel.rendering import Renderer
from marketing_agent.panel.security import apply_security_headers
from marketing_agent.strategy.service import (
    IncompleteDecisions,
    MissingPrerequisite,
    PendingProposal,
    ProposalInProgress,
)

logger = logging.getLogger(__name__)

BUSY_MESSAGE: Final = "Há uma resposta em curso."
UPSTREAM_MESSAGE: Final = "Não foi possível gerar o resultado. Tente novamente mais tarde."
NOT_FOUND_MESSAGE: Final = "Não encontrado."
INVALID_REQUEST_MESSAGE: Final = "Pedido inválido."
INTERNAL_MESSAGE: Final = "Ocorreu um erro interno. Tente novamente."
STATUS_AREA: Final = "#estado"


class InvalidForm(Exception):
    """Campo de formulario invalido; a mensagem e fixa e segura para mostrar ao utilizador."""


class UpstreamFailure(Exception):
    """A geracao pelo modelo nao terminou com sucesso (execucao falhada, bloqueada, etc.)."""


# Excecao -> (estado HTTP, mensagem em portugues). `InvalidForm` usa a sua propria mensagem.
_RESPONSES: Final[dict[type[Exception], tuple[int, str]]] = {
    SubmissionConflict: (409, "Esta chave já foi usada com outra resposta. Recarregue a página."),
    SessionBusy: (409, BUSY_MESSAGE),
    InvalidAnswer: (422, "A resposta está vazia ou é demasiado longa."),
    IncompleteDecisions: (422, "Decida todas as propostas: aceitar ou recusar cada uma."),
    MissingPrerequisite: (
        409,
        "Falta um passo anterior: confirme primeiro o perfil e o diagnóstico.",
    ),
    PendingProposal: (409, "Já existe uma proposta deste tipo à espera da sua decisão."),
    ProposalInProgress: (409, "Já há uma proposta em curso. Aguarde."),
    InvalidSessionState: (409, "Esta entrevista já não aceita esta ação."),
    # Fixa: as mensagens do `KbError` citam chaves e ids da base, que podem ser texto do utilizador.
    KbError: (
        422,
        "Não foi possível guardar na base de conhecimento: o item já existe ou não foi encontrado.",
    ),
    InvalidTransition: (409, "Este item já foi decidido ou já não aceita esta ação."),
    SessionNotFound: (404, NOT_FOUND_MESSAGE),
    ArtifactNotFound: (404, NOT_FOUND_MESSAGE),
    ProfileRequestFailed: (502, UPSTREAM_MESSAGE),
    InterviewStartFailed: (502, UPSTREAM_MESSAGE),
    UpstreamFailure: (502, UPSTREAM_MESSAGE),
}


def is_htmx(request: Request) -> bool:
    """Verdadeiro para pedidos feitos pelo HTMX (cabecalho `HX-Request`)."""
    return request.headers.get("hx-request", "").lower() == "true"


class _ErrorHandlers:
    """Tratadores de excecoes; cada um regista so a classe, o estado e o caminho."""

    def __init__(self, renderer: Renderer) -> None:
        self._renderer = renderer

    def respond(self, request: Request, status_code: int, message: str) -> Response:
        """Resposta de erro (fragmento ou pagina) ja com os cabecalhos de seguranca."""
        response = _error_response(request, self._renderer, status_code, message)
        # Os tratadores de `Exception` correm fora do middleware: aplicar aqui e idempotente.
        apply_security_headers(response.headers)
        return response

    async def domain_error(self, request: Request, exc: Exception) -> Response:
        """Erro de dominio mapeado em `_RESPONSES` (tambem para subclasses)."""
        status_code, message = _lookup(exc)
        logger.info("painel: %s -> %s %s", type(exc).__name__, status_code, request.url.path)
        return self.respond(request, status_code, message)

    async def invalid_form(self, request: Request, exc: Exception) -> Response:
        """Formulario invalido: a mensagem de `InvalidForm` e fixa e segura."""
        logger.info("painel: formulario invalido %s", request.url.path)
        return self.respond(request, 422, str(exc))

    async def validation(self, request: Request, exc: Exception) -> Response:
        """Parametros do pedido invalidos (ex.: id mal formado), sem detalhes."""
        return self.respond(request, 422, INVALID_REQUEST_MESSAGE)

    async def http(self, request: Request, exc: Exception) -> Response:
        """`HTTPException`: JSON por omissao; fragmento para pedidos HTMX."""
        assert isinstance(exc, HTTPException)
        if not is_htmx(request):
            return await http_exception_handler(request, exc)
        return self.respond(request, exc.status_code, _http_message(exc))

    async def unexpected(self, request: Request, exc: Exception) -> Response:
        """Qualquer outra excecao: 500 generico, sem texto da excecao no log nem na resposta."""
        logger.error("painel: erro inesperado %s em %s", type(exc).__name__, request.url.path)
        return self.respond(request, 500, INTERNAL_MESSAGE)


def install_error_handlers(app: FastAPI, renderer: Renderer) -> None:
    """Regista os tratadores de erro de dominio, de validacao, HTTP e inesperados."""
    handlers = _ErrorHandlers(renderer)
    for exception_type in _RESPONSES:
        app.add_exception_handler(exception_type, handlers.domain_error)
    app.add_exception_handler(InvalidForm, handlers.invalid_form)
    app.add_exception_handler(RequestValidationError, handlers.validation)
    app.add_exception_handler(HTTPException, handlers.http)
    app.add_exception_handler(Exception, handlers.unexpected)


def _lookup(exc: Exception) -> tuple[int, str]:
    """Estado e mensagem da excecao, procurando tambem nas classes-base."""
    for exception_type in type(exc).__mro__:
        if exception_type in _RESPONSES:
            return _RESPONSES[exception_type]
    return 500, INTERNAL_MESSAGE


def _http_message(exc: HTTPException) -> str:
    """Mensagem de um `HTTPException`: a do painel (portugues) ou uma generica para o 404."""
    if exc.status_code == 404:
        return NOT_FOUND_MESSAGE
    return exc.detail if isinstance(exc.detail, str) else INVALID_REQUEST_MESSAGE


def _error_response(
    request: Request, renderer: Renderer, status_code: int, message: str
) -> Response:
    if is_htmx(request):
        headers = {"HX-Retarget": STATUS_AREA, "HX-Reswap": "innerHTML"}
        return renderer.partial("_error.html", {"message": message}, status_code, headers)
    return renderer.page(
        "error.html", None, {"message": message, "status_code": status_code}, status_code
    )
