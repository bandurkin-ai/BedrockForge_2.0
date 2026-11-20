"""Metal equivalent (ТЗ 8.2.2, 8.0.1; ADR 0018 §7): a field of the source table

    Eq = g_1 + Σ_(e≥2) g_e·(P_e·R_e·k_e) / (P_1·R_1·k_1)

P — the price, R — the recovery (0–1], k — the unit of the grade in the unit of the price: grams of metal in a
tonne of ore for 1 unit of the grade, divided by the grams the price is quoted for.

    grade   г/т 1 · ppm 1 · ppb 0,001 · % 10 000 · кг/т 1000          (g of metal per tonne)
    price   $/унц 31,1034768 · $/г 1 · $/кг 1000 · $/т 1 000 000      (g the price is for; the troy ounce)
    k = grade / price:  г/т and $/унц — 1/31,1034768;  % and $/т — 0,01;  ppm = г/т;  ppb — ×0,001

    p = EquivalentParams.of(body, units={"AU": "г/т", "CU": "%"})     # CompositeError {code, message, hint}
        body: base, base_price, base_recovery (0,9), base_unit?, base_price_unit?, nan_zero?, name?,
              others: [{field, price, recovery, unit?, price_unit?}] or «ПОЛЕ цена извлечение; …»
        p.fields  the fields to read: the base, then the others;  p.name — the field to write (`<base>EQ`)
        p.factors  (P_e·R_e·k_e) / (P_1·R_1·k_1) of every other metal
    eq = equivalent([g_1, g_2, …], p, tick=…) -> Equivalent(values float64[n], report)
        report: name, base, unit, factors [{field, factor, k, unit, price_unit, price, recovery, text}],
                n, n_missing, n_zeroed, nan_zero

Units. The unit of a grade: the one of the request (the window sends `base_unit`, `others[].unit`), else the
unit of the field (`units`, as the table stores it: «g/t», «г/т», «ppm», «%», «pct» …), else an error
`unit_unknown` — a unit is never guessed. The unit of a price: the one of the request, else by the grade —
% and кг/т — $/т, the rest — $/унц.

Missing grades. A sample without a grade of any of the metals has no equivalent (NaN). With `nan_zero`
(«пустое содержание считать нулём», an explicit choice) a missing grade counts as 0 when the sample has at
least one of the grades; a sample with none of them stays without a value — it was not assayed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .intervals import CompositeError
from .strips import Tick

TROY_OUNCE_G = 31.1034768  # ТЗ 8.0.1
GRADE_G_PER_T = {"г/т": 1.0, "ppm": 1.0, "ppb": 0.001, "%": 10_000.0, "кг/т": 1000.0}
PRICE_G = {"$/унц": TROY_OUNCE_G, "$/г": 1.0, "$/кг": 1000.0, "$/т": 1_000_000.0}
_GRADE_ALIASES = {
    "г/т": "г/т", "g/t": "г/т", "гр/т": "г/т", "gpt": "г/т",
    "ppm": "ppm", "мг/кг": "ppm", "mg/kg": "ppm",
    "ppb": "ppb", "мг/т": "ppb", "mg/t": "ppb",
    "%": "%", "pct": "%", "percent": "%", "проц": "%",
    "кг/т": "кг/т", "kg/t": "кг/т",
}  # fmt: skip
_PRICE_ALIASES = {
    "$/унц": "$/унц", "$/oz": "$/унц", "$/toz": "$/унц", "usd/oz": "$/унц", "$/унция": "$/унц",
    "$/г": "$/г", "$/g": "$/г", "usd/g": "$/г",
    "$/кг": "$/кг", "$/kg": "$/кг", "usd/kg": "$/кг",
    "$/т": "$/т", "$/t": "$/т", "usd/t": "$/т",
}  # fmt: skip
_BLOCK = 1 << 20  # rows between two ticks
_HINT_GRADE = "Единицы содержания: г/т, ppm, ppb, %, кг/т."
_HINT_PRICE = "Единицы цены: $/унц, $/г, $/кг, $/т."
_HINT_OTHERS = "Запишите «ПОЛЕ цена извлечение» через «;», например «CU 9000 0,9; AG 25 0,8»."


def _norm(text: Any) -> str:
    return "".join(str(text or "").split()).lower()


def grade_unit(text: Any) -> str | None:
    """The unit of a grade as a table or a request writes it → one of `GRADE_G_PER_T`; unknown or empty — None."""
    return _GRADE_ALIASES.get(_norm(text))


def price_unit(text: Any) -> str | None:
    """The unit of a price → one of `PRICE_G`; unknown or empty — None."""
    return _PRICE_ALIASES.get(_norm(text))


def default_price_unit(grade: str) -> str:
    """The unit of the price that goes with a grade unit: % and кг/т — $/т, the rest — $/унц."""
    return "$/т" if grade in ("%", "кг/т") else "$/унц"


def k_factor(grade: str, price: str) -> float:
    """k: 1 unit of the grade in the unit of the price (г/т and $/унц — 1/31,1034768; % and $/т — 0,01)."""
    return GRADE_G_PER_T[grade] / PRICE_G[price]


def _bad(message: str, hint: str, param: str, code: str = "bad_param") -> CompositeError:
    return CompositeError(code, message, hint, param=param)


def _positive(raw: Any, label: str, param: str, top: float = math.inf) -> float:
    """A number of (0; top] — «9 000,5» of a text request is 9000.5 — or CompositeError `bad_param`."""
    try:
        if isinstance(raw, bool):
            raise TypeError
        text = raw.replace(" ", "").replace("\u00a0", "").replace(",", ".") if isinstance(raw, str) else raw
        value = float(text)
    except (TypeError, ValueError):
        value = math.nan
    if not (0.0 < value <= top):  # NaN fails too
        if math.isinf(top):
            raise _bad(f"{label}: нужно число больше нуля.", "Введите положительное число, например 2000.", param)
        raise _bad(f"{label}: нужна доля больше 0 и не больше 1.", "Введите долю, например 0,9 (это 90 %).", param)
    if math.isinf(value):
        raise _bad(f"{label}: нужно конечное число.", "Введите положительное число, например 2000.", param)
    return value


def _flag(raw: Any) -> bool:
    """A boolean of a request: true / false, 1 / 0, «да» / «нет»."""
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "да", "on")
    return bool(raw)


@dataclass(frozen=True)
class Metal:
    """A metal of the equivalent: its field, price, recovery and units; k — `k_factor(unit, price_unit)`."""

    field: str
    price: float
    recovery: float
    unit: str
    price_unit: str

    @property
    def k(self) -> float:
        return k_factor(self.unit, self.price_unit)

    @property
    def worth(self) -> float:
        """P·R·k — the value of 1 unit of the grade in a tonne of ore."""
        return self.price * self.recovery * self.k


def _metal(
    name: Any, price: Any, recovery: Any, unit: Any, p_unit: Any, units: Mapping[str, Any], label: str, param: str
) -> Metal:
    fld = str(name or "").strip()
    if not fld:
        raise _bad(f"{label}: не задано поле содержания.", "Выберите числовое поле таблицы.", param)
    label = f"{label} «{fld}»"
    cost = _positive(price, f"{label}: цена", param)
    share = _positive(recovery, f"{label}: извлечение", param, 1.0)
    if unit is not None and str(unit).strip():
        grade = grade_unit(unit)
        if grade is None:
            raise _bad(f"{label}: единица содержания «{unit}» не поддерживается.", _HINT_GRADE, param)
    else:
        known = next((u for f, u in units.items() if str(f).lower() == fld.lower()), None)
        grade = grade_unit(known)
        if grade is None:
            what = f"единица поля «{known}» не распознана" if str(known or "").strip() else "у поля нет единицы"
            raise _bad(
                f"{label}: не задана единица содержания — {what}.",
                f"Укажите единицу в окне «Эквивалент металла» или в свойствах поля. {_HINT_GRADE}",
                param,
                "unit_unknown",
            )
    if p_unit is not None and str(p_unit).strip():
        cost_unit = price_unit(p_unit)
        if cost_unit is None:
            raise _bad(f"{label}: единица цены «{p_unit}» не поддерживается.", _HINT_PRICE, param)
    else:
        cost_unit = default_price_unit(grade)
    return Metal(fld, cost, share, grade, cost_unit)


def _others(raw: Any) -> list[dict[str, Any]]:
    """`others` of a request: a list of {field, price, recovery, unit?, price_unit?} or «ПОЛЕ цена извлечение; …»."""
    if isinstance(raw, str):
        out = []
        for part in raw.replace("\n", ";").split(";"):
            if not part.strip():
                continue
            bits = part.split()
            if len(bits) != 3:
                raise _bad(f"Прочие металлы: «{part.strip()}» — нужно «ПОЛЕ цена извлечение».", _HINT_OTHERS, "others")
            out.append({"field": bits[0], "price": bits[1], "recovery": bits[2]})
        return out
    if isinstance(raw, Sequence) and all(isinstance(x, Mapping) for x in raw):
        return [dict(x) for x in raw]
    raise _bad("Прочие металлы: нужен список «поле, цена, извлечение».", _HINT_OTHERS, "others")


@dataclass(frozen=True)
class EquivalentParams:
    base: Metal
    others: tuple[Metal, ...]
    name: str
    nan_zero: bool = False

    @classmethod
    def of(cls, raw: Mapping[str, Any], units: Mapping[str, Any] | None = None) -> EquivalentParams:
        """From the body of `composite.equivalent`; `units` — the units of the fields of the table, used when
        the request names none. CompositeError `bad_param` / `unit_unknown` with a hint."""
        known = units or {}
        recovery = raw.get("base_recovery")
        base = _metal(
            raw.get("base"), raw.get("base_price"), 0.9 if recovery is None or recovery == "" else recovery,
            raw.get("base_unit"), raw.get("base_price_unit"), known, "Основной металл", "base",
        )  # fmt: skip
        rows = _others(raw.get("others") if raw.get("others") is not None else [])
        if not rows:
            raise _bad("Прочие металлы не заданы.", _HINT_OTHERS, "others")
        others = tuple(
            _metal(r.get("field"), r.get("price"), r.get("recovery"), r.get("unit"), r.get("price_unit"), known,
                   "Металл", "others")
            for r in rows
        )  # fmt: skip
        seen = {base.field.lower()}
        for m in others:
            if m.field.lower() in seen:
                same = m.field.lower() == base.field.lower()
                clash = "основной металл не может быть среди прочих" if same else "задан дважды"
                raise _bad(f"Прочие металлы: «{m.field}» — {clash}.", "Оставьте каждое поле один раз.", "others")
            seen.add(m.field.lower())
        name = str(raw.get("name") or "").strip() or f"{base.field}EQ"
        if name.lower() in seen:
            hint = f"Задайте другое имя, например {base.field}EQ."
            raise _bad(f"Имя поля «{name}» занято металлом эквивалента.", hint, "name")
        return cls(base, others, name, _flag(raw.get("nan_zero", False)))

    @property
    def fields(self) -> list[str]:
        return [self.base.field, *(m.field for m in self.others)]

    @property
    def factors(self) -> list[float]:
        """(P_e·R_e·k_e) / (P_1·R_1·k_1) of the other metals, in their order."""
        return [m.worth / self.base.worth for m in self.others]


@dataclass(frozen=True)
class Equivalent:
    values: np.ndarray
    report: dict[str, Any]


def _text(x: float) -> str:
    return f"{x:.6g}".replace(".", ",")


def equivalent(columns: Sequence[Any], params: EquivalentParams, *, tick: Tick | None = None) -> Equivalent:
    """Eq of every row. `columns` — the grades of `params.fields` (the base first), float64[n] each. The sum
    goes in the order of the metals; `tick(fraction)` is called between blocks of rows and may raise."""
    cols = [np.ascontiguousarray(c, dtype=np.float64) for c in columns]
    if len(cols) != len(params.fields) or any(c.ndim != 1 or c.shape != cols[0].shape for c in cols):
        raise ValueError("columns must be the 1D grades of params.fields, of one length")
    n = cols[0].shape[0]
    weights = [1.0, *params.factors]
    out = np.empty(n)
    n_zeroed = 0
    for lo in range(0, n, _BLOCK):
        hi = min(lo + _BLOCK, n)
        if params.nan_zero:
            miss = [np.isnan(c[lo:hi]) for c in cols]
            none = np.logical_and.reduce(miss)
            acc = np.zeros(hi - lo)
            for c, f, m in zip(cols, weights, miss, strict=True):
                acc += np.where(m, 0.0, c[lo:hi]) * f
                n_zeroed += int((m & ~none).sum())
            acc[none] = np.nan
        else:
            acc = cols[0][lo:hi].copy()
            for c, f in zip(cols[1:], weights[1:], strict=True):
                acc += c[lo:hi] * f  # NaN in any metal — NaN
        out[lo:hi] = acc
        if tick is not None:
            tick(hi / n)
    base = params.base
    n_missing = int(np.isnan(out).sum())
    report = {
        "name": params.name,
        "base": base.field,
        "unit": base.unit,
        "price": base.price,
        "recovery": base.recovery,
        "price_unit": base.price_unit,
        "k": base.k,
        "factors": [
            {
                "field": m.field,
                "factor": f,
                "k": m.k,
                "unit": m.unit,
                "price_unit": m.price_unit,
                "price": m.price,
                "recovery": m.recovery,
                "text": f"1 {m.unit} {m.field} = {_text(f)} {base.unit} {base.field}-экв",
            }
            for m, f in zip(params.others, params.factors, strict=True)
        ],
        "n": n - n_missing,
        "n_missing": n_missing,
        "n_zeroed": n_zeroed,
        "nan_zero": params.nan_zero,
    }
    return Equivalent(out, report)
