"""Montagem dos testes da avaliacao (2C): servicos reais, base de teste e um LLM falso por papel.

Os jobs de uma corrida nascem na mesma transacao (mesmo `created_at`), por isso a ordem de
reclamacao entre eles nao e fixa. O `RoleLlm` responde pelo papel (reconhecido pelo prompt de
sistema), com um guiao por papel e uma resposta de omissao valida, e nao pela ordem global.
"""

import itertools
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from content_rig import VALID_FROM, Factory, _activate_strategy, _seed_media
from llm_capabilities import VISION_CAPABILITIES
from sqlalchemy import select

from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.budget.controller import BudgetController
from marketing_agent.budget.pricing import (
    ImagePrice,
    ModelPrice,
    PriceBook,
    PriceTier,
    Usage,
    sync_model_prices,
)
from marketing_agent.clock import FixedClock
from marketing_agent.config import RoleRoute
from marketing_agent.content.specs import load_content_specs
from marketing_agent.db.models import EvalItem, EvalRun, Job
from marketing_agent.evals.handler import EvalHandlerDeps, EvalPackageHandler
from marketing_agent.evals.judging import JudgingService
from marketing_agent.evals.runs import EvalRunDeps, EvalRunService
from marketing_agent.evals.simple_path import load_simple_spec
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.harness.engine import AgentEngine
from marketing_agent.images.factory import ImageBackend
from marketing_agent.images.generation import FAKE_IMAGE_MODEL, FakeImageGenerator
from marketing_agent.jobs.queue import JobQueue
from marketing_agent.jobs.worker import Worker
from marketing_agent.kb.service import KbService, NewKbEntry
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.router import LlmRouter
from marketing_agent.llm.types import LlmRequest, LlmResponse
from marketing_agent.media.brand_kit import BrandKitService
from marketing_agent.media.library import MediaLibrary
from marketing_agent.media.store import LocalMediaStore
from marketing_agent.startup import content_job_lease
from marketing_agent.strategy.service import StrategyService

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "evals"
SIZE = "1024x1536"
ACTOR = "ana"
_ids = itertools.count(1)
# Inicio do prompt de sistema de cada papel (`agents/<papel>/prompt.md`).
ROLE_PROMPTS = {
    "copywriter": "Es o Copywriter",
    "designer": "Es o Designer",
    "revisor": "Es o Revisor",
    "simples": "Es o criador de conteudos",
}


def _default(role: str) -> list[LlmResponse]:
    if role == "copywriter":
        return list(fake_scenarios.caption_draft())  # type: ignore[arg-type]
    if role == "designer":
        return list(fake_scenarios.visual_draft())  # type: ignore[arg-type]
    if role == "revisor":
        return list(fake_scenarios.review("aprovar", imagem_verificada=True))  # type: ignore[arg-type]
    return list(fake_scenarios.simple_package())  # type: ignore[arg-type]


@dataclass
class RoleLlm:
    """Cliente falso que responde pelo papel; `scripts[papel]` tem prioridade sobre a omissao."""

    capabilities: frozenset[str] = VISION_CAPABILITIES
    scripts: dict[str, list[LlmResponse | Exception]] = field(
        default_factory=lambda: defaultdict(list)
    )
    requests: list[tuple[str, LlmRequest]] = field(default_factory=list)
    before: Callable[[str], None] | None = None

    def complete(self, request: LlmRequest) -> LlmResponse:
        role = next(
            (name for name, start in ROLE_PROMPTS.items() if request.system.startswith(start)),
            "desconhecido",
        )
        self.requests.append((role, request))
        if self.before is not None:
            self.before(role)
        queue = self.scripts[role]
        item = queue.pop(0) if queue else _default(role)[0]
        if isinstance(item, Exception):
            raise item
        return LlmResponse(
            text=item.text,
            usage=Usage(input_tokens=100, output_tokens=50),
            provider_request_id=f"eval-req-{next(_ids)}",
            resolved_model="fake-1",
            stop_reason=item.stop_reason,
        )

    def count(self, role: str) -> int:
        return sum(1 for name, _ in self.requests if name == role)


@dataclass
class EvalRig:
    """Servicos da avaliacao e do lote sobre a base de teste e o `RoleLlm`."""

    factory: Factory
    clock: FixedClock
    brand_id: str
    llm: RoleLlm
    generator: FakeImageGenerator
    runs: EvalRunService
    handler: EvalPackageHandler
    queue: JobQueue
    worker: Worker
    kb: KbService
    feedback: FeedbackService
    library: MediaLibrary
    kits: BrandKitService
    photo_id: str
    engine: AgentEngine
    judging: JudgingService

    def add_price_item(self, key: str = "pao", amount: str = "12.50") -> str:
        entry = NewKbEntry(
            kind="preco",
            key=key,
            title="Pao",
            body="Pao de forno a lenha.",
            price_amount=Decimal(amount),
            price_currency="EUR",
            valid_from=date(2026, 11, 1),
            valid_until=date(2026, 12, 1),
            source_kind="dito_pelo_responsavel",
        )
        return self.kb.add(self.brand_id, entry, ACTOR).id

    def start(self, set_name: str = "briefs_teste") -> str:
        estimate = self.runs.estimate(self.brand_id, set_name)
        return self.runs.start_run(self.brand_id, set_name, estimate.upper_bound_usd, ACTOR)

    def drain(self, limit: int = 40) -> int:
        processed = 0
        while self.worker.run_once():
            processed += 1
            assert processed < limit, "a fila nao esvazia"
        return processed

    def items(self, run_id: str) -> list[EvalItem]:
        with self.factory() as session:
            statement = select(EvalItem).where(EvalItem.run_id == run_id)
            return list(session.scalars(statement.order_by(EvalItem.brief_key, EvalItem.path)))

    def run(self, run_id: str) -> EvalRun:
        with self.factory() as session:
            return session.get_one(EvalRun, run_id)

    def job(self, job_id: str) -> Job:
        with self.factory() as session:
            return session.get_one(Job, job_id)


class _NoPipeline:
    """O worker so recebe jobs de avaliacao nestes testes; os de post falham se aparecerem."""

    def generate(self, job: object) -> str:
        raise AssertionError("job de post inesperado")

    def review_manual(self, job: object) -> str:
        raise AssertionError("job de post inesperado")

    def fail_job(self, job: object, message: str, retry: bool) -> None:
        raise AssertionError("job de post inesperado")


def build_eval_rig(
    factory: Factory,
    clock: FixedClock,
    brand_id: str,
    media_dir: Path,
    *,
    image_price: str = "0",
    text_price_per_million: str = "0",
    package_max_cost_usd: Decimal = Decimal("1.50"),
    worker_max_cost_usd: Decimal | None = None,
    evals_dir: Path = FIXTURES,
    pipeline: object | None = None,
) -> EvalRig:
    """Marca com perfil, estrategia ativa, kit e uma foto; corridas e worker prontos."""
    rate = Decimal(text_price_per_million)
    tier = PriceTier(0, rate, rate)
    per_image = tuple(ImagePrice(q, SIZE, Decimal(image_price)) for q in ("low", "medium", "high"))
    sync_model_prices(
        factory,
        [
            ModelPrice("fake", "fake-1", "v1", (tier,), valid_from=VALID_FROM),
            ModelPrice("fake", FAKE_IMAGE_MODEL, "v1", (tier,), VALID_FROM, per_image),
        ],
    )
    llm = RoleLlm()
    route = RoleRoute(provider="fake", model_id="fake-1", capabilities=VISION_CAPABILITIES)
    text_route = route.model_copy(update={"capabilities": frozenset({"text", "json_schema"})})
    routes = {"copywriter": text_route, "designer": text_route, "revisor": route}
    budget = BudgetController(factory, PriceBook(factory), clock, Decimal(20), (50, 80, 100))
    store = ArtifactStore(factory, clock)
    generator = FakeImageGenerator()
    engine = AgentEngine(
        factory,
        LlmRouter(routes, {"fake": llm}),  # type: ignore[dict-item]
        budget,
        store,
        clock,
        sleep=lambda seconds: None,
        images=ImageBackend(generator, "fake", FAKE_IMAGE_MODEL, "medium"),
    )
    strategy = StrategyService(factory, engine, store, clock)
    kb = KbService(factory, store, clock)
    feedback = FeedbackService(factory, clock)
    queue = JobQueue(factory, clock, content_job_lease(time_multiplier=1))
    library = MediaLibrary(factory, LocalMediaStore(media_dir), clock)
    kits = BrandKitService(factory, library, clock)
    photo_id = _seed_media(library, kits, brand_id, True)
    _activate_strategy(store, strategy, brand_id)
    runs = EvalRunService(
        factory,
        EvalRunDeps(store, strategy, kb, feedback, library, kits, budget, queue),
        clock,
        evals_dir=evals_dir,
        package_max_cost_usd=package_max_cost_usd,
    )
    handler = EvalPackageHandler(
        factory,
        EvalHandlerDeps(engine, queue, library, kits, load_content_specs(), load_simple_spec()),
        clock,
        image_quality="medium",
        # O worker pode correr com um `.env` diferente do processo que criou a corrida.
        package_max_cost_usd=worker_max_cost_usd or package_max_cost_usd,
    )
    worker = Worker(
        queue,
        pipeline or _NoPipeline(),  # type: ignore[arg-type]
        "w1",
        evals=handler,
        reconcile_evals=runs.reconcile,
        sleep=lambda seconds: None,
    )
    return EvalRig(
        factory, clock, brand_id, llm, generator, runs, handler, queue, worker, kb, feedback,
        library, kits, photo_id, engine, JudgingService(factory, library, clock),
    )  # fmt: skip
