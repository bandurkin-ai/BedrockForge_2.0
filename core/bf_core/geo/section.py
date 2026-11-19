"""Exact section of a wireframe by a plane, float64 (ТЗ 7.14 «Каркасы — линиями сечения», 7.21; ADR 0013 §4).

Ported from BF `geo/section.py`. Only the parts of the triangle array whose box the plane touches are read
(`wireframes.render["parts"]`: the triangles are stored in spatial order, a part is a run of them). A vertex
lying on the plane counts as being on its positive side, so every cut triangle has exactly two cut edges; a cut
edge gets one point, computed from its two ends in the order of their numbers, so the triangles that share it
get the same point bit for bit and the segments chain into polylines: closed ones on a closed wireframe, open
ones from border to border of an open one. Where the plane passes through a vertex the points of the edges
around it coincide (they are the vertex itself, exactly) and are written once. Consequences of the rule: the
plane of a face of a solid gives the outline of that face when the solid is behind the plane and nothing when
it is in front; a triangle lying in the plane gives nothing.

    plane_section(xyz, tri, point, normal, parts=…)  → Section: xyz float64 k×3 (project coordinates),
                                                       offsets int64 n+1, closed bool n
    section_window(x0, y0, az, length=…, towards=…, away=…)  the window of a section line (ADR 0007 §4)
    window_section(xyz, tri, window, offset=0)       the section in the plane of the line (or of a face of
                                                     the window), cut at the ends of a line that has a length
    Section.clip(planes)                             the lines inside half-spaces n·p + c ≥ 0

Accuracy: a point is the float64 nearest to the exact crossing within two roundings of its coordinates
(2e-9 m on UTM northings). Differences from BF: coordinates are project ones, not offsets from an origin, and
are not rounded to 1e-6; the chains are built by a numba kernel; the point of an edge is taken from the end
nearer to the plane (a vertex on the plane is returned bit for bit).

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.jit import njit

from .tri_kernels import BOX_PAD_M, half_spaces, mesh_arrays, part_arrays, vector

# progress(fraction 0…1 or None, message or None); it may raise (JobCancelled) to cancel the calculation.
Tick = Callable[[float | None, str | None], None]

BATCH = 1 << 20  # triangles between two ticks (about 10 ms)
_MESSAGE = "Сечение каркаса"

# --- kernels -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _height(xyz, i, px, py, pz, nx, ny, nz):  # type: ignore[no-untyped-def]
    return (xyz[i, 0] - px) * nx + (xyz[i, 1] - py) * ny + (xyz[i, 2] - pz) * nz


@njit(cache=True, nogil=True)
def _cut(xyz, tri, first, count, px, py, pz, nx, ny, nz, keys):  # type: ignore[no-untyped-def]
    """The two cut edges of every triangle of the runs [first, first + count) the plane passes through, as
    keys low vertex · n + high vertex, into `keys` (two a triangle). Returns their number; −1 — a triangle
    refers to a vertex that does not exist."""
    nv = xyz.shape[0]
    n = 0
    for r in range(first.shape[0]):
        for i in range(first[r], first[r] + count[r]):
            i0, i1, i2 = tri[i, 0], tri[i, 1], tri[i, 2]
            if i0 < 0 or i1 < 0 or i2 < 0 or i0 >= nv or i1 >= nv or i2 >= nv:
                return -1
            s0 = _height(xyz, i0, px, py, pz, nx, ny, nz) >= 0.0
            s1 = _height(xyz, i1, px, py, pz, nx, ny, nz) >= 0.0
            s2 = _height(xyz, i2, px, py, pz, nx, ny, nz) >= 0.0
            if s0 == s1 and s1 == s2:
                continue
            # the corner alone on its side: the two edges from it are cut
            if s1 == s2:
                a, b, c = np.int64(i0), np.int64(i1), np.int64(i2)
            elif s0 == s2:
                a, b, c = np.int64(i1), np.int64(i2), np.int64(i0)
            else:
                a, b, c = np.int64(i2), np.int64(i0), np.int64(i1)
            k1 = min(a, b) * nv + max(a, b)
            k2 = min(a, c) * nv + max(a, c)
            if k1 == k2:  # a triangle with a repeated vertex: no area
                continue
            keys[n] = k1
            keys[n + 1] = k2
            n += 2
    return n


@njit(cache=True, nogil=True)
def _nodes(xyz, keys, px, py, pz, nx, ny, nz):  # type: ignore[no-untyped-def]
    """The point of every cut edge, from its end nearer to the plane."""
    nv = xyz.shape[0]
    out = np.empty((keys.shape[0], 3), dtype=np.float64)
    for k in range(keys.shape[0]):
        a = keys[k] // nv
        b = keys[k] % nv
        da = _height(xyz, a, px, py, pz, nx, ny, nz)
        db = _height(xyz, b, px, py, pz, nx, ny, nz)
        if abs(da) > abs(db):
            a, b = b, a
            da, db = db, da
        t = 0.0  # ends on one side by a rounding that differs from `_cut`: the plane is at the nearer end
        if (da >= 0.0) != (db >= 0.0):
            t = da / (da - db)
        for c in range(3):
            out[k, c] = xyz[a, c] + (xyz[b, c] - xyz[a, c]) * t
    return out


@njit(cache=True, nogil=True)
def _chain(links, n_nodes):  # type: ignore[no-untyped-def]
    """Segments (pairs of node numbers) joined into lines: (nodes of all the lines, offsets, closed). Lines
    that end at a node of odd degree (a border, a non-manifold edge) first, then the loops. A closed line does
    not repeat its first node. Every segment is used once."""
    m = links.shape[0]
    start = np.zeros(n_nodes + 1, dtype=np.int64)
    for i in range(m):
        start[links[i, 0] + 1] += 1
        start[links[i, 1] + 1] += 1
    for k in range(n_nodes):
        start[k + 1] += start[k]
    fill = start[:n_nodes].copy()
    around = np.empty(2 * m, dtype=np.int64)  # the segments at every node
    for i in range(m):
        for c in range(2):
            around[fill[links[i, c]]] = i
            fill[links[i, c]] += 1
    used = np.zeros(m, dtype=np.bool_)
    cursor = start[:n_nodes].copy()  # the next segment of a node to look at
    seq = np.empty(2 * m, dtype=np.int64)
    offsets = np.zeros(m + 1, dtype=np.int64)
    closed = np.zeros(m, dtype=np.bool_)
    n_seq = 0
    n_lines = 0
    for phase in range(2):
        for s in range(n_nodes):
            if phase == 0 and (start[s + 1] - start[s]) % 2 == 0:
                continue
            while True:
                while cursor[s] < start[s + 1] and used[around[cursor[s]]]:
                    cursor[s] += 1
                if cursor[s] == start[s + 1]:
                    break
                first = n_seq
                seq[n_seq] = s
                n_seq += 1
                node = s
                while True:
                    while cursor[node] < start[node + 1] and used[around[cursor[node]]]:
                        cursor[node] += 1
                    if cursor[node] == start[node + 1]:
                        break
                    e = around[cursor[node]]
                    used[e] = True
                    node = links[e, 1] if links[e, 0] == node else links[e, 0]
                    seq[n_seq] = node
                    n_seq += 1
                    if node == s:
                        break
                loop = n_seq - first > 2 and seq[n_seq - 1] == s
                if loop:
                    n_seq -= 1
                closed[n_lines] = loop
                n_lines += 1
                offsets[n_lines] = n_seq
    return seq[:n_seq].copy(), offsets[: n_lines + 1].copy(), closed[:n_lines].copy()


@njit(cache=True, nogil=True)
def _same(a, i, b, j):  # type: ignore[no-untyped-def]
    return a[i, 0] == b[j, 0] and a[i, 1] == b[j, 1] and a[i, 2] == b[j, 2]


@njit(cache=True, nogil=True)
def _lines(nodes, seq, offsets, closed):  # type: ignore[no-untyped-def]
    """Coordinates of the lines without repeated consecutive points; a line of one point is dropped, a loop of
    two points is an open line."""
    n_lines = closed.shape[0]
    xyz = np.empty((seq.shape[0], 3), dtype=np.float64)
    out_offsets = np.zeros(n_lines + 1, dtype=np.int64)
    out_closed = np.zeros(n_lines, dtype=np.bool_)
    k = 0
    n = 0
    for i in range(n_lines):
        first = k
        for j in range(offsets[i], offsets[i + 1]):
            if k > first and _same(xyz, k - 1, nodes, seq[j]):
                continue
            xyz[k] = nodes[seq[j]]
            k += 1
        loop = closed[i]
        if loop and k - first > 1 and _same(xyz, k - 1, xyz, first):
            k -= 1
        if k - first < 2:
            k = first
            continue
        out_closed[n] = loop and k - first > 2
        n += 1
        out_offsets[n] = k
    return xyz[:k].copy(), out_offsets[: n + 1].copy(), out_closed[:n].copy()


@njit(cache=True, nogil=True)
def _clip(xyz, offsets, closed, a, b, c, d):  # type: ignore[no-untyped-def]
    """The pieces of the lines where a·x + b·y + c·z + d ≥ 0. A closed line that leaves the half-space becomes
    open pieces (none of them split at its first point)."""
    n_lines = closed.shape[0]
    total = xyz.shape[0]
    out = np.empty((2 * total + 2, 3), dtype=np.float64)
    out_offsets = np.zeros(total + n_lines + 2, dtype=np.int64)
    out_closed = np.zeros(total + n_lines + 1, dtype=np.bool_)
    dist = np.empty(total, dtype=np.float64)
    for i in range(total):
        dist[i] = a * xyz[i, 0] + b * xyz[i, 1] + c * xyz[i, 2] + d
    k = 0
    n = 0
    for line in range(n_lines):
        lo, hi = offsets[line], offsets[line + 1]
        m = hi - lo
        inside = 0
        for i in range(lo, hi):
            if dist[i] >= 0.0:
                inside += 1
        if inside == 0:
            continue
        if inside == m:
            out[k : k + m] = xyz[lo:hi]
            k += m
            out_closed[n] = closed[line]
            n += 1
            out_offsets[n] = k
            continue
        s = lo
        steps = m - 1
        if closed[line]:
            steps = m
            while dist[s] >= 0.0:  # start outside: the walk meets every piece whole
                s += 1
        first = k  # where the piece being written starts
        if dist[s] >= 0.0:
            out[k] = xyz[s]
            k += 1
        for q in range(steps):
            i = s + q
            if i >= hi:
                i -= m
            j = i + 1
            if j >= hi:
                j -= m
            enters = dist[j] >= 0.0
            if (dist[i] >= 0.0) != enters:  # the point on the plane, from the end nearer to it
                p, r = (i, j) if abs(dist[i]) <= abs(dist[j]) else (j, i)
                t = dist[p] / (dist[p] - dist[r])
                if enters:
                    first = k
                for axis in range(3):
                    out[k, axis] = xyz[p, axis] + (xyz[r, axis] - xyz[p, axis]) * t
                if not (k > first and _same(out, k - 1, out, k)):
                    k += 1
                if not enters:  # the piece ends here; one point is not a line
                    if k - first >= 2:
                        n += 1
                        out_offsets[n] = k
                    else:
                        k = first
                    first = k
            if enters and not (k > first and _same(out, k - 1, xyz, j)):
                out[k] = xyz[j]
                k += 1
        if k - first >= 2:  # an open line that ends inside
            n += 1
            out_offsets[n] = k
        else:
            k = first
    return out[:k].copy(), out_offsets[: n + 1].copy(), out_closed[:n].copy()


# --- the result --------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Section:
    """Polylines of a section: line i is `xyz[offsets[i]:offsets[i + 1]]`; a closed line does not repeat its
    first point. The layout of BFA1 `section` (ADR 0013 §5)."""

    xyz: np.ndarray  # float64 k×3, project coordinates
    offsets: np.ndarray  # int64 n + 1
    closed: np.ndarray  # bool n

    def __len__(self) -> int:
        return len(self.closed)

    def line(self, i: int) -> np.ndarray:
        return np.asarray(self.xyz[int(self.offsets[i]) : int(self.offsets[i + 1])])

    def lengths(self) -> np.ndarray:
        """The length of every line (of a closed one — with the segment that closes it)."""
        out = np.zeros(len(self), dtype=np.float64)
        for i in range(len(self)):
            p = self.line(i)
            steps = np.diff(np.vstack([p, p[:1]]) if self.closed[i] else p, axis=0)
            out[i] = math.fsum(np.sqrt(np.einsum("ij,ij->i", steps, steps)).tolist())
        return out

    def clip(self, planes: Any) -> Section:
        """The lines inside every half-space of `planes` (k×4 [nx, ny, nz, c]: n·p + c ≥ 0 is kept, a point on
        a plane is kept). A closed line cut by a plane becomes open pieces."""
        xyz, offsets, closed = self.xyz, self.offsets, self.closed
        for a, b, c, d in half_spaces(planes).tolist():
            if not len(closed):
                break
            xyz, offsets, closed = _clip(xyz, offsets, closed, a, b, c, d)
        return Section(xyz, offsets, closed)


def _empty() -> Section:
    return Section(np.zeros((0, 3), dtype=np.float64), np.zeros(1, dtype=np.int64), np.zeros(0, dtype=np.bool_))


# --- the section -------------------------------------------------------------------------------------------


def _plane(point: Any, normal: Any) -> tuple[np.ndarray, np.ndarray]:
    p, n = vector(point, "point"), vector(normal, "normal")
    norm = float(np.linalg.norm(n))
    if not norm > 0.0:
        raise ValueError("the normal of the plane is zero")
    return p, n / norm


def parts_cut(parts: Any, point: Any, normal: Any, *, triangles: int | None = None) -> np.ndarray:
    """(first, count) int64 r×2 of the parts of a stored wireframe whose box the plane touches."""
    p, n = _plane(point, normal)
    first, count, box = part_arrays(parts, triangles if triangles is not None else np.iinfo(np.int64).max)
    return _touched(first, count, box, p, n)


def _touched(first: np.ndarray, count: np.ndarray, box: np.ndarray, p: np.ndarray, n: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):  # a box without bounds (no part table) is always read
        centre, half = (box[:, :3] + box[:, 3:]) / 2.0, (box[:, 3:] - box[:, :3]) / 2.0
        far = np.abs((centre - p) @ n) > np.abs(half) @ np.abs(n) + BOX_PAD_M
    keep = ~far & (count > 0)
    return np.column_stack([first[keep], count[keep]]).astype(np.int64).reshape(-1, 2)


def _batches(runs: np.ndarray, size: int) -> list[np.ndarray]:
    """Runs of triangles in portions of about `size` triangles (a longer run is cut)."""
    pieces = [(a, min(size, first + count - a)) for first, count in runs.tolist()
              for a in range(first, first + count, size)]  # fmt: skip
    out: list[np.ndarray] = []
    batch: list[tuple[int, int]] = []
    held = 0
    for piece in pieces:
        if batch and held + piece[1] > size:
            out.append(np.array(batch, dtype=np.int64))
            batch, held = [], 0
        batch.append(piece)
        held += piece[1]
    if batch:
        out.append(np.array(batch, dtype=np.int64))
    return out


def plane_section(
    xyz: Any,
    tri: Any,
    point: Any,
    normal: Any,
    *,
    parts: Any = None,
    clip: Any = None,
    tick: Tick | None = None,
) -> Section:
    """Polylines where the plane through `point` with the normal `normal` (project coordinates) cuts the mesh.

    `xyz` float64 n×3 and `tri` int32 m×3 — the blobs of a wireframe (memory maps are read in place); `parts` —
    `wireframes.render["parts"]`, so that only the parts the plane touches are read; `clip` — planes k×4 the
    lines are cut by (`Section.clip`); `tick` — progress and cancellation between portions of triangles.
    ValueError: a normal that is zero, a value that is not a number, a triangle of a vertex that is not there.
    """
    v, t = mesh_arrays(xyz, tri)
    p, n = _plane(point, normal)
    planes = half_spaces(clip)
    first, count, box = part_arrays(parts, len(t))
    runs = _touched(first, count, box, p, n)
    total = int(runs[:, 1].sum())
    if tick is not None:
        tick(0.0, _MESSAGE)
    found: list[np.ndarray] = []
    done = 0
    for batch in _batches(runs, BATCH):
        size = int(batch[:, 1].sum())
        keys = np.empty(2 * size, dtype=np.int64)
        k = _cut(v, t, batch[:, 0].copy(), batch[:, 1].copy(), p[0], p[1], p[2], n[0], n[1], n[2], keys)
        if k < 0:
            raise ValueError("a triangle refers to a vertex that does not exist")
        if k:
            found.append(keys[:k].copy())
        done += size
        if tick is not None and done < total:
            tick(done / total, _MESSAGE)
    out = _empty()
    if found:
        edges, inverse = np.unique(np.concatenate(found), return_inverse=True)
        nodes = _nodes(v, edges, p[0], p[1], p[2], n[0], n[1], n[2])
        links = np.ascontiguousarray(inverse.reshape(-1, 2), dtype=np.int64)
        out = Section(*_lines(nodes, *_chain(links, len(edges))))
        if len(planes):
            out = out.clip(planes)
    if tick is not None:
        tick(1.0, _MESSAGE)
    return out


# --- the window of a section line (ТЗ 7.14; ADR 0007 §4; ui/src/viz/section/math.ts) -----------------------


def _sin_cos(deg: float) -> tuple[float, float]:
    """sin and cos of degrees, exact on the multiples of 90° (as `sinCosDeg` of the 3D window)."""
    d = math.fmod(math.fmod(deg, 360.0) + 360.0, 360.0)
    exact = {0.0: (0.0, 1.0), 90.0: (1.0, 0.0), 180.0: (0.0, -1.0), 270.0: (-1.0, 0.0)}
    return exact.get(d) or (math.sin(math.radians(d)), math.cos(math.radians(d)))


@dataclass(frozen=True, eq=False)
class SectionWindow:
    """A vertical section: the line from (x0, y0) along an azimuth and the window around its plane. The viewer
    stands to the right of the line: `normal` looks at the viewer, `towards` is the depth of the window on that
    side, `away` — behind the plane."""

    point: np.ndarray  # (x0, y0, 0): a point of the plane of the line
    normal: np.ndarray  # unit, level, to the right of the line
    along: np.ndarray  # unit, along the line
    length: float | None  # None — the line has no ends
    towards: float
    away: float

    def planes(self) -> np.ndarray:
        """The half-spaces of the window, k×4 [nx, ny, nz, c] (n·p + c ≥ 0 inside): the two faces and, for a
        line with a length, its two ends — `clipPlanes` of the 3D window from the origin (0, 0)."""
        nc = float(self.normal @ self.point)
        faces = np.array([[*self.normal, -nc + self.away], [*(-self.normal), nc + self.towards]]) + 0.0
        return np.vstack([faces, self.ends()])

    def ends(self) -> np.ndarray:
        """The half-spaces of the two ends of a line with a length (0×4 without one)."""
        if self.length is None:
            return np.zeros((0, 4))
        ac = float(self.along @ self.point)
        return np.array([[*self.along, -ac], [*(-self.along), ac + self.length]]) + 0.0


def section_window(
    x0: float, y0: float, az: float, *, length: float | None = None, towards: float = 25.0, away: float = 25.0
) -> SectionWindow:
    """The window of `SectionLine {x0, y0, az, length, towards, away}` (ADR 0007 §4): azimuth in degrees
    clockwise from north, the normal to the right of the line."""
    numbers = [x0, y0, az, towards, away] + ([] if length is None else [length])
    if not all(math.isfinite(float(x)) for x in numbers) or towards < 0.0 or away < 0.0:
        raise ValueError("a section line needs finite x0, y0, az and towards ≥ 0, away ≥ 0")
    if length is not None and not length > 0.0:
        raise ValueError("the length of a section line is positive, or None for a line without ends")
    s, c = _sin_cos(float(az))
    return SectionWindow(
        np.array([float(x0), float(y0), 0.0]),
        np.array([c, -s, 0.0]) + 0.0,  # -0.0 → 0.0
        np.array([s, c, 0.0]),
        None if length is None else float(length),
        float(towards),
        float(away),
    )


def window_section(
    xyz: Any,
    tri: Any,
    window: SectionWindow,
    *,
    offset: float = 0.0,
    parts: Any = None,
    tick: Tick | None = None,
) -> Section:
    """The lines a wireframe is shown by when the view is clipped by the window of a section line: its section
    by the plane of the line, cut at the ends of a line that has a length. `offset` moves the plane along the
    normal: `window.towards` and `-window.away` are the faces of the window. The faces themselves do not clip
    (they are parallel to the plane; a window of zero depth must keep its own section)."""
    if not math.isfinite(offset):
        raise ValueError("the offset of the plane must be a number")
    point = window.point + window.normal * float(offset)
    ends = window.ends()
    return plane_section(xyz, tri, point, window.normal, parts=parts, clip=ends if len(ends) else None, tick=tick)
