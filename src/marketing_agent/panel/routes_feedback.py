"""Feedback guardado: lista dos exemplos e desativacao (nunca se apaga)."""

from typing import Final

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from marketing_agent.panel.context import MUTATION_GUARD, CurrentUser, Panel, RecordId, see_other

router = APIRouter()
FEEDBACK_URL: Final = "/feedback"
MAX_LISTED_FEEDBACK: Final = 100


@router.get("/feedback")
def show_feedback(user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Os exemplos mais recentes (ativos ou nao), com o botao de desativar nos ativos."""
    context = {
        "examples": panel.content.feedback.list_examples(panel.brand_id, MAX_LISTED_FEEDBACK),
        "now": panel.content.clock.now(),
        "max_listed": MAX_LISTED_FEEDBACK,
    }
    return panel.renderer.page("feedback.html", user, context)


@router.post("/feedback/{example_id}/desativar", dependencies=[MUTATION_GUARD])
def deactivate_feedback(example_id: RecordId, user: CurrentUser, panel: Panel) -> RedirectResponse:
    """Desativa o exemplo (idempotente); um id inexistente ou de outra marca da 422."""
    panel.content.feedback.deactivate(panel.brand_id, example_id, user.login)
    return see_other(FEEDBACK_URL)
