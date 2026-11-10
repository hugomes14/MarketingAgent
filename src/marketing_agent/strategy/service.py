"""Servico de diagnostico e estrategia: propostas sem repeticao e decisoes por proposta.

Cada passo de BD e uma transacao curta; a chamada ao motor (e ao fornecedor) corre sempre
fora de qualquer transacao. Nenhuma mudanca de estado de artefacto depende da saida do LLM:
o motor so cria um `draft`, o servico submete-o, e so `approve_*` / `reject_*` decidem.
A vista ativa da estrategia so contem as propostas de oferta que o responsavel aceitou.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.agents.diretor import DIRETOR_DIR
from marketing_agent.agents.diretor.schemas import Diagnostico, Estrategia, PropostaOferta
from marketing_agent.artifacts.store import (
    ArtifactRecord,
    ArtifactStore,
    ArtifactType,
)
from marketing_agent.clock import Clock
from marketing_agent.db.models import AuditLog, StrategyDecision
from marketing_agent.harness.engine import AgentEngine, RunResult
from marketing_agent.harness.spec import load_agent_spec
from marketing_agent.proposals import (
    MissingPrerequisite,
    PendingProposal,
    ProposalInProgress,
    ProposalRunner,
    require_active,
    require_type,
)
from marketing_agent.strategy.loops import (
    DiagnosisLoop,
    DiagnosisPayload,
    StrategyLoop,
    StrategyPayload,
)

# `PendingProposal` e `ProposalInProgress` vivem em `proposals`; reexportados porque os
# chamadores (painel, fila de trabalhos, testes) sempre os importaram daqui.
__all__ = [
    "SCHEMAS",
    "ActiveStrategy",
    "IncompleteDecisions",
    "MissingPrerequisite",
    "PendingProposal",
    "ProposalInProgress",
    "StrategyService",
]

SCHEMAS: Mapping[str, type[BaseModel]] = {"Diagnostico": Diagnostico, "Estrategia": Estrategia}


class IncompleteDecisions(Exception):
    """As decisoes nao cobrem exatamente as propostas de oferta, ou nao sao booleanas."""


@dataclass(frozen=True)
class ActiveStrategy:
    """Estrategia ativa e so as propostas de oferta aceites, por ordem de indice."""

    artifact_id: str
    estrategia: Estrategia
    propostas_aceites: list[PropostaOferta]


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
        self._store = store
        self._clock = clock
        self._runner = ProposalRunner(session_factory, engine, store, clock)
        self._diagnosis_spec = load_agent_spec(DIRETOR_DIR / "spec_diagnostico.yaml", SCHEMAS)
        self._strategy_spec = load_agent_spec(DIRETOR_DIR / "spec_estrategia.yaml", SCHEMAS)

    def propose_diagnosis(self, brand_id: str) -> RunResult:
        """Gera um diagnostico `submitted`; exige `perfil_marca` ativo."""
        profile = require_active(self._store, brand_id, ArtifactType.PERFIL_MARCA, "diagnostico")
        parent = self._store.active(brand_id, ArtifactType.DIAGNOSTICO)
        payload = DiagnosisPayload(profile.payload, parent.id if parent else None)
        return self._runner.propose(
            brand_id,
            ArtifactType.DIAGNOSTICO,
            self._diagnosis_spec,
            "strategy.diagnosis",
            DiagnosisLoop(),
            payload,
        )

    def propose_strategy(self, brand_id: str) -> RunResult:
        """Gera uma estrategia `submitted`; exige `perfil_marca` e `diagnostico` ativos."""
        profile = require_active(self._store, brand_id, ArtifactType.PERFIL_MARCA, "estrategia")
        diagnosis = require_active(self._store, brand_id, ArtifactType.DIAGNOSTICO, "estrategia")
        parent = self._store.active(brand_id, ArtifactType.ESTRATEGIA)
        payload = StrategyPayload(profile.payload, diagnosis.payload, parent.id if parent else None)
        return self._runner.propose(
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
        require_type(record, expected)
        return record
