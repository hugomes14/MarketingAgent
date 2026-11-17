"""Esquema de saida do percurso simples: o pacote inteiro numa so resposta do modelo.

Vive num modulo proprio (e nao em `simple_path`) para o carregamento dos specs
(`content.specs`) o poder importar sem ciclo com as voltas do pacote.
"""

from pydantic import BaseModel, ConfigDict

from marketing_agent.content.schemas import RascunhoTexto
from marketing_agent.content.visual import RascunhoVisual


class PacoteSimples(BaseModel):
    """Texto e escolha visual de uma publicacao, devolvidos numa so resposta do modelo.

    Aninha os esquemas dos dois papeis do percurso especializado, por isso os validadores de
    codigo aplicam-se a cada parte sem adaptacao.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    texto: RascunhoTexto
    visual: RascunhoVisual
