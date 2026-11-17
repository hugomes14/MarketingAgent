"""Media, kits de marca e imagens nas revisoes.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
ORIGENS_ANTIGAS = "'agente', 'edicao_responsavel'"
# A convencao de nomes da 0002 prefixou novamente o nome explicito do CHECK.
ORIGEM_CHECK_0002 = "ck_post_revision_ck_post_revision_origin"
LAYOUTS = "'foto_inteira', 'foto_titulo', 'fundo_ia_titulo', 'manual'"
COLUNAS_IMAGEM = (
    "final_asset_id",
    "layout",
    "layout_version",
    "components",
    "is_ai_generated",
    "review_image_checked",
    "image_cost_usd",
    "package_cost_usd",
)


def upgrade() -> None:
    """Cria media e kit e estende revisoes sem alterar dados existentes."""
    criar_media()
    restringir_media()
    criar_kit()
    acrescentar_imagem()
    op.drop_constraint(op.f(ORIGEM_CHECK_0002), "post_revision", type_="check")
    op.create_check_constraint(
        "origin", "post_revision", f"origin IN ({ORIGENS_ANTIGAS}, 'upload')"
    )
    # O gatilho da 0002 recusa qualquer UPDATE, incluindo as novas colunas.


def criar_media() -> None:
    """Cria as colunas, as chaves e os valores por omissao dos ativos."""
    op.create_table(
        "media_asset",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("brand_id", sa.String(36), sa.ForeignKey("brand.id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("storage_backend", sa.Text, nullable=False),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("mime", sa.Text, nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mostra", sa.Text),
        sa.Column("descricao", sa.Text),
        sa.Column("origem", sa.Text, nullable=False),
        sa.Column("licenca_nota", sa.Text),
        sa.Column("tem_autorizacao_pessoas", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("brand_id", "sha256"),
        sa.UniqueConstraint("brand_id", "id"),
    )


def restringir_media() -> None:
    """Aplica enums e regras de consentimento, dimensoes e identidade."""
    restricoes = (
        ("kind", "kind IN ('foto', 'logotipo', 'final', 'componente_gerado')"),
        (
            "origem",
            "origem IN ('propria', 'banco_licenciado', 'cliente_autorizado', 'gerada', 'composta')",
        ),
        ("mime", "mime IN ('image/jpeg', 'image/png')"),
        ("storage_backend", "storage_backend IN ('local')"),
        ("mostra", "mostra IN ('produto', 'instalacao', 'pessoas', 'ambiente', 'outro')"),
        # Um CHECK com descricao NULL seria UNKNOWN e aceitaria uma foto sem descricao.
        (
            "foto_requires_subject_description",
            "kind <> 'foto' OR (mostra IS NOT NULL "
            "AND descricao IS NOT NULL AND char_length(descricao) BETWEEN 1 AND 300)",
        ),
        (
            "pessoas_requires_authorization",
            "mostra IS DISTINCT FROM 'pessoas' OR tem_autorizacao_pessoas",
        ),
        ("positive_dimensions_size", "width > 0 AND height > 0 AND bytes > 0"),
        ("sha256", "sha256 ~ '^[0-9a-f]{64}$'"),
    )
    for nome, expressao in restricoes:
        op.create_check_constraint(nome, "media_asset", expressao)


def criar_kit() -> None:
    """Cria kits versionados com o logotipo da mesma marca."""
    op.create_table(
        "brand_kit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("brand_id", sa.String(36), sa.ForeignKey("brand.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("logo_asset_id", sa.String(36), nullable=False),
        sa.Column("cor_primaria", sa.String(7), nullable=False),
        sa.Column("cor_secundaria", sa.String(7), nullable=False),
        sa.Column("fonte", sa.Text, nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("brand_id", "version"),
        sa.ForeignKeyConstraint(
            ["brand_id", "logo_asset_id"], ["media_asset.brand_id", "media_asset.id"]
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.CheckConstraint("cor_primaria ~ '^#[0-9A-F]{6}$'", name="cor_primaria"),
        sa.CheckConstraint("cor_secundaria ~ '^#[0-9A-F]{6}$'", name="cor_secundaria"),
        sa.CheckConstraint("fonte IN ('inter-bold')", name="fonte"),
    )


def acrescentar_imagem() -> None:
    """Acrescenta imagem e custos; os defaults preservam revisoes apenas de texto."""
    op.add_column("post_revision", sa.Column("final_asset_id", sa.String(36)))
    op.add_column("post_revision", sa.Column("layout", sa.Text))
    op.add_column("post_revision", sa.Column("layout_version", sa.Integer))
    op.add_column(
        "post_revision",
        sa.Column("components", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    for coluna in ("is_ai_generated", "review_image_checked"):
        op.add_column(
            "post_revision",
            sa.Column(coluna, sa.Boolean, nullable=False, server_default=sa.false()),
        )
    for coluna in ("image_cost_usd", "package_cost_usd"):
        op.add_column(
            "post_revision",
            sa.Column(coluna, sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        )
    op.create_foreign_key(
        op.f("fk_post_revision_brand_id_final_asset_id_media_asset"),
        "post_revision",
        "media_asset",
        ["brand_id", "final_asset_id"],
        ["brand_id", "id"],
    )
    op.create_check_constraint("layout", "post_revision", f"layout IN ({LAYOUTS})")
    op.create_check_constraint(
        "image_all_or_nothing",
        "post_revision",
        "(final_asset_id IS NULL AND layout IS NULL AND layout_version IS NULL) OR "
        "(final_asset_id IS NOT NULL AND layout IS NOT NULL AND layout_version IS NOT NULL)",
    )


def downgrade() -> None:
    """Restaura o esquema 0002; origens upload devem ser resolvidas antes da reversao."""
    op.drop_constraint(op.f("ck_post_revision_origin"), "post_revision", type_="check")
    op.create_check_constraint(
        op.f(ORIGEM_CHECK_0002), "post_revision", f"origin IN ({ORIGENS_ANTIGAS})"
    )
    op.drop_constraint(
        op.f("ck_post_revision_image_all_or_nothing"), "post_revision", type_="check"
    )
    op.drop_constraint(op.f("ck_post_revision_layout"), "post_revision", type_="check")
    op.drop_constraint(
        op.f("fk_post_revision_brand_id_final_asset_id_media_asset"),
        "post_revision",
        type_="foreignkey",
    )
    for coluna in reversed(COLUNAS_IMAGEM):
        op.drop_column("post_revision", coluna)
    op.drop_table("brand_kit")
    op.drop_table("media_asset")
