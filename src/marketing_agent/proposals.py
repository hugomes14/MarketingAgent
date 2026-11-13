"""Pedidos de proposta ao Diretor com marcador de exclusao por marca e tipo.

O marcador em system_flag dura a execucao efetiva mais a ultima chamada em excesso.
Impede propostas simultaneas e permite recuperar depois de um processo morrer.
Chamadas ao motor e ao fornecedor correm fora de qualquer transacao.
"""

import logging
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import ColumnElement, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import (
    ArtifactRecord,
    ArtifactState,
    ArtifactStore,
    ArtifactType,
)
from marketing_agent.clock import Clock
from marketing_agent.db.base import new_id
from marketing_agent.db.models import Artifact, SystemFlag
from marketing_agent.harness.engine import AgentEngine, Loop, RunResult, RunStatus
from marketing_agent.harness.spec import AgentSpec

logger = logging.getLogger(__name__)

_ACTOR = "diretor"


class MissingPrerequisite(Exception):
    """Falta o artefacto ativo de que a proposta depende."""


class PendingProposal(Exception):
    """Ja existe uma proposta submetida deste tipo, a aguardar decisao do responsavel."""


class ProposalInProgress(Exception):
    """Outra proposta do mesmo tipo e marca esta a ser gerada neste momento."""


def marker_key(brand_id: str, type: ArtifactType, scope: str | None = None) -> str:  # noqa: A002
    """Chave do marcador: por marca e tipo, e por `scope` quando as propostas sao independentes."""
    base = f"proposal:{brand_id}:{type.value}"
    return base if scope is None else f"{base}:{scope}"


def require_type(record: ArtifactRecord, expected: ArtifactType) -> None:
    """`ValueError` se `record` nao for do tipo `expected`."""
    if record.type is not expected:
        raise ValueError(
            f"o artefacto {record.id} e do tipo {record.type.value}, esperava {expected.value}"
        )


def require_active(
    store: ArtifactStore,
    brand_id: str,
    type: ArtifactType,  # noqa: A002
    wanted: str,
) -> ArtifactRecord:
    """Artefacto ativo de `type`; `MissingPrerequisite` (citando `wanted`) se nao existe."""
    record = store.active(brand_id, type)
    if record is None:
        raise MissingPrerequisite(
            f"{wanted}: falta um artefacto {type.value} ativo para a marca {brand_id}"
        )
    return record


class ProposalRunner:
    """Executa um pedido de proposta ao Diretor: marcador, recusa de pendente, motor e submissao."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: AgentEngine,
        store: ArtifactStore,
        clock: Clock,
    ) -> None:
        """Guarda a fabrica de sessoes, o motor, a loja de artefactos e o relogio."""
        self._session_factory = session_factory
        self._engine = engine
        self._store = store
        self._clock = clock

    def propose[TIn](
        self,
        brand_id: str,
        type: ArtifactType,  # noqa: A002
        spec: AgentSpec,
        trigger: str,
        loop: Loop[TIn, ArtifactRecord],
        payload: TIn,
        *,
        scope: str | None = None,
        only_pending_where: Sequence[ColumnElement[bool]] = (),
    ) -> RunResult:
        """Reclama o marcador, corre o motor fora de transacao e submete o rascunho.

        O marcador e por marca e tipo (e por `scope`, se as propostas forem independentes).
        `only_pending_where` restringe a recusa de proposta pendente (ex.: a mesma semana).
        """
        key = marker_key(brand_id, type, scope)
        token = self._claim(key, self._engine.claim_duration_s(spec))
        try:
            # Depois de reclamar: so uma execucao de cada vez ve se ja ha proposta submetida.
            self._refuse_if_pending(brand_id, type, only_pending_where)
            run = self._engine.run(spec, brand_id, trigger, loop, payload)
            if run.status is RunStatus.SUCCEEDED:
                self._submit_output(run)
            return run
        finally:
            self._release(key, token)

    def _submit_output(self, run: RunResult) -> None:
        if not isinstance(run.output, ArtifactRecord):
            raise TypeError(f"execucao {run.run_id}: saida inesperada {type(run.output).__name__}")
        self._store.submit(run.output.id, actor=_ACTOR)
        logger.info("proposta submetida: artefacto %s (execucao %s)", run.output.id, run.run_id)

    def _refuse_if_pending(
        self,
        brand_id: str,
        type: ArtifactType,  # noqa: A002
        conditions: Sequence[ColumnElement[bool]],
    ) -> None:
        with self._session_factory() as session:
            pending = session.scalar(
                select(Artifact.id)
                .where(
                    Artifact.brand_id == brand_id,
                    Artifact.type == type.value,
                    Artifact.state == ArtifactState.SUBMITTED.value,
                    *conditions,
                )
                .limit(1)
            )
        if pending is not None:
            raise PendingProposal(f"ja existe um {type.value} submetido a aguardar decisao")

    def _claim(self, key: str, max_duration_s: float) -> str:
        """Reclama `key` se livre ou caducada; senao recusa."""
        now = self._clock.now()
        token = new_id()
        insertion = insert(SystemFlag).values(key=key, value=token, set_by=_ACTOR, set_at=now)
        statement = insertion.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": token, "set_by": _ACTOR, "set_at": now},
            where=SystemFlag.set_at < now - timedelta(seconds=max_duration_s),
        ).returning(SystemFlag.key)
        with self._session_factory.begin() as session:
            claimed = session.execute(statement).scalar_one_or_none()
        if claimed is None:
            raise ProposalInProgress(f"ja ha uma proposta em curso ({key})")
        return token

    def _release(self, key: str, token: str) -> None:
        """Liberta o marcador so se ainda for deste pedido; falha de limpeza nao mascara o resto."""
        try:
            with self._session_factory.begin() as session:
                session.execute(
                    delete(SystemFlag).where(SystemFlag.key == key, SystemFlag.value == token)
                )
        except Exception as exc:
            logger.error("proposta: libertar marcador falhou (%s) para %s", type(exc).__name__, key)
