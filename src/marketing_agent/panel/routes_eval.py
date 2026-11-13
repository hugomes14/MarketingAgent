"""Corridas de avaliacao: estimar, confirmar e acompanhar sem revelar os percursos."""

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from marketing_agent.evals.runs import (
    EVAL_START_ERRORS,
    EstimateChanged,
    EvalSetMismatch,
    RunInProgress,
)
from marketing_agent.evals.views import EvalNotFound
from marketing_agent.panel.context import (
    MUTATION_GUARD,
    CurrentUser,
    EvalServices,
    Fields,
    Panel,
    PanelContext,
    RecordId,
    see_other,
)

router = APIRouter()


def services(panel: PanelContext) -> EvalServices:
    """Exige montagem dos servicos de avaliacao; nunca tenta chamadas de geracao no painel."""
    if panel.evals is None:
        raise HTTPException(503, "A avaliação não está configurada.")
    return panel.evals


@router.get("/avaliacao")
def list_runs(panel: Panel, user: CurrentUser) -> HTMLResponse:
    """Lista as corridas desta marca."""
    return panel.renderer.page(
        "eval_runs.html", user, {"runs": services(panel).views.list_runs(panel.brand_id)}
    )


@router.get("/avaliacao/nova")
def new_run(panel: Panel, user: CurrentUser, conjunto: str = "briefs_v1") -> HTMLResponse:
    """Mostra a estimativa antes de criar qualquer job ou custo."""
    try:
        estimate = services(panel).runs.estimate(panel.brand_id, conjunto)
    except EVAL_START_ERRORS:
        raise HTTPException(422, "Conjunto ou pré-requisitos de avaliação inválidos.") from None
    return panel.renderer.page("eval_new.html", user, {"estimate": estimate})


@router.post("/avaliacao", dependencies=[MUTATION_GUARD])
def start_run(panel: Panel, user: CurrentUser, fields: Fields) -> RedirectResponse:
    """Confirma teto e versao; a transacao cria corrida, itens e jobs juntos."""
    try:
        ceiling = Decimal(fields.get("teto", ""))
        version = int(fields.get("versao", ""))
        if not ceiling.is_finite() or ceiling < 0 or version < 1:
            raise ValueError
    except (ValueError, InvalidOperation):
        raise HTTPException(422, "Estimativa inválida.") from None
    try:
        run_id = services(panel).runs.start_run(
            panel.brand_id,
            fields.get("conjunto", ""),
            ceiling,
            user.login,
            expected_set_version=version,
        )
    except (EstimateChanged, EvalSetMismatch, RunInProgress):
        raise HTTPException(409, "A estimativa mudou ou já existe uma corrida em curso.") from None
    except EVAL_START_ERRORS:
        raise HTTPException(422, "Conjunto ou pré-requisitos de avaliação inválidos.") from None
    return see_other(f"/avaliacao/{run_id}")


def progress_context(panel: PanelContext, run_id: str) -> dict[str, object]:
    """Leitura agregada com verificacao da marca e erro 404 fixo."""
    try:
        progress = services(panel).views.progress(panel.brand_id, run_id)
    except EvalNotFound:
        raise HTTPException(404, "Corrida inexistente.") from None
    return {"progress": progress}


@router.get("/avaliacao/{run_id}")
def show_run(run_id: RecordId, panel: Panel, user: CurrentUser) -> HTMLResponse:
    """Pagina da corrida sem ids de itens, percursos ou ordem de conclusao."""
    return panel.renderer.page("eval_run.html", user, progress_context(panel, run_id))


@router.get("/avaliacao/{run_id}/estado")
def show_progress(run_id: RecordId, panel: Panel, user: CurrentUser) -> HTMLResponse:
    """Fragmento HTMX; a sondagem termina quando todos os itens sao terminais."""
    return panel.renderer.partial("_eval_progress.html", progress_context(panel, run_id))
