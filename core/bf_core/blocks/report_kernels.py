"""The kernel of the grade–tonnage report (`blocks/report.py`): numba `cache=True, nogil=True` (the cache —
bf_core.paths).

Every row that is counted adds its volume, tonnes and, per grade, the tonnes with a grade, the metal (tonnes ×
grade) and the tonnes without a grade (a gap is not a zero) to the bin of its cut-off and to the bin of its group.
The sums are Neumaier (sum, carry) accumulators kept between the portions and fed in the order of the rows of the
file, so the result is the same bit for bit however the model is cut into portions. The kernel is single-threaded:
the cost is the reading of the columns, not the sums.

Layout of a row of the accumulators (`Q = 2 + 3·grades`): 0 volume, 1 tonnes, then for the grade k: 2+3k tonnes with
the grade, 3+3k Σ tonnes·grade, 4+3k tonnes without it. The counts `n[r, 0]` — blocks, `n[r, 1+k]` — blocks without
the grade k.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit


@njit(cache=True, nogil=True)
def _nadd(s, c, r, q, x):  # type: ignore[no-untyped-def]
    """Neumaier: s[r, q] += x with the lost part kept in c[r, q]."""
    t = s[r, q] + x
    if abs(s[r, q]) >= abs(x):
        c[r, q] += (s[r, q] - t) + x
    else:
        c[r, q] += (x - t) + s[r, q]
    s[r, q] = t


@njit(cache=True, nogil=True)
def _add(s, c, n, r, vol, tonnes, grades, i):  # type: ignore[no-untyped-def]
    n[r, 0] += 1
    _nadd(s, c, r, 0, vol)
    _nadd(s, c, r, 1, tonnes)
    for k in range(grades.shape[0]):
        v = grades[k, i]
        if np.isfinite(v):
            _nadd(s, c, r, 2 + 3 * k, tonnes)
            _nadd(s, c, r, 3 + 3 * k, tonnes * v)
        else:
            n[r, 1 + k] += 1
            _nadd(s, c, r, 4 + 3 * k, tonnes)


@njit(cache=True, nogil=True)
def accumulate(bin0, bing, grp, vol, tonnes, grades, s0, c0, n0, sg, cg, ng, nbg):  # type: ignore[no-untyped-def]
    """One portion. `bin0[i]` — the bin of the row among the bins of the total (−1: the row is not counted),
    `bing[i]` — its bin among the bins of a group, `grp[i]` — its group (−1: no groups), `grades` [k, n]."""
    for i in range(bin0.shape[0]):
        b = bin0[i]
        if b < 0:
            continue
        _add(s0, c0, n0, b, vol[i], tonnes[i], grades, i)
        g = grp[i]
        if g >= 0:
            _add(sg, cg, ng, g * nbg + bing[i], vol[i], tonnes[i], grades, i)
