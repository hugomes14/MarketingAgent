"""Montagem da aplicacao FastAPI do painel (servido atras do `tailscale serve`).

A seguranca assenta em quatro pontos: identidade do proxy confiavel (`auth`), CSRF em todas as
mutacoes (`csrf`), saida escapada por omissao (`rendering`) e cabecalhos de seguranca em todas
as respostas (`security`). O lancador do painel tem de correr o uvicorn sem reescrita de
cabecalhos de proxy (ver o docstring de `auth`).
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from marketing_agent.budget.controller import BudgetController
from marketing_agent.config import Settings
from marketing_agent.interview.service import InterviewService
from marketing_agent.oficina.config import OficinaConfig
from marketing_agent.oficina.queries import TeamStateQueries
from marketing_agent.panel import (
    routes_batch,
    routes_eval,
    routes_feedback,
    routes_home,
    routes_interview,
    routes_kb,
    routes_kit,
    routes_media,
    routes_oficina,
    routes_plan,
    routes_strategy,
)
from marketing_agent.panel.auth import _parse_networks
from marketing_agent.panel.context import ContentServices, EvalServices, MediaServices, PanelContext
from marketing_agent.panel.csrf import MIN_SECRET_BYTES
from marketing_agent.panel.errors import install_error_handlers
from marketing_agent.panel.hosts import AllowedHostsMiddleware, normalize_allowed_hosts
from marketing_agent.panel.queries import PanelQueries
from marketing_agent.panel.rendering import STATIC_DIR, Renderer
from marketing_agent.panel.security import SecurityHeadersMiddleware
from marketing_agent.strategy.service import StrategyService


def create_app(
    settings: Settings,
    interview: InterviewService,
    strategy: StrategyService,
    budget: BudgetController,
    csrf_secret: bytes,
    *,
    brand_id: str,
    queries: PanelQueries,
    content: ContentServices,
    media: MediaServices,
    team: TeamStateQueries,
    oficina: OficinaConfig,
    evals: EvalServices | None = None,
) -> FastAPI:
    """Monta o painel; falha no arranque se a configuracao de seguranca for invalida.

    Levanta `ValueError` se `panel_trusted_proxy` tiver uma entrada invalida ou demasiado
    larga, se `panel_allowed_logins` estiver vazio (ninguem poderia entrar) ou se o segredo
    CSRF tiver menos de `MIN_SECRET_BYTES` bytes, ou se `panel_allowed_hosts` estiver vazio ou
    tiver entradas invalidas (`*`, vazias, com porta). Qualquer pedido cujo `Host` nao esteja
    nessa lista recebe 400 antes da autenticacao.
    """
    _validate_security_settings(settings, csrf_secret)
    # Sem /docs, /redoc nem /openapi.json: o painel nao expoe o esquema das rotas.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings  # exigido por require_tailscale_user
    app.state.csrf_secret = csrf_secret
    renderer = Renderer(csrf_secret)
    app.state.panel = PanelContext(
        interview=interview,
        strategy=strategy,
        budget=budget,
        queries=queries,
        renderer=renderer,
        brand_id=brand_id,
        max_message_chars=settings.max_message_chars,
        content=content,
        media=media,
        team=team,
        oficina=oficina,
        evals=evals,
    )
    # Ordem: o ultimo adicionado e o mais externo. Os cabecalhos de seguranca envolvem o filtro
    # de Host, para tambem cobrirem as suas respostas 400.
    app.add_middleware(AllowedHostsMiddleware, allowed_hosts=settings.panel_allowed_hosts)
    app.add_middleware(SecurityHeadersMiddleware)
    install_error_handlers(app, renderer)
    for module in (
        routes_home,
        routes_interview,
        routes_media,
        routes_kit,
        routes_strategy,
        routes_kb,
        routes_plan,
        routes_batch,
        routes_feedback,
        routes_eval,
        routes_oficina,
    ):
        app.include_router(module.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _validate_security_settings(settings: Settings, csrf_secret: bytes) -> None:
    _parse_networks(tuple(settings.panel_trusted_proxy))
    normalize_allowed_hosts(settings.panel_allowed_hosts)
    if not any(login.strip() for login in settings.panel_allowed_logins):
        raise ValueError("PANEL_ALLOWED_LOGINS esta vazio: ninguem poderia entrar no painel")
    if len(csrf_secret) < MIN_SECRET_BYTES:
        raise ValueError(f"o segredo CSRF deve ter pelo menos {MIN_SECRET_BYTES} bytes")
