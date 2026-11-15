"""assistant: saved chats and the usage of Claude

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

JSON = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "assistant_chat",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default="Новый диалог"),
        sa.Column("messages", JSON, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("items", JSON, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("pending", JSON, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("model", sa.String(64), nullable=False, server_default=""),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Double(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_assistant_chat_project_id", "assistant_chat", ["project_id"])
    op.create_table(
        "assistant_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="SET NULL"), nullable=True),
        sa.Column("chat_id", sa.String(32), nullable=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Double(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_assistant_usage_project_id", "assistant_usage", ["project_id"])
    op.create_index("ix_assistant_usage_created_at", "assistant_usage", ["created_at"])


def downgrade() -> None:
    op.drop_table("assistant_usage")
    op.drop_table("assistant_chat")
