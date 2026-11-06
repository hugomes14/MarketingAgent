"""Diagnostico e estrategia: propor, rever e decidir (aprovar so por POST explicito)."""

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from marketing_agent.artifacts.store import ArtifactRecord, ArtifactType
from marketing_agent.harness.engine import RunResult, RunStatus
from marketing_agent.panel.context import (
    MUTATION_GUARD,
    CurrentUser,
    Fields,
    Panel,
    RecordId,
    see_other,
)
from marketing_agent.panel.errors import UpstreamFailure
from marketing_agent.panel.inputs import read_decisions, read_reason

logger = logging.getLogger(__name__)
router = APIRouter()


def _proposed_id(run: RunResult) -> str:
    """Id do artefacto submetido por uma proposta bem-sucedida; senao `UpstreamFailure`."""
    if run.status is RunStatus.SUCCEEDED and isinstance(run.output, ArtifactRecord):
        return run.output.id
    logger.warning("painel: proposta sem sucesso (%s) execucao %s", run.status.value, run.run_id)
    raise UpstreamFailure(f"execucao {run.run_id} terminou em {run.status.value}")


@router.post("/diagnostico", dependencies=[MUTATION_GUARD])
def propose_diagnosis(panel: Panel) -> RedirectResponse:
    """Pede um diagnostico (exige perfil ativo) e abre a pagina de revisao."""
    run = panel.strategy.propose_diagnosis(panel.brand_id)
    return see_other(f"/diagnostico/{_proposed_id(run)}")


@router.get("/diagnostico/{artifact_id}")
def show_diagnosis(artifact_id: RecordId, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Revisao do diagnostico, com os botoes de decisao enquanto estiver submetido."""
    view = panel.queries.diagnosis_view(panel.brand_id, artifact_id)
    return panel.renderer.page("diagnosis.html", user, {"view": view})


@router.post("/diagnostico/{artifact_id}/aprovar", dependencies=[MUTATION_GUARD])
def approve_diagnosis(artifact_id: RecordId, user: CurrentUser, panel: Panel) -> RedirectResponse:
    """Aprova e ativa o diagnostico."""
    panel.queries.require_artifact(panel.brand_id, artifact_id, ArtifactType.DIAGNOSTICO)
    panel.strategy.approve_diagnosis(artifact_id, user.login)
    return see_other(f"/diagnostico/{artifact_id}")


@router.post("/diagnostico/{artifact_id}/rejeitar", dependencies=[MUTATION_GUARD])
def reject_diagnosis(
    artifact_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Rejeita o diagnostico submetido; o ativo mantem-se."""
    panel.queries.require_artifact(panel.brand_id, artifact_id, ArtifactType.DIAGNOSTICO)
    panel.strategy.reject_diagnosis(artifact_id, user.login, read_reason(fields))
    return see_other(f"/diagnostico/{artifact_id}")


@router.post("/estrategia", dependencies=[MUTATION_GUARD])
def propose_strategy(panel: Panel) -> RedirectResponse:
    """Pede uma estrategia (exige perfil e diagnostico ativos) e abre a revisao."""
    run = panel.strategy.propose_strategy(panel.brand_id)
    return see_other(f"/estrategia/{_proposed_id(run)}")


@router.get("/estrategia/{artifact_id}")
def show_strategy(artifact_id: RecordId, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Revisao da estrategia: cada proposta de oferta pede uma escolha explicita."""
    view = panel.queries.strategy_view(panel.brand_id, artifact_id)
    return panel.renderer.page("strategy.html", user, {"view": view})


@router.post("/estrategia/{artifact_id}/aprovar", dependencies=[MUTATION_GUARD])
def approve_strategy(
    artifact_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Aprova a estrategia com uma decisao por proposta (`decisao_{i}`); sem todas, 422."""
    panel.queries.require_artifact(panel.brand_id, artifact_id, ArtifactType.ESTRATEGIA)
    panel.strategy.approve_strategy(artifact_id, user.login, read_decisions(fields))
    return see_other(f"/estrategia/{artifact_id}")


@router.post("/estrategia/{artifact_id}/rejeitar", dependencies=[MUTATION_GUARD])
def reject_strategy(
    artifact_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Rejeita a estrategia submetida; a ativa mantem-se."""
    panel.queries.require_artifact(panel.brand_id, artifact_id, ArtifactType.ESTRATEGIA)
    panel.strategy.reject_strategy(artifact_id, user.login, read_reason(fields))
    return see_other(f"/estrategia/{artifact_id}")
