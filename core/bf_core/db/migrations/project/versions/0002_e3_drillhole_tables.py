"""E3 drillhole tables

Revision ID: 0002
Revises: 0001
Created: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

import bf_core.db.dialect as bfd

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dh_holes",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("set_id", bfd.PK, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("x", sa.Double(), nullable=True),
        sa.Column("y", sa.Double(), nullable=True),
        sa.Column("z", sa.Double(), nullable=True),
        sa.Column("td", sa.Double(), nullable=True),
        sa.Column("azimuth", sa.Double(), nullable=True),
        sa.Column("dip", sa.Double(), nullable=True),
        sa.Column("has_collar", sa.Boolean(), nullable=False),
        sa.Column("attrs", bfd.JSONType, nullable=True),
        sa.ForeignKeyConstraint(
            ["set_id"], ["objects.id"], name=op.f("fk_dh_holes_set_id_objects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dh_holes")),
        sa.UniqueConstraint("set_id", "name", name=op.f("uq_dh_holes_set_id_name")),
    )
    op.create_table(
        "dh_tables",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("set_id", bfd.PK, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("name_key", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("job_id", bfd.PK, nullable=True),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.Column("updated_at", bfd.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["set_id"], ["objects.id"], name=op.f("fk_dh_tables_set_id_objects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dh_tables")),
    )
    with op.batch_alter_table("dh_tables", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_dh_tables_set_id_name_key"), ["set_id", "name_key"], unique=False)

    op.create_table(
        "export_log",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("object_id", bfd.PK, nullable=True),
        sa.Column("job_id", bfd.PK, nullable=True),
        sa.Column("file_name", sa.String(length=400), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("settings", bfd.JSONType, nullable=False),
        sa.Column("counts", bfd.JSONType, nullable=False),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_export_log_object_id_objects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_export_log")),
    )
    op.create_table(
        "import_log",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("object_id", bfd.PK, nullable=True),
        sa.Column("job_id", bfd.PK, nullable=True),
        sa.Column("file_name", sa.String(length=400), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=32), nullable=False),
        sa.Column("mapping", bfd.JSONType, nullable=False),
        sa.Column("counts", bfd.JSONType, nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_import_log_object_id_objects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_log")),
    )
    with op.batch_alter_table("import_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_import_log_object_id"), ["object_id"], unique=False)

    op.create_table(
        "dh_columns",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("table_id", bfd.PK, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=True),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["table_id"], ["dh_tables.id"], name=op.f("fk_dh_columns_table_id_dh_tables"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dh_columns")),
    )
    with op.batch_alter_table("dh_columns", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_dh_columns_table_id"), ["table_id"], unique=False)

    op.create_table(
        "dh_rows",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("table_id", bfd.PK, nullable=False),
        sa.Column("hole_id", bfd.PK, nullable=False),
        sa.Column("from_m", sa.Double(), nullable=True),
        sa.Column("to_m", sa.Double(), nullable=True),
        sa.Column("num", sa.LargeBinary(), nullable=True),
        sa.Column("qual", sa.LargeBinary(), nullable=True),
        sa.Column("txt", bfd.JSONType, nullable=True),
        sa.ForeignKeyConstraint(
            ["hole_id"], ["dh_holes.id"], name=op.f("fk_dh_rows_hole_id_dh_holes"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["table_id"], ["dh_tables.id"], name=op.f("fk_dh_rows_table_id_dh_tables"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dh_rows")),
    )
    with op.batch_alter_table("dh_rows", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_dh_rows_table_id_hole_id_from_m"), ["table_id", "hole_id", "from_m"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_dh_rows_hole_id"), ["hole_id"], unique=False)

    op.create_table(
        "dh_surveys",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("set_id", bfd.PK, nullable=False),
        sa.Column("hole_id", bfd.PK, nullable=False),
        sa.Column("depth", sa.Double(), nullable=False),
        sa.Column("azimuth", sa.Double(), nullable=True),
        sa.Column("dip", sa.Double(), nullable=True),
        sa.Column("attrs", bfd.JSONType, nullable=True),
        sa.ForeignKeyConstraint(
            ["hole_id"], ["dh_holes.id"], name=op.f("fk_dh_surveys_hole_id_dh_holes"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["set_id"], ["objects.id"], name=op.f("fk_dh_surveys_set_id_objects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dh_surveys")),
    )
    with op.batch_alter_table("dh_surveys", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_dh_surveys_set_id_hole_id_depth"), ["set_id", "hole_id", "depth"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_dh_surveys_hole_id"), ["hole_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("dh_surveys", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_dh_surveys_hole_id"))
        batch_op.drop_index(batch_op.f("ix_dh_surveys_set_id_hole_id_depth"))

    op.drop_table("dh_surveys")
    with op.batch_alter_table("dh_rows", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_dh_rows_hole_id"))
        batch_op.drop_index(batch_op.f("ix_dh_rows_table_id_hole_id_from_m"))

    op.drop_table("dh_rows")
    with op.batch_alter_table("dh_columns", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_dh_columns_table_id"))

    op.drop_table("dh_columns")
    with op.batch_alter_table("import_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_import_log_object_id"))

    op.drop_table("import_log")
    op.drop_table("export_log")
    with op.batch_alter_table("dh_tables", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_dh_tables_set_id_name_key"))

    op.drop_table("dh_tables")
    op.drop_table("dh_holes")
