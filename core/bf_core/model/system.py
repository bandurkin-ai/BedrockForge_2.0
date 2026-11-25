"""The linear system of one patch of the implicit model (ТЗ 8.9.2, Т8.9-3; ADR 0026 §3, решения 7, 9, 11а; Э19, 19.2).

    [ Φ + η·I   P ] [ λ ]   [ f ]          Φ_ij = φ(‖x_i − x_j‖), P = (1, x, y, z) of the points
    [   Pᵀ      0 ] [ c ] = [ 0 ]          in the NORMALISED coordinates of the patch: (x − centre) / R

    sol = solve_patch(xyz, f, kind="cubic", radius=None, eta=0.0, centre=…, scale=R)  one patch: LU, cond₁, λ and c
    sol.evaluate(targets)       f(x) = Σ λ_i·φ(‖x − x_i‖) + c0 + c·(x − centre)/R      (a check, not the evaluator of
                                the field: that is `evaluate.py`, with the partition of unity and numba over bricks)
    a = assemble(xn, kind, radius_n, eta)   the matrix of the system for points already normalised
    with single_blas_thread(): …            BLAS on one thread around a whole pool of patches (below)

`xyz` are the points of the patch in the space AFTER the anisotropy A of 8.9.2 (the caller turns them), in metres,
float64; `f` — the values. `radius` is `a` of the finite-radius kernel in the same metres. η = s·h̄^k is the smoothing
in the units of φ(metres) (8.9.2); the solution is exactly that of the system in metres.

Equilibration (a decision of 19.2, the solution does not change): the block is built on x/R, not on metres.
φ(R·r) = R^s·φ(r) (s = 1 linear, 3 cubic, 0 finite radius with a/R), so  Φ_m = R^s·Φ_n  and
(Φ_n + η/R^s·I)·(R^s·λ) + P·c = f,  Pᵀ·(R^s·λ) = 0:  λ_m = λ_n / R^s, c is the same. Φ and P are then of one size
(a cubic kernel in metres is 1e9 against 1 in P) and the cond₁ below is that of the equilibrated matrix.

Failure — `RbfSingular` (422 `rbf_singular`, text and hint in Russian; `extra`: `reason` and `points`, the numbers of
the rows of the call, which the caller maps to the sources of the points): fewer than 4 points; the points on a plane
or a line (P of rank < 4: no η helps); coincident points at η = 0; an exact zero pivot or cond₁ ≥ `cond_max` (1/ε by
default: the system is a singular one to the working precision). NaN and ∞ in the data are 422 `rbf_bad_data`, never
zero (8.0.1).

Threads (ADR 0026, решение 9): the patches go to a pool of Python threads; the LAPACK of SciPy lets the GIL go. BLAS
must run on ONE thread inside every patch — 4 patches × 4 BLAS threads is slower and the sums of a multi-thread BLAS
are not the bytes of a one-thread one (Т8.9-8). The limit of OpenBLAS (pthreads) is of the whole process, and
threadpoolctl lets only ONE thread set it (the first; the calls of the others do nothing, and its `with` gives the
control back on exit): a limit set by every patch would be lost for the others in the middle of their LU. So the
thread that runs a pool enters `single_blas_thread()` once around the whole run and the workers set nothing; a patch
solved outside such a section (one thread, no pool) limits BLAS itself. A pool of threads calling `solve_patch`
WITHOUT the section is therefore not supported (the BLAS threads and the bytes of the result are not defined).

numba is not imported here: the kernels (`kernels.py`) are imported inside the functions that build a block, so a job
handler that imports this module does not pay for numba in the API process.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from scipy.linalg import LinAlgWarning, lu_factor, lu_solve
from scipy.linalg.lapack import dgecon
from threadpoolctl import threadpool_limits

from bf_core.api.errors import ApiError

if TYPE_CHECKING:
    from types import ModuleType

#: cond₁ at which a system is taken as singular to the working precision (rcond < ε).
COND_MAX = 1.0 / float(np.finfo(np.float64).eps)
#: the points are on a plane or a line when σ_min/σ_max of their centred coordinates is below this.
PLANAR_TOL = 1e-10
HINT_SMOOTHING = "Включите сглаживание «Авто» или задайте s > 0."
MAX_PAIRS = 20  # the pairs of coincident points named in an error
_CHUNK = 2048  # the targets of `evaluate` per block of φ


class RbfSingular(ApiError):
    """422 `rbf_singular`: the system of a patch has no solution. `extra["reason"]`: `few_points`, `coplanar`,
    `coincident`, `ill_conditioned`; `extra["points"]` — pairs [first, second] of coincident rows (coincident)."""

    def __init__(self, reason: str, message: str, hint: str, *, points: list[list[int]] | None = None,
                 cond1: float | None = None) -> None:  # fmt: skip
        extra: dict[str, Any] = {"reason": reason}
        if points:
            extra["points"] = points
        if cond1 is not None and math.isfinite(cond1):
            extra["cond1"] = cond1
        super().__init__(422, "rbf_singular", message, hint, extra)


def _bad_data(message: str, hint: str) -> ApiError:
    return ApiError(422, "rbf_bad_data", message, hint)


def _kn() -> ModuleType:
    from . import kernels  # numba is loaded here, at the first block, never at the import of this module

    return kernels


def kernel_scale(kind: str | int, scale: float) -> float:
    """R^s of the equilibration: φ(R·r) = R^s·φ(r)."""
    return float(scale) ** _kn().homogeneity(kind)


_managed = 0  # sections `single_blas_thread` open now (any thread)
_lock = threading.Lock()


@contextmanager
def single_blas_thread() -> Iterator[None]:
    """BLAS on one thread for the duration, for the thread that runs a pool of patches (a `with` of threadpoolctl: it
    takes the control of the limit and gives it back on exit). Nested sections are fine; while one is open
    `solve_patch` does not touch the limit."""
    global _managed
    with threadpool_limits(limits=1, user_api="blas"):
        with _lock:
            _managed += 1
        try:
            yield
        finally:
            with _lock:
                _managed -= 1


def _blas_guard() -> AbstractContextManager[Any]:
    return nullcontext() if _managed else threadpool_limits(limits=1, user_api="blas")


def _aligned(count: int) -> np.ndarray:
    """float64 [count] whose address is a multiple of 64: the kernels of OpenBLAS sum in an order that depends on the
    alignment of their operands, so a buffer from `malloc` (16 bytes, and a different offset every run) would give
    other last bits to the same patch. The matrix of a system and its right-hand side are taken from here; with the
    leading dimension n + 4 fixed by the patch, every block LAPACK touches then has the same offset in every run and
    on every thread."""
    raw = np.empty(count * 8 + 64, dtype=np.uint8)
    off = (-raw.ctypes.data) % 64
    return raw[off : off + count * 8].view(np.float64)


def assemble(xn: Any, kind: str | int, radius: float | None = None, eta: float = 0.0) -> np.ndarray:
    """The matrix [Φ + η·I, P; Pᵀ, 0] of the points `xn` [n, 3] already in the coordinates of the patch; `radius` is
    `a` of the finite-radius kernel in those coordinates. [n + 4, n + 4], float64, C-ordered."""
    pts = np.array(xn, dtype=np.float64, order="C", copy=True)
    n = pts.shape[0]
    a = _aligned((n + 4) * (n + 4)).reshape(n + 4, n + 4)
    _kn().gram(pts, kind, radius, eta=eta, out=a)
    a[:n, n] = 1.0
    a[:n, n + 1 :] = pts
    a[n:, :n] = a[:n, n:].T
    a[n:, n:] = 0.0
    return a


@dataclass(frozen=True)
class PatchSolution:
    """λ, c and the diagnostics of one patch. `lam` is in the units of the system in metres (f = Σ lam_i·φ_m(…) + …);
    `poly` = (c0, cx, cy, cz) of the normalised coordinates (x − centre) / scale; `cond1` — the LAPACK estimate."""

    points: np.ndarray
    lam: np.ndarray
    poly: np.ndarray
    centre: np.ndarray
    scale: float
    kind: str
    radius: float | None
    eta: float
    cond1: float

    @property
    def rcond(self) -> float:
        return 1.0 / self.cond1 if self.cond1 > 0.0 else 0.0

    def gram_apply(self, targets: Any) -> np.ndarray:
        """Σ_i λ_i·φ(‖t − x_i‖) at the `targets` [m, 3] (no polynomial); the distances are taken from the centre of the
        patch, which is exact for the coordinates of 1e6–1e7 m."""
        kn = _kn()
        t = np.array(targets, dtype=np.float64, order="C", copy=True).reshape(-1, 3) - self.centre
        src = self.points - self.centre
        out = np.empty(t.shape[0], dtype=np.float64)
        for i in range(0, t.shape[0], _CHUNK):
            out[i : i + _CHUNK] = kn.cross(t[i : i + _CHUNK], src, self.kind, self.radius) @ self.lam
        return out

    def evaluate(self, targets: Any) -> np.ndarray:
        """f at the `targets` [m, 3]: the sum of the kernels plus the polynomial."""
        t = np.array(targets, dtype=np.float64, order="C", copy=True).reshape(-1, 3)
        tn = (t - self.centre) / self.scale
        return self.gram_apply(t) + self.poly[0] + tn @ self.poly[1:]


def _frame(x: np.ndarray, centre: Any, scale: float | None) -> tuple[np.ndarray, float]:
    """The centre of the patch and R: the centre of the box and the farthest point, unless the caller (the octree) gave
    them."""
    c = 0.5 * (x.min(axis=0) + x.max(axis=0)) if centre is None else np.asarray(centre, dtype=np.float64).reshape(3)
    if scale is None:
        d = np.sqrt(np.max(np.sum((x - c) ** 2, axis=1)))
        scale = d if d > 0.0 else 1.0
    if not (math.isfinite(scale) and scale > 0.0) or not np.all(np.isfinite(c)):
        raise ApiError(422, "bad_param", "Центр или радиус участка заданы неверно.", "Радиус участка должен быть > 0.")
    return c, float(scale)


def _coincident_pairs(xn: np.ndarray) -> list[list[int]]:
    """[first, k] for every row k equal to an earlier one (all three coordinates), by k, at most `MAX_PAIRS`."""
    _, first, inverse = np.unique(xn, axis=0, return_index=True, return_inverse=True)
    inverse = np.asarray(inverse).reshape(-1)
    dup = np.nonzero(first[inverse] != np.arange(len(xn)))[0]
    return [[int(first[inverse[k]]), int(k)] for k in dup[:MAX_PAIRS]]


def _check_geometry(xn: np.ndarray, eta: float) -> None:
    """The degenerate patches that are known before the LU: P of rank < 4 and coincident points without smoothing."""
    n = xn.shape[0]
    if n < 4:
        raise RbfSingular(
            "few_points",
            f"В участке {n} точек: для системы RBF нужно не меньше 4 (линейная часть 1, x, y, z).",
            "Увеличьте N_min или добавьте данные в этой области.",
        )
    sv = np.linalg.svd(xn - xn.mean(axis=0), compute_uv=False)
    if sv[0] == 0.0 or sv[-1] <= PLANAR_TOL * sv[0]:
        raise RbfSingular(
            "coplanar",
            "Точки участка лежат на одной плоскости или прямой: линейная часть системы RBF не определена.",
            "Добавьте данные вне этой плоскости (контакты на других глубинах, ручные точки) или увеличьте D_max; "
            "сглаживание здесь не поможет.",
        )
    if eta == 0.0:
        pairs = _coincident_pairs(xn)
        if pairs:
            raise RbfSingular(
                "coincident",
                f"В участке есть совпадающие точки (например, {pairs[0][0] + 1}-я и {pairs[0][1] + 1}-я): "
                "система RBF при нулевом сглаживании вырождена.",
                HINT_SMOOTHING,
                points=pairs,
            )


def solve_patch(
    xyz: Any,
    values: Any,
    kind: str | int = "linear",
    radius: float | None = None,
    eta: float = 0.0,
    *,
    centre: Any = None,
    scale: float | None = None,
    cond_max: float = COND_MAX,
) -> PatchSolution:
    """Assemble and solve the system of one patch (see the module text). `centre`, `scale` — the centre and R of the
    patch (default: the box centre of the points and the farthest point). Raises `RbfSingular` / 422 `rbf_bad_data`."""
    kn = _kn()
    code = kn.kernel_code(kind)
    name = next(k for k, v in kn.KERNELS.items() if v == code)
    x = np.array(xyz, dtype=np.float64, order="C", copy=True)
    f = np.array(values, dtype=np.float64, copy=True).reshape(-1)
    if x.ndim != 2 or x.shape[1] != 3 or f.shape[0] != x.shape[0]:
        raise _bad_data(
            "Число значений не совпадает с числом точек участка.", "Это ошибка данных: сообщите разработчику."
        )
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(f))):
        raise _bad_data(
            "В данных участка есть пропуски или бесконечные значения.", "Уберите такие точки: пропуск не равен нулю."
        )
    if not (math.isfinite(eta) and eta >= 0.0) or (code == kn.WENDLAND and not (radius and radius > 0.0)):
        raise ApiError(422, "bad_param", "Сглаживание или радиус ядра заданы неверно.", "Нужны s ≥ 0 и радиус a > 0.")
    n = x.shape[0]
    c, r = _frame(x, centre, scale) if n else (np.zeros(3), 1.0)
    xn = (x - c) / r
    _check_geometry(xn, eta)

    s = r ** kn.homogeneity(code)
    a = assemble(xn, code, None if radius is None else radius / r, eta / s)
    anorm = float(np.max(np.sum(np.abs(a), axis=0)))
    rhs = _aligned(n + 4)
    rhs[:n], rhs[n:] = f, 0.0
    try:
        with _blas_guard():
            # `a` is symmetric: its transpose is the same matrix in the Fortran order LAPACK wants, factored in place
            lu, piv = lu_factor(a.T, overwrite_a=True, check_finite=False)
            if not np.all(np.diagonal(lu) != 0.0):
                raise LinAlgWarning("zero pivot")
            rcond, info = dgecon(lu, anorm, norm="1")
            sol = lu_solve((lu, piv), rhs, overwrite_b=True, check_finite=False)
    except LinAlgWarning:
        raise RbfSingular(
            "ill_conditioned",
            "Система RBF участка вырождена: нулевой ведущий элемент при разложении.",
            HINT_SMOOTHING,
        ) from None
    # the estimate is a diagnostic; 6 digits keep it the same in every run (the work arrays of xGECON are not aligned)
    cond1 = float(f"{1.0 / rcond:.6g}") if (info == 0 and rcond > 0.0) else math.inf
    if not (cond1 < cond_max) or not np.all(np.isfinite(sol)):
        raise RbfSingular(
            "ill_conditioned",
            "Система RBF участка вырождена: число обусловленности превышает предел точности "
            f"({'∞' if not math.isfinite(cond1) else format(cond1, '.2e')}).",
            HINT_SMOOTHING,
            cond1=cond1,
        )
    return PatchSolution(
        points=x, lam=sol[:n] / s, poly=sol[n:].copy(), centre=c, scale=r, kind=name,
        radius=None if radius is None else float(radius), eta=float(eta), cond1=cond1,
    )  # fmt: skip
