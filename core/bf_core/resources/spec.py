"""Parameters of a resource report (подшаг 20.1; ТЗ 8.10; 7.35 Д-36, Д-37; ADR 0027 решения 3–11, 14, 17, 22, 30;
contract — docs/progress/e20-api.md §2–§9). Pure: no database, no numpy; fields and labels come from the model as
plain dicts (`Model.fields`), so the compute, the store and the routes share one reading of the body.

    req  = parse_request(body)                 # {params, name?, save}: POST /resources/reports
    spec = parse_params(raw)                   # ReportSpec; ApiError 422 `bad_param` {param}
    spec = spec.resolved(model.fields, (z_lo, z_hi))   # defaults filled, fields checked, bench and labels known
    meta = spec.canonical()                    # `objects(kind="resource_report").meta["params"]`
    sha  = spec.params_hash()                  # sha256 of canonical + ALGO without `template_id` (a view, №17)
    radices = spec.radices(); labels = spec.group_labels(code)     # the mixed-radix code of a group (решение 4)
    raise refuse("group_numeric", param="groups.0")                 # the refusals of решение 30 (`spec_parts`)

The parts (`spec_parts.py`) and the other bodies (`spec_bodies.py`: drill, export, compare, …) are re-exported here.

A key the body leaves out (or null, or "") takes its default: `elements` — the fields `*_CUT`, else those with the
role `grade` (as 7.31); `cutoff_field` — the first element; `cutoffs` — [null] (no cut-off: all blocks of a group, as
BF `cutoff=None`); `density` — the varying field SG, else a varying field with the role `density`; `groups` — none;
`bench` — H = 10 m, Z0 a multiple of H under the bottom, the range of the model in Z; `curve` — 100 cut-offs
(0 — none, 2–500); `template_id` — none. A key the body does not know is ignored (the stored `meta` parses as it
is). `param` of an error is the path inside `params`: "cutoffs.2", "groups.1", "bench.h", "balance.off_cutoff".
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from bf_core.blocks.filters.schema import normalize_rules
from bf_core.geostat.estimate.spec_parts import id_of, list_of, obj, text_of
from bf_core.geostat.variogram.spec import _number, bad

from .spec_bodies import (
    Drill,
    DrillRows,
    Export,
    FieldCompare,
    Query,
    parse_compare_reports,
    parse_drill,
    parse_drill_rows,
    parse_export,
    parse_field_compare,
    parse_query,
    parse_rename,
)
from .spec_parts import (
    ALGO,
    BENCH_H,
    CURVE_DEFAULT,
    CURVE_MAX,
    CUT_LIMIT,
    EMPTY_LABEL,
    EXPORT_FORMATS,
    H_MAX,
    KEY_KINDS,
    KIND,
    MAX_BENCHES,
    MAX_CELLS,
    MAX_COMBINATIONS,
    MAX_CUTOFFS,
    MAX_ELEMENTS,
    MAX_KEYS,
    METAL_BASE_LABEL,
    NAME_MAX,
    PAGE_SIZE_MAX,
    REFUSALS,
    ROW_KINDS,
    TROY_OUNCE_G,
    VERSION,
    Z_LIMIT,
    Balance,
    Bench,
    Element,
    Key,
    _clean,
    _cutoff,
    _flag,
    _int,
    _name,
    _num,
    _template_id,
    bench_label,
    default_density,
    default_elements,
    metal_family,
    metal_k,
    norm_unit,
    refuse,
    resolve_bench,
    unit_label,
)

__all__ = ["ALGO", "EMPTY_LABEL", "KIND", "MAX_CELLS", "MAX_COMBINATIONS", "METAL_BASE_LABEL", "PAGE_SIZE_MAX",
           "REFUSALS", "TROY_OUNCE_G", "VERSION", "Balance", "Bench", "Drill", "DrillRows", "Element", "Export",
           "FieldCompare", "Key", "Query", "ReportSpec", "Request", "bench_label", "default_density",
           "default_elements", "defaults_json", "metal_family", "metal_k", "norm_unit", "parse_compare_reports",
           "parse_drill", "parse_drill_rows", "parse_export", "parse_field_compare", "parse_params", "parse_query",
           "parse_rename", "parse_request", "ranges_json", "refuse", "resolve_bench", "unit_label"]  # fmt: skip


@dataclass(frozen=True)
class ReportSpec:
    model_id: int
    elements: tuple[Element, ...]  # () — by default (the fields of the model)
    cutoff_field: str | None  # g_б; None — the first element
    cutoffs: tuple[float | None, ...]  # None — «без борта»
    balance: Balance | None
    density: str | float | None  # a field, a number > 0, None — by default
    filter_id: int | None
    restrict: dict[str, Any] | None
    keys: tuple[Key, ...]
    bench: Bench | None
    curve: int
    template_id: str | None
    resolved_: bool = False

    # --- the canonical form -----------------------------------------------------------------------

    def canonical(self) -> dict[str, Any]:
        """`meta["params"]`: JSON-able, the same defaults ⇒ the same dict; `parse_params(canonical)` is `self`."""
        bench = self.bench
        return {
            "model_id": self.model_id,
            "elements": [{"field": e.field, "unit": e.unit} for e in self.elements] or None,
            "cutoff_field": self.cutoff_field,
            "cutoffs": list(self.cutoffs),
            "balance": None if self.balance is None else {"cutoff": self.balance.cutoff,
                                                          "off_cutoff": self.balance.off_cutoff},
            "density": self.density,
            "filter_id": self.filter_id,
            "restrict": self.restrict,
            "groups": [{"kind": "bench"} if k.kind == "bench" else {"kind": "field", "field": k.field}
                       for k in self.keys],
            "bench": None if bench is None else {"h": bench.h, "z0": bench.z0, "z_min": bench.z_min,
                                                 "z_max": bench.z_max},
            "curve": self.curve,
            "template_id": self.template_id,
        }  # fmt: skip

    def hash_params(self) -> dict[str, Any]:
        """What the result depends on: the canonical form without the template (a view of the result) + ALGO."""
        out = self.canonical()
        del out["template_id"]
        out["algo"] = ALGO
        return out

    def params_hash(self) -> str:
        text = json.dumps(self.hash_params(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    # --- the code of a group (решение 4) ----------------------------------------------------------

    def radices(self) -> tuple[int, ...]:
        """Per key: the number of codes, the empty one (0) included. Needs a resolved spec."""
        return tuple(len(k.labels or ()) + 1 for k in self.keys)

    def group_count(self) -> int:
        return math.prod(self.radices())

    def decode(self, code: int) -> tuple[int, ...]:
        """The code of a group → the code of each key (the first key — the most significant digit)."""
        out: list[int] = []
        for r in reversed(self.radices()):
            code, d = divmod(code, r)
            out.append(d)
        return tuple(reversed(out))

    def group_labels(self, code: int) -> list[str]:
        """The labels of a group code, `EMPTY_LABEL` for a digit 0 (a block with no value or out of the benches)."""
        return [EMPTY_LABEL if d == 0 else (k.labels or ())[d - 1]
                for k, d in zip(self.keys, self.decode(code), strict=True)]  # fmt: skip

    # --- the fields of the model ------------------------------------------------------------------

    def resolved(self, fields: Sequence[Mapping[str, Any]], z_range: tuple[float, float] | None = None) -> ReportSpec:
        """The defaults filled and everything checked against the model: `fields` — `Model.fields` (name, kind, unit,
        role, labels, stats), `z_range` — the extent of the block centres ± half a block in Z (for the bench).
        404 `block_field_not_found {param}`, 422 `group_numeric {param}`, `bad_param`, `report_too_big`."""
        by_name = {str(f["name"]).casefold(): f for f in fields}

        def find(name: str, param: str, kind: str) -> Mapping[str, Any]:
            f = by_name.get(name.casefold())
            if f is None:
                raise refuse("block_field_not_found", f"Поле «{name[:80]}» не найдено.", param=param)
            if f["kind"] != kind:
                what = "числовое" if kind == "number" else "категорийное"
                raise bad("bad_param", f"Поле «{f['name']}» не {what}.", f"Выберите {what} поле.", param)
            return f

        names = [e.field for e in self.elements] or default_elements(fields)
        if not names:
            raise bad("bad_param", "В модели не найдено поле содержания.",
                      "Укажите элементы (например AU_CUT) или задайте полю роль «содержание».", "elements")  # fmt: skip
        given = {e.field: e.unit for e in self.elements}
        elements = []
        for i, name in enumerate(names):
            f = find(name, f"elements.{i}.field", "number")
            unit = given.get(name) or norm_unit(f.get("unit"), f"elements.{i}.unit")
            elements.append(Element(str(f["name"]), unit))
        cut = str(find(self.cutoff_field, "cutoff_field", "number")["name"]) if self.cutoff_field else elements[0].field
        density: str | float | None = self.density
        if density is None:
            density = default_density(fields)
            if density is None:
                raise bad("bad_param", "Не найдена плотность: нет меняющегося поля SG или поля с ролью «плотность».",
                          "Укажите поле плотности или число, например 2,7.", "density")  # fmt: skip
        elif isinstance(density, str):
            density = str(find(density, "density", "number")["name"])
        bench = self.bench
        keys: list[Key] = []
        seen: set[str] = set()
        for i, k in enumerate(self.keys):
            if k.kind == "bench":
                assert bench is not None
                if z_range is None:
                    raise bad("bad_param", "Уступы: неизвестен охват модели по Z.", "Постройте модель заново.", "bench")
                bench = resolve_bench(bench, z_range)
                keys.append(replace(k, labels=bench.labels()))
                continue
            assert k.field is not None
            g = by_name.get(k.field.casefold())
            if g is None:
                raise refuse("block_field_not_found", f"Поле «{k.field[:80]}» не найдено.", param=f"groups.{i}")
            f = g
            if f["kind"] == "number":
                raise refuse("group_numeric", f"Поле «{f['name']}» числовое: группировать можно только по категориям.",
                             param=f"groups.{i}")  # fmt: skip
            if str(f["name"]).casefold() in seen:
                raise bad("bad_param", f"Поле «{f['name']}» повторяется среди ключей группы.", "Уберите повтор.",
                          f"groups.{i}")  # fmt: skip
            seen.add(str(f["name"]).casefold())
            keys.append(Key("field", str(f["name"]), tuple(str(x) for x in f.get("labels") or ())))
        out = replace(self, elements=tuple(elements), cutoff_field=cut, density=density, keys=tuple(keys),
                      bench=bench, resolved_=True)  # fmt: skip
        if out.group_count() > MAX_COMBINATIONS:
            raise refuse("report_too_big")
        return out


# --- parsing the report ---------------------------------------------------------------------------------------------


def _elements(raw: Any) -> tuple[Element, ...]:
    if raw is None:
        return ()
    items = list_of(raw, "Элементы", "elements", MAX_ELEMENTS)
    if not items:
        raise bad("bad_param", "Элементы: список пуст.", "Укажите хотя бы одно поле содержания или уберите ключ.",
                  "elements")  # fmt: skip
    out: list[Element] = []
    for i, it in enumerate(items):
        if isinstance(it, Mapping):
            name, unit = _name(it.get("field"), "Элемент", f"elements.{i}.field"), norm_unit(it.get("unit"),
                                                                                              f"elements.{i}.unit")
        else:
            name, unit = _name(it, "Элемент", f"elements.{i}"), None
        if any(e.field.casefold() == name.casefold() for e in out):
            raise bad("bad_param", f"Элемент «{name}» повторяется.", "Уберите повтор.", f"elements.{i}")
        out.append(Element(name, unit))
    return tuple(out)


def _cutoffs(raw: Any) -> tuple[float | None, ...]:
    """A list (≤ 200, null — «без борта») or a range {from, to, step}; the range is expanded upwards."""
    if raw is None:
        return (None,)
    if isinstance(raw, Mapping):
        lo = _num(raw.get("from"), "Борт «от»", "cutoffs.from", -CUT_LIMIT, CUT_LIMIT)
        hi = _num(raw.get("to"), "Борт «до»", "cutoffs.to", -CUT_LIMIT, CUT_LIMIT)
        step = _num(raw.get("step"), "Шаг бортов", "cutoffs.step", 0.0, CUT_LIMIT, lo_open=True)
        if hi < lo:
            raise bad("bad_param", "Борта: «до» меньше «от».", "Задайте «до» не меньше «от».", "cutoffs.to")
        count = math.floor((hi - lo) / step * (1 + 1e-12) + 1e-9) + 1
        if count > MAX_CUTOFFS:
            raise bad("bad_param", f"Бортов больше {MAX_CUTOFFS}.", "Увеличьте шаг или сузьте диапазон.",
                      "cutoffs.step")
        return tuple(dict.fromkeys(_clean(lo + i * step) for i in range(count)))
    if not isinstance(raw, list) or not raw or len(raw) > MAX_CUTOFFS:
        raise bad("bad_param", f"Борта: список из 1–{MAX_CUTOFFS} чисел или диапазон {{от, до, шаг}}.",
                  "Например [0,5; 1] или {от: 0,5, до: 3, шаг: 0,5}; пусто — «без борта».", "cutoffs")  # fmt: skip
    return tuple(dict.fromkeys(_cutoff(c, f"cutoffs.{i}") for i, c in enumerate(raw)))


def _balance(raw: Any) -> Balance | None:
    if raw is None:
        return None
    b = obj(raw, "Баланс", "balance")
    if b.get("cutoff") is None:
        raise bad("bad_param", "Баланс: не задан балансовый борт.", "Задайте борт c_б.", "balance.cutoff")
    on = _num(b["cutoff"], "Балансовый борт", "balance.cutoff", -CUT_LIMIT, CUT_LIMIT)
    off = _cutoff(b.get("off_cutoff"), "balance.off_cutoff")
    if off is not None and not off < on:
        raise bad("bad_param", "Забалансовый борт должен быть меньше балансового.", "Задайте c_з меньше c_б.",
                  "balance.off_cutoff")  # fmt: skip
    return Balance(on, off)


def _density(raw: Any) -> str | float | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, str):
        try:
            raw = float(raw.strip().replace(",", "."))
        except ValueError:
            return _name(raw, "Плотность", "density")
    v = _number(raw, "Плотность", "density")
    if v <= 0:
        raise bad("bad_param", "Плотность должна быть больше нуля.", "Например 2,7 или имя поля SG.", "density")
    return v


def _keys(raw: Any) -> tuple[Key, ...]:
    if raw is None:
        return ()
    out: list[Key] = []
    for i, it in enumerate(list_of(raw, "Группы", "groups", MAX_KEYS)):
        p = f"groups.{i}"
        if isinstance(it, str):
            it = {"kind": "field", "field": it}
        if not isinstance(it, Mapping):
            raise bad("bad_param", "Ключ группы: поле или «Уступ».", "Выберите категорийное поле или «Уступ».", p)
        kind = it.get("kind") or ("field" if it.get("field") is not None else None)
        if kind not in KEY_KINDS:
            raise bad("bad_param", "Ключ группы: поле или «Уступ».", f"Допустимо: {', '.join(KEY_KINDS)}.", p)
        key = Key("bench") if kind == "bench" else Key("field", _name(it.get("field"), "Поле группы", p + ".field"))
        if any(o.kind == key.kind and (o.field or "").casefold() == (key.field or "").casefold() for o in out):
            raise bad("bad_param", "Ключ группы повторяется.", "Уберите повтор.", p)
        out.append(key)
    return tuple(out)


def _bench(raw: Any) -> Bench:
    b = obj(raw, "Уступы", "bench")
    h = BENCH_H if b.get("h") in (None, "") else _num(b["h"], "Высота уступа H", "bench.h", 0.0, H_MAX, lo_open=True)
    labels = (("z0", "Отметка Z0"), ("z_min", "Нижняя отметка"), ("z_max", "Верхняя отметка"))
    z0, z_min, z_max = (None if b.get(k) in (None, "") else _num(b[k], lb, f"bench.{k}", -Z_LIMIT, Z_LIMIT)
                        for k, lb in labels)
    if z_min is not None and z_max is not None and not z_min < z_max:
        raise bad("bad_param", "Уступы: нижняя отметка не меньше верхней.", "Задайте z_min меньше z_max.",
                  "bench.z_max")  # fmt: skip
    return Bench(h, z0, z_min, z_max)


def parse_params(raw: Any) -> ReportSpec:
    """`params` of the report (docs/progress/e20-api.md §2) checked for its shapes and ranges; the fields are
    checked against the model by `ReportSpec.resolved`. A canonical form parses to itself."""
    b = obj(raw, "Параметры отчёта", "params")
    model_id = id_of(b.get("model_id"), "блочная модель", "model_id")
    assert model_id is not None
    cut_field = b.get("cutoff_field")
    quick = b.get("restrict")
    if quick is not None and not isinstance(quick, Mapping):
        raise bad("bad_param", "Ограничитель: нужен объект правил.", "Соберите правила в окне отчёта.", "restrict")
    curve_raw = b.get("curve")
    curve = CURVE_DEFAULT if curve_raw in (None, "") else _int(curve_raw, "Точек кривой", "curve", 0, CURVE_MAX, 0)
    if curve == 1:
        raise bad("bad_param", f"Точек кривой: 0 (без кривой) или 2–{CURVE_MAX}.", "Задайте 0 или от 2 до 500.",
                  "curve")  # fmt: skip
    keys = _keys(b.get("groups"))
    bench = _bench(b.get("bench")) if any(k.kind == "bench" for k in keys) else None  # a lone `bench` is dropped
    return ReportSpec(
        model_id=model_id,
        elements=_elements(b.get("elements")),
        cutoff_field=None if cut_field in (None, "") else _name(cut_field, "Поле борта", "cutoff_field"),
        cutoffs=_cutoffs(b.get("cutoffs")),
        balance=_balance(b.get("balance")),
        density=_density(b.get("density")),
        filter_id=id_of(b.get("filter_id"), "фильтр", "filter_id", required=False),
        restrict=None if quick is None else normalize_rules(dict(quick), "restrict"),
        keys=keys,
        bench=bench,
        curve=curve,
        template_id=_template_id(b.get("template_id"), "template_id"),
    )  # fmt: skip


@dataclass(frozen=True)
class Request:
    params: ReportSpec
    name: str | None
    save: bool  # False — a preview: nothing is written (works in a read-only project)


def parse_request(body: Any) -> Request:
    """The body of `POST /resources/reports`: `{params, name?, save}`; `save` defaults to false (a preview)."""
    b = obj(body, "Запрос отчёта", "body")
    if b.get("params") is None:
        raise bad("bad_param", "Не заданы параметры отчёта.", "Передайте params.", "params")
    return Request(parse_params(b["params"]), text_of(b.get("name"), "Имя отчёта", "name", NAME_MAX, required=False),
                   _flag(b.get("save"), False, "Сохранение", "save"))  # fmt: skip


# --- the form of the window -----------------------------------------------------------------------------------------


def defaults_json() -> dict[str, Any]:
    """The defaults and the limits the window fills its form with (`GET /resources/defaults` → `params`)."""
    return {"elements": None, "cutoff_field": None, "cutoffs": [None], "balance": None, "density": None,
            "filter_id": None, "restrict": None, "groups": [], "bench": {"h": BENCH_H, "z0": None, "z_min": None,
                                                                         "z_max": None},
            "curve": CURVE_DEFAULT, "template_id": None, "save": False,
            "choices": {"key_kinds": list(KEY_KINDS), "row_kinds": list(ROW_KINDS),
                        "export_formats": list(EXPORT_FORMATS)},
            "limits": {"elements": MAX_ELEMENTS, "cutoffs": MAX_CUTOFFS, "group_keys": MAX_KEYS,
                       "benches": MAX_BENCHES, "combinations": MAX_COMBINATIONS, "cells": MAX_CELLS,
                       "curve": CURVE_MAX, "page": PAGE_SIZE_MAX}}  # fmt: skip


def ranges_json() -> dict[str, Any]:
    """The ranges for the form: key → {lo, hi, lo_open, integer, unit}."""
    return {"bench.h": {"lo": 0.0, "hi": H_MAX, "lo_open": True, "integer": False, "unit": "м"},
            "bench.z": {"lo": -Z_LIMIT, "hi": Z_LIMIT, "lo_open": False, "integer": False, "unit": "м"},
            "cutoff": {"lo": -CUT_LIMIT, "hi": CUT_LIMIT, "lo_open": False, "integer": False, "unit": ""},
            "curve": {"lo": 2, "hi": CURVE_MAX, "lo_open": False, "integer": True, "unit": "точек (0 — без кривой)"}}
