"""Ore intervals of a set of holes (ТЗ 7.35 Д-26 «Результат», ADR 0018 §8, решение 21): what the job writes —
the table «Рудные интервалы», the fields of the samples, the summary.

    run_set(lines, params, *, fields=None, nested=True, threads=1, tick=None) -> OreSet

The kernel (`ore_intervals`) runs on strips of 4096 holes on threads; the rest is taken afterwards:

    table       `OreSet.table()` — columns of the rows of all classes: HOLE (index of the hole in `lines`), FROM,
                TO, LENGTH (along the hole), M (thickness of the calculation), C, MC, CLASS and BASIS (codes of
                `ore_units`; names — `CLASS_NAMES`, `BASIS_NAMES`), N_SAMPLES, WASTE_LEN, NESTED, ORE_ID (number of
                the `ore` interval in its hole, 1…k; the nested rows carry that of their interval), FLAG (1 — holds
                a missing stretch counted as waste), DOMAIN (code of the domain of the sampling line, −1 — none)
                and the other numeric fields. X, Y, Z of the middle, LABEL, MODE and MANUAL are the job's
                (`label` here writes «8,0 м @ 2,84 г/т (МС 22,7)»).
    fields      the other numeric fields of the samples: the mean over the samples of the row weighted by their
                thickness l (as 8.2); a sample without a value is skipped, a row without values is NaN — a
                missing value never becomes zero. A missing sample counted as waste («пустая порода до L_н»)
                gives the grade of the calculation 0 by that choice, and its own values to the other fields.
    samples     `ore_id` (ORE_ID, 0 — empty) and `ore_cls` (code of ORE_CLS, −1 — empty: the sample is outside
                the sampling lines) per row of the samples.
    summary     by class, and by class and domain: the number of intervals, Σ M, the mean M, the mean C =
                Σ MC / Σ M, Σ MC (И1, сл. 23). The sums are exactly rounded (`math.fsum`) — they do not depend
                on the order of the holes or on the number of threads. `waste_in` and `frame` are nested: they
                lie over other rows; Σ M of the classes with NESTED = 0 is the thickness of the sampling lines.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np

from bf_core.jit import njit

from . import ore_units as ou
from .ore_intervals import ore_intervals
from .ore_types import IV_HOLE, IV_LINE, IV_ROW0, OreError, OreIntervals, OreLines, OreParams
from .strips import Tick, run

__all__ = ["BASIS_NAMES", "CLASS_NAMES", "NESTED_CLASSES", "OreSet", "label", "run_set", "warm"]

CLASS_NAMES = ou.CLASSES
BASIS_NAMES = ou.BASES
NESTED_CLASSES = ("waste_in", "frame")
ROWS_CHUNK = 1 << 16  # rows of the table in a strip of the weighted means


@njit(cache=True, nogil=True)
def _weighted(r0, r1, row0, row1, state, frm, to, thick, use_thick, values, out):  # type: ignore[no-untyped-def]
    """Rows r0 ≤ r < r1 of the table: the mean of every column of `values` over the samples row0[r]…row1[r]
    that take part in the calculation, weighted by their thickness; values that are not numbers are skipped;
    no values — NaN."""
    nf = values.shape[1]
    sw = np.empty(nf, np.float64)
    swv = np.empty(nf, np.float64)
    for r in range(r0, r1):
        for f in range(nf):
            sw[f] = 0.0
            swv[f] = 0.0
        if row0[r] >= 0:
            for k in range(row0[r], row1[r] + 1):
                if state[k] != ou.ROW_PIECE and state[k] != ou.ROW_BRIDGED:
                    continue
                w = to[k] - frm[k]
                if use_thick and math.isfinite(thick[k]) and thick[k] > 0.0:
                    w = thick[k]
                for f in range(nf):
                    v = values[k, f]
                    if math.isfinite(v):
                        sw[f] += w
                        swv[f] += w * v
        for f in range(nf):
            out[r, f] = swv[f] / sw[f] if sw[f] > 0.0 else math.nan


@dataclass(frozen=True)
class OreSet:
    """Result of Д-26 for a set of holes: the rows, the fields of the samples, the summary."""

    intervals: OreIntervals  # rows of all classes, the nested ones too (`ore_types`)
    domain: np.ndarray  # int32 per row: the code of the domain of its sampling line, −1 — no domains
    fields: dict[str, np.ndarray]  # other numeric fields: float64 per row, NaN — no values
    ore_id: np.ndarray  # int32 per sample: ORE_ID, 0 — empty
    ore_cls: np.ndarray  # int8 per sample: code of ORE_CLS (`CLASS_NAMES`), −1 — empty
    summary: dict[str, Any]

    def __len__(self) -> int:
        return len(self.intervals)

    def table(self) -> dict[str, np.ndarray]:
        """Columns of the table «Рудные интервалы», one value per row."""
        iv = self.intervals
        return {
            "HOLE": iv.hole,
            "FROM": iv.frm,
            "TO": iv.to,
            "LENGTH": iv.to - iv.frm,
            "M": iv.m,
            "C": iv.c,
            "MC": iv.mc,
            "CLASS": iv.cls,
            "BASIS": iv.basis,
            "N_SAMPLES": iv.n_samples,
            "WASTE_LEN": iv.waste_len,
            "NESTED": iv.nested,
            "ORE_ID": iv.ore_id,
            "FLAG": iv.flag,
            "DOMAIN": self.domain,
            **self.fields,
        }

    def labels(self, unit: str = "") -> list[str]:
        """LABEL of every row: «8,0 м @ 2,84 г/т (МС 22,7)» for `ore`, empty for the rest (решение 20)."""
        iv = self.intervals
        out = [""] * len(iv)
        for r in np.flatnonzero(iv.cls == ou.CLS_ORE).tolist():
            out[r] = label(float(iv.m[r]), float(iv.mc[r]) / float(iv.m[r]), float(iv.mc[r]), unit)
        return out


def _ru(x: float, digits: int) -> str:
    """A number with `digits` decimals as it is written by hand: half up on its shortest notation, comma."""
    if not math.isfinite(x):
        return "—"
    return str(Decimal(repr(x)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)).replace(".", ",")


def label(m: float, c: float, mc: float, unit: str = "") -> str:
    """The label of an ore interval: M and MC with one decimal, C with two, the unit of the field."""
    grade = f"{_ru(c, 2)} {unit}".rstrip()
    return f"{_ru(m, 1)} м @ {grade} (МС {_ru(mc, 1)})"


def _row_domain(iv: OreIntervals, lines: OreLines) -> np.ndarray:
    """The domain of every row: that of the first sample of its sampling line (a line begins with a sample
    and lies in one domain)."""
    n = len(iv)
    if lines.domain is None or n == 0:
        return np.full(n, -1, np.int32)
    change = np.ones(n, bool)
    change[1:] = (iv.iv_i[1:, IV_HOLE] != iv.iv_i[:-1, IV_HOLE]) | (iv.iv_i[1:, IV_LINE] != iv.iv_i[:-1, IV_LINE])
    first = np.flatnonzero(change)
    codes = lines.domain[iv.iv_i[first, IV_ROW0]]
    return np.ascontiguousarray(codes[np.cumsum(change) - 1], dtype=np.int32)


@njit(cache=True, nogil=True)
def _add_exact(parts, n, x):  # type: ignore[no-untyped-def]
    """Adds x to the sum kept exactly as the non-overlapping parts[:n] (Shewchuk; the algorithm of
    `math.fsum`). Returns the new n; 64 parts are room for any doubles."""
    i = 0
    for j in range(n):
        y = parts[j]
        if abs(x) < abs(y):
            x, y = y, x
        hi = x + y
        lo = y - (hi - x)
        if lo != 0.0:
            parts[i] = lo
            i += 1
        x = hi
    parts[i] = x
    return i + 1


@njit(cache=True, nogil=True)
def _exact_total(parts, n):  # type: ignore[no-untyped-def]
    """The exactly rounded value of the sum kept in parts[:n] (the last step of `math.fsum`)."""
    if n == 0:
        return 0.0
    n -= 1
    hi = parts[n]
    lo = 0.0
    while n > 0:
        x = hi
        n -= 1
        y = parts[n]
        hi = x + y
        lo = y - (hi - x)
        if lo != 0.0:
            break
    if n > 0 and ((lo < 0.0 and parts[n - 1] < 0.0) or (lo > 0.0 and parts[n - 1] > 0.0)):
        y = lo * 2.0
        x = hi + y
        if y == x - hi:
            hi = x
    return hi


@njit(cache=True, nogil=True)
def _sums(order, key, m, mc, nested, g_key, g_count, g_m, g_mc, c_count, c_m, c_mc):  # type: ignore[no-untyped-def]
    """One pass over the rows in the order of their key (class · 2³² + domain + 1): the exact sums of M and MC
    by key (`g_*`, a group per key met) and by class (`c_*`), and of M of the rows that are not nested.
    Returns (groups, that sum)."""
    pm = np.empty(64, np.float64)
    pmc = np.empty(64, np.float64)
    qm = np.empty(64, np.float64)
    qmc = np.empty(64, np.float64)
    total = np.empty(64, np.float64)
    nm = np.int64(0)  # not the constant 0: one specialization of `_add_exact` and `_exact_total`
    nmc = np.int64(0)
    km = np.int64(0)
    kmc = np.int64(0)
    nt = np.int64(0)
    ng = 0
    count = 0
    cls_count = 0
    for i in range(len(order)):
        r = order[i]
        if i > 0 and key[r] != key[order[i - 1]]:
            g_key[ng] = key[order[i - 1]]
            g_count[ng] = count
            g_m[ng] = _exact_total(pm, nm)
            g_mc[ng] = _exact_total(pmc, nmc)
            ng += 1
            nm = np.int64(0)
            nmc = np.int64(0)
            count = 0
            if key[r] >> 32 != key[order[i - 1]] >> 32:
                c = key[order[i - 1]] >> 32
                c_count[c] = cls_count
                c_m[c] = _exact_total(qm, km)
                c_mc[c] = _exact_total(qmc, kmc)
                km = np.int64(0)
                kmc = np.int64(0)
                cls_count = 0
        count += 1
        cls_count += 1
        nm = _add_exact(pm, nm, m[r])
        nmc = _add_exact(pmc, nmc, mc[r])
        km = _add_exact(qm, km, m[r])
        kmc = _add_exact(qmc, kmc, mc[r])
        if nested[r] == 0:
            nt = _add_exact(total, nt, m[r])
    if len(order) > 0:
        last = key[order[len(order) - 1]]
        g_key[ng] = last
        g_count[ng] = count
        g_m[ng] = _exact_total(pm, nm)
        g_mc[ng] = _exact_total(pmc, nmc)
        ng += 1
        c_count[last >> 32] = cls_count
        c_m[last >> 32] = _exact_total(qm, km)
        c_mc[last >> 32] = _exact_total(qmc, kmc)
    return ng, _exact_total(total, nt)


def _summary(iv: OreIntervals, domain: np.ndarray, with_domains: bool) -> dict[str, Any]:
    """The summary by class and by (domain, class). Every sum is exactly rounded, whatever the order of the rows."""
    rows = len(iv)
    key = (iv.cls.astype(np.int64) << 32) + (domain.astype(np.int64) + 1)
    codes = int(domain.max()) + 2 if rows else 1
    if len(CLASS_NAMES) * codes <= 1 << 16:  # few domains: 16-bit keys sort in linear time
        order = np.argsort((iv.cls * codes + domain + 1).astype(np.uint16), kind="stable")
    else:
        order = np.argsort(key, kind="stable")
    groups = int(np.count_nonzero(np.diff(key[order]))) + 1 if rows else 0
    g_key = np.empty(groups, np.int64)
    g_count = np.empty(groups, np.int64)
    g_m = np.empty(groups, np.float64)
    g_mc = np.empty(groups, np.float64)
    c_count = np.zeros(len(CLASS_NAMES), np.int64)
    c_m = np.zeros(len(CLASS_NAMES), np.float64)
    c_mc = np.zeros(len(CLASS_NAMES), np.float64)
    m = np.ascontiguousarray(iv.m)
    mc = np.ascontiguousarray(iv.mc)
    _, length = _sums(order, key, m, mc, np.ascontiguousarray(iv.nested), g_key, g_count, g_m, g_mc, c_count, c_m, c_mc)

    def entry(code: int, count: int, sum_m: float, sum_mc: float) -> dict[str, Any]:
        return {
            "cls": CLASS_NAMES[code],
            "nested": CLASS_NAMES[code] in NESTED_CLASSES,
            "count": count,
            "m": sum_m,
            "m_mean": sum_m / count if count else math.nan,
            "c": sum_mc / sum_m if sum_m > 0.0 else math.nan,
            "mc": sum_mc,
        }

    by_class = [
        entry(code, int(c_count[code]), float(c_m[code]), float(c_mc[code]))
        for code in range(len(CLASS_NAMES))
        if c_count[code]
    ]
    by_domain = []
    if with_domains:
        for k, count, sum_m, sum_mc in zip(g_key.tolist(), g_count.tolist(), g_m.tolist(), g_mc.tolist(), strict=True):
            by_domain.append({"domain": (k & 0xFFFFFFFF) - 1, **entry(k >> 32, count, sum_m, sum_mc)})
        by_domain.sort(key=lambda e: (e["domain"], CLASS_NAMES.index(e["cls"])))
    return {"classes": by_class, "domains": by_domain, "length": float(length), "rows": rows, **iv.stats}


def run_set(
    lines: OreLines,
    params: OreParams,
    *,
    fields: Mapping[str, Any] | None = None,
    nested: bool = True,
    threads: int = 1,
    tick: Tick | None = None,
) -> OreSet:
    """Д-26 for a set of holes: the rows of all classes (with the nested ones unless `nested=False`), ORE_ID and
    ORE_CLS of the samples, the means of the other numeric `fields` (name → a value per sample) and the
    summary. `tick(fraction)` is called after every strip and may raise to cancel."""
    n = len(lines.frm)
    names = list(fields or {})
    cols = [np.ascontiguousarray((fields or {})[name], dtype=np.float64) for name in names]
    if any(c.shape != (n,) for c in cols):
        raise OreError(
            "ore_bad_lines", "Пробы переданы ядру в неверном виде.", "Сообщите разработчику: это ошибка программы."
        )
    share = 0.85 if cols else 1.0
    iv = ore_intervals(
        lines, params, nested=nested, threads=threads, tick=None if tick is None else lambda f: tick(f * share)
    )
    means: dict[str, np.ndarray] = {}
    if cols:
        values = np.column_stack(cols)
        out = np.empty((len(iv), len(cols)), np.float64)
        row0 = np.ascontiguousarray(iv.row0)
        row1 = np.ascontiguousarray(iv.row1)
        thick = lines.thick if lines.thick is not None else np.empty(0, np.float64)

        def part(r0: int, r1: int) -> None:
            _weighted(
                r0, r1, row0, row1, iv.sample_state, lines.frm, lines.to, thick, lines.thick is not None, values, out
            )

        run(part, len(iv), threads=threads, tick=tick, size=ROWS_CHUNK, span=(share, 1.0))
        means = {name: np.ascontiguousarray(out[:, k]) for k, name in enumerate(names)}
    domain = _row_domain(iv, lines)
    return OreSet(
        iv, domain, means, iv.sample_ore_id, iv.sample_cls, _summary(iv, domain, lines.domain is not None)
    )


def warm() -> None:
    """Every kernel of Д-26 once, with the dtypes of the job and of the preview (`composites/warm.py`): the five
    modes with and without events, the nested rows, a thickness field, domains, «пустая порода до L_н», the
    means of other fields."""
    nan = math.nan
    lines = OreLines.of(
        [0, 9, 14, 17],
        [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 0.0, 1.0, 2.0, 3.5, 4.5, 0.0, 1.0, 2.0],
        [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 1.0, 2.0, 3.0, 4.5, 5.5, 1.0, 2.0, 3.0],
        [1.5, 1.5, 0.8, 1.2, 0.9, 0.9, 1.5, 1.5, 0.1, 2.4, 0.3, 2.5, nan, 3.0, 1.1, 0.8, 1.3],
        [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.9, 0.9, 0.9, 0.9, 0.9, 1.0, 1.0, 1.0],
        np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1], np.int32),
    )
    other = {"AG": np.linspace(1.0, 17.0, 17)}
    for mode in ou.MODES:
        for missing in ("break", "waste"):
            p = OreParams(1.0, 3.0, 3.0, mode=mode, missing=missing, missing_max=1.0)
            ore_intervals(lines, p, events=True, nested=True)
            run_set(lines, p, fields=other)
