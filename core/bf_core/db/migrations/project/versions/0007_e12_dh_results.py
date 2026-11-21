"""E12 results of sample processing

Revision ID: 0007
Revises: 0006
Created: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

import bf_core.db.dialect as bfd

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A new table only: Э4 tables, rows and columns stay as they are (a result table is an ordinary set table).
    op.create_table(
        "dh_results",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("set_id", bfd.REF, nullable=False),
        sa.Column("table_id", bfd.REF, nullable=True),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("params", bfd.JSONType, nullable=False),
        sa.Column("inputs", bfd.JSONType, nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("algo", sa.String(length=32), nullable=False),
        sa.Column("report", bfd.JSONType, nullable=False),
        sa.Column("job_id", bfd.REF, nullable=True),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["set_id"], ["objects.id"], name=op.f("fk_dh_results_set_id_objects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["table_id"], ["dh_tables.id"], name=op.f("fk_dh_results_table_id_dh_tables"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dh_results")),
    )
    with op.batch_alter_table("dh_results", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_dh_results_set_id"), ["set_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_dh_results_table_id"), ["table_id"], unique=False)


def downgrade() -> None:
    # Result tables and columns written in place stay in the set as ordinary tables and columns; only the
    # metadata (parameters, inputs, «устарел») goes.
    with op.batch_alter_table("dh_results", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_dh_results_table_id"))
        batch_op.drop_index(batch_op.f("ix_dh_results_set_id"))
    op.drop_table("dh_results")
