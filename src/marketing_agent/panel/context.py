"""Estado partilhado do painel e dependencias FastAPI usadas pelas rotas."""

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Request
from fastapi.responses import RedirectResponse

from marketing_agent.artifacts.store import ArtifactRecord
from marketing_agent.budget.controller import BudgetController
from marketing_agent.clock import Clock
from marketing_agent.content.review import ReviewService
from marketing_agent.evals.judging import JudgingService
from marketing_agent.evals.runs import EvalRunService
from marketing_agent.evals.views import EvalViews
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.harness.engine import RunResult, RunStatus
from marketing_agent.interview.service import InterviewService
from marketing_agent.kb.service import KbService
from marketing_agent.media.brand_kit import BrandKitService
from marketing_agent.media.library import MediaLibrary
from marketing_agent.panel.auth import TailscaleUser, require_tailscale_user
from marketing_agent.panel.csrf import require_same_origin_and_csrf
from marketing_agent.panel.errors import UpstreamFailure
from marketing_agent.panel.forms import FormFields, read_form
from marketing_agent.panel.queries import PanelQueries
from marketing_agent.panel.rendering import Renderer
from marketing_agent.planning.service import PlanningService
from marketing_agent.strategy.service import StrategyService

logger = logging.getLogger(__name__)
UUID_PATTERN = r"^[0-9a-fA-F-]{36}$"
WEEK_PATTERN = r"^\d{4}-W\d{2}$"


@dataclass(frozen=True)
class ContentServices:
    """Servicos do texto semanal (base, plano, revisao, feedback) e o relogio das paginas."""

    kb: KbService
    planning: PlanningService
    review: ReviewService
    feedback: FeedbackService
    clock: Clock


@dataclass(frozen=True)
class MediaServices:
    """Servicos de media injetados no painel."""

    library: MediaLibrary
    kits: BrandKitService


@dataclass(frozen=True)
class EvalServices:
    """Servicos da comparacao cega, independentes do lote semanal."""

    runs: EvalRunService
    views: EvalViews
    judging: JudgingService


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
    content: ContentServices
    media: MediaServices
    evals: EvalServices | None = None


def get_panel(request: Request) -> PanelContext:
    """Dependencia: o contexto montado por `create_app`."""
    panel: PanelContext = request.app.state.panel
    return panel


Panel = Annotated[PanelContext, Depends(get_panel)]
CurrentUser = Annotated[TailscaleUser, Depends(require_tailscale_user)]
Fields = Annotated[FormFields, Depends(read_form)]
RecordId = Annotated[str, Path(pattern=UUID_PATTERN)]
# Semana ISO `AAAA-Www`; que exista no calendario verifica-se a seguir (`require_week`).
WeekId = Annotated[str, Path(pattern=WEEK_PATTERN)]
# Toda a rota que muda estado declara isto em `dependencies=[...]`.
MUTATION_GUARD = Depends(require_same_origin_and_csrf)


def see_other(url: str) -> RedirectResponse:
    """Redireciona um POST para a pagina de resultado (303: o browser volta a pedir com GET)."""
    return RedirectResponse(url, status_code=303)


def proposed_id(run: RunResult) -> str:
    """Id do artefacto submetido por uma proposta bem-sucedida; senao `UpstreamFailure`.

    Nada do resultado do modelo vai para a mensagem nem para o log: so o estado e o id da execucao.
    """
    if run.status is RunStatus.SUCCEEDED and isinstance(run.output, ArtifactRecord):
        return run.output.id
    logger.warning("painel: proposta sem sucesso (%s) execucao %s", run.status.value, run.run_id)
    raise UpstreamFailure(f"execucao {run.run_id} terminou em {run.status.value}")
