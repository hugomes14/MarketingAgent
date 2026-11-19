"""Respostas do responsavel aos pares cegos e a revelacao (spec 2C, secao 5).

Regras de integridade:

- so ha pares numa corrida `ready` ou `revealed`, e so os de briefs com os DOIS pacotes
  `succeeded` (um par com um lado falhado nao se pode comparar as cegas);
- as respostas sao imutaveis (gatilho na base). Corrigir e gravar uma linha nova que substitui a
  ativa (`supersedes_id`); o formulario prova qual era a ativa (`replaces_id`) e uma prova
  desatualizada (outra aba) e recusada, para nunca se perder uma correcao em silencio;
- a revelacao so acontece com todos os pares respondidos e, depois dela, nada se responde.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from marketing_agent.clock import Clock
from marketing_agent.db.models import AuditLog, EvalItem, EvalJudgement, EvalRun
from marketing_agent.evals.blind import (
    BlindBrief,
    BlindPair,
    CurrentAnswer,
    Side,
    blind_side,
    path_for_side,
    side_a_path,
)
from marketing_agent.evals.context import FixedBrief
from marketing_agent.media.library import MediaLibrary

type ErrorKind = Literal[
    "facto_errado", "alegacao_sem_apoio", "voz_errada", "imagem_inadequada", "outro"
]
type EditMinutes = Literal["0", "1", "2_5", "mais_5"]
MAX_NOTE_CHARS: Final = 2000


class RunNotReady(Exception):
    """A corrida ainda nao terminou (ou nao existe nesta marca)."""


class PairNotFound(Exception):
    """Nao ha par com este numero nesta corrida."""


class StaleJudgement(Exception):
    """A resposta a substituir ja nao e a ativa (outra aba ou outra correcao)."""


class AlreadyRevealed(Exception):
    """A corrida ja foi revelada: as respostas ficam fechadas."""


class RevealBlocked(Exception):
    """Faltam pares por responder; `missing` sao os numeros deles."""

    def __init__(self, missing: Sequence[int]) -> None:
        """Guarda os numeros dos pares em falta (so numeros, nunca texto)."""
        super().__init__(f"pares por responder: {list(missing)}")
        self.missing = tuple(missing)


class JudgementInput(BaseModel):
    """Resposta a um par: o melhor lado, os erros e os minutos de edicao por lado e uma nota."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    better: Literal["a", "b", "empate"]
    errors_a: list[ErrorKind] = Field(default_factory=list)
    errors_b: list[ErrorKind] = Field(default_factory=list)
    edit_a: EditMinutes
    edit_b: EditMinutes
    note: Annotated[str, Field(max_length=MAX_NOTE_CHARS)] | None = None

    @field_validator("errors_a", "errors_b")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("erros repetidos")
        return value

    @field_validator("note")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


@dataclass(frozen=True)
class JudgingProgress:
    """Contagens da avaliacao de uma corrida (nada por percurso)."""

    pairs: int
    judged: int


@dataclass(frozen=True)
class _PairRef:
    index: int
    brief: FixedBrief
    items: dict[str, EvalItem]


class JudgingService:
    """Pares cegos, respostas imutaveis e revelacao de uma corrida."""

    def __init__(
        self, session_factory: sessionmaker[Session], library: MediaLibrary, clock: Clock
    ) -> None:
        """Recebe as sessoes, a biblioteca (imagens finais) e o relogio."""
        self._session_factory = session_factory
        self._library = library
        self._clock = clock

    def pair(self, brand_id: str, run_id: str, index: int) -> BlindPair:
        """Par `index` com a resposta ativa, se houver; `RunNotReady`/`PairNotFound` senao."""
        with self._session_factory() as session:
            run = _ready_run(session, brand_id, run_id)
            pairs = _pairs(session, run)
            ref = _pick(pairs, index)
            seed = run.seed
            sides = {
                side: blind_side(_snapshot(ref.items[path_for_side(seed, ref.brief.key, side)]))
                for side in ("a", "b")
            }
            current = _active(session, run_id, ref.brief.key)
            brief = ref.brief
            return BlindPair(
                index=index,
                total=len(pairs),
                brief=BlindBrief(brief.tema, brief.pilar, brief.objetivo, brief.canais),
                a=sides["a"],
                b=sides["b"],
                current=None if current is None else _as_current(current),
            )

    def pair_image(self, brand_id: str, run_id: str, index: int, side: Side) -> bytes:
        """Bytes JPEG da imagem final de um lado (o id do ativo nunca sai daqui)."""
        with self._session_factory() as session:
            run = _ready_run(session, brand_id, run_id)
            ref = _pick(_pairs(session, run), index)
            item = ref.items[path_for_side(run.seed, ref.brief.key, side)]
            asset_id = item.final_asset_id
        if asset_id is None:  # inalcancavel: so ha pares com os dois itens `succeeded`
            raise PairNotFound
        return self._library.read_asset(brand_id, asset_id).data

    def progress(self, brand_id: str, run_id: str) -> JudgingProgress:
        """Pares comparaveis e quantos ja tem resposta."""
        with self._session_factory() as session:
            run = _ready_run(session, brand_id, run_id)
            pairs = _pairs(session, run)
            judged = sum(1 for ref in pairs if _active(session, run_id, ref.brief.key))
            return JudgingProgress(len(pairs), judged)

    def record(
        self,
        brand_id: str,
        run_id: str,
        index: int,
        answer: JudgementInput,
        *,
        seconds_spent: int,
        replaces_id: str | None,
        actor: str,
    ) -> str:
        """Grava a resposta (ou a correcao) do par; devolve o id da linha nova.

        Raises:
            RunNotReady, PairNotFound, AlreadyRevealed, StaleJudgement.
        """
        with self._session_factory.begin() as session:
            run = _ready_run(session, brand_id, run_id, lock=True)
            if run.status == "revealed":
                raise AlreadyRevealed
            ref = _pick(_pairs(session, run), index)
            current = _active(session, run_id, ref.brief.key)
            current_id = None if current is None else current.id
            if replaces_id != current_id:
                raise StaleJudgement
            row = EvalJudgement(
                brand_id=brand_id,
                run_id=run_id,
                brief_key=ref.brief.key,
                side_a_path=side_a_path(run.seed, ref.brief.key),
                better=answer.better,
                errors_a=list(answer.errors_a),
                errors_b=list(answer.errors_b),
                edit_a=answer.edit_a,
                edit_b=answer.edit_b,
                note=answer.note,
                seconds_spent=seconds_spent,
                decided_by=actor,
                decided_at=self._clock.now(),
                supersedes_id=current_id,
            )
            session.add(row)
            session.flush()
            return row.id

    def reveal(self, brand_id: str, run_id: str, actor: str) -> None:
        """Revela a corrida; `RevealBlocked` com os pares em falta. Idempotente se ja revelada."""
        with self._session_factory.begin() as session:
            run = _ready_run(session, brand_id, run_id, lock=True)
            if run.status == "revealed":
                return
            pairs = _pairs(session, run)
            missing = [
                ref.index for ref in pairs if _active(session, run_id, ref.brief.key) is None
            ]
            if missing:
                raise RevealBlocked(missing)
            now = self._clock.now()
            run.status = "revealed"
            run.revealed_at = now
            session.add(
                AuditLog(
                    actor=actor,
                    action="eval_run_revealed",
                    subject_type="eval_run",
                    subject_id=run_id,
                    detail={"pairs": len(pairs)},
                    at=now,
                )
            )


def _ready_run(session: Session, brand_id: str, run_id: str, *, lock: bool = False) -> EvalRun:
    statement = select(EvalRun).where(EvalRun.id == run_id, EvalRun.brand_id == brand_id)
    if lock:
        statement = statement.with_for_update()
    run = session.scalar(statement)
    if run is None or run.status == "running":
        raise RunNotReady
    return run


def _pairs(session: Session, run: EvalRun) -> list[_PairRef]:
    """Pares comparaveis, pela ordem do conjunto: briefs com os dois itens `succeeded`."""
    items: dict[str, dict[str, EvalItem]] = {}
    for item in session.scalars(select(EvalItem).where(EvalItem.run_id == run.id)):
        if item.status == "succeeded":
            items.setdefault(item.brief_key, {})[item.path] = item
    pairs: list[_PairRef] = []
    for raw in run.briefs:
        brief = FixedBrief.from_json(raw)
        both = items.get(brief.key, {})
        if len(both) == 2:
            pairs.append(_PairRef(len(pairs) + 1, brief, both))
    return pairs


def _pick(pairs: list[_PairRef], index: int) -> _PairRef:
    if not 1 <= index <= len(pairs):
        raise PairNotFound
    return pairs[index - 1]


def _active(session: Session, run_id: str, brief_key: str) -> EvalJudgement | None:
    """A resposta do par que nenhuma outra substitui."""
    newer = aliased(EvalJudgement)
    statement = select(EvalJudgement).where(
        EvalJudgement.run_id == run_id,
        EvalJudgement.brief_key == brief_key,
        ~exists().where(newer.supersedes_id == EvalJudgement.id),
    )
    return session.scalar(statement)


def _snapshot(item: EvalItem) -> dict[str, object]:
    return cast(dict[str, object], item.revision_snapshot or {})


def _as_current(row: EvalJudgement) -> CurrentAnswer:
    return CurrentAnswer(
        id=row.id,
        better=row.better,
        errors_a=tuple(row.errors_a),
        errors_b=tuple(row.errors_b),
        edit_a=row.edit_a,
        edit_b=row.edit_b,
        note=row.note,
    )


def active_judgements(session: Session, run_id: str) -> list[EvalJudgement]:
    """Respostas ativas de uma corrida (uma por par respondido)."""
    newer = aliased(EvalJudgement)
    statement = select(EvalJudgement).where(
        EvalJudgement.run_id == run_id,
        ~exists().where(newer.supersedes_id == EvalJudgement.id),
    )
    return list(session.scalars(statement.order_by(EvalJudgement.brief_key)))
