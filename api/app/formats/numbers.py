"""Lenient number parsing for values typed by people and laboratories.

Accepts a decimal point or comma, spaces as thousands separators, and
detection-limit notation: "<0.005" becomes half the limit (0.0025), the
usual convention for statistics; ">1000" becomes the limit itself. Common
"no data" marks are treated as empty.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import pandas as pd

MISSING = frozenset({
    "", "-", "—", "–", "na", "n/a", "#n/a", "nan", "null", "none", "nd", "n.d.",
    "н/д", "нд", "н.д.", "н.о.", "но", "нет",
})

_NUMBER = re.compile(r"^[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?$")
_GROUPED = re.compile(r"^[+-]?\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?$")
_LIMIT = re.compile(r"^([<>])\s*(.+)$")


@dataclass
class ParsedColumn:
    values: pd.Series  # float64, NaN where empty or not a number
    parsed: int  # values that are numbers (including detection-limit ones)
    bad: int  # non-empty values that are not numbers
    below_limit: int  # "<x" values replaced by x/2
    above_limit: int  # ">x" values replaced by x
    bad_examples: list[str]


def _plain_number(text: str) -> float | None:
    if _GROUPED.match(text):
        text = re.sub(r"[   ]", "", text)
    if not _NUMBER.match(text):
        return None
    return float(text.replace(",", "."))


def parse_value(raw) -> tuple[float | None, str]:
    """(value, kind) where kind is 'missing', 'number', 'below', 'above' or 'bad'."""
    if raw is None or (isinstance(raw, float) and math.isnan(raw)) or raw is pd.NA:
        return None, "missing"
    if isinstance(raw, bool):
        return None, "bad"
    if isinstance(raw, (int, float)):
        return float(raw), "number"
    text = str(raw).strip()
    if text.lower() in MISSING:
        return None, "missing"
    number = _plain_number(text)
    if number is not None:
        return number, "number"
    limit = _LIMIT.match(text)
    if limit:
        number = _plain_number(limit.group(2).strip())
        if number is not None:
            return (number / 2, "below") if limit.group(1) == "<" else (number, "above")
    return None, "bad"


def parse_numbers(series: pd.Series) -> ParsedColumn:
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        values = series.astype("float64")
        return ParsedColumn(values, int(values.notna().sum()), 0, 0, 0, [])

    out: list[float] = []
    counts = {"number": 0, "below": 0, "above": 0, "bad": 0}
    bad_examples: list[str] = []
    for raw in series.tolist():
        value, kind = parse_value(raw)
        out.append(math.nan if value is None else value)
        if kind != "missing":
            counts[kind] += 1
        if kind == "bad" and len(bad_examples) < 5:
            bad_examples.append(str(raw))
    parsed = counts["number"] + counts["below"] + counts["above"]
    return ParsedColumn(
        pd.Series(out, index=series.index, dtype="float64"),
        parsed, counts["bad"], counts["below"], counts["above"], bad_examples,
    )
