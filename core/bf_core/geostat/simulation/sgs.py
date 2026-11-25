"""Sequential Gaussian simulation, SGS (ТЗ 8.8.1; ADR 0024 §9; Д-23, Д-38; Э18б, 18.31).

    res = simulate(par, samples, grid, centres, threads=4, tick=tick)       → SgsResult
    res.real  f8 [n_real, n]    the realizations in the grains (Z), the nodes in the order of the model (`grid`)

The way of GSLIB `sgsim`, in normal scores, for every realization r:

 1. the normal scores of the samples (`stats.transforms.nscore`, with the weights of declustering of the row: a weight
    NaN or ≤ 0 is left out of the table, the score of the sample is read from it);
 2. the path over the nodes `path.realization_path(seed, n_real, r, n)`, the stream goes on: `draws[s]` — the s-th
    N(0, 1) of the stream after the permutation is the draw of the s-th node of the path (it is drawn for every step,
    used or not, so the stream does not depend on what the nodes find);
 3. the node: the conditioning samples (≤ `n_max`, found once for all realizations, `nodes.sample_neighbours`) and the
    nodes simulated before it (≤ `nodes`, the spiral template, `nodes.find_nodes`) → the simple kriging with the mean 0
    by the model in Y (Д-38: the model is read in normal scores directly, no back-transform of the model) →
    μ = Σ λ·y, σ² = C(0) − Σ λ·C̄ (≥ 0) → y = μ + σ·draw;
 4. `backtr` (the table of 1 with the tails of the parameters) turns the realization into Z.

The point at the node. A sample that lies at a node (the distance ≤ `samples.TOL`, 1e−6 — the tolerance with which the
coincident samples are one) is the node: y = the score of the sample, bit for bit (no kriging; GSLIB moves a sample to
its node). A node coincident with a conditioning sample is not used as a datum (the same point twice makes the system
singular).
The case of a degenerate system or of no datum. A node with no sample and no simulated node in the neighbourhood (the
start of the path, or a node farther than the ellipsoid) takes y = draw, unconditionally, N(0, 1) (count `free`);
a system whose pivot ≤ 1e−12·C(0) (KR_SINGULAR) is the same: y = draw (count `degenerate`). A gap is never a zero: a
node always gets a finite value or the whole run is refused (422).

The frame. `centres` [n, 3] are the centres of the nodes in the frame of `samples.local` (8.0.1: the project minus the
origin), the grid axes are the axes of that frame: `par.steps` = (dx, dy, dz) of a cell, `par.ell` and `par.model` are
given in that frame too (a rotated block model is a matter of the caller: the samples, the angles of the model and the
ellipsoid are brought to the grid axes first). `grid_centres(grid, steps, origin)` makes the centres of an unrotated
model.

Threads: the realizations go to the pool, each from its own stream and its own work buffers (the numba kernel lets the
GIL go), so the result is the same bit for bit with 1, 2, 4, 8 threads and in any order of the realizations (Т8.8-13).
`tick(progress, label)` is called from the calling thread (≈ every 0.2 s; label «Реализации k / N»); it raises to cancel
— the running portions stop at the next portion of `STEP` nodes.

Memory: `real` n_real·n·8 bytes (the back-transform is in place), `keep_y` doubles it; a thread — the path, the draws
and the flags of one realization (≈ 17·n bytes); the samples of the nodes (n·n_max·4 bytes) are found once.

    warm_sgs() -> None        the kernel on writable and read-only inputs (one signature, lesson 17.39б)
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate.cov import CovModel, cov_model
from bf_core.geostat.estimate.krige import O_EST, O_KV, KrigeWork, krige
from bf_core.geostat.estimate.samples import TOL, D, RowSamples
from bf_core.geostat.estimate.search import Ellipsoid, Searcher, _own
from bf_core.geostat.estimate.spec_parts import Pass
from bf_core.geostat.estimate.store_base import bad
from bf_core.geostat.simulation import nodes as nd
from bf_core.geostat.simulation.path import check_n_real, check_seed, realization_path
from bf_core.geostat.variogram.model import Model
from bf_core.jit import njit
from bf_core.stats.transforms import NormalScoreTransform, backtr, check_tails, nscore
from bf_core.stats.weighted import StatsError

Tick = Callable[[float | None, str | None], None]

N_MAX_LIMIT = 128  # Д-23: samples 1–128 (24 by default)
STEP = 1 << 15  # nodes of the path per call of the kernel: the cancel and the progress go between them
POLL = 0.2  # s, the tick of the calling thread while the pool works

C_DEGENERATE, C_FREE, C_ON_SAMPLE = 0, 1, 2  # the columns of the counts of a realization
COUNTS = 3


# --- the parameters ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SgsParams:
    """What `simulate` needs of the simulation (`S/spec.py` of 18.32 makes it from the body)."""

    model: Model  # the variogram in normal scores (Д-38 `y`), the local frame
    ell: Ellipsoid  # the search ellipsoid, the local frame
    steps: tuple[float, float, float]  # the cell (dx, dy, dz) of the grid of the nodes
    seed: int = 12345
    n_real: int = 20
    n_max: int = 24  # conditioning samples 1–128
    nodes: int = 12  # simulated nodes 0–64
    ltail: str | None = "linear"  # the tails of `backtr` (as `stats.transforms.backtr`)
    utail: str | None = "linear"
    ltpar: float | None = None
    utpar: float | None = None
    zmin: float | None = None
    zmax: float | None = None
    template_limit: int = nd.TEMPLATE_MAX


@dataclass
class SgsResult:
    real: np.ndarray  # float64 [n_real, n]: Z, the nodes in the order of the grid
    y: np.ndarray | None  # float64 [n_real, n]: the normal scores before the back-transform (keep_y)
    transform: NormalScoreTransform  # the table of the samples
    counts: np.ndarray  # int64 [n_real, 3]: degenerate systems, nodes with no datum, nodes of a sample
    cond: np.ndarray  # int16 [n]: conditioning samples of every node
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)  # lines for the journal of the job


def check_params(par: SgsParams) -> SgsParams:
    """The numbers of the simulation checked (422 `bad_param` {param}); the tails are checked as `backtr` does."""
    check_seed(par.seed)
    check_n_real(par.n_real)
    nd.check_nodes(par.nodes)
    if isinstance(par.n_max, bool) or not isinstance(par.n_max, int | np.integer) or not 1 <= par.n_max <= N_MAX_LIMIT:
        raise bad("n_max", f"Число проб: нужно целое число от 1 до {N_MAX_LIMIT}.", "Задайте, например, 24.")
    try:
        check_tails(par.ltail, par.utail, par.ltpar, par.utpar)
    except StatsError as exc:
        raise bad(exc.param or "tails", exc.message, exc.hint) from exc
    return par


# --- the nodes ----------------------------------------------------------------------------------------------------


def grid_centres(grid: nd.NodeGrid, steps: Sequence[float], origin: Sequence[float] = (0.0, 0.0, 0.0)) -> np.ndarray:
    """The centres [n, 3] of the nodes of an unrotated regular model: origin + (i + ½, j + ½, k + ½)·steps."""
    i, j, k = grid.ijk()
    st = np.asarray(steps, dtype=np.float64)
    o = np.asarray(origin, dtype=np.float64)
    out = np.empty((grid.n, 3), dtype=np.float64)
    out[:, 0] = o[0] + (i + 0.5) * st[0]
    out[:, 1] = o[1] + (j + 0.5) * st[1]
    out[:, 2] = o[2] + (k + 0.5) * st[2]
    return out


def sample_scores(samples: RowSamples, par: SgsParams) -> tuple[np.ndarray, NormalScoreTransform]:
    """The normal scores of the samples [n] and the table: the table is built from the samples of D with the weights of
    declustering (NaN or ≤ 0 — no weight, the score is read from the table); the other domains are read from it."""
    in_d = samples.dom == D
    if not bool(in_d.any()) or not bool(np.isfinite(samples.z[in_d]).all()):
        raise ApiError(422, "no_samples", "Нет проб для моделирования: у проб нет значения или домена.",
                       "Проверьте поле проб, домен и отбор.", {"param": "samples"})  # fmt: skip
    w = None
    if samples.w is not None:
        wd = samples.w[in_d]
        w = np.where(np.isfinite(wd) & (wd > 0.0), wd, 0.0)
        w = w if float(w.sum()) > 0.0 else None
    try:
        sc, tr = nscore(samples.z[in_d], w, par.zmin, par.zmax)
    except StatsError as exc:
        raise bad(exc.param or "samples", exc.message, exc.hint) from exc
    y = np.asarray(tr.forward(samples.z), dtype=np.float64)
    y[in_d] = sc
    return y, tr


# --- the kernel --------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def sgs_steps(kinds: np.ndarray, pars: np.ndarray, head: np.ndarray, index: np.ndarray, nx: int, ny: int, nz: int,
              template: np.ndarray, cell: np.ndarray, centres: np.ndarray, cidx: np.ndarray, cn: np.ndarray,
              sxyz: np.ndarray, sy: np.ndarray, path: np.ndarray, draws: np.ndarray, s0: int, s1: int,
              n_nodes: int, tol2: float, ok: int, mean: float, simulated: np.ndarray, y: np.ndarray,
              found: np.ndarray, xyz: np.ndarray, zz: np.ndarray, centre: np.ndarray, offs: np.ndarray,
              a: np.ndarray, rhs: np.ndarray, sol: np.ndarray, kout: np.ndarray,
              count: np.ndarray) -> None:  # fmt: skip
    """The steps s0 … s1−1 of one realization (the module text): `y[node]` and `simulated[node]` of the nodes of the
    path, `count` += (degenerate, free, on a sample). Every buffer is the caller's; `ok` = 0 and `mean` = 0.0 (the SK)
    are arguments so that they are variables of one type for `krige` (not literals)."""
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
        if m == 0:
            count[1] += 1
        else:
            centre[0], centre[1], centre[2] = cx, cy, cz
            st = krige(kinds, pars, head, xyz, m, zz, centre, offs, head[1], mean, ok, a, rhs, sol, kout)
            if st == 0:
                kv = kout[O_KV]
                sigma = math.sqrt(kv) if kv > 0.0 else 0.0
                value = kout[O_EST] + sigma * draws[s]
            else:
                count[0] += 1
        y[node] = value
        simulated[node] = True


# --- the realizations ---------------------------------------------------------------------------------------------


class _Stopped(Exception):
    """The pool is stopped (a cancel of the caller or an error of another realization)."""


class _Shared:
    """What every realization reads (the arrays are the kernel's own writable copies): made once."""

    def __init__(self, par: SgsParams, samples: RowSamples, grid: nd.NodeGrid, centres: np.ndarray, sy: np.ndarray,
                 cidx: np.ndarray, cn: np.ndarray, tpl: np.ndarray, cm: CovModel) -> None:  # fmt: skip
        self.par = par
        self.n = grid.n
        self.kinds, self.pars, self.head = (_own(v, v.dtype) for v in cm.args)
        self.index = _own(grid.index, np.int32)
        self.shape = grid.shape
        self.template = _own(tpl, np.int32)
        self.cell = _own(grid.cell, np.int64)
        self.centres = _own(centres, np.float64)
        self.cidx = _own(cidx, np.int32)
        self.cn = _own(cn, np.int16)
        self.sxyz = _own(samples.local, np.float64)
        self.sy = _own(sy, np.float64)
        self.cap = par.n_max + par.nodes
        self.tol2 = TOL * TOL


class _Work:
    """The buffers of a thread (they need no clearing between realizations)."""

    def __init__(self, cap: int, n_nodes: int) -> None:
        self.found = np.empty(max(n_nodes, 1), dtype=np.int64)
        self.xyz = np.empty((cap, 3), dtype=np.float64)
        self.zz = np.empty(cap, dtype=np.float64)
        self.centre = np.empty(3, dtype=np.float64)
        self.offs = np.zeros((1, 3), dtype=np.float64)
        kw = KrigeWork(cap + 1)
        self.a, self.rhs, self.sol, self.kout = kw.a, kw.rhs, kw.sol, kw.out


def _realization(sh: _Shared, r: int, row: np.ndarray, count: np.ndarray, work: _Work, stop: threading.Event,
                 progress: Callable[[int], None]) -> None:  # fmt: skip
    """The normal scores of the realization r into `row` (float64 [n]) and its counts."""
    n = sh.n
    path, rng = realization_path(sh.par.seed, sh.par.n_real, r, n)
    draws = rng.standard_normal(n)
    path = _own(path, np.int64)
    simulated = np.zeros(n, dtype=np.bool_)
    nx, ny, nz = sh.shape
    ok, mean = 0, 0.0
    for s0 in range(0, n, STEP):
        if stop.is_set():
            raise _Stopped
        s1 = min(s0 + STEP, n)
        sgs_steps(sh.kinds, sh.pars, sh.head, sh.index, nx, ny, nz, sh.template, sh.cell, sh.centres, sh.cidx, sh.cn,
                  sh.sxyz, sh.sy, path, draws, s0, s1, sh.par.nodes, sh.tol2, ok, mean, simulated, row, work.found,
                  work.xyz, work.zz, work.centre, work.offs, work.a, work.rhs, work.sol, work.kout, count)  # fmt: skip
        progress(s1 - s0)


def simulate(par: SgsParams, samples: RowSamples, grid: nd.NodeGrid, centres: np.ndarray, *, threads: int = 1,
             tick: Tick | None = None, keep_y: bool = False,
             sampler: Searcher | None = None) -> SgsResult:  # fmt: skip
    """The realizations of the SGS (the module text). `centres` [n, 3] — the centres of the nodes of `grid` in the frame
    of `samples.local`. `sampler` — a ready `Searcher` of the samples (one pass, `n_max`) to share its tree; by default
    it is made. 422 `bad_param` {param} for a number, `no_samples`; `tick` raises to cancel."""
    t0 = time.perf_counter()
    par = check_params(par)
    c = np.asarray(centres, dtype=np.float64)
    if c.shape != (grid.n, 3) or not bool(np.isfinite(c).all()):
        raise bad("centres", "Центры узлов: по три конечных координаты на узел.", "Сообщите разработчику.")
    sy, tr = sample_scores(samples, par)
    notes = [f"Нормальные оценки: проб {samples.n}, таблица из {int(tr.values.size)} значений."]
    n, n_real = grid.n, par.n_real
    real = np.empty((n_real, n), dtype=np.float64)
    counts = np.zeros((n_real, COUNTS), dtype=np.int64)
    if n == 0:
        return SgsResult(real, real.copy() if keep_y else None, tr, counts, np.zeros(0, np.int16),
                         time.perf_counter() - t0, notes)  # fmt: skip
    cm = cov_model(par.model)
    sr = sampler or Searcher(samples, par.ell, (Pass(1.0, 0, par.n_max, 0, 0, 0, 0),))
    cidx, cn = nd.sample_neighbours(sr, c, tick=tick)
    tpl = nd.spiral_template(par.steps, par.ell, cm, limit=par.template_limit, shape=grid.shape)
    sh = _Shared(par, samples, grid, c, sy, cidx, cn, tpl, cm)
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

        _realization(sh, r, ys[r], counts[r], work, stop, progress)
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

        pool = ThreadPoolExecutor(workers, thread_name_prefix="bf-sgs")
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
        notes.append(f"Вырожденных систем: {int(counts[:, C_DEGENERATE].sum())} (узлы взяты из N(0, 1) без условия).")
    return SgsResult(real, ys if keep_y else None, tr, counts, cn, time.perf_counter() - t0, notes)


def _back_transform(par: SgsParams, tr: NormalScoreTransform, y: np.ndarray, out: np.ndarray) -> None:
    """The scores `y` of a realization → Z into `out` (the same array for the in-place run); a non-finite score is a
    bug of the run, never a value."""
    if not bool(np.isfinite(y).all()):
        raise ApiError(500, "sim_nan", "Моделирование дало нечисловое значение.", "Сообщите разработчику.")
    out[:] = backtr(y, tr, par.ltail, par.utail, par.ltpar, par.utpar)


# --- the warm-up -----------------------------------------------------------------------------------------------


def warm_sgs() -> None:
    """Compiles `sgs_steps` and runs `simulate` on writable and on read-only inputs (one signature)."""
    from bf_core.geostat.estimate.samples import prepare
    from bf_core.geostat.variogram.model import Structure

    nd.warm_nodes()
    rng = np.random.default_rng(3)
    n = 40
    xyz = rng.uniform(0.0, 8.0, (n, 3))
    s = prepare(xyz, rng.uniform(1.0, 5.0, n), None, None, np.zeros(n, np.int32), ["h"], np.zeros(n, np.int16),
                np.arange(n, dtype=np.int64), np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip
    grid = nd.NodeGrid.of((4, 4, 2), np.arange(32, dtype=np.uint64))
    model = Model(0.1, (Structure("spherical", 0.9, 5.0, 4.0, 3.0),))
    par = SgsParams(model, Ellipsoid.of((0.0, 0.0, 0.0), (5.0, 4.0, 3.0)), (2.0, 2.0, 2.0), 1, 2, 6, 4)
    centres = grid_centres(grid, par.steps)
    for read_only in (False, True):
        if read_only:
            for a in (centres, grid.index, grid.cell, s.local, s.z):
                a.setflags(write=False)
        simulate(par, s, grid, centres)
