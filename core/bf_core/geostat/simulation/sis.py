"""Sequential indicator simulation, SIS (ТЗ 8.8.1; ADR 0024 §9; Д-23; Э18б, 18.33): a continuous
variable by K thresholds, or K categories.

    par = SisParams(models, ell, steps, thresholds=[t_1 … t_K])         continuous: f8 [n_real, n]
    par = SisParams(models, ell, steps, codes=[c_1 … c_K])              categories: u1 [n_real, n] (the codes)
    res = simulate(par, samples, grid, centres, cat=codes_of_samples, threads=4, tick=tick)       → SisResult

The way of GSLIB `sisim`, for every realization r (the path, the stream, the template of the nodes, the samples found
once, the point at the node, the pool of threads — all as the SGS of 18.31, `sgs.py`; the stream is the one of
`path.realization_path`, the draws after the permutation are uniform: `draws[s] = U[0, 1)` of the step s):

 1. the class of a sample. Continuous: c = the number of thresholds below z (z ≤ t_1 → 0, t_(k−1) < z ≤ t_k → k,
    z > t_K → K); the indicator of the threshold k is [c ≤ k] (= [z ≤ t_k]). Categories: c = the place of the code of
    the sample among `codes`, the indicator of the category k is [c = k]; a sample with another code (or a gap in z,
    a NaN) is no
    datum — never a 0 or a class (a sample of D with a gap in z is refused: 422 `no_samples`).
 2. the means of the SK: the declustered proportions of the samples of D (`indicator.proportions`; the weights of the
    row: NaN or ≤ 0 is no weight, no weight at all — equal ones): p_k = P(z ≤ t_k), or the share of the category k.
 3. the node: the conditioning samples (≤ `n_max`) and the nodes simulated before it (≤ `nodes`, the spiral template)
    → for every k the SK of the indicator with the mean p_k: F_k* = p_k + Σ λ·(i_k(x) − p_k). The models: K of them (the
    indicator model of every threshold or category, sill p_k(1 − p_k); one system for every k) or ONE (the median
    IK: one system and one set of weights λ for all the k, F_k* = `apply_weights`). The class of a node simulated
    before is its drawn class (not its value), so the indicator of a node is exactly [c ≤ k] / [c = k].
 4. the correction. Continuous: `indicator.order_relations` (GSLIB: clip to [0; 1], the mean of the upward and the
    downward corrections) → F_1 ≤ … ≤ F_K in [0; 1]. Categories: clip to [0; 1], then divide by the sum
    (Σ p_k = 1 to 1e−15; a sum 0 — the proportions p of the samples).
 5. the draw u = draws[s]. Categories: the first k whose cumulative p_1 + … + p_k exceeds u. Continuous: the class is
    the first k (from 0) with u ≤ F_(k+1), K when there is none; the value is read from the conditional distribution:
      · inside a class (t_(k−1), t_k]: linear, t_(k−1) + (t_k − t_(k−1))·(u − F_(k−1)) / (F_k − F_(k−1))
        (the middle option 1 of GSLIB; an empty class — the middle);
      · below t_1: between the limit `zmin` and t_1 by the share u / F_1 — as the lower tail of `backtr`:
        zmin + (t_1 − zmin)·share^(1 / ltpar), "linear" — power 1, "power" — ltpar, None — the value t_1;
      · above t_K: between t_K and `zmax` by the share (u − F_K) / (1 − F_K): t_K + (zmax − t_K)·share^(1 / utpar),
        "hyperbolic" — (t_K^ω·(1 − F_K) / (1 − u))^(1 / ω), ω = utpar ≥ 1, no limit; None — the value t_K.
    `zmin`, `zmax` — the limits of the tails: by default the least and the greatest value of the samples of D, moved to
    the nearest threshold if the data do not reach it (zmin ≤ t_1 ≤ t_K ≤ zmax; a given one beyond is refused).

Special cases. A threshold (a category) with p_k ≤ 0 or ≥ 1 is not kriged: F_k = p_k everywhere (an indicator that
is constant in the data). A node with no datum in the neighbourhood draws from the marginal distribution F = p (count
`free`); a degenerate system (a pivot ≤ 1e−12·C(0), the same as `krige`) does so for the threshold concerned (count
`degenerate`, once a node). A node is never left empty. A sample at a node (distance ≤ `samples.TOL`) is the node: the
class and (continuous) the value z of the sample bit for bit, no draw is used.

Threads: as the SGS — the same bits with 1, 2, 4, 8 threads and any order of the realizations (Т18б-С4). Memory: the
result n_real·n·(8 | 1) bytes, a thread — the path, the draws, the flags and the classes of one realization (≈ 20·n
bytes); `keep_p` adds n_real·n·K·8 (the probabilities: for the tests and the inspector, small models). The time of a
node is K systems of (n_max + nodes) with per-threshold models, one with the median one.

    warm_sis() -> None        the kernel on writable and read-only inputs (one signature, lesson 17.39б)
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
from bf_core.geostat.estimate.cov import PARS, CovModel, cov_model
from bf_core.geostat.estimate.indicator import K_MAX, proportions
from bf_core.geostat.estimate.krige import KrigeWork
from bf_core.geostat.estimate.samples import TOL, D, RowSamples, declus_mean
from bf_core.geostat.estimate.search import Ellipsoid, Searcher, _own
from bf_core.geostat.estimate.spec_parts import Pass
from bf_core.geostat.estimate.store_base import bad
from bf_core.geostat.simulation import nodes as nd
from bf_core.geostat.simulation.path import check_n_real, check_seed, realization_path
from bf_core.geostat.simulation.sgs import (
    C_DEGENERATE,
    C_FREE,
    C_ON_SAMPLE,
    COUNTS,
    N_MAX_LIMIT,
    POLL,
    STEP,
    Tick,
)
from bf_core.geostat.simulation.sis_kernel import sis_steps
from bf_core.geostat.variogram.model import Model
from bf_core.stats.transforms import check_tails
from bf_core.stats.weighted import StatsError

__all__ = ["C_DEGENERATE", "C_FREE", "C_ON_SAMPLE", "SisParams", "SisResult", "simulate", "warm_sis"]

CODE_MAX = 255  # the code of a category is u1
CAT_MIN = 2
LOWER, UPPER = {None: 0, "linear": 1, "power": 2}, {None: 0, "linear": 1, "power": 2, "hyperbolic": 3}


# --- the parameters ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SisParams:
    """What `simulate` needs of the simulation. Exactly one of `thresholds` (continuous) and `codes` (categories)."""

    models: tuple[Model, ...]  # the indicator models: K (the order of the thresholds / codes) or 1 (the median one)
    ell: Ellipsoid  # the search ellipsoid, the local frame
    steps: tuple[float, float, float]  # the cell (dx, dy, dz) of the grid of the nodes
    thresholds: tuple[float, ...] | None = None  # t_1 < … < t_K, 1–20
    codes: tuple[int, ...] | None = None  # the codes of the categories, 2–20 of 0–255 (the result is u1 of them)
    seed: int = 12345
    n_real: int = 20
    n_max: int = 24  # conditioning samples 1–128
    nodes: int = 12  # simulated nodes 0–64
    ltail: str | None = "linear"  # the tails of the values, as `stats.transforms.backtr` (continuous)
    utail: str | None = "linear"
    ltpar: float | None = None
    utpar: float | None = None
    zmin: float | None = None  # the limits of the tails (None — of the data)
    zmax: float | None = None
    template_limit: int = nd.TEMPLATE_MAX

    @property
    def k(self) -> int:
        return len(self.thresholds if self.thresholds is not None else self.codes or ())

    @property
    def categorical(self) -> bool:
        return self.codes is not None


@dataclass
class SisResult:
    real: np.ndarray  # f8 [n_real, n] (the values) or u1 [n_real, n] (the codes), the nodes in the order of the grid
    p: np.ndarray | None  # f8 [n_real, n, K]: F (corrected) / p of every node (keep_p); a sample — its indicators
    prop: np.ndarray  # f8 [K]: the declustered proportions of the samples (the means of the SK)
    counts: np.ndarray  # int64 [n_real, 3]: degenerate systems, nodes with no datum, nodes of a sample
    cond: np.ndarray  # int16 [n]: conditioning samples of every node
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)  # lines for the journal of the job


def _num(v: object) -> bool:
    return isinstance(v, int | float | np.integer | np.floating) and not isinstance(v, bool) and math.isfinite(v)


def check_params(par: SisParams) -> SisParams:
    """The numbers of the simulation checked (422 `bad_param` {param}); the tails are checked as `backtr` does."""
    check_seed(par.seed)
    check_n_real(par.n_real)
    nd.check_nodes(par.nodes)
    if isinstance(par.n_max, bool) or not isinstance(par.n_max, int | np.integer) or not 1 <= par.n_max <= N_MAX_LIMIT:
        raise bad("n_max", f"Число проб: нужно целое число от 1 до {N_MAX_LIMIT}.", "Задайте, например, 24.")
    if (par.thresholds is None) == (par.codes is None):
        raise bad("thresholds", "Задайте либо пороги (непрерывная величина), либо категории.",
                  "Нужен ровно один из двух списков.")  # fmt: skip
    k = par.k
    if par.codes is not None:
        cs = list(par.codes)
        if not CAT_MIN <= k <= K_MAX or any(not isinstance(c, int | np.integer) or isinstance(c, bool) for c in cs):
            raise bad("codes", f"Категорий: от {CAT_MIN} до {K_MAX}, коды — целые числа.", "Выберите 2–20 подписей.")
        if len(set(int(c) for c in cs)) != k or min(cs) < 0 or max(cs) > CODE_MAX:
            raise bad("codes", f"Коды категорий: различные целые числа от 0 до {CODE_MAX}.", "Проверьте подписи.")
    else:
        t = list(par.thresholds or ())
        if not 1 <= k <= K_MAX or not all(_num(v) for v in t) or any(b <= a for a, b in zip(t, t[1:], strict=False)):
            raise bad("thresholds", f"Пороги: от 1 до {K_MAX} конечных чисел по возрастанию.",
                      "Задайте пороги, например, по квантилям.")  # fmt: skip
        try:
            check_tails(par.ltail, par.utail, par.ltpar, par.utpar)
        except StatsError as exc:
            raise bad(exc.param or "tails", exc.message, exc.hint) from exc
        lo_ok = par.zmin is None or (_num(par.zmin) and par.zmin <= t[0])
        hi_ok = par.zmax is None or (_num(par.zmax) and par.zmax >= t[-1])
        for name, ok, word in (("zmin", lo_ok, "не больше первого"), ("zmax", hi_ok, "не меньше последнего")):
            if not ok:
                raise bad(
                    name,
                    f"Предел хвоста «{name}»: число {word} порога.",
                    "Оставьте поле пустым — пределом станет крайнее значение данных.",
                )
        if par.utail == "hyperbolic" and not t[-1] > 0.0:
            raise bad("utail", "Гиперболический хвост не определён: последний порог не больше нуля.",
                      "Выберите линейный или степенной верхний хвост.")  # fmt: skip
    if len(par.models) not in (1, k) or not all(isinstance(m, Model) and m.sill > 0.0 for m in par.models):
        raise bad("models", f"Моделей: одна (медианная) или {k} (по порогам / категориям), с положительным порогом.",
                  "Проверьте индикаторные вариограммы.")  # fmt: skip
    return par


# --- the samples -------------------------------------------------------------------------------------------------


def _fail_samples() -> ApiError:
    return ApiError(422, "no_samples", "Нет проб для моделирования: у проб нет значения, категории или домена.",
                    "Проверьте поле проб, домен и отбор.", {"param": "samples"})  # fmt: skip


def sample_classes(par: SisParams, samples: RowSamples,
                   cat: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, list[str]]:  # fmt: skip
    """(class int16 [ns] — −1: no datum; proportions f8 [K], notes). The proportions are the declustered ones of the
    samples of D (the weights of the row), the classes are those of the module text."""
    in_d = samples.dom == D
    if not bool(in_d.any()):
        raise _fail_samples()
    k = par.k
    w = None if samples.w is None else samples.w[in_d]
    notes: list[str] = []
    if par.codes is not None:
        if cat is None or np.asarray(cat).shape != (samples.n,):
            raise bad("cat", "Категории проб: по одному коду на пробу.", "Выберите поле категорий проб.")
        cc = np.asarray(cat).astype(np.int64)
        lut = {int(c): i for i, c in enumerate(par.codes)}
        cls = np.array([lut.get(int(c), -1) for c in cc], dtype=np.int16)
        valid = cls[in_d] >= 0
        if not bool(valid.any()):
            raise _fail_samples()
        ind = (cls[in_d][valid, None] == np.arange(k)[None, :]).astype(np.float64)
        wv = None if w is None else w[valid]
        prop = np.empty(k)
        for q in range(k):
            m = declus_mean(ind[:, q], wv)
            prop[q] = float(ind[:, q].mean()) if m is None else m
        left = int((~valid).sum())
        if left:
            notes.append(f"Проб домена с кодом вне списка категорий: {left} (не используются).")
        return cls, prop, notes
    z = samples.z
    if not bool(np.isfinite(z[in_d]).all()):
        raise _fail_samples()
    th = np.asarray(par.thresholds, dtype=np.float64)
    cls = np.where(np.isfinite(z), np.searchsorted(th, np.where(np.isfinite(z), z, 0.0), side="left"), -1)
    return cls.astype(np.int16), np.asarray(proportions(z[in_d], w, th), dtype=np.float64), notes


def _stack_models(models: Sequence[Model]) -> tuple[np.ndarray, np.ndarray, np.ndarray, CovModel]:
    """The models as one set of arrays [M, S, …]: a structure that a model lacks is padded with c = 0 (adds nothing)."""
    cms = [cov_model(m) for m in models]
    s = max(c.kinds.shape[0] for c in cms)
    kinds = np.zeros((len(cms), s), dtype=np.int64)
    pars = np.zeros((len(cms), s, PARS), dtype=np.float64)
    pars[:, :, 1:4] = 1.0
    pars[:, :, 4:13] = np.eye(3).reshape(9)
    head = np.empty((len(cms), 3), dtype=np.float64)
    for i, c in enumerate(cms):
        n = c.kinds.shape[0]
        kinds[i, :n] = c.kinds
        pars[i, :n] = c.pars
        head[i] = c.head
    return kinds, pars, head, cms[len(cms) // 2]


# --- the realizations ---------------------------------------------------------------------------------------------


class _Stopped(Exception):
    """The pool is stopped (a cancel of the caller or an error of another realization)."""


class _Shared:
    """What every realization reads (the arrays are the kernel's own writable copies): made once."""

    def __init__(self, par: SisParams, samples: RowSamples, grid: nd.NodeGrid, centres: np.ndarray, scls: np.ndarray,
                 prop: np.ndarray, cidx: np.ndarray, cn: np.ndarray, tpl: np.ndarray) -> None:  # fmt: skip
        self.par = par
        self.n = grid.n
        self.kk = par.k
        kinds, pars, head, _ = _stack_models(par.models)
        self.kinds, self.pars, self.head = _own(kinds, np.int64), _own(pars, np.float64), _own(head, np.float64)
        self.index = _own(grid.index, np.int32)
        self.shape = grid.shape
        self.template = _own(tpl, np.int32)
        self.cell = _own(grid.cell, np.int64)
        self.centres = _own(centres, np.float64)
        self.cidx = _own(cidx, np.int32)
        self.cn = _own(cn, np.int16)
        self.sxyz = _own(samples.local, np.float64)
        self.scls = _own(scls, np.int16)
        self.sz = _own(samples.z if not par.categorical else np.zeros(1), np.float64)
        self.prop = _own(prop, np.float64)
        self.skip = _own((~((prop > 0.0) & (prop < 1.0))).astype(np.int8), np.int8)
        self.cap = par.n_max + par.nodes
        self.tol2 = TOL * TOL
        self.cont = 0 if par.categorical else 1
        self.keep = 0  # 1 — the probabilities go to `pout` (set by `simulate`)
        self.cat = 1 if par.categorical else 0
        self.codes = _own(np.asarray(par.codes if par.codes is not None else [0]), np.int64)
        th = np.asarray(par.thresholds if par.thresholds is not None else [0.0], dtype=np.float64)
        self.th = _own(th, np.float64)
        in_d = samples.dom == D
        zd = samples.z[in_d]
        zlo = par.zmin if par.zmin is not None else min(float(zd.min()), float(th[0])) if not par.categorical else 0.0
        zhi = par.zmax if par.zmax is not None else max(float(zd.max()), float(th[-1])) if not par.categorical else 0.0
        lp = 1.0 if par.ltail == "linear" or par.ltail is None else 1.0 / float(par.ltpar or 1.0)
        up = 1.0 if par.utail in ("linear", None) else 1.0 / float(par.utpar or 1.0)
        self.tail = np.array([zlo, zhi, LOWER[par.ltail], UPPER[par.utail], lp, up, float(par.utpar or 1.0)])


class _Work:
    """The buffers of a thread (they need no clearing between realizations)."""

    def __init__(self, cap: int, n_nodes: int, n: int, kk: int, keep: bool, cont: bool) -> None:
        self.found = np.empty(max(n_nodes, 1), dtype=np.int64)
        self.xyz = np.empty((cap, 3), dtype=np.float64)
        self.cd = np.empty(cap, dtype=np.int16)
        self.zz = np.empty(cap, dtype=np.float64)
        self.centre = np.empty(3, dtype=np.float64)
        self.offs = np.zeros((1, 3), dtype=np.float64)
        self.f = np.empty(kk, dtype=np.float64)
        self.cls = np.empty(n, dtype=np.int16)
        self.dummy = np.empty(1, dtype=np.float64)
        self.pdummy = np.empty((1, 1), dtype=np.float64)
        kw = KrigeWork(cap + 1)
        self.a, self.rhs, self.sol, self.kout = kw.a, kw.rhs, kw.sol, kw.out


def _realization(sh: _Shared, r: int, row: np.ndarray, pout: np.ndarray, count: np.ndarray, work: _Work,
                 stop: threading.Event, progress: Callable[[int], None]) -> None:  # fmt: skip
    """The classes of the realization r into `work.cls`, the values into `row` (continuous), the probabilities into
    `pout` (keep_p) and its counts."""
    n = sh.n
    path, rng = realization_path(sh.par.seed, sh.par.n_real, r, n)
    draws = rng.random(n)
    path = _own(path, np.int64)
    simulated = np.zeros(n, dtype=np.bool_)
    nx, ny, nz = sh.shape
    keep = sh.keep
    out = row if sh.cont == 1 else work.dummy
    po = pout if keep == 1 else work.pdummy
    ok = 0
    for s0 in range(0, n, STEP):
        if stop.is_set():
            raise _Stopped
        s1 = min(s0 + STEP, n)
        sis_steps(sh.kinds, sh.pars, sh.head, sh.cat, keep, sh.cont, sh.index, nx, ny, nz, sh.template, sh.cell,
                  sh.centres, sh.cidx, sh.cn, sh.sxyz, sh.scls, sh.sz, path, draws, s0, s1, sh.par.nodes, sh.tol2, ok,
                  sh.th, sh.tail, sh.prop, sh.skip, simulated, work.cls, out, po, work.found, work.xyz, work.cd,
                  work.zz, work.centre, work.offs, work.a, work.rhs, work.sol, work.kout, work.f, count)  # fmt: skip
        progress(s1 - s0)


def simulate(par: SisParams, samples: RowSamples, grid: nd.NodeGrid, centres: np.ndarray, *,
             cat: np.ndarray | None = None, threads: int = 1, tick: Tick | None = None, keep_p: bool = False,
             sampler: Searcher | None = None) -> SisResult:  # fmt: skip
    """The realizations of the SIS (the module text). `centres` [n, 3] — the centres of the nodes of `grid` in the frame
    of `samples.local`; `cat` — the code of every sample (categories; a code that is not in `par.codes` is no datum);
    `sampler` — a ready `Searcher` of the samples (one pass, `n_max`). 422 `bad_param` {param}, `no_samples`; `tick`
    raises to cancel."""
    t0 = time.perf_counter()
    par = check_params(par)
    c = np.asarray(centres, dtype=np.float64)
    if c.shape != (grid.n, 3) or not bool(np.isfinite(c).all()):
        raise bad("centres", "Центры узлов: по три конечных координаты на узел.", "Сообщите разработчику.")
    scls, prop, notes = sample_classes(par, samples, cat)
    kk, n, n_real = par.k, grid.n, par.n_real
    notes.insert(0, f"Индикаторы: проб {samples.n}, классов {kk + (0 if par.categorical else 1)}; доли "
                    + "; ".join(f"{p:.3f}" for p in prop) + ".")  # fmt: skip
    codes = np.asarray(par.codes if par.codes is not None else [0], dtype=np.uint8)
    real = np.empty((n_real, n), dtype=np.uint8 if par.categorical else np.float64)
    pall = np.zeros((n_real, n, kk)) if keep_p else None
    counts = np.zeros((n_real, COUNTS), dtype=np.int64)
    if n == 0:
        return SisResult(real, pall, prop, counts, np.zeros(0, np.int16), time.perf_counter() - t0, notes)
    sr = sampler or Searcher(samples, par.ell, (Pass(1.0, 0, par.n_max, 0, 0, 0, 0),))
    cidx, cn = nd.sample_neighbours(sr, c, tick=tick)
    _, _, _, cm = _stack_models(par.models)
    tpl = nd.spiral_template(par.steps, par.ell, cm, limit=par.template_limit, shape=grid.shape)
    sh = _Shared(par, samples, grid, c, scls, prop, cidx, cn, tpl)
    sh.keep = 1 if keep_p else 0
    total = n * n_real
    done = [0] * n_real  # nodes of every realization (each thread writes its own item)
    stop = threading.Event()
    label = f"Реализации {{}} / {n_real}"
    finished = 0
    empty_p = np.empty((1, 1))

    def run_one(r: int, work: _Work) -> int:
        def progress(k: int) -> None:
            done[r] += k

        row = real[r] if not par.categorical else work.dummy
        _realization(sh, r, row, pall[r] if pall is not None else empty_p, counts[r], work, stop, progress)
        if par.categorical:
            real[r] = codes[work.cls[:n]]
        return r

    def make_work() -> _Work:
        return _Work(sh.cap, par.nodes, n, kk, keep_p, not par.categorical)

    workers = max(1, min(int(threads), n_real))
    if workers == 1:
        work = make_work()
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
                w = local.work = make_work()
            return run_one(r, w)

        pool = ThreadPoolExecutor(workers, thread_name_prefix="bf-sis")
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
    if not par.categorical and not bool(np.isfinite(real).all()):
        raise ApiError(500, "sim_nan", "Моделирование дало нечисловое значение.", "Сообщите разработчику.")
    if int(counts[:, C_DEGENERATE].sum()):
        notes.append(
            f"Вырожденных систем: {int(counts[:, C_DEGENERATE].sum())} (узлы взяты из долей проб без условия)."
        )
    return SisResult(real, pall, prop, counts, cn, time.perf_counter() - t0, notes)


# --- the warm-up -----------------------------------------------------------------------------------------------


def warm_sis() -> None:
    """Compiles `sis_steps` and runs `simulate` on writable and on read-only inputs (one signature)."""
    from bf_core.geostat.estimate.samples import prepare
    from bf_core.geostat.variogram.model import Structure

    nd.warm_nodes()
    rng = np.random.default_rng(3)
    n = 40
    xyz = rng.uniform(0.0, 8.0, (n, 3))
    z = rng.uniform(1.0, 5.0, n)
    s = prepare(xyz, z, None, None, np.zeros(n, np.int32), ["h"], np.zeros(n, np.int16), np.arange(n, dtype=np.int64),
                np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip
    grid = nd.NodeGrid.of((4, 4, 2), np.arange(32, dtype=np.uint64))
    model = Model(0.05, (Structure("spherical", 0.2, 5.0, 4.0, 3.0),))
    ell = Ellipsoid.of((0.0, 0.0, 0.0), (5.0, 4.0, 3.0))
    codes_of = (z > 3.0).astype(np.int64) * 7
    st = (2.0, 2.0, 2.0)
    pars = (
        SisParams((model,), ell, st, thresholds=(2.0, 3.0, 4.0), seed=1, n_real=2, n_max=6, nodes=4),
        SisParams((model, model), ell, st, codes=(0, 7), seed=1, n_real=2, n_max=6, nodes=4),
    )
    from bf_core.geostat.simulation.sgs import grid_centres

    centres = grid_centres(grid, (2.0, 2.0, 2.0))
    for read_only in (False, True):
        if read_only:
            for a in (centres, grid.index, grid.cell, s.local, s.z, codes_of):
                a.setflags(write=False)
        for par in pars:
            simulate(par, s, grid, centres, cat=codes_of if par.categorical else None)
