"""Where drillholes cross wireframes: the marks on the hole traces and the intervals inside solids (ТЗ 7.15;
ADR 0007 §5 «сгущённая дуга, затем уточнение по positions() до 1 мм»; ADR 0013 §4).

Ported from BF `geo/intersect.py::MeshTarget`. BF tested every 2 m chord of a hole against every triangle near
the hole (a mask over the whole mesh for each hole) and took the depth from the chord; here

  * the mesh gets a tree of boxes once (`tri_kernels.bvh_build`: runs of consecutive triangles in spatial
    order), so a piece of a hole reads a few dozen boxes and a few triangles;
  * a course between two stations is replaced by straight pieces that stay within `SAGITTA_M` (1 mm) of it: an
    arc of minimum curvature by as many as its bend asks for, a course of the balanced tangent method by its
    two halves, a straight course by itself. An arc far from the mesh (by the boxes, grown by the bend of the
    arc) is not cut into pieces at all;
  * a piece crosses a triangle when its line passes through it (`line_triangle`: nothing slips between two
    triangles) and its ends are on different sides of the plane of the triangle, an end on the plane counting
    as above it — so a station lying on a surface gives one crossing, not two or none;
  * the depth is then moved from the chord to the course itself: the depth at which `Trajectories.positions`
    is on the plane of the triangle (a few steps down the slope of the chord, to `DEPTH_TOL_M`), and the point of the
    mark is `positions(hole, depth)` — on the trace as it is drawn, bit for bit;
  * crossings of one hole with one wireframe nearer than `SAME_HIT_M` along the chord are one crossing (the
    hole went through an edge or a vertex shared by several triangles); its side is the sign of the sum: a
    hole that only touches a crease of a solid goes into one face and out of the other at the same point —
    side 0, event CROSS, and the solid is not entered.

Events (ADR 0007: `event` of a mark). A wireframe said to be a closed solid (`solid="outward"` or "inward":
`wireframes.stats` closed and orientation) gives ENTER and LEAVE by the side of the triangle the hole comes
from; any other wireframe gives CROSS. `inside_intervals` turns them into the from–to intervals inside the
solids: a hole that leaves a solid it never entered started inside it (from the collar), one that does not
leave ends inside (to the end of the hole). A hole wholly inside a solid has no crossing to tell it by and gets
no interval.

Accuracy: the depth of a crossing is that of the exact path to about 1e-8 m (the acceptance of 7.15 is 1 mm).
What the 1 mm of the pieces can change is only whether a hole that grazes a surface, or passes within 1 mm of
its border, is said to cross it.

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.drillholes.desurvey import (
    _STRAIGHT,
    BALANCED_TANGENT,
    METHODS,
    MINIMUM_CURVATURE,
    Trajectories,
    _delta,
)
from bf_core.jit import njit

from . import meshes
from .tri_kernels import BOX_PAD_M, bvh_build, leaf_hits, mesh_arrays, plane_sides, segment_leaves

# progress(fraction 0…1 or None, message or None); it may raise (JobCancelled) to cancel the calculation.
Tick = Callable[[float | None, str | None], None]

SAGITTA_M = 1e-3  # the straight pieces of a course stay this near to it
SAME_HIT_M = 1e-6  # crossings of a hole with a wireframe nearer than this along the hole are one crossing
DEPTH_TOL_M = 1e-8  # the depth of a crossing on a curved course is refined until it moves less than this
CROSS, ENTER, LEAVE = 0, 1, 2  # `event` of a crossing (ADR 0007 §5): 1 and 2 — into and out of a closed solid
LEAF = 8  # triangles in a run of the tree
MAX_LEAVES = 1 << 20  # more triangles make longer runs, not a larger tree (96 MB of boxes at most)
HOLE_CHUNK = 1 << 12  # holes between two ticks
MAX_PIECES = 1 << 16  # of one course (a guard against a course of absurd length)


# --- kernels -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _pieces(method, seg, ax, ay, az, bx, by, bz, sagitta):  # type: ignore[no-untyped-def]
    """(number of straight pieces a course of `seg` metres between the directions a and b is replaced by, how
    far the course goes from its chord). Follows the branches of `desurvey._delta`."""
    if method == MINIMUM_CURVATURE:
        cos_b = ax * bx + ay * by + az * bz
        ux = ay * bz - az * by
        uy = az * bx - ax * bz
        uz = ax * by - ay * bx
        sin_b = math.sqrt(ux * ux + uy * uy + uz * uz)
        if sin_b >= _STRAIGHT:  # an arc of radius seg / β: its sagitta is at most seg · β / 8
            bend = seg * math.atan2(sin_b, cos_b) / 8.0
            return max(1, min(MAX_PIECES, int(math.ceil(math.sqrt(bend / sagitta))))), bend
        if cos_b > 0.0:
            return 1, 0.0
        return 2, 0.0  # the direction turns back: two straight halves
    if method == BALANCED_TANGENT:
        return 2, 0.0
    return 1, 0.0


@njit(cache=True, nogil=True)
def _course_point(method, s, depth, md, sxyz, dirs):  # type: ignore[no-untyped-def]
    """The point of the course s … s + 1 at a measured depth, as `Trajectories.positions` computes it."""
    dx, dy, dz = _delta(method, depth - md[s], md[s + 1] - md[s], dirs[s, 0], dirs[s, 1], dirs[s, 2],
                        dirs[s + 1, 0], dirs[s + 1, 1], dirs[s + 1, 2])  # fmt: skip
    return sxyz[s, 0] + dx, sxyz[s, 1] + dy, sxyz[s, 2] + dz


@njit(cache=True, nogil=True)
def _refine(method, s, md, sxyz, dirs, xyz, i0, i1, i2, da, db, slope, depth):  # type: ignore[no-untyped-def]
    """The depth within [da, db] at which the course itself, not its chord, is on the plane of the triangle.
    `slope` — the change of the height above the plane per metre of depth along the chord."""
    for _ in range(12):
        x, y, z = _course_point(method, s, depth, md, sxyz, dirs)
        g, _same = plane_sides(xyz, i0, i1, i2, x, y, z, x, y, z)
        nxt = min(max(depth - g / slope, da), db)
        moved = abs(nxt - depth)
        depth = nxt
        if moved < DEPTH_TOL_M:
            break
    return depth


@njit(cache=True, nogil=True)
def _runs_near(mesh, stack, near, ax, ay, az, bx, by, bz, extra):  # type: ignore[no-untyped-def]
    """`segment_leaves` into `near`, made longer when it is too short: (number of runs, near)."""
    xyz, tri, leaf, leaves, box, pad = mesh
    count = segment_leaves(xyz, tri, leaf, leaves, box, pad, stack, ax, ay, az, bx, by, bz, extra, near)
    while count < 0:
        near = np.empty(2 * near.shape[0], dtype=np.int64)
        count = segment_leaves(xyz, tri, leaf, leaves, box, pad, stack, ax, ay, az, bx, by, bz, extra, near)
    return count, near


@njit(cache=True, nogil=True)
def _piece_hits(mesh, near, count, check, ax, ay, az, bx, by, bz, hits):  # type: ignore[no-untyped-def]
    """`leaf_hits` into `hits` (t, triangle, sign), made longer when too short: (number of crossings, hits)."""
    xyz, tri, leaf, leaves, box, _pad = mesh
    h_t, h_tri, h_sign = hits
    found = leaf_hits(xyz, tri, leaf, leaves, box, near, count, check, ax, ay, az, bx, by, bz, h_t, h_tri, h_sign)
    while found < 0:
        room = 2 * h_t.shape[0]
        h_t, h_tri, h_sign = np.empty(room, np.float64), np.empty(room, np.int64), np.empty(room, np.int8)
        found = leaf_hits(xyz, tri, leaf, leaves, box, near, count, check, ax, ay, az, bx, by, bz, h_t, h_tri, h_sign)
    return found, (h_t, h_tri, h_sign)


@njit(cache=True, nogil=True)
def _walk(path, holes, j0, j1, mesh, sagitta, out):  # type: ignore[no-untyped-def]
    """Crossings of the holes holes[j0:j1] of `path` (method, offsets, md, xyz, dirs of the trajectories) with
    `mesh` (xyz, tri, leaf, leaves, box, pad of a `MeshTarget`) into `out` (in no order within a hole): the
    hole, the depth along the chords, the depth along the path, the triangle, the sign. Returns (j, n): `n`
    crossings written, of the holes before `j`; j < j1 — `out` is full, go on from hole `j`."""
    method, offsets, md, sxyz, dirs = path
    xyz, tri = mesh[0], mesh[1]
    o_hole, o_chord, o_depth, o_tri, o_sign = out
    stack = np.empty(128, dtype=np.int64)
    near = np.empty(256, dtype=np.int64)  # the runs of triangles near a course
    hits = (np.empty(64, np.float64), np.empty(64, np.int64), np.empty(64, np.int8))
    n = 0
    for j in range(j0, j1):
        h = holes[j]
        start = n
        for s in range(offsets[h], offsets[h + 1] - 1):
            seg = md[s + 1] - md[s]
            if not seg > 0.0:
                continue
            ax, ay, az = sxyz[s, 0], sxyz[s, 1], sxyz[s, 2]
            ex, ey, ez = sxyz[s + 1, 0], sxyz[s + 1, 1], sxyz[s + 1, 2]
            if not math.isfinite(ax + ay + az + ex + ey + ez):
                continue
            pieces, bend = _pieces(method, seg, dirs[s, 0], dirs[s, 1], dirs[s, 2],
                                   dirs[s + 1, 0], dirs[s + 1, 1], dirs[s + 1, 2], sagitta)  # fmt: skip
            arc = bend > 0.0
            count = 0
            if arc:  # the runs of triangles near the arc, once for all its pieces; none — the arc is not cut
                count, near = _runs_near(mesh, stack, near, ax, ay, az, ex, ey, ez, bend)
                if count == 0:
                    continue
            da = md[s]
            for k in range(1, pieces + 1):
                bx, by, bz, db = ex, ey, ez, md[s + 1]
                if k < pieces:
                    db = md[s] + seg * k / pieces
                    bx, by, bz = _course_point(method, s, db, md, sxyz, dirs)
                found = 0
                if math.isfinite(bx + by + bz):
                    if not arc:
                        count, near = _runs_near(mesh, stack, near, ax, ay, az, bx, by, bz, 0.0)
                    found, hits = _piece_hits(mesh, near, count, arc, ax, ay, az, bx, by, bz, hits)
                for q in range(found):
                    if n == o_hole.shape[0]:
                        return j, start
                    i = hits[1][q]
                    chord = da + hits[0][q] * (db - da)
                    depth = chord
                    if arc:
                        ga, gb = plane_sides(xyz, tri[i, 0], tri[i, 1], tri[i, 2], ax, ay, az, bx, by, bz)
                        depth = _refine(method, s, md, sxyz, dirs, xyz, tri[i, 0], tri[i, 1], tri[i, 2],
                                        da, db, (gb - ga) / (db - da), chord)  # fmt: skip
                    o_hole[n] = h
                    o_chord[n] = chord
                    o_depth[n] = depth
                    o_tri[n] = i
                    o_sign[n] = hits[2][q]
                    n += 1
                ax, ay, az, da = bx, by, bz, db
    return j1, n


@njit(cache=True, nogil=True)
def _merge(hole, chord, depth, sign, tol, keep, side):  # type: ignore[no-untyped-def]
    """Crossings sorted by (hole, chord): those of a hole nearer than `tol` to the one before are one crossing
    — its first row into `keep`, the sign of the sum of their signs into `side`. The depths of a hole are made
    not to go back (the order of the crossings is that of the chords). Returns the number of crossings."""
    n = hole.shape[0]
    k = 0
    i = 0
    while i < n:
        total = np.int64(sign[i])
        j = i + 1
        while j < n and hole[j] == hole[i] and chord[j] - chord[j - 1] <= tol:
            total += sign[j]
            j += 1
        if k > 0 and hole[keep[k - 1]] == hole[i] and depth[i] < depth[keep[k - 1]]:
            depth[i] = depth[keep[k - 1]]
        keep[k] = i
        side[k] = 1 if total > 0 else (-1 if total < 0 else 0)
        k += 1
        i = j
    return k


@njit(cache=True, nogil=True)
def _spans(hole, mesh, event, owner, opened, closed):  # type: ignore[no-untyped-def]
    """Crossings sorted by (hole, mesh, depth) → the stretches inside the solids: for each, a crossing of its
    hole and mesh (`owner`), the crossing it starts at (−1: at the collar) and the one it ends at (−1: at the
    end of the hole). The level of a hole is how many shells it is inside of: it starts as low as it can
    without ever being negative. Returns the number of stretches."""
    n = hole.shape[0]
    k = 0
    i = 0
    while i < n:
        j = i
        level = 0
        lowest = 0
        while j < n and hole[j] == hole[i] and mesh[j] == mesh[i]:
            if event[j] == ENTER:
                level += 1
            elif event[j] == LEAVE:
                level -= 1
            if level < lowest:
                lowest = level
            j += 1
        level = -lowest
        since = -1
        for q in range(i, j):
            before = level
            if event[q] == ENTER:
                level += 1
            elif event[q] == LEAVE:
                level -= 1
            if before == 0 and level > 0:
                since = q
            elif before > 0 and level == 0:
                owner[k] = i
                opened[k] = since
                closed[k] = q
                k += 1
        if level > 0:
            owner[k] = i
            opened[k] = since
            closed[k] = -1
            k += 1
        i = j
    return k


# --- a wireframe ready for many holes ----------------------------------------------------------------------


class MeshTarget:
    """A wireframe ready for many holes: its triangles in spatial order under a tree of boxes.

    `solid` — "outward" or "inward" for a closed solid with its normals looking out or in (`wireframes.stats`:
    closed, orientation), None for any other wireframe: its crossings are not entries and exits. `ordered` —
    the triangles are in spatial order already (a stored wireframe): they are neither sorted nor copied.
    ValueError: a triangle of a vertex that is not there."""

    def __init__(self, xyz: Any, tri: Any, *, solid: str | None = None, ordered: bool = False) -> None:
        if solid not in (None, "outward", "inward"):
            raise ValueError(f"solid is 'outward', 'inward' or None, got {solid!r}")
        v, t = mesh_arrays(xyz, tri)
        self.solid = solid
        self.order: np.ndarray | None = None  # the triangle of the tree → its number as given
        if len(t) and not ordered:
            if int(t.min()) < 0 or int(t.max()) >= len(v):
                raise ValueError("a triangle refers to a vertex that does not exist")
            order = meshes.morton_order(v, t)
            if not np.array_equal(order, np.arange(len(t))):
                t = np.ascontiguousarray(t[order])
                self.order = order
        self.xyz, self.tri = v, t
        self.triangles = len(t)
        self.leaf = max(LEAF, math.ceil(len(t) / MAX_LEAVES))
        scale = max(abs(float(v.min())), abs(float(v.max()))) if len(v) else 0.0
        self.pad = BOX_PAD_M + 1e-12 * (scale if math.isfinite(scale) else 0.0)  # what the boxes are grown by
        self.leaves, self.box = bvh_build(v, t, self.leaf, self.pad)
        if self.leaves == 0:
            raise ValueError("a triangle refers to a vertex that does not exist")

    def kernel_args(self) -> tuple[Any, ...]:
        """(xyz, tri, leaf, leaves, box, pad) — `mesh` of the kernels."""
        return self.xyz, self.tri, self.leaf, self.leaves, self.box, self.pad

    @classmethod
    def from_stats(cls, xyz: Any, tri: Any, stats: Mapping[str, Any] | None, *, ordered: bool = False) -> MeshTarget:
        """With `solid` from the measures of the stored wireframe (`wireframes.stats`)."""
        orientation = (stats or {}).get("orientation") if (stats or {}).get("closed") else None
        return cls(xyz, tri, solid=orientation if orientation in ("outward", "inward") else None, ordered=ordered)


# --- crossings ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Crossings:
    """Crossings of holes with wireframes, sorted by hole, depth, wireframe."""

    hole: np.ndarray  # int64: the dense index of the hole in the trajectories
    mesh: np.ndarray  # int32: the index of the wireframe in the list given
    depth: np.ndarray  # float64: measured depth, metres
    xyz: np.ndarray  # float64 k×3: `positions(hole, depth)`
    triangle: np.ndarray  # int64: the triangle crossed, its number in `tri` as given
    side: np.ndarray  # int8: +1 the hole goes the way the normal of the triangle looks, −1 against it, 0 a touch
    event: np.ndarray  # uint8: CROSS, ENTER or LEAVE

    def __len__(self) -> int:
        return len(self.hole)


def _which(traj: Trajectories, holes: Any) -> np.ndarray:
    if holes is None:
        return np.arange(traj.n_holes, dtype=np.int64)
    which = np.unique(np.asarray(holes, dtype=np.int64).reshape(-1))
    if len(which) and (which[0] < 0 or which[-1] >= traj.n_holes):
        raise ValueError(f"hole index outside 0…{traj.n_holes - 1}")
    return which


def _events(side: np.ndarray, solid: str | None) -> np.ndarray:
    event = np.full(len(side), CROSS, dtype=np.uint8)
    if solid is not None:
        into = -1 if solid == "outward" else 1  # into a solid: against the normals that look out
        event[side == into] = ENTER
        event[side == -into] = LEAVE
    return event


def hole_crossings(
    traj: Trajectories,
    targets: Sequence[MeshTarget],
    *,
    holes: Any = None,
    tick: Tick | None = None,
) -> Crossings:
    """Where the holes of `traj` (all of them, or the dense indices `holes`) cross the wireframes `targets`.

    `tick` — progress and cancellation, called between portions of `HOLE_CHUNK` holes. Holes without a
    trajectory give nothing. The memory is that of the trees (13 MB for a million triangles) and of the
    crossings found; 50 000 holes against a million triangles take about 0.7 s on one thread."""
    which = _which(traj, holes)
    md, sxyz, dirs = (np.ascontiguousarray(a, dtype=np.float64) for a in (traj.md, traj.xyz, traj.dirs))
    path = (METHODS.index(traj.method), np.ascontiguousarray(traj.offsets, dtype=np.int64), md, sxyz, dirs)
    room = 4 * HOLE_CHUNK
    bufs = _buffers(room)
    found: list[tuple[np.ndarray, ...]] = []
    if tick is not None:
        tick(0.0, "Пересечения стволов с каркасами")
    for k, target in enumerate(targets):
        rows: list[tuple[np.ndarray, ...]] = []
        j = 0
        while j < len(which):
            nxt, n = _walk(path, which, j, min(len(which), j + HOLE_CHUNK), target.kernel_args(), SAGITTA_M, bufs)
            if n:
                rows.append(tuple(b[:n].copy() for b in bufs))
            if nxt == j:  # one hole alone has more crossings than the buffers hold
                room *= 4
                bufs = _buffers(room)
            j = nxt
            if tick is not None and j < len(which):
                tick((k + j / len(which)) / len(targets), f"Пересечения стволов с каркасами: каркас {k + 1}")
        if rows:
            found.append(_of_mesh(k, target, *(np.concatenate(col) for col in zip(*rows, strict=True))))
    if tick is not None:
        tick(1.0, "Пересечения стволов с каркасами")
    if not found:
        return _crossings(traj, *(np.zeros(0, dtype=t) for t in (np.int64, np.int32, np.float64, np.int64, np.int8,
                                                                 np.uint8)))  # fmt: skip
    hole, mesh, depth, triangle, side, event = (np.concatenate(col) for col in zip(*found, strict=True))
    order = np.lexsort((mesh, depth, hole))
    return _crossings(traj, hole[order], mesh[order], depth[order], triangle[order], side[order], event[order])


def _buffers(room: int) -> tuple[np.ndarray, ...]:
    return (np.empty(room, dtype=np.int64), np.empty(room, dtype=np.float64), np.empty(room, dtype=np.float64),
            np.empty(room, dtype=np.int64), np.empty(room, dtype=np.int8))  # fmt: skip


def _of_mesh(k: int, target: MeshTarget, hole: np.ndarray, chord: np.ndarray, depth: np.ndarray,
             triangle: np.ndarray, sign: np.ndarray) -> tuple[np.ndarray, ...]:  # fmt: skip
    """The crossings of one wireframe: the rows of `_walk` sorted, the repeated ones joined."""
    if target.order is not None:
        triangle = target.order[triangle]
    order = np.lexsort((triangle, chord, hole))
    hole, chord, depth, triangle, sign = (np.ascontiguousarray(a[order]) for a in (hole, chord, depth, triangle, sign))
    keep = np.empty(len(hole), dtype=np.int64)
    side = np.empty(len(hole), dtype=np.int8)
    n = _merge(hole, chord, depth, sign, SAME_HIT_M, keep, side)
    keep, side = keep[:n], side[:n]
    mesh = np.full(n, k, dtype=np.int32)
    return hole[keep], mesh, depth[keep], triangle[keep], side, _events(side, target.solid)


def _crossings(traj: Trajectories, hole: np.ndarray, mesh: np.ndarray, depth: np.ndarray, triangle: np.ndarray,
               side: np.ndarray, event: np.ndarray) -> Crossings:  # fmt: skip
    xyz = traj.positions(hole, depth) if len(hole) else np.zeros((0, 3), dtype=np.float64)
    return Crossings(hole, mesh, depth, xyz, triangle, side, event)


# --- intervals inside solids -------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Intervals:
    """Stretches of holes inside closed solids, sorted by hole, depth, wireframe."""

    hole: np.ndarray  # int64
    mesh: np.ndarray  # int32
    from_m: np.ndarray  # float64: 0 where the hole starts inside the solid
    to_m: np.ndarray  # float64: the length of the hole where it ends inside
    xyz_from: np.ndarray  # float64 k×3
    xyz_to: np.ndarray  # float64 k×3
    from_collar: np.ndarray  # bool: the hole is inside from its collar (no entry was found)
    to_end: np.ndarray  # bool: the hole is still inside at its end

    def __len__(self) -> int:
        return len(self.hole)


def inside_intervals(traj: Trajectories, crossings: Crossings) -> Intervals:
    """From–to of the stretches the holes spend inside the closed solids, from the ENTER and LEAVE events of
    `hole_crossings` (wireframes not said to be solids give none). Stretches of no length are left out."""
    n = len(crossings)
    order = np.lexsort((np.arange(n), crossings.mesh, crossings.hole))
    hole, mesh = np.ascontiguousarray(crossings.hole[order]), np.ascontiguousarray(crossings.mesh[order])
    depth = crossings.depth[order]
    owner, opened, closed = (np.empty(2 * n, dtype=np.int64) for _ in range(3))
    k = _spans(hole, mesh, np.ascontiguousarray(crossings.event[order]), owner, opened, closed)
    owner, opened, closed = owner[:k], opened[:k], closed[:k]
    span_hole, span_mesh = hole[owner], mesh[owner]
    from_m = np.where(opened >= 0, depth[np.maximum(opened, 0)], 0.0) if k else np.zeros(0)
    to_m = np.where(closed >= 0, depth[np.maximum(closed, 0)], traj.lengths()[span_hole]) if k else np.zeros(0)
    keep = to_m > from_m
    rank = np.lexsort((span_mesh[keep], from_m[keep], span_hole[keep]))
    span_hole, span_mesh, from_m, to_m = (a[keep][rank] for a in (span_hole, span_mesh, from_m, to_m))
    return Intervals(
        span_hole,
        span_mesh,
        from_m,
        to_m,
        traj.positions(span_hole, from_m) if len(span_hole) else np.zeros((0, 3)),
        traj.positions(span_hole, to_m) if len(span_hole) else np.zeros((0, 3)),
        (opened < 0)[keep][rank],
        (closed < 0)[keep][rank],
    )
