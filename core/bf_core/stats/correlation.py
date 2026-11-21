"""Correlations of the fields of a table (ТЗ 7.27, 8.3; ADR 0019 §4) — the port of BF `stats/dendrogram.py`
(`weighted_corr`, `correlation_matrix`) and of the rules of its route. All the pairs come from two passes of
one numba kernel over the rows, not from a pass per pair.

    CorrSpec(method, log, missing, negatives, exclude_zero)      the keys of `charts.settings` (ADR 0019 §9);
        `CorrSpec.from_settings(settings)` — StatsError `bad_param` {param}
    correlations(columns, weights=None, spec=CorrSpec(), tick=None, threads=None) -> Correlation
        columns — k arrays of one length (NaN — missing), weights — None (every row 1) or an array
        Correlation: r float64 [k, k] (NaN — not defined), n int64 [k, k] (rows shared by a pair; the diagonal —
        rows of a field), counts, fields [{missing, negative, zero_excluded, nonpositive, used}], spec
    pair_moments(columns, weights=None, tick=None, threads=None) -> PairMoments
        the sums behind r for values already prepared: n, total (Σ w), mean[i, j] (of field i over the rows it
        shares with j), sxx[i, j] = Σ w·(x_i − mean)², sxy[i, j], r — the regressions of a scatter take them
    average_ranks(values) -> float64       ranks from 1, equal values share the mean of their ranks, NaN stays
    run_tasks(tasks, work, threads, done), thread_count(threads, tasks)    blocks of rows in threads, in order
    warm()

Rules (BF; ТЗ 7.27 «< 0 и нули — как в гистограмме»), for every field apart: a value that is missing (NaN,
±inf) takes no part; a row without a weight (NaN, ±inf, ≤ 0) takes no part at all; values < 0 — "half_abs"
(|c| / 2, the default: «<0.01» written as −0.01), "keep", "exclude"; zeros — left out with `exclude_zero`; with
`log` (the default) the correlation is that of ln, values ≤ 0 have none and are left out (`nonpositive`).
Missing values: "pairwise" — a pair takes the rows where both fields have a value; "complete" — only the rows
where every field has one. A pair with fewer than MIN_PAIRS (3) rows or without spread has no r (NaN).
A missing value never becomes 0.

`counts` of the rows: rows = empty (no field has a value) + bad_weight (values but no weight) + incomplete
(left out for what is missing after the rules: "complete" — a field without a value, "pairwise" — fewer than
two values) + used.

Pearson: r = Σ w·(a − ā)·(b − b̄) / √(Σ w·(a − ā)² · Σ w·(b − b̄)²) over the rows of the pair, ā and b̄ — its
weighted means. Spearman — the same of the average ranks of the rows of the pair (ranks without weights, the
correlation with them — as BF); ranks do not change under ln, so `log` only leaves out the values ≤ 0.

Numbers. The first pass gives the means, the second sums the squares about them (what is left of the mean's
rounding is taken off at the end), so values far from zero lose nothing: 1e−12 against a direct count. Rows
with every field go into k·(k + 1) / 2 sums, the others — into the sums of their pairs; the two parts are
joined by the formula of a merged variance (no subtraction of large numbers). Sums run over chunks of 1024
rows in the order of the rows and are added with compensation (Neumaier); the blocks of rows may be computed
in several threads (the kernel releases the GIL) and are added in their order — the same bits with any number
of threads. Two equal columns give r = 1 exactly. Memory: a block of BLOCK rows per thread; Spearman keeps
the prepared columns (8·k·rows bytes) and, when the rows differ in what is missing, their sort orders too.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from queue import SimpleQueue
from typing import Any

import numpy as np

from bf_core.jit import njit

from .select import NEGATIVES, bad_param
from .weighted import Tick

METHODS = ("pearson", "spearman")
MISSING = ("pairwise", "complete")
MIN_PAIRS = 3  # rows a pair needs (BF)
MAX_THREADS = 4
_CHUNK = 1024  # rows summed plainly between two compensated additions
_CELLS = 1 << 20  # values of a block: BLOCK rows × k fields
_NONE = np.zeros(0)

_ENUMS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "method": (METHODS, "Корреляция", "pearson — Пирсон, spearman — Спирмен"),
    "missing": (MISSING, "Правило для пропусков", "pairwise — попарно, complete — только полные строки"),
    "negatives": (NEGATIVES, "Правило для значений меньше нуля", "keep — оставить, exclude — исключить, "
                  "half_abs — половина модуля"),
}  # fmt: skip


@dataclass(frozen=True)
class CorrSpec:
    """How the correlations of a chart are computed (the keys of `charts.settings`, ADR 0019 §9)."""

    method: str = "pearson"  # pearson | spearman
    log: bool = True  # of ln — the default of ТЗ 7.27
    missing: str = "pairwise"  # pairwise | complete
    negatives: str = "half_abs"  # keep | exclude | half_abs — the default of the window and of BF
    exclude_zero: bool = False

    def __post_init__(self) -> None:
        for key, (allowed, title, names) in _ENUMS.items():
            value = getattr(self, key)
            if value not in allowed:
                raise bad_param(key, f"{title}: значение «{value}» неизвестно.", f"Допустимо: {names}.")
        for key, title in (("log", "«По логарифмам»"), ("exclude_zero", "«Исключить нули»")):
            if not isinstance(getattr(self, key), bool):
                raise bad_param(key, f"{title} — это да или нет.", "Передайте true или false.")

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> CorrSpec:
        """The rules of the settings of a chart; keys that are absent or None take the defaults."""
        own = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in settings.items() if k in own and v is not None})


@dataclass(frozen=True)
class PairMoments:
    """The sums of every pair of fields over the rows they share (k × k; [i, j] — field i with field j)."""

    n: np.ndarray  # int64, symmetric; the diagonal — rows of a field
    total: np.ndarray  # Σ w
    mean: np.ndarray  # of field i over the rows shared with j; NaN — no rows
    sxx: np.ndarray  # Σ w·(x_i − mean[i, j])²
    sxy: np.ndarray  # Σ w·(x_i − mean[i, j])·(x_j − mean[j, i]), symmetric
    r: np.ndarray  # NaN — fewer than MIN_PAIRS rows or no spread; the diagonal — 1
    rows: tuple[int, int, int, int]  # rows with every field, with none, with one, with two … k − 1 of them


@dataclass(frozen=True)
class Correlation:
    """The correlations of the fields of a chart (see the module docstring)."""

    r: np.ndarray
    n: np.ndarray
    counts: dict[str, int]
    fields: list[dict[str, int]]
    spec: CorrSpec


# --- kernels -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _flush(acc, comp, s, size):  # type: ignore[no-untyped-def]
    """Add the first `size` sums of a chunk to the totals with compensation; they are zeroed."""
    for j in range(size):
        x = s[j]
        t = acc[j] + x
        if abs(acc[j]) >= abs(x):
            comp[j] += (acc[j] - t) + x
        else:
            comp[j] += (x - t) + acc[j]
        acc[j] = t
        s[j] = 0.0


@njit(cache=True, nogil=True)
def _block_sums(x, m, w, off, has_w, quad, cc, cp, out_f, out_i):  # type: ignore[no-untyped-def]
    """The sums of the rows 0 … m − 1 of the block x [k, ≥ m] (NaN — no value; the weight of row r — w[off + r]).

    Rows with every field, about the centres cc[k]: out_f[0] Σ w; [1 … k] Σ w·d_i; then k × k (i ≤ j) Σ w·d_i·d_j.
    The other rows, by pairs i ≤ j, about the centres cp (cp[i, j] — of field i with j): four k × k tables —
    Σ w ([i, j]), Σ w·d ([i, j] — of i, [j, i] — of j), Σ w·d² (the same), Σ w·d_i·d_j ([i, j], i < j).
    out_i: [0] rows with every field, [1] with none, [2] with one, [3] with some; then k × k (i ≤ j) rows of a
    pair among the «other» rows. Without `quad` the squares and the products are not summed (the first pass)."""
    k = x.shape[0]
    kk = k * k
    o2 = 1 + k
    ow = o2 + kk
    os_ = ow + kk
    oq = os_ + kk
    ox = oq + kk
    size = ox + kk
    acc = np.zeros(size)
    comp = np.zeros(size)
    s = np.zeros(size)
    val = np.empty(k)
    d = np.empty(k)
    idx = np.empty(k, dtype=np.int64)
    a = 0
    while a < m:
        b = min(a + _CHUNK, m)
        some = False
        for r in range(a, b):
            cnt = 0
            for j in range(k):
                v = x[j, r]
                if v == v:
                    idx[cnt] = j
                    val[cnt] = v
                    cnt += 1
            if cnt == 0:
                out_i[1] += 1
                continue
            wt = 1.0
            if has_w:
                wt = w[off + r]
            if cnt == k:
                out_i[0] += 1
                s[0] += wt
                for i in range(k):
                    d[i] = val[i] - cc[i]
                    s[1 + i] += wt * d[i]
                if quad:
                    for i in range(k):
                        t = wt * d[i]
                        base = o2 + i * k
                        for j in range(i, k):
                            s[base + j] += t * d[j]
                continue
            some = True
            if cnt == 1:
                out_i[2] += 1
            else:
                out_i[3] += 1
            for p in range(cnt):
                i = idx[p]
                e = i * k + i
                out_i[4 + e] += 1
                da = val[p] - cp[i, i]
                ta = wt * da
                s[ow + e] += wt
                s[os_ + e] += ta
                if quad:
                    s[oq + e] += ta * da
                for q in range(p + 1, cnt):
                    j = idx[q]
                    e = i * k + j
                    g = j * k + i
                    out_i[4 + e] += 1
                    da = val[p] - cp[i, j]
                    db = val[q] - cp[j, i]
                    ta = wt * da
                    tb = wt * db
                    s[ow + e] += wt
                    s[os_ + e] += ta
                    s[os_ + g] += tb
                    if quad:
                        s[oq + e] += ta * da
                        s[oq + g] += tb * db
                        s[ox + e] += ta * db
        _flush(acc, comp, s, size if some else ow)
        a = b
    for j in range(size):
        out_f[j] = acc[j] + comp[j]


@njit(cache=True, nogil=True)
def _rank(v, order, member, use_member, out):  # type: ignore[no-untyped-def]
    """Average ranks (from 1) of the rows `order` (ascending by v) into out[row]; with `use_member` — among the
    rows where member[row], the others are not written."""
    n = order.shape[0]
    done = 0
    a = 0
    while a < n:
        x = v[order[a]]
        b = a
        cnt = 0
        while b < n and v[order[b]] == x:
            if not use_member or member[order[b]]:
                cnt += 1
            b += 1
        if cnt > 0:
            rank = done + 0.5 * (cnt + 1)
            for t in range(a, b):
                row = order[t]
                if not use_member or member[row]:
                    out[row] = rank
            done += cnt
        a = b


# --- the rows of a block ------------------------------------------------------------------------------------


class _Source:
    """Rows a … b − 1 of the columns into a block [k, b − a]: the rules of `spec` (None — the values as they
    are), NaN — the value takes no part. `fill` returns the counts of the block: [k + 1, 4] — of every field
    (missing, negative, zero_excluded, nonpositive), then of the rows (empty, bad_weight)."""

    def __init__(self, columns: list[np.ndarray], weights: np.ndarray | None, spec: CorrSpec | None,
                 take_log: bool = True) -> None:  # fmt: skip
        self.columns, self.weights, self.spec = columns, weights, spec
        self.take_log = take_log and spec is not None and spec.log

    def fill(self, buf: np.ndarray, a: int, b: int) -> np.ndarray:
        m = b - a
        k = len(self.columns)
        spec = self.spec
        cnt = np.zeros((k + 1, 4), dtype=np.int64)
        w_bad = None
        if self.weights is not None:
            w = self.weights[a:b]
            w_bad = ~(np.isfinite(w) & (w > 0.0))
        any_value = np.zeros(m, dtype=np.bool_)
        for j, col in enumerate(self.columns):
            v = buf[j, :m]
            v[:] = col[a:b]
            bad = ~np.isfinite(v)
            cnt[j, 0] = int(np.count_nonzero(bad))
            any_value |= ~bad
            if w_bad is not None:
                bad |= w_bad
            if spec is not None:
                with np.errstate(invalid="ignore"):
                    neg = (v < 0.0) & ~bad
                cnt[j, 1] = int(np.count_nonzero(neg))
                if spec.negatives == "exclude":
                    bad |= neg
                elif spec.negatives == "half_abs":
                    np.multiply(v, -0.5, out=v, where=neg)
                if spec.exclude_zero:
                    zero = (v == 0.0) & ~bad
                    cnt[j, 2] = int(np.count_nonzero(zero))
                    bad |= zero
                if spec.log:
                    with np.errstate(invalid="ignore"):
                        low = (v <= 0.0) & ~bad
                    cnt[j, 3] = int(np.count_nonzero(low))
                    bad |= low
            v[bad] = np.nan
            if self.take_log:
                np.log(v, out=v)
        cnt[k, 0] = m - int(np.count_nonzero(any_value))
        if w_bad is not None:
            cnt[k, 1] = int(np.count_nonzero(any_value & w_bad))
        if spec is not None and spec.missing == "complete":
            part = np.isnan(buf[:, :m]).any(axis=0)
            buf[:, :m][:, part] = np.nan
        return cnt


class _Sum:
    """A compensated sum of vectors, in the order they are added."""

    def __init__(self, size: int) -> None:
        self.acc, self.comp = np.zeros(size), np.zeros(size)

    def add(self, x: np.ndarray) -> None:
        t = self.acc + x
        self.comp += np.where(np.abs(self.acc) >= np.abs(x), (self.acc - t) + x, (x - t) + self.acc)
        self.acc = t

    @property
    def value(self) -> np.ndarray:
        return np.asarray(self.acc + self.comp)


def block_rows(k: int) -> int:
    """Rows of a block of k fields: about a million values, 4096 … 65536 rows (depends on k only — the bits of
    the answer do not depend on the machine)."""
    return int(min(max(_CELLS // max(k, 1), 4096), 65536))


def thread_count(threads: int | None, tasks: int) -> int:
    """Threads for `tasks` tasks: `threads`, or up to MAX_THREADS of the processors of the machine."""
    limit = min(MAX_THREADS, os.cpu_count() or 1) if threads is None else int(threads)
    return max(1, min(limit, tasks))


def run_tasks(tasks: list[Any], work: Callable[[Any], Any], threads: int, done: Callable[[int, Any], None]) -> None:
    """`work(task)` for every task, in `threads` threads; `done(number, result)` — in the order of the tasks,
    in the caller's thread (it may raise to cancel: tasks not started are dropped)."""
    if threads <= 1:
        for i, task in enumerate(tasks):
            done(i, work(task))
        return
    pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="bf-corr")
    try:
        futures = [pool.submit(work, task) for task in tasks]
        for i, future in enumerate(futures):
            done(i, future.result())
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _pass(
    src: _Source, n: int, quad: bool, cc: np.ndarray, cp: np.ndarray, tick: Tick | None, lo: float, hi: float,
    threads: int | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:  # fmt: skip
    """One pass over the rows: (the sums, the numbers of rows, the counts of `_Source.fill`)."""
    k = len(src.columns)
    size, block = 1 + k + 5 * k * k, block_rows(k)
    spans = [(a, min(a + block, n)) for a in range(0, n, block)]
    workers = thread_count(threads, len(spans))
    bufs: SimpleQueue[np.ndarray] = SimpleQueue()
    for _ in range(workers):
        bufs.put(np.empty((k, min(block, max(n, 1)))))
    has_w = src.weights is not None
    w = src.weights if src.weights is not None else _NONE

    def work(span: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        a, b = span
        buf = bufs.get()
        try:
            cnt = src.fill(buf, a, b)
            f, i = np.zeros(size), np.zeros(4 + k * k, dtype=np.int64)
            _block_sums(buf, b - a, w, a, has_w, quad, cc, cp, f, i)
            return f, i, cnt
        finally:
            bufs.put(buf)

    sums, rows, counts = _Sum(size), np.zeros(4 + k * k, dtype=np.int64), np.zeros((k + 1, 4), dtype=np.int64)

    def done(i: int, result: tuple[np.ndarray, np.ndarray, np.ndarray]) -> None:
        sums.add(result[0])
        rows[:] += result[1]
        counts[:] += result[2]
        if tick is not None:
            tick(lo + (hi - lo) * (i + 1) / len(spans))

    with np.errstate(over="ignore", invalid="ignore"):
        run_tasks(spans, work, workers, done)
    return sums.value, rows, counts


def _upper(flat: np.ndarray, k: int, diagonal: bool = True) -> np.ndarray:
    """A k × k table kept for i ≤ j (or i < j) made symmetric."""
    m = np.triu(flat.reshape(k, k), 0 if diagonal else 1)
    return np.asarray(m + np.triu(m, 1).T)


def _moments(src: _Source, n: int, tick: Tick | None, threads: int | None) -> tuple[PairMoments, np.ndarray]:
    """The two passes: the means, then the sums about them (see the module docstring)."""
    k = len(src.columns)
    kk = k * k
    o2, ow = 1 + k, 1 + k + kk
    zero_c, zero_p = np.zeros(k), np.zeros((k, k))
    f, rows, counts = _pass(src, n, False, zero_c, zero_p, tick, 0.0, 0.4, threads)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        nc = int(rows[0])
        npair = _upper(rows[4:], k)
        wp1 = _upper(f[ow : ow + kk], k)
        cc = f[1:o2] / f[0] if nc else zero_c
        cp = np.where(npair > 0, f[ow + kk : ow + 2 * kk].reshape(k, k) / wp1, 0.0)
        cc, cp = np.where(np.isfinite(cc), cc, 0.0), np.ascontiguousarray(np.where(np.isfinite(cp), cp, 0.0))
        f, rows, _ = _pass(src, n, True, np.ascontiguousarray(cc), cp, tick, 0.4, 1.0, threads)
        wc = float(f[0]) if nc else 0.0
        dc = f[1:o2] / wc if nc else zero_c
        mc = cc + dc
        c_c = _upper(f[o2:ow], k) - wc * np.outer(dc, dc) if nc else zero_p
        has = npair > 0
        wp = np.where(has, _upper(f[ow : ow + kk], k), 0.0)
        dp = np.where(has, f[ow + kk : ow + 2 * kk].reshape(k, k) / wp, 0.0)
        mp = cp + dp
        sq = np.where(has, f[ow + 2 * kk : ow + 3 * kk].reshape(k, k) - wp * dp * dp, 0.0)
        cross = _upper(f[ow + 3 * kk :], k, diagonal=False)
        cross[np.diag_indices(k)] = np.diag(f[ow + 2 * kk : ow + 3 * kk].reshape(k, k))
        sx = np.where(has, cross - wp * dp * dp.T, 0.0)
        total = wc + wp
        share = np.where(total > 0.0, wc * wp / total, 0.0)
        gap = np.where(has, mc[:, None] - mp, 0.0)
        sxx = np.maximum(np.diag(c_c)[:, None] + sq + share * gap * gap, 0.0)
        sxy = c_c + sx + share * gap * gap.T
        mean = np.where(total > 0.0, (wc * mc[:, None] + wp * mp) / total, np.nan)
        count = nc + npair
        den = np.sqrt(sxx * sxx.T)
        r = np.where((count >= MIN_PAIRS) & (den > 0.0) & np.isfinite(den), sxy / den, np.nan)
        r = np.clip(r, -1.0, 1.0)
    diagonal = np.diag(count)
    r[np.diag_indices(k)] = np.where(diagonal > 0, 1.0, np.nan)
    whole = (int(rows[0]), int(rows[1]), int(rows[2]), int(rows[3]))
    return PairMoments(count.astype(np.int64), total, mean, sxx, sxy, r, whole), counts


def _checked(columns: Sequence[Any], weights: Any) -> tuple[list[np.ndarray], np.ndarray | None, int]:
    cols = [np.ascontiguousarray(c, dtype=np.float64) for c in columns]
    if not cols:
        raise ValueError("no columns")
    n = int(cols[0].shape[0])
    if any(c.ndim != 1 or c.shape[0] != n for c in cols):
        raise ValueError("columns must be 1D of one length")
    w = None if weights is None else np.ascontiguousarray(weights, dtype=np.float64)
    if w is not None and w.shape != (n,):
        raise ValueError("weights must have the length of the columns")
    return cols, w, n


def pair_moments(
    columns: Sequence[Any], weights: Any = None, *, tick: Tick | None = None, threads: int | None = None
) -> PairMoments:
    """The sums of every pair of columns over the rows where both have a value (NaN, ±inf — none) and the row
    has a weight > 0 (`weights` None — every row 1); see `PairMoments`."""
    cols, w, n = _checked(columns, weights)
    return _moments(_Source(cols, w, None), n, tick, threads)[0]


# --- ranks --------------------------------------------------------------------------------------------------


def average_ranks(values: Any) -> np.ndarray:
    """Ranks from 1 of the values that are not NaN; equal values share the mean of their ranks (scipy
    `rankdata`, method "average"); NaN stays NaN."""
    v = np.ascontiguousarray(values, dtype=np.float64)
    out = np.full(v.shape, np.nan)
    missing = np.isnan(v)
    if missing.any():
        rows = np.flatnonzero(~missing)
        order = rows[np.argsort(v[rows])]
    else:
        order = np.argsort(v)
    _rank(v, order, missing, False, out)
    return out


def _spearman(
    src: _Source, n: int, tick: Tick | None, threads: int | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[int, int, int, int]]:
    """(r, n, the counts of `_Source.fill`, the numbers of rows) of the average ranks of every pair."""
    k = len(src.columns)
    step = (lambda f: None) if tick is None else tick
    x = np.empty((k, n))
    counts = src.fill(x, 0, n)
    step(0.1)
    present = ~np.isnan(x)
    held = present.sum(axis=0)
    whole = (int(np.count_nonzero(held == k)), int(np.count_nonzero(held == 0)), int(np.count_nonzero(held == 1)),
             int(np.count_nonzero((held > 1) & (held < k))))  # fmt: skip
    workers = thread_count(threads, k)
    if whole[2] + whole[3] == 0:
        # every row is whole or empty: one ranking of a field serves all its pairs
        def rank(j: int) -> None:
            x[j] = average_ranks(x[j])

        run_tasks(list(range(k)), rank, workers, lambda i, _: step(0.1 + 0.5 * (i + 1) / k))
        m = _moments(_Source(list(x), src.weights, None), n, lambda f: step(0.6 + 0.4 * f), threads)[0]
        return m.r, m.n, counts, whole

    orders: list[np.ndarray] = [_NONE] * k
    own = np.full((k, n), np.nan)

    def sort(j: int) -> None:
        rows = np.flatnonzero(present[j])
        orders[j] = rows[np.argsort(x[j][rows])]
        _rank(x[j], orders[j], present[j], False, own[j])

    run_tasks(list(range(k)), sort, workers, lambda i, _: step(0.1 + 0.3 * (i + 1) / k))
    held_by = present.sum(axis=1)
    r, count = np.eye(k), np.diag(held_by).astype(np.int64)
    r[np.diag_indices(k)] = np.where(held_by > 0, 1.0, np.nan)
    pairs = [(i, j) for i in range(k) for j in range(i + 1, k)]

    def pair(ij: tuple[int, int]) -> tuple[int, float]:
        i, j = ij
        both = present[i] & present[j]
        m = int(np.count_nonzero(both))
        if m < MIN_PAIRS:
            return m, float("nan")
        sides = []
        for f in (i, j):
            if m == held_by[f]:
                sides.append(own[f])  # the field has a value in no other row: its own ranks
                continue
            ranks = np.full(n, np.nan)
            _rank(x[f], orders[f], both, True, ranks)
            sides.append(ranks)
        return m, float(_moments(_Source(sides, src.weights, None), n, None, 1)[0].r[0, 1])

    def put(t: int, result: tuple[int, float]) -> None:
        i, j = pairs[t]
        count[i, j] = count[j, i] = result[0]
        r[i, j] = r[j, i] = result[1]
        step(0.4 + 0.6 * (t + 1) / len(pairs))

    run_tasks(pairs, pair, thread_count(threads, len(pairs)), put)
    return r, count, counts, whole


# --- the chart ---------------------------------------------------------------------------------------------


def correlations(
    columns: Sequence[Any], weights: Any = None, spec: CorrSpec | None = None, *, tick: Tick | None = None,
    threads: int | None = None,
) -> Correlation:  # fmt: skip
    """The correlations of k ≥ 2 columns by the rules of `spec` (see the module docstring); `tick(fraction)`
    is called between the blocks from the caller's thread and may raise to cancel."""
    spec = CorrSpec() if spec is None else spec
    cols, w, n = _checked(columns, weights)
    k = len(cols)
    if k < 2:
        raise bad_param("fields", "Для корреляции нужно 2 и больше полей.", "Выберите хотя бы два числовых поля.")
    if spec.method == "spearman":
        r, count, cnt, whole = _spearman(_Source(cols, w, spec, take_log=False), n, tick, threads)
    else:
        m, cnt = _moments(_Source(cols, w, spec), n, tick, threads)
        r, count, whole = m.r, m.n, m.rows
    if n == 0 and tick is not None:
        tick(1.0)
    empty, bad_weight = int(cnt[k, 0]), int(cnt[k, 1])
    used = whole[0] if spec.missing == "complete" else whole[0] + whole[3]
    counts = {"rows": n, "empty": empty, "bad_weight": bad_weight, "incomplete": n - empty - bad_weight - used,
              "used": used}  # fmt: skip
    per_field = [
        {"missing": int(cnt[j, 0]), "negative": int(cnt[j, 1]), "zero_excluded": int(cnt[j, 2]),
         "nonpositive": int(cnt[j, 3]), "used": int(count[j, j])}
        for j in range(k)
    ]  # fmt: skip
    return Correlation(r, count, counts, per_field, spec)


def warm() -> None:
    """Compile the kernels on a few rows (the `stats` step of the warm-up)."""
    a = np.array([1.0, 2.0, 4.0, 3.0, np.nan, 6.0])
    b = np.array([2.0, 1.0, 5.0, np.nan, 4.0, 7.0])
    for method in METHODS:
        correlations([a, b, a + b], np.ones(6), CorrSpec(method=method, log=False), threads=1)
    correlations([a, a * a], None, CorrSpec(log=False), threads=1)
