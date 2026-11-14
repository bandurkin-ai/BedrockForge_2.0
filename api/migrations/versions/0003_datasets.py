"""datasets: drillhole sets and wireframes

Every project gets objects of several kinds. Existing drillhole data of a project moves
into one drillhole set named "Скважины"; the tables reference the set instead of the project.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Tables that move from project_id to set_id.
TABLES = ["collar", "survey", "assay", "component", "import_job", "audit_run"]


def upgrade() -> None:
    op.create_table(
        "dataset",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dataset_project_id", "dataset", ["project_id"])

    op.create_table(
        "wireframe",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("triangle_count", sa.Integer(), nullable=False),
        sa.Column("extent", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("vertices", sa.LargeBinary(), nullable=False),
        sa.Column("triangles", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wireframe_dataset_id", "wireframe", ["dataset_id"])

    # One drillhole set for every project that already has drillhole data.
    used = " UNION ".join(f"SELECT project_id FROM {t}" for t in TABLES)
    op.execute(
        f"INSERT INTO dataset (project_id, kind, name, meta) "
        f"SELECT DISTINCT project_id, 'drillholes', 'Скважины', '{{}}'::jsonb FROM ({used}) used"
    )
    for table in TABLES:
        op.add_column(table, sa.Column("set_id", sa.Integer(), nullable=True))
        op.execute(
            f"UPDATE {table} t SET set_id = d.id FROM dataset d "
            f"WHERE d.project_id = t.project_id AND d.kind = 'drillholes'"
        )
        op.alter_column(table, "set_id", nullable=False)
        op.create_foreign_key(f"{table}_set_id_fkey", table, "dataset", ["set_id"], ["id"], ondelete="CASCADE")
        # Dropping the column also drops its foreign key, unique constraint and indexes.
        op.drop_column(table, "project_id")

    op.create_unique_constraint("collar_set_id_hole_id_key", "collar", ["set_id", "hole_id"])
    op.create_unique_constraint("component_set_id_name_key", "component", ["set_id", "name"])
    op.create_index("ix_collar_set_id", "collar", ["set_id"])
    op.create_index("ix_survey_hole", "survey", ["set_id", "hole_id", "depth"])
    op.create_index("ix_assay_hole", "assay", ["set_id", "hole_id", "from_m"])
    op.create_index("ix_import_job_set_id", "import_job", ["set_id"])
    op.create_index("ix_audit_run_set_id", "audit_run", ["set_id"])


def downgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("project_id", sa.Integer(), nullable=True))
        op.execute(f"UPDATE {table} t SET project_id = d.project_id FROM dataset d WHERE d.id = t.set_id")
        op.alter_column(table, "project_id", nullable=False)
        op.create_foreign_key(f"{table}_project_id_fkey", table, "project", ["project_id"], ["id"], ondelete="CASCADE")
        op.drop_column(table, "set_id")
    op.create_unique_constraint("collar_project_id_hole_id_key", "collar", ["project_id", "hole_id"])
    op.create_unique_constraint("component_project_id_name_key", "component", ["project_id", "name"])
    op.create_index("ix_collar_project_id", "collar", ["project_id"])
    op.create_index("ix_survey_hole", "survey", ["project_id", "hole_id", "depth"])
    op.create_index("ix_assay_hole", "assay", ["project_id", "hole_id", "from_m"])
    op.create_index("ix_import_job_project_id", "import_job", ["project_id"])
    op.create_index("ix_audit_run_project_id", "audit_run", ["project_id"])
    op.drop_table("wireframe")
    op.drop_table("dataset")
