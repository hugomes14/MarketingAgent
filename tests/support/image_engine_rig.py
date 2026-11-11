"""Motor montado com um gerador de imagens de teste e precos por imagem proprios."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from threading import Lock

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore, ArtifactType
from marketing_agent.budget.controller import BudgetController
from marketing_agent.budget.pricing import (
    ImagePrice,
    ModelPrice,
    PriceBook,
    PriceTier,
    sync_model_prices,
)
from marketing_agent.clock import FixedClock
from marketing_agent.config import RoleRoute
from marketing_agent.db.models import AgentRun, AgentStep, CostEntry, CostReservation
from marketing_agent.harness.engine import AgentEngine, Loop, RunContext, RunResult
from marketing_agent.harness.image_calls import ImageAllowance
from marketing_agent.harness.spec import AgentLimits, AgentSpec, LoopType
from marketing_agent.images.factory import ImageBackend
from marketing_agent.images.generation import (
    ImageGenerator,
    ImageQuality,
    ImageRequest,
    ImageResult,
)
from marketing_agent.images.prompts import PromptFixo, build_background_prompt
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.router import LlmRouter
from marketing_agent.llm.types import LlmResponse

VALID_FROM = datetime(2026, 11, 1, tzinfo=UTC)
SIZE = "1024x1536"
QUALITIES: tuple[ImageQuality, ...] = ("low", "medium", "high")


class ScriptedImageGenerator:
    """Gerador que devolve, por ordem, o proximo item do guiao (resultado ou excecao).

    Esgotado o guiao repete o ultimo item. `requests` regista todos os pedidos recebidos.
    """

    provider = "fake"

    def __init__(self, script: Sequence[ImageResult | BaseException]) -> None:
        self.requests: list[ImageRequest] = []
        self._script = list(script)
        self._lock = Lock()

    def generate(self, request: ImageRequest) -> ImageResult:
        with self._lock:
            self.requests.append(request)
            item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, BaseException):
            raise item
        return item


def make_spec(max_cost_usd: str = "0.50", max_steps: int = 10) -> AgentSpec:
    return AgentSpec(
        role="designer",
        name="Designer",
        version=1,
        loop_type=LoopType.STRUCTURED,
        prompt_file="p.md",
        output_schema="Out",
        required_capabilities=frozenset({"text", "json_schema"}),
        reads=[],
        writes=[ArtifactType.ESTRATEGIA],
        validators=[],
        limits=AgentLimits(
            max_steps=max_steps,
            max_tokens=100000,
            max_cost_usd=Decimal(max_cost_usd),
            max_duration_s=60,
        ),
        max_output_tokens=1000,
        budget_share_hint=Decimal("0.1"),
        escalation="diretor",
    )


def make_prompt(theme: str = "ceu de verao") -> PromptFixo:
    return build_background_prompt(theme, "aguarela")


class CallLoop:
    """Loop que corre `action(ctx)` e devolve o que ela devolver."""

    def __init__(self, action: Callable[[RunContext], object]) -> None:
        self._action = action

    def run(self, ctx: RunContext, payload: str) -> object:
        return self._action(ctx)


@dataclass
class ImageRig:
    """Motor sobre base de teste; `backend.generator` e o gerador sob observacao."""

    engine: AgentEngine
    backend: ImageBackend
    clock: FixedClock
    factory: sessionmaker[Session]
    brand_id: str
    sleeps: list[float] = field(default_factory=list)
    llm: FakeLlmClient = field(default_factory=lambda: FakeLlmClient([]))

    def run_loop[TIn, TOut](
        self,
        spec: AgentSpec,
        loop: Loop[TIn, TOut],
        payload: TIn,
        allowance: ImageAllowance | None = None,
    ) -> RunResult:
        """Corre um ciclo real (por exemplo o do Designer) com a quota de imagens dada."""
        return self.engine.run(
            spec, self.brand_id, "test", loop, payload, image_allowance=allowance
        )

    def run(
        self,
        action: Callable[[RunContext], object],
        allowance: ImageAllowance | None = None,
        spec: AgentSpec | None = None,
    ) -> RunResult:
        return self.engine.run(
            spec or make_spec(),
            self.brand_id,
            "test",
            CallLoop(action),
            "pedido",
            image_allowance=allowance,
        )

    def steps(self) -> list[AgentStep]:
        with self.factory() as session:
            return list(session.scalars(select(AgentStep).order_by(AgentStep.seq)))

    def reservations(self) -> list[CostReservation]:
        with self.factory() as session:
            stmt = (
                select(CostReservation)
                .join(AgentStep, CostReservation.step_id == AgentStep.id)
                .order_by(AgentStep.seq)
            )
            return list(session.scalars(stmt))

    def entries(self) -> list[CostEntry]:
        with self.factory() as session:
            return list(session.scalars(select(CostEntry)))

    def run_row(self, run_id: str) -> AgentRun:
        with self.factory() as session:
            return session.get_one(AgentRun, run_id)


def make_image_rig(
    factory: sessionmaker[Session],
    clock: FixedClock,
    brand_id: str,
    generator: ImageGenerator,
    *,
    price: str = "0",
    provider: str = "fake",
    model_id: str = "fake-image-1",
    with_backend: bool = True,
    with_image_prices: bool = True,
    llm_script: Sequence[LlmResponse | Exception] = (),
) -> ImageRig:
    """Sincroniza `price` USD por imagem para todas as qualidades e monta o motor.

    `llm_script` e o guiao do papel `designer` (modelo `fake-1`, preco 0); cada resposta deve ter
    `provider_request_id` proprio. Sem guiao, qualquer chamada de texto falha.
    """
    per_image = tuple(ImagePrice(q, SIZE, Decimal(price)) for q in QUALITIES if with_image_prices)
    tier = PriceTier(0, Decimal(0), Decimal(0))
    sync_model_prices(
        factory,
        [
            ModelPrice(provider, model_id, "vtest", (tier,), VALID_FROM, per_image),
            ModelPrice("fake", "fake-1", "vtest", (tier,), VALID_FROM),
        ],
    )
    route = RoleRoute(
        provider="fake", model_id="fake-1", capabilities=frozenset({"text", "json_schema"})
    )
    budget = BudgetController(factory, PriceBook(factory), clock, Decimal(20), (50, 80, 100))
    backend = ImageBackend(generator, provider, model_id, "medium")
    sleeps: list[float] = []
    llm = FakeLlmClient(llm_script)
    engine = AgentEngine(
        factory,
        LlmRouter({"designer": route}, {"fake": llm}),
        budget,
        ArtifactStore(factory, clock),
        clock,
        sleep=sleeps.append,
        images=backend if with_backend else None,
    )
    return ImageRig(engine, backend, clock, factory, brand_id, sleeps, llm)
