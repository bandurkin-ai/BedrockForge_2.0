"""interval tables of drillhole sets (lithology, alteration, RQD, more assays)

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interval_table",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("set_id", sa.Integer(), sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False, server_default=""),
        sa.Column("columns", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("set_id", "name", name="uq_interval_table_set_name"),
    )
    op.create_index("ix_interval_table_set_id", "interval_table", ["set_id"])
    op.create_table(
        "interval_row",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("table_id", sa.Integer(), sa.ForeignKey("interval_table.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hole_id", sa.String(64), nullable=False),
        sa.Column("from_m", sa.Double(), nullable=True),
        sa.Column("to_m", sa.Double(), nullable=True),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_interval_row_hole", "interval_row", ["table_id", "hole_id", "from_m"])


def downgrade() -> None:
    op.drop_table("interval_row")
    op.drop_table("interval_table")
