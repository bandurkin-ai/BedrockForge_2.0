"""Reading uploaded files, suggesting column mappings and validating them."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from app.formats.excel import read_excel
from app.formats.micromine import read_micromine
from app.formats.numbers import parse_numbers
from app.formats.tabular import ReadOptions, TableRead
from app.formats.text import read_text
from app.importing.schema import ELEMENTS, PRECIOUS, SCHEMAS

TEXT_SUFFIXES = {".csv", ".txt", ".tsv", ".prn"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
SUPPORTED_SUFFIXES = {".dat"} | TEXT_SUFFIXES | EXCEL_SUFFIXES


class UnsupportedFormatError(ValueError):
    pass


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    field: str | None = None
    count: int | None = None


def check_supported(suffix: str) -> None:
    """Raise UnsupportedFormatError with a message for the user if the extension is not readable."""
    suffix = suffix.lower()
    if suffix in SUPPORTED_SUFFIXES:
        return
    if suffix == ".xls":
        raise UnsupportedFormatError("Старый формат Excel (.xls) не поддерживается: сохраните файл как .xlsx")
    raise UnsupportedFormatError(
        f"Формат файла «{suffix or 'без расширения'}» не поддерживается. Подходят: .DAT (Micromine), .csv, .txt, .xlsx"
    )


def read_table(path: Path, options: ReadOptions | None = None) -> TableRead:
    """Read any supported file into a table. Options apply to text and Excel files."""
    options = options or ReadOptions()
    suffix = path.suffix.lower()
    if suffix == ".dat":
        table = read_micromine(path)
        return TableRead(table.frame, "micromine", {"encoding": table.codepage})
    if suffix in TEXT_SUFFIXES:
        return read_text(path, options)
    if suffix in EXCEL_SUFFIXES:
        return read_excel(path, options)
    check_supported(suffix)
    raise AssertionError("unreachable")


def _norm(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().upper())


def _base(name: str) -> str:
    """'Глубина (м)' -> 'Глубина', 'X, м' -> 'X': column name without its unit."""
    return re.split(r"[,(\[]", name, maxsplit=1)[0]


def suggest_mapping(kind: str, columns: list[str]) -> dict[str, str]:
    by_norm: dict[str, str] = {}
    for col in columns:
        by_norm.setdefault(_norm(col), col)
    for col in columns:  # exact names win over names stripped of units
        by_norm.setdefault(_norm(_base(col)), col)

    used: set[str] = set()
    mapping: dict[str, str] = {}
    for field in SCHEMAS[kind]:
        for alias in field.aliases:
            col = by_norm.get(alias)
            if col is not None and col not in used:
                mapping[field.key] = col
                used.add(col)
                break
    return mapping


_SYMBOL = re.compile(r"^([A-Za-z]{1,3})(?:$|[\s_,(\[/.-])")


def component_symbol(name: str) -> tuple[str | None, str | None]:
    """'Au, г/т' -> ('AU', 'g/t'); 'CU_PCT' -> ('CU', '%'); 'Comments' -> (None, None)."""
    match = _SYMBOL.match(name.strip())
    if not match or match.group(1).upper() not in ELEMENTS:
        return None, None
    lower = name.lower()
    unit = None
    if "%" in lower or "pct" in lower or "проц" in lower:
        unit = "%"
    elif "ppm" in lower or "г/т" in lower or "g/t" in lower:
        unit = "g/t" if ("г/т" in lower or "g/t" in lower) else "ppm"
    elif "ppb" in lower or "мг/т" in lower:
        unit = "ppb"
    return match.group(1).upper(), unit


def suggest_components(df: pd.DataFrame, mapping: dict[str, str]) -> list[dict[str, str]]:
    mapped = set(mapping.values())
    result = []
    for col in df.columns:
        if col in mapped or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        symbol, unit = component_symbol(str(col))
        if symbol:
            result.append({"column": col, "unit": unit or ("g/t" if symbol in PRECIOUS else "%")})
    return result


def detect_kind(columns: list[str]) -> str | None:
    best_kind, best_score = None, 0.0
    for kind, fields in SCHEMAS.items():
        required = [f for f in fields if f.required]
        mapping = suggest_mapping(kind, columns)
        score = sum(1 for f in required if f.key in mapping) / len(required)
        if score > best_score:
            best_kind, best_score = kind, score
    if best_score < 0.6:
        return None
    if best_kind == "assay":
        # From-to columns alone do not make assays: those have a sample number or grades (Au, Cu, ...).
        # Lithology, alteration, RQD and the like are interval tables.
        mapping = suggest_mapping("assay", columns)
        mapped = set(mapping.values())
        graded = any(component_symbol(str(c))[0] for c in columns if c not in mapped)
        if "sample_id" not in mapping and not graded:
            return "interval"
    return best_kind


def to_records(df: pd.DataFrame, limit: int | None = None) -> list[dict]:
    """JSON-safe rows: NaN becomes None."""
    part = df if limit is None else df.head(limit)
    return part.astype(object).where(part.notna(), None).to_dict(orient="records")


def apply_mapping(
    df: pd.DataFrame,
    kind: str,
    mapping: dict[str, str],
    components: list[dict[str, str]],
    attributes: list[str],
) -> tuple[pd.DataFrame | None, list[Issue]]:
    """Build the normalized table. Returns (None, issues) if the mapping is invalid."""
    issues: list[Issue] = []
    schema = {f.key: f for f in SCHEMAS[kind]}

    for key in mapping:
        if key not in schema:
            issues.append(Issue("error", "unknown_field", f"Неизвестное поле «{key}»", key))
    for field in schema.values():
        if field.required and not mapping.get(field.key):
            issues.append(Issue("error", "missing_required", f"Не назначена колонка для «{field.label}»", field.key))

    used: dict[str, str] = {}
    for key, col in mapping.items():
        if col not in df.columns:
            issues.append(Issue("error", "unknown_column", f"В файле нет колонки «{col}»", key))
        elif col in used:
            issues.append(Issue("error", "duplicate_column", f"Колонка «{col}» назначена дважды", key))
        else:
            used[col] = key

    if components and kind != "assay":
        issues.append(Issue("error", "components_not_allowed", "Компоненты доступны только для проб"))
    for comp in components:
        col = comp["column"]
        if col not in df.columns:
            issues.append(Issue("error", "unknown_column", f"В файле нет колонки «{col}»", col))
        elif col in used:
            issues.append(Issue("error", "duplicate_column", f"Колонка «{col}» уже назначена", col))
        elif parse_numbers(df[col]).parsed == 0:
            issues.append(Issue("error", "component_not_numeric", f"Компонент «{col}» не числовой", col))
        else:
            used[col] = f"comp:{col}"
        if not comp.get("unit", "").strip():
            issues.append(Issue("error", "missing_unit", f"Не указана единица для «{col}»", col))
    for col in attributes:
        if col not in df.columns:
            issues.append(Issue("error", "unknown_column", f"В файле нет колонки «{col}»", col))
        elif col in used:
            issues.append(Issue("error", "duplicate_column", f"Колонка «{col}» уже используется", col))
        else:
            used[col] = f"attr:{col}"

    if any(i.severity == "error" for i in issues):
        return None, issues

    out = pd.DataFrame(index=df.index)
    for key, col in mapping.items():
        field = schema[key]
        if field.numeric:
            parsed = parse_numbers(df[col])
            empty = int(parsed.values.isna().sum()) - parsed.bad
            if parsed.bad:
                example = f", например «{parsed.bad_examples[0]}»" if parsed.bad_examples else ""
                issues.append(Issue("error", "non_numeric", f"«{field.label}»: нечисловых значений{example}", key, parsed.bad))
            if empty:
                issues.append(Issue("warning", "empty_values", f"«{field.label}»: пустых значений", key, empty))
            out[key] = parsed.values
        else:
            series = df[col].astype("string").str.strip()
            empty = int((series.isna() | (series == "")).sum())
            if empty and key == "hole_id":
                issues.append(Issue("error", "empty_hole_id", "Пустой номер скважины", key, empty))
            elif key == "hole_id" and kind == "collar":
                duplicates = int(series.duplicated().sum())
                if duplicates:
                    issues.append(Issue("error", "duplicate_hole_id", "Повторяющиеся номера скважин в Collar", key, duplicates))
            elif empty and field.required:
                issues.append(Issue("warning", "empty_values", f"«{field.label}»: пустых значений", key, empty))
            out[key] = series
    for comp in components:
        parsed = parse_numbers(df[comp["column"]])
        if parsed.bad:
            example = f", например «{parsed.bad_examples[0]}»" if parsed.bad_examples else ""
            issues.append(Issue("error", "non_numeric", f"«{comp['column']}»: нечисловых значений{example}", comp["column"], parsed.bad))
        if parsed.below_limit:
            issues.append(Issue("warning", "below_limit", f"«{comp['column']}»: значения «<x» приняты как x/2", comp["column"], parsed.below_limit))
        out[f"comp:{comp['column']}"] = parsed.values
    for col in attributes:
        out[f"attr:{col}"] = df[col]
    return out, issues
