"""The orientation of intervals by wireframes and surfaces (ТЗ 8.5.2 «Каркас или поверхность»; ADR 0021 §5,
решения 7, 8).

    o = mesh_orientation(traj, hole, frm, to, targets, d_max=50.0, r_n=0.0, todo=None, threads=1, tick=None,
                         stage=None) -> MeshOrientation
        o.normal    float64 [n, 3]  the unit normal of the plane of every interval, n_z ≥ 0; NaN — not found
        o.way       uint8 [n]       WAY_NONE | WAY_CROSSING («каркас: пересечение») | WAY_NEAREST («каркас: ближайшая»)
        o.count     int32 [n]       crossings of the hole with the wireframes inside the interval
        o.distance  float64 [n]     WAY_NEAREST — metres from the middle of the interval to the wireframes; else NaN
        o.journal, o.report         lines for the job log; numbers for `dh_results.report`
    mesh_source(read, stage=None) -> orient.MeshSource
        the source «каркас» of the chain (`orient.Inputs.mesh`, `tt.of_table(…, mesh=…)`): `read(source)` gives
        the wireframes of a `spec.Source` of the kind mesh as `targets`, or None — the source cannot answer
    project_source(project, keep=True, max_triangles=None, stage=None) -> orient.MeshSource
        the same on the wireframes of a project (`StoredTargets`: `source.meshes` through `wireframes.exact`,
        one at a time); more than `max_triangles` in the list — the source does not answer (the preview)
    warm()                          compiles the kernels (the step `intercepts`)

`traj` — `desurvey.Trajectories` of the set; `hole` — the dense hole index of every interval (−1 — the set does
not have the hole); `frm`, `to` — its depths; `todo` — bool [n], the intervals asked about (None — all).
`targets` — the wireframes and surfaces, closed or not (`geo.intersect.MeshTarget`; how many there may be —
16 — is the business of `spec`): a list, or anything with `len()` that gives them one after another when
iterated — a job builds a wireframe when it is asked for, and only one is in memory at a time (the one before
is let go before the next is asked for; a generator has to let it go too). They are gone through once, and
once more when r_n > 0.

The rule, for an interval of a hole with a trajectory, both depths and TO > FROM (any other gets nothing):

  1. Crossings. `intersect.hole_crossings` of the holes with every wireframe; those at a depth within
     [FROM − 1e−6; TO + 1e−6] m are inside the interval (one on a boundary is inside both intervals). The unit
     normal of the triangle crossed is turned against the hole — −side · n, `side` of the crossing — and the
     normals of all the crossings of all the wireframes are added (in the order of the wireframes, then of the
     depths: a handful of unit vectors). One crossing — the normal of its triangle; the entry into a body and
     the exit — norm(n_in − n_out) by the outward normals, as the ТЗ writes it; the hanging wall and the
     footwall as two surfaces — their middle plane; folds and several bodies — the same sum. A triangle
     without area gives nothing. A sum of the length 1e−9 or more is the plane: WAY_CROSSING.
  2. The nearest. No crossing, or the sum is shorter (a trench that goes down through a surface and comes
     back: the two normals cancel) — the triangle of all the wireframes nearest to the middle of the interval
     (`Trajectories.positions` at (FROM + TO) / 2) and not farther than d_max: `nearest.nearest_triangles`, the
     wireframes one into another; equal distances — the lower number of the triangle, then of the wireframe.
     WAY_NEAREST. d_max = 0 — crossings only: nothing is searched for.
  3. r_n > 0 — instead of the normal of the nearest triangle, Σ area · normal of the triangles not farther than
     r_n from the point found, every normal turned to the side of the nearest triangle
     (`nearest.area_normals`); a sum shorter than 1e−9 — the nearest triangle itself. The triangles are those
     of all the wireframes of the list, not of the wireframe of the nearest one alone: so the answer does not
     depend on how the triangles are put into wireframes — the two sides of a thin body give its middle plane
     whether they are one closed wireframe or two surfaces, as they do in the rule of the crossings and in the
     search of the nearest. The price is the second pass over the wireframes. r_n does not touch crossings.
  4. Neither — WAY_NONE: the normal is NaN, never a zero; the next source of the chain or bit 2 of TT_FLAG.

The normal is given to the upper side (`formulas.upper_normal`: n_z ≥ 0; of a vertical plane — the azimuth in
[0°; 180°)), so it does not depend on the order of the corners of the triangles.

Coordinates: project ones, float64. The kernels here and in `geo` read them only as differences — the
subtraction of a reference point of ТЗ 8.0.1 without a rounding of its own.

Rows are independent: a row of the result depends on its interval and the wireframes alone — not on the other
rows, their order or `todo`. Strips of `STRIP_ROWS` rows and bands of points go to Python threads over `nogil`
kernels (no `prange`, no `fastmath`; ADR 0021, решение 20): the same bytes with 1, 2, 4 and 8 threads.
`tick(fraction)` is called in the calling thread between portions of holes, bands and wireframes and may raise
to cancel; `stage(text)` gets the name of the step that starts («Пересечения стволов: каркас 2 из 5»).

Memory: 37 bytes a row of the result; 92 bytes a row asked about for the search of the nearest (the middles
and `nearest.Nearest`; 68 more while one wireframe is taken into another); about 90 bytes a crossing. Nothing
a triangle beyond its `MeshTarget`.

Time (подшаг 15.7; a plate of two sheets 12 m apart dipping 35°, the triangles in the order of the store;
holes of 100 m that start 20–70 m above it, intervals of 1 m, d_max 50 m — 96 % of them take the nearest
triangle). 1 million triangles, 500 thousand intervals: 2,3 s on one thread, 0,8 s on four. 10 million
triangles, 5 million intervals: 17,7 s on four threads — the crossings 1,7 s (one thread, `geo.intersect`), the
nearest triangles 15,4 s — and 56 s on one; the process with the wireframe and its tree peaks at 1,6 GB. The
second pass of r_n = 5 m adds 16,5 s on four threads. The search of the nearest is the cost: 11 µs a point on
one thread for points tens of metres from a dipping surface of triangles of a metre (the boxes of the tree are
along the axes, `geo/nearest.py` «Time»).

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from bf_core.composites import strips
from bf_core.composites.length import check_range
from bf_core.composites.strips import Tick
from bf_core.drillholes.desurvey import Trajectories
from bf_core.geo.intersect import MeshTarget, hole_crossings
from bf_core.geo.nearest import NORMAL_MIN, Nearest, area_normals, mean_normals, nearest_triangles
from bf_core.jit import njit

from . import orient
from .formulas import upper_normal

__all__ = ["WAY_CROSSING", "WAY_NEAREST", "WAY_NONE", "MeshOrientation", "StoredTargets", "Targets",
           "mesh_orientation", "mesh_source", "project_source", "warm"]  # fmt: skip

WAY_NONE, WAY_CROSSING, WAY_NEAREST = 0, 1, 2  # `way` of an interval
D_MAX_DEFAULT, D_MAX_RANGE = 50.0, (0.0, 1000.0)  # d_max, m (ТЗ 8.5.4)
R_N_DEFAULT, R_N_RANGE = 0.0, (0.0, 200.0)  # r_n, m
DEPTH_TOL_M = 1e-6  # a crossing this far outside an interval is inside it
STRIP_ROWS = 1 << 16  # rows of a strip; fixed, so the result does not depend on the number of threads
_CROSSINGS_SHARE = 0.3  # of the work on a wireframe when the nearest triangles are searched for as well
_AREA_SHARE = 0.25  # of the whole work: the second pass, of r_n
_CHUNK = 1 << 18  # rows turned to the upper side at a time
_SRC = np.array([orient.SRC_NONE, orient.SRC_MESH_CROSSING, orient.SRC_MESH_NEAREST], dtype=np.uint8)  # of a `way`


class Targets(Protocol):
    """Wireframes that can be gone through more than once: a list, or a reader that builds them as asked."""

    def __len__(self) -> int: ...

    def __iter__(self) -> Iterator[MeshTarget]: ...


@dataclass(frozen=True, eq=False)
class MeshOrientation:
    """The planes of intervals by wireframes — see the module docstring."""

    normal: np.ndarray  # float64 [n, 3]: unit, n_z ≥ 0; NaN — not found
    way: np.ndarray  # uint8 [n]: WAY_NONE | WAY_CROSSING | WAY_NEAREST
    count: np.ndarray  # int32 [n]: crossings inside the interval, of all the wireframes
    distance: np.ndarray  # float64 [n]: WAY_NEAREST — from the middle of the interval to the wireframes, m
    journal: list[str]  # lines for the job log
    #: wireframes, triangles, crossings (of the holes asked about, anywhere along them); rows: asked — those
    #: that could be answered, crossing, nearest, none, cancelled — with crossings whose normals add up to
    #: nothing, no_path — asked about, with depths, but of a hole without a trajectory.
    report: dict[str, int]


# --- kernels -------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _turned(xyz, tri, rows, side, out):  # type: ignore[no-untyped-def]
    """out[k] = −side[k] · the unit normal (corner1 − corner0) × (corner2 − corner0) of the triangle rows[k] of
    the tree: the normal turned against the hole that crosses it. NaN — a triangle without area."""
    for k in range(rows.shape[0]):
        i = rows[k]
        i0, i1, i2 = tri[i, 0], tri[i, 1], tri[i, 2]
        e1x, e1y, e1z = xyz[i1, 0] - xyz[i0, 0], xyz[i1, 1] - xyz[i0, 1], xyz[i1, 2] - xyz[i0, 2]
        e2x, e2y, e2z = xyz[i2, 0] - xyz[i0, 0], xyz[i2, 1] - xyz[i0, 1], xyz[i2, 2] - xyz[i0, 2]
        nx = e1y * e2z - e1z * e2y
        ny = e1z * e2x - e1x * e2z
        nz = e1x * e2y - e1y * e2x
        length = np.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 0.0:
            s = -1.0 if side[k] > 0 else 1.0
            out[k, 0] = s * (nx / length)
            out[k, 1] = s * (ny / length)
            out[k, 2] = s * (nz / length)
        else:
            out[k, 0] = out[k, 1] = out[k, 2] = np.nan


@njit(cache=True, nogil=True)
def _gather(first, depth, turned, hole, frm, to, ask, tol, r0, r1, sums, count):  # type: ignore[no-untyped-def]
    """The rows r0 ≤ r < r1: the turned normals of the crossings inside the interval are added to sums[r], their
    number to count[r]. The crossings of the hole h are first[h] : first[h + 1], by depth."""
    for r in range(r0, r1):
        if not ask[r]:
            continue
        h = hole[r]
        lo = first[h]
        end = first[h + 1]
        if lo == end:
            continue
        a = frm[r] - tol
        b = to[r] + tol
        hi = end
        while lo < hi:  # the first crossing at a or deeper
            mid = (lo + hi) >> 1
            if depth[mid] < a:
                lo = mid + 1
            else:
                hi = mid
        sx, sy, sz, c = sums[r, 0], sums[r, 1], sums[r, 2], count[r]
        while lo < end and depth[lo] <= b:
            x = turned[lo, 0]
            if x == x:
                sx += x
                sy += turned[lo, 1]
                sz += turned[lo, 2]
                c += 1
            lo += 1
        sums[r, 0], sums[r, 1], sums[r, 2], count[r] = sx, sy, sz, c


# --- steps ---------------------------------------------------------------------------------------------------


def _plain(values: Any, dtype: Any) -> np.ndarray:
    """A C-contiguous writable array of the dtype (one numba specialization of every kernel)."""
    return np.require(values, dtype=dtype, requirements=["C", "W"])


def _span(tick: Tick | None, lo: float, hi: float) -> Tick | None:
    return None if tick is None else (lambda fraction: tick(lo + (hi - lo) * min(max(fraction, 0.0), 1.0)))


def _count(x: int) -> str:
    return f"{int(x):,}".replace(",", " ")


def _asked(traj: Trajectories, h: np.ndarray, f: np.ndarray, t: np.ndarray, todo: Any) -> tuple[np.ndarray, int]:
    """(bool [n]: the interval is asked about and can be answered — a hole with a trajectory, both depths,
    TO > FROM; how many are asked about with depths but of a hole without a trajectory)."""
    if h.ndim != 1 or f.shape != h.shape or t.shape != h.shape:
        raise ValueError("the hole and the depths of the intervals must be one-dimensional and of one length")
    with np.errstate(invalid="ignore"):
        ask = np.isfinite(f) & np.isfinite(t) & (t > f)
    if todo is not None:
        mask = np.asarray(todo)
        if mask.dtype != np.bool_ or mask.shape != h.shape:
            raise ValueError(f"todo must be a bool array of {h.shape[0]} intervals, got {mask.dtype} {mask.shape}")
        ask &= mask
    offsets = np.asarray(traj.offsets)
    built = np.append(offsets[1:] > offsets[:-1], False)  # the last one — of a hole the set does not have
    path = built[np.where((h >= 0) & (h < traj.n_holes), h, traj.n_holes)]
    return ask & path, int(np.count_nonzero(ask & ~path))


def _tree_rows(target: MeshTarget, triangle: np.ndarray) -> np.ndarray:
    """The rows of `target.tri` of the triangles given by their numbers in `tri` as it came."""
    if target.order is None:
        return np.ascontiguousarray(triangle, dtype=np.int64)
    wanted = np.unique(triangle)
    mark = np.zeros(target.triangles, dtype=np.bool_)
    mark[wanted] = True
    at = np.flatnonzero(mark[target.order])
    place = np.empty(len(wanted), dtype=np.int64)
    place[np.searchsorted(wanted, target.order[at])] = at
    return place[np.searchsorted(wanted, triangle)]  # type: ignore[no-any-return]


def _add_crossings(traj: Trajectories, target: MeshTarget, holes: np.ndarray, h: np.ndarray, f: np.ndarray,
                   t: np.ndarray, ask: np.ndarray, sums: np.ndarray, count: np.ndarray, threads: int,
                   tick: Tick | None) -> int:  # fmt: skip
    """Step 1 for one wireframe; returns the number of its crossings with the holes."""
    passed = None if tick is None else (lambda fraction, _text: None if fraction is None else tick(fraction))
    c = hole_crossings(traj, [target], holes=holes, tick=passed)
    if len(c):
        turned = np.empty((len(c), 3), dtype=np.float64)
        _turned(target.xyz, target.tri, _tree_rows(target, c.triangle), _plain(c.side, np.int8), turned)
        first = np.searchsorted(c.hole, np.arange(traj.n_holes + 1)).astype(np.int64)
        depth = _plain(c.depth, np.float64)
        strips.run(lambda a, b: _gather(first, depth, turned, h, f, t, ask, DEPTH_TOL_M, a, b, sums, count),
                   h.shape[0], threads=threads, size=STRIP_ROWS)  # fmt: skip
    return len(c)


def _upper(vectors: np.ndarray) -> np.ndarray:
    """`formulas.upper_normal` of many rows, a portion at a time."""
    out = np.empty_like(vectors)
    for a in range(0, len(vectors), _CHUNK):
        out[a : a + _CHUNK] = upper_normal(vectors[a : a + _CHUNK])
    return out


def _journal(report: dict[str, int], d_max: float) -> list[str]:
    lines = []
    if report["cancelled"]:
        lines.append(f"Каркас: интервалов, где нормали пересечений в сумме не дают направления (ствол пересекает "
                     f"поверхность туда и обратно), — {_count(report['cancelled'])}: для них взята ближайшая точка "
                     f"каркаса, если она не дальше d_max.")  # fmt: skip
    if report["none"]:
        why = (f"ближайшая точка каркаса дальше d_max = {d_max:g} м".replace(".", ",") if d_max > 0.0
               else "d_max = 0 — ближайшая точка каркаса не ищется")  # fmt: skip
        lines.append(f"Каркас: интервалов без ориентировки — {_count(report['none'])} (ствол не пересекает каркас "
                     f"внутри интервала, {why}).")  # fmt: skip
    return lines


# --- the orientation -----------------------------------------------------------------------------------------


def mesh_orientation(
    traj: Trajectories,
    hole: Any,
    frm: Any,
    to: Any,
    targets: Targets,
    *,
    d_max: float = D_MAX_DEFAULT,
    r_n: float = R_N_DEFAULT,
    todo: Any = None,
    threads: int = 1,
    tick: Tick | None = None,
    stage: Callable[[str], None] | None = None,
) -> MeshOrientation:
    """The plane of every interval [frm; to] of the holes `hole` of `traj` by the wireframes `targets`: the
    crossings inside the interval, else the nearest triangle within `d_max` metres of its middle, averaged by
    area within `r_n` metres (module docstring). `d_max`, `r_n` outside the ranges of ТЗ 8.5.4 —
    CompositeError `bad_param`."""
    d_max = check_range(d_max, *D_MAX_RANGE, "Наибольшее расстояние до каркаса d_max", "d_max", " м")
    r_n = check_range(r_n, *R_N_RANGE, "Радиус осреднения нормали r_n", "r_n", " м")
    raw = np.asarray(hole)
    if raw.size and raw.dtype.kind not in "iu":
        raise TypeError(f"hole must be an integer array of hole indices, got {raw.dtype}")
    h, f, t = _plain(raw, np.int64), _plain(frm, np.float64), _plain(to, np.float64)
    ask, no_path = _asked(traj, h, f, t, todo)
    n = h.shape[0]
    rows = np.flatnonzero(ask)
    normal = np.zeros((n, 3), dtype=np.float64)  # the sums of the crossings first
    count = np.zeros(n, dtype=np.int32)
    way = np.zeros(n, dtype=np.uint8)
    distance = np.full(n, np.nan, dtype=np.float64)
    report = {"wireframes": 0, "triangles": 0, "crossings": 0, "asked": int(rows.shape[0]), "crossing": 0,
              "nearest": 0, "none": 0, "cancelled": 0, "no_path": no_path}  # fmt: skip
    if rows.shape[0] == 0:  # nobody to ask about: the wireframes are not even read
        normal.fill(np.nan)
        return MeshOrientation(normal, way, count, distance, [], report)

    def say(step: str, k: int) -> None:
        if stage is not None:
            stage(f"{step}: каркас {k + 1} из {max(total, k + 1)}")

    def share(k: int, lo: float, hi: float) -> tuple[float, float]:
        """The part of the progress lo … hi of the wireframe k."""
        width = (hi - lo) / max(total, k + 1)
        return lo + width * k, lo + width * (k + 1)

    if not hasattr(targets, "__len__"):
        raise TypeError("targets must be a list of MeshTarget or a reader with len() that can be gone through again")
    total = len(targets)
    holes = np.unique(h[rows])
    points = traj.positions(h[rows], 0.5 * (f[rows] + t[rows])) if d_max > 0.0 else None
    first_pass = 1.0 - _AREA_SHARE if r_n > 0.0 and points is not None else 1.0
    near: Nearest | None = None
    k = 0
    for target in targets:  # steps 1 and 2, a wireframe at a time
        if not isinstance(target, MeshTarget):
            raise TypeError(f"targets must give MeshTarget, got {type(target).__name__}")
        lo, hi = share(k, 0.0, first_pass)
        mid = hi if points is None else lo + _CROSSINGS_SHARE * (hi - lo)
        say("Пересечения стволов", k)
        report["crossings"] += _add_crossings(traj, target, holes, h, f, t, ask, normal, count, threads,
                                              _span(tick, lo, mid))  # fmt: skip
        if points is not None:
            say("Ближайшие треугольники", k)
            near = nearest_triangles(target, points, d_max, into=near, mesh=k, threads=threads,
                                     tick=_span(tick, mid, hi))  # fmt: skip
        report["triangles"] += int(target.triangles)
        k += 1
        del target  # before the next one is asked for
        if tick is not None:
            tick(hi)
    report["wireframes"] = k

    crossed = np.flatnonzero(count > 0)
    sums = normal[crossed]
    long = np.sqrt((sums * sums).sum(axis=1)) >= NORMAL_MIN  # NaN — not a direction either
    by_crossing, sums = crossed[long], _upper(sums[long])
    report["crossing"], report["cancelled"] = int(by_crossing.shape[0]), int(crossed.shape[0] - by_crossing.shape[0])
    normal.fill(np.nan)
    normal[by_crossing] = sums
    way[by_crossing] = WAY_CROSSING
    if near is not None:
        way[rows[near.triangle >= 0]] = WAY_NEAREST
        way[by_crossing] = WAY_CROSSING
        found = np.flatnonzero(way[rows] == WAY_NEAREST)  # among `rows`
        vectors = near.normal[found]
        if r_n > 0.0 and found.shape[0]:  # step 3: once more over the wireframes
            point = near.point[found]
            area = np.zeros((found.shape[0], 3), dtype=np.float64)
            k = 0
            for target in targets:
                say("Осреднение нормалей", k)
                lo, hi = share(k, first_pass, 1.0)
                area += area_normals(target, point, vectors, r_n, threads=threads, tick=_span(tick, lo, hi))
                k += 1
                del target
            vectors = mean_normals(area, vectors)
        normal[rows[found]] = _upper(vectors)
        distance[rows[found]] = np.sqrt(near.d2[found])
        report["nearest"] = int(found.shape[0])
    report["none"] = report["asked"] - report["crossing"] - report["nearest"]
    if tick is not None:
        tick(1.0)
    return MeshOrientation(normal, way, count, distance, _journal(report, d_max), report)


# --- the source of the chain ---------------------------------------------------------------------------------


def mesh_source(
    read: Callable[[Any], Targets | None], *, stage: Callable[[str], None] | None = None
) -> orient.MeshSource:
    """The source «каркас или поверхность» of the chain of `orient.resolve` — a function of the protocol
    `orient.MeshSource` for `orient.Inputs(mesh=…)` and `tt.of_table(…, mesh=…)`. `read(source)` gives the
    wireframes of the source (`spec.Source` of the kind mesh: `meshes`, `d_max`, `r_n`) as the `targets` of
    `mesh_orientation`, or None when they cannot be taken (the preview of a large wireframe): the chain goes on
    without the source and names it. Only the rows of `todo` are computed; `todo` is not changed."""

    def give(rows: orient.Intervals, traj: Trajectories, source: Any, *, todo: np.ndarray, threads: int = 1,
             tick: Tick | None = None) -> orient.Given | None:  # fmt: skip
        targets = read(source)
        if targets is None:
            return None
        o = mesh_orientation(traj, rows.dense_rows(), rows.frm, rows.to, targets, d_max=source.d_max, r_n=source.r_n,
                             todo=todo, threads=threads, tick=tick, stage=stage)  # fmt: skip
        return orient.Given(o.normal, _SRC[o.way], journal=o.journal, report=dict(o.report))

    return give


class StoredTargets:
    """The wireframes of a project one at a time: `meshes` — (`viz.meshes.MeshState`, stats) by priority, as
    `spec.Source.meshes` holds them — through the cache of `wireframes.exact` (the triangles in the order of
    the store, the tree of boxes built once). `keep=False` — a wireframe leaves the cache once read (a job on
    wireframes larger than the cache); the second pass of r_n then reads it again."""

    def __init__(self, project: Any, meshes: Any, *, keep: bool = True) -> None:
        self.project, self.meshes, self.keep = project, list(meshes), keep

    def __len__(self) -> int:
        return len(self.meshes)

    def __iter__(self) -> Iterator[MeshTarget]:
        from bf_core.wireframes import exact

        for state, stats in self.meshes:
            g = exact.geometry(self.project, state)
            target = g.target(stats)
            if not self.keep:
                exact.drop(g.key)
            del g
            yield target
            del target


def project_source(project: Any, *, keep: bool = True, max_triangles: int | None = None,
                   stage: Callable[[str], None] | None = None) -> orient.MeshSource:  # fmt: skip
    """`mesh_source` on the wireframes of `project`. `max_triangles` — the most triangles of a list the source
    answers for (the preview of one hole: 2 million, ADR 0021 §5); a larger list — None, «не считается»."""

    def read(source: Any) -> Targets | None:
        if max_triangles is not None and sum(int(state.triangles) for state, _stats in source.meshes) > max_triangles:
            return None
        return StoredTargets(project, source.meshes, keep=keep)

    return mesh_source(read, stage=stage)


def warm() -> None:
    """Compiles the kernels with the arrays of the job and of the preview — a wireframe in memory and one
    mapped from its blobs, read-only (into NUMBA_CACHE_DIR)."""
    from bf_core.drillholes.desurvey import Collars, desurvey

    xyz = np.array([[0.0, 0.0, -5.0], [10.0, 0.0, -5.0], [0.0, 10.0, -5.0], [10.0, 10.0, -6.0], [0.0, 0.0, -9.0],
                    [10.0, 0.0, -9.0], [0.0, 10.0, -9.0]])  # fmt: skip
    tri = np.array([[0, 1, 2], [1, 3, 2], [4, 6, 5]], dtype=np.int32)
    traj = desurvey(Collars([0], [2.0], [2.0], [0.0], [20.0]))
    hole = np.zeros(5, dtype=np.int64)
    frm = np.arange(0.0, 20.0, 4.0)
    for fixed in (False, True):
        v, t = xyz.copy(), tri.copy()
        v.setflags(write=not fixed)
        t.setflags(write=not fixed)
        meshes = [MeshTarget(v, t, ordered=True), MeshTarget(v, t[::-1])]
        mesh_orientation(traj, hole, frm, frm + 4.0, meshes, d_max=50.0, r_n=5.0)
