"""The numba kernels of `swath_sub` (подшаг 17.10): the share of a box below a plane (`_f1`, `_f2`, `_f3`,
`_cdf`), the box of a row of a model v2 in the project axes (`_geom`), the extent of the rows taken (`_extent`) and
the sums of the swaths of a group of rows (`_sums`). All `cache=True, nogil=True`, no `prange`, no `fastmath`; the
meaning of the numbers is in the docstring of `swath_sub`."""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

_FILTERED, _MISSING, _BAD_WEIGHT, _OUTSIDE, _BAD_CELL = range(5)
_DROP = 1e-13  # an amplitude below this share of the largest one is a rounding error of a zero
_GAUSS = 0.5773502691896257  # 1/√3: two-point rule of Gauss, exact up to degree 3
_WELL = 0.02  # amplitudes with c1 ≥ _WELL·c3: the closed formula of three terms loses ≤ 1e-12 to the cancellation

# --- the share of a box below a plane ---------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _f1(x, b):  # type: ignore[no-untyped-def]
    """P(b·U ≤ x)."""
    if x <= 0.0:
        return 0.0
    if x >= b:
        return 1.0
    return x / b


@njit(cache=True, nogil=True)
def _f2(r, b1, b2):  # type: ignore[no-untyped-def]
    """P(b1·U1 + b2·U2 ≤ r) = ∫₀¹ _f1(r − b2·v, b1) dv: the integrand is linear between v₁ = (r − b1)/b2 and
    v₂ = r/b2, 1 before and 0 after — its mean on the middle part is its value at the middle."""
    if r <= 0.0:
        return 0.0
    if r >= b1 + b2:
        return 1.0
    v1 = min(max((r - b1) / b2, 0.0), 1.0)
    v2 = min(max(r / b2, 0.0), 1.0)
    mid = (v1 + v2) * 0.5
    g = min(max((r - b2 * mid) / b1, 0.0), 1.0)
    return v1 + (v2 - v1) * g


@njit(cache=True, nogil=True)
def _f3(x, b1, b2, b3):  # type: ignore[no-untyped-def]
    """P(b1·U1 + b2·U2 + b3·U3 ≤ x), b1 ≤ b2 ≤ b3. Amplitudes of one order: the closed formula
    (x³ − Σ(x − bᵢ)₊³ + Σ(x − bᵢ − bⱼ)₊³) / (6·b1·b2·b3). A small b1 — the formula loses digits to the cancellation —
    ∫₀¹ _f2(x − b3·v, b1, b2) dv: between the breakpoints of _f2 (r = 0, b1, b2, b1 + b2) the integrand is a
    quadratic of v — two points of Gauss are exact."""
    if x <= 0.0:
        return 0.0
    if x >= b1 + b2 + b3:
        return 1.0
    if b1 >= _WELL * b3:
        tot = x * x * x
        for b in (b1, b2, b3):
            d = x - b
            if d > 0.0:
                tot -= d * d * d
        for b in (b1 + b2, b1 + b3, b2 + b3):
            d = x - b
            if d > 0.0:
                tot += d * d * d
        return min(max(tot / (6.0 * b1 * b2 * b3), 0.0), 1.0)
    cut = np.empty(6)
    cut[0] = 0.0
    m = 1
    for r in (0.0, b1, b2, b1 + b2):
        v = (x - r) / b3
        if v > 0.0 and v < 1.0:
            j = m
            while j > 1 and cut[j - 1] > v:
                cut[j] = cut[j - 1]
                j -= 1
            cut[j] = v
            m += 1
    cut[m] = 1.0
    total = 0.0
    for q in range(m):
        a = cut[q]
        h = (cut[q + 1] - a) * 0.5
        c = a + h
        total += h * (_f2(x - b3 * (c - h * _GAUSS), b1, b2) + _f2(x - b3 * (c + h * _GAUSS), b1, b2))
    return min(max(total, 0.0), 1.0)


@njit(cache=True, nogil=True)
def _cdf(x, m, c1, c2, c3):  # type: ignore[no-untyped-def]
    """The share below `x` (from the low end of the box) of a box with m amplitudes c1 ≤ c2 ≤ c3 (m = 1…3)."""
    if m == 1:
        return _f1(x, c1)
    if m == 2:
        return _f2(x, c1, c2)
    return _f3(x, c1, c2, c3)


@njit(cache=True, nogil=True)
def _amps(b0, b1, b2):  # type: ignore[no-untyped-def]
    """(m, c1, c2, c3): the amplitudes that count (a rounding error of a zero is dropped), ascending."""
    top = max(b0, b1, b2)
    lim = top * _DROP
    c1 = c2 = c3 = 0.0
    m = 0
    for b in (b0, b1, b2):
        if b > lim:
            if m == 0:
                c1 = b
            elif m == 1:
                c2 = b
            else:
                c3 = b
            m += 1
    if m >= 2 and c1 > c2:
        c1, c2 = c2, c1
    if m == 3:
        if c2 > c3:
            c2, c3 = c3, c2
        if c1 > c2:
            c1, c2 = c2, c1
    return m, c1, c2, c3


# --- the rows: boxes in the project axes -------------------------------------------------------------------------


@njit(cache=True, nogil=True, inline="always")  # arrays in a call cost a reference count each
def _along(c0, c1, c2, ax, e):  # type: ignore[no-untyped-def]
    """The coordinate along the axis `e` of the frame: the terms in the order x, y, z, a zero component skipped
    (`swath_frame.project`)."""
    t = 0.0
    if ax[e, 0] != 0.0:
        t += c0 * ax[e, 0]
    if ax[e, 1] != 0.0:
        t += c1 * ax[e, 1]
    if ax[e, 2] != 0.0:
        t += c2 * ax[e, 2]
    return t



def _build(tens: bool, sub: bool):  # type: ignore[no-untyped-def]
    """The kernels for a regular step with sub-blocks (`sub`), without them, or for the tensors of OMF (`tens`):
    the kind of the grid is a constant of the compilation — a run-time branch on it in the loop of rows costs 30
    times what the rows do."""

    @njit(cache=True, nogil=True, inline="always")  # arrays in a call cost a reference count each
    def _geom(i, j, k, r, s, w, g, ni, edges, steps):  # type: ignore[no-untyped-def]
        """The box of a row of the parent (i, j, k): the local centre q (3) and the sizes d (3) as `blocks.geometry`
        counts them (q = (i + (s + w/2)/div)·size, d = w·size/div; the middles and steps of the edges on a variable
        step). div is a power of 2: the product with 1/div is the quotient, bit for bit."""
        if tens:
            q0 = (edges[0, i] + edges[0, i + 1]) * 0.5
            q1 = (edges[1, j] + edges[1, j + 1]) * 0.5
            q2 = (edges[2, k] + edges[2, k + 1]) * 0.5
            d0 = steps[0, i]
            d1 = steps[1, j]
            d2 = steps[2, k]
        else:
            if sub:
                s0 = float(s[r, 0])
                s1 = float(s[r, 1])
                s2 = float(s[r, 2])
                w0 = float(w[r, 0])
                w1 = float(w[r, 1])
                w2 = float(w[r, 2])
            else:
                s0 = s1 = s2 = 0.0
                w0 = g[8]
                w1 = g[9]
                w2 = g[10]
            q0 = (float(i) + (s0 + w0 * 0.5) * g[11]) * g[5]
            q1 = (float(j) + (s1 + w1 * 0.5) * g[12]) * g[6]
            q2 = (float(k) + (s2 + w2 * 0.5) * g[13]) * g[7]
            d0 = w0 * g[5] * g[11]
            d1 = w1 * g[6] * g[12]
            d2 = w2 * g[7] * g[13]
        return q0, q1, q2, d0, d1, d2


    @njit(cache=True, nogil=True)
    def _extent(p, s, w, sel, has_sel, g, ni, edges, steps, ax, amp, cells):  # type: ignore[no-untyped-def]
        """The extent of the rows taken along u, v, w: 3×2 (+inf, −inf — none), the number of them and of the numbers
        that are not of the grid."""
        out = np.empty((3, 2))
        out[:, 0] = np.inf
        out[:, 1] = -np.inf
        taken = 0
        bad = 0
        nx = ni[0]
        ny = ni[1]
        row_lo = row_hi = rj = rk = np.int64(0)  # the row of cells along X of the last cell: its j, k
        for r in range(p.shape[0]):
            cell = np.int64(p[r])
            if cell < row_lo or cell >= row_hi:
                if cell < 0 or cell >= cells:
                    bad += 1
                    continue
                row = cell // nx
                row_lo = row * nx
                row_hi = row_lo + nx
                rj = row % ny
                rk = row // ny
            if has_sel and not sel[r]:
                continue
            q0, q1, q2, d0, d1, d2 = _geom(cell - row_lo, rj, rk, r, s, w, g, ni, edges, steps)
            c0 = g[0] + (g[3] * q0 - g[4] * q1)
            c1 = g[1] + (g[4] * q0 + g[3] * q1)
            c2 = g[2] + q2
            taken += 1
            for e in range(3):
                t = _along(c0, c1, c2, ax, e)
                h = 0.5 * (abs(amp[e, 0]) * d0 + abs(amp[e, 1]) * d1 + abs(amp[e, 2]) * d2)
                if t - h < out[e, 0]:
                    out[e, 0] = t - h
                if t + h > out[e, 1]:
                    out[e, 1] = t + h
        return out, taken, bad


    @njit(cache=True, nogil=True)
    def _sums(  # type: ignore[no-untyped-def]
        p, s, w, v, dens, frac, sel, has_sel, has_frac, mode, g, ni, edges, steps, ax, amp, cells, lim, tol, start,
        width, n,
    ):
        """A group of rows into the sums of its swaths: (Σ w, Σ w·v, the number of blocks) by swath and the counts of
        what was left out. Per swath — the cascade of `weighted.pair_sums` (as `swath_blocks._sums`)."""
        rows = p.shape[0]
        levels = 1
        m = rows >> 3
        while m > 0:
            levels += 1
            m >>= 1
        st = np.empty((n, levels, 2))
        depth = np.zeros(n, dtype=np.int64)
        s0 = np.zeros(n)
        s1 = np.zeros(n)
        c = np.zeros(n, dtype=np.int64)
        cnt = np.zeros(5, dtype=np.int64)
        last = n - 1
        v0, v1 = lim[1, 0] - tol[1], lim[1, 1] + tol[1]
        w0, w1 = lim[2, 0] - tol[2], lim[2, 1] + tol[2]
        nx = ni[0]
        ny = ni[1]
        row_lo = row_hi = rj = rk = np.int64(0)
        for r in range(rows):
            cell = np.int64(p[r])
            if cell < row_lo or cell >= row_hi:
                if cell < 0 or cell >= cells:
                    cnt[_BAD_CELL] += 1
                    continue
                row = cell // nx
                row_lo = row * nx
                row_hi = row_lo + nx
                rj = row % ny
                rk = row // ny
            if has_sel and not sel[r]:
                cnt[_FILTERED] += 1
                continue
            x = v[r]
            if not np.isfinite(x):
                cnt[_MISSING] += 1
                continue
            q0, q1, q2, d0, d1, d2 = _geom(cell - row_lo, rj, rk, r, s, w, g, ni, edges, steps)
            if mode == 2:
                wt = 1.0
            else:
                wt = (d0 * d1) * d2
                if has_frac:
                    wt = wt * frac[r]
                if mode == 1:
                    wt = wt * dens[r]
            if not (wt > 0.0 and wt < np.inf):
                cnt[_BAD_WEIGHT] += 1
                continue
            c0 = g[0] + (g[3] * q0 - g[4] * q1)
            c1 = g[1] + (g[4] * q0 + g[3] * q1)
            c2 = g[2] + q2
            tv = _along(c0, c1, c2, ax, 1)
            tw = _along(c0, c1, c2, ax, 2)
            if not (tv >= v0 and tv <= v1 and tw >= w0 and tw <= w1):
                cnt[_OUTSIDE] += 1
                continue
            tu = _along(c0, c1, c2, ax, 0)
            a0 = abs(amp[0, 0]) * d0
            a1 = abs(amp[0, 1]) * d1
            a2 = abs(amp[0, 2]) * d2
            span = a0 + a1 + a2
            tlow = tu - span * 0.5
            thigh = tu + span * 0.5
            eps = 4e-16 * (abs(tu) + span) + 1e-13 * span  # the same plane to the rounding of the coordinate
            lo = max(tlow, lim[0, 0])
            hi = min(thigh, lim[0, 1])
            if not (hi - lo > eps):
                cnt[_OUTSIDE] += 1
                continue
            na, c1a, c2a, c3a = _amps(a0, a1, a2)
            top = c1a + c2a + c3a
            k0 = min(max(int(np.floor((lo - start) / width)), 0), last)
            k1 = min(max(int(np.floor((hi - start) / width)), 0), last)
            prev = -1.0
            got = False
            for k in range(k0, k1 + 1):
                if prev < 0.0:
                    xl = lo - tlow
                    prev = 0.0 if xl <= eps else (1.0 if xl >= top - eps else _cdf(xl, na, c1a, c2a, c3a))
                if k == k1:
                    xh = hi - tlow
                else:
                    xh = start + (k + 1) * width - tlow
                cur = 0.0 if xh <= eps else (1.0 if xh >= top - eps else _cdf(xh, na, c1a, c2a, c3a))
                piece = cur - prev
                prev = cur
                if not piece > 0.0:
                    continue
                got = True
                wp = wt * piece
                s0[k] += wp
                s1[k] += wp * x
                mm = c[k] + 1
                c[k] = mm
                if (mm & 7) == 0:
                    mm >>= 3
                    d = depth[k]
                    q = s0[k]
                    z = s1[k]
                    while (mm & 1) == 0:
                        d -= 1
                        q = st[k, d, 0] + q
                        z = st[k, d, 1] + z
                        mm >>= 1
                    st[k, d, 0] = q
                    st[k, d, 1] = z
                    depth[k] = d + 1
                    s0[k] = 0.0
                    s1[k] = 0.0
            if not got:
                cnt[_OUTSIDE] += 1
        weight = np.empty(n)
        wsum = np.empty(n)
        for k in range(n):
            q = s0[k]
            z = s1[k]
            d = depth[k]
            while d > 0:
                d -= 1
                q = st[k, d, 0] + q
                z = st[k, d, 1] + z
            weight[k] = q
            wsum[k] = z
        return weight, wsum, c, cnt

    return _extent, _sums


_KERNELS = {"plain": _build(False, False), "sub": _build(False, True), "tensors": _build(True, False)}
