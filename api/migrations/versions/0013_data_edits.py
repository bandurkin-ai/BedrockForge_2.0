"""hand corrections of drillhole tables: the edit log

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_edit",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("set_id", sa.Integer(), sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False),
        sa.Column("table_key", sa.String(32), nullable=False),
        sa.Column("table_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("row_id", sa.BigInteger(), nullable=False),
        sa.Column("hole_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("undone", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_data_edit_set_id", "data_edit", ["set_id"])


def downgrade() -> None:
    op.drop_table("data_edit")
