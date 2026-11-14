"""Turning rows of cells (from a text file or a spreadsheet) into a typed table."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from app.formats.numbers import MISSING, parse_numbers


@dataclass(frozen=True)
class ReadOptions:
    """How to read a CSV/TXT/Excel file. None means 'detect automatically'."""

    encoding: str | None = None
    delimiter: str | None = None  # ",", ";", "\t", "|" or "whitespace"
    header_row: int = 1  # 1-based number of the row with column names; 0 = no header
    sheet: str | None = None


@dataclass
class TableRead:
    frame: pd.DataFrame
    format: str  # "micromine" | "text" | "excel"
    # What was actually used, e.g. {"encoding": "cp1251", "delimiter": ";"}
    detected: dict = field(default_factory=dict)
    sheets: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for name in names:
        if name in seen:
            seen[name] += 1
            result.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 1
            result.append(name)
    return result


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return isinstance(value, str) and value.strip() == ""


def _cell(value):
    """Spreadsheet dates become ISO strings; text is stripped."""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip()
    return value


def frame_from_rows(rows: list[list], header_row: int, notes: list[str]) -> pd.DataFrame:
    """Build a table: header from `header_row`, data below it, numeric columns detected."""
    if header_row < 0:
        raise ValueError("header_row must be >= 0")
    if header_row > len(rows):
        raise ValueError(f"В файле только {len(rows)} строк, а строка заголовка указана {header_row}")

    width = max((len(r) for r in rows), default=0)
    padded = [[_cell(v) for v in r] + [None] * (width - len(r)) for r in rows]

    if header_row == 0:
        named = [False] * width
        header = [f"Колонка {i + 1}" for i in range(width)]
        data = padded
    else:
        raw_header = padded[header_row - 1]
        named = [not _is_empty(h) for h in raw_header]
        header = [str(h).strip() if named[i] else f"Колонка {i + 1}" for i, h in enumerate(raw_header)]
        data = padded[header_row:]

    # Drop fully empty rows, and unnamed columns without data (spreadsheets are full of them).
    data = [r for r in data if not all(_is_empty(v) for v in r)]
    keep = [i for i in range(width) if named[i] or any(not _is_empty(r[i]) for r in data)]
    header = unique_names([header[i] for i in keep])
    frame = pd.DataFrame([[r[i] for i in keep] for r in data], columns=header, dtype=object)

    for col in frame.columns:
        parsed = parse_numbers(frame[col])
        if parsed.parsed > 0 and parsed.bad == 0:
            frame[col] = parsed.values
            if parsed.below_limit:
                notes.append(f"{col}: {parsed.below_limit} знач. вида «<x» (ниже предела обнаружения) заменены на x/2")
            if parsed.above_limit:
                notes.append(f"{col}: {parsed.above_limit} знач. вида «>x» заменены на x")
        else:
            frame[col] = frame[col].map(lambda v: None if _is_empty(v) or (isinstance(v, str) and v.lower() in MISSING) else str(v)).astype("string")
            if parsed.parsed > 0 and parsed.bad > 0 and parsed.bad <= max(3, parsed.parsed // 20):
                notes.append(f"{col}: колонка прочитана как текст из-за нечисловых значений, например «{parsed.bad_examples[0]}»")
    return frame.reset_index(drop=True)
