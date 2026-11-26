"""The kernels of the resource report (подшаг 20.2; ТЗ 8.10, 8.0.1; ADR 0027 decisions 4–7, 12): a generalisation of
`blocks/report_kernels.py` (numba `cache=True, nogil=True`, float64; the cache — bf_core.paths).

Every counted row adds its volume, tonnes and, per grade, the tonnes with a grade, the metal-sum (tonnes × grade) and
the tonnes without a grade (a gap is not a zero) to the bin of its cut-off in two accumulators: the TOTAL (one set of
bins) and its GROUP (a row of `sg` per group × bin). The total is its own accumulator, not the sum of the groups, so
that «Контроль» can compare the two (ADR 0027 decision 5). The sums are Neumaier (sum, carry) accumulators fed in the
order of the rows of the file: the result is the same bit for bit however the model is cut into portions.

Bins of a cut-off field g (`edges` — sorted, unique; E = len(edges)): bin j ∈ [0; E] holds edges[j−1] ≤ g < edges[j]
(`np.searchsorted(edges, g, "right")`), bin E+1 — «нет g_б» (g is NaN, or the report has no cut-off field). So
  * a cut-off c (g ≥ c): the bins [`cutoff_bin(edges, c)`; E+1) — a block with no g is in none of them;
  * «без борта» (c = null, as BF `cutoff=None`): the bins [0; E+2) — every counted block, the ones with no g too;
  * a balance interval c₂ ≤ g < c₁: the bins [cutoff_bin(c₂); cutoff_bin(c₁)).
`range_sums` adds the bins of a range from the top by Neumaier (the same order every time).

Group key. The code of a group is a mixed-radix number: the digit of a key is 0 for «(пусто)» and code + 1 for a
category (radix = labels + 1); a bench key has the digit 0 for the part of a block outside the benches and j + 1 for the
bench j (radix = benches + 1). `gcode[i] < 0` — the row has no group (no keys): only the total is kept.

Benches (decision 7): the bench j is [r0 + j·h; r0 + (j+1)·h], j = 0…nb−1. A block [zlo; zhi] gives every bench the
share overlap / (zhi − zlo) of its volume, tonnes and metal, the part outside the benches goes to the digit 0 (№ 107).
A piece shorter than 1e−12 of the block is dropped and the shares are renormalised, so the shares add up to 1 (a block
whose edge lies on a bench boundary up to rounding is not counted in the next bench). The block COUNT of a group is the
number of blocks that touch it (a block on two benches is counted in both; the total counts it once).

Layout of a row of the accumulators (`Q = 2 + 3·grades`): 0 volume, 1 tonnes, then for the grade k: 2+3k tonnes with
the grade, 3+3k Σ tonnes·grade, 4+3k tonnes without it. The counts `n[r, 0]` — blocks, `n[r, 1+k]` — blocks without
the grade k. The kernels are single-threaded: the cost is the reading of the columns, not the sums.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

SLIVER = 1e-12  # a bench piece shorter than this share of the block is rounding, not a part of it


@njit(cache=True, nogil=True)
def _nadd(s, c, r, q, x):  # type: ignore[no-untyped-def]
    """Neumaier: s[r, q] += x with the lost part kept in c[r, q]."""
    t = s[r, q] + x
    if abs(s[r, q]) >= abs(x):
        c[r, q] += (s[r, q] - t) + x
    else:
        c[r, q] += (x - t) + s[r, q]
    s[r, q] = t


@njit(cache=True, nogil=True)
def _add(s, c, n, r, vol, tonnes, grades, i, sh):  # type: ignore[no-untyped-def]
    """The share `sh` ∈ (0; 1] of the row i into the row r of the accumulators (sh = 1.0 changes no bit)."""
    n[r, 0] += 1
    t = tonnes * sh
    _nadd(s, c, r, 0, vol * sh)
    _nadd(s, c, r, 1, t)
    for k in range(grades.shape[0]):
        v = grades[k, i]
        if np.isfinite(v):
            _nadd(s, c, r, 2 + 3 * k, t)
            _nadd(s, c, r, 3 + 3 * k, t * v)
        else:
            n[r, 1 + k] += 1
            _nadd(s, c, r, 4 + 3 * k, t)


@njit(cache=True, nogil=True)
def accumulate(bin_t, bin_g, gcode, vol, tonnes, grades, s0, c0, n0, sg, cg, ng, nbg):  # type: ignore[no-untyped-def]
    """One portion, no benches. `bin_t[i]` — the bin of the row among the bins of the total (−1: the row is not
    counted), `bin_g[i]` — its bin among the bins of a group (nbg of them), `gcode[i]` — its group (−1: none),
    `vol`, `tonnes` — the counted volume and tonnes (volume × FRAC × f; × density), `grades` [k, n]."""
    for i in range(bin_t.shape[0]):
        b = bin_t[i]
        if b < 0:
            continue
        _add(s0, c0, n0, b, vol[i], tonnes[i], grades, i, 1.0)
        g = gcode[i]
        if g >= 0:
            _add(sg, cg, ng, g * nbg + bin_g[i], vol[i], tonnes[i], grades, i, 1.0)


@njit(cache=True, nogil=True)
def accumulate_bench(  # type: ignore[no-untyped-def]
    bin_t, bin_g, gcode, bstride, nb, r0, h, zlo, zhi, vol, tonnes, grades, s0, c0, n0, sg, cg, ng, nbg
):
    """One portion with a bench key. `gcode` — the code of the other keys (≥ 0: a bench is a key, so every counted row
    has a group), `bstride` — the stride of the bench digit, `nb`, `r0`, `h` — the benches, `zlo`, `zhi` — the vertical
    extent of the row (a NaN extent: the whole block in «(пусто)»). The total takes the whole block once."""
    r1 = r0 + nb * h
    for i in range(bin_t.shape[0]):
        b = bin_t[i]
        if b < 0:
            continue
        _add(s0, c0, n0, b, vol[i], tonnes[i], grades, i, 1.0)
        a, e = zlo[i], zhi[i]
        span = e - a
        base = gcode[i]
        if not (span > 0.0) or not np.isfinite(span):
            _add(sg, cg, ng, base * nbg + bin_g[i], vol[i], tonnes[i], grades, i, 1.0)
            continue
        eps = SLIVER * span
        below = min(e, r0) - a  # outside, under the first bench
        above = e - max(a, r1)  # outside, over the last one
        below = below if below > eps else 0.0
        above = above if above > eps else 0.0
        lo, hi = max(a, r0), min(e, r1)
        j0, j1 = 0, -1
        if hi > lo:
            j0 = max(0, int(np.floor((lo - r0) / h)))
            j1 = min(nb - 1, int(np.ceil((hi - r0) / h)) - 1)
        kept = below + above
        for j in range(j0, j1 + 1):
            ov = min(e, r0 + (j + 1) * h) - max(a, r0 + j * h)
            if ov > eps:
                kept += ov
        if kept <= 0.0:  # no piece left: the block is on no bench and not outside — keep it whole in «(пусто)»
            _add(sg, cg, ng, base * nbg + bin_g[i], vol[i], tonnes[i], grades, i, 1.0)
            continue
        if below + above > 0.0:
            _add(sg, cg, ng, base * nbg + bin_g[i], vol[i], tonnes[i], grades, i, (below + above) / kept)
        for j in range(j0, j1 + 1):
            ov = min(e, r0 + (j + 1) * h) - max(a, r0 + j * h)
            if ov > eps:
                _add(sg, cg, ng, (base + (j + 1) * bstride) * nbg + bin_g[i], vol[i], tonnes[i], grades, i, ov / kept)


@njit(cache=True, nogil=True)
def masked_sum(acc, mask, x):  # type: ignore[no-untyped-def]
    """Neumaier: acc[0] (sum) and acc[1] (carry) += x[i] where mask[i], in the order of the rows."""
    for i in range(mask.shape[0]):
        if mask[i]:
            t = acc[0] + x[i]
            if abs(acc[0]) >= abs(x[i]):
                acc[1] += (acc[0] - t) + x[i]
            else:
                acc[1] += (x[i] - t) + acc[0]
            acc[0] = t


# --- the bins -------------------------------------------------------------------------------------------------------


def cutoff_bin(edges: np.ndarray, c: float) -> int:
    """The first bin of the blocks with g ≥ c (`c` must be one of `edges`)."""
    return int(np.searchsorted(edges, c, side="left")) + 1


def bins_of(edges: np.ndarray, g: np.ndarray | None, n: int) -> np.ndarray:
    """int32 [n]: the bin of each value of g among `edges` (E + 1 — «нет g_б»: NaN, or `g` is None)."""
    e = len(edges)
    if g is None:
        return np.full(n, e + 1, dtype=np.int32)
    return np.where(np.isfinite(g), np.searchsorted(edges, g, side="right"), e + 1).astype(np.int32)


def range_sums(s: np.ndarray, c: np.ndarray, n: np.ndarray, lo: int, hi: int) -> tuple[np.ndarray, np.ndarray]:
    """The sum of the bins [lo; hi) of the accumulators s, c [G, B, Q] and counts n [G, B, K]: floats [G, Q] and counts
    [G, K]. The bins are added from the top by Neumaier (sum + carry of each, then the carry of the run), so the answer
    does not depend on how the portions were cut. An empty range gives zeros."""
    g, _, q = s.shape
    run_s, run_c = np.zeros((g, q)), np.zeros((g, q))
    for j in range(hi - 1, lo - 1, -1):
        for x in (s[:, j, :], c[:, j, :]):
            t = run_s + x
            run_c += np.where(np.abs(run_s) >= np.abs(x), (run_s - t) + x, (x - t) + run_s)
            run_s = t
    return run_s + run_c, n[:, lo:hi, :].sum(axis=1)


def suffix_sums(s: np.ndarray, c: np.ndarray, n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Σ of the bins ≥ j for j = 0…B (the bin B holds nothing): floats [G, B+1, Q], counts [G, B+1, K]. The same
    order of the sums as `range_sums` (from the top), so `suffix[j]` is `range_sums(j, B)` bit for bit: the curve of
    100 cut-offs in one pass. Give it the VALUE bins only (`s[:, :E+1]`): the bin «нет g_б» is in no cut-off."""
    g, b, q = s.shape
    out = np.zeros((g, b + 1, q))
    run_s, run_c = np.zeros((g, q)), np.zeros((g, q))
    for j in range(b - 1, -1, -1):
        for x in (s[:, j, :], c[:, j, :]):
            t = run_s + x
            run_c += np.where(np.abs(run_s) >= np.abs(x), (run_s - t) + x, (x - t) + run_s)
            run_s = t
        out[:, j, :] = run_s + run_c
    counts = np.zeros((g, b + 1, n.shape[2]), dtype=np.int64)
    counts[:, :b, :] = np.cumsum(n[:, ::-1, :], axis=1)[:, ::-1, :]
    return out, counts


def warm() -> None:
    """Compiles the kernels (`resources/warm.py` calls it)."""
    one = np.zeros(1)
    z = np.zeros((1, 5))
    n = np.zeros((1, 2), dtype=np.int64)
    i32 = np.zeros(1, np.int32)
    i64 = np.zeros(1, np.int64)
    args = (z.copy(), z.copy(), n.copy(), z.copy(), z.copy(), n.copy(), 1)
    accumulate(i32, i32, i64, one, one, np.zeros((1, 1)), *args)
    accumulate_bench(i32, i32, i64, 1, 1, 0.0, 1.0, one, one + 1.0, one, one, np.zeros((1, 1)), *args)
    masked_sum(np.zeros(2), np.ones(1, np.bool_), one)
