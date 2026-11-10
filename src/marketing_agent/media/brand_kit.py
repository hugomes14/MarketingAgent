"""Kit de marca versionado, com contraste WCAG 2.x e logotipos imutaveis."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import Clock
from marketing_agent.db.models import AuditLog, Brand, BrandKit
from marketing_agent.media.ingest import NormalizedImage, normalize_logo
from marketing_agent.media.library import AssetNotFound, MediaLibrary

MIN_TITLE_CONTRAST = 4.5
_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
type HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]
type BrandFont = Literal["inter-bold"]


class KitError(ValueError):
    """Pedido de kit invalido, com mensagem segura para o painel."""


def _normalize_color(color: str) -> str:
    if _HEX_COLOR.fullmatch(color) is None:
        raise KitError("A cor deve usar o formato #RRGGBB.")
    return color.upper()


def _luminance(color: str) -> float:
    color = _normalize_color(color)
    channels = (int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
    # WCAG 2.x lineariza sRGB antes de ponderar os canais pela luminancia relativa.
    red, green, blue = (
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    )
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(color_a: str, color_b: str) -> float:
    """Calcula a razao WCAG entre duas cores #RRGGBB, independentemente da ordem."""
    first, second = sorted((_luminance(color_a), _luminance(color_b)))
    return (second + 0.05) / (first + 0.05)


class BrandKitInput(BaseModel):
    """Cores normalizadas com contraste minimo real, e fonte registada."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cor_primaria: HexColor
    cor_secundaria: HexColor
    fonte: BrandFont = "inter-bold"

    @field_validator("cor_primaria", "cor_secundaria")
    @classmethod
    def normalize_color(cls, value: str) -> str:
        """Normaliza hexadecimal sem aceitar espacos ou terminadores de linha."""
        return _normalize_color(value)

    @model_validator(mode="after")
    def require_contrast(self) -> Self:
        """Compara a razao sem arredondar; arredonda apenas a mensagem apresentada."""
        ratio = contrast_ratio(self.cor_secundaria, self.cor_primaria)
        if ratio < MIN_TITLE_CONTRAST:
            raise KitError(f"Contraste {ratio:.2f}: o título exige pelo menos 4.5.")
        return self


@dataclass(frozen=True)
class BrandKitView:
    """Versao publica imutavel; nao expoe chaves privadas de armazenamento."""

    id: str
    version: int
    logo_asset_id: str
    cor_primaria: str
    cor_secundaria: str
    fonte: BrandFont
    contrast: float
    created_at: datetime


def _history_query(brand_id: str) -> Select[BrandKit]:
    return select(BrandKit).where(BrandKit.brand_id == brand_id).order_by(BrandKit.version.desc())


def _view(row: BrandKit) -> BrandKitView:
    if row.fonte != "inter-bold":
        raise KitError("Tipo de letra do kit inválido.")
    return BrandKitView(
        id=row.id,
        version=row.version,
        logo_asset_id=row.logo_asset_id,
        cor_primaria=row.cor_primaria,
        cor_secundaria=row.cor_secundaria,
        fonte="inter-bold",
        contrast=contrast_ratio(row.cor_secundaria, row.cor_primaria),
        created_at=row.created_at,
    )


class BrandKitService:
    """Acrescenta versoes numa transacao serializada pela linha da marca."""

    def __init__(
        self, session_factory: sessionmaker[Session], library: MediaLibrary, clock: Clock
    ) -> None:
        """Recebe sessoes, biblioteca de media e relogio injetados."""
        self._sessions = session_factory
        self._library = library
        self._clock = clock

    def save(
        self, brand_id: str, kit: BrandKitInput, logo_png: bytes | None, actor: str
    ) -> BrandKitView:
        """Cria uma versao sem alterar anteriores; a primeira exige logotipo PNG com alfa."""
        image = normalize_logo(logo_png) if logo_png is not None else None
        with self._sessions.begin() as session:
            # Bloquear a marca funciona tambem quando ainda nao existe nenhuma versao.
            brand = session.scalar(select(Brand.id).where(Brand.id == brand_id).with_for_update())
            if brand is None:
                raise KitError("Marca não encontrada.")
            previous = session.scalar(_history_query(brand_id).limit(1))
            logo_id = self._logo_id(session, brand_id, previous, image, actor)
            row = BrandKit(
                brand_id=brand_id,
                version=previous.version + 1 if previous is not None else 1,
                logo_asset_id=logo_id,
                cor_primaria=kit.cor_primaria,
                cor_secundaria=kit.cor_secundaria,
                fonte=kit.fonte,
                created_by=actor,
                created_at=self._clock.now(),
            )
            session.add(row)
            session.flush()
            self._audit(session, row, actor)
            return _view(row)

    def active(self, brand_id: str) -> BrandKitView | None:
        """Devolve a versao mais alta, ou None quando a marca ainda nao tem kit."""
        with self._sessions() as session:
            row = session.scalar(_history_query(brand_id).limit(1))
            return _view(row) if row is not None else None

    def history(self, brand_id: str) -> list[BrandKitView]:
        """Lista apenas as versoes da marca, da mais recente para a mais antiga."""
        with self._sessions() as session:
            return [_view(row) for row in session.scalars(_history_query(brand_id))]

    def logo_png(self, brand_id: str, kit: BrandKitView) -> bytes:
        """Le o logotipo canonico da versao, comprovando a marca na base de dados."""
        with self._sessions() as session:
            row = session.scalar(
                select(BrandKit).where(BrandKit.brand_id == brand_id, BrandKit.id == kit.id)
            )
            if row is None:
                raise KitError("Kit de marca não encontrado.")
            logo_id = row.logo_asset_id
        try:
            asset = self._library.read_asset(brand_id, logo_id)
        except AssetNotFound as error:
            raise KitError("Logótipo do kit não encontrado.") from error
        if asset.mime != "image/png":
            raise KitError("Logótipo do kit inválido.")
        return asset.data

    def _logo_id(
        self,
        session: Session,
        brand_id: str,
        previous: BrandKit | None,
        image: NormalizedImage | None,
        actor: str,
    ) -> str:
        if image is not None:
            # A deduplicacao reutiliza ativos sem sobrescrever os metadados originais.
            return self._library.store_asset_in(
                session, brand_id, image, kind="logotipo", origem="propria", actor=actor
            )
        if previous is None:
            raise KitError("O primeiro kit exige um logótipo.")
        return previous.logo_asset_id

    def _audit(self, session: Session, row: BrandKit, actor: str) -> None:
        session.add(
            AuditLog(
                actor=actor,
                action="brand_kit.save",
                subject_type="brand_kit",
                subject_id=row.id,
                detail={
                    "version": row.version,
                    "cor_primaria": row.cor_primaria,
                    "cor_secundaria": row.cor_secundaria,
                },
                at=self._clock.now(),
            )
        )
