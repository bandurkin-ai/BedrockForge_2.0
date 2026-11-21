"""Blanks of QAQC (ТЗ 8.4.2; ADR 0020 §4): the status of every blank against k_б·DL, the table of the
laboratories and the carry-over of contamination. Pure functions of numpy over the columns of the blanks of one
element (after the filter, in the order of the rows of the file); no database.

    effective_dl(lab, dl_table, dl_field=None) -> float64[n]
        the DL of every sample: that of its record (`dl:E`) when it is there, else that of its laboratory —
        `dl_table[lab]`, the table of the settings resolved per laboratory by the caller ((element,
        laboratory), else (element, any)) in the units of the element; NaN — no DL (a DL that is not a number
        > 0 is none)
    blanks(x, lab, date, seq, dl, limits=None, below=None) -> Blanks
        x float64[n] (NaN, ±inf — no value); lab int[n] codes ≥ 0; date, seq — as `standards`; dl float64[n]
        (NaN — none: `effective_dl`); below bool[n] — the value is «ниже DL» (`q:E` = 1)
    Blanks — the rows (aligned with the input), the series and the laboratories:
        status int8 (`limits.NORM` 0, `FAIL` 2, `NOT_ASSESSED` −1), limit float64 (k_б·DL; NaN — no DL)
        order int64[m], start int64[g + 1], rows(i) — the series of a laboratory, as `Standards`
        per laboratory, ascending: lab, n (values), missing (samples without a value), dl and group_limit
        (the DL of its blanks and k_б·DL; NaN — none, or the blanks have different DL: `mixed`), norm, fail,
        not_assessed
    carryover(x, prev) -> (n, r)
        the pairs (the previous sample of the batch `b:E`, the blank) where both have a value and Pearson's r
        of them (NaN — fewer than three pairs or no spread)

Rules (ADR 0020 §4). A blank fails when x > k_б·DL — compared as x / DL against k_б with the tolerance of a
threshold (`limits.gt`), so the border is the soft side in any units (ppb or %). A value «ниже DL» is «норма»
whatever the table says: the laboratory itself reports nothing found. Without a DL a blank is «не оценён» —
not «норма». A sample without a value is counted (`missing`) and is in no series. The series of a laboratory
goes by (DATE, SEQ, row of the file), what is empty last.

The carry-over takes the sums of `stats.correlation.pair_moments` (Э13; a numba kernel compiled there — it is
imported by `carryover` only): two passes, compensated, r = 1 exactly on a straight line. 150 thousand blanks —
about 0.02 s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .limits import FAIL, NORM, NOT_ASSESSED, Limits, gt
from .standards import codes_of, group_index, series_order, values_of


@dataclass(frozen=True)
class Blanks:
    """The blanks of one element (see the module docstring)."""

    status: np.ndarray
    limit: np.ndarray
    order: np.ndarray
    start: np.ndarray
    lab: np.ndarray
    n: np.ndarray
    missing: np.ndarray
    dl: np.ndarray
    group_limit: np.ndarray
    mixed: np.ndarray
    norm: np.ndarray
    fail: np.ndarray
    not_assessed: np.ndarray

    def rows(self, group: int) -> np.ndarray:
        """The rows of the series of a laboratory, in its order."""
        return np.asarray(self.order[int(self.start[group]) : int(self.start[group + 1])])


def _usable(dl: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.asarray(np.isfinite(dl) & (dl > 0.0))


def _finite(a: np.ndarray) -> np.ndarray:
    return np.where(np.isfinite(a), a, np.nan)


def effective_dl(lab: Any, dl_table: Any, dl_field: Any = None) -> np.ndarray:
    """The DL of every sample: of its record when given, else of its laboratory; NaN — none."""
    table = values_of(dl_table, name="dl_table")
    codes = np.asarray(lab)
    n = int(codes.shape[0]) if codes.ndim == 1 else -1
    lb = codes_of(codes, n, "lab")
    if n and int(lb.max()) >= table.shape[0]:
        raise ValueError("lab must be codes of the rows of dl_table")
    out = table[lb]
    if dl_field is not None:
        own = values_of(dl_field, n, "dl_field")
        out = np.where(_usable(own), own, out)
    return np.where(_usable(out), out, np.nan)


def blanks(
    x: Any, lab: Any, date: Any, seq: Any, dl: Any, limits: Limits | None = None, *, below: Any = None
) -> Blanks:
    """The blanks of one element: the status of every sample and the table of the laboratories (see the module
    docstring)."""
    lim = limits if limits is not None else Limits()
    xs = values_of(x)
    n = int(xs.shape[0])
    lb = codes_of(lab, n, "lab")
    d = values_of(dl, n, "dl")
    low = np.zeros(n, dtype=np.bool_) if below is None else np.asarray(below, dtype=np.bool_)
    if low.shape != (n,):
        raise ValueError("below must have the length of the rows")

    index, (g_lab,) = group_index(lb)
    g = int(g_lab.shape[0])
    has = np.isfinite(xs)
    order = series_order(index, date, seq, has)
    size = np.bincount(index[order], minlength=g).astype(np.int64, copy=False)
    start = np.concatenate((np.zeros(1, dtype=np.int64), np.cumsum(size)))

    known = _usable(d)
    with np.errstate(all="ignore"):
        limit = _finite(np.where(known, lim.k_blank * d, np.nan))
        over = known & gt(xs / np.where(known, d, 1.0), lim.k_blank)
    status = np.full(n, NOT_ASSESSED, dtype=np.int8)
    status[has & known] = NORM
    status[has & over] = FAIL
    status[has & low] = NORM

    def count(where: np.ndarray) -> np.ndarray:
        return np.bincount(index[where], minlength=g).astype(np.int64, copy=False)

    # the DL of a laboratory: the least and the greatest over its series (NaN — a blank without a DL)
    least, most = np.full(g, np.nan), np.full(g, np.nan)
    filled = size > 0
    if filled.any():
        series_dl = np.where(known, d, np.nan)[order]
        least[filled] = np.fmin.reduceat(series_dl, start[:-1][filled])
        most[filled] = np.fmax.reduceat(series_dl, start[:-1][filled])
    with np.errstate(invalid="ignore"):
        mixed = np.asarray(least < most)
        group_dl = np.where(least == most, least, np.nan)
    with np.errstate(all="ignore"):
        group_limit = _finite(lim.k_blank * group_dl)

    return Blanks(
        status=status,
        limit=limit,
        order=order,
        start=start,
        lab=g_lab,
        n=size,
        missing=count(~has),
        dl=group_dl,
        group_limit=group_limit,
        mixed=mixed,
        norm=count(has & (status == NORM)),
        fail=count(has & (status == FAIL)),
        not_assessed=count(has & (status == NOT_ASSESSED)),
    )


def carryover(x: Any, prev: Any) -> tuple[int, float]:
    """The carry-over of contamination: the number of pairs (previous sample of the batch, blank) with both
    values and Pearson's r of them; NaN — fewer than three pairs or no spread of either."""
    xs = values_of(x)
    ps = values_of(prev, int(xs.shape[0]), "prev")
    if xs.shape[0] == 0:
        return 0, float("nan")
    from bf_core.stats.correlation import pair_moments

    m = pair_moments([ps, xs], None, threads=1)
    return int(m.n[0, 1]), float(m.r[0, 1])
