"""Which rows of a field go into a chart and how their values are treated (ТЗ 7.25; ADR 0019 §3, §4, решение 7)
— the port of BF `routers/stats.py` (`SampleFilter`, `filter_values`) without SQL. One rule for every chart of
Э13 and for the sample series of a swath.

    SampleFilter(weight, weight_field, negatives, exclude_zero, bottom_cut, bottom_mode, top_cut, top_mode)
        the settings of a chart that choose the values; `SampleFilter.from_settings(settings)` reads them from
        the JSON of a chart (other keys are ignored) — StatsError `bad_param` {param} on a wrong one
    sample_weights(mode, length=None, weight_values=None) -> float64[n] | None
        the weight column: "none" — None (every row 1), "length" — the length of the interval, "field" — the
        values of the weight field, "length_field" — length × field (a tonnage weight, e.g. with density)
    select_values(values, weights=None, flt=None, tick=None) -> Selection
    select_sample(values, flt, length=None, weight_values=None, tick=None) -> Selection   # the two in one call
    Selection: values, weights (float64), rows (int64 — the numbers of the rows taken, ascending), counts
        .n   .sort(tick=None) -> `weighted.Sorted` (one sort for the statistics, the bins, the Q-Q)
        .require() -> self, or StatsError `no_values` whose hint says what the rows were left out for

The order of the rules is that of BF: rows without a value (NaN, ±inf) → rows without a weight (NaN, ±inf,
≤ 0) → values < 0 ("keep", "exclude", "half_abs": |c| / 2 — «<0.01» written as −0.01) → zeros (left out when
`exclude_zero`) → the bottom cut → the top cut ("exclude" leaves the row out, "cap" replaces the value by the
cut). The statistics are those of the values after the cuts. Defaults (ТЗ 7.25): weight by length, negatives
kept, the bottom cut excludes, the top cut caps.

`counts` (all shown in the window): rows — given; missing — no value; bad_weight — a value but no weight;
negative — values < 0 among the rows with a value and a weight (whatever is done with them); zero_excluded;
bottom_capped / bottom_excluded; capped / top_excluded; used — rows taken. A missing value never becomes 0.

One pass of a numba kernel over blocks of 2**20 rows, `tick(fraction)` between the blocks (it may raise to
cancel). The outputs are views of buffers of the size of the input: pages beyond `used` are never touched.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from numbers import Real
from typing import Any

import numpy as np

from bf_core.jit import njit

from .weighted import Sorted, StatsError, Tick, sort_groups

WEIGHTS = ("none", "length", "field", "length_field")
NEGATIVES = ("keep", "exclude", "half_abs")
CUT_MODES = ("exclude", "cap")
COUNT_KEYS = (
    "rows", "missing", "bad_weight", "negative", "zero_excluded", "bottom_capped", "bottom_excluded", "capped",
    "top_excluded", "used",
)  # fmt: skip
_BLOCK = 1 << 20  # rows between two calls of `tick`

_WEIGHT_NAMES = "none — без веса, length — длина интервала, field — поле, length_field — длина × поле"
_ENUMS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "weight": (WEIGHTS, "Вес", _WEIGHT_NAMES),
    "negatives": (NEGATIVES, "Правило для значений меньше нуля", "keep — оставить, exclude — исключить, "
                  "half_abs — половина модуля"),
    "bottom_mode": (CUT_MODES, "Режим нижней отсечки", "exclude — исключить, cap — заменить значением отсечки"),
    "top_mode": (CUT_MODES, "Режим верхней отсечки", "exclude — исключить, cap — заменить значением отсечки"),
}  # fmt: skip


def bad_param(param: str, message: str, hint: str) -> StatsError:
    """The refusal of a wrong setting of a chart (API — 422 `bad_param` {param})."""
    return StatsError("bad_param", message, hint, param=param)


@dataclass(frozen=True)
class SampleFilter:
    """The settings that choose the values of a chart (the keys of `charts.settings`, ADR 0019 §9)."""

    weight: str = "length"  # none | length | field | length_field
    weight_field: str | None = None  # the name of the weight field ("field", "length_field")
    negatives: str = "keep"  # keep | exclude | half_abs
    exclude_zero: bool = False
    bottom_cut: float | None = None
    bottom_mode: str = "exclude"  # exclude | cap
    top_cut: float | None = None
    top_mode: str = "cap"  # cap | exclude

    def __post_init__(self) -> None:
        for key, (allowed, title, names) in _ENUMS.items():
            value = getattr(self, key)
            if value not in allowed:
                raise bad_param(key, f"{title}: значение «{value}» неизвестно.", f"Допустимо: {names}.")
        if not isinstance(self.exclude_zero, bool):
            raise bad_param("exclude_zero", "«Исключить нули» — это да или нет.", "Передайте true или false.")
        for key, title in (("bottom_cut", "Нижняя отсечка"), ("top_cut", "Верхняя отсечка")):
            cut = getattr(self, key)
            if cut is None:
                continue
            if isinstance(cut, bool) or not isinstance(cut, Real) or not math.isfinite(cut):
                raise bad_param(key, f"{title} должна быть числом.", "Введите число или очистите поле отсечки.")
        if self.weight in ("field", "length_field") and not self.weight_field:
            raise bad_param("weight_field", "Не выбрано поле веса.", "Укажите поле веса или выберите другой вес.")
        if self.bottom_cut is not None and self.top_cut is not None and self.top_cut <= self.bottom_cut:
            raise bad_param(
                "top_cut", "Верхняя отсечка должна быть больше нижней.",
                f"Нижняя отсечка — {self.bottom_cut:g}: задайте верхнюю больше неё или уберите одну из них.",
            )  # fmt: skip

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> SampleFilter:
        """The filter of the settings of a chart; keys that are absent or None take the defaults."""
        own = {f.name for f in fields(cls)}
        given = {k: v for k, v in settings.items() if k in own and v is not None}
        if "weight_field" in given and not isinstance(given["weight_field"], str):
            raise bad_param("weight_field", "Поле веса задаётся именем.", "Выберите поле веса из списка.")
        for key in ("bottom_cut", "top_cut"):
            cut = given.get(key)
            if isinstance(cut, int) and not isinstance(cut, bool):
                given[key] = float(cut)
        return cls(**given)

    @property
    def weighted(self) -> bool:
        return self.weight != "none"


@dataclass(frozen=True)
class Selection:
    """The rows a chart takes: values after the rules, weights > 0, the numbers of the rows, the counts."""

    values: np.ndarray
    weights: np.ndarray
    rows: np.ndarray
    counts: dict[str, int]

    @property
    def n(self) -> int:
        return int(self.values.shape[0])

    def sort(self, *, tick: Tick | None = None) -> Sorted:
        """The sample sorted once with its running sums (`weighted.sort_groups`)."""
        return sort_groups(self.values, self.weights, tick=tick)[0]

    def require(self) -> Selection:
        """The selection itself, or the refusal `no_values` when no row is taken."""
        if self.n == 0:
            raise no_values(self.counts)
        return self


_LEFT_OUT = (
    ("missing", "без значения"), ("bad_weight", "без веса"), ("negative", "меньше нуля"),
    ("zero_excluded", "нулей исключено"), ("bottom_excluded", "ниже нижней отсечки"),
    ("top_excluded", "выше верхней отсечки"),
)  # fmt: skip


def no_values(counts: Mapping[str, int] | None = None) -> StatsError:
    """The refusal of a chart without values (API — 422 `no_values`); the hint names what `counts` left out."""
    c = counts or {}
    parts = [f"строк — {c['rows']}"] if "rows" in c else []
    parts += [f"{title} — {c[key]}" for key, title in _LEFT_OUT if c.get(key, 0) > 0]
    told = f"Отбор: {', '.join(parts)}. " if parts else ""
    return StatsError(
        "no_values", "Нет значений для графика после отбора.", f"{told}Проверьте поле, вес, скважины и отсечки."
    )


@njit(cache=True, nogil=True)
def _take(  # type: ignore[no-untyped-def]
    v, w, has_w, neg, drop_zero, use_lo, lo, lo_cap, use_hi, hi, hi_cap, a, b, k, ov, ow, orow, cnt
):
    """Rows a … b − 1 through the rules; the rows taken are written from position k on. Returns the new k."""
    for i in range(a, b):
        x = v[i]
        if not np.isfinite(x):
            cnt[0] += 1
            continue
        y = 1.0
        if has_w:
            y = w[i]
            if not (np.isfinite(y) and y > 0.0):
                cnt[1] += 1
                continue
        if x < 0.0:
            cnt[2] += 1
            if neg == 1:
                continue
            if neg == 2:
                x = -x / 2.0
        if drop_zero and x == 0.0:
            cnt[3] += 1
            continue
        if use_lo and x < lo:
            if lo_cap:
                cnt[4] += 1
                x = lo
            else:
                cnt[5] += 1
                continue
        if use_hi and x > hi:
            if hi_cap:
                cnt[6] += 1
                x = hi
            else:
                cnt[7] += 1
                continue
        ov[k] = x
        ow[k] = y
        orow[k] = i
        k += 1
    return k


def sample_weights(mode: str, length: Any = None, weight_values: Any = None) -> np.ndarray | None:
    """The weight column of the mode `mode` from the lengths of the intervals and the values of the weight
    field; None — no weights. A row whose weight is missing or ≤ 0 is left out by `select_values`."""
    if mode not in WEIGHTS:
        raise bad_param("weight", f"Вес: значение «{mode}» неизвестно.", f"Допустимо: {_WEIGHT_NAMES}.")
    if mode == "none":
        return None
    if mode != "field" and length is None:
        raise bad_param(
            "weight", "У таблицы нет длины интервала: вес по длине недоступен.",
            "Выберите «Без веса» или вес по полю.",
        )  # fmt: skip
    if mode != "length" and weight_values is None:
        raise bad_param("weight_field", "Не выбрано поле веса.", "Укажите поле веса или выберите другой вес.")
    if mode == "length":
        return np.asarray(length, dtype=np.float64)
    if mode == "field":
        return np.asarray(weight_values, dtype=np.float64)
    with np.errstate(invalid="ignore", over="ignore"):
        return np.asarray(np.asarray(length, dtype=np.float64) * np.asarray(weight_values, dtype=np.float64))


def select_values(
    values: Any, weights: Any = None, flt: SampleFilter | None = None, *, tick: Tick | None = None
) -> Selection:
    """The rows of `values` a chart takes by the rules of `flt` (see the module docstring); `weights` None —
    every row weighs 1. `flt.weight` is not read here: the weights are those given."""
    f = SampleFilter(weight="none") if flt is None else flt
    v = np.ascontiguousarray(values, dtype=np.float64)
    if v.ndim != 1:
        raise ValueError("values must be 1D")
    has_w = weights is not None
    w = np.ascontiguousarray(weights, dtype=np.float64) if has_w else v
    if w.shape != v.shape:
        raise ValueError("values and weights must be of one length")
    n = v.shape[0]
    ov, ow, orow = np.empty(n), np.empty(n), np.empty(n, dtype=np.int64)
    cnt = np.zeros(8, dtype=np.int64)
    use_lo, use_hi = f.bottom_cut is not None, f.top_cut is not None
    lo = float(f.bottom_cut) if f.bottom_cut is not None else 0.0
    hi = float(f.top_cut) if f.top_cut is not None else 0.0
    neg = NEGATIVES.index(f.negatives)
    k = 0
    for a in range(0, n, _BLOCK):
        b = min(a + _BLOCK, n)
        k = _take(v, w, has_w, neg, f.exclude_zero, use_lo, lo, f.bottom_mode == "cap", use_hi, hi,
                  f.top_mode == "cap", a, b, k, ov, ow, orow, cnt)  # fmt: skip
        if tick is not None:
            tick(b / n)
    if n == 0 and tick is not None:
        tick(1.0)
    counts = dict(zip(COUNT_KEYS, [n, *(int(c) for c in cnt), int(k)], strict=True))
    return Selection(ov[:k], ow[:k], orow[:k], counts)


def select_sample(
    values: Any, flt: SampleFilter, *, length: Any = None, weight_values: Any = None, tick: Tick | None = None
) -> Selection:
    """`select_values` with the weights of `flt.weight` built from the columns `length` (the lengths of the
    intervals) and `weight_values` (the values of the field `flt.weight_field`)."""
    return select_values(values, sample_weights(flt.weight, length, weight_values), flt, tick=tick)


def warm() -> None:
    """Compile the kernel on a few rows (the `stats` step of the warm-up)."""
    rows = np.array([1.0, -1.0, 0.0, np.nan])
    select_values(rows, rows, SampleFilter(weight="none", negatives="half_abs", bottom_cut=0.1, top_cut=0.9))
    select_values(rows, None, SampleFilter(weight="none"))
