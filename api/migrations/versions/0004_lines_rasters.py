"""lines and rasters

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _jsonb(name: str, default: str = "'{}'::jsonb") -> sa.Column:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text(default))


def upgrade() -> None:
    op.create_table(
        "line_layer",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        _jsonb("extent"),
        _jsonb("stats"),
        _jsonb("line_attributes", "'[]'::jsonb"),
        sa.Column("vertices", sa.LargeBinary(), nullable=False),
        sa.Column("offsets", sa.LargeBinary(), nullable=False),
        sa.Column("closed", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_line_layer_dataset_id", "line_layer", ["dataset_id"])
    op.create_table(
        "raster",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        _jsonb("georef"),
        _jsonb("extent"),
        sa.Column("elevation", sa.Double(), nullable=True),
        sa.Column("opacity", sa.Double(), nullable=False, server_default=sa.text("1")),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column("mime", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_raster_dataset_id", "raster", ["dataset_id"])


def downgrade() -> None:
    op.drop_table("raster")
    op.drop_table("line_layer")
