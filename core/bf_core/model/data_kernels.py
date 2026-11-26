"""The kernel of the points along holes (ТЗ 8.9.2, ADR 0026 решение 5) — numba, imported only inside the functions of
`data_holes.py`, so the modules of job handlers do not load numba at import.

`domain_strip(h0, h1, …)` walks the holes h0 ≤ h < h1 (rows `starts[h]:starts[h + 1]` of the intervals, ascending
depths) with the state of every row: 1 — in D, 0 — not in D, −1 — no code. A *run* is a stretch of touching rows of one
state; a run of a known state is bounded at each end either by a *contact* (the touching neighbour has the other known
state) or is free (a gap, a row without a code, the end of the hole).

  * contact: a point f = 0 at the depth of the change (written once, by the run that starts there);
  * a run with a contact at one or both ends: s(t) = −(distance along the hole to the nearest contact of the run) in
    D, +distance outside, points every `step` from each contact while |s| ≤ `d_max` (a run with two contacts is shared
    at its middle: each point goes with the nearer contact) and — not less often than one point per interval — the
    middle of the part of an interval inside that zone when no point fell there;
  * a run without any contact (the whole hole in D, or outside D, or between two gaps): −d_max (+d_max) in the middle of
    every cell of 2·step.

The first call (`write` False) only counts the points of the strip; the second fills the arrays from index 0.
Everything is float64; the kernel is `nogil`, strips go to threads, the result does not depend on their number.
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

_TOL = 1e-9


@njit(cache=True, nogil=True)
def _put(write, n, oh, ot, of, ok, orow, h, t, f, kind, row):  # type: ignore[no-untyped-def]
    if write:
        oh[n] = h
        ot[n] = t
        of[n] = f
        ok[n] = kind
        orow[n] = row


@njit(cache=True, nogil=True)
def _row_of(to, s, e, i, j, t):  # type: ignore[no-untyped-def]
    """The row (of the run i ≤ row < j) whose interval holds the depth t; on a boundary — the upper one."""
    r = s + np.searchsorted(to[s:e], t)
    if r < i:
        r = i
    if r > j - 1:
        r = j - 1
    return r


@njit(cache=True, nogil=True)
def _dist(t, ra, rb, ca, cb):  # type: ignore[no-untyped-def]
    d = np.inf
    if ca:
        d = t - ra
    if cb:
        d = min(d, rb - t)
    return max(d, 0.0)


@njit(cache=True, nogil=True)
def domain_strip(h0, h1, starts, frm, to, st, d_max, step, eps, write, oh, ot, of, ok, orow):  # type: ignore[no-untyped-def]
    n = 0
    for h in range(h0, h1):
        s = starts[h]
        e = starts[h + 1]
        i = s
        while i < e:
            cur = st[i]
            j = i + 1
            while j < e and st[j] == cur and frm[j] - to[j - 1] <= eps:
                j += 1
            if cur >= 0:
                ra = frm[i]
                rb = to[j - 1]
                ca = i > s and st[i - 1] >= 0 and st[i - 1] != cur and frm[i] - to[i - 1] <= eps
                cb = j < e and st[j] >= 0 and st[j] != cur and frm[j] - to[j - 1] <= eps
                sg = -1.0 if cur == 1 else 1.0
                kd = 1 if cur == 1 else 2
                if ca:
                    _put(write, n, oh, ot, of, ok, orow, h, ra, 0.0, 0, -1)
                    n += 1
                if not ca and not cb:
                    length = rb - ra
                    m = max(1, int(math.ceil(length / (2.0 * step) - _TOL)))
                    for k in range(m):
                        t = ra + (k + 0.5) * length / m
                        _put(write, n, oh, ot, of, ok, orow, h, t, sg * d_max, kd, _row_of(to, s, e, i, j, t))
                        n += 1
                else:
                    mid = 0.5 * (ra + rb)
                    zhi = min(ra + d_max, mid if cb else rb)  # the zone of the contact at the top of the run
                    zlo = max(rb - d_max, mid if ca else ra)  # … of the contact at the bottom
                    if ca:
                        k = 1
                        while ra + k * step <= zhi + _TOL:
                            t = ra + k * step
                            _put(write, n, oh, ot, of, ok, orow, h, t, sg * _dist(t, ra, rb, ca, cb), kd,
                                 _row_of(to, s, e, i, j, t))  # fmt: skip
                            n += 1
                            k += 1
                    if cb:
                        k = 1
                        while rb - k * step >= zlo - _TOL:
                            t = rb - k * step
                            if ca and t <= mid + _TOL:
                                break
                            _put(write, n, oh, ot, of, ok, orow, h, t, sg * _dist(t, ra, rb, ca, cb), kd,
                                 _row_of(to, s, e, i, j, t))  # fmt: skip
                            n += 1
                            k += 1
                    for r in range(i, j):  # not less often than one point per interval
                        if ca:
                            lo = max(frm[r], ra)
                            hi = min(to[r], zhi)
                            if hi - lo > _TOL:
                                k0 = max(1, int(math.ceil((lo - ra) / step - _TOL)))
                                if ra + k0 * step > hi + _TOL:
                                    t = 0.5 * (lo + hi)
                                    _put(write, n, oh, ot, of, ok, orow, h, t, sg * _dist(t, ra, rb, ca, cb), kd, r)
                                    n += 1
                        if cb:
                            lo = max(frm[r], zlo)
                            hi = min(to[r], rb)
                            if hi - lo > _TOL:
                                k0 = max(1, int(math.ceil((rb - hi) / step - _TOL)))
                                tb = rb - k0 * step
                                if tb < lo - _TOL or (ca and tb <= mid + _TOL):
                                    t = 0.5 * (lo + hi)
                                    _put(write, n, oh, ot, of, ok, orow, h, t, sg * _dist(t, ra, rb, ca, cb), kd, r)
                                    n += 1
            i = j
    return n
