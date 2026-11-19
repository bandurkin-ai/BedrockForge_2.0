"""numba kernels of «Исправить каркас» (`geo/repair.py`, ADR 0015 §7): degenerate and repeated triangles,
adjacency of half-edges, consistent windings, flips of zero-area triangles.

Ported from BF `geo/repair.py` (section «kernels») with the same algorithms. Differences: no `prange` (ADR 0014:
one thread per kernel; the kernels release the GIL), `nogil=True` everywhere. Vertex coordinates are float64
relative to the centre of the extent (the caller subtracts it); triangle corners are int32.

Half-edge h = 3·i + k runs from corner k to corner k + 1 of triangle i. `link` gives its twin — the half-edge of
the other triangle on the same edge — or −1 on a free edge and −2 on an edge with more than two triangles.

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

# --- degenerate and repeated triangles ---------------------------------------------------------------------


@njit(cache=True, nogil=True)
def degenerate_kinds(v, t, eps):  # type: ignore[no-untyped-def]
    """Per triangle: 0 — fine, 1 — a repeated corner, 2 — three distinct corners but an area below `eps`."""
    m = t.shape[0]
    kind = np.zeros(m, dtype=np.uint8)
    for i in range(m):
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


@njit(cache=True, nogil=True)
def triangle_hashes(t):  # type: ignore[no-untyped-def]
    """A 64-bit hash of the sorted corner triple: equal for every rotation and winding of a triangle."""
    m = t.shape[0]
    out = np.empty(m, dtype=np.uint64)
    k1 = np.uint64(0x9E3779B97F4A7C15)
    k2 = np.uint64(0xBF58476D1CE4E5B9)
    k3 = np.uint64(0x94D049BB133111EB)
    for i in range(m):
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


@njit(cache=True, nogil=True)
def _same_triple(t, i, j):  # type: ignore[no-untyped-def]
    a, b, c = t[i, 0], t[i, 1], t[i, 2]
    p, q, r = t[j, 0], t[j, 1], t[j, 2]
    return (
        (a == p or a == q or a == r)
        and (b == p or b == q or b == r)
        and (c == p or c == q or c == r)
        and (p == a or p == b or p == c)
        and (q == a or q == b or q == c)
        and (r == a or r == b or r == c)
    )


@njit(cache=True, nogil=True)
def mark_duplicates(t, hashes, order):  # type: ignore[no-untyped-def]
    """True for every triangle repeating an earlier one. `order` is a stable sort of the hashes, so the first
    triangle of a group of equal triples has the lowest index and is the one kept."""
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


@njit(cache=True, nogil=True)
def vertex_roots(t, n):  # type: ignore[no-untyped-def]
    """Union–find over vertices joined by triangles: the root (the lowest vertex) of every vertex."""
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


# --- adjacency, orientation ----------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def edge_keys(t, n):  # type: ignore[no-untyped-def]
    """One key per half-edge 3·i + k, equal for both directions of an edge: min · n + max."""
    m = t.shape[0]
    keys = np.empty(3 * m, dtype=np.int64)
    for i in range(m):
        for k in range(3):
            a = t[i, k]
            b = t[i, (k + 1) % 3]
            if a > b:
                a, b = b, a
            keys[3 * i + k] = np.int64(a) * n + b
    return keys


@njit(cache=True, nogil=True)
def link(keys, order):  # type: ignore[no-untyped-def]
    """Half-edge → its twin; −1 on a free edge, −2 on an edge with more than two triangles."""
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


@njit(cache=True, nogil=True)
def propagate(t, adj):  # type: ignore[no-untyped-def]
    """Consistent windings by a walk over manifold edges.

    Returns (flip per triangle, part per triangle, parts, conflicts): a part is a set of triangles connected
    through edges with exactly two triangles, its first triangle keeps its winding; conflicts are edges of a
    part that cannot be oriented (a Möbius band)."""
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
                # neighbours walking the shared edge the same way have opposite normals
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


@njit(cache=True, nogil=True)
def flip_triangles(t, flip):  # type: ignore[no-untyped-def]
    """(a, b, c) → (a, c, b) where `flip` is set; the number flipped."""
    n = 0
    for i in range(t.shape[0]):
        if flip[i]:
            t[i, 1], t[i, 2] = t[i, 2], t[i, 1]
            n += 1
    return n


@njit(cache=True, nogil=True)
def flip_linked(t, adj, flip):  # type: ignore[no-untyped-def]
    """Flips windings and keeps the half-edge links valid: (a, b, c) → (a, c, b) swaps the half-edges 0 and 2
    of the triangle."""
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


@njit(cache=True, nogil=True)
def part_stats(v, t, adj, comp, parts, flipped, originals):  # type: ignore[no-untyped-def]
    """Per part: six signed volumes, free or non-manifold half-edges, flipped and all source triangles (those
    below `originals`)."""
    vol = np.zeros(parts, dtype=np.float64)
    free = np.zeros(parts, dtype=np.int64)
    nflip = np.zeros(parts, dtype=np.int64)
    count = np.zeros(parts, dtype=np.int64)
    for i in range(t.shape[0]):
        c = comp[i]
        a, b, d = t[i, 0], t[i, 1], t[i, 2]
        vol[c] += (
            v[a, 0] * (v[b, 1] * v[d, 2] - v[b, 2] * v[d, 1])
            + v[a, 1] * (v[b, 2] * v[d, 0] - v[b, 0] * v[d, 2])
            + v[a, 2] * (v[b, 0] * v[d, 1] - v[b, 1] * v[d, 0])
        )
        for k in range(3):
            if adj[3 * i + k] < 0:
                free[c] += 1
        if i < originals:
            count[c] += 1
            if flipped[i]:
                nflip[c] += 1
    return vol, free, nflip, count


# --- zero-area triangles ---------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def plan_flips(v, t, adj, bad, kind, touched):  # type: ignore[no-untyped-def]
    """For every zero-area triangle `bad[x]`: the neighbour across its longest edge and the new diagonal.

    plan[x] = (triangle, neighbour, c, d) for a flip of the shared edge into c–d; neighbour −1 when the
    triangle can only be dropped (a free or non-manifold edge); −2 when it waits for the next pass (the
    neighbour is degenerate too, or one of the two is in another flip of this pass)."""
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


@njit(cache=True, nogil=True)
def apply_flips(t, plan, approved):  # type: ignore[no-untyped-def]
    """(a, b, c) + its neighbour over a–b with the far corner d → (a, d, c) and (d, b, c): the winding of the
    zero-area triangle, the area of the neighbour, no new vertex."""
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
