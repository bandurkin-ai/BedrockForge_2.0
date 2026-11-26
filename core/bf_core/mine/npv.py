"""The calendar and the NPV of the pit-by-pit report (подшаг 21.8; ТЗ 8.11.4; ADR 0028 decisions 18, 19, 34).

    npv(report, calendar) -> Npv(best [N], worst [N])       n = 1…N: the NPV of the best and of the worst case of n
    npv_sequence(o, w, cf, calendar) -> float               one sequence of parts i (ore, waste, CF) in order

The calendar (`spec.Calendar`: Q_р `q_ore`, Q_гм `q_mass` t/year, r `rate` per year) is a VIEW of the stored result: not
a part of the fingerprint of the scenario; the NPV is recomputed from `data` and `benches` (`report.Report`), nothing
is read from the model. A part i is an increment ΔC_i (the best case: ΔC_1 … ΔC_n) or a bench of C_n (the worst case:
from the top level of the model down), with the ore O_i and the waste W_i in tonnes and CF_i in $. The formulas of the
ТЗ word for word:

    τ_i = max(O_i / Q_р; (O_i + W_i) / Q_гм)                 (Q_гм not set — no limit: τ_i = O_i / Q_р), years
    t_i = Σ_(j < i) τ_j
    NPV = Σ_i CF_i · ((1 + r)^(−t_i) − (1 + r)^(−(t_i + τ_i))) / (τ_i · ln(1 + r))
    τ_i = 0 → CF_i · (1 + r)^(−t_i);   r = 0 → the limit, Σ CF_i

The term is written as (1 + r)^(−t_i) · (−expm1(−x)) / x with x = τ_i·ln(1 + r): the same number, without the
cancellation of two close powers; x = 0 (τ_i = 0 or r = 0) gives the factor 1 — the two special cases of the ТЗ are the
limit of the formula, so the NPV is continuous in τ and r. The best case is a prefix: t_i of a part does not depend on
n, so the NPV of n is the sum of the first n terms. Sums are exact (`math.fsum`): the same bits every time.

The calendar without Q_р gives no NPV: 422 `bad_param` (`calendar.q_ore`) with the hint (the report is shown without
the NPV, `spec.validate` warns `no_calendar`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .econ import bad_param
from .report import Report
from .spec_parts import Calendar


@dataclass(frozen=True)
class Npv:
    """The NPV of the best and of the worst case of n = 1…N shells ($)."""

    best: np.ndarray
    worst: np.ndarray


def check_calendar(cal: Calendar) -> None:
    """422 `bad_param {param}` if the calendar cannot give an NPV: Q_р is required (Q_р > 0), Q_гм is none or > 0,
    r ≥ 0."""
    q = cal.q_ore
    if q is None:
        raise bad_param("calendar.q_ore", "Не задана производительность переработки Q_р: NPV не рассчитать.",
                        "Укажите Q_р в календаре, например 1 000 000 т/год.")  # fmt: skip
    if not (math.isfinite(q) and q > 0.0):
        raise bad_param("calendar.q_ore", "Производительность переработки Q_р должна быть больше нуля.",
                        "Например 1 000 000 т/год.")  # fmt: skip
    if cal.q_mass is not None and not (math.isfinite(cal.q_mass) and cal.q_mass > 0.0):
        raise bad_param("calendar.q_mass", "Производительность по горной массе Q_гм должна быть больше нуля.",
                        "Оставьте поле пустым, если ограничения нет.")  # fmt: skip
    if not (math.isfinite(cal.rate) and cal.rate >= 0.0):
        raise bad_param("calendar.rate", "Ставка дисконтирования r не может быть отрицательной.",
                        "Например 0,08 — восемь процентов в год.")  # fmt: skip


def durations(o: np.ndarray, w: np.ndarray, cal: Calendar) -> np.ndarray:
    """τ_i [years] of the parts: max(O_i/Q_р; (O_i + W_i)/Q_гм); Q_гм not set — O_i/Q_р."""
    check_calendar(cal)
    assert cal.q_ore is not None
    tau = np.asarray(o, dtype=np.float64) / cal.q_ore
    if cal.q_mass is not None:
        tau = np.maximum(tau, (np.asarray(o, dtype=np.float64) + np.asarray(w, dtype=np.float64)) / cal.q_mass)
    return tau


def terms(o: np.ndarray, w: np.ndarray, cf: np.ndarray, cal: Calendar) -> np.ndarray:
    """The discounted CF_i of the parts in order (module docstring): their sum is the NPV, a prefix sum is the NPV of
    the first parts."""
    tau = durations(o, w, cal)
    cf = np.asarray(cf, dtype=np.float64)
    t = np.concatenate(([0.0], np.cumsum(tau)[:-1])) if tau.size else tau  # t_i = Σ_(j<i) τ_j, in order
    ln = math.log1p(cal.rate)
    x = tau * ln
    small = x <= 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        f = np.where(small, 1.0, -np.expm1(-x) / np.where(small, 1.0, x))
    return cf * np.exp(-t * ln) * f  # type: ignore[no-any-return]


def npv_sequence(o: np.ndarray, w: np.ndarray, cf: np.ndarray, cal: Calendar) -> float:
    """The NPV of one sequence of parts (O_i, W_i, CF_i) in order."""
    return math.fsum(terms(o, w, cf, cal).tolist())


def npv_best(o: np.ndarray, w: np.ndarray, cf: np.ndarray, cal: Calendar) -> np.ndarray:
    """[N]: the NPV of ΔC_1 … ΔC_n in order, n = 1…N (a prefix of the terms)."""
    tm = terms(o, w, cf, cal).tolist()
    return np.array([math.fsum(tm[:n]) for n in range(1, len(tm) + 1)], dtype=np.float64)


def npv_worst(benches: dict[str, np.ndarray], n_shells: int, cal: Calendar) -> np.ndarray:
    """[N]: the NPV of C_n mined bench by bench from the top (`Report.benches`), n = 1…N. An n with no bench has the
    NPV 0 (nothing is mined)."""
    check_calendar(cal)
    n_col = np.asarray(benches["n"])
    out = np.zeros(n_shells, dtype=np.float64)
    if not n_col.size:
        return out
    lo = np.searchsorted(n_col, np.arange(1, n_shells + 1), side="left")
    hi = np.searchsorted(n_col, np.arange(1, n_shells + 1), side="right")
    for n in range(n_shells):
        a, b = int(lo[n]), int(hi[n])
        if b > a:
            out[n] = npv_sequence(benches["ore_t"][a:b], benches["waste_t"][a:b], benches["cf"][a:b], cal)
    return out


def npv(rep: Report, cal: Calendar) -> Npv:
    """The NPV of the best and of the worst case of every n of the report `rep` under the calendar `cal`."""
    check_calendar(cal)
    o, w, cf = rep.increments()
    return Npv(npv_best(o, w, cf, cal), npv_worst(rep.benches, rep.n_shells, cal))
