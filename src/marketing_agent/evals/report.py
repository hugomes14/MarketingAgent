"""Relatorio descritivo, acessivel so depois da revelacao; decisoes humanas acrescentadas."""

from collections import Counter
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import Clock
from marketing_agent.content.package_costs import family_costs_in
from marketing_agent.db.models import (
    EDIT_MINUTES,
    EVAL_ERROR_KINDS,
    EVAL_PATHS,
    AuditLog,
    EvalItem,
    EvalJudgement,
    RoleDecision,
)
from marketing_agent.evals.judging import current_answers_in, eligible_pairs_in
from marketing_agent.evals.progress import item_triggers
from marketing_agent.evals.report_types import (
    LIMITACOES,
    OWNER_TO_REVIEWER,
    PERGUNTA_D35,
    DecisionView,
    EvalReport,
    PathReport,
    ReviewerAgreement,
)
from marketing_agent.evals.snapshot import PackageSnapshot
from marketing_agent.evals.stats import wilson_interval
from marketing_agent.evals.views import require_run

__all__ = ["EvalReport", "LIMITACOES", "OWNER_TO_REVIEWER", "PERGUNTA_D35", "render_markdown"]


class RevealPending(Exception):
    """Relatorio e decisoes so estao disponiveis depois da revelacao explicita."""


class RoleDecisionInput(BaseModel):
    """Fronteira de escrita da decisao do responsavel (sem politica automatica)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Literal["copywriter", "designer", "revisor"]
    decision: Literal["manter", "simplificar", "retirar"]
    justificacao: str = Field(min_length=1, max_length=2000)

    @field_validator("justificacao")
    @classmethod
    def nonempty(cls, value: str) -> str:
        """Espacos em branco nao justificam uma decisao."""
        value = value.strip()
        if not value:
            raise ValueError("falta justificacao")
        return value


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / len(values) if values else Decimal(0)


def _answers_for_path(
    answers: list[EvalJudgement],
    path: str,
) -> tuple[dict[str, int], dict[str, int]]:
    errors: Counter[str] = Counter()
    minutes: Counter[str] = Counter()
    for answer in answers:
        is_a = answer.side_a_path == path
        errors.update(answer.errors_a if is_a else answer.errors_b)
        minutes.update([answer.edit_a if is_a else answer.edit_b])
    return (
        {kind: errors[kind] for kind in EVAL_ERROR_KINDS},
        {bucket: minutes[bucket] for bucket in EDIT_MINUTES},
    )


def _path_report(
    session: Session,
    path: str,
    items: list[EvalItem],
    answers: list[EvalJudgement],
) -> PathReport:
    selected = [item for item in items if item.path == path]
    costs = [item.cost_usd for item in selected]
    images = [
        family_costs_in(session, item.brand_id, item_triggers(item.id).family).image_usd
        for item in selected
    ]
    texts = [cost - image for cost, image in zip(costs, images, strict=True)]
    latencies = [item.latency_ms for item in selected if item.latency_ms is not None]
    errors, minutes = _answers_for_path(answers, path)
    return PathReport(
        path,
        len(selected),
        sum(item.status == "failed" for item in selected),
        errors,
        minutes,
        _mean(costs),
        max(costs, default=Decimal(0)),
        _mean(texts),
        max(texts, default=Decimal(0)),
        _mean(images),
        max(images, default=Decimal(0)),
        _mean([Decimal(ms) for ms in latencies]) if latencies else None,
        max(latencies, default=None),
        sum(item.invalid_outputs for item in selected),
        sum(item.review_rounds for item in selected),
    )


def _agreement(items: list[EvalItem], answers: list[EvalJudgement]) -> ReviewerAgreement:
    specialized = {item.brief_key: item for item in items if item.path == "especializado"}
    matched, total = 0, 0
    for answer in answers:
        errors = answer.errors_a if answer.side_a_path == "especializado" else answer.errors_b
        item = specialized[answer.brief_key]
        snapshot = PackageSnapshot.model_validate(item.revision_snapshot)
        violations = {v for review in snapshot.pareceres for v in review.violacoes}
        total += len(errors)
        matched += sum(bool(OWNER_TO_REVIEWER[error] & violations) for error in errors)
    return ReviewerAgreement(matched, total)


class ReportService:
    """Calcula resultados das respostas atuais; acrescenta decisoes sem alterar o pipeline."""

    def __init__(self, factory: sessionmaker[Session], clock: Clock) -> None:
        """Acesso a contabilidade real e relogio injetado para decisoes auditaveis."""
        self._factory = factory
        self._clock = clock

    def build(self, brand_id: str, run_id: str) -> EvalReport:
        """Nao fornece resultados parciais antes da revelacao, mesmo a chamadores internos."""
        with self._factory() as session:
            run = require_run(session, brand_id, run_id)
            if run.status != "revealed":
                raise RevealPending
            pairs = eligible_pairs_in(session, run)
            eligible = {brief.key for brief, _ in pairs}
            answers = [
                a for key, a in current_answers_in(session, run_id).items() if key in eligible
            ]
            wins = sum(
                a.better != "empate" and ((a.better == "a") == (a.side_a_path == "especializado"))
                for a in answers
            )
            ties = sum(a.better == "empate" for a in answers)
            losses = len(answers) - wins - ties
            items = list(session.scalars(select(EvalItem).where(EvalItem.run_id == run_id)))
            decisions = session.scalars(
                select(RoleDecision)
                .where(RoleDecision.run_id == run_id)
                .order_by(RoleDecision.decided_at, RoleDecision.id)
            )
            return EvalReport(
                run.id,
                run.set_name,
                run.set_version,
                len(pairs),
                len(run.briefs) - len(pairs),
                wins,
                losses,
                ties,
                wilson_interval(wins, wins + losses),
                tuple(_path_report(session, path, items, answers) for path in EVAL_PATHS),
                run.estimate_usd,
                sum((item.cost_usd for item in items), Decimal(0)),
                _agreement(items, answers),
                tuple(
                    DecisionView(d.role, d.decision, d.justificacao, d.decided_by, d.decided_at)
                    for d in decisions
                ),
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
        """Guarda uma decisao humana e auditoria, so depois da revelacao; nao executa codigo."""
        data = RoleDecisionInput(role=role, decision=decision, justificacao=justificacao)
        with self._factory.begin() as session:
            run = require_run(session, brand_id, run_id, lock=True)
            if run.status != "revealed":
                raise RevealPending
            row = RoleDecision(
                brand_id=brand_id,
                run_id=run_id,
                **data.model_dump(),
                decided_by=actor,
                decided_at=self._clock.now(),
            )
            session.add(row)
            session.flush()
            session.add(
                AuditLog(
                    actor=actor,
                    action="eval_role_decision",
                    subject_type="role_decision",
                    subject_id=row.id,
                    detail={"run_id": run_id, "role": data.role, "decision": data.decision},
                    at=self._clock.now(),
                )
            )
            return row.id


def render_markdown(report: EvalReport) -> str:
    """Exporta o mesmo contrato do HTML; importacao local evita ciclo com o renderizador."""
    from marketing_agent.evals.report_markdown import render_report

    return render_report(report)
