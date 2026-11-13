"""Leituras autenticadas da oficina; nenhuma rota executa trabalho ou altera dados."""

from typing import Final

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from marketing_agent.oficina.config import ESTILO_REFERENCIA
from marketing_agent.oficina.estado import etag_for, etag_matches, team_state_json
from marketing_agent.oficina.papeis import PAPEIS, PAPEL_TEXTO, Papel
from marketing_agent.panel.context import CurrentUser, Panel
from marketing_agent.panel.rendering import Context

router = APIRouter()
RESUMO_URL: Final = "/#entrevistas"


def _papel(value: str) -> Papel:
    if value not in PAPEIS:
        raise HTTPException(status_code=404)
    return value


def _agent_context(panel: Panel, papel: Papel) -> Context:
    return {
        "trabalho": panel.team.agent_panel(panel.brand_id, papel),
        "papeis_texto": PAPEL_TEXTO,
        "resumo_url": RESUMO_URL,
    }


@router.get("/oficina")
def oficina(user: CurrentUser, panel: Panel, papel: str = "diretor") -> HTMLResponse:
    """Vista completa, utilizavel sem JavaScript, com um papel escolhido por ligacao."""
    selected = _papel(papel)
    context: Context = {
        **_agent_context(panel, selected),
        "equipa": panel.team.team_state(panel.brand_id),
        "config": panel.oficina,
        "estilos": ESTILO_REFERENCIA,
        "selecionado": selected,
    }
    return panel.renderer.page("oficina.html", user, context)


@router.get("/oficina/agente/{papel}")
def agente(papel: str, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Entregas e falhas escapadas do papel; todas as decisoes permanecem no lote."""
    return panel.renderer.partial("_oficina_agente.html", _agent_context(panel, _papel(papel)))


@router.get("/oficina/estado")
def estado(request: Request, user: CurrentUser, panel: Panel) -> Response:
    """Devolve o snapshot minimo; o cliente conserva o ETag e revalida explicitamente."""
    body = team_state_json(panel.team.team_state(panel.brand_id))
    etag = etag_for(body)
    headers = {"ETag": etag, "Cache-Control": "no-store"}
    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)
