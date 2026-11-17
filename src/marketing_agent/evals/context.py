"""Contexto fixado no inicio de uma corrida de avaliacao e reconstrucao do mesmo para cada item.

Justica (spec 2C, secao 3; Foco 5): os dois percursos de um brief veem EXATAMENTE o mesmo
contexto, e esse contexto e o do instante em que a corrida comecou. `ContextSnapshot` guarda,
em `eval_run.context_snapshot`, so ids e valores imutaveis:

- itens da base (ids): as linhas de `kb_item` so mudam de `is_active`, por isso reler por id
  devolve o mesmo texto mesmo que o item seja desativado ou expire depois;
- exemplos de feedback (ids, pela ordem do prompt): idem;
- perfil e estrategia (ids de artefactos, cujo payload e imutavel) e as propostas aceites
  (valores, porque a decisao do responsavel vive noutra tabela);
- fotos (ids) e versao do kit de marca (`DesignSnapshot`).

`EvalContext.build` e a unica via por onde o contexto de um item e montado: nunca le a vista
ativa de nada. Os testes comparam os pedidos ao modelo dos dois percursos.
"""

from collections.abc import Mapping
from datetime import date
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.agents.diretor.schemas import Estrategia, PropostaOferta
from marketing_agent.artifacts.store import ArtifactStore, ArtifactType
from marketing_agent.content.loops import CopyPayload
from marketing_agent.content.package import PackageContext
from marketing_agent.content.package_media import DesignSnapshot, capture_design_snapshot
from marketing_agent.evals.sets import EvalBrief
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.kb.service import KbService
from marketing_agent.strategy.service import StrategyService

SNAPSHOT_VERSION: Final = 1


class EvalPrerequisiteMissing(Exception):
    """Falta algo de que os dois percursos dependem; `reason` e uma frase fixa."""

    def __init__(self, reason: str) -> None:
        """Guarda a razao fixa (a mesma que a mensagem)."""
        super().__init__(reason)
        self.reason = reason


class EvalSetInvalid(Exception):
    """O conjunto cita um pilar ou um item da base que nao serve; a mensagem e fixa.

    Nunca repete valores do ficheiro: so o tipo (`pilar`/`kb_ref`) e a chave do brief, que o
    carregador ja validou como `^[a-z0-9_]{1,40}$`.
    """

    def __init__(self, brief_key: str, field: Literal["pilar", "kb_ref"]) -> None:
        """Monta a mensagem fixa a partir do campo e da chave validada."""
        super().__init__(
            f"o brief '{brief_key}' cita um {field} que nao existe ou nao esta valido; "
            "corrija o conjunto de avaliacao"
        )


class ContextSnapshot(BaseModel):
    """O que ambos os percursos veem durante toda a corrida (forma de `context_snapshot`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = SNAPSHOT_VERSION
    kb_valid_on: date
    kb_ids: list[str]
    feedback_style_ids: list[str]
    feedback_negative_ids: list[str]
    profile_artifact_id: str = Field(min_length=1)
    strategy_artifact_id: str = Field(min_length=1)
    accepted_proposals: list[PropostaOferta]
    photo_ids: list[str]
    kit_version: int | None

    @property
    def design(self) -> DesignSnapshot:
        """As entradas de desenho fixadas, como as le `DesignMedia`."""
        return DesignSnapshot(frozenset(self.photo_ids), self.kit_version)


def brief_mapping(brief: EvalBrief) -> dict[str, object]:
    """O brief na forma que o pacote recebe (a de `post.brief`): sem chave nem etiquetas."""
    return {
        "tema": brief.tema,
        "pilar": brief.pilar,
        "objetivo": brief.objetivo,
        "kb_refs": list(brief.kb_refs),
    }


class EvalContext:
    """Fixa o contexto no inicio da corrida e reconstroi-o, igual, para cada item."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        store: ArtifactStore,
        strategy: StrategyService,
        kb: KbService,
        feedback: FeedbackService,
    ) -> None:
        """Guarda os servicos de onde vem cada parte do contexto."""
        self._session_factory = session_factory
        self._store = store
        self._strategy = strategy
        self._kb = kb
        self._feedback = feedback

    def capture(self, brand_id: str, briefs: list[EvalBrief], on: date) -> ContextSnapshot:
        """Fixa o contexto de `briefs` na data `on`; valida pilares e itens da base.

        Raises:
            EvalPrerequisiteMissing: sem perfil ativo, estrategia ativa ou kit de marca.
            EvalSetInvalid: um brief cita um pilar fora da estrategia ativa ou um item da
                base inexistente, desativado ou fora de validade em `on`.
        """
        profile = self._store.active(brand_id, ArtifactType.PERFIL_MARCA)
        active = self._strategy.active_strategy(brand_id)
        if profile is None:
            raise EvalPrerequisiteMissing("falta um perfil de marca ativo")
        if active is None:
            raise EvalPrerequisiteMissing("falta uma estrategia ativa")
        design = capture_design_snapshot(self._session_factory, brand_id)
        if design.kit_version is None:
            raise EvalPrerequisiteMissing("falta o kit de marca")
        kb_ids = self._valid_kb_ids(brand_id, briefs, on)
        for brief in briefs:
            if brief.pilar not in active.estrategia.pilares:
                raise EvalSetInvalid(brief.key, "pilar")
        styles, negatives = self._feedback.prompt_examples(brand_id)
        return ContextSnapshot(
            kb_valid_on=on,
            kb_ids=kb_ids,
            feedback_style_ids=[example.id for example in styles],
            feedback_negative_ids=[example.id for example in negatives],
            profile_artifact_id=profile.id,
            strategy_artifact_id=active.artifact_id,
            accepted_proposals=list(active.propostas_aceites),
            photo_ids=sorted(design.photo_ids),
            kit_version=design.kit_version,
        )

    def _valid_kb_ids(self, brand_id: str, briefs: list[EvalBrief], on: date) -> list[str]:
        """Ids citados que existem e estao ativos e validos em `on` (a regra do pipeline)."""
        valid: set[str] = set()
        for brief in briefs:
            found = self._kb.valid_refs(brand_id, brief.kb_refs, on)
            if set(brief.kb_refs) - set(found):
                raise EvalSetInvalid(brief.key, "kb_ref")
            valid.update(found)
        return sorted(valid)

    def build(
        self, brand_id: str, snapshot: ContextSnapshot, brief: Mapping[str, object]
    ) -> PackageContext:
        """O contexto do pacote de um brief, so a partir do `snapshot` (nunca da vista ativa).

        `brief` e o dicionario guardado em `eval_run.briefs` (`EvalBrief.model_dump`). A ordem
        dos itens da base e a do brief; a dos exemplos, a do prompt no inicio da corrida.
        """
        parsed = EvalBrief.model_validate(brief)
        refs = [ref for ref in parsed.kb_refs if ref in set(snapshot.kb_ids)]
        payload = CopyPayload(
            brief=brief_mapping(parsed),
            channels=list(parsed.canais),
            perfil_marca=self._store.get(snapshot.profile_artifact_id).payload,
            pilares=self._pilares(snapshot),
            accepted_proposals=snapshot.accepted_proposals,
            kb_entries=self._kb.entries_by_ids(brand_id, refs),
            style_examples=self._feedback.examples_by_ids(brand_id, snapshot.feedback_style_ids),
            negative_examples=self._feedback.examples_by_ids(
                brand_id, snapshot.feedback_negative_ids
            ),
            reviewer_notes=None,
            owner_comment=None,
        )
        return PackageContext(payload, None, snapshot.design)

    def _pilares(self, snapshot: ContextSnapshot) -> list[str]:
        record = self._store.get(snapshot.strategy_artifact_id)
        return list(Estrategia.model_validate(record.payload).pilares)
