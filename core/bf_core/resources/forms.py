"""The form of a resource report — the rows of a result turned into the table of a template (подшаг 20.4; ТЗ 8.10
«Формы»; ADR 0027 decisions 17–19).

The core makes the form, the interface and the XLSX only show it (Т8.10-5: one source of the numbers): every cell is
`{value, text}` — `value` the number in the unit of the template (float64, NOT rounded; null — no value), `text` the
shown text (rounded half away from zero to the decimals of the template, the decimal comma, a no-break space between
the thousands; null — an empty text). A missing value is never a zero (CLAUDE.md): a grade without tonnes is null.

The input rows are those of the result (ADR 0027 decision 15): `g1…g3` (the labels of the groups; all null — a row of
the total), `kind` (cutoff / all / balance / offbalance), `cutoff`, `blocks`, `volume`, `tonnes` and for each element
`<e>:wt`, `<e>:sum`, `<e>:metal`, `<e>:grade`, `<e>:missing_t`, `<e>:missing_n`; the metal is in the base unit of the
element (g for g/t, ppm, ppb; t for %, decision 10), the tonnes in t, the volume in m³.

    build(rows, template, elements, roles=None, cutoff=None) -> form
        rows      the rows of the result (mappings); `elements` — [{name, unit}] (unit of the grade: g/t, г/т, ppm,
                  ppb, %; another one — no metal, a warning); `roles` — the role of each of g1…g3 («class», «domain»,
                  «bench», …; default: g1 is the class); `cutoff` — the cut-off of the table (null: the rows «all»,
                  else the lowest cut-off); a balance template shows the rows `balance` and `offbalance` instead
        template  a template of `templates.py` (`get` / `defaults()`)
    form = {template: {id?, name, version?}, elements: [{name, unit, unit_label, base}], cutoff: {kind, value},
            cutoffs: [values], columns: [column], sections: [{title, rows: [row]}], warnings: [text]}
    column = {key, group, title, metric, element, unit, decimals}      # `group`: «Балансовые» / «Забалансовые» / null
    row    = {label, type: group | subtotal | other | total, members?: [labels], cells: {column key: cell}}
    cell   = {value: float | null, text: str}
    rows_flat(form) -> [{section, label, type, cells: [cell …]}]       # the cells in the order of `columns`
    format_number(value, decimals) -> text    excel_format(decimals) -> number format of a column in XLSX

The rows of the template come first (a subtotal after its last member), then «Прочие» (the groups outside the
template, with a warning), then the total. The subtotals and the total are SUMS of the tonnes, the volume, the sums
t·g and the weights t of the rows, ḡ = Σ t·g / Σ wt (not the mean of the means); the sums are `math.fsum` (exact:
any grouping gives the same number). Rows with other keys than the class and the section (a bench under a class) are
summed into the class row. A row of the template that no group fills has T = 0 and an empty grade.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Any

from .templates import (
    METAL_UNITS,
    OTHER_LABEL,
    TONNES_UNITS,
    VOLUME_UNITS,
    fold,
)

EMPTY_LABEL = "(пусто)"  # the label of a block with no value of the key (`blocks/report.py`)
BALANCE_PARTS = (("balance", "Балансовые"), ("offbalance", "Забалансовые"))
# The unit of a grade → (the label for the user, the base unit of the metal: g, t; None — no metal).
_GRADE_UNITS: dict[str, tuple[str, str | None]] = {
    "g/t": ("г/т", "g"),
    "г/т": ("г/т", "g"),
    "ppm": ("ppm", "g"),
    "ppb": ("ppb", "g"),
    "%": ("%", "t"),
}
_CTX = Context(prec=80)
_REL = 1e-12


def grade_unit(unit: Any) -> tuple[str, str | None]:
    """`(label, base)` of the unit of a grade: base `g` (g/t, ppm, ppb) or `t` (%); a unit unknown to the report —
    its own text and no base (no metal)."""
    text = str(unit).strip() if unit is not None else ""
    known = _GRADE_UNITS.get(text.lower())
    return known if known is not None else (text, None)


def format_number(value: float | None, decimals: int) -> str:
    """The text of a number: rounded half away from zero to `decimals`, the decimal comma, a no-break space between
    the thousands; null — an empty text; a negative that rounds to zero has no sign."""
    if value is None:
        return ""
    d = Decimal(repr(float(value))).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP, context=_CTX)
    if d == 0:
        d = abs(d)
    return format(d, f",.{decimals}f").replace(",", " ").replace(".", ",")


def excel_format(decimals: int) -> str:
    """The number format of a column for XLSX that shows what `format_number` does (the thousands, the decimals)."""
    return "#,##0" + (("." + "0" * decimals) if decimals else "")


def _num(x: Any) -> float | None:
    """A number or None (a null, a NaN and a text are no number)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _cell(value: float | None, decimals: int) -> dict[str, Any]:
    return {"value": value, "text": format_number(value, decimals)}


class _Agg:
    """The sums of a set of rows: the lists are `fsum`med at the end (so any grouping gives the same numbers)."""

    __slots__ = ("blocks", "metal", "names", "sum", "tonnes", "volume", "wt")

    def __init__(self, names: Sequence[str]) -> None:
        self.names = names
        self.blocks = 0
        self.volume: list[float] = []
        self.tonnes: list[float] = []
        self.wt: dict[str, list[float]] = {n: [] for n in names}
        self.sum: dict[str, list[float]] = {n: [] for n in names}
        self.metal: dict[str, list[float]] = {n: [] for n in names}

    def add(self, row: Mapping[str, Any]) -> None:
        blocks = _num(row.get("blocks"))
        self.blocks += int(blocks) if blocks is not None else 0
        for dst, key in ((self.volume, "volume"), (self.tonnes, "tonnes")):
            v = _num(row.get(key))
            if v is not None:
                dst.append(v)
        for n in self.names:
            for dst2, suffix in ((self.wt, "wt"), (self.sum, "sum"), (self.metal, "metal")):
                v = _num(row.get(f"{n}:{suffix}"))
                if v is not None:
                    dst2[n].append(v)

    @classmethod
    def combine(cls, names: Sequence[str], parts: Iterable[_Agg]) -> _Agg:
        out = cls(names)
        for p in parts:
            out.blocks += p.blocks
            out.volume += p.volume
            out.tonnes += p.tonnes
            for n in names:
                out.wt[n] += p.wt[n]
                out.sum[n] += p.sum[n]
                out.metal[n] += p.metal[n]
        return out

    def grade(self, name: str) -> float | None:
        """ḡ = Σ t·g / Σ wt; no weight (no tonnes with a grade, or Σ wt = 0) — None, not 0."""
        wt = math.fsum(self.wt[name]) if self.wt[name] else 0.0
        return math.fsum(self.sum[name]) / wt if wt > 0.0 else None

    def metal_of(self, name: str) -> float | None:
        return math.fsum(self.metal[name]) if self.metal[name] else None


def _same(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and math.isclose(a, b, rel_tol=_REL, abs_tol=_REL)


def _pick(
    rows: Sequence[Mapping[str, Any]], part: str | None, cutoff: float | None
) -> tuple[list[Mapping[str, Any]], float | None]:
    """The rows of one table and its cut-off: `part` is balance / offbalance or None (the cut-off table: the rows
    `cutoff` of the asked cut-off; with `cutoff` null — the rows `all`, else the lowest cut-off)."""
    if part is not None:
        mine = [r for r in rows if r.get("kind") == part]
        first = next((c for c in (_num(r.get("cutoff")) for r in mine) if c is not None), None)
        return ([r for r in mine if _same(_num(r.get("cutoff")), first)] if first is not None else mine), first
    if cutoff is None:
        alls = [r for r in rows if r.get("kind") == "all"]
        if alls:
            return alls, None
    cuts = sorted(_cutoffs(rows))
    if not cuts:
        return [], cutoff
    want = cutoff if cutoff is not None else cuts[0]
    return [r for r in rows if r.get("kind") == "cutoff" and _same(_num(r.get("cutoff")), want)], want


def _cutoffs(rows: Iterable[Mapping[str, Any]]) -> set[float]:
    return {c for c in (_num(r.get("cutoff")) for r in rows if r.get("kind") == "cutoff") if c is not None}


def _columns(
    template: Mapping[str, Any], elements: Sequence[Mapping[str, Any]], parts: Sequence[tuple[str | None, str | None]]
) -> list[dict[str, Any]]:
    """The columns of the form in the order of the template: tonnes, volume, then for each element its grade and
    metal (at the first of the two in the template); the whole set once per table (balance / offbalance)."""
    units, dec, metrics = template["units"], template["decimals"], template["columns"]
    cols: list[dict[str, Any]] = []
    for part, group in parts:
        pre = f"{part}:" if part else ""

        def add(key: str, title: str, metric: str, unit: str, element: str | None, part: str | None = part,
                group: str | None = group, pre: str = pre) -> None:  # fmt: skip
            cols.append({"key": pre + key, "part": part, "group": group, "title": title, "metric": metric,
                         "element": element, "unit": unit, "decimals": dec[metric]})  # fmt: skip

        per_element = False
        for m in metrics:
            if m == "tonnes":
                label = TONNES_UNITS[units["tonnes"]][0]
                add("tonnes", f"Руда, {label}", "tonnes", label, None)
            elif m == "volume":
                label = VOLUME_UNITS[units["volume"]][0]
                add("volume", f"Объём, {label}", "volume", label, None)
            elif not per_element:
                per_element = True
                for el in elements:
                    label, base = grade_unit(el.get("unit"))
                    name = str(el["name"])
                    for m2 in metrics:
                        if m2 == "grade":
                            add(f"grade:{name}", f"{name}, {label}" if label else name, "grade", label, name)
                        elif m2 == "metal" and base is not None:
                            unit = METAL_UNITS[units["metal"][base]][0]
                            add(f"metal:{name}", f"Металл {name}, {unit}", "metal", unit, name)
    return cols


def _metal_value(m: float | None, base: str | None, unit: str) -> float | None:
    """The metal of the base unit (g for g/t, ppm, ppb; t for %) in the unit of the template."""
    if m is None or base is None:
        return None
    return m * (1e6 if base == "t" else 1.0) * METAL_UNITS[unit][1]


def _cells(
    agg: _Agg, columns: Sequence[Mapping[str, Any]], template: Mapping[str, Any], bases: Mapping[str, str | None],
    part: str | None,
) -> dict[str, dict[str, Any]]:  # fmt: skip
    """The cells of one row of one table (`part`): the columns of that table only."""
    units = template["units"]
    out: dict[str, dict[str, Any]] = {}
    for c in columns:
        if c["part"] != part:
            continue
        metric, el = c["metric"], c["element"]
        value: float | None
        if metric == "tonnes":
            value = math.fsum(agg.tonnes) * TONNES_UNITS[units["tonnes"]][1]
        elif metric == "volume":
            value = math.fsum(agg.volume) * VOLUME_UNITS[units["volume"]][1]
        elif metric == "grade":
            value = agg.grade(el)
        else:
            base = bases[el]
            value = _metal_value(agg.metal_of(el), base, units["metal"][base]) if base else None
        out[c["key"]] = _cell(value, c["decimals"])
    return out


def _label_of(row: Mapping[str, Any], index: int) -> str:
    v = row.get(f"g{index + 1}")
    return EMPTY_LABEL if v is None else " ".join(str(v).split())


class _Table:
    """The sums of one table (cut-off or balance / offbalance) by (section, folded label of the class)."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names = names
        self.aggs: dict[tuple[str, str], _Agg] = {}
        self.totals = _Agg(names)  # the rows of the total of the result — used when there are no group rows
        self.has_groups = False
        self.missing = False  # the result has no row of this table (no balance cut-off): its cells are empty, not 0

    def total(self, sec: str | None) -> _Agg:
        if not self.has_groups:
            return self.totals
        return _Agg.combine(self.names, (a for (s, _lab), a in self.aggs.items() if sec is None or s == sec))

    def of(self, label_keys: Iterable[str], sec: str | None) -> _Agg:
        keys = set(label_keys)
        return _Agg.combine(self.names, (a for (s, lab), a in self.aggs.items() if lab in keys and sec in (None, s)))


def build(
    rows: Sequence[Mapping[str, Any]],
    template: Mapping[str, Any],
    elements: Sequence[Mapping[str, Any]],
    roles: Sequence[str] | None = None,
    cutoff: float | None = None,
) -> dict[str, Any]:
    """The form of `rows` by `template` (see the module text). The rows are not changed."""
    warnings: list[str] = []
    names = [str(e["name"]) for e in elements]
    bases = {n: grade_unit(e.get("unit"))[1] for n, e in zip(names, elements, strict=True)}
    parts: list[tuple[str | None, str | None]] = list(BALANCE_PARTS) if template["balance"] else [(None, None)]  # type: ignore[arg-type,list-item]
    columns = _columns(template, elements, parts)
    if "metal" in template["columns"]:
        warnings += [f"Для элемента «{n}» единица содержания не задана или неизвестна — металл не показан."
                     for n in names if bases[n] is None]  # fmt: skip

    # the keys of the groups: the class (the rows of the template) and the section (the outer key)
    key_i, sec_i = 0, None
    if roles is not None:
        if template["key"] in roles:
            key_i = list(roles).index(template["key"])
        else:
            warnings.append(f"В отчёте нет группы с ролью «{template['key']}»: строки построены по первой группе.")
        if template["section"] is not None:
            if template["section"] in roles:
                sec_i = list(roles).index(template["section"])
            else:
                warnings.append(f"В отчёте нет группы с ролью «{template['section']}»: разделов нет.")

    tables: list[_Table] = []
    cut_info: dict[str, Any] = {}
    section_order: list[str] = []
    spelled: dict[str, str] = {}  # the folded label → the first spelling met
    for part, _group in parts:
        picked, used = _pick(rows, part, cutoff)
        if part is None:
            cut_info = {"kind": "all" if used is None and picked else "cutoff", "value": used}
            if cutoff is not None and not picked:
                warnings.append("Борта с таким значением нет в результате: таблица пуста.")
        else:
            cut_info[part] = used
        table = _Table(names)
        if part is not None and not picked:  # the report was made without this cut-off: nothing was counted
            table.missing = True
            what = "балансовый" if part == "balance" else "забалансовый"
            warnings.append(f"В отчёте не задан {what} борт: столбцы «{_group}» пусты. Задайте его в окне отчёта и "
                            "постройте отчёт заново.")  # fmt: skip
        for r in picked:
            if r.get("g1") is None:  # a row of the total
                table.totals.add(r)
                continue
            table.has_groups = True
            sec = _label_of(r, sec_i) if sec_i is not None else ""
            lab = _label_of(r, key_i)
            spelled.setdefault(fold(lab), lab)
            if sec not in section_order:
                section_order.append(sec)
            table.aggs.setdefault((sec, fold(lab)), _Agg(names)).add(r)
        tables.append(table)
    if not section_order:
        section_order = [""]

    labels: list[str] = template["labels"]
    known = {fold(x) for x in labels}
    others = sorted({lab for t in tables for (_s, lab) in t.aggs if lab not in known}, key=lambda x: spelled[x])
    if others:
        shown = ", ".join(spelled[o] for o in others[:10]) + (" и другие" if len(others) > 10 else "")
        warnings.append(f"Группы вне шаблона: {shown} — они показаны строкой «{OTHER_LABEL}».")
    ends: dict[int, list[dict[str, Any]]] = {}
    for s in template["subtotals"]:
        ends.setdefault(max(labels.index(m) for m in s["members"]), []).append(s)

    def row(
        label: str, typ: str, sec: str | None, keys: list[str] | None, members: list[str] | None = None
    ) -> dict[str, Any]:
        """One row over every table; `keys` the folded labels it sums, None — the total."""
        cells: dict[str, dict[str, Any]] = {}
        for (part, _g), t in zip(parts, tables, strict=True):
            if t.missing:
                cells.update({c["key"]: _cell(None, c["decimals"]) for c in columns if c["part"] == part})
                continue
            cells.update(_cells(t.total(sec) if keys is None else t.of(keys, sec), columns, template, bases, part))
        out: dict[str, Any] = {"label": label, "type": typ, "cells": cells}
        if members is not None:
            out["members"] = members
        return out

    def block(sec: str | None) -> list[dict[str, Any]]:
        """The rows of one section (None — all the sections together)."""
        result: list[dict[str, Any]] = []
        for i, label in enumerate(labels):
            result.append(row(label, "group", sec, [fold(label)]))
            for s in ends.get(i, []):
                result.append(row(s["label"], "subtotal", sec, [fold(m) for m in s["members"]], list(s["members"])))
        if others:
            result.append(row(OTHER_LABEL, "other", sec, others))
        if template["total"] is not None:
            result.append(row(template["total"], "total", sec, None))
        return result

    sections: list[dict[str, Any]] = []
    if sec_i is None:
        sections.append({"title": None, "rows": block(None)})
    else:
        sections += [{"title": sec, "rows": block(sec)} for sec in section_order]
        if template["total"] is not None and len(section_order) > 1:
            sections.append({"title": "Все разделы", "rows": [row(template["total"], "total", None, None)]})
    return {
        "template": {k: template[k] for k in ("id", "name", "version") if k in template},
        "elements": [
            {"name": n, "unit": e.get("unit"), "unit_label": grade_unit(e.get("unit"))[0], "base": bases[n]}
            for n, e in zip(names, elements, strict=True)
        ],  # fmt: skip
        "cutoff": cut_info,
        "cutoffs": sorted(_cutoffs(rows)),
        "columns": columns,
        "sections": sections,
        "warnings": warnings,
    }


def rows_flat(form: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The rows of every section one after the other, the cells as a list in the order of `form["columns"]` (for the
    XLSX, the CSV and the table of the interface): `{section, label, type, cells: [cell | None …]}`."""
    keys = [c["key"] for c in form["columns"]]
    return [
        {"section": s["title"], "label": r["label"], "type": r["type"], "cells": [r["cells"].get(k) for k in keys]}
        for s in form["sections"]
        for r in s["rows"]
    ]
