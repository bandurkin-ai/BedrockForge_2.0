"""Lenient number parsing for values typed by people and laboratories (ТЗ 7.7, 9.6).

Accepts a decimal point or comma, spaces (also no-break ones) between digit groups
(`4 584 699,70`) and detection-limit notation: `<0.005` becomes half the limit (0.0025), the usual
convention for statistics; `>1000` becomes the limit itself — both are counted. The usual
"no data" marks (`-`, `н/д`, `н.о.`, `NA`, `N/A` …) are empty. A missing value is always NaN in a
float64 array, never 0.

Ported from BF `formats/numbers.py`. Differences: values are numpy float64 arrays (not pandas
Series); big columns go through a numba kernel that gives bit-for-bit the same result as the
Python reference (`classify_reference`); a value that overflows a double (`1e999`) is not a number;
numbers written like identifiers (`007`, more than 15 digits, `1E5`) are marked IDLIKE so that a
table reader can keep such a column as text — for `parse_numbers` they are ordinary numbers.

Public: `MISSING`, `parse_value`, `parse_numbers` → `ParsedColumn`, `classify`, `number_text`.
"""

from __future__ import annotations

import math
import numbers as _abc
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

MISSING = frozenset(
    {
        *("", "-", "—", "–", "na", "n/a", "#n/a", "nan", "null", "none", "nd", "n.d."),
        *("н/д", "нд", "н.д.", "н.о.", "но", "нет"),
    }
)

# Kinds of a cell (uint8 codes of `classify`).
EMPTY, NUMBER, BELOW, ABOVE, BAD, IDLIKE, MARKER = 0, 1, 2, 3, 4, 5, 6
_UNSURE = 7
_KIND_NAMES = ("missing", "number", "below", "above", "bad", "number", "missing")

_SEPARATORS = " \u00a0\u202f"  # space, no-break space, narrow no-break space
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?$")
_GROUPED = re.compile(rf"^[+-]?\d{{1,3}}(?:[{_SEPARATORS}]\d{{3}})+(?:[.,]\d+)?$")
_LIMIT = re.compile(r"^([<>])\s*(.+)$")
_INTEGER = re.compile(r"[+-]?(\d+)")
_E_FORM = re.compile(r"\d+[eE]\d+")
_UNGROUP = str.maketrans("", "", _SEPARATORS)

KERNEL_MIN = 4096  # fewer cells than this are parsed in Python: numba is not loaded for small tables …
KERNEL_SMALL = 64  # … unless the kernel is already loaded: then it takes every column longer than this


@dataclass
class ParsedColumn:
    values: np.ndarray  # float64, NaN where empty or not a number
    parsed: int  # values that are numbers (including detection-limit ones)
    bad: int  # non-empty values that are not numbers
    below_limit: int  # "<x" values replaced by x/2
    above_limit: int  # ">x" values replaced by x
    bad_examples: list[str] = field(default_factory=list)
    empty: int = 0  # empty cells and "no data" marks


def _plain_number(text: str) -> float | None:
    if _GROUPED.match(text):
        text = text.translate(_UNGROUP)
    if not _NUMBER.match(text):
        return None
    value = float(text.replace(",", "."))
    return value if math.isfinite(value) else None


def _idlike(text: str) -> bool:
    """A number written like an identifier: leading zeros, more digits than a double keeps, or "1E5"."""
    m = _INTEGER.fullmatch(text)
    if m is None:
        return _E_FORM.fullmatch(text) is not None
    digits = m.group(1)
    return (len(digits) > 1 and digits[0] == "0") or len(digits.lstrip("0")) > 15


def _classify_text(text: str) -> tuple[float, int]:
    text = text.strip()
    if not text:
        return math.nan, EMPTY
    if text.lower() in MISSING:
        return math.nan, MARKER
    number = _plain_number(text)
    if number is not None:
        return number, (IDLIKE if _idlike(text) else NUMBER)
    limit = _LIMIT.match(text)
    if limit:
        number = _plain_number(limit.group(2).strip())
        if number is not None:
            return (number / 2, BELOW) if limit.group(1) == "<" else (number, ABOVE)
    return math.nan, BAD


def _classify_object(raw: Any) -> tuple[float, int]:
    """One cell of any type: text, a number of a spreadsheet, None."""
    if raw is None or type(raw).__name__ in ("NAType", "NaTType"):
        return math.nan, EMPTY
    if isinstance(raw, str):
        return _classify_text(raw)
    if isinstance(raw, bool) or type(raw).__name__ in ("bool", "bool_"):
        return math.nan, BAD
    if isinstance(raw, _abc.Real):
        value = float(raw)
        if math.isnan(value):
            return math.nan, EMPTY
        if not math.isfinite(value):
            return math.nan, BAD
        if isinstance(raw, _abc.Integral) and abs(int(raw)) >= 1 << 53:
            return value, IDLIKE
        return value, NUMBER
    return _classify_text(str(raw))


def parse_value(raw: Any) -> tuple[float | None, str]:
    """(value, kind) where kind is 'missing', 'number', 'below', 'above' or 'bad'."""
    value, kind = _classify_object(raw)
    return (None if math.isnan(value) else value), _KIND_NAMES[kind]


def classify_reference(cells: Sequence[Any]) -> tuple[np.ndarray, np.ndarray]:
    """`classify` in plain Python: the definition the kernel is tested against."""
    import numpy as np

    values = np.full(len(cells), np.nan, dtype=np.float64)
    kinds = np.zeros(len(cells), dtype=np.uint8)
    for i, cell in enumerate(cells):
        values[i], kinds[i] = _classify_object(cell)
    return values, kinds


_markers: Any = None


def _marker_table() -> tuple[np.ndarray, np.ndarray]:
    global _markers
    if _markers is None:
        import numpy as np

        marks = sorted(m for m in MISSING if m)
        table = np.zeros((len(marks), max(map(len, marks))), dtype=np.uint32)
        for i, mark in enumerate(marks):
            table[i, : len(mark)] = [ord(ch) for ch in mark]
        _markers = (table, np.array([len(m) for m in marks], dtype=np.int64))
    return _markers  # type: ignore[no-any-return]


_kernel_loaded = False


def load_kernel() -> None:
    """Load (compile or take from the cache) the numba kernel now: readers of big files call it once."""
    global _kernel_loaded
    if not _kernel_loaded:
        _kernel(["1"])
        _kernel_loaded = True


def _kernel(strings: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    """The numba kernel on a column of strings; what it cannot decide exactly is left to the reference."""
    import numpy as np

    from bf_core.formats._numkernel import classify_joined

    n = len(strings)
    # The cells go to the kernel as one UCS-4 array, NUL between them: no Python work per cell.
    codes = np.frombuffer("\x00".join(strings).encode("utf-32-le", "surrogatepass"), dtype=np.uint32)
    values = np.empty(n, dtype=np.float64)
    kinds = np.empty(n, dtype=np.uint8)
    markers, marker_len = _marker_table()
    if classify_joined(codes, n, markers, marker_len, values, kinds) != n:
        # A NUL inside a cell breaks the separation: such cells go to the reference, the rest again.
        slow = [i for i, c in enumerate(strings) if "\x00" in c]
        skip = set(slow)
        clean = ["" if i in skip else c for i, c in enumerate(strings)]
        values, kinds = _kernel(clean)
        for i in slow:
            values[i], kinds[i] = _classify_text(strings[i])
        return values, kinds
    for i in np.flatnonzero(kinds == _UNSURE).tolist():
        values[i], kinds[i] = _classify_text(strings[i])
    return values, kinds


def _use_kernel(n: int) -> bool:
    return n >= KERNEL_MIN or (_kernel_loaded and n >= KERNEL_SMALL)


def classify(cells: Sequence[Any], plain: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """float64 values (NaN where there is no number) and uint8 kinds (EMPTY … MARKER) of the cells.

    `plain`: the caller knows that every cell is a `str` (saves a pass over the cells).
    """
    import numpy as np

    n = len(cells)
    if not _use_kernel(n):
        return classify_reference(cells)
    if plain:
        return _kernel(cells)
    types = set(map(type, cells))
    if types == {str}:
        return _kernel(cells)
    if types <= {str, type(None)}:
        return _kernel([c if c is not None else "" for c in cells])
    where = [i for i, c in enumerate(cells) if type(c) is str]
    if not _use_kernel(len(where)):
        return classify_reference(cells)
    values = np.full(n, np.nan, dtype=np.float64)
    kinds = np.zeros(n, dtype=np.uint8)
    values[where], kinds[where] = _kernel([cells[i] for i in where])
    is_text = np.zeros(n, dtype=bool)
    is_text[where] = True
    for i in np.flatnonzero(~is_text).tolist():
        values[i], kinds[i] = _classify_object(cells[i])
    return values, kinds


def _cells_of(values: Any) -> tuple[Any, Sequence[Any] | None]:
    """(float64 array, None) for numeric input, else (None, list of cells)."""
    import numpy as np

    if hasattr(values, "to_pylist"):  # pyarrow Array / ChunkedArray
        return None, values.to_pylist()
    if hasattr(values, "to_numpy") and not isinstance(values, np.ndarray):  # pandas Series
        values = values.to_numpy()
    if isinstance(values, np.ndarray):
        if values.dtype.kind in "fiu":
            return values.astype(np.float64), None
        return None, values.tolist()
    return None, values if isinstance(values, (list, tuple)) else list(values)


def parse_numbers(values: Iterable[Any]) -> ParsedColumn:
    """A column as float64 with counts: list, numpy array, pandas Series or pyarrow (Chunked)Array."""
    import numpy as np

    numeric, cells = _cells_of(values)
    if cells is None:
        return ParsedColumn(numeric, int(np.isfinite(numeric).sum()), 0, 0, 0, [], int(np.isnan(numeric).sum()))
    out, kinds = classify(cells)
    counts = np.bincount(kinds, minlength=8)
    examples = [str(cells[i]) for i in np.flatnonzero(kinds == BAD)[:5].tolist()]
    return ParsedColumn(
        values=out,
        parsed=int(counts[NUMBER] + counts[BELOW] + counts[ABOVE] + counts[IDLIKE]),
        bad=int(counts[BAD]),
        below_limit=int(counts[BELOW]),
        above_limit=int(counts[ABOVE]),
        bad_examples=examples,
        empty=int(counts[EMPTY] + counts[MARKER]),
    )


def number_text(value: float) -> str:
    """A number as an identifier would be typed: 12.0 → "12", 0.1 → "0.1", NaN → ""."""
    value = float(value)
    if math.isnan(value):
        return ""
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value)
