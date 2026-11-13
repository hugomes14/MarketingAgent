"""Contrato do relatorio revelado e notas metodologicas sempre presentes."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

LIMITACOES = (
    "Poucos briefs: a amostra é pequena e o intervalo de incerteza pode ser largo.",
    "Um só avaliador: os resultados refletem o julgamento de uma pessoa.",
    "Sem causalidade: esta comparação não isola a contribuição de cada papel.",
)
PERGUNTA_D35 = (
    "Os papéis especializados melhoram o resultado o suficiente para pagar o custo? "
    "Que papéis quer manter, simplificar ou retirar?"
)

# Correspondencia explicita, descritiva: nao e uma medida de verdade factual nem causalidade.
OWNER_TO_REVIEWER: dict[str, frozenset[str]] = {
    "facto_errado": frozenset({"alegacao_sem_apoio", "preco_sem_validade", "promessa_indevida"}),
    "alegacao_sem_apoio": frozenset({"alegacao_sem_apoio"}),
    "voz_errada": frozenset({"voz_incoerente"}),
    "imagem_inadequada": frozenset(
        {
            "imagem_inadequada",
            "pessoa_ou_produto_gerado",
            "texto_ilegivel",
            "marca_incoerente",
            "alt_text_inadequado",
        }
    ),
    "outro": frozenset({"outro"}),
}


@dataclass(frozen=True)
class ReviewerAgreement:
    """Erros do responsavel no lado especializado assinalados em qualquer volta do Revisor."""

    matched: int
    total: int

    @property
    def ratio(self) -> Decimal | None:
        """Fracao, ou desconhecida quando nao se marcaram erros no lado especializado."""
        return Decimal(self.matched) / self.total if self.total else None


@dataclass(frozen=True)
class PathReport:
    """Medidas por percurso; custos/falhas incluem itens excluidos do julgamento."""

    path: str
    packages: int
    failures: int
    errors: dict[str, int]
    edit_minutes: dict[str, int]
    cost_mean_usd: Decimal
    cost_max_usd: Decimal
    text_mean_usd: Decimal
    text_max_usd: Decimal
    image_mean_usd: Decimal
    image_max_usd: Decimal
    latency_mean_ms: Decimal | None
    latency_max_ms: int | None
    invalid_outputs: int
    review_rounds: int
    pending_usd: Decimal = Decimal(0)
    unknown_usd: Decimal = Decimal(0)
    unsettled_reservations: int = 0
    latency_measured_packages: int = 0
    timing_incomplete_packages: int = 0
    latency_partial_ms: int | None = None


@dataclass(frozen=True)
class DecisionView:
    """Decisao humana historica; manter varias decisoes do mesmo papel conserva o rasto."""

    role: str
    decision: str
    justificacao: str
    actor: str
    at: datetime


@dataclass(frozen=True)
class EvalReport:
    """Resultados agregados apos revelacao; nunca inclui uma recomendacao automatica."""

    run_id: str
    set_name: str
    set_version: int
    pairs: int
    excluded: int
    wins: int
    losses: int
    ties: int
    interval: tuple[float, float] | None
    paths: tuple[PathReport, ...]
    estimate_usd: Decimal
    actual_usd: Decimal
    reviewer_agreement: ReviewerAgreement
    decisions: tuple[DecisionView, ...]
    limitations: tuple[str, ...] = LIMITACOES
    question: str = PERGUNTA_D35
    pending_usd: Decimal = Decimal(0)
    unknown_usd: Decimal = Decimal(0)
    unsettled_reservations: int = 0

    @property
    def accounting_incomplete(self) -> bool:
        """Mesmo uma reserva de valor zero pode ter desfecho ainda desconhecido."""
        return self.unsettled_reservations > 0
