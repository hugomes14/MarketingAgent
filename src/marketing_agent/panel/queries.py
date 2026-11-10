"""Leituras do painel: vistas tipadas, so de leitura, para as paginas.

Os servicos expoem acoes, nao leituras de sessoes, turnos ou artefactos; este modulo faz essas
consultas sem mudar nenhum estado. Todas as leituras sao filtradas pela marca do painel: um id
de outra marca e tratado como inexistente.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.agents.diretor.gap_map import GapItem, parse_gap_map
from marketing_agent.agents.diretor.schemas import Diagnostico, Estrategia, PerfilMarca
from marketing_agent.artifacts.store import (
    ArtifactNotFound,
    ArtifactRecord,
    ArtifactStore,
    ArtifactType,
)
from marketing_agent.content.review_errors import PostNotFound
from marketing_agent.db.models import (
    AgentRun,
    Artifact,
    InterviewSession,
    InterviewTurn,
    StrategyDecision,
)
from marketing_agent.interview.service import SessionNotFound
from marketing_agent.panel import queries_batch, queries_content
from marketing_agent.panel.errors import InactiveProfile
from marketing_agent.panel.queries_batch import BatchView, ValidRefs
from marketing_agent.panel.queries_content import KbGroup, PlanLink, PlanView
from marketing_agent.planning.schemas import PlanoSemanal

MAX_LISTED_SESSIONS: Final = 20

# Tipos que o painel reve: etiqueta em portugues e prefixo da pagina de revisao.
REVIEWED_TYPES: Final[dict[ArtifactType, tuple[str, str]]] = {
    ArtifactType.PERFIL_MARCA: ("Perfil da marca", "/perfil"),
    ArtifactType.DIAGNOSTICO: ("Diagnóstico", "/diagnostico"),
    ArtifactType.ESTRATEGIA: ("Estratégia", "/estrategia"),
    ArtifactType.PLANO: ("Plano semanal", "/plano"),
}


@dataclass(frozen=True)
class SessionSummary:
    """Linha da lista de entrevistas; `started_at` vem da primeira execucao do Diretor."""

    session_id: str
    state: str
    message_count: int
    started_at: datetime | None


@dataclass(frozen=True)
class ActiveArtifact[T: BaseModel]:
    """Versao ativa de um artefacto, com o conteudo ja validado."""

    artifact_id: str
    content: T


@dataclass(frozen=True)
class PendingArtifact:
    """Artefacto `submitted` a espera da decisao do responsavel."""

    artifact_id: str
    label: str
    url: str


@dataclass(frozen=True)
class HomeSnapshot:
    """Dados de leitura da pagina inicial (a estrategia ativa vem do `StrategyService`)."""

    sessions: list[SessionSummary]
    profile: ActiveArtifact[PerfilMarca] | None
    diagnosis: ActiveArtifact[Diagnostico] | None
    pending: list[PendingArtifact]
    latest_plan: PlanLink | None
    in_review_count: int
    review_week: str | None  # semana mais antiga com publicacoes por rever


@dataclass(frozen=True)
class TurnView:
    """Turno da entrevista; `role` e `user` ou `director`."""

    seq: int
    role: str
    content: str


@dataclass(frozen=True)
class SessionView:
    """Sessao de entrevista com os turnos por ordem e o mapa de lacunas."""

    session_id: str
    state: str
    turns: list[TurnView]
    gap_map: list[GapItem]


@dataclass(frozen=True)
class ProfileView:
    """Perfil submetido ou ativo, para revisao."""

    record: ArtifactRecord
    perfil: PerfilMarca
    is_active: bool


@dataclass(frozen=True)
class DiagnosisView:
    """Diagnostico submetido ou ativo, para revisao."""

    record: ArtifactRecord
    diagnostico: Diagnostico


@dataclass(frozen=True)
class StrategyView:
    """Estrategia para revisao; `decisions` mapeia o indice da proposta para aceite/recusa."""

    record: ArtifactRecord
    estrategia: Estrategia
    decisions: dict[int, bool]


class PanelQueries:
    """Consultas de leitura do painel sobre a base e o armazem de artefactos."""

    def __init__(self, session_factory: sessionmaker[Session], store: ArtifactStore) -> None:
        """Guarda a fabrica de sessoes e o armazem usados nas leituras."""
        self._session_factory = session_factory
        self._store = store

    def home_snapshot(self, brand_id: str) -> HomeSnapshot:
        """Entrevistas recentes, versoes ativas de perfil e diagnostico, e decisoes pendentes."""
        return HomeSnapshot(
            sessions=self._recent_sessions(brand_id),
            profile=self._active(brand_id, ArtifactType.PERFIL_MARCA, PerfilMarca),
            diagnosis=self._active(brand_id, ArtifactType.DIAGNOSTICO, Diagnostico),
            pending=self._pending(brand_id),
            latest_plan=self._read(queries_content.latest_plan_link, brand_id),
            in_review_count=self._read(queries_content.count_in_review, brand_id),
            review_week=self._read(queries_content.earliest_review_week, brand_id),
        )

    def require_session(self, brand_id: str, session_id: str) -> None:
        """Levanta `SessionNotFound` se a sessao nao existir ou for de outra marca."""
        with self._session_factory() as session:
            self._session_row(session, brand_id, session_id)

    def session_view(self, brand_id: str, session_id: str) -> SessionView:
        """Sessao com turnos e mapa de lacunas; `SessionNotFound` se nao for desta marca."""
        with self._session_factory() as session:
            row = self._session_row(session, brand_id, session_id)
            turns = session.scalars(
                select(InterviewTurn)
                .where(InterviewTurn.session_id == session_id)
                .order_by(InterviewTurn.seq)
            )
            views = [TurnView(turn.seq, turn.role, turn.content) for turn in turns]
            return SessionView(row.id, row.state, views, parse_gap_map(row.gap_map))

    def require_artifact(
        self, brand_id: str, artifact_id: str, expected: ArtifactType
    ) -> ArtifactRecord:
        """Artefacto desta marca e do tipo pedido; senao `ArtifactNotFound`."""
        record = self._store.get(artifact_id)
        if record.brand_id != brand_id or record.type is not expected:
            raise ArtifactNotFound(f"artefacto {expected.value} inexistente: {artifact_id}")
        return record

    def profile_view(self, brand_id: str, artifact_id: str) -> ProfileView:
        """Perfil da marca com o conteudo validado."""
        record = self.require_artifact(brand_id, artifact_id, ArtifactType.PERFIL_MARCA)
        perfil = PerfilMarca.model_validate(record.payload)
        return ProfileView(record, perfil, self._is_active(brand_id, record))

    def require_active_profile(self, brand_id: str, artifact_id: str) -> PerfilMarca:
        """Perfil ativo com este id; `ArtifactNotFound` se for outro tipo ou marca.

        Levanta `InactiveProfile` se o perfil existe mas nao e o ativo: so o ativo se promove.
        """
        record = self.require_artifact(brand_id, artifact_id, ArtifactType.PERFIL_MARCA)
        if not self._is_active(brand_id, record):
            raise InactiveProfile(f"o perfil {artifact_id} nao e o perfil ativo")
        return PerfilMarca.model_validate(record.payload)

    def plan_view(self, brand_id: str, artifact_id: str) -> PlanView:
        """Plano com o conteudo validado, os titulos da base que cita e as publicacoes criadas."""
        record = self.require_artifact(brand_id, artifact_id, ArtifactType.PLANO)
        plano = PlanoSemanal.model_validate(record.payload)
        refs = {ref for slot in plano.slots for ref in slot.kb_refs}
        with self._session_factory() as session:
            titles = queries_content.kb_titles(session, brand_id, refs)
            posts = queries_content.count_plan_posts(session, artifact_id)
        return PlanView(record, plano, titles, posts)

    def batch_view(self, brand_id: str, week: str, valid_refs: ValidRefs) -> BatchView:
        """Lote da semana: publicacoes desta marca com a ultima revisao e o historico.

        `valid_refs` (`KbService.valid_refs`) decide que alegacoes tem apoio valido na data de
        cada publicacao; as consultas sao em numero fixo (so a validade e uma por publicacao).
        """
        with self._session_factory() as session:
            rows = queries_batch.load_week(session, brand_id, week)
        return queries_batch.build_batch(brand_id, week, rows, valid_refs)

    def review_week(self, brand_id: str) -> str | None:
        """Semana mais antiga com publicacoes por rever, ou `None`."""
        return self._read(queries_content.earliest_review_week, brand_id)

    def post_week(self, brand_id: str, post_id: str) -> str:
        """Semana ISO da publicacao desta marca; `PostNotFound` se nao existe ou e de outra."""
        with self._session_factory() as session:
            week = queries_batch.post_week(session, brand_id, post_id)
        if week is None:
            raise PostNotFound
        return week

    def kb_view(self, brand_id: str) -> list[KbGroup]:
        """Itens ativos da base agrupados por tipo."""
        return self._read(queries_content.kb_groups, brand_id)

    def diagnosis_view(self, brand_id: str, artifact_id: str) -> DiagnosisView:
        """Diagnostico com o conteudo validado."""
        record = self.require_artifact(brand_id, artifact_id, ArtifactType.DIAGNOSTICO)
        return DiagnosisView(record, Diagnostico.model_validate(record.payload))

    def strategy_view(self, brand_id: str, artifact_id: str) -> StrategyView:
        """Estrategia com o conteudo validado e as decisoes ja gravadas (se houver)."""
        record = self.require_artifact(brand_id, artifact_id, ArtifactType.ESTRATEGIA)
        with self._session_factory() as session:
            rows = session.execute(
                select(StrategyDecision.proposal_index, StrategyDecision.accepted).where(
                    StrategyDecision.strategy_artifact_id == artifact_id
                )
            )
            decisions = {index: accepted for index, accepted in rows}
        return StrategyView(record, Estrategia.model_validate(record.payload), decisions)

    def _read[T](self, query: Callable[[Session, str], T], brand_id: str) -> T:
        with self._session_factory() as session:
            return query(session, brand_id)

    def _is_active(self, brand_id: str, record: ArtifactRecord) -> bool:
        active = self._store.active(brand_id, record.type)
        return active is not None and active.id == record.id

    def _active[T: BaseModel](
        self,
        brand_id: str,
        type: ArtifactType,
        schema: type[T],  # noqa: A002
    ) -> ActiveArtifact[T] | None:
        record = self._store.active(brand_id, type)
        if record is None:
            return None
        return ActiveArtifact(record.id, schema.model_validate(record.payload))

    def _pending(self, brand_id: str) -> list[PendingArtifact]:
        with self._session_factory() as session:
            rows = session.execute(
                select(Artifact.id, Artifact.type)
                .where(
                    Artifact.brand_id == brand_id,
                    Artifact.state == "submitted",
                    Artifact.type.in_([t.value for t in REVIEWED_TYPES]),
                )
                .order_by(Artifact.type, Artifact.id)
            )
            pending = []
            for artifact_id, type_value in rows:
                label, prefix = REVIEWED_TYPES[ArtifactType(type_value)]
                pending.append(PendingArtifact(artifact_id, label, f"{prefix}/{artifact_id}"))
            return pending

    def _recent_sessions(self, brand_id: str) -> list[SessionSummary]:
        activity = (
            select(
                InterviewTurn.session_id.label("session_id"),
                func.count(InterviewTurn.id).label("messages"),
                func.min(AgentRun.started_at).label("started_at"),
            )
            .join(AgentRun, InterviewTurn.run_id == AgentRun.id, isouter=True)
            .group_by(InterviewTurn.session_id)
            .subquery()
        )
        statement = (
            select(
                InterviewSession.id,
                InterviewSession.state,
                activity.c.messages,
                activity.c.started_at,
            )
            .join(activity, activity.c.session_id == InterviewSession.id, isouter=True)
            .where(InterviewSession.brand_id == brand_id)
            .order_by(activity.c.started_at.desc().nulls_last(), InterviewSession.id)
            .limit(MAX_LISTED_SESSIONS)
        )
        with self._session_factory() as session:
            return [
                SessionSummary(session_id, state, messages or 0, started_at)
                for session_id, state, messages, started_at in session.execute(statement)
            ]

    @staticmethod
    def _session_row(session: Session, brand_id: str, session_id: str) -> InterviewSession:
        row = session.get(InterviewSession, session_id)
        if row is None or row.brand_id != brand_id:
            raise SessionNotFound(f"sessao de entrevista inexistente: {session_id}")
        return row
