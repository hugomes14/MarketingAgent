"""Ciclo do plano semanal: uma chamada estruturada ao Diretor e um artefacto `plano` em `draft`.

O artefacto fica `draft`; submeter, aprovar e criar posts e trabalhos e decisao do servico.
Os tetos, a semana ISO, as experiencias e a base so se verificam em codigo, via
`extra_validator` do motor, nunca confiando na saida do LLM.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from marketing_agent.agents.diretor import DIRETOR_DIR
from marketing_agent.agents.diretor.schemas import Estrategia, PropostaOferta
from marketing_agent.artifacts.store import ArtifactRecord, ArtifactType
from marketing_agent.harness.engine import ExtraValidator, RunContext
from marketing_agent.kb.service import KbEntry
from marketing_agent.planning.schemas import PlanoSemanal
from marketing_agent.planning.validation import PlanLimits, validate_plan
from marketing_agent.strategy.loops import build_request, wrap_data

PLAN_REQUEST = (
    "Produz agora o plano da semana {week}, com no maximo {max_per_week} publicacoes por semana "
    "e {max_per_day} por dia, com base nos dados abaixo."
)


@dataclass(frozen=True)
class PlanPayload:
    """Entrada do plano: semana, tetos, dados ativos e o plano ativo da semana (pai).

    `kb_entries` leva so itens ativos e validos em todos os dias da semana `week`: sao os unicos
    que o plano pode citar em `kb_refs`. `accepted_proposals` leva so as propostas de oferta que
    o responsavel aceitou.
    """

    week: str
    limits: PlanLimits
    perfil_marca: Mapping[str, object]
    estrategia: Estrategia
    accepted_proposals: Sequence[PropostaOferta]
    kb_entries: Sequence[KbEntry]
    parent_id: str | None


def _strategy_view(estrategia: Estrategia) -> dict[str, object]:
    """Estrategia como dado, com o indice (a partir de 0) que `experiment_index` referencia."""
    view = estrategia.model_dump(mode="json")
    view["experiencias"] = [
        {"indice": index, **experiencia.model_dump(mode="json")}
        for index, experiencia in enumerate(estrategia.experiencias)
    ]
    return view


def _kb_view(entry: KbEntry) -> dict[str, object]:
    price = None if entry.price_amount is None else f"{entry.price_amount} {entry.price_currency}"
    return {
        "id": entry.id,
        "tipo": entry.kind,
        "titulo": entry.title,
        "texto": entry.body,
        "preco": price,
        "valido_de": entry.valid_from.isoformat(),
        "valido_ate": entry.valid_until.isoformat() if entry.valid_until else None,
    }


def _plan_validator(payload: PlanPayload) -> ExtraValidator[PlanoSemanal]:
    """Validador de contexto: a semana, os tetos, as experiencias e a base desta chamada."""
    valid_kb_refs = frozenset(entry.id for entry in payload.kb_entries)

    def check(plan: PlanoSemanal) -> list[str]:
        return validate_plan(
            plan,
            week=payload.week,
            limits=payload.limits,
            experiment_count=len(payload.estrategia.experiencias),
            valid_kb_refs=valid_kb_refs,
        )

    return check


class PlanLoop:
    """Gera o plano semanal e cria o artefacto `plano` em `draft`."""

    def run(self, ctx: RunContext, payload: PlanPayload) -> ArtifactRecord:
        """Chama o LLM com os dados ativos; violacoes do codigo viram repeticoes do motor."""
        instruction = PLAN_REQUEST.format(
            week=payload.week,
            max_per_week=payload.limits.max_per_week,
            max_per_day=payload.limits.max_per_day,
        )
        messages = build_request(
            instruction,
            wrap_data("perfil_marca", payload.perfil_marca),
            wrap_data("estrategia", _strategy_view(payload.estrategia)),
            wrap_data(
                "propostas_aceites",
                {"propostas": [p.model_dump(mode="json") for p in payload.accepted_proposals]},
            ),
            wrap_data("base_conhecimento", {"itens": [_kb_view(e) for e in payload.kb_entries]}),
        )
        output = ctx.call_llm(
            ctx.spec.load_prompt(DIRETOR_DIR),
            messages,
            PlanoSemanal,
            extra_validator=_plan_validator(payload),
        )
        return ctx.create_artifact(
            ArtifactType.PLANO, output.model_dump(mode="json"), parent_id=payload.parent_id
        )
