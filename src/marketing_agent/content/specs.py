"""Specs do Copywriter, do Revisor e do Designer, carregados uma vez pelo pipeline e pelo arranque.

O arranque precisa dos mesmos specs para calcular a lease dos trabalhos (ver `startup`), por
isso o carregamento vive aqui e nao dentro do pipeline.
"""

from dataclasses import dataclass

from marketing_agent.agents.copywriter import COPYWRITER_DIR
from marketing_agent.agents.designer import DESIGNER_DIR
from marketing_agent.agents.revisor import REVISOR_DIR
from marketing_agent.content.schemas import RascunhoTexto, Revisao
from marketing_agent.content.visual import RascunhoVisual
from marketing_agent.harness.spec import AgentSpec, load_agent_spec


@dataclass(frozen=True)
class ContentSpecs:
    """Os papeis que geram uma publicacao: texto (Copywriter, Revisor) e imagem (Designer)."""

    copywriter: AgentSpec
    reviewer: AgentSpec
    designer: AgentSpec


def load_content_specs() -> ContentSpecs:
    """Carrega os specs do Copywriter, do Revisor e do Designer; `SpecLoadError` se algum falha."""
    return ContentSpecs(
        copywriter=load_agent_spec(COPYWRITER_DIR / "spec.yaml", {"RascunhoTexto": RascunhoTexto}),
        reviewer=load_agent_spec(REVISOR_DIR / "spec.yaml", {"Revisao": Revisao}),
        designer=load_agent_spec(DESIGNER_DIR / "spec.yaml", {"RascunhoVisual": RascunhoVisual}),
    )
