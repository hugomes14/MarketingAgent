"""Montagem partilhada pelos testes das corridas de avaliacao (Plano 2C, T5).

Sobre o `ContentRig` (motor falso, base de teste, kit e foto): um conjunto de briefs escrito
numa pasta temporaria, o `EvalContext` e o `EvalRunService` reais. O guiao do cliente falso e
partilhado pelos dois percursos, por ordem de execucao: use `simple_package`/`package_pass`.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import yaml
from content_rig import ContentRig, Factory
from sqlalchemy import func, select

from marketing_agent.budget.controller import BudgetController
from marketing_agent.budget.pricing import PriceBook
from marketing_agent.db.models import EvalItem, EvalRun, Job
from marketing_agent.evals.context import EvalContext
from marketing_agent.evals.runs import EvalRunService
from marketing_agent.jobs.queue import JobQueue
from marketing_agent.kb.service import NewKbEntry

MakeEvalRig = Callable[..., "EvalRig"]  # tipo da fixture `make_eval_rig` (tests/evals/conftest.py)
PILAR = "Producao artesanal"
SET_NAME = "conj"
CEILING = Decimal("1.50")


def brief(key: str, **changes: object) -> dict[str, object]:
    """Um brief do conjunto (forma de `EvalBrief`), valido para o rig por omissao."""
    base: dict[str, object] = {
        "key": key, "tema": f"Tema {key}", "pilar": PILAR, "objetivo": "Mensagens",
        "canais": ["instagram"], "kb_refs": [], "tags": [],
    }  # fmt: skip
    return {**base, **changes}


def write_set(
    directory: Path,
    briefs: Sequence[dict[str, object]],
    *,
    name: str = SET_NAME,
    version: int = 1,
) -> None:
    """Escreve `<directory>/<name>.yaml`."""
    directory.mkdir(parents=True, exist_ok=True)
    document = {"name": name, "version": version, "briefs": list(briefs)}
    (directory / f"{name}.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")


@dataclass
class EvalRig:
    """Servico de corridas e contexto sobre o rig do conteudo."""

    content: ContentRig
    context: EvalContext
    service: EvalRunService
    budget: BudgetController
    evals_dir: Path

    @property
    def factory(self) -> Factory:
        return self.content.factory

    @property
    def brand_id(self) -> str:
        return self.content.brand_id

    def start(self, set_name: str = SET_NAME, actor: str = "ana") -> str:
        """Estima e inicia uma corrida com o limite superior de agora."""
        estimate = self.service.estimate(self.brand_id, set_name)
        return self.service.start_run(self.brand_id, set_name, estimate.upper_bound_usd, actor)

    def items(self, run_id: str) -> list[EvalItem]:
        with self.factory() as session:
            statement = select(EvalItem).where(EvalItem.run_id == run_id)
            return list(session.scalars(statement.order_by(EvalItem.brief_key, EvalItem.path)))

    def item(self, item_id: str) -> EvalItem:
        with self.factory() as session:
            return session.get_one(EvalItem, item_id)

    def run(self, run_id: str) -> EvalRun:
        with self.factory() as session:
            return session.get_one(EvalRun, run_id)

    def job_of(self, item: EvalItem) -> Job:
        assert item.job_id is not None
        with self.factory() as session:
            return session.get_one(Job, item.job_id)

    def add_price_item(self, key: str = "novo", valid_until: date | None = None) -> str:
        """Um item de preco criado agora (depois do inicio da corrida, nos testes)."""
        entry = NewKbEntry(
            kind="preco",
            key=key,
            title="Preco novo",
            body="Bolo novo por 99 EUR.",
            price_amount=Decimal("99.00"),
            price_currency="EUR",
            valid_from=date(2026, 11, 1),
            valid_until=valid_until or date(2026, 12, 1),
            source_kind="dito_pelo_responsavel",
        )
        return self.content.kb.add(self.brand_id, entry, "ana").id

    def add_style_example(self) -> str:
        """Um exemplo de feedback `estilo` ligado a uma revisao semeada fora do pipeline."""
        post_id = self.content.seed_post(f"2026-W46-{datetime.now(UTC).timestamp()}")
        revision_id = self.content.add_owner_revision(post_id)
        with self.factory.begin() as session:
            example_id = self.content.feedback.record_in(
                session,
                brand_id=self.brand_id,
                post_id=post_id,
                revision_id=revision_id,
                choice="estilo",
                before_text="Antes do responsavel.",
                after_text="Depois do responsavel.",
                note=None,
                expires_at=None,
                actor="ana",
            )
        assert example_id is not None
        return example_id


def build_eval_rig(
    content: ContentRig,
    evals_dir: Path,
    *,
    briefs: Sequence[dict[str, object]] | None = None,
    ceiling: Decimal = CEILING,
    check_routes: Callable[[], None] | None = None,
    new_seed: Callable[[], int] | None = None,
    queue: JobQueue | None = None,
) -> EvalRig:
    """Escreve o conjunto `conj` (2 briefs por omissao) e liga o servico de corridas."""
    write_set(evals_dir, briefs if briefs is not None else [brief("b1"), brief("b2")])
    context = EvalContext(
        content.factory, content.store, content.strategy, content.kb, content.feedback
    )
    budget = BudgetController(
        content.factory, PriceBook(content.factory), content.clock, Decimal(20), (50, 80, 100)
    )
    extra = {} if new_seed is None else {"new_seed": new_seed}
    service = EvalRunService(
        content.factory,
        context,
        budget,
        queue or content.queue,
        content.clock,
        evals_dir,
        ceiling,
        check_routes or (lambda: None),
        **extra,
    )
    return EvalRig(content, context, service, budget, evals_dir)


def count(factory: Factory, *models: type) -> list[int]:
    """Numero de linhas de cada modelo."""
    with factory() as session:
        return [session.scalar(select(func.count()).select_from(model)) or 0 for model in models]
