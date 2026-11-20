"""Analysis of conditions (ТЗ 7.35 Д-26 «Анализ кондиций», Т8.2-20; ADR 0018 §12, решения 23, 27): the ore
intervals of a set of holes for every variant Сб × Мрт × Мпп — the table of the form И1, сл. 24.

    conditions_analysis(lines, cutoffs, m_min, m_waste_max, *, mode="strict_excl", …, exclude=None,
                        threads=1, tick=None) -> Conditions
    exclusion(column, values) -> bool per sample        `exclude {field, values}` of the command
    neighbours(variants) -> variants                    the step of Σ M and the grade of the прирезка

A variant is a run of the kernel of Д-26 (`ore_intervals`) with its Сб, Мрт, Мпп and the rest of the parameters
as given: МС = Сб·Мрт and Смин = Сб unless `mc_min`, `c_min` are set (then they are the same in every
variant). Its row is that of the summary of the class `ore` (`ore_set`): the number of intervals, Σ M, the mean
M = Σ M / number, the mean C = Σ MC / Σ M, Σ MC (И1, сл. 23–24) — bit for bit what a separate run gives: the
sums are exactly rounded, whatever the strips and the threads. No intervals — the sums are 0, the means are None.

Variants go by (Мрт, Мпп, Сб) — the groups of the form И1, сл. 24. Inside a group a variant is compared with
the previous, lower Сб (`neighbours`):

    step_pct   (Σ M₁ − Σ M₂) / Σ M₁ · 100 — by how much the lower cutoff 1 has more ore; the variants of an
               analysis should differ by 10–15 % (И1, сл. 25) — `step_flag`, `mark` say when they do not
    c_trim     (Σ MC₁ − Σ MC₂) / (Σ M₁ − Σ M₂) — the grade of the прирезка the lower cutoff adds (И1, сл. 26:
               «контроль правильности»); None when Σ M does not grow

`exclude` — samples left out of the analysis (решение 23; И1, сл. 26: the analysis for underground mining goes
«после исключения интервалов, попавших в открытую отработку»): a sample left out ends its sampling line, as a
sample outside the domains does — it is not waste and not zero, whatever the choice for missing values.

The run. The holes go in the fixed strips of `strips` (4096 holes); a task is a strip in a variant, tasks run
on threads; a task keeps only the exact sums of its `ore` rows. `tick(fraction)` is called after every task (a
fraction of a second) and may raise to cancel.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from bf_core.jit import njit

from . import ore_units as ou
from .ore_intervals import ore_intervals
from .ore_set import _add_exact
from .ore_types import IV_CLS, IV_M, IV_MC, MODE_TITLES, OreError, OreLines, OreParams
from .strips import STRIP_HOLES, Tick, ranges, run

__all__ = [
    "MAX_VARIANTS", "STEP_MAX_PCT", "STEP_MIN_PCT", "Conditions", "conditions_analysis", "exclusion",
    "neighbours", "variant_row", "warm",
]  # fmt: skip

MAX_VARIANTS = 200
STEP_MIN_PCT, STEP_MAX_PCT = 10.0, 15.0  # neighbouring cutoffs should change Σ M by 10–15 % (И1, сл. 25)


@njit(cache=True, nogil=True)
def _ore_sums(iv_i, iv_f, parts_m, parts_mc):  # type: ignore[no-untyped-def]
    """The `ore` rows: their number and the exact sums of M and MC as non-overlapping parts (`ore_set`).
    Returns (number, parts of Σ M, parts of Σ MC)."""
    count = 0
    nm = np.int64(0)  # not the constant 0: the specialization of `_add_exact` the summary compiled
    nmc = np.int64(0)
    for r in range(iv_i.shape[0]):
        if iv_i[r, IV_CLS] == ou.CLS_ORE:
            count += 1
            nm = _add_exact(parts_m, nm, iv_f[r, IV_M])
            nmc = _add_exact(parts_mc, nmc, iv_f[r, IV_MC])
    return count, nm, nmc


def _ru(x: float) -> str:
    return f"{x:.10g}".replace(".", ",")


def variant_row(
    cutoff: float, m_min: float, m_waste_max: float, count: int, m_sum: float, mc_sum: float
) -> dict[str, Any]:
    """The row of a variant (И1, сл. 24): the mean M = Σ M / number, the mean C = Σ MC / Σ M."""
    return {
        "cutoff": cutoff,
        "m_min": m_min,
        "m_waste_max": m_waste_max,
        "count": count,
        "m_sum": m_sum,
        "m_mean": m_sum / count if count else None,
        "c_mean": mc_sum / m_sum if m_sum > 0.0 else None,
        "mc_sum": mc_sum,
    }


def neighbours(variants: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The variants by (Мрт, Мпп, Сб) with `step_pct`, `c_trim`, `step_flag` and `mark` against the previous
    (lower) cutoff of their group; the first of a group has None, False, None."""
    out = sorted((dict(v) for v in variants), key=lambda v: (v["m_min"], v["m_waste_max"], v["cutoff"]))
    for k, v in enumerate(out):
        prev = (
            out[k - 1]
            if k and (out[k - 1]["m_min"], out[k - 1]["m_waste_max"]) == (v["m_min"], v["m_waste_max"])
            else None
        )
        step = trim = mark = None
        flag = False
        if prev is not None:
            grown = prev["m_sum"] - v["m_sum"]
            if prev["m_sum"] > 0.0:
                step = grown / prev["m_sum"] * 100.0
            if grown > 0.0:
                trim = (prev["mc_sum"] - v["mc_sum"]) / grown
            if grown < 0.0:
                flag = True
                mark = f"Σ M больше, чем при Сб {_ru(prev['cutoff'])}: с ростом борта мощность руды не должна расти"
            elif step is not None and not (ou.ge(step, STEP_MIN_PCT) and ou.le(step, STEP_MAX_PCT)):
                flag = True
                mark = (
                    f"Σ M отличается от варианта Сб {_ru(prev['cutoff'])} на {f'{step:.1f}'.replace('.', ',')} % — "
                    f"рекомендуется {STEP_MIN_PCT:g}–{STEP_MAX_PCT:g} % (И1, сл. 25)"
                )
        v.update(step_pct=step, c_trim=trim, step_flag=flag, mark=mark)
    return out


@dataclass(frozen=True)
class Conditions:
    """The analysis: `variants` — rows by (Мрт, Мпп, Сб): `cutoff`, `m_min`, `m_waste_max`, `mc_min`, `c_min`,
    `count`, `m_sum`, `m_mean`, `c_mean`, `mc_sum`, `step_pct`, `c_trim`, `step_flag`, `mark`."""

    variants: list[dict[str, Any]]
    cutoffs: tuple[float, ...]
    m_min: tuple[float, ...]
    m_waste_max: tuple[float, ...]
    mode: str
    stats: dict[str, int]  # samples, holes, excluded

    def as_dict(self) -> dict[str, Any]:
        """What goes to the report of the result (JSON: no NaN)."""
        return {
            "variants": self.variants, "cutoffs": list(self.cutoffs), "m_min": list(self.m_min),
            "m_waste_max": list(self.m_waste_max), "mode": self.mode, "mode_title": MODE_TITLES[self.mode],
            "step_pct": [STEP_MIN_PCT, STEP_MAX_PCT], **self.stats,
        }  # fmt: skip


def exclusion(column: Any, values: Sequence[Any]) -> np.ndarray:
    """Samples whose field is one of `values` (решение 23: `exclude {field, values}`; the values come as
    typed — strings). A numeric field is compared by value («1,50» is 1.5; a text that is not a number matches
    nothing), a text field — as text without the outer spaces. A sample without a value is never left out."""
    import pandas as pd

    col = np.asarray(column)
    texts = {str(v).strip() for v in values}
    numbers = set()
    for t in texts:
        try:
            x = float(t.replace(",", ".").replace("−", "-"))
        except ValueError:
            continue
        if math.isfinite(x):
            numbers.add(x)
    if col.dtype.kind in "iuf":
        return np.isin(col, np.array(sorted(numbers), np.float64)) if numbers else np.zeros(len(col), bool)
    codes, uniques = pd.factorize(col)  # a missing value is code −1
    hit = [
        k
        for k, u in enumerate(uniques.tolist())
        if (
            u.strip() in texts
            if isinstance(u, str)
            else not isinstance(u, bool) and isinstance(u, (int, float)) and u in numbers
        )
    ]
    return np.isin(codes, np.array(hit, np.int64))


def _values(raw: Any, title: str) -> tuple[float, ...]:
    """A list of the analysis: numbers without repeats in ascending order, at least one."""
    try:
        items = sorted({float(x) for x in (raw if isinstance(raw, (list, tuple, np.ndarray)) else [raw])})
    except (TypeError, ValueError):
        raise OreError("ore_bad_param", f"{title}: ожидается список чисел.", "Введите значения через «;».") from None
    if not items:
        raise OreError("ore_bad_param", f"{title}: задайте хотя бы одно значение.", "Введите значения через «;».")
    return tuple(items)


def conditions_analysis(
    lines: OreLines,
    cutoffs: Any,
    m_min: Any,
    m_waste_max: Any,
    *,
    mode: str = "strict_excl",
    missing: str = "break",
    missing_max: float = 0.0,
    contacts_values: tuple[int, ...] | None = None,
    mc_min: float | None = None,
    c_min: float | None = None,
    exclude: Any | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> Conditions:
    """Д-26 for every variant Сб × Мрт × Мпп (at most `MAX_VARIANTS`). `exclude` — True for the samples left
    out of the analysis (`exclusion`). `tick(fraction)` is called after every task and may raise to cancel."""
    lists = (
        _values(cutoffs, "Бортовые содержания Сб"),
        _values(m_min, "Минимальные мощности Мрт"),
        _values(m_waste_max, "Максимальные мощности прослоя Мпп"),
    )
    total = len(lists[0]) * len(lists[1]) * len(lists[2])
    if total > MAX_VARIANTS:
        raise OreError(
            "ore_bad_param", f"Вариантов {total}: не больше {MAX_VARIANTS}.", "Сократите списки Сб, Мрт или Мпп."
        )
    base = OreParams(
        lists[0][0], lists[1][0], lists[2][0], mc_min=mc_min, c_min=c_min, mode=mode,
        missing=missing, missing_max=missing_max, contacts_values=contacts_values,
    )  # fmt: skip
    variants = [
        replace(base, cutoff=cb, m_min=mrt, m_waste_max=mpp).checked()  # every value is checked, МС and Смин set
        for mrt in lists[1]
        for mpp in lists[2]
        for cb in lists[0]
    ]
    if mode == "contacts" and lines.domain is None:  # before a domain is made up for the samples left out
        raise OreError(
            "ore_no_domain",
            "Режим «По контактам» требует поле домена.",
            "Выберите поле домена (или кодирование каркасом) либо другой режим.",
        )
    holes = len(lines.starts) - 1
    left_out = 0
    if exclude is not None:
        mask = np.asarray(exclude, dtype=bool)
        if mask.shape != lines.frm.shape:
            raise OreError(
                "ore_bad_lines", "Пробы переданы ядру в неверном виде.", "Сообщите разработчику: это ошибка программы."
            )
        left_out = int(np.count_nonzero(mask))
        if left_out:  # a sample left out ends its sampling line, as a sample outside the domains does
            domain = np.zeros(len(mask), np.int32) if lines.domain is None else lines.domain.copy()
            domain[mask] = -1
            lines = replace(lines, domain=domain)
    strips = ranges(holes, STRIP_HOLES)
    nv = len(variants)

    def task(k: int, _k1: int) -> tuple[int, np.ndarray, np.ndarray]:
        h0, h1 = strips[k // nv]
        found = ore_intervals(lines.holes(h0, h1), variants[k % nv])
        parts_m = np.empty(64, np.float64)
        parts_mc = np.empty(64, np.float64)
        count, nm, nmc = _ore_sums(found.iv_i, found.iv_f, parts_m, parts_mc)
        return count, parts_m[:nm], parts_mc[:nmc]

    done = run(task, len(strips) * nv, threads=threads, tick=tick, size=1)
    rows = []
    for v, p in enumerate(variants):
        mine = done[v::nv]
        row = variant_row(
            p.cutoff, p.m_min, p.m_waste_max, sum(c for c, _, _ in mine),
            math.fsum(x for _, parts, _ in mine for x in parts.tolist()),
            math.fsum(x for _, _, parts in mine for x in parts.tolist()),
        )  # fmt: skip
        rows.append({**row, "mc_min": p.mc_min, "c_min": p.c_min})
    stats = {"samples": len(lines.frm), "holes": holes, "excluded": left_out}
    return Conditions(neighbours(rows), *lists, mode, stats)


def warm() -> None:
    """The kernel of the sums once (`composites/warm.py`; the kernels of Д-26 — `ore_set.warm`)."""
    lines = OreLines.of([0, 3, 5], [0.0, 1.0, 2.0, 0.0, 1.0], [1.0, 2.0, 3.0, 1.0, 2.0], [2.0, 0.5, 3.0, 1.5, math.nan])
    conditions_analysis(lines, [0.5, 1.0], [1.0], [1.0, 2.0], exclude=np.array([False, False, False, False, True]))
