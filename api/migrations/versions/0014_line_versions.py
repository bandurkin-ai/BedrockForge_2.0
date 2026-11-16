"""line layers: version number and the journal of saved versions

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("line_layer", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_table(
        "line_layer_version",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("layer_id", sa.Integer(), sa.ForeignKey("line_layer.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False, server_default="save"),
        sa.Column("note", sa.String(255), nullable=False, server_default=""),
        sa.Column("author", sa.String(128), nullable=True),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("prev_line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prev_point_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blobs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("line_attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("layer_id", "version", name="uq_line_layer_version"),
    )
    op.create_index("ix_line_layer_version_layer_id", "line_layer_version", ["layer_id"])
    # Layers imported before: their present state is version 1, so the first edit can be undone.
    op.execute(
        """
        INSERT INTO line_layer_version (layer_id, version, action, note, line_count, point_count, blobs, line_attributes, stats)
        SELECT id, 1, 'import', '', line_count, point_count,
               jsonb_build_object('vertices', blobs->'vertices', 'offsets', blobs->'offsets', 'closed', blobs->'closed'),
               line_attributes, stats
        FROM line_layer
        """
    )


def downgrade() -> None:
    op.drop_table("line_layer_version")
    op.drop_column("line_layer", "version")
