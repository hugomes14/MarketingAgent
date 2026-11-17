"""Pagina inicial: estado do mes, entrevistas, versoes ativas, kit de marca e decisoes pendentes."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from marketing_agent.panel.context import CurrentUser, Panel

router = APIRouter()


@router.get("/")
def home(user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Mostra gasto, reservas e projecao do mes, o aviso de referencia e o que aguarda decisao."""
    context = {
        "budget": panel.budget.status(panel.brand_id),
        "snapshot": panel.queries.home_snapshot(panel.brand_id),
        "strategy": panel.strategy.active_strategy(panel.brand_id),
        # Uma so linha (a versao mais alta): consulta limitada e da marca do painel.
        "kit": panel.media.kits.active(panel.brand_id),
    }
    return panel.renderer.page("home.html", user, context)
