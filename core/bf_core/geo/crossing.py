"""Lines where two wireframes cross, and the grid broad phase they share with `clip_sheet.py` (ТЗ 7.23
«Линии пересечения»; ADR 0015 §7).

    boxes(v, t)                          lower and upper corners of every triangle
    Grid.build(lo, hi, box_lo, box_hi)   triangles listed by the cells their boxes cover
    overlapping_centroids(p, t, tick=)   triangles whose centroid (2D) lies strictly inside another one
    intersection_lines(a, b, tick=)      polylines where two meshes (open or closed) cross

intersection_lines: the grid over the common part of the two boxes, the exact segment of every crossing pair of
triangles (Möller's interval method, «A fast triangle-triangle intersection test», 1997; a point on an edge is
computed from the edge's ends in a fixed order, so neighbouring pairs get it bit for bit equal), then welding
(1e-9 of the common box) and chaining. Coplanar pairs give no segment. float64 relative to a whole-number
origin at the centre of the inputs; the points are returned in project coordinates.

The grid: cells about twice the typical triangle, every triangle listed in the cells its box covers (a
triangle covering more than MAX_CELLS_PER_TRIANGLE cells is kept aside as "big" and tested against
everything); keys sorted, runs found by binary search. Ported from BF `geo/solids.py` without `prange` (ADR
0014): the scans run on one thread in portions of PORTION triangles, `tick(fraction, message)` between the
portions reports progress and may raise to cancel.

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from bf_core.jit import njit

Tick = Callable[[float, str], None] | None

MAX_CELLS_PER_TRIANGLE = 512
PORTION = 1 << 17  # triangles scanned between two calls of `tick`
MAX_GRID = 2**20  # cells along an axis


def _tick(tick: Tick, fraction: float, message: str) -> None:
    if tick is not None:
        tick(float(min(max(fraction, 0.0), 1.0)), message)


# --- the grid --------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True, inline="always")
def _cell_index(x, g, cell, n):  # type: ignore[no-untyped-def]
    c = int((x - g) // cell)
    if c < 0:
        return 0
    if c >= n:
        return n - 1
    return c


@njit(cache=True, nogil=True)
def _grid_counts(lo, hi, cell, gmin, dims):  # type: ignore[no-untyped-def]
    m = lo.shape[0]
    out = np.empty(m, dtype=np.int64)
    for i in range(m):
        n = 1
        for k in range(3):
            n *= _cell_index(hi[i, k], gmin[k], cell, dims[k]) - _cell_index(lo[i, k], gmin[k], cell, dims[k]) + 1
        out[i] = n
    return out


@njit(cache=True, nogil=True)
def _grid_fill(lo, hi, cell, gmin, dims, starts, keys, ids, big):  # type: ignore[no-untyped-def]
    for i in range(lo.shape[0]):
        if big[i]:
            continue
        x0 = _cell_index(lo[i, 0], gmin[0], cell, dims[0])
        y0 = _cell_index(lo[i, 1], gmin[1], cell, dims[1])
        z0 = _cell_index(lo[i, 2], gmin[2], cell, dims[2])
        x1 = _cell_index(hi[i, 0], gmin[0], cell, dims[0])
        y1 = _cell_index(hi[i, 1], gmin[1], cell, dims[1])
        z1 = _cell_index(hi[i, 2], gmin[2], cell, dims[2])
        p = starts[i]
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    keys[p] = (x * dims[1] + y) * dims[2] + z
                    ids[p] = i
                    p += 1


def boxes(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lo = v[t[:, 0]]
    hi = lo.copy()
    for k in (1, 2):
        corner = v[t[:, k]]
        np.minimum(lo, corner, out=lo)
        np.maximum(hi, corner, out=hi)
    return lo, hi


@dataclass(frozen=True)
class Grid:
    cell: float
    gmin: np.ndarray  # float64 (3,): the lower corner
    dims: np.ndarray  # int64 (3,)
    ukeys: np.ndarray  # int64: the cells that hold triangles, ascending
    runs: np.ndarray  # int64 len(ukeys) + 1: their runs in `ids`
    ids: np.ndarray  # int64: triangles, cell by cell
    big: np.ndarray  # int64: triangles too large to be listed

    @staticmethod
    def build(
        lo: np.ndarray, hi: np.ndarray, box_lo: np.ndarray, box_hi: np.ndarray, size: float | None = None
    ) -> Grid:
        """The grid over the box [box_lo, box_hi] with the triangles of the boxes lo, hi; cells twice `size`
        (default: the median longest side of a box)."""
        if size is None:
            size = float(np.median((hi - lo).max(axis=1))) if len(lo) else 1.0
        span = float((box_hi - box_lo).max())
        cell = max(size * 2.0, span / MAX_GRID, 1e-9)
        dims = np.floor((box_hi - box_lo) / cell).astype(np.int64) + 1
        gmin = np.ascontiguousarray(box_lo, dtype=np.float64)
        counts = _grid_counts(lo, hi, cell, gmin, dims)
        big = counts > MAX_CELLS_PER_TRIANGLE
        counts[big] = 0
        starts = np.zeros(len(counts), dtype=np.int64)
        np.cumsum(counts[:-1], out=starts[1:])
        total = int(counts.sum())
        keys = np.empty(total, dtype=np.int64)
        ids = np.empty(total, dtype=np.int64)
        _grid_fill(lo, hi, cell, gmin, dims, starts, keys, ids, big)
        order = np.argsort(keys, kind="stable")
        keys, ids = keys[order], ids[order]
        first = (
            np.flatnonzero(np.concatenate([[True], keys[1:] != keys[:-1]])) if total else np.zeros(0, dtype=np.int64)
        )
        runs = np.append(first, total).astype(np.int64)
        return Grid(cell, gmin, dims, keys[first], runs, ids, np.flatnonzero(big).astype(np.int64))


# --- centroids inside other triangles (is a surface a single-valued sheet?) ------------------------------


@njit(cache=True, nogil=True)
def _strictly_inside(x, y, p, a, b, c):  # type: ignore[no-untyped-def]
    ax, ay = p[a, 0], p[a, 1]
    bx, by = p[b, 0], p[b, 1]
    cx, cy = p[c, 0], p[c, 1]
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if area == 0.0:
        return False
    w0 = ((bx - ax) * (y - ay) - (by - ay) * (x - ax)) / area
    w1 = ((cx - bx) * (y - by) - (cy - by) * (x - bx)) / area
    w2 = ((ax - cx) * (y - cy) - (ay - cy) * (x - cx)) / area
    return w0 > 1e-9 and w1 > 1e-9 and w2 > 1e-9


@njit(cache=True, nogil=True)
def _centroids_inside(p, t, start, stop, cell, gmin, dims, ukeys, runs, ids, big):  # type: ignore[no-untyped-def]
    """Triangles start…stop−1 whose centroid lies strictly inside another triangle (2D, z = 0)."""
    found = 0
    for i in range(start, stop):
        x = (p[t[i, 0], 0] + p[t[i, 1], 0] + p[t[i, 2], 0]) / 3.0
        y = (p[t[i, 0], 1] + p[t[i, 1], 1] + p[t[i, 2], 1]) / 3.0
        key = (_cell_index(x, gmin[0], cell, dims[0]) * dims[1] + _cell_index(y, gmin[1], cell, dims[1])) * dims[2]
        hit = False
        r = np.searchsorted(ukeys, key)
        if r < ukeys.shape[0] and ukeys[r] == key:
            for q in range(runs[r], runs[r + 1]):
                j = ids[q]
                if j != i and _strictly_inside(x, y, p, t[j, 0], t[j, 1], t[j, 2]):
                    hit = True
                    break
        if not hit:
            for q in range(big.shape[0]):
                j = big[q]
                if j != i and _strictly_inside(x, y, p, t[j, 0], t[j, 1], t[j, 2]):
                    hit = True
                    break
        if hit:
            found += 1
    return found


def overlapping_centroids(p: np.ndarray, t: np.ndarray, *, tick: Tick = None, message: str = "") -> int:
    """Number of triangles whose centroid, in the plane z = 0 of `p` (n×3, z ignored), lies strictly inside
    another triangle: 0 for a single-valued sheet."""
    if len(t) < 2:
        return 0
    flat = np.ascontiguousarray(p, dtype=np.float64).copy()
    flat[:, 2] = 0.0
    tri = np.ascontiguousarray(t, dtype=np.int64)
    lo, hi = boxes(flat, tri)
    grid = Grid.build(lo, hi, lo.min(axis=0), hi.max(axis=0))
    found = 0
    for start in range(0, len(tri), PORTION):
        stop = min(start + PORTION, len(tri))
        found += int(
            _centroids_inside(
                flat, tri, start, stop, grid.cell, grid.gmin, grid.dims, grid.ukeys, grid.runs, grid.ids, grid.big
            )
        )
        _tick(tick, stop / len(tri), message)
    return found


# --- the segment two triangles have in common (Möller's interval method) ---------------------------------


@njit(cache=True, nogil=True, inline="always")
def _edge_point(v, i, j, di, dj):  # type: ignore[no-untyped-def]
    """Point of the edge i–j on the plane (di, dj: signed distances of its ends), computed from the lower
    vertex id to the higher: the same bits whichever triangle asks."""
    if i > j:
        i, j, di, dj = j, i, dj, di
    if di == 0.0:
        return (v[i, 0], v[i, 1], v[i, 2])
    if dj == 0.0:
        return (v[j, 0], v[j, 1], v[j, 2])
    f = di / (di - dj)
    return (v[i, 0] + (v[j, 0] - v[i, 0]) * f, v[i, 1] + (v[j, 1] - v[i, 1]) * f, v[i, 2] + (v[j, 2] - v[i, 2]) * f)


@njit(cache=True, nogil=True, inline="always")
def _same_side(a, b):  # type: ignore[no-untyped-def]
    return (a > 0.0 and b > 0.0) or (a < 0.0 and b < 0.0)


@njit(cache=True, nogil=True)
def _plane_chord(v, i0, i1, i2, d0, d1, d2):  # type: ignore[no-untyped-def]
    """Ends of the piece of the other triangle's plane inside this triangle: (found, P, Q)."""
    zero = (0.0, 0.0, 0.0)
    if _same_side(d0, d1):
        return True, _edge_point(v, i2, i0, d2, d0), _edge_point(v, i2, i1, d2, d1)
    if _same_side(d0, d2):
        return True, _edge_point(v, i1, i0, d1, d0), _edge_point(v, i1, i2, d1, d2)
    if _same_side(d1, d2) or d0 != 0.0:
        return True, _edge_point(v, i0, i1, d0, d1), _edge_point(v, i0, i2, d0, d2)
    if d1 != 0.0:
        return True, _edge_point(v, i1, i0, d1, d0), _edge_point(v, i1, i2, d1, d2)
    if d2 != 0.0:
        return True, _edge_point(v, i2, i0, d2, d0), _edge_point(v, i2, i1, d2, d1)
    return False, zero, zero  # coplanar: no segment


@njit(cache=True, nogil=True, inline="always")
def _distance(v, i, px, py, pz, nx, ny, nz):  # type: ignore[no-untyped-def]
    return nx * (v[i, 0] - px) + ny * (v[i, 1] - py) + nz * (v[i, 2] - pz)


@njit(cache=True, nogil=True, inline="always")
def _snap(d, limit):  # type: ignore[no-untyped-def]
    return 0.0 if abs(d) <= limit else d


@njit(cache=True, nogil=True)
def _pair_segment(va, a0, a1, a2, vb, b0, b1, b2, eps, out, k, write):  # type: ignore[no-untyped-def]
    """Writes the segment the triangles share to out[k] (when `write`): True if there is one. A vertex closer
    than `eps` (m) to the other plane lies on it: noise of the coordinates does not make coplanar pairs cross."""
    bx, by, bz = vb[b0, 0], vb[b0, 1], vb[b0, 2]
    ux, uy, uz = vb[b1, 0] - bx, vb[b1, 1] - by, vb[b1, 2] - bz
    wx, wy, wz = vb[b2, 0] - bx, vb[b2, 1] - by, vb[b2, 2] - bz
    nbx, nby, nbz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
    limit = eps * np.sqrt(nbx * nbx + nby * nby + nbz * nbz)
    da0 = _snap(_distance(va, a0, bx, by, bz, nbx, nby, nbz), limit)
    da1 = _snap(_distance(va, a1, bx, by, bz, nbx, nby, nbz), limit)
    da2 = _snap(_distance(va, a2, bx, by, bz, nbx, nby, nbz), limit)
    if (da0 > 0.0 and da1 > 0.0 and da2 > 0.0) or (da0 < 0.0 and da1 < 0.0 and da2 < 0.0):
        return False
    ax, ay, az = va[a0, 0], va[a0, 1], va[a0, 2]
    ux, uy, uz = va[a1, 0] - ax, va[a1, 1] - ay, va[a1, 2] - az
    wx, wy, wz = va[a2, 0] - ax, va[a2, 1] - ay, va[a2, 2] - az
    nax, nay, naz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
    limit = eps * np.sqrt(nax * nax + nay * nay + naz * naz)
    db0 = _snap(_distance(vb, b0, ax, ay, az, nax, nay, naz), limit)
    db1 = _snap(_distance(vb, b1, ax, ay, az, nax, nay, naz), limit)
    db2 = _snap(_distance(vb, b2, ax, ay, az, nax, nay, naz), limit)
    if (db0 > 0.0 and db1 > 0.0 and db2 > 0.0) or (db0 < 0.0 and db1 < 0.0 and db2 < 0.0):
        return False
    ok, p1, q1 = _plane_chord(va, a0, a1, a2, da0, da1, da2)
    if not ok:
        return False
    ok, p2, q2 = _plane_chord(vb, b0, b1, b2, db0, db1, db2)
    if not ok:
        return False
    # Both chords lie on the line of the two planes; their overlap along it is the segment.
    dx, dy, dz = nay * nbz - naz * nby, naz * nbx - nax * nbz, nax * nby - nay * nbx
    s1p = dx * p1[0] + dy * p1[1] + dz * p1[2]
    s1q = dx * q1[0] + dy * q1[1] + dz * q1[2]
    s2p = dx * p2[0] + dy * p2[1] + dz * p2[2]
    s2q = dx * q2[0] + dy * q2[1] + dz * q2[2]
    if s1p > s1q:
        s1p, s1q, p1, q1 = s1q, s1p, q1, p1
    if s2p > s2q:
        s2p, s2q, p2, q2 = s2q, s2p, q2, p2
    lo, start = (s1p, p1) if s1p >= s2p else (s2p, p2)
    hi, end = (s1q, q1) if s1q <= s2q else (s2q, q2)
    if lo >= hi:
        return False
    if write:
        out[k, 0], out[k, 1], out[k, 2] = start[0], start[1], start[2]
        out[k, 3], out[k, 4], out[k, 5] = end[0], end[1], end[2]
    return True


@njit(cache=True, nogil=True, inline="always")
def _boxes_touch(loa, hia, i, lob, hib, j):  # type: ignore[no-untyped-def]
    for k in range(3):
        if loa[i, k] > hib[j, k] or lob[j, k] > hia[i, k]:
            return False
    return True


@njit(cache=True, nogil=True, inline="always")
def _owned(loa, hia, i, lob, hib, j, cell, gmin, dims, key):  # type: ignore[no-untyped-def]
    """The boxes overlap and the lower corner of their overlap lies in the cell `key`: a pair listed in
    several cells is taken in one of them only."""
    c = 0
    for k in range(3):
        a = max(loa[i, k], lob[j, k])
        if a > min(hia[i, k], hib[j, k]):
            return False
        c = c * dims[k] + _cell_index(a, gmin[k], cell, dims[k])
    return c == key


@njit(cache=True, nogil=True)
def _scan(va, ta, loa, hia, vb, tb, lob, hib, cell, gmin, dims, ukeys, runs, ids, big, eps, start, stop, out):  # type: ignore[no-untyped-def]
    """Segments of the crossing pairs (triangles start…stop−1 of A against B): written to `out` while it has
    room; returns how many there are (more than len(out) — run again with a larger `out`)."""
    cap = out.shape[0]
    n = 0
    for i in range(start, stop):
        a0, a1, a2 = ta[i, 0], ta[i, 1], ta[i, 2]
        x0 = _cell_index(loa[i, 0], gmin[0], cell, dims[0])
        y0 = _cell_index(loa[i, 1], gmin[1], cell, dims[1])
        z0 = _cell_index(loa[i, 2], gmin[2], cell, dims[2])
        x1 = _cell_index(hia[i, 0], gmin[0], cell, dims[0])
        y1 = _cell_index(hia[i, 1], gmin[1], cell, dims[1])
        z1 = _cell_index(hia[i, 2], gmin[2], cell, dims[2])
        if (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1) > MAX_CELLS_PER_TRIANGLE:
            # Too large for the grid: against every triangle of B whose box it touches.
            for j in range(tb.shape[0]):
                if _boxes_touch(loa, hia, i, lob, hib, j):
                    if _pair_segment(va, a0, a1, a2, vb, tb[j, 0], tb[j, 1], tb[j, 2], eps, out, n, n < cap):
                        n += 1
            continue
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    key = (x * dims[1] + y) * dims[2] + z
                    r = np.searchsorted(ukeys, key)
                    if r >= ukeys.shape[0] or ukeys[r] != key:
                        continue
                    for q in range(runs[r], runs[r + 1]):
                        j = ids[q]
                        if _owned(loa, hia, i, lob, hib, j, cell, gmin, dims, key):
                            if _pair_segment(va, a0, a1, a2, vb, tb[j, 0], tb[j, 1], tb[j, 2], eps, out, n, n < cap):
                                n += 1
        for q in range(big.shape[0]):
            j = big[q]
            if _boxes_touch(loa, hia, i, lob, hib, j):
                if _pair_segment(va, a0, a1, a2, vb, tb[j, 0], tb[j, 1], tb[j, 2], eps, out, n, n < cap):
                    n += 1
    return n


# --- welding and chaining --------------------------------------------------------------------------------


def _weld_points(points: np.ndarray, tolerance: float) -> np.ndarray:
    """Group id of every point: points closer than about `tolerance` share a group. Two grids shifted by
    half a step: points split by a cell border of one grid fall into one cell of the other; the groups of
    both grids are then joined."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(points)
    scaled = points / tolerance
    labels = []
    for shift in (0.0, 0.5):
        cells = np.floor(scaled + shift).astype(np.int64)
        _, inverse = np.unique(cells, axis=0, return_inverse=True)
        labels.append(inverse.reshape(-1))
    groups_a = int(labels[0].max()) + 1
    size = groups_a + int(labels[1].max()) + 1
    graph = coo_matrix((np.ones(n, dtype=np.int8), (labels[0], labels[1] + groups_a)), shape=(size, size))
    _, component = connected_components(graph, directed=False)
    _, group = np.unique(component[labels[0]], return_inverse=True)
    return np.asarray(group).reshape(-1)


def _chain(count: int, links: np.ndarray) -> list[tuple[bool, np.ndarray]]:
    """Joins segments (pairs of node ids) into polylines: (closed, node ids). A line runs through nodes of
    degree 2 and ends at any other node (a free end or a junction); what is left afterwards are loops."""
    degree = np.bincount(links.reshape(-1), minlength=count)
    order = np.argsort(links.reshape(-1), kind="stable")
    incident = (order // 2).tolist()
    start = np.concatenate([[0], np.cumsum(degree)]).tolist()
    pairs = links.tolist()
    deg = degree.tolist()
    used = [False] * len(pairs)

    def walk(node: int, seg: int) -> list[int]:
        line = [node]
        while True:
            used[seg] = True
            u, w = pairs[seg]
            node = w if u == node else u
            line.append(node)
            if deg[node] != 2 or node == line[0]:
                return line
            s0, s1 = incident[start[node]], incident[start[node] + 1]
            seg = s1 if used[s0] else s0
            if used[seg]:
                return line

    lines = []
    for node in np.flatnonzero((degree != 2) & (degree > 0)).tolist():
        for k in range(start[node], start[node + 1]):
            if not used[incident[k]]:
                lines.append(walk(node, incident[k]))
    for seg in range(len(pairs)):
        if not used[seg]:
            lines.append(walk(pairs[seg][0], seg))
    out = []
    for line in lines:
        closed = len(line) > 3 and line[0] == line[-1]
        out.append((closed, np.asarray(line[:-1] if closed else line, dtype=np.int64)))
    return out


def _segments(va, ta, vb, tb, box_lo, box_hi, eps, tick):  # type: ignore[no-untyped-def]
    loa, hia = boxes(va, ta)
    lob, hib = boxes(vb, tb)
    keep_a = np.all((loa <= box_hi) & (hia >= box_lo), axis=1)
    keep_b = np.all((lob <= box_hi) & (hib >= box_lo), axis=1)
    if not keep_a.any() or not keep_b.any():
        return np.zeros((0, 6))
    ta, loa, hia = ta[keep_a], loa[keep_a], hia[keep_a]
    tb, lob, hib = tb[keep_b], lob[keep_b], hib[keep_b]
    # Cells fit the coarser mesh: its triangles are few per cell, so the work stays proportional to the
    # number of triangles of the finer one.
    size = max(float(np.median((hia - loa).max(axis=1))), float(np.median((hib - lob).max(axis=1))))
    g = Grid.build(lob, hib, box_lo, box_hi, size)
    found = []
    out = np.empty((4096, 6))
    m = len(ta)
    for first in range(0, m, PORTION):
        last = min(first + PORTION, m)
        while True:
            args = (va, ta, loa, hia, vb, tb, lob, hib, g.cell, g.gmin, g.dims, g.ukeys, g.runs, g.ids, g.big)
            n = int(_scan(*args, eps, first, last, out))
            if n <= len(out):
                break
            out = np.empty((2 * n, 6))
        if n:
            found.append(out[:n].copy())
        _tick(tick, 0.05 + 0.85 * last / m, "Линии пересечения")
    return np.concatenate(found) if found else np.zeros((0, 6))


def intersection_lines(a: object, b: object, *, tolerance: float | None = None, tick: Tick = None) -> list[dict]:
    """Polylines where two meshes (open or closed) cross: [{"closed": bool, "points": (k, 3) float64}] in
    project coordinates; a closed line does not repeat its first point. Ends of segments closer than
    `tolerance` (default: 1e-9 of the size of the common box) are welded before chaining, and a vertex closer
    than it to the plane of the other triangle lies on that plane: coplanar triangles give no line.
    `tick(fraction, message)` is called between portions and may raise to cancel."""
    from bf_core.geo.solids import local_origin, mesh_arrays

    va, ta = mesh_arrays(a, "Каркас A")
    vb, tb = mesh_arrays(b, "Каркас B")
    _tick(tick, 0.0, "Линии пересечения")
    if len(ta) == 0 or len(tb) == 0:
        return []
    origin = local_origin(va, vb)
    va, vb = va - origin, vb - origin
    box_lo = np.maximum(va.min(axis=0), vb.min(axis=0))
    box_hi = np.minimum(va.max(axis=0), vb.max(axis=0))
    if np.any(box_lo > box_hi):
        _tick(tick, 1.0, "Линии пересечения")
        return []
    if tolerance is None:
        tolerance = max(1e-9 * float(np.linalg.norm(box_hi - box_lo)), 1e-12)
    segments = _segments(va, ta, vb, tb, box_lo, box_hi, float(tolerance), tick)
    lines: list[dict] = []
    if len(segments):
        points = segments.reshape(-1, 3)
        group = _weld_points(points, float(tolerance))
        nodes = np.empty((int(group.max()) + 1, 3))
        nodes[group[::-1]] = points[::-1]  # the first point of every group represents it
        links = group.reshape(-1, 2)
        links = links[links[:, 0] != links[:, 1]]
        if len(links):
            links = np.unique(np.sort(links, axis=1), axis=0)
            lines = [{"closed": c, "points": nodes[line] + origin} for c, line in _chain(len(nodes), links)]
    _tick(tick, 1.0, "Линии пересечения")
    return lines
