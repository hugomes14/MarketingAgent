"""Posts de uma semana a partir do plano aprovado: criar, atualizar, manter ou cancelar por slot.

Tudo corre na transacao do chamador (a mesma que aprova o plano): ou ficam todos os posts e
trabalhos, ou nenhum. A chave do post e o `slot_key`, estavel dentro da semana, por isso um
plano novo substitui o anterior slot a slot em vez de duplicar posts.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketing_agent.artifacts.store import ArtifactRecord
from marketing_agent.clock import Clock
from marketing_agent.db.models import JsonObject, Post
from marketing_agent.jobs.queue import GENERATE_POST_JOB, JobQueue
from marketing_agent.planning.schemas import PlanoSemanal, PlanSlot

PLANNED = "planned"
CANCELLED = "cancelled"
# Ainda nao foram para revisao: o plano novo pode reescrever o brief.
_REWRITABLE = frozenset({PLANNED, "drafting", CANCELLED})
# Sem slot correspondente no plano novo, estes posts deixam de ter razao de existir.
_CANCELLED_WHEN_REMOVED = frozenset({PLANNED, "drafting", "in_review", "approved"})


class _Action(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    KEPT = "kept"
    RESTARTED = "restarted"  # igual ao anterior, mas sem trabalho ativo: gera-se de novo


@dataclass(frozen=True)
class Reconciliation:
    """Resultado de aplicar um plano: ids dos posts do plano, por ordem de slot, e contagens."""

    post_ids: list[str]
    created: int
    updated: int
    kept: int
    cancelled: int
    jobs: int


def _brief(slot: PlanSlot) -> JsonObject:
    return {
        "tema": slot.tema,
        "pilar": slot.pilar,
        "objetivo": slot.objetivo,
        "justificacao": slot.justificacao,
        "kb_refs": list(slot.kb_refs),
        "experiment_index": slot.experiment_index,
    }


def _differs(post: Post, slot: PlanSlot) -> bool:
    return (
        dict(post.brief) != _brief(slot)
        or list(post.channels) != list(slot.canais)
        or post.scheduled_date != slot.data
    )


class PostReconciler:
    """Aplica um plano aprovado aos posts da semana, na transacao do chamador."""

    def __init__(self, queue: JobQueue, clock: Clock) -> None:
        """Guarda a fila (trabalhos `generate_post`) e o relogio (data de criacao dos posts)."""
        self._queue = queue
        self._clock = clock

    def apply(
        self, session: Session, plan_record: ArtifactRecord, plan: PlanoSemanal, strategy_id: str
    ) -> Reconciliation:
        """Cria, atualiza, mantem ou cancela os posts da semana e enfileira os trabalhos.

        Slot novo, cancelado ou com brief, canais ou data diferentes: post `planned` com um
        trabalho novo (o trabalho ativo antigo do post e cancelado). Slot igual em `planned` ou
        `drafting`: passa a apontar ao plano novo e so recebe trabalho novo se nao tem nenhum
        `generate_post` ativo (o anterior falhou, morreu ou foi cancelado). Slot em `in_review`,
        `approved` ou `rejected`: intocado. Slot que saiu do plano: `cancelled`, e o seu trabalho
        tambem.
        """
        existing = self._week_posts(session, plan_record.brand_id, plan.semana)
        outcomes = [
            self._place(session, existing.get(slot.slot_key), slot, plan_record, strategy_id)
            for slot in plan.slots
        ]
        session.flush()  # os posts novos so ganham id aqui
        cancelled = self._cancel_removed(session, existing, {slot.slot_key for slot in plan.slots})
        to_run = [post for post, action in outcomes if action is not _Action.KEPT]
        for post in to_run:
            self._enqueue(session, post, plan_record.id)
        counts = Counter(action for _, action in outcomes)
        return Reconciliation(
            post_ids=[post.id for post, _ in outcomes],
            created=counts[_Action.CREATED],
            updated=counts[_Action.UPDATED],
            kept=counts[_Action.KEPT] + counts[_Action.RESTARTED],
            cancelled=cancelled,
            jobs=len(to_run),
        )

    def _week_posts(self, session: Session, brand_id: str, week: str) -> dict[str, Post]:
        """Posts da semana, bloqueados ate ao fim da transacao (aprovacoes da semana serializam)."""
        statement = (
            select(Post)
            .where(Post.brand_id == brand_id, Post.slot_key.startswith(f"{week}-", autoescape=True))
            .with_for_update()
        )
        return {post.slot_key: post for post in session.scalars(statement)}

    def _place(
        self,
        session: Session,
        post: Post | None,
        slot: PlanSlot,
        plan_record: ArtifactRecord,
        strategy_id: str,
    ) -> tuple[Post, _Action]:
        if post is None:
            created = self._new_post(slot, plan_record, strategy_id)
            session.add(created)
            return created, _Action.CREATED
        if post.lifecycle not in _REWRITABLE:
            return post, _Action.KEPT
        post.plan_artifact_id = plan_record.id
        post.strategy_artifact_id = strategy_id
        if post.lifecycle != CANCELLED and not _differs(post, slot):
            return post, self._restart_if_idle(session, post)
        self._queue.cancel_for_post_in(session, post.id)  # nao deixar o brief antigo a correr
        self._rewrite(post, slot)
        return post, _Action.UPDATED

    def _restart_if_idle(self, session: Session, post: Post) -> _Action:
        """Slot igual: so gera de novo se o trabalho anterior terminou sem sucesso (ou sumiu).

        O post esta bloqueado `FOR UPDATE`, por isso nenhum outro pedido cria o trabalho entre
        a verificacao e o `enqueue`. O ciclo de vida (`planned` ou `drafting`) nao muda.
        """
        if self._queue.has_active_for_post_in(session, post.id):
            return _Action.KEPT
        post.last_error = None
        return _Action.RESTARTED

    def _new_post(self, slot: PlanSlot, plan_record: ArtifactRecord, strategy_id: str) -> Post:
        return Post(
            brand_id=plan_record.brand_id,
            plan_artifact_id=plan_record.id,
            slot_key=slot.slot_key,
            strategy_artifact_id=strategy_id,
            experiment_index=slot.experiment_index,
            channels=list(slot.canais),
            scheduled_date=slot.data,
            brief=_brief(slot),
            lifecycle=PLANNED,
            created_at=self._clock.now(),
        )

    def _rewrite(self, post: Post, slot: PlanSlot) -> None:
        post.experiment_index = slot.experiment_index
        post.channels = list(slot.canais)
        post.scheduled_date = slot.data
        post.brief = _brief(slot)
        post.lifecycle = PLANNED
        post.review_rounds = 0  # brief novo ou post revivido: comeca uma geracao nova
        post.last_error = None

    def _cancel_removed(
        self, session: Session, existing: Mapping[str, Post], kept_keys: set[str]
    ) -> int:
        removed = [
            post
            for key, post in existing.items()
            if key not in kept_keys and post.lifecycle in _CANCELLED_WHEN_REMOVED
        ]
        for post in removed:
            post.lifecycle = CANCELLED
            self._queue.cancel_for_post_in(session, post.id)
        return len(removed)

    def _enqueue(self, session: Session, post: Post, plan_id: str) -> None:
        payload = {"post_id": post.id, "owner_comment": None, "reason": "plan"}
        self._queue.enqueue_in(
            session, GENERATE_POST_JOB, payload, f"{GENERATE_POST_JOB}:{post.id}:plan:{plan_id}"
        )
