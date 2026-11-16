"""Database schema.

A project holds datasets of several kinds (drillhole sets, wireframes, ...). Drillhole tables
belong to a drillhole set (`set_id`); sets are independent, so hole ids may repeat between them.
Hole references in survey/assay are plain text (hole_id), not foreign keys:
files can arrive in any order and the audit stage reports orphans.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Double, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

def _project_fk() -> ForeignKey:
    return ForeignKey("project.id", ondelete="CASCADE")


def _set_fk() -> ForeignKey:
    return ForeignKey("dataset.id", ondelete="CASCADE")


# Dataset kinds shown in the project tree. Only drillholes and wireframes can be loaded so far.
DATASET_KINDS = ("drillholes", "lines", "wireframes", "blocks", "rasters")


class Project(Base):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    # A note about the project: deposit, licence area, stage of work.
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Dataset(Base):
    """An object of the project tree: a drillhole set, a wireframe file, ..."""

    __tablename__ = "dataset"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(_project_fk(), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Source file, format, notes of the import.
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Folder in the project tree inside the kind's group, "/"-separated; "" is the group itself.
    folder: Mapped[str] = mapped_column(String(255), default="", server_default="")
    # Stored files of the object as a whole, role -> SHA-256 (e.g. "source": the imported file).
    blobs: Mapped[dict] = mapped_column(JSONB, default=dict)


class Chart(Base):
    """A saved chart of a drillhole set (histogram, Q-Q, dendrogram): its type and settings.
    Shown in the project tree under the set; opening it draws it again from the current data."""

    __tablename__ = "chart"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(_set_fk(), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    # False once the user renamed it: the name no longer follows the settings.
    auto_name: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Blob(Base):
    """A file of the content-addressed store (app/storage/blobs.py)."""

    __tablename__ = "blob"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str | None] = mapped_column(String(64))
    # Registration time; also refreshed on re-registration, so a fresh blob is never collected.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Upload(Base):
    """A resumable upload: the file arrives in chunks into DATA_DIR/uploads/<id>.part."""

    __tablename__ = "upload"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[int] = mapped_column(_project_fk(), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(BigInteger)
    received: Mapped[int] = mapped_column(BigInteger, default=0)
    # receiving -> complete; a consumed upload is deleted.
    status: Mapped[str] = mapped_column(String(12), default="receiving")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


JOB_STATUSES = ("queued", "running", "done", "failed", "cancelled")


class Job(Base):
    """A background task (import, calculation) run by a worker process (app/jobs/)."""

    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_queue", "status", "id"),
        Index("ix_job_project_updated", "project_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(_project_fk())
    kind: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(12), default="queued")
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[float] = mapped_column(Double, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    # [{"t": iso time, "level": "info" | "warning" | "error", "text": ...}]
    log: Mapped[list] = mapped_column(JSONB, default=list)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    worker: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Wireframe(Base):
    """One triangulated surface or solid. Geometry is stored as raw little-endian arrays."""

    __tablename__ = "wireframe"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(_set_fk(), index=True)
    name: Mapped[str] = mapped_column(String(255))
    color: Mapped[str] = mapped_column(String(7))  # "#rrggbb"
    point_count: Mapped[int] = mapped_column(Integer)
    triangle_count: Mapped[int] = mapped_column(Integer)
    # {"min": [x, y, z], "max": [x, y, z]}
    extent: Mapped[dict] = mapped_column(JSONB)
    # Area, volume, closed, degenerate triangles, source attributes.
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Integer metres near the centre of the extent; render buffers are relative to it.
    origin: Mapped[list] = mapped_column(JSONB, default=list)
    # Stored arrays, role -> SHA-256 (app/storage/geometry.py):
    # vertices <f8 x y z, triangles <i4 i j k, render_vertices <f4 x y z relative to origin,
    # lodN_vertices / lodN_triangles: simplified copies for distant views.
    blobs: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Display structure: {"chunk_triangles", "chunks": [[first, count, bbox...]], "lods": [...]};
    # empty for wireframes stored before stage 10 (drawn whole).
    render: Mapped[dict] = mapped_column(JSONB, default=dict)
    # None: an ordinary wireframe. A dict: the wireframe is a digital terrain model (the «ЦТМ»
    # section of the tree) with its settings: {"raster_id", "clip", "tint", "opacity", "shade"}.
    dtm: Mapped[dict | None] = mapped_column(JSONB, default=None)


class LineLayer(Base):
    """Polylines of one layer (STR string code or DXF layer), stored as one vertex array."""

    __tablename__ = "line_layer"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(_set_fk(), index=True)
    name: Mapped[str] = mapped_column(String(255))
    color: Mapped[str] = mapped_column(String(7))
    line_count: Mapped[int] = mapped_column(Integer)
    point_count: Mapped[int] = mapped_column(Integer)
    extent: Mapped[dict] = mapped_column(JSONB)
    # Total length, closed lines, flat (constant Z).
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    # One dict per line (attributes from the file).
    line_attributes: Mapped[list] = mapped_column(JSONB, default=list)
    origin: Mapped[list] = mapped_column(JSONB, default=list)
    # vertices <f8 x y z, offsets <i8 start of every line plus the end, closed u1 per line,
    # render_vertices <f4 relative to origin, render_offsets <u4.
    blobs: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Number of the current saved state; every save of the editor makes the next one (line_layer_version).
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class LineLayerVersion(Base):
    """A saved state of a line layer: the journal of the string editor. The store is immutable,
    so a version is only references to the arrays it had; returning to it writes a new version."""

    __tablename__ = "line_layer_version"
    __table_args__ = (UniqueConstraint("layer_id", "version", name="uq_line_layer_version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    layer_id: Mapped[int] = mapped_column(ForeignKey("line_layer.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    # "import" | "create" | "save" | "restore" | "move" | "operation"
    action: Mapped[str] = mapped_column(String(16), default="save")
    note: Mapped[str] = mapped_column(String(255), default="")
    # Who saved it; empty until the application has user accounts (stage 28a).
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    line_count: Mapped[int] = mapped_column(Integer)
    point_count: Mapped[int] = mapped_column(Integer)
    prev_line_count: Mapped[int] = mapped_column(Integer, default=0)
    prev_point_count: Mapped[int] = mapped_column(Integer, default=0)
    # vertices, offsets, closed of this version (the exact arrays).
    blobs: Mapped[dict] = mapped_column(JSONB, default=dict)
    line_attributes: Mapped[list] = mapped_column(JSONB, default=list)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Raster(Base):
    """A georeferenced image placed in plan at an elevation."""

    __tablename__ = "raster"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(_set_fk(), index=True)
    name: Mapped[str] = mapped_column(String(255))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    # affine, source, crs, pixel size, rotation, corners, control point checks, notes
    georef: Mapped[dict] = mapped_column(JSONB)
    extent: Mapped[dict] = mapped_column(JSONB)
    # None: placed under the lowest object of the scene.
    elevation: Mapped[float | None] = mapped_column(Double)
    opacity: Mapped[float] = mapped_column(Double, default=1.0)
    # image: the display copy (JPEG / PNG).
    blobs: Mapped[dict] = mapped_column(JSONB, default=dict)
    mime: Mapped[str] = mapped_column(String(32))


class Collar(Base):
    __tablename__ = "collar"
    __table_args__ = (UniqueConstraint("set_id", "hole_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(_set_fk(), index=True)
    hole_id: Mapped[str] = mapped_column(String(64))
    x: Mapped[float | None] = mapped_column(Double)
    y: Mapped[float | None] = mapped_column(Double)
    z: Mapped[float | None] = mapped_column(Double)
    td: Mapped[float | None] = mapped_column(Double)
    azimuth: Mapped[float | None] = mapped_column(Double)
    dip: Mapped[float | None] = mapped_column(Double)
    date: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(String(64))
    stage: Mapped[str | None] = mapped_column(String(64))
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)


class Survey(Base):
    __tablename__ = "survey"
    __table_args__ = (Index("ix_survey_hole", "set_id", "hole_id", "depth"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(_set_fk())
    hole_id: Mapped[str] = mapped_column(String(64))
    depth: Mapped[float | None] = mapped_column(Double)
    azimuth: Mapped[float | None] = mapped_column(Double)
    dip: Mapped[float | None] = mapped_column(Double)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)


class Assay(Base):
    __tablename__ = "assay"
    __table_args__ = (Index("ix_assay_hole", "set_id", "hole_id", "from_m"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(_set_fk())
    hole_id: Mapped[str] = mapped_column(String(64))
    from_m: Mapped[float | None] = mapped_column(Double)
    to_m: Mapped[float | None] = mapped_column(Double)
    sample_id: Mapped[str | None] = mapped_column(String(64))
    # Component values by name, e.g. {"AU": 0.007}; units live in the component table.
    analytes: Mapped[dict] = mapped_column(JSONB, default=dict)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)


class IntervalTable(Base):
    """An interval table of a drillhole set besides the assays: lithology, alteration, RQD, more
    assays. Its columns: [{"name", "kind": "number" | "text", "unit"}]."""

    __tablename__ = "interval_table"
    __table_args__ = (UniqueConstraint("set_id", "name", name="uq_interval_table_set_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(_set_fk(), index=True)
    name: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255), default="", server_default="")
    columns: Mapped[list] = mapped_column(JSONB, default=list)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # "interval": rows have from / to; "event": a single depth (kept in from_m and to_m), no thickness
    kind: Mapped[str] = mapped_column(String(16), default="interval", server_default="interval")


class Style(Base):
    """An entry of the styles library of a project.
    level: "palette" (a colouring: classes, a gradient or category colours), "layer" (the settings of one
    layer of a display), "display" (a template of a whole display). The frontend owns the shape of `body`;
    the server only checks the classes and colours in it."""

    __tablename__ = "style"
    __table_args__ = (UniqueConstraint("project_id", "level", "name", name="uq_style_project_level_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(_project_fk(), index=True)
    name: Mapped[str] = mapped_column(String(255))
    level: Mapped[str] = mapped_column(String(16))
    # "number" | "text" | "event" | "mixed"
    data_kind: Mapped[str] = mapped_column(String(16), default="number", server_default="number")
    body: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Field names (may contain "*") the style is applied to by default.
    default_for: Mapped[list] = mapped_column(JSONB, default=list)
    # Unknown top-level keys of an imported entry: written back on export.
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntervalRow(Base):
    __tablename__ = "interval_row"
    __table_args__ = (Index("ix_interval_row_hole", "table_id", "hole_id", "from_m"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("interval_table.id", ondelete="CASCADE"))
    hole_id: Mapped[str] = mapped_column(String(64))
    from_m: Mapped[float | None] = mapped_column(Double)
    to_m: Mapped[float | None] = mapped_column(Double)
    values: Mapped[dict] = mapped_column(JSONB, default=dict)


class Component(Base):
    __tablename__ = "component"
    __table_args__ = (UniqueConstraint("set_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(_set_fk())
    name: Mapped[str] = mapped_column(String(64))
    unit: Mapped[str] = mapped_column(String(32))


class ImportJob(Base):
    __tablename__ = "import_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(_set_fk(), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    filename: Mapped[str] = mapped_column(String(255))
    row_count: Mapped[int] = mapped_column(Integer)
    mapping: Mapped[dict] = mapped_column(JSONB)
    components: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditRun(Base):
    """Latest audit result of a drillhole set; a new run replaces the previous one."""

    __tablename__ = "audit_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(_set_fk(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    config: Mapped[dict] = mapped_column(JSONB)
    # {"by_severity": {"error": n, ...}, "by_code": {code: {"findings": n, "rows": m}}}
    counts: Mapped[dict] = mapped_column(JSONB)


class AuditFinding(Base):
    __tablename__ = "audit_finding"
    __table_args__ = (Index("ix_audit_finding_run", "run_id", "severity", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_run.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(8))
    table_name: Mapped[str] = mapped_column(String(16))
    hole_id: Mapped[str | None] = mapped_column(String(64))
    count: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class AssistantChat(Base):
    """A conversation with Claude in a project. `messages` is the Messages API history as sent
    (thinking blocks included), `items` what the chat panel shows; `pending` holds the state of
    a turn that waits for the browser to run interface commands."""

    __tablename__ = "assistant_chat"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[int] = mapped_column(_project_fk(), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Новый диалог")
    messages: Mapped[list] = mapped_column(JSONB, default=list)
    items: Mapped[list] = mapped_column(JSONB, default=list)
    pending: Mapped[dict] = mapped_column(JSONB, default=dict)
    model: Mapped[str] = mapped_column(String(64), default="")
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[float] = mapped_column(Double, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AssistantUsage(Base):
    """Tokens of one request to Claude, for the spending summary (kept when a chat is deleted)."""

    __tablename__ = "assistant_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True)
    chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Double, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class DataEdit(Base):
    """A correction made by hand in a table of a drillhole set: the edit log, with undo.
    `changes`: field -> [old, new]; for a deleted row the whole row as [value, None]."""

    __tablename__ = "data_edit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    set_id: Mapped[int] = mapped_column(_set_fk(), index=True)
    # "collar" | "survey" | "assay" | the id of an interval table
    table_key: Mapped[str] = mapped_column(String(32))
    table_name: Mapped[str] = mapped_column(String(255), default="")
    row_id: Mapped[int] = mapped_column(BigInteger)
    hole_id: Mapped[str] = mapped_column(String(64), default="")
    # "update" | "insert" | "delete"
    action: Mapped[str] = mapped_column(String(16))
    changes: Mapped[dict] = mapped_column(JSONB, default=dict)
    undone: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
