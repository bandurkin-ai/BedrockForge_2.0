"""The solution of all patches of the implicit model on a pool of threads (ТЗ 8.9.2; ADR 0026, decision 9; 19.4).

    sols = solve_patches(points, values, patches, kind="linear", eta=0.0, threads=4, tick=tick)
    -> list[PatchSolution], the patches in the order of their numbers (`system.PatchSolution`: λ, c, cond₁, η, the
       points of the patch in the space of its system)

`points` [n, 3] float64 and `values` [n] are in the canonical order the patches were built from (`octree.build_patches`;
`patches.indices` are positions in this array); with `patches.matrix` the points are project coordinates and A is
applied once here, as `build_patches` and `hash_patches` do. Every patch is solved around its own centre with its R
(`solve_patch(centre=c_j, scale=R_j)`).

Threads. The patches go to a pool of Python threads (the LAPACK of SciPy and the numba kernels let the GIL go); the
thread that runs the pool enters `system.single_blas_thread()` once around the whole run — BLAS on one thread inside
every patch, whatever the number of threads. A patch is a function of its own points and values alone and the result
of the run is the list in the order of the numbers, so the bytes of λ, c and cond₁ do not depend on the number of
threads (Т8.9-8). The patches are submitted from the largest down (the tail of a run is short), collected by number.

Cancel. `tick()` is called in the calling thread after every collected patch and may raise (`JobCancelled`): the
patches not begun are dropped, those running finish (at most `threads` of them) and the exception goes on.

Failure. The first patch by number that has no solution raises `RbfSingular` (422 `rbf_singular`) with the numbers of
the patch and of its points given in the CANONICAL numbers of `points` (1-based in the text, 0-based in `extra`):
`patch`,
`centre`, `n_points`, `reason`, `points` (the pairs of coincident points, or the first 20 points of the patch for the
other reasons) and, when the caller gives `sources` (one id per point: a hole, a file row), `sources` — the same pairs
or list in these ids. NaN or ∞ in the values is 422 `rbf_bad_data` with the number of the point.

Memory. A patch needs two matrices at its peak, 2·(n + 4)² float64 (`patch_bytes`); the pool starts a patch only
while the patches in flight stay within `max_memory` (a patch alone is always let through): a few giant patches do not
run together. It changes the schedule, never the result.

Per patch: `eta` is a number or a list (the smoothing of 19.10), `radius` the same for the finite-radius kernel,
`matrices` (A_j of 19.9, or None for a patch) turn the points of that patch — and its centre — before its system is
built; `evaluate.PatchField(…, matrices=…)` takes the same list. numba is not imported here (the kernels come with
`solve_patch`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from bf_core.api.errors import ApiError

from .octree import PatchSet, transform_points
from .system import COND_MAX, MAX_PAIRS, PatchSolution, RbfSingular, single_blas_thread, solve_patch

F8 = NDArray[np.float64]
Tick = Callable[[], None]


def _bad(code: str, message: str, hint: str) -> ApiError:
    return ApiError(422, code, message, hint)


def patch_bytes(n: int) -> int:
    """The memory one patch of `n` points needs at its peak: the matrix, (n + 4)² float64, and the `|A|` that
    `solve_patch` makes for its 1-norm (measured: the process peak is 2 matrices for the largest patch)."""
    return 16 * (int(n) + 4) ** 2 + 4096


class _Stopped(Exception):
    """A patch that had not begun when the run was cancelled or failed (never leaves this module)."""


class _Budget:
    """The bytes of the matrices of the patches being solved: `hold(need)` waits while the sum would pass `cap` and
    something is in flight (so a patch larger than the cap runs alone, and the run cannot deadlock)."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.used = 0
        self.stopped = False
        self._cv = threading.Condition()

    def acquire(self, need: int) -> None:
        with self._cv:
            while self.used and self.used + need > self.cap and not self.stopped:
                self._cv.wait()
            if self.stopped:
                raise _Stopped
            self.used += need

    def release(self, need: int) -> None:
        with self._cv:
            self.used -= need
            self._cv.notify_all()

    def stop(self) -> None:
        with self._cv:
            self.stopped = True
            self._cv.notify_all()


def _per_patch(value: float | Sequence[float] | None, m: int, name: str) -> list[float | None]:
    """A number for all patches or a list with one entry per patch."""
    if value is None or isinstance(value, (int, float, np.floating, np.integer)):
        return [None if value is None else float(value)] * m
    arr = list(value)
    if len(arr) != m:
        raise _bad("bad_param", f"Параметр «{name}» задан списком не по числу участков ({len(arr)} вместо {m}).",
                   "Задайте одно число или по одному значению на участок.")  # fmt: skip
    return [None if v is None else float(v) for v in arr]


def _located(err: RbfSingular, j: int, idx: np.ndarray, centre: np.ndarray, sources: np.ndarray | None) -> RbfSingular:
    """The error of patch j, its point numbers mapped from the rows of the call to the canonical ones (and sources)."""
    reason = err.extra.get("reason", "")
    pairs: list[list[int]] = [[int(idx[a]), int(idx[b])] for a, b in err.extra.get("points", [])]
    where = f"Участок № {j + 1} (центр {centre[0]:.1f}, {centre[1]:.1f}, {centre[2]:.1f}; точек {len(idx)})"
    if reason == "coincident" and pairs:
        message = (f"{where}: есть совпадающие точки данных (например, № {pairs[0][0] + 1} и № {pairs[0][1] + 1}); "
                   "система RBF при нулевом сглаживании вырождена.")  # fmt: skip
        shown: list[Any] = pairs
    else:
        message = f"{where}: {err.message}"
        shown = [int(k) for k in idx[:MAX_PAIRS]]
    out = RbfSingular(reason, message, err.hint, points=None, cond1=err.extra.get("cond1"))
    out.extra.update({"patch": int(j), "centre": [float(v) for v in centre], "n_points": int(len(idx)),
                      "points": shown})  # fmt: skip
    if sources is not None:
        out.extra["sources"] = [[sources[k].item() for k in p] for p in shown] if shown and isinstance(shown[0], list) \
            else [sources[k].item() for k in shown]  # fmt: skip
    return out


def solve_patches(
    points: ArrayLike,
    values: ArrayLike,
    patches: PatchSet,
    *,
    kind: str | int = "linear",
    radius: float | Sequence[float] | None = None,
    eta: float | Sequence[float] = 0.0,
    threads: int = 1,
    tick: Tick | None = None,
    cond_max: float = COND_MAX,
    matrices: Sequence[ArrayLike | None] | None = None,
    sources: ArrayLike | None = None,
    max_memory: int = 2 << 30,
) -> list[PatchSolution]:
    """Solve every patch (see the module text). Raises `RbfSingular`, 422 `rbf_bad_data` / `bad_param`."""
    pts = np.asarray(points, dtype=np.float64)
    f = np.asarray(values, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3 or f.shape != (pts.shape[0],):
        raise _bad("rbf_bad_points", "Точки и значения не согласованы: нужны n × 3 координаты и n значений.",
                   "Это ошибка подготовки данных: сообщите разработчикам.")  # fmt: skip
    if patches.indices.size and int(patches.indices.max()) >= pts.shape[0]:
        raise _bad("rbf_bad_points", "Точки не соответствуют участкам: другое число точек.",
                   "Передайте тот же массив точек, по которому строились участки.")  # fmt: skip
    if not np.isfinite(f).all():
        bad = int(np.flatnonzero(~np.isfinite(f))[0])
        raise _bad("rbf_bad_data", f"Значение точки № {bad + 1} — пропуск или бесконечность.",
                   "Уберите такие точки из данных: пропуск не заменяется нулём.")  # fmt: skip
    if not np.isfinite(pts).all():
        bad = int(np.flatnonzero(~np.isfinite(pts).all(axis=1))[0])
        raise _bad("rbf_bad_data", f"В координатах точки № {bad + 1} есть пропуск или бесконечность.",
                   "Уберите такие точки из данных: пропуск не заменяется нулём.")  # fmt: skip
    src: np.ndarray | None = None
    if sources is not None:
        src = np.asarray(sources).reshape(-1)
        if src.shape[0] != pts.shape[0]:
            raise _bad("bad_param", "Источников точек не столько же, сколько точек.",
                       "Передайте по одному источнику (скважине, строке) на точку.")  # fmt: skip
    if patches.matrix is not None:
        pts = transform_points(pts, patches.matrix)
    m = patches.n_patches
    etas = _per_patch(eta, m, "сглаживание")
    radii_a = _per_patch(radius, m, "радиус ядра")
    if matrices is not None and len(matrices) != m:
        raise _bad("bad_param", "Матриц анизотропии участков не столько же, сколько участков.",
                   "Задайте по одной матрице (или None) на участок.")  # fmt: skip
    nthreads = max(int(threads), 1)

    budget = _Budget(int(max_memory))

    def one(j: int) -> PatchSolution:
        idx = patches.patch(j)
        need = patch_bytes(len(idx))
        budget.acquire(need)
        try:
            x = pts[idx]
            c = patches.centers[j]
            a = None if matrices is None else matrices[j]
            if a is not None:
                x = transform_points(x, a)
                c = transform_points(c.reshape(1, 3), a)[0]
            return solve_patch(
                x, f[idx], kind, radii_a[j], etas[j] or 0.0, centre=c, scale=float(patches.radii[j]), cond_max=cond_max
            )
        finally:
            budget.release(need)

    def solved(j: int, fut: Any) -> PatchSolution:
        try:
            res: PatchSolution = fut.result() if fut is not None else one(j)
        except RbfSingular as err:
            raise _located(err, j, patches.patch(j), patches.centers[j], src) from None
        return res

    results: list[PatchSolution] = []
    with single_blas_thread():
        if nthreads == 1 or m < 2:
            for j in range(m):
                results.append(solved(j, None))
                if tick is not None:
                    tick()
            return results
        pool = ThreadPoolExecutor(min(nthreads, m), thread_name_prefix="bf-solve")
        try:
            futures: dict[int, Any] = {}
            for j in np.argsort(-patches.sizes, kind="stable"):
                futures[int(j)] = pool.submit(one, int(j))
            for j in range(m):
                results.append(solved(j, futures[j]))
                if tick is not None:
                    tick()
        finally:
            budget.stop()  # the patches that wait for memory do not begin
            pool.shutdown(wait=True, cancel_futures=True)
    return results


__all__ = ["patch_bytes", "solve_patches"]
