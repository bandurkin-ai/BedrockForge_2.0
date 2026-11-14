"""object store, resumable uploads, background jobs, tree folders

Geometry and images move from bytea columns to the content-addressed file store
(app/storage/blobs.py, DATA_DIR). Existing rows are converted: their arrays are written
to files, render copies are made, exact extents are recomputed. The downgrade reads the
files back into bytea.

Revision ID: 0005
Revises: 0004
"""
import numpy as np
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _jsonb(name: str, default: str = "'{}'::jsonb", nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=nullable,
                     server_default=sa.text(default) if default else None)


def _ts(name: str, nullable: bool = True, default: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable,
                     server_default=sa.text("now()") if default else None)


def upgrade() -> None:
    op.create_table(
        "blob",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(64), nullable=True),
        _ts("created_at", nullable=False, default=True),
    )
    op.create_table(
        "upload",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("received", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(12), nullable=False, server_default="receiving"),
        _ts("created_at", nullable=False, default=True),
        _ts("updated_at", nullable=False, default=True),
    )
    op.create_index("ix_upload_project_id", "upload", ["project_id"])
    op.create_table(
        "job",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        _jsonb("params"),
        _jsonb("result", default=None, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("progress", sa.Double(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        _jsonb("log", "'[]'::jsonb"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("worker", sa.String(64), nullable=True),
        _ts("created_at", nullable=False, default=True),
        _ts("started_at"),
        _ts("finished_at"),
        _ts("heartbeat_at"),
        _ts("updated_at", nullable=False, default=True),
    )
    op.create_index("ix_job_queue", "job", ["status", "id"])
    op.create_index("ix_job_project_updated", "job", ["project_id", "updated_at"])

    op.add_column("dataset", sa.Column("folder", sa.String(255), nullable=False, server_default=""))
    op.add_column("dataset", _jsonb("blobs"))
    for table in ("wireframe", "line_layer"):
        op.add_column(table, _jsonb("origin", "'[]'::jsonb"))
        op.add_column(table, _jsonb("blobs"))
    op.add_column("raster", _jsonb("blobs"))

    _move_to_store()

    for column in ("vertices", "triangles"):
        op.drop_column("wireframe", column)
    for column in ("vertices", "offsets", "closed"):
        op.drop_column("line_layer", column)
    op.drop_column("raster", "image")


def _move_to_store() -> None:
    import json

    from app.storage import blobs, geometry

    conn = op.get_bind()
    session = Session(bind=conn)
    for row in conn.execute(sa.text("SELECT id, vertices, triangles FROM wireframe ORDER BY id")).mappings().all():
        v = np.frombuffer(row["vertices"], dtype="<f8").reshape(-1, 3)
        t = np.frombuffer(row["triangles"], dtype="<i4").reshape(-1, 3)
        stored = geometry.store_mesh(session, v, t)
        conn.execute(
            sa.text("UPDATE wireframe SET blobs = CAST(:b AS jsonb), origin = CAST(:o AS jsonb), extent = CAST(:e AS jsonb) WHERE id = :id"),
            {"b": json.dumps(stored.blobs), "o": json.dumps(stored.origin), "e": json.dumps(stored.extent), "id": row["id"]},
        )
    for row in conn.execute(sa.text("SELECT id, vertices, offsets, closed FROM line_layer ORDER BY id")).mappings().all():
        v = np.frombuffer(row["vertices"], dtype="<f8").reshape(-1, 3)
        stored = geometry.store_lines(
            session, v, np.frombuffer(row["offsets"], dtype="<i8"), np.frombuffer(row["closed"], dtype="u1")
        )
        conn.execute(
            sa.text("UPDATE line_layer SET blobs = CAST(:b AS jsonb), origin = CAST(:o AS jsonb), extent = CAST(:e AS jsonb) WHERE id = :id"),
            {"b": json.dumps(stored.blobs), "o": json.dumps(stored.origin), "e": json.dumps(stored.extent), "id": row["id"]},
        )
    for row in conn.execute(sa.text("SELECT id, image, mime FROM raster ORDER BY id")).mappings().all():
        sha = blobs.put_bytes(session, bytes(row["image"]), media_type=row["mime"])
        conn.execute(sa.text("UPDATE raster SET blobs = CAST(:b AS jsonb) WHERE id = :id"),
                     {"b": json.dumps({"image": sha}), "id": row["id"]})


def downgrade() -> None:
    from app.storage import blobs

    op.add_column("wireframe", sa.Column("vertices", sa.LargeBinary(), nullable=True))
    op.add_column("wireframe", sa.Column("triangles", sa.LargeBinary(), nullable=True))
    for column in ("vertices", "offsets", "closed"):
        op.add_column("line_layer", sa.Column(column, sa.LargeBinary(), nullable=True))
    op.add_column("raster", sa.Column("image", sa.LargeBinary(), nullable=True))

    conn = op.get_bind()
    for table, columns in (("wireframe", ("vertices", "triangles")),
                           ("line_layer", ("vertices", "offsets", "closed")),
                           ("raster", ("image",))):
        for row in conn.execute(sa.text(f"SELECT id, blobs FROM {table}")).mappings().all():
            values = {c: blobs.read_bytes(row["blobs"][c]) for c in columns}
            sets = ", ".join(f"{c} = :{c}" for c in columns)
            conn.execute(sa.text(f"UPDATE {table} SET {sets} WHERE id = :id"), {**values, "id": row["id"]})
        for c in columns:
            op.alter_column(table, c, nullable=False)

    op.drop_column("raster", "blobs")
    for table in ("wireframe", "line_layer"):
        op.drop_column(table, "blobs")
        op.drop_column(table, "origin")
    op.drop_column("dataset", "blobs")
    op.drop_column("dataset", "folder")
    op.drop_table("job")
    op.drop_table("upload")
    op.drop_table("blob")
