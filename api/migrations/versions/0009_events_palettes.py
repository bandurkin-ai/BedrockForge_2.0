"""event tables of drillhole sets (a depth, no thickness) and colour palettes of a project

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # "interval": from / to; "event": one depth (stored in from_m and to_m)
    op.add_column("interval_table", sa.Column("kind", sa.String(16), nullable=False, server_default="interval"))
    op.create_table(
        "palette",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("classes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "name", name="uq_palette_project_name"),
    )
    op.create_index("ix_palette_project_id", "palette", ["project_id"])


def downgrade() -> None:
    op.drop_table("palette")
    op.drop_column("interval_table", "kind")
