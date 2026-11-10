"""Posts de uma semana a partir do plano aprovado: criar, atualizar, manter ou cancelar por slot.

Tudo corre na transacao do chamador (a mesma que aprova o plano): ou ficam todos os posts e
trabalhos, ou nenhum. A chave do post e o `slot_key`, estavel dentro da semana, por isso um
plano novo substitui o anterior slot a slot em vez de duplicar posts. O que acontece a cada slot
decide-se em `slot_rules`, a mesma regra que alimenta a previsao.
"""

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketing_agent.artifacts.store import ArtifactRecord
from marketing_agent.clock import Clock
from marketing_agent.db.models import Post
from marketing_agent.jobs.queue import GENERATE_POST_JOB, JobQueue
from marketing_agent.planning.errors import ConfirmationRequired
from marketing_agent.planning.preview import ApprovalPreview, build_preview
from marketing_agent.planning.schemas import PlanoSemanal, PlanSlot
from marketing_agent.planning.slot_rules import (
    CANCELLED,
    PLANNED,
    SlotDecision,
    SlotOutcome,
    classify_week,
    slot_brief,
)

# Estes resultados mantem o post e o seu trabalho como estao (sem trabalho novo).
_NO_NEW_JOB = frozenset(
    {SlotOutcome.UNCHANGED, SlotOutcome.DECIDED_KEPT, SlotOutcome.DECIDED_DIFFERS}
)
_KEPT = _NO_NEW_JOB | {SlotOutcome.RESTARTED}


@dataclass(frozen=True)
class Reconciliation:
    """Resultado de aplicar um plano: ids dos posts do plano, por ordem de slot, e contagens."""

    post_ids: list[str]
    created: int
    updated: int
    kept: int
    cancelled: int
    jobs: int


class PostReconciler:
    """Aplica um plano aprovado aos posts da semana, na transacao do chamador."""

    def __init__(self, queue: JobQueue, clock: Clock) -> None:
        """Guarda a fila (trabalhos `generate_post`) e o relogio (data de criacao dos posts)."""
        self._queue = queue
        self._clock = clock

    def preview(self, session: Session, brand_id: str, plan: PlanoSemanal) -> ApprovalPreview:
        """O que `apply` faria, so com leituras: sem escritas e sem bloquear linhas.

        E uma fotografia: quem aplica volta a decidir com os posts bloqueados.
        """
        return build_preview(self._decide(session, brand_id, plan, lock=False))

    def apply(
        self,
        session: Session,
        plan_record: ArtifactRecord,
        plan: PlanoSemanal,
        strategy_id: str,
        *,
        confirm_cancel: bool = False,
    ) -> Reconciliation:
        """Cria, atualiza, mantem ou cancela os posts da semana e enfileira os trabalhos.

        Slot novo, cancelado ou com brief, canais ou data diferentes: post `planned` com um
        trabalho novo (o trabalho ativo antigo do post e cancelado). Slot igual em `planned` ou
        `drafting`: passa a apontar ao plano novo e so recebe trabalho novo se nao tem nenhum
        `generate_post` ativo (o anterior falhou, morreu ou foi cancelado). Slot em `in_review`,
        `approved` ou `rejected`: intocado. Slot que saiu do plano: `cancelled`, e o seu trabalho
        tambem.

        Cancelar posts `approved` ou `in_review` exige `confirm_cancel`; senao levanta
        `ConfirmationRequired` antes de escrever (o chamador reverte a transacao). A verificacao
        usa os posts ja bloqueados, por isso nenhum estado muda entre verificar e aplicar.
        """
        decisions = self._decide(session, plan_record.brand_id, plan, lock=True)
        if build_preview(decisions).cancels_decided and not confirm_cancel:
            raise ConfirmationRequired
        placed = [self._place(session, d, plan_record, strategy_id) for d in decisions if d.slot]
        session.flush()  # os posts novos so ganham id aqui
        cancelled = [self._cancel(session, d) for d in decisions if d.slot is None]
        to_run = [post for post, outcome in placed if outcome not in _NO_NEW_JOB]
        for post in to_run:
            self._enqueue(session, post, plan_record.id)
        counts = Counter(outcome for _, outcome in placed)
        return Reconciliation(
            post_ids=[post.id for post, _ in placed],
            created=counts[SlotOutcome.NEW],
            updated=counts[SlotOutcome.REWRITTEN],
            kept=sum(counts[outcome] for outcome in _KEPT),
            cancelled=len(cancelled),
            jobs=len(to_run),
        )

    def _decide(
        self, session: Session, brand_id: str, plan: PlanoSemanal, *, lock: bool
    ) -> list[SlotDecision]:
        existing = self._week_posts(session, brand_id, plan.semana, lock=lock)
        return classify_week(
            existing, plan, lambda post: self._queue.has_active_for_post_in(session, post.id)
        )

    def _week_posts(
        self, session: Session, brand_id: str, week: str, *, lock: bool
    ) -> dict[str, Post]:
        """Posts da semana; com `lock`, ficam bloqueados ate ao fim da transacao."""
        statement = (
            select(Post)
            .where(Post.brand_id == brand_id, Post.slot_key.startswith(f"{week}-", autoescape=True))
            .order_by(Post.id)  # mesma ordem de `requeue_expired`: sem ciclos entre transacoes
        )
        if lock:
            statement = statement.with_for_update()
        return {post.slot_key: post for post in session.scalars(statement)}

    def _place(
        self,
        session: Session,
        decision: SlotDecision,
        plan_record: ArtifactRecord,
        strategy_id: str,
    ) -> tuple[Post, SlotOutcome]:
        """Aplica a decisao de um slot do plano; devolve o post (novo ou existente)."""
        slot, post, outcome = decision.slot, decision.post, decision.outcome
        if slot is None:
            raise ValueError("decisao de slot sem slot")
        if outcome is SlotOutcome.NEW:
            created = self._new_post(slot, plan_record, strategy_id)
            session.add(created)
            return created, outcome
        if post is None:
            raise ValueError("decisao sobre post existente sem post")
        if outcome in (SlotOutcome.DECIDED_KEPT, SlotOutcome.DECIDED_DIFFERS):
            return post, outcome
        post.plan_artifact_id = plan_record.id
        post.strategy_artifact_id = strategy_id
        if outcome is SlotOutcome.REWRITTEN:
            self._queue.cancel_for_post_in(session, post.id)  # nao deixar o brief antigo a correr
            self._rewrite(post, slot)
        elif outcome is SlotOutcome.RESTARTED:
            post.last_error = None  # o ciclo de vida (`planned` ou `drafting`) nao muda
        return post, outcome

    def _new_post(self, slot: PlanSlot, plan_record: ArtifactRecord, strategy_id: str) -> Post:
        return Post(
            brand_id=plan_record.brand_id,
            plan_artifact_id=plan_record.id,
            slot_key=slot.slot_key,
            strategy_artifact_id=strategy_id,
            experiment_index=slot.experiment_index,
            channels=list(slot.canais),
            scheduled_date=slot.data,
            brief=slot_brief(slot),
            lifecycle=PLANNED,
            created_at=self._clock.now(),
        )

    def _rewrite(self, post: Post, slot: PlanSlot) -> None:
        post.experiment_index = slot.experiment_index
        post.channels = list(slot.canais)
        post.scheduled_date = slot.data
        post.brief = slot_brief(slot)
        post.lifecycle = PLANNED
        post.review_rounds = 0  # brief novo ou post revivido: comeca uma geracao nova
        post.last_error = None

    def _cancel(self, session: Session, decision: SlotDecision) -> str:
        if decision.post is None:
            raise ValueError("cancelamento sem post")
        decision.post.lifecycle = CANCELLED
        self._queue.cancel_for_post_in(session, decision.post.id)
        return decision.post.id

    def _enqueue(self, session: Session, post: Post, plan_id: str) -> None:
        payload = {"post_id": post.id, "owner_comment": None, "reason": "plan"}
        self._queue.enqueue_in(
            session, GENERATE_POST_JOB, payload, f"{GENERATE_POST_JOB}:{post.id}:plan:{plan_id}"
        )
