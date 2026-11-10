"""Base de conhecimento: listar, acrescentar, desativar e promover um topico do perfil.

A promocao vive aqui (e nao em `routes_interview`) por ser uma acao sobre a base; o URL
mantem-se debaixo de `/perfil/{id}` porque o topico pertence a esse perfil. O autor de cada
alteracao e sempre o utilizador da sessao, nunca um campo do formulario.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from marketing_agent.db.models import KB_KINDS, SOURCE_KINDS
from marketing_agent.panel.context import (
    MUTATION_GUARD,
    CurrentUser,
    Fields,
    Panel,
    RecordId,
    see_other,
)
from marketing_agent.panel.errors import InvalidForm
from marketing_agent.panel.inputs import DEFAULT_SOURCE_KIND, read_kb_entry, read_profile_topic
from marketing_agent.panel.rendering import Context

router = APIRouter()
KB_URL = "/base"


@router.get("/base")
def show_kb(user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Itens ativos por tipo, com a janela de validade, e o formulario para acrescentar."""
    context: Context = {
        "groups": panel.queries.kb_view(panel.brand_id),
        "kinds": KB_KINDS,
        "sources": SOURCE_KINDS,
        "default_source": DEFAULT_SOURCE_KIND,
    }
    return panel.renderer.page("kb.html", user, context)


@router.post("/base", dependencies=[MUTATION_GUARD])
def add_kb_item(user: CurrentUser, panel: Panel, fields: Fields) -> RedirectResponse:
    """Acrescenta um item manual; campos invalidos ou item repetido dao 422."""
    panel.content.kb.add(panel.brand_id, read_kb_entry(fields), user.login)
    return see_other(KB_URL)


@router.post("/base/{item_id}/desativar", dependencies=[MUTATION_GUARD])
def deactivate_kb_item(item_id: RecordId, user: CurrentUser, panel: Panel) -> RedirectResponse:
    """Desativa o item (nunca se apaga); idempotente. Item inexistente ou alheio da 422."""
    panel.content.kb.deactivate(panel.brand_id, item_id, user.login)
    return see_other(KB_URL)


@router.post("/perfil/{artifact_id}/promover", dependencies=[MUTATION_GUARD])
def promote_profile_topic(
    artifact_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Copia um topico do perfil ativo para a base; um topico que o perfil nao tem da 422."""
    topic = read_profile_topic(fields)
    profile = panel.queries.require_active_profile(panel.brand_id, artifact_id)
    if topic not in profile.topicos:
        raise InvalidForm("Escolha um tópico do perfil.")
    panel.content.kb.promote_profile_topic(panel.brand_id, topic, user.login)
    return see_other(KB_URL)
