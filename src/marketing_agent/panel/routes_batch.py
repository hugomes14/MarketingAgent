"""Lote semanal: ver as publicacoes da semana e decidir sobre cada uma.

Quatro acoes por publicacao (aprovar, editar, pedir alteracoes, rejeitar), todas POST com o
guarda de mutacao. O autor e sempre o utilizador da sessao. Cada formulario leva o id da revisao
que a pagina mostrava: se entretanto surgiu outra (edicao noutra aba, geracao nova), o servico
recusa com 409 e o responsavel recarrega a pagina. O `ReviewService` e a autoridade sobre o que
e permitido; a pagina so esconde os botoes que ele recusaria.
"""

from datetime import timedelta
from typing import get_args

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from marketing_agent.feedback.service import FeedbackChoice
from marketing_agent.panel.context import (
    MUTATION_GUARD,
    CurrentUser,
    Fields,
    Panel,
    RecordId,
    WeekId,
    see_other,
)
from marketing_agent.panel.errors import InvalidForm
from marketing_agent.panel.inputs_review import (
    TEMPORARY,
    read_caption_edit,
    read_feedback,
    read_revision_id,
)
from marketing_agent.panel.rendering import Context
from marketing_agent.planning.validation import week_range

router = APIRouter()
BATCH_URL = "/lote"


def require_week(week: str) -> str:
    """A semana que o caminho ja trouxe no formato `AAAA-Www`, se existir no calendario ISO."""
    try:
        week_range(week)
    except ValueError:
        raise InvalidForm("Semana inválida: use AAAA-Www, por exemplo 2026-W46.") from None
    return week


def _neighbour_week(week: str, days: int) -> str:
    monday, _ = week_range(week)
    year, number, _ = (monday + timedelta(days=days)).isocalendar()
    return f"{year}-W{number:02d}"


def _batch_context(panel: Panel, week: str) -> Context:
    view = panel.queries.batch_view(panel.brand_id, week, panel.content.kb.valid_refs)
    return {
        "week": week,
        "view": view,
        "edit_choices": get_args(FeedbackChoice),
        # Rejeitar nao leva validade, por isso `temporario` (que a exige) so existe ao editar.
        "reject_choices": tuple(c for c in get_args(FeedbackChoice) if c != TEMPORARY),
        "max_comment_chars": panel.max_message_chars,
    }


@router.get("/lote")
def current_batch(user: CurrentUser, panel: Panel) -> RedirectResponse:
    """Sem semana: a semana com publicacoes por rever (a mais antiga) ou, senao, a de hoje."""
    week = panel.queries.review_week(panel.brand_id)
    if week is None:
        year, number, _ = panel.content.clock.now().isocalendar()
        week = f"{year}-W{number:02d}"
    return see_other(f"{BATCH_URL}/{week}")


@router.get("/lote/{week}")
def show_batch(week: WeekId, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Cartoes da semana; so le (ver o lote nunca aprova nem gera nada)."""
    require_week(week)
    context = {
        **_batch_context(panel, week),
        "previous_week": _neighbour_week(week, -7),
        "next_week": _neighbour_week(week, 7),
    }
    return panel.renderer.page("batch.html", user, context)


@router.get("/lote/{week}/estado")
def show_batch_state(week: WeekId, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Fragmento HTMX com os cartoes; so volta a pedir a si mesmo enquanto houver geracao."""
    require_week(week)
    context = {
        **_batch_context(panel, week),
        "user": user,
        "csrf_token": panel.renderer.token_for(user),
    }
    return panel.renderer.partial("_batch_cards.html", context)


def _batch_redirect(panel: Panel, post_id: str) -> str:
    """URL do lote da semana da publicacao; `PostNotFound` (404) se nao for desta marca."""
    return f"{BATCH_URL}/{panel.queries.post_week(panel.brand_id, post_id)}#post-{post_id}"


@router.post("/post/{post_id}/aprovar", dependencies=[MUTATION_GUARD])
def approve_post(
    post_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Aprova a revisao que a pagina mostrava; so vale se ainda for a mais recente."""
    url = _batch_redirect(panel, post_id)
    panel.content.review.approve(panel.brand_id, post_id, read_revision_id(fields), user.login)
    return see_other(url)


@router.post("/post/{post_id}/editar", dependencies=[MUTATION_GUARD])
def edit_post(
    post_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Cria uma revisao do responsavel; exige o tipo de feedback (sem valor por omissao)."""
    url = _batch_redirect(panel, post_id)
    base_revision_id = read_revision_id(fields, "base_revision_id")
    feedback = read_feedback(fields)
    changes = read_caption_edit(fields)
    panel.content.review.edit(
        panel.brand_id,
        post_id,
        base_revision_id,
        changes,
        feedback.choice,
        feedback.note,
        feedback.expires_at,
        user.login,
    )
    return see_other(url)


@router.post("/post/{post_id}/alteracoes", dependencies=[MUTATION_GUARD])
def request_post_changes(
    post_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Pede uma geracao nova com o comentario do responsavel (que e dado, nunca instrucao)."""
    url = _batch_redirect(panel, post_id)
    panel.content.review.request_changes(
        panel.brand_id, post_id, read_revision_id(fields), fields.get("comentario", ""), user.login
    )
    return see_other(url)


@router.post("/post/{post_id}/rejeitar", dependencies=[MUTATION_GUARD])
def reject_post(
    post_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Rejeita a revisao; exige o tipo de feedback (sem valor por omissao)."""
    url = _batch_redirect(panel, post_id)
    revision_id = read_revision_id(fields)
    feedback = read_feedback(fields)
    panel.content.review.reject(
        panel.brand_id, post_id, revision_id, feedback.choice, feedback.note, user.login
    )
    return see_other(url)
