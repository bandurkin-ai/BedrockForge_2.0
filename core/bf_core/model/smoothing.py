"""Smoothing η of the patches of the implicit model: «Вручную» and «Авто» (ТЗ 8.9.2, 7.35 Д-25; ADR 0026, решения 7, 9,
11а, 22; Э19, 19.10).

    eta = eta_for_patches(points, values, patches, spec, group=g, matrices=None, threads=4, tick=tick)   # f8[m]
    res = smooth_patches(points, patches, spec, group=g, …)   # PatchEta: eta f8[m], cond f8[m] (NaN — not evaluated),
                                                             # exact bool[m] (the spectrum of the patch was measured)
    sols = solve_patches(points, values, patches, kind=…, radius=kernel_radii(…), eta=eta, …)   # η per patch

`points` [n, 3] float64 are those the patches were built from (`octree.build_patches`; `patches.matrix`, A of the
model, is applied here as `solve_patches` does; `matrices` — A_j of the structural trend, 19.9, the same list as for
`solve_patches` and `PatchField`). `values` is accepted for the call of the job and is not used: η depends on the
positions only (points that coincide with a different f are what η is for). `spec` is a `ModelSpec` (or anything with
`kernel`, `kernel_radius`, `smoothing`); `s` is `spec.s_of(group)` unless given. η is in the units of φ (metres), as
`solve_patches(eta=…)` takes it, rounded to 6 digits (the last bits of a Lanczos run are not the same on every
machine; the 6 digits are, and they are what the cache of 19.12 and the table of the patches keep).

«Вручную»: η = s·h̄^k (`spec_parts.eta`) for every patch; h̄ is the median distance to the nearest point over ALL points
in the space of A (the coincident ones give 0 and count), or `h_bar` of the caller (the by-group value of the job).

«Авто» (Д-25, решение 11а): η_j is the smallest η ≥ η₀ = s·h̄^k with cond₂(Φ_⊥ + η·I) ≤ `cond_target` (the
default 1e8), where Φ_⊥ = Q₂ᵀΦQ₂ is Φ on the space orthogonal to P (Q₂ — the complement of the columns of
P = (1, x, y, z) in the normalised coordinates of the patch, as `system` builds them). Φ_⊥ is positive definite for the
three kernels, so cond₂ = (λ_max + η)/(λ_min + η) and the search is a bisection in log η (no random numbers, no
gradient): from η₀ (η = η₀ if cond₂(η₀) ≤ target — Т8.9-15) to η_max = 1e-2·max abs(Φ) until
cond₂ ∈ [(1 − 5 %)·target, target]. A patch the solve fails on (P of rank < 4, fewer than 4 points) gets η₀ and the
failure stays with `solve_patches`, which names the patch.

The spectrum, cheaply. The two ends of Φ_⊥ cost a full factorisation of the patch if they are looked for on every
patch (the budget of Д-25 is 10 % of the solve), so the work is done in two tiers.
1. Screen, O(n log n): the cond₁ of the system of a patch follows its size n and the smallest distance q between its
   points, cond₁ ≈ c·n/q^β (β = 1 linear, ≈ 2 finite radius, 3 cubic; `SCREEN`; c from boreholes, uniform and
   clustered clouds and 0–5 % of doubles at 1e-3…1e-9 apart — the table of 19.10). A patch whose figure, with the
   factor `SAFETY`, stays under the target has no degenerate pair and gets η₀ without a look at its matrix.
2. Measurement, on the rest: Φ is built once; λ_max of Φ_⊥ — Lanczos (`scipy.sparse.linalg.eigsh`, ≤ 30 vectors, start
   vector from PCG64(12345) projected onto the complement) on the operator Π·Φ·Π; η_p = max(η₀, λ_max/(2·target)) is
   put on the diagonal and the matrix of the system [Φ + η_pI, P; Pᵀ, 0] is factorised; the top-left n × n block of
   its inverse is Q₂(Φ_⊥ + η_pI)⁻¹Q₂ᵀ, so Lanczos on the solves of that LU finds 1/(λ_min + η_p) in a dozen steps (a
   5 % cloud of doubles included: their eigenvalue is the top of the inverse). Patches of up to `DENSE_N` points take
   `eigvalsh` of Φ_⊥ instead. A measured patch costs about one solve of it.
The screen is a figure, not a certificate: a degenerate patch it lets through is rejected by `solve_patches` as
`rbf_singular` with the number of the patch (`extra["patch"]`) — the job answers with `refine_patch` for that patch
and solves it again.

Threads and bytes. The patches go to a pool of threads (the `solve_patches` scheme: BLAS on one thread inside
`system.single_blas_thread()`, the largest first, collected by number, `tick()` after each, memory budget). A patch is
a function of its own points: the result does not depend on the number of threads or on the order of the input
(Т8.9-16, with the canonical order of the points the patches are built from).

numba is not imported here: the matrix of a patch comes from `system.assemble`, which loads the kernels at the first
patch that is measured (a screen alone, and «Вручную», never load it).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import ArrayLike, NDArray

from bf_core.api.errors import ApiError

from .octree import PatchSet, transform_points
from .solve import _Budget, patch_bytes
from .spec_parts import COND_TARGET, COND_TOL, ETA_MAX_FACTOR, ETA_POWER, LANCZOS_MAX, SEED, eta
from .system import RbfSingular, _aligned, _check_geometry, assemble, single_blas_thread

F8 = NDArray[np.float64]
Tick = Callable[[], None]

#: the screen: cond₁ of the system ≈ c·n/q^β at most (measured, see the module text); `SAFETY` — the margin on top.
SCREEN: dict[str, tuple[float, float]] = {"linear": (3.0, 1.0), "cubic": (15.0, 3.0), "wendland": (20.0, 2.0)}
SAFETY = 10.0
#: patches of up to this many points are measured with `eigvalsh` of Φ_⊥ (no LU, no Lanczos)
DENSE_N = 96
DIGITS = 6  # η is kept to these significant digits (see the module text)
_ARPACK_TOL = 1e-5  # the residual of a Ritz pair relative to it; the cond may be off by 5 %, the value is far better
NCV = 14  # Lanczos vectors per run (the ADR bound is 30; the top of a spectrum converges in a dozen)
_ARPACK_RESTARTS = 40  # restarts of ARPACK (a restart is ncv − 1 vectors); not reached on data that converge


class PatchEta(NamedTuple):
    """η of every patch (metres, as `solve_patches(eta=…)`), the cond₂ of Φ_⊥ + η·I the search ended with (NaN —
    the patch was not measured: «Вручную», the screen, a patch the solve rejects) and whether the spectrum of the
    patch was measured."""

    eta: F8
    cond: F8
    exact: NDArray[np.bool_]


def _bad(message: str, hint: str, code: str = "bad_param") -> ApiError:
    return ApiError(422, code, message, hint)


def _round(v: float) -> float:
    return float(f"{v:.{DIGITS}g}")


# --- η₀, h̄, the radii ------------------------------------------------------------------------------------------


def nearest_median(points: ArrayLike, matrix: ArrayLike | None = None) -> float:
    """h̄ of 8.9.2: the median over the points of the distance to the nearest OTHER point (a coincident one gives 0 and
    counts), in the space of A (`matrix`, or the points as they are). 0 for fewer than 2 points."""
    from scipy.spatial import cKDTree  # lazy: as in `octree`

    pts = np.asarray(points, dtype=np.float64)
    if matrix is not None:
        pts = transform_points(pts, matrix)
    if pts.shape[0] < 2:
        return 0.0
    d, _ = cKDTree(pts).query(pts, k=2, workers=1)
    return float(np.median(d[:, 1]))


def kernel_radii(kernel: str, kernel_radius: float | None, patches: PatchSet) -> list[float] | None:
    """The `radius=` of `solve_patches` for the model: None for the kernels without a radius, the given `a` of the
    finite-radius kernel for every patch or, when the model does not give it, 2·R_j of each patch."""
    if kernel != "wendland":
        return None
    if kernel_radius is not None:
        return [float(kernel_radius)] * patches.n_patches
    return [float(2.0 * r) for r in patches.radii]


def _per_patch(value: float | Sequence[float] | None, m: int) -> list[float | None]:
    if value is None or isinstance(value, (int, float, np.floating, np.integer)):
        return [None if value is None else float(value)] * m
    out = list(value)
    if len(out) != m:
        raise _bad(f"Радиус ядра задан списком не по числу участков ({len(out)} вместо {m}).",
                   "Задайте одно число или по одному значению на участок.")  # fmt: skip
    return [None if v is None else float(v) for v in out]


# --- cond₂ and the search of η ---------------------------------------------------------------------------------


def cond2(lam_min: float, lam_max: float, eta_n: float) -> float:
    """cond₂ of Φ_⊥ + η·I from the ends of the spectrum of Φ_⊥ (∞ for a zero denominator)."""
    den = lam_min + eta_n
    return math.inf if den <= 0.0 else (lam_max + eta_n) / den


def search_eta(lam_min: float, lam_max: float, eta0: float, eta_max: float, target: float,
               tol: float = COND_TOL) -> tuple[float, float]:  # fmt: skip
    """The bisection in log η of Д-25: the smallest η ≥ η₀ with cond₂ ≤ target, stopped as soon as
    cond₂ ∈ [(1 − tol)·target, target]; (η, cond₂). η₀ itself when cond₂(η₀) ≤ target; η_max when even that does not
    reach the target (the best the algorithm may give). Units — those of `lam_*`."""
    c0 = cond2(lam_min, lam_max, eta0)
    if c0 <= target:
        return eta0, c0
    hi = max(eta_max, eta0)
    c_hi = cond2(lam_min, lam_max, hi)
    if c_hi > target:
        return hi, c_hi
    lo = eta0 if eta0 > 0.0 else hi * 1e-18
    if cond2(lam_min, lam_max, lo) <= target:  # η₀ = 0 and the floor already does it: a cond just above the target
        return lo, cond2(lam_min, lam_max, lo)
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        c = cond2(lam_min, lam_max, mid)
        if c <= target:
            hi, c_hi = mid, c
            if c >= (1.0 - tol) * target:
                break
        else:
            lo = mid
        if hi <= lo * (1.0 + 1e-12):
            break
    return hi, c_hi


# --- the spectrum of Φ_⊥ ---------------------------------------------------------------------------------------


def _complement(xn: F8, full: bool) -> tuple[F8, F8 | None]:
    """An orthonormal basis of the range of P = (1, x, y, z) (n × 4) and, with `full`, of its complement."""
    from scipy.linalg import qr

    n = xn.shape[0]
    p = np.empty((n, 4), dtype=np.float64)
    p[:, 0] = 1.0
    p[:, 1:] = xn
    if not full:
        q, _ = qr(p, mode="economic", check_finite=False)
        return np.ascontiguousarray(q), None
    q, _ = qr(p, mode="full", check_finite=False)
    return np.ascontiguousarray(q[:, :4]), np.ascontiguousarray(q[:, 4:])


def _start(n: int, q_p: F8) -> F8:
    """The start vector of Lanczos: PCG64(SEED), taken into the complement of P (the same bytes every run)."""
    v = np.random.Generator(np.random.PCG64(SEED)).random(n) - 0.5
    v -= q_p @ (q_p.T @ v)
    return v


def _lanczos_top(matvec: Callable[[F8], F8], n: int, v0: F8) -> float:
    """The largest eigenvalue of the symmetric operator (≤ `LANCZOS_MAX` vectors, restarts bounded; a run that does
    not converge gives its best Ritz value)."""
    from scipy.sparse.linalg import ArpackNoConvergence, LinearOperator, eigsh

    op = LinearOperator((n, n), matvec=lambda v: matvec(np.asarray(v, dtype=np.float64).reshape(-1)), dtype=np.float64)
    ncv = min(NCV, LANCZOS_MAX, n - 1)
    try:
        w = eigsh(op, k=1, which="LA", v0=v0, ncv=ncv, maxiter=_ARPACK_RESTARTS * ncv, tol=_ARPACK_TOL,
                  return_eigenvectors=False)  # fmt: skip
    except ArpackNoConvergence as err:
        if err.eigenvalues.size == 0:
            raise
        w = err.eigenvalues
    return float(np.max(w))


def measured_ends(
    xn: F8, kernel: str, radius_n: float | None, eta0_n: float, target: float
) -> tuple[float, float, float]:
    """(λ_min, λ_max, max abs Φ) of Φ_⊥ for the points `xn` of a patch in its normalised coordinates (the module text,
    tier 2); `eta0_n`, `target` pick the η_p the factorisation is made at (the answer does not depend on it)."""
    from scipy.linalg import LinAlgWarning, eigvalsh, lu_factor, lu_solve

    n = xn.shape[0]
    a = assemble(xn, kernel, radius_n, 0.0)
    phi = a[:n, :n]
    max_abs = max(float(phi.max()), -float(phi.min()))
    if n <= DENSE_N:
        _, q_c = _complement(xn, True)
        assert q_c is not None
        w = eigvalsh(q_c.T @ phi @ q_c, check_finite=False)
        return max(float(w[0]), 0.0), float(w[-1]), max_abs
    q_p, _ = _complement(xn, False)
    v0 = _start(n, q_p)

    def phi_mv(v: F8) -> F8:
        u = v - q_p @ (q_p.T @ v)
        y = phi @ u
        return np.asarray(y - q_p @ (q_p.T @ y))

    lam_max = _lanczos_top(phi_mv, n, v0)
    eta_p = max(eta0_n, lam_max / (2.0 * target))
    for attempt in range(4):  # the first η_p is enough unless a pivot vanishes (a rare dead end: then 1000 times more)
        if attempt:
            eta_p *= 1e3
            a = assemble(xn, kernel, radius_n, 0.0)
        a[np.arange(n), np.arange(n)] += eta_p
        try:
            lu, piv = lu_factor(a.T, overwrite_a=True, check_finite=False)
            if np.all(np.diagonal(lu) != 0.0):
                break
        except LinAlgWarning:
            pass
    else:
        return 0.0, lam_max, max_abs
    rhs = _aligned(n + 4)

    def inverse_mv(v: F8) -> F8:
        rhs[:n], rhs[n:] = v, 0.0
        return np.asarray(lu_solve((lu, piv), rhs, check_finite=False)[:n])

    theta = _lanczos_top(inverse_mv, n, v0)
    lam_min = max(1.0 / theta - eta_p, 0.0) if theta > 0.0 else 0.0
    return lam_min, lam_max, max_abs


def predicted_cond(kernel: str, n: int, q: float) -> float:
    """The screen of tier 1: a figure above the cond₁ of a patch of `n` points whose closest pair is `q` apart
    (normalised coordinates), with the margin `SAFETY`; ∞ for coincident points."""
    c, beta = SCREEN[kernel]
    return math.inf if q <= 0.0 else SAFETY * c * n / q**beta


# --- one patch, all patches ------------------------------------------------------------------------------------


def patch_eta(xn: F8, kernel: str, radius_n: float | None, eta0_n: float, target: float,
              *, screen: bool = True) -> tuple[float, float, bool]:  # fmt: skip
    """(η, cond₂, measured) of a patch in its normalised coordinates, η in the units of Φ_n (the caller scales).
    `screen=False` measures the patch whatever its figure is (`refine_patch`, tests)."""
    from scipy.spatial import cKDTree

    n = xn.shape[0]
    try:
        _check_geometry(xn, 1.0)  # the patch the solve rejects: the solve names it, with its points
    except RbfSingular:
        return eta0_n, math.nan, False
    if screen:
        d, _ = cKDTree(xn).query(xn, k=2, workers=1)
        if predicted_cond(kernel, n, float(d[:, 1].min())) < target:
            return eta0_n, math.nan, False
    lam_min, lam_max, max_abs = measured_ends(xn, kernel, radius_n, eta0_n, target)
    lam_min, lam_max = float(f"{lam_min:.6g}"), float(f"{lam_max:.6g}")
    e, c = search_eta(lam_min, lam_max, eta0_n, ETA_MAX_FACTOR * max_abs, target)
    return e, c, True


def _s_value(spec: Any, group: Any, s: float | None) -> float:
    if s is not None:
        return float(s)
    if group is not None:
        return float(spec.s_of(group))
    given = spec.smoothing.s
    return 0.0 if given is None else float(given)


def smooth_patches(
    points: ArrayLike,
    patches: PatchSet,
    spec: Any,
    *,
    group: Any = None,
    s: float | None = None,
    radius: float | Sequence[float] | None = None,
    matrices: Sequence[ArrayLike | None] | None = None,
    h_bar: float | None = None,
    only: Sequence[int] | None = None,
    screen: bool = True,
    threads: int = 1,
    tick: Tick | None = None,
    max_memory: int = 2 << 30,
) -> PatchEta:
    """η, cond₂ and the measured flag of every patch (the module text). `radius` — the `a` of the finite-radius kernel
    (one number or one per patch; the default is `kernel_radii(spec.kernel, spec.kernel_radius, patches)`). `only` —
    the numbers of the patches to look at (the others get η₀, not measured: `refine_patch`)."""
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise _bad("Точки заданы неверно: нужны n × 3 координаты.",
                   "Это ошибка подготовки данных: сообщите разработчикам.", "rbf_bad_points")  # fmt: skip
    if patches.indices.size and int(patches.indices.max()) >= pts.shape[0]:
        raise _bad("Точки не соответствуют участкам: другое число точек.",
                   "Передайте тот же массив точек, по которому строились участки.", "rbf_bad_points")  # fmt: skip
    kernel = str(spec.kernel)
    if kernel not in ETA_POWER:
        raise _bad("Неизвестное ядро RBF.", "Выберите ядро из списка.")
    sm = spec.smoothing
    m = patches.n_patches
    if patches.matrix is not None:
        pts = transform_points(pts, patches.matrix)
    hb = nearest_median(pts) if h_bar is None else float(h_bar)
    eta0 = eta(kernel, _s_value(spec, group, s), hb)
    out_eta, out_cond = np.full(m, _round(eta0)), np.full(m, math.nan)
    out_exact = np.zeros(m, dtype=bool)
    if sm.mode != "auto":
        return PatchEta(out_eta, out_cond, out_exact)
    target = float(COND_TARGET if sm.cond_target is None else sm.cond_target)
    if matrices is not None and len(matrices) != m:
        raise _bad("Матриц анизотропии участков не столько же, сколько участков.",
                   "Задайте по одной матрице (или None) на участок.")  # fmt: skip
    radii = _per_patch(kernel_radii(kernel, spec.kernel_radius, patches) if radius is None else radius, m)
    power = ETA_POWER[kernel]
    todo = list(range(m)) if only is None else sorted({int(j) for j in only})
    budget = _Budget(int(max_memory))

    def one(j: int) -> tuple[float, float, bool]:
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
            r = float(patches.radii[j])
            rad = radii[j]
            e, cnd, ex = patch_eta((x - c) / r, kernel, None if rad is None else rad / r, eta0 / r**power, target,
                                   screen=screen)  # fmt: skip
            return e * r**power, cnd, ex
        finally:
            budget.release(need)

    def collect(j: int, res: tuple[float, float, bool]) -> None:
        out_eta[j], out_cond[j], out_exact[j] = _round(res[0]), res[1], res[2]
        if tick is not None:
            tick()

    nthreads = max(int(threads), 1)
    with single_blas_thread():
        if nthreads == 1 or len(todo) < 2:
            for j in todo:
                collect(j, one(j))
            return PatchEta(out_eta, out_cond, out_exact)
        pool = ThreadPoolExecutor(min(nthreads, len(todo)), thread_name_prefix="bf-smooth")
        try:
            futures: dict[int, Any] = {}
            sizes = patches.sizes
            for j in sorted(todo, key=lambda k: (-int(sizes[k]), k)):
                futures[j] = pool.submit(one, j)
            for j in todo:
                collect(j, futures[j].result())
        finally:
            budget.stop()  # the patches that wait for memory do not begin
            pool.shutdown(wait=True, cancel_futures=True)
    return PatchEta(out_eta, out_cond, out_exact)


def eta_for_patches(
    points: ArrayLike,
    values: ArrayLike | None,
    patches: PatchSet,
    spec: Any,
    **kw: Any,
) -> F8:
    """η of every patch, f8[m], metres: `solve_patches(…, eta=eta_for_patches(…))`. The keywords are those of
    `smooth_patches` (`group`, `s`, `radius`, `matrices`, `h_bar`, `threads`, `tick`, `max_memory`)."""
    if values is not None and np.asarray(values).shape != (np.asarray(points).shape[0],):
        raise _bad("Значений не столько же, сколько точек.", "Передайте по одному значению на точку.", "rbf_bad_points")
    return smooth_patches(points, patches, spec, **kw).eta


def refine_patch(
    points: ArrayLike,
    patches: PatchSet,
    spec: Any,
    j: int,
    **kw: Any,
) -> tuple[float, float]:
    """(η, cond₂) of patch `j` measured whatever the screen says: the answer to the `rbf_singular` (`extra["patch"]`)
    of a patch the screen let through. Same keywords as `smooth_patches`."""
    res = smooth_patches(points, patches, spec, only=[j], screen=False, **kw)
    return float(res.eta[j]), float(res.cond[j])


__all__ = ["DENSE_N", "SAFETY", "SCREEN", "PatchEta", "cond2", "eta_for_patches", "kernel_radii", "measured_ends",
           "nearest_median", "patch_eta", "predicted_cond", "refine_patch", "search_eta", "smooth_patches"]  # fmt: skip
