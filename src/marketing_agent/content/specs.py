"""Specs do Copywriter, do Revisor, do Designer e do Simples, carregados uma vez.

O arranque precisa dos mesmos specs para calcular a lease dos trabalhos (ver `startup`), por
isso o carregamento vive aqui e nao dentro do pipeline.
"""

from dataclasses import dataclass

from marketing_agent.agents.copywriter import COPYWRITER_DIR
from marketing_agent.agents.designer import DESIGNER_DIR
from marketing_agent.agents.revisor import REVISOR_DIR
from marketing_agent.agents.simples import SIMPLES_DIR
from marketing_agent.content.schemas import RascunhoTexto, Revisao
from marketing_agent.content.visual import RascunhoVisual
from marketing_agent.evals.simple_schema import PacoteSimples
from marketing_agent.harness.spec import AgentSpec, load_agent_spec


@dataclass(frozen=True)
class ContentSpecs:
    """Os papeis que geram uma publicacao: texto (Copywriter, Revisor) e imagem (Designer).

    `simple` e o papel `simples` da comparacao do Plano 2C: nao e um papel do worker (o arranque
    nao lhe exige rota; sem rota propria usa a do Copywriter, ver `llm.router.ROUTE_FALLBACKS`).
    """

    copywriter: AgentSpec
    reviewer: AgentSpec
    designer: AgentSpec
    simple: AgentSpec


def load_content_specs() -> ContentSpecs:
    """Carrega os specs dos quatro papeis; `SpecLoadError` se algum falha."""
    return ContentSpecs(
        copywriter=load_agent_spec(COPYWRITER_DIR / "spec.yaml", {"RascunhoTexto": RascunhoTexto}),
        reviewer=load_agent_spec(REVISOR_DIR / "spec.yaml", {"Revisao": Revisao}),
        designer=load_agent_spec(DESIGNER_DIR / "spec.yaml", {"RascunhoVisual": RascunhoVisual}),
        simple=load_agent_spec(SIMPLES_DIR / "spec.yaml", {"PacoteSimples": PacoteSimples}),
    )
