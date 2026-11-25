"""Tables of the project database (`project.sqlite` / schema `p_<id>`) and of the instance database
(`instance.sqlite` in the profile / schema `public`). ADR 0003 §3: no `project_id` in project tables.

SQLAlchemy Core tables; later stages add their tables here (or in `bf_core/<module>/schema.py` imported below)
together with an Alembic revision. Times are set by the application (`dialect.utcnow()`).
"""

from __future__ import annotations

import sqlalchemy as sa

from .dialect import NAMING_CONVENTION, PK, REF, JSONType, UtcDateTime

project_md = sa.MetaData(naming_convention=NAMING_CONVENTION)
instance_md = sa.MetaData(naming_convention=NAMING_CONVENTION)


def _jobs(md: sa.MetaData) -> tuple[sa.Table, sa.Table]:
    jobs = sa.Table(
        "jobs",
        md,
        sa.Column("id", PK, primary_key=True),
        sa.Column("queue", sa.String(16), nullable=False),  # heavy | quick
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),  # queued running done failed cancelled
        sa.Column("priority", sa.Integer, nullable=False, default=100),
        sa.Column("params", JSONType, nullable=False),
        # Archive password etc.: only until a worker takes the job (claim clears it); never in state or logs.
        sa.Column("secret", sa.String(1024)),
        sa.Column("result", JSONType),
        sa.Column("error", JSONType),  # {code, message, hint}
        sa.Column("progress", sa.Double, nullable=False, default=0.0),
        sa.Column("message", sa.String(400)),
        sa.Column("cancel_requested", sa.Boolean, nullable=False, default=False),
        sa.Column("attempts", sa.Integer, nullable=False, default=0),
        sa.Column("max_attempts", sa.Integer, nullable=False, default=3),
        sa.Column("memory_mb", sa.Integer),
        sa.Column("worker", sa.String(64)),
        sa.Column("project_uid", sa.String(32)),  # instance jobs: the project they concern, if any
        sa.Column("created_at", UtcDateTime, nullable=False),
        sa.Column("started_at", UtcDateTime),
        sa.Column("heartbeat_at", UtcDateTime),
        sa.Column("finished_at", UtcDateTime),
        sa.Index(None, "queue", "status", "priority", "id"),
    )
    job_log = sa.Table(
        "job_log",
        md,
        sa.Column("job_id", REF, sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("seq", sa.Integer, primary_key=True),
        sa.Column("at", UtcDateTime, nullable=False),
        sa.Column("level", sa.String(8), nullable=False),  # info | warn | error
        sa.Column("text", sa.Text, nullable=False),
    )
    return jobs, job_log


# --- project level ------------------------------------------------------------------------------

project_meta = sa.Table(
    "project_meta",
    project_md,
    sa.Column("id", PK, primary_key=True),  # always 1
    sa.Column("uid", sa.Uuid, nullable=False),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("name_key", sa.String(200), nullable=False),
    sa.Column("description", JSONType, nullable=False),  # {deposit, license, stage, text}
    sa.Column("crs", sa.String(200)),
    sa.Column("app_version", sa.String(32), nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.Column("version", sa.Integer, nullable=False, default=1),
)

objects = sa.Table(
    "objects",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("parent_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE")),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("name_key", sa.String(200), nullable=False),
    sa.Column("folder", sa.String(200)),
    sa.Column("sort_key", sa.Double, nullable=False, default=0.0),
    sa.Column("state", sa.String(16), nullable=False, default="ready"),  # building | ready
    sa.Column("version", sa.Integer, nullable=False, default=1),
    sa.Column("meta", JSONType, nullable=False),
    sa.Column("extent", JSONType),  # [xmin, ymin, zmin, xmax, ymax, zmax]
    sa.Column("crs", sa.String(200)),
    sa.Column("job_id", REF),  # the job building it (state = building)
    sa.Column("author", sa.String(100)),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.Index(None, "kind", "state"),
    sa.Index(None, "parent_id"),
)

object_refs = sa.Table(
    "object_refs",
    project_md,
    sa.Column("src_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("dst_id", REF, sa.ForeignKey("objects.id", ondelete="RESTRICT"), primary_key=True),
    sa.Column("role", sa.String(32), primary_key=True),
    sa.Index(None, "dst_id"),
)

blobs = sa.Table(
    "blobs",
    project_md,
    sa.Column("sha256", sa.String(64), primary_key=True),
    sa.Column("size", sa.BigInteger, nullable=False),
    sa.Column("media_type", sa.String(100), nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
)

blob_owners = sa.Table(
    "blob_owners",
    project_md,
    sa.Column("sha256", sa.String(64), sa.ForeignKey("blobs.sha256", ondelete="CASCADE"), primary_key=True),
    sa.Column("owner_kind", sa.String(32), primary_key=True),
    sa.Column("owner_id", REF, primary_key=True),
    sa.Column("role", sa.String(32), primary_key=True),
    sa.Index(None, "owner_kind", "owner_id"),
)

jobs, job_log = _jobs(project_md)

edit_log = sa.Table(
    "edit_log",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("scope_kind", sa.String(32), nullable=False),
    sa.Column("scope_id", REF, nullable=False),
    sa.Column("group_id", REF, nullable=False),  # one user action may touch many rows
    sa.Column("action", sa.String(32), nullable=False),  # update | insert | delete | meta
    sa.Column("row_ref", JSONType),
    sa.Column("changes", JSONType, nullable=False),  # {field: [before, after]}
    sa.Column("undone", sa.Boolean, nullable=False, default=False),
    sa.Column("author", sa.String(100)),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Index(None, "scope_kind", "scope_id", "id"),
)

settings = sa.Table(
    "settings",
    project_md,
    sa.Column("scope", sa.String(32), primary_key=True),  # view | tree | …
    sa.Column("key", sa.String(200), primary_key=True),
    sa.Column("value", JSONType, nullable=False),
    sa.Column("version", sa.Integer, nullable=False, default=1),
)

# --- drillhole sets (Э3, ADR 0003 §3–4) ---------------------------------------------------------
# A set is an object of kind "drillhole_set"; its tables hang on it with ON DELETE CASCADE.

dh_holes = sa.Table(
    "dh_holes",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("set_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("x", sa.Double),
    sa.Column("y", sa.Double),
    sa.Column("z", sa.Double),
    sa.Column("td", sa.Double),  # total depth
    sa.Column("azimuth", sa.Double),
    sa.Column("dip", sa.Double),
    sa.Column("has_collar", sa.Boolean, nullable=False, default=False),
    sa.Column("attrs", JSONType),  # other collar columns: {name: value}
    sa.UniqueConstraint("set_id", "name"),
)

dh_surveys = sa.Table(
    "dh_surveys",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("set_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("hole_id", REF, sa.ForeignKey("dh_holes.id", ondelete="CASCADE"), nullable=False),
    sa.Column("depth", sa.Double, nullable=False),
    sa.Column("azimuth", sa.Double),
    sa.Column("dip", sa.Double),
    sa.Column("attrs", JSONType),
    # Э7 (ADR 0011 §1): a station with coordinates (trench vertices) — they win over the desurvey method.
    sa.Column("x", sa.Double),
    sa.Column("y", sa.Double),
    sa.Column("z", sa.Double),
    sa.Index(None, "set_id", "hole_id", "depth"),
    sa.Index(None, "hole_id"),  # cascades from dh_holes and «скважина без данных» checks
)

dh_tables = sa.Table(
    "dh_tables",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("set_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("name_key", sa.String(200), nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),  # assay | interval | event
    sa.Column("row_count", sa.Integer, nullable=False, default=0),
    sa.Column("rev", sa.Integer, nullable=False, default=1),  # grows on every edit (column snapshots)
    # A table being imported is "building" (invisible) until the job swaps it in (ТЗ 7.6: no partial result).
    sa.Column("state", sa.String(16), nullable=False, default="ready"),
    sa.Column("job_id", REF),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.Index(None, "set_id", "name_key"),
)

dh_columns = sa.Table(
    "dh_columns",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("table_id", REF, sa.ForeignKey("dh_tables.id", ondelete="CASCADE"), nullable=False),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),  # number | text
    sa.Column("unit", sa.String(40)),
    sa.Column("slot", sa.Integer, nullable=False),  # position in dh_rows.num (number) or key in txt (text)
    sa.Column("role", sa.String(16), nullable=False, default="value"),  # component | value | source
    sa.Column("deleted", sa.Boolean, nullable=False, default=False),
    sa.Column("position", sa.Integer, nullable=False, default=0),  # order of columns in the table view
    sa.Index(None, "table_id"),
)

dh_rows = sa.Table(
    "dh_rows",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("table_id", REF, sa.ForeignKey("dh_tables.id", ondelete="CASCADE"), nullable=False),
    sa.Column("hole_id", REF, sa.ForeignKey("dh_holes.id", ondelete="CASCADE"), nullable=False),
    sa.Column("from_m", sa.Double),
    sa.Column("to_m", sa.Double),
    # All number columns as one float64 little-endian vector: slot = dh_columns.slot, NaN = missing,
    # a short vector = missing tail slots (ADR 0003 §4).
    sa.Column("num", sa.LargeBinary),
    sa.Column("qual", sa.LargeBinary),  # per slot: 0 plain, 1 below / 2 above detection limit; NULL = all plain
    sa.Column("txt", JSONType),  # text columns {slot: value}
    sa.Index(None, "table_id", "hole_id", "from_m"),
    sa.Index(None, "hole_id"),  # cascades from dh_holes and «скважина без данных» checks
)

# Э12 (ADR 0018 §2): a result of sample processing — a table of the set (composites, coding, ore intervals…),
# columns written in place (top cut, equivalent, DECL_W) or neither (conditions analysis: table_id NULL).
# `inputs` holds no ids (names, revisions, geometry keys): «устарел» is computed on reading, `.bfggis` keeps it.
dh_results = sa.Table(
    "dh_results",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("set_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("table_id", REF, sa.ForeignKey("dh_tables.id", ondelete="CASCADE")),
    sa.Column("op", sa.String(16), nullable=False),  # length domain bench coding merge coords topcut … conditions
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("params", JSONType, nullable=False),  # the numbers the kernel ran with
    sa.Column("inputs", JSONType, nullable=False),  # tables (name_key, token, rev, fields), set version, meshes
    sa.Column("sha256", sa.String(64), nullable=False),  # of the input bytes and the parameters
    sa.Column("algo", sa.String(32), nullable=False),  # version of the algorithm of the operation
    sa.Column("report", JSONType, nullable=False),  # summary before/after, thresholds, written columns, timings
    sa.Column("job_id", REF),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Index(None, "set_id"),
    sa.Index(None, "table_id"),  # cascades from dh_tables
)

# Э18 (ADR 0025 §4, §8, §11): the graph of dependencies of computed objects. A row is an edge «dst was built from
# src» (role `in`, with the fingerprint of the source at that time and its name for the reason after a deletion)
# or the mark «dst is pinned» (role `pin`, src empty). Nodes are (kind, id, sub): no foreign keys — a source may be
# deleted and the row must stay; `sub` is the name of a field of a block model (`bm_field`), else "".
lineage = sa.Table(
    "lineage",
    project_md,
    sa.Column("dst_kind", sa.String(48), primary_key=True),
    sa.Column("dst_id", REF, primary_key=True),
    sa.Column("dst_sub", sa.String(200), primary_key=True, default=""),
    sa.Column("role", sa.String(8), primary_key=True, default="in"),  # in | pin
    sa.Column("src_kind", sa.String(48), primary_key=True, default=""),
    sa.Column("src_id", REF, primary_key=True, default=0),  # 0: the source is not in this project (archive)
    sa.Column("src_sub", sa.String(200), primary_key=True, default=""),
    sa.Column("pos", sa.Integer, nullable=False, default=0),  # order of the inputs
    sa.Column("src_fp", sa.String(200)),  # fingerprint of the source when dst was built; NULL — not compared
    sa.Column("src_name", sa.String(200), nullable=False, default=""),
    sa.Index(None, "src_kind", "src_id"),  # «Зависимые»: who was built from this node (dst is the primary key)
)

import_log = sa.Table(
    "import_log",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("object_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE")),
    sa.Column("job_id", REF),
    sa.Column("file_name", sa.String(400), nullable=False),
    sa.Column("format", sa.String(32), nullable=False),
    sa.Column("target", sa.String(32), nullable=False),  # collars | surveys | assay | interval | event | objects
    sa.Column("mapping", JSONType, nullable=False),  # read settings and column assignment
    sa.Column("counts", JSONType, nullable=False),  # rows read/written, replaced detection limits, errors
    sa.Column("sha256", sa.String(64)),  # the source file in blobs (owner kind "import")
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Index(None, "object_id"),
)

export_log = sa.Table(
    "export_log",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("object_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE")),
    sa.Column("job_id", REF),
    sa.Column("file_name", sa.String(400), nullable=False),
    sa.Column("format", sa.String(32), nullable=False),
    sa.Column("settings", JSONType, nullable=False),
    sa.Column("counts", JSONType, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
)

audit_runs = sa.Table(  # ТЗ 7.12: the last audit of a set (a new run replaces it)
    "audit_runs",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("set_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE"), nullable=False),
    # the sample table checked (NULL once it is deleted or replaced: the result is «устарел» then)
    sa.Column("table_id", REF, sa.ForeignKey("dh_tables.id", ondelete="SET NULL")),
    sa.Column("table_name", sa.String(200), nullable=False),
    sa.Column("config", JSONType, nullable=False),  # thresholds used
    sa.Column("versions", JSONType, nullable=False),  # set version and table revision: «устарел» when they move
    sa.Column("summary", JSONType, nullable=False),  # every rule: rows and holes affected (passed ones too)
    sa.Column("totals", JSONType, nullable=False),  # findings by severity
    sa.Column("job_id", REF),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Index(None, "set_id"),
)

audit_findings = sa.Table(  # grouped by rule and hole: the count and up to 20 examples
    "audit_findings",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("run_id", REF, sa.ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("code", sa.String(40), nullable=False),
    sa.Column("severity", sa.String(10), nullable=False),
    sa.Column("source", sa.String(10), nullable=False),  # collar | survey | assay
    sa.Column("hole", sa.String(200)),
    sa.Column("count", sa.Integer, nullable=False),
    sa.Column("examples", JSONType),
    sa.Index(None, "run_id", "severity"),
    sa.Index(None, "run_id", "hole"),
)

# --- 3D displays (Э6, ADR 0007 §3) ----------------------------------------------------------------

styles = sa.Table(  # ТЗ 7.16: the style library of the project (palettes, layer styles, display templates)
    "styles",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("uid", sa.String(32), nullable=False, unique=True),  # links of displays (survive .bfggis)
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("name_key", sa.String(255), nullable=False),
    sa.Column("level", sa.String(16), nullable=False),  # palette | layer | display
    sa.Column("data_kind", sa.String(16), nullable=False),  # number | text | event | mixed
    sa.Column("body", JSONType, nullable=False),
    sa.Column("default_for", JSONType, nullable=False),  # field masks: AU, AU*
    sa.Column("extra", JSONType, nullable=False),  # unknown keys of a .bfstyle.json entry, kept
    sa.Column("version", sa.Integer, nullable=False, default=1),
    sa.Column("author", sa.String(100)),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("level", "name_key"),
)

charts = sa.Table(  # 1 : 1 to objects(kind="chart"): saved displays along holes (striplog) now, charts on Э13
    "charts",
    project_md,
    sa.Column("object_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("settings", JSONType, nullable=False),
    sa.Column("auto_name", sa.Boolean, nullable=False, default=False),
)

# --- lines, rasters, wireframes (Э7, ADR 0011 §1) ----------------------------------------------------
# A line file is objects(kind="line_file") with its source schema in `meta` and the `source` blob; its layers
# are objects(kind="line_layer", parent_id=file) with geometry in blobs (BLOB_ROLES) and a row here.

line_layers = sa.Table(  # 1 : 1 to objects(kind="line_layer")
    "line_layers",
    project_md,
    sa.Column("object_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("line_count", sa.Integer, nullable=False),  # 0 for a layer of points
    sa.Column("point_count", sa.Integer, nullable=False),  # vertices (points)
    sa.Column("length", sa.Double, nullable=False),
    sa.Column("closed_count", sa.Integer, nullable=False),
    sa.Column("flat", sa.Boolean, nullable=False),  # every line lies at one level (points: all at one level)
    sa.Column("color", sa.String(16)),  # #rrggbb
    sa.Column("attr_schema", JSONType, nullable=False),  # [{name, kind, unit, scope: feature | vertex}]
    sa.Column("points_only", sa.Boolean, nullable=False),  # no offsets / closed blobs
    sa.Column("origin", JSONType, nullable=False),  # [x, y, z]: whole metres, float32 offsets of the display
    # Э9 (ADR 0015 §1): the number of the current version of the journal (`line_layer_versions`) — the base of a
    # save, not `objects.version` (renaming and colour raise that one); `next_lid` — the stable number the next
    # new line gets (NULL: the line count, the layer has no `lid` blob and the number of a line is its position).
    sa.Column("rev", sa.Integer, nullable=False, default=1, server_default="1"),
    sa.Column("next_lid", sa.Integer),
)

line_layer_versions = sa.Table(  # ТЗ 7.22: the journal of saves of a line layer; files — owner `line_version`
    "line_layer_versions",
    project_md,
    sa.Column("id", PK, primary_key=True),
    sa.Column("layer_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),  # 1…N, no gaps
    sa.Column("action", sa.String(16), nullable=False),  # import create save restore move operation
    sa.Column("note", sa.String(255)),
    sa.Column("author", sa.String(100)),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("line_count", sa.Integer, nullable=False),
    sa.Column("point_count", sa.Integer, nullable=False),
    sa.Column("prev_line_count", sa.Integer, nullable=False),
    sa.Column("prev_point_count", sa.Integer, nullable=False),
    # length (3D and plan), area, closed, flat, extent, origin, attr_schema, points_only, next_lid
    sa.Column("stats", JSONType, nullable=False),
    sa.UniqueConstraint("layer_id", "version"),
)

rasters = sa.Table(  # 1 : 1 to objects(kind="raster")
    "rasters",
    project_md,
    sa.Column("object_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("width", sa.Integer, nullable=False),
    sa.Column("height", sa.Integer, nullable=False),
    # affine (6), affine_z or null, source (geotiff/world/tab/grf/section/manual), crs, pixel, rotation,
    # corners3d f64 4×3, residuals of the reference points of the other files
    sa.Column("georef", JSONType),
    sa.Column("elevation", sa.Double),  # null: under the lowest object
    sa.Column("vertical", sa.Boolean, nullable=False, default=False),
    sa.Column("opacity", sa.Double, nullable=False, default=1.0),  # 0.05…1
    sa.Column("version", sa.Integer, nullable=False, default=1),
)

wireframes = sa.Table(  # 1 : 1 to objects(kind="wireframe"); a DTM is a wireframe with `dtm` set (ТЗ 7.20)
    "wireframes",
    project_md,
    sa.Column("object_id", REF, sa.ForeignKey("objects.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("point_count", sa.Integer, nullable=False),
    sa.Column("triangle_count", sa.Integer, nullable=False),
    sa.Column("origin", JSONType, nullable=False),
    sa.Column("stats", JSONType, nullable=False),
    sa.Column("render", JSONType),  # parts and LOD (the files themselves are in cache\mesh\)
    sa.Column("attrs", JSONType, nullable=False),  # attributes set by the user
    # null, or {clip, tint, opacity, shade}; the draped raster is object_refs(src=wireframe, dst=raster, role=drape)
    sa.Column("dtm", JSONType),
)

# --- instance level -------------------------------------------------------------------------------

instance_jobs, instance_job_log = _jobs(instance_md)

#: Cache of the project list (the folders and their project_meta are the truth); deleted without loss.
projects_cache = sa.Table(
    "projects",
    instance_md,
    sa.Column("uid", sa.String(32), primary_key=True),
    sa.Column("path", sa.Text, nullable=False),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("name_key", sa.String(200), nullable=False),
    sa.Column("summary", JSONType, nullable=False),
    sa.Column("mtime", sa.Double, nullable=False),
    sa.Column("opened_at", UtcDateTime),
)

#: Kinds of blob owners. A new kind that stores files registers here, or tests/test_db.py fails (ТЗ 6.5).
#: `line_version` — a row of `line_layer_versions` (ADR 0015 §1): the files of a saved version of a line layer.
BLOB_OWNER_KINDS: frozenset[str] = frozenset({"object", "job", "import", "line_version"})

#: Roles of a blob for its owner (ADR 0011 §1). `data` — a file of no particular role; `source` — the file as
#: loaded; `sidecar:<ext>` — a companion file of a raster (`sidecar:jgw`, `sidecar:tab`, …); `display0/1` —
#: raster copies ≤ 4096 / ≤ 1024 px; lines: `xyz <f8 N×3`, `offsets <i8`, `closed u1`, `seq <u4`, `attrs` and
#: `vattrs` (Parquet), or `pack:<n>` — entry n of a blob that holds these files of several small layers
#: (`lines/pack.py`); wireframes: `xyz <f8`, `tri <i4`; point clouds (ADR 0014 §1, `lines/cloud.py`): `xyz`, `seq`,
#: `class u1` (ASPRS code), `ret u1` (return number and number of returns), `h <f4` (height above the ground),
#: `dz <f4` (Z − DTM); `lid <u4` — the stable number of every line of a layer (ADR 0015 §1; no blob: the position).
#: A new role registers here, or add_owner refuses it.
BLOB_ROLES: frozenset[str] = frozenset(
    {
        "data",
        "source",
        "display0",
        "display1",
        "xyz",
        "offsets",
        "closed",
        "seq",
        "attrs",
        "vattrs",
        "tri",
        "class",
        "ret",
        "h",
        "dz",
        "lid",
        "cells",  # block models: Parquet of the occupied cells (ADR 0017 §3, blocks/schema.py)
        "vario",  # variogram models: the form of the calculation (JSON, ADR 0022 §12, geostat/variogram/store.py)
        "nscore",  # variogram models: the table of the normal scores (Parquet: value, score, weight)
    }  # fmt: skip
)
BLOB_ROLE_PREFIXES: tuple[str, ...] = ("sidecar:", "pack:", "fld:")


def valid_blob_role(role: str) -> bool:
    if role in BLOB_ROLES:
        return True
    for prefix in BLOB_ROLE_PREFIXES:
        rest = role[len(prefix) :] if role.startswith(prefix) else ""
        if 0 < len(rest) <= 16 and rest.isascii() and rest.isalnum() and rest == rest.lower():
            return True
    return False
