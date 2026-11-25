"""Co-SGS with a collocated secondary variable (ТЗ 8.8.1; ADR 0024 §9; Д-23, Т18б-С3, С4; Э18б, 18.34).

    res = simulate(par, samples, grid, centres, secondary, rho=None, threads=4, tick=tick)      → CosgsResult
    res.real  f8 [n_real, n]    the realizations in the grains (Z) of the primary variable, the nodes as in `grid`
    res.rho                     the coefficient used; res.secondary_y f8 [n] — the secondary in normal scores

`par` is the `sgs.SgsParams` of the SGS (the model is the variogram of the primary variable in normal scores, Д-38).
`secondary` f8 [n] — the field of the block model at the centres of the nodes (the density `SG` / `DENSITY`, answer
№ 15, or another numeric field); NaN / ±∞ — a gap, never a zero (a true 0 is a value of the table).

The way of GSLIB `sgsim` with `ktype=4` (the collocated cokriging, Markov model 1, MM1), in normal scores. For every
realization the path, the draws of the nodes, the conditioning (≤ `n_max` samples, ≤ `nodes` simulated nodes), the
point at the node and the back-transform are those of `sgs.simulate` (the same code and the same stream: the path
`path.realization_path(seed, n_real, r, n)`, the draw of the s-th node of the path is the s-th N(0, 1)).

 1. The secondary: the table of its own — the finite values of the nodes, equal weights (the rank of a group of equal
    values is its middle, so a field of a few classes has no spatial artefact); the scores are standardized to mean 0,
    variance 1 (the MM1 below needs Var Y₂ = 1; a continuous field loses nothing). `secondary_scores`.
 2. ρ — given (−1 … 1) or, with `rho=None`, computed (`colocated_rho`): the weighted Pearson correlation of the normal
    scores of the primary samples of D (the weights of declustering of the row, none — equal) and of the secondary at
    the node whose cell holds the sample (≥ 10 pairs; else 422 `rho_pairs`).
 3. The node with a secondary value s (the sill C₀ = C(0) of the model; the secondary is scaled to s' = s·√C₀ so that
    Var Y₂ = C₀ as Var Y₁):  MM1 — C₁₂(h) = ρ·C₁₁(h), the secondary is used at the node alone. The data are the
    n primary values y_i (the samples and the simulated nodes of the neighbourhood, SK with mean 0) and s'. The
    system of n + 1 rows:

        | C₁₁(x_i − x_j)    ρ·C₁₁(x_i − x₀) | | λ_j |   | C₁₁(x_i − x₀) |
        | ρ·C₁₁(x_j − x₀)   C₀              | | λ_s | = | ρ·C₀          |

    y* = Σ λ_i·y_i + λ_s·s'      σ² = C₀ − Σ λ_i·C₁₁(x_i − x₀) − λ_s·ρ·C₀ (≥ 0)      y = y* + σ·draw
    (the row of the node is the covariance of Y₂(x₀) with the data: C₁₂(x_i − x₀) = ρ·C₁₁, of Y₂(x₀) with Y₁(x₀) =
    ρ·C₀). A node with no primary datum is n = 0: λ_s = ρ, σ² = C₀(1 − ρ²).
 4. |ρ| = 1: y = ρ·s' without the system (σ² = 0; the samples near the node are not read; a sample AT the node is the
    node, as always).  ρ = 0: the secondary does not enter — `simulate` is then `sgs.simulate` itself with the same seed
    (bit for bit; `zero_as_sgs=False` runs the n + 1 kernel, whose row of the secondary is all zeros: the same bits).
 5. A node with no secondary value (a gap) is a node of the SGS: the system of n rows, the count `C_GAP` (column 3 of
    `counts`); a node with neither a datum nor a secondary takes y = draw (`free`).
 6. The degenerate system (a pivot ≤ 1e−12·C₀; `degenerate`): the secondary alone, y = ρ·s' + √(C₀(1 − ρ²))·draw.
 7. `backtr` of the table of the primary samples with the tails of `par` (the primary only: the secondary is not
    back-transformed).

Threads, the cancel, the progress and the memory are as in `sgs.simulate`: the realization r is the stream (seed, r)
and its own buffers, so 1, 2, 4, 8 threads give the same bits (Т18б-С4).

    colocated_rho(samples, sy, centres, steps, secondary_y)  → (ρ, pairs)
    secondary_scores(values)                                  → f8 [n]
    warm_cosgs() -> None        the kernel on writable and read-only inputs
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

import numpy as np

from bf_core.geostat.estimate.cov import CovModel, cov_c, cov_model
from bf_core.geostat.estimate.samples import D, RowSamples
from bf_core.geostat.estimate.search import Searcher, _own
from bf_core.geostat.estimate.spec_parts import Pass
from bf_core.geostat.estimate.store_base import bad
from bf_core.geostat.simulation import nodes as nd
from bf_core.geostat.simulation import sgs
from bf_core.geostat.simulation.path import realization_path
from bf_core.geostat.simulation.sgs import (
    POLL,
    STEP,
    SgsParams,
    SgsResult,
    Tick,
    _back_transform,
    _Shared,
    _Stopped,
    _Work,
)
from bf_core.jit import njit

C_DEGENERATE, C_FREE, C_ON_SAMPLE, C_GAP = 0, 1, 2, 3  # the columns of the counts of a realization
COUNTS = 4
RHO_MIN_PAIRS = 10  # the pairs sample–node of the computed ρ
PIVOT_REL = 1e-12  # a pivot ≤ 1e−12·C(0) — the system is degenerate (as `krige`)


@dataclass
class CosgsResult(SgsResult):
    """`SgsResult` (its `counts` have 4 columns: + the nodes with no secondary) and what the secondary adds."""

    rho: float = 0.0  # the coefficient used
    rho_pairs: int = 0  # the pairs of the computed ρ (0 — ρ was given)
    secondary_y: np.ndarray | None = None  # float64 [n]: the secondary in normal scores, NaN — a gap
    n_gap: int = 0  # the nodes with no secondary value
    notes: list[str] = field(default_factory=list)


# --- the secondary and ρ ------------------------------------------------------------------------------------------


def secondary_scores(values: np.ndarray) -> np.ndarray:
    """The normal scores [n] of the secondary field (the module text, 1): NaN / ±∞ stay NaN; 422 `secondary` when
    there are fewer than 3 finite values or all of them are equal."""
    from scipy.stats import norm

    v = np.asarray(values, dtype=np.float64)
    out = np.full(v.shape, np.nan)
    fin = np.isfinite(v)
    x = v[fin]
    if x.size < 3 or float(x.max()) == float(x.min()):
        raise bad("secondary", "Вторичная переменная: нужно не меньше трёх узлов с разными значениями.",
                  "Выберите числовое поле модели, например плотность, заполненное в узлах цели.")  # fmt: skip
    uniq, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
    mid = (np.cumsum(cnt) - 0.5 * cnt) / x.size  # the middle of the rank of a group of equal values
    s = np.asarray(norm.ppf(mid), dtype=np.float64)[inv]
    s = (s - float(s.mean())) / float(s.std())
    out[fin] = s
    return out


def colocated_rho(samples: RowSamples, sy: np.ndarray, centres: np.ndarray, steps: Sequence[float],
                  secondary_y: np.ndarray) -> tuple[float, int]:  # fmt: skip
    """ρ of the normal scores of the primary (the samples of D) and of the secondary at the node whose cell contains the
    sample (|Δ| ≤ ½ step on every axis of the frame; a node without a secondary does not count). The weighted Pearson
    correlation; the weights of declustering of the row (NaN or ≤ 0 — 1). → (ρ, the pairs); 422 `rho_pairs`."""
    from scipy.spatial import cKDTree

    pairs = 0
    rho = math.nan
    if centres.shape[0] and samples.n:
        st = np.asarray(steps, dtype=np.float64)
        rows = np.flatnonzero(samples.dom == D)
        _, node = cKDTree(centres).query(samples.local[rows])
        inside = np.all(np.abs(samples.local[rows] - centres[node]) <= 0.5 * st * (1.0 + 1e-9), axis=1)
        sec = secondary_y[node]
        keep = inside & np.isfinite(sec) & np.isfinite(sy[rows])
        rows, node = rows[keep], node[keep]
        pairs = int(rows.size)
        if pairs >= RHO_MIN_PAIRS:
            x, s = sy[rows], secondary_y[node]
            w = np.ones(pairs)
            if samples.w is not None:
                wr = samples.w[rows]
                w = np.where(np.isfinite(wr) & (wr > 0.0), wr, 1.0)
            w = w / w.sum()
            x0, s0 = x - float(w @ x), s - float(w @ s)
            den = math.sqrt(float(w @ (x0 * x0)) * float(w @ (s0 * s0)))
            if den > 0.0:
                rho = float(w @ (x0 * s0)) / den
    if not math.isfinite(rho):
        raise bad("rho", f"Коэффициент ρ не вычислен: проб в узлах со вторичной переменной {pairs} "
                  f"(нужно не меньше {RHO_MIN_PAIRS}) или значения одинаковы.",
                  "Задайте ρ вручную (от −1 до 1) или проверьте поле вторичной переменной.")  # fmt: skip
    return max(-1.0, min(1.0, rho)), pairs


def check_rho(rho: object) -> float:
    """ρ of a request: a finite number −1 … 1 (a bool is not one); 422 `bad_param` {rho}."""
    if isinstance(rho, bool) or not isinstance(rho, int | float | np.integer | np.floating):
        raise bad("rho", "Коэффициент ρ: нужно число от −1 до 1.", "Задайте, например, 0,6 — или оставьте пустым.")
    r = float(rho)
    if not math.isfinite(r) or not -1.0 <= r <= 1.0:
        raise bad("rho", "Коэффициент ρ: нужно число от −1 до 1.", "Задайте, например, 0,6 — или оставьте пустым.")
    return r


# --- the kernel --------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _lu_solve(a: np.ndarray, sol: np.ndarray, m: int, tol: float) -> bool:
    """`sol[:m]` ← the solution of a·x = sol[:m] by the LU with partial pivoting (the first of equal pivots), the
    operations in the order of `krige`; False — a pivot ≤ tol (a is spoilt)."""
    for k in range(m):
        p = k
        best = abs(a[k, k])
        for i in range(k + 1, m):
            v = abs(a[i, k])
            if v > best:
                best = v
                p = i
        if not (best > tol):  # also false for NaN
            return False
        if p != k:
            for j in range(k, m):
                t = a[k, j]
                a[k, j] = a[p, j]
                a[p, j] = t
            t = sol[k]
            sol[k] = sol[p]
            sol[p] = t
        piv = a[k, k]
        for i in range(k + 1, m):
            f = a[i, k] / piv
            if f != 0.0:
                for j in range(k + 1, m):
                    a[i, j] -= f * a[k, j]
                sol[i] -= f * sol[k]
    for i in range(m - 1, -1, -1):
        s = sol[i]
        for j in range(i + 1, m):
            s -= a[i, j] * sol[j]
        sol[i] = s / a[i, i]
    return True


@njit(cache=True, nogil=True)
def cosgs_steps(kinds: np.ndarray, pars: np.ndarray, head: np.ndarray, index: np.ndarray, nx: int, ny: int, nz: int,
                template: np.ndarray, cell: np.ndarray, centres: np.ndarray, cidx: np.ndarray, cn: np.ndarray,
                sxyz: np.ndarray, sy: np.ndarray, sec: np.ndarray, rho: float, path: np.ndarray, draws: np.ndarray,
                s0: int, s1: int, n_nodes: int, tol2: float, simulated: np.ndarray, y: np.ndarray,
                found: np.ndarray, xyz: np.ndarray, zz: np.ndarray, a: np.ndarray, rhs: np.ndarray,
                sol: np.ndarray, count: np.ndarray) -> None:  # fmt: skip
    """The steps s0 … s1−1 of one realization (the module text): `y[node]`, `simulated[node]`, `count` += (degenerate,
    free, on a sample, no secondary). `sec` [n] — the secondary scaled by √C₀, NaN — a gap. The neighbourhood is that
    of `sgs.sgs_steps`; the buffers are the caller's (`a`, `rhs`, `sol` hold n + 1 rows)."""
    c0 = head[1]
    tol = PIVOT_REL * c0
    one = abs(rho) >= 1.0
    for s in range(s0, s1):
        node = path[s]
        cx, cy, cz = centres[node, 0], centres[node, 1], centres[node, 2]
        ns = int(cn[node])
        hit = -1
        for q in range(ns):  # a sample at the node is the node
            si = cidx[node, q]
            dx, dy, dz = sxyz[si, 0] - cx, sxyz[si, 1] - cy, sxyz[si, 2] - cz
            if dx * dx + dy * dy + dz * dz <= tol2:
                hit = si
                break
        if hit >= 0:
            y[node] = sy[hit]
            simulated[node] = True
            count[2] += 1
            continue
        s2 = sec[node]
        has = not math.isnan(s2)
        if has and one:  # |ρ| = 1: the secondary alone
            y[node] = rho * s2
            simulated[node] = True
            continue
        m = 0
        for q in range(ns):
            si = cidx[node, q]
            xyz[m, 0] = sxyz[si, 0]
            xyz[m, 1] = sxyz[si, 1]
            xyz[m, 2] = sxyz[si, 2]
            zz[m] = sy[si]
            m += 1
        nn = nd.find_nodes(index, nx, ny, nz, simulated, template, cell[node], n_nodes, found)
        for q in range(nn):
            other = found[q]
            ox, oy, oz = centres[other, 0], centres[other, 1], centres[other, 2]
            same = False
            for t in range(ns):  # the node that is a conditioning sample is that sample
                dx, dy, dz = xyz[t, 0] - ox, xyz[t, 1] - oy, xyz[t, 2] - oz
                if dx * dx + dy * dy + dz * dz <= tol2:
                    same = True
                    break
            if same:
                continue
            xyz[m, 0] = ox
            xyz[m, 1] = oy
            xyz[m, 2] = oz
            zz[m] = y[other]
            m += 1
        value = draws[s]
        if not has:
            count[3] += 1
        if m == 0 and not has:
            count[1] += 1
        else:
            mm = m + 1 if has else m  # the rows of the system; the last one is the secondary
            for i in range(m):
                r = cov_c(kinds, pars, head, xyz[i, 0] - cx, xyz[i, 1] - cy, xyz[i, 2] - cz)
                rhs[i] = r
                sol[i] = r
                for j in range(i + 1):
                    c = cov_c(kinds, pars, head, xyz[i, 0] - xyz[j, 0], xyz[i, 1] - xyz[j, 1], xyz[i, 2] - xyz[j, 2])
                    a[i, j] = c
                    a[j, i] = c
                if has:
                    a[i, m] = rho * r
                    a[m, i] = rho * r
            if has:
                a[m, m] = c0
                rhs[m] = rho * c0
                sol[m] = rho * c0
            if _lu_solve(a, sol, mm, tol):
                sc = 0.0  # Σ λ_i·C(x_i − x₀)
                est = 0.0
                for i in range(m):
                    sc += sol[i] * rhs[i]
                    est += sol[i] * zz[i]
                kv = c0 - sc
                if has:
                    est += sol[m] * s2
                    kv -= sol[m] * rhs[m]
                value = est + (math.sqrt(kv) if kv > 0.0 else 0.0) * draws[s]
            else:
                count[0] += 1
                if has:
                    value = rho * s2 + math.sqrt(c0 * (1.0 - rho * rho)) * draws[s]
        y[node] = value
        simulated[node] = True


# --- the realizations ---------------------------------------------------------------------------------------------


class _CoShared(_Shared):
    """What every realization reads: the SGS parts, the secondary (scaled by √C₀) and ρ."""

    def __init__(self, par: SgsParams, samples: RowSamples, grid: nd.NodeGrid, centres: np.ndarray, sy: np.ndarray,
                 cidx: np.ndarray, cn: np.ndarray, tpl: np.ndarray, cm: CovModel, sec: np.ndarray,
                 rho: float) -> None:  # fmt: skip
        super().__init__(par, samples, grid, centres, sy, cidx, cn, tpl, cm)
        self.sec = _own(sec * math.sqrt(float(cm.sill)), np.float64)
        self.rho = float(rho)


def _co_realization(sh: _CoShared, r: int, row: np.ndarray, count: np.ndarray, work: _Work, stop: threading.Event,
                    progress: Callable[[int], None]) -> None:  # fmt: skip
    """The normal scores of the realization r into `row` (float64 [n]) and its counts [4]."""
    n = sh.n
    path, rng = realization_path(sh.par.seed, sh.par.n_real, r, n)
    draws = rng.standard_normal(n)
    path = _own(path, np.int64)
    simulated = np.zeros(n, dtype=np.bool_)
    nx, ny, nz = sh.shape
    for s0 in range(0, n, STEP):
        if stop.is_set():
            raise _Stopped
        s1 = min(s0 + STEP, n)
        cosgs_steps(sh.kinds, sh.pars, sh.head, sh.index, nx, ny, nz, sh.template, sh.cell, sh.centres, sh.cidx, sh.cn,
                    sh.sxyz, sh.sy, sh.sec, sh.rho, path, draws, s0, s1, sh.par.nodes, sh.tol2, simulated, row,
                    work.found, work.xyz, work.zz, work.a, work.rhs, work.sol, count)  # fmt: skip
        progress(s1 - s0)


def simulate(par: SgsParams, samples: RowSamples, grid: nd.NodeGrid, centres: np.ndarray, secondary: np.ndarray,
             rho: float | None = None, *, threads: int = 1, tick: Tick | None = None, keep_y: bool = False,
             sampler: Searcher | None = None, zero_as_sgs: bool = True) -> CosgsResult:  # fmt: skip
    """The realizations of the co-SGS (the module text). `secondary` [n] — the field at the centres of the nodes (NaN —
    a gap); `rho` None — computed from the samples. 422 `bad_param` {param} (`rho`, the numbers of `par`), `secondary`,
    `rho_pairs`-type `rho` when it cannot be computed, `no_samples`; `tick` raises to cancel."""
    t0 = time.perf_counter()
    par = sgs.check_params(par)
    n = grid.n
    c = np.asarray(centres, dtype=np.float64)
    if c.shape != (n, 3) or not bool(np.isfinite(c).all()):
        raise bad("centres", "Центры узлов: по три конечных координаты на узел.", "Сообщите разработчику.")
    sec_in = np.asarray(secondary, dtype=np.float64)
    if sec_in.shape != (n,):
        raise bad("secondary", "Вторичная переменная: по одному значению на узел.", "Сообщите разработчику.")
    rho_given = None if rho is None else check_rho(rho)
    sy, tr = sgs.sample_scores(samples, par)
    if n == 0:
        real = np.empty((par.n_real, 0), dtype=np.float64)
        return CosgsResult(real, real.copy() if keep_y else None, tr, np.zeros((par.n_real, COUNTS), np.int64),
                           np.zeros(0, np.int16), time.perf_counter() - t0, [], rho_given or 0.0)  # fmt: skip
    sec_y = secondary_scores(sec_in)
    n_gap = int(np.count_nonzero(np.isnan(sec_y)))
    pairs = 0
    if rho_given is None:
        used, pairs = colocated_rho(samples, sy, c, par.steps, sec_y)
    else:
        used = rho_given
    how = f"из {pairs} пар проба–узел" if rho_given is None else "задан"
    notes = [f"Вторичная переменная: значений {n - n_gap} из {n}; ρ = {used:.4f} ({how})."]
    if used == 0.0 and zero_as_sgs:
        base = sgs.simulate(par, samples, grid, c, threads=threads, tick=tick, keep_y=keep_y, sampler=sampler)
        counts = np.zeros((par.n_real, COUNTS), dtype=np.int64)
        counts[:, : sgs.COUNTS] = base.counts
        return CosgsResult(base.real, base.y, base.transform, counts, base.cond, time.perf_counter() - t0,
                           notes + base.notes, used, pairs, sec_y, n_gap)  # fmt: skip
    n_real = par.n_real
    real = np.empty((n_real, n), dtype=np.float64)
    counts = np.zeros((n_real, COUNTS), dtype=np.int64)
    cm = cov_model(par.model)
    sr = sampler or Searcher(samples, par.ell, (Pass(1.0, 0, par.n_max, 0, 0, 0, 0),))
    cidx, cn = nd.sample_neighbours(sr, c, tick=tick)
    tpl = nd.spiral_template(par.steps, par.ell, cm, limit=par.template_limit, shape=grid.shape)
    sh = _CoShared(par, samples, grid, c, sy, cidx, cn, tpl, cm, sec_y, used)
    ys = np.empty_like(real) if keep_y else real
    total = n * n_real
    done = [0] * n_real  # nodes of every realization (each thread writes its own item)
    stop = threading.Event()
    label = f"Реализации {{}} / {n_real}"
    finished = 0
    workers = max(1, min(int(threads), n_real))
    inline = workers == 1  # the calling thread runs the realizations: it ticks inside them (no pool to poll)
    due = [time.monotonic() + POLL]

    def run_one(r: int, work: _Work) -> int:
        def progress(k: int) -> None:
            done[r] += k
            if inline and tick is not None and time.monotonic() >= due[0]:  # one thread: the tick goes from here
                due[0] = time.monotonic() + POLL
                tick(sum(done) / total, label.format(finished))

        _co_realization(sh, r, ys[r], counts[r], work, stop, progress)
        _back_transform(par, tr, ys[r], real[r])
        return r

    if workers == 1:
        work = _Work(sh.cap, par.nodes)
        for r in range(n_real):
            run_one(r, work)
            finished += 1
            if tick is not None:
                tick(sum(done) / total, label.format(finished))
    else:
        local = threading.local()

        def task(r: int) -> int:
            w = getattr(local, "work", None)
            if w is None:
                w = local.work = _Work(sh.cap, par.nodes)
            return run_one(r, w)

        pool = ThreadPoolExecutor(workers, thread_name_prefix="bf-cosgs")
        pending: set[Future[int]] = {pool.submit(task, r) for r in range(n_real)}
        try:
            while pending:
                ready, pending = wait(pending, timeout=POLL, return_when=FIRST_COMPLETED)
                for f in ready:
                    f.result()
                    finished += 1
                if tick is not None:
                    tick(sum(done) / total, label.format(finished))
        except BaseException:
            stop.set()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
    if int(counts[:, C_DEGENERATE].sum()):
        bads = int(counts[:, C_DEGENERATE].sum())
        notes.append(f"Вырожденных систем: {bads} (узлы взяты по вторичной переменной).")
    if int(counts[:, C_GAP].sum()):
        notes.append(f"Узлов без вторичной переменной: {n_gap} (моделируются как SGS, без неё).")
    return CosgsResult(real, ys if keep_y else None, tr, counts, cn, time.perf_counter() - t0, notes, used, pairs,
                       sec_y, n_gap)  # fmt: skip


# --- the warm-up -----------------------------------------------------------------------------------------------


def warm_cosgs() -> None:
    """Compiles `cosgs_steps` and runs `simulate` on writable and on read-only inputs (one signature)."""
    from bf_core.geostat.estimate.samples import prepare
    from bf_core.geostat.estimate.search import Ellipsoid
    from bf_core.geostat.variogram.model import Model, Structure

    nd.warm_nodes()
    rng = np.random.default_rng(3)
    n = 40
    xyz = rng.uniform(0.0, 8.0, (n, 3))
    s = prepare(xyz, rng.uniform(1.0, 5.0, n), None, None, np.zeros(n, np.int32), ["h"], np.zeros(n, np.int16),
                np.arange(n, dtype=np.int64), np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip
    grid = nd.NodeGrid.of((4, 4, 2), np.arange(32, dtype=np.uint64))
    model = Model(0.1, (Structure("spherical", 0.9, 5.0, 4.0, 3.0),))
    par = SgsParams(model, Ellipsoid.of((0.0, 0.0, 0.0), (5.0, 4.0, 3.0)), (2.0, 2.0, 2.0), 1, 2, 6, 4)
    centres = sgs.grid_centres(grid, par.steps)
    sec = rng.uniform(1.0, 4.0, grid.n)
    sec[3] = np.nan
    for read_only in (False, True):
        if read_only:
            for a in (centres, grid.index, grid.cell, s.local, s.z, sec):
                a.setflags(write=False)
        simulate(par, s, grid, centres, sec, 0.5, zero_as_sgs=False)
        simulate(par, s, grid, centres, sec, 1.0)
