"""Checks of a wireframe that need more than counting (ТЗ 7.21; ADR 0013 §1, §3, §4).

    orientation_conflicts(tri, n)     edges walked the same way by two triangles (flipped normals)
    self_intersections(xyz, tri)      pairs of triangles that cross, the triangles that take part, points
    mesh_checks(xyz, tri)             every check in one dictionary (the measures of `meshes.mesh_stats` too)
    run_checks(xyz, tri)              what the job `wireframe.check` stores: `stats` for
                                      `wireframes.stats.checks` and the triangles of the red layer

What is checked when a wireframe is loaded (`meshes.mesh_stats`): area, volume of a closed solid, closedness,
orientation, open and non-manifold edges, degenerate triangles. What the job adds: the consistency of the
normals and the self-intersections.

Self-intersections. A pair is two triangles **without a common vertex** one of whose edges pierces the other
(the test of BF, `checks_kernels.crossing`: float64 relative to the centre of the mesh, tolerance 1e-9 of the
edge and of the triangle). Not found, and said so in «Свойства»: triangles that overlap in one plane (no edge
pierces), and neighbours folded over each other (they share a vertex). The triangles are put on a grid of cells
about twice the size of a typical triangle: each is listed in the cells its box covers, the pairs of a cell are
tested, and a pair is counted only in the cell that holds the lower corner of the overlap of the two boxes — so
once. A triangle that would cover more than 512 cells (`MAX_CELLS`; fewer when the listing would take too
much memory) is listed on a coarser grid (cells `FACTOR` times larger, and so on): a mesh of mixed sizes costs
about what its boxes overlap (BF tested such triangles against every other one). The result does not depend on
the grid: every pair of overlapping boxes is tested.

Memory: about 24 bytes a triangle (boxes), 12 bytes a listed cell (2–4 a triangle; at most `MAX_LISTED` on
average, or 8 each) and the vertices once more (centred). Time is linear in the triangles unless many of them
share one place: then the pairs of that place are all looked at — the scan reports progress and can be
cancelled all the way (`progress(fraction)` may raise). One thread. Measured: 1 million triangles — 0.6 s in
the order of the store (0.9 s shuffled) and about 100 MB; 10 million — 6.2 s and 490 MB.

Ported from BF `geo/checks.py` (`orientation_conflicts`, `self_intersections`) and `geo/repair.py`
(`mesh_checks`, `self_intersecting_triangles`). Differences: the levels of the grid, float32 boxes, no threads,
progress and cancellation, the list of triangles (ADR 0013 §4: at most 200 000) and points that lie on the
crossings (BF gave the middles between the centres of the two triangles, the first 200 found; here the pairs
are taken evenly over the whole mesh).

numba is imported on the first call, not with this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.geo.meshes import MeshError, check_mesh, mesh_stats

MAX_TRIANGLES = 200_000  # triangles listed for the red layer (ADR 0013 §4)
SAMPLES = 200  # points of crossings kept (ADR 0013 §3)
EPS = 1e-9  # of BF: relative to the edge and to the triangle
MAX_CELLS = (512, 64, 27, 8)  # cells a triangle may cover on its grid: the first that keeps the listing small
MAX_LISTED = 6  # cells listed per triangle on average (a typical mesh has 2–4); 8 a triangle always fit
FACTOR = 4.0  # the cells of the next level are this many times larger
MIN_CELL = 1e-9
MAX_GRID = 2**20  # cells along an axis of the finest grid
BUDGET = 20_000_000  # pairs looked at between two calls of `progress` (about 0.1 s)
MAX_SPAN_M = 1e15  # a mesh wider than this has no coordinates to speak of

Progress = Callable[[float], None] | None


@dataclass(frozen=True)
class SelfIntersections:
    pairs: int  # pairs of triangles that cross
    triangle_count: int  # triangles that cross another one
    triangles: np.ndarray  # their numbers, ascending, int32: at most `limit` of them
    points: np.ndarray  # float64 k×3, k ≤ `samples`: points of crossings, spread over the mesh
    big_triangles: int = 0  # triangles listed on a coarser grid

    def as_dict(self) -> dict[str, Any]:
        """JSON values: {pairs, triangles — how many cross, listed — how many of them are in the list, points}."""
        return {
            "pairs": self.pairs,
            "triangles": self.triangle_count,
            "listed": int(len(self.triangles)),
            "points": self.points.tolist(),
        }


@dataclass(frozen=True)
class Checks:
    stats: dict[str, Any]  # `wireframes.stats.checks`
    triangles: np.ndarray  # the triangles of the red layer (`cache/mesh/<key>/checks.bfa`)


def _tick(progress: Progress, value: float) -> None:
    if progress:
        progress(float(min(max(value, 0.0), 1.0)))


def _within(progress: Progress, start: float, stop: float) -> Progress:
    if progress is None:
        return None
    return lambda fraction: progress(start + (stop - start) * min(max(float(fraction), 0.0), 1.0))


def orientation_conflicts(tri: Any, vertex_count: int) -> int:
    """Directed edges used more than once: neighbouring triangles wound opposite ways (their normals look to
    different sides of the surface). 0 — the normals are consistent. 24 bytes a triangle, sorted in place."""
    t = np.asarray(tri)
    m = len(t)
    if m == 0:
        return 0
    n = max(int(vertex_count), int(t.max()) + 1)
    keys = np.empty(3 * m, dtype=np.int64)
    for k, (i, j) in enumerate(((0, 1), (1, 2), (2, 0))):
        part = keys[k * m : (k + 1) * m]
        part[:] = t[:, i]
        part *= n
        part += t[:, j]
    keys.sort()
    return int(np.count_nonzero(keys[1:] == keys[:-1]))


def _nothing() -> SelfIntersections:
    return SelfIntersections(0, 0, np.zeros(0, dtype=np.int32), np.zeros((0, 3)))


def self_intersections(
    xyz: Any, tri: Any, *, limit: int = MAX_TRIANGLES, samples: int = SAMPLES, progress: Progress = None
) -> SelfIntersections:
    """The self-intersections of a mesh (see the module docstring). `MeshError` — the mesh is not one
    (`meshes.check_mesh`). `progress(fraction)` is called at least every `BUDGET` pairs and may raise."""
    if len(np.asarray(tri)) == 0:
        return _nothing()
    v, t = check_mesh(xyz, tri)
    m = len(t)
    if m < 2:
        _tick(progress, 1.0)
        return _nothing()
    from bf_core.geo import checks_kernels as kernels

    low, high = v.min(axis=0), v.max(axis=0)
    if not float((high - low).max()) < MAX_SPAN_M:
        raise MeshError(
            "bad_coordinates",
            "Координаты каркаса слишком велики для проверки самопересечений.",
            "Проверьте исходные данные: размер каркаса больше 10¹⁵ м.",
        )
    centre = (low + high) / 2.0
    vc = np.ascontiguousarray(v - centre)
    _tick(progress, 0.0)
    box = np.empty((m, 6), dtype=np.float32)
    whole = kernels.boxes(vc, t, box)
    gmin = np.ascontiguousarray(whole[:3])
    extent = np.empty(m, dtype=np.float32)
    kernels.extents(box, extent)
    span = float((whole[3:] - whole[:3]).max())
    cell = max(float(np.median(extent)) * 2.0, span / MAX_GRID, MIN_CELL)
    del extent
    sizes = [cell]
    while sizes[-1] < span:  # the coarsest grid: a triangle covers at most 2 cells along an axis
        sizes.append(sizes[-1] * FACTOR)
    size = 1.0 / np.array(sizes, dtype=np.float64)  # the kernels multiply
    dims = np.empty((len(size), 3), dtype=np.int64)
    kernels.grid_dims(whole, size, dims)
    cells = [int(d[0]) * int(d[1]) * int(d[2]) for d in dims]
    offs = np.zeros(len(size) + 1, dtype=np.int64)
    offs[1:] = np.cumsum(cells)  # below 2⁶¹: the finest grid has at most (2²⁰ + 1)³ cells
    level = np.empty(m, dtype=np.int8)
    count = np.empty(m, dtype=np.int64)
    for most in MAX_CELLS:  # the finest placing of the larger triangles that the memory allows
        per_level = kernels.assign_levels(box, gmin, size, most, level, count)
        total = int(count.sum())
        if total <= MAX_LISTED * m:
            break
    starts = np.zeros(m, dtype=np.int64)
    np.cumsum(count[:-1], out=starts[1:])
    del count
    _tick(progress, 0.05)
    keys = np.empty(total, dtype=np.int64)
    ids = np.empty(total, dtype=np.int32)
    kernels.fill(box, gmin, size, dims, offs, level, starts, keys, ids)
    del starts
    _tick(progress, 0.1)
    if int(offs[-1]) * m < 2**62:  # cell and triangle fit one number: sorted in place, triangles ascending
        kernels.combine(keys, ids, m)
        keys.sort()
        kernels.split(keys, ids, m)
    else:
        order = np.argsort(keys, kind="stable")
        keys, ids = keys[order], ids[order]
        del order
    runs = np.concatenate([[0], np.flatnonzero(keys[1:] != keys[:-1]) + 1, [total]]).astype(np.int64)
    ukeys = np.ascontiguousarray(keys[runs[:-1]])
    del keys
    level_runs = np.searchsorted(ukeys, offs).astype(np.int64)
    _tick(progress, 0.2)

    mark = np.zeros(m, dtype=np.uint8)
    sample = np.empty((max(int(samples), 0), 2), dtype=np.int64)
    counters = np.zeros(5, dtype=np.int64)
    counters[kernels.STRIDE] = 1
    lengths = np.diff(runs).astype(np.float64)
    work = max(float((lengths * (lengths + 1.0) / 2.0).sum()), 1.0)  # what `scan_runs` counts, run by run
    del lengths
    several = int(np.count_nonzero(per_level)) > 1
    share = 0.75 if several else 0.8
    state = np.zeros(2, dtype=np.int64)
    done = 0.0
    while state[0] < len(ukeys):
        done += kernels.scan_runs(
            vc, t, box, gmin, size, dims, offs, level, ukeys, ids, runs, state, BUDGET, EPS, mark, sample, counters
        )
        _tick(progress, 0.2 + share * min(done / work, 1.0))
    if several:  # triangles of different sizes: the finer against the cells of the coarser
        state = np.zeros(1, dtype=np.int64)
        while state[0] < m:
            kernels.scan_levels(
                vc, t, box, gmin, size, dims, offs, level, ukeys, ids, runs, level_runs, state, BUDGET, EPS, mark,
                sample, counters,
            )  # fmt: skip
            _tick(progress, 0.95 + 0.05 * float(state[0]) / m)
    kept = int(counters[kernels.KEPT])
    points = np.empty((kept, 3), dtype=np.float64)
    found = int(kernels.crossings(vc, t, sample[:kept], EPS, points))
    listed = np.flatnonzero(mark)[: max(int(limit), 0)].astype(np.int32)
    _tick(progress, 1.0)
    return SelfIntersections(
        pairs=int(counters[kernels.PAIRS]),
        triangle_count=int(counters[kernels.MARKED]),
        triangles=listed,
        points=points[:found] + centre,
        big_triangles=int(m - per_level[0]),
    )


def mesh_checks(xyz: Any, tri: Any, *, intersections: bool = True, progress: Progress = None) -> dict[str, Any]:
    """Every check of a wireframe in one dictionary: `triangles`, `vertices`, the measures of
    `meshes.mesh_stats` (area, plan_area, volume and orientation of a closed solid, closed, open_edges,
    nonmanifold_edges, degenerate), `orientation_conflicts` and `self_intersections` — the number of crossing
    pairs, None when `intersections` is off (it is the slowest check)."""
    if len(np.asarray(tri)) == 0:
        return {
            "triangles": 0, "vertices": int(len(np.asarray(xyz))), **mesh_stats(np.zeros((0, 3)), np.zeros((0, 3))),
            "orientation": None, "nonmanifold_edges": 0, "orientation_conflicts": 0,
            "self_intersections": 0 if intersections else None,
        }  # fmt: skip
    v, t = check_mesh(xyz, tri)
    out: dict[str, Any] = {"triangles": int(len(t)), "vertices": int(len(v))}
    out.update(mesh_stats(v, t, _within(progress, 0.0, 0.15)))
    out["orientation_conflicts"] = orientation_conflicts(t, len(v))
    _tick(progress, 0.2)
    out["self_intersections"] = None
    if intersections:
        out["self_intersections"] = self_intersections(
            v, t, limit=0, samples=0, progress=_within(progress, 0.2, 1.0)
        ).pairs
    _tick(progress, 1.0)
    return out


def run_checks(xyz: Any, tri: Any, progress: Progress = None) -> Checks:
    """The checks of the job `wireframe.check` (ADR 0013 §3): the normals, then the self-intersections.
    `stats` — {state, normal_conflicts, pairs, triangles (how many cross), listed (how many of them are in
    `Checks.triangles`: at most `MAX_TRIANGLES`), points (at most `SAMPLES`, project coordinates), seconds}."""
    started = time.perf_counter()
    t = np.asarray(tri)
    conflicts = orientation_conflicts(t, len(np.asarray(xyz)))
    _tick(progress, 0.05)
    found = self_intersections(xyz, t, progress=_within(progress, 0.05, 1.0))
    stats = {"state": "done", "normal_conflicts": conflicts, **found.as_dict()}
    stats["seconds"] = round(time.perf_counter() - started, 3)
    _tick(progress, 1.0)
    return Checks(stats, found.triangles)
