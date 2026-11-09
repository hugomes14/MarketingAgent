"""Contratos de media, kit de marca e imagens em revisoes imutaveis."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import product

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.db import models

AGORA = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
Fabrica = sessionmaker[Session]


def criar_media(marca_id: str, resumo: str = "a" * 64) -> models.MediaAsset:
    """Prepara uma foto valida com valores minimos para as restricoes."""
    return models.MediaAsset(
        brand_id=marca_id,
        kind="foto",
        storage_backend="local",
        storage_key=f"{resumo}.jpg",
        mime="image/jpeg",
        width=1080,
        height=1350,
        bytes=100,
        sha256=resumo,
        mostra="produto",
        descricao="Produto da marca",
        origem="propria",
        created_by="teste",
        created_at=AGORA,
    )


def criar_kit(marca_id: str, logotipo_id: str) -> models.BrandKit:
    """Prepara a primeira versao de um kit valido."""
    return models.BrandKit(
        brand_id=marca_id,
        version=1,
        logo_asset_id=logotipo_id,
        cor_primaria="#ABC123",
        cor_secundaria="#FFFFFF",
        fonte="inter-bold",
        created_by="teste",
        created_at=AGORA,
    )


def gravar_media(fabrica: Fabrica, marca_id: str) -> str:
    """Grava media e devolve o id para referencias compostas."""
    with fabrica.begin() as sessao:
        media = criar_media(marca_id)
        sessao.add(media)
        sessao.flush()
        return media.id


def gravar_post(fabrica: Fabrica, marca_id: str) -> str:
    """Cria um post e o artefacto que o enquadra."""
    with fabrica.begin() as sessao:
        artefacto = models.Artifact(
            brand_id=marca_id, type="plano", schema_version=1, state="approved", payload={}
        )
        sessao.add(artefacto)
        sessao.flush()
        post = models.Post(
            brand_id=marca_id,
            plan_artifact_id=artefacto.id,
            strategy_artifact_id=artefacto.id,
            slot_key="2026-W46-1",
            channels=["instagram"],
            scheduled_date=date(2026, 11, 10),
            brief={},
            lifecycle="planned",
            created_at=AGORA,
        )
        sessao.add(post)
        sessao.flush()
        return post.id


def criar_revisao(marca_id: str, post_id: str) -> models.PostRevision:
    """Prepara uma revisao apenas com texto e sem parecer."""
    return models.PostRevision(
        brand_id=marca_id,
        post_id=post_id,
        rev_no=1,
        revision_hash="b" * 64,
        caption="Legenda",
        hashtags=[],
        claims=[],
        review_violations=[],
        origin="agente",
        created_at=AGORA,
    )


@pytest.mark.parametrize(
    ("mostra", "descricao"),
    [(None, "Produto"), ("produto", None), ("produto", ""), ("produto", "x" * 301)],
)
def test_photo_requires_subject_and_description(
    session_factory: Fabrica, brand_id: str, mostra: str | None, descricao: str | None
) -> None:
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        media = criar_media(brand_id)
        media.mostra = mostra
        media.descricao = descricao
        sessao.add(media)


def test_people_photo_requires_authorization(session_factory: Fabrica, brand_id: str) -> None:
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        media = criar_media(brand_id)
        media.mostra = "pessoas"
        sessao.add(media)
    with session_factory.begin() as sessao:
        media = criar_media(brand_id)
        media.mostra = "pessoas"
        media.tem_autorizacao_pessoas = True
        sessao.add(media)


def test_sha256_unique_per_brand_only(session_factory: Fabrica, brand_id: str) -> None:
    gravar_media(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(criar_media(brand_id))
    with session_factory.begin() as sessao:
        outra = models.Brand(name="Outra marca")
        sessao.add(outra)
        sessao.flush()
        sessao.add(criar_media(outra.id))


@pytest.mark.parametrize("campo", ["cor_primaria", "cor_secundaria"])
@pytest.mark.parametrize("valor", ["#abc123", "ABC123", "#ABCDE", "#ABCDEG"])
def test_brand_kit_version_unique_and_colors_format(
    session_factory: Fabrica, brand_id: str, campo: str, valor: str
) -> None:
    media_id = gravar_media(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        kit = criar_kit(brand_id, media_id)
        setattr(kit, campo, valor)
        sessao.add(kit)
    with session_factory.begin() as sessao:
        sessao.add(criar_kit(brand_id, media_id))
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(criar_kit(brand_id, media_id))
    with session_factory.begin() as sessao:
        kit = criar_kit(brand_id, media_id)
        kit.version = 2
        sessao.add(kit)


@pytest.mark.parametrize(
    ("tem_ativo", "tem_layout", "tem_versao"),
    [campos for campos in product((False, True), repeat=3) if len(set(campos)) == 2],
)
def test_revision_image_columns_all_or_nothing(
    session_factory: Fabrica,
    brand_id: str,
    tem_ativo: bool,
    tem_layout: bool,
    tem_versao: bool,
) -> None:
    media_id = gravar_media(session_factory, brand_id)
    post_id = gravar_post(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        revisao = criar_revisao(brand_id, post_id)
        revisao.final_asset_id = media_id if tem_ativo else None
        revisao.layout = "foto_inteira" if tem_layout else None
        revisao.layout_version = 1 if tem_versao else None
        sessao.add(revisao)


def test_cross_brand_final_asset_rejected(session_factory: Fabrica, brand_id: str) -> None:
    media_id = gravar_media(session_factory, brand_id)
    with session_factory.begin() as sessao:
        outra = models.Brand(name="Outra marca")
        sessao.add(outra)
        sessao.flush()
        outra_id = outra.id
    post_id = gravar_post(session_factory, outra_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        revisao = criar_revisao(outra_id, post_id)
        revisao.final_asset_id = media_id
        revisao.layout = "foto_inteira"
        revisao.layout_version = 1
        sessao.add(revisao)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(criar_kit(outra_id, media_id))


@pytest.mark.parametrize(
    "atribuicao",
    [
        "final_asset_id = NULL, layout = NULL, layout_version = NULL",
        "layout = 'manual'",
        "layout_version = 2",
        'components = \'[{"kind": "texto"}]\'::jsonb',
        "is_ai_generated = true",
        "review_image_checked = true",
        "image_cost_usd = 0.04",
        "package_cost_usd = 0.05",
    ],
)
def test_revision_image_columns_immutable(
    session_factory: Fabrica, brand_id: str, atribuicao: str
) -> None:
    media_id = gravar_media(session_factory, brand_id)
    post_id = gravar_post(session_factory, brand_id)
    with session_factory.begin() as sessao:
        revisao = criar_revisao(brand_id, post_id)
        revisao.final_asset_id = media_id
        revisao.layout = "foto_inteira"
        revisao.layout_version = 1
        sessao.add(revisao)
        sessao.flush()
        revisao_id = revisao.id
    with pytest.raises(DBAPIError, match="imutaveis"), session_factory.begin() as sessao:
        sessao.execute(
            text(f"UPDATE post_revision SET {atribuicao} WHERE id = :id"), {"id": revisao_id}
        )


def test_job_type_review_post_and_origin_upload_accepted(
    session_factory: Fabrica, brand_id: str
) -> None:
    assert "review_post" in models.JOB_TYPES
    post_id = gravar_post(session_factory, brand_id)
    with session_factory.begin() as sessao:
        revisao = criar_revisao(brand_id, post_id)
        revisao.origin = "upload"
        sessao.add(revisao)
        sessao.add(
            models.Job(
                type="review_post",
                payload={},
                status="queued",
                run_at=AGORA,
                created_at=AGORA,
                updated_at=AGORA,
            )
        )
        sessao.flush()
        assert revisao.review_verdict is None
        assert revisao.components == []
        assert revisao.is_ai_generated is False
        assert revisao.review_image_checked is False
        assert revisao.image_cost_usd == Decimal(0)
        assert revisao.package_cost_usd == Decimal(0)


@pytest.mark.parametrize("campo", ["width", "height", "bytes"])
@pytest.mark.parametrize("valor", [0, -1])
def test_media_dimensions_and_size_positive(
    session_factory: Fabrica, brand_id: str, campo: str, valor: int
) -> None:
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        media = criar_media(brand_id)
        setattr(media, campo, valor)
        sessao.add(media)


@pytest.mark.parametrize("resumo", ["A" * 64, "g" * 64, "a" * 63, "a" * 65])
def test_media_sha256_is_lowercase_hex(
    session_factory: Fabrica, brand_id: str, resumo: str
) -> None:
    with pytest.raises((IntegrityError, DataError)), session_factory.begin() as sessao:
        sessao.add(criar_media(brand_id, resumo))


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("kind", "video"),
        ("origem", "web"),
        ("mime", "image/webp"),
        ("storage_backend", "s3"),
        ("mostra", "desconhecido"),
    ],
)
def test_media_enums_reject_unknown_values(
    session_factory: Fabrica, brand_id: str, campo: str, valor: str
) -> None:
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        media = criar_media(brand_id)
        setattr(media, campo, valor)
        sessao.add(media)


def test_non_photo_can_omit_subject_and_description(
    session_factory: Fabrica, brand_id: str
) -> None:
    with session_factory.begin() as sessao:
        media = criar_media(brand_id)
        media.kind = "logotipo"
        media.mime = "image/png"
        media.mostra = None
        media.descricao = None
        sessao.add(media)
        sessao.flush()
        assert media.tem_autorizacao_pessoas is False
        assert media.is_active is True


@pytest.mark.parametrize("versao", [0, -1])
def test_brand_kit_version_positive(session_factory: Fabrica, brand_id: str, versao: int) -> None:
    media_id = gravar_media(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        kit = criar_kit(brand_id, media_id)
        kit.version = versao
        sessao.add(kit)


def test_brand_kit_font_allowlist(session_factory: Fabrica, brand_id: str) -> None:
    media_id = gravar_media(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        kit = criar_kit(brand_id, media_id)
        kit.fonte = "comic-sans"
        sessao.add(kit)


def test_revision_layout_allowlist(session_factory: Fabrica, brand_id: str) -> None:
    media_id = gravar_media(session_factory, brand_id)
    post_id = gravar_post(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        revisao = criar_revisao(brand_id, post_id)
        revisao.final_asset_id = media_id
        revisao.layout = "desconhecido"
        revisao.layout_version = 1
        sessao.add(revisao)
