"""The structural trend (ТЗ 7.35 Д-15, ТЗ 8.9; ADR 0026, решения 11, 27; вопрос № 97): the axes of the anisotropy
change from point to point with the attitude of the structure.

`meta` of a trend of `kind: "structural"`: {v: 1, kind, source, radius, ratios: [1, r2, r3], size, color, centre:
null, origin: {type: "manual"}}, with `source` either

    {type: "measurements", set_id, table, alpha, dipdir, depth?}   the measurements «точка, α, Aп» (ADR 0021 §2):
        a table of the set — the position is the field `depth` along the hole, or, without it, the middle of
        FROM–TO of the row; `alpha` (0…90°) and `dipdir` (0…360°) are the fields of the plane;
    {type: "mesh", wireframe_id, r_n?}                              the supporting wireframe (8.9.2): the plane at
        a point is the plane of the nearest triangle (`geo.nearest`); `r_n` > 0 — the normal averaged by area
        over the triangles within r_n (`area_normals` + `mean_normals`), 0 by default.

    local_axes(t, xyz, *, source, threads=1, tick=None, report=None) -> float64 [m, 3, 3]
        the rows of R_j at every point: u1 — along the strike, u2 — along the dip (the line of dip with its sign
        turned to keep R right-handed: u2 = n × u1), u3 = n — the normal, the upper one (`rotation.plane_of`: of a
        vertical plane — the one with the dip direction in [0°; 180°)). R_j is the matrix of `rotation.rotation_matrix`
        of `angles_of_plane(α, Aп, pitch = 0)`; A_j = diag(1, 1 / r2, 1 / r3)·R_j (as `trend.matrix`). NaN rows —
        the point has no coordinates (never a zero).
    load_source(project, t, tick=None) -> Measurements | MeshNormals       what `local_axes` reads
    count_measurements(project, t) -> int | None                            usable measurements
    discs(project, t) -> {radius, n, total, thinned, discs: {x, y, z, nx, ny, nz}}   what 3D shows

The normal at a point of the measurements is the principal eigenvector of Σ w·n·nᵀ over the measurements within
R_тр, w = 1 / max(d, 1e−6 m) (a plane's normal is a line: the sign does not matter). A point without measurements
within R_тр takes the nearest measurement (counted in `report` and its journal). Measurements without a position
(a hole without a trajectory, a depth that is missing) or with α outside [0; 90], Aп outside [0; 360] or missing are
left out and counted — a gap is never a 0.

numpy only before `local_axes` is called: the kernels (numba) and scipy are imported then, never before BF-READY.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .rotation import bad_param, geostat_error, number

KIND = "structural"
RADIUS_DEFAULT, RADIUS_RANGE = 200.0, (10.0, 5000.0)  # R_тр, m
R_N_RANGE = (0.0, 200.0)  # m
DISCS_MESH_MAX = 10_000  # the discs of a wireframe: the centres of its faces, thinned
DISCS_POINTS_MAX = 50_000  # the discs of the measurements, thinned
LEVEL_RHO = 1.7453292519943295e-8  # sin 1e−6°: a plane flatter than this has no dip direction (rotation.LEVEL_DEG)
PLANE_VERTICAL = 1e-12  # |n_z| of a vertical plane (ADR 0021 §2)

Tick = Callable[[float], None]


def source_lost(what: str) -> ValueError:
    """The measurements or the wireframe of a trend are not in the project (409 `source_gone`)."""
    return geostat_error("source_gone", f"{what} структурного тренда удалены или недоступны.",
                         "Выберите другой источник в окне тренда или удалите тренд.", "source")  # fmt: skip


# --- parameters ----------------------------------------------------------------------------------------------------


def radius_of(value: Any) -> float:
    """R_тр, m: 10…5000; absent — 200."""
    if value is None or value == "":
        return RADIUS_DEFAULT
    x = number(_text_number(value), "radius", "Радиус влияния замеров R_тр")
    lo, hi = RADIUS_RANGE
    if not lo <= x <= hi:
        raise bad_param(
            "radius", "Радиус влияния замеров R_тр вне диапазона от 10 до 5000 м.", "Задайте от 10 до 5000 м."
        )
    return x


def r_n_of(value: Any) -> float:
    """The radius of averaging of the normal of a wireframe, m: 0…200; absent — 0 (the nearest triangle)."""
    if value is None or value == "":
        return 0.0
    x = number(_text_number(value), "source.r_n", "Радиус осреднения нормали r_n")
    if not R_N_RANGE[0] <= x <= R_N_RANGE[1]:
        raise bad_param("source.r_n", "Радиус осреднения нормали r_n вне диапазона от 0 до 200 м.",
                        "Задайте от 0 до 200 м.")  # fmt: skip
    return x


def _text_number(value: Any) -> Any:
    from .trend import text_number

    return text_number(value)


# --- the normals of the measurements -----------------------------------------------------------------------------


def _sin_cos(deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """sin and cos of degrees, exact on the multiples of 90° (as `sections.geometry.sin_cos_deg`)."""
    d = np.mod(deg, 360.0)
    s, c = np.sin(np.radians(d)), np.cos(np.radians(d))
    for at, (sv, cv) in ((0.0, (0.0, 1.0)), (90.0, (1.0, 0.0)), (180.0, (0.0, -1.0)), (270.0, (-1.0, 0.0))):
        hit = d == at
        s[hit], c[hit] = sv, cv
    return s, c


def plane_normals(alpha: Any, dipdir: Any) -> np.ndarray:
    """The upper unit normals (n × 3) of the planes «α, Aп»: (sin α·sin Aп, sin α·cos Aп, cos α). A row with α outside
    [0; 90], Aп outside [0; 360] (360 is 0) or a missing number is NaN."""
    a = np.asarray(alpha, dtype=np.float64)
    p = np.asarray(dipdir, dtype=np.float64)
    ok = np.isfinite(a) & np.isfinite(p) & (a >= 0.0) & (a <= 90.0) & (p >= 0.0) & (p <= 360.0)
    sa, ca = _sin_cos(np.where(ok, a, 0.0))
    sp, cp = _sin_cos(np.where(ok, p, 0.0))
    out = np.column_stack([sa * sp, sa * cp, ca])
    out[~ok] = np.nan
    return out


def turn_up(n: np.ndarray) -> np.ndarray:
    """The normals turned to the upper side; of a vertical plane (|n_z| ≤ 1e−12) — the one with the dip direction in
    [0°; 180°) (`rotation.plane_of`). A copy; NaN stays."""
    out = np.array(n, dtype=np.float64)
    x, y, z = out[:, 0], out[:, 1], out[:, 2]
    vertical = np.abs(z) <= PLANE_VERTICAL
    flip = np.where(vertical, (x < 0.0) | ((x == 0.0) & (y < 0.0)), z < 0.0)
    out[flip] = -out[flip]
    return out + 0.0  # no −0.0


def frames_of(normal: np.ndarray) -> np.ndarray:
    """R_j (m × 3 × 3) of the upper unit normals: the rows (strike, n × strike, n). A plane flatter than 1e−6° has the
    dip direction 0 (the strike points west). NaN normal — NaN frame."""
    n = np.asarray(normal, dtype=np.float64)
    m = len(n)
    nx, ny = n[:, 0], n[:, 1]
    rho = np.hypot(nx, ny)
    level = ~(rho > LEVEL_RHO)
    safe = np.where(level, 1.0, rho)
    s = np.column_stack([-ny / safe, nx / safe, np.zeros(m)])
    if level.any():
        west = np.array([-1.0, 0.0, 0.0])
        flat = west - (n[level] @ west)[:, None] * n[level]
        s[level] = flat / np.linalg.norm(flat, axis=1)[:, None]
    u2 = np.cross(n, s)
    out = np.empty((m, 3, 3), dtype=np.float64)
    out[:, 0], out[:, 1], out[:, 2] = s, u2, n
    out[np.isnan(n).any(axis=1)] = np.nan
    return out + 0.0


# --- sources -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Measurements:
    """The measurements of a trend: positions and the upper unit normals; `counts` — what was left out."""

    xyz: np.ndarray  # float64 [n, 3], project coordinates
    normal: np.ndarray  # float64 [n, 3]
    counts: dict[str, int] = field(default_factory=dict)  # rows, no_position, no_plane, used

    @property
    def n(self) -> int:
        return int(self.xyz.shape[0])


@dataclass(frozen=True, eq=False)
class MeshNormals:
    """The supporting wireframe: its `MeshTarget` (the tree of boxes) and the radius of averaging."""

    target: Any
    r_n: float = 0.0


def _meta(t: Any) -> Mapping[str, Any]:
    meta = t if isinstance(t, Mapping) else getattr(t, "meta", None)
    if not isinstance(meta, Mapping) or meta.get("kind") != KIND:
        raise bad_param("kind", "Это не структурный тренд.", "Выберите структурный тренд.")
    return meta


def source_of(t: Any) -> Mapping[str, Any]:
    src = _meta(t).get("source")
    if not isinstance(src, Mapping) or src.get("type") not in ("measurements", "mesh"):
        raise bad_param("source", "У структурного тренда не задан источник.", "Выберите замеры или опорный каркас.")
    return src


def _positions(project: Any, cols: Any, depth: np.ndarray | None) -> np.ndarray:
    """X, Y, Z (n × 3) of the rows: the middle of FROM–TO, or the `depth` along the hole; NaN — no position."""
    from bf_core.composites import coords
    from bf_core.composites import source as dh_source
    from bf_core.stats import source

    if depth is None:
        return np.asarray(source.midpoints(project, cols), dtype=np.float64)
    st = dh_source.trajectories(project, cols.table.set_id)
    traj = st.traj
    dense = coords.hole_index(st.hole_ids, cols.hole_db_ids)
    known = (dense >= 0) & (dense < traj.n_holes)
    at = np.where(known, dense, 0)
    built = known & (traj.offsets[at + 1] > traj.offsets[at]) if traj.n_holes else known
    rows = np.repeat(np.where(built, dense, -1), np.diff(cols.starts))
    return np.asarray(coords.positions(traj, rows, depth), dtype=np.float64)


def read_measurements(project: Any, src: Mapping[str, Any]) -> Measurements:
    """The measurements of `source {type: "measurements"}` from the project (409 `source_gone` — the set, the table
    or the fields are not there)."""
    from bf_core.api.errors import ApiError
    from bf_core.stats import source

    names = [str(src["alpha"]), str(src["dipdir"])] + ([str(src["depth"])] if src.get("depth") else [])
    try:
        cols = source.read(project, int(src["set_id"]), src.get("table"), names)
    except (ApiError, KeyError, TypeError, ValueError) as e:
        raise source_lost("Замеры") from e
    depth = cols.number(str(src["depth"])) if src.get("depth") else None
    xyz = _positions(project, cols, depth)
    normal = plane_normals(cols.number(str(src["alpha"])), cols.number(str(src["dipdir"])))
    placed = np.isfinite(xyz).all(axis=1)
    planar = np.isfinite(normal).all(axis=1)
    keep = placed & planar
    counts = {"rows": int(len(xyz)), "no_position": int((~placed).sum()),
              "no_plane": int((placed & ~planar).sum()), "used": int(keep.sum())}  # fmt: skip
    return Measurements(np.ascontiguousarray(xyz[keep]), turn_up(normal[keep]), counts)


def read_mesh(project: Any, src: Mapping[str, Any]) -> MeshNormals:
    """The wireframe of `source {type: "mesh"}` as a `MeshTarget` (409 `source_gone`)."""
    from bf_core.db import dialect
    from bf_core.wireframes import exact

    try:
        wid = int(src["wireframe_id"])
    except (KeyError, TypeError, ValueError) as e:
        raise source_lost("Опорный каркас") from e
    with dialect.read_tx(project.engine) as conn:
        found = exact.set_targets(conn, [wid])
    if not found:
        raise source_lost("Опорный каркас")
    state, stats = found[0]
    return MeshNormals(exact.geometry(project, state).target(stats), r_n_of(src.get("r_n")))


def load_source(project: Any, t: Any, tick: Tick | None = None) -> Measurements | MeshNormals:
    """What `local_axes` reads, from the project."""
    src = source_of(t)
    if tick is not None:
        tick(0.0)
    return read_measurements(project, src) if src["type"] == "measurements" else read_mesh(project, src)


def count_measurements(project: Any, t: Any) -> int | None:
    """The measurements of a trend that can be used; None — the source is a wireframe or is gone."""
    src = source_of(t)
    if src["type"] != "measurements":
        return None
    try:
        return read_measurements(project, src).n
    except ValueError:
        return None


# --- the axes -----------------------------------------------------------------------------------------------------


def _count(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def local_axes(t: Any, xyz: Any, *, source: Measurements | MeshNormals, threads: int = 1, tick: Tick | None = None,
               report: dict[str, Any] | None = None) -> np.ndarray:  # fmt: skip
    """R_j (m × 3 × 3, float64) at the points `xyz` (m × 3, project coordinates) — the module docstring. `t` — the
    `meta` of the trend (`radius` is read). `tick(fraction)` is called after every band and may raise to cancel.
    `report` gets {points, nearest (the points that took the nearest measurement), measurements, journal [str]}."""
    meta = _meta(t)
    q = np.ascontiguousarray(xyz, dtype=np.float64)
    if q.ndim != 2 or q.shape[1] != 3:
        raise ValueError(f"xyz must be an m × 3 array, got {q.shape}")
    given: dict[str, Any] = {"points": len(q), "nearest": 0, "journal": []}
    if isinstance(source, Measurements):
        normal = _from_measurements(source, q, radius_of(meta.get("radius")), threads, tick, given)
    elif isinstance(source, MeshNormals):
        normal = _from_mesh(source, q, threads, tick, given)
    else:
        raise TypeError(f"source must be Measurements or MeshNormals, got {type(source).__name__}")
    if report is not None:
        report.update(given)
    return frames_of(turn_up(normal))


def _from_measurements(src: Measurements, q: np.ndarray, radius: float, threads: int, tick: Tick | None,
                       report: dict[str, Any]) -> np.ndarray:  # fmt: skip
    from . import trend_struct_kernels as kernels

    if src.n == 0:
        raise geostat_error("no_measurements", "В таблице нет пригодных замеров: ни одного с положением, α и Aп.",
                            "Проверьте таблицу замеров, её поля α, Aп и глубину.", "source")  # fmt: skip
    report["measurements"] = src.n
    grid = kernels.build_grid(src.xyz, src.normal, radius)
    normal, count = kernels.axes(grid, q, radius, threads=threads, tick=tick)
    far = np.flatnonzero(count == 0)
    if far.size:  # no measurement within R_тр: the nearest one (`Σ` of one is its normal)
        from scipy.spatial import cKDTree

        _d, at = cKDTree(src.xyz).query(q[far], k=1)
        normal[far] = src.normal[at]
        report["nearest"] = int(far.size)
        report["journal"].append(
            f"Точек без замеров в радиусе R_тр = {radius:g} м — {_count(int(far.size))}: взят ближайший замер."
        )
    return normal


def _from_mesh(src: MeshNormals, q: np.ndarray, threads: int, tick: Tick | None, report: dict[str, Any]) -> np.ndarray:
    from bf_core.geo.nearest import area_normals, mean_normals, nearest_triangles

    near = nearest_triangles(src.target, q, threads=threads, tick=tick)
    normal = np.array(near.normal, dtype=np.float64)
    if src.r_n > 0.0:
        found = np.flatnonzero(near.found)
        if found.size:
            area = area_normals(src.target, near.point[found], near.normal[found], src.r_n, threads=threads)
            normal[found] = mean_normals(area, near.normal[found])
    lost = int((~near.found).sum())
    if lost:  # no coordinates (or no triangle): NaN, never a direction
        report["journal"].append(f"Точек без координат или без каркаса рядом — {_count(lost)}: оси не определены.")
    return normal


# --- the discs of the 3D window -----------------------------------------------------------------------------------


def _thin(n: int, limit: int) -> np.ndarray:
    """At most `limit` of n rows, evenly (the first and every step-th); all of them when n ≤ limit."""
    if n <= limit:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, limit).astype(np.int64))


def discs(project: Any, t: Any) -> dict[str, Any]:
    """`{radius, n, total, thinned, discs: {x, y, z, nx, ny, nz}}`: a disc at every measurement (its upper normal) — at
    most 50 000; of a wireframe — at the centres of its faces, at most 10 000. World coordinates, float64."""
    meta = _meta(t)
    src = source_of(meta)
    radius = radius_of(meta.get("radius"))
    if src["type"] == "measurements":
        m = read_measurements(project, src)
        pick = _thin(m.n, DISCS_POINTS_MAX)
        xyz, normal, total = m.xyz[pick], m.normal[pick], m.n
    else:
        mesh = read_mesh(project, src).target
        v, tri = mesh.xyz, mesh.tri
        total = int(len(tri))
        pick = _thin(total, DISCS_MESH_MAX)
        tr = tri[pick]
        a, b, c = v[tr[:, 0]], v[tr[:, 1]], v[tr[:, 2]]
        cross = np.cross(b - a, c - a)
        length = np.linalg.norm(cross, axis=1)
        ok = length > 0.0  # a face without area has no plane
        xyz = ((a + b + c) / 3.0)[ok]
        normal = turn_up(cross[ok] / length[ok][:, None])
    d = {k: [float(x) for x in xyz[:, i]] for i, k in enumerate("xyz")}
    d.update({f"n{k}": [float(x) for x in normal[:, i]] for i, k in enumerate("xyz")})
    return {"radius": radius, "n": int(len(xyz)), "total": total, "thinned": int(len(xyz)) < total, "discs": d}


__all__ = ["KIND", "MeshNormals", "Measurements", "count_measurements", "discs", "frames_of", "load_source",
           "local_axes", "plane_normals", "radius_of", "source_of", "turn_up"]  # fmt: skip
