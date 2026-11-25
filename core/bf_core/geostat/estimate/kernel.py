"""The estimation kernel of one row of the plan (ТЗ 8.8.1–8.8.2; ADR 0024 §5–7; Д-27, Д-30; Э18, 18.7): a portion of
blocks → the passes → the method. NN, IDW, SK, OK (point and block). IK (18.9) joins through `SOLVERS`. The same code
answers the job (`estimate`, `run`), the cross-validation (`exclude`), the preview and the inspector (`estimate_block`).

    rk = compile_row(spec, samples, block_size=(dx, dy, dz), rotation_deg=φ, blockcov=…)     a row, compiled once
    res = estimate(rk, centres [m, 3], sizes=None, active=None, exclude=None, local=False)    one portion
    for res in run(rk, chunks, threads=4, tick=tick): …                                        the portions in order
    d = estimate_block(rk, centre, size=None, pool=None)                                       one block in detail

`centres` are the centres of the blocks in the coordinates of the PROJECT; the kernel subtracts `samples.origin`
(8.0.1: the kernels count in the local ones; `local=True` — the centres are local already). `sizes` [m, 3] — the size
of every block (`sub: own`; the discretization and C̄_VV are taken per distinct size); without it `rk.size` is the
size of all (`sub: parent`: the centre and the size of the parent P; the caller gives every block (P, D) the result).
`active` [m] — the blocks inside the domain and the restrictor (the job knows them); the others get STATUS 1.
`exclude` — ("sample", numbers [m]) | ("hole", dense indices [m]) — the cross-validation (18.14) leaves a sample or a
hole out for its block; the centre is then the position of the sample.

The status of a block, Д-27 (`ChunkResult.status`, i1; also in `values["STATUS"]`):
    0 estimated — the first pass that has enough samples (n_min, octants, holes) and a solvable system; PASS = its
      number. A pass whose system is degenerate (the pivot ≤ 1e−12·C(0)) goes to the next one; the counts by pass —
      `by_pass`, `singular` — are for the journal
    1 out of the domain or of the restrictor (`active` False), or the centre is not finite
    2 few samples — no pass had enough of them
    3 degenerate system — the passes that had enough samples all failed
With the status 1–3 every other output is NaN, not 0 (8.0.1).

KV is clipped from below at 0 (the variance of a rounding-noise system can be a hair negative); `neg_kv` counts such
blocks. SLOPE, KE come from the raw one. NN and IDW have no KV. A portion does not depend on how the blocks were cut
into portions or on the number of threads: a block is computed alone, in a fixed order (Т8.8-13).

Threads: `run` takes the portions from `chunks` (arrays of centres, or `Portion`), computes up to `2·threads` of
them ahead on a pool (the search of scipy and the numba kernels let the GIL go), and yields the results in the order
of the portions. `tick(None, None)` is the check of the cancel (it raises `JobCancelled`): before a portion goes to
the pool and after every result; pending portions are dropped, running ones finish.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

from . import methods as mt
from .cov import BlockCov, CovModel, cov_model
from .samples import RowSamples
from .search import Ellipsoid, Neighbours, Searcher
from .spec import EstimationSpec

ST_OK, ST_OUT, ST_FEW, ST_SINGULAR = 0, 1, 2, 3
KEYS_NEAR = ("NS", "NH", "NHY", "AD", "DMIN", "PASS", "NOCT")
KEYS_COLS = ("EST", "KV", "SLOPE", "KE", "LAGR", "SNW")  # the columns of `methods.krige_rows`
METHOD_KEYS = {
    "nn": ("EST",),
    "idw": ("EST",),
    "sk": ("EST", "KV", "SLOPE", "KE", "SNW"),
    "ok": KEYS_COLS,
}
Tick = Callable[[float | None, str | None], None]


def _err(code: str, message: str, hint: str, param: str) -> ApiError:
    return ApiError(422, code, message, hint, {"param": param})


# --- the compiled row ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RowKernel:
    """A row of the plan ready to estimate. Immutable but for the cache of the sizes; shared by the threads."""

    spec: EstimationSpec
    samples: RowSamples
    cov: CovModel | None  # SK, OK; None for NN, IDW (IK: the models are in `extra`, 18.9)
    ell: Ellipsoid
    searcher: Searcher
    blockcov: BlockCov | None
    disc: tuple[int, int, int]
    phi: float  # the rotation of the model, degrees
    size: tuple[float, float, float] | None  # the size of a block when all are alike (sub: parent)
    mean: float  # SK: m
    offsets: np.ndarray  # f8 [N, 3] of `size` (a point: [[0, 0, 0]])
    cvv: float  # C̄_VV of `size` (a point: C(0))
    local: np.ndarray  # f8 [n, 3] the samples, writable (one signature for numba)
    class_means: np.ndarray | None = None  # IK (18.9)
    extra: dict[str, Any] = field(default_factory=dict)  # IK (18.9): what its solver needs
    _geo: dict[Any, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def kind(self) -> str:
        return self.spec.method.kind

    @property
    def point(self) -> bool:
        return self.disc == (1, 1, 1)

    @property
    def n_passes(self) -> int:
        return self.searcher.n_passes

    @property
    def keys(self) -> tuple[str, ...]:
        """What the method gives: the near-field outputs and the columns of the solver."""
        if self.kind == "ik":
            k = len(self.spec.method.thresholds)
            return (*(f"P{i + 1}" for i in range(k)), "ETYPE", *KEYS_NEAR)
        return (*METHOD_KEYS.get(self.kind, ("EST",)), *KEYS_NEAR)

    @property
    def blocky(self) -> bool:
        """The discretization of a block matters (a model is there and the block is not a point)."""
        return (self.cov is not None or self.kind == "ik") and not self.point

    def ik_setup(self, size: Any) -> Any:
        """`indicator.IkSetup` of a block of the `size` (cached; a point — the centre)."""
        from .indicator import IkSetup

        ik = self.extra["ik"]
        if not self.point and size is None:
            raise _err("no_block_size", "Не задан размер блоков для дискретизации.",
                       "Передайте размер блока (сообщите разработчику).", "size")  # fmt: skip
        key = None if self.point else (float(size[0]), float(size[1]), float(size[2]))
        with self._lock:
            got = self._geo.get(("ik", key))  # type: ignore[call-overload]
        if got is None:
            got = IkSetup.of(ik["thresholds"], ik["covs"], ik["means"], size=key, disc=self.disc, phi=self.phi,
                             block_cov=ik["blockcovs"])  # fmt: skip
            with self._lock:
                got = self._geo.setdefault(("ik", key), got)  # type: ignore[call-overload,arg-type]
        return got

    def geometry(self, size: Any) -> tuple[np.ndarray, float]:
        """The points of the discretization and C̄_VV of a block of the `size` (cached; a point — the centre, C(0))."""
        if self.cov is None or self.blockcov is None:
            return self.offsets, self.cvv
        if self.point:
            return self.offsets, self.cvv
        key = (float(size[0]), float(size[1]), float(size[2]))
        with self._lock:
            got = self._geo.get(key)
        if got is None:
            got = (np.array(self.blockcov.points(key, self.disc, self.phi)), self.blockcov.vv(key, self.disc, self.phi))
            with self._lock:
                got = self._geo.setdefault(key, got)
        return got


def _own(a: np.ndarray) -> np.ndarray:
    out = np.asarray(a, dtype=np.float64, order="C")
    return out if out.flags.writeable else out.copy()


def _variogram_cov(spec: EstimationSpec) -> CovModel:
    from bf_core.geostat.variogram.model import parse_model

    ref = spec.variogram
    if ref is None or ref.snapshot is None:
        raise _err("no_variogram", "Модель вариограммы не выбрана или не скопирована в параметры оценки.",
                   "Выберите сохранённую модель вариограммы.", "variogram.model_id")  # fmt: skip
    return cov_model(parse_model(ref.snapshot))


def _sk_mean(spec: EstimationSpec, samples: RowSamples) -> float:
    if spec.method.mean_mode == "value" and spec.method.mean_value is not None:
        return float(spec.method.mean_value)
    for key in ("declus", "naive"):
        v = samples.means.get(key)
        if v is not None and math.isfinite(v):
            return float(v)
    raise _err("no_mean", "Для простого кригинга нет среднего: в домене нет проб.",
               "Задайте среднее вручную или проверьте домен проб.", "method.mean.value")  # fmt: skip


def _ik_extra(spec: EstimationSpec, samples: RowSamples, extra: dict[str, Any]) -> np.ndarray:
    """The thresholds, the models (K "per" or one "median"), their C̄_VV caches and the means of the classes."""
    from bf_core.geostat.variogram.model import parse_model

    from .indicator import class_means

    refs = list(spec.variograms) if spec.variograms else ([spec.variogram] if spec.variogram else [])
    covs = []
    for i, ref in enumerate(refs):
        if ref is None or ref.snapshot is None:
            raise _err("no_variogram", "Модель вариограммы индикатора не выбрана или не скопирована в параметры.",
                       "Выберите сохранённую модель для каждого порога.", f"variograms.{i}.model_id")  # fmt: skip
        covs.append(cov_model(parse_model(ref.snapshot)))
    d = samples.dom == 0
    t = np.asarray(spec.method.thresholds, dtype=np.float64)
    means = class_means(samples.z[d], None if samples.w is None else samples.w[d], t)
    extra["ik"] = {"thresholds": t, "covs": tuple(covs), "means": means,
                   "blockcovs": [BlockCov(c) for c in covs]}  # fmt: skip
    return means


def compile_row(
    spec: EstimationSpec,
    samples: RowSamples,
    *,
    block_size: Any = None,
    rotation_deg: float = 0.0,
    blockcov: BlockCov | None = None,
    k_c: int | None = None,
) -> RowKernel:
    """The row of the plan → what `estimate` needs. `block_size` — (dx, dy, dz) of the blocks (`sub: parent`: of the
    parent), None when `estimate` gets `sizes`; `rotation_deg` — the rotation of the model (the discretization turns
    with it); `blockcov` — the cache of C̄_VV of the model, shared by the rows with the same model (a new one if
    None); `k_c` — the first number of candidates of the tree (tests). The spec must have the search resolved."""
    s = spec.search
    if s.angles is None or s.radii is None:
        raise _err("search_unresolved", "Эллипсоид поиска не определён.",
                   "Выберите модель вариограммы или задайте радиусы поиска вручную.", "search")  # fmt: skip
    ell = Ellipsoid.of(s.angles, s.radii)
    soft = 0.0 if spec.samples.soft is None else float(spec.samples.soft[1])
    searcher = Searcher(samples, ell, s.passes, soft, k_c=k_c)
    kind = spec.method.kind
    cov = _variogram_cov(spec) if kind in ("sk", "ok") else None
    bc = None if cov is None else (blockcov if blockcov is not None and blockcov.cov.key == cov.key else BlockCov(cov))
    size = None if block_size is None else tuple(float(v) for v in block_size)
    if size is not None and len(size) != 3:
        raise _err("bad_param", "Размер блока: три числа.", "Задайте размеры по X, Y, Z.", "size")
    disc = spec.disc
    point = disc == (1, 1, 1)
    offsets = np.zeros((1, 3))
    cvv = cov.sill if cov is not None else math.nan
    if cov is not None and not point and size is not None and bc is not None:
        offsets = np.array(bc.points(size, disc, rotation_deg))
        cvv = bc.vv(size, disc, rotation_deg)
    mean = _sk_mean(spec, samples) if kind == "sk" else math.nan
    extra: dict[str, Any] = {}
    means = None
    if kind == "ik":
        means = _ik_extra(spec, samples, extra)
    return RowKernel(spec, samples, cov, ell, searcher, bc, disc, float(rotation_deg), size, mean, offsets,
                     float(cvv), _own(samples.local), means, extra)  # fmt: skip


# --- the methods -----------------------------------------------------------------------------------------------------


@dataclass
class Step:
    """What a solver gets for one pass of one portion."""

    k: int  # the pass, from 0
    c: np.ndarray  # f8 [m, 3] the centres, local
    nb: Neighbours  # the selection of the pass
    rows: np.ndarray  # i8 [r] the blocks to solve (the pass had enough samples)
    groups: list[tuple[np.ndarray, np.ndarray, float]]  # [(rows of one size, offsets, C̄_VV)]
    want_lam: bool


@dataclass
class Solved:
    status: np.ndarray  # i8 [m]: 0 — solved (`krige.KR_*` for the others); read at `rows` only
    cols: np.ndarray  # f8 [m, 6]: EST, KV, SLOPE, KE, LAGR, SNW (`KEYS_COLS`); NaN — none
    lam: np.ndarray | None = None  # f8 [m, width] the weights of the selected, when asked for
    extra: dict[str, np.ndarray] = field(default_factory=dict)  # f8 [m] other outputs (IK: P1…PK, ETYPE)


Solver = Callable[[RowKernel, Step], Solved]


def _blank(step: Step) -> Solved:
    m = step.c.shape[0]
    lam = np.full(step.nb.idx.shape, math.nan) if step.want_lam else np.empty((1, 1))
    return Solved(np.zeros(m, np.int64), np.full((m, mt.N_COLS), math.nan), lam if step.want_lam else None)


def _solve_nn(rk: RowKernel, step: Step) -> Solved:
    out = _blank(step)
    nb = step.nb
    lam = out.lam if out.lam is not None else np.empty((1, 1))
    mt.nn_rows(nb.idx, nb.h, nb.z, nb.n, step.rows, out.cols, out.status, lam, out.lam is not None)
    return out


def _solve_idw(rk: RowKernel, step: Step) -> Solved:
    out = _blank(step)
    nb = step.nb
    lam = out.lam if out.lam is not None else np.empty((1, 1))
    mt.idw_rows(nb.h, nb.z, nb.n, step.rows, float(rk.spec.method.power), float(rk.ell.radii[0]), out.cols,
                out.status, lam, out.lam is not None)  # fmt: skip
    return out


def _solve_krige(rk: RowKernel, step: Step) -> Solved:
    assert rk.cov is not None
    out = _blank(step)
    nb = step.nb
    lam = out.lam if out.lam is not None else np.empty((1, 1))
    ok = 1 if rk.kind == "ok" else 0
    for rows, offs, bv in step.groups:
        mt.krige_rows(*rk.cov.args, rk.local, nb.idx, nb.z, nb.n, step.c, rows, offs, float(bv), float(rk.mean), ok,
                      out.cols, out.status, lam, out.lam is not None)  # fmt: skip
    return out


SOLVERS: dict[str, Solver] = {"nn": _solve_nn, "idw": _solve_idw, "sk": _solve_krige, "ok": _solve_krige}


def _groups(rk: RowKernel, rows: np.ndarray, sizes: np.ndarray | None, inverse: np.ndarray | None,
            uniq: np.ndarray | None) -> list[tuple[np.ndarray, np.ndarray, float]]:  # fmt: skip
    """The blocks `rows` split by the size of the block: [(rows, offsets, C̄_VV)]."""
    if not rk.blocky or sizes is None or inverse is None or uniq is None:
        if rk.blocky and rk.size is None:
            raise _err("no_block_size", "Не задан размер блоков для дискретизации.",
                       "Передайте размер блока (сообщите разработчику).", "size")  # fmt: skip
        return [(rows, rk.offsets, rk.cvv)]
    out = []
    which = inverse[rows]
    for g in range(uniq.shape[0]):
        sub = rows[which == g]
        if sub.size:
            offs, bv = rk.geometry(uniq[g])
            out.append((np.ascontiguousarray(sub), offs, bv))
    return out


# --- a portion -------------------------------------------------------------------------------------------------------


@dataclass
class Portion:
    """A portion of `run` with what goes beside the centres (see `estimate`)."""

    centres: np.ndarray
    sizes: np.ndarray | None = None
    active: np.ndarray | None = None
    exclude: tuple[str, np.ndarray] | None = None


@dataclass
class ChunkResult:
    values: dict[str, np.ndarray]  # the keys of `outputs` (+ STATUS): f8 [m], NaN — none
    status: np.ndarray  # i1 [m]
    by_pass: np.ndarray  # i8 [P]: the blocks estimated by the pass
    singular: np.ndarray  # i8 [P]: the blocks whose system the pass could not solve (degenerate)
    counts: np.ndarray  # i8 [4]: the blocks by the status
    neg_kv: int = 0  # the blocks whose KV was below 0 (it is written as 0)


@dataclass
class _Trace:
    passes: list[dict[str, Any]] = field(default_factory=list)
    sel: np.ndarray | None = None
    lam: np.ndarray | None = None
    k_last: int = 0


def _is_pk(key: str) -> bool:
    """P1…PK of IK (the key «P» of the outputs stands for them)."""
    return key[:1] == "P" and key[1:].isdigit()


def _one_portion(rk: RowKernel, p: Portion, local: bool, trace: _Trace | None = None) -> ChunkResult:
    centres = np.asarray(p.centres, dtype=np.float64).reshape(-1, 3)
    m = centres.shape[0]
    c = centres if local else centres - rk.samples.origin
    c = np.ascontiguousarray(c)
    act: np.ndarray = np.asarray(np.isfinite(c).all(axis=1))
    if p.active is not None:
        act &= np.asarray(p.active, dtype=np.bool_).reshape(m)
    sizes = inverse = uniq = None
    if p.sizes is not None and rk.blocky:
        sizes = np.ascontiguousarray(np.asarray(p.sizes, dtype=np.float64).reshape(m, 3))
        uniq, inverse = np.unique(sizes, axis=0, return_inverse=True)
        inverse = np.asarray(inverse).reshape(m)
    if rk.kind == "ik":
        return _ik_portion(rk, p, c, act, sizes, inverse, uniq, trace)
    solver = SOLVERS.get(rk.kind)
    if solver is None:
        raise ApiError(500, "internal", f"Метод «{rk.kind}» не подключён к ядру оценки.", "Сообщите разработчику.")
    n_pass = rk.n_passes
    status = np.where(act, ST_FEW, ST_OUT).astype(np.int8)
    pending = act.copy()
    enough = np.zeros(m, np.bool_)
    acc: dict[str, np.ndarray] = {key: np.full(m, math.nan) for key in rk.keys}
    by_pass = np.zeros(n_pass, np.int64)
    singular = np.zeros(n_pass, np.int64)
    want = trace is not None
    for k in range(n_pass):
        if not pending.any():
            break
        nb = rk.searcher.gather(c, k, todo=pending, exclude=p.exclude)
        have = pending & nb.ok
        enough |= have
        rows = np.flatnonzero(have)
        good = np.zeros(m, np.bool_)
        solved = None
        if rows.size:
            step = Step(k, c, nb, rows, _groups(rk, rows, sizes, inverse, uniq), want)
            solved = solver(rk, step)
            good = have & (solved.status == 0)
            by_pass[k] = int(good.sum())
            singular[k] = int(rows.size - good.sum())
        if trace is not None:
            _note(trace, k, nb, have, good, solved)
        if good.any():
            status[good] = ST_OK
            for key, col in zip(KEYS_COLS, range(mt.N_COLS), strict=True):
                if key in acc and solved is not None:
                    acc[key][good] = solved.cols[good, col]
            if solved is not None:
                for key, arr in solved.extra.items():
                    acc.setdefault(key, np.full(m, math.nan))[good] = arr[good]
            for key, arr in (("NS", nb.n), ("NH", nb.nh), ("NHY", nb.nhy), ("AD", nb.ad), ("DMIN", nb.dmin),
                             ("NOCT", nb.noct)):  # fmt: skip
                acc[key][good] = arr[good]
            acc["PASS"][good] = k + 1
        pending &= ~good
    status[(status != ST_OK) & enough] = ST_SINGULAR
    return _result(rk, acc, status, by_pass, singular, trace is not None)


def _result(rk: RowKernel, acc: dict[str, np.ndarray], status: np.ndarray, by_pass: np.ndarray,
            singular: np.ndarray, everything: bool) -> ChunkResult:  # fmt: skip
    """The outputs of the row from the accumulated arrays: KV clipped at 0, the keys asked for, STATUS."""
    neg = 0
    if "KV" in acc:
        kv = acc["KV"]
        neg = int((kv < 0.0).sum())
        acc["KV"] = np.where(kv < 0.0, 0.0, kv)
    wanted = {"STATUS", *rk.spec.outputs.fields}
    values = {key: arr for key, arr in acc.items() if everything or key in wanted or _is_pk(key)}
    values["STATUS"] = status.astype(np.float64)
    counts = np.bincount(status, minlength=4).astype(np.int64)
    return ChunkResult(values, status, by_pass, singular, counts, neg)


def _ik_portion(rk: RowKernel, p: Portion, c: np.ndarray, act: np.ndarray, sizes: np.ndarray | None,
                inverse: np.ndarray | None, uniq: np.ndarray | None, trace: _Trace | None) -> ChunkResult:  # fmt: skip
    """IK (18.9) of a portion: the passes are `indicator.ik_passes`; the blocks of one size go together."""
    from .indicator import ik_passes

    m = c.shape[0]
    n_pass = rk.n_passes
    status = np.where(act, ST_FEW, ST_OUT).astype(np.int8)
    acc: dict[str, np.ndarray] = {key: np.full(m, math.nan) for key in rk.keys}
    which = np.zeros(m, np.int64) if inverse is None else inverse
    groups = [(np.flatnonzero(act), rk.size)] if uniq is None else [
        (np.flatnonzero(act & (which == g)), tuple(uniq[g])) for g in range(uniq.shape[0])]  # fmt: skip
    pass_all = np.zeros(m, np.int64)
    for rows, size in groups:
        if rows.size == 0:
            continue
        ex = None if p.exclude is None else (p.exclude[0], np.asarray(p.exclude[1])[rows])
        run_ = ik_passes(rk.searcher, rk.local, c[rows], rk.ik_setup(size), exclude=ex)
        status[rows] = run_.status
        good = rows[run_.status == ST_OK]
        sel = np.flatnonzero(run_.status == ST_OK)
        for q in range(run_.p.shape[1]):
            acc[f"P{q + 1}"][good] = run_.p[sel, q]
        acc["ETYPE"][good] = run_.etype[sel]
        nb = run_.nb
        for key, arr in (("NS", nb.n), ("NH", nb.nh), ("NHY", nb.nhy), ("AD", nb.ad), ("DMIN", nb.dmin),
                         ("NOCT", nb.noct)):  # fmt: skip
            acc[key][good] = arr[sel]
        acc["PASS"][good] = run_.pass_[sel]
        pass_all[good] = run_.pass_[sel]
    by_pass = np.bincount(pass_all[pass_all > 0], minlength=n_pass + 1)[1 : n_pass + 1].astype(np.int64)
    if trace is not None and pass_all[0] > 0:
        trace.k_last = int(pass_all[0]) - 1
        trace.passes.append({"pass": int(pass_all[0]), "ok": True, "n": int(acc["NS"][0]), "reason": None})
    elif trace is not None and act[0]:
        trace.k_last = n_pass - 1
    return _result(rk, acc, status, by_pass, np.zeros(n_pass, np.int64), trace is not None)


def estimate(
    rk: RowKernel,
    centres: np.ndarray,
    *,
    sizes: np.ndarray | None = None,
    active: np.ndarray | None = None,
    exclude: tuple[str, np.ndarray] | None = None,
    local: bool = False,
) -> ChunkResult:
    """One portion of blocks (module docstring). Keys of `values`: those of `spec.outputs.fields` the method gives."""
    return _one_portion(rk, Portion(centres, sizes, active, exclude), local)


def _as_portion(item: np.ndarray | Portion) -> Portion:
    return item if isinstance(item, Portion) else Portion(np.asarray(item))


def run(
    rk: RowKernel,
    chunks: Iterable[np.ndarray | Portion],
    *,
    threads: int = 1,
    tick: Tick | None = None,
    local: bool = False,
) -> Iterator[ChunkResult]:
    """The portions of `chunks` in order, `threads` at a time (module docstring)."""

    def check() -> None:
        if tick is not None:
            tick(None, None)

    if threads <= 1:
        for item in chunks:
            check()
            res = _one_portion(rk, _as_portion(item), local)
            check()
            yield res
        return
    pool = ThreadPoolExecutor(int(threads), thread_name_prefix="bf-estimate")
    queue: deque[Any] = deque()
    ahead = 2 * int(threads)
    try:
        for item in chunks:
            check()
            queue.append(pool.submit(_one_portion, rk, _as_portion(item), local))
            if len(queue) >= ahead:
                res = queue.popleft().result()
                check()
                yield res
        while queue:
            res = queue.popleft().result()
            check()
            yield res
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


# --- one block, in detail --------------------------------------------------------------------------------------------


def _note(trace: _Trace, k: int, nb: Neighbours, have: np.ndarray, good: np.ndarray, solved: Solved | None) -> None:
    """The record of a pass for a one-block portion (`estimate_block`)."""
    n = int(nb.n[0])
    reason = None if good[0] else ("singular" if have[0] else "few")
    trace.passes.append({"pass": k + 1, "ok": bool(have[0]), "n": n, "reason": reason})
    trace.k_last = k
    if good[0] and solved is not None:
        trace.sel = nb.idx[0, :n].astype(np.int64)
        trace.lam = None if solved.lam is None else solved.lam[0, :n].copy()


def __getattr__(name: str) -> Any:
    """`kernel.estimate_block`, `kernel.BlockDetail` — in `kernel_block.py`, `kernel.warm_kernel` — in `kernel_warm.py`
    (the file would pass 600 lines)."""
    if name in ("estimate_block", "BlockDetail"):
        from . import kernel_block

        return getattr(kernel_block, name)
    if name == "warm_kernel":
        from . import kernel_warm

        return kernel_warm.warm_kernel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
