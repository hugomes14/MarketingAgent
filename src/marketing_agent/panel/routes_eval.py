"""Corridas de avaliacao: estimar, confirmar e acompanhar sem revelar os percursos."""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from typing import get_args

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from marketing_agent.evals.blind import ErrorKind, JudgementInput
from marketing_agent.evals.judging import (
    AlreadyRevealed,
    RevealBlocked,
    RunNotReady,
    StaleJudgement,
)
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
from marketing_agent.panel.forms import FormFields
from marketing_agent.panel.pair_timer import PairTimer

router = APIRouter()


def services(panel: PanelContext) -> EvalServices:
    """Exige montagem dos servicos de avaliacao; nunca tenta chamadas de geracao no painel."""
    if panel.evals is None:
        raise HTTPException(503, "A avaliação não está configurada.")
    return panel.evals


@contextmanager
def eval_errors() -> Iterator[None]:
    """Traduz falhas esperadas para mensagens fixas, sem dados dos pacotes."""
    try:
        yield
    except EvalNotFound:
        raise HTTPException(404, "Par ou corrida inexistente.") from None
    except (AlreadyRevealed, RevealBlocked, RunNotReady, StaleJudgement):
        raise HTTPException(
            409, "A avaliação mudou ou ainda não permite esta ação. Recarregue."
        ) from None
    except (ValidationError, ValueError):
        raise HTTPException(422, "Resposta ou temporizador inválido.") from None


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
    context = progress_context(panel, run_id)
    with eval_errors():
        progress = services(panel).views.progress(panel.brand_id, run_id)
        context["summary"] = (
            services(panel).judging.summary(panel.brand_id, run_id)
            if progress.status != "running"
            else None
        )
    return panel.renderer.page("eval_run.html", user, context)


@router.get("/avaliacao/{run_id}/estado")
def show_progress(run_id: RecordId, panel: Panel, user: CurrentUser) -> HTMLResponse:
    """Fragmento HTMX; a sondagem termina quando todos os itens sao terminais."""
    return panel.renderer.partial("_eval_progress.html", progress_context(panel, run_id))


ERROR_LABELS = {
    "facto_errado": "Facto errado",
    "alegacao_sem_apoio": "Alegação sem apoio",
    "voz_errada": "Voz errada",
    "imagem_inadequada": "Imagem inadequada",
    "outro": "Outro",
}
EDIT_LABELS = {
    "0": "0 minutos",
    "1": "1 minuto",
    "2_5": "2–5 minutos",
    "mais_5": "Mais de 5 minutos",
}


@router.get("/avaliacao/{run_id}/par/{index}")
def show_pair(
    run_id: RecordId, index: int, request: Request, panel: Panel, user: CurrentUser
) -> HTMLResponse:
    """Mostra A/B com exatamente os mesmos campos e sem proveniencia dos percursos."""
    with eval_errors():
        pair = services(panel).judging.pair(panel.brand_id, run_id, index)
        summary = services(panel).judging.summary(panel.brand_id, run_id)
    timer = PairTimer(request.app.state.csrf_secret)
    return panel.renderer.page(
        "eval_pair.html",
        user,
        {
            "run_id": run_id,
            "pair": pair,
            "summary": summary,
            "inicio": timer.issue(run_id, index, panel.content.clock.now()),
            "error_labels": ERROR_LABELS,
            "edit_labels": EDIT_LABELS,
            "max_note_chars": min(panel.max_message_chars, 2000),
        },
    )


@router.get("/avaliacao/{run_id}/par/{index}/imagem/{side}")
def pair_image(
    run_id: RecordId,
    index: int,
    side: str,
    panel: Panel,
    user: CurrentUser,
) -> Response:
    """Serve JPEG final num URL de lado, sem asset id, nome, ETag ou Content-Disposition."""
    if side not in ("a", "b"):
        raise HTTPException(404, "Lado inexistente.")
    with eval_errors():
        asset_id = services(panel).judging.pair_image(
            panel.brand_id, run_id, index, "a" if side == "a" else "b"
        )
        asset = panel.media.library.read_asset(panel.brand_id, asset_id)
    if asset.mime != "image/jpeg":
        raise HTTPException(422, "Imagem de avaliação inválida.")
    return Response(asset.data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _answer(fields: FormFields) -> JudgementInput:
    return JudgementInput.model_validate(
        {
            "better": fields.get("better"),
            "edit_a": fields.get("edit_a"),
            "edit_b": fields.get("edit_b"),
            "errors_a": [
                kind for kind in get_args(ErrorKind) if fields.get(f"erro_a_{kind}") == "on"
            ],
            "errors_b": [
                kind for kind in get_args(ErrorKind) if fields.get(f"erro_b_{kind}") == "on"
            ],
            "note": fields.get("note") or None,
        }
    )


@router.post("/avaliacao/{run_id}/par/{index}", dependencies=[MUTATION_GUARD])
def record_pair(
    run_id: RecordId,
    index: int,
    request: Request,
    panel: Panel,
    user: CurrentUser,
    fields: Fields,
) -> RedirectResponse:
    """Mede tempo no servidor e grava uma resposta nova, preservando qualquer resposta anterior."""
    with eval_errors():
        seconds = PairTimer(request.app.state.csrf_secret).seconds(
            fields.get("inicio", ""), run_id, index, panel.content.clock.now()
        )
        services(panel).judging.record(
            panel.brand_id,
            run_id,
            index,
            _answer(fields),
            seconds,
            fields.get("substitui") or None,
            user.login,
        )
        summary = services(panel).judging.summary(panel.brand_id, run_id)
    suffix = f"/par/{summary.missing[0]}" if summary.missing else ""
    return see_other(f"/avaliacao/{run_id}{suffix}")


@router.post("/avaliacao/{run_id}/revelar", dependencies=[MUTATION_GUARD])
def reveal_run(run_id: RecordId, panel: Panel, user: CurrentUser) -> RedirectResponse:
    """Revelacao explicita depois de avaliar todos os pares elegiveis."""
    with eval_errors():
        services(panel).judging.reveal(panel.brand_id, run_id, user.login)
    return see_other(f"/avaliacao/{run_id}")
