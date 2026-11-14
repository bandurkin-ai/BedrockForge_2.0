"""Database schema.

A project holds datasets of several kinds (drillhole sets, wireframes, ...). Drillhole tables
belong to a drillhole set (`set_id`); sets are independent, so hole ids may repeat between them.
Hole references in survey/assay are plain text (hole_id), not foreign keys:
files can arrive in any order and the audit stage reports orphans.
"""

from datetime import datetime

from sqlalchemy import DateTime, Double, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, func
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
    vertices: Mapped[bytes] = mapped_column(LargeBinary)  # float64 x, y, z per point
    triangles: Mapped[bytes] = mapped_column(LargeBinary)  # int32 i, j, k per triangle


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
    vertices: Mapped[bytes] = mapped_column(LargeBinary)  # float64 x, y, z
    offsets: Mapped[bytes] = mapped_column(LargeBinary)  # int64 start of every line, plus the end
    closed: Mapped[bytes] = mapped_column(LargeBinary)  # uint8 per line


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
    image: Mapped[bytes] = mapped_column(LargeBinary)  # display copy
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
