"""E6 styles and charts

Revision ID: 0004
Revises: 0003
Created: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

import bf_core.db.dialect as bfd

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "styles",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("uid", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_key", sa.String(length=255), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("data_kind", sa.String(length=16), nullable=False),
        sa.Column("body", bfd.JSONType, nullable=False),
        sa.Column("default_for", bfd.JSONType, nullable=False),
        sa.Column("extra", bfd.JSONType, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("author", sa.String(length=100), nullable=True),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.Column("updated_at", bfd.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_styles")),
        sa.UniqueConstraint("level", "name_key", name=op.f("uq_styles_level_name_key")),
        sa.UniqueConstraint("uid", name=op.f("uq_styles_uid")),
    )
    op.create_table(
        "charts",
        sa.Column("object_id", bfd.PK, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("settings", bfd.JSONType, nullable=False),
        sa.Column("auto_name", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_charts_object_id_objects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("object_id", name=op.f("pk_charts")),
    )


def downgrade() -> None:
    op.drop_table("charts")
    op.drop_table("styles")
