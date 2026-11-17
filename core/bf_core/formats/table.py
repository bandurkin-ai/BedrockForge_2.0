"""The common result of every table reader, and turning rows of cells into typed columns.

Result (`TableRead`): `columns` — a list of `Column(name, kind, values)`:
  * kind "number": `values` is a numpy float64 array, NaN = missing (never 0);
  * kind "text": `values` is a pyarrow ChunkedArray of strings, null = missing (5 million short
    strings take tens of megabytes, a Python list would take hundreds);
`rows`, `warnings` (Russian texts for the operation log) and `info` (`ReadInfo`: format, encoding,
delimiter, header row, sheet, sheets, DAT header). `to_arrow()` / `to_pandas()` give the whole table;
`Column.text()` gives identifiers as text whatever the kind (12.0 → "12").

`TableBuilder` is the streaming form of BF `formats/tabular.py::frame_from_rows`: rows arrive in
chunks, the header row gives the names, fully empty rows and unnamed empty columns are dropped,
repeated names get a suffix, a column is a number when every filled cell is a number
(`formats.numbers`), otherwise text with the "no data" marks as nulls. Differences from BF:
  * a column with numbers written like identifiers (`007`, more than 15 digits, `1E5`) stays text;
  * `ReadOptions.text_columns` keeps the named columns as text whatever is in them (identifiers);
  * counts of `<x` / `>x` replacements are also on the column (`below_limit`, `above_limit`).
A column that turns out to be text after its first chunks were stored as numbers is listed in
`TableBuilder.late_text`: the reader reads the file once more with these columns as text.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from bf_core.formats.errors import FormatError

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd
    import pyarrow as pa

# progress(fraction 0…1, message or None); it may raise (JobCancelled) to cancel the reading.
Progress = Callable[[float, str | None], None]


def no_progress(fraction: float, message: str | None = None) -> None:
    return None


@dataclass(frozen=True)
class ReadOptions:
    """How to read a CSV/TXT/Excel file. None means 'detect automatically'."""

    encoding: str | None = None
    delimiter: str | None = None  # ",", ";", "\t", "|" or "whitespace"
    header_row: int = 1  # 1-based number of the row with column names; 0 = no header
    sheet: str | None = None
    text_columns: tuple[str, ...] = ()  # columns kept as text even when every value is a number


@dataclass
class ReadInfo:
    """What was actually used to read the file (detected or chosen)."""

    format: str  # "dat" | "csv" | "xlsx" | "xls"
    encoding: str | None = None
    delimiter: str | None = None
    header_row: int | None = None
    sheet: str | None = None
    sheets: list[str] = field(default_factory=list)
    header: Any = None  # micromine_header.DatHeader of a DAT file: field types, widths, descriptions


@dataclass
class Column:
    name: str
    kind: str  # "number" | "text"
    values: Any  # number: numpy float64 array; text: pyarrow ChunkedArray of strings
    below_limit: int = 0  # "<x" cells replaced by x/2
    above_limit: int = 0  # ">x" cells replaced by x
    source: str = ""  # field type in a DAT file (C, R, N, …)
    #: Number columns of a DAT file: row → text that is not a number (read as NaN). Text columns of CSV and
    #: Excel keep such cells as text and are parsed by the importer; both paths then report them the same way.
    bad_cells: dict[int, str] | None = None

    def __len__(self) -> int:
        return len(self.values)

    def to_pylist(self) -> list[Any]:
        """Python values: floats (NaN = missing) or str / None."""
        return self.values.tolist() if self.kind == "number" else self.values.to_pylist()  # type: ignore[no-any-return]

    def to_numpy(self) -> np.ndarray:
        """float64 array, or an object array of str / None."""
        import numpy as np

        if self.kind == "number":
            return self.values  # type: ignore[no-any-return]
        return np.array(self.values.to_pylist(), dtype=object)

    def text(self) -> list[str | None]:
        """Values as identifiers: text as it is, whole numbers without ".0", None for missing."""
        if self.kind == "text":
            return self.values.to_pylist()  # type: ignore[no-any-return]
        from bf_core.formats.numbers import number_text

        return [number_text(v) or None for v in self.values.tolist()]


@dataclass
class TableRead:
    columns: list[Column]
    rows: int
    info: ReadInfo
    warnings: list[str] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)

    def to_arrow(self) -> pa.Table:
        import pyarrow as pa

        arrays = [pa.array(c.values, type=pa.float64()) if c.kind == "number" else c.values for c in self.columns]
        return pa.Table.from_arrays(arrays, names=self.names)

    def to_pandas(self) -> pd.DataFrame:
        """float64 columns and pandas "string" columns (as BF's frame)."""
        import pandas as pd

        data = {
            c.name: c.values if c.kind == "number" else pd.array(c.values.to_pylist(), dtype="string")
            for c in self.columns
        }
        return pd.DataFrame(data, columns=self.names)


def unique_names(names: Sequence[str]) -> list[str]:
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


# ------------------------------------------------------------------------------------------ builder
MAX_COLUMNS = 4096
_HINT_HEADER = "Укажите номер строки с названиями колонок (пустые строки не считаются) или 0, если заголовка нет."


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value or value.isspace()
    return isinstance(value, float) and value != value  # NaN


def _cell(value: Any) -> Any:
    """Spreadsheet dates become ISO strings; text is stripped."""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip()
    return value


def _text_of(value: Any) -> str:
    """A cell of a text column as text: whole numbers without ".0"."""
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, float):
        from bf_core.formats.numbers import number_text

        return number_text(value)
    return str(value).strip()


class _Numbers:
    """A float64 column that grows in place: no list of chunks to join at the end (that doubled the memory)."""

    __slots__ = ("data", "size")

    def __init__(self, capacity: int) -> None:
        import numpy as np

        self.data = np.empty(max(capacity, 4096), dtype=np.float64)  # untouched pages take no memory
        self.size = 0

    def reserve(self, capacity: int) -> None:
        import numpy as np

        if capacity > len(self.data):
            data = np.empty(capacity, dtype=np.float64)
            data[: self.size] = self.data[: self.size]
            self.data = data

    def append(self, values: Any) -> None:
        end = self.size + len(values)
        if end > len(self.data):
            self.data.resize(max(end, len(self.data) + max(len(self.data) // 8, 65536)), refcheck=False)
        self.data[self.size : end] = values
        self.size = end

    def take(self) -> Any:
        data = self.data
        if len(data) > self.size:
            data.resize(self.size, refcheck=False)
        return data


class _State:
    """One column while the rows arrive."""

    __slots__ = ("above", "bad", "below", "examples", "filled", "forced", "late", "numbers", "parsed", "texts")

    def __init__(self, lead: int, forced: bool, capacity: int) -> None:
        import numpy as np
        import pyarrow as pa

        self.forced = forced  # text whatever is in it
        self.numbers: _Numbers | None = None if forced else _Numbers(capacity)  # while the column is a number
        self.texts: list[Any] = []  # pyarrow string chunks once it is text
        self.late = False  # became text after numbers had been stored: their text is gone
        self.parsed = self.bad = self.below = self.above = self.filled = 0
        self.examples: list[str] = []
        if lead:  # data rows before the column first appeared: all missing
            if self.numbers is not None:
                self.numbers.append(np.full(lead, np.nan))
            else:
                self.texts.append(pa.nulls(lead, pa.string()))

    def feed(self, cells: Sequence[Any], values: Any, kinds: Any, plain: bool) -> None:
        """The next cells of the column with their classification (`numbers.classify`); `plain`: all are str."""
        import numpy as np
        import pyarrow as pa

        from bf_core.formats import numbers as nm

        counts = np.bincount(kinds, minlength=8)
        if counts[nm.BAD] and len(self.examples) < 5:
            for i in np.flatnonzero(kinds == nm.BAD)[: 5 - len(self.examples)].tolist():
                self.examples.append(_text_of(cells[i]))
        if self.numbers is not None and (counts[nm.BAD] or counts[nm.IDLIKE]):
            if self.parsed:
                self.late = True
            else:  # everything so far was empty: as text it is nulls
                self.texts = [pa.nulls(self.numbers.size, pa.string())]
            self.numbers = None
        self.parsed += int(counts[nm.NUMBER] + counts[nm.BELOW] + counts[nm.ABOVE] + counts[nm.IDLIKE])
        self.bad += int(counts[nm.BAD])
        self.below += int(counts[nm.BELOW])
        self.above += int(counts[nm.ABOVE])
        self.filled += len(cells) - int(counts[nm.EMPTY])
        if self.late:
            return
        if self.numbers is not None:
            self.numbers.append(values)
        else:
            mask = (kinds == nm.EMPTY) | (kinds == nm.MARKER)
            texts = [c.strip() for c in cells] if plain else [_text_of(c) for c in cells]
            self.texts.append(pa.array(texts, type=pa.string(), mask=mask))

    def feed_missing(self, count: int) -> None:
        """Rows in which the column has no cell at all."""
        import numpy as np
        import pyarrow as pa

        if self.late:
            return
        if self.numbers is not None:
            self.numbers.append(np.full(count, np.nan))
        else:
            self.texts.append(pa.nulls(count, pa.string()))


class TableBuilder:
    """Rows of cells (text or spreadsheet values), chunk by chunk → typed columns.

    `header_row`: 1-based number of the row with the names among the rows given; 0 = no header.
    `text_columns`: names of columns kept as text; `text_indices`: the same by position (the second
    pass of a reader, see `late_text`). `trim_trailing_empty`: empty rows at the end do not count
    as rows of the file (spreadsheets).

    `add(rows)` takes rows of cells; `add_columns(columns, rows)` takes the same cells already
    arranged by column (a text reader does that for blocks where every line has the same number of
    cells) and may be used once the title rows and the header have been given (`header_pending == 0`).
    """

    def __init__(
        self,
        header_row: int,
        text_columns: Sequence[str] = (),
        text_indices: Sequence[int] = (),
        trim_trailing_empty: bool = False,
    ) -> None:
        if header_row < 0:
            raise FormatError("header_row", "Номер строки заголовка не может быть отрицательным.", _HINT_HEADER)
        self.header_row = header_row
        self._text_names = set(text_columns)
        self._text_indices = set(text_indices)
        self._trim = trim_trailing_empty
        self._seen = 0  # rows given so far (title rows and the header included)
        self._last_filled = 0  # number of the last row with any value
        self._header: list[Any] | None = None
        self._states: list[_State] = []
        self._expected = 0
        self.rows = 0  # data rows kept

    @property
    def late_text(self) -> set[int]:
        return {i for i, s in enumerate(self._states) if s.late}

    def expect(self, rows: int) -> None:
        """About how many data rows the file holds: number columns are allocated once (optional)."""
        self._expected = rows
        for state in self._states:
            if state.numbers is not None:
                state.numbers.reserve(rows)

    @property
    def header_pending(self) -> int:
        """Rows still to be given before the data starts (title rows and the header)."""
        return max(0, self.header_row - self._seen)

    def _names(self, width: int) -> tuple[list[str], list[bool]]:
        header = self._header or []
        named = [i < len(header) and not _is_empty(header[i]) for i in range(width)]
        return [str(header[i]).strip() if named[i] else f"Колонка {i + 1}" for i in range(width)], named

    def _forced(self, index: int, width: int) -> bool:
        if index in self._text_indices:
            return True
        return bool(self._text_names) and unique_names(self._names(width)[0])[index] in self._text_names

    def add(self, rows: list[list[Any]]) -> None:
        """The next rows of the file, in order."""
        if not rows:
            return
        start = 0
        if self._seen < self.header_row:  # title rows and the header itself
            start = min(len(rows), self.header_row - self._seen)
            if self._seen + start == self.header_row:
                self._header = [_cell(v) for v in rows[start - 1]]
        if self._trim:
            for k in range(len(rows) - 1, -1, -1):
                if any(v not in (None, "") for v in rows[k]):
                    self._last_filled = max(self._last_filled, self._seen + k + 1)
                    break
        self._seen += len(rows)
        data = rows[start:] if start else rows
        if not data:
            return
        lengths = set(map(len, data))
        width = max(lengths)
        if width > MAX_COLUMNS:
            self._too_wide()
        if len(lengths) > 1:
            data = [r if len(r) == width else [*r, *([None] * (width - len(r)))] for r in data]
        self._columns(list(zip(*data, strict=True)), len(data), False)

    def add_columns(self, columns: list[Sequence[Any]], rows: int, plain: bool = False) -> None:
        """The next `rows` data rows, given as their columns (each of length `rows`); `plain`: every cell is a str."""
        if self.header_pending:
            raise RuntimeError("give the title rows and the header with add() first")
        self._seen += rows
        if len(columns) > MAX_COLUMNS:
            self._too_wide()
        if rows:
            self._columns(columns, rows, plain)

    @staticmethod
    def _too_wide() -> None:
        raise FormatError(
            "too_large",
            f"В строке больше {MAX_COLUMNS} колонок: это не таблица скважин или выбран не тот разделитель.",
            "Проверьте разделитель колонок и содержимое файла.",
        )

    def _columns(self, columns: list[Sequence[Any]], count: int, all_plain: bool) -> None:
        import numpy as np

        from bf_core.formats import numbers as nm

        parsed = []
        for cells in columns:
            plain = all_plain or set(map(type, cells)) == {str}
            if not plain:
                cells = [_cell(v) for v in cells]
            values, kinds = nm.classify(cells, plain)
            parsed.append((cells, values, kinds, plain))
        # Fully empty rows are dropped.
        empty = np.ones(count, dtype=bool)
        for _, _, kinds, _ in parsed:
            empty &= kinds == nm.EMPTY
            if not empty.any():
                break
        else:
            keep = ~empty
            count = int(keep.sum())
            if count == 0:
                return
            flags = keep.tolist()
            parsed = [
                ([c for c, k in zip(cells, flags, strict=True) if k], v[keep], kd[keep], p)
                for cells, v, kd, p in parsed
            ]
        width = max(len(self._states), len(columns))
        for i in range(len(self._states), width):
            forced = self._forced(i, max(width, len(self._header or [])))
            self._states.append(_State(self.rows, forced, self._expected))
        for i, state in enumerate(self._states):
            if i < len(parsed):
                state.feed(*parsed[i])
            else:
                state.feed_missing(count)
        self.rows += count

    def finish(self) -> tuple[list[Column], list[str]]:
        """Columns and notes for the log. Check `late_text` first: such columns need a second pass."""
        import pyarrow as pa

        if self.late_text:
            raise RuntimeError("columns in late_text need a second pass")
        total = self._last_filled if self._trim else self._seen
        if self.header_row > total:
            raise FormatError(
                "header_row",
                f"В файле только {total} строк, а строка заголовка указана {self.header_row}",
                _HINT_HEADER,
            )
        width = max(len(self._states), len(self._header or []))
        header, named = self._names(width)
        for i in range(len(self._states), width):  # named columns without a single data row
            self._states.append(_State(self.rows, self._forced(i, width), 0))
        # Unnamed columns without data are dropped (spreadsheets are full of them).
        keep = [i for i in range(width) if named[i] or self._states[i].filled]
        names = unique_names([header[i] for i in keep])
        columns: list[Column] = []
        notes: list[str] = []
        for name, i in zip(names, keep, strict=True):
            s = self._states[i]
            if s.numbers is not None and s.parsed:
                columns.append(Column(name, "number", s.numbers.take(), s.below, s.above))
                s.numbers = None
                if s.below:
                    notes.append(f"{name}: {s.below} знач. вида «<x» (ниже предела обнаружения) заменены на x/2")
                if s.above:
                    notes.append(f"{name}: {s.above} знач. вида «>x» заменены на x")
                continue
            # A column of nothing but empty cells is text.
            chunks = [pa.nulls(self.rows, pa.string())] if s.numbers is not None else s.texts
            columns.append(Column(name, "text", pa.chunked_array(chunks, type=pa.string())))
            if not (s.forced and i not in self._text_indices) and s.parsed and 0 < s.bad <= max(3, s.parsed // 20):
                notes.append(
                    f"{name}: колонка прочитана как текст из-за нечисловых значений, например «{s.examples[0]}»"
                )
        return columns, notes
