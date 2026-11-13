"""Estado dos itens e das corridas de avaliacao: fechar um item, fechar a corrida, reconciliar.

Um item acaba num de dois estados terminais, `succeeded` ou `failed`. A corrida passa a `ready`
quando TODOS os seus itens sao terminais, e isso decide-se com a linha da corrida bloqueada
(`SELECT ... FOR UPDATE`): dois itens a acabar ao mesmo tempo serializam-se nesse lock, o
segundo ve o primeiro ja gravado e so um deles marca a corrida `ready` (sem perda de
atualizacao). Ordem dos locks de quem escreve aqui e tambem no job: corrida primeiro, job depois.

Custo, saidas invalidas e voltas de um item derivam da familia de triggers do item
(`eval_item:<id>`, a unica ligacao entre o item e a contabilidade do motor): somam TODAS as
tentativas do job, nunca so a ultima, e calculam-se no momento de fechar (com sucesso ou nao).
"""

from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.content.package_costs import (
    PackageTriggers,
    family_costs_in,
    family_invalid_outputs_in,
)
from marketing_agent.db.models import EvalItem, EvalRun, Job, JsonObject
from marketing_agent.jobs.queue import truncate_error

TERMINAL_ITEM_STATUSES: Final = ("succeeded", "failed")
# Estados de um job que ja nao vai produzir o item (cancelado, falhado ou esgotado).
DEAD_JOB_STATUSES: Final = ("failed", "dead", "cancelled")


def item_triggers(item_id: str) -> PackageTriggers:
    """A familia de triggers do item: a unica ligacao entre o item e o custo."""
    return PackageTriggers.single(f"eval_item:{item_id}")


@dataclass(frozen=True)
class ItemOutcome:
    """Como o item acabou; `revision_snapshot` e `final_asset_id` so existem se `succeeded`."""

    status: str
    revision_snapshot: JsonObject | None
    final_asset_id: str | None
    review_rounds: int
    latency_ms: int | None
    last_error: str | None


def failed_outcome(
    error: str, *, review_rounds: int = 0, latency_ms: int | None = None
) -> ItemOutcome:
    """Item falhado: `error` e uma frase fixa do codigo (nunca texto de modelo)."""
    return ItemOutcome("failed", None, None, review_rounds, latency_ms, truncate_error(error))


def lock_open_item(session: Session, run_id: str, item_id: str) -> tuple[EvalRun, EvalItem] | None:
    """Bloqueia a corrida e devolve (corrida, item) se ainda ha trabalho a gravar.

    `None` se a corrida ja nao esta `running`, o item nao e dela ou ja e terminal: quem chama
    nao escreve nada (a segunda entrega do mesmo job e inofensiva).
    """
    run = session.scalars(
        select(EvalRun).where(EvalRun.id == run_id).with_for_update()
    ).one_or_none()
    # `populate_existing`: depois de esperar pelo lock o item pode ter mudado noutra transacao.
    item = session.scalars(
        select(EvalItem)
        .where(EvalItem.id == item_id, EvalItem.run_id == run_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if run is None or item is None or run.status != "running":
        return None
    if item.status in TERMINAL_ITEM_STATUSES:
        return None
    return run, item


def close_item_in(session: Session, run: EvalRun, item: EvalItem, outcome: ItemOutcome) -> bool:
    """Grava o fim do item (com a corrida bloqueada) e fecha a corrida se foi o ultimo.

    Devolve `True` se esta chamada passou a corrida a `ready`.
    """
    triggers = item_triggers(item.id).family
    costs = family_costs_in(session, item.brand_id, triggers)
    item.status = outcome.status
    item.revision_snapshot = outcome.revision_snapshot
    item.final_asset_id = outcome.final_asset_id
    item.cost_usd = costs.package_usd
    item.invalid_outputs = family_invalid_outputs_in(session, item.brand_id, triggers)
    item.review_rounds = outcome.review_rounds
    item.latency_ms = outcome.latency_ms
    item.last_error = outcome.last_error
    session.flush()
    return _ready_if_finished(session, run)


def _ready_if_finished(session: Session, run: EvalRun) -> bool:
    """Passa a corrida (ja bloqueada) a `ready` se nenhum item ficou por acabar."""
    pending = select(EvalItem.id).where(
        EvalItem.run_id == run.id, EvalItem.status.not_in(TERMINAL_ITEM_STATUSES)
    )
    if session.execute(pending.limit(1)).first() is not None:
        return False
    run.status = "ready"
    return True


def item_id_of_job(session_factory: sessionmaker[Session], job_id: str) -> str | None:
    """O item que o job produz (`eval_item.job_id` e unico), mesmo se o payload esta estragado."""
    with session_factory() as session:
        return session.scalar(select(EvalItem.id).where(EvalItem.job_id == job_id))


def close_stalled_item(session_factory: sessionmaker[Session], item_id: str) -> bool:
    """Falha o item cujo job acabou sem concluir (`failed`, `dead`, `cancelled`).

    Reconfirma o estado do job com a corrida bloqueada: um job que entretanto voltou a
    `queued` nao fecha o item. Devolve `True` se o item foi fechado agora. Idempotente.
    """
    with session_factory.begin() as session:
        item = session.get(EvalItem, item_id)
        if item is None or item.job_id is None:
            return False
        locked = lock_open_item(session, item.run_id, item_id)
        job = session.execute(
            select(Job.status, Job.last_error).where(Job.id == item.job_id)
        ).one_or_none()
        if locked is None or job is None or job.status not in DEAD_JOB_STATUSES:
            return False
        run, open_item = locked
        # `last_error` do job so leva frases fixas (`describe_error`, recuperacao da fila).
        detail = f"job {job.status}" + (f": {job.last_error}" if job.last_error else "")
        close_item_in(session, run, open_item, failed_outcome(detail))
        return True


def reconcile_runs(session_factory: sessionmaker[Session], brand_id: str | None = None) -> int:
    """Fecha os itens de jobs mortos e as corridas que ja so tem itens terminais.

    Seguro e idempotente: corre no arranque do worker, nas iteracoes ociosas e a pedido.
    Devolve quantos itens fechou.
    """
    stalled = (
        select(EvalItem.id)
        .join(Job, Job.id == EvalItem.job_id)
        .join(EvalRun, EvalRun.id == EvalItem.run_id)
        .where(
            EvalRun.status == "running",
            EvalItem.status.not_in(TERMINAL_ITEM_STATUSES),
            Job.status.in_(DEAD_JOB_STATUSES),
        )
        .order_by(EvalItem.id)
    )
    if brand_id is not None:
        stalled = stalled.where(EvalItem.brand_id == brand_id)
    with session_factory() as session:
        item_ids = list(session.scalars(stalled))
    closed = sum(close_stalled_item(session_factory, item_id) for item_id in item_ids)
    ready_finished_runs(session_factory, brand_id)
    return closed


def ready_finished_runs(session_factory: sessionmaker[Session], brand_id: str | None) -> int:
    """Passa a `ready` as corridas `running` sem itens por acabar; devolve quantas."""
    pending = select(EvalItem.id).where(
        EvalItem.run_id == EvalRun.id, EvalItem.status.not_in(TERMINAL_ITEM_STATUSES)
    )
    candidates = select(EvalRun.id).where(EvalRun.status == "running", ~pending.exists())
    if brand_id is not None:
        candidates = candidates.where(EvalRun.brand_id == brand_id)
    with session_factory() as session:
        run_ids = list(session.scalars(candidates.order_by(EvalRun.id)))
    return sum(_ready_run(session_factory, run_id) for run_id in run_ids)


def _ready_run(session_factory: sessionmaker[Session], run_id: str) -> bool:
    with session_factory.begin() as session:
        run = session.scalars(
            select(EvalRun).where(EvalRun.id == run_id).with_for_update()
        ).one_or_none()
        return run is not None and run.status == "running" and _ready_if_finished(session, run)
