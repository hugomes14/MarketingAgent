"""Voltas do pacote texto e imagem (Copywriter, Designer, Revisor): so execucoes do motor.

Nenhuma escrita na base: o resultado e o pacote final (texto, imagem e parecer efetivo), a
falha da parte de imagem (o texto segue sozinho), ou o `RunResult` que parou a geracao
(`needs_human` ou limite do Copywriter ou do Revisor). Um `FAILED` do Copywriter ou do Revisor
nunca volta como resultado: sobe como `AgentRunFailed`.
"""

from dataclasses import dataclass, replace
from typing import Literal, NoReturn

from marketing_agent.content.design import DesignerLoop, DesignPayload, DesignResult
from marketing_agent.content.image_failure import describe_image_stop
from marketing_agent.content.loops import (
    CopyPayload,
    CopywriterLoop,
    ReviewerLoop,
    ReviewImage,
    ReviewPayload,
)
from marketing_agent.content.package_costs import run_trigger
from marketing_agent.content.package_media import DesignInputs, DesignMedia, ImageUnavailable
from marketing_agent.content.revision_store import GeneratedText
from marketing_agent.content.schemas import (
    BACKGROUND_VIOLATIONS,
    VISUAL_VIOLATIONS,
    RascunhoTexto,
    Revisao,
)
from marketing_agent.content.specs import ContentSpecs
from marketing_agent.harness.engine import (
    AgentEngine,
    NeedsHuman,
    RunLimitExceeded,
    RunResult,
    RunStatus,
)
from marketing_agent.harness.image_calls import GeneratedImage, ImageAllowance
from marketing_agent.harness.run_errors import RunConfigurationFailed
from marketing_agent.images.generation import ImageQuality
from marketing_agent.jobs.errors import describe_error

MAX_REVIEW_ROUNDS = 2  # voltas do pacote: no maximo 3 passagens por geracao
# Nota fixa acrescentada ao parecer quando as voltas param por falta de quota de imagens.
IMAGE_LIMIT_NOTE = "limite de 2 imagens geradas atingido"


class AgentRunFailed(Exception):
    """A execucao de um agente falhou sem ser limite nem pedido de ajuda humana (erro inesperado).

    A mensagem e fixa: a causa fica em `agent_run`/`agent_step` e no log do motor.
    """


def describe_stop(result: RunResult) -> str:
    """`last_error` de uma execucao parada por limite ou por pedido de ajuda humana."""
    if result.status is RunStatus.BLOCKED_LIMIT:
        # So o nome da classe do limite (distingue custo de passos/duracao); sem mensagem.
        return describe_error((result.limit or RunLimitExceeded)())
    return describe_error(NeedsHuman(""))  # o motivo do motor pode citar o modelo: so a classe


def _as_typed[T](result: RunResult, expected: type[T], role: str) -> T | RunResult:
    """Saida tipada de uma execucao bem-sucedida; limites e `NeedsHuman` voltam como `RunResult`."""
    if result.status is RunStatus.SUCCEEDED:
        if not isinstance(result.output, expected):
            raise TypeError(f"{role}: saida de tipo inesperado ({type(result.output).__name__})")
        return result.output
    if result.status is RunStatus.FAILED:
        _raise_failed(result, role)
    return result


# Falhas de configuracao do motor cuja mensagem so tem nomes de papel, fornecedor, modelo e
# capacidades (ver `llm.router` e `budget.pricing`): sao as unicas que `last_error` pode mostrar.
_CONFIGURATION_FAILURES = ("MissingCapability", "RouteNotConfigured", "UnknownPrice")


def _raise_if_configuration(result: RunResult, role: str) -> None:
    """Falha de configuracao (so nomes de papel, modelo e capacidades) sobe com o seu texto."""
    message = result.message or ""
    if any(message.startswith(f"{name}: ") for name in _CONFIGURATION_FAILURES):
        raise RunConfigurationFailed(f"{role}: {message}")


def _raise_failed(result: RunResult, role: str) -> NoReturn:
    """Falha de configuracao (sem texto do modelo) ou erro inesperado de mensagem fixa."""
    _raise_if_configuration(result, role)
    raise AgentRunFailed(f"a execucao do {role} falhou ({result.run_id or 'sem execucao'})")


def _effective(review: Revisao) -> Literal["aprovar", "rever", "rejeitar"]:
    """`aprovar` com violacoes e incoerente: trata-se (e grava-se) como `rever`."""
    if review.veredicto == "aprovar" and review.violacoes:
        return "rever"
    return review.veredicto


def _reviewer_notes(review: Revisao) -> str:
    violations = ", ".join(review.violacoes)  # valores do enum fechado
    return f"{review.notas}\nViolacoes: {violations}" if violations else review.notas


@dataclass(frozen=True)
class GeneratedPackage:
    """Pacote final: texto com o parecer efetivo, imagem final e se o Revisor viu a imagem."""

    text: GeneratedText
    design: DesignResult
    image_checked: bool


@dataclass(frozen=True)
class ImageFailure:
    """A parte de imagem falhou: o ultimo texto valido segue sozinho (sem parecer).

    `reason` e uma frase fixa (`content.image_failure`); o texto do motor nunca e copiado.
    """

    text: GeneratedText
    reason: str


@dataclass(frozen=True)
class _NoImage:
    """Interna: o Designer nao produziu imagem."""

    reason: str


type PackageOutcome = GeneratedPackage | ImageFailure | RunResult


def _designer_output(result: RunResult) -> DesignResult | _NoImage:
    """Imagem do Designer ou a razao fixa da falha; configuracao em falta sobe com o seu texto."""
    if result.status is RunStatus.SUCCEEDED:
        if not isinstance(result.output, DesignResult):
            raise TypeError(f"designer: saida de tipo inesperado ({type(result.output).__name__})")
        return result.output
    if result.status is RunStatus.FAILED:
        _raise_if_configuration(result, "designer")
    return _NoImage(describe_image_stop(result))


class PackageRounds:
    """Corre Copywriter, Designer e Revisor ate o parecer deixar de pedir revisao."""

    def __init__(
        self,
        engine: AgentEngine,
        specs: ContentSpecs,
        media: DesignMedia,
        image_quality: ImageQuality,
    ) -> None:
        """Guarda o motor, os specs dos tres papeis, a media do Designer e a qualidade."""
        self.engine = engine
        self.specs = specs
        self.media = media
        self.image_quality = image_quality

    def run(
        self,
        job_id: str,
        brand_id: str,
        payload: CopyPayload,
        owner_comment: str | None,
        allowance: ImageAllowance,
    ) -> PackageOutcome:
        """Uma geracao completa do pacote; as execucoes ficam ligadas ao `job_id` (trigger).

        Cada passagem corre o Copywriter (so se o parecer apontou o texto), o Designer e o
        Revisor com a imagem final. Em `rever`, no maximo `MAX_REVIEW_ROUNDS` voltas:
        - alguma violacao que nao seja visual: Copywriter e depois Designer;
        - so violacoes visuais: so o Designer;
        - violacao de `BACKGROUND_VIOLATIONS`: o Designer gera um fundo novo
          (`force_new_background`); se o fundo e gerado e a quota de imagens esta esgotada,
          as voltas param e o pacote segue para o responsavel com as notas e `IMAGE_LIMIT_NOTE`
          (nao se reutiliza um fundo que o Revisor acabou de recusar).
        `rejeitar` para as voltas, como no 2A. Uma falha do Designer devolve `ImageFailure`.
        """
        run = _PackageRun(self, job_id, brand_id, payload, owner_comment, allowance)
        return run.execute()


class _PackageRun:
    """Estado de UMA geracao do pacote: o rascunho vigente e as execucoes ja feitas."""

    def __init__(
        self,
        rounds: PackageRounds,
        job_id: str,
        brand_id: str,
        payload: CopyPayload,
        owner_comment: str | None,
        allowance: ImageAllowance,
    ) -> None:
        self._r = rounds
        self._job_id = job_id
        self._brand_id = brand_id
        self._payload = payload
        self._owner_comment = owner_comment
        self._allowance = allowance
        self._inputs: DesignInputs | None = None
        self._draft: RascunhoTexto | None = None
        self._copy_run_id = ""

    def execute(self) -> PackageOutcome:
        notes: str | None = None
        design: DesignResult | None = None
        is_copy_needed, is_new_background = True, False
        for rounds in range(MAX_REVIEW_ROUNDS + 1):
            if is_copy_needed:
                copied = self._copy(notes)
                if isinstance(copied, RunResult):
                    return copied
            designed = self._design(notes, design, is_new_background)
            if isinstance(designed, _NoImage):
                return ImageFailure(self._text(rounds, None), designed.reason)
            design = designed
            review = self._review(design)
            if isinstance(review, RunResult):
                return review
            verdict = _effective(review)
            if verdict != "rever" or rounds == MAX_REVIEW_ROUNDS:
                return self._package(rounds, design, review, None)
            violations = set(review.violacoes)
            is_new_background = bool(BACKGROUND_VIOLATIONS & violations)
            if (
                is_new_background
                and design.generated is not None
                and self._allowance.remaining == 0
            ):
                return self._package(rounds, design, review, IMAGE_LIMIT_NOTE)
            is_copy_needed = not (violations and violations <= VISUAL_VIOLATIONS)
            notes = _reviewer_notes(review)
        raise AssertionError("inalcancavel: a ultima volta devolve sempre")

    def _copy(self, notes: str | None) -> RunResult | None:
        payload = replace(self._payload, reviewer_notes=notes, owner_comment=self._owner_comment)
        run = self._r.engine.run(
            self._r.specs.copywriter,
            self._brand_id,
            run_trigger("copy", self._job_id),
            CopywriterLoop(),
            payload,
        )
        draft = _as_typed(run, RascunhoTexto, "copywriter")
        if isinstance(draft, RunResult):
            return draft
        self._draft, self._copy_run_id = draft, run.run_id
        return None

    def _design(
        self, notes: str | None, previous: DesignResult | None, is_new_background: bool
    ) -> DesignResult | _NoImage:
        draft = self._require_draft()
        try:
            inputs = self._design_inputs()
        except ImageUnavailable as unavailable:
            return _NoImage(unavailable.reason)
        payload = DesignPayload(
            brief=self._payload.brief,
            texto=draft,
            kit=inputs.kit,
            logo_png=inputs.logo_png,
            photos=inputs.photos,
            photo_bytes=inputs.photo_bytes,
            image_quality=self._r.image_quality,
            reviewer_notes=notes,
            previous_background=_previous_background(previous),
            force_new_background=is_new_background,
        )
        run = self._r.engine.run(
            self._r.specs.designer,
            self._brand_id,
            run_trigger("design", self._job_id),
            DesignerLoop(),
            payload,
            image_allowance=self._allowance,
        )
        return _designer_output(run)

    def _design_inputs(self) -> DesignInputs:
        if self._inputs is None:
            self._inputs = self._r.media.load(self._brand_id)
        return self._inputs

    def _review(self, design: DesignResult) -> Revisao | RunResult:
        payload = ReviewPayload(
            draft=self._require_draft(),
            brief=self._payload.brief,
            channels=self._payload.channels,
            kb_entries=self._payload.kb_entries,
            perfil_marca=self._payload.perfil_marca,
            image=ReviewImage.from_design(design),
        )
        run = self._r.engine.run(
            self._r.specs.reviewer,
            self._brand_id,
            run_trigger("review", self._job_id),
            ReviewerLoop(),
            payload,
        )
        return _as_typed(run, Revisao, "revisor")

    def _require_draft(self) -> RascunhoTexto:
        if self._draft is None:  # inalcancavel: a primeira passagem corre sempre o Copywriter
            raise AssertionError("pacote sem rascunho de texto")
        return self._draft

    def _text(self, rounds: int, review: Revisao | None, note: str | None = None) -> GeneratedText:
        draft = self._require_draft()
        if review is None:
            return GeneratedText(draft, None, [], None, rounds, self._copy_run_id)
        notes = review.notas if not note else f"{review.notas}\n{note}"
        return GeneratedText(
            draft, _effective(review), list(review.violacoes), notes, rounds, self._copy_run_id
        )

    def _package(
        self, rounds: int, design: DesignResult, review: Revisao, note: str | None
    ) -> GeneratedPackage:
        return GeneratedPackage(self._text(rounds, review, note), design, review.imagem_verificada)


def _previous_background(design: DesignResult | None) -> GeneratedImage | None:
    return design.generated if design is not None else None
