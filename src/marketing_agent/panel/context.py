"""Estado partilhado do painel e dependencias FastAPI usadas pelas rotas."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Request
from fastapi.responses import RedirectResponse

from marketing_agent.budget.controller import BudgetController
from marketing_agent.interview.service import InterviewService
from marketing_agent.media.library import MediaLibrary
from marketing_agent.panel.auth import TailscaleUser, require_tailscale_user
from marketing_agent.panel.csrf import require_same_origin_and_csrf
from marketing_agent.panel.forms import FormFields, read_form
from marketing_agent.panel.queries import PanelQueries
from marketing_agent.panel.rendering import Renderer
from marketing_agent.strategy.service import StrategyService

UUID_PATTERN = r"^[0-9a-fA-F-]{36}$"


@dataclass(frozen=True)
class MediaServices:
    """Servicos de media injetados no painel."""

    library: MediaLibrary


@dataclass(frozen=True)
class PanelContext:
    """Servicos, leituras e marca com que as rotas trabalham (guardado em `app.state.panel`)."""

    interview: InterviewService
    strategy: StrategyService
    budget: BudgetController
    queries: PanelQueries
    renderer: Renderer
    brand_id: str
    max_message_chars: int
    media: MediaServices


def get_panel(request: Request) -> PanelContext:
    """Dependencia: o contexto montado por `create_app`."""
    panel: PanelContext = request.app.state.panel
    return panel


Panel = Annotated[PanelContext, Depends(get_panel)]
CurrentUser = Annotated[TailscaleUser, Depends(require_tailscale_user)]
Fields = Annotated[FormFields, Depends(read_form)]
RecordId = Annotated[str, Path(pattern=UUID_PATTERN)]
# Toda a rota que muda estado declara isto em `dependencies=[...]`.
MUTATION_GUARD = Depends(require_same_origin_and_csrf)


def see_other(url: str) -> RedirectResponse:
    """Redireciona um POST para a pagina de resultado (303: o browser volta a pedir com GET)."""
    return RedirectResponse(url, status_code=303)
