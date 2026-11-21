"""Thresholds of QAQC (ТЗ 8.4.2; ADR 0020 §3 «Пороги», §4 «Сравнения с порогами»): the defaults and the ranges of
ТЗ, their check, the comparisons with a tolerance and the codes of a status the kernels share.

    Limits(sigma_warn=2, sigma_fail=3, run=9, k_blank=5, k_dup=5, th_group=11, hard_p=10, hard_c=20, hard_f=30,
           hard_ump=10, hard_share=90, n_boot=1000, seed=12345)
        frozen; every value is checked on creation — `QaqcError` `bad_param` {param} when out of its range
        .as_dict() -> {name: value}                    in the order of the fields
    Limits.parse(raw) -> Limits                        a mapping of the settings (None — the defaults); a key that
                                                       is not a threshold — `bad_param` {param: key}
    RANGES {name: Range(lo, hi, default, title, whole, odd)}, ranges() -> JSON-ready {name: {min, max, default,
        integer, odd}} for the window «Пороги»
    le(a, b), gt(a, b)                                 comparisons with the tolerance `TOL`, numpy broadcasting
    QaqcError(code, message, hint, param=None)         a refusal of QAQC, .as_dict() -> {code, message, hint}

Ranges (ТЗ 8.4.2, ADR 0020 §3): `sigma_warn`, `sigma_fail` 1–5 and warn < fail; `run` 5–20 (whole); `k_blank`
1–50; `k_dup` 0–50; `th_group` 5–51 (whole, odd); `hard_p`, `hard_c`, `hard_f`, `hard_ump` 1–100 %; `hard_share`
50–100 %; `n_boot` 100–5000 (whole); `seed` 0 … 2⁶³ − 1 (whole). A whole threshold takes 9 and 9.0, not 9.5; a
flag (true, false), a text, NaN are refused. The borders belong to the range. `RANGES` is the one table of the
ranges and the defaults: the settings of the project are checked by it.

Comparisons (ADR 0020, находка 7: in float64 (1,10 − 1,00) / 0,05 = 2,0000000000000018): `le(a, b)` is
a ≤ b + 1e−9·max(1, |b|) and `gt(a, b)` is a > b + 1e−9·max(1, |b|) — its negation for numbers, so a value right
on a border (2σ, 3σ, k_б·DL, HARD 10 %) falls on the soft side. With NaN on either side both are False: what is
missing is neither «норма» nor «отказ», a kernel leaves it «не оценён».

Constants of ТЗ (not settings): the classes of the bias of a CRM 5 and 10 %, the window of the moving average
10, at least 50 pairs for Thompson–Howarth. The module imports nothing heavy (numpy — inside `le` and `gt`
only): the settings of the project are checked with it before BF-READY.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from numbers import Integral, Real
from typing import Any

TOL = 1e-9  # the tolerance of a comparison with a threshold, relative from 1 up
BIAS_OK, BIAS_ATTENTION = 5.0, 10.0  # |bias| of a CRM, %: ≤ 5 — «норма», ≤ 10 — «внимание», more — «недопустимо»
MA_WINDOW = 10  # values of the moving average of a control chart
TH_MIN_PAIRS = 50  # pairs Thompson–Howarth needs

# The status of a sample, of a batch and the class of a bias: one scale for every section (and one palette).
NOT_ASSESSED, NORM, WARN, FAIL = -1, 0, 1, 2


class QaqcError(ValueError):
    """A refusal of QAQC: {code, message, hint} in Russian, shown to the user (API — 422). The same shape as
    `stats.weighted.StatsError`; `param` — the key of the parameter at fault."""

    def __init__(self, code: str, message: str, hint: str = "", *, param: str | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.hint, self.param = code, message, hint, param

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


@dataclass(frozen=True)
class Range:
    """The range of a threshold (the borders belong to it), its default and its name in the window."""

    lo: float
    hi: float
    default: float
    title: str
    whole: bool = False
    odd: bool = False


RANGES: dict[str, Range] = {
    "sigma_warn": Range(1, 5, 2.0, "Порог предупреждения, σ"),
    "sigma_fail": Range(1, 5, 3.0, "Порог отказа, σ"),
    "run": Range(5, 20, 9, "Длина серии по одну сторону μ", whole=True),
    "k_blank": Range(1, 50, 5.0, "Порог бланка, × DL"),
    "k_dup": Range(0, 50, 5.0, "Отсев дубликатов, × DL"),
    "th_group": Range(5, 51, 11, "Размер группы Томпсона–Ховарта", whole=True, odd=True),
    "hard_p": Range(1, 100, 10.0, "Критерий HARD пульповых дубликатов, %"),
    "hard_c": Range(1, 100, 20.0, "Критерий HARD дроблёных дубликатов, %"),
    "hard_f": Range(1, 100, 30.0, "Критерий HARD полевых дубликатов, %"),
    "hard_ump": Range(1, 100, 10.0, "Критерий HARD внешнего контроля, %"),
    "hard_share": Range(50, 100, 90.0, "Доля пар в пределах критерия HARD, %"),
    "n_boot": Range(100, 5000, 1000, "Число реализаций бутстрапа", whole=True),
    "seed": Range(0, 2**63 - 1, 12345, "Зерно генератора", whole=True),
}


def _text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value).replace(".", ",")


def _refusal(name: str) -> QaqcError:
    r = RANGES[name]
    kind = "нечётное целое число" if r.odd else "целое число" if r.whole else "число"
    return QaqcError(
        "bad_param",
        f"«{r.title}» — {kind} от {_text(r.lo)} до {_text(r.hi)}.",
        f"Значение по умолчанию — {_text(r.default)}.",
        param=name,
    )


def _checked(name: str, value: Any) -> float | int:
    """The value of a threshold inside its range (a whole one — as int), or the refusal `bad_param`."""
    r = RANGES[name]
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _refusal(name)
    if not r.whole:
        number = float(value)
        if not (math.isfinite(number) and r.lo <= number <= r.hi):
            raise _refusal(name)
        return number
    if isinstance(value, Integral):
        whole = int(value)  # as it is: a seed past 2⁵³ does not go through float
    elif float(value).is_integer():
        whole = int(float(value))
    else:
        raise _refusal(name)
    if not r.lo <= whole <= r.hi or (r.odd and whole % 2 == 0):
        raise _refusal(name)
    return whole


@dataclass(frozen=True)
class Limits:
    """The thresholds of QAQC of a project (see the module docstring); always inside the ranges of ТЗ."""

    sigma_warn: float = 2.0
    sigma_fail: float = 3.0
    run: int = 9
    k_blank: float = 5.0
    k_dup: float = 5.0
    th_group: int = 11
    hard_p: float = 10.0
    hard_c: float = 20.0
    hard_f: float = 30.0
    hard_ump: float = 10.0
    hard_share: float = 90.0
    n_boot: int = 1000
    seed: int = 12345

    def __post_init__(self) -> None:
        for f in fields(self):
            object.__setattr__(self, f.name, _checked(f.name, getattr(self, f.name)))
        if not self.sigma_warn < self.sigma_fail:
            raise QaqcError(
                "bad_param",
                "Порог предупреждения должен быть меньше порога отказа.",
                f"Сейчас предупреждение — {_text(self.sigma_warn)}σ, отказ — {_text(self.sigma_fail)}σ. "
                "По умолчанию — 2σ и 3σ.",
                param="sigma_warn",
            )

    @classmethod
    def parse(cls, raw: Mapping[str, Any] | None) -> Limits:
        """The thresholds of a mapping of the settings: the keys that are missing take their defaults."""
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise QaqcError(
                "bad_param", "Пороги QAQC — это набор «название: значение».", "Передайте объект с порогами.",
                param="limits",
            )  # fmt: skip
        for key in raw:
            if key not in RANGES:
                raise QaqcError(
                    "bad_param", f"Неизвестный порог QAQC: «{key}».", f"Пороги: {', '.join(RANGES)}.", param=str(key)
                )
        return cls(**raw)

    def as_dict(self) -> dict[str, float | int]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def ranges() -> dict[str, dict[str, Any]]:
    """The ranges of the thresholds for the window «Пороги» (JSON-ready)."""
    return {
        name: {"min": r.lo, "max": r.hi, "default": r.default, "integer": r.whole, "odd": r.odd, "title": r.title}
        for name, r in RANGES.items()
    }


def le(a: Any, b: Any) -> Any:
    """a ≤ b with the tolerance of a threshold (see the module docstring): a border belongs to the soft side."""
    import numpy as np

    return np.less_equal(a, b + TOL * np.maximum(1.0, np.abs(b)))


def gt(a: Any, b: Any) -> Any:
    """a > b with the tolerance of a threshold: the negation of `le` for numbers; False when either is NaN."""
    import numpy as np

    return np.greater(a, b + TOL * np.maximum(1.0, np.abs(b)))
