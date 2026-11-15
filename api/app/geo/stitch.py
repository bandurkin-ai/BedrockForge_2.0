"""Solids from contours: stitching closed strings drawn on a series of sections, extrusion, manual edits.

    stitch_contours(contours, ties)   contours on parallel-ish sections → one triangulated solid
    extrude_polygon(polygon, ...)     polygon → prism along its normal or along a vector
    cap_triangles(polygon)            triangulation of a (non-convex) polygon in its best-fit plane
    edit_triangles(v, t, remove, add) drop triangles by index, add triangles by corner coordinates

How a series is stitched (docs/prompts/strings-wireframes-editing.md, 6.2):

* The common normal is the mean of the contours' plane normals; contours are grouped into sections
  by their position along it and every contour is turned counter-clockwise as seen from the tip of
  the normal. All geometry is computed in float64 relative to the centre of the data; vertices of
  the input contours are copied to the output untouched (bit-identical).
* Two contours: the strip is a shortest path in the Fuchs–Kedem–Uselton graph — node (i, j) is the
  span a_i–b_j, a step along either contour adds one triangle and costs the length of the new span.
  Spans are measured after both contours are brought to a common centre and size, so a shifted or
  smaller neighbour is matched by shape, not by absolute position. The best start is found exactly:
  shortest paths for different starts do not cross, which bounds each search by two known paths
  (O(n·m·log m)). A path goes once round both contours, so the strip has no twist by construction.
* Tie lines fix nodes of that path: the search is run separately between consecutive ties, so every
  tie is an edge of the result.
* Branching (one contour against several): the parent is cut by a chord between the groups of
  children; each part is stitched to its child as a closed contour. The two tubes use the chord's
  edges in opposite directions, so together they are a manifold "pants" surface bounded on the
  parent's section by the parent contour. The chord's end points are inserted into the parent
  contour for all its uses (the strip on its other side, its cap); the chord's inner points are
  lowered towards the children (a saddle), which keeps them clear of the cap and of a second
  branching on the parent's other side.
* Ends: flat cap, pinch-out to a point or to a ridge line, or continuation by a share of the spacing.

Known limits: a contour must not be perpendicular to the sections; the chord of a branching is a
single straight cut, so a parent whose children are not separable by a line may give a strip with
self-intersections (reported with the pair of sections; tie lines or an extra contour help).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numba import njit

from app.formats.wireframes import edge_summary
from app.geo.checks import orientation_conflicts, self_intersections

CAPS = ("none", "flat", "point", "line", "extend")
# Share of the gap between sections by which the inner points of a branching chord are lowered.
CHORD_SAG = 0.25
# Tie ends snap to contour vertices within this share of the typical contour size.
TIE_SNAP = 1e-3
# Ridge of a "line" cap: share of the contour's extent along its main axis.
RIDGE_SHARE = 0.5


class StitchError(ValueError):
    """Message in Russian, shown to the user.

    `between` = (section a, section b): zero-based indices of the sections (as in
    `report["sections"]`) where the problem is; (k, k) means the cap of section k; None if unknown.
    """

    def __init__(self, message: str, between: tuple[int, int] | None = None):
        super().__init__(message)
        self.between = between


@dataclass
class StitchResult:
    vertices: np.ndarray   # (n, 3) float64; the input contour vertices first, in input order
    triangles: np.ndarray  # (m, 3) int32, consistently oriented, normals outward when closed
    report: dict = field(default_factory=dict)


# ---------------------------------------------------------------- planes and polygons


def _area_vector(points: np.ndarray) -> np.ndarray:
    """Newell's formula: the normal of the best-fit plane scaled by the polygon's area."""
    p = points - points.mean(axis=0)
    return 0.5 * np.cross(p, np.roll(p, -1, axis=0)).sum(axis=0)


def _plane_axes(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unit u, v with u × v = normal: a polygon counter-clockwise in (u, v) turns about the normal."""
    k = int(np.argmin(np.abs(normal)))
    seed = np.zeros(3)
    seed[k] = 1.0
    u = np.cross(seed, normal)
    u /= np.linalg.norm(u)
    return u, np.cross(normal, u)


def _signed_area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _centroid(q: np.ndarray) -> np.ndarray:
    """Area centroid of a 2D polygon (mean of the vertices if it has no area)."""
    o = q.mean(axis=0)
    p = q - o
    n = np.roll(p, -1, axis=0)
    w = p[:, 0] * n[:, 1] - n[:, 0] * p[:, 1]
    total = w.sum()
    scale = np.abs(p).max() ** 2
    if abs(total) <= 1e-12 * scale or scale == 0.0:
        return o
    return o + ((p + n) * w[:, None]).sum(axis=0) / (3.0 * total)


def _clean_polygon(polygon, what: str) -> np.ndarray:
    p = np.asarray(polygon, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] != 3:
        raise StitchError(f"{what}: ожидается массив координат (k, 3)")
    if not np.isfinite(p).all():
        raise StitchError(f"{what}: в координатах есть пропуски")
    if len(p) > 1 and np.array_equal(p[0], p[-1]):
        p = p[:-1]
    if len(p) < 3:
        raise StitchError(f"{what}: нужно не меньше трёх вершин")
    return p


@njit(cache=True)
def _ear_clip(xy):
    """Ear clipping of a counter-clockwise polygon; always returns n − 2 triangles.

    An ear is a strictly convex corner with no other vertex inside or on it. When there is none
    (collinear runs, a damaged outline) the least harmful corner is cut, so the outline's edges
    are still each used once — the cap stays watertight even where it is degenerate.
    """
    n = len(xy)
    out = np.empty((n - 2, 3), dtype=np.int64)
    nxt = np.empty(n, dtype=np.int64)
    prv = np.empty(n, dtype=np.int64)
    for i in range(n):
        nxt[i] = (i + 1) % n
        prv[i] = (i - 1) % n
    left = n
    cur = 0
    k = 0
    while left > 3:
        found = -1
        fallback = -1
        fallback_cross = -np.inf
        b = cur
        for _ in range(left):
            a, c = prv[b], nxt[b]
            cross = ((xy[b, 0] - xy[a, 0]) * (xy[c, 1] - xy[a, 1])
                     - (xy[b, 1] - xy[a, 1]) * (xy[c, 0] - xy[a, 0]))
            if cross > 0.0:
                blocked = False
                p = nxt[c]
                while p != a:
                    px, py = xy[p, 0], xy[p, 1]
                    d1 = (xy[b, 0] - xy[a, 0]) * (py - xy[a, 1]) - (xy[b, 1] - xy[a, 1]) * (px - xy[a, 0])
                    d2 = (xy[c, 0] - xy[b, 0]) * (py - xy[b, 1]) - (xy[c, 1] - xy[b, 1]) * (px - xy[b, 0])
                    d3 = (xy[a, 0] - xy[c, 0]) * (py - xy[c, 1]) - (xy[a, 1] - xy[c, 1]) * (px - xy[c, 0])
                    if d1 >= 0.0 and d2 >= 0.0 and d3 >= 0.0:
                        same = ((px == xy[a, 0] and py == xy[a, 1]) or (px == xy[b, 0] and py == xy[b, 1])
                                or (px == xy[c, 0] and py == xy[c, 1]))
                        if not same:
                            blocked = True
                            break
                    p = nxt[p]
                if not blocked:
                    found = b
                    break
            # Without a true ear: prefer a flat corner (zero-area triangle), then the least reflex.
            score = -abs(cross) if cross <= 0.0 else -np.inf
            if score > fallback_cross:
                fallback_cross = score
                fallback = b
            b = nxt[b]
        if found < 0:
            found = fallback if fallback >= 0 else cur
        a, c = prv[found], nxt[found]
        out[k, 0], out[k, 1], out[k, 2] = a, found, c
        k += 1
        nxt[a] = c
        prv[c] = a
        cur = c
        left -= 1
    out[k, 0], out[k, 1], out[k, 2] = prv[cur], cur, nxt[cur]
    return out


def _covers_outline(tris: np.ndarray, n: int) -> bool:
    """n − 2 triangles that use every outline edge i→i+1 once and no directed edge twice."""
    if tris.shape != (n - 2, 3) or tris.min() < 0 or tris.max() >= n:
        return False
    t = tris.astype(np.int64)
    keys = np.concatenate([t[:, 0] * n + t[:, 1], t[:, 1] * n + t[:, 2], t[:, 2] * n + t[:, 0]])
    if len(np.unique(keys)) != len(keys):
        return False
    i = np.arange(n, dtype=np.int64)
    return bool(np.isin(i * n + (i + 1) % n, keys).all())


def _triangulate(q: np.ndarray) -> np.ndarray:
    """Triangles (indices into q) of a simple 2D polygon, wound like the polygon itself.

    manifold3d's triangulator is used when its answer keeps every vertex of the outline (collinear
    ones too: the strips next to a cap use them); otherwise the ear clipping above.
    """
    n = len(q)
    if n == 3:
        return np.array([[0, 1, 2]], dtype=np.int64)
    ccw = _signed_area(q) >= 0.0
    p = np.ascontiguousarray(q if ccw else q[::-1], dtype=np.float64)
    p = p - p.mean(axis=0)
    tris = None
    try:
        import manifold3d

        got = np.asarray(manifold3d.triangulate([p]), dtype=np.int64).reshape(-1, 3)
        if _covers_outline(got, n):
            tris = got
    except Exception:  # noqa: BLE001 — any failure of the library falls back to ear clipping
        tris = None
    if tris is None:
        tris = _ear_clip(p)
    if not ccw:
        tris = (n - 1 - tris)[:, ::-1]
    return tris


def cap_triangles(polygon: np.ndarray) -> np.ndarray:
    """(k − 2, 3) indices into `polygon`: its triangulation in the best-fit plane.

    Works for non-convex polygons; the triangles are wound like the polygon. A repeated last
    vertex must not be passed (indices refer to the array as given).
    """
    p = np.asarray(polygon, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] != 3 or len(p) < 3:
        raise StitchError("Полигон: нужно не меньше трёх вершин (k, 3)")
    area = _area_vector(p)
    length = float(np.linalg.norm(area))
    if length == 0.0:
        raise StitchError("Полигон вырожден: у него нулевая площадь")
    normal = area / length
    u, v = _plane_axes(normal)
    c = p - p.mean(axis=0)
    return _triangulate(np.column_stack([c @ u, c @ v])).astype(np.int32)


# ---------------------------------------------------------------- strip between two contours (numba)


@njit(cache=True)
def _span_matrix(a, b, h):
    """Length of the span a_i–b_j for contours brought to a common centre and size, h apart."""
    n, m = len(a), len(b)
    d = np.empty((n, m), dtype=np.float64)
    hh = h * h
    for i in range(n):
        for j in range(m):
            dx = a[i, 0] - b[j, 0]
            dy = a[i, 1] - b[j, 1]
            d[i, j] = math.sqrt(dx * dx + dy * dy + hh)
    return d


@njit(cache=True)
def _dp_fill(d, i0, di, j0, dj, lo, hi, cost, back):
    """Cheapest monotone path from node (i0, j0) to (i0 + di, j0 + dj); returns its cost.

    Row r is vertex (i0 + r) mod n of the first contour, column j is vertex j mod m of the second
    (columns are absolute, 0..2m). Only columns lo[r]..hi[r] of each row are visited. `back` gets
    1 where the path came along the first contour (from the row above) and 2 along the second.
    """
    n, m = d.shape
    last = j0 + dj
    for r in range(di + 1):
        ia = (i0 + r) % n
        left = lo[r] if lo[r] > j0 else j0
        right = hi[r] if hi[r] < last else last
        up_lo = j0
        up_hi = j0 - 1
        if r > 0:
            up_lo = lo[r - 1] if lo[r - 1] > j0 else j0
            up_hi = hi[r - 1] if hi[r - 1] < last else last
        for j in range(left, right + 1):
            if r == 0 and j == j0:
                cost[0, j] = 0.0
                back[0, j] = 0
                continue
            best = np.inf
            move = 0
            if up_lo <= j <= up_hi:
                best = cost[r - 1, j]
                move = 1
            if j > left and cost[r, j - 1] < best:
                best = cost[r, j - 1]
                move = 2
            cost[r, j] = best + d[ia, j % m]
            back[r, j] = move
    return cost[di, last]


@njit(cache=True)
def _dp_bounds(back, di, j0, dj, first, last):
    """First and last column of the found path in every row."""
    r = di
    j = j0 + dj
    last[r] = j
    while r > 0 or j > j0:
        if back[r, j] == 1:
            first[r] = j
            r -= 1
            last[r] = j
        else:
            j -= 1
    first[0] = j0


@njit(cache=True)
def _dp_trace(back, i0, di, j0, dj, n, m, la, lb, tris, k0):
    """Triangles of the found path into tris[k0 : k0 + di + dj] (vertex numbers from la, lb).

    With both contours counter-clockwise seen from the second one's side, the triangles
    (a_i, a_i+1, b_j) and (b_j+1, b_j, a_i) look away from the contours' interior.
    """
    r = di
    j = j0 + dj
    k = k0 + di + dj - 1
    turns = -1
    move = 0
    while r > 0 or j > j0:
        if back[r, j] != move:
            move = back[r, j]
            turns += 1
        if back[r, j] == 1:
            tris[k, 0] = la[(i0 + r - 1) % n]
            tris[k, 1] = la[(i0 + r) % n]
            tris[k, 2] = lb[j % m]
            r -= 1
        else:
            tris[k, 0] = lb[j % m]
            tris[k, 1] = lb[(j - 1) % m]
            tris[k, 2] = la[(i0 + r) % n]
            j -= 1
        k -= 1
    return turns


@njit(cache=True)
def _ring_path(d, i0, j0, la, lb, tris):
    """Cheapest path once round both contours from node (i0, j0) that turns at least three times.

    A path with fewer turns walks one whole contour from a single vertex of the other; it passes
    the same span twice, so the "strip" is two cones glued along an edge. The usual search finds
    such a path only for very dissimilar contours; then this one is used. State: the direction
    of the last step and the number of turns so far (0..3). Returns the cost (inf: no path).
    """
    n, m = d.shape
    cost = np.full((n + 1, m + 1, 8), np.inf)
    back = np.zeros((n + 1, m + 1, 8), dtype=np.int8)  # previous state + 1
    for r in range(n + 1):
        ia = (i0 + r) % n
        for j in range(m + 1):
            if r == 0 and j == 0:
                continue
            w = d[ia, (j0 + j) % m]
            for move in range(2):  # 0: along the first contour (from the row above), 1: along the second
                pr = r - 1 if move == 0 else r
                pj = j if move == 0 else j - 1
                if pr < 0 or pj < 0:
                    continue
                if pr == 0 and pj == 0:
                    cost[r, j, move * 4] = w
                    back[r, j, move * 4] = 0
                    continue
                for state in range(8):
                    c = cost[pr, pj, state]
                    if c == np.inf:
                        continue
                    turns = state % 4
                    if state // 4 != move and turns < 3:
                        turns += 1
                    new = move * 4 + turns
                    if c + w < cost[r, j, new]:
                        cost[r, j, new] = c + w
                        back[r, j, new] = state + 1
    state = 3 if cost[n, m, 3] <= cost[n, m, 7] else 7
    total = cost[n, m, state]
    if total == np.inf:
        return total
    r, j = n, m
    k = n + m - 1
    while r > 0 or j > 0:
        before = back[r, j, state] - 1
        if state // 4 == 0:
            tris[k, 0] = la[(i0 + r - 1) % n]
            tris[k, 1] = la[(i0 + r) % n]
            tris[k, 2] = lb[(j0 + j) % m]
            r -= 1
        else:
            tris[k, 0] = lb[(j0 + j) % m]
            tris[k, 1] = lb[(j0 + j - 1) % m]
            tris[k, 2] = la[(i0 + r) % n]
            j -= 1
        state = before
        k -= 1
    return total


@njit(cache=True)
def _best_start(d, cost, back):
    """The vertex s of the second contour to pair with vertex 0 of the first for the cheapest strip.

    Exact search over all starts (Fuchs, Kedem, Uselton 1977): the paths for starts a < s < b lie
    between the paths for a and b, so each is searched only inside that band.
    """
    n, m = d.shape
    first = np.empty((m + 1, n + 1), dtype=np.int64)
    last = np.empty((m + 1, n + 1), dtype=np.int64)
    lo = np.zeros(n + 1, dtype=np.int64)
    hi = np.full(n + 1, m, dtype=np.int64)
    best = _dp_fill(d, 0, n, 0, m, lo, hi, cost, back)
    best_s = 0
    _dp_bounds(back, n, 0, m, first[0], last[0])
    for r in range(n + 1):
        first[m, r] = first[0, r] + m
        last[m, r] = last[0, r] + m
    stack = np.empty((m + 2, 2), dtype=np.int64)
    top = 0
    stack[0, 0], stack[0, 1] = 0, m
    top = 1
    while top > 0:
        top -= 1
        a, b = stack[top, 0], stack[top, 1]
        if b - a < 2:
            continue
        s = (a + b) // 2
        for r in range(n + 1):
            lo[r] = first[a, r]
            hi[r] = last[b, r]
        c = _dp_fill(d, 0, n, s, m, lo, hi, cost, back)
        _dp_bounds(back, n, s, m, first[s], last[s])
        if c < best:
            best = c
            best_s = s
        stack[top, 0], stack[top, 1] = a, s
        stack[top + 1, 0], stack[top + 1, 1] = s, b
        top += 2
    return best_s


def _match_coords(q: np.ndarray) -> tuple[np.ndarray, float]:
    """The contour moved to its centre and scaled to unit size (both weighted by edge length)."""
    nxt = np.roll(q, -1, axis=0)
    mid = (q + nxt) / 2.0
    w = np.linalg.norm(nxt - q, axis=1)
    if w.sum() <= 0.0:
        w = np.ones(len(q))
    c = (mid * w[:, None]).sum(axis=0) / w.sum()
    size = math.sqrt(float((w * ((mid - c) ** 2).sum(axis=1)).sum() / w.sum()))
    if size <= 0.0:
        size = 1.0
    return (q - c) / size, size


def _tie_segments(ties: list[tuple[int, int]], n: int, m: int) -> list[tuple[int, int, int, int]] | None:
    """Pieces (i0, di, j0, dj) of the path between consecutive ties; None if the ties cross."""
    i_first, j_first = min(ties)
    order = sorted(set(ties), key=lambda t: (t[0], (t[1] - j_first) % m))
    out = []
    walked = 0
    for k, (i, j) in enumerate(order):
        if k + 1 < len(order):
            ni, nj = order[k + 1]
            di, dj = ni - i, (nj - j) % m
        else:
            di, dj = i_first + n - i, m - walked
        walked += dj
        if dj < 0 or walked > m:
            return None
        out.append((i, di, j, dj))
    return out


def _strip(pa: np.ndarray, pb: np.ndarray, la: np.ndarray, lb: np.ndarray, gap: float,
           ties: list[tuple[int, int]] | None = None) -> np.ndarray | None:
    """Triangles between contour a (2D points pa, vertex numbers la) and contour b above it.

    Both contours are counter-clockwise in the projection. `ties` are pairs (index in a, index
    in b) that must be edges of the strip. None is returned when the ties cross each other.
    """
    n, m = len(la), len(lb)
    qa, sa = _match_coords(pa)
    qb, sb = _match_coords(pb)
    d = _span_matrix(np.ascontiguousarray(qa), np.ascontiguousarray(qb), abs(gap) / ((sa + sb) / 2.0))
    cost = np.empty((n + 1, 2 * m + 1), dtype=np.float64)
    back = np.empty((n + 1, 2 * m + 1), dtype=np.int8)
    if ties:
        segments = _tie_segments(ties, n, m)
        if segments is None:
            return None
    else:
        segments = [(0, n, int(_best_start(d, cost, back)), m)]
    tris = np.empty((n + m, 3), dtype=np.int64)
    k = 0
    for i0, di, j0, dj in segments:
        if di == 0 and dj == 0:
            continue
        lo = np.full(di + 1, j0, dtype=np.int64)
        hi = np.full(di + 1, j0 + dj, dtype=np.int64)
        _dp_fill(d, i0, di, j0, dj, lo, hi, cost, back)
        turns = _dp_trace(back, i0, di, j0, dj, n, m, la, lb, tris, k)
        if di == n and dj == m and turns < 3:
            # Two cones instead of a strip (see _ring_path): search again with the turns counted;
            # without ties, from a spread of starts.
            starts = [j0] if ties else sorted({j0, *np.linspace(0, m - 1, min(m, 16)).astype(int).tolist()})
            best = np.inf
            trial = np.empty((n + m, 3), dtype=np.int64)
            for start in starts:
                total = _ring_path(d, i0, start, la, lb, trial)
                if total < best:
                    best = total
                    tris[k:k + n + m] = trial
        k += di + dj
    return tris[:k]


# ---------------------------------------------------------------- the series


class _Series:
    """Working state of one stitching: vertices, contours as loops of vertex numbers, triangles."""

    def __init__(self, contours: list[np.ndarray]):
        self.counts = [len(c) for c in contours]
        self.starts = np.concatenate([[0], np.cumsum(self.counts)]).astype(np.int64)
        self.given = np.concatenate(contours, axis=0)  # the input vertices, untouched
        self.centre = (self.given.min(axis=0) + self.given.max(axis=0)) / 2.0
        self.local = self.given - self.centre          # everything is computed in these
        self.added: list[np.ndarray] = []              # new vertices, relative to the centre
        self._all: np.ndarray | None = self.local
        self.loops: list[list[int]] = []               # loop id → vertex numbers, counter-clockwise
        self.family: list[list[int]] = []              # contour → ids of its loop and of its parts
        self.groups: list[dict] = []                   # {"between": (a, b), "tris": array}
        self.warnings: list[str] = []

    def add_vertex(self, point: np.ndarray) -> int:
        self.added.append(np.asarray(point, dtype=np.float64))
        self._all = None
        return len(self.local) + len(self.added) - 1

    def xyz(self, numbers) -> np.ndarray:
        if self._all is None:
            self._all = np.vstack([self.local, np.asarray(self.added)])
        return self._all[np.asarray(numbers, dtype=np.int64)]

    def flat(self, numbers) -> np.ndarray:
        """Projection of the vertices along the common normal."""
        p = self.xyz(numbers)
        return np.column_stack([p @ self.u, p @ self.v])

    def emit(self, between: tuple[int, int], tris: np.ndarray) -> None:
        self.groups.append({"between": between, "tris": np.asarray(tris, dtype=np.int64).reshape(-1, 3)})

    # ---- branching

    def insert_on_edge(self, contour: int, g1: int, g2: int, w: int) -> None:
        """Puts vertex w between g1 and g2 in every loop of the contour that has this edge."""
        for loop_id in self.family[contour]:
            loop = self.loops[loop_id]
            if g1 not in loop:
                continue
            i = loop.index(g1)
            k = len(loop)
            if loop[(i + 1) % k] == g2:
                loop.insert(i + 1, w)
            elif loop[(i - 1) % k] == g2:
                loop.insert(i, w)

    def cut(self, contour: int, loop_id: int, children: list[int], toward: float, gap: float,
            between: tuple[int, int], depth: int = 0) -> list[tuple[int, int]]:
        """Cuts the loop into one part per child; returns [(loop id of the part, child contour)].

        The children are split into two groups by the straight line with the widest clearance
        between them; the parent is cut along the corresponding line. More than two children:
        each part is cut again (`depth` grows; the saddle of each next cut is half as deep).
        """
        if len(children) == 1:
            return [(loop_id, children[0])]
        shapes = [self.flat(self.loops[c]) for c in children]
        cents = np.array([_centroid(shape) for shape in shapes])
        q = self.flat(self.loops[loop_id])
        own = _centroid(q)

        # Candidate directions: between every two children and across them. The best one has the
        # widest empty band between the children's extents; the cut goes through its middle.
        best = None  # (width of the band, axis, level)
        farthest = (0.0, None)
        for i in range(len(children)):
            for j in range(i + 1, len(children)):
                d = cents[j] - cents[i]
                length = float(np.linalg.norm(d))
                if length > farthest[0]:
                    farthest = (length, d / max(length, 1e-300))
                if length == 0.0:
                    continue
                d = d / length
                for axis in (d, np.array([-d[1], d[0]])):
                    spans = sorted((float((shape @ axis).min()), float((shape @ axis).max())) for shape in shapes)
                    reach = spans[0][1]
                    for lo, hi in spans[1:]:
                        if lo - reach > 0 and (best is None or lo - reach > best[0]):
                            best = (lo - reach, axis, (lo + reach) / 2.0)
                        reach = max(reach, hi)
        if best is None:
            # The children overlap in every direction: split their centres at the widest gap.
            axis = farthest[1]
            if axis is None:
                raise StitchError(
                    f"Разветвление между разрезами {between[0] + 1} и {between[1] + 1}: контуры-ветви совпадают по "
                    "положению, их не удаётся разделить", between)
            t = np.sort(cents @ axis)
            i = int(np.argmax(np.diff(t)))
            best = (0.0, axis, (t[i] + t[i + 1]) / 2.0)
        _, axis, level = best
        t = cents @ axis
        groups = ([c for c, x in zip(children, t) if x < level], [c for c, x in zip(children, t) if x >= level])
        # The children's extent across the cut is mapped onto the parent's: a body that drifts or
        # tapers between the sections is cut in the corresponding place, not where the children
        # happen to project.
        c_lo = min(float((shape @ axis).min()) for shape in shapes)
        c_hi = max(float((shape @ axis).max()) for shape in shapes)
        p_lo, p_hi = float((q @ axis).min()), float((q @ axis).max())
        child_level = level
        level = p_lo + (level - c_lo) / (c_hi - c_lo) * (p_hi - p_lo)
        side = q @ axis - level
        if (side >= 0).all() or (side < 0).all():
            level = float(own @ axis)
            side = q @ axis - level
        along = np.array([-axis[1], axis[0]])
        loop = list(self.loops[loop_id])
        k = len(loop)
        size = float(np.ptp(q, axis=0).max())
        crossings = []  # (position along the cut, edge index, share of the edge)
        for i in range(k):
            j = (i + 1) % k
            if (side[i] >= 0) != (side[j] >= 0):
                share = side[i] / (side[i] - side[j])
                crossings.append((float((q[i] + share * (q[j] - q[i])) @ along), i, share))
        crossings.sort()
        if len(crossings) < 2:
            raise StitchError(
                f"Разветвление между разрезами {between[0] + 1} и {between[1] + 1}: не удалось разделить контур "
                "на части по числу ветвей", between)
        # A line may cross a non-convex contour several times: take the inner piece nearest to the centre.
        ref = float(own @ along)
        pieces = [(crossings[i], crossings[i + 1]) for i in range(0, len(crossings) - 1, 2)]
        start, end = min(pieces, key=lambda p: (max(p[0][0] - ref, ref - p[1][0], 0.0), p[0][0] - p[1][0]))

        ends = []
        for _, i, share in (start, end):
            g1, g2 = loop[i], loop[(i + 1) % k]
            p1, p2 = self.xyz([g1, g2])
            edge = float(np.linalg.norm(q[(i + 1) % k] - q[i]))
            if share * edge <= 1e-7 * size:
                ends.append(g1)
            elif (1.0 - share) * edge <= 1e-7 * size:
                ends.append(g2)
            else:
                w = self.add_vertex(p1 + share * (p2 - p1))
                self.insert_on_edge(contour, g1, g2, w)
                ends.append(w)
        x, y = ends
        if x == y:
            raise StitchError(
                f"Разветвление между разрезами {between[0] + 1} и {between[1] + 1}: линия раздела вырождена, "
                "контур не удаётся разделить", between)

        # Inner points of the chord, about one per typical edge. They are moved a share of the way
        # towards the children (a saddle): down along the normal and sideways to where the chord
        # would arrive between the children — the middle of the band between the two groups, as
        # long as the groups are. Each tube leans from its part to its child; a saddle left right
        # under the chord would be crossed by a tube leaning over it.
        loop = self.loops[loop_id]
        k = len(loop)
        px, py = self.xyz([x, y])
        ring = self.xyz(loop)
        typical = float(np.linalg.norm(np.roll(ring, -1, axis=0) - ring, axis=1).mean())
        inner_count = int(np.clip(round(float(np.linalg.norm(py - px)) / max(typical, 1e-12)) - 1, 1, 200))
        share = CHORD_SAG / 2.0 ** depth
        reach = [[f(float(f(shape @ along)) for c, shape in zip(children, shapes) if c in group) for f in (min, max)]
                 for group in groups]
        target_lo = (reach[0][0] + reach[1][0]) / 2.0
        target_hi = (reach[0][1] + reach[1][1]) / 2.0
        qx, qy = self.flat([x, y])
        inner = []
        for i in range(1, inner_count + 1):
            w = i / (inner_count + 1.0)
            image = child_level * axis + (target_lo + w * (target_hi - target_lo)) * along
            move = share * (image - (qx + w * (qy - qx)))
            inner.append(self.add_vertex(px + w * (py - px) + move[0] * self.u + move[1] * self.v
                                         + toward * share * gap * self.normal))

        ix, iy = loop.index(x), loop.index(y)
        arc_xy = [loop[(ix + i) % k] for i in range((iy - ix) % k + 1)]
        arc_yx = [loop[(iy + i) % k] for i in range((ix - iy) % k + 1)]
        part_xy = arc_xy + inner[::-1]   # the chord is walked y → x here and x → y in the other part
        part_yx = arc_yx + inner
        between_xy = arc_xy[1:-1]
        if between_xy:
            positive = float((self.flat(between_xy) @ axis - level).mean()) >= 0.0
        else:
            positive = float((self.flat(arc_yx[1:-1]) @ axis - level).mean()) < 0.0
        parts = (part_yx, part_xy) if positive else (part_xy, part_yx)  # groups[0] is on the negative side

        out = []
        new_ids = []
        for part in parts:
            self.loops.append(part)
            self.family[contour].append(len(self.loops) - 1)
            new_ids.append(len(self.loops) - 1)
        for part_id, group in zip(new_ids, groups):
            out += self.cut(contour, part_id, group, toward, gap, between, depth + 1)
        return out

    # ---- caps

    def plane_point(self, loop: list[int], q: np.ndarray) -> np.ndarray:
        """The 2D point q of the projection put on the mean level of the loop."""
        level = float((self.xyz(loop) @ self.normal).mean())
        return q[0] * self.u + q[1] * self.v + level * self.normal

    def cap(self, kind: str, loop: list[int], outward: float, spacing: float, distance: float,
            section: int) -> None:
        if kind == "none":
            return
        between = (section, section)
        ids = np.asarray(loop, dtype=np.int64)
        q = self.flat(loop)
        reach = outward * distance * spacing * self.normal
        if kind == "flat":
            tris = ids[_triangulate(q)]
            self.emit(between, tris if outward > 0 else tris[:, ::-1])
        elif kind == "point":
            apex = self.add_vertex(self.plane_point(loop, _centroid(q)) + reach)
            nxt = np.roll(ids, -1)
            top = np.full(len(ids), apex, dtype=np.int64)
            self.emit(between, np.column_stack([ids, nxt, top] if outward > 0 else [nxt, ids, top]))
        elif kind == "extend":
            ring = self.xyz(loop) + reach
            copy = np.array([self.add_vertex(p) for p in ring], dtype=np.int64)
            k = len(ids)
            i = np.arange(k)
            j = (i + 1) % k
            lower, upper = (ids, copy) if outward > 0 else (copy, ids)
            self.emit(between, np.vstack([np.column_stack([lower[i], lower[j], upper[i]]),
                                          np.column_stack([lower[j], upper[j], upper[i]])]))
            tris = copy[_triangulate(q)]
            self.emit(between, tris if outward > 0 else tris[:, ::-1])
        elif kind == "line":
            # Ridge along the main axis of the contour, through its centre, shortened.
            c = _centroid(q)
            axis = np.linalg.svd(q - q.mean(axis=0), full_matrices=False)[2][0]
            t = (q - c) @ axis
            pieces = int(np.clip(len(ids) // 4, 1, 50))
            stops = np.linspace(t.min() * RIDGE_SHARE, t.max() * RIDGE_SHARE, pieces + 1)
            ridge = [self.add_vertex(self.plane_point(loop, c + s * axis) + reach) for s in stops]
            # The ridge as a contour of no width: there along one side and back along the other.
            both = np.array(ridge + ridge[-2:0:-1], dtype=np.int64)
            gap = distance * spacing
            if outward > 0:
                tris = _strip(q, self.flat(both), ids, both, gap)
            else:
                tris = _strip(self.flat(both), q, both, ids, gap)
            # An end of the ridge reached from a single contour vertex gives the same triangle on
            # both sides of the ridge: such a fold has no volume and is dropped (the ridge gets shorter).
            _, inverse, counts = np.unique(np.sort(tris, axis=1), axis=0, return_inverse=True, return_counts=True)
            self.emit(between, tris[counts[inverse.reshape(-1)] == 1])


def _sections(pos: np.ndarray, tolerance: float | None) -> tuple[list[list[int]], float]:
    """Contours grouped by position along the normal; the tolerance used."""
    order = np.argsort(pos, kind="stable")
    gaps = np.diff(pos[order])
    if tolerance is None:
        widest = float(gaps.max()) if len(gaps) else 0.0
        real = gaps[gaps > max(1e-6, 0.05 * widest)]
        tolerance = max(1e-6, 0.1 * float(np.median(real))) if len(real) else 1e-6
    out = [[int(order[0])]]
    for i, gap in enumerate(gaps):
        if gap < tolerance:
            out[-1].append(int(order[i + 1]))
        else:
            out.append([int(order[i + 1])])
    return [sorted(s) for s in out], float(tolerance)


def _links(cent: np.ndarray, lower: list[int], upper: list[int]) -> list[tuple[list[int], list[int]]]:
    """Which contours of two neighbouring sections are stitched: [(lower contours, upper contours)].

    Equal counts: one to one, the pairing with the smallest total distance between centres.
    Otherwise every contour of the smaller group first gets one of the larger group (same rule),
    and each remaining contour joins the nearest one of the smaller group (a branching).
    """
    from scipy.optimize import linear_sum_assignment

    small, large = (lower, upper) if len(lower) <= len(upper) else (upper, lower)
    dist = np.linalg.norm(cent[small][:, None, :] - cent[large][None, :, :], axis=2)
    rows, cols = linear_sum_assignment(dist)
    children: dict[int, list[int]] = {s: [] for s in small}
    for r, c in zip(rows, cols):
        children[small[r]].append(large[c])
    for c in set(range(len(large))) - set(cols.tolist()):
        children[small[int(np.argmin(dist[:, c]))]].append(large[c])
    out = []
    for s in small:
        kids = sorted(children[s])
        out.append(([s], kids) if small is lower else (kids, [s]))
    return out


def _edge_faults(tris: np.ndarray, vertex_count: int) -> np.ndarray:
    """Triangles with an edge that is open, shared by more than two, or walked twice the same way."""
    t = tris.astype(np.int64)
    m = len(t)
    a = np.concatenate([t[:, 0], t[:, 1], t[:, 2]])
    b = np.concatenate([t[:, 1], t[:, 2], t[:, 0]])
    owner = np.tile(np.arange(m), 3)
    plain = np.minimum(a, b) * vertex_count + np.maximum(a, b)
    _, inverse, counts = np.unique(plain, return_inverse=True, return_counts=True)
    bad = counts[inverse] != 2
    _, inverse, counts = np.unique(a * vertex_count + b, return_inverse=True, return_counts=True)
    bad |= counts[inverse] > 1
    return np.unique(owner[bad])


@njit(cache=True)
def _pierced(v, ta, tb, tol):
    """An edge of triangle ta goes through triangle tb: its ends are on opposite sides of tb's
    plane, farther than tol from it, and the crossing point is inside tb (or on its outline)."""
    a0, a1, a2 = v[tb[0]], v[tb[1]], v[tb[2]]
    e1 = a1 - a0
    e2 = a2 - a0
    n = np.array([e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]])
    size = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
    if size == 0.0:
        return False
    n = n / size
    d = np.empty(3)
    for k in range(3):
        p = v[ta[k]]
        d[k] = (p[0] - a0[0]) * n[0] + (p[1] - a0[1]) * n[1] + (p[2] - a0[2]) * n[2]
    for k in range(3):
        dp, dq = d[k], d[(k + 1) % 3]
        if not ((dp > tol and dq < -tol) or (dp < -tol and dq > tol)):
            continue
        p, q = v[ta[k]], v[ta[(k + 1) % 3]]
        x = p + (dp / (dp - dq)) * (q - p)
        inside = True
        for c in range(3):
            b0, b1 = v[tb[c]], v[tb[(c + 1) % 3]]
            f = b1 - b0
            g = x - b0
            w = ((f[1] * g[2] - f[2] * g[1]) * n[0] + (f[2] * g[0] - f[0] * g[2]) * n[1]
                 + (f[0] * g[1] - f[1] * g[0]) * n[2])
            if w < -1e-9 * size:
                inside = False
                break
        if inside:
            return True
    return False


@njit(cache=True)
def _first_crossing(v, t, lo, hi, order, axis, tol):
    """(i, j): the first pair of triangles without a common vertex that go through each other, or (−1, −1).

    Sweep along `axis` over triangles sorted by the lower bound of their boxes. Unlike the fast
    grid check this one is not misled by triangles that lie in one plane or touch a plane with an
    edge (walls continued by an extension, a vertex inserted on a straight edge): there the
    crossing point is a ratio of rounding errors.
    """
    m = len(t)
    o1, o2 = (axis + 1) % 3, (axis + 2) % 3
    for a in range(m):
        i = order[a]
        for b in range(a + 1, m):
            j = order[b]
            if lo[j, axis] > hi[i, axis]:
                break
            if lo[j, o1] > hi[i, o1] or lo[i, o1] > hi[j, o1] or lo[j, o2] > hi[i, o2] or lo[i, o2] > hi[j, o2]:
                continue
            shared = False
            for x in range(3):
                for y in range(3):
                    if t[i, x] == t[j, y]:
                        shared = True
            if shared:
                continue
            if _pierced(v, t[i], t[j], tol) or _pierced(v, t[j], t[i], tol):
                return i, j
    return -1, -1


def _crossing(v: np.ndarray, t: np.ndarray) -> tuple[int, int]:
    """Indices of two triangles that really cross (see _first_crossing), or (−1, −1)."""
    corners = v[t]
    lo, hi = corners.min(axis=1), corners.max(axis=1)
    extent = v.max(axis=0) - v.min(axis=0)
    axis = int(np.argmax(extent))
    order = np.argsort(lo[:, axis], kind="stable")
    i, j = _first_crossing(np.ascontiguousarray(v), np.ascontiguousarray(t, dtype=np.int64), lo, hi, order, axis,
                           1e-8 * float(np.linalg.norm(extent)))
    return int(i), int(j)


def _signed_volume(v: np.ndarray, t: np.ndarray) -> float:
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0


def _where(between: tuple[int, int]) -> str:
    if between[0] == between[1]:
        return f"На торце у разреза {between[0] + 1}"
    return f"Между контурами разрезов {between[0] + 1} и {between[1] + 1}"


def stitch_contours(contours: list[np.ndarray], ties: list[np.ndarray] = (), *, start_cap: str = "flat",
                    end_cap: str = "flat", cap_distance: float = 0.5, section_tolerance: float | None = None,
                    tie_tolerance: float | None = None, check: bool = True, progress=None) -> StitchResult:
    """A triangulated solid from closed contours drawn on a series of sections.

    contours            (k, 3) arrays, closed without a repeated last vertex (a repeated one is
                        dropped), any winding, any orientation of the sections.
    ties                (2, 3) arrays: a tie line between vertices of two contours that are
                        stitched to each other; it becomes an edge of the result.
    start_cap, end_cap  "none" | "flat" | "point" | "line" | "extend" for the first and the last
                        section. The first section is the end of the series nearer to the first
                        contour of the list.
    cap_distance        length of "point", "line" and "extend" caps as a share of the distance
                        to the neighbouring section.
    section_tolerance   contours closer than this along the common normal are on one section
                        (default: a tenth of the median spacing, at least 1e-6).
    tie_tolerance       how far a tie end may be from a contour vertex (default: 1e-3 of the
                        typical contour size).
    check               look for self-intersections; StitchError names the pair of sections.
    progress            callback (fraction, message).

    The vertices of the contours come first in the result, in input order and bit-identical;
    new vertices (caps, branch cuts) follow.
    """
    def tell(fraction: float, message: str) -> None:
        if progress is not None:
            progress(float(fraction), message)

    for name, value in (("start_cap", start_cap), ("end_cap", end_cap)):
        if value not in CAPS:
            raise StitchError(f"Неизвестный способ замыкания торца «{value}» ({name}): допустимы {', '.join(CAPS)}")
    if not (cap_distance > 0 and math.isfinite(cap_distance)):
        raise StitchError("Длина замыкания торца (доля расстояния между разрезами) должна быть больше нуля")
    tell(0.0, "Подготовка контуров")
    clean = [_clean_polygon(c, f"Контур {i + 1}") for i, c in enumerate(contours)]
    if len(clean) < 2:
        raise StitchError("Для сшивки нужны контуры хотя бы на двух разрезах")
    s = _Series(clean)
    count = len(clean)

    # ---- common normal, sections
    normals = np.empty((count, 3))
    for i in range(count):
        area = _area_vector(s.xyz(np.arange(s.starts[i], s.starts[i + 1])))
        length = float(np.linalg.norm(area))
        if length <= 1e-12 * max(float(np.ptp(clean[i], axis=0).max()) ** 2, 1e-300):
            raise StitchError(f"Контур {i + 1} вырожден: у него нулевая площадь")
        normals[i] = area / length
    reference = normals[0]
    normals[normals @ reference < 0] *= -1.0
    normal = normals.mean(axis=0)
    if float(np.linalg.norm(normal)) < 1e-9:
        raise StitchError("Плоскости контуров слишком различаются: общее направление разрезов не определяется")
    normal /= np.linalg.norm(normal)
    cent3 = np.array([s.xyz(np.arange(s.starts[i], s.starts[i + 1])).mean(axis=0) for i in range(count)])
    pos = cent3 @ normal
    if pos[0] > pos[-1] or (pos[0] == pos[-1] and normal[int(np.argmax(np.abs(normal)))] < 0):
        normal, pos = -normal, -pos
    for i in range(count):
        if abs(float(normals[i] @ normal)) < 0.5:
            raise StitchError(f"Контур {i + 1} лежит не в плоскости разрезов: он развёрнут к остальным больше чем на 60°")
    s.normal = normal
    s.u, s.v = _plane_axes(normal)
    sections, section_tolerance = _sections(pos, section_tolerance)
    if len(sections) < 2:
        raise StitchError("Все контуры лежат на одном разрезе: для сшивки нужны контуры хотя бы на двух разрезах")
    level = np.array([float(pos[sec].mean()) for sec in sections])
    section_of = np.empty(count, dtype=np.int64)
    for k, sec in enumerate(sections):
        section_of[sec] = k

    # ---- loops: every contour counter-clockwise as seen from the tip of the normal
    cent2 = np.empty((count, 2))
    sizes = np.empty(count)
    for i in range(count):
        ids = list(range(int(s.starts[i]), int(s.starts[i + 1])))
        q = s.flat(ids)
        if _signed_area(q) < 0:
            ids.reverse()
        s.loops.append(ids)
        s.family.append([i])
        cent2[i] = _centroid(q)
        sizes[i] = float(np.linalg.norm(np.ptp(clean[i], axis=0)))
        if (np.linalg.norm(np.roll(q, -1, axis=0) - q, axis=1) == 0).any():
            s.warnings.append(f"Контур {i + 1}: есть совпадающие соседние вершины")

    # ---- which contours are stitched; branch cuts (all of them before any strip is built)
    jobs = []        # {"between", "lower": loop id, "upper": loop id, "a": contour, "b": contour, "pair": index}
    pairs = []       # report entries
    for k in range(len(sections) - 1):
        gap = float(level[k + 1] - level[k])
        for lower, upper in _links(cent2, sections[k], sections[k + 1]):
            pairs.append({"sections": [k, k + 1], "a": lower, "b": upper, "triangles": 0,
                          "branch": len(lower) + len(upper) > 2})
            if len(lower) == 1:
                parts = s.cut(lower[0], lower[0], upper, 1.0, gap, (k, k + 1))
                found = [(part, child, lower[0], child) for part, child in parts]
            else:
                parts = s.cut(upper[0], upper[0], lower, -1.0, gap, (k, k + 1))
                found = [(child, part, child, upper[0]) for part, child in parts]
            for lo_id, up_id, a, b in found:
                jobs.append({"between": (k, k + 1), "lower": lo_id, "upper": up_id, "a": a, "b": b,
                             "pair": len(pairs) - 1, "gap": gap, "ties": []})
    job_of = {(job["a"], job["b"]): job for job in jobs}

    # ---- tie lines
    tolerance = float(tie_tolerance) if tie_tolerance is not None else TIE_SNAP * float(np.median(sizes))
    ties_used = 0
    if len(ties):
        from scipy.spatial import cKDTree

        tree = cKDTree(s.local)
        for number, tie in enumerate(ties, start=1):
            line = np.asarray(tie, dtype=np.float64)
            if line.shape != (2, 3) or not np.isfinite(line).all():
                raise StitchError(f"Тай-линия {number}: ожидаются две точки (2, 3)")
            dist, hit = tree.query(line - s.centre)
            for end in range(2):
                if dist[end] > tolerance:
                    raise StitchError(
                        f"Тай-линия {number}: её конец не совпадает ни с одной вершиной контуров "
                        f"(до ближайшей {dist[end]:.3f} м, допуск {tolerance:.3f} м)")
            ca, cb = (int(np.searchsorted(s.starts, h, side="right")) - 1 for h in hit)
            ga, gb = int(hit[0]), int(hit[1])
            if section_of[ca] > section_of[cb]:
                ca, cb, ga, gb = cb, ca, gb, ga
            between = (int(section_of[ca]), int(section_of[cb]))
            if between[1] - between[0] != 1:
                raise StitchError(
                    f"Тай-линия {number} должна соединять контуры соседних разрезов "
                    f"(сейчас — разрезы {between[0] + 1} и {between[1] + 1})", between)
            job = job_of.get((ca, cb))
            if job is None:
                raise StitchError(
                    f"Тай-линия {number} соединяет контуры {ca + 1} и {cb + 1}, которые не сшиваются друг с другом",
                    between)
            la, lb = s.loops[job["lower"]], s.loops[job["upper"]]
            if ga not in la or gb not in lb:
                raise StitchError(
                    f"Тай-линия {number}: при разветвлении её вершина попала в часть контура, которая сшивается "
                    "с другой ветвью", between)
            job["ties"].append((la.index(ga), lb.index(gb), number))
            ties_used += 1

    # ---- strips
    for done, job in enumerate(jobs):
        tell(0.05 + 0.75 * done / len(jobs), f"Сшивка контуров: {done + 1} из {len(jobs)}")
        la = np.asarray(s.loops[job["lower"]], dtype=np.int64)
        lb = np.asarray(s.loops[job["upper"]], dtype=np.int64)
        tris = _strip(s.flat(la), s.flat(lb), la, lb, job["gap"], [(i, j) for i, j, _ in job["ties"]])
        if tris is None:
            numbers = ", ".join(str(n) for _, _, n in job["ties"])
            raise StitchError(
                f"{_where(job['between'])} тай-линии пересекаются (линии {numbers}): порядок вершин вдоль "
                "одного контура не совпадает с порядком вдоль другого", job["between"])
        s.emit(job["between"], tris)
        pairs[job["pair"]]["triangles"] += len(tris)

    # ---- caps
    tell(0.82, "Замыкание торцов")
    last = len(sections) - 1
    for kind, section, outward, spacing in ((start_cap, 0, -1.0, level[1] - level[0]),
                                            (end_cap, last, 1.0, level[last] - level[last - 1])):
        for contour in sections[section]:
            s.cap(kind, s.loops[contour], outward, float(spacing), cap_distance, section)

    # ---- assembly and checks
    tell(0.88, "Проверка тела")
    local = s.xyz(np.arange(len(s.local) + len(s.added)))
    tris = np.vstack([g["tris"] for g in s.groups])
    # New vertices left without triangles (a shortened ridge) are dropped; the given ones always stay.
    used = np.zeros(len(local), dtype=bool)
    used[:len(s.given)] = True
    used[tris] = True
    if not used.all():
        remap = np.cumsum(used) - 1
        local = local[used]
        tris = remap[tris]
        for g in s.groups:
            g["tris"] = remap[g["tris"]]
    owner = np.repeat(np.arange(len(s.groups)), [len(g["tris"]) for g in s.groups])
    open_edges, nonmanifold = edge_summary(tris, len(local))
    conflicts = orientation_conflicts(tris, len(local))
    closed = open_edges == 0 and nonmanifold == 0
    must_close = start_cap != "none" and end_cap != "none"
    if (must_close and not closed) or nonmanifold or conflicts:
        faulty = _edge_faults(tris, len(local)) if must_close else np.zeros(0, dtype=np.int64)
        between = None
        if len(faulty):
            # Strips are blamed before caps: a cap only repeats the outline it was given.
            blamed = sorted({s.groups[g]["between"] for g in owner[faulty]}, key=lambda b: (b[0] == b[1], b))
            between = blamed[0]
        place = _where(between) if between else "В результате"
        raise StitchError(
            f"{place} тело не замыкается (открытых рёбер: {open_edges}, рёбер с тремя и более треугольниками: "
            f"{nonmanifold}, конфликтов ориентации: {conflicts}): поправьте контуры или поставьте тай-линии", between)
    if closed and _signed_volume(local, tris) < 0:
        tris = tris[:, ::-1]
    tris = np.ascontiguousarray(tris, dtype=np.int32)
    vertices = np.empty((len(local), 3), dtype=np.float64)
    vertices[:len(s.given)] = s.given
    vertices[len(s.given):] = local[len(s.given):] + s.centre
    # The check of stage 10 on the result as it will be stored; what it finds is confirmed by the
    # slower exact search, because nearly coplanar triangles can give it a false alarm.
    if check and self_intersections(vertices, tris)["pairs"]:
        i, j = _crossing(local, tris)
        if i >= 0:
            (a1, b1), (a2, b2) = s.groups[owner[i]]["between"], s.groups[owner[j]]["between"]
            between = (min(a1, a2), max(b1, b2))
            if owner[i] != owner[j]:
                # Two strips cross each other; a strip that crosses itself is the better culprit.
                for g in s.groups:
                    if _crossing(local, g["tris"])[0] >= 0:
                        between = g["between"]
                        break
            advice = ("выберите другой способ замыкания торца или поправьте контур" if between[0] == between[1]
                      else "поставьте тай-линии или поправьте контуры")
            raise StitchError(f"{_where(between)} получаются самопересечения: {advice}", between)
    offset = float(s.centre @ normal)
    report = {
        "sections": [{"position": float(level[k] + offset), "contours": [int(c) for c in sec]}
                     for k, sec in enumerate(sections)],
        "pairs": [{**p, "a": [int(c) for c in p["a"]], "b": [int(c) for c in p["b"]]} for p in pairs],
        "ties_used": ties_used,
        "tolerance": tolerance,
        "section_tolerance": section_tolerance,
        "normal": [float(x) for x in normal],
        "warnings": s.warnings,
        "closed": bool(closed),
    }
    tell(1.0, "Готово")
    return StitchResult(vertices, tris, report)


# ---------------------------------------------------------------- extrusion and manual edits


def extrude_polygon(polygon: np.ndarray, *, distance: float, vector: np.ndarray | None = None,
                    both_sides: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Closed solid: the polygon moved by `distance` along its plane normal or along `vector`.

    Only the direction of `vector` is used. With `both_sides` the polygon is moved half the
    distance each way. Vertices: the base polygon (the given one, bit-identical, unless
    `both_sides`), then its moved copy. Normals look outward for any winding and sign of distance.
    """
    p = _clean_polygon(polygon, "Полигон")
    if not (math.isfinite(distance) and distance != 0):
        raise StitchError("Расстояние выдавливания должно быть отличным от нуля числом")
    area = _area_vector(p)
    length = float(np.linalg.norm(area))
    if length == 0.0:
        raise StitchError("Полигон вырожден: у него нулевая площадь")
    normal = area / length
    direction = normal
    if vector is not None:
        direction = np.asarray(vector, dtype=np.float64).reshape(-1)
        size = float(np.linalg.norm(direction)) if direction.shape == (3,) else 0.0
        if not (size > 0 and math.isfinite(size)):
            raise StitchError("Вектор выдавливания должен быть ненулевым вектором из трёх чисел")
        direction = direction / size
        if abs(float(direction @ normal)) < 1e-9:
            raise StitchError("Вектор выдавливания лежит в плоскости полигона: тело получается плоским")
    move = direction * float(distance)
    base, top = (p - move / 2.0, p + move / 2.0) if both_sides else (p, p + move)
    k = len(p)
    cap = cap_triangles(p).astype(np.int64)
    i = np.arange(k)
    j = (i + 1) % k
    tris = np.vstack([cap[:, ::-1], cap + k,
                      np.column_stack([i, j, i + k]), np.column_stack([j, j + k, i + k])])
    if float(move @ normal) < 0:  # the copy went below the polygon: everything is inside out
        tris = tris[:, ::-1]
    return np.vstack([base, top]), np.ascontiguousarray(tris, dtype=np.int32)


def edit_triangles(vertices, triangles, remove: np.ndarray, add: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Manual repair: drops triangles by index and appends triangles given by corner coordinates.

    `add` is (k, 3, 3) float64. A corner that coincides exactly (bit for bit) with an existing
    vertex reuses it, otherwise a new vertex is appended. Vertices left without triangles are
    dropped; the others keep their order and exact coordinates.
    """
    v = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    t = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    drop = np.asarray(remove if remove is not None else [], dtype=np.int64).reshape(-1)
    if len(drop) and (drop.min() < 0 or drop.max() >= len(t)):
        raise StitchError("Удаляемый треугольник: номер вне каркаса")
    extra = np.asarray(add if add is not None else [], dtype=np.float64)
    if extra.size == 0:
        extra = np.zeros((0, 3, 3))
    if extra.ndim != 3 or extra.shape[1:] != (3, 3) or not np.isfinite(extra).all():
        raise StitchError("Добавляемые треугольники: ожидается массив координат (k, 3, 3)")
    keep = np.ones(len(t), dtype=bool)
    keep[drop] = False
    t = t[keep]
    if len(extra):
        # Few corners against possibly millions of vertices: one exact comparison pass per distinct corner.
        known: dict[bytes, int] = {}
        fresh = []
        ids = np.empty(3 * len(extra), dtype=np.int64)
        for n, row in enumerate(extra.reshape(-1, 3) + 0.0):  # −0.0 and 0.0 are the same coordinate
            key = row.tobytes()
            if key not in known:
                same = np.flatnonzero((v[:, 0] == row[0]) & (v[:, 1] == row[1]) & (v[:, 2] == row[2]))
                if len(same):
                    known[key] = int(same[0])
                else:
                    known[key] = len(v) + len(fresh)
                    fresh.append(row)
            ids[n] = known[key]
        new = ids.reshape(-1, 3)
        if ((new[:, 0] == new[:, 1]) | (new[:, 1] == new[:, 2]) | (new[:, 0] == new[:, 2])).any():
            raise StitchError("Добавляемый треугольник вырожден: у него совпадают вершины")
        if fresh:
            v = np.vstack([v, np.asarray(fresh)])
        t = np.vstack([t, new])
    used = np.unique(t)
    remap = np.full(len(v), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return v[used], np.ascontiguousarray(remap[t], dtype=np.int32)
