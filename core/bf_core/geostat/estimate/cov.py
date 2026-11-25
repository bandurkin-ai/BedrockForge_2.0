"""Covariance for the estimation (ТЗ 8.8.2; ADR 0024 §5; Э18, 18.3; Э19 reuses it): a numba mirror of
`geostat.variogram.model.Model.cov`, the point–block and block–block means, the discretization of a block.
float64 throughout; no `prange`, no `fastmath`, no BLAS; every kernel is `cache=True, nogil=True`.

The model goes into the kernels as three flat arrays (`CovModel`, from `cov_model(Model)`):

    kinds int64 [S]       0 spherical, 1 exponential, 2 gaussian (the structures in the order of the model)
    pars  float64 [S, 13] per structure: c, a1, a2, a3, then R row by row (R 3×3 of `geostat.rotation`:
                          the rows are the axes u₁, u₂, u₃ in the world — the angles are in R, not passed)
    head  float64 [3]     C0 (nugget), the sill = C0 + Σ c_s (added in the order of `Model.sill`), Σ c_s

    h_r = √((u₁·Δ / a1)² + (u₂·Δ / a2)² + (u₃·Δ / a3)²)      the same operations in the same order as `Model`
    C(Δ)  = sill − (C0 + Σ c_s·g_s(h_r,s))     C(0) = sill       with the nugget: point–point, and N = 1
    C*(Δ) = Σc − Σ c_s·g_s(h_r,s)              C*(0) = Σ c_s     without it (GSLIB kb2d/kt3d): the blocks, N > 1

A NaN in Δ gives NaN — a gap is not a zero. `C` equals `Model.cov` to 1e−15 (the test holds it).

Block of N = nx·ny·nz points: u_k are the centres of the sub-cells of the block, as offsets from its centre in the
WORLD axes — the local offsets turned by R_z(φ) (φ of `grid.rotation` of a model, degrees, counter-clockwise seen
from above). The order of the points: ix slowest, iz fastest (the loops of GSLIB); N = 1 is the centre itself.

    C̄_iV = (1/N)·Σ_k C*(x_i − (x_0 + u_k))          N = 1: C(x_i − x_0)
    C̄_VV = (1/N²)·Σ_k Σ_l C*(u_k − u_l)             N = 1: C(0); the full double sum, k = l included (C*(0) = Σc)

    cov_model(model) -> CovModel                       .args = (kinds, pars, head); `*cov.args` into the kernels
    cov_c(kinds, pars, head, dx, dy, dz) -> float      C(Δ); `cov_cstar(...)` — C*(Δ)         (call them from njit)
    cov_pb(kinds, pars, head, px, py, pz, cx, cy, cz, offs [N, 3]) -> float                     C̄_iV
    cov_bb(kinds, pars, head, offs [N, 3]) -> float                                               C̄_VV
    cov_matrix(kinds, pars, head, xyz [n, 3], out [n, n])       out[i, j] = C(x_i − x_j), bitwise symmetric
    cov_rhs(kinds, pars, head, xyz [n, 3], centre [3], offs [N, 3], out [n])      out[i] = C̄_iV
    disc_points(size [3], disc [3], phi=0.0) -> offs [N, 3]
    BlockCov(cov).vv(size, disc, phi=0.0) -> C̄_VV      the cache by (size, disc, φ) of one model; `.points(...)`
    warm_kernels() -> None                             every kernel on writable and on read-only arrays

`cov_c`, `cov_cstar` are `inline="always"`: a `cache=True` callee is linked as object code and is not inlined by
LLVM, which costs ×4–5 per call (measured: 45 → 10 ns); a kernel of 18.4 calls them in its loops.
A read-only array (a cached column of `stats.source`, a memory map) is another signature for numba: it compiles
the kernel anew. `warm_kernels` compiles both, as the callers give them (the lesson of 17.39б: cold start ×4–5).
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.geostat.rotation import bad_param
from bf_core.geostat.variogram.model import Model
from bf_core.jit import njit
from bf_core.sections.geometry import sin_cos_deg

KIND_CODES = {"spherical": 0, "exponential": 1, "gaussian": 2}
PARS = 13  # c, a1, a2, a3, R (9)
MAX_DISC = 10  # ТЗ 8.8.2: 1–10 per axis
HINT_DISC = "Задайте три целых числа от 1 до 10."


# --- the model as arrays ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CovModel:
    """The arrays of a model for the kernels. Created writable, C-contiguous float64 / int64."""

    kinds: np.ndarray
    pars: np.ndarray
    head: np.ndarray

    @property
    def args(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return self.kinds, self.pars, self.head

    @property
    def sill(self) -> float:
        return float(self.head[1])

    @property
    def nugget(self) -> float:
        return float(self.head[0])

    @property
    def key(self) -> tuple[Any, ...]:
        """What the model is made of — for a cache."""
        return self.kinds.tobytes(), self.pars.tobytes(), self.head.tobytes()


def cov_model(model: Model) -> CovModel:
    """`Model` → the arrays. The sill is summed as `Model.sill` does (C0 first, then the structures in order)."""
    s = len(model.structures)
    kinds = np.zeros(s, dtype=np.int64)
    pars = np.zeros((s, PARS), dtype=np.float64)
    csum = 0.0
    for i, st in enumerate(model.structures):
        kinds[i] = KIND_CODES[st.type]
        pars[i, 0] = st.c
        pars[i, 1:4] = st.ranges
        pars[i, 4:13] = np.asarray(st.axes, dtype=np.float64).reshape(9)
        csum += st.c
    head = np.array([model.c0, model.sill, csum], dtype=np.float64)
    return CovModel(kinds, np.ascontiguousarray(pars), head)


# --- point – point ---------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True, inline="always")
def _shape(kind: int, h: float) -> float:
    if kind == 0:
        if h < 1.0:
            return h * (1.5 - 0.5 * h * h)
        return 1.0
    if kind == 1:
        return -math.expm1(-3.0 * h)
    return -math.expm1(-3.0 * h * h)


@njit(cache=True, nogil=True, inline="always")
def _term(kinds: np.ndarray, pars: np.ndarray, s: int, dx: float, dy: float, dz: float) -> float:
    """c_s·g_s(h_r) of the structure s. A spherical structure with h_r² ≥ 1 is on its sill without the root:
    the result is the same bit for bit (√t ≥ 1 ⇔ t ≥ 1 for the test h < 1 of `Model`, and g = 1 on both sides)."""
    q1 = (dx * pars[s, 4] + dy * pars[s, 5] + dz * pars[s, 6]) / pars[s, 1]
    q2 = (dx * pars[s, 7] + dy * pars[s, 8] + dz * pars[s, 9]) / pars[s, 2]
    q3 = (dx * pars[s, 10] + dy * pars[s, 11] + dz * pars[s, 12]) / pars[s, 3]
    t = q1 * q1 + q2 * q2 + q3 * q3
    if kinds[s] == 0 and t >= 1.0:
        return pars[s, 0] * 1.0
    return pars[s, 0] * _shape(kinds[s], math.sqrt(t))


@njit(cache=True, nogil=True, inline="always")
def cov_c(kinds: np.ndarray, pars: np.ndarray, head: np.ndarray, dx: float, dy: float, dz: float) -> float:
    """C(Δ) with the nugget; C(0) = the sill; NaN in Δ — NaN."""
    if math.isnan(dx) or math.isnan(dy) or math.isnan(dz):
        return math.nan
    if dx == 0.0 and dy == 0.0 and dz == 0.0:
        return head[1]
    total = head[0]
    for s in range(kinds.shape[0]):
        total = total + _term(kinds, pars, s, dx, dy, dz)
    return head[1] - total


@njit(cache=True, nogil=True, inline="always")
def cov_cstar(kinds: np.ndarray, pars: np.ndarray, head: np.ndarray, dx: float, dy: float, dz: float) -> float:
    """C*(Δ) without the nugget; C*(0) = Σ c_s; NaN in Δ — NaN."""
    if math.isnan(dx) or math.isnan(dy) or math.isnan(dz):
        return math.nan
    if dx == 0.0 and dy == 0.0 and dz == 0.0:
        return head[2]
    total = 0.0
    for s in range(kinds.shape[0]):
        total = total + _term(kinds, pars, s, dx, dy, dz)
    return head[2] - total


# --- point – block, block – block ------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def cov_pb(
    kinds: np.ndarray,
    pars: np.ndarray,
    head: np.ndarray,
    px: float,
    py: float,
    pz: float,
    cx: float,
    cy: float,
    cz: float,
    offs: np.ndarray,
) -> float:
    """C̄_iV of the point p and the block with the centre c and the points `offs` [N, 3] (offsets from c)."""
    n = offs.shape[0]
    if n == 1:
        return cov_c(kinds, pars, head, px - (cx + offs[0, 0]), py - (cy + offs[0, 1]), pz - (cz + offs[0, 2]))
    total = 0.0
    for k in range(n):
        total += cov_cstar(kinds, pars, head, px - (cx + offs[k, 0]), py - (cy + offs[k, 1]), pz - (cz + offs[k, 2]))
    return total / n


@njit(cache=True, nogil=True)
def cov_bb(kinds: np.ndarray, pars: np.ndarray, head: np.ndarray, offs: np.ndarray) -> float:
    """C̄_VV of a block with the points `offs` [N, 3]: N = 1 — C(0), else the mean of C* over all N² pairs."""
    n = offs.shape[0]
    if n == 1:
        return head[1]
    total = 0.0
    for k in range(n):
        for m in range(n):
            total += cov_cstar(
                kinds, pars, head, offs[k, 0] - offs[m, 0], offs[k, 1] - offs[m, 1], offs[k, 2] - offs[m, 2]
            )
    return total / (n * n)


@njit(cache=True, nogil=True)
def cov_matrix(kinds: np.ndarray, pars: np.ndarray, head: np.ndarray, xyz: np.ndarray, out: np.ndarray) -> None:
    """out[i, j] = C(x_i − x_j) for i, j < n = len(xyz); the lower triangle is computed and mirrored."""
    n = xyz.shape[0]
    for i in range(n):
        for j in range(i + 1):
            c = cov_c(kinds, pars, head, xyz[i, 0] - xyz[j, 0], xyz[i, 1] - xyz[j, 1], xyz[i, 2] - xyz[j, 2])
            out[i, j] = c
            out[j, i] = c


@njit(cache=True, nogil=True)
def cov_rhs(
    kinds: np.ndarray,
    pars: np.ndarray,
    head: np.ndarray,
    xyz: np.ndarray,
    centre: np.ndarray,
    offs: np.ndarray,
    out: np.ndarray,
) -> None:
    """out[i] = C̄_iV of the sample i and the block (centre [3], points `offs` [N, 3])."""
    for i in range(xyz.shape[0]):
        out[i] = cov_pb(kinds, pars, head, xyz[i, 0], xyz[i, 1], xyz[i, 2], centre[0], centre[1], centre[2], offs)


# --- the discretization ----------------------------------------------------------------------------------------------


def _count(disc: Any) -> tuple[int, int, int]:
    try:
        values = [int(v) for v in disc]
        ok = len(values) == 3 and all(int(v) == v for v in disc)
    except (TypeError, ValueError):
        raise bad_param("disc", "Дискретизация блока задана неверно.", HINT_DISC) from None
    if not ok or not all(1 <= v <= MAX_DISC for v in values):
        raise bad_param("disc", "Дискретизация блока: от 1 до 10 по каждой оси.", HINT_DISC)
    return values[0], values[1], values[2]


def disc_points(size: Any, disc: Any, phi: float = 0.0) -> np.ndarray:
    """The N = nx·ny·nz points of a block of the `size` (x, y, z) at the centres of its sub-cells, [N, 3] float64:
    offsets from the centre of the block along the world axes, the block turned by φ degrees about +Z (counter-
    clockwise seen from above). ix is the slowest index, iz the fastest. A size of 0 along an axis puts the points
    of that axis in the centre (a flat block)."""
    nx, ny, nz = _count(disc)
    try:
        sx, sy, sz = (float(v) for v in size)
        ok = len(size) == 3 and all(math.isfinite(v) and v >= 0.0 for v in (sx, sy, sz))
    except (TypeError, ValueError):
        ok = False
    if not ok:
        raise bad_param("size", "Размер блока задан неверно.", "Ожидаются три неотрицательных числа в метрах.")
    ax = (np.arange(nx, dtype=np.float64) + 0.5) / nx - 0.5
    ay = (np.arange(ny, dtype=np.float64) + 0.5) / ny - 0.5
    az = (np.arange(nz, dtype=np.float64) + 0.5) / nz - 0.5
    gx, gy, gz = np.meshgrid(ax * sx, ay * sy, az * sz, indexing="ij")
    out = np.stack([gx.reshape(-1), gy.reshape(-1), gz.reshape(-1)], axis=1)
    p = float(phi)
    if not math.isfinite(p):
        raise bad_param("phi", "Поворот модели задан неверно.", "Ожидается число градусов.")
    if p != 0.0:
        s, c = sin_cos_deg(p)
        x, y = out[:, 0].copy(), out[:, 1].copy()
        out[:, 0] = c * x - s * y
        out[:, 1] = s * x + c * y
    return np.ascontiguousarray(out + 0.0)  # −0.0 → 0.0


class BlockCov:
    """C̄_VV of one model, computed once per (block size, discretization, φ) and kept. Thread-safe; the value
    does not depend on who computes it first. `computed` counts the double sums made, `hits` the answers from the
    cache."""

    def __init__(self, cov: CovModel) -> None:
        self.cov = cov
        self._lock = threading.Lock()
        self._vv: dict[tuple[Any, ...], float] = {}
        self._pts: dict[tuple[Any, ...], np.ndarray] = {}
        self.computed = 0
        self.hits = 0

    @staticmethod
    def _key(size: Any, disc: Any, phi: float) -> tuple[Any, ...]:
        return tuple(float(v) for v in size), _count(disc), float(phi) + 0.0

    def points(self, size: Any, disc: Any, phi: float = 0.0) -> np.ndarray:
        """`disc_points`, kept; the array is shared — read-only."""
        key = self._key(size, disc, phi)
        with self._lock:
            got = self._pts.get(key)
        if got is None:
            got = disc_points(size, disc, phi)
            got.setflags(write=False)
            with self._lock:
                got = self._pts.setdefault(key, got)
        return got

    def vv(self, size: Any, disc: Any, phi: float = 0.0) -> float:
        key = self._key(size, disc, phi)
        with self._lock:
            got = self._vv.get(key)
            if got is not None:
                self.hits += 1
                return got
        value = float(cov_bb(*self.cov.args, self.points(size, disc, phi)))
        with self._lock:
            self.computed += 1
            return self._vv.setdefault(key, value)


# --- the warm-up -----------------------------------------------------------------------------------------------------


def _frozen(a: np.ndarray) -> np.ndarray:
    out = np.array(a)
    out.setflags(write=False)
    return out


def warm_kernels() -> None:
    """Every kernel once on writable arrays and once on read-only ones (no second specialization afterwards)."""
    from bf_core.geostat.variogram.model import Structure

    model = Model(0.1, (Structure("spherical", 0.9, 30.0, 20.0, 10.0, 30.0, 10.0, 5.0),))
    cov = cov_model(model)
    offs = disc_points((4.0, 4.0, 2.0), (2, 2, 2), 10.0)
    xyz = np.array([[0.0, 0.0, 0.0], [5.0, 1.0, 2.0], [-3.0, 4.0, 1.0]])
    centre = np.array([1.0, 2.0, 0.5])
    for freeze in (False, True):
        k, p, h = (_frozen(a) if freeze else a for a in cov.args)
        o, x, c = (_frozen(a) if freeze else a for a in (offs, xyz, centre))
        cov_c(k, p, h, 1.0, 2.0, 3.0)
        cov_cstar(k, p, h, 1.0, 2.0, 3.0)
        cov_pb(k, p, h, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, o)
        cov_pb(k, p, h, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, o[:1])
        cov_bb(k, p, h, o)
        cov_matrix(k, p, h, x, np.empty((3, 3)))
        cov_rhs(k, p, h, x, c, o, np.empty(3))
