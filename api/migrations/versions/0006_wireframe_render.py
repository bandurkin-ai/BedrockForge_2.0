"""wireframe display structure (parts, levels of detail)

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Wireframes stored earlier keep an empty structure and are drawn whole.
    op.add_column("wireframe", sa.Column("render", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                                         server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("wireframe", "render")
