"""Standards (CRM) of QAQC (ТЗ 8.4.2; ADR 0020 §4): z and the status of every sample, «две подряд», the run on
one side of μ, a swapped CRM, the bias of a group and the moving average of its control chart. A pure function
of numpy over the columns of the CRM samples of one element (after the filter, in the order of the rows of the
file); no numba, no database.

    standards(x, crm, lab, date, seq, mu, sd, limits=None, mismatch=None) -> Standards
        x     float64[n]   the values, NaN (±inf) — no value
        crm   int[n]       the code of the CRM of a sample: a row of `mu`, `sd`
        lab   int[n]       the code of its laboratory, ≥ 0 (the empty laboratory has a code of its own)
        date  float64[n] — milliseconds, NaN — none (`qaqc.records`); datetime64[n] (NaT) and int64[n]
              (`NO_DATE`) are taken too;  seq  float64[n], NaN — none
        mu, sd  float64[k] the certified mean and σ of every CRM in the units of the element; NaN — the CRM is
                           not in the table for this element
        mismatch bool[k]   the units of the CRM cannot be brought to those of the element
    Standards — the rows (aligned with the input), the series and the groups:
        z float64 (NaN — not assessed), status int8 (`limits.NORM` 0, `WARN` 1, `FAIL` 2, `NOT_ASSESSED` −1),
        rule uint8 (bits `RULE_PAIR` 1, `RULE_RUN` 2, `RULE_SWAP` 4), ma float64 (NaN — the first nine of a
        series), run int32 (the length of the run a sample ends; 0 — on μ, not assessed),
        swap int32[n, 3] (the codes of the CRM a failure is near, the nearest first; −1 — none)
        order int64[m]   the rows with a value, group by group, each group in the order of its series
        start int64[g + 1]  the bounds of the groups in `order`;  rows(i) -> order[start[i]:start[i + 1]]
        per group, ascending (crm, lab): crm, lab, state uint8 (`OK`, `NO_REFERENCE`, `UNIT_MISMATCH` —
        `STATES`), n (values), missing (samples without a value), mu, sd (NaN — no reference), mean, std
        (population), bias (%), bias_class int8 (0 «норма», 1 «внимание», 2 «недопустимо», −1 — none),
        norm, warn, fail, pair_fails, run_flags, swaps (samples)
    series_order(group, date, seq, keep) -> int64   the rows `keep` by (group, DATE, SEQ, row)
    group_index(*codes) -> (index[n], [codes of the groups per column])   the groups of rows by codes ≥ 0
    date_key(date) -> int64[n]                       the dates as numbers, `NO_DATE` — none

Rules (ADR 0020 §4; every comparison with a threshold — `limits.le`, `limits.gt`: a border is the soft side).
A group is CRM × laboratory. Its series is the samples with a value by (DATE, SEQ, row of the file), an empty
date and an empty SEQ last; a sample without a value is counted (`missing`) and is not in the series. A CRM
without a usable μ and σ (NaN, σ ≤ 0) or with other units is not assessed: `state`, status −1, z NaN — its mean
and σ of the values are still there.
    z = (x − μ) / σ;  |z| ≤ k₂ — 0;  k₂ < |z| ≤ k₃ — 1;  |z| > k₃ — 2          (k₂, k₃ — sigma_warn, sigma_fail)
    pair: a sample in k₂ < |z| ≤ k₃ whose previous one in the series is there too and on the same side of μ —
          2 (the third in a row as well); the previous one beyond k₃ makes no pair
    run:  the number of samples in a row on one side of μ; a sample on μ (|z| within the tolerance of a
          comparison) is on neither side and breaks it; from the length `run` on the sample has the flag and
          a status not below 1
    swap: |z| > k₃ and |z_j| ≤ k₂ by another CRM j of the table (any, also one without samples) — the flag and
          up to three such j, by |z_j|, then by the code; the status stays 2
    bias = 100·(x̄ − μ) / μ over all the values of the group; |bias| ≤ 5 — 0, ≤ 10 — 1, more — 2
    ma:  the mean of the last 10 values of the series

Numbers. The mean of a group is its first value plus the mean of the differences from it, σ — a second pass
about the mean with the correction of what its rounding left: 1e−12 relative on values far from zero; the
moving average is a sum of ten, not a difference of running totals. A number that overflows float64 is NaN
(not computed), never inf and never 0. Equal inputs give equal bits (stable sorts, sums in the order of the
series). 300 thousand samples in 60 groups — about 0.06 s, 40 MB; the search of the swaps costs the number
of pairs (failure, CRM within k₂·σ of it): 300 thousand failures near 20 CRM each — 0.25 s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .limits import BIAS_ATTENTION, BIAS_OK, FAIL, MA_WINDOW, NORM, NOT_ASSESSED, WARN, Limits, gt, le

RULE_PAIR, RULE_RUN, RULE_SWAP = 1, 2, 4  # the bits of `rule`
SWAPS = 3  # CRM named beside a swap
OK, NO_REFERENCE, UNIT_MISMATCH = 0, 1, 2  # the state of a group
STATES = ("ok", "no_reference", "unit_mismatch")
NO_DATE = int(np.iinfo(np.int64).min)  # the code of NaT
_LATE = int(np.iinfo(np.int64).max)
_DENSE = 1 << 16  # a table of groups of this size is always affordable
_DAY = 86_400_000  # milliseconds: dates without the time of day are whole numbers of it
_PAD = 1e-6  # the search of the near CRM is wider than `le` by this share


@dataclass(frozen=True)
class Standards:
    """The standards of one element (see the module docstring)."""

    z: np.ndarray
    status: np.ndarray
    rule: np.ndarray
    ma: np.ndarray
    run: np.ndarray
    swap: np.ndarray
    order: np.ndarray
    start: np.ndarray
    crm: np.ndarray
    lab: np.ndarray
    state: np.ndarray
    n: np.ndarray
    missing: np.ndarray
    mu: np.ndarray
    sd: np.ndarray
    mean: np.ndarray
    std: np.ndarray
    bias: np.ndarray
    bias_class: np.ndarray
    norm: np.ndarray
    warn: np.ndarray
    fail: np.ndarray
    pair_fails: np.ndarray
    run_flags: np.ndarray
    swaps: np.ndarray

    def rows(self, group: int) -> np.ndarray:
        """The rows of the series of a group, in its order."""
        return np.asarray(self.order[int(self.start[group]) : int(self.start[group + 1])])


# --- columns, groups, the order of a series ------------------------------------------------------------------


def values_of(x: Any, n: int | None = None, name: str = "x") -> np.ndarray:
    """A column of values as float64[n]."""
    out = np.asarray(x, dtype=np.float64)
    if out.ndim != 1 or (n is not None and out.shape[0] != n):
        raise ValueError(f"{name} must be 1D of the length of the rows")
    return out


def codes_of(codes: Any, n: int, name: str) -> np.ndarray:
    """A column of codes as int64[n]; a code is a whole number ≥ 0."""
    out = np.asarray(codes)
    if out.dtype.kind not in "iu":
        raise TypeError(f"{name} must be whole codes")
    if out.ndim != 1 or out.shape[0] != n:
        raise ValueError(f"{name} must be 1D of the length of the rows")
    out = out.astype(np.int64, copy=False)
    if n and int(out.min()) < 0:
        raise ValueError(f"{name} must be codes ≥ 0")
    return out


def date_key(date: Any, n: int | None = None) -> np.ndarray:
    """The dates as int64 in their own unit; `NO_DATE` — no date (NaT of datetime64, NaN of float64 — the
    milliseconds of `qaqc.records`, whole numbers)."""
    d = np.asarray(date)
    if d.dtype.kind not in "Mif":
        raise TypeError("date must be datetime64, float64 or int64")
    if d.ndim != 1 or (n is not None and d.shape[0] != n):
        raise ValueError("date must be 1D of the length of the rows")
    if d.dtype.kind == "f":
        with np.errstate(invalid="ignore"):
            known = np.abs(d) < 9.0e18  # NaN, ±inf and what int64 cannot hold — no date
        return np.where(known, np.where(known, d, 0.0).astype(np.int64), NO_DATE)
    return d.view(np.int64) if d.dtype.kind == "M" else d.astype(np.int64, copy=False)


def group_index(*codes: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
    """The groups of the rows by one or several columns of codes ≥ 0: the number of the group of every row and
    the codes of the groups (an array per column); the groups ascend by the codes, the first column first."""
    n = int(codes[0].shape[0])
    if n == 0:
        return np.zeros(0, dtype=np.int64), [np.zeros(0, dtype=np.int64) for _ in codes]
    spans = [int(c.max()) + 1 for c in codes]
    size = 1
    for s in spans:
        size *= s
    if size > 1 << 62:  # the codes do not fit one key
        table, index = np.unique(np.stack(codes, axis=1), axis=0, return_inverse=True)
        columns = [np.ascontiguousarray(table[:, j]) for j in range(len(codes))]
        return np.asarray(index, dtype=np.int64).reshape(n), columns
    key = codes[0].astype(np.int64, copy=True)
    for c, s in zip(codes[1:], spans[1:], strict=True):
        key *= s
        key += c
    if size <= max(_DENSE, 4 * n):
        present = np.flatnonzero(np.bincount(key, minlength=size))
        lookup = np.zeros(size, dtype=np.int64)
        lookup[present] = np.arange(present.shape[0])
        index = lookup[key]
    else:
        present, index = np.unique(key, return_inverse=True)
        index = np.asarray(index, dtype=np.int64).reshape(n)
    out: list[np.ndarray] = []
    rest = present.astype(np.int64, copy=True)
    for s in reversed(spans[1:]):
        out.append(rest % s)
        rest //= s
    out.append(rest)
    return index, out[::-1]


def _sort_key(a: np.ndarray, known: np.ndarray, step: int = 1) -> np.ndarray:
    """A key that sorts as `a` with what is not `known` last. Values a whole number of `step` apart within a
    range of 2¹⁶ become uint16 — numpy sorts those by counting, many times faster (SEQ, dates without the time
    of day)."""
    whole = bool(known.all())
    v = a if whole else a[known]
    low = v.min()
    exact = a.dtype.kind == "f"
    if float(v.max()) - float(low) < 65535.0 * step:
        apart = v - low
        if bool(np.all(apart == np.rint(apart)) if exact else np.all(apart % step == 0)):
            out = np.full(a.shape, 65535, dtype=np.uint16)
            out[known] = apart if exact else apart // step
            return out
    return a if whole else np.where(known, a, np.inf if exact else _LATE)


def series_order(group: np.ndarray, date: Any, seq: Any, keep: np.ndarray) -> np.ndarray:
    """The rows `keep` in the order of their series: by the group, then (DATE, SEQ, row); an empty date and an
    empty SEQ go last. The sorts are stable — rows equal in everything keep the order of the file."""
    rows = np.flatnonzero(keep)
    if rows.shape[0] == 0:
        return rows
    n = int(group.shape[0])
    keys: list[np.ndarray] = []
    s = values_of(seq, n, "seq")[rows]
    known = np.isfinite(s)
    if known.any():
        keys.append(_sort_key(s, known))
    d = date_key(date, n)[rows]
    if int(d.min()) != int(d.max()):
        keys.append(_sort_key(d, d != NO_DATE, _DAY))
    g = group[rows]
    top = int(g.max())
    if top > 0:
        keys.append(g.astype(np.uint16) if top < 1 << 16 else g)
    turn: np.ndarray | None = None
    for key in keys:  # the least key first, each sort stable (`lexsort` does not sort uint16 by counting)
        by = np.argsort(key if turn is None else key[turn], kind="stable")
        turn = by if turn is None else turn[by]
    return rows if turn is None else rows[turn]


# --- the rules -----------------------------------------------------------------------------------------------


def _swaps(x: np.ndarray, own: np.ndarray, mu: np.ndarray, sd: np.ndarray, usable: np.ndarray, k2: float) -> np.ndarray:
    """The CRM a failure is near: for every value x of a sample of the CRM `own` — up to `SWAPS` other usable
    CRM j with |x − μ_j| / σ_j ≤ k₂, by that distance, then by the code; −1 — none. The values are sorted once;
    a CRM takes the stretch of them between μ_j ± k₂·σ_j (a little wider — the exact test follows) and is put
    into the nearest kept so far of every value there: the work is the number of pairs (value, near CRM)."""
    m = int(x.shape[0])
    out = np.full((m, SWAPS), -1, dtype=np.int32)
    refs = np.flatnonzero(usable)
    if m == 0 or refs.shape[0] == 0:
        return out
    by_x = np.argsort(x, kind="stable")
    xs, own_s = x[by_x], own[by_x]
    half = (k2 * (1.0 + _PAD) + _PAD) * sd[refs]
    lo = np.searchsorted(xs, mu[refs] - half, side="left")
    hi = np.searchsorted(xs, mu[refs] + half, side="right")
    near = np.full((m, SWAPS), np.inf)  # the distances kept, ascending along a row
    code = np.full((m, SWAPS), -1, dtype=np.int32)
    for t in np.flatnonzero(hi > lo):  # the codes ascend: of equal distances the lesser code stays first
        j, a, b = int(refs[t]), int(lo[t]), int(hi[t])
        dist = np.abs((xs[a:b] - mu[j]) / sd[j])
        fits = le(dist, k2) & (own_s[a:b] != j)
        if not fits.any():
            continue
        dist[~fits] = np.inf  # precedes nothing
        kept, names = near[a:b], code[a:b]
        before = [dist < kept[:, col] for col in range(SWAPS)]
        for col in range(SWAPS - 1, -1, -1):
            put = before[col]
            if col:
                np.copyto(kept[:, col], kept[:, col - 1], where=before[col - 1])
                np.copyto(names[:, col], names[:, col - 1], where=before[col - 1])
                put = put & ~before[col - 1]
            np.copyto(kept[:, col], dist, where=put)
            np.copyto(names[:, col], j, where=put)
    out[by_x] = code
    return out


def _moving_average(v: np.ndarray, within: np.ndarray) -> np.ndarray:
    """The mean of the last `MA_WINDOW` values of a series (`within` — the place of a value in its series)."""
    m = int(v.shape[0])
    out = np.full(m, np.nan)
    w = MA_WINDOW
    if m >= w:
        total = v[: m - w + 1].copy()
        with np.errstate(all="ignore"):
            for j in range(1, w):
                total += v[j : m - w + 1 + j]
            out[w - 1 :] = total / w
        out[within < w - 1] = np.nan
    return out


def _finite(a: np.ndarray) -> np.ndarray:
    return np.where(np.isfinite(a), a, np.nan)


def _moments(v: np.ndarray, start: np.ndarray, size: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The mean and the population σ of every group of the values `v` laid group by group (NaN — no values)."""
    g = int(size.shape[0])
    mean, std = np.full(g, np.nan), np.full(g, np.nan)
    filled = size > 0
    if not filled.any():
        return mean, std
    a, cnt = start[:-1][filled], size[filled]
    first = v[a]
    with np.errstate(all="ignore"):
        mean_f = first + np.add.reduceat(v - np.repeat(first, cnt), a) / cnt
        dev = v - np.repeat(mean_f, cnt)
        s1, s2 = np.add.reduceat(dev, a), np.add.reduceat(dev * dev, a)
        mean[filled] = _finite(mean_f)
        std[filled] = _finite(np.sqrt(np.maximum(s2 - s1 * s1 / cnt, 0.0) / cnt))
    return mean, std


def _count(group: np.ndarray, where: np.ndarray, g: int) -> np.ndarray:
    return np.bincount(group[where], minlength=g).astype(np.int64, copy=False)


def standards(
    x: Any, crm: Any, lab: Any, date: Any, seq: Any, mu: Any, sd: Any, limits: Limits | None = None, *,
    mismatch: Any = None,
) -> Standards:  # fmt: skip
    """The standards of one element: the status of every sample and the table of the groups CRM × laboratory
    (see the module docstring)."""
    lim = limits if limits is not None else Limits()
    xs = values_of(x)
    n = int(xs.shape[0])
    c, lb = codes_of(crm, n, "crm"), codes_of(lab, n, "lab")
    ref_mu = values_of(mu, name="mu")
    k = int(ref_mu.shape[0])
    ref_sd = values_of(sd, k, "sd")
    if n and int(c.max()) >= k:
        raise ValueError("crm must be codes of the rows of mu and sd")
    other_units = np.zeros(k, dtype=np.bool_) if mismatch is None else np.asarray(mismatch, dtype=np.bool_)
    if other_units.shape != (k,):
        raise ValueError("mismatch must have the length of mu")
    with np.errstate(invalid="ignore"):
        usable = np.isfinite(ref_mu) & np.isfinite(ref_sd) & (ref_sd > 0.0) & ~other_units

    index, (g_crm, g_lab) = group_index(c, lb)
    g = int(g_crm.shape[0])
    has = np.isfinite(xs)
    order = series_order(index, date, seq, has)
    m = int(order.shape[0])
    of = index[order]  # the group of every value of the series
    size = np.bincount(of, minlength=g).astype(np.int64, copy=False)
    start = np.concatenate((np.zeros(1, dtype=np.int64), np.cumsum(size)))
    v, code = xs[order], c[order]
    ok = usable[code]

    with np.errstate(all="ignore"):
        z = np.where(ok, (v - ref_mu[code]) / ref_sd[code], np.nan)
        beyond = gt(np.abs(z), lim.sigma_fail)
        band = gt(np.abs(z), lim.sigma_warn) & ~beyond
        side = gt(z, 0.0).astype(np.int8) - gt(-z, 0.0).astype(np.int8)
    head = np.zeros(m, dtype=np.bool_)
    head[start[:-1][size > 0]] = True
    same = np.zeros(m, dtype=np.bool_)  # the previous value of the series is on the same side
    same[1:] = (side[1:] == side[:-1]) & ~head[1:]
    pair = band & same
    pair[1:] &= band[:-1]
    place = np.arange(m, dtype=np.int64)
    run = place - np.maximum.accumulate(np.where(same, 0, place)) + 1
    run[side == 0] = 0
    flagged = run >= lim.run

    status = np.full(m, NOT_ASSESSED, dtype=np.int8)
    status[ok] = NORM
    status[band] = WARN
    status[beyond | pair] = FAIL
    status[flagged & (status == NORM)] = WARN
    rule = (pair * RULE_PAIR + flagged * RULE_RUN).astype(np.uint8)
    swap = np.full((m, SWAPS), -1, dtype=np.int32)
    with np.errstate(all="ignore"):
        swap[beyond] = _swaps(v[beyond], code[beyond], ref_mu, ref_sd, usable, lim.sigma_warn)
    swapped = swap[:, 0] >= 0
    rule[swapped] |= RULE_SWAP

    within = place - np.repeat(start[:-1], size)
    mean, std = _moments(v, start, size)
    g_ok = usable[g_crm]
    g_mu, g_sd = np.where(g_ok, ref_mu[g_crm], np.nan), np.where(g_ok, ref_sd[g_crm], np.nan)
    with np.errstate(all="ignore"):
        bias = _finite(100.0 * (mean - g_mu) / g_mu)
        bias_class = np.where(le(np.abs(bias), BIAS_OK), NORM, np.where(le(np.abs(bias), BIAS_ATTENTION), WARN, FAIL))
    bias_class = np.where(np.isnan(bias), NOT_ASSESSED, bias_class).astype(np.int8)
    state = np.where(g_ok, OK, np.where(other_units[g_crm], UNIT_MISMATCH, NO_REFERENCE)).astype(np.uint8)

    def spread(values: np.ndarray, fill: Any, dtype: Any) -> np.ndarray:
        out = np.full((n, *values.shape[1:]), fill, dtype=dtype)
        out[order] = values
        return out

    return Standards(
        z=spread(_finite(z), np.nan, np.float64),
        status=spread(status, NOT_ASSESSED, np.int8),
        rule=spread(rule, 0, np.uint8),
        ma=spread(_finite(_moving_average(v, within)), np.nan, np.float64),
        run=spread(run, 0, np.int32),
        swap=spread(swap, -1, np.int32),
        order=order,
        start=start,
        crm=g_crm,
        lab=g_lab,
        state=state,
        n=size,
        missing=_count(index, ~has, g),
        mu=g_mu,
        sd=g_sd,
        mean=mean,
        std=std,
        bias=bias,
        bias_class=bias_class,
        norm=_count(of, status == NORM, g),
        warn=_count(of, status == WARN, g),
        fail=_count(of, status == FAIL, g),
        pair_fails=_count(of, pair, g),
        run_flags=_count(of, flagged, g),
        swaps=_count(of, swapped, g),
    )
