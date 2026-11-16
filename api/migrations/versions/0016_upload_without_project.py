"""uploads: a file may arrive before its project exists (import of a project archive)

Revision ID: 0016
Revises: 0015
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("upload", "project_id", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM upload WHERE project_id IS NULL")
    op.alter_column("upload", "project_id", nullable=False)
