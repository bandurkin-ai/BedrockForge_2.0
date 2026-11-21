"""Samples by domains (ТЗ 8.3; ADR 0019 §4): the rows of a selection split by the codes of a text field and
sorted once — what the box plot, the log-probability plot and this table of statistics by domains share.

    group_samples(selection, codes=None, labels=None, tick=None) -> Groups
        one `weighted.sort_groups` for all the domains. Groups: groups [Group(domain, role, sample: Sorted)] —
        the domains that have rows, in the order of `labels`, then the rows without a domain (role "none");
        index (int64 [n of the selection] — the group of every row), counts (those of the selection +
        `no_domain`). Without `codes` — one group of all the rows (role "all").
    group_sums(values, weights, index, n) -> (Σ w, Σ w·v) [n]      compensated, rows without a value or a
                                                                    weight > 0 take no part
    domain_stats(selection, codes=None, labels=None, length=None, decl_weights=None, tick=None) -> dict
        the answer of `POST /stats/domains` but the names:
        {weighted, counts, rows [{domain, role, count, length, weight_sum, mean, declustered_mean, median, std,
         cv, min, max, p10, p50, p90}], total {the same of all the rows}, declustered, notes, conventions}
    warm()

Domains. `codes` — the codes of the domain field for every row the selection was made of (`stats.source`:
`Columns.text` or `domain_at`; −1 — no domain), `labels` — the names of the codes. A row whose code is outside
0 … len(labels) − 1 has no domain: such rows make a group of their own (domain None, role "none") so that the
numbers of the rows of the groups add up to the rows taken (Σ n = `counts.used`). Domains without rows are not
shown. More than MAX_DOMAINS (300) domains with rows — `too_many_domains`.

The table: n; the length — Σ of the lengths of the intervals (`length`, None without it); the mean with the
weights of the selection; the declustered mean — Σ d·c / Σ d with the weights d of the field `DECL_W`
(`decl_weights`; rows without it take no part; None without the field — `notes` says how to get it); the
median, σ (the population one), CV = σ / mean (None for a mean ≤ 0), min, max, P10, P50, P90 — by the rule of
`weighted_percentiles`. `total` — the same of all the rows taken, from a sort of its own (in a second thread
for ≥ 200 thousand rows — every sort is computed alone, the bits do not depend on the threads). What is not
defined is None, never 0.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.jit import njit

from .select import Selection
from .weighted import Sorted, StatsError, Tick, sort_groups

MAX_DOMAINS = 300  # as many values of a text field as `GET /stats/fields` lists
PARALLEL_FROM = 200_000  # rows from which the sort of the total goes in a second thread
PERCENTILES = (10.0, 50.0, 90.0)
DECL_FIELD = "DECL_W"
NO_DECL = (
    f"Декластерированное среднее не посчитано: в таблице нет поля {DECL_FIELD}. "
    "Выполните «Обработка проб → Декластеризация»."
)
CONVENTIONS = (
    "Процентили по GSLIB: середина накопленного веса, линейная интерполяция. Дисперсия — генеральная. "
    f"Декластерированное среднее — с весами поля {DECL_FIELD}."
)


@dataclass(frozen=True)
class Group:
    """The rows of one domain, sorted."""

    domain: str | None  # the name; None — the rows without a domain, or all the rows
    role: str  # domain | none | all
    sample: Sorted

    def head(self) -> dict[str, Any]:
        return {"domain": self.domain, "role": self.role}


@dataclass(frozen=True)
class Groups:
    """The rows of a selection by domains (see the module docstring)."""

    groups: list[Group]
    index: np.ndarray  # int64: the position in `groups` of every row of the selection
    counts: dict[str, int]


def too_many_domains(found: int) -> StatsError:
    return StatsError(
        "too_many_domains", f"В поле доменов слишком много значений ({found}, максимум {MAX_DOMAINS}).",
        "Выберите поле доменов с меньшим числом значений (литология, зона, рудное тело).", param="domain",
    )  # fmt: skip


def group_samples(selection: Selection, codes: Any = None, labels: Any = None, *, tick: Tick | None = None) -> Groups:
    """The rows of `selection` split by the codes of the domains and sorted once (see the module docstring)."""
    selection.require()
    n = selection.n
    if codes is None:
        sample = sort_groups(selection.values, selection.weights, tick=tick)[0]
        return Groups([Group(None, "all", sample)], np.zeros(n, dtype=np.int64), {**selection.counts, "no_domain": 0})
    names = [str(name) for name in ([] if labels is None else labels)]
    k = len(names)
    c = np.asarray(codes)[selection.rows].astype(np.int64)
    c[(c < 0) | (c >= k)] = k  # the rows without a domain — the last group
    held = np.bincount(c, minlength=k + 1)
    found = int(np.count_nonzero(held[:k]))
    if found > MAX_DOMAINS:
        raise too_many_domains(found)
    samples = sort_groups(selection.values, selection.weights, c, k + 1, tick=tick)
    position = np.full(k + 1, -1, dtype=np.int64)
    groups: list[Group] = []
    for g in range(k + 1):
        if held[g]:
            position[g] = len(groups)
            groups.append(Group(names[g], "domain", samples[g]) if g < k else Group(None, "none", samples[g]))
    return Groups(groups, position[c], {**selection.counts, "no_domain": int(held[k])})


@njit(cache=True, nogil=True)
def _sums(v, w, index, n, out):  # type: ignore[no-untyped-def]
    """out[0, g] = Σ w, out[1, g] = Σ w·v, out[2, g] = rows — over the rows of group g that have a value and
    a weight > 0; compensated (Neumaier), in the order of the rows."""
    comp = np.zeros((2, n))
    for i in range(v.shape[0]):
        x = v[i]
        y = w[i]
        if not (np.isfinite(x) and np.isfinite(y) and y > 0.0):
            continue
        g = index[i]
        out[2, g] += 1.0
        for j in range(2):
            t = out[j, g] + y
            if abs(out[j, g]) >= abs(y):
                comp[j, g] += (out[j, g] - t) + y
            else:
                comp[j, g] += (y - t) + out[j, g]
            out[j, g] = t
            y *= x
    for j in range(2):
        for g in range(n):
            out[j, g] += comp[j, g]


def group_sums(values: Any, weights: Any, index: Any, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(Σ w, Σ w·v, the number of rows) of every group 0 … n − 1 (`index` — the group of a row)."""
    out = np.zeros((3, n))
    _sums(np.ascontiguousarray(values, dtype=np.float64), np.ascontiguousarray(weights, dtype=np.float64),
          np.ascontiguousarray(index, dtype=np.int64), n, out)  # fmt: skip
    return out[0], out[1], out[2].astype(np.int64)


def _num(x: float) -> float | None:
    x = float(x)
    return x if math.isfinite(x) else None


def _row(head: dict[str, Any], s: Sorted, length: float | None, declustered: float | None) -> dict[str, Any]:
    p10, p50, p90 = (float(q) for q in s.percentiles(PERCENTILES))
    mean, std = s.mean, math.sqrt(s.variance)
    return {
        **head,
        "count": s.n,
        "length": length,
        "weight_sum": _num(s.total),
        "mean": _num(mean),
        "declustered_mean": declustered,
        "median": _num(p50),
        "std": _num(std),
        "cv": _num(std / mean) if mean > 0.0 else None,
        "min": _num(s.vmin),
        "max": _num(s.vmax),
        "p10": _num(p10),
        "p50": _num(p50),
        "p90": _num(p90),
    }


def domain_stats(
    selection: Selection, codes: Any = None, labels: Any = None, *, length: Any = None, decl_weights: Any = None,
    tick: Tick | None = None, parallel_from: int = PARALLEL_FROM,
) -> dict[str, Any]:  # fmt: skip
    """The table of statistics by domains for the rows of `selection` (see the module docstring). `codes`,
    `length` (the lengths of the intervals) and `decl_weights` (the field `DECL_W`) are columns of the rows the
    selection was made of — `selection.rows` index them."""
    step = (lambda f: None) if tick is None else tick
    selection.require()
    total: Sorted | None = None
    if codes is None:
        found = group_samples(selection, tick=lambda f: step(0.8 * f))
        total = found.groups[0].sample
    elif selection.n >= parallel_from:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="bf-domains") as pool:
            other = pool.submit(sort_groups, selection.values, selection.weights)
            found = group_samples(selection, codes, labels)
            total = other.result()[0]
    else:
        found = group_samples(selection, codes, labels, tick=lambda f: step(0.5 * f))
        total = sort_groups(selection.values, selection.weights, tick=lambda f: step(0.5 + 0.3 * f))[0]
    step(0.8)
    k = len(found.groups)
    lengths: list[float | None] = [None] * (k + 1)
    if length is not None:
        col = np.asarray(length, dtype=np.float64)[selection.rows]
        sums, _, rows = group_sums(np.ones(col.shape), col, found.index, k)
        lengths = [float(s) if r else None for s, r in zip(sums, rows, strict=True)]
        lengths.append(math.fsum(s for s, r in zip(sums, rows, strict=True) if r) if rows.any() else None)
    means: list[float | None] = [None] * (k + 1)
    with_decl = decl_weights is not None
    if with_decl:
        d = np.asarray(decl_weights, dtype=np.float64)[selection.rows]
        sw, swv, rows = group_sums(selection.values, d, found.index, k)
        means = [_num(m / w) if r else None for w, m, r in zip(sw, swv, rows, strict=True)]
        means.append(_num(math.fsum(swv) / math.fsum(sw)) if rows.any() else None)
    step(0.9)
    out = {
        "weighted": selection.weighted,
        "counts": found.counts,
        "rows": [_row(g.head(), g.sample, lengths[i], means[i]) for i, g in enumerate(found.groups)],
        "total": _row({"domain": None, "role": "all"}, total, lengths[k], means[k]),
        "declustered": with_decl,
        "notes": [] if with_decl else [NO_DECL],
        "conventions": CONVENTIONS,
    }
    step(1.0)
    return out


def warm() -> None:
    """Compile the kernels on a few rows (the `stats` step of the warm-up)."""
    from .select import select_values

    rows = np.array([1.0, 3.0, 2.0, 5.0])
    domain_stats(select_values(rows, rows), np.array([0, 1, 0, -1]), ["a", "b"], length=rows, decl_weights=rows)
