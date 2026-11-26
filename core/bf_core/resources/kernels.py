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

Passes: `prepare` (the rows that count, their volume, tonnes and bins — one pass instead of a dozen numpy temporaries:
on 20 million rows 1.8 s instead of 3.7 s, ADR 0027 decision 27) and `accumulate` / `accumulate_bench` (the sums).

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
    for k in range(n.shape[1] - 1):
        v = grades[k, i]
        if np.isfinite(v):
            _nadd(s, c, r, 2 + 3 * k, t)
            _nadd(s, c, r, 3 + 3 * k, t * v)
        else:
            n[r, 1 + k] += 1
            _nadd(s, c, r, 4 + 3 * k, t)


@njit(cache=True, nogil=True)
def _bin(edges, g):  # type: ignore[no-untyped-def]
    """`np.searchsorted(edges, g, "right")`: the number of edges ≤ g (g is finite)."""
    lo, hi = 0, edges.shape[0]
    while lo < hi:
        mid = (lo + hi) >> 1
        if edges[mid] <= g:
            lo = mid + 1
        else:
            hi = mid
    return lo


@njit(cache=True, nogil=True)
def prepare(f, vol, vol_c, dens, dens_c, cut, edges, gedges, bin_t, bin_g, veff, tonnes, counts, lost):  # type: ignore[no-untyped-def]
    """One pass over a portion before the sums (what was numpy temporaries): which rows are counted, their volume and
    tonnes, their bins. `f` — the share of the restrictor; `vol` — the volume of each row (length 0: all rows have
    `vol_c`); `dens` — the density of each row (length 0: `dens_c`); `cut` — the cut-off field (length 0: none).
    Out: `bin_t`, `bin_g` (−1 in `bin_t`: the row is not counted), `veff` = vol × f, `tonnes` = veff × density;
    `counts` [3] += (f = 0, no volume, no density), `lost` [2] — the Neumaier sum and carry of the volume of the rows
    with no density. A row with no cut-off value is in the last bin of both (E + 1)."""
    e, eg = edges.shape[0], gedges.shape[0]
    for i in range(f.shape[0]):
        bin_t[i] = -1
        if not f[i] > 0.0:
            counts[0] += 1
            continue
        v = vol[i] if vol.shape[0] > 0 else vol_c
        if np.isnan(v):
            counts[1] += 1
            continue
        ve = v * f[i]
        d = dens[i] if dens.shape[0] > 0 else dens_c
        if not (np.isfinite(d) and d >= 0.0):
            counts[2] += 1
            x = lost[0] + ve
            if abs(lost[0]) >= abs(ve):
                lost[1] += (lost[0] - x) + ve
            else:
                lost[1] += (ve - x) + lost[0]
            lost[0] = x
            continue
        veff[i] = ve
        tonnes[i] = ve * d
        if cut.shape[0] > 0 and np.isfinite(cut[i]):
            bin_t[i] = _bin(edges, cut[i])
            bin_g[i] = _bin(gedges, cut[i])
        else:
            bin_t[i] = e + 1
            bin_g[i] = eg + 1


@njit(cache=True, nogil=True)
def _code(kcols, kmul, radix, i):  # type: ignore[no-untyped-def]
    """The code of the field keys of the row i: Σ digit × stride; the digit is 0 for «(пусто)» (−1, or beyond the
    labels) and code + 1 for a category. A slot with `kmul` 0 is a bench or unused."""
    code = 0
    for a in range(3):
        if kmul[a] != 0:
            d = kcols[a][i] + 1
            if d < 0 or d >= radix[a]:
                d = 0
            code += d * kmul[a]
    return code


@njit(cache=True, nogil=True)
def accumulate(bin_t, bin_g, kcols, kmul, radix, grouped, vol, tonnes, grades, s0, c0, n0, sg, cg, ng, nbg):  # type: ignore[no-untyped-def]
    """One portion, no benches. `bin_t[i]` — the bin of the row among the bins of the total (−1: the row is not
    counted), `bin_g[i]` — its bin among the bins of a group (nbg of them), `kcols` — three int32 columns of the field
    keys (a dummy where `kmul` is 0), `kmul` their strides in the code, `radix` their radixes, `grouped` — the report
    has keys; `vol`, `tonnes` — the counted volume and tonnes; `grades` [k, ≥ n]."""
    for i in range(bin_t.shape[0]):
        b = bin_t[i]
        if b < 0:
            continue
        _add(s0, c0, n0, b, vol[i], tonnes[i], grades, i, 1.0)
        if grouped:
            g = _code(kcols, kmul, radix, i)
            _add(sg, cg, ng, g * nbg + bin_g[i], vol[i], tonnes[i], grades, i, 1.0)


@njit(cache=True, nogil=True)
def accumulate_bench(  # type: ignore[no-untyped-def]
    bin_t,
    bin_g,
    kcols,
    kmul,
    radix,
    bstride,
    nb,
    r0,
    h,
    zlo,
    zhi,
    p,
    nxy,
    cz,
    dz,
    vol,
    tonnes,
    grades,
    s0,
    c0,
    n0,
    sg,
    cg,
    ng,
    nbg,
):
    """One portion with a bench key (a bench is a key, so every counted row has a group). The vertical extent of a
    row: `zlo[i]`, `zhi[i]` (a NaN extent: the whole block in «(пусто)»), or — `zlo` of length 0, a model of whole
    parents on a regular grid — the layer k = P // nxy: [cz + k·dz; cz + (k+1)·dz]. `bstride`, `nb`, `r0`, `h` — the
    bench digit and the benches. The total takes the whole block once."""
    r1 = r0 + nb * h
    for i in range(bin_t.shape[0]):
        b = bin_t[i]
        if b < 0:
            continue
        _add(s0, c0, n0, b, vol[i], tonnes[i], grades, i, 1.0)
        if zlo.shape[0] > 0:
            a, e = zlo[i], zhi[i]
        else:
            k = p[i] // nxy
            a, e = cz + k * dz, cz + (k + 1) * dz
        span = e - a
        base = _code(kcols, kmul, radix, i)
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
        if kept <= 0.0:  # no piece left: keep the block whole in «(пусто)»
            _add(sg, cg, ng, base * nbg + bin_g[i], vol[i], tonnes[i], grades, i, 1.0)
            continue
        if below + above > 0.0:
            _add(sg, cg, ng, base * nbg + bin_g[i], vol[i], tonnes[i], grades, i, (below + above) / kept)
        for j in range(j0, j1 + 1):
            ov = min(e, r0 + (j + 1) * h) - max(a, r0 + j * h)
            if ov > eps:
                _add(sg, cg, ng, (base + (j + 1) * bstride) * nbg + bin_g[i], vol[i], tonnes[i], grades, i, ov / kept)


# --- the bins -------------------------------------------------------------------------------------------------------


def cutoff_bin(edges: np.ndarray, c: float) -> int:
    """The first bin of the blocks with g ≥ c (`c` must be one of `edges`)."""
    return int(np.searchsorted(edges, c, side="left")) + 1


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
    u64 = np.zeros(1, np.uint64)
    kc = (i32, i32, i32)
    kmul, radix = np.zeros(3, np.int64), np.ones(3, np.int64)
    prepare(one, np.empty(0), 1.0, np.empty(0), 2.5, one, one, one, i32.copy(), i32.copy(), one.copy(), one.copy(),
            i64.copy(), np.zeros(2))  # fmt: skip
    args = (z.copy(), z.copy(), n.copy(), z.copy(), z.copy(), n.copy(), 1)
    accumulate(i32, i32, kc, kmul, radix, True, one, one, np.zeros((1, 1)), *args)
    accumulate_bench(i32, i32, kc, kmul, radix, 1, 1, 0.0, 1.0, np.empty(0), np.empty(0), u64, 1, 0.0, 1.0, one, one,
                     np.zeros((1, 1)), *args)  # fmt: skip
    accumulate_bench(i32, i32, kc, kmul, radix, 1, 1, 0.0, 1.0, one, one + 1.0, u64, 1, 0.0, 1.0, one, one,
                     np.zeros((1, 1)), *args)  # fmt: skip
