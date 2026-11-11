"""Montagem dos servicos de texto semanal e de media do painel (partilhada com os testes).

O painel e o worker partilham a base, mas nao o processo: aqui constroem-se os servicos que o
painel chama (base, plano, revisao, feedback) sobre o motor, a loja e o relogio ja montados.
A fila usa a mesma lease que a do worker (`content_job_lease`), embora o painel so enfileire e
cancele trabalhos e nunca os reclame.
"""

from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.clock import Clock
from marketing_agent.config import Settings
from marketing_agent.content.review import ReviewService
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.harness.engine import AgentEngine
from marketing_agent.jobs.queue import JobQueue
from marketing_agent.kb.service import KbService
from marketing_agent.media.brand_kit import BrandKitService
from marketing_agent.media.library import MediaLibrary
from marketing_agent.media.store import LocalMediaStore
from marketing_agent.panel.context import ContentServices, MediaServices
from marketing_agent.planning.service import PlanningService
from marketing_agent.planning.validation import PlanLimits
from marketing_agent.startup import content_job_lease
from marketing_agent.strategy.service import StrategyService


def build_content_services(
    settings: Settings,
    session_factory: sessionmaker[Session],
    engine: AgentEngine,
    store: ArtifactStore,
    strategy: StrategyService,
    clock: Clock,
) -> ContentServices:
    """Liga a base, o plano, a revisao e o feedback aos mesmos motor, loja e relogio do painel.

    Levanta `StartupError` se os specs do Copywriter ou do Revisor forem invalidos (a lease da
    fila deriva deles).
    """
    kb = KbService(session_factory, store, clock)
    feedback = FeedbackService(session_factory, clock)
    queue = JobQueue(session_factory, clock, content_job_lease())
    limits = PlanLimits(settings.plan_max_posts_per_week, settings.plan_max_posts_per_day)
    planning = PlanningService(session_factory, engine, store, strategy, kb, queue, clock, limits)
    media = build_media_services(settings, session_factory, clock)
    review = ReviewService(
        session_factory,
        feedback,
        queue,
        kb,
        clock,
        max_comment_chars=settings.max_message_chars,
        library=media.library,
        kits=media.kits,
    )
    return ContentServices(kb=kb, planning=planning, review=review, feedback=feedback, clock=clock)


def build_media_services(
    settings: Settings, session_factory: sessionmaker[Session], clock: Clock
) -> MediaServices:
    """Liga a biblioteca de fotos e o kit de marca ao mesmo armazem local (`MEDIA_DIR`)."""
    library = MediaLibrary(session_factory, LocalMediaStore(settings.media_dir), clock)
    return MediaServices(library=library, kits=BrandKitService(session_factory, library, clock))
