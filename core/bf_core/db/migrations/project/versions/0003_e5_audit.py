"""E5 audit

Revision ID: 0003
Revises: 0002
Created: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

import bf_core.db.dialect as bfd

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_runs",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("set_id", bfd.PK, nullable=False),
        sa.Column("table_id", bfd.PK, nullable=True),
        sa.Column("table_name", sa.String(length=200), nullable=False),
        sa.Column("config", bfd.JSONType, nullable=False),
        sa.Column("versions", bfd.JSONType, nullable=False),
        sa.Column("summary", bfd.JSONType, nullable=False),
        sa.Column("totals", bfd.JSONType, nullable=False),
        sa.Column("job_id", bfd.PK, nullable=True),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["set_id"], ["objects.id"], name=op.f("fk_audit_runs_set_id_objects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["table_id"], ["dh_tables.id"], name=op.f("fk_audit_runs_table_id_dh_tables"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_runs")),
    )
    with op.batch_alter_table("audit_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_runs_set_id"), ["set_id"], unique=False)
    op.create_table(
        "audit_findings",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("run_id", bfd.PK, nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("hole", sa.String(length=200), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("examples", bfd.JSONType, nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["audit_runs.id"], name=op.f("fk_audit_findings_run_id_audit_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_findings")),
    )
    with op.batch_alter_table("audit_findings", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_findings_run_id_severity"), ["run_id", "severity"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_findings_run_id_hole"), ["run_id", "hole"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("audit_findings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_findings_run_id_hole"))
        batch_op.drop_index(batch_op.f("ix_audit_findings_run_id_severity"))
    op.drop_table("audit_findings")
    with op.batch_alter_table("audit_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_runs_set_id"))
    op.drop_table("audit_runs")
