"""Entradas de media do Designer num pacote: kit, logotipo, fotos e leitura segura das fotos.

Tudo o que falha aqui e "falha de imagem" (o texto segue sozinho): as razoes sao frases fixas do
codigo, sem caminhos, ids de ficheiros nem texto de excecoes.

Por omissao a media le-se ao vivo (fotos ativas e kit mais recente). Com um `DesignSnapshot`
(`capture_design_snapshot`) as entradas ficam fixadas: so as fotos do snapshot (mesmo desativadas
depois: os ativos sao imutaveis e continuam legiveis) e exatamente a versao do kit fixada. E a
fonte unica que os dois percursos da comparacao leem, para verem o mesmo contexto.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.db.models import BrandKit, MediaAsset
from marketing_agent.media.brand_kit import BrandKitService, BrandKitView, KitError
from marketing_agent.media.library import AssetNotFound, MediaLibrary, PhotoRef
from marketing_agent.media.store import MediaStoreError

MISSING_KIT_REASON: Final = "Falta o kit de marca."
INVALID_KIT_REASON: Final = "O kit de marca está indisponível."
PHOTO_UNAVAILABLE_MESSAGE: Final = "foto indisponível"
SNAPSHOT_INVALID_REASON: Final = "O contexto de desenho fixado é inválido."
MAX_SNAPSHOT_PHOTOS: Final = 50  # o mesmo limite de `MediaLibrary.photo_refs`


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
class DesignSnapshot:
    """Entradas de desenho fixadas: ids das fotos oferecidas e versao do kit (None = sem kit)."""

    photo_ids: frozenset[str]
    kit_version: int | None


def capture_design_snapshot(
    session_factory: sessionmaker[Session], brand_id: str
) -> DesignSnapshot:
    """Fixa as fotos ativas (as mesmas e pela mesma ordem de `photo_refs`) e o kit mais recente."""
    photos = (
        select(MediaAsset.id)
        .where(
            MediaAsset.brand_id == brand_id,
            MediaAsset.kind == "foto",
            MediaAsset.is_active.is_(True),
        )
        .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
        .limit(MAX_SNAPSHOT_PHOTOS)
    )
    kit = select(func.max(BrandKit.version)).where(BrandKit.brand_id == brand_id)
    with session_factory() as session:
        return DesignSnapshot(frozenset(session.scalars(photos)), session.scalar(kit))


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

    def load(self, brand_id: str, snapshot: DesignSnapshot | None = None) -> DesignInputs:
        """Entradas do Designer; `ImageUnavailable` se nao ha kit ativo ou o logotipo falha.

        Com `snapshot` so entram as fotos e a versao do kit fixadas (ver o modulo).
        """
        kit, logo = self._load_kit(brand_id, snapshot)
        if snapshot is None:
            photos = self._library.photo_refs(brand_id)
            reader = partial(self._read_photo, brand_id)
        else:
            photos = self._pinned_photos(brand_id, snapshot)
            reader = partial(self._read_pinned, brand_id, photos)
        return DesignInputs(kit=kit, logo_png=logo, photos=photos, photo_bytes=reader)

    def _load_kit(
        self, brand_id: str, snapshot: DesignSnapshot | None
    ) -> tuple[BrandKitView, bytes]:
        """O kit (ativo ou o fixado) e o seu logotipo; `ImageUnavailable` com razao fixa."""
        kit: BrandKitView | None = None
        logo: bytes | None = None
        is_broken = False
        try:
            kit = (
                self._kits.active(brand_id)
                if snapshot is None
                else self._pinned_kit(brand_id, snapshot)
            )
            if kit is not None:
                logo = self._kits.logo_png(brand_id, kit)
        except (KitError, MediaStoreError):
            is_broken = True
        # Levantado fora do `except`: a razao e fixa e nao herda o contexto do erro original.
        if kit is None and not is_broken and (snapshot is None or snapshot.kit_version is None):
            raise ImageUnavailable(MISSING_KIT_REASON)
        if kit is None or logo is None:
            raise ImageUnavailable(INVALID_KIT_REASON)
        return kit, logo

    def _pinned_kit(self, brand_id: str, snapshot: DesignSnapshot) -> BrandKitView | None:
        """A versao fixada do kit; None sem kit fixado. Versao em falta: `KitError` (fixo)."""
        if snapshot.kit_version is None:
            return None
        kit = self._kits.get_version(brand_id, snapshot.kit_version)
        if kit is None:
            raise KitError("Kit de marca não encontrado.")
        return kit

    def _pinned_photos(self, brand_id: str, snapshot: DesignSnapshot) -> dict[str, PhotoRef]:
        """As fotos do snapshot (ativas ou nao); um id que nao e foto desta marca invalida-o."""
        photos = self._library.photo_refs_by_ids(brand_id, snapshot.photo_ids)
        if len(photos) != len(snapshot.photo_ids):
            raise ImageUnavailable(SNAPSHOT_INVALID_REASON)
        return photos

    def _read_pinned(self, brand_id: str, photos: Mapping[str, PhotoRef], photo_id: str) -> bytes:
        """Bytes de uma foto FIXADA, mesmo desativada depois; `PhotoUnavailable` se de fora."""
        if photo_id not in photos:
            raise PhotoUnavailable
        try:
            return self._library.read_asset(brand_id, photo_id).data
        except (AssetNotFound, MediaStoreError):
            raise PhotoUnavailable from None

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
