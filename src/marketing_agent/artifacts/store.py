"""Armazem de artefactos: payload imutavel, estado que avanca por transicoes validas.

A versao ativa vive em `artifact_head`, separada do artefacto: criar, submeter ou rejeitar
uma versao nova nunca altera a ativa; so `approve_and_activate` a troca.
"""

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import Clock
from marketing_agent.db.base import new_id
from marketing_agent.db.models import Artifact, ArtifactHead, AuditLog, BrandProfile


class ArtifactType(StrEnum):
    """Tipos de artefacto do nucleo."""

    PERFIL_MARCA = "perfil_marca"
    DIAGNOSTICO = "diagnostico"
    ESTRATEGIA = "estrategia"
    PLANO = "plano"
    BRIEF = "brief"
    RASCUNHO_TEXTO = "rascunho_texto"
    RASCUNHO_VISUAL = "rascunho_visual"
    REVISAO = "revisao"
    NOTA_PESQUISA = "nota_pesquisa"
    REVISAO_MENSAL = "revisao_mensal"
    FICHA_LEAD = "ficha_lead"


class ArtifactState(StrEnum):
    """Estados do ciclo de vida de um artefacto."""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


# Unico sitio onde as transicoes permitidas sao definidas.
ALLOWED_TRANSITIONS: Mapping[ArtifactState, frozenset[ArtifactState]] = {
    ArtifactState.DRAFT: frozenset({ArtifactState.SUBMITTED, ArtifactState.WITHDRAWN}),
    ArtifactState.SUBMITTED: frozenset(
        {ArtifactState.APPROVED, ArtifactState.REJECTED, ArtifactState.WITHDRAWN}
    ),
}


class InvalidTransition(Exception):
    """Transicao de estado nao permitida."""


class ArtifactNotFound(Exception):
    """Nao existe artefacto com o identificador pedido."""


@dataclass(frozen=True)
class ArtifactRecord:
    """Vista imutavel de um artefacto; `payload` e uma copia, sem alias com a base."""

    id: str
    brand_id: str
    type: ArtifactType
    state: ArtifactState
    payload: Mapping[str, object]
    parent_id: str | None
    schema_version: int
    produced_by_run_id: str | None


def to_record(row: Artifact) -> ArtifactRecord:
    """Converte a linha ORM num registo imutavel, copiando o payload."""
    return ArtifactRecord(
        id=row.id,
        brand_id=row.brand_id,
        type=ArtifactType(row.type),
        state=ArtifactState(row.state),
        payload=copy.deepcopy(row.payload),
        parent_id=row.parent_id,
        schema_version=row.schema_version,
        produced_by_run_id=row.produced_by_run_id,
    )


class ArtifactStore:
    """Cria artefactos e aplica transicoes; cada operacao e uma transacao propria."""

    def __init__(self, session_factory: sessionmaker[Session], clock: Clock) -> None:
        """Guarda a fabrica de sessoes e o relogio usado nas auditorias."""
        self._session_factory = session_factory
        self._clock = clock

    def create(
        self,
        brand_id: str,
        type: ArtifactType,
        payload: Mapping[str, object],
        *,
        parent_id: str | None = None,
        produced_by_run_id: str | None = None,
        schema_version: int = 1,
    ) -> ArtifactRecord:
        """Cria um artefacto em `draft`; nao toca na versao ativa."""
        row = Artifact(
            brand_id=brand_id,
            type=type.value,
            schema_version=schema_version,
            state=ArtifactState.DRAFT.value,
            payload=copy.deepcopy(dict(payload)),
            parent_id=parent_id,
            produced_by_run_id=produced_by_run_id,
        )
        with self._session_factory.begin() as session:
            session.add(row)
            session.flush()
            return to_record(row)

    def submit(self, artifact_id: str, actor: str) -> ArtifactRecord:
        """`draft -> submitted`."""
        return self._transition(artifact_id, actor, ArtifactState.SUBMITTED, "artifact.submit")

    def reject(self, artifact_id: str, actor: str, reason: str) -> ArtifactRecord:
        """`submitted -> rejected`; o motivo vai para o detalhe da auditoria."""
        return self._transition(
            artifact_id, actor, ArtifactState.REJECTED, "artifact.reject", reason=reason
        )

    def withdraw(self, artifact_id: str, actor: str) -> ArtifactRecord:
        """`draft|submitted -> withdrawn`."""
        return self._transition(artifact_id, actor, ArtifactState.WITHDRAWN, "artifact.withdraw")

    def approve_and_activate(
        self, artifact_id: str, actor: str, scope: str = "brand"
    ) -> ArtifactRecord:
        """Aprova, torna ativo (guardando o anterior) e projeta o perfil, numa transacao."""
        with self._session_factory.begin() as session:
            return self.approve_and_activate_in(session, artifact_id, actor, scope)

    def approve_and_activate_in(
        self, session: Session, artifact_id: str, actor: str, scope: str = "brand"
    ) -> ArtifactRecord:
        """Corpo de `approve_and_activate` na transacao do chamador (que faz o commit).

        Permite juntar a aprovacao a outras escritas atomicas; uma falha aqui levanta
        sem tocar na versao ativa e o chamador deve reverter a sua transacao.
        """
        row = self._advance(session, artifact_id, actor, ArtifactState.APPROVED, "artifact.approve")
        self._upsert_head(session, row, actor, scope)
        if row.type == ArtifactType.PERFIL_MARCA.value:
            self._upsert_profile(session, row)
        return to_record(row)

    def get(self, artifact_id: str) -> ArtifactRecord:
        """Le um artefacto; levanta `ArtifactNotFound` se nao existir."""
        with self._session_factory() as session:
            row = session.get(Artifact, artifact_id)
            if row is None:
                raise ArtifactNotFound(f"artefacto inexistente: {artifact_id}")
            return to_record(row)

    def active(
        self, brand_id: str, type: ArtifactType, scope: str = "brand"
    ) -> ArtifactRecord | None:
        """Versao ativa para marca, tipo e ambito, ou `None` se ainda nao houver."""
        with self._session_factory() as session:
            head = session.scalars(
                select(ArtifactHead).where(
                    ArtifactHead.brand_id == brand_id,
                    ArtifactHead.type == type.value,
                    ArtifactHead.scope == scope,
                )
            ).one_or_none()
            if head is None:
                return None
            return to_record(session.get_one(Artifact, head.active_artifact_id))

    def _transition(
        self, artifact_id: str, actor: str, target: ArtifactState, action: str, **extra: str
    ) -> ArtifactRecord:
        with self._session_factory.begin() as session:
            row = self._advance(session, artifact_id, actor, target, action, **extra)
            return to_record(row)

    def _advance(
        self,
        session: Session,
        artifact_id: str,
        actor: str,
        target: ArtifactState,
        action: str,
        **extra: str,
    ) -> Artifact:
        """Bloqueia a linha, valida a transicao, muda o estado e audita."""
        row = session.scalars(
            select(Artifact).where(Artifact.id == artifact_id).with_for_update()
        ).one_or_none()
        if row is None:
            raise ArtifactNotFound(f"artefacto inexistente: {artifact_id}")
        source = ArtifactState(row.state)
        if target not in ALLOWED_TRANSITIONS.get(source, frozenset()):
            raise InvalidTransition(
                f"{source.value} -> {target.value} nao permitida ({artifact_id})"
            )
        row.state = target.value
        session.add(
            AuditLog(
                actor=actor,
                action=action,
                subject_type="artifact",
                subject_id=row.id,
                detail={"from": source.value, "to": target.value, **extra},
                at=self._clock.now(),
            )
        )
        session.flush()
        return row

    def _upsert_head(self, session: Session, row: Artifact, actor: str, scope: str) -> None:
        """Troca a versao ativa guardando a anterior, num unico `INSERT ... ON CONFLICT`.

        No `DO UPDATE`, `ArtifactHead.active_artifact_id` e o valor da linha existente (nao
        `excluded`), logo `previous_artifact_id` e lido e escrito atomicamente. O Postgres
        bloqueia a linha em conflito, por isso aprovacoes concorrentes da mesma chave
        serializam-se, inclusive a primeira ativacao (sem linha previa nao ha o que ler).
        Numa insercao nova, `previous_artifact_id` fica `None`.
        """
        now = self._clock.now()
        statement = insert(ArtifactHead).values(
            id=new_id(),
            brand_id=row.brand_id,
            type=row.type,
            scope=scope,
            active_artifact_id=row.id,
            previous_artifact_id=None,
            activated_by=actor,
            activated_at=now,
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["brand_id", "type", "scope"],
                set_={
                    "previous_artifact_id": ArtifactHead.active_artifact_id,
                    "active_artifact_id": row.id,
                    "activated_by": actor,
                    "activated_at": now,
                },
            )
        )

    def _upsert_profile(self, session: Session, row: Artifact) -> None:
        """Projeta o `perfil_marca` aprovado em `brand_profile` (uma linha por marca)."""
        values = {
            "artifact_id": row.id,
            "data": copy.deepcopy(row.payload),
            "updated_at": self._clock.now(),
        }
        statement = insert(BrandProfile).values(id=new_id(), brand_id=row.brand_id, **values)
        session.execute(statement.on_conflict_do_update(index_elements=["brand_id"], set_=values))
