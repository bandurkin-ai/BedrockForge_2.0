"""The parts of the parameters of a pit scenario (подшаг 21.1; ТЗ 8.11.2, 8.11.5; ADR 0028 решения 3–12, 19, 22, 30;
contract — docs/progress/e21-api.md §2–§3, §13). Pure: no database, no numpy. The scenario itself — `spec.py`
(re-exports this file); the readers of the parts — `spec_parse.py`; the check of completeness — `spec_validate.py`.

    refuse(code, message=None, hint=None, **extra) -> ApiError        REFUSALS[code] (решение 30)
    grade_k(unit) -> k_e (решение 3), price_label(unit), grade_label(unit)
    Element, RockRule, RockBy, RockType, Rocks, Sector, Domain, Slopes, Mining, Calendar      the parts
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate.spec_parts import text_of
from bf_core.geostat.variogram.spec import _number, bad
from bf_core.resources.spec_parts import TROY_OUNCE_G, norm_unit

ALGO = "pit-1"  # решение 34: a change of the core that changes PIT or the report raises it
VERSION = 1
KIND = "pit_scenario"
MAX_SCENARIOS = 200  # решение 20
MAX_ELEMENTS, MAX_TYPES, MAX_RULES = 20, 100, 50
MAX_SECTORS, MAX_DOMAINS = 16, 16  # ТЗ 8.11.2
MAX_SHELLS = 200  # ТЗ 8.11.5
RF_MIN, RF_MAX = 0.01, 5.0
RF_DEFAULT = (0.30, 1.50, 0.05)  # from, to, step: 25 shells
ANGLE_MIN, ANGLE_MAX = 10.0, 80.0  # degrees
K_MIN, K_MAX = 1, 20
TOL_DEFAULT, TOL_MIN, TOL_MAX = 2.5, 0.5, 10.0  # degrees
RATE_DEFAULT, RATE_MAX = 0.08, 0.5  # r, a fraction per year (8 %; 0–50 %)
H_GL_DEFAULT, C_GL_DEFAULT = 10.0, 0.0  # решение 3
MONEY_MAX = 1e12  # prices, costs, Z_ref, Q: |x| ≤ 10¹²
Z_LIMIT = 1e7
NAME_MAX, FIELD_MAX, PREFIX_MAX = 200, 100, 40
SOLVERS = ("auto", "hpf", "ortools")
ROCK_BY_KINDS = ("field", "rules")
EMPTY_LABEL = "(пусто)"  # the empty category of a rock field and the empty cells of the model (решение 7)
OTHER_LABEL = "Прочие"  # no rule of the rock rules fits
ALL_LABEL = "Все породы"  # no rock field and no rules: one type for every block
_PREFIX = re.compile(r"\w{0,40}")

#: units of a grade (решение 3): g/t, ppm, ppb → troy ounces per (tonne × grade); % → tonnes of metal per tonne
GRADE_UNITS = ("g/t", "ppm", "ppb", "%")
GRADE_K = {"g/t": 1.0 / TROY_OUNCE_G, "ppm": 1.0 / TROY_OUNCE_G, "ppb": 1e-3 / TROY_OUNCE_G, "%": 0.01}
GRADE_LABELS = {"g/t": "г/т", "ppm": "ppm", "ppb": "ppb", "%": "%"}
PRICE_LABELS = {"g/t": "$/унц", "ppm": "$/унц", "ppb": "$/унц", "%": "$/т"}  # the price per unit of metal

#: решение 30 (+ interpretations of docs/progress/e21-api.md §15): code → (status, message, hint)
REFUSALS: dict[str, tuple[int, str, str]] = {
    "pit_not_ready": (422, "Сценарий не готов к расчёту.", "Задайте недостающие параметры и повторите."),
    "variable_grid": (422, "Оптимизация карьера требует постоянного шага сетки.",
                      "Регуляризуйте модель: Блочные модели → Регуляризация."),
    "pit_too_large": (422, "Граф слишком велик: больше 10 млн узлов или охват не помещается в индекс.",
                      "Укрупните модель регуляризацией или сузьте ограничитель."),
    "pit_density_missing": (422, "У блоков нет плотности, а плотность по умолчанию не задана.",
                            "Задайте плотность по умолчанию или заполните поле плотности."),
    "pit_empty": (422, "При максимальном коэффициенте выручки нет блоков с положительной стоимостью.",
                  "Проверьте цены, извлечения, затраты и коэффициенты выручки."),
    "filter_field_missing": (422, "В ограничителе используется поле, которого нет в модели.",
                             "Постройте поле или уберите условие."),
    "pit_solid_failed": (422, "Не удалось построить замкнутый каркас карьера.",
                         "Выберите другую оболочку или постройте поверхность и каркас по разности."),
    "too_many_scenarios": (422, "В проекте уже 200 сценариев карьера.", "Удалите ненужные сценарии."),
    "pit_internal": (500, "Нарушено условие замкнутости оболочки: расчёт остановлен.",
                     "Скопируйте журнал операции и отправьте разработчику."),
    "pit_scenario_not_found": (404, "Сценарий карьера не найден.", "Обновите раздел «Карьер»."),
    "block_model_not_found": (404, "Блочная модель не найдена.", "Обновите дерево проекта и выберите модель заново."),
    "block_field_not_found": (404, "Поле блочной модели не найдено.", "Обновите свойства модели."),
    "wireframe_not_found": (404, "Каркас (рельеф) не найден.", "Обновите раздел «Каркасы» и выберите рельеф заново."),
    "filter_not_found": (404, "Фильтр не найден.", "Обновите библиотеку фильтров и выберите фильтр заново."),
    "pit_running": (409, "Сценарий сейчас считается.", "Дождитесь окончания задания или отмените его."),
    "pit_no_result": (409, "У сценария нет результата.", "Запустите расчёт сценария."),
    "version_conflict": (409, "Сценарий изменён в другом окне.", "Обновите вкладку и повторите."),
    "name_taken": (409, "Сценарий с таким именем уже есть.", "Выберите другое имя."),
    "pit_fields_taken": (409, "Поля PIT или ORE с таким именем уже есть и принадлежат не этому сценарию.",
                         "Задайте префикс полей."),
    "model_changed": (409, "Блочная модель изменилась во время расчёта: ничего не записано.",
                      "Повторите расчёт."),
    "project_read_only": (423, "Проект открыт только для чтения.",
                          "Закройте проект в другом окне BedrockForge и откройте его заново."),
}  # fmt: skip


def refuse(code: str, message: str | None = None, hint: str | None = None, **extra: Any) -> ApiError:
    """An ApiError of REFUSALS[code]; `message` / `hint` replace the stock text, `extra` — machine fields
    (`param`, `missing`, `blocks`, `version`), never user text."""
    status, msg, tip = REFUSALS[code]
    return ApiError(status, code, message or msg, hint or tip, extra or None)


# --- units ------------------------------------------------------------------------------------------------------------


def grade_unit(raw: Any, param: str) -> str | None:
    """The canonical unit of a grade (`resources.norm_unit`: г/т, gpt → "g/t"); None — the unit of the field is taken;
    a unit without k_e (решение 3) — 422 `bad_param`."""
    unit = norm_unit(raw, param)
    if unit is not None and unit not in GRADE_K:
        raise bad("bad_param", f"Единица содержания «{unit}» не поддерживается.", "Допустимо: г/т, ppm, ppb, %.", param)
    return unit


def grade_k(unit: str | None) -> float | None:
    """k_e of R_b: troy ounces per (tonne × grade) for g/t, ppm, ppb; tonnes per tonne for %; None — unknown unit."""
    return GRADE_K.get(unit or "")


def grade_label(unit: str | None) -> str | None:
    return None if unit is None else GRADE_LABELS.get(unit, unit)


def price_label(unit: str | None) -> str | None:
    """The unit of P_e and S_e: «$/унц» for g/t, ppm, ppb; «$/т» for %."""
    return PRICE_LABELS.get(unit or "")


# --- the helpers of the shapes ---------------------------------------------------------------------------------------


def num(raw: Any, label: str, param: str, lo: float, hi: float, *, lo_open: bool = False, hi_open: bool = False,
        hint: str = "") -> float:  # fmt: skip
    v = _number(raw, label, param)
    ok_lo = lo < v if lo_open else lo <= v
    ok_hi = v < hi if hi_open else v <= hi
    if not (ok_lo and ok_hi):
        upper = "и меньше" if hi_open else "до"
        tip = hint or f"Задайте число {'больше' if lo_open else 'от'} {lo:g} {upper} {hi:g}."
        raise bad("bad_param", f"{label}: значение вне диапазона.", tip, param)
    return v + 0.0  # −0.0 → 0.0


def opt_num(raw: Any, label: str, param: str, lo: float, hi: float, **kw: Any) -> float | None:
    """A number or None for a missing / null / «» value (not yet set)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    return num(raw, label, param, lo, hi, **kw)


def num_or(raw: Any, default: float, label: str, param: str, lo: float, hi: float, **kw: Any) -> float:
    v = opt_num(raw, label, param, lo, hi, **kw)
    return default if v is None else v


def integer(raw: Any, label: str, param: str, lo: int, hi: int) -> int:
    v = _number(raw, label, param)
    if v != math.floor(v) or not lo <= v <= hi:
        raise bad("bad_param", f"{label}: нужно целое число от {lo} до {hi}.", f"Задайте целое от {lo} до {hi}.", param)
    return int(v)


def flag(raw: Any, default: bool, label: str, param: str) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise bad("bad_param", f"{label}: ожидается «да» или «нет».", "Отметьте или снимите галочку.", param)
    return raw


def name_of(raw: Any, label: str, param: str, most: int = FIELD_MAX) -> str:
    if raw is not None and not isinstance(raw, str):
        raise bad("bad_param", f"{label}: нужно имя.", "Выберите значение из списка.", param)
    out = text_of(raw, label, param, most)
    assert out is not None
    return " ".join(out.split())


def prefix_of(raw: Any, param: str = "prefix") -> str:
    """The prefix of the fields `<prefix>PIT`, `<prefix>ORE` (№ 120): letters, digits, «_», ≤ 40; none — empty."""
    if raw is None:
        return ""
    if not isinstance(raw, str) or not _PREFIX.fullmatch(raw.strip()):
        raise bad("bad_param", "Префикс полей: допустимы буквы, цифры и «_», до 40 знаков.", "Например, V1_.", param)
    return raw.strip()


def clean(x: float) -> float:
    """A value of a range without the noise of the sum (0.3 + 3·0.05 → 0.45)."""
    return float(f"{x:.12g}") + 0.0


# --- the parts ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Element:
    """An element of the revenue (решение 3): the grade field, its unit (→ k_e), the price P_e and the selling cost S_e
    per unit of metal (`price_label`), the royalty roy_e ∈ [0; 1). `price` None — not set yet (`validate`)."""

    field: str
    unit: str | None  # canonical (`grade_unit`); None — the unit of the field (filled by `resolved`)
    price: float | None
    sales: float
    royalty: float

    @property
    def k(self) -> float | None:
        return grade_k(self.unit)

    def canonical(self) -> dict[str, Any]:
        return {"field": self.field, "unit": self.unit, "price": self.price, "sales": self.sales,
                "royalty": self.royalty}  # fmt: skip


@dataclass(frozen=True)
class RockRule:
    """A rule of the rock type (решение 4): the name of the type and the tree of `filters/schema`; the first rule
    with a positive share of the block wins."""

    name: str
    rules: dict[str, Any]


@dataclass(frozen=True)
class RockBy:
    """Where the rock type of a block comes from: `field` — a category field (every label is a type, `EMPTY_LABEL`
    included), `rules` — the ordered rules (no rule fits — `OTHER_LABEL`)."""

    kind: str
    field: str | None = None
    rules: tuple[RockRule, ...] = ()

    def canonical(self) -> dict[str, Any]:
        if self.kind == "field":
            return {"kind": "field", "field": self.field}
        return {"kind": "rules", "rules": [{"name": r.name, "rules": r.rules} for r in self.rules]}


@dataclass(frozen=True)
class RockType:
    """The economics of a rock type r (решение 4, 6): c_p — the cost of processing, c_mr — the extra cost of mining ore
    (c_mр), rec — rec_e(r) per element of `Element.field`, `waste_only` — R_b = 0 («только пустая»), `air` —
    out of the graph («воздух»). `c_p` None — not set yet; a type with `air` or `waste_only` needs no c_p and rec."""

    name: str
    c_p: float | None
    c_mr: float
    rec: dict[str, float]
    waste_only: bool
    air: bool

    def canonical(self) -> dict[str, Any]:
        return {"name": self.name, "c_p": self.c_p, "c_mr": self.c_mr,
                "rec": {k: self.rec[k] for k in sorted(self.rec, key=str.casefold)},
                "waste_only": self.waste_only, "air": self.air}  # fmt: skip


@dataclass(frozen=True)
class Rocks:
    by: RockBy | None  # None — one type `ALL_LABEL` for every block
    types: tuple[RockType, ...]  # sorted by the name (casefold)

    def type_of(self, name: str) -> RockType | None:
        key = name.casefold()
        return next((t for t in self.types if t.name.casefold() == key), None)

    def canonical(self) -> dict[str, Any]:
        return {"by": None if self.by is None else self.by.canonical(), "types": [t.canonical() for t in self.types]}


@dataclass(frozen=True)
class Sector:
    az: float  # the azimuth of the centre, degrees clockwise from the north, [0; 360)
    angle: float  # the slope angle φ, degrees, [10; 80]


@dataclass(frozen=True)
class Domain:
    label: str  # a label of the domain field
    table: tuple[Sector, ...]


@dataclass(frozen=True)
class Slopes:
    """φ(az) (решение 10): ≤ 16 sectors, linear between the centres along the circle; a domain has its own table, a
    block without one (and without the field) uses `table`; the domain of the lower block of an arc decides."""

    table: tuple[Sector, ...]
    domain_field: str | None
    domains: tuple[Domain, ...]  # ≤ 16, sorted by the label (casefold)
    tolerance: float  # degrees: max abs(φ_eff − φ) above it is the warning «увеличьте K» (решение 12)

    def canonical(self) -> dict[str, Any]:
        def rows(t: tuple[Sector, ...]) -> list[dict[str, float]]:
            return [{"az": s.az, "angle": s.angle} for s in t]

        return {"table": rows(self.table), "domain_field": self.domain_field,
                "domains": [{"label": d.label, "table": rows(d.table)} for d in self.domains],
                "tolerance": self.tolerance}  # fmt: skip


@dataclass(frozen=True)
class Mining:
    """C_m,b = t_b·(c_m0 + c_gl·max(0; (Z_ref − z_b)/H_gl)) (решение 6); `c_m0` None — not set yet, `z_ref` None —
    the top of the grid (filled by `resolved`)."""

    c_m0: float | None
    z_ref: float | None
    h_gl: float
    c_gl: float

    def canonical(self) -> dict[str, Any]:
        return {"c_m0": self.c_m0, "z_ref": self.z_ref, "h_gl": self.h_gl, "c_gl": self.c_gl}


@dataclass(frozen=True)
class Calendar:
    """The calendar of the best and the worst case (решение 19): Q_р — the processing, Q_гм — the rock mass (t/year,
    None — no limit), r — the discount rate per year (a fraction). A view: not a part of `params_hash`."""

    q_ore: float | None
    q_mass: float | None
    rate: float

    def canonical(self) -> dict[str, Any]:
        return {"q_ore": self.q_ore, "q_mass": self.q_mass, "rate": self.rate}
