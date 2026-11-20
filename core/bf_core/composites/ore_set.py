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


def _summary(iv: OreIntervals, domain: np.ndarray, with_domains: bool) -> dict[str, Any]:
    """The summary by class and by (domain, class). Every sum is `math.fsum` over the rows of the group."""
    cls = iv.cls
    m = iv.m
    mc = iv.mc

    def entry(rows: np.ndarray, code: int) -> dict[str, Any]:
        count = len(rows)
        sum_m = math.fsum(m[rows].tolist())
        sum_mc = math.fsum(mc[rows].tolist())
        return {
            "cls": CLASS_NAMES[code],
            "nested": CLASS_NAMES[code] in NESTED_CLASSES,
            "count": count,
            "m": sum_m,
            "m_mean": sum_m / count if count else math.nan,
            "c": sum_mc / sum_m if sum_m > 0.0 else math.nan,
            "mc": sum_mc,
        }

    order = np.argsort(cls, kind="stable")
    bounds = np.searchsorted(cls[order], np.arange(len(CLASS_NAMES) + 1))
    by_class = []
    by_domain = []
    for code in range(len(CLASS_NAMES)):
        rows = order[bounds[code] : bounds[code + 1]]
        if len(rows) == 0:
            continue
        by_class.append(entry(rows, code))
        if not with_domains:
            continue
        codes = domain[rows]
        inner = np.argsort(codes, kind="stable")
        values, starts = np.unique(codes[inner], return_index=True)
        ends = [*starts[1:].tolist(), len(rows)]
        for value, lo, hi in zip(values.tolist(), starts.tolist(), ends, strict=True):
            by_domain.append({"domain": int(value), **entry(rows[inner[lo:hi]], code)})
    by_domain.sort(key=lambda e: (e["domain"], CLASS_NAMES.index(e["cls"])))
    length = math.fsum(m[iv.nested == 0].tolist())
    return {"classes": by_class, "domains": by_domain, "length": length, "rows": len(iv), **iv.stats}


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
