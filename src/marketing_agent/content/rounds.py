"""Voltas Copywriter -> Revisor de uma geracao: nenhuma escrita na base, so execucoes do motor.

O resultado e o rascunho final com o parecer efetivo, ou o `RunResult` que parou a geracao
(`needs_human` ou limite). `FAILED` nunca volta como resultado: sobe como `AgentRunFailed`.
"""

from dataclasses import replace
from typing import Literal

from marketing_agent.content.loops import CopyPayload, CopywriterLoop, ReviewerLoop, ReviewPayload
from marketing_agent.content.revision_store import GeneratedText
from marketing_agent.content.schemas import RascunhoTexto, Revisao
from marketing_agent.content.specs import ContentSpecs
from marketing_agent.harness.engine import (
    AgentEngine,
    NeedsHuman,
    RunLimitExceeded,
    RunResult,
    RunStatus,
)
from marketing_agent.jobs.errors import describe_error

MAX_REVIEW_ROUNDS = 2  # voltas ao Copywriter: no maximo 3 rascunhos por geracao


class AgentRunFailed(Exception):
    """A execucao de um agente falhou sem ser limite nem pedido de ajuda humana (erro inesperado).

    A mensagem e fixa: a causa fica em `agent_run`/`agent_step` e no log do motor.
    """


def describe_stop(result: RunResult) -> str:
    """`last_error` de uma execucao parada por limite ou por pedido de ajuda humana."""
    if result.status is RunStatus.BLOCKED_LIMIT:
        # A mensagem do motor so tem numeros dos limites (tipo seguro de `describe_error`).
        return describe_error(RunLimitExceeded(result.message or ""))
    return describe_error(NeedsHuman(""))  # o motivo do motor pode citar o modelo: so a classe


def _as_typed[T](result: RunResult, expected: type[T], role: str) -> T | RunResult:
    """Saida tipada de uma execucao bem-sucedida; limites e `NeedsHuman` voltam como `RunResult`."""
    if result.status is RunStatus.SUCCEEDED:
        if not isinstance(result.output, expected):
            raise TypeError(f"{role}: saida de tipo inesperado ({type(result.output).__name__})")
        return result.output
    if result.status is RunStatus.FAILED:
        raise AgentRunFailed(f"a execucao do {role} falhou ({result.run_id or 'sem execucao'})")
    return result


def _effective(review: Revisao) -> Literal["aprovar", "rever", "rejeitar"]:
    """`aprovar` com violacoes e incoerente: trata-se (e grava-se) como `rever`."""
    if review.veredicto == "aprovar" and review.violacoes:
        return "rever"
    return review.veredicto


def _reviewer_notes(review: Revisao) -> str:
    violations = ", ".join(review.violacoes)  # valores do enum fechado
    return f"{review.notas}\nViolacoes: {violations}" if violations else review.notas


class ReviewRounds:
    """Corre o Copywriter e o Revisor ate o parecer deixar de pedir revisao."""

    def __init__(self, engine: AgentEngine, specs: ContentSpecs) -> None:
        """Guarda o motor e os specs dos dois papeis."""
        self._engine = engine
        self._specs = specs

    def run(
        self, brand_id: str, payload: CopyPayload, owner_comment: str | None
    ) -> GeneratedText | RunResult:
        """Copywriter -> Revisor; `rever` repete com as notas, no maximo `MAX_REVIEW_ROUNDS` vezes.

        `rejeitar` para as voltas (reescrever nao resolve uma rejeicao, por exemplo por
        instrucao suspeita): o responsavel decide. Depois da ultima volta o rascunho final segue
        com o parecer, mesmo `rever`.
        """
        notes: str | None = None
        for rounds in range(MAX_REVIEW_ROUNDS + 1):
            round_payload = replace(payload, reviewer_notes=notes, owner_comment=owner_comment)
            spec = self._specs.copywriter
            copy_run = self._engine.run(
                spec, brand_id, "post.copy", CopywriterLoop(), round_payload
            )
            draft = _as_typed(copy_run, RascunhoTexto, "copywriter")
            if isinstance(draft, RunResult):
                return draft
            review = self._review(brand_id, payload, draft)
            if isinstance(review, RunResult):
                return review
            verdict = _effective(review)
            if verdict != "rever" or rounds == MAX_REVIEW_ROUNDS:
                return GeneratedText(
                    draft, verdict, list(review.violacoes), review.notas, rounds, copy_run.run_id
                )
            notes = _reviewer_notes(review)
        raise AssertionError("inalcancavel: a ultima volta devolve sempre")

    def _review(
        self, brand_id: str, payload: CopyPayload, draft: RascunhoTexto
    ) -> Revisao | RunResult:
        review_payload = ReviewPayload(
            draft=draft,
            brief=payload.brief,
            channels=payload.channels,
            kb_entries=payload.kb_entries,
            perfil_marca=payload.perfil_marca,
        )
        spec = self._specs.reviewer
        run = self._engine.run(spec, brand_id, "post.review", ReviewerLoop(), review_payload)
        return _as_typed(run, Revisao, "revisor")
