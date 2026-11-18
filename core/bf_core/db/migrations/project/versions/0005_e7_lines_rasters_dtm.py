"""E7 lines, rasters, wireframes (DTM); survey stations with coordinates

Revision ID: 0005
Revises: 0004
Created: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

import bf_core.db.dialect as bfd

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _owner(table: str) -> list[sa.schema.SchemaItem]:
    return [
        sa.Column("object_id", bfd.PK, nullable=False),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f(f"fk_{table}_object_id_objects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("object_id", name=op.f(f"pk_{table}")),
    ]


def upgrade() -> None:
    op.create_table(
        "line_layers",
        *_owner("line_layers"),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("length", sa.Double(), nullable=False),
        sa.Column("closed_count", sa.Integer(), nullable=False),
        sa.Column("flat", sa.Boolean(), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("attr_schema", bfd.JSONType, nullable=False),
        sa.Column("points_only", sa.Boolean(), nullable=False),
        sa.Column("origin", bfd.JSONType, nullable=False),
    )
    op.create_table(
        "rasters",
        *_owner("rasters"),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("georef", bfd.JSONType, nullable=True),
        sa.Column("elevation", sa.Double(), nullable=True),
        sa.Column("vertical", sa.Boolean(), nullable=False),
        sa.Column("opacity", sa.Double(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "wireframes",
        *_owner("wireframes"),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("triangle_count", sa.Integer(), nullable=False),
        sa.Column("origin", bfd.JSONType, nullable=False),
        sa.Column("stats", bfd.JSONType, nullable=False),
        sa.Column("render", bfd.JSONType, nullable=True),
        sa.Column("attrs", bfd.JSONType, nullable=False),
        sa.Column("dtm", bfd.JSONType, nullable=True),
    )
    with op.batch_alter_table("dh_surveys", schema=None) as batch_op:
        batch_op.add_column(sa.Column("x", sa.Double(), nullable=True))
        batch_op.add_column(sa.Column("y", sa.Double(), nullable=True))
        batch_op.add_column(sa.Column("z", sa.Double(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dh_surveys", schema=None) as batch_op:
        batch_op.drop_column("z")
        batch_op.drop_column("y")
        batch_op.drop_column("x")
    op.drop_table("wireframes")
    op.drop_table("rasters")
    op.drop_table("line_layers")
