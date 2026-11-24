"""Relatorio de uma corrida revelada e as decisoes por papel (spec 2C, secao 6; D35).

O relatorio NAO decide: junta vitorias, erros, minutos, custo, latencia e concordancia do
Revisor, diz sempre as limitacoes e termina com a pergunta do D35. A decisao (manter,
simplificar ou retirar cada papel) e do responsavel e fica registada (`role_decision`, so se
acrescenta; a mais recente de cada papel e a que vale).
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import Clock
from marketing_agent.content.package_costs import request_run_ids
from marketing_agent.db.models import (
    DECIDABLE_ROLES,
    EDIT_MINUTES,
    EVAL_ERROR_KINDS,
    EVAL_PATHS,
    ROLE_DECISIONS,
    AuditLog,
    CostEntry,
    EvalItem,
    EvalJudgement,
    EvalRun,
    RoleDecision,
)
from marketing_agent.evals.judging import active_judgements
from marketing_agent.evals.stats import wilson_interval

SPECIALIZED: Final = "especializado"
MAX_JUSTIFICATION_CHARS: Final = 2000
# Texto mostrado ao responsavel (painel e Markdown), por isso em portugues com acentos.
LIMITACOES: Final[tuple[str, ...]] = (
    "Poucos briefs: o intervalo de incerteza é largo e um só par muda muito o resultado.",
    "Um só avaliador (o responsável): o gosto de uma pessoa não representa o público.",
    "Sem causalidade: os resultados descrevem esta corrida, com estes modelos e este contexto.",
)
PERGUNTA_D35: Final = (
    "Que papéis trazem benefício em qualidade, custo ou esforço humano que justifique mantê-los? "
    "Para cada um (Copywriter, Designer, Revisor): manter, simplificar ou retirar?"
)
# Erros marcados pelo responsavel -> violacoes do Revisor que contam como "o Revisor viu".
OWNER_TO_REVIEWER: Final[Mapping[str, frozenset[str]]] = {
    "facto_errado": frozenset({"alegacao_sem_apoio", "preco_sem_validade", "promessa_indevida"}),
    "alegacao_sem_apoio": frozenset({"alegacao_sem_apoio", "preco_sem_validade"}),
    "voz_errada": frozenset({"voz_incoerente"}),
    "imagem_inadequada": frozenset(
        {
            "imagem_inadequada",
            "pessoa_ou_produto_gerado",
            "marca_incoerente",
            "texto_ilegivel",
            "alt_text_inadequado",
        }
    ),
    "outro": frozenset(
        {"outro", "fora_do_tema", "limite_canal", "dados_pessoais", "instrucao_suspeita"}
    ),
}


class RevealPending(Exception):
    """A corrida ainda nao foi revelada (ou nao existe nesta marca)."""


class InvalidDecision(ValueError):
    """Papel, decisao ou justificacao invalidos (mensagem fixa)."""


@dataclass(frozen=True)
class PathStats:
    """Medidas de um percurso na corrida."""

    path: str
    packages: int
    failed: int
    errors: Mapping[str, int]
    edit_minutes: Mapping[str, int]
    cost_mean_usd: Decimal
    cost_max_usd: Decimal
    image_cost_usd: Decimal
    latency_mean_ms: int
    latency_max_ms: int
    invalid_outputs: int
    review_rounds: int


@dataclass(frozen=True)
class DecisionView:
    """Decisao em vigor de um papel."""

    role: str
    decision: str
    justificacao: str
    decided_by: str
    decided_at: datetime


@dataclass(frozen=True)
class EvalReport:
    """Tudo o que o relatorio mostra; nada aqui recomenda uma decisao."""

    run_id: str
    set_name: str
    set_version: int
    pairs: int
    wins: int
    losses: int
    ties: int
    interval: tuple[float, float] | None
    paths: tuple[PathStats, ...]
    estimate_usd: Decimal
    actual_usd: Decimal
    reviewer_agreement: tuple[int, int] | None
    decisions: tuple[DecisionView, ...]
    limitations: tuple[str, ...] = LIMITACOES
    question: str = PERGUNTA_D35
    seconds_per_pair_mean: int = 0
    notes: tuple[str, ...] = ()


class ReportService:
    """Constroi o relatorio de uma corrida revelada e regista decisoes por papel."""

    def __init__(self, session_factory: sessionmaker[Session], clock: Clock) -> None:
        """Recebe as sessoes e o relogio."""
        self._session_factory = session_factory
        self._clock = clock

    def build(self, brand_id: str, run_id: str) -> EvalReport:
        """Relatorio da corrida; `RevealPending` antes da revelacao."""
        with self._session_factory() as session:
            run = _revealed_run(session, brand_id, run_id)
            items = list(session.scalars(select(EvalItem).where(EvalItem.run_id == run_id)))
            judgements = active_judgements(session, run_id)
            by_key = {(item.brief_key, item.path): item for item in items}
            wins, losses, ties = _outcomes(judgements)
            return EvalReport(
                run_id=run.id,
                set_name=run.set_name,
                set_version=run.set_version,
                pairs=len(judgements),
                wins=wins,
                losses=losses,
                ties=ties,
                interval=wilson_interval(wins, wins + losses),
                paths=tuple(
                    _path_stats(session, brand_id, path, items, judgements) for path in EVAL_PATHS
                ),
                estimate_usd=run.estimate_usd,
                actual_usd=sum((item.cost_usd for item in items), Decimal(0)),
                reviewer_agreement=_agreement(judgements, by_key),
                decisions=tuple(_decisions(session, run_id)),
                seconds_per_pair_mean=_mean_int([j.seconds_spent for j in judgements]),
                notes=tuple(j.note for j in judgements if j.note),
            )

    def record_role_decision(
        self,
        brand_id: str,
        run_id: str,
        role: str,
        decision: str,
        justificacao: str,
        actor: str,
    ) -> str:
        """Acrescenta a decisao de um papel (depois da revelacao); devolve o id."""
        now = self._clock.now()
        with self._session_factory.begin() as session:
            _revealed_run(session, brand_id, run_id)  # antes dos campos: o passo em falta primeiro
            if role not in DECIDABLE_ROLES or decision not in ROLE_DECISIONS:
                raise InvalidDecision("papel ou decisao desconhecidos")
            text = justificacao.strip()
            if not text or len(text) > MAX_JUSTIFICATION_CHARS:
                raise InvalidDecision("justificacao vazia ou demasiado longa")
            row = RoleDecision(
                brand_id=brand_id,
                run_id=run_id,
                role=role,
                decision=decision,
                justificacao=text,
                decided_by=actor,
                decided_at=now,
            )
            session.add(row)
            session.flush()
            session.add(
                AuditLog(
                    actor=actor,
                    action="role_decision",
                    subject_type="eval_run",
                    subject_id=run_id,
                    detail={"role": role, "decision": decision},
                    at=now,
                )
            )
            return row.id


def _revealed_run(session: Session, brand_id: str, run_id: str) -> EvalRun:
    run = session.scalar(select(EvalRun).where(EvalRun.id == run_id, EvalRun.brand_id == brand_id))
    if run is None or run.status != "revealed":
        raise RevealPending
    return run


def _specialized_side(judgement: EvalJudgement) -> Literal["a", "b"]:
    return "a" if judgement.side_a_path == SPECIALIZED else "b"


def _outcomes(judgements: Sequence[EvalJudgement]) -> tuple[int, int, int]:
    """Vitorias, derrotas e empates do especializado."""
    wins = losses = ties = 0
    for judgement in judgements:
        if judgement.better == "empate":
            ties += 1
        elif judgement.better == _specialized_side(judgement):
            wins += 1
        else:
            losses += 1
    return wins, losses, ties


def _side_of(judgement: EvalJudgement, path: str) -> Literal["a", "b"]:
    specialized = _specialized_side(judgement)
    if path == SPECIALIZED:
        return specialized
    return "b" if specialized == "a" else "a"


def _path_stats(
    session: Session,
    brand_id: str,
    path: str,
    items: Sequence[EvalItem],
    judgements: Sequence[EvalJudgement],
) -> PathStats:
    mine = [item for item in items if item.path == path]
    errors: Counter[str] = Counter({kind: 0 for kind in EVAL_ERROR_KINDS})
    minutes: Counter[str] = Counter({value: 0 for value in EDIT_MINUTES})
    for judgement in judgements:
        side = _side_of(judgement, path)
        errors.update(judgement.errors_a if side == "a" else judgement.errors_b)
        minutes[judgement.edit_a if side == "a" else judgement.edit_b] += 1
    costs = [item.cost_usd for item in mine]
    latencies = [item.latency_ms or 0 for item in mine if item.status == "succeeded"]
    return PathStats(
        path=path,
        packages=len(mine),
        failed=sum(1 for item in mine if item.status == "failed"),
        errors=dict(errors),
        edit_minutes=dict(minutes),
        cost_mean_usd=_mean_money(costs),
        cost_max_usd=max(costs, default=Decimal(0)),
        image_cost_usd=_image_cost(session, brand_id, mine),
        latency_mean_ms=_mean_int(latencies),
        latency_max_ms=max(latencies, default=0),
        invalid_outputs=sum(item.invalid_outputs for item in mine),
        review_rounds=sum(item.review_rounds for item in mine),
    )


def _image_cost(session: Session, brand_id: str, items: Sequence[EvalItem]) -> Decimal:
    total = Decimal(0)
    for item in items:
        runs = request_run_ids(brand_id, item.id, "eval")
        value = session.scalar(
            select(func.coalesce(func.sum(CostEntry.cost_usd), Decimal(0))).where(
                CostEntry.run_id.in_(runs), CostEntry.units.has_key("images")
            )
        )
        total += Decimal(value or 0)
    return total


def _agreement(
    judgements: Sequence[EvalJudgement], items: Mapping[tuple[str, str], EvalItem]
) -> tuple[int, int] | None:
    """(erros do lado especializado que o Revisor assinalou em alguma volta, total desses erros)."""
    agreed = total = 0
    for judgement in judgements:
        item = items.get((judgement.brief_key, SPECIALIZED))
        if item is None:
            continue
        side = _specialized_side(judgement)
        marked = judgement.errors_a if side == "a" else judgement.errors_b
        seen = set(_all_violations(item))
        for kind in marked:
            total += 1
            agreed += bool(OWNER_TO_REVIEWER.get(kind, frozenset()) & seen)
    return None if total == 0 else (agreed, total)


def _all_violations(item: EvalItem) -> list[str]:
    snapshot = item.revision_snapshot or {}
    review = cast(Mapping[str, object], snapshot.get("review") or {})
    return cast(list[str], review.get("all_violations") or [])


def _decisions(session: Session, run_id: str) -> list[DecisionView]:
    """A decisao mais recente de cada papel desta corrida."""
    rows = session.scalars(
        select(RoleDecision)
        .where(RoleDecision.run_id == run_id)
        .order_by(RoleDecision.decided_at.desc(), RoleDecision.id.desc())
    )
    latest: dict[str, DecisionView] = {}
    for row in rows:
        latest.setdefault(
            row.role,
            DecisionView(row.role, row.decision, row.justificacao, row.decided_by, row.decided_at),
        )
    return [latest[role] for role in DECIDABLE_ROLES if role in latest]


def _mean_money(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    return (sum(values, Decimal(0)) / len(values)).quantize(Decimal("0.000001"))


def _mean_int(values: Sequence[int]) -> int:
    return round(sum(values) / len(values)) if values else 0
