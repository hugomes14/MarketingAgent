"""Voltas do pacote texto e imagem (Copywriter, Designer, Revisor): so execucoes do motor.

Nenhuma escrita na base: o resultado e o pacote final (texto, imagem e parecer efetivo), a
falha da parte de imagem (o texto segue sozinho), ou o `RunResult` que parou a geracao
(`needs_human` ou limite do Copywriter ou do Revisor). Um `FAILED` do Copywriter ou do Revisor
nunca volta como resultado: sobe como `AgentRunFailed`.

Teto de custo do pacote: com um `PackageCostGuard`, cada execucao corre com o limite de custo
reduzido ao que sobra do teto. Esgotado o teto, antes de existir uma passagem completa o
resultado e `CostCeilingReached`; depois, segue a ultima passagem completa com uma nota fixa.
"""

from dataclasses import dataclass, replace
from typing import Literal, NoReturn

from marketing_agent.budget.controller import RunCostLimitExceeded
from marketing_agent.content.design import DesignerLoop, DesignPayload, DesignResult
from marketing_agent.content.image_failure import describe_image_stop, is_image_limit
from marketing_agent.content.loops import (
    CopyPayload,
    CopywriterLoop,
    ReviewerLoop,
    ReviewImage,
    ReviewPayload,
)
from marketing_agent.content.package_costs import (
    PackageCostExceeded,
    PackageCostGuard,
    PackageTriggers,
)
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
    Loop,
    NeedsHuman,
    RunLimitExceeded,
    RunResult,
    RunStatus,
)
from marketing_agent.harness.image_calls import GeneratedImage, ImageAllowance
from marketing_agent.harness.run_errors import RunConfigurationFailed
from marketing_agent.harness.spec import AgentSpec
from marketing_agent.images.generation import ImageQuality
from marketing_agent.jobs.errors import describe_error

MAX_REVIEW_ROUNDS = 2  # voltas do pacote: no maximo 3 passagens por geracao
# Nota fixa acrescentada ao parecer quando as voltas param por falta de quota de imagens.
IMAGE_LIMIT_NOTE = "limite de 2 imagens geradas atingido"
# Nota fixa quando uma passagem posterior nao chega ao fim e segue o ultimo pacote completo.
FINAL_REVIEW_UNAVAILABLE_NOTE = "revisao final indisponivel: "
# Razao fixa (depois de `FINAL_REVIEW_UNAVAILABLE_NOTE`) quando o teto de custo do pacote acaba.
COST_CEILING_NOTE = "teto de custo do pacote atingido"
# Frase do codigo (nunca do modelo) para o Designer quando a quota esta a zero.
NO_IMAGE_QUOTA_NOTE = (
    "Ja nao ha imagens geradas disponiveis neste pedido: mantem o tema de fundo atual "
    "ou escolhe uma foto da biblioteca."
)


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
    """Pacote final: texto com o parecer efetivo, imagem final e se o Revisor viu a imagem.

    `reviews` sao os pareceres de todas as passagens completas ate ao pacote enviado (o ultimo
    e o do pacote); `text` guarda o parecer efetivo desse.
    """

    text: GeneratedText
    design: DesignResult
    image_checked: bool
    reviews: tuple[Revisao, ...] = ()


@dataclass(frozen=True)
class ImageFailure:
    """A parte de imagem falhou: o ultimo texto valido segue sozinho (sem parecer).

    `reason` e uma frase fixa (`content.image_failure`); o texto do motor nunca e copiado.
    """

    text: GeneratedText
    reason: str


@dataclass(frozen=True)
class CostCeilingReached:
    """O teto de custo do pacote acabou antes de existir uma passagem completa (nada a enviar)."""


@dataclass(frozen=True)
class _NoImage:
    """Interna: o Designer nao produziu imagem; `is_limit` se foi a quota de imagens."""

    reason: str
    is_limit: bool = False


@dataclass(frozen=True)
class _Complete:
    """Interna: uma passagem completa (texto, imagem e parecer), pronta a seguir para revisao."""

    draft: RascunhoTexto
    copy_run_id: str
    design: DesignResult
    review: Revisao
    rounds: int
    reviews: tuple[Revisao, ...]


type PackageOutcome = GeneratedPackage | ImageFailure | CostCeilingReached | RunResult
# Paragem de um papel de texto: a paragem tal como veio (1.a passagem) ou o ultimo pacote completo.
type _Stop = GeneratedPackage | RunResult


def _designer_output(result: RunResult) -> DesignResult | _NoImage:
    """Imagem do Designer ou a razao fixa da falha; configuracao em falta sobe com o seu texto."""
    if result.status is RunStatus.SUCCEEDED:
        if not isinstance(result.output, DesignResult):
            raise TypeError(f"designer: saida de tipo inesperado ({type(result.output).__name__})")
        return result.output
    if result.status is RunStatus.FAILED:
        _raise_if_configuration(result, "designer")
    return _NoImage(describe_image_stop(result), is_image_limit(result))


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
        triggers: PackageTriggers,
        brand_id: str,
        payload: CopyPayload,
        owner_comment: str | None,
        allowance: ImageAllowance,
        guard: PackageCostGuard | None = None,
    ) -> PackageOutcome:
        """Uma geracao completa do pacote; as execucoes ficam ligadas aos `triggers` do pedido.

        Cada passagem corre o Copywriter (so se o parecer apontou o texto), o Designer e o
        Revisor com a imagem final. Em `rever`, no maximo `MAX_REVIEW_ROUNDS` voltas:
        - alguma violacao que nao seja visual: Copywriter e depois Designer;
        - so violacoes visuais: so o Designer;
        - violacao de `BACKGROUND_VIOLATIONS`: o Designer gera um fundo novo
          (`force_new_background`); se o fundo e gerado e a quota de imagens esta esgotada,
          as voltas param e o pacote segue para o responsavel com as notas e `IMAGE_LIMIT_NOTE`
          (nao se reutiliza um fundo que o Revisor acabou de recusar).
        `rejeitar` para as voltas, como no 2A. Uma falha do Designer na 1.a passagem devolve
        `ImageFailure`. Depois de uma passagem completa, uma falha do Designer (limite de
        imagens incluido), uma paragem ou falha do Copywriter ou do Revisor nao descartam o
        pacote ja revisto e faturado: segue a ultima passagem completa (texto e imagem dessa
        passagem) com uma nota fixa (`IMAGE_LIMIT_NOTE` ou `FINAL_REVIEW_UNAVAILABLE_NOTE`).
        Na 1.a passagem essas paragens e falhas mantem-se: `RunResult` ou `AgentRunFailed`.
        Com `guard`, o teto de custo do pacote vale para cada execucao: ao esgotar-se, segue a
        ultima passagem completa (nota `COST_CEILING_NOTE`) ou, sem ela, `CostCeilingReached`.
        """
        run = _PackageRun(self, triggers, brand_id, payload, owner_comment, allowance, guard)
        return run.execute()


class _PackageRun:
    """Estado de UMA geracao do pacote: o rascunho vigente e as execucoes ja feitas."""

    def __init__(
        self,
        rounds: PackageRounds,
        triggers: PackageTriggers,
        brand_id: str,
        payload: CopyPayload,
        owner_comment: str | None,
        allowance: ImageAllowance,
        guard: PackageCostGuard | None,
    ) -> None:
        self._r = rounds
        self._triggers = triggers
        self._guard = guard
        self._brand_id = brand_id
        self._payload = payload
        self._owner_comment = owner_comment
        self._allowance = allowance
        self._inputs: DesignInputs | None = None
        self._draft: RascunhoTexto | None = None
        self._copy_run_id = ""
        self._last: _Complete | None = None
        self._reviews: list[Revisao] = []

    def execute(self) -> PackageOutcome:
        """Corre as passagens e devolve o pacote final, a falha de imagem ou a paragem.

        O teto de custo do pacote (`PackageCostExceeded`, antes de qualquer chamada) nunca
        descarta um pacote ja revisto e faturado: segue o ultimo, com uma nota fixa.
        """
        try:
            return self._execute_rounds()
        except PackageCostExceeded:
            if self._last is None:
                return CostCeilingReached()
            return self._ship(self._last, _unavailable_note(COST_CEILING_NOTE))

    def _engine_run[TIn, TOut](
        self,
        spec: AgentSpec,
        trigger: str,
        loop: Loop[TIn, TOut],
        payload: TIn,
        allowance: ImageAllowance | None = None,
    ) -> RunResult:
        """Corre uma execucao sob o teto de custo do pacote (sem `guard`, com o `spec` tal e qual).

        `PackageCostExceeded` se o teto ja esgotou ou se a execucao parou por custo com o limite
        reduzido pelo teto: o limite que parou a execucao foi o do pacote, nao o do papel.
        """
        capped = spec if self._guard is None else self._guard.cap(spec)
        result = self._r.engine.run(
            capped, self._brand_id, trigger, loop, payload, image_allowance=allowance
        )
        is_ceiling_binding = capped.limits.max_cost_usd < spec.limits.max_cost_usd
        if is_ceiling_binding and _is_cost_block(result):
            raise PackageCostExceeded
        return result

    def _execute_rounds(self) -> PackageOutcome:
        notes: str | None = None
        design: DesignResult | None = None
        is_copy_needed, is_new_background = True, False
        for rounds in range(MAX_REVIEW_ROUNDS + 1):
            if is_copy_needed:
                copied = self._copy(notes)
                if copied is not None:
                    return copied
            designed = self._design(notes, design, is_new_background)
            if isinstance(designed, _NoImage):
                return self._without_image(rounds, designed)
            design = designed
            review = self._review(design)
            if not isinstance(review, Revisao):
                return review
            self._reviews.append(review)
            self._last = _Complete(
                self._require_draft(),
                self._copy_run_id,
                design,
                review,
                rounds,
                tuple(self._reviews),
            )
            if _effective(review) != "rever" or rounds == MAX_REVIEW_ROUNDS:
                return self._ship(self._last, None)
            violations = set(review.violacoes)
            is_new_background = bool(BACKGROUND_VIOLATIONS & violations)
            if is_new_background and design.generated is not None and self._no_quota():
                return self._ship(self._last, IMAGE_LIMIT_NOTE)
            is_copy_needed = not (violations and violations <= VISUAL_VIOLATIONS)
            notes = _reviewer_notes(review)
        raise AssertionError("inalcancavel: a ultima volta devolve sempre")

    def _no_quota(self) -> bool:
        return self._allowance.remaining == 0

    def _without_image(self, rounds: int, no_image: _NoImage) -> PackageOutcome:
        """Designer sem imagem: 1.a passagem => texto sozinho; depois, o ultimo pacote completo."""
        last = self._last
        if last is None:
            return ImageFailure(self._text_only(rounds), no_image.reason)
        # Decide-se pela classe do limite (`RunResult.limit`), nunca pelo texto da mensagem.
        note = IMAGE_LIMIT_NOTE if no_image.is_limit else _unavailable_note(no_image.reason)
        return self._ship(last, note)

    def _stopped(self, result: RunResult, role: str) -> _Stop:
        """Parado (ajuda humana ou limite): 1.a passagem => a paragem; depois, o ultimo pacote."""
        last = self._last
        if last is None:
            return result
        return self._ship(last, _unavailable_note(_stop_reason(result, role)))

    def _failed(self, role: str) -> GeneratedPackage:
        """Papel de texto falhou depois de uma passagem completa: segue o ultimo pacote."""
        last = self._require_last()
        return self._ship(last, _unavailable_note(f"falha do {role}"))

    def _copy(self, notes: str | None) -> _Stop | None:
        """Corre o Copywriter; `None` se produziu o rascunho, senao a paragem a devolver."""
        payload = replace(self._payload, reviewer_notes=notes, owner_comment=self._owner_comment)
        run = self._engine_run(
            self._r.specs.copywriter, self._triggers.copy, CopywriterLoop(), payload
        )
        try:
            draft = _as_typed(run, RascunhoTexto, "copywriter")
        except AgentRunFailed:
            if self._last is None:
                raise
            return self._failed("copywriter")
        if isinstance(draft, RunResult):
            return self._stopped(draft, "copywriter")
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
            reviewer_notes=self._designer_notes(notes),
            previous_background=_previous_background(previous),
            force_new_background=is_new_background,
        )
        run = self._engine_run(
            self._r.specs.designer,
            self._triggers.design,
            DesignerLoop(),
            payload,
            self._allowance,
        )
        return _designer_output(run)

    def _design_inputs(self) -> DesignInputs:
        if self._inputs is None:
            self._inputs = self._r.media.load(self._brand_id)
        return self._inputs

    def _designer_notes(self, notes: str | None) -> str | None:
        """Notas do Revisor para o Designer; sem quota acrescenta a frase fixa do codigo."""
        if not self._no_quota():
            return notes
        return NO_IMAGE_QUOTA_NOTE if not notes else f"{notes}\n{NO_IMAGE_QUOTA_NOTE}"

    def _review(self, design: DesignResult) -> Revisao | _Stop:
        payload = ReviewPayload(
            draft=self._require_draft(),
            brief=self._payload.brief,
            channels=self._payload.channels,
            kb_entries=self._payload.kb_entries,
            perfil_marca=self._payload.perfil_marca,
            image=ReviewImage.from_design(design),
        )
        run = self._engine_run(
            self._r.specs.reviewer, self._triggers.review, ReviewerLoop(), payload
        )
        try:
            reviewed = _as_typed(run, Revisao, "revisor")
        except AgentRunFailed:
            if self._last is None:
                raise
            return self._failed("revisor")
        if isinstance(reviewed, RunResult):
            return self._stopped(reviewed, "revisor")
        return reviewed

    def _require_last(self) -> _Complete:
        if self._last is None:  # inalcancavel: so se chama depois de uma passagem completa
            raise AssertionError("sem passagem completa para enviar")
        return self._last

    def _require_draft(self) -> RascunhoTexto:
        if self._draft is None:  # inalcancavel: a primeira passagem corre sempre o Copywriter
            raise AssertionError("pacote sem rascunho de texto")
        return self._draft

    def _text_only(self, rounds: int) -> GeneratedText:
        """Texto do rascunho vigente sem parecer (so para a falha de imagem da 1.a passagem)."""
        return GeneratedText(self._require_draft(), None, [], None, rounds, self._copy_run_id)

    def _ship(self, complete: _Complete, note: str | None) -> GeneratedPackage:
        """Pacote de uma passagem completa: o seu texto, a sua imagem e o seu parecer."""
        review = complete.review
        notes = review.notas if not note else f"{review.notas}\n{note}"
        text = GeneratedText(
            complete.draft,
            _effective(review),
            list(review.violacoes),
            notes,
            complete.rounds,
            complete.copy_run_id,
        )
        return GeneratedPackage(text, complete.design, review.imagem_verificada, complete.reviews)


def _is_cost_block(result: RunResult) -> bool:
    """A execucao parou por limite de custo (decide-se pela classe do limite, nunca pelo texto)."""
    return (
        result.status is RunStatus.BLOCKED_LIMIT
        and result.limit is not None
        and issubclass(result.limit, RunCostLimitExceeded)
    )


def _unavailable_note(reason: str) -> str:
    """Nota fixa de que a revisao final nao chegou a existir, com a razao fixa."""
    return f"{FINAL_REVIEW_UNAVAILABLE_NOTE}{reason}"


def _stop_reason(result: RunResult, role: str) -> str:
    """Razao fixa de uma paragem de um papel de texto (nunca o texto do motor)."""
    if result.status is RunStatus.BLOCKED_LIMIT:
        return f"limite do {role} atingido"
    return f"o {role} pediu ajuda humana"


def _previous_background(design: DesignResult | None) -> GeneratedImage | None:
    return design.generated if design is not None else None
