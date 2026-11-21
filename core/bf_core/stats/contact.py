"""The contact analysis (ТЗ 8.3; ADR 0019 §4): how a grade behaves on both sides of the contact of two domains
along the holes — the mean and the number of samples by steps of the distance from the contact. A step line of
the means is a hard boundary, a slope — a soft one (ТЗ 8.8.2). BF has no such chart: written anew.

    contact_chart(hole, frm, to, codes, labels, a, b, values, flt=None, length=None, weight_values=None,
                  distance=20, step=1, eps=1e−6, threads=1, tick=None) -> dict
        the answer of `POST /stats/contact` but `set_id`, `field`, `domain`, `wireframe_id`:
        {distance, step, weighted, counts, contacts, holes, edges, mean, n, a {label, n, mean},
         b {label, n, mean}, conventions}
        `hole`, `frm`, `to`, `codes`, `values`, `length`, `weight_values` — columns of the rows of one table in
        any order (`stats.source.Columns`: `hole_of()`, `from_m`, `to_m`, `text(domain)` or `domain_at`,
        `select_args(field, weight_field)`); `labels` — the names of the codes, `a`, `b` — two of them
    steps_of(distance, step) -> Steps(distance, step, n_side, edges)
    domain_contacts(lines, codes, code_a, code_b, reach, eps=1e−6, threads=1, tick=None) -> Contacts
        Contacts: dist (float64 [n], the distance of the middle of a sample from its contact), side (int8: 0 — A,
        1 — B, −1 — the sample is at no contact), contacts, holes; `.signed()` — the distance with the sign of
        the side (A < 0 < B), NaN — none
    warm()

The rule (ADR 0019 §4). The rows of a hole go by depth (`composites.intervals.build_lines`: rows without a
depth, with TO ≤ FROM or overlapping the row before them take no part and are counted). A run is a stretch of
consecutive samples of one domain without a gap wider than `eps` (`intervals.runs`). A contact is the common
boundary of two adjacent runs, one of domain A and the other of B, in either order down the hole; across a gap
in sampling, a sample without a domain or a run of a third domain there is no contact. A sample belongs to a
contact through its run: its distance is that of its middle from the end of its own run where the contact is —
from the nearer one when the run has a contact at both ends, so a sample is taken once and never from a contact
beyond its run. Samples of A have d < 0, of B — d > 0; those with |d| ≤ D (`distance`, 20 m) are taken.

Steps. Step k of a side holds the samples with k·b ≤ |d| < (k + 1)·b (`step` b, 1 m), so the two sides are
mirrors of each other: asking for B, A gives the steps of A, B in the reverse order, bit for bit. A middle within
`eps` of an edge of a step is on it (the float noise of depths: 1 m samples and steps of 0.5 m put every middle
on an edge) and belongs to the step that starts there. When D is not a whole number of steps the outermost step
is shorter and ends at D. `edges` — the 2·n + 1 edges from −D to D, n = ⌈D / b⌉ ≤ MAX_STEPS a side; `mean[i]`,
`n[i]` — of the step between edges i and i + 1 (side A — the steps below 0).

Values. The rows go through `stats.select` (the weight — the length by default, the cuts, the rule for values
below zero) after the runs are found: a sample without a value keeps its place in its run and is not counted.
The mean of a step is Σ w·c / Σ w, taken as c₀ + Σ w·(c − c₀) / Σ w about the first value c₀ of the step with
compensated sums in the order of the rows by (hole, FROM) — exact when the grade is constant (Т8.3-2: 1.0 and
3.0, not 2.9999999999999996) and the same bits whatever the order of the rows given and the number of threads.
A step without samples has no mean (None, never 0). No contact found is an answer with `contacts` 0, not a
refusal. `counts` — those of the selection of the rows with a depth (`rows` — all the rows given) and
`no_depth`, `overlap` (rows left out of the lines), `no_domain` (rows taken without a domain code), `in_steps`.
Refusals — StatsError `bad_param` {param: a, b, distance, step, …}, `no_values`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Any

import numpy as np

from bf_core.jit import njit

from .select import SampleFilter, Selection, bad_param, select_sample
from .weighted import Tick

DISTANCE = 20.0  # m: D, how far from a contact the samples are taken
STEP = 1.0  # m: b
MAX_STEPS = 500  # steps on a side
DEPTH_EPS = 1e-6  # m: `composites.intervals.DEPTH_EPS` — depths closer than this are one depth
_BLOCK = 1 << 20  # rows between two calls of `tick`
CONVENTIONS = (
    "Контакт — граница смежных участков доменов A и B вдоль ствола (зазор не больше 1e−6 м); через пропуск "
    "опробования контакта нет. Проба относится к ближайшему контакту своего участка, расстояние — от середины "
    "пробы по стволу: сторона A — меньше нуля, B — больше. Среднее шага — с весами отбора; шаг без проб пуст."
)


# --- the steps ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Steps:
    """The steps of a chart: `n_side` steps of `step` m on each side of the contact, up to `distance` m."""

    distance: float
    step: float
    n_side: int
    edges: list[float]  # 2·n_side + 1: −D … 0 … D


def _positive(key: str, title: str, value: Any, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value <= 0:
        raise bad_param(key, f"{title} — число больше нуля.", f"Задайте его в метрах, например {default:g}.")
    return float(value)


def steps_of(distance: Any = None, step: Any = None) -> Steps:
    """The steps for D = `distance` and b = `step` (None — 20 m and 1 m). StatsError `bad_param`."""
    d = _positive("distance", "Расстояние D", distance, DISTANCE)
    b = _positive("step", "Шаг b", step, STEP)
    ratio = d / b
    if not ratio <= MAX_STEPS + 1e-9:
        count = f"{math.ceil(ratio - 1e-9):d}" if ratio < 1e15 else "слишком много"
        raise bad_param(
            "step", f"Шагов на сторону — {count}, допустимо не больше {MAX_STEPS}.",
            "Увеличьте шаг b или уменьшите расстояние D.",
        )  # fmt: skip
    n = max(1, math.ceil(ratio - 1e-9))
    inner = [k * b for k in range(1, n)]
    return Steps(d, b, n, [-d, *(-x for x in reversed(inner)), 0.0, *inner, d])


# --- the distance of every sample from its contact ---------------------------------------------------------


@dataclass(frozen=True)
class Contacts:
    """The samples at the contacts, rows in the order of the lines (see the module docstring)."""

    dist: np.ndarray  # float64: |d| of the middle of the sample; defined where side ≥ 0
    side: np.ndarray  # int8: 0 — side A (d < 0), 1 — side B (d > 0), −1 — at no contact within the reach
    contacts: int
    holes: int  # holes that have a contact

    def signed(self) -> np.ndarray:
        """d of every row: negative on side A, positive on side B, NaN — the row is at no contact."""
        return np.where(self.side == 0, -self.dist, np.where(self.side == 1, self.dist, np.nan))  # type: ignore[no-any-return]


@njit(cache=True, nogil=True)
def _domain_strip(h0, h1, r_starts, r_frm, r_to, r_code, first, last, frm, to, code_a, code_b, gap, reach, dist, side):  # type: ignore[no-untyped-def]
    """Holes [h0, h1): the runs r_starts[h] : r_starts[h + 1] of every hole, rows first[k] : last[k] of run k.
    Writes `dist` and `side` of their rows; returns (contacts, holes with a contact)."""
    contacts = 0
    holes = 0
    for h in range(h0, h1):
        k0 = r_starts[h]
        k1 = r_starts[h + 1]
        found = 0
        for k in range(k0, k1):
            c = r_code[k]
            s = -1
            if c == code_a:
                s = 0
            elif c == code_b:
                s = 1
            top = False
            bottom = False
            if s >= 0:
                other = code_b if s == 0 else code_a
                top = k > k0 and r_code[k - 1] == other and r_frm[k] - r_to[k - 1] <= gap
                bottom = k + 1 < k1 and r_code[k + 1] == other and r_frm[k + 1] - r_to[k] <= gap
                if top:
                    found += 1  # a contact is counted once, at the run below it
            for i in range(first[k], last[k]):
                side[i] = -1
                dist[i] = np.nan
                if not (top or bottom):
                    continue
                mid = 0.5 * (frm[i] + to[i])
                d = np.inf
                if top:
                    d = mid - r_frm[k]
                if bottom and r_to[k] - mid < d:
                    d = r_to[k] - mid
                if d <= reach:
                    dist[i] = d
                    side[i] = s
        contacts += found
        if found:
            holes += 1
    return contacts, holes


def _part(tick: Tick | None, lo: float, hi: float) -> Tick | None:
    """`tick` of a part of the work: its fraction 0 … 1 mapped into lo … hi of the whole."""
    return None if tick is None else (lambda f: tick(lo + (hi - lo) * f))


def domain_contacts(
    lines: Any, codes: Any, code_a: int, code_b: int, reach: float, *, eps: float = DEPTH_EPS, threads: int = 1,
    tick: Tick | None = None,
) -> Contacts:  # fmt: skip
    """The contacts of the domains `code_a` | `code_b` along the holes of `lines` (`intervals.Lines`) and, for
    every row, the distance of its middle from the contact of its run and its side; `codes` — int32 in line
    order (−1 — no domain); rows farther than `reach` (+ `eps`) are at no contact."""
    from bf_core.composites import intervals as iv
    from bf_core.composites import strips

    r = iv.runs(lines, codes, gap=eps, threads=threads, tick=_part(tick, 0.0, 0.5))
    dist = np.empty(lines.n, dtype=np.float64)
    side = np.empty(lines.n, dtype=np.int8)
    args = (r.lines.starts, r.lines.frm, r.lines.to, r.code, r.first, r.last, lines.frm, lines.to,
            int(code_a), int(code_b), float(eps), float(reach) + float(eps), dist, side)  # fmt: skip
    found = strips.run(
        lambda lo, hi: _domain_strip(lo, hi, *args), r.lines.n_holes, threads=threads, tick=_part(tick, 0.5, 1.0)
    )
    if tick is not None and not found:
        tick(1.0)
    return Contacts(dist, side, sum(int(c) for c, _ in found), sum(int(h) for _, h in found))


# --- the means of the steps --------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _add(total, comp, q, x):  # type: ignore[no-untyped-def]
    """total[q] += x with the rounding error kept in comp[q] (Neumaier)."""
    t = total[q] + x
    if abs(total[q]) >= abs(x):
        comp[q] += (total[q] - t) + x
    else:
        comp[q] += (x - t) + total[q]
    total[q] = t


@njit(cache=True, nogil=True)
def _bin(a, e, rows, v, w, dist, side, step, eps, n_side, pivot, n, sw, cw, sm, cm):  # type: ignore[no-untyped-def]
    """Rows a … e − 1 of the selection into the steps: n, Σ w and Σ w·(v − pivot) of every step, the pivot of a
    step being its first value."""
    for j in range(a, e):
        i = rows[j]
        s = side[i]
        if s < 0:
            continue
        k = int(math.floor((dist[i] + eps) / step))
        if k >= n_side:
            k = n_side - 1
        q = n_side - 1 - k if s == 0 else n_side + k
        x = v[j]
        y = w[j]
        if n[q] == 0:
            pivot[q] = x
        n[q] += 1
        _add(sw, cw, q, y)
        _add(sm, cm, q, y * (x - pivot[q]))


def _num(x: float) -> float | None:
    x = float(x)
    return x if math.isfinite(x) else None


def _side_of(label: str, n: np.ndarray, w: np.ndarray, s: np.ndarray, pivot: np.ndarray) -> dict[str, Any]:
    """All the samples of a side (its steps from the contact outwards): their number and their mean, about the
    pivot of the first step that has samples."""
    have = np.flatnonzero(n > 0)
    if not len(have):
        return {"label": label, "n": 0, "mean": None}
    p0 = float(pivot[have[0]])
    try:
        with np.errstate(invalid="ignore", over="ignore"):
            mean = p0 + math.fsum([*(w[have] * (pivot[have] - p0)), *s[have]]) / math.fsum(w[have])
    except (OverflowError, ValueError):  # sums beyond float64: no mean, as a step of such values has none
        mean = math.nan
    return {"label": label, "n": int(n[have].sum()), "mean": _num(mean)}


def _chart(
    sel: Selection, found: Contacts, st: Steps, labels: tuple[str, str], counts: dict[str, int], conventions: str,
    eps: float, tick: Tick | None,
) -> dict[str, Any]:  # fmt: skip
    """The answer: the rows of the selection `sel` (its `rows` index `found`) by the steps `st`."""
    m = 2 * st.n_side
    n = np.zeros(m, dtype=np.int64)
    pivot, sw, cw, sm, cm = (np.zeros(m, dtype=np.float64) for _ in range(5))
    for a in range(0, sel.n, _BLOCK):
        e = min(a + _BLOCK, sel.n)
        _bin(a, e, sel.rows, sel.values, sel.weights, found.dist, found.side, st.step, float(eps), st.n_side,
             pivot, n, sw, cw, sm, cm)  # fmt: skip
        if tick is not None:
            tick(e / sel.n)
    w, s = sw + cw, sm + cm
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        mean = pivot + s / w
    # the steps of each side from the contact outwards: the two sides are counted alike (the mirror)
    half = slice(st.n_side - 1, None, -1), slice(st.n_side, m)
    side_a, side_b = (_side_of(label, n[h], w[h], s[h], pivot[h]) for label, h in zip(labels, half, strict=True))
    return {
        "distance": st.distance,
        "step": st.step,
        "weighted": sel.weighted,
        "counts": {**counts, "in_steps": int(n.sum())},
        "contacts": found.contacts,
        "holes": found.holes,
        "edges": st.edges,
        "mean": [_num(x) if k else None for x, k in zip(mean.tolist(), n.tolist(), strict=True)],
        "n": n.tolist(),
        "a": side_a,
        "b": side_b,
        "conventions": conventions,
    }


# --- the chart ---------------------------------------------------------------------------------------------


def _domain_codes(labels: Sequence[str], a: Any, b: Any) -> tuple[int, int]:
    """The codes of the domains named `a` and `b`. StatsError `bad_param` {param: a | b}."""
    index = {name: k for k, name in enumerate(labels)}
    for key, title, name in (("a", "A", a), ("b", "B", b)):
        if name is None or name == "":
            raise bad_param(key, f"Не выбран домен {title}.", "Выберите домен из значений поля домена.")
        if not isinstance(name, str):
            raise bad_param(key, f"Домен {title} задаётся названием.", "Выберите домен из значений поля домена.")
        if name not in index:
            raise bad_param(
                key, f"В поле домена нет значения «{name[:80]}».", f"Выберите домен {title} из значений поля домена."
            )
    if a == b:
        raise bad_param("b", "Домены A и B должны различаться.", "Выберите два разных домена.")
    return index[a], index[b]


def _column(name: str, column: Any, n: int, dtype: Any = np.float64) -> np.ndarray:
    col = np.asarray(column, dtype=dtype)
    if col.shape != (n,):
        raise ValueError(f"{name} must be a column of the {n} rows, got {col.shape}")
    return col


def _select(
    flt: SampleFilter, take: Any, frm: np.ndarray, to: np.ndarray, values: np.ndarray, length: Any,
    weight_values: Any, tick: Tick | None,
) -> Selection:  # fmt: skip
    """The selection of the rows `take(column)` picks; without `length` the weight by length is TO − FROM."""
    n = values.shape[0]
    if flt.weight in ("length", "length_field"):
        lengths = take(to) - take(frm) if length is None else take(_column("length", length, n))
    else:
        lengths = None
    wv = None if weight_values is None else take(_column("weight_values", weight_values, n))
    return select_sample(take(values), flt, length=lengths, weight_values=wv, tick=tick)


def contact_chart(
    hole: Any, frm: Any, to: Any, codes: Any, labels: Sequence[str], a: Any, b: Any, values: Any,
    flt: SampleFilter | None = None, *, length: Any = None, weight_values: Any = None, distance: Any = None,
    step: Any = None, eps: float = DEPTH_EPS, threads: int = 1, tick: Tick | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The contact analysis of the domains `a` | `b` (names of `labels`) along the holes — see the module
    docstring. `hole` — any integer id of the hole of every row; `flt` — the selection of the values (None —
    the defaults: the weight is the length); `length`, `weight_values` — as `select_sample` takes them;
    `distance`, `step` — D and b in metres (None — 20 and 1). `tick(fraction)` may raise to cancel."""
    from bf_core.composites import intervals as iv

    flt = SampleFilter() if flt is None else flt
    st = steps_of(distance, step)
    code_a, code_b = _domain_codes(labels, a, b)
    frm_c, to_c = np.asarray(frm, dtype=np.float64), np.asarray(to, dtype=np.float64)
    n = frm_c.shape[0]
    vals = _column("values", values, n)
    lines = iv.build_lines(hole, frm_c, to_c, eps=eps)
    domain = np.ascontiguousarray(lines.take(_column("codes", codes, n, np.int32)))
    if tick is not None:
        tick(0.1)
    found = domain_contacts(lines, domain, code_a, code_b, st.distance, eps=eps, threads=threads,
                            tick=_part(tick, 0.1, 0.5))  # fmt: skip
    sel = _select(flt, lines.take, frm_c, to_c, vals, length, weight_values, _part(tick, 0.5, 0.8))
    taken = domain[sel.rows]
    counts = {
        **sel.counts,
        "rows": n,
        "no_depth": lines.n_bad,
        "overlap": lines.n_overlap,
        "no_domain": int(np.count_nonzero((taken < 0) | (taken >= len(labels)))),
    }
    sel = Selection(sel.values, sel.weights, sel.rows, counts, sel.weighted).require()
    return _chart(sel, found, st, (a, b), counts, CONVENTIONS, eps, _part(tick, 0.8, 1.0))


def warm() -> None:
    """Compile the kernels on a few rows (the `stats` step of the warm-up)."""
    hole = np.array([0, 0, 0, 0, 1], dtype=np.int32)
    frm = np.array([0.0, 1.0, 2.0, 3.5, 0.0])
    codes = np.array([0, 0, 1, 1, -1], dtype=np.int32)
    values = np.array([1.0, np.nan, 3.0, 2.0, 5.0])
    for weight in ("length", "none"):
        contact_chart(hole, frm, frm + 1.0, codes, ["a", "b"], "a", "b", values, SampleFilter(weight=weight))
