"""Flat regions of a triangle mesh, thinned in their planes before pyfqmr (ADR 0013 §4 `flat.py`, «Долги Э7»).

Why. The quadric of a vertex inside a flat region has no single best point, of a vertex on a straight edge
between two flat regions — a whole line of them. pyfqmr solves for the point all the same: on a plane tilted
along both axes its determinant is rounding noise, not zero, the point lands anywhere, and the collapse is
refused or, once the threshold has grown, taken. Measured on Э7 (tests/test_viz_mesh.py, a dense flat grid
tilted both ways): 1/2 of the triangles instead of 1/8 and vertices up to 0.2 of a cell off the plane with
cells of 1 m; nothing removed and vertices 40 m off the plane with cells of 10 m.

What. `thin(local, tri, target)` takes out vertices that the surface does not need, until `target`
triangles are left or no such vertex can go:
  * a vertex inside a flat region: every triangle around it lies in one plane;
  * a vertex on a straight edge between two flat regions: two planes, and the edge runs straight through it.
The vertex goes and the hole it leaves — one flat polygon, or two with the edge between them — is cut into
triangles again: ears, the best-shaped first. No vertex is made and none is moved, so the surface is the same
surface: the deviation is 0 for exact planes and at most `FLAT_TOL` × the size of a triangle otherwise. A
vertex with an open fan — the border of the part, of a hole — never goes: neighbouring parts keep their
common border bit for bit at every level (ADR 0004 §3). A fold, a triangle without area, a vertex on a new
edge and an edge that the mesh has already are refused.

Order. Passes over the vertices in a scattered order; a vertex that goes closes the vertices around it until
the next pass, so every pass thins the mesh evenly, and the last, partial, pass — evenly too. The least
quality allowed for a new triangle goes down a step (`QUALITY`) only when nothing can go at the current one,
so the triangles stay as good as the border and the target allow.

`held` of the answer marks the vertices of flat regions and straight edges that stayed (no cut of the hole
was good enough, or the fan is longer than `MAX_RING`): pyfqmr must not get their triangles — `join` puts
them back after it (`geo/meshes.py::simplify`). Everything else — relief, noise above the tolerance,
corners, edges that bend — has a quadric of full rank and is left to pyfqmr.

ADR 0013 names the collapse of an edge onto a neighbour; a collapse is one of the cuts of the hole (a fan
from that neighbour) and leaves long fans in strips between edges — benches of a pit: vertices that could
not go, slivers. The cut by ears has no such case; the properties the ADR asks for are the same.

Time is linear: a vertex that could not go is tried again only after its fan has changed or the quality has
gone down a step. numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from bf_core.jit import njit

FLAT_TOL = 1e-6  # the sine of the largest angle between an edge at a vertex and the plane of its fan
QUALITY = (0.4, 0.2, 0.1, 0.05, 0.02, 0.005, 0.001)  # the least quality of a new triangle, step by step
MAX_RING = 64  # the longest fan that is looked at: the choice of a neighbour is quadratic in it
MAX_PASSES = 400
_Q = 2.0 * math.sqrt(3.0)  # twice the area over the sum of the squared sides is 1/_Q for an equilateral triangle


@njit(cache=True, nogil=True)
def _prune(v, alive, head, nxt):  # type: ignore[no-untyped-def]
    """Take the corners of removed triangles out of the list of a vertex."""
    prev = -1
    c = head[v]
    while c >= 0:
        following = nxt[c]
        if alive[c // 3]:
            prev = c
        elif prev < 0:
            head[v] = following
        else:
            nxt[prev] = following
        c = following


@njit(cache=True, nogil=True)
def _fan(v, tri, head, nxt, slot, ra, rb, rt, ring, rtri):  # type: ignore[no-untyped-def]
    """The closed fan of a vertex in order: triangle rtri[s] is (v, ring[s], ring[s + 1]). Returns the number
    of triangles; 0 — the fan is open, is not one cycle or is not oriented one way; −1 — it is longer than
    the buffers. `slot` (−1 everywhere) is used and left as it was."""
    k = 0
    c = head[v]
    while c >= 0:
        if k == len(ra):
            return -1
        t = c // 3
        j = c - 3 * t
        ra[k] = tri[t, (j + 1) % 3]
        rb[k] = tri[t, (j + 2) % 3]
        rt[k] = t
        k += 1
        c = nxt[c]
    if k < 3:
        return 0
    ok = True
    marked = 0
    for i in range(k):
        if slot[ra[i]] >= 0:  # two triangles leave the vertex along one edge
            ok = False
            break
        slot[ra[i]] = i
        marked += 1
    if ok:
        i = 0
        for s in range(k):
            ring[s] = ra[i]
            rtri[s] = rt[i]
            i = slot[rb[i]]
            if i < 0 or (i == 0) != (s == k - 1):  # an open end, or a cycle shorter than the fan
                ok = False
                break
    for i in range(marked):
        slot[ra[i]] = -1
    return k if ok else 0


@njit(cache=True, nogil=True)
def _planes(k, d, cr, c2, off, side, nrm, tol2):  # type: ignore[no-untyped-def]
    """How the fan of a vertex lies (d — the ring from the vertex). Returns (kind, p, q):
    1 — in one plane (nrm[0], side 0 everywhere);
    2 — in two planes that meet in a straight line through the vertex: triangles p … q − 1 in nrm[0] (side 0),
        the others in nrm[1] (side 1), the line runs from ring p to ring q;
    0 — neither. A triangle without area or turned over makes it 0."""
    big = 0.0
    sa = -1
    for s in range(k):
        e = s + 1 if s + 1 < k else 0
        x = d[s, 1] * d[e, 2] - d[s, 2] * d[e, 1]
        y = d[s, 2] * d[e, 0] - d[s, 0] * d[e, 2]
        z = d[s, 0] * d[e, 1] - d[s, 1] * d[e, 0]
        cr[s, 0] = x
        cr[s, 1] = y
        cr[s, 2] = z
        c2[s] = x * x + y * y + z * z
        if c2[s] > big:
            big = c2[s]
            sa = s
    if sa < 0:
        return 0, 0, 0
    inv = 1.0 / math.sqrt(big)
    for a in range(3):
        nrm[0, a] = cr[sa, a] * inv
    for s in range(k):
        h = d[s, 0] * nrm[0, 0] + d[s, 1] * nrm[0, 1] + d[s, 2] * nrm[0, 2]
        off[s] = h * h > tol2 * (d[s, 0] * d[s, 0] + d[s, 1] * d[s, 1] + d[s, 2] * d[s, 2])
    others = 0
    for s in range(k):
        e = s + 1 if s + 1 < k else 0
        up = cr[s, 0] * nrm[0, 0] + cr[s, 1] * nrm[0, 1] + cr[s, 2] * nrm[0, 2] > 0.0
        if up and not off[s] and not off[e]:
            side[s] = 0
        else:
            side[s] = 1
            others += 1
    if others == 0:
        return 1, 0, 0
    # the run of the first plane around its largest triangle: p … q − 1
    q = sa + 1 if sa + 1 < k else 0
    while side[q] == 0:
        q = q + 1 if q + 1 < k else 0
    p = sa
    while side[p - 1 if p > 0 else k - 1] == 0:
        p = p - 1 if p > 0 else k - 1
    run = p - q if p >= q else p - q + k  # triangles q … p − 1: all the others must be there
    if run != others:
        return 0, 0, 0
    big = 0.0
    sb = -1
    s = q
    for _ in range(run):
        if c2[s] > big:
            big = c2[s]
            sb = s
        s = s + 1 if s + 1 < k else 0
    if sb < 0:
        return 0, 0, 0
    inv = 1.0 / math.sqrt(big)
    for a in range(3):
        nrm[1, a] = cr[sb, a] * inv
    s = q
    for _ in range(run + 1):  # ring vertices q … p
        h = d[s, 0] * nrm[1, 0] + d[s, 1] * nrm[1, 1] + d[s, 2] * nrm[1, 2]
        if h * h > tol2 * (d[s, 0] * d[s, 0] + d[s, 1] * d[s, 1] + d[s, 2] * d[s, 2]):
            return 0, 0, 0
        s = s + 1 if s + 1 < k else 0
    s = q
    for _ in range(run):
        if not cr[s, 0] * nrm[1, 0] + cr[s, 1] * nrm[1, 1] + cr[s, 2] * nrm[1, 2] > 0.0:
            return 0, 0, 0
        s = s + 1 if s + 1 < k else 0
    # the line is straight at the vertex: ring p and ring q look opposite ways
    dot = d[p, 0] * d[q, 0] + d[p, 1] * d[q, 1] + d[p, 2] * d[q, 2]
    if not dot < 0.0:
        return 0, 0, 0
    x = d[p, 1] * d[q, 2] - d[p, 2] * d[q, 1]
    y = d[p, 2] * d[q, 0] - d[p, 0] * d[q, 2]
    z = d[p, 0] * d[q, 1] - d[p, 1] * d[q, 0]
    lp = d[p, 0] * d[p, 0] + d[p, 1] * d[p, 1] + d[p, 2] * d[p, 2]
    lq = d[q, 0] * d[q, 0] + d[q, 1] * d[q, 1] + d[q, 2] * d[q, 2]
    if x * x + y * y + z * z > tol2 * lp * lq:
        return 0, 0, 0
    return 2, p, q


@njit(cache=True, nogil=True)
def _least(k, d, c2):  # type: ignore[no-untyped-def]
    """The least quality of the triangles of a fan (c2 — their squared double areas, from `_planes`)."""
    least = 4.0
    for s in range(k):
        e = s + 1 if s + 1 < k else 0
        x = d[e, 0] - d[s, 0]
        y = d[e, 1] - d[s, 1]
        z = d[e, 2] - d[s, 2]
        sides = (
            d[s, 0] * d[s, 0] + d[s, 1] * d[s, 1] + d[s, 2] * d[s, 2]
            + d[e, 0] * d[e, 0] + d[e, 1] * d[e, 1] + d[e, 2] * d[e, 2]
            + x * x + y * y + z * z
        )  # fmt: skip
        ratio = c2[s] / (sides * sides)
        if ratio < least:
            least = ratio
    return _Q * math.sqrt(least)


@njit(cache=True, nogil=True)
def _project(poly, n, d, nrm, w, px, py):  # type: ignore[no-untyped-def]
    """Plan coordinates of the ring places poly[0 … n − 1] in the plane nrm[w]: counter-clockwise there is
    counter-clockwise about the normal."""
    nx = nrm[w, 0]
    ny = nrm[w, 1]
    nz = nrm[w, 2]
    if abs(nx) <= abs(ny) and abs(nx) <= abs(nz):  # normal × the axis it is farthest from
        ex, ey, ez = 0.0, nz, -ny
    elif abs(ny) <= abs(nz):
        ex, ey, ez = -nz, 0.0, nx
    else:
        ex, ey, ez = ny, -nx, 0.0
    inv = 1.0 / math.sqrt(ex * ex + ey * ey + ez * ez)
    ex *= inv
    ey *= inv
    ez *= inv
    fx = ny * ez - nz * ey
    fy = nz * ex - nx * ez
    fz = nx * ey - ny * ex
    for i in range(n):
        s = poly[i]
        px[s] = d[s, 0] * ex + d[s, 1] * ey + d[s, 2] * ez
        py[s] = d[s, 0] * fx + d[s, 1] * fy + d[s, 2] * fz


@njit(cache=True, nogil=True)
def _edge(x, y, v, tri, head, nxt):  # type: ignore[no-untyped-def]
    """Whether the mesh has the edge x–y in a triangle that is not a triangle of v."""
    c = head[x]
    while c >= 0:
        t = c // 3
        j = c - 3 * t
        a = tri[t, (j + 1) % 3]
        b = tri[t, (j + 2) % 3]
        if (a == y or b == y) and a != v and b != v:
            return True
        c = nxt[c]
    return False


@njit(cache=True, nogil=True)
def _ears(poly, n, px, py, floor, v, ring, tri, head, nxt, cut, base):  # type: ignore[no-untyped-def]
    """Cut a flat polygon — ring places poly[0 … n − 1], counter-clockwise in (px, py) — into n − 2 triangles
    cut[base …] (ring places), the best-shaped ear first. An ear is a triangle on two sides of the polygon
    that looks the right way, holds no other vertex of the polygon inside or on its sides and whose third
    side is not an edge of the mesh yet. Returns the least quality of the triangles; −1 — no ear of the
    quality `floor` is left. `poly` is spent."""
    worst = 2.0
    made = base
    while n >= 3:
        best = -1
        bestq = -1.0
        for i in range(n):
            ia = poly[i - 1 if i > 0 else n - 1]
            ib = poly[i]
            ic = poly[i + 1 if i + 1 < n else 0]
            abx = px[ib] - px[ia]
            aby = py[ib] - py[ia]
            bcx = px[ic] - px[ib]
            bcy = py[ic] - py[ib]
            cax = px[ia] - px[ic]
            cay = py[ia] - py[ic]
            twice = abx * bcy - aby * bcx
            if not twice > 0.0:
                continue
            sides = abx * abx + aby * aby + bcx * bcx + bcy * bcy + cax * cax + cay * cay
            quality = _Q * twice / sides
            if quality < floor or quality <= bestq:
                continue
            if n == 3:  # the last triangle: its sides are there already
                best = i
                bestq = quality
                break
            on = -1e-12 * sides
            clear = True
            for m in range(n):
                ix = poly[m]
                if ix == ia or ix == ib or ix == ic:
                    continue
                if (
                    abx * (py[ix] - py[ia]) - aby * (px[ix] - px[ia]) >= on
                    and bcx * (py[ix] - py[ib]) - bcy * (px[ix] - px[ib]) >= on
                    and cax * (py[ix] - py[ic]) - cay * (px[ix] - px[ic]) >= on
                ):
                    clear = False
                    break
            if clear and not _edge(ring[ia], ring[ic], v, tri, head, nxt):
                best = i
                bestq = quality
        if best < 0:
            return -1.0
        cut[made, 0] = poly[best - 1 if best > 0 else n - 1]
        cut[made, 1] = poly[best]
        cut[made, 2] = poly[best + 1 if best + 1 < n else 0]
        made += 1
        if bestq < worst:
            worst = bestq
        for i in range(best, n - 1):
            poly[i] = poly[i + 1]
        n -= 1
        if n == 2:
            break
    return worst


@njit(cache=True, nogil=True)
def _stride(n):  # type: ignore[no-untyped-def]
    """A step coprime to n near n / φ: i · step mod n visits 0 … n − 1 once each, scattered."""
    step = max(1, int(n * 0.6180339887498949))
    while True:
        a = step
        b = n
        while b:
            a, b = b, a % b
        if a == 1:
            return step
        step += 1


@njit(cache=True, nogil=True)
def _thin(xyz, tri, alive, cand, target, tol, quality, max_ring, max_passes):  # type: ignore[no-untyped-def]
    """Take vertices of flat fans and of straight edges (see the module) out of `tri` until `target` triangles
    are alive. `cand` comes back True at the vertices that are such and stayed. Returns the triangles alive."""
    n = len(xyz)
    m = len(tri)
    head = np.full(n, -1, np.int32)  # the first corner (3 · triangle + place) of a vertex
    nxt = np.empty(3 * m, np.int32)  # the next corner of the same vertex
    for c in range(3 * m - 1, -1, -1):
        v = tri[c // 3, c % 3]
        nxt[c] = head[v]
        head[v] = c
    changed = np.zeros(n, np.int32)  # the pass in which the fan of a vertex has changed
    tried = np.zeros(n, np.int32)  # the pass in which a vertex could not go
    slot = np.full(n, -1, np.int32)
    ra = np.empty(max_ring, np.int32)
    rb = np.empty(max_ring, np.int32)
    rt = np.empty(max_ring, np.int32)
    ring = np.empty(max_ring, np.int32)
    rtri = np.empty(max_ring, np.int32)
    poly = np.empty(max_ring + 1, np.int32)
    cut = np.empty((max_ring, 3), np.int32)
    d = np.empty((max_ring, 3), np.float64)
    cr = np.empty((max_ring, 3), np.float64)
    c2 = np.empty(max_ring, np.float64)
    px = np.empty(max_ring, np.float64)
    py = np.empty(max_ring, np.float64)
    off = np.empty(max_ring, np.bool_)
    side = np.empty(max_ring, np.uint8)
    nrm = np.empty((2, 3), np.float64)
    tol2 = tol * tol
    queue = np.empty(n, np.int32)
    step = _stride(n) if n > 0 else 1
    for i in range(n):
        queue[i] = (i * step) % n
    live = n
    count = m
    stage = 0
    since = 1  # the first pass of the current step of quality
    passno = 0
    while count > target and live > 0 and stage < len(quality) and passno < max_passes:
        passno += 1
        done = 0
        kept = 0
        for i in range(live):
            v = queue[i]
            floor = quality[stage]
            if count <= target:  # enough: the rest of the queue is not looked at
                break
            if changed[v] == passno or (tried[v] >= since and changed[v] < tried[v]):
                queue[kept] = v  # closed until the next pass, or nothing new since it could not go
                kept += 1
                continue
            k = _fan(v, tri, head, nxt, slot, ra, rb, rt, ring, rtri)
            if k <= 0:
                if k == 0:
                    cand[v] = False
                else:
                    queue[kept] = v
                    kept += 1
                continue
            for s in range(k):
                r = ring[s]
                d[s, 0] = xyz[r, 0] - xyz[v, 0]
                d[s, 1] = xyz[r, 1] - xyz[v, 1]
                d[s, 2] = xyz[r, 2] - xyz[v, 2]
            kind, p, q = _planes(k, d, cr, c2, off, side, nrm, tol2)
            if kind == 0:
                cand[v] = False
                continue
            least = _least(k, d, c2)
            if least < floor:  # the fan has slivers of its own: the cut may be as bad, not worse
                floor = least
            fits = False
            if kind == 1:  # one polygon: the whole ring
                for s in range(k):
                    poly[s] = s
                _project(poly, k, d, nrm, 0, px, py)
                fits = _ears(poly, k, px, py, floor, v, ring, tri, head, nxt, cut, 0) >= 0.0
            elif not _edge(ring[p], ring[q], v, tri, head, nxt):  # two: ring p … q and ring q … p
                first = 0
                s = p
                while True:
                    poly[first] = s
                    first += 1
                    if s == q:
                        break
                    s = s + 1 if s + 1 < k else 0
                _project(poly, first, d, nrm, 0, px, py)
                if _ears(poly, first, px, py, floor, v, ring, tri, head, nxt, cut, 0) >= 0.0:
                    second = 0
                    s = q
                    while True:
                        poly[second] = s
                        second += 1
                        if s == p:
                            break
                        s = s + 1 if s + 1 < k else 0
                    _project(poly, second, d, nrm, 1, px, py)
                    fits = _ears(poly, second, px, py, floor, v, ring, tri, head, nxt, cut, first - 2) >= 0.0
            if not fits:
                tried[v] = passno
                queue[kept] = v
                kept += 1
                continue
            # the fan goes; the k − 2 triangles of the cut take the places of its first triangles
            for s in range(k):
                alive[rtri[s]] = False
            for s in range(k):
                _prune(ring[s], alive, head, nxt)
                changed[ring[s]] = passno
            head[v] = -1
            cand[v] = False
            for place in range(k - 2):
                t = rtri[place]
                alive[t] = True
                for j in range(3):
                    r = ring[cut[place, j]]
                    c = 3 * t + j
                    tri[t, j] = r
                    nxt[c] = head[r]
                    head[r] = c
            count -= 2
            done += 1
        if count <= target:
            break
        live = kept
        if done == 0:
            stage += 1
            since = passno + 1
    return count


def thin(local: Any, tri: Any, target: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Take the vertices of flat regions and of straight edges between them out of a mesh until about
    `target` triangles are left (see the module). Returns (xyz, tri, held): the vertices that are left
    (float64, a subset of the given ones, bit for bit, in their order), the triangles over them (int32) and,
    per vertex, True where a vertex of a flat region or a straight edge stayed — pyfqmr must not move it.
    A mesh without such vertices comes back as it is: the same arrays."""
    xyz = np.ascontiguousarray(local, dtype=np.float64)
    faces = np.asarray(tri)
    m = len(faces)
    if m == 0 or m <= int(target):
        return local, tri, np.zeros(len(xyz), dtype=np.bool_)
    if not xyz.flags.writeable:
        xyz = xyz.copy()  # one compiled variant of the kernel: numba tells read-only arrays apart
    work = np.array(faces, dtype=np.int32, order="C")
    alive = np.ones(m, dtype=np.bool_)
    cand = np.ones(len(xyz), dtype=np.bool_)
    quality = np.array(QUALITY, dtype=np.float64)
    left = int(_thin(xyz, work, alive, cand, max(int(target), 1), FLAT_TOL, quality, MAX_RING, MAX_PASSES))
    if left == m:
        used = np.zeros(len(xyz), dtype=np.bool_)
        used[work.reshape(-1)] = True
        return local, tri, cand & used
    used_ids, inverse = np.unique(work[alive], return_inverse=True)
    out = np.ascontiguousarray(inverse.reshape(-1, 3), dtype=np.int32)
    return np.ascontiguousarray(xyz[used_ids]), out, cand[used_ids]


def _rows(a: np.ndarray) -> np.ndarray:
    """One item of 24 bytes per vertex: equal items are the same three float64 numbers, bit for bit."""
    return np.ascontiguousarray(a, dtype=np.float64).view(np.dtype((np.void, 24))).reshape(-1)


def join(
    xyz: np.ndarray, kept: np.ndarray, seam: np.ndarray, rest_v: np.ndarray, rest_t: np.ndarray
) -> tuple[np.ndarray, np.ndarray] | None:
    """A mesh of two pieces: (rest_v, rest_t) — the triangles pyfqmr was given, simplified with their border
    kept — and `kept`, triangles over `xyz` that were held back. `seam` — the vertices of `xyz` that both
    pieces had: each must be among `rest_v` bit for bit (it was on the border of what pyfqmr got) and the
    kept triangles are put on it there; None when one is not — the pieces would not join."""
    own, inverse = np.unique(kept, return_inverse=True)
    n = len(rest_v)
    _uniq, first, back = np.unique(np.concatenate([_rows(rest_v), _rows(xyz[own])]), return_index=True, return_inverse=True)
    at = first[back[n:]]  # where the coordinates of a vertex of the kept triangles are met first
    shared = np.isin(own, seam)
    if not (at[shared] < n).all():
        return None
    index = np.where(shared, at, n + np.cumsum(~shared) - 1)
    out_v = np.concatenate([np.asarray(rest_v, dtype=np.float64), xyz[own][~shared]])
    out_t = np.concatenate([np.asarray(rest_t, dtype=np.int32), index[inverse].reshape(-1, 3).astype(np.int32)])
    return np.ascontiguousarray(out_v), np.ascontiguousarray(out_t)


def bordered(tri: np.ndarray, vertices: np.ndarray) -> bool:
    """Whether every one of `vertices` is on the border of the mesh: on an edge of one triangle only. pyfqmr
    keeps such vertices; a vertex of an edge of three triangles that lost one of them is not such."""
    n = int(tri.max()) + 1 if len(tri) else 0
    a = np.concatenate([tri[:, 0], tri[:, 1], tri[:, 2]]).astype(np.int64)
    b = np.concatenate([tri[:, 1], tri[:, 2], tri[:, 0]]).astype(np.int64)
    keys, counts = np.unique(np.minimum(a, b) * n + np.maximum(a, b), return_counts=True)
    single = keys[counts == 1]
    on = np.zeros(n, dtype=np.bool_)
    on[single // n] = True
    on[single % n] = True
    return bool(on[np.asarray(vertices, dtype=np.int64)].all())


__all__ = ["FLAT_TOL", "MAX_RING", "QUALITY", "bordered", "join", "thin"]
