"""Regra unica de reconciliacao de um plano com os posts da semana: o que acontece a cada slot.

A aprovacao (`PostReconciler.apply`) e a previsao (`preview_approval`) chamam as MESMAS funcoes
daqui, por isso nao podem divergir: a previsao mostra exatamente o que a aprovacao vai fazer.
Sao funcoes sem escrita; quem decide tambem diz se o post tem trabalho ativo (`is_busy`).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from marketing_agent.db.models import JsonObject, Post
from marketing_agent.planning.schemas import PlanoSemanal, PlanSlot

PLANNED = "planned"
CANCELLED = "cancelled"
# Ainda nao foram para revisao: o plano novo pode reescrever o brief.
REWRITABLE = frozenset({PLANNED, "drafting", CANCELLED})
# Sem slot correspondente no plano novo, estes posts deixam de ter razao de existir.
CANCELLED_WHEN_REMOVED = frozenset({PLANNED, "drafting", "in_review", "approved"})
# Cancelar estes perde trabalho do responsavel: exige confirmacao explicita.
NEEDS_CONFIRMATION = frozenset({"in_review", "approved"})


class SlotOutcome(StrEnum):
    """O que a aprovacao faz a um lugar do plano."""

    NEW = "new"  # nao havia post: cria-se, com trabalho
    REWRITTEN = "rewritten"  # brief, canais ou data mudaram (ou o post estava cancelado)
    UNCHANGED = "unchanged"  # igual e ainda com trabalho ativo: so aponta ao plano novo
    RESTARTED = "restarted"  # igual, mas sem trabalho ativo: gera-se de novo
    DECIDED_KEPT = "decided_kept"  # ja em revisao/decidido e igual ao plano: intocado
    DECIDED_DIFFERS = "decided_differs"  # ja em revisao/decidido, mantem o texto e o plano diverge
    CANCELLED = "cancelled"  # o lugar saiu do plano


@dataclass(frozen=True)
class SlotDecision:
    """Decisao sobre um lugar: `slot` falta nos cancelados, `post` falta nos novos."""

    slot_key: str
    slot: PlanSlot | None
    post: Post | None
    outcome: SlotOutcome


def slot_brief(slot: PlanSlot) -> JsonObject:
    """Brief guardado no post a partir do slot do plano."""
    return {
        "tema": slot.tema,
        "pilar": slot.pilar,
        "objetivo": slot.objetivo,
        "justificacao": slot.justificacao,
        "kb_refs": list(slot.kb_refs),
        "experiment_index": slot.experiment_index,
    }


def slot_differs(post: Post, slot: PlanSlot) -> bool:
    """O post foi escrito para outro brief, outros canais ou outra data que o slot?"""
    return (
        dict(post.brief) != slot_brief(slot)
        or list(post.channels) != list(slot.canais)
        or post.scheduled_date != slot.data
    )


def classify_slot(
    post: Post | None, slot: PlanSlot, is_busy: Callable[[Post], bool]
) -> SlotOutcome:
    """Resultado de aplicar `slot` ao `post` existente (se houver)."""
    if post is None:
        return SlotOutcome.NEW
    differs = slot_differs(post, slot)
    if post.lifecycle not in REWRITABLE:
        return SlotOutcome.DECIDED_DIFFERS if differs else SlotOutcome.DECIDED_KEPT
    if post.lifecycle == CANCELLED or differs:
        return SlotOutcome.REWRITTEN
    return SlotOutcome.UNCHANGED if is_busy(post) else SlotOutcome.RESTARTED


def classify_week(
    existing: Mapping[str, Post], plan: PlanoSemanal, is_busy: Callable[[Post], bool]
) -> list[SlotDecision]:
    """Decisoes do plano, por ordem de slot, e depois as dos posts cujo lugar saiu do plano."""
    decisions: list[SlotDecision] = []
    for slot in plan.slots:
        post = existing.get(slot.slot_key)
        decisions.append(
            SlotDecision(slot.slot_key, slot, post, classify_slot(post, slot, is_busy))
        )
    kept_keys = {slot.slot_key for slot in plan.slots}
    removed = sorted(
        (post for key, post in existing.items() if key not in kept_keys),
        key=lambda post: (post.scheduled_date, post.slot_key),
    )
    decisions += [
        SlotDecision(post.slot_key, None, post, SlotOutcome.CANCELLED)
        for post in removed
        if post.lifecycle in CANCELLED_WHEN_REMOVED
    ]
    return decisions
