"""Entradas de media do Designer num pacote: kit, logotipo, fotos e leitura segura das fotos.

Tudo o que falha aqui e "falha de imagem" (o texto segue sozinho): as razoes sao frases fixas do
codigo, sem caminhos, ids de ficheiros nem texto de excecoes.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from marketing_agent.media.brand_kit import BrandKitService, BrandKitView, KitError
from marketing_agent.media.library import AssetNotFound, MediaLibrary, PhotoRef
from marketing_agent.media.store import MediaStoreError

MISSING_KIT_REASON: Final = "Falta o kit de marca."
INVALID_KIT_REASON: Final = "O kit de marca está indisponível."
PHOTO_UNAVAILABLE_MESSAGE: Final = "foto indisponível"


class ImageUnavailable(Exception):
    """Faltam entradas de media para o Designer; `reason` e uma frase fixa para `last_error`."""

    def __init__(self, reason: str) -> None:
        """Guarda a razao fixa (a mesma que a mensagem)."""
        super().__init__(reason)
        self.reason = reason


class PhotoUnavailable(Exception):
    """A foto escolhida deixou de estar ativa (ou legivel) entre a validacao e a leitura.

    A mensagem e fixa: nunca leva o id nem o caminho do ficheiro.
    """

    def __init__(self) -> None:
        """Sem argumentos: a mensagem e sempre `PHOTO_UNAVAILABLE_MESSAGE`."""
        super().__init__(PHOTO_UNAVAILABLE_MESSAGE)


@dataclass(frozen=True)
class DesignInputs:
    """O que o Designer recebe da media: kit, PNG do logotipo, fotos ativas e leitor de fotos."""

    kit: BrandKitView
    logo_png: bytes
    photos: Mapping[str, PhotoRef]
    photo_bytes: Callable[[str], bytes]


class DesignMedia:
    """Le o kit ativo, o logotipo e as fotos ativas de uma marca para o Designer."""

    def __init__(self, library: MediaLibrary, kits: BrandKitService) -> None:
        """Recebe a biblioteca de media e o servico do kit de marca."""
        self._library = library
        self._kits = kits

    def load(self, brand_id: str) -> DesignInputs:
        """Entradas do Designer; `ImageUnavailable` se nao ha kit ativo ou o logotipo falha."""
        kit: BrandKitView | None = None
        logo: bytes | None = None
        is_broken = False
        try:
            kit = self._kits.active(brand_id)
            if kit is not None:
                logo = self._kits.logo_png(brand_id, kit)
        except (KitError, MediaStoreError):
            is_broken = True
        # Levantado fora do `except`: a razao e fixa e nao herda o contexto do erro original.
        if kit is None and not is_broken:
            raise ImageUnavailable(MISSING_KIT_REASON)
        if kit is None or logo is None:
            raise ImageUnavailable(INVALID_KIT_REASON)
        return DesignInputs(
            kit=kit,
            logo_png=logo,
            photos=self._library.photo_refs(brand_id),
            photo_bytes=lambda photo_id: self._read_photo(brand_id, photo_id),
        )

    def _read_photo(self, brand_id: str, photo_id: str) -> bytes:
        """Bytes de uma foto ATIVA da marca; `PhotoUnavailable` (mensagem fixa) senao.

        `read_asset` le tambem ativos desativados (revisoes antigas), por isso a atividade
        verifica-se primeiro: uma foto desativada depois da validacao nao e usada.
        """
        data: bytes | None = None
        try:
            if self._library.active_photo(brand_id, photo_id) is not None:
                data = self._library.read_asset(brand_id, photo_id).data
        except (AssetNotFound, MediaStoreError):
            data = None
        if data is None:
            raise PhotoUnavailable
        return data
