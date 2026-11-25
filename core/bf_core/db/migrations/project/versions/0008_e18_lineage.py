"""E18 graph of dependencies of computed objects (ADR 0025)

Revision ID: 0008
Revises: 0007
Created: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op

import bf_core.db.dialect as bfd

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A new table only: results written before it have no rows and are judged by their own direct checks (ADR 0025 §12).
    op.create_table(
        "lineage",
        sa.Column("dst_kind", sa.String(length=48), nullable=False),
        sa.Column("dst_id", bfd.REF, nullable=False),
        sa.Column("dst_sub", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("src_kind", sa.String(length=48), nullable=False),
        sa.Column("src_id", bfd.REF, nullable=False),
        sa.Column("src_sub", sa.String(length=200), nullable=False),
        sa.Column("pos", sa.Integer(), nullable=False),
        sa.Column("src_fp", sa.String(length=200), nullable=True),
        sa.Column("src_name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint(
            "dst_kind", "dst_id", "dst_sub", "role", "src_kind", "src_id", "src_sub", name=op.f("pk_lineage")
        ),
    )
    with op.batch_alter_table("lineage", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_lineage_src_kind_src_id"), ["src_kind", "src_id"], unique=False)


def downgrade() -> None:
    # The states of the computed objects go back to their own direct checks; the results themselves stay.
    with op.batch_alter_table("lineage", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_lineage_src_kind_src_id"))
    op.drop_table("lineage")
