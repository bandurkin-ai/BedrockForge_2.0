"""E2 instance tables

Revision ID: 0001
Revises: -
Created: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

import bf_core.db.dialect as bfd

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("queue", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=400), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("params", bfd.JSONType, nullable=False),
        sa.Column("secret", sa.String(length=1024), nullable=True),
        sa.Column("result", bfd.JSONType, nullable=True),
        sa.Column("error", bfd.JSONType, nullable=True),
        sa.Column("progress", sa.Double(), nullable=False),
        sa.Column("message", sa.String(length=400), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("memory_mb", sa.Integer(), nullable=True),
        sa.Column("worker", sa.String(length=64), nullable=True),
        sa.Column("project_uid", sa.String(length=32), nullable=True),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.Column("started_at", bfd.UtcDateTime(), nullable=True),
        sa.Column("heartbeat_at", bfd.UtcDateTime(), nullable=True),
        sa.Column("finished_at", bfd.UtcDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_jobs_queue_status_priority_id"), ["queue", "status", "priority", "id"], unique=False
        )

    op.create_table(
        "projects",
        sa.Column("uid", sa.String(length=32), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("name_key", sa.String(length=200), nullable=False),
        sa.Column("summary", bfd.JSONType, nullable=False),
        sa.Column("mtime", sa.Double(), nullable=False),
        sa.Column("opened_at", bfd.UtcDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("uid", name=op.f("pk_projects")),
    )
    op.create_table(
        "job_log",
        sa.Column("job_id", bfd.PK, nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("at", bfd.UtcDateTime(), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_job_log_job_id_jobs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "seq", name=op.f("pk_job_log")),
    )


def downgrade() -> None:
    op.drop_table("job_log")
    op.drop_table("projects")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_queue_status_priority_id"))

    op.drop_table("jobs")
