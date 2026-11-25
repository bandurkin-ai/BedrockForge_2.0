"""The 2D thin-plate surfaces of a vein: w(u, v) through the contacts of one side (ТЗ 8.9.2 «Жилы»; ADR 0026 решение 17;
19.15).

    fit_surface(uv, w, *, dense_max, threads, tick) -> Surface     the surface through the points (u, v, w)
    Surface.evaluate(q, *, threads, tick) -> f8[m]                 w at the points q [m, 2]; NaN never becomes 0
    Tps2                                                           one dense system of n points

The radial function is φ(r) = r²·ln r with the linear polynomial [1, u, v] (the thin-plate spline): the system
[Φ, P; Pᵀ, 0] is dense and solved by LU (`scipy.linalg.lu_factor`) with BLAS on one thread, as in `system.py` (aligned
buffers, `a.T` factored in place, cond₁ from `dgecon`). Up to `DENSE_MAX` points one system serves the whole side. Above
it the same function goes through the partition of unity of the 3D model (`pou_w`, ADR 0026 решение 8) in 2D: the
quadtree of the points (a leaf holds at most `PATCH_POINTS` points, split at the middle of its cell — the geometry,
never the order of the points, decides), a patch for every leaf with the radius of its half diagonal × (1 + `OVERLAP`)
and at least `PATCH_MIN` points, W(r) = (1 − r)⁴₊·(4r + 1); the surface is Σ W_j·s_j / Σ W_j. A point no patch reaches
(outside the quadtree box) takes the patch of the nearest centre alone. The patches are solved and evaluated on a pool
of Python threads; the sums are taken in the order of the patches, so the bytes do not depend on the number of threads.

The scale of (u, v) is taken away before the solve (the centre and the half extent of the box): r²·ln r turns into
r²·ln r + r²·ln R, and r² is in the span of the system under Σλ = 0, Σλ·x = 0 — the surface does not change.
The loops (the block Φ, the sum at the query points) are numba kernels (`veins_kernels.py`) imported inside the
functions: a handler of jobs that imports this module does not pay for numba.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.linalg import LinAlgWarning, lu_factor, lu_solve
from scipy.linalg.lapack import dgecon
from scipy.spatial import cKDTree

from .system import _aligned, _blas_guard, single_blas_thread

Tick = Callable[[float | None, str | None], None]

DENSE_MAX = 4000  # points of a side solved by one system
PATCH_POINTS = 400  # points of a quadtree leaf
PATCH_MIN = 24  # points of a patch at least
OVERLAP = 0.5  # δ of the patch radius
MAX_DEPTH = 14
COND_MAX = 1.0e14
FLAT = 1.0e-7  # smallest / largest singular value of the centred points: below it they lie on a line


class SurfaceError(ValueError):
    """The points cannot carry a thin-plate surface; `reason` is `collinear`, `ill_conditioned` or `not_finite`."""

    def __init__(self, reason: str, text: str) -> None:
        super().__init__(text)
        self.reason = reason


def _kn() -> Any:
    from . import veins_kernels

    return veins_kernels


def pou_w(r: np.ndarray) -> np.ndarray:
    """W(r) = (1 − r)⁴₊·(4r + 1), r = distance / radius of the patch."""
    q = np.clip(1.0 - r, 0.0, None)
    return q * q * q * q * (4.0 * r + 1.0)


def _frame(xy: np.ndarray) -> tuple[np.ndarray, float]:
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    return (lo + hi) / 2.0, max(float(np.max(hi - lo)) / 2.0, 1e-300)


@dataclass
class Tps2:
    """One dense thin-plate system: the points are kept in the coordinates of the patch ((x − centre) / scale)."""

    centre: np.ndarray
    scale: float
    pts: np.ndarray  # [n, 2] normalised
    lam: np.ndarray  # [n]
    poly: np.ndarray  # [3]
    cond1: float

    def evaluate(self, q: np.ndarray) -> np.ndarray:
        qn = (np.asarray(q, dtype=np.float64) - self.centre) / self.scale
        return np.asarray(_kn().evaluate(qn, self.pts, self.lam, self.poly))  # one fixed order of the sum


def fit_tps(xy: np.ndarray, w: np.ndarray) -> Tps2:
    """Solve the dense system of the points `xy` [n, 2] with the values `w` [n] (no two points coincide)."""
    xy = np.asarray(xy, dtype=np.float64)
    w = np.asarray(w, dtype=np.float64).reshape(-1)
    n = len(xy)
    if n < 3 or not (np.isfinite(xy).all() and np.isfinite(w).all()):
        raise SurfaceError("not_finite", "Меньше трёх точек или в данных есть нечисловые значения.")
    centre, scale = _frame(xy)
    pts = np.ascontiguousarray((xy - centre) / scale)
    sv = np.linalg.svd(pts - pts.mean(axis=0), compute_uv=False)
    if sv[0] <= 0.0 or sv[-1] < FLAT * sv[0]:
        raise SurfaceError("collinear", "Контакты лежат на одной прямой: поверхность не определена.")
    m = n + 3
    a = _aligned(m * m).reshape(m, m)
    a[...] = 0.0
    _kn().gram(pts, a)
    a[:n, n] = 1.0
    a[:n, n + 1 :] = pts
    a[n, :n] = 1.0
    a[n + 1 :, :n] = pts.T
    anorm = float(np.max(np.sum(np.abs(a), axis=0)))
    rhs = _aligned(m)
    rhs[:n], rhs[n:] = w, 0.0
    try:
        with _blas_guard():
            lu, piv = lu_factor(a.T, overwrite_a=True, check_finite=False)  # `a` is symmetric
            if not np.all(np.diagonal(lu) != 0.0):
                raise LinAlgWarning("zero pivot")
            rcond, info = dgecon(lu, anorm, norm="1")
            sol = lu_solve((lu, piv), rhs, overwrite_b=True, check_finite=False)
    except LinAlgWarning:
        raise SurfaceError("ill_conditioned", "Система поверхности вырождена: нулевой ведущий элемент.") from None
    cond1 = float(f"{1.0 / rcond:.6g}") if (info == 0 and rcond > 0.0) else math.inf
    if not (cond1 < COND_MAX) or not np.all(np.isfinite(sol)):
        raise SurfaceError("ill_conditioned", f"Система поверхности вырождена: обусловленность {cond1:.2e}.")
    return Tps2(centre, scale, pts, sol[:n].copy(), sol[n:].copy(), cond1)


# --- the surface of a side ---------------------------------------------------------------------------------------


@dataclass
class Patch:
    centre: np.ndarray
    radius: float
    system: Tps2
    members: int


class Surface:
    """A fitted w(u, v): one dense system or the patches of the partition of unity."""

    def __init__(self, dense: Tps2 | None, patches: list[Patch], n_points: int) -> None:
        self.dense, self.patches, self.n_points = dense, patches, n_points
        self.cond1 = dense.cond1 if dense is not None else max((p.system.cond1 for p in patches), default=0.0)

    @property
    def n_patches(self) -> int:
        return len(self.patches)

    def evaluate(self, q: np.ndarray, *, threads: int = 1, tick: Tick | None = None) -> np.ndarray:
        q = np.ascontiguousarray(q, dtype=np.float64).reshape(-1, 2)
        if self.dense is not None:
            return _dense_rows(self.dense, q, threads)
        return self._pou(q, threads, tick)

    def _pou(self, q: np.ndarray, threads: int, tick: Tick | None) -> np.ndarray:
        tree = cKDTree(q)
        num, den = np.zeros(len(q)), np.zeros(len(q))

        def job(j: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            p = self.patches[j]
            idx = np.sort(np.asarray(tree.query_ball_point(p.centre, p.radius), dtype=np.int64))
            if len(idx) == 0:
                return idx, idx.astype(np.float64), idx.astype(np.float64)
            r = np.sqrt(np.sum((q[idx] - p.centre) ** 2, axis=1)) / p.radius
            wt = pou_w(r)
            return idx, wt * p.system.evaluate(q[idx]), wt

        _run(job, len(self.patches), threads, tick, "Поверхность жилы: участки",
             lambda r: _accumulate(num, den, r))  # fmt: skip
        out = np.full(len(q), np.nan)
        got = den > 0.0
        out[got] = num[got] / den[got]
        if not got.all():  # outside every patch: the nearest patch alone
            miss = np.flatnonzero(~got)
            centres = np.array([p.centre for p in self.patches])
            near = cKDTree(centres).query(q[miss])[1]
            for j in np.unique(near):
                sel = miss[near == j]
                out[sel] = self.patches[int(j)].system.evaluate(q[sel])
        return out


def _dense_rows(system: Tps2, q: np.ndarray, threads: int) -> np.ndarray:
    """The dense spline at q; the rows are independent, so the pieces on a pool give the same bytes as one call."""
    if threads <= 1 or len(q) < 2048:
        return system.evaluate(q)
    cuts = np.linspace(0, len(q), 4 * threads + 1).astype(np.int64)
    spans = list(zip(cuts[:-1], cuts[1:], strict=True))
    out = np.empty(len(q))
    with ThreadPoolExecutor(max_workers=threads) as pool:
        for (lo, hi), part in zip(spans, pool.map(lambda r: system.evaluate(q[r[0] : r[1]]), spans), strict=True):
            out[lo:hi] = part
    return out


def _accumulate(num: np.ndarray, den: np.ndarray, r: tuple[np.ndarray, np.ndarray, np.ndarray]) -> None:
    idx, a, b = r
    num[idx] += a
    den[idx] += b


def _run(job: Callable[[int], Any], count: int, threads: int, tick: Tick | None, text: str,
         take: Callable[[Any], None]) -> None:  # fmt: skip
    """`job(0..count − 1)` on a pool, the results handed to `take` in the order of the jobs."""
    workers = max(1, min(int(threads), count))
    if workers == 1:
        with _blas_guard():
            for j in range(count):
                take(job(j))
                if tick is not None:
                    tick(None if count == 0 else (j + 1) / count, text)
        return
    with single_blas_thread(), ThreadPoolExecutor(max_workers=workers) as pool:
        for j, res in enumerate(pool.map(job, range(count))):
            take(res)
            if tick is not None:
                tick((j + 1) / count, text)


def _leaves(xy: np.ndarray, cap: int) -> list[tuple[np.ndarray, float]]:
    """(centre, half diagonal) of the leaves of the quadtree of the points, in the depth-first order."""
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    pad = 1e-9 * max(float(np.max(hi - lo)), 1.0)
    box = (lo[0] - pad, lo[1] - pad, hi[0] + pad, hi[1] + pad)
    out: list[tuple[np.ndarray, float]] = []
    stack = [(box, np.arange(len(xy)), 0)]
    while stack:
        (x0, y0, x1, y1), idx, depth = stack.pop()
        if len(idx) <= cap or depth >= MAX_DEPTH:
            out.append((np.array([(x0 + x1) / 2.0, (y0 + y1) / 2.0]), 0.5 * math.hypot(x1 - x0, y1 - y0)))
            continue
        xm, ym = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        east, north = xy[idx, 0] >= xm, xy[idx, 1] >= ym
        quads = [((x0, y0, xm, ym), idx[~east & ~north]), ((xm, y0, x1, ym), idx[east & ~north]),
                 ((x0, ym, xm, y1), idx[~east & north]), ((xm, ym, x1, y1), idx[east & north])]  # fmt: skip
        for cell, sub in reversed(quads):
            stack.append((cell, sub, depth + 1))
    return out


def fit_surface(uv: np.ndarray, w: np.ndarray, *, dense_max: int = DENSE_MAX, patch_points: int = PATCH_POINTS,
                threads: int = 1, tick: Tick | None = None) -> Surface:  # fmt: skip
    """The thin-plate surface through (u, v, w): one system up to `dense_max` points, the 2D partition of unity above.
    The points must be distinct (the caller merges the coincident ones) and in a fixed order (sorted)."""
    uv = np.ascontiguousarray(uv, dtype=np.float64)
    w = np.ascontiguousarray(w, dtype=np.float64).reshape(-1)
    n = len(uv)
    if n <= dense_max:
        return Surface(fit_tps(uv, w), [], n)
    leaves = _leaves(uv, patch_points)
    tree = cKDTree(uv)
    k = min(PATCH_MIN, n)

    def job(j: int) -> Patch:
        centre, half = leaves[j]
        radius = half * (1.0 + OVERLAP)
        radius = max(radius, float(np.atleast_1d(tree.query(centre, k=k)[0])[-1]) * 1.0001)
        for _ in range(6):  # a patch of collinear points is enlarged
            idx = np.sort(np.asarray(tree.query_ball_point(centre, radius), dtype=np.int64))
            try:
                return Patch(centre, radius, fit_tps(uv[idx], w[idx]), len(idx))
            except SurfaceError as exc:
                if exc.reason == "not_finite":
                    raise
                radius *= 1.5
        raise SurfaceError("ill_conditioned", "Участок поверхности жилы вырожден: точки почти на одной прямой.")

    patches: list[Patch] = []
    _run(job, len(leaves), threads, tick, "Поверхность жилы: решение участков", patches.append)
    return Surface(None, patches, n)
