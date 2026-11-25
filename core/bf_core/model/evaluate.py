"""The field of the implicit model: the partition of unity over the solved patches (ТЗ 8.9.2; ADR 0026, decision 10).
Э19, 19.4.

    field = PatchField(patches, solutions)            patches: `octree.PatchSet`, solutions: `solve.solve_patches`
    f = field(points)                                  float64 [n, 3] → float64 [n]; the function `geo/iso.py` takes
    f = field(points, threads=4, tick=tick)            bands of points on threads, `tick()` after every band

    f(x) = Σ_j W(‖x − c_j‖ / R_j)·f_j(x) / Σ_j W(‖x − c_j‖ / R_j),   W(r) = (1 − r)⁴₊·(4r + 1)

over the patches in the order of their numbers; f_j is the solution of the patch (`PatchSolution`). W and W' vanish at
r = 1, so the field is C¹ across the boundary of a ball; where the balls of two patches overlap the field is the
weighted mean of their solutions and it reproduces the data of every patch that holds a point (η = 0, Т8.9-3).

Coverage. The balls of the patches cover the whole extent of the model (the octree leaves tile it and every ball
holds its leaf with a margin), and a little beyond it: Σ W > 0 there and the field is the extrapolation of the nearest
patches. A point that no ball reaches — farther than the balls, a NaN coordinate — is **NaN**, never 0 (8.0.1). The
caller that needs a closed surface (the isosurface, 8.9.3 item 6) replaces what lies outside `extent_min`…`extent_max`
by +H itself; this module does not guess a value outside the model.

Space. With `patches.matrix` (the global anisotropy A that `build_patches` applied) the points of `field(...)` are
project coordinates and A is applied here once (`octree.transform_points`); without it the points are in the space of
the patches. The anisotropy of a patch, A_j (19.9), is an optional `matrices` list: `None` for a patch means the
identity. The solution of such a patch was found in the space of A_j·x (`solve.solve_patches(matrices=…)`), the weight
stays in the space of the patches.

Which patches reach a point (the decision of 19.4). Points are taken in bands of `BAND`; when there are more than a
band the points are first ordered by their nearest patch centre (a `cKDTree` of the centres, stable `argsort`: the
patches are numbered in Morton order of the leaves, so a band is a compact cloud), the box of a band is cut against
the balls (distance from the centre to the box < R·(1 + 1e-6): a superset), and the kernel tests every point of the band
against those candidates exactly (r < 1 in float64). The result of a point is thus the sum of ITS patches, the sources
of each in their order — it depends on the point and the solutions alone, not on the other points, the order they are
given in, the bands or the threads (the same bytes with 1, 2, 4, 8 threads).

numba is not imported here: the kernel (`field_kernel.py`) is imported inside the call that needs it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from bf_core.api.errors import ApiError

from .octree import PatchSet, transform_points

if TYPE_CHECKING:
    from types import ModuleType

    from .system import PatchSolution

F8 = NDArray[np.float64]
Tick = Callable[[], None]

#: points per band (and per tick); fixed, so the bands do not depend on the number of threads.
BAND = 4096
#: a band keeps a patch whose ball is this much wider than the exact reach: only a superset is needed.
_REACH_PAD = 1e-6
_KINDS = {"linear": 0, "cubic": 1, "wendland": 2}


def _kernel() -> ModuleType:
    from . import field_kernel  # numba is loaded here, at the first evaluation, never at the import of this module

    return field_kernel


def _bad(code: str, message: str, hint: str) -> ApiError:
    return ApiError(422, code, message, hint)


class PatchField:
    """The field of a solved model; call it with points. Immutable after construction, safe to call from threads."""

    def __init__(
        self,
        patches: PatchSet,
        solutions: Sequence[PatchSolution],
        *,
        matrices: Sequence[ArrayLike | None] | None = None,
        threads: int = 1,
    ) -> None:
        m = patches.n_patches
        if len(solutions) != m:
            raise _bad("bad_param", f"Решений участков ({len(solutions)}) не столько же, сколько участков ({m}).",
                       "Это ошибка вызова: решите все участки набора.")  # fmt: skip
        if matrices is not None and len(matrices) != m:
            raise _bad("bad_param", "Матриц анизотропии участков не столько же, сколько участков.",
                       "Задайте по одной матрице (или None) на участок.")  # fmt: skip
        kinds = {s.kind for s in solutions}
        if len(kinds) != 1 or next(iter(kinds)) not in _KINDS:
            raise _bad("bad_param", "Все участки модели должны быть решены одним ядром.",
                       "Решите участки одним вызовом solve_patches.")  # fmt: skip
        self.kind: str = next(iter(kinds))
        self._code = _KINDS[self.kind]
        self.patches = patches
        self.threads = max(int(threads), 1)
        self.extent_min = patches.extent_min
        self.extent_max = patches.extent_max
        self._matrix = patches.matrix

        sizes = np.fromiter((len(s.lam) for s in solutions), dtype=np.int64, count=m)
        self._indptr = np.zeros(m + 1, dtype=np.int64)
        np.cumsum(sizes, out=self._indptr[1:])
        self._srct = np.empty((3, int(self._indptr[-1])), dtype=np.float64)
        self._lam = np.empty(int(self._indptr[-1]), dtype=np.float64)
        self._poly = np.empty((m, 4), dtype=np.float64)
        self._scale = np.empty(m, dtype=np.float64)
        self._arad = np.ones(m, dtype=np.float64)
        self._amat = np.zeros((m, 3, 3), dtype=np.float64)
        self._has_a = np.zeros(m, dtype=np.bool_)
        for j, s in enumerate(solutions):
            a = None if matrices is None else matrices[j]
            if a is None and not np.array_equal(s.centre, patches.centers[j]):
                raise _bad("bad_param", f"Центр решения участка № {j + 1} не совпадает с центром участка.",
                           "Решите участок вокруг его центра (solve_patches делает это сам).")  # fmt: skip
            self._srct[:, self._indptr[j] : self._indptr[j + 1]] = (s.points - s.centre).T
            self._lam[self._indptr[j] : self._indptr[j + 1]] = s.lam
            self._poly[j] = s.poly
            self._scale[j] = s.scale
            if s.radius is not None:
                self._arad[j] = s.radius
            if a is not None:
                mat = np.array(a, dtype=np.float64)
                if mat.shape != (3, 3) or not np.isfinite(mat).all():
                    raise _bad("bad_param", f"Матрица анизотропии участка № {j + 1} должна быть конечной 3 × 3.",
                               "Проверьте оси структурного тренда.")  # fmt: skip
                self._amat[j] = mat
                self._has_a[j] = True
        self._centres = np.ascontiguousarray(patches.centers, dtype=np.float64)
        self._radii = np.ascontiguousarray(patches.radii, dtype=np.float64)
        self._tree: Any = None  # cKDTree of the centres, built at the first call with more than a band of points

    # -- the points -----------------------------------------------------------------------------------------------

    def _space(self, points: ArrayLike) -> F8:
        """The points as float64 [n, 3] in the space of the patches (the global A applied once)."""
        pts = np.asarray(points, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[1] != 3:
            raise _bad("bad_param", f"Точки поля должны быть таблицей из трёх колонок, получено {pts.shape}.",
                       "Передайте массив n × 3 (x, y, z) в метрах.")  # fmt: skip
        if self._matrix is not None:
            return transform_points(pts, self._matrix)
        return np.ascontiguousarray(pts)

    def _order(self, q: F8, threads: int) -> np.ndarray[Any, np.dtype[np.int64]]:
        """The order of the points by their nearest patch centre (stable): consecutive bands are compact. A point with a
        non-finite coordinate goes last. The order changes no value (every point is independent)."""
        n = q.shape[0]
        if n <= BAND:
            return np.arange(n, dtype=np.int64)
        if self._tree is None:
            from scipy.spatial import cKDTree

            self._tree = cKDTree(self._centres)
        ok = np.isfinite(q).all(axis=1)
        owner = np.full(n, self._centres.shape[0], dtype=np.int64)
        owner[ok] = self._tree.query(q[ok], workers=threads)[1]
        return np.argsort(owner, kind="stable")

    def _candidates(self, band: F8) -> np.ndarray[Any, np.dtype[np.int64]]:
        """The patches whose ball may reach a point of the band: the box of the finite points against the balls."""
        ok = np.isfinite(band).all(axis=1)
        if not ok.any():
            return np.empty(0, dtype=np.int64)
        pts = band[ok]
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        gap = np.maximum(np.maximum(lo - self._centres, self._centres - hi), 0.0)
        reach = self._radii * (1.0 + _REACH_PAD)
        return np.flatnonzero(np.einsum("ij,ij->i", gap, gap) <= reach * reach).astype(np.int64)

    # -- the field ------------------------------------------------------------------------------------------------

    def __call__(self, points: ArrayLike, *, threads: int | None = None, tick: Tick | None = None) -> F8:
        """f at `points` [n, 3] (see the module text); NaN where no patch reaches. `tick()` — after every band, in
        the calling thread; it may raise to cancel: the bands not yet begun are dropped."""
        nthreads = self.threads if threads is None else max(int(threads), 1)
        q = self._space(points)
        n = q.shape[0]
        out = np.empty(n, dtype=np.float64)
        if n == 0:
            return out
        kn = _kernel()
        order = self._order(q, nthreads)
        qs = np.ascontiguousarray(q[order])
        res = np.empty(n, dtype=np.float64)
        spans = [(a, min(a + BAND, n)) for a in range(0, n, BAND)]

        def band(span: tuple[int, int]) -> None:
            a, b = span
            cand = self._candidates(qs[a:b])
            kn.field_band(qs[a:b], res[a:b], cand, self._centres, self._radii, self._indptr, self._srct, self._lam,
                          self._poly, self._scale, self._arad, self._amat, self._has_a, self._code)  # fmt: skip

        _run(band, spans, nthreads, tick)
        out[order] = res
        return out

    def terms(self, points: ArrayLike) -> int:
        """The number of kernel terms a call with these points sums (Σ over the points of the sizes of the patches
        reaching them): the measure of the cost, ns/term = time / terms."""
        q = self._space(points)
        kn = _kernel()
        total = 0
        for a in range(0, q.shape[0], BAND):
            band = np.ascontiguousarray(q[a : a + BAND])
            total += int(kn.count_terms(band, self._candidates(band), self._centres, self._radii, self._indptr))
        return total

    def inside(self, points: ArrayLike) -> NDArray[np.bool_]:
        """True for the points inside `extent_min…extent_max` of the model (in the space of the patches)."""
        q = self._space(points)
        return np.asarray(np.all((q >= self.extent_min) & (q <= self.extent_max), axis=1))


def _run(fn: Callable[[tuple[int, int]], None], spans: list[tuple[int, int]], threads: int, tick: Tick | None) -> None:
    """fn(span) over the fixed bands; `tick` after each in the calling thread (inline for one thread or one band)."""
    if threads <= 1 or len(spans) < 2:
        for span in spans:
            fn(span)
            if tick is not None:
                tick()
        return
    pool = ThreadPoolExecutor(min(threads, len(spans)), thread_name_prefix="bf-field")
    try:
        for _ in pool.map(fn, spans):
            if tick is not None:
                tick()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def partition_weight(r: ArrayLike) -> F8:
    """W(r) = (1 − r)⁴₊·(4r + 1) of the radius r = ‖x − c‖ / R (8.9.2); 0 for r ≥ 1."""
    t = np.asarray(r, dtype=np.float64)
    u = np.maximum(1.0 - t, 0.0)
    u2 = u * u
    return np.asarray(u2 * u2 * (4.0 * t + 1.0))


def evaluate(
    patches: PatchSet,
    solutions: Sequence[PatchSolution],
    points: ArrayLike,
    *,
    matrices: Sequence[ArrayLike | None] | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> F8:
    """One-shot `PatchField(...)(points)`. Build the field once when it is called many times (bricks of the surface)."""
    return PatchField(patches, solutions, matrices=matrices, threads=threads)(points, tick=tick)


__all__ = ["BAND", "PatchField", "evaluate", "partition_weight"]
