"""Column mapping and normalisation of drillhole tables (ТЗ 7.7). Ported from BF importing/service.py onto the
column readers of bf_core.formats (no pandas frame: number columns are float64 arrays, text — Arrow).

`normalize()` checks a mapping and turns a read table into what bf_core.drillholes.store writes:
collars, surveys or an interval table. Errors in the mapping always stop; errors in the data stop unless
«Загрузить с ошибками — исправлю в BedrockForge» (force): a value that is not a number becomes empty and its
text is kept in the attribute «<колонка> (исходное)», a row without a hole gets «БЕЗ НОМЕРА (строка N)», a
repeated collar «<номер> (повтор k)».
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from bf_core.drillholes.store import CollarRow, ColumnSpec, TableData
from bf_core.formats.table import Column, TableRead

from .names import match_column
from .schema import ELEMENTS, KIND_LABELS, PRECIOUS, SCHEMAS

DATA_ERRORS = frozenset({"non_numeric", "empty_hole_id", "duplicate_hole_id"})
_KEPT = " — загружено как есть, исправьте в BedrockForge"


@dataclass(frozen=True)
class Issue:
    severity: str  # error | warning
    code: str
    message: str
    field: str | None = None
    count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Normalized:
    kind: str
    rows: int
    collars: list[CollarRow] = field(default_factory=list)
    #: (hole, depth, azimuth, dip, attributes or None)
    surveys: list[tuple[str, float, float | None, float | None, dict[str, Any] | None]] = field(default_factory=list)
    table: TableData | None = None
    counts: dict[str, int] = field(default_factory=dict)


def suggest_mapping(kind: str, columns: list[str]) -> dict[str, str]:
    """Source column for each target field (fields in schema order; a taken column is not offered again)."""
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for f in SCHEMAS[kind]:
        col = match_column(columns, f.aliases, used)
        if col is not None:
            mapping[f.key] = col
            used.add(col)
    return mapping


_SYMBOL = re.compile(r"^([A-Za-z]{1,3})(?:$|[\s_,(\[/.-])")


def component_symbol(name: str) -> tuple[str | None, str | None]:
    """'Au, г/т' → ('AU', 'g/t'); 'CU_PCT' → ('CU', '%'); 'Au_ppm' → ('AU', 'ppm'); 'Comments' → (None, None)."""
    match = _SYMBOL.match(name.strip())
    if not match or match.group(1).upper() not in ELEMENTS:
        return None, None
    lower = name.lower()
    unit = None
    if "%" in lower or "pct" in lower or "проц" in lower:
        unit = "%"
    elif "ppm" in lower:
        unit = "ppm"
    elif "г/т" in lower or "g/t" in lower or "gpt" in lower:
        unit = "g/t"
    elif "ppb" in lower or "мг/т" in lower:
        unit = "ppb"
    return match.group(1).upper(), unit


def suggest_components(table: TableRead, mapping: dict[str, str]) -> list[dict[str, str]]:
    mapped = set(mapping.values())
    out = []
    for col in table.columns:
        if col.name in mapped:
            continue
        symbol, unit = component_symbol(col.name)
        if not symbol:
            continue
        if col.kind != "number":
            # A grade column with a few typos is read as text: still a component when most values are numbers
            # (the check then reports the bad ones; «Загрузить с ошибками» keeps their text).
            values, bad, *_ = _numbers(col)
            numbers = int((~_isnan(values)).sum())
            if numbers == 0 or numbers < bad:
                continue
        if symbol:
            out.append({"column": col.name, "unit": unit or ("g/t" if symbol in PRECIOUS else "%")})
    return out


def detect_kind(columns: list[str]) -> str | None:
    """collar | survey | assay | interval | event by the names of the columns; None when unsure."""
    best_kind, best = None, 0.0
    for kind, fields in SCHEMAS.items():
        required = [f for f in fields if f.required]
        mapping = suggest_mapping(kind, columns)
        score = sum(1 for f in required if f.key in mapping) / len(required)
        if score > best:
            best_kind, best = kind, score
    if best < 0.6:
        return None
    if best_kind == "assay":
        mapping = suggest_mapping("assay", columns)
        mapped = set(mapping.values())
        graded = any(component_symbol(c)[0] for c in columns if c not in mapped)
        if "sample_id" not in mapping and not graded:
            return "interval"  # from–to without a sample number or grades: lithology, alteration, RQD…
    return best_kind


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


# --- normalisation --------------------------------------------------------------------------


def _numbers(col: Column) -> tuple[Any, int, int, int, list[str]]:
    """(float64 values, bad, below, above, bad examples): number columns as read, text ones parsed here."""
    if col.kind == "number":
        cells = col.bad_cells or {}
        examples = [cells[k] for k in sorted(cells)[:5]]
        return col.values, len(cells), col.below_limit, col.above_limit, examples
    from bf_core.formats.numbers import parse_numbers

    p = parse_numbers(col.to_pylist())
    return p.values, p.bad, p.below_limit, p.above_limit, p.bad_examples


def _texts(col: Column) -> list[str | None]:
    if col.kind == "text":
        return [None if v is None else str(v).strip() for v in col.to_pylist()]
    from bf_core.formats.numbers import number_text

    out = [None if math.isnan(v) else number_text(v) for v in col.values.tolist()]
    for row, text in (col.bad_cells or {}).items():  # DAT: the cell as it was written
        out[row] = text or None
    return out


def normalize(
    table: TableRead,
    kind: str,
    mapping: dict[str, str],
    components: list[dict[str, str]] | None = None,
    attributes: list[str] | None = None,
    *,
    force: bool = False,
    table_name: str | None = None,
) -> tuple[Normalized | None, list[Issue]]:
    components = components or []
    attributes = attributes or []
    issues: list[Issue] = []
    if kind not in SCHEMAS:
        return None, [Issue("error", "unknown_kind", f"Неизвестный тип таблицы «{kind}»")]
    schema = {f.key: f for f in SCHEMAS[kind]}
    names = set(table.names)

    for key in mapping:
        if key not in schema:
            issues.append(Issue("error", "unknown_field", f"Неизвестное поле «{key}»", key))
    for f in schema.values():
        if f.required and not mapping.get(f.key):
            issues.append(Issue("error", "missing_required", f"Не назначена колонка для «{f.label}»", f.key))
    used: dict[str, str] = {}

    def take(col: str, owner: str, what: str) -> bool:
        if col not in names:
            issues.append(Issue("error", "unknown_column", f"В файле нет колонки «{col}»", owner))
        elif col in used:
            issues.append(Issue("error", "duplicate_column", f"Колонка «{col}» {what}", owner))
        else:
            used[col] = owner
            return True
        return False

    for key, col in mapping.items():
        if key in schema:
            take(col, key, "назначена дважды")
    if components and kind != "assay":
        issues.append(Issue("error", "components_not_allowed", "Компоненты доступны только для проб"))
    for comp in components:
        col = comp["column"]
        if take(col, f"comp:{col}", "уже назначена"):
            parsed, bad, *_ = _numbers(table.column(col))
            if bad and int((~_isnan(parsed)).sum()) == 0:
                issues.append(Issue("error", "component_not_numeric", f"Компонент «{col}» не числовой", col))
        if not str(comp.get("unit", "")).strip():
            issues.append(Issue("error", "missing_unit", f"Не указана единица для «{col}»", col))
    for col in attributes:
        take(col, f"attr:{col}", "уже используется")
    if any(i.severity == "error" for i in issues):
        return None, issues

    level = "warning" if force else "error"
    kept = _KEPT if force else ""
    n = table.rows
    out: dict[str, Any] = {}
    originals: dict[str, list[str | None]] = {}
    counts = {"rows": n, "below_limit": 0, "above_limit": 0, "non_numeric": 0}

    def number_field(label: str, col_name: str, key: str) -> Any:
        values, bad, below, above, examples = _numbers(table.column(col_name))
        if bad:
            example = f", например «{examples[0]}»" if examples else ""
            issues.append(Issue(level, "non_numeric", f"«{label}»: нечисловых значений{example}{kept}", key, bad))
            counts["non_numeric"] += bad
            if force:
                raw = _texts(table.column(col_name))
                originals[f"{col_name} (исходное)"] = [
                    r if (r and math.isnan(v)) else None for r, v in zip(raw, values.tolist(), strict=True)
                ]
        if below:
            issues.append(Issue("warning", "below_limit", f"«{label}»: значения «<x» приняты как x/2", key, below))
            counts["below_limit"] += below
        if above:
            issues.append(Issue("warning", "above_limit", f"«{label}»: значения «>x» приняты как x", key, above))
            counts["above_limit"] += above
        empty = int(_isnan(values).sum()) - bad
        if empty and schema.get(key) is not None and schema[key].required:
            issues.append(Issue("warning", "empty_values", f"«{label}»: пустых значений", key, empty))
        return values

    for key, col in mapping.items():
        f = schema[key]
        if f.numeric:
            out[key] = number_field(f.label, col, key)
            continue
        texts = _texts(table.column(col))
        if key == "hole_id":
            blank = [i for i, t in enumerate(texts) if not t]
            if blank:
                issues.append(Issue(level, "empty_hole_id", f"Пустой номер скважины{kept}", key, len(blank)))
                if force:
                    for i in blank:
                        texts[i] = f"БЕЗ НОМЕРА (строка {i + 2})"
            if kind == "collar":
                seen: dict[str, int] = {}
                dup = 0
                for i, t in enumerate(texts):
                    if not t:
                        continue
                    k = seen.get(t, 0)
                    seen[t] = k + 1
                    if k:
                        dup += 1
                        if force:
                            texts[i] = f"{t} (повтор {k + 1})"
                if dup:
                    issues.append(
                        Issue(level, "duplicate_hole_id", f"Повторяющиеся номера скважин в устьях{kept}", key, dup)
                    )
        out[key] = texts
    comp_values = {c["column"]: number_field(c["column"], c["column"], c["column"]) for c in components}
    if any(i.severity == "error" for i in issues):
        return None, issues

    attr_values = {col: _attr(table.column(col)) for col in attributes}
    attr_values.update(originals)
    result = Normalized(kind=kind, rows=n, counts=counts)
    holes: list[str] = out["hole_id"]
    if kind == "collar":
        extra: list[str] = [k for k in ("date", "status", "stage") if k in out]
        for i in range(n):
            if not holes[i]:
                continue
            attrs: dict[str, Any] = {k: out[k][i] for k in extra if out[k][i] not in (None, "")}
            attrs.update({name: vals[i] for name, vals in attr_values.items() if vals[i] not in (None, "")})
            x, y, z, td, az, dip = (_at(out.get(k), i) for k in ("x", "y", "z", "td", "azimuth", "dip"))
            result.collars.append(CollarRow(holes[i], x, y, z, td, az, dip, attrs=attrs))
    elif kind == "survey":
        skipped = 0
        for i in range(n):
            if not holes[i]:
                continue
            depth = _at(out["depth"], i)
            if depth is None:  # a reading without its depth cannot be placed: counted and reported
                skipped += 1
                continue
            attrs = {name: vals[i] for name, vals in attr_values.items() if vals[i] not in (None, "")}
            result.surveys.append((holes[i], depth, _at(out["azimuth"], i), _at(out["dip"], i), attrs or None))
        if skipped:
            counts["skipped"] = skipped
            issues.append(
                Issue("warning", "survey_without_depth", "Замеры без глубины не загружаются", "depth", skipped)
            )
    else:
        import numpy as np

        keep = [i for i in range(n) if holes[i]]
        idx = np.asarray(keep, dtype=np.int64)
        cols: list[ColumnSpec] = []
        values: list[Any] = []
        for c in components:  # numbers stay float64 arrays (NaN = missing): no per-cell Python work
            cols.append(ColumnSpec(c["column"], "number", c["unit"].strip(), "component"))
            values.append(np.asarray(comp_values[c["column"]], dtype=float)[idx])
        if "sample_id" in out:
            cols.append(ColumnSpec("Номер пробы", "text", None, "sample"))
            values.append([out["sample_id"][i] for i in keep])
        for name, vals in attr_values.items():
            role = "source" if name.endswith(" (исходное)") else "value"
            kind_of = "number" if all(v is None or isinstance(v, float) for v in vals) else "text"
            cols.append(ColumnSpec(name, kind_of, None, role))
            values.append([vals[i] for i in keep])
        start, stop = ("depth", None) if kind == "event" else ("from", "to")
        result.table = TableData(
            name=table_name_of({"table_name": table_name, "kind": kind}),
            kind=kind,
            holes=[holes[i] for i in keep],
            from_m=np.asarray(out[start], dtype=float)[idx],
            to_m=np.asarray(out[stop], dtype=float)[idx] if stop else np.full(len(keep), np.nan),
            columns=cols,
            values=values,
        )
    return result, issues


def _isnan(values: Any) -> Any:
    import numpy as np

    return np.isnan(values)


def _at(values: Any, i: int) -> float | None:
    if values is None:
        return None
    v = float(values[i])
    return None if math.isnan(v) else v


def _attr(col: Column) -> list[Any]:
    if col.kind == "number":
        return [None if math.isnan(v) else float(v) for v in col.values.tolist()]
    return [None if v in (None, "") else str(v) for v in col.to_pylist()]


def table_name_of(f: dict[str, Any]) -> str:
    """The name a file's table gets in the set."""
    return (f.get("table_name") or "").strip() or kind_label(str(f.get("kind")))


def duplicate_targets(files: list[dict[str, Any]]) -> str | None:
    """Two files of one import that would land in the same table: the second would silently replace the first.
    Several collar or survey files are fine (they are joined); tables are not."""
    from bf_core.db.dialect import name_key

    seen: dict[tuple[str, str], str] = {}
    for f in files:
        kind = str(f.get("kind"))
        if kind in ("collar", "survey"):
            continue
        key = (kind, "" if kind == "assay" else name_key(table_name_of(f)))
        label = str(f.get("path", "")).replace("\\", "/").rsplit("/", 1)[-1]
        if key in seen:
            if kind == "assay":
                return f"Файлы «{seen[key]}» и «{label}» — оба пробы: в наборе одна таблица проб."
            return f"Файлы «{seen[key]}» и «{label}» загружаются в одну таблицу «{table_name_of(f)}»."
        seen[key] = label
    return None
