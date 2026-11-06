"""Servico de diagnostico e estrategia: propostas sem repeticao e decisoes por proposta.

Cada passo de BD e uma transacao curta; a chamada ao motor (e ao fornecedor) corre sempre
fora de qualquer transacao. Nenhuma mudanca de estado de artefacto depende da saida do LLM:
o motor so cria um `draft`, o servico submete-o, e so `approve_*` / `reject_*` decidem.
A vista ativa da estrategia so contem as propostas de oferta que o responsavel aceitou.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.agents.diretor import DIRETOR_DIR
from marketing_agent.agents.diretor.schemas import Diagnostico, Estrategia, PropostaOferta
from marketing_agent.artifacts.store import (
    ArtifactRecord,
    ArtifactState,
    ArtifactStore,
    ArtifactType,
)
from marketing_agent.clock import Clock
from marketing_agent.db.base import new_id
from marketing_agent.db.models import Artifact, AuditLog, StrategyDecision, SystemFlag
from marketing_agent.harness.engine import AgentEngine, Loop, RunResult, RunStatus
from marketing_agent.harness.spec import AgentSpec, load_agent_spec
from marketing_agent.strategy.loops import (
    DiagnosisLoop,
    DiagnosisPayload,
    StrategyLoop,
    StrategyPayload,
)

logger = logging.getLogger(__name__)

SCHEMAS: Mapping[str, type[BaseModel]] = {"Diagnostico": Diagnostico, "Estrategia": Estrategia}
_ACTOR = "diretor"


class MissingPrerequisite(Exception):
    """Falta o artefacto ativo de que a proposta depende."""


class IncompleteDecisions(Exception):
    """As decisoes nao cobrem exatamente as propostas de oferta, ou nao sao booleanas."""


class PendingProposal(Exception):
    """Ja existe uma proposta submetida deste tipo, a aguardar decisao do responsavel."""


class ProposalInProgress(Exception):
    """Outra proposta do mesmo tipo e marca esta a ser gerada neste momento."""


@dataclass(frozen=True)
class ActiveStrategy:
    """Estrategia ativa e so as propostas de oferta aceites, por ordem de indice."""

    artifact_id: str
    estrategia: Estrategia
    propostas_aceites: list[PropostaOferta]


def _marker_key(brand_id: str, type: ArtifactType) -> str:  # noqa: A002
    return f"proposal:{brand_id}:{type.value}"


def _require_type(record: ArtifactRecord, expected: ArtifactType) -> None:
    if record.type is not expected:
        raise ValueError(
            f"o artefacto {record.id} e do tipo {record.type.value}, esperava {expected.value}"
        )


def _check_decisions(decisions: Mapping[int, bool], proposal_count: int) -> None:
    """Exige uma decisao booleana para cada indice `0..n-1`, sem indices a mais."""
    expected = set(range(proposal_count))
    missing = sorted(expected - set(decisions))
    extra = sorted(set(decisions) - expected)
    non_bool = sorted(i for i, accepted in decisions.items() if not isinstance(accepted, bool))
    if missing or extra or non_bool:
        raise IncompleteDecisions(
            f"decisoes devem cobrir exatamente os indices 0..{proposal_count - 1}: "
            f"em falta {missing}, a mais {extra}, nao booleanas {non_bool}"
        )


class StrategyService:
    """Propoe diagnostico e estrategia e regista as decisoes do responsavel."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: AgentEngine,
        store: ArtifactStore,
        clock: Clock,
    ) -> None:
        """Guarda os colaboradores e carrega os specs de diagnostico e de estrategia."""
        self._session_factory = session_factory
        self._engine = engine
        self._store = store
        self._clock = clock
        self._diagnosis_spec = load_agent_spec(DIRETOR_DIR / "spec_diagnostico.yaml", SCHEMAS)
        self._strategy_spec = load_agent_spec(DIRETOR_DIR / "spec_estrategia.yaml", SCHEMAS)

    def propose_diagnosis(self, brand_id: str) -> RunResult:
        """Gera um diagnostico `submitted`; exige `perfil_marca` ativo."""
        profile = self._require_active(brand_id, ArtifactType.PERFIL_MARCA, "diagnostico")
        parent = self._store.active(brand_id, ArtifactType.DIAGNOSTICO)
        payload = DiagnosisPayload(profile.payload, parent.id if parent else None)
        return self._propose(
            brand_id,
            ArtifactType.DIAGNOSTICO,
            self._diagnosis_spec,
            "strategy.diagnosis",
            DiagnosisLoop(),
            payload,
        )

    def propose_strategy(self, brand_id: str) -> RunResult:
        """Gera uma estrategia `submitted`; exige `perfil_marca` e `diagnostico` ativos."""
        profile = self._require_active(brand_id, ArtifactType.PERFIL_MARCA, "estrategia")
        diagnosis = self._require_active(brand_id, ArtifactType.DIAGNOSTICO, "estrategia")
        parent = self._store.active(brand_id, ArtifactType.ESTRATEGIA)
        payload = StrategyPayload(profile.payload, diagnosis.payload, parent.id if parent else None)
        return self._propose(
            brand_id,
            ArtifactType.ESTRATEGIA,
            self._strategy_spec,
            "strategy.strategy",
            StrategyLoop(),
            payload,
        )

    def approve_diagnosis(self, artifact_id: str, actor: str) -> None:
        """Aprova e ativa o diagnostico submetido."""
        self._require_artifact(artifact_id, ArtifactType.DIAGNOSTICO)
        self._store.approve_and_activate(artifact_id, actor)

    def reject_diagnosis(self, artifact_id: str, actor: str, reason: str) -> None:
        """Rejeita o diagnostico submetido; a versao ativa nao muda."""
        self._require_artifact(artifact_id, ArtifactType.DIAGNOSTICO)
        self._store.reject(artifact_id, actor, reason)

    def reject_strategy(self, artifact_id: str, actor: str, reason: str) -> None:
        """Rejeita a estrategia submetida; a versao ativa nao muda."""
        self._require_artifact(artifact_id, ArtifactType.ESTRATEGIA)
        self._store.reject(artifact_id, actor, reason)

    def approve_strategy(self, artifact_id: str, actor: str, decisions: Mapping[int, bool]) -> None:
        """Aprova a estrategia com uma decisao por proposta, tudo numa transacao.

        Sem decisao para cada proposta (nem indices a mais) levanta `IncompleteDecisions`;
        se a aprovacao falha (ex.: nao esta `submitted`) nenhuma decisao fica gravada.
        """
        record = self._require_artifact(artifact_id, ArtifactType.ESTRATEGIA)
        strategy = Estrategia.model_validate(record.payload)
        _check_decisions(decisions, len(strategy.propostas_oferta))
        with self._session_factory.begin() as session:
            self._store.approve_and_activate_in(session, artifact_id, actor)
            self._record_decisions(session, record, actor, decisions)

    def active_strategy(self, brand_id: str) -> ActiveStrategy | None:
        """Estrategia ativa com so as propostas aceites; rejeitadas ou sem decisao nao entram."""
        record = self._store.active(brand_id, ArtifactType.ESTRATEGIA)
        if record is None:
            return None
        strategy = Estrategia.model_validate(record.payload)
        accepted = self._accepted_indices(record)
        proposals = [p for i, p in enumerate(strategy.propostas_oferta) if i in accepted]
        return ActiveStrategy(record.id, strategy, proposals)

    def _record_decisions(
        self,
        session: Session,
        record: ArtifactRecord,
        actor: str,
        decisions: Mapping[int, bool],
    ) -> None:
        now = self._clock.now()
        for index in sorted(decisions):
            accepted = decisions[index]
            session.add(
                StrategyDecision(
                    brand_id=record.brand_id,
                    strategy_artifact_id=record.id,
                    proposal_index=index,
                    accepted=accepted,
                    decided_by=actor,
                    decided_at=now,
                )
            )
            session.add(
                AuditLog(
                    actor=actor,
                    action="strategy.decision",
                    subject_type="artifact",
                    subject_id=record.id,
                    detail={"proposal_index": index, "accepted": accepted},
                    at=now,
                )
            )

    def _accepted_indices(self, record: ArtifactRecord) -> set[int]:
        with self._session_factory() as session:
            return set(
                session.scalars(
                    select(StrategyDecision.proposal_index).where(
                        StrategyDecision.brand_id == record.brand_id,
                        StrategyDecision.strategy_artifact_id == record.id,
                        StrategyDecision.accepted.is_(True),
                    )
                )
            )

    def _require_artifact(self, artifact_id: str, expected: ArtifactType) -> ArtifactRecord:
        record = self._store.get(artifact_id)
        _require_type(record, expected)
        return record

    def _require_active(self, brand_id: str, type: ArtifactType, wanted: str) -> ArtifactRecord:  # noqa: A002
        record = self._store.active(brand_id, type)
        if record is None:
            raise MissingPrerequisite(
                f"{wanted}: falta um artefacto {type.value} ativo para a marca {brand_id}"
            )
        return record

    def _propose[TIn](
        self,
        brand_id: str,
        type: ArtifactType,  # noqa: A002
        spec: AgentSpec,
        trigger: str,
        loop: Loop[TIn, ArtifactRecord],
        payload: TIn,
    ) -> RunResult:
        """Reclama o marcador, corre o motor fora de transacao e submete o rascunho."""
        key = _marker_key(brand_id, type)
        token = self._claim_marker(key, spec)
        try:
            # Depois de reclamar: so uma execucao de cada vez ve se ja ha proposta submetida.
            self._refuse_if_pending(brand_id, type)
            run = self._engine.run(spec, brand_id, trigger, loop, payload)
            if run.status is RunStatus.SUCCEEDED:
                self._submit_output(run)
            return run
        finally:
            self._release_marker(key, token)

    def _submit_output(self, run: RunResult) -> None:
        if not isinstance(run.output, ArtifactRecord):
            raise TypeError(f"execucao {run.run_id}: saida inesperada {type(run.output).__name__}")
        self._store.submit(run.output.id, actor=_ACTOR)
        logger.info("proposta submetida: artefacto %s (execucao %s)", run.output.id, run.run_id)

    def _refuse_if_pending(self, brand_id: str, type: ArtifactType) -> None:  # noqa: A002
        with self._session_factory() as session:
            pending = session.scalar(
                select(Artifact.id)
                .where(
                    Artifact.brand_id == brand_id,
                    Artifact.type == type.value,
                    Artifact.state == ArtifactState.SUBMITTED.value,
                )
                .limit(1)
            )
        if pending is not None:
            raise PendingProposal(f"ja existe um {type.value} submetido a aguardar decisao")

    def _claim_marker(self, key: str, spec: AgentSpec) -> str:
        """Reclama `key` se livre ou caducado (mais velho que `max_duration_s`); senao recusa."""
        now = self._clock.now()
        token = new_id()
        insertion = insert(SystemFlag).values(key=key, value=token, set_by=_ACTOR, set_at=now)
        statement = insertion.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": token, "set_by": _ACTOR, "set_at": now},
            where=SystemFlag.set_at < now - timedelta(seconds=spec.limits.max_duration_s),
        ).returning(SystemFlag.key)
        with self._session_factory.begin() as session:
            claimed = session.execute(statement).scalar_one_or_none()
        if claimed is None:
            raise ProposalInProgress(f"ja ha uma proposta em curso ({key})")
        return token

    def _release_marker(self, key: str, token: str) -> None:
        """Liberta o marcador so se ainda for deste pedido; falha de limpeza nao mascara o resto."""
        try:
            with self._session_factory.begin() as session:
                session.execute(
                    delete(SystemFlag).where(SystemFlag.key == key, SystemFlag.value == token)
                )
        except Exception as exc:
            logger.error("proposta: libertar marcador falhou (%s) para %s", type(exc).__name__, key)
