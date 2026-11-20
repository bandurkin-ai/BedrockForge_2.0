"""Composites by length (ТЗ 8.2.2, ADR 0018 §6): the value of a composite and the parameters.

    p = LengthParams.of({"length": 2, "remainder": "attach", "f_min": 0.5, "p_min": 0.5, "origin": "domain",
                         "unsampled": None})          # the keys of `composite.run`; raises CompositeError
    v = composite_values(lines, values, targets, density=rho, by_density=True, texts=[codes], p_min=0.5,
                         unsampled=None, threads=…, tick=…) -> CompositeValues

Value of a composite [c0; c1] (`intervals.aggregate`), for every numeric field:
    l_i — the overlap of sample i that has a value; w_i = 1 or ρ_i («вес по плотности»)
    g_c = Σ l_i·w_i·g_i / Σ l_i·w_i;  ρ_c = Σ l_i·ρ_i / Σ l_i;  L_оп = Σ l_i
    L_оп < p_min·(c1 − c0) → g_c = NaN, FLAG «мало проб» (L_оп = p_min·(c1 − c0) is enough)
    text — the mode by length, a tie — the first value down the hole; nothing — empty
A missing value takes no part and never becomes 0. Unsampled parts: NaN (default); `unsampled` = 0 or a number
— the part of the composite without a value of the field takes it, weighted by length:
g_c = (g_c·L_оп + u·(c1 − c0 − L_оп)) / (c1 − c0); then p_min only sets the flag.

Density weighting with gaps in the density (it is often measured on a part of the samples): a sample with a
grade and without a density takes the mean density of its composite; no density in the whole composite —
w = 1. Such samples are counted (`n_no_density`) for the job log.

Columns of the result (ТЗ 8.2.3): SAMPLED_LEN and N_SAMPLES are the length and the number of the samples that
have a value in at least one of the chosen numeric fields; FLAG is set when any of the fields is short of
p_min. `summary` — «до/после» of the window: count, total length, mean and metal Σ l·g of every field.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any

import numpy as np

from bf_core.jit import njit

from . import intervals as iv
from .intervals import CompositeError
from .intervals_kernels import DEPTH
from .strips import Tick

FLAG_LOW = "мало проб"
REMAINDERS = ("attach", "drop", "equal")  # остаток — присоединить к последнему / отбросить; равные части
ORIGINS = ("domain", "collar", "first")  # от начала участка / от устья / от кровли первой пробы


def _number(raw: Any, label: str, param: str) -> float:
    try:
        if isinstance(raw, bool):
            raise TypeError
        value = float(raw)
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value):
        raise CompositeError("bad_param", f"{label}: нужно число.", "Введите число, например 0,5.", param=param)
    return value


def check_range(raw: Any, lo: float, hi: float, label: str, param: str, unit: str = "") -> float:
    """A number of [lo; hi] or CompositeError `bad_param` (the ranges of the table of ТЗ 8.2.2)."""
    value = _number(raw, label, param)
    if not lo <= value <= hi:
        span = f"от {lo:g} до {hi:g}{unit}".replace(".", ",")
        raise CompositeError("bad_param", f"{label}: значение вне диапазона {span}.", f"Задайте {span}.", param=param)
    return value


def check_choice(raw: Any, choices: Sequence[str], label: str, param: str) -> str:
    if raw not in choices:
        allowed = ", ".join(choices)
        raise CompositeError(
            "bad_param", f"{label}: неизвестное значение «{raw}».", f"Допустимо: {allowed}.", param=param
        )
    return str(raw)


@dataclass(frozen=True)
class LengthParams:
    """Parameters of composites by length; the names and values are those of the command `composite.run`."""

    length: float = 2.0  # L, m: 0.1–100
    remainder: str = "attach"  # attach | drop | equal
    f_min: float = 0.5  # 0–1: a remainder of f_min·L and longer is a composite of its own
    p_min: float = 0.5  # 0–1: the least sampled share of a composite
    origin: str = "domain"  # domain | collar | first
    unsampled: float | None = None  # the value of unsampled parts; None — no value (NaN)

    @classmethod
    def of(cls, raw: Mapping[str, Any]) -> LengthParams:
        """From the parameters of a request: absent or None — the default; checked."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known and (v is not None or k == "unsampled")}).checked()

    def checked(self) -> LengthParams:
        """The same parameters as numbers within the ranges of ТЗ 8.2.2, or CompositeError `bad_param`."""
        unsampled = None
        if self.unsampled is not None:
            unsampled = _number(self.unsampled, "Значение непробованных участков", "unsampled")
        return LengthParams(
            check_range(self.length, 0.1, 100.0, "Длина композита L", "length", " м"),
            check_choice(self.remainder, REMAINDERS, "Правило остатка", "remainder"),
            check_range(self.f_min, 0.0, 1.0, "Доля остатка f_min", "f_min"),
            check_range(self.p_min, 0.0, 1.0, "Минимальная доля опробованной длины p_min", "p_min"),
            check_choice(self.origin, ORIGINS, "Отсчёт", "origin"),
            unsampled,
        )


@dataclass(frozen=True)
class CompositeValues:
    values: np.ndarray  # float64 [n, n_fields]: g_c of the numeric fields, NaN — no value
    density: np.ndarray  # float64 [n]: DENSITY ρ_c, NaN — no density
    sampled: np.ndarray  # float64 [n]: SAMPLED_LEN
    count: np.ndarray  # int32 [n]: N_SAMPLES
    low: np.ndarray  # bool [n]: FLAG «мало проб»
    texts: tuple[np.ndarray, ...]  # int32 [n] codes of every text field, −1 — empty
    n_no_density: int = 0  # samples with a value and without a density that were weighted by the mean one

    def notes(self) -> list[str]:
        """Lines for the job log."""
        if not self.n_no_density:
            return []
        return [f"Проб без плотности — {self.n_no_density}: взвешены средней плотностью своего композита."]


def composite_values(
    lines: iv.Lines,
    values: Any,
    targets: iv.Lines,
    *,
    density: Any = None,
    by_density: bool = True,
    texts: Sequence[Any] = (),
    p_min: float = 0.5,
    unsampled: float | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> CompositeValues:
    """Values of the composites `targets` over the samples `lines` — see the module docstring. `values` —
    float64 [n, n_fields] in line order (`intervals.stack_fields`); `density` — per sample or None: gives
    ρ_c and, with `by_density`, the weights; `texts` — int32 codes of text fields, in line order."""
    weight = density if by_density else None
    share = 1.0 / (1.0 + 0.3 * len(texts))  # of the progress: the numbers, then the text fields

    def part(lo: float, hi: float) -> Tick | None:
        return None if tick is None else (lambda f: tick(lo + (hi - lo) * f))

    ag = iv.aggregate(
        lines,
        values,
        targets,
        weight=weight,
        density=density,
        p_min=float(p_min),
        fill=math.nan if unsampled is None else float(unsampled),
        threads=threads,
        tick=part(0.0, share),
    )
    modes = []
    for k, codes in enumerate(texts):
        span = (share + (1.0 - share) * k / len(texts), share + (1.0 - share) * (k + 1) / len(texts))
        modes.append(iv.mode_by_length(lines, codes, targets, threads=threads, tick=part(*span)))
    lost = 0
    if weight is not None:
        rows = np.flatnonzero(~(np.asarray(weight, dtype=np.float64) > 0.0))
        if len(rows):
            lost = int(np.count_nonzero(~np.isnan(np.asarray(values)[rows]).all(axis=1)))
    return CompositeValues(ag.values, ag.density, ag.sampled, ag.count, ag.low, tuple(modes), lost)


def flag_column(marks: Sequence[tuple[str, np.ndarray]], n: int) -> np.ndarray:
    """FLAG of n rows as text: the names of the marks set for a row, joined by «; »; None — no mark."""
    out = np.full(n, None, dtype=object)
    for name, mask in marks:
        rows = np.flatnonzero(mask)
        out[rows] = [name if v is None else f"{v}; {name}" for v in out[rows]]
    return out


# --- «до/после» --------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _totals(length, vals, out):  # type: ignore[no-untyped-def]
    """out[f] = (Σ l, Σ l·g) over the rows where field f has a value; returns Σ l of all the rows. The sums go
    in the order of the rows: blocks of 8, cascade (as `intervals_kernels`)."""
    n = vals.shape[0]
    nf = vals.shape[1]
    acc = np.zeros((nf, 2))
    st = np.zeros((DEPTH, nf, 2))
    tot = 0.0
    stot = np.zeros(DEPTH)
    depth = 0
    for i in range(n):
        ln = length[i]
        tot += ln
        for f in range(nf):
            v = vals[i, f]
            if v == v:
                acc[f, 0] += ln
                acc[f, 1] += ln * v
        k = i + 1
        if (k & 7) == 0:
            m = k >> 3
            while (m & 1) == 0:
                depth -= 1
                for f in range(nf):
                    acc[f, 0] = st[depth, f, 0] + acc[f, 0]
                    acc[f, 1] = st[depth, f, 1] + acc[f, 1]
                tot = stot[depth] + tot
                m >>= 1
            for f in range(nf):
                st[depth, f, 0] = acc[f, 0]
                st[depth, f, 1] = acc[f, 1]
                acc[f, 0] = 0.0
                acc[f, 1] = 0.0
            stot[depth] = tot
            tot = 0.0
            depth += 1
    while depth > 0:
        depth -= 1
        for f in range(nf):
            acc[f, 0] = st[depth, f, 0] + acc[f, 0]
            acc[f, 1] = st[depth, f, 1] + acc[f, 1]
        tot = stot[depth] + tot
    for f in range(nf):
        out[f, 0] = acc[f, 0]
        out[f, 1] = acc[f, 1]
    return tot


def summary(lengths: Any, values: Any, names: Sequence[str]) -> dict[str, Any]:
    """«До/после» of the window (ТЗ 8.2.3) for the rows of a table — samples or composites:
    {"count", "length", "fields": {name: {"mean", "metal"}}}; metal = Σ l·g and mean = Σ l·g / Σ l over the
    rows where the field has a value; None — the field has no value at all. One thread, a fixed order."""
    ln = np.ascontiguousarray(lengths, dtype=np.float64)
    vals = np.ascontiguousarray(values, dtype=np.float64)
    if vals.ndim != 2 or vals.shape != (ln.shape[0], len(names)):
        raise ValueError("values must be [rows, fields]")
    out = np.zeros((len(names), 2), dtype=np.float64)
    total = float(_totals(ln, vals, out))
    per = {}
    for k, name in enumerate(names):
        have = out[k, 0] > 0.0
        per[name] = {
            "mean": float(out[k, 1] / out[k, 0]) if have else None,
            "metal": float(out[k, 1]) if have else None,
        }
    return {"count": int(ln.shape[0]), "length": total, "fields": per}
