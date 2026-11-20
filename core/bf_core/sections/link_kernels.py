"""numba kernels of the links of a series (ТЗ 8.1.4; ADR 0016 §8): exact orientation signs, a point in a polygon,
closed outlines that meet, two polygons whose projections overlap, the links of two neighbouring sections.
Imported lazily by `links.py`, never before BF-READY.

Exact signs. The orientation of a, b, c is the sign of (ax − cx)(by − cy) − (ay − cy)(bx − cx). The float64 value
is taken when it is certain: the signs of the two products differ or one is zero, or |det| is at least
Shewchuk's bound (3 + 16ε)ε·(|detleft| + |detright|). Otherwise the determinant is summed exactly: each
difference is a two-term expansion (two_sum), each of the eight products of their parts two terms (Dekker's
split), and the sixteen terms are grown into a nonoverlapping expansion (zero elimination) whose largest
component has the sign of the sum (J. R. Shewchuk, «Adaptive precision floating-point arithmetic and fast robust
geometric predicates», 1997). No fused multiply-add: numba does not contract without fastmath.

Overlap of two polygons (closed sets, nonzero winding): their bounding boxes meet, and a vertex of one is inside
the other or on its outline, or two edges meet (closed segments: touching counts). Before the test both are
moved by the centre of the pair (one subtraction per vertex, monotonic, so the boxes move the same way); the
edges are compared by a sweep along u of the edges that meet the common box.
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

EPS = 1.1102230246251565e-16  # 2^-53
SPLITTER = 134217729.0  # 2^27 + 1
CCW_A = (3.0 + 16.0 * EPS) * EPS


@njit(cache=True, nogil=True)
def _two_sum(a, b):  # type: ignore[no-untyped-def]
    x = a + b
    bv = x - a
    av = x - bv
    return x, (a - av) + (b - bv)


@njit(cache=True, nogil=True)
def _two_product(a, b):  # type: ignore[no-untyped-def]
    x = a * b
    c = SPLITTER * a
    ahi = c - (c - a)
    alo = a - ahi
    c = SPLITTER * b
    bhi = c - (c - b)
    blo = b - bhi
    err = x - ahi * bhi
    err = err - alo * bhi
    err = err - ahi * blo
    return x, alo * blo - err


@njit(cache=True, nogil=True)
def _grow(e, n, q):  # type: ignore[no-untyped-def]
    """e[:n] + q as a nonoverlapping expansion in e (zero components dropped); its new length."""
    m = 0
    for j in range(n):
        q, h = _two_sum(q, e[j])
        if h != 0.0:
            e[m] = h
            m += 1
    if q != 0.0:
        e[m] = q
        m += 1
    return m


@njit(cache=True, nogil=True)
def orient_exact(ax, ay, bx, by, cx, cy):  # type: ignore[no-untyped-def]
    """The exact sign of (ax − cx)(by − cy) − (ay − cy)(bx − cx)."""
    acx, acxt = _two_sum(ax, -cx)
    bcx, bcxt = _two_sum(bx, -cx)
    acy, acyt = _two_sum(ay, -cy)
    bcy, bcyt = _two_sum(by, -cy)
    e = np.empty(17)
    n = 0
    for p, q, s in (
        (acx, bcy, 1.0), (acx, bcyt, 1.0), (acxt, bcy, 1.0), (acxt, bcyt, 1.0),
        (acy, bcx, -1.0), (acy, bcxt, -1.0), (acyt, bcx, -1.0), (acyt, bcxt, -1.0),
    ):  # fmt: skip
        hi, lo = _two_product(p, q)
        n = _grow(e, n, s * lo)
        n = _grow(e, n, s * hi)
    if n == 0:
        return 0
    return 1 if e[n - 1] > 0.0 else -1


@njit(cache=True, nogil=True)
def _sign(x):  # type: ignore[no-untyped-def]
    return 1 if x > 0.0 else (-1 if x < 0.0 else 0)


@njit(cache=True, nogil=True)
def orient(ax, ay, bx, by, cx, cy):  # type: ignore[no-untyped-def]
    """+1 — c is to the left of a → b, −1 — to the right, 0 — on the line; exact."""
    detleft = (ax - cx) * (by - cy)
    detright = (ay - cy) * (bx - cx)
    det = detleft - detright
    if detleft > 0.0:
        if detright <= 0.0:
            return _sign(det)
        detsum = detleft + detright
    elif detleft < 0.0:
        if detright >= 0.0:
            return _sign(det)
        detsum = -detleft - detright
    else:
        return _sign(det)
    bound = CCW_A * detsum
    if det >= bound or -det >= bound:
        return _sign(det)
    return orient_exact(ax, ay, bx, by, cx, cy)


@njit(cache=True, nogil=True)
def _between(a, b, x):  # type: ignore[no-untyped-def]
    return (a <= x <= b) if a <= b else (b <= x <= a)


@njit(cache=True, nogil=True)
def where(poly, px, py):  # type: ignore[no-untyped-def]
    """1 — (px, py) is inside the closed polygon `poly` (k×2, nonzero winding), 0 — on its outline, −1 — outside."""
    n = poly.shape[0]
    wn = 0
    for i in range(n):
        j = i + 1 if i + 1 < n else 0
        ax, ay, bx, by = poly[i, 0], poly[i, 1], poly[j, 0], poly[j, 1]
        if py < min(ay, by) or py > max(ay, by):
            continue
        o = orient(ax, ay, bx, by, px, py)
        if o == 0:
            if _between(ax, bx, px):
                return 0
            continue
        if ay <= py < by:
            if o > 0:
                wn += 1
        elif by <= py < ay:
            if o < 0:
                wn -= 1
    return 1 if wn != 0 else -1


@njit(cache=True, nogil=True)
def segments_meet(ax, ay, bx, by, cx, cy, dx, dy):  # type: ignore[no-untyped-def]
    """The closed segments ab and cd have a common point."""
    d1 = orient(cx, cy, dx, dy, ax, ay)
    d2 = orient(cx, cy, dx, dy, bx, by)
    d3 = orient(ax, ay, bx, by, cx, cy)
    d4 = orient(ax, ay, bx, by, dx, dy)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return True
    if d1 == 0 and _between(cx, dx, ax) and _between(cy, dy, ay):
        return True
    if d2 == 0 and _between(cx, dx, bx) and _between(cy, dy, by):
        return True
    if d3 == 0 and _between(ax, bx, cx) and _between(ay, by, cy):
        return True
    return d4 == 0 and _between(ax, bx, dx) and _between(ay, by, dy)


@njit(cache=True, nogil=True)
def _edges(p, x0, y0, x1, y1):  # type: ignore[no-untyped-def]
    """The edges of `p` that meet the box, sorted by their smallest u: (numbers, umin, umax, wmin, wmax)."""
    n = p.shape[0]
    idx = np.empty(n, dtype=np.int64)
    k = 0
    for i in range(n):
        j = i + 1 if i + 1 < n else 0
        if (max(p[i, 0], p[j, 0]) >= x0 and min(p[i, 0], p[j, 0]) <= x1
                and max(p[i, 1], p[j, 1]) >= y0 and min(p[i, 1], p[j, 1]) <= y1):  # fmt: skip
            idx[k] = i
            k += 1
    idx = idx[:k]
    lo = np.empty(k)
    for t in range(k):
        i = idx[t]
        j = i + 1 if i + 1 < n else 0
        lo[t] = min(p[i, 0], p[j, 0])
    order = np.argsort(lo, kind="mergesort")
    idx = idx[order]
    umin, umax, wmin, wmax = np.empty(k), np.empty(k), np.empty(k), np.empty(k)
    for t in range(k):
        i = idx[t]
        j = i + 1 if i + 1 < n else 0
        umin[t], umax[t] = min(p[i, 0], p[j, 0]), max(p[i, 0], p[j, 0])
        wmin[t], wmax[t] = min(p[i, 1], p[j, 1]), max(p[i, 1], p[j, 1])
    return idx, umin, umax, wmin, wmax


@njit(cache=True, nogil=True)
def _meet(p, i, q, j):  # type: ignore[no-untyped-def]
    i2 = i + 1 if i + 1 < p.shape[0] else 0
    j2 = j + 1 if j + 1 < q.shape[0] else 0
    return segments_meet(p[i, 0], p[i, 1], p[i2, 0], p[i2, 1], q[j, 0], q[j, 1], q[j2, 0], q[j2, 1])


@njit(cache=True, nogil=True)
def outlines_meet(pa, pb, x0, y0, x1, y1):  # type: ignore[no-untyped-def]
    """An edge of `pa` and an edge of `pb` meet; only the edges that meet the box [x0, x1] × [y0, y1] count."""
    ia, ua0, ua1, wa0, wa1 = _edges(pa, x0, y0, x1, y1)
    ib, ub0, ub1, wb0, wb1 = _edges(pb, x0, y0, x1, y1)
    na, nb = ia.shape[0], ib.shape[0]
    if na == 0 or nb == 0:
        return False
    act_a = np.empty(na, dtype=np.int64)
    act_b = np.empty(nb, dtype=np.int64)
    ka = kb = 0
    i = j = 0
    while i < na or j < nb:
        if j >= nb or (i < na and ua0[i] <= ub0[j]):
            x = ua0[i]
            m = 0
            for t in range(kb):
                f = act_b[t]
                if ub1[f] >= x:
                    act_b[m] = f
                    m += 1
            kb = m
            for t in range(kb):
                f = act_b[t]
                if wb1[f] >= wa0[i] and wb0[f] <= wa1[i] and _meet(pa, ia[i], pb, ib[f]):
                    return True
            act_a[ka] = i
            ka += 1
            i += 1
        else:
            x = ub0[j]
            m = 0
            for t in range(ka):
                f = act_a[t]
                if ua1[f] >= x:
                    act_a[m] = f
                    m += 1
            ka = m
            for t in range(ka):
                f = act_a[t]
                if wa1[f] >= wb0[j] and wa0[f] <= wb1[j] and _meet(pa, ia[f], pb, ib[j]):
                    return True
            act_b[kb] = j
            kb += 1
            j += 1
    return False


@njit(cache=True, nogil=True)
def overlap(uw, a0, a1, b0, b1, box_a, box_b):  # type: ignore[no-untyped-def]
    """The polygons uw[a0:a1] and uw[b0:b1] (closed, no repeated vertex) have a common point; their boxes
    [umin, wmin, umax, wmax] meet (the caller checked it)."""
    cu = 0.5 * (min(box_a[0], box_b[0]) + max(box_a[2], box_b[2]))
    cw = 0.5 * (min(box_a[1], box_b[1]) + max(box_a[3], box_b[3]))
    pa = np.empty((a1 - a0, 2))
    pb = np.empty((b1 - b0, 2))
    for i in range(a1 - a0):
        pa[i, 0] = uw[a0 + i, 0] - cu
        pa[i, 1] = uw[a0 + i, 1] - cw
    for i in range(b1 - b0):
        pb[i, 0] = uw[b0 + i, 0] - cu
        pb[i, 1] = uw[b0 + i, 1] - cw
    if where(pb, pa[0, 0], pa[0, 1]) >= 0 or where(pa, pb[0, 0], pb[0, 1]) >= 0:
        return True
    x0 = max(box_a[0], box_b[0]) - cu
    y0 = max(box_a[1], box_b[1]) - cw
    x1 = min(box_a[2], box_b[2]) - cu
    y1 = min(box_a[3], box_b[3]) - cw
    return outlines_meet(pa, pb, x0, y0, x1, y1)


@njit(cache=True, nogil=True)
def section_pair(uw, offsets, lower, upper, box, cu, cw, limit, out_a, out_b, out_how):  # type: ignore[no-untyped-def]
    """Links of the contours `lower` (section k) and `upper` (k + 1): how = 1 — the projections overlap, 2 — they
    do not, the centres are at most `limit` apart. Written to out_*; returns their number."""
    n = 0
    for ia in range(lower.shape[0]):
        a = lower[ia]
        for ib in range(upper.shape[0]):
            b = upper[ib]
            how = 0
            if box[a, 0] <= box[b, 2] and box[b, 0] <= box[a, 2] and box[a, 1] <= box[b, 3] and box[b, 1] <= box[a, 3]:
                if overlap(uw, offsets[a], offsets[a + 1], offsets[b], offsets[b + 1], box[a], box[b]):
                    how = 1
            if how == 0 and math.hypot(cu[a] - cu[b], cw[a] - cw[b]) <= limit:
                how = 2
            if how != 0:
                out_a[n] = a
                out_b[n] = b
                out_how[n] = how
                n += 1
    return n
