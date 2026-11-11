"""Montagem partilhada pelos testes do pipeline e do worker: motor falso, base e servicos reais.

O `HookedLlm` deixa o teste intervir a meio de uma geracao (por exemplo, gravar uma revisao do
responsavel entre a chamada ao Copywriter e a gravacao), que e onde moram as corridas.

O pacote e texto e imagem: o rig traz o kit de marca (logotipo sintetico), uma foto na
biblioteca (`rig.photo_id`) e um gerador de imagens falso com preco por imagem proprio
(`image_price`). Uma passagem do guiao e Copywriter, Designer e Revisor (`package_pass`).
"""

import itertools
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from image_factory import make_jpeg, make_png
from llm_capabilities import VISION_CAPABILITIES
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore, ArtifactType
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
from marketing_agent.config import Capability, RoleRoute
from marketing_agent.content.pipeline import ContentPipeline
from marketing_agent.db.base import new_id
from marketing_agent.db.models import AgentRun, CostEntry, Job, MediaAsset, Post, PostRevision
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.harness.engine import AgentEngine
from marketing_agent.images.factory import ImageBackend
from marketing_agent.images.generation import FAKE_IMAGE_MODEL, FakeImageGenerator, ImageGenerator
from marketing_agent.jobs.queue import GENERATE_POST_JOB, ClaimedJob, JobQueue
from marketing_agent.kb.service import KbService, NewKbEntry
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.router import LlmRouter
from marketing_agent.llm.types import LlmRequest, LlmResponse, TextPart
from marketing_agent.media.brand_kit import BrandKitInput, BrandKitService
from marketing_agent.media.library import MediaLibrary, PhotoUpload
from marketing_agent.media.store import LocalMediaStore
from marketing_agent.startup import content_job_lease
from marketing_agent.strategy.service import StrategyService

Factory = sessionmaker[Session]
SIZE = "1024x1536"
Script = list[LlmResponse | Exception]
WEEK = "2026-W46"  # segunda 2026-11-09; o relogio dos testes e quinta 2026-11-05
POST_DATE = date(2026, 11, 9)
VALID_FROM = datetime(2026, 11, 1, tzinfo=UTC)
PROFILE_PAYLOAD = {
    "topicos": {"o_que_vende": {"texto": "Pao artesanal.", "origem": "dito_pelo_responsavel"}},
    "lacunas_abertas": ["concorrentes"],
    "suposicoes": [],
}
_request_ids = itertools.count(1)  # o ledger exige provider_request_id unico


def unique(script: Script) -> Script:
    """Cenario com `provider_request_id` unicos, como o ledger exige."""
    return [
        item
        if isinstance(item, Exception)
        else LlmResponse(
            text=item.text,
            usage=Usage(input_tokens=100, output_tokens=50),
            provider_request_id=f"req-{next(_request_ids)}",
            resolved_model="fake-1",
            stop_reason=item.stop_reason,
        )
        for item in script
    ]


class HookedLlm(FakeLlmClient):
    """Cliente falso que executa um gancho antes de servir o pedido de numero N (1-based)."""

    def __init__(self, responses: Sequence[LlmResponse | Exception]) -> None:
        super().__init__(responses, VISION_CAPABILITIES)
        self.hooks: dict[int, Callable[[], None]] = {}

    def complete(self, request: LlmRequest) -> LlmResponse:
        hook = self.hooks.pop(len(self.requests) + 1, None)
        if hook is not None:
            hook()
        return super().complete(request)

    def load(self, script: Script) -> None:
        """Substitui o guiao pendente (para scripts que dependem de ids criados no teste)."""
        self._script[:] = unique(script)

    def request_text(self, number: int) -> str:
        """Texto da primeira mensagem do pedido `number` (1-based)."""
        part = self.requests[number - 1].messages[0].content[0]
        assert isinstance(part, TextPart)
        return part.text


@dataclass
class ContentRig:
    """Pipeline real sobre cliente falso, base de teste e relogio fixo."""

    pipeline: ContentPipeline
    engine: AgentEngine
    llm: HookedLlm
    generator: ImageGenerator
    library: MediaLibrary
    kits: BrandKitService
    photo_id: str
    store: ArtifactStore
    strategy: StrategyService
    kb: KbService
    feedback: FeedbackService
    queue: JobQueue
    clock: FixedClock
    factory: Factory
    brand_id: str
    plan_id: str
    strategy_id: str

    def seed_post(
        self,
        slot_key: str = f"{WEEK}-1",
        *,
        lifecycle: str = "planned",
        channels: Sequence[str] = ("instagram",),
        kb_refs: Sequence[str] = (),
    ) -> str:
        post = Post(
            brand_id=self.brand_id,
            plan_artifact_id=self.plan_id,
            slot_key=slot_key,
            strategy_artifact_id=self.strategy_id,
            channels=list(channels),
            scheduled_date=POST_DATE,
            brief={
                "tema": "Pao do dia",
                "pilar": "Producao artesanal",
                "objetivo": "Mensagens",
                "kb_refs": list(kb_refs),
            },
            lifecycle=lifecycle,
            created_at=self.clock.now(),
        )
        with self.factory.begin() as session:
            session.add(post)
            session.flush()
            return post.id

    def enqueue(self, post_id: str, reason: str = "plan", owner_comment: str | None = None) -> str:
        payload = {"post_id": post_id, "owner_comment": owner_comment, "reason": reason}
        with self.factory.begin() as session:
            job_id = self.queue.enqueue_in(
                session, GENERATE_POST_JOB, payload, f"{GENERATE_POST_JOB}:{post_id}:{new_id()}"
            )
        assert job_id is not None
        return job_id

    def claimed(
        self, post_id: str, reason: str = "plan", owner_comment: str | None = None
    ) -> ClaimedJob:
        """Enfileira e reclama o job do post (worker `w1`)."""
        self.enqueue(post_id, reason, owner_comment)
        job = self.queue.claim("w1")
        assert job is not None
        return job

    def add_owner_revision(
        self, post_id: str, rev_no: int = 1, origin: str = "edicao_responsavel"
    ) -> str:
        """Revisao gravada diretamente (como o painel faria), fora do pipeline."""
        with self.factory.begin() as session:
            post = session.get_one(Post, post_id)
            revision = PostRevision(
                brand_id=post.brand_id,
                post_id=post_id,
                rev_no=rev_no,
                revision_hash="0" * 64,
                caption="Texto do responsavel.",
                hashtags=["#x"],
                call_to_action=None,
                alt_text="Alt.",
                claims=[],
                review_verdict=None,
                review_violations=[],
                review_notes=None,
                origin=origin,
                created_at=self.clock.now(),
            )
            session.add(revision)
            session.flush()
            return revision.id

    def add_price_item(self) -> str:
        entry = NewKbEntry(
            kind="preco",
            key="pao",
            title="Pao",
            body="Pao de forno a lenha.",
            price_amount=Decimal("12.50"),
            price_currency="EUR",
            valid_from=date(2026, 11, 1),
            valid_until=date(2026, 12, 1),
            source_kind="dito_pelo_responsavel",
        )
        return self.kb.add(self.brand_id, entry, "ana").id

    def revisions(self, post_id: str) -> list[PostRevision]:
        with self.factory() as session:
            statement = select(PostRevision).where(PostRevision.post_id == post_id)
            return list(session.scalars(statement.order_by(PostRevision.rev_no)))

    def post(self, post_id: str) -> Post:
        with self.factory() as session:
            return session.get_one(Post, post_id)

    def job(self, job_id: str) -> Job:
        with self.factory() as session:
            return session.get_one(Job, job_id)

    def assets(self, kind: str) -> list[MediaAsset]:
        with self.factory() as session:
            statement = select(MediaAsset).where(MediaAsset.kind == kind)
            return list(session.scalars(statement.order_by(MediaAsset.created_at, MediaAsset.id)))

    def entries(self) -> list[CostEntry]:
        with self.factory() as session:
            return list(session.scalars(select(CostEntry)))

    def image_entries(self) -> list[CostEntry]:
        """Lancamentos de custo de imagem (os de texto, a custo 0, ficam de fora)."""
        return [entry for entry in self.entries() if "images" in entry.units]

    def runs(self, job_id: str) -> list[AgentRun]:
        """Execucoes ligadas ao job (trigger `post.<passo>:<job>`), por ordem de inicio."""
        with self.factory() as session:
            statement = select(AgentRun).where(AgentRun.trigger.like(f"%:{job_id}"))
            return list(session.scalars(statement.order_by(AgentRun.started_at, AgentRun.id)))

    def generated_requests(self) -> int:
        assert isinstance(self.generator, FakeImageGenerator)
        return len(self.generator.requests)

    def expire_leases(self) -> None:
        """Avanca o relogio para depois de qualquer lease."""
        self.clock.advance(content_job_lease() + timedelta(seconds=1))


def package_pass(
    verdict: str = "aprovar",
    violations: Sequence[str] = (),
    notes: str = "",
    legenda: str = "Pao quente todas as manhas, feito com receitas de familia.",
    *,
    visual: Script | None = None,
    checked: bool = True,
) -> Script:
    """Uma passagem Copywriter + Designer + Revisor (com imagem).

    O Designer escolhe por omissao `fundo_ia_titulo` (sem foto: serve a qualquer rig).
    """
    return [
        *fake_scenarios.caption_draft(legenda),
        *(visual if visual is not None else fake_scenarios.visual_draft()),
        *fake_scenarios.review(verdict, violations, notes, imagem_verificada=checked),
    ]


def build_rig(
    session_factory: Factory,
    clock: FixedClock,
    brand_id: str,
    script: Script,
    *,
    media_dir: Path,
    reviewer_capabilities: frozenset[Capability] = VISION_CAPABILITIES,
    designer_capabilities: frozenset[Capability] = frozenset({"text", "json_schema"}),
    image_price: str = "0",
    generator: ImageGenerator | None = None,
    with_kit: bool = True,
) -> ContentRig:
    free = PriceTier(0, Decimal(0), Decimal(0))
    per_image = tuple(ImagePrice(q, SIZE, Decimal(image_price)) for q in ("low", "medium", "high"))
    sync_model_prices(
        session_factory,
        [
            ModelPrice("fake", "fake-1", "v1", (free,), valid_from=VALID_FROM),
            ModelPrice("fake", FAKE_IMAGE_MODEL, "v1", (free,), VALID_FROM, per_image),
        ],
    )
    llm = HookedLlm(unique(script))
    route = RoleRoute(provider="fake", model_id="fake-1", capabilities=VISION_CAPABILITIES)
    reviewer_route = route.model_copy(update={"capabilities": reviewer_capabilities})
    designer_route = route.model_copy(update={"capabilities": designer_capabilities})
    budget = BudgetController(
        session_factory, PriceBook(session_factory), clock, Decimal(20), (50, 80, 100)
    )
    store = ArtifactStore(session_factory, clock)
    image_generator = generator or FakeImageGenerator()
    backend = ImageBackend(image_generator, "fake", FAKE_IMAGE_MODEL, "medium")
    engine = AgentEngine(
        session_factory,
        LlmRouter(
            {"copywriter": route, "revisor": reviewer_route, "designer": designer_route},
            {"fake": llm},
        ),
        budget,
        store,
        clock,
        sleep=lambda seconds: None,
        images=backend,
    )
    strategy = StrategyService(session_factory, engine, store, clock)
    kb = KbService(session_factory, store, clock)
    feedback = FeedbackService(session_factory, clock)
    queue = JobQueue(session_factory, clock, content_job_lease())
    library = MediaLibrary(session_factory, LocalMediaStore(media_dir), clock)
    kits = BrandKitService(session_factory, library, clock)
    photo_id = _seed_media(library, kits, brand_id, with_kit)
    pipeline = ContentPipeline(
        session_factory, engine, kb, feedback, queue, clock, store, strategy,
        library, kits, "medium",
    )  # fmt: skip
    strategy_id = _activate_strategy(store, strategy, brand_id)
    plan = store.create(brand_id, ArtifactType.PLANO, {"semana": WEEK, "slots": []})
    return ContentRig(
        pipeline, engine, llm, image_generator, library, kits, photo_id, store, strategy, kb,
        feedback, queue, clock, session_factory, brand_id, plan.id, strategy_id,
    )  # fmt: skip


def _seed_media(library: MediaLibrary, kits: BrandKitService, brand_id: str, with_kit: bool) -> str:
    """Uma foto de produto na biblioteca e (se `with_kit`) o kit com logotipo sintetico."""
    meta = PhotoUpload(
        mostra="produto", descricao="Pao no balcao", origem="propria", tem_autorizacao_pessoas=False
    )
    photo = library.upload_photo(brand_id, make_jpeg(1200, 1500), meta, "ana")
    if with_kit:
        kit = BrandKitInput(cor_primaria="#1F3A5F", cor_secundaria="#FFFFFF", fonte="inter-bold")
        kits.save(brand_id, kit, make_png(240, 240, alpha=True), "ana")
    return photo.id


def _activate_strategy(store: ArtifactStore, strategy: StrategyService, brand_id: str) -> str:
    profile = store.create(brand_id, ArtifactType.PERFIL_MARCA, PROFILE_PAYLOAD)
    store.submit(profile.id, "ana")
    store.approve_and_activate(profile.id, "ana")
    _, scenario = fake_scenarios.diagnosis_and_strategy()
    assert not isinstance(scenario, Exception)
    created = store.create(brand_id, ArtifactType.ESTRATEGIA, json.loads(scenario.text))
    store.submit(created.id, "ana")
    strategy.approve_strategy(created.id, "ana", {0: True, 1: False})
    return created.id
