"""E2 project tables

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
        "blobs",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("sha256", name=op.f("pk_blobs")),
    )
    op.create_table(
        "edit_log",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("scope_kind", sa.String(length=32), nullable=False),
        sa.Column("scope_id", bfd.PK, nullable=False),
        sa.Column("group_id", bfd.PK, nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("row_ref", bfd.JSONType, nullable=True),
        sa.Column("changes", bfd.JSONType, nullable=False),
        sa.Column("undone", sa.Boolean(), nullable=False),
        sa.Column("author", sa.String(length=100), nullable=True),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_edit_log")),
    )
    with op.batch_alter_table("edit_log", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_edit_log_scope_kind_scope_id_id"), ["scope_kind", "scope_id", "id"], unique=False
        )

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
        "objects",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("parent_id", bfd.PK, nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("name_key", sa.String(length=200), nullable=False),
        sa.Column("folder", sa.String(length=200), nullable=True),
        sa.Column("sort_key", sa.Double(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("meta", bfd.JSONType, nullable=False),
        sa.Column("extent", bfd.JSONType, nullable=True),
        sa.Column("crs", sa.String(length=200), nullable=True),
        sa.Column("job_id", bfd.PK, nullable=True),
        sa.Column("author", sa.String(length=100), nullable=True),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.Column("updated_at", bfd.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["objects.id"], name=op.f("fk_objects_parent_id_objects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_objects")),
    )
    with op.batch_alter_table("objects", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_objects_kind_state"), ["kind", "state"], unique=False)
        batch_op.create_index(batch_op.f("ix_objects_parent_id"), ["parent_id"], unique=False)

    op.create_table(
        "project_meta",
        sa.Column("id", bfd.PK, nullable=False),
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("name_key", sa.String(length=200), nullable=False),
        sa.Column("description", bfd.JSONType, nullable=False),
        sa.Column("crs", sa.String(length=200), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", bfd.UtcDateTime(), nullable=False),
        sa.Column("updated_at", bfd.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_meta")),
    )
    op.create_table(
        "settings",
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("value", bfd.JSONType, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "key", name=op.f("pk_settings")),
    )
    op.create_table(
        "blob_owners",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("owner_kind", sa.String(length=32), nullable=False),
        sa.Column("owner_id", bfd.PK, nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["sha256"], ["blobs.sha256"], name=op.f("fk_blob_owners_sha256_blobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("sha256", "owner_kind", "owner_id", "role", name=op.f("pk_blob_owners")),
    )
    with op.batch_alter_table("blob_owners", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_blob_owners_owner_kind_owner_id"), ["owner_kind", "owner_id"], unique=False
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
    op.create_table(
        "object_refs",
        sa.Column("src_id", bfd.PK, nullable=False),
        sa.Column("dst_id", bfd.PK, nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["dst_id"], ["objects.id"], name=op.f("fk_object_refs_dst_id_objects"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["src_id"], ["objects.id"], name=op.f("fk_object_refs_src_id_objects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("src_id", "dst_id", "role", name=op.f("pk_object_refs")),
    )
    with op.batch_alter_table("object_refs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_object_refs_dst_id"), ["dst_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("object_refs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_object_refs_dst_id"))

    op.drop_table("object_refs")
    op.drop_table("job_log")
    with op.batch_alter_table("blob_owners", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_blob_owners_owner_kind_owner_id"))

    op.drop_table("blob_owners")
    op.drop_table("settings")
    op.drop_table("project_meta")
    with op.batch_alter_table("objects", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_objects_parent_id"))
        batch_op.drop_index(batch_op.f("ix_objects_kind_state"))

    op.drop_table("objects")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_queue_status_priority_id"))

    op.drop_table("jobs")
    with op.batch_alter_table("edit_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_edit_log_scope_kind_scope_id_id"))

    op.drop_table("edit_log")
    op.drop_table("blobs")
