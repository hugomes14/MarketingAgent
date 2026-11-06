"""Entrevista e perfil: criar sessao, responder, corrigir topicos, pedir e decidir o perfil."""

import uuid
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from marketing_agent.agents.diretor.gap_map import GAP_TOPICS
from marketing_agent.artifacts.store import ArtifactType
from marketing_agent.interview.service import InvalidAnswer, TurnResult, TurnStatus
from marketing_agent.panel.context import (
    MUTATION_GUARD,
    CurrentUser,
    Fields,
    Panel,
    RecordId,
    see_other,
)
from marketing_agent.panel.inputs import read_idempotency_key, read_reason, read_topic

router = APIRouter()

# Estados que fecham a submissao: a chave nao pode ser reutilizada e o formulario recebe outra.
_FRESH_KEY_STATUSES: Final = frozenset(
    {TurnStatus.COMPLETED, TurnStatus.BLOCKED, TurnStatus.NEEDS_HUMAN}
)


@router.post("/entrevista", dependencies=[MUTATION_GUARD])
def start_interview(panel: Panel) -> RedirectResponse:
    """Cria a sessao (so depois de obtida a primeira pergunta) e abre-a."""
    started = panel.interview.start(panel.brand_id)
    return see_other(f"/entrevista/{started.session_id}")


@router.get("/entrevista/{session_id}")
def show_interview(session_id: RecordId, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Turnos, mapa de lacunas e formularios; cada render do formulario traz uma chave nova."""
    context = {
        "view": panel.queries.session_view(panel.brand_id, session_id),
        "topics": GAP_TOPICS,
        "idempotency_key": str(uuid.uuid4()),
        "max_chars": panel.max_message_chars,
        "oob": False,
        "text": "",
    }
    return panel.renderer.page("interview.html", user, context)


@router.post("/entrevista/{session_id}/resposta", dependencies=[MUTATION_GUARD])
def submit_answer(
    session_id: RecordId, request: Request, user: CurrentUser, panel: Panel, fields: Fields
) -> HTMLResponse:
    """Envia a resposta com a chave do cabecalho e devolve o fragmento dos novos turnos."""
    key = read_idempotency_key(request)
    text = fields.get("text", "")
    if not text.strip():
        raise InvalidAnswer("texto vazio")
    panel.queries.require_session(panel.brand_id, session_id)
    result = panel.interview.submit_answer(session_id, text, key)
    context = {
        "session_id": session_id,
        "result": result,
        "text": text,
        "idempotency_key": _next_key(result),
        "csrf_token": panel.renderer.token_for(user),
        "max_chars": panel.max_message_chars,
        "oob": True,
    }
    return panel.renderer.partial("_answer_result.html", context)


def _next_key(result: TurnResult) -> str | None:
    """Chave nova se a submissao fechou; `None` mantem o formulario (e a chave) como estao.

    `in_progress` nao fecha nada: a mesma chave tem de voltar a ser enviada.
    """
    return str(uuid.uuid4()) if result.status in _FRESH_KEY_STATUSES else None


@router.post("/entrevista/{session_id}/topico", dependencies=[MUTATION_GUARD])
def correct_topic(
    session_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> HTMLResponse:
    """Correcao direta de um topico pelo responsavel; devolve o mapa de lacunas atualizado."""
    topic = read_topic(fields)
    panel.queries.require_session(panel.brand_id, session_id)
    gap_map = panel.interview.correct_topic(session_id, topic, fields.get("text", ""), user.login)
    return panel.renderer.partial(
        "_topic_result.html", {"gap_map": gap_map, "oob": False, "topic": topic}
    )


@router.post("/entrevista/{session_id}/perfil", dependencies=[MUTATION_GUARD])
def request_profile(session_id: RecordId, panel: Panel) -> RedirectResponse:
    """Pede o perfil e abre a pagina de revisao; nada fica ativo sem confirmacao."""
    panel.queries.require_session(panel.brand_id, session_id)
    return see_other(f"/perfil/{panel.interview.request_profile(session_id)}")


@router.get("/perfil/{artifact_id}")
def show_profile(artifact_id: RecordId, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Revisao do perfil, com os botoes de decisao enquanto estiver submetido."""
    view = panel.queries.profile_view(panel.brand_id, artifact_id)
    return panel.renderer.page("profile.html", user, {"view": view})


@router.post("/perfil/{artifact_id}/confirmar", dependencies=[MUTATION_GUARD])
def confirm_profile(artifact_id: RecordId, user: CurrentUser, panel: Panel) -> RedirectResponse:
    """Aprova e ativa o perfil: so existe como POST explicito do responsavel."""
    panel.queries.require_artifact(panel.brand_id, artifact_id, ArtifactType.PERFIL_MARCA)
    panel.interview.confirm_profile(artifact_id, user.login)
    return see_other(f"/perfil/{artifact_id}")


@router.post("/perfil/{artifact_id}/rejeitar", dependencies=[MUTATION_GUARD])
def reject_profile(
    artifact_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Rejeita o perfil submetido; o perfil ativo, se houver, mantem-se."""
    panel.queries.require_artifact(panel.brand_id, artifact_id, ArtifactType.PERFIL_MARCA)
    panel.interview.reject_profile(artifact_id, user.login, read_reason(fields))
    return see_other(f"/perfil/{artifact_id}")
