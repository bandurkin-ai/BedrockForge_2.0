"""Reading uploaded files, suggesting column mappings and validating them."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from app.formats.micromine import read_micromine
from app.importing.schema import ELEMENTS, PRECIOUS, SCHEMAS


class UnsupportedFormatError(ValueError):
    pass


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    field: str | None = None
    count: int | None = None


def read_table(path: Path) -> tuple[pd.DataFrame, str]:
    """Return (data, format name). New formats (CSV, Excel) plug in here."""
    suffix = path.suffix.lower()
    if suffix == ".dat":
        return read_micromine(path).frame, "micromine"
    raise UnsupportedFormatError(f"Формат файла «{suffix or 'без расширения'}» пока не поддерживается")


def _norm(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().upper())


def suggest_mapping(kind: str, columns: list[str]) -> dict[str, str]:
    by_norm: dict[str, str] = {}
    for col in columns:
        by_norm.setdefault(_norm(col), col)

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


def suggest_components(df: pd.DataFrame, mapping: dict[str, str]) -> list[dict[str, str]]:
    mapped = set(mapping.values())
    result = []
    for col in df.columns:
        if col in mapped or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        symbol = _norm(col)
        if symbol in ELEMENTS:
            result.append({"column": col, "unit": "g/t" if symbol in PRECIOUS else "%"})
    return result


def detect_kind(columns: list[str]) -> str | None:
    best_kind, best_score = None, 0.0
    for kind, fields in SCHEMAS.items():
        required = [f for f in fields if f.required]
        mapping = suggest_mapping(kind, columns)
        score = sum(1 for f in required if f.key in mapping) / len(required)
        if score > best_score:
            best_kind, best_score = kind, score
    return best_kind if best_score >= 0.6 else None


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
        elif not pd.api.types.is_numeric_dtype(df[col]):
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
            series = pd.to_numeric(df[col], errors="coerce")
            non_numeric = int((series.isna() & df[col].notna()).sum())
            empty = int((series.isna() & df[col].isna()).sum())
            if non_numeric:
                issues.append(Issue("error", "non_numeric", f"«{field.label}»: нечисловых значений", key, non_numeric))
            if empty:
                issues.append(Issue("warning", "empty_values", f"«{field.label}»: пустых значений", key, empty))
            out[key] = series
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
        out[f"comp:{comp['column']}"] = df[comp["column"]]
    for col in attributes:
        out[f"attr:{col}"] = df[col]
    return out, issues
