"""Julgamento cego: mutacoes serializadas na corrida, respostas so por acrescento."""

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased, sessionmaker

from marketing_agent.clock import Clock
from marketing_agent.db.models import AuditLog, EvalItem, EvalJudgement, EvalRun
from marketing_agent.evals.blind import (
    BlindPair,
    BlindSide,
    CurrentJudgement,
    JudgementInput,
    JudgingSummary,
    Side,
    side_a_path,
)
from marketing_agent.evals.sets import EvalBrief
from marketing_agent.evals.snapshot import PackageSnapshot
from marketing_agent.evals.views import EvalNotFound, require_run

__all__ = ["JudgementInput", "JudgingService"]


class RunNotReady(Exception):
    """Os pacotes ainda estao em execucao."""


class AlreadyRevealed(Exception):
    """Respostas cegas ja nao podem mudar depois da revelacao."""


class StaleJudgement(Exception):
    """Outra aba gravou uma resposta; recarregar antes de corrigir."""


class RevealBlocked(Exception):
    """Faltam respostas a pares elegiveis."""

    def __init__(self, missing: tuple[int, ...]) -> None:
        """Indices dos pares em falta, sem nomes de percursos."""
        self.missing = missing
        super().__init__("faltam pares por avaliar")


def eligible_pairs_in(
    session: Session, run: EvalRun
) -> list[tuple[EvalBrief, dict[str, EvalItem]]]:
    """Pares completos na ordem original; falhas excluem o par inteiro, nunca so um lado."""
    if run.status == "running":
        raise RunNotReady
    grouped: dict[str, dict[str, EvalItem]] = {}
    for item in session.scalars(select(EvalItem).where(EvalItem.run_id == run.id)):
        if item.status == "succeeded":
            grouped.setdefault(item.brief_key, {})[item.path] = item
    pairs = []
    for raw in run.briefs:
        brief = EvalBrief.model_validate(raw)
        items = grouped.get(brief.key, {})
        if set(items) == {"simples", "especializado"}:
            pairs.append((brief, items))
    return pairs


def current_answers_in(session: Session, run_id: str) -> dict[str, EvalJudgement]:
    """Folhas das cadeias de substituicao; respostas antigas nunca contam duas vezes."""
    newer = aliased(EvalJudgement)
    replaced = select(newer.id).where(newer.supersedes_id == EvalJudgement.id).exists()
    rows = session.scalars(select(EvalJudgement).where(EvalJudgement.run_id == run_id, ~replaced))
    return {row.brief_key: row for row in rows}


def _pair_at(session: Session, run: EvalRun, index: int) -> tuple[EvalBrief, dict[str, EvalItem]]:
    pairs = eligible_pairs_in(session, run)
    if not 1 <= index <= len(pairs):
        raise EvalNotFound
    return pairs[index - 1]


def _blind_side(item: EvalItem) -> BlindSide:
    if item.revision_snapshot is None:
        raise EvalNotFound
    snapshot = PackageSnapshot.model_validate(item.revision_snapshot)
    draft, image = snapshot.texto, snapshot.imagem
    return BlindSide(
        draft.legenda,
        tuple(draft.hashtags),
        draft.apelo_a_acao,
        image.alt_text,
        tuple(claim.texto for claim in draft.alegacoes),
        image.gerada_por_ia,
    )


def _current(row: EvalJudgement | None) -> CurrentJudgement | None:
    if row is None:
        return None
    answer = JudgementInput.model_validate(
        {name: getattr(row, name) for name in JudgementInput.model_fields}
    )
    return CurrentJudgement(row.id, answer)


class JudgingService:
    """Apresenta pares cegos e regista respostas antes da revelacao explicita."""

    def __init__(
        self, factory: sessionmaker[Session], clock: Clock, max_note_chars: int = 2000
    ) -> None:
        """Texto livre limitado tambem pelo teto do esquema e da base."""
        self._factory = factory
        self._clock = clock
        self._max_note_chars = min(max_note_chars, 2000)

    def pair(self, brand_id: str, run_id: str, index: int) -> BlindPair:
        """Par anonimizado, com uma resposta atual que nunca inclui `side_a_path`."""
        with self._factory() as session:
            run = require_run(session, brand_id, run_id)
            brief, items = _pair_at(session, run, index)
            path_a = side_a_path(run.seed, brief.key)
            path_b = "especializado" if path_a == "simples" else "simples"
            current = _current(current_answers_in(session, run_id).get(brief.key))
            return BlindPair(
                index, brief, _blind_side(items[path_a]), _blind_side(items[path_b]), current
            )

    def pair_image(self, brand_id: str, run_id: str, index: int, side: Side) -> str:
        """Id interno da imagem do lado pedido; o painel serve bytes sem revelar este id."""
        if side not in ("a", "b"):
            raise EvalNotFound
        with self._factory() as session:
            run = require_run(session, brand_id, run_id)
            brief, items = _pair_at(session, run, index)
            path = side_a_path(run.seed, brief.key)
            if side == "b":
                path = "especializado" if path == "simples" else "simples"
            asset_id = items[path].final_asset_id
            if asset_id is None:
                raise EvalNotFound
            return asset_id

    def summary(self, brand_id: str, run_id: str) -> JudgingSummary:
        """Contagens para navegacao e revelacao, sem a ordem de conclusao dos itens."""
        with self._factory() as session:
            run = require_run(session, brand_id, run_id)
            return self._summary_in(session, run)

    @staticmethod
    def _summary_in(session: Session, run: EvalRun) -> JudgingSummary:
        pairs = eligible_pairs_in(session, run)
        answers = current_answers_in(session, run.id)
        missing = tuple(i for i, (brief, _) in enumerate(pairs, 1) if brief.key not in answers)
        return JudgingSummary(
            len(pairs), len(pairs) - len(missing), len(run.briefs) - len(pairs), missing
        )

    def record(
        self,
        brand_id: str,
        run_id: str,
        index: int,
        answer: JudgementInput,
        seconds_spent: int,
        replaces_id: str | None,
        actor: str,
    ) -> str:
        """Acrescenta resposta ou correcao; lock da corrida serializa com outras abas e revelar."""
        if (
            not isinstance(seconds_spent, int)
            or isinstance(seconds_spent, bool)
            or not 0 <= seconds_spent <= 3600
        ):
            raise ValueError("tempo de avaliacao invalido")
        if answer.note is not None and len(answer.note) > self._max_note_chars:
            raise ValueError("nota acima do limite")
        with self._factory.begin() as session:
            run = require_run(session, brand_id, run_id, lock=True)
            if run.status == "revealed":
                raise AlreadyRevealed
            brief, _ = _pair_at(session, run, index)
            current = current_answers_in(session, run_id).get(brief.key)
            if replaces_id != (current.id if current else None):
                raise StaleJudgement
            row = EvalJudgement(
                brand_id=brand_id,
                run_id=run_id,
                brief_key=brief.key,
                side_a_path=side_a_path(run.seed, brief.key),
                **answer.model_dump(),
                seconds_spent=seconds_spent,
                supersedes_id=replaces_id,
                decided_by=actor,
                decided_at=self._clock.now(),
            )
            session.add(row)
            session.flush()
            return row.id

    def reveal(self, brand_id: str, run_id: str, actor: str) -> None:
        """Revela uma unica vez, so depois de todos os pares elegiveis terem resposta."""
        with self._factory.begin() as session:
            run = require_run(session, brand_id, run_id, lock=True)
            if run.status == "revealed":
                return
            summary = self._summary_in(session, run)
            if summary.missing:
                raise RevealBlocked(summary.missing)
            run.status, run.revealed_at = "revealed", self._clock.now()
            session.add(
                AuditLog(
                    actor=actor,
                    action="eval_revealed",
                    subject_type="eval_run",
                    subject_id=run_id,
                    detail={"pairs": summary.pairs, "excluded": summary.excluded},
                    at=self._clock.now(),
                )
            )
