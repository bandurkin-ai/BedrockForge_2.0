"""numba kernels of `geo/checks.py`: self-intersections of a triangle mesh on grids of cells (ADR 0013 §4).

Ported from BF `geo/checks.py` (`_segment_hits_triangle`, `_crosses`, `_fill_cells`, `_pair_owned`, `_count_runs`)
with the same test of a pair, so the same pairs are found. Differences:

* the boxes of the triangles are one float32 row each (24 bytes, never smaller than the triangle), not two
  float64 arrays; every cell number is computed from them by one function (`_cell`), so the cells a triangle is
  listed in and the cell that owns a pair always agree;
* a triangle too large for the grid goes to a coarser one (cells `factor` times larger, level by level) instead
  of being tested against every triangle: the pairs of one level are found in its cells, a pair of two levels —
  by looking the finer triangle up in the cells of the coarser level (`scan_levels`). BF's «big against all» was
  quadratic on a mesh of mixed sizes;
* no `prange`: the scans are sequential, do a bounded amount of work per call and go on from where they stopped
  (`state`), so the caller reports progress and can cancel between calls even when all triangles share a cell;
* besides the count: a flag per crossing triangle and an evenly thinned sample of pairs.

A pair is counted once: in the cell that holds the lower corner of the overlap of the two boxes (at the level of
the coarser triangle). The kernels release the GIL. numba is imported with this module: import it lazily.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

PAIRS, SEEN, KEPT, STRIDE, MARKED = range(5)  # the counters of a scan (int64[5]); STRIDE starts at 1

# --- one triangle against another ------------------------------------------------------------------------


@njit(cache=True, nogil=True, inline="always")
def _sub(a, b):  # type: ignore[no-untyped-def]
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


@njit(cache=True, nogil=True, inline="always")
def _cross(a, b):  # type: ignore[no-untyped-def]
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


@njit(cache=True, nogil=True, inline="always")
def _dot(a, b):  # type: ignore[no-untyped-def]
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


@njit(cache=True, nogil=True)
def _segment_hit(p, q, a, b, c, eps):  # type: ignore[no-untyped-def]
    """Möller–Trumbore: does the open segment p→q pierce the closed triangle abc? Returns (hit, t): the point
    is p + t · (q − p).

    The segment must cross the plane strictly between its ends (a triangle that only touches another with a
    corner does not cross it), but the point may lie on an edge of the triangle: two meshes that pass through
    each other along a line of their grids are caught. A segment in the plane of the triangle never counts."""
    d = _sub(q, p)
    e1 = _sub(b, a)
    e2 = _sub(c, a)
    h = _cross(d, e2)
    det = _dot(e1, h)
    if abs(det) < 1e-300:
        return False, 0.0
    inv = 1.0 / det
    s = _sub(p, a)
    u = _dot(s, h) * inv
    if u < -eps or u > 1.0 + eps:
        return False, 0.0
    qq = _cross(s, e1)
    w = _dot(d, qq) * inv
    if w < -eps or u + w > 1.0 + eps:
        return False, 0.0
    t = _dot(e2, qq) * inv
    return eps < t < 1.0 - eps, t


@njit(cache=True, nogil=True, inline="always")
def _corner(v, k):  # type: ignore[no-untyped-def]
    return (v[k, 0], v[k, 1], v[k, 2])


@njit(cache=True, nogil=True, inline="always")
def _put(s0, s1, w, out):  # type: ignore[no-untyped-def]
    for k in range(3):
        out[k] = s0[k] + w * (s1[k] - s0[k])
    return True


@njit(cache=True, nogil=True)
def crossing(v, t, i, j, eps, out):  # type: ignore[no-untyped-def]
    """Do the triangles i and j cross (an edge of one pierces the other)? `out` (3) gets the point where the
    first such edge does. Coplanar triangles never cross."""
    a, b, c = _corner(v, t[i, 0]), _corner(v, t[i, 1]), _corner(v, t[i, 2])
    p, q, r = _corner(v, t[j, 0]), _corner(v, t[j, 1]), _corner(v, t[j, 2])
    # all the corners of one strictly on one side of the plane of the other: no crossing
    n1 = _cross(_sub(b, a), _sub(c, a))
    dp, dq, dr = _dot(n1, _sub(p, a)), _dot(n1, _sub(q, a)), _dot(n1, _sub(r, a))
    if (dp > 0 and dq > 0 and dr > 0) or (dp < 0 and dq < 0 and dr < 0):
        return False
    n2 = _cross(_sub(q, p), _sub(r, p))
    da, db, dc = _dot(n2, _sub(a, p)), _dot(n2, _sub(b, p)), _dot(n2, _sub(c, p))
    if (da > 0 and db > 0 and dc > 0) or (da < 0 and db < 0 and dc < 0):
        return False
    hit, w = _segment_hit(a, b, p, q, r, eps)
    if hit:
        return _put(a, b, w, out)
    hit, w = _segment_hit(b, c, p, q, r, eps)
    if hit:
        return _put(b, c, w, out)
    hit, w = _segment_hit(c, a, p, q, r, eps)
    if hit:
        return _put(c, a, w, out)
    hit, w = _segment_hit(p, q, a, b, c, eps)
    if hit:
        return _put(p, q, w, out)
    hit, w = _segment_hit(q, r, a, b, c, eps)
    if hit:
        return _put(q, r, w, out)
    hit, w = _segment_hit(r, p, a, b, c, eps)
    if hit:
        return _put(r, p, w, out)
    return False


@njit(cache=True, nogil=True)
def crossings(v, t, pairs, eps, out):  # type: ignore[no-untyped-def]
    """`out[k]` — the point where the triangles `pairs[k]` cross. Returns how many of the pairs do."""
    point = np.empty(3, dtype=np.float64)
    n = 0
    for k in range(pairs.shape[0]):
        if crossing(v, t, pairs[k, 0], pairs[k, 1], eps, point):
            out[n, 0], out[n, 1], out[n, 2] = point[0], point[1], point[2]
            n += 1
    return n


# --- boxes and cells -------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def boxes(v, t, box):  # type: ignore[no-untyped-def]
    """`box[i]` = [xmin, ymin, zmin, xmax, ymax, zmax] of triangle i as float32 rounded outwards. Returns the
    extent of all the boxes (6 float64)."""
    down, up = np.float32(-np.inf), np.float32(np.inf)
    whole = np.empty(6, dtype=np.float64)
    whole[:3] = np.inf
    whole[3:] = -np.inf
    for i in range(t.shape[0]):
        i0, i1, i2 = t[i, 0], t[i, 1], t[i, 2]
        for k in range(3):
            lo = min(v[i0, k], v[i1, k], v[i2, k])
            hi = max(v[i0, k], v[i1, k], v[i2, k])
            a = np.float32(lo)
            if a > lo:
                a = np.nextafter(a, down)
            b = np.float32(hi)
            if b < hi:
                b = np.nextafter(b, up)
            box[i, k] = a
            box[i, k + 3] = b
            if a < whole[k]:
                whole[k] = a
            if b > whole[k + 3]:
                whole[k + 3] = b
    return whole


@njit(cache=True, nogil=True)
def extents(box, out):  # type: ignore[no-untyped-def]
    """The largest side of every box."""
    for i in range(box.shape[0]):
        out[i] = max(box[i, 3] - box[i, 0], box[i, 4] - box[i, 1], box[i, 5] - box[i, 2])


@njit(cache=True, nogil=True, inline="always")
def _cell(x, g, c):  # type: ignore[no-untyped-def]
    """The number of the cell of size c, counted from g, that holds the coordinate x."""
    return int((np.float64(x) - g) // c)


@njit(cache=True, nogil=True)
def grid_dims(whole, size, dims):  # type: ignore[no-untyped-def]
    """`dims[L]` — cells along X, Y and Z of the grid of level L (cells of `size[L]`) over the extent `whole`."""
    for level in range(size.shape[0]):
        for k in range(3):
            dims[level, k] = _cell(whole[k + 3], whole[k], size[level]) + 1


@njit(cache=True, nogil=True)
def assign_levels(box, gmin, size, max_cells, level, count):  # type: ignore[no-untyped-def]
    """`level[i]` — the finest grid in which triangle i covers at most `max_cells` cells (the coarsest one takes
    whatever is left), `count[i]` — how many it covers there. Returns the triangles of every level."""
    top = size.shape[0] - 1
    per = np.zeros(top + 1, dtype=np.int64)
    for i in range(box.shape[0]):
        n = 1
        found = top
        for lv in range(top + 1):
            c = size[lv]
            n = 1
            for k in range(3):
                n *= _cell(box[i, k + 3], gmin[k], c) - _cell(box[i, k], gmin[k], c) + 1
            if n <= max_cells:
                found = lv
                break
        level[i] = found
        count[i] = n
        per[found] += 1
    return per


@njit(cache=True, nogil=True)
def fill(box, gmin, size, dims, offs, level, starts, keys, ids):  # type: ignore[no-untyped-def]
    """List every triangle in the cells its box covers at its level: `keys[p]` — the cell (`offs[level]` + its
    number in the grid of the level), `ids[p]` — the triangle; triangle i writes from `starts[i]`."""
    for i in range(box.shape[0]):
        lv = level[i]
        c = size[lv]
        x0, x1 = _cell(box[i, 0], gmin[0], c), _cell(box[i, 3], gmin[0], c)
        y0, y1 = _cell(box[i, 1], gmin[1], c), _cell(box[i, 4], gmin[1], c)
        z0, z1 = _cell(box[i, 2], gmin[2], c), _cell(box[i, 5], gmin[2], c)
        d1, d2, off = dims[lv, 1], dims[lv, 2], offs[lv]
        p = starts[i]
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    keys[p] = off + (x * d1 + y) * d2 + z
                    ids[p] = i
                    p += 1


@njit(cache=True, nogil=True)
def combine(keys, ids, m):  # type: ignore[no-untyped-def]
    """keys[p] = keys[p] · m + ids[p]: one number to sort by cell, then by triangle."""
    for p in range(keys.shape[0]):
        keys[p] = keys[p] * m + ids[p]


@njit(cache=True, nogil=True)
def split(keys, ids, m):  # type: ignore[no-untyped-def]
    """The reverse of `combine`, in place."""
    for p in range(keys.shape[0]):
        ids[p] = keys[p] % m
        keys[p] //= m


# --- the scans -------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True, inline="always")
def _found(i, j, mark, sample, counters):  # type: ignore[no-untyped-def]
    """Count a crossing pair, flag its triangles and keep it in the sample: every `STRIDE`-th pair is kept; when
    the sample is full, every second one of it stays and the stride doubles."""
    counters[PAIRS] += 1
    if mark[i] == 0:
        mark[i] = 1
        counters[MARKED] += 1
    if mark[j] == 0:
        mark[j] = 1
        counters[MARKED] += 1
    cap = sample.shape[0]
    if cap > 0 and counters[SEEN] % counters[STRIDE] == 0:
        if counters[KEPT] == cap:
            half = (cap + 1) // 2
            for q in range(half):
                sample[q, 0], sample[q, 1] = sample[2 * q, 0], sample[2 * q, 1]
            counters[KEPT] = half
            counters[STRIDE] *= 2
        if counters[SEEN] % counters[STRIDE] == 0:
            sample[counters[KEPT], 0], sample[counters[KEPT], 1] = i, j
            counters[KEPT] += 1
    counters[SEEN] += 1


@njit(cache=True, nogil=True)
def scan_runs(  # type: ignore[no-untyped-def]
    v, t, box, gmin, size, dims, offs, level, ukeys, ids, runs, state, budget, eps, mark, sample, counters
):
    """Pairs of triangles of one level: within every cell (`runs[r] … runs[r + 1]` of `ids`, the cell is
    `ukeys[r]`). `state` = [run, position in `ids`] says where to go on from and is updated; the scan stops
    after about `budget` pairs of entries. Returns how many it looked at; `state[0] == len(ukeys)` — done."""
    point = np.empty(3, dtype=np.float64)
    work = 0
    r = state[0]
    x = state[1]
    count = ukeys.shape[0]
    while r < count:
        end = runs[r + 1]
        key = ukeys[r]
        lv = level[ids[runs[r]]]
        c = size[lv]
        d1, d2, off = dims[lv, 1], dims[lv, 2], offs[lv]
        g0, g1, g2 = gmin[0], gmin[1], gmin[2]
        while x < end - 1:
            i = ids[x]
            b0, b1, b2, b3, b4, b5 = box[i, 0], box[i, 1], box[i, 2], box[i, 3], box[i, 4], box[i, 5]
            i0, i1, i2 = t[i, 0], t[i, 1], t[i, 2]
            for y in range(x + 1, end):
                j = ids[y]
                a0 = max(b0, box[j, 0])
                if a0 > min(b3, box[j, 3]):
                    continue
                a1 = max(b1, box[j, 1])
                if a1 > min(b4, box[j, 4]):
                    continue
                a2 = max(b2, box[j, 2])
                if a2 > min(b5, box[j, 5]):
                    continue
                if off + (_cell(a0, g0, c) * d1 + _cell(a1, g1, c)) * d2 + _cell(a2, g2, c) != key:
                    continue  # another cell owns the pair
                j0, j1, j2 = t[j, 0], t[j, 1], t[j, 2]
                if i0 == j0 or i0 == j1 or i0 == j2 or i1 == j0 or i1 == j1 or i1 == j2 or i2 == j0 or i2 == j1 or i2 == j2:  # fmt: skip
                    continue
                if crossing(v, t, i, j, eps, point):
                    _found(i, j, mark, sample, counters)
            work += end - x
            x += 1
            if work >= budget:
                state[0], state[1] = r, x
                return work
        work += 1
        x = end
        r += 1
    state[0], state[1] = count, x
    return work


@njit(cache=True, nogil=True, inline="always")
def _run_of(ukeys, first, last, key):  # type: ignore[no-untyped-def]
    """The run whose cell is `key` among the runs first … last − 1 (sorted by cell); −1 — there is none."""
    lo, hi = first, last
    while lo < hi:
        mid = (lo + hi) >> 1
        if ukeys[mid] < key:
            lo = mid + 1
        else:
            hi = mid
    if lo < last and ukeys[lo] == key:
        return lo
    return -1


@njit(cache=True, nogil=True)
def scan_levels(  # type: ignore[no-untyped-def]
    v, t, box, gmin, size, dims, offs, level, ukeys, ids, runs, level_runs, state, budget, eps, mark, sample, counters
):
    """Pairs of triangles of different levels: every triangle against the cells of the coarser grids its box
    covers (`level_runs[L] … level_runs[L + 1]` — the runs of level L). `state[0]` — the triangle to go on
    from (updated); stops after about `budget` steps. Returns the steps done; `state[0] == len(t)` — done."""
    point = np.empty(3, dtype=np.float64)
    m = t.shape[0]
    top = size.shape[0] - 1
    work = 0
    i = state[0]
    g0, g1, g2 = gmin[0], gmin[1], gmin[2]
    while i < m:
        b0, b1, b2, b3, b4, b5 = box[i, 0], box[i, 1], box[i, 2], box[i, 3], box[i, 4], box[i, 5]
        i0, i1, i2 = t[i, 0], t[i, 1], t[i, 2]
        for lv in range(level[i] + 1, top + 1):
            first, last = level_runs[lv], level_runs[lv + 1]
            if first == last:
                continue
            c = size[lv]
            d1, d2, off = dims[lv, 1], dims[lv, 2], offs[lv]
            for x in range(_cell(b0, g0, c), _cell(b3, g0, c) + 1):
                for y in range(_cell(b1, g1, c), _cell(b4, g1, c) + 1):
                    for z in range(_cell(b2, g2, c), _cell(b5, g2, c) + 1):
                        key = off + (x * d1 + y) * d2 + z
                        r = _run_of(ukeys, first, last, key)
                        work += 1
                        if r < 0:
                            continue
                        work += runs[r + 1] - runs[r]
                        for p in range(runs[r], runs[r + 1]):
                            j = ids[p]
                            a0 = max(b0, box[j, 0])
                            if a0 > min(b3, box[j, 3]):
                                continue
                            a1 = max(b1, box[j, 1])
                            if a1 > min(b4, box[j, 4]):
                                continue
                            a2 = max(b2, box[j, 2])
                            if a2 > min(b5, box[j, 5]):
                                continue
                            if off + (_cell(a0, g0, c) * d1 + _cell(a1, g1, c)) * d2 + _cell(a2, g2, c) != key:
                                continue
                            j0, j1, j2 = t[j, 0], t[j, 1], t[j, 2]
                            if i0 == j0 or i0 == j1 or i0 == j2 or i1 == j0 or i1 == j1 or i1 == j2 or i2 == j0 or i2 == j1 or i2 == j2:  # fmt: skip
                                continue
                            if crossing(v, t, i, j, eps, point):
                                _found(min(i, j), max(i, j), mark, sample, counters)
        work += 1
        i += 1
        if work >= budget:
            break
    state[0] = i
    return work
