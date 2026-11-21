"""Pairs of QAQC (ТЗ 8.4.2; ADR 0020 §4): the duplicates (x1 — the original sample, x2 — the duplicate) and the
pairs of the external control (x1 — the main laboratory, x2 — the control one) — which pairs are taken, HARD,
HRD, AMPD, the ranked HARD with the criterion and the scatter. Pure functions of numpy over the columns of the
pairs of one type and one element (after the filter, in the order of the rows of the file); no database.

    select(x1, x2, dl=None, k_dup=5, no_parent=None) -> Selection
        x1, x2 float64[n] (NaN — no value); dl float64[n] — the DL of the laboratory of the second sample (NaN —
        none: `blanks.effective_dl`); no_parent bool[n] — the first sample was not found at the import
        Selection: used bool[n]; excluded {no_parent, missing, negative, zero, below} — each pair under the
        first reason that holds; no_dl — the pairs taken without a DL (the screening was not applied to them)
    measure(x1, x2, limit, share_limit=90) -> Pairs          the pairs that are taken:
        hard, hrd float64[n] (%; AMPD = 2·HARD), share10, share20, share30, share_at_limit (% of the pairs
        with HARD ≤ 10, 20, 30 and `limit`; NaN — no pairs), passed (share_at_limit ≥ share_limit; None — no
        pairs), ranked_p, ranked_hard (HARD ascending against 100·i / n; at most `RANKED_POINTS` of them)
    scatter_stats(x1, x2) -> {mean1, mean2, pearson, rma {a, b}}      None — not defined
    hard_limit(kind, limits) -> float      the criterion of a type: DUP_P `hard_p`, DUP_C `hard_c`, DUP_F
                                           `hard_f`, UMP `hard_ump`

Rules (ADR 0020 §4; comparisons with a threshold — `limits.le`, `limits.gt`: a border is the soft side).
    HARD = 100·|x1 − x2| / (x1 + x2);  HRD = 100·(x1 − x2) / (x1 + x2);  AMPD = 2·HARD
    a pair is taken when both values are there, none is negative, x1 + x2 > 0 and the mean m = (x1 + x2) / 2
    is not below k_д·DL (m / DL against k_д — the same in any units); without a DL the screening is not applied
    the criterion of a type holds when the share of the pairs with HARD ≤ its limit is ≥ `hard_share`
    the ranked chart: i = 1 … n; of more than 2000 pairs — 2000 evenly by the rank, the first and the last
    among them; the shares are of all the pairs
    the scatter: the moments of the pairs, r and RMA (y = a + b·x, x — the first sample) of `stats.scatter`
    (Э13; its numba kernels are compiled there — `scatter_stats` alone imports them): y = x gives b = 1, a = 0
150 thousand pairs — about 0.03 s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .limits import Limits, gt, le
from .standards import values_of

EXCLUDED = ("no_parent", "missing", "negative", "zero", "below")
RANKED_POINTS = 2000
HARD_KEYS = {"DUP_F": "hard_f", "DUP_C": "hard_c", "DUP_P": "hard_p", "UMP": "hard_ump"}
SHARES = (10.0, 20.0, 30.0)


@dataclass(frozen=True)
class Selection:
    """Which pairs are taken (see the module docstring)."""

    used: np.ndarray
    excluded: dict[str, int]
    no_dl: int


@dataclass(frozen=True)
class Pairs:
    """HARD of the pairs taken and what follows from it (see the module docstring)."""

    hard: np.ndarray
    hrd: np.ndarray
    share10: float
    share20: float
    share30: float
    share_at_limit: float
    passed: bool | None
    ranked_p: np.ndarray
    ranked_hard: np.ndarray


def hard_limit(kind: str, limits: Limits) -> float:
    """The limit of HARD of a type of pairs, %."""
    return float(getattr(limits, HARD_KEYS[kind]))


def select(x1: Any, x2: Any, dl: Any = None, k_dup: float = 5.0, *, no_parent: Any = None) -> Selection:
    """The pairs that go into the calculation and the counts of those left out, each under its first reason."""
    a = values_of(x1, name="x1")
    n = int(a.shape[0])
    b = values_of(x2, n, "x2")
    left = np.zeros(n, dtype=np.bool_) if no_parent is None else np.asarray(no_parent, dtype=np.bool_)
    if left.shape != (n,):
        raise ValueError("no_parent must have the length of the pairs")
    limit = np.full(n, np.nan) if dl is None else values_of(dl, n, "dl")
    with np.errstate(all="ignore"):
        total = a + b
        missing = ~left & ~(np.isfinite(a) & np.isfinite(b) & np.isfinite(total))
        ok = ~left & ~missing
        negative = ok & ((a < 0.0) | (b < 0.0))
        ok &= ~negative
        zero = ok & (total == 0.0)
        ok &= ~zero
        known = np.isfinite(limit) & (limit > 0.0)
        below = ok & known & gt(k_dup, 0.5 * total / np.where(known, limit, 1.0))
        ok &= ~below
    counts = (left, missing, negative, zero, below)
    excluded = {name: int(np.count_nonzero(c)) for name, c in zip(EXCLUDED, counts, strict=True)}
    return Selection(ok, excluded, int(np.count_nonzero(ok & ~known)))


def measure(x1: Any, x2: Any, limit: float, share_limit: float = 90.0) -> Pairs:
    """HARD and HRD of the pairs taken (both values ≥ 0, x1 + x2 > 0), the shares within the limits, the
    criterion and the points of the ranked chart."""
    a = values_of(x1, name="x1")
    n = int(a.shape[0])
    b = values_of(x2, n, "x2")
    with np.errstate(all="ignore"):
        total = a + b
        hrd = 100.0 * (a - b) / total
    hard = np.abs(hrd)
    if n == 0:
        nan = float("nan")
        return Pairs(hard, hrd, nan, nan, nan, nan, None, np.zeros(0), np.zeros(0))
    share = [100.0 * int(np.count_nonzero(le(hard, t))) / n for t in (*SHARES, limit)]
    order = np.sort(hard)
    rank = np.arange(n, dtype=np.int64)
    if n > RANKED_POINTS:
        rank = np.round(np.linspace(0.0, n - 1.0, RANKED_POINTS)).astype(np.int64)
    return Pairs(hard, hrd, share[0], share[1], share[2], share[3], bool(le(share_limit, share[3])),
                 100.0 * (rank + 1) / n, order[rank])  # fmt: skip


def scatter_stats(x1: Any, x2: Any) -> dict[str, Any]:
    """The means, Pearson's r and the RMA line y = a + b·x of the pairs (x — the first sample)."""
    from bf_core.stats.scatter import pairs, regressions

    fit = regressions(pairs(values_of(x1, name="x1"), values_of(x2, name="x2")))
    rma = fit["rma"] or {"a": None, "b": None}
    return {"mean1": fit["x"]["mean"], "mean2": fit["y"]["mean"], "pearson": fit["pearson"],
            "rma": {"a": rma["a"], "b": rma["b"]}}  # fmt: skip
