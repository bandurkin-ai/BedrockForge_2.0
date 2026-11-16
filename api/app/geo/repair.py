"""Editing of triangle meshes (wireframes): checks, repair, simplification, smoothing, merge/split.

    mesh_checks(v, t)                  every check of stage 10 in one dictionary ("было → стало")
    repair(v, t, ...)                  weld · degenerate · duplicate · non-manifold · holes · orientation
    self_intersecting_triangles(v, t)  triangles taking part in self-intersections (shown, never fixed)
    simplify_mesh(v, t, ...)           to a triangle count or to a maximum deviation
    smooth_mesh(v, t, ...)             Taubin λ/μ smoothing, the volume of a closed mesh is kept
    max_deviation(points, v, t)        largest exact distance from points to a surface
    merge_meshes(list) / split_components(v, t)

Everything works on float64 coordinates relative to the centre of the extent (UTM-size numbers
lose nothing) and returns absolute coordinates; vertices that survive an operation of `repair`
are bit-identical to the input ones. Heavy passes are numba kernels or whole-array numpy; the only
Python loops run over boundary loops (holes) and over the few steps of a search.

Repair, in the order it is run (each step is switched on separately):

    weld         vertices on the same grid point of the tolerance, then neighbours closer than the
                 tolerance (KD-tree), form a cluster; its first vertex (input order) represents it.
    duplicate    triangles with the same three vertices (any rotation or winding): the first stays.
    degenerate   repeated index → dropped. Zero area with three distinct vertices (a vertex lying
                 on the opposite edge) → the longest edge is flipped with the neighbour across it,
                 which removes the triangle without opening the surface and without changing the
                 volume; where no flip is possible (free edge, non-manifold edge, the new edge
                 already exists) the triangle is dropped.
    nonmanifold  the mesh is cut into patches — groups of triangles connected through edges with
                 exactly two triangles. Patches meeting on an edge with more than two triangles
                 are removed greedily, dangling ones (with free edges) first, then by increasing
                 area, while every edge of the patch keeps at least two triangles; a closed patch
                 that uses each of its non-manifold edges twice is a separate body touching another
                 one along an edge: it is never removed, its vertices on that edge are duplicated
                 instead so the bodies become separate. Anything else is left and reported.
    holes        boundary loops are traced around the fans of their vertices and triangulated
                 without new vertices: earcut in the best-fit plane when the projection is a
                 simple polygon (exact for planar holes), otherwise ear clipping in 3D; a fan
                 around a centroid vertex only for loops too long for the ear clipping. The largest
                 loop of a connected part that spans (almost) the whole extent of that part is the
                 outer boundary of an open surface (DTM, fault) and is left alone.
    orient       windings are propagated over manifold edges (BFS); closed parts are turned
                 outward (positive signed volume), open parts keep the winding of their majority.

Self-intersections are reported, never fixed: no library available here does it reliably.
Known limits: every closed part is turned outward on its own, so an inner shell that was meant
as a cavity (inward on purpose) becomes a body; non-manifold vertices (parts touching in a
point) are not a defect here and stay.
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit, prange

from app.formats.wireframes import edge_summary
from app.geo.checks import (
    MAX_CELLS_PER_TRIANGLE,
    _cell_counts,
    _crosses,
    _fill_cells,
    _pair_owned,
    _share_vertex,
    orientation_conflicts,
    self_intersections,
)

# A triangle below this area (m², as in mesh_stats of stage 10) is degenerate.
DEGENERATE_AREA = 1e-9
# The largest loop of a part is its outer boundary when its box diagonal reaches this share of the part's.
OPEN_SURFACE_SPAN = 0.9
# Loops longer than this are not ear-clipped in 3D (quadratic time): a centroid fan closes them.
EAR_CLIP_LIMIT = 20_000
# Passes of edge flips over zero-area triangles (a flip may leave another sliver behind).
FLIP_PASSES = 5
# Whole-mesh quadric decimation above this size goes by spatial parts (memory, see meshes.simplify).
QUADRIC_WHOLE_LIMIT = 4_000_000
# Steps of the search over the triangle count when simplifying to a tolerance.
TOLERANCE_SEARCH_STEPS = 8
# Taubin pass-band frequency: 1/λ + 1/μ = k.
TAUBIN_PASS_BAND = 0.1

TITLES = {
    "weld": "Сшивка совпадающих вершин",
    "duplicate": "Удаление повторных треугольников",
    "degenerate": "Удаление вырожденных треугольников",
    "nonmanifold": "Устранение неманифолдных рёбер",
    "holes": "Закрытие отверстий",
    "orient": "Согласование нормалей",
}


# ---------------------------------------------------------------- small helpers


def _arrays(vertices, triangles) -> tuple[np.ndarray, np.ndarray]:
    v = np.ascontiguousarray(vertices, dtype=np.float64).reshape(-1, 3)
    t = np.ascontiguousarray(triangles, dtype=np.int32).reshape(-1, 3)
    return v, t


def _centre(v: np.ndarray) -> np.ndarray:
    if len(v) == 0:
        return np.zeros(3)
    return (v.min(axis=0) + v.max(axis=0)) / 2.0


def _compact(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drops vertices no triangle uses; the order (and the bits) of the others is kept."""
    used = np.zeros(len(v), dtype=bool)
    used[t.reshape(-1)] = True
    if used.all():
        return v, t
    remap = np.cumsum(used, dtype=np.int64) - 1
    return v[used], remap[t].astype(np.int32)


# ---------------------------------------------------------------- kernels: measures


@njit(cache=True, parallel=True)
def _measure(v, t, eps):
    """(area, six signed volumes, degenerate triangles) with v relative to the centre."""
    m = t.shape[0]
    area = 0.0
    vol = 0.0
    bad = 0
    for i in prange(m):
        a, b, c = t[i, 0], t[i, 1], t[i, 2]
        ax, ay, az = v[a, 0], v[a, 1], v[a, 2]
        bx, by, bz = v[b, 0], v[b, 1], v[b, 2]
        cx, cy, cz = v[c, 0], v[c, 1], v[c, 2]
        ux, uy, uz = bx - ax, by - ay, bz - az
        wx, wy, wz = cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
        s = 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
        area += s
        vol += ax * (by * cz - bz * cy) + ay * (bz * cx - bx * cz) + az * (bx * cy - by * cx)
        if a == b or b == c or a == c or s < eps:
            bad += 1
    return area, vol, bad


@njit(cache=True, parallel=True)
def _degenerate_kinds(v, t, eps):
    """0 fine, 1 repeated index, 2 three distinct vertices but no area."""
    m = t.shape[0]
    kind = np.zeros(m, dtype=np.uint8)
    for i in prange(m):
        a, b, c = t[i, 0], t[i, 1], t[i, 2]
        if a == b or b == c or a == c:
            kind[i] = 1
            continue
        ux, uy, uz = v[b, 0] - v[a, 0], v[b, 1] - v[a, 1], v[b, 2] - v[a, 2]
        wx, wy, wz = v[c, 0] - v[a, 0], v[c, 1] - v[a, 1], v[c, 2] - v[a, 2]
        nx, ny, nz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
        if 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz) < eps:
            kind[i] = 2
    return kind


@njit(cache=True, parallel=True)
def _triangle_hashes(t):
    """A 64-bit hash of the sorted vertex triple (equal for every rotation and winding)."""
    m = t.shape[0]
    out = np.empty(m, dtype=np.uint64)
    k1 = np.uint64(0x9E3779B97F4A7C15)
    k2 = np.uint64(0xBF58476D1CE4E5B9)
    k3 = np.uint64(0x94D049BB133111EB)
    for i in prange(m):
        a, b, c = t[i, 0], t[i, 1], t[i, 2]
        if a > b:
            a, b = b, a
        if b > c:
            b, c = c, b
        if a > b:
            a, b = b, a
        h = (np.uint64(a) + k1) * k2
        h ^= h >> np.uint64(29)
        h = (h + np.uint64(b)) * k3
        h ^= h >> np.uint64(31)
        h = (h + np.uint64(c)) * k1
        h ^= h >> np.uint64(32)
        out[i] = h
    return out


@njit(cache=True)
def _same_triple(t, i, j):
    a, b, c = t[i, 0], t[i, 1], t[i, 2]
    p, q, r = t[j, 0], t[j, 1], t[j, 2]
    return ((a == p or a == q or a == r) and (b == p or b == q or b == r) and (c == p or c == q or c == r)
            and (p == a or p == b or p == c) and (q == a or q == b or q == c) and (r == a or r == b or r == c))


@njit(cache=True)
def _mark_duplicates(t, hashes, order):
    """True for every triangle repeating an earlier one. `order` is a stable sort of the hashes,
    so the first triangle of a group of equal triples has the lowest index and is the one kept."""
    m = t.shape[0]
    dup = np.zeros(m, dtype=np.bool_)
    reps = np.empty(8, dtype=np.int64)
    s = 0
    while s < m:
        e = s + 1
        h = hashes[order[s]]
        while e < m and hashes[order[e]] == h:
            e += 1
        if e - s > 1:
            n = 1
            reps[0] = order[s]
            for x in range(s + 1, e):
                i = order[x]
                found = False
                for r in range(n):
                    if _same_triple(t, i, reps[r]):
                        found = True
                        break
                if found:
                    dup[i] = True
                elif n < 8:  # a hash collision: another triple with the same hash
                    reps[n] = i
                    n += 1
        s = e
    return dup


def _duplicate_mask(t: np.ndarray) -> np.ndarray:
    if len(t) < 2:
        return np.zeros(len(t), dtype=bool)
    hashes = _triangle_hashes(t)
    return _mark_duplicates(t, hashes, np.argsort(hashes, kind="stable"))


@njit(cache=True)
def _vertex_roots(t, n):
    """Union-find over vertices joined by triangles: the root of every vertex."""
    parent = np.arange(n).astype(np.int32)
    for i in range(t.shape[0]):
        a = t[i, 0]
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        for k in (1, 2):
            b = t[i, k]
            while parent[b] != b:
                parent[b] = parent[parent[b]]
                b = parent[b]
            if a != b:
                if a < b:
                    parent[b] = a
                else:
                    parent[a] = b
                    a = b
    for x in range(n):
        r = x
        while parent[r] != r:
            r = parent[r]
        parent[x] = r
    return parent


# ---------------------------------------------------------------- kernels: adjacency, orientation


@njit(cache=True, parallel=True)
def _edge_keys(t, n):
    """One key per half-edge 3·i+k (corner k → k+1 of triangle i), equal for both directions."""
    m = t.shape[0]
    keys = np.empty(3 * m, dtype=np.int64)
    for i in prange(m):
        for k in range(3):
            a = t[i, k]
            b = t[i, (k + 1) % 3]
            if a > b:
                a, b = b, a
            keys[3 * i + k] = np.int64(a) * n + b
    return keys


@njit(cache=True)
def _link(keys, order):
    """Half-edge → its twin; -1 on a free edge, -2 on an edge with more than two triangles."""
    total = len(order)
    adj = np.empty(total, dtype=np.int32)
    s = 0
    while s < total:
        e = s + 1
        k = keys[order[s]]
        while e < total and keys[order[e]] == k:
            e += 1
        if e - s == 1:
            adj[order[s]] = -1
        elif e - s == 2:
            adj[order[s]] = order[s + 1]
            adj[order[s + 1]] = order[s]
        else:
            for x in range(s, e):
                adj[order[x]] = -2
        s = e
    return adj


def _adjacency(t: np.ndarray, n: int) -> np.ndarray:
    keys = _edge_keys(t, max(n, 1))
    return _link(keys, np.argsort(keys))


@njit(cache=True)
def _propagate(t, adj):
    """Consistent windings by BFS over manifold edges.

    Returns (flip per triangle, part per triangle, parts, conflicts): a part is a set of
    triangles connected through edges with exactly two triangles; conflicts are edges of a part
    that cannot be oriented (a Möbius band)."""
    m = t.shape[0]
    flip = np.zeros(m, dtype=np.bool_)
    comp = np.full(m, -1, dtype=np.int32)
    stack = np.empty(m, dtype=np.int32)
    parts = 0
    conflicts = 0
    for s in range(m):
        if comp[s] >= 0:
            continue
        comp[s] = parts
        stack[0] = s
        top = 1
        while top > 0:
            top -= 1
            i = stack[top]
            for k in range(3):
                h = adj[3 * i + k]
                if h < 0:
                    continue
                j = h // 3
                # Neighbours walking the shared edge the same way have opposite normals.
                same = t[i, k] == t[j, h - 3 * j]
                want = flip[i] != same
                if comp[j] < 0:
                    comp[j] = parts
                    flip[j] = want
                    stack[top] = j
                    top += 1
                elif flip[j] != want:
                    conflicts += 1
        parts += 1
    return flip, comp, parts, conflicts // 2


@njit(cache=True)
def _flip_triangles(t, flip):
    n = 0
    for i in range(t.shape[0]):
        if flip[i]:
            t[i, 1], t[i, 2] = t[i, 2], t[i, 1]
            n += 1
    return n


@njit(cache=True)
def _flip_linked(t, adj, flip):
    """Flips windings and keeps the half-edge links valid: (a, b, c) → (a, c, b) swaps the
    half-edges 0 and 2 of the triangle."""
    m = t.shape[0]
    n = 0
    for i in range(m):
        if flip[i]:
            t[i, 1], t[i, 2] = t[i, 2], t[i, 1]
            adj[3 * i], adj[3 * i + 2] = adj[3 * i + 2], adj[3 * i]
            n += 1
    if n:
        for h in range(3 * m):
            g = adj[h]
            if g >= 0 and flip[g // 3]:
                e = g % 3
                if e == 0:
                    adj[h] = g + 2
                elif e == 2:
                    adj[h] = g - 2
    return n


@njit(cache=True)
def _part_stats(v, t, adj, comp, parts, flipped, originals):
    """Per part: six signed volumes, free or non-manifold half-edges, flipped and all source triangles."""
    vol = np.zeros(parts, dtype=np.float64)
    free = np.zeros(parts, dtype=np.int64)
    nflip = np.zeros(parts, dtype=np.int64)
    count = np.zeros(parts, dtype=np.int64)
    for i in range(t.shape[0]):
        c = comp[i]
        a, b, d = t[i, 0], t[i, 1], t[i, 2]
        vol[c] += (v[a, 0] * (v[b, 1] * v[d, 2] - v[b, 2] * v[d, 1])
                   + v[a, 1] * (v[b, 2] * v[d, 0] - v[b, 0] * v[d, 2])
                   + v[a, 2] * (v[b, 0] * v[d, 1] - v[b, 1] * v[d, 0]))
        for k in range(3):
            if adj[3 * i + k] < 0:
                free[c] += 1
        if i < originals:
            count[c] += 1
            if flipped[i]:
                nflip[c] += 1
    return vol, free, nflip, count


@njit(cache=True)
def _part_boxes(v, t, comp, parts):
    lo = np.full((parts, 3), np.inf)
    hi = np.full((parts, 3), -np.inf)
    for i in range(t.shape[0]):
        c = comp[i]
        for k in range(3):
            for d in range(3):
                x = v[t[i, k], d]
                lo[c, d] = min(lo[c, d], x)
                hi[c, d] = max(hi[c, d], x)
    return lo, hi


# ---------------------------------------------------------------- kernels: degenerate triangles


@njit(cache=True)
def _plan_flips(v, t, adj, bad, kind, touched):
    """For every zero-area triangle: the neighbour across its longest edge and the new diagonal.

    plan[x] = (triangle, neighbour, c, d) for a flip of the shared edge into c–d; neighbour -1
    when the triangle can only be dropped; -2 when it must wait for the next pass."""
    plan = np.empty((len(bad), 4), dtype=np.int64)
    for x in range(len(bad)):
        i = bad[x]
        best = -1.0
        k = 0
        for e in range(3):
            a, b = t[i, e], t[i, (e + 1) % 3]
            d2 = (v[a, 0] - v[b, 0]) ** 2 + (v[a, 1] - v[b, 1]) ** 2 + (v[a, 2] - v[b, 2]) ** 2
            if d2 > best:
                best = d2
                k = e
        plan[x, 0] = i
        plan[x, 2] = t[i, (k + 2) % 3]
        plan[x, 3] = -1
        h = adj[3 * i + k]
        if h < 0:
            plan[x, 1] = -1
            continue
        j = h // 3
        if kind[j] != 0 or touched[j] or touched[i]:
            plan[x, 1] = -2
            continue
        touched[i] = True
        touched[j] = True
        plan[x, 1] = j
        plan[x, 3] = t[j, (h - 3 * j + 2) % 3]
    return plan


@njit(cache=True)
def _apply_flips(t, plan, approved):
    """(a, b, c) + its neighbour over a–b with the far corner d → (a, d, c) and (d, b, c)."""
    for x in range(plan.shape[0]):
        if not approved[x]:
            continue
        i, j, c, d = plan[x, 0], plan[x, 1], plan[x, 2], plan[x, 3]
        k = 0
        for e in range(3):
            if t[i, e] == c:
                k = e
        a, b = t[i, (k + 1) % 3], t[i, (k + 2) % 3]
        t[i, 0], t[i, 1], t[i, 2] = a, d, c
        t[j, 0], t[j, 1], t[j, 2] = d, b, c


# ---------------------------------------------------------------- kernels: non-manifold patches


@njit(cache=True)
def _greedy_patches(priority, starts, inc_edge, uses, free_edges):
    """Which candidate patches to remove; incidences of a patch are sorted by edge.

    A patch goes only if each of its non-manifold edges keeps two triangles without it, and never
    when it is a closed body touching another one (no free edges, every such edge used twice)."""
    removed = np.zeros(len(priority), dtype=np.bool_)
    for c in priority:
        s, e = starts[c], starts[c + 1]
        ok = True
        paired = free_edges[c] == 0
        x = s
        while x < e:
            y = x + 1
            while y < e and inc_edge[y] == inc_edge[x]:
                y += 1
            if uses[inc_edge[x]] - (y - x) < 2:
                ok = False
                break
            if (y - x) % 2 == 1:
                paired = False
            x = y
        if not ok or paired:
            continue
        for x in range(s, e):
            uses[inc_edge[x]] -= 1
        removed[c] = True
    return removed


# ---------------------------------------------------------------- kernels: boundary loops


@njit(cache=True)
def _corner(t, i, v):
    for k in range(3):
        if t[i, k] == v:
            return k
    return 0


@njit(cache=True)
def _boundary_loops(t, adj):
    """Loops of free edges, traced by turning around each vertex through its fan of triangles
    (so a vertex where two holes touch belongs to each of them, and windings do not matter).

    Returns (starts, vertices, ok, first half-edge): loop i is vertices[starts[i]:starts[i+1]] in the
    direction of its first half-edge; ok is False when the walk met a non-manifold edge."""
    m = t.shape[0]
    total = 0
    for h in range(3 * m):
        if adj[h] == -1:
            total += 1
    seen = np.zeros(3 * m, dtype=np.bool_)
    verts = np.empty(total, dtype=np.int32)
    starts = np.empty(total + 1, dtype=np.int64)
    ok = np.empty(total, dtype=np.bool_)
    first = np.empty(total, dtype=np.int64)
    loops = 0
    p = 0
    for h0 in range(3 * m):
        if adj[h0] != -1 or seen[h0]:
            continue
        starts[loops] = p
        first[loops] = h0
        good = True
        seen[h0] = True
        i = h0 // 3
        k = h0 - 3 * i
        verts[p] = t[i, k]
        p += 1
        v = t[i, (k + 1) % 3]
        h = h0
        while True:
            i = h // 3
            k = h - 3 * i
            c = _corner(t, i, v)
            g = 3 * i + ((c + 2) % 3 if c == k else c)
            steps = 0
            while adj[g] >= 0 and steps <= 3 * m:
                g2 = adj[g]
                j = g2 // 3
                c = _corner(t, j, v)
                g = 3 * j + ((c + 2) % 3 if c == g2 - 3 * j else c)
                steps += 1
            if adj[g] != -1:
                good = False
                break
            if g == h0:
                break
            if seen[g]:
                good = False
                break
            seen[g] = True
            verts[p] = v
            p += 1
            j = g // 3
            a = t[j, g - 3 * j]
            v = t[j, (g - 3 * j + 1) % 3] if a == v else a
            h = g
        ok[loops] = good
        loops += 1
    starts[loops] = p
    return starts[:loops + 1], verts[:p], ok[:loops], first[:loops]


@njit(cache=True)
def _ear_clip(p, normal):
    """Triangulates a closed 3D polygon by cutting, at every step, the sharpest corner that turns
    the way the polygon does (`normal` is its area vector). Triangles keep the polygon's order."""
    n = p.shape[0]
    prev = np.empty(n, dtype=np.int64)
    nxt = np.empty(n, dtype=np.int64)
    for i in range(n):
        prev[i] = i - 1
        nxt[i] = i + 1
    prev[0] = n - 1
    nxt[n - 1] = 0
    out = np.empty((n - 2, 3), dtype=np.int64)
    count = n
    cur = 0
    k = 0
    while count > 3:
        best = cur
        best_score = -1e30
        i = cur
        for _ in range(count):
            a, b = prev[i], nxt[i]
            ux, uy, uz = p[i, 0] - p[a, 0], p[i, 1] - p[a, 1], p[i, 2] - p[a, 2]
            wx, wy, wz = p[b, 0] - p[i, 0], p[b, 1] - p[i, 1], p[b, 2] - p[i, 2]
            lu = math.sqrt(ux * ux + uy * uy + uz * uz)
            lw = math.sqrt(wx * wx + wy * wy + wz * wz)
            if lu == 0.0 or lw == 0.0:
                score = 3.0
            else:
                turn = ((uy * wz - uz * wy) * normal[0] + (uz * wx - ux * wz) * normal[1]
                        + (ux * wy - uy * wx) * normal[2])
                score = -(ux * wx + uy * wy + uz * wz) / (lu * lw)
                if turn <= 0.0:
                    score -= 4.0
            if score > best_score:
                best_score = score
                best = i
            i = nxt[i]
        a, b = prev[best], nxt[best]
        out[k, 0], out[k, 1], out[k, 2] = a, best, b
        k += 1
        nxt[a] = b
        prev[b] = a
        cur = b
        count -= 1
    out[k, 0], out[k, 1], out[k, 2] = prev[cur], cur, nxt[cur]
    return out


# ---------------------------------------------------------------- kernels: distances


@njit(cache=True)
def _segment_d2(px, py, pz, ax, ay, az, bx, by, bz):
    ux, uy, uz = bx - ax, by - ay, bz - az
    wx, wy, wz = px - ax, py - ay, pz - az
    uu = ux * ux + uy * uy + uz * uz
    s = 0.0
    if uu > 0.0:
        s = min(max((ux * wx + uy * wy + uz * wz) / uu, 0.0), 1.0)
    dx, dy, dz = wx - s * ux, wy - s * uy, wz - s * uz
    return dx * dx + dy * dy + dz * dz


@njit(cache=True)
def _point_triangle_d2(px, py, pz, ax, ay, az, bx, by, bz, cx, cy, cz):
    """Squared distance from a point to a triangle (Ericson, Real-Time Collision Detection, 5.1.5)."""
    abx, aby, abz = bx - ax, by - ay, bz - az
    acx, acy, acz = cx - ax, cy - ay, cz - az
    apx, apy, apz = px - ax, py - ay, pz - az
    d1 = abx * apx + aby * apy + abz * apz
    d2 = acx * apx + acy * apy + acz * apz
    if d1 <= 0.0 and d2 <= 0.0:
        return apx * apx + apy * apy + apz * apz
    bpx, bpy, bpz = px - bx, py - by, pz - bz
    d3 = abx * bpx + aby * bpy + abz * bpz
    d4 = acx * bpx + acy * bpy + acz * bpz
    if d3 >= 0.0 and d4 <= d3:
        return bpx * bpx + bpy * bpy + bpz * bpz
    cpx, cpy, cpz = px - cx, py - cy, pz - cz
    d5 = abx * cpx + aby * cpy + abz * cpz
    d6 = acx * cpx + acy * cpy + acz * cpz
    if d6 >= 0.0 and d5 <= d6:
        return cpx * cpx + cpy * cpy + cpz * cpz
    vc = d1 * d4 - d3 * d2
    vb = d5 * d2 - d1 * d6
    va = d3 * d6 - d5 * d4
    denom = va + vb + vc
    if vc <= 0.0 or vb <= 0.0 or va <= 0.0 or denom <= 0.0:
        # Nearest to an edge (or the triangle has no area): the three sides cover every case.
        return min(_segment_d2(px, py, pz, ax, ay, az, bx, by, bz),
                   _segment_d2(px, py, pz, bx, by, bz, cx, cy, cz),
                   _segment_d2(px, py, pz, cx, cy, cz, ax, ay, az))
    s = vb / denom
    w = vc / denom
    dx = apx - abx * s - acx * w
    dy = apy - aby * s - acy * w
    dz = apz - abz * s - acz * w
    return dx * dx + dy * dy + dz * dz


@njit(cache=True)
def _d2(p, x, v, t, i):
    a, b, c = t[i, 0], t[i, 1], t[i, 2]
    return _point_triangle_d2(p[x, 0], p[x, 1], p[x, 2], v[a, 0], v[a, 1], v[a, 2],
                              v[b, 0], v[b, 1], v[b, 2], v[c, 0], v[c, 1], v[c, 2])


@njit(cache=True, parallel=True)
def _distances(p, bound, v, t, cell, gmin, dims, ukeys, runs, ids, big_ids, brute_cells):
    """Exact distance from every point to the surface. `bound` is an upper limit (the distance
    to the nearest vertex): only the grid cells within it are searched."""
    out = np.empty(p.shape[0], dtype=np.float64)
    for x in prange(p.shape[0]):
        r = bound[x] * (1.0 + 1e-12) + 1e-300
        best = r * r
        c0 = np.empty(3, dtype=np.int64)
        c1 = np.empty(3, dtype=np.int64)
        cells = 1
        for k in range(3):
            lo = int(math.floor((p[x, k] - r - gmin[k]) / cell))  # noqa: RUF046 — typed for numba
            hi = int(math.floor((p[x, k] + r - gmin[k]) / cell))  # noqa: RUF046
            c0[k] = min(max(lo, 0), dims[k] - 1)
            c1[k] = min(max(hi, 0), dims[k] - 1)
            cells *= c1[k] - c0[k] + 1
        if cells > brute_cells:
            for i in range(t.shape[0]):
                d = _d2(p, x, v, t, i)
                best = min(best, d)
        else:
            for cx in range(c0[0], c1[0] + 1):
                for cy in range(c0[1], c1[1] + 1):
                    for cz in range(c0[2], c1[2] + 1):
                        key = (cx * dims[1] + cy) * dims[2] + cz
                        pos = np.searchsorted(ukeys, key)
                        if pos >= len(ukeys) or ukeys[pos] != key:
                            continue
                        for y in range(runs[pos], runs[pos + 1]):
                            d = _d2(p, x, v, t, ids[y])
                            best = min(best, d)
            for y in range(len(big_ids)):
                d = _d2(p, x, v, t, big_ids[y])
                best = min(best, d)
        out[x] = math.sqrt(best)
    return out


# ---------------------------------------------------------------- kernels: self-intersections


@njit(parallel=True, cache=True)
def _mark_runs(v, t, lo, hi, cell, gmin, dims, keys, ids, runs, eps, mark):
    for r in prange(len(runs) - 1):
        s, e = runs[r], runs[r + 1]
        key = keys[s]
        for x in range(s, e):
            i = ids[x]
            for y in range(x + 1, e):
                j = ids[y]
                if _share_vertex(t, i, j) or not _pair_owned(i, j, lo, hi, cell, gmin, key, dims):
                    continue
                if _crosses(v, t[i], t[j], eps):
                    mark[i] = True
                    mark[j] = True


@njit(parallel=True, cache=True)
def _mark_big(v, t, lo, hi, big_ids, eps, mark):
    m = t.shape[0]
    for b in prange(len(big_ids)):
        i = big_ids[b]
        for j in range(m):
            if j == i or _share_vertex(t, i, j):
                continue
            ok = True
            for k in range(3):
                if lo[i, k] > hi[j, k] or lo[j, k] > hi[i, k]:
                    ok = False
                    break
            if ok and _crosses(v, t[i], t[j], eps):
                mark[i] = True
                mark[j] = True


def _triangle_grid(v: np.ndarray, t: np.ndarray):
    """The uniform grid of checks.self_intersections: every triangle listed in the cells its box
    covers (triangles covering too many cells are returned apart as `big`)."""
    lo = v[t[:, 0]]
    hi = lo.copy()
    for k in (1, 2):
        corner = v[t[:, k]]
        np.minimum(lo, corner, out=lo)
        np.maximum(hi, corner, out=hi)
    size = np.median((hi - lo).max(axis=1))
    gmin = lo.min(axis=0)
    span = float((hi.max(axis=0) - gmin).max())
    cell = max(float(size) * 2.0, span / 2**20, 1e-9)
    dims = np.floor((hi.max(axis=0) - gmin) / cell).astype(np.int64) + 1
    counts = _cell_counts(lo, hi, cell, gmin)
    big = counts > MAX_CELLS_PER_TRIANGLE
    counts[big] = 0
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    total = int(counts.sum())
    keys = np.empty(total, dtype=np.int64)
    ids = np.empty(total, dtype=np.int32)
    _fill_cells(lo, hi, cell, gmin, dims, starts, keys, ids, big)
    order = np.argsort(keys)
    keys = keys[order]
    ids = ids[order]
    if total:
        runs = np.append(np.flatnonzero(np.concatenate([[True], keys[1:] != keys[:-1]])), total).astype(np.int64)
    else:
        runs = np.zeros(1, dtype=np.int64)
    return lo, hi, cell, gmin, dims, keys, ids, runs, big


# ---------------------------------------------------------------- checks


def _component_count(t: np.ndarray, n: int) -> int:
    if len(t) == 0:
        return 0
    roots = _vertex_roots(t, n)
    return len(np.unique(roots[t[:, 0]]))


def mesh_checks(vertices, triangles, *, intersections: bool = True) -> dict:
    """Every check of a wireframe in one dictionary (the "было → стало" of the repair report).

    `degenerate` counts triangles with a repeated index or an area below 1e-9 m²; `duplicate` —
    triangles repeating an earlier one (any rotation or winding); `components` — parts connected
    through shared vertices; `volume` and `orientation` exist for closed meshes only (the sign of
    the total signed volume). `self_intersections` is the number of crossing pairs, or None when
    `intersections` is off (it is the slowest check)."""
    v, t = _arrays(vertices, triangles)
    m, n = len(t), len(v)
    out = {
        "triangles": m, "vertices": n, "closed": False, "open_edges": 0, "nonmanifold_edges": 0,
        "degenerate": 0, "duplicate": 0, "orientation_conflicts": 0,
        "self_intersections": 0 if intersections else None, "components": 0,
        "area": 0.0, "volume": None, "orientation": None,
    }
    if m == 0:
        return out
    area, vol6, degenerate = _measure(v - _centre(v), t, DEGENERATE_AREA)
    open_edges, nonmanifold = edge_summary(t, n)
    closed = open_edges == 0 and nonmanifold == 0
    out.update(
        closed=closed, open_edges=open_edges, nonmanifold_edges=nonmanifold, degenerate=int(degenerate),
        duplicate=int(np.count_nonzero(_duplicate_mask(t))), orientation_conflicts=orientation_conflicts(t, n),
        components=_component_count(t, n), area=float(area),
    )
    if closed:
        out["volume"] = abs(float(vol6)) / 6.0
        out["orientation"] = "outward" if vol6 > 0 else "inward"
    if intersections:
        out["self_intersections"] = int(self_intersections(v, t)["pairs"])
    return out


def self_intersecting_triangles(vertices, triangles, limit: int = 100_000) -> np.ndarray:
    """Indices (ascending, at most `limit`) of the triangles crossing another triangle of the mesh.

    The same test as checks.self_intersections (pairs sharing a vertex and coplanar overlaps are
    not reported); meant for highlighting in the 3D view — the fix is left to the user."""
    v, t = _arrays(vertices, triangles)
    if len(t) < 2:
        return np.zeros(0, dtype=np.int64)
    vc = np.ascontiguousarray(v - _centre(v))
    lo, hi, cell, gmin, dims, keys, ids, runs, big = _triangle_grid(vc, t)
    mark = np.zeros(len(t), dtype=np.bool_)
    eps = 1e-9
    if len(keys):
        _mark_runs(vc, t, lo, hi, cell, gmin, dims, keys, ids, runs, eps, mark)
    big_ids = np.flatnonzero(big).astype(np.int64)
    if len(big_ids):
        _mark_big(vc, t, lo, hi, big_ids, eps, mark)
    return np.flatnonzero(mark)[:max(int(limit), 0)]


# ---------------------------------------------------------------- distances


class _Surface:
    """A triangle mesh prepared for point-distance queries (grid of triangles + KD-tree of vertices)."""

    def __init__(self, centred_vertices: np.ndarray, triangles: np.ndarray):
        from scipy.spatial import cKDTree

        self.v = np.ascontiguousarray(centred_vertices, dtype=np.float64)
        self.t = np.ascontiguousarray(triangles, dtype=np.int32)
        used = np.unique(self.t)
        self.tree = cKDTree(self.v[used])
        _, _, self.cell, self.gmin, self.dims, keys, self.ids, self.runs, big = _triangle_grid(self.v, self.t)
        self.ukeys = np.ascontiguousarray(keys[self.runs[:-1]]) if len(keys) else np.zeros(0, dtype=np.int64)
        self.big_ids = np.flatnonzero(big).astype(np.int64)

    def distances(self, centred_points: np.ndarray) -> np.ndarray:
        p = np.ascontiguousarray(centred_points, dtype=np.float64).reshape(-1, 3)
        if len(p) == 0:
            return np.zeros(0)
        bound, _ = self.tree.query(p, workers=-1)
        # A point far from the surface would visit more cells than there are triangles: test them all.
        brute = max(512, len(self.t) // 8)
        return _distances(p, bound, self.v, self.t, self.cell, self.gmin, self.dims, self.ukeys, self.runs,
                          self.ids, self.big_ids, brute)


def max_deviation(points, vertices, triangles) -> float:
    """The largest distance from `points` to the surface: exact point–triangle distances, the
    nearest triangle found through a uniform grid bounded by the nearest vertex (KD-tree).

    One-sided; for a symmetric measure call it both ways (source vertices → result and result
    vertices → source). 0.0 without points, nan without triangles."""
    v, t = _arrays(vertices, triangles)
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(p) == 0:
        return 0.0
    if len(t) == 0:
        return float("nan")
    centre = _centre(v)
    return float(_Surface(v - centre, t).distances(p - centre).max())


def _two_sided(source: _Surface, vc: np.ndarray, t: np.ndarray, sample_all: bool = True) -> float:
    """Sampled symmetric deviation of a result (centred like the source) from the source surface:
    source vertices → result, and result vertices plus triangle centroids → source."""
    if len(t) == 0:
        return float("inf")
    result = _Surface(vc, t)
    used = np.unique(source.t)
    forward = float(result.distances(source.v[used]).max())
    samples = vc[np.unique(t)]
    if sample_all:
        samples = np.concatenate([samples, (vc[t[:, 0]] + vc[t[:, 1]] + vc[t[:, 2]]) / 3.0])
    return max(forward, float(source.distances(samples).max()))


# ---------------------------------------------------------------- repair steps


def _weld(v: np.ndarray, t: np.ndarray, tolerance: float) -> tuple[np.ndarray, int]:
    """Triangles re-indexed to the first vertex of every cluster of coincident vertices.

    Returns (triangles, merged vertices); vertices themselves are untouched (compacted later)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    n = len(v)
    vc = v - _centre(v)
    key = np.round(vc / tolerance).astype(np.int64) if tolerance > 0 else vc
    order = np.lexsort((key[:, 2], key[:, 1], key[:, 0]))
    ks = key[order]
    starts = np.empty(n, dtype=bool)
    starts[0] = True
    np.any(ks[1:] != ks[:-1], axis=1, out=starts[1:])
    del ks, key
    group = np.cumsum(starts) - 1
    groups = int(group[-1]) + 1
    first = np.full(groups, n, dtype=np.int64)
    np.minimum.at(first, group, order)
    of_vertex = np.empty(n, dtype=np.int64)
    of_vertex[order] = group
    del order, group
    if tolerance > 0 and groups > 1:
        # Neighbours on two sides of a grid line: clusters of points closer than the tolerance.
        pairs = cKDTree(vc[first], balanced_tree=False, compact_nodes=False).query_pairs(tolerance, output_type="ndarray")
        if len(pairs):
            graph = coo_matrix((np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(groups, groups))
            clusters, label = connected_components(graph, directed=False)
            lowest = np.full(clusters, n, dtype=np.int64)
            np.minimum.at(lowest, label, first)
            first = lowest[label]
    rep = first[of_vertex]
    used_before = np.zeros(n, dtype=bool)
    used_before[t.reshape(-1)] = True
    welded = rep[t].astype(np.int32)
    used_after = np.zeros(n, dtype=bool)
    used_after[welded.reshape(-1)] = True
    return welded, int(np.count_nonzero(used_before) - np.count_nonzero(used_after))


def _fix_degenerate(vc: np.ndarray, t: np.ndarray, n: int, drop: bool = True) -> tuple[np.ndarray, int, int]:
    """(triangles, dropped, flipped): see the module docstring. With drop=False only flips are made."""
    kind = _degenerate_kinds(vc, t, DEGENERATE_AREA)
    dropped = flipped = 0
    if drop and np.any(kind == 1):
        keep = kind != 1
        dropped += int(len(t) - np.count_nonzero(keep))
        t, kind = t[keep], kind[keep]
    for _ in range(FLIP_PASSES):
        bad = np.flatnonzero(kind == 2)
        if len(bad) == 0:
            break
        t = np.array(t)
        keys = _edge_keys(t, max(n, 1))
        order = np.argsort(keys)
        adj = _link(keys, order)
        plan = _plan_flips(vc, t, adj, bad, kind, np.zeros(len(t), dtype=np.bool_))
        can = plan[:, 1] >= 0
        # A flip must not create an edge the mesh already has (it would become non-manifold).
        c, d = np.minimum(plan[:, 2], plan[:, 3]), np.maximum(plan[:, 2], plan[:, 3])
        new_keys = c * max(n, 1) + d
        sorted_keys = keys[order]
        pos = np.clip(np.searchsorted(sorted_keys, new_keys), 0, len(sorted_keys) - 1)
        exists = sorted_keys[pos] == new_keys
        approved = can & ~exists
        _apply_flips(t, plan, approved)
        flipped += int(np.count_nonzero(approved))
        gone = (plan[:, 1] == -1) | (can & exists)
        progress = bool(approved.any())
        if drop and gone.any():
            keep = np.ones(len(t), dtype=bool)
            keep[plan[gone, 0]] = False
            dropped += int(np.count_nonzero(gone))
            t = t[keep]
            progress = True
        if not progress:
            break
        kind = _degenerate_kinds(vc, t, DEGENERATE_AREA)
    if drop:
        # Whatever the flips could not resolve (chains of slivers) is dropped after all.
        left = kind == 2
        if left.any():
            dropped += int(np.count_nonzero(left))
            t = t[~left]
    return np.ascontiguousarray(t), dropped, flipped


def _triangle_areas(vc: np.ndarray, t: np.ndarray) -> np.ndarray:
    cross = np.cross(vc[t[:, 1]] - vc[t[:, 0]], vc[t[:, 2]] - vc[t[:, 0]])
    return 0.5 * np.sqrt(np.einsum("ij,ij->i", cross, cross))


def _patches(t: np.ndarray, n: int):
    """Edge ids and uses of every half-edge, and the patch (triangles joined over two-triangle edges)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    m = len(t)
    keys = _edge_keys(t, max(n, 1))
    order = np.argsort(keys)
    ks = keys[order]
    start = np.concatenate([[True], ks[1:] != ks[:-1]])
    edge_sorted = np.cumsum(start) - 1
    uses = np.bincount(edge_sorted)
    edge = np.empty(3 * m, dtype=np.int64)
    edge[order] = edge_sorted
    pos = np.flatnonzero(start)[uses == 2]
    a, b = order[pos] // 3, order[pos + 1] // 3
    count, patch = connected_components(coo_matrix((np.ones(len(a), dtype=np.int8), (a, b)), shape=(m, m)), directed=False)
    return edge, uses, patch, count


def _fix_nonmanifold(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """Removes fins and extra sheets, separates bodies touching along an edge (module docstring)."""
    info = {"removed_triangles": 0, "removed_area": 0.0, "duplicated_vertices": 0}
    n = len(v)
    vc = v - _centre(v)
    edge, uses, patch, count = _patches(t, n)
    if not np.any(uses > 2):
        return v, t, info
    half_uses = uses[edge]
    area = _triangle_areas(vc, t)
    patch_area = np.bincount(patch, weights=area, minlength=count)
    free = np.bincount(patch[np.flatnonzero(half_uses == 1) // 3], minlength=count)
    nm = np.flatnonzero(half_uses > 2)
    inc_patch, inc_edge = patch[nm // 3], edge[nm]
    o = np.lexsort((inc_edge, inc_patch))
    inc_patch, inc_edge = inc_patch[o], inc_edge[o]
    cand, starts = np.unique(inc_patch, return_index=True)
    starts = np.append(starts, len(inc_patch)).astype(np.int64)
    # Dangling patches first, then the smaller ones.
    priority = np.lexsort((patch_area[cand], free[cand] == 0)).astype(np.int64)
    removed = _greedy_patches(priority, starts, inc_edge, uses.astype(np.int64), free[cand].astype(np.int64))
    if removed.any():
        gone = np.zeros(count, dtype=bool)
        gone[cand[removed]] = True
        drop = gone[patch]
        info["removed_triangles"] = int(np.count_nonzero(drop))
        info["removed_area"] = float(area[drop].sum())
        t = np.ascontiguousarray(t[~drop])
        edge, uses, patch, count = _patches(t, n)
        if not np.any(uses > 2):
            return v, t, info
        half_uses = uses[edge]
        patch_area = np.bincount(patch, weights=_triangle_areas(vc, t), minlength=count)
        nm = np.flatnonzero(half_uses > 2)

    # What is left: patches using an edge exactly twice are bodies of their own touching there.
    pair = edge[nm] * count + patch[nm // 3]
    _, inverse, mult = np.unique(pair, return_inverse=True, return_counts=True)
    own = nm[mult[inverse] == 2]
    if len(own) == 0:
        return v, t, info
    tri, k = own // 3, own % 3
    pv = np.concatenate([t[tri, k], t[tri, (k + 1) % 3]]).astype(np.int64)
    pp = np.concatenate([patch[tri], patch[tri]]).astype(np.int64)
    ukey = np.unique(pv * count + pp)
    uv, up = ukey // count, ukey % count
    # The largest patch at a vertex keeps it; every other one gets its own copy.
    o = np.lexsort((up, -patch_area[up], uv))
    keeper = np.zeros(len(ukey), dtype=bool)
    keeper[o[np.concatenate([[True], uv[o][1:] != uv[o][:-1]])]] = True
    new_id = uv.copy()
    copies = np.flatnonzero(~keeper)
    new_id[copies] = n + np.arange(len(copies))
    corner_key = t.astype(np.int64) * count + patch[:, None]
    pos = np.clip(np.searchsorted(ukey, corner_key), 0, len(ukey) - 1)
    hit = ukey[pos] == corner_key
    t = np.array(t)
    t[hit] = new_id[pos[hit]]
    info["duplicated_vertices"] = len(copies)
    return np.concatenate([v, v[uv[copies]]]), t, info


def _fill_polygon(pts: np.ndarray) -> tuple[np.ndarray, bool]:
    """Triangles (local indices, wound like the polygon) closing a 3D loop; (None, False) when the
    loop is too long for the 3D fallback and needs a centroid fan."""
    import mapbox_earcut

    n = len(pts)
    if n == 3:
        return np.array([[0, 1, 2]], dtype=np.int64), True
    q = pts - pts.mean(axis=0)
    nxt = np.roll(q, -1, axis=0)
    normal = np.cross(q, nxt).sum(axis=0)  # twice the area vector of the loop
    scale = float(np.abs(q).max())
    if scale > 0 and np.linalg.norm(normal) > 1e-12 * scale * scale:
        _, _, axes = np.linalg.svd(q, full_matrices=False)
        xy = np.ascontiguousarray(q @ axes[:2].T)
        tri = mapbox_earcut.triangulate_float64(xy, np.array([n], dtype=np.uint32)).reshape(-1, 3).astype(np.int64)
        if len(tri) == n - 2:
            x, y = xy[:, 0], xy[:, 1]
            polygon = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
            a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
            signed = 0.5 * ((x[b] - x[a]) * (y[c] - y[a]) - (x[c] - x[a]) * (y[b] - y[a]))
            # The projection is a simple polygon exactly when the triangles tile its area.
            if abs(np.abs(signed).sum() - abs(polygon)) <= 1e-6 * abs(polygon):
                wrong = signed * polygon < 0
                tri[wrong] = tri[wrong][:, [0, 2, 1]]
                return tri, True
    if n > EAR_CLIP_LIMIT:
        return None, False
    return _ear_clip(np.ascontiguousarray(q), normal), True


def _split_pinched(starts: np.ndarray, verts: np.ndarray, ok: np.ndarray, first: np.ndarray):
    """Loops passing twice through a vertex (two holes touching there) cut into simple loops."""
    loops = len(starts) - 1
    loop_of = np.repeat(np.arange(loops), np.diff(starts))
    o = np.lexsort((verts, loop_of))
    repeated = (loop_of[o][1:] == loop_of[o][:-1]) & (verts[o][1:] == verts[o][:-1])
    pinched = np.unique(loop_of[o][1:][repeated])
    if len(pinched) == 0:
        return starts, verts, ok, first
    rings, flags, firsts = [], [], []
    is_pinched = np.zeros(loops, dtype=bool)
    is_pinched[pinched] = True
    for i in range(loops):
        ring = verts[starts[i]:starts[i + 1]]
        if not is_pinched[i] or not ok[i]:
            rings.append(ring)
            flags.append(bool(ok[i]))
            firsts.append(first[i])
            continue
        stack: list[int] = []
        on_stack: dict[int, int] = {}
        for x in ring.tolist():
            if x in on_stack:
                at = on_stack[x]
                part = stack[at:]
                for y in part[1:]:
                    del on_stack[y]
                del stack[at + 1:]
                rings.append(np.array(part, dtype=np.int32))
                flags.append(True)
                firsts.append(first[i])
            else:
                on_stack[x] = len(stack)
                stack.append(x)
        rings.append(np.array(stack, dtype=np.int32))
        flags.append(True)
        firsts.append(first[i])
    new_starts = np.concatenate([[0], np.cumsum([len(r) for r in rings])]).astype(np.int64)
    return new_starts, np.concatenate(rings), np.array(flags), np.array(firsts, dtype=np.int64)


def _fill_holes(v: np.ndarray, vc: np.ndarray, t: np.ndarray, adj: np.ndarray, comp: np.ndarray, parts: int,
                max_hole_edges: int | None, keep_outer: bool) -> tuple[np.ndarray, np.ndarray, dict]:
    info = {"filled": 0, "skipped_outer": 0, "skipped_large": 0, "skipped_complex": 0, "added_vertices": 0}
    starts, loop_verts, ok, first = _boundary_loops(t, adj)
    if len(starts) == 1:
        return v, t, info
    starts, loop_verts, ok, first = _split_pinched(starts, loop_verts, ok, first)
    loops = len(starts) - 1
    sizes = np.diff(starts)
    points = vc[loop_verts]
    lo = np.minimum.reduceat(points, starts[:-1], axis=0)
    hi = np.maximum.reduceat(points, starts[:-1], axis=0)
    diag = np.linalg.norm(hi - lo, axis=1)
    part = comp[first // 3]
    outer = np.zeros(loops, dtype=bool)
    if keep_outer:
        plo, phi = _part_boxes(vc, t, comp, parts)
        part_diag = np.linalg.norm(phi - plo, axis=1)
        # The largest loop of every part, if it spans the part.
        o = np.lexsort((-diag, part))
        largest = o[np.concatenate([[True], part[o][1:] != part[o][:-1]])]
        outer[largest] = diag[largest] >= OPEN_SURFACE_SPAN * part_diag[part[largest]]
    new_tris, new_points = [], []
    base = len(v)
    for i in range(loops):
        size = int(sizes[i])
        if outer[i]:
            info["skipped_outer"] += 1
            continue
        if not ok[i] or size < 3:
            info["skipped_complex"] += 1
            continue
        if max_hole_edges is not None and size > max_hole_edges:
            info["skipped_large"] += 1
            continue
        # The mesh walks the loop forwards, so the patch must walk it backwards.
        ring = loop_verts[starts[i]:starts[i + 1]][::-1]
        tri, done = _fill_polygon(vc[ring])
        if done:
            new_tris.append(ring[tri])
        else:
            centre = base + len(new_points)
            new_points.append(v[ring].mean(axis=0))
            new_tris.append(np.column_stack([ring, np.roll(ring, -1), np.full(size, centre)]))
        info["filled"] += 1
    if new_tris:
        t = np.ascontiguousarray(np.concatenate([t, *new_tris]).astype(np.int32))
    if new_points:
        v = np.concatenate([v, np.array(new_points)])
        info["added_vertices"] = len(new_points)
    return v, t, info


def repair(vertices, triangles, *, weld: float | None = 1e-6, drop_degenerate: bool = True,
           drop_duplicate: bool = True, orient: bool = True, fill_holes: bool = True, fix_nonmanifold: bool = True,
           max_hole_edges: int | None = None, progress=None, keep_outer_boundary: bool = True,
           intersections: bool = True) -> tuple[np.ndarray, np.ndarray, dict]:
    """Repairs a wireframe; every fix is switched on separately (algorithms: module docstring).

    Returns (vertices, triangles, report): report["before"] / report["after"] are mesh_checks,
    report["steps"] lists what every enabled step changed (`changed` and a Russian `title`, plus a
    `note` where something was left alone on purpose). Surviving vertices keep their bits and their
    order; vertices no triangle uses are dropped. Self-intersections are never fixed.

    `weld` is the tolerance in metres (0 — exactly equal vertices only, None — no welding);
    `max_hole_edges` leaves longer boundary loops open; `keep_outer_boundary=False` closes even a
    loop spanning its whole part (by default it is taken for the outer boundary of an open surface);
    `intersections=False` skips the slow self-intersection count in both checks."""
    v, t = _arrays(vertices, triangles)
    t = np.array(t)
    steps: list[dict] = []
    done = [0]

    def tick() -> None:
        done[0] += 1
        if progress:
            progress(min(done[0] / 9.0, 1.0))

    def step(name: str, changed: int, **extra) -> None:
        steps.append({"step": name, "title": TITLES[name], "changed": int(changed), **extra})
        tick()

    before = mesh_checks(v, t, intersections=intersections)
    tick()
    if len(t) == 0:
        return v, t, {"before": before, "after": before, "steps": steps, "tolerance": weld}

    if weld is not None:
        t, merged = _weld(v, t, float(weld))
        step("weld", merged)
    v, t = _compact(v, t)
    vc = v - _centre(v)

    if drop_duplicate:
        dup = _duplicate_mask(t)
        if dup.any():
            t = np.ascontiguousarray(t[~dup])
        step("duplicate", np.count_nonzero(dup))

    if drop_degenerate:
        t, dropped, flipped = _fix_degenerate(vc, t, len(v))
        step("degenerate", dropped + flipped, dropped=dropped, flipped=flipped)

    adj = _adjacency(t, len(v)) if (fix_nonmanifold or fill_holes or orient) and len(t) else None
    if fix_nonmanifold and adj is not None:
        info = {"removed_triangles": 0, "removed_area": 0.0, "duplicated_vertices": 0}
        if np.any(adj == -2):
            v, t, info = _fix_nonmanifold(v, t)
            vc = v - _centre(v)
            adj = _adjacency(t, len(v))
        remaining = int(np.count_nonzero(adj == -2))
        extra = dict(info, remaining_halfedges=remaining)
        if remaining:
            extra["note"] = ("Часть неманифолдных рёбер оставлена: поверхности сходятся на ребре так, "
                             "что лишнюю нельзя выбрать однозначно — исправьте вручную")
        step("nonmanifold", info["removed_triangles"] + info["duplicated_vertices"], **extra)

    originals = len(t)
    flipped = np.zeros(originals, dtype=np.bool_)
    comp = None
    parts = conflicts = 0
    if (orient or fill_holes) and len(t):
        flip, comp, parts, conflicts = _propagate(t, adj)
        if orient:
            _flip_linked(t, adj, flip)
            flipped = flip

    if fill_holes and len(t):
        count = len(t)
        v, t, info = _fill_holes(v, vc, t, adj, comp, parts, max_hole_edges, keep_outer_boundary)
        vc = v - _centre(v)
        added = len(t) - count
        if added and drop_degenerate:
            # A patch over collinear boundary vertices: flipped away, never dropped (that would reopen it).
            t, _, _ = _fix_degenerate(vc, t, len(v), drop=False)
        notes = []
        if info["skipped_outer"]:
            notes.append(f"Наружная граница незамкнутой поверхности оставлена открытой (контуров: {info['skipped_outer']})")
        if info["skipped_large"]:
            notes.append(f"Пропущены отверстия длиннее {max_hole_edges} рёбер (контуров: {info['skipped_large']})")
        if info["skipped_complex"]:
            notes.append(f"Не закрыты контуры, проходящие через неманифолдные рёбра (контуров: {info['skipped_complex']})")
        extra = dict(info)
        if notes:
            extra["note"] = "; ".join(notes)
        step("holes", added, **extra)
        if added and orient:
            adj = _adjacency(t, len(v))
            flip, comp, parts, conflicts = _propagate(t, adj)
            _flip_linked(t, adj, flip)
            flipped = flipped ^ flip[:originals]

    if orient and len(t):
        # Closed parts outward; open parts keep the winding most of their triangles had.
        vol, free, nflip, count = _part_stats(vc, t, adj, comp, parts, flipped, originals)
        turn = np.where(free == 0, vol < 0, 2 * nflip > count)
        if turn.any():
            whole = turn[comp]
            _flip_triangles(t, whole)
            flipped = flipped ^ whole[:originals]
        extra = {"parts": int(parts), "closed_parts": int(np.count_nonzero(free == 0)),
                 "turned_outward": int(np.count_nonzero(turn & (free == 0)))}
        if conflicts:
            extra["note"] = f"Поверхность неориентируема: не согласовано рёбер — {conflicts}"
        step("orient", np.count_nonzero(flipped), **extra)

    v, t = _compact(v, np.ascontiguousarray(t, dtype=np.int32))
    after = mesh_checks(v, t, intersections=intersections)
    if progress:
        progress(1.0)
    return v, t, {"before": before, "after": after, "steps": steps, "tolerance": weld}


# ---------------------------------------------------------------- simplification


def _signed_volume(vc: np.ndarray, t: np.ndarray) -> float:
    return float(_measure(vc, t, DEGENERATE_AREA)[1]) / 6.0


def _is_closed(t: np.ndarray, n: int) -> bool:
    return len(t) > 0 and edge_summary(t, n) == (0, 0)


def _quadric(vc: np.ndarray, t: np.ndarray, target: int) -> tuple[np.ndarray, np.ndarray]:
    """Quadric edge-collapse decimation (pyfqmr) of a centred mesh to about `target` triangles."""
    if len(t) > QUADRIC_WHOLE_LIMIT:
        from app.geo.meshes import morton_order, simplify

        v2, t2 = simplify(vc, t[morton_order(vc, t)], target)
    else:
        import pyfqmr

        s = pyfqmr.Simplify()
        # pyfqmr needs writable arrays of its own.
        s.setMesh(np.array(vc, dtype=np.float64, order="C"), np.array(t, dtype=np.int32, order="C"))
        s.simplify_mesh(target_count=int(target), aggressiveness=7, preserve_border=True, verbose=False)
        v2, t2, _ = s.getMesh()
    v2 = np.asarray(v2, dtype=np.float64)
    t2 = np.asarray(t2, dtype=np.int32)
    keep = (t2[:, 0] != t2[:, 1]) & (t2[:, 1] != t2[:, 2]) & (t2[:, 0] != t2[:, 2])
    return _compact(v2, np.ascontiguousarray(t2[keep]))


def _manifold_simplify(vc: np.ndarray, t: np.ndarray, tolerance: float) -> tuple[np.ndarray, np.ndarray] | None:
    """manifold3d: collapses within the tolerance, keeps a subset of the vertices; None if the
    library does not accept the mesh as a manifold."""
    import manifold3d

    try:
        solid = manifold3d.Manifold(manifold3d.Mesh64(vert_properties=np.ascontiguousarray(vc),
                                                      tri_verts=np.ascontiguousarray(t, dtype=np.uint64)))
        if solid.status() != manifold3d.Error.NoError or solid.is_empty():
            return None
        mesh = solid.simplify(float(tolerance)).to_mesh64()
    except Exception:  # noqa: BLE001 — any refusal of the library means "use the other method"
        return None
    v2 = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    t2 = np.asarray(mesh.tri_verts).astype(np.int32)
    if len(t2) == 0:
        return None
    return _compact(np.ascontiguousarray(v2), np.ascontiguousarray(t2))


def simplify_mesh(vertices, triangles, *, target_triangles: int | None = None, tolerance: float | None = None,
                  progress=None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Fewer triangles: down to `target_triangles`, or as few as keep the surface within
    `tolerance` metres of the source (with both, not fewer than `target_triangles`).

    To a count — quadric edge collapses (pyfqmr, free borders preserved). To a tolerance — a
    bisection over the count of the same decimation, every candidate measured against the source
    (source vertices → result, result vertices and triangle centroids → source; exact
    point–triangle distances), a closed source must stay closed; for closed manifolds the result
    of manifold3d's `simplify(tolerance)` competes too and the smaller mesh wins. If nothing
    passes, the source is returned unchanged (method "none").

    report: triangles_before/after, volume_before/after (closed meshes), volume_change_percent,
    max_deviation (the sampled symmetric one), closed_before/after, method."""
    if target_triangles is None and tolerance is None:
        raise ValueError("Укажите число треугольников или допуск по отклонению")
    if tolerance is not None and not tolerance > 0:
        raise ValueError("Допуск по отклонению должен быть больше нуля")
    if target_triangles is not None and target_triangles < 1:
        raise ValueError("Число треугольников должно быть положительным")
    v, t = _arrays(vertices, triangles)
    m = len(t)
    centre = _centre(v)
    vc = v - centre
    closed_before = _is_closed(t, len(v))
    volume_before = abs(_signed_volume(vc, t)) if closed_before else None
    source = _Surface(vc, t) if m else None
    best_v, best_t, method, deviation = vc, t, "none", 0.0

    if m and tolerance is None:
        if target_triangles < m:
            best_v, best_t = _quadric(vc, t, target_triangles)
            method = "quadric"
            deviation = _two_sided(source, best_v, best_t)
        if progress:
            progress(1.0)
    elif m:
        def passes(cv, ct):
            if len(ct) == 0 or (closed_before and not _is_closed(ct, len(cv))):
                return None
            d = _two_sided(source, cv, ct)
            return d if d <= tolerance else None

        steps = TOLERANCE_SEARCH_STEPS + 1
        if closed_before:
            got = _manifold_simplify(vc, t, tolerance)
            if got is not None and len(got[1]) < m:
                d = passes(*got)
                if d is not None:
                    best_v, best_t, method, deviation = got[0], got[1], "manifold", d
        if progress:
            progress(1.0 / steps)
        # Bisection over the logarithm of the count: `high` is the smallest count known to pass.
        low = float(max(target_triangles or 4, 4))
        high = float(len(best_t))
        for i in range(TOLERANCE_SEARCH_STEPS):
            if high <= low * 1.1:
                break
            count = round(math.sqrt(low * high))
            cv, ct = _quadric(vc, t, count)
            d = passes(cv, ct) if len(ct) < len(best_t) else None
            if d is not None:
                best_v, best_t, method, deviation = cv, ct, "quadric", d
                high = float(min(count, len(ct)))
            else:
                low = float(count)
            if progress:
                progress((i + 2) / steps)
        if progress:
            progress(1.0)

    if method == "none":
        out_v, out_t = v, t
    else:
        out_v = best_v + centre
        if method == "manifold" and len(out_v):
            # The library keeps a subset of the source vertices: give them their exact bits back.
            dist, idx = source.tree.query(best_v)
            used = np.unique(t)
            exact = dist <= 1e-9 * max(float(np.abs(vc).max()), 1.0)
            out_v[exact] = v[used[idx[exact]]]
        out_t = best_t
    closed_after = _is_closed(out_t, len(out_v))
    volume_after = abs(_signed_volume(out_v - centre, out_t)) if closed_after else None
    change = None
    if volume_before and volume_after is not None:
        change = (volume_after - volume_before) / volume_before * 100.0
    return out_v, out_t, {
        "triangles_before": m, "triangles_after": len(out_t),
        "volume_before": volume_before, "volume_after": volume_after, "volume_change_percent": change,
        "max_deviation": float(deviation), "closed_before": closed_before, "closed_after": closed_after,
        "method": method,
    }


# ---------------------------------------------------------------- smoothing


def smooth_mesh(vertices, triangles, *, iterations: int = 10, strength: float = 0.5,
                keep_boundary: bool = True) -> tuple[np.ndarray, np.ndarray, dict]:
    """Taubin λ/μ smoothing: every iteration moves vertices towards the mean of their neighbours
    (λ = `strength`) and then back out (μ < −λ), which removes roughness without the shrinkage of
    plain Laplacian smoothing. A closed mesh is finally scaled about its centroid so that its
    volume equals the source's. Triangles (topology) are returned unchanged; with `keep_boundary`
    vertices on free and non-manifold edges do not move.

    report: volume_before/after (closed meshes), volume_change_percent, max_deviation (source
    vertices → result and result vertices → source), iterations, scale."""
    from scipy.sparse import coo_matrix, diags

    v, t = _arrays(vertices, triangles)
    n, m = len(v), len(t)
    iterations = max(int(iterations), 0)
    lam = min(max(float(strength), 0.0), 0.95)
    centre = _centre(v)
    vc = v - centre
    closed = _is_closed(t, n)
    volume_before = _signed_volume(vc, t) if closed else None
    x = vc.copy()
    scale = 1.0
    if m and iterations and lam > 0:
        mu = 1.0 / (TAUBIN_PASS_BAND - 1.0 / lam)
        keys = np.unique(_edge_keys(t, n))
        a, b = keys // n, keys % n
        ones = np.ones(len(a))
        graph = coo_matrix((np.concatenate([ones, ones]), (np.concatenate([a, b]), np.concatenate([b, a]))), shape=(n, n)).tocsr()
        degree = np.asarray(graph.sum(axis=1)).ravel()
        movable = degree > 0
        if keep_boundary:
            adj = _adjacency(t, n)
            edge = np.flatnonzero(adj < 0)
            movable[t[edge // 3, edge % 3]] = False
            movable[t[edge // 3, (edge % 3 + 1) % 3]] = False
        weights = diags(np.where(movable, 1.0 / np.maximum(degree, 1.0), 0.0)) @ graph
        step = movable.astype(np.float64)[:, None]
        for _ in range(iterations):
            for factor in (lam, mu):
                x += factor * step * (weights @ x - x)
        if closed and volume_before:
            # Two passes: the second removes the rounding of the first.
            for _ in range(2):
                now = _signed_volume(x, t)
                if now * volume_before <= 0:
                    break
                k = (volume_before / now) ** (1.0 / 3.0)
                origin = x[np.unique(t)].mean(axis=0)
                x = origin + (x - origin) * k
                scale *= k
    volume_after = _signed_volume(x, t) if closed else None
    deviation = 0.0
    if m:
        used = np.unique(t)
        deviation = max(float(_Surface(x, t).distances(vc[used]).max()), float(_Surface(vc, t).distances(x[used]).max()))
    change = None
    if volume_before:
        change = (abs(volume_after) - abs(volume_before)) / abs(volume_before) * 100.0
    return x + centre, t, {
        "volume_before": abs(volume_before) if closed else None, "volume_after": abs(volume_after) if closed else None,
        "volume_change_percent": change, "max_deviation": deviation, "iterations": iterations, "scale": float(scale),
    }


# ---------------------------------------------------------------- merge and split


def merge_meshes(meshes: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """Several meshes as one object: vertices concatenated, indices offset; nothing is welded."""
    verts, tris, base = [], [], 0
    for vertices, triangles in meshes:
        v, t = _arrays(vertices, triangles)
        verts.append(v)
        tris.append(t.astype(np.int64) + base)
        base += len(v)
    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32)
    return np.concatenate(verts), np.concatenate(tris).astype(np.int32)


def split_components(vertices, triangles) -> list[tuple[np.ndarray, np.ndarray]]:
    """Connected parts (triangles sharing a vertex are connected), the one with most triangles
    first; each part gets its own vertex array without unused vertices, order preserved."""
    v, t = _arrays(vertices, triangles)
    if len(t) == 0:
        return []
    roots = _vertex_roots(t, len(v))
    tri_root = roots[t[:, 0]]
    uniq, tri_label, counts = np.unique(tri_root, return_inverse=True, return_counts=True)
    if len(uniq) == 1:
        return [_compact(v, t)]
    rank = np.empty(len(uniq), dtype=np.int64)
    rank[np.argsort(-counts, kind="stable")] = np.arange(len(uniq))
    tri_rank = rank[tri_label]
    used = np.zeros(len(v), dtype=bool)
    used[t.reshape(-1)] = True
    label_of_root = np.full(len(v), -1, dtype=np.int64)
    label_of_root[uniq] = rank
    vert_rank = label_of_root[roots]
    # Vertices grouped by part (input order inside a part), triangles likewise.
    vert_ids = np.flatnonzero(used)
    vert_ids = vert_ids[np.argsort(vert_rank[vert_ids], kind="stable")]
    vert_counts = np.bincount(vert_rank[vert_ids], minlength=len(uniq))
    vert_start = np.concatenate([[0], np.cumsum(vert_counts)])
    local = np.empty(len(v), dtype=np.int64)
    local[vert_ids] = np.arange(len(vert_ids)) - vert_start[vert_rank[vert_ids]]
    tri_order = np.argsort(tri_rank, kind="stable")
    tri_start = np.concatenate([[0], np.cumsum(np.bincount(tri_rank, minlength=len(uniq)))])
    local_t = local[t[tri_order]].astype(np.int32)
    return [(v[vert_ids[vert_start[i]:vert_start[i + 1]]], local_t[tri_start[i]:tri_start[i + 1]])
            for i in range(len(uniq))]
