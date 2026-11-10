"""Previsao de aprovar um plano: o que acontece a cada lugar da semana, sem escrever nada.

Construida a partir das `SlotDecision` da regra unica (`slot_rules`), as mesmas que a aprovacao
aplica. So leva temas (texto do plano e dos briefs) e estados: o painel escapa-os ao mostrar.
"""

from dataclasses import dataclass
from datetime import date

from marketing_agent.planning.slot_rules import NEEDS_CONFIRMATION, SlotDecision, SlotOutcome

_KEPT = frozenset({SlotOutcome.UNCHANGED, SlotOutcome.RESTARTED, SlotOutcome.DECIDED_KEPT})


@dataclass(frozen=True)
class PreviewEntry:
    """Um lugar da semana: tema antigo e novo, estado do post e o que lhe vai acontecer."""

    slot_key: str
    scheduled_date: date
    old_theme: str | None
    new_theme: str | None
    lifecycle: str | None
    outcome: SlotOutcome


@dataclass(frozen=True)
class ApprovalPreview:
    """O que aprovar o plano faria, lugar a lugar (slots do plano e depois os que saem)."""

    entries: tuple[PreviewEntry, ...]

    def _count(self, *outcomes: SlotOutcome) -> int:
        return sum(entry.outcome in outcomes for entry in self.entries)

    @property
    def created(self) -> int:
        """Lugares sem post: criam-se."""
        return self._count(SlotOutcome.NEW)

    @property
    def rewritten(self) -> int:
        """Posts por rever que o plano novo reescreve."""
        return self._count(SlotOutcome.REWRITTEN)

    @property
    def kept(self) -> int:
        """Posts mantidos tal como estao (incluindo os que vao ser gerados de novo)."""
        return self._count(*_KEPT)

    @property
    def kept_differs(self) -> int:
        """Posts decididos ou em revisao que mantem o texto, mas o plano mostra outro conteudo."""
        return self._count(SlotOutcome.DECIDED_DIFFERS)

    @property
    def cancelled(self) -> int:
        """Posts cujo lugar sai do plano."""
        return self._count(SlotOutcome.CANCELLED)

    @property
    def has_existing_posts(self) -> bool:
        """A semana ja tem posts (qualquer lugar que nao seja novo)."""
        return any(entry.lifecycle is not None for entry in self.entries)

    @property
    def cancels_decided(self) -> tuple[PreviewEntry, ...]:
        """Cancelamentos de posts `approved` ou `in_review`: so avancam com confirmacao."""
        return tuple(
            entry
            for entry in self.entries
            if entry.outcome is SlotOutcome.CANCELLED and entry.lifecycle in NEEDS_CONFIRMATION
        )


def build_preview(decisions: list[SlotDecision]) -> ApprovalPreview:
    """Previsao a partir das decisoes (que ja trazem a ordem certa)."""
    return ApprovalPreview(tuple(_entry(decision) for decision in decisions))


def _entry(decision: SlotDecision) -> PreviewEntry:
    """A data e a do plano; nos lugares que saem (sem slot), a do post cancelado."""
    post, slot = decision.post, decision.slot
    if slot is not None:
        scheduled = slot.data
    elif post is not None:
        scheduled = post.scheduled_date
    else:
        raise ValueError("decisao sem slot nem post")
    return PreviewEntry(
        slot_key=decision.slot_key,
        scheduled_date=scheduled,
        old_theme=None if post is None else str(post.brief.get("tema", "")),
        new_theme=None if slot is None else slot.tema,
        lifecycle=None if post is None else post.lifecycle,
        outcome=decision.outcome,
    )
