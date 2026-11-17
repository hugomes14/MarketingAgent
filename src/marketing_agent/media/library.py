"""Biblioteca de fotos e media imutavel, isolada por marca."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.dml import ReturningInsert

from marketing_agent.clock import Clock
from marketing_agent.db.base import new_id
from marketing_agent.db.models import MEDIA_KINDS, MEDIA_ORIGINS, AuditLog, MediaAsset
from marketing_agent.media.ingest import NormalizedImage, normalize_photo
from marketing_agent.media.store import MediaStore, StoredObject

type PhotoSubject = Literal["produto", "instalacao", "pessoas", "ambiente", "outro"]
type PhotoOrigin = Literal["propria", "banco_licenciado", "cliente_autorizado"]
type AssetMime = Literal["image/jpeg", "image/png"]


class MediaError(ValueError):
    """Dados invalidos; mensagem fixa que pode ser apresentada no painel."""

    def __init__(self) -> None:
        """Nunca inclui metadados ou bytes do responsavel na mensagem."""
        super().__init__("Dados de media inválidos.")


class AssetNotFound(LookupError):
    """Ativo inexistente ou nao pertencente a marca pedida."""

    def __init__(self) -> None:
        """Nao distingue uma referencia inexistente de uma referencia de outra marca."""
        super().__init__("Media não encontrada.")


class PhotoUpload(BaseModel):
    """Metadados declarados, com autorizacao explicita quando ha pessoas."""

    model_config = ConfigDict(extra="forbid")

    mostra: PhotoSubject
    descricao: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ]
    origem: PhotoOrigin
    licenca_nota: str | None = Field(default=None, max_length=300)
    tem_autorizacao_pessoas: bool

    @model_validator(mode="after")
    def require_people_authorization(self) -> Self:
        """Recusa fotos de pessoas sem autorizacao declarada."""
        if self.mostra == "pessoas" and not self.tem_autorizacao_pessoas:
            raise MediaError()
        return self


@dataclass(frozen=True)
class PhotoView:
    """Foto da biblioteca, sem chave de armazenamento nem hash expostos."""

    id: str
    mostra: PhotoSubject
    descricao: str
    origem: PhotoOrigin
    licenca_nota: str | None
    tem_autorizacao_pessoas: bool
    is_active: bool
    width: int
    height: int
    created_at: datetime


@dataclass(frozen=True)
class PhotoRef:
    """Contexto minimo de uma foto ativa para o Designer."""

    id: str
    mostra: PhotoSubject
    descricao: str
    tem_autorizacao_pessoas: bool


@dataclass(frozen=True)
class AssetBytes:
    """Bytes imutaveis de media, incluindo revisoes com ativos desativados."""

    mime: AssetMime
    data: bytes


def _photo_view(row: MediaAsset) -> PhotoView:
    # As linhas de foto nascem de PhotoUpload; os CHECKs preservam o contrato na base.
    if row.kind != "foto" or row.mostra is None or row.descricao is None:
        raise MediaError()
    return PhotoView(
        id=row.id,
        mostra=cast(PhotoSubject, row.mostra),
        descricao=row.descricao,
        origem=cast(PhotoOrigin, row.origem),
        licenca_nota=row.licenca_nota,
        tem_autorizacao_pessoas=row.tem_autorizacao_pessoas,
        is_active=row.is_active,
        width=row.width,
        height=row.height,
        created_at=row.created_at,
    )


def _photo_ref(row: MediaAsset) -> PhotoRef:
    return PhotoRef(
        row.id,
        cast(PhotoSubject, row.mostra),
        cast(str, row.descricao),
        row.tem_autorizacao_pessoas,
    )


def _photo_query(brand_id: str, active_only: bool) -> Select[MediaAsset]:
    statement = select(MediaAsset).where(MediaAsset.brand_id == brand_id, MediaAsset.kind == "foto")
    if active_only:
        statement = statement.where(MediaAsset.is_active.is_(True))
    # O id desempata instantes identicos para o contexto ter ordem deterministica.
    return statement.order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())


class MediaLibrary:
    """Guarda bytes antes da linha; uma interrupcao deixa apenas um objeto orfao."""

    def __init__(
        self, session_factory: sessionmaker[Session], store: MediaStore, clock: Clock
    ) -> None:
        """Recebe sessoes, armazenamento imutavel e relogio injetados."""
        self._sessions = session_factory
        self._store = store
        self._clock = clock

    def upload_photo(self, brand_id: str, data: bytes, meta: PhotoUpload, actor: str) -> PhotoView:
        """Normaliza e obtem ou cria a foto, preservando metadados de duplicados.

        Carregar de novo uma foto desativada reativa-a (com auditoria), mantendo os
        metadados guardados; e a unica forma de a recuperar pelo painel.
        """
        image = normalize_photo(data)
        with self._sessions.begin() as session:
            row = self._store_in(session, brand_id, image, "foto", meta.origem, actor, meta)
            # O hash e unico mesmo entre tipos: um final nao pode virar foto implicitamente.
            view = _photo_view(row)
            return self._reactivate(session, row, actor) if not view.is_active else view

    def deactivate(self, brand_id: str, asset_id: str, actor: str) -> None:
        """Desativa apenas fotos da marca, sem apagar bytes ou repetir auditorias."""
        with self._sessions.begin() as session:
            row = session.scalar(
                select(MediaAsset)
                .where(
                    MediaAsset.brand_id == brand_id,
                    MediaAsset.id == asset_id,
                    MediaAsset.kind == "foto",
                )
                .with_for_update()
            )
            if row is None:
                raise AssetNotFound()
            if row.is_active:
                row.is_active = False
                self._audit(session, row, "media.deactivate", actor)

    def list_photos(
        self, brand_id: str, mostra: PhotoSubject | None = None, *, active_only: bool = True
    ) -> list[PhotoView]:
        """Lista fotos da marca por data decrescente, opcionalmente por assunto."""
        statement = _photo_query(brand_id, active_only)
        if mostra is not None:
            statement = statement.where(MediaAsset.mostra == mostra)
        with self._sessions() as session:
            return [_photo_view(row) for row in session.scalars(statement)]

    def photo_refs(self, brand_id: str, limit: int = 50) -> dict[str, PhotoRef]:
        """Devolve as fotos ativas recentes; limita sempre o contexto a 50 referencias."""
        if limit < 0:
            raise MediaError()
        with self._sessions() as session:
            photos = session.scalars(_photo_query(brand_id, True).limit(min(limit, 50)))
            return {row.id: _photo_ref(row) for row in photos}

    def active_photo(self, brand_id: str, asset_id: str) -> PhotoRef | None:
        """A foto ativa da marca com este id, ou None.

        Inexistente, desativada, de outra marca ou de outro tipo (logotipo) dao todas None.
        """
        with self._sessions() as session:
            row = session.scalar(_photo_query(brand_id, True).where(MediaAsset.id == asset_id))
            return _photo_ref(row) if row is not None else None

    def store_asset_in(
        self,
        session: Session,
        brand_id: str,
        image: NormalizedImage,
        *,
        kind: str,
        origem: str,
        actor: str,
    ) -> str:
        """Obtem ou cria por marca/hash na transacao recebida, sem fazer commit.

        Reutiliza qualquer tipo existente sem alterar a sua classificacao. Criar
        uma foto nova requer os metadados de upload_photo.
        """
        if kind not in MEDIA_KINDS or origem not in MEDIA_ORIGINS:
            raise MediaError()
        existing = session.scalar(
            select(MediaAsset).where(
                MediaAsset.brand_id == brand_id, MediaAsset.sha256 == image.sha256
            )
        )
        if existing is not None:
            return existing.id
        if kind == "foto":
            raise MediaError()
        return self._store_in(session, brand_id, image, kind, origem, actor).id

    def read_asset(self, brand_id: str, asset_id: str) -> AssetBytes:
        """Le qualquer tipo de ativo da marca, mesmo desativado em revisoes antigas."""
        with self._sessions() as session:
            row = session.scalar(
                select(MediaAsset).where(
                    MediaAsset.brand_id == brand_id,
                    MediaAsset.id == asset_id,
                )
            )
            if row is None:
                raise AssetNotFound()
            return AssetBytes(cast(AssetMime, row.mime), self._store.read(row.storage_key))

    def _reactivate(self, session: Session, row: MediaAsset, actor: str) -> PhotoView:
        # Reler com bloqueio: dois reuploads concorrentes auditam a reativacao uma so vez.
        session.refresh(row, with_for_update=True)
        if not row.is_active:
            row.is_active = True
            self._audit(session, row, "media.reactivate", actor)
        return _photo_view(row)

    def _store_in(
        self,
        session: Session,
        brand_id: str,
        image: NormalizedImage,
        kind: str,
        origem: str,
        actor: str,
        meta: PhotoUpload | None = None,
    ) -> MediaAsset:
        stored = self._store.put(
            brand_id, image.data, "jpg" if image.mime == "image/jpeg" else "png"
        )
        statement = self._asset_insert(brand_id, image, stored, kind, origem, actor, meta)
        row = session.scalars(statement).one_or_none()
        if row is not None:
            self._audit(session, row, "media.upload", actor)
            return row
        # Em READ COMMITTED a consulta ve a linha concorrente que resolveu o conflito.
        return session.scalars(
            select(MediaAsset).where(
                MediaAsset.brand_id == brand_id,
                MediaAsset.sha256 == stored.sha256,
            )
        ).one()

    def _asset_insert(
        self,
        brand_id: str,
        image: NormalizedImage,
        stored: StoredObject,
        kind: str,
        origem: str,
        actor: str,
        meta: PhotoUpload | None,
    ) -> ReturningInsert[MediaAsset]:
        statement = insert(MediaAsset).values(
            id=new_id(),
            brand_id=brand_id,
            kind=kind,
            storage_backend="local",
            storage_key=stored.key,
            mime=image.mime,
            width=image.width,
            height=image.height,
            bytes=stored.size,
            sha256=stored.sha256,
            mostra=meta.mostra if meta else None,
            descricao=meta.descricao if meta else None,
            origem=origem,
            licenca_nota=meta.licenca_nota if meta else None,
            tem_autorizacao_pessoas=meta.tem_autorizacao_pessoas if meta else False,
            is_active=True,
            created_by=actor,
            created_at=self._clock.now(),
        )
        # So esta chave e idempotente: FKs, CHECKs e outros erros continuam visiveis.
        return statement.on_conflict_do_nothing(index_elements=["brand_id", "sha256"]).returning(
            MediaAsset
        )

    def _audit(self, session: Session, row: MediaAsset, action: str, actor: str) -> None:
        session.add(
            AuditLog(
                actor=actor,
                action=action,
                subject_type="media_asset",
                subject_id=row.id,
                detail={"kind": row.kind, "mostra": row.mostra, "sha256": row.sha256[:12]},
                at=self._clock.now(),
            )
        )
