"""Ativos de um pacote: fundo gerado (normalizado) e imagem final, guardados na transacao do save.

Chamar so DEPOIS de a verificacao de obsoletos passar e com o post bloqueado: as linhas de
`media_asset` nascem na transacao final do pipeline e desfazem-se com ela. Os bytes sao
publicados antes da linha (`MediaLibrary.store_asset_in`), por isso uma interrupcao deixa quando
muito ficheiros orfaos, nunca uma linha sem ficheiro.
"""

from typing import Final

from sqlalchemy.orm import Session

from marketing_agent.content.design import Componente
from marketing_agent.content.revision_store import RevisionImage
from marketing_agent.content.rounds import GeneratedPackage
from marketing_agent.media.ingest import NormalizedImage, normalize_generated
from marketing_agent.media.library import MediaLibrary

AGENT_ACTOR: Final = "agente"


def store_package_image(
    session: Session, library: MediaLibrary, brand_id: str, package: GeneratedPackage
) -> RevisionImage:
    """Guarda o fundo gerado (se o houver) e a imagem final; devolve a parte de imagem da revisao.

    - Fundo gerado: normalizado (PNG sem metadados) e guardado como `componente_gerado`
      (`origem='gerada'`); o `asset_id` do componente `gerada` passa a ser esse ativo (o `ref`
      continua a ser o id do passo). Um fundo reutilizado numa volta posterior e o mesmo objeto
      que o ultimo, por isso guarda-se uma so vez.
    - Imagem final: `final`/`composta`; bytes identicos (mesmo sha256) reutilizam o ativo.
    """
    design = package.design
    components = list(design.components)
    if design.generated is not None:
        background = library.store_asset_in(
            session,
            brand_id,
            normalize_generated(design.generated.data),
            kind="componente_gerado",
            origem="gerada",
            actor=AGENT_ACTOR,
        )
        components = [_with_asset(component, background) for component in components]
    final = design.final
    final_id = library.store_asset_in(
        session,
        brand_id,
        NormalizedImage(final.data, "image/jpeg", final.width, final.height, final.sha256),
        kind="final",
        origem="composta",
        actor=AGENT_ACTOR,
    )
    return RevisionImage(
        asset_id=final_id,
        sha256=final.sha256,
        layout=final.layout,
        layout_version=final.layout_version,
        components=[component.model_dump(mode="json") for component in components],
        is_ai_generated=design.is_ai_generated,
        alt_text=design.draft.alt_text,
        review_image_checked=package.image_checked,
    )


def _with_asset(component: Componente, asset_id: str) -> Componente:
    return (
        component.model_copy(update={"asset_id": asset_id})
        if component.tipo == "gerada"
        else component
    )
