"""Plano semanal: propor, rever os slots e decidir (aprovar so por POST explicito).

Aprovar cria as publicacoes e os trabalhos numa so transacao (`PlanningService.approve_plan`) e
leva ao lote da semana do plano, onde as publicacoes vao aparecendo a medida que sao geradas.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from marketing_agent.artifacts.store import ArtifactState, ArtifactType
from marketing_agent.panel.context import (
    MUTATION_GUARD,
    CurrentUser,
    Fields,
    Panel,
    RecordId,
    proposed_id,
    see_other,
)
from marketing_agent.panel.inputs import read_confirm_cancel, read_reason, read_week
from marketing_agent.planning.preview import ApprovalPreview

router = APIRouter()


@router.post("/plano", dependencies=[MUTATION_GUARD])
def propose_plan(panel: Panel, fields: Fields) -> RedirectResponse:
    """Pede o plano da semana (campo `semana` opcional; por omissao, a seguinte) e abre-o."""
    run = panel.content.planning.propose_plan(panel.brand_id, read_week(fields))
    return see_other(f"/plano/{proposed_id(run)}")


def _approval_preview(
    panel: Panel, artifact_id: str, state: ArtifactState
) -> ApprovalPreview | None:
    """Previsao do que aprovar faria, so para um plano `submitted` cuja semana ja tem posts."""
    if state is not ArtifactState.SUBMITTED:
        return None
    preview = panel.content.planning.preview_approval(panel.brand_id, artifact_id)
    return preview if preview.has_existing_posts else None


@router.get("/plano/{artifact_id}")
def show_plan(artifact_id: RecordId, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Slots, suposicoes e, enquanto estiver submetido, os botoes de decisao."""
    strategy = panel.strategy.active_strategy(panel.brand_id)
    experiments = (
        {index: test.hipotese for index, test in enumerate(strategy.estrategia.experiencias)}
        if strategy
        else {}
    )
    view = panel.queries.plan_view(panel.brand_id, artifact_id)
    context = {
        "view": view,
        "experiments": experiments,
        "strategy_id": strategy.artifact_id if strategy else None,
        "preview": _approval_preview(panel, artifact_id, view.record.state),
    }
    return panel.renderer.page("plan.html", user, context)


@router.post("/plano/{artifact_id}/aprovar", dependencies=[MUTATION_GUARD])
def approve_plan(
    artifact_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Aprova o plano e cria as publicacoes e os trabalhos; um plano ja decidido da 409.

    Se cancelasse publicacoes aprovadas ou em revisao, exige o campo `confirm_cancel` (409 sem ele).
    """
    record = panel.queries.require_artifact(panel.brand_id, artifact_id, ArtifactType.PLANO)
    panel.content.planning.approve_plan(
        artifact_id, user.login, confirm_cancel=read_confirm_cancel(fields)
    )
    return see_other(f"/lote/{record.payload['semana']}")


@router.post("/plano/{artifact_id}/rejeitar", dependencies=[MUTATION_GUARD])
def reject_plan(
    artifact_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Rejeita o plano submetido; nao cria publicacoes e o plano ativo mantem-se."""
    panel.queries.require_artifact(panel.brand_id, artifact_id, ArtifactType.PLANO)
    panel.content.planning.reject_plan(artifact_id, user.login, read_reason(fields))
    return see_other(f"/plano/{artifact_id}")
