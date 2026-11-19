"""E9 journal of versions of line layers

Revision ID: 0006
Revises: 0005
Created: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

import bf_core.db.dialect as bfd

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # An Э7 layer gets rev = 1 (its version 1 is the state as imported) and next_lid NULL (lid = position).
    with op.batch_alter_table("line_layers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rev", sa.Integer(), server_default="1", nullable=False))
        batch_op.add_column(sa.Column("next_lid", sa.Integer(), nullable=True))
    op.create_table(
        "line_layer_versions",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("layer_id", bfd.PK, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("author", sa.String(length=100), nullable=True),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("prev_line_count", sa.Integer(), nullable=False),
        sa.Column("prev_point_count", sa.Integer(), nullable=False),
        sa.Column("stats", bfd.JSONType, nullable=False),
        sa.ForeignKeyConstraint(
            ["layer_id"], ["objects.id"], name=op.f("fk_line_layer_versions_layer_id_objects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_line_layer_versions")),
        sa.UniqueConstraint("layer_id", "version", name=op.f("uq_line_layer_versions_layer_id_version")),
    )


def downgrade() -> None:
    # The files of the versions lose their owner: the storage cleanup removes them (the layers keep theirs).
    op.execute("DELETE FROM blob_owners WHERE owner_kind = 'line_version'")
    op.drop_table("line_layer_versions")
    with op.batch_alter_table("line_layers", schema=None) as batch_op:
        batch_op.drop_column("next_lid")
        batch_op.drop_column("rev")
