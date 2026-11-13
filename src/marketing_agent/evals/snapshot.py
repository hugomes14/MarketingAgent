"""Leitura tipada do conteudo persistido dos pacotes, sem expor a proveniencia ao painel."""

from pydantic import BaseModel, ConfigDict, Field

from marketing_agent.content.schemas import RascunhoTexto


class SnapshotImage(BaseModel):
    """Campos visuais necessarios para apresentar os dois lados de forma simetrica."""

    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)
    alt_text: str
    gerada_por_ia: bool


class SnapshotReview(BaseModel):
    """Parecer sem notas livres, usado apenas no relatorio depois da revelacao."""

    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)
    veredicto: str
    violacoes: list[str]


class PackageSnapshot(BaseModel):
    """Projecao tipada de `revision_snapshot`; ignora proveniencia que nao e necessaria."""

    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)
    texto: RascunhoTexto
    imagem: SnapshotImage
    pareceres: list[SnapshotReview] = Field(default_factory=list)
