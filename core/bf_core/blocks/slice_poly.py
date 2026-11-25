"""The polygons of the section of a block model by a plane (подшаг 17.24; ТЗ 8.7.5 «Срез плоскостью», ADR 0023
decision 19, docs/progress/e17-api.md §12 `POST /blocks/{id}/slice/polygons`; for the print of Э22).

    pl = slice.plane_of(grid, origin, normal)
    poly = section_polygons(grid, pl, p, s, w, passed, limit=200000)         # whole arrays of a model
    sec = Sectioner(grid, pl, limit=…); sec.add(p, s, w, passed, first=start) …; poly = sec.finish()     # by portions

The convex polygon plane ∩ block of every row the plane crosses, in the project axes, counter-clockwise seen from
the end of the normal. Boxes are [lo; hi) on every axis (as the raster of `slice.py`): a plane on a shared face
belongs to the block above it (the normal of a plane ⟂ an axis is taken as +e_a for that, the ring goes the other
way for −e_a), a plane that only touches a block (an edge, a corner) makes no polygon. A plane ⟂ an axis gives the
rectangle of the cross-section exactly (the product of the two sides); an oblique one is clipped on the twelve edges
of the box and ordered by the angle about the mean. `area` of a polygon is half the norm of the sum of the triangle
fan; `total` — the Neumaier sum over EVERY polygon the plane makes (also beyond `limit`; the same bits in any
portions of the rows).
Memory: the portion is counted first, then only `limit` polygons are kept (6 vertices, 144 bytes each).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bf_core.blocks.schema import GridV2
from bf_core.blocks.slice import (
    _CORNER,
    _EDGES,
    LIMIT,
    MAX_LIMIT,
    Plane,
    _box,
    _parent_at,
    _tables,
    bad_plane,
)
from bf_core.jit import njit


@njit(cache=True, nogil=True)
def _clip(lo, hi, o, n, uq, vq, corner, edges, pts, tmp):  # type: ignore[no-untyped-def]
    """The points where the plane crosses the edges of the box [lo; hi) (an edge crosses where its ends lie on
    different sides, d > 0 against d ≤ 0), ordered counter-clockwise about their mean in (u, v): → their number."""
    dist = np.empty(8)
    for c in range(8):
        acc = 0.0
        for a in range(3):
            x = hi[a] if corner[c, a] else lo[a]
            acc += n[a] * (x - o[a])
        dist[c] = acc
    m = 0
    for e in range(12):
        c0 = edges[e, 0]
        c1 = edges[e, 1]
        if (dist[c0] > 0.0) == (dist[c1] > 0.0):
            continue
        t = dist[c0] / (dist[c0] - dist[c1])
        for a in range(3):
            x0 = hi[a] if corner[c0, a] else lo[a]
            x1 = hi[a] if corner[c1, a] else lo[a]
            pts[m, a] = x0 + (x1 - x0) * t
        m += 1
    if m < 3:
        return 0
    cen = np.zeros(3)
    for k in range(m):
        for a in range(3):
            cen[a] += pts[k, a] / m
    for k in range(m):
        du_ = 0.0
        dv_ = 0.0
        for a in range(3):
            du_ += (pts[k, a] - cen[a]) * uq[a]
            dv_ += (pts[k, a] - cen[a]) * vq[a]
        tmp[k] = np.arctan2(dv_, du_)
    for k in range(1, m):  # insertion sort by the angle (m ≤ 6)
        ang = tmp[k]
        x = pts[k, 0]
        y = pts[k, 1]
        z = pts[k, 2]
        q = k - 1
        while q >= 0 and tmp[q] > ang:
            tmp[q + 1] = tmp[q]
            pts[q + 1, 0] = pts[q, 0]
            pts[q + 1, 1] = pts[q, 1]
            pts[q + 1, 2] = pts[q, 2]
            q -= 1
        tmp[q + 1] = ang
        pts[q + 1, 0] = x
        pts[q + 1, 1] = y
        pts[q + 1, 2] = z
    out = 1  # drop the repeats (the plane through a corner or an edge gives the same point twice)
    for k in range(1, m):
        same = True
        for a in range(3):
            if abs(pts[k, a] - pts[out - 1, a]) > 1e-12 * (1.0 + abs(pts[k, a])):
                same = False
        if not same:
            for a in range(3):
                pts[out, a] = pts[k, a]
            out += 1
    last = True
    for a in range(3):
        if abs(pts[out - 1, a] - pts[0, a]) > 1e-12 * (1.0 + abs(pts[0, a])):
            last = False
    if last and out > 1:
        out -= 1
    return out if out >= 3 else 0


@njit(cache=True, nogil=True)
def _polygons(
    p,
    s,
    w,
    has_sub,
    keep,
    has_keep,
    nx,
    ny,
    reg,
    d,
    ed,
    tab,
    toff,
    axis,
    pa,
    o,
    n,
    uq,
    vq,  # type: ignore[no-untyped-def]
    first,
    cap,
    out_row,
    out_n,
    out_ring,
    out_area,
    acc,
    corner,
    edges,
):
    """Count (cap = 0) or fill the polygons of the rows the plane crosses; `acc` — the compensated sum of the areas
    (Neumaier). Returns the number of the polygons of the portion (also beyond `cap`)."""
    cnt = 0
    nxy = nx * ny
    ijk = np.empty(3, dtype=np.int64)
    lo = np.empty(3)
    hi = np.empty(3)
    pts = np.empty((12, 3))
    tmp = np.empty(12)
    for r in range(p.shape[0]):
        if has_keep and not keep[r]:
            continue
        pi = np.int64(p[r])
        kk = pi // nxy
        rem = pi - kk * nxy
        jj = rem // nx
        ii = rem - jj * nx
        ijk[0] = ii
        ijk[1] = jj
        ijk[2] = kk
        if axis >= 0 and ijk[axis] != pa:
            continue
        _box(reg, d, ed, tab, toff, ijk, s, w, has_sub, r, lo, hi)
        area = 0.0
        m = 0
        if axis >= 0:
            if not (lo[axis] <= o[axis] and o[axis] < hi[axis]):
                continue
            ua = (axis + 1) % 3
            va = (axis + 2) % 3
            m = 4
            pts[0, ua] = lo[ua]
            pts[0, va] = lo[va]
            pts[1, ua] = hi[ua]
            pts[1, va] = lo[va]
            pts[2, ua] = hi[ua]
            pts[2, va] = hi[va]
            pts[3, ua] = lo[ua]
            pts[3, va] = hi[va]
            for k in range(4):
                pts[k, axis] = o[axis]
            area = (hi[ua] - lo[ua]) * (hi[va] - lo[va])
        else:
            dist = 0.0
            rad = 0.0
            for a in range(3):
                dist += n[a] * (0.5 * (lo[a] + hi[a]) - o[a])
                rad += abs(n[a]) * 0.5 * (hi[a] - lo[a])
            if abs(dist) >= rad:
                continue
            m = _clip(lo, hi, o, n, uq, vq, corner, edges, pts, tmp)
            if m < 3:
                continue
            ax = 0.0
            ay = 0.0
            az = 0.0
            for k in range(1, m - 1):
                e1x = pts[k, 0] - pts[0, 0]
                e1y = pts[k, 1] - pts[0, 1]
                e1z = pts[k, 2] - pts[0, 2]
                e2x = pts[k + 1, 0] - pts[0, 0]
                e2y = pts[k + 1, 1] - pts[0, 1]
                e2z = pts[k + 1, 2] - pts[0, 2]
                ax += e1y * e2z - e1z * e2y
                ay += e1z * e2x - e1x * e2z
                az += e1x * e2y - e1y * e2x
            area = 0.5 * abs(ax * n[0] + ay * n[1] + az * n[2])
        t = acc[0] + area  # Neumaier: the compensated sum, the same bits for any portions of the rows
        if abs(acc[0]) >= abs(area):
            acc[1] += (acc[0] - t) + area
        else:
            acc[1] += (area - t) + acc[0]
        acc[0] = t
        if cnt < cap:
            out_row[cnt] = first + r
            out_n[cnt] = m
            out_area[cnt] = area
            for k in range(6):
                kk2 = k if k < m else m - 1
                for a in range(3):
                    out_ring[cnt, k, a] = pts[kk2, a]
        cnt += 1
    return cnt


# --- the polygons -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Polygons:
    """The section: `row` int64 [m] (of the file), `n` uint8 [m] (3–6 vertices), `ring` float64 [m, 6, 3] (project axes,
    counter-clockwise seen from the end of the normal, padded with the last vertex), `area` float64 [m]; `total` —
    the area of every polygon the plane makes (also beyond `limit`), `count` — their number, `truncated`."""

    row: np.ndarray
    n: np.ndarray
    ring: np.ndarray
    area: np.ndarray
    total: float
    count: int
    truncated: bool

    def __len__(self) -> int:
        return int(self.row.shape[0])

    def vertices(self, i: int) -> np.ndarray:
        """The ring of polygon `i`: float64 [n, 3]."""
        return self.ring[i, : int(self.n[i])]


class Sectioner:
    """The polygons of a plane over portions of rows (module docstring)."""

    def __init__(self, grid: GridV2, plane: Plane, *, sub: bool = True, limit: int = LIMIT) -> None:
        if not 1 <= int(limit) <= MAX_LIMIT:
            raise bad_plane(f"Число многоугольников: от 1 до {MAX_LIMIT}.", "limit",
                            f"Задайте число от 1 до {MAX_LIMIT}.")  # fmt: skip
        self.grid, self.plane, self.sub, self.limit = grid, plane, bool(sub), int(limit)
        self._pa = _parent_at(grid, plane.axis, float(plane.o[plane.axis])) if plane.axis >= 0 else -1
        self._empty = self._pa is None
        self._reg, self._d, self._tab, self._toff = _tables(grid)
        self._ed = np.asarray(grid.sub.div if sub else (1, 1, 1), dtype=np.float64)
        if plane.axis >= 0:
            self._uq = np.zeros(3)
            self._uq[(plane.axis + 1) % 3] = 1.0
            self._vq = np.zeros(3)
            self._vq[(plane.axis + 2) % 3] = 1.0
        else:
            n = plane.n
            h = float(np.hypot(n[0], n[1]))
            self._uq = np.array([-n[1] / h, n[0] / h, 0.0])
            self._vq = np.cross(n, self._uq)
        self._acc = np.zeros(2)
        self._parts: list[tuple[np.ndarray, ...]] = []
        self._have = 0
        self.count = 0

    def add(self, p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None, passed: np.ndarray | None = None,
            first: int = 0) -> None:  # fmt: skip
        """One portion of rows; `first` — the number of its first row in the file."""
        if self._empty or not len(p):
            return
        p = np.ascontiguousarray(p, dtype=np.uint64)
        has_sub = self.sub and s is not None and w is not None
        one = np.zeros((1, 1), dtype=np.uint8)
        keep = None if passed is None else np.asarray(passed, dtype=np.bool_)
        grid, pl = self.grid, self.plane
        args = (
            p, np.ascontiguousarray(s) if has_sub else one, np.ascontiguousarray(w) if has_sub else one, has_sub,
            keep if keep is not None else np.zeros(1, dtype=np.bool_), keep is not None,
            grid.counts[0], grid.counts[1], self._reg, self._d, self._ed, self._tab, self._toff,
            pl.axis, self._pa, pl.o, pl.n, self._uq, self._vq, first,
        )  # fmt: skip
        z = np.zeros(1)
        counted = int(_polygons(*args, 0, np.zeros(1, np.int64), np.zeros(1, np.uint8), np.zeros((1, 6, 3)), z,
                                self._acc, _CORNER, _EDGES))  # fmt: skip
        self.count += counted
        room = min(counted, self.limit - self._have)
        if room > 0:
            row, nv, ring, area = (np.empty(room, np.int64), np.empty(room, np.uint8), np.empty((room, 6, 3)),
                                   np.empty(room))  # fmt: skip
            _polygons(*args, room, row, nv, ring, area, np.zeros(2), _CORNER, _EDGES)
            self._parts.append((row, nv, ring, area))
            self._have += room

    def finish(self) -> Polygons:
        if self._parts:
            row, nv, ring, area = (np.concatenate([x[i] for x in self._parts]) for i in range(4))
        else:
            row, nv, ring, area = np.empty(0, np.int64), np.empty(0, np.uint8), np.empty((0, 6, 3)), np.empty(0)
        m = len(row)
        pts = self.grid.to_project(ring.reshape(-1, 3)).reshape(m, 6, 3) if m else ring
        if self.plane.flip and m:  # the normal −e_a: counter-clockwise seen from its end — the other way round
            for i in range(m):
                k = int(nv[i])
                pts[i, :k] = pts[i, :k][::-1]
                pts[i, k:] = pts[i, k - 1] if k else pts[i, k:]
        return Polygons(row, nv, pts, area, float(self._acc[0] + self._acc[1]), self.count, self.count > m)


def section_polygons(grid: GridV2, plane: Plane, p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None,
                     passed: np.ndarray | None = None, *, sub: bool = True,
                     limit: int = LIMIT) -> Polygons:  # fmt: skip
    """The polygons of the whole arrays of a model (one portion)."""
    sec = Sectioner(grid, plane, sub=sub, limit=limit)
    sec.add(p, s, w, passed)
    return sec.finish()
