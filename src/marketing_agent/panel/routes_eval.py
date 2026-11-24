"""Avaliacao cega no painel (2C): corridas, estimativa, progresso, pares, respostas e revelacao.

Cegueira (spec 2C, secao 5): nenhuma pagina, atributo, URL, cabecalho ou nome de imagem desta
area revela o percurso de um lado antes da revelacao. O progresso de uma corrida so mostra
contagens agregadas; as imagens servem-se por lado (`a`/`b`) e nunca pelo id do ativo; as
classes e os atributos dos dois lados vem do mesmo parcial e dependem so de `a`/`b`.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Path, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from marketing_agent.db.models import (
    DECIDABLE_ROLES,
    EDIT_MINUTES,
    EVAL_ERROR_KINDS,
    ROLE_DECISIONS,
)
from marketing_agent.evals.judging import JudgementInput
from marketing_agent.evals.runs import EvalRunNotFound
from marketing_agent.panel.context import (
    MUTATION_GUARD,
    UUID_PATTERN,
    CurrentUser,
    Fields,
    Panel,
    RecordId,
    see_other,
)
from marketing_agent.panel.errors import InvalidForm
from marketing_agent.panel.forms import FormFields
from marketing_agent.panel.rendering import Context

router = APIRouter()
EVAL_URL: Final = "/avaliacao"
DEFAULT_SET: Final = "briefs_v1"
PairIndex = Annotated[int, Path(ge=1, le=100)]
SideName = Annotated[Literal["a", "b"], Path()]
# Imagem de um lado: tipo fixo, sem nome de ficheiro, sem ETag e sem cache (nada a identifica).
_IMAGE_HEADERS: Final = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
_UUID_RE: Final = re.compile(UUID_PATTERN)


@router.get("/avaliacao")
def list_runs(user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Corridas da marca e a ligacao para comecar uma nova."""
    context: Context = {
        "runs": panel.evals.runs.list_runs(panel.brand_id),
        "default_set": DEFAULT_SET,
    }
    return panel.renderer.page("eval_runs.html", user, context)


@router.get("/avaliacao/nova")
def new_run(
    user: CurrentUser, panel: Panel, conjunto: Annotated[str, Query(max_length=40)] = DEFAULT_SET
) -> HTMLResponse:
    """Estimativa (majorante e gasto do mes) e o formulario de confirmacao."""
    estimate = panel.evals.runs.estimate(panel.brand_id, conjunto)
    return panel.renderer.page("eval_new.html", user, {"estimate": estimate})


@router.post("/avaliacao", dependencies=[MUTATION_GUARD])
def start_run(user: CurrentUser, panel: Panel, fields: Fields) -> RedirectResponse:
    """Comeca a corrida com o majorante confirmado no formulario; se mudou, 409."""
    set_name = fields.get("conjunto", "")
    try:
        confirmed = Decimal(fields.get("majorante", ""))
    except InvalidOperation:
        raise InvalidForm("Confirme a estimativa antes de começar.") from None
    if not confirmed.is_finite():  # NaN, sNaN e Infinity constroem-se mas nao sao valores
        raise InvalidForm("Confirme a estimativa antes de começar.")
    run_id = panel.evals.runs.start_run(panel.brand_id, set_name, confirmed, user.login)
    return see_other(f"{EVAL_URL}/{run_id}")


@router.get("/avaliacao/{run_id}")
def show_run(run_id: RecordId, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Estado da corrida; com a corrida pronta, o progresso da avaliacao e a revelacao."""
    return panel.renderer.page("eval_run.html", user, _run_context(panel, run_id))


@router.get("/avaliacao/{run_id}/estado")
def run_status(run_id: RecordId, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Fragmento do progresso (HTMX); so pede nova atualizacao enquanto a corrida corre."""
    context = _run_context(panel, run_id)
    run = panel.evals.runs.run_view(panel.brand_id, run_id)
    # A secao "Avaliar" so existe na pagina inteira: ao terminar, o HTMX recarrega-a.
    headers = {"HX-Refresh": "true"} if run is not None and run.status != "running" else None
    return panel.renderer.partial("_eval_progress.html", context, headers=headers)


def _run_context(panel: Panel, run_id: str) -> Context:
    run = panel.evals.runs.run_view(panel.brand_id, run_id)
    if run is None:
        raise EvalRunNotFound
    progress = None
    if run.status != "running":
        progress = panel.evals.judging.progress(panel.brand_id, run_id)
    return {"run": run, "progress": progress}


@router.get("/avaliacao/{run_id}/par/{index}")
def show_pair(run_id: RecordId, index: PairIndex, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Um par cego: brief, lado A e lado B, e o formulario de resposta (sem pre-selecao)."""
    pair = panel.evals.judging.pair(panel.brand_id, run_id, index)
    context: Context = {
        "run_id": run_id,
        "pair": pair,
        "timer": panel.pair_timer.issue(run_id, index, panel.content.clock.now()),
        "error_kinds": EVAL_ERROR_KINDS,
        "edit_values": EDIT_MINUTES,
    }
    return panel.renderer.page("eval_pair.html", user, context)


@router.get("/avaliacao/{run_id}/par/{index}/imagem/{side}")
def pair_image(
    run_id: RecordId, index: PairIndex, side: SideName, user: CurrentUser, panel: Panel
) -> Response:
    """Imagem final de um lado; sem `Content-Disposition`, `ETag` nem nada que a identifique."""
    data = panel.evals.judging.pair_image(panel.brand_id, run_id, index, side)
    return Response(data, media_type="image/jpeg", headers=_IMAGE_HEADERS)


@router.post("/avaliacao/{run_id}/par/{index}", dependencies=[MUTATION_GUARD])
def record_judgement(
    run_id: RecordId, index: PairIndex, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Grava a resposta (ou a correcao) e segue para o par seguinte ou para a corrida."""
    answer = _read_answer(fields)
    try:
        seconds = panel.pair_timer.seconds(
            fields.get("inicio", ""), run_id, index, panel.content.clock.now()
        )
    except ValueError:
        raise InvalidForm("A página expirou ou foi alterada. Recarregue o par.") from None
    replaces = _read_replaces(fields)
    judging = panel.evals.judging
    judging.record(
        panel.brand_id,
        run_id,
        index,
        answer,
        seconds_spent=seconds,
        replaces_id=replaces,
        actor=user.login,
    )
    total = judging.progress(panel.brand_id, run_id).pairs
    target = f"{EVAL_URL}/{run_id}/par/{index + 1}" if index < total else f"{EVAL_URL}/{run_id}"
    return see_other(target)


@router.post("/avaliacao/{run_id}/revelar", dependencies=[MUTATION_GUARD])
def reveal_run(run_id: RecordId, user: CurrentUser, panel: Panel) -> RedirectResponse:
    """Revela a corrida (409 com pares por responder)."""
    panel.evals.judging.reveal(panel.brand_id, run_id, user.login)
    return see_other(f"{EVAL_URL}/{run_id}")


def _read_answer(fields: FormFields) -> JudgementInput:
    """Le os campos do formulario do par; qualquer campo em falta ou invalido da 422."""
    raw = {
        "better": fields.get("melhor"),
        "errors_a": [kind for kind in EVAL_ERROR_KINDS if fields.get(f"erro_a_{kind}")],
        "errors_b": [kind for kind in EVAL_ERROR_KINDS if fields.get(f"erro_b_{kind}")],
        "edit_a": fields.get("edicao_a"),
        "edit_b": fields.get("edicao_b"),
        "note": fields.get("nota") or None,
    }
    try:
        return JudgementInput.model_validate(raw)
    except ValidationError:
        raise InvalidForm(
            "Escolha o melhor lado e os minutos de edição de cada lado; a nota tem um limite."
        ) from None


def _read_replaces(fields: FormFields) -> str | None:
    value = fields.get("substitui", "").strip()
    if not value:
        return None
    if not _UUID_RE.fullmatch(value):
        raise InvalidForm("Pedido inválido.")
    return value


@router.get("/avaliacao/{run_id}/relatorio")
def show_report(run_id: RecordId, user: CurrentUser, panel: Panel) -> HTMLResponse:
    """Relatorio da corrida revelada, com as decisoes em vigor e o formulario de decisao."""
    report = panel.evals.report.build(panel.brand_id, run_id)
    context: Context = {
        "report": report,
        "roles": DECIDABLE_ROLES,
        "decisions": ROLE_DECISIONS,
        "edit_values": EDIT_MINUTES,
    }
    return panel.renderer.page("eval_report.html", user, context)


@router.post("/avaliacao/{run_id}/decisao", dependencies=[MUTATION_GUARD])
def record_decision(
    run_id: RecordId, user: CurrentUser, panel: Panel, fields: Fields
) -> RedirectResponse:
    """Regista a decisao de um papel (so acrescenta; a mais recente vale)."""
    panel.evals.report.record_role_decision(
        panel.brand_id,
        run_id,
        fields.get("papel", ""),
        fields.get("decisao", ""),
        fields.get("justificacao", ""),
        user.login,
    )
    return see_other(f"{EVAL_URL}/{run_id}/relatorio")
