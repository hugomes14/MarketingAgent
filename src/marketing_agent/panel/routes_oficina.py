"""Leituras autenticadas da oficina; nenhuma rota executa trabalho ou altera dados."""

from fastapi import APIRouter, Request, Response

from marketing_agent.oficina.estado import etag_for, etag_matches, team_state_json
from marketing_agent.panel.context import CurrentUser, Panel

router = APIRouter()


@router.get("/oficina/estado")
def estado(request: Request, user: CurrentUser, panel: Panel) -> Response:
    """Devolve o snapshot minimo; o cliente conserva o ETag e revalida explicitamente."""
    body = team_state_json(panel.team.team_state(panel.brand_id))
    etag = etag_for(body)
    headers = {"ETag": etag, "Cache-Control": "no-store"}
    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)
