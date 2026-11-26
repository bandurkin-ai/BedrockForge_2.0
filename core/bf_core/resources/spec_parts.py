"""The parts of the parameters of a resource report (подшаг 20.1; ТЗ 8.10; ADR 0027 решения 3–11, 14, 17, 30): the
limits and defaults, the refusals with their Russian texts, the units of a grade and the metal, the elements, the
balance, the bench and the keys of a group, the readers of the shapes (`_num`, `_int`, `_flag`, …). Pure: no
database, no numpy. The report itself — `spec.py` (re-exports this file); the other bodies — `spec_bodies.py`.

    refuse(code, message=None, hint=None, **extra) -> ApiError         REFUSALS[code] (решение 30)
    norm_unit(raw, param), metal_family(unit), metal_k(unit), unit_label(unit)      the units (решение 10)
    bench_label(z_from, z_to) -> «+250…+260»; resolve_bench(bench, (z_lo, z_hi)) -> Bench
    default_elements(fields), default_density(fields)                                the defaults of 7.31
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate.spec_parts import text_of
from bf_core.geostat.variogram.spec import _number, bad

ALGO = "resources-1"
VERSION = 1
KIND = "resources.report"
MAX_ELEMENTS, MAX_CUTOFFS, MAX_KEYS = 20, 200, 3
MAX_BENCHES, MAX_COMBINATIONS, MAX_CELLS = 1000, 20_000, 2_000_000
CURVE_DEFAULT, CURVE_MAX = 100, 500
BENCH_H = 10.0  # default height of a bench, m
Z_LIMIT = 1e7  # |Z0|, |z_min|, |z_max| ≤ 10⁷ m
H_MIN, H_MAX = 1e-6, 1000.0  # the height of a bench, m (below 1 µm z / h does not fit a double)
CUT_LIMIT = 1e12  # |cut-off| ≤ 10¹²
PAGE_SIZE_MAX, REPORT_PAGE_MAX = 1000, 5000
NAME_MAX, FIELD_MAX, UNIT_MAX, TEMPLATE_MAX, PATH_MAX = 200, 100, 20, 64, 1024
EMPTY_LABEL = "(пусто)"
TROY_OUNCE_G = 31.1034768
KEY_KINDS = ("field", "bench")
ROW_KINDS = ("cutoff", "all", "balance", "offbalance")
EXPORT_FORMATS = ("xlsx", "csv")
_TEMPLATE_ID = re.compile(r"[A-Za-z0-9_.\-]{1,64}")

#: units of a grade (решение 10): the aliases fold to the canonical text; the family of the metal is the base unit
#: it is kept in (`g` grams for g/t, ppm, ppb; `t` tonnes for %)
UNIT_ALIASES = {"g/t": "g/t", "gpt": "g/t", "г/т": "g/t", "ppm": "ppm", "%": "%", "ppb": "ppb"}
UNIT_LABELS = {"g/t": "г/т", "ppm": "ppm", "%": "%", "ppb": "ppb"}
METAL_K = {"g/t": 1.0, "ppm": 1.0, "ppb": 1e-3, "%": 0.01}  # grade → grams per tonne of rock (%: tonnes per tonne)
METAL_FAMILY = {"g/t": "g", "ppm": "g", "ppb": "g", "%": "t"}
METAL_BASE_LABEL = {"g": "г", "t": "т"}

#: решение 30 and the routes of docs/progress/e20-api.md §9: code → (status, message, hint)
REFUSALS: dict[str, tuple[int, str, str]] = {
    "report_too_big": (422, "Отчёт слишком велик: сочетаний групп больше 20 000 или ячеек больше 2 000 000.",
                       "Уберите один из ключей группы, задайте меньше бортов или добавьте ограничитель."),
    "filter_field_missing": (422, "В ограничителе используется поле, которого нет в модели.",
                             "Постройте поле (например PIT) или уберите условие."),
    "group_numeric": (422, "Группировать можно только по категориям: поле числовое.",
                      "Переведите числовое поле в категории калькулятором блочной модели."),
    "resource_report_not_found": (404, "Отчёт по ресурсам не найден.", "Обновите раздел «Отчёты по ресурсам»."),
    "block_field_not_found": (404, "Поле блочной модели не найдено.", "Обновите свойства модели."),
    "filter_not_found": (404, "Фильтр не найден.", "Обновите библиотеку фильтров и выберите фильтр заново."),
    "template_not_found": (404, "Шаблон отчёта не найден.", "Обновите список шаблонов."),
    "report_building": (409, "Отчёт сейчас строится.", "Дождитесь окончания задания или отмените его."),
    "version_conflict": (409, "Отчёт или шаблон изменён в другом окне.", "Обновите вкладку и повторите."),
    "files_exist": (409, "Файл с таким именем уже есть: он не заменяется.", "Выберите другое имя или удалите файл."),
    "project_read_only": (423, "Проект открыт только для чтения.",
                          "Закройте проект в другом окне BedrockForge и откройте его заново."),
}  # fmt: skip


def refuse(code: str, message: str | None = None, hint: str | None = None, **extra: Any) -> ApiError:
    """An ApiError of REFUSALS[code]; `message` / `hint` replace the stock text, `extra` — machine fields
    (`param`, `field`, `files`, `version`), never user text."""
    status, msg, tip = REFUSALS[code]
    return ApiError(status, code, message or msg, hint or tip, extra or None)


# --- units ------------------------------------------------------------------------------------------------------------


def norm_unit(raw: Any, param: str) -> str | None:
    """The canonical text of a unit: g/t, г/т, gpt → "g/t"; ppm, %, ppb as they are; another one — trimmed as given
    (the metal is then unknown, a warning of the report); empty — None (the unit of the field is taken)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise bad("bad_param", "Единица содержания: нужен текст.", "Например г/т, %, ppm.", param)
    text = raw.strip()
    if len(text) > UNIT_MAX:
        raise bad("bad_param", f"Единица содержания длиннее {UNIT_MAX} знаков.", "Например г/т, %, ppm.", param)
    return UNIT_ALIASES.get(text.casefold().replace(" ", ""), text)


def metal_family(unit: str | None) -> str | None:
    """`g` (grams: g/t, ppm, ppb), `t` (tonnes: %), None — the metal of this unit is unknown."""
    return METAL_FAMILY.get(unit or "")


def metal_k(unit: str | None) -> float | None:
    """k_e of M_e = Σ t·g_e·k_e (решение 10): grams of metal per (tonne × grade), % — tonnes; None — unknown unit."""
    return METAL_K.get(unit or "")


def unit_label(unit: str | None) -> str | None:
    return None if unit is None else UNIT_LABELS.get(unit, unit)


# --- the helpers of the shapes ---------------------------------------------------------------------------------------


def _num(raw: Any, label: str, param: str, lo: float, hi: float, *, lo_open: bool = False, hint: str = "") -> float:
    v = _number(raw, label, param)
    if not (lo < v if lo_open else lo <= v) or not v <= hi:
        tip = hint or f"Задайте число {'больше' if lo_open else 'от'} {lo:g} до {hi:g}."
        raise bad("bad_param", f"{label}: значение вне диапазона.", tip, param)
    return v + 0.0  # −0.0 → 0.0


def _flag(raw: Any, default: bool, label: str, param: str) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise bad("bad_param", f"{label}: ожидается «да» или «нет».", "Отметьте или снимите галочку.", param)
    return raw


def _int(raw: Any, label: str, param: str, lo: int, hi: int, default: int) -> int:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    v = _number(raw, label, param)
    if v != math.floor(v) or not lo <= v <= hi:
        raise bad("bad_param", f"{label}: нужно целое число от {lo} до {hi}.", f"Задайте целое от {lo} до {hi}.", param)
    return int(v)


def _name(raw: Any, label: str, param: str) -> str:
    if raw is not None and not isinstance(raw, str):
        raise bad("bad_param", f"{label}: нужно имя поля.", "Выберите поле из списка.", param)
    out = text_of(raw, label, param, FIELD_MAX)
    assert out is not None
    return out


def _cutoff(raw: Any, param: str) -> float | None:
    if raw is None:
        return None
    return _num(raw, "Борт", param, -CUT_LIMIT, CUT_LIMIT)


def _template_id(raw: Any, param: str) -> str | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str) or not _TEMPLATE_ID.fullmatch(raw.strip()):
        raise bad("bad_param", "Шаблон: неверный код.", f"Латиница, цифры, «_», «-», «.», до {TEMPLATE_MAX} знаков.",
                  param)  # fmt: skip
    return raw.strip()


def _clean(x: float) -> float:
    """A cut-off of a range without the noise of the sum (0.1 + 2·0.1 → 0.3)."""
    return float(f"{x:.12g}") + 0.0


# --- the parts ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Element:
    field: str
    unit: str | None  # canonical (`norm_unit`); None — the unit of the field (filled by `resolved`)


@dataclass(frozen=True)
class Balance:
    cutoff: float  # c_б: balance g_б ≥ c_б
    off_cutoff: float | None  # c_з: off-balance c_з ≤ g_б < c_б; None — no off-balance rows


@dataclass(frozen=True)
class Bench:
    h: float
    z0: float | None = None  # None until `resolved`
    z_min: float | None = None
    z_max: float | None = None
    j0: int | None = None  # the number of the first bench: [Z0 + j·H; Z0 + (j + 1)·H]
    n: int | None = None  # benches j0 … j0 + n − 1

    def label(self, j: int) -> str:
        assert self.z0 is not None
        return bench_label(self.z0 + j * self.h, self.z0 + (j + 1) * self.h)

    def labels(self) -> tuple[str, ...]:
        assert self.j0 is not None and self.n is not None
        return tuple(self.label(self.j0 + i) for i in range(self.n))


@dataclass(frozen=True)
class Key:
    """One key of a group: a category field or the bench. `labels` — the labels of the codes 1… (code 0 is
    «(пусто)»); None until `resolved`."""

    kind: str
    field: str | None = None
    labels: tuple[str, ...] | None = None


def bench_label(z_from: float, z_to: float) -> str:
    """«+250…+260», «-10…0», «+12,5…+22,5» (решение 7)."""

    def one(x: float) -> str:
        x += 0.0
        t = format(x, ".10g").replace(".", ",")
        return "0" if x == 0 else (t if x < 0 else "+" + t)

    return f"{one(z_from)}…{one(z_to)}"


def default_elements(fields: Sequence[Mapping[str, Any]]) -> list[str]:
    """The fields `*_CUT`, else those with the role `grade` (7.31)."""
    nums = [f for f in fields if f["kind"] == "number"]
    cut = [str(f["name"]) for f in nums if str(f["name"]).casefold().endswith("_cut")]
    return cut or [str(f["name"]) for f in nums if f.get("role") == "grade"]


def default_density(fields: Sequence[Mapping[str, Any]]) -> str | None:
    """The varying field SG, else a varying field with the role `density`; a constant one is skipped."""

    def varies(f: Mapping[str, Any]) -> bool:
        st = f.get("stats") or {}
        return not (st.get("min") is not None and st.get("min") == st.get("max"))

    nums = [f for f in fields if f["kind"] == "number" and varies(f)]
    for f in nums:
        if str(f["name"]).casefold() == "sg":
            return str(f["name"])
    return next((str(f["name"]) for f in nums if f.get("role") == "density"), None)


def resolve_bench(bench: Bench, z_range: tuple[float, float]) -> Bench:
    """Z0 (a multiple of H under the bottom), the range and the benches j0…j0+n−1 that touch [z_min; z_max]
    (решение 7; a block part outside them is «(пусто)», № 107). 422 `bad_param {param: "bench.h"}` over 1000."""
    z_min = z_range[0] if bench.z_min is None else bench.z_min
    z_max = z_range[1] if bench.z_max is None else bench.z_max
    if not z_min < z_max:
        raise bad("bad_param", "Уступы: нижняя граница не меньше верхней.", "Задайте z_min меньше z_max.",
                  "bench.z_max")  # fmt: skip
    h = bench.h
    far = (z_min / h, (z_max - z_min) / h, z_max / h)  # a range that does not fit a double is not a bench count
    if not all(math.isfinite(x) for x in far) or (bench.z0 is not None and not math.isfinite((z_max - bench.z0) / h)):
        raise bad("bad_param", f"Уступов больше {MAX_BENCHES}.", "Увеличьте высоту уступа или сузьте диапазон.",
                  "bench.h")  # fmt: skip
    z0 = bench.z0 if bench.z0 is not None else math.floor(z_min / h + 1e-9) * h
    j0 = math.floor((z_min - z0) / h + 1e-9)
    j1 = math.ceil((z_max - z0) / h - 1e-9) - 1
    n = j1 - j0 + 1
    if n > MAX_BENCHES:
        raise bad("bad_param", f"Уступов больше {MAX_BENCHES}.", "Увеличьте высоту уступа или сузьте диапазон.",
                  "bench.h")  # fmt: skip
    return Bench(h, z0 + 0.0, z_min + 0.0, z_max + 0.0, j0, max(n, 1))
