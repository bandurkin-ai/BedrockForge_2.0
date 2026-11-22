"""The variogram along the holes — for the nugget (ТЗ 8.6.1, Т8.6-7; ADR 0022 §5).

    dh = downhole(hole, depth, v, lag, nlag, lag_tol=None, *, threads=1, tick=None)
        hole    the hole of a row (an integer ≥ 0; the dense index of `points.Points.hole`)
        depth   the middle of the interval along its hole, m;  v — the value (the variable of the calculation)
        a row with a missing number (or a negative hole) takes no part and is counted (`n_missing`)
    dh = calculate(p, lag, nlag, lag_tol=None, *, threads=1, tick=None)    over `points.Points` — all the points
    dh.n, dh.n_rel  int64[nlag];  dh.h, dh.gamma, dh.rho, dh.pairwise  float64[nlag], NaN — no value
    dh.few(min_pairs)  bool[nlag];  dh.sums — `experimental.PairSums` of one row;  dh.n_points, dh.n_missing, dh.holes
    dh.to_json(min_pairs)   `downhole` of the answer (docs/progress/e16-api.md)

A pair is two points of one hole, the distance is abs of the difference of their depths; no direction. The lags,
their windows (edges included, a pair on an edge is in both), the sums and the types are those of `pairs` and
`experimental`: lag k = 1 … nlag centred on k·L, no lag at 0, no pair of a point with itself; two points at one
depth enter only with a tolerance of a whole lag. Without a direction a pair counts both ways in the tail and head
sums (as an omnidirectional variogram). The points of a hole are taken by depth (then by row); the holes go in
fixed strips of `STRIP_HOLES`, each with its own compensated sums, added pairwise in their order — the result is
the same bit for bit with 1, 2, 4, 8 threads. Never sampled (ADR 0022 §4): it is cheap — the pairs of a hole
within reach only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.composites import strips
from bf_core.jit import njit

from . import pairs
from .experimental import PairSums, finite_rows
from .pairs import NSUM, Tick, into_lags

if TYPE_CHECKING:
    from .points import Points

STRIP_HOLES = 64  # holes in a strip: fixed, so the result does not depend on the threads


@njit(cache=True, nogil=True)
def _holes(depth, vs, ws, hstart, h0, h1, lag, tol, nlag, acc, cnt):  # type: ignore[no-untyped-def]
    """The pairs of the holes h0 ≤ h < h1 (points sorted by depth inside a hole) into acc[nlag·8], cnt[nlag·2]."""
    comp = np.zeros(acc.shape[0])
    cen = np.empty(nlag)
    for k in range(nlag):
        cen[k] = lag * (k + 1)
    hmax = cen[nlag - 1] + tol
    inv = 1.0 / lag
    span = 1 if tol <= 0.5 * lag else 2
    for hh in range(h0, h1):
        e = hstart[hh + 1]
        for i in range(hstart[hh], e):
            for j in range(i + 1, e):
                d = depth[j] - depth[i]
                if d > hmax:
                    break
                into_lags(acc, comp, cnt, 0, d, vs[i] - vs[j], vs[i] + vs[j], ws[i], ws[j], cen, tol, inv, span)
    for p in range(acc.shape[0]):
        acc[p] += comp[p]


@dataclass(frozen=True)
class Downhole:
    lag: float
    nlag: int
    lag_tol: float
    sums: PairSums  # one row
    n_points: int
    n_missing: int
    holes: int  # holes with at least one point

    n = property(lambda self: self.sums.n[0])
    n_rel = property(lambda self: self.sums.n_rel[0])
    h = property(lambda self: self.sums.h[0])
    gamma = property(lambda self: self.sums.gamma[0])
    rho = property(lambda self: self.sums.rho[0])
    one_minus_rho = property(lambda self: self.sums.one_minus_rho[0])
    pairwise = property(lambda self: self.sums.pairwise[0])

    def few(self, min_pairs: int = 30) -> np.ndarray:
        return self.sums.few(min_pairs)[0]

    @property
    def pw_skipped(self) -> int:
        return int((self.sums.n - self.sums.n_rel).sum())

    def to_json(self, min_pairs: int = 30) -> dict[str, Any]:
        """{lag, lag_tol, lags, h, n, gamma, rho, pairwise, n_pw, few}."""
        return {"lag": float(self.lag), "lag_tol": float(self.lag_tol)} | self.sums.curves_json(0, self.lag, min_pairs)


def downhole(
    hole: Any, depth: Any, v: Any, lag: float, nlag: int, lag_tol: float | None = None, *, threads: int = 1,
    tick: Tick | None = None,
) -> Downhole:  # fmt: skip
    """The variogram along the holes of the rows (see the module)."""
    lag, nlag, tol = pairs.check_lags(lag, nlag, lag_tol)
    hole_f = np.asarray(hole, dtype=np.float64)
    (hs, ds, vs), n_missing = finite_rows(np.where(hole_f >= 0.0, hole_f, np.nan), depth, v)
    hid = hs.astype(np.int64)
    order = np.lexsort((np.arange(len(hid)), ds, hid))  # by hole, depth, row
    hid, ds, vs = hid[order], np.ascontiguousarray(ds[order]), np.ascontiguousarray(vs[order])
    first = np.flatnonzero(np.concatenate([[True], hid[1:] != hid[:-1]])) if len(hid) else np.zeros(0, np.int64)
    hstart = np.concatenate([first, [len(hid)]]).astype(np.int64)
    nh = len(first)
    shift = float(np.mean(vs)) if len(vs) else 0.0
    ws = vs - shift
    nstrip = max(1, -(-nh // STRIP_HOLES))
    acc = np.zeros((nstrip, nlag, NSUM))
    cnt = np.zeros((nstrip, nlag, 2), dtype=np.int64)

    def one(s: int, _hi: int) -> None:
        h0, h1 = s * STRIP_HOLES, min(nh, (s + 1) * STRIP_HOLES)
        _holes(ds, vs, ws, hstart, h0, h1, lag, tol, nlag, acc[s].reshape(-1), cnt[s].reshape(-1))

    strips.run(one, nstrip, threads=threads, tick=tick, size=1)
    total = cnt.sum(axis=0)
    sums = PairSums(total[None, :, 0].copy(), total[None, :, 1].copy(), pairs.pairwise(acc)[None], shift,
                    np.array([True]))  # fmt: skip
    return Downhole(lag, nlag, tol, sums, len(vs), n_missing, nh)


def calculate(
    p: Points, lag: float, nlag: int, lag_tol: float | None = None, *, threads: int = 1, tick: Tick | None = None
) -> Downhole:
    """Along the holes over all the points of a calculation (`points.load`): the variable `values`, the middles
    `depth` on the holes `hole`."""
    return downhole(p.hole, p.depth, p.values, lag, nlag, lag_tol, threads=threads, tick=tick)
