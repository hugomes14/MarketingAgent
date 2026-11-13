"""Tratador dos jobs `eval_package`: produz o pacote de UM item e grava o resultado.

Um item e um brief num percurso (`simples` ou `especializado`). O tratador:

1. reconstroi o contexto SO a partir do `context_snapshot` da corrida (`EvalContext.build`),
   igual para os dois percursos, e mede a latencia da tentativa (relogio injetado);
2. corre o produtor do percurso com `PackageTriggers.single("eval_item:<id>")`: esse trigger e a
   unica ligacao entre o item e o custo, por isso repeticoes do job somam ao mesmo teto e quota;
3. grava, numa so transacao com `queue.complete_in` (e a corrida bloqueada), o fim do item:
   `revision_snapshot`, imagem final, custo da familia, saidas invalidas, voltas e latencia.
   Se a lease se perdeu a transacao desfaz-se. Se foi o ultimo item, a corrida passa a `ready`.

Idempotente: um job entregue de novo depois de o item acabar (ou com a corrida fechada) conclui
sem trabalho. `AgentRunFailed` e `RunConfigurationFailed` sobem ao worker (nova tentativa ate
`max_attempts`); um job que acaba `dead`/`failed` falha o item (`fail_job`, `on_terminal_failure`)
e, se a morte ocorreu fora do worker, `reconcile` fecha-o no arranque e nas iteracoes ociosas.

So escreve as tabelas de avaliacao, a contabilidade do motor e `media_asset` (imagem final e
fundo gerado, ruling C4): nunca `post`, `post_revision`, `approval`, `feedback_example` nem
`artifact_head`. `last_error` so leva frases fixas (razoes do codigo, `describe_error`).
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import Clock
from marketing_agent.content.package import (
    PackageProducer,
    PackageRequest,
    PackageResult,
    SpecializedPath,
)
from marketing_agent.content.package_assets import store_package_image
from marketing_agent.content.package_media import DesignMedia
from marketing_agent.content.revision_store import RevisionImage
from marketing_agent.content.rounds import PackageRounds
from marketing_agent.content.schemas import Revisao
from marketing_agent.content.specs import load_content_specs
from marketing_agent.db.models import EVAL_PATHS, EvalItem, EvalRun, JsonObject
from marketing_agent.evals.context import ContextSnapshot, EvalContext
from marketing_agent.evals.progress import (
    DEAD_JOB_STATUSES,
    TERMINAL_ITEM_STATUSES,
    ItemOutcome,
    close_item_in,
    close_stalled_item,
    failed_outcome,
    item_id_of_job,
    item_triggers,
    lock_open_item,
    reconcile_runs,
)
from marketing_agent.evals.simple_path import SimplePath
from marketing_agent.harness.engine import AgentEngine
from marketing_agent.images.generation import ImageQuality
from marketing_agent.jobs.errors import describe_error
from marketing_agent.jobs.queue import ClaimedJob, JobFailure, JobQueue
from marketing_agent.media.brand_kit import BrandKitService
from marketing_agent.media.library import MediaLibrary

logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA = 1
MS_PER_SECOND = 1000
PATH_SIMPLE = "simples"
PATH_SPECIALIZED = "especializado"


class EvalJobPayload(BaseModel):
    """Payload de um job `eval_package`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eval_run_id: str = Field(min_length=1)
    eval_item_id: str = Field(min_length=1)


class _LeaseLost(Exception):
    """Interna: o worker perdeu a lease, por isso a transacao de gravacao e desfeita."""


@dataclass(frozen=True)
class _Work:
    """O que um item precisa de saber para ser produzido (lido em `_begin`)."""

    run_id: str
    item_id: str
    brand_id: str
    path: str
    brief: JsonObject
    snapshot: ContextSnapshot
    ceiling_usd: Decimal


def build_package_paths(
    session_factory: sessionmaker[Session],
    engine: AgentEngine,
    library: MediaLibrary,
    kits: BrandKitService,
    image_quality: ImageQuality,
    ceiling_usd: Decimal,
) -> dict[str, PackageProducer]:
    """Os dois produtores sobre a MESMA media, qualidade de imagem e teto por pacote."""
    specs = load_content_specs()
    media = DesignMedia(library, kits)
    return {
        PATH_SIMPLE: SimplePath(
            session_factory, engine, specs.simple, media, image_quality, ceiling_usd
        ),
        PATH_SPECIALIZED: SpecializedPath(
            session_factory, PackageRounds(engine, specs, media, image_quality), ceiling_usd
        ),
    }


def revision_snapshot_of(result: PackageResult, image: RevisionImage) -> JsonObject:
    """Texto e proveniencia de um pacote `ok` (o que a avaliacao compara), so JSON.

    Leva o rascunho de texto, a escolha visual, a imagem (ativo, layout, componentes com os
    rotulos de proveniencia, IA) e, no percurso especializado, o veredicto e as violacoes de
    cada passagem do Revisor. As notas livres do Revisor nao se guardam.
    """
    text, visual = result.text, result.visual
    if text is None or visual is None:
        raise ValueError("so um resultado 'ok' tem texto e escolha visual")
    return {
        "schema": SNAPSHOT_SCHEMA,
        "texto": text.draft.model_dump(mode="json"),
        "visual": visual.model_dump(mode="json"),
        "imagem": {
            "asset_id": image.asset_id,
            "sha256": image.sha256,
            "layout": image.layout,
            "layout_version": image.layout_version,
            "componentes": [dict(component) for component in image.components],
            "gerada_por_ia": image.is_ai_generated,
            "alt_text": image.alt_text,
            "verificada": image.review_image_checked,
        },
        "pareceres": [_review_json(review) for review in result.reviews],
        "parecer_final": (
            None
            if text.verdict is None
            else {"veredicto": text.verdict, "violacoes": list(text.violations)}
        ),
        "voltas": result.review_rounds,
    }


class EvalPackageHandler:
    """Produz e grava os itens de avaliacao; fecha-os (e a corrida) quando o job morre."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        queue: JobQueue,
        clock: Clock,
        context: EvalContext,
        library: MediaLibrary,
        paths: Mapping[str, PackageProducer],
    ) -> None:
        """`paths` mapeia o percurso do item (`simples`/`especializado`) ao seu produtor."""
        self._session_factory = session_factory
        self._queue = queue
        self._clock = clock
        self._context = context
        self._library = library
        self._paths = paths

    def handle(self, job: ClaimedJob) -> None:
        """Produz o pacote do item do job e fecha o job; erros inesperados sobem ao worker."""
        try:
            payload = EvalJobPayload.model_validate(job.payload)
        except ValidationError as exc:
            self.fail_job(job, describe_error(exc), retry=False)  # nao e transitorio
            return
        work = self._begin(job, payload)
        if work is None:
            return
        context = self._context.build(work.brand_id, work.snapshot, work.brief)
        request = PackageRequest(
            work.brand_id, item_triggers(work.item_id), context, work.ceiling_usd
        )
        started = self._clock.now()
        result = self._paths[work.path].produce(request)
        latency_ms = int((self._clock.now() - started).total_seconds() * MS_PER_SECOND)
        self._finish(job, work, result, latency_ms)

    def fail_job(self, job: ClaimedJob, message: str, retry: bool) -> JobFailure | None:
        """Falha o job; se ficou `failed` ou `dead` o item falha tambem (e a corrida fecha).

        `None` se o worker perdeu a lease (nada se altera). `message` e sempre uma frase fixa
        (`describe_error`), nunca texto de modelo.
        """
        with self._session_factory.begin() as session:
            status = self._queue.fail_in(session, job, message, retry)
        if status in DEAD_JOB_STATUSES:
            self.on_terminal_failure(job, status)
        return status

    def on_terminal_failure(self, job: ClaimedJob, status: str) -> None:
        """Um job `failed`/`dead`/`cancelled` falha o item e fecha a corrida se foi o ultimo.

        Idempotente; se a escrita falhar, `reconcile` fecha o item no arranque seguinte.
        """
        if status not in DEAD_JOB_STATUSES:
            return
        # Pelo id do job e nao pelo payload: um payload invalido tambem tem de fechar o item.
        item_id = item_id_of_job(self._session_factory, job.id)
        if item_id is not None:
            close_stalled_item(self._session_factory, item_id)

    def reconcile(self, brand_id: str | None = None) -> int:
        """Fecha os itens cujo job morreu e as corridas terminadas; devolve os itens fechados."""
        return reconcile_runs(self._session_factory, brand_id)

    def _begin(self, job: ClaimedJob, payload: EvalJobPayload) -> _Work | None:
        """Marca o item `running`; `None` (job concluido sem trabalho) se ja nao ha o que fazer."""
        with self._session_factory.begin() as session:
            if not self._queue.holds_lease_in(session, job):
                return None  # cancelado ou reclamado por outro: nada a tocar
            run = session.get(EvalRun, payload.eval_run_id)
            item = session.get(EvalItem, payload.eval_item_id)
            if run is None or item is None or item.run_id != run.id or run.status != "running":
                self._queue.complete_in(session, job)
                return None
            if item.status in TERMINAL_ITEM_STATUSES:
                self._queue.complete_in(session, job)
                return None
            item.status = "running"
            return _Work(
                run.id,
                item.id,
                item.brand_id,
                item.path,
                _brief_of(run, item.brief_key),
                ContextSnapshot.model_validate(run.context_snapshot),
                # A estimativa guarda o teto de TODOS os pacotes, mesmo dos que falharem.
                run.estimate_usd / (len(run.briefs) * len(EVAL_PATHS)),
            )

    def _finish(self, job: ClaimedJob, work: _Work, result: PackageResult, latency_ms: int) -> None:
        """Grava o fim do item e conclui o job na mesma transacao (corrida bloqueada primeiro)."""
        try:
            with self._session_factory.begin() as session:
                locked = lock_open_item(session, work.run_id, work.item_id)
                if not self._queue.holds_lease_in(session, job):
                    raise _LeaseLost  # nao escrever ficheiros para um job que ja nao e nosso
                if locked is not None:
                    run, item = locked
                    outcome = self._outcome(session, work, result, latency_ms)
                    close_item_in(session, run, item, outcome)
                if not self._queue.complete_in(session, job):
                    raise _LeaseLost  # desfaz o item: este worker ja nao e o dono do job
        except _LeaseLost:
            logger.warning("job %s: lease perdida antes de gravar; nada foi gravado", job.id)

    def _outcome(
        self, session: Session, work: _Work, result: PackageResult, latency_ms: int
    ) -> ItemOutcome:
        """`ok` guarda a imagem final e o texto; tudo o resto falha o item com uma razao fixa."""
        if result.outcome != "ok":
            reason = result.reason or "sem razao"
            return failed_outcome(
                f"{result.outcome}: {reason}",
                review_rounds=result.review_rounds,
                latency_ms=latency_ms,
            )
        image = store_package_image(session, self._library, work.brand_id, result.as_package())
        return ItemOutcome(
            "succeeded",
            revision_snapshot_of(result, image),
            image.asset_id,
            result.review_rounds,
            latency_ms,
            None,
        )


def _review_json(review: Revisao) -> JsonObject:
    """Veredicto e violacoes de uma passagem do Revisor (sem as notas livres)."""
    return {
        "veredicto": review.veredicto,
        "violacoes": [str(violation) for violation in review.violacoes],
        "imagem_verificada": review.imagem_verificada,
    }


def _brief_of(run: EvalRun, brief_key: str) -> JsonObject:
    """O brief do item na copia guardada na corrida (editar o YAML depois nao a altera)."""
    for brief in run.briefs:
        if brief.get("key") == brief_key:
            return brief
    raise LookupError("o brief do item nao esta na corrida")
