"""What the scenarios `simulate_*` share (ADR 0024 «Замеры», подшаг 18.36; ТЗ 8.8.1, № 84, Д-23).

    World.build(work, params)       `prepare`: the fields and the samples of a synthetic 2D deposit in the work folder
    w = World.open(work)            `run`: the world; w.samples(side), w.cat(...), w.secondary(side), w.grid(side)
    model_y(), ellipsoid(), indicator_model(p)   the variogram models of the scenarios (the range is 10 cells)
    ks_distance, gamma_ratio, cancel_seconds, cost    the checks and the numbers of every scenario

The deposit is the one of the tests of 18.31–18.34 (Т8.8-14, Т18б-С1…С3) made for any size: a grid of `rows` nodes,
one layer (100×100×1 for `ci`, 1000×1000×1 for `full`), cells 1×1×1, a spherical model of the range 10 cells with the
nugget 0.1 in Y; `samples` samples near the centres of distinct cells (the jitter 0.4 cell, the density 0.03 per cell
as in the tests). The fields are drawn by the circulant embedding (FFT) — exact for a model that is zero beyond its
range, no Cholesky of n × n: y₂ (the secondary), w (independent), the primary y₁ = ρ·y₂ + √(1 − ρ²)·w with the same
covariance C (so the model of the samples is the model of the run), ρ = `RHO`. The grade of a sample z = exp(1 + 0.6·y₁)
(skewed), the secondary of a node exp(0.5 + 0.3·y₂), the category of a sample — the classes of y₁ at the 30 and 70 %
(the codes 3, 5, 9).

The statistics the scenarios check are the tests': the Kolmogorov–Smirnov distance of the realizations to the
distribution of the samples (`ks_distance`), the variogram of the realizations on lags along x and y against the model
(`gamma_ratio`), the shares of the classes, the bits against the same call of the kernel on 1 thread, the cancel
(`cancel_seconds`)."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from . import estimate_common as ec

RANGE = 10.0  # cells, the range of every model
NUGGET = 0.1  # of the model in Y
STEPS = (1.0, 1.0, 1.0)
RHO = 0.7  # the correlation of the primary with the secondary in the data
JITTER = 0.4  # cells, the samples near the centres of distinct cells
SMALL = 100  # the side of the grid of the checks against 1 thread
WORLD = "world.npz"
QUANTILES = (0.1, 0.3, 0.5, 0.7, 0.9)  # the thresholds of the continuous SIS
CODES = (3, 5, 9)  # the categories of the categorical SIS
CAT_CUTS = (0.3, 0.7)
TARGET_NODES = 20_000_000  # 1 млн узлов × 20 реализаций (ТЗ № 84)
KS_MAX = 0.05  # Т8.8-14
GAMMA_TOL = 0.15  # Т8.8-14, Т18б-С1: ±15 % of the model
LAGS = (1, 3, 5, 7)


def limit_threads(params: dict[str, Any]) -> int:
    return ec.limit_threads(params)


def peak_mb() -> float | None:
    return ec.peak_mb()


def side_of(params: dict[str, Any], key: str = "rows") -> int:
    return math.ceil(math.sqrt(int(params[key])))


# --- the models -----------------------------------------------------------------------------------------------------


def model_y() -> Any:
    """The variogram model in normal scores: nugget 0.1, a spherical structure 0.9 of the range 10 cells, sill 1."""
    from bf_core.geostat.variogram.model import Model, Structure

    return Model(NUGGET, (Structure("spherical", 1.0 - NUGGET, RANGE, RANGE, RANGE),))


def ellipsoid(radius: float = RANGE) -> Any:
    from bf_core.geostat.estimate.search import Ellipsoid

    return Ellipsoid.of((0.0, 0.0, 0.0), (radius, radius, radius))


def indicator_model(p: float, nugget: float = 0.3, kind: str = "exponential") -> Any:
    """The model of an indicator of the share p: the sill p(1 − p), the nugget a part of it (as the tests of 18.33)."""
    from bf_core.geostat.variogram.model import Model, Structure

    s = p * (1.0 - p)
    return Model(nugget * s, (Structure(kind, (1.0 - nugget) * s, RANGE, RANGE, RANGE),))


# --- the world ------------------------------------------------------------------------------------------------------


def field_pair(nx: int, ny: int, model: Any, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Two independent Gaussian fields [ny, nx] of the covariance of `model` (unit variance) by the circulant
    embedding: the covariance is zero beyond the range, so a period of nx + range cells is exact."""
    from scipy.fft import fft2, next_fast_len

    mx, my = next_fast_len(nx + int(RANGE) + 2), next_fast_len(ny + int(RANGE) + 2)
    ix = np.minimum(np.arange(mx), mx - np.arange(mx)).astype(np.float64)
    iy = np.minimum(np.arange(my), my - np.arange(my)).astype(np.float64)
    h = np.zeros((my, mx, 3))
    h[..., 0] = ix[None, :]
    h[..., 1] = iy[:, None]
    lam = np.maximum(fft2(np.asarray(model.cov(h))).real, 0.0)
    noise = rng.standard_normal((my, mx)) + 1j * rng.standard_normal((my, mx))
    f = fft2(np.sqrt(lam / (mx * my)) * noise)
    return np.ascontiguousarray(f.real[:ny, :nx]), np.ascontiguousarray(f.imag[:ny, :nx])


class World:
    """The arrays of the work folder: the samples (xyz, z, the category), the secondary at the nodes, the grid."""

    def __init__(self, nx: int, ny: int, xyz: np.ndarray, z: np.ndarray, cat: np.ndarray, sec: np.ndarray) -> None:
        self.nx, self.ny, self.xyz, self.z, self.cat_all, self.sec = nx, ny, xyz, z, cat, sec

    @classmethod
    def build(cls, work: Path, params: dict[str, Any]) -> int:
        """`prepare` of a scenario: the world of `rows` nodes and `samples` samples → the number of samples."""
        side = side_of(params)
        ns = int(params["samples"])
        rng = np.random.default_rng(int(params.get("seed", 7)))
        y2, w = field_pair(side, side, model_y(), rng)
        y1 = RHO * y2 + math.sqrt(1.0 - RHO * RHO) * w
        cells = np.sort(rng.choice(side * side, size=ns, replace=False))
        i, j = cells % side, cells // side
        xyz = np.column_stack([i + 0.5 + rng.uniform(-JITTER, JITTER, ns), j + 0.5 + rng.uniform(-JITTER, JITTER, ns),
                               np.full(ns, 0.5)])  # fmt: skip
        yv = y1.reshape(-1)[cells]
        cat = np.asarray(CODES)[np.digitize(yv, np.quantile(yv, CAT_CUTS))]
        np.savez(work / WORLD, xyz=xyz, z=np.exp(1.0 + 0.6 * yv), cat=cat, sec=np.exp(0.5 + 0.3 * y2.reshape(-1)),
                 side=np.asarray(side))  # fmt: skip
        return ns

    @classmethod
    def open(cls, work: Path) -> World:
        with np.load(work / WORLD) as f:
            side = int(f["side"])
            return cls(side, side, f["xyz"], f["z"], f["cat"], f["sec"])

    def inside(self, side: int) -> np.ndarray:
        return (self.xyz[:, 0] < side) & (self.xyz[:, 1] < side)

    def samples(self, side: int | None = None) -> Any:
        """`RowSamples` of the samples in the square [0, side)² (all by default), as `samples.prepare` makes them."""
        from bf_core.geostat.estimate.samples import prepare

        keep = np.ones(self.z.size, bool) if side is None else self.inside(side)
        xyz, z = self.xyz[keep], self.z[keep]
        n = z.size
        return prepare(xyz, z, None, None, np.zeros(n, np.int32), ["h"], np.zeros(n, np.int16),
                       np.flatnonzero(keep).astype(np.int64), np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip

    def cat(self, s: Any) -> np.ndarray:
        """The category of every sample of `s` in the order `prepare` gave it (`row_id` — the place in the world)."""
        return self.cat_all[np.asarray(s.row_id)]

    def secondary(self, side: int) -> np.ndarray:
        """The secondary at the nodes of the grid [0, side)² in the order of the model (x fastest)."""
        return np.ascontiguousarray(self.sec.reshape(self.ny, self.nx)[:side, :side]).reshape(-1)

    def grid(self, side: int | None = None) -> Any:
        from bf_core.geostat.simulation import nodes as nd

        sx = self.nx if side is None else side
        return nd.NodeGrid.of((sx, sx, 1), np.arange(sx * sx, dtype=np.uint64))


def centres(grid: Any) -> np.ndarray:
    from bf_core.geostat.simulation.sgs import grid_centres

    return grid_centres(grid, STEPS)


# --- the numbers of a run -------------------------------------------------------------------------------------------


def cost(seconds: float, nodes: int, n_real: int) -> dict[str, float]:
    """The microseconds of a node of a realization and the straight line to 1 млн узлов × 20 (ТЗ № 84)."""
    per = nodes * n_real
    return {"us_per_node": round(1e6 * seconds / max(per, 1), 3),
            "forecast_1m_s": round(seconds * TARGET_NODES / max(per, 1), 1)}  # fmt: skip


def timed(fn: Callable[[], Any]) -> tuple[Any, float, float]:
    """`fn()` → (its result, the wall seconds, the CPU seconds of the process). The machines of the bench are shared
    (the runner of CI, a workstation with other jobs): the CPU seconds over the threads is what the call costs on a
    quiet machine, the wall seconds is what it took here."""
    c0, t0 = time.process_time(), time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t0, time.process_time() - c0


def ks_distance(values: np.ndarray, cdf: Callable[[np.ndarray], np.ndarray]) -> float:
    """The Kolmogorov–Smirnov distance of the values to a distribution function."""
    a = np.sort(np.asarray(values).reshape(-1))
    n = a.size
    f = cdf(a)
    return float(max(np.max(np.arange(1, n + 1) / n - f), np.max(f - np.arange(n) / n)))


def gamma_ratio(fields: Iterable[np.ndarray], gamma: Callable[[int], float], lags: Sequence[int] = LAGS,
                shape: tuple[int, int] | None = None) -> list[float]:  # fmt: skip
    """The experimental variogram of the realizations (arrays [ny, nx]; x and y together) at the lags of the cells
    over the model γ(lag) → the ratio for every lag. A boolean field (an indicator) is taken as it is."""
    total = {lag: 0.0 for lag in lags}
    count = 0
    for r in fields:
        f = r.reshape(shape) if shape is not None else r
        for lag in lags:
            for a, b in ((f[lag:, :], f[:-lag, :]), (f[:, lag:], f[:, :-lag])):
                d = (a != b) if f.dtype == np.bool_ else (a - b)
                total[lag] += 0.5 * float(np.mean(d if f.dtype == np.bool_ else d * d))
        count += 2
    return [total[lag] / count / gamma(lag) for lag in lags]


def gamma_of(model: Any) -> Callable[[int], float]:
    return lambda lag: float(model.gamma(np.array([[float(lag), 0.0, 0.0]]))[0])


class Stopped(Exception):
    """What the tick of `cancel_seconds` raises."""


def cancel_seconds(call: Callable[[Callable[[float | None, str | None], None]], Any]) -> float:
    """Cancel in the middle of a run: `call(tick)` runs a simulation, the tick raises at the first progress of the
    realizations → the seconds from the raise to the return of the call (the running portions stop at the next
    portion of 2¹⁵ nodes, the pool is shut down). A call that was not cancelled is a failure."""
    at: list[float] = []

    def tick(progress: float | None, label: str | None) -> None:
        if not at and progress and label and label.startswith("Реализации"):
            at.append(time.perf_counter())
            raise Stopped

    try:
        call(tick)
    except Stopped:
        return time.perf_counter() - at[0]
    raise AssertionError("the simulation was not cancelled by its tick")


def assert_same(a: np.ndarray, b: np.ndarray, what: str) -> None:
    assert a.shape == b.shape and a.tobytes() == b.tobytes(), f"{what}: the bits differ"
