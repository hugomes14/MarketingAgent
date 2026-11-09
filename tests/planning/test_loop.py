"""PlanLoop: pedido com blocos de dados, validador de contexto e artefacto `plano`."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from marketing_agent.agents.diretor.schemas import Estrategia, PropostaOferta
from marketing_agent.artifacts.store import ArtifactType
from marketing_agent.kb.service import KbEntry
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.types import Message, TextPart
from marketing_agent.planning.loop import PlanLoop, PlanPayload
from marketing_agent.planning.schemas import PlanoSemanal
from marketing_agent.planning.validation import PlanLimits

WEEK = "2026-W46"


def make_strategy() -> Estrategia:
    _, strategy = fake_scenarios.diagnosis_and_strategy()
    assert not isinstance(strategy, Exception)
    return Estrategia.model_validate_json(strategy.text)


def make_entry(entry_id: str) -> KbEntry:
    return KbEntry(
        id=entry_id,
        kind="preco",
        key="cabaz",
        title="Cabaz",
        body="Cabaz semanal.",
        price_amount=Decimal("12.50"),
        price_currency="EUR",
        valid_from=date(2026, 11, 1),
        valid_until=date(2026, 12, 1),
        source_kind="manual",
        source_ref=None,
        is_active=True,
    )


@dataclass
class StubSpec:
    def load_prompt(self, base_dir: object) -> str:
        return "prompt do plano"


@dataclass
class StubContext:
    """Substituto minimo de `RunContext`: devolve o plano do cenario e regista o pedido."""

    plan: PlanoSemanal
    system: str = ""
    messages: list[Message] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    created: list[tuple[ArtifactType, Mapping[str, object], str | None]] = field(
        default_factory=list
    )
    spec: StubSpec = field(default_factory=StubSpec)

    def call_llm(
        self,
        system: str,
        messages: list[Message],
        schema: type[BaseModel],
        extra_validator: Callable[[PlanoSemanal], list[str]] | None = None,
    ) -> PlanoSemanal:
        assert schema is PlanoSemanal and extra_validator is not None
        self.system, self.messages = system, messages
        self.violations = extra_validator(self.plan)
        return self.plan

    def create_artifact(
        self, type: ArtifactType, payload: Mapping[str, object], parent_id: str | None = None
    ) -> str:  # noqa: A002
        self.created.append((type, payload, parent_id))
        return "artefacto"


def make_payload(entries: list[KbEntry]) -> PlanPayload:
    return PlanPayload(
        week=WEEK,
        limits=PlanLimits(max_per_week=5, max_per_day=1),
        perfil_marca={"topicos": {}},
        estrategia=make_strategy(),
        accepted_proposals=[PropostaOferta(proposta="Cabaz semanal", justificacao="Recorrente.")],
        kb_entries=entries,
        parent_id="plano-anterior",
    )


def make_context() -> StubContext:
    [response] = fake_scenarios.weekly_plan(WEEK, slots=3)
    assert not isinstance(response, Exception)
    return StubContext(PlanoSemanal.model_validate_json(response.text))


def run_loop(ctx: StubContext, payload: PlanPayload) -> object:
    return PlanLoop().run(ctx, payload)  # type: ignore[arg-type]


def test_loop_creates_plano_draft_with_parent() -> None:
    ctx = make_context()
    run_loop(ctx, make_payload([]))
    [(type_, payload, parent_id)] = ctx.created
    assert type_ is ArtifactType.PLANO and parent_id == "plano-anterior"
    assert payload == ctx.plan.model_dump(mode="json")
    assert ctx.violations == []


def test_request_wraps_every_input_as_tagged_data() -> None:
    ctx = make_context()
    run_loop(ctx, make_payload([make_entry("kb-1")]))
    [message] = ctx.messages
    [part] = message.content
    assert isinstance(part, TextPart)
    for tag in ("perfil_marca", "estrategia", "propostas_aceites", "base_conhecimento"):
        assert part.text.count(f"<{tag}>") == 1 and part.text.count(f"</{tag}>") == 1
    assert WEEK in part.text and "5 publicacoes por semana" in part.text
    assert ctx.system == "prompt do plano"
    estrategia = json.loads(part.text.split("<estrategia>")[1].split("</estrategia>")[0])
    assert estrategia["experiencias"][0]["indice"] == 0
    assert '"id": "kb-1"' in part.text and "12.50 EUR" in part.text


def test_validator_uses_payload_context() -> None:
    ctx = make_context()
    payload = make_payload([])
    cited = ctx.plan.slots[0].model_copy(update={"kb_refs": ["kb-9"]})
    ctx.plan = ctx.plan.model_copy(update={"slots": [cited, *ctx.plan.slots[1:]]})
    run_loop(ctx, payload)
    assert len(ctx.violations) == 1 and "kb-9" in ctx.violations[0]
    run_loop(ctx, make_payload([make_entry("kb-9")]))
    assert ctx.violations == []
