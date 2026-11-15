"""styles library of a project (palettes, layer styles, display templates); replaces the palettes

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "style",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        # "palette" | "layer" | "display"
        sa.Column("level", sa.String(16), nullable=False),
        # "number" | "text" | "event" | "mixed"
        sa.Column("data_kind", sa.String(16), nullable=False, server_default="number"),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("default_for", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "level", "name", name="uq_style_project_level_name"),
    )
    op.create_index("ix_style_project_id", "style", ["project_id"])
    # The ids are kept: saved displays refer to palettes by id.
    op.execute(
        "INSERT INTO style (id, project_id, name, level, data_kind, body, created_at, updated_at) "
        "SELECT id, project_id, name, 'palette', 'number', "
        "jsonb_build_object('kind', 'classes', 'classes', classes), created_at, created_at FROM palette"
    )
    op.execute("SELECT setval(pg_get_serial_sequence('style', 'id'), (SELECT max(id) FROM style)) WHERE EXISTS (SELECT 1 FROM style)")
    op.drop_table("palette")


def downgrade() -> None:
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
    # Layer styles and display templates have no place in the old schema and are lost.
    op.execute(
        "INSERT INTO palette (id, project_id, name, classes, created_at) "
        "SELECT id, project_id, name, body->'classes', created_at FROM style "
        "WHERE level = 'palette' AND jsonb_typeof(body->'classes') = 'array'"
    )
    op.execute("SELECT setval(pg_get_serial_sequence('palette', 'id'), (SELECT max(id) FROM palette)) WHERE EXISTS (SELECT 1 FROM palette)")
    op.drop_table("style")
