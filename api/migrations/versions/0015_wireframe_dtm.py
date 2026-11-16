"""wireframes: the DTM section (topographic surfaces) and its settings

Revision ID: 0015
Revises: 0014
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("wireframe", sa.Column("dtm", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("wireframe", "dtm")
