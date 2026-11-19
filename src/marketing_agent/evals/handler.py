"""Job `eval_package`: produz o pacote de um `eval_item` pelo seu percurso e grava as medidas.

Como o `ContentPipeline`, nenhum lock e mantido enquanto o modelo trabalha: so a gravacao final
e transacional (item bloqueado, imagem final guardada, medidas, conclusao do job e, se era o
ultimo, a corrida passa a `ready`). Diferencas para o pipeline do lote:

- nunca le nem escreve `post`, `post_revision`, `approval`, `feedback_example` ou `artifact_head`;
- o contexto vem da copia fixada no inicio da corrida (`evals.context`);
- as execucoes ligam-se ao item pelo trigger `eval.<passo>:<item>`: o custo, as saidas invalidas
  e a latencia do item derivam delas, somando TODAS as tentativas (um worker morto a meio volta
  a correr o item e o custo da tentativa perdida continua a contar).

Mensagens de falha: so frases fixas ou `describe_error`/`describe_stop` chegam a `last_error`.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import Clock
from marketing_agent.content.image_failure import image_error
from marketing_agent.content.package_assets import store_package_image
from marketing_agent.content.package_costs import job_costs_in, remaining_images, request_run_ids
from marketing_agent.content.package_guard import PackageCostGuard
from marketing_agent.content.package_media import DesignInputs, DesignMedia, ImageUnavailable
from marketing_agent.content.revision_store import RevisionImage
from marketing_agent.content.rounds import (
    GeneratedPackage,
    ImageFailure,
    PackageOutcome,
    PackageRounds,
    describe_stop,
)
from marketing_agent.content.specs import ContentSpecs
from marketing_agent.db.models import (
    AgentRun,
    AgentStep,
    EvalItem,
    EvalRun,
    JsonObject,
    JsonValue,
)
from marketing_agent.evals.context import EvalContext, FixedBrief, fixed_design_inputs
from marketing_agent.evals.runs import TERMINAL_ITEM_STATUSES, close_run_if_done
from marketing_agent.evals.simple_path import SimplePath
from marketing_agent.harness.engine import AgentEngine
from marketing_agent.harness.image_calls import ImageAllowance
from marketing_agent.harness.spec import AgentSpec
from marketing_agent.images.generation import ImageQuality
from marketing_agent.jobs.errors import describe_error
from marketing_agent.jobs.queue import ClaimedJob, JobFailure, JobQueue
from marketing_agent.media.brand_kit import BrandKitService
from marketing_agent.media.library import MediaLibrary

logger = logging.getLogger(__name__)

EvalOutcome = Literal["succeeded", "failed", "stale"]
SPECIALIZED: Final = "especializado"


class EvalJobPayload(BaseModel):
    """Payload de um job `eval_package`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eval_run_id: str = Field(min_length=1)
    eval_item_id: str = Field(min_length=1)


class _LeaseLost(Exception):
    """Interna: o worker perdeu a lease; a transacao de gravacao e desfeita."""


@dataclass(frozen=True)
class _Work:
    """O que um item precisa para correr, lido na transacao de inicio."""

    item_id: str
    brand_id: str
    path: str
    brief: FixedBrief
    context: EvalContext


@dataclass(frozen=True)
class _Measures:
    cost_usd: Decimal
    latency_ms: int
    invalid_outputs: int


@dataclass(frozen=True)
class EvalHandlerDeps:
    """Colaboradores do handler (agrupados para o construtor ficar curto)."""

    engine: AgentEngine
    queue: JobQueue
    library: MediaLibrary
    kits: BrandKitService
    specs: ContentSpecs
    simple_spec: AgentSpec


class EvalPackageHandler:
    """Processa e fecha jobs `eval_package`; erros inesperados sobem ao worker."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        deps: EvalHandlerDeps,
        clock: Clock,
        *,
        image_quality: ImageQuality,
        package_max_cost_usd: Decimal,
    ) -> None:
        """`image_quality` e a dos fundos; `package_max_cost_usd` o teto por pacote."""
        self._session_factory = session_factory
        self._deps = deps
        self._clock = clock
        self._ceiling = package_max_cost_usd
        media = DesignMedia(deps.library, deps.kits)
        self._rounds = PackageRounds(deps.engine, deps.specs, media, image_quality)
        self._simple = SimplePath(deps.engine, deps.simple_spec, image_quality)

    def handle(self, job: ClaimedJob) -> EvalOutcome:
        """Corre o item do job e fecha-o; `stale` se nada havia a fazer ou a lease se perdeu."""
        try:
            payload = EvalJobPayload.model_validate(job.payload)
        except ValidationError as exc:
            self._deps.queue.fail(job, describe_error(exc), retry=False)
            return "failed"
        work = self._begin(job, payload)
        if work is None:
            return "stale"
        try:
            media = fixed_design_inputs(
                work.context, work.brand_id, self._deps.library, self._deps.kits
            )
        except ImageUnavailable as unavailable:
            return self._finish_failed(job, work, image_error(unavailable.reason))
        outcome = self._produce(work, media)
        if isinstance(outcome, GeneratedPackage):
            return self._finish_ok(job, work, outcome)
        if isinstance(outcome, ImageFailure):
            return self._finish_failed(job, work, image_error(outcome.reason))
        return self._finish_failed(job, work, describe_stop(outcome))

    def _begin(self, job: ClaimedJob, payload: EvalJobPayload) -> _Work | None:
        """Bloqueia o item; conclui o job sem trabalho se o item ja e terminal."""
        with self._session_factory.begin() as session:
            item = _lock_item(session, payload.eval_item_id)
            if not self._deps.queue.holds_lease_in(session, job):
                return None
            if item is None or item.run_id != payload.eval_run_id:
                self._deps.queue.complete_in(session, job)
                return None
            if item.status in TERMINAL_ITEM_STATUSES:
                self._deps.queue.complete_in(session, job)
                return None
            item.status = "running"
            run = session.get_one(EvalRun, item.run_id)
            brief = next(
                FixedBrief.from_json(raw) for raw in run.briefs if raw["key"] == item.brief_key
            )
            context = EvalContext.from_json(run.context_snapshot)
            return _Work(item.id, item.brand_id, item.path, brief, context)

    def _produce(self, work: _Work, media: DesignInputs) -> PackageOutcome:
        guard = PackageCostGuard(
            self._session_factory, work.brand_id, work.item_id, self._ceiling, "eval"
        )
        allowance = ImageAllowance(
            remaining_images(self._session_factory, work.brand_id, work.item_id, "eval")
        )
        copy = work.context.copy_payload(work.brief)
        if work.path == SPECIALIZED:
            return self._rounds.run(
                work.item_id,
                work.brand_id,
                copy,
                None,
                allowance,
                scope="eval",
                guard=guard,
                inputs=media,
            )
        return self._simple.run(
            work.item_id, work.brand_id, copy, media, allowance, scope="eval", guard=guard
        )

    def _finish_ok(self, job: ClaimedJob, work: _Work, package: GeneratedPackage) -> EvalOutcome:
        """Guarda a imagem final e as medidas e conclui o job, numa transacao."""
        try:
            with self._session_factory.begin() as session:
                item = _lock_item(session, work.item_id)
                if item is None or not self._deps.queue.holds_lease_in(session, job):
                    raise _LeaseLost
                image = store_package_image(session, self._deps.library, work.brand_id, package)
                item.revision_snapshot = package_snapshot(package, image)
                item.final_asset_id = image.asset_id
                item.review_rounds = package.text.rounds
                self._close_item(session, item, "succeeded", None)
                if not self._deps.queue.complete_in(session, job):
                    raise _LeaseLost
                close_run_if_done(session, item.run_id)
                return "succeeded"
        except _LeaseLost:
            logger.warning("job %s: lease perdida antes de gravar; nada foi gravado", job.id)
            return "stale"

    def _finish_failed(self, job: ClaimedJob, work: _Work, message: str) -> EvalOutcome:
        """Item `failed` com o motivo fixo e o job falhado sem repeticao, numa transacao."""
        with self._session_factory.begin() as session:
            item = _lock_item(session, work.item_id)
            if self._deps.queue.fail_in(session, job, message, retry=False) is None:
                return "stale"
            if item is not None and item.status not in TERMINAL_ITEM_STATUSES:
                self._close_item(session, item, "failed", message)
                close_run_if_done(session, item.run_id)
            return "failed"

    def fail_job(self, job: ClaimedJob, message: str, retry: bool) -> JobFailure | None:
        """Falha o job; se ficou terminal, o item passa a `failed` com `message` (fixa)."""
        item_id = job.payload.get("eval_item_id")
        with self._session_factory.begin() as session:
            item = _lock_item(session, item_id) if isinstance(item_id, str) else None
            status = self._deps.queue.fail_in(session, job, message, retry)
            is_terminal = status in ("failed", "dead")
            if is_terminal and item is not None and item.status not in TERMINAL_ITEM_STATUSES:
                self._close_item(session, item, "failed", message)
                close_run_if_done(session, item.run_id)
            return status

    def _close_item(self, session: Session, item: EvalItem, status: str, error: str | None) -> None:
        measures = item_measures(session, item.brand_id, item.id)
        item.status = status
        item.last_error = error
        item.cost_usd = measures.cost_usd
        item.latency_ms = measures.latency_ms
        item.invalid_outputs = measures.invalid_outputs
        item.finished_at = self._clock.now()


def _lock_item(session: Session, item_id: str) -> EvalItem | None:
    return session.scalar(select(EvalItem).where(EvalItem.id == item_id).with_for_update())


def item_measures(session: Session, brand_id: str, item_id: str) -> _Measures:
    """Custo, latencia (soma das duracoes das execucoes) e saidas invalidas de um item.

    Todas as tentativas contam: as execucoes ligam-se ao item pelo trigger, nao ao job.
    """
    runs = request_run_ids(brand_id, item_id, "eval")
    cost = job_costs_in(session, brand_id, item_id, "eval").package_usd
    invalid = session.scalar(
        select(func.count())
        .select_from(AgentStep)
        .where(AgentStep.run_id.in_(runs), AgentStep.error_class == "invalid_output")
    )
    durations = session.execute(
        select(AgentRun.started_at, AgentRun.ended_at).where(AgentRun.id.in_(runs))
    ).all()
    latency = sum(
        (ended - started).total_seconds() for started, ended in durations if ended is not None
    )
    return _Measures(Decimal(cost), round(latency * 1000), int(invalid or 0))


def package_snapshot(package: GeneratedPackage, image: RevisionImage) -> JsonObject:
    """Texto, escolha visual, proveniencia e pareceres do pacote (sem bytes de imagem)."""
    text = package.text
    return {
        "texto": text.draft.model_dump(mode="json"),
        "visual": package.design.draft.model_dump(mode="json"),
        "layout": image.layout,
        "components": cast(JsonValue, image.components),
        "is_ai_generated": image.is_ai_generated,
        "alt_text": image.alt_text,
        "review": {
            "verdict": text.verdict,
            "violations": list(text.violations),
            "notes": text.notes,
            "image_checked": package.image_checked,
            "all_violations": list(package.all_violations),
        },
    }
