"""Block files in portions: Micromine DAT, CSV and CSV Micromine (ТЗ 7.31 «Форматы», 8.7.2; ADR 0023 decision 5, Д-13).

A model of 20 million blocks never sits in memory: a source gives it as `BlockChunk`s — the columns of a few hundred
thousand rows, numbers as float64 (NaN — empty, never 0) and text as codes into the chunk's own labels — and the
importer (`importing/object_blockfile.py`) spools them to disk. The sources:

* `DatSource` — Micromine DAT (types C, N, R, F, L, S, B; code pages cp1251, cp1252, … and BOCU-1; the header of 4096
  or 8192 bytes: `micromine_header`). The records are a `numpy.memmap`; only the wanted columns are converted.
  A number written as text (N) that is not a number is empty and counted; `<x`, `>x` as in the tables (`numbers`).
* `CsvSource` — CSV / TXT: the encoding (UTF-8 with or without BOM, UTF-16, cp1251, cp866, KOI8-R: `text`), the
  delimiter (`;` `,` tab `|`, runs of spaces), a decimal comma or point (both read: `numbers.classify`). A **CSV
  Micromine** has three lines before the data — the names, the types (`DOUBLE`, `CHARACTER`, …) and the grid
  `x0, dx, y0, dy, z0, dz, CENTRE|CORNER` — the grid goes to `hint` (`blocks_roles.micromine_grid_line`). The type of
  a column is told by the first lines of the file (a column with text in them is text; one with only numbers or
  nothing — a number); a text in a number column later in the file is empty and counted.

A source that follows the same contract — `format`, `label`, `names`, `kinds`, `rows`, `hint`, `encoding`, `notes`,
`chunks(want, rows, tick, limit)`, `close()` — is read by the same importer (Datamine, `datamine_blocks.py`). `chunks`
may raise (a cancel in `tick`); `notes` — warnings for the log — are complete when the iteration has ended.

`EMPTY_CODES` (−99, `blocks_roles`) are offered, the user types the others: `empty_to_nan` turns the codes into NaN
in place (counts them) and `text_is_empty` does the same for the labels of a text field.

Ported from BF `formats/blockmodel.py` (`_read_csv`, `_grid_line`) and `formats/micromine.py`; differences: portions
instead of a whole table, the encodings and delimiters of `formats/text.py`, the numbers of `formats/numbers.py`.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import repeat
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.blocks.grid_infer import GridHint
from bf_core.formats.errors import FormatError
from bf_core.formats.table import unique_names

__all__ = [
    "CHUNK_ROWS",
    "BlockChunk",
    "BlockSource",
    "CsvSource",
    "DatSource",
    "TextColumn",
    "empty_to_nan",
    "open_block_file",
    "peek_names",
    "text_is_empty",
]

CHUNK_ROWS = 1 << 18  # rows of one portion
SAMPLE_LINES = 100_000  # the lines of a CSV that tell the types of its columns
NUMBER, TEXT = "number", "text"
CSV_TYPES = frozenset({"DOUBLE", "NUMERIC", "CHARACTER", "REAL", "FLOAT", "INTEGER", "LONG", "SHORT", "BYTE", "TEXT",
                       "STRING", "C", "N", "R"})  # fmt: skip
CSV_TEXT_TYPES = frozenset({"CHARACTER", "TEXT", "STRING", "C"})
Tick = Callable[[float, str | None], None]
_HINT_DAT = "Откройте файл в Micromine и сохраните его заново или экспортируйте таблицу в CSV."


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


@dataclass
class TextColumn:
    """A text column of a portion: `codes` int32 into `labels` (−1 — empty), the labels of this portion only."""

    codes: np.ndarray
    labels: list[str]


@dataclass
class BlockChunk:
    """Rows [first, first + rows) of the file (0-based data rows): the wanted columns by name."""

    first: int
    rows: int
    numbers: dict[str, np.ndarray] = field(default_factory=dict)  # float64, NaN — empty
    texts: dict[str, TextColumn] = field(default_factory=dict)


def _text_of(array: Any) -> TextColumn:
    """An Arrow string array (null — empty; «» — empty too) → codes and labels."""
    import pyarrow as pa

    if isinstance(array, pa.ChunkedArray):
        array = array.combine_chunks()
    enc = array.dictionary_encode()
    codes = np.asarray(enc.indices.fill_null(-1).to_numpy(zero_copy_only=False)).astype(np.int32, copy=False)
    labels = [str(v) for v in enc.dictionary.to_pylist()]
    if "" in labels:
        blank = labels.index("")
        codes = np.where(codes == blank, -1, codes).astype(np.int32)
    return TextColumn(codes, labels)


def empty_to_nan(values: np.ndarray, codes: Sequence[float]) -> int:
    """The user's codes of «пусто» (−99, …) → NaN, in place: how many values were turned. 0 is a value, not an empty."""
    if not len(codes) or not len(values):
        return 0
    hit = np.isin(values, np.asarray([float(c) for c in codes], dtype=np.float64))
    n = int(hit.sum())
    if n:
        values[hit] = np.nan
    return n


def text_is_empty(labels: Sequence[str], codes: Sequence[float]) -> np.ndarray:
    """Which labels of a text field are a code of «пусто» (`-99` written as text)."""
    from bf_core.formats.numbers import parse_value

    out = np.zeros(len(labels), dtype=bool)
    if not len(codes):
        return out
    want = {float(c) for c in codes}
    for i, label in enumerate(labels):
        value, _ = parse_value(label)
        out[i] = value is not None and float(value) in want
    return out


class BlockSource:
    """The contract of a source of blocks (see the module docstring); the readers below fill it in."""

    format = ""  # "dat" | "csv" | "csv_micromine" | "dm" | "fbm"
    label = ""  # for the user: «Micromine DAT»
    encoding: str | None = None
    names: list[str]
    kinds: dict[str, str]  # name → "number" | "text"
    rows: int | None = None  # None — not known until the file is read
    hint: GridHint | None = None
    types: list[str] | None = None  # the types line of a CSV Micromine
    notes: list[str]

    def __init__(self) -> None:
        self.names, self.kinds, self.notes = [], {}, []

    @property
    def rows_estimate(self) -> int:
        return int(self.rows or 0)

    def chunks(
        self, want: Sequence[str] | None = None, rows: int = CHUNK_ROWS, tick: Tick | None = None,
        limit: int | None = None,
    ) -> Iterator[BlockChunk]:  # pragma: no cover - the contract
        raise NotImplementedError

    def close(self) -> None:
        return None


# --- Micromine DAT -------------------------------------------------------------------------------------------


class DatSource(BlockSource):
    format, label = "dat", "Micromine DAT"

    def __init__(self, path: str | Path, encoding: str | None = None) -> None:
        from bf_core.formats.micromine_header import FIELD_TYPES, read_header

        super().__init__()
        self.path = Path(path)
        h = self.header = read_header(self.path, encoding)
        self.encoding = h.encoding
        if h.record_size > 16 << 20:
            raise FormatError("too_large", f"Длина записи {_n(h.record_size)} байт больше допустимой.", _HINT_DAT)
        body = self.path.stat().st_size - h.data_offset
        rows, rest = divmod(body, h.record_size)
        if body < 0 or rest:
            raise FormatError(
                "truncated",
                f"Файл обрезан или повреждён: длина данных {_n(max(body, 0))} байт не кратна длине записи "
                f"{h.record_size}.",
                "Скопируйте файл заново; если он такой же — сохраните его заново в Micromine.",
            )
        self.rows = rows
        all_names = h.names
        self._used = [i for i, f in enumerate(h.fields) if f.kind in FIELD_TYPES]
        self.notes = list(h.warnings)
        for i, f in enumerate(h.fields):
            if f.kind not in FIELD_TYPES:
                self.notes.append(f"Поле «{all_names[i]}» неизвестного типа «{f.kind}» (ширина {f.width}) пропущено.")
        self.names = [all_names[i] for i in self._used]
        self.kinds = {all_names[i]: (TEXT if h.fields[i].kind == "C" else NUMBER) for i in self._used}

    def chunks(
        self, want: Sequence[str] | None = None, rows: int = CHUNK_ROWS, tick: Tick | None = None,
        limit: int | None = None,
    ) -> Iterator[BlockChunk]:
        from bf_core.formats import bocu1, micromine
        from bf_core.formats.micromine_header import BINARY_TYPES, BOCU

        h, total = self.header, int(self.rows or 0)
        if limit is not None:
            total = min(total, max(0, limit))
        if not total:
            return
        names = h.names
        wanted = set(self.names if want is None else want)
        picks = [i for i in self._used if names[i] in wanted]
        step = max(1024, min(rows, (64 << 20) // h.record_size))
        dtype = np.dtype({"names": [f"f{i}" for i in range(len(h.fields))] + ["eol"],
                          "formats": [BINARY_TYPES.get(f.kind, f"S{f.width}") for f in h.fields] + ["S2"]})  # fmt: skip
        below: dict[str, int] = {}
        above: dict[str, int] = {}
        bad: dict[str, tuple[int, str]] = {}
        damaged: dict[str, int] = {}
        wrong_eol = 0
        with self.path.open("rb") as fh:  # read, not mapped: the pages of a big file do not pile up in the process
            fh.seek(h.data_offset)
            for a in range(0, total, step):
                b = min(total, a + step)
                if tick is not None:
                    tick(a / total, f"Чтение записей: {_n(a)} из {_n(total)}")
                block = np.fromfile(fh, dtype=dtype, count=b - a)
                if len(block) != b - a:
                    raise FormatError("truncated", "Файл обрезан: записей меньше, чем в заголовке.", _HINT_DAT)
                wrong = block["eol"] != b"\r\n"
                if wrong.any():
                    if a == 0 and wrong[0]:
                        raise FormatError("broken", "Первая запись не заканчивается CRLF: структура файла не "
                                          "совпадает с заголовком.", _HINT_DAT)  # fmt: skip
                    wrong_eol += int(wrong.sum())
                chunk = BlockChunk(a, b - a)
                for i in picks:
                    f, name = h.fields[i], names[i]
                    column = block[f"f{i}"]
                    if f.kind in BINARY_TYPES:
                        chunk.numbers[name] = column.astype(np.float64)
                    elif f.kind == "N":
                        raw = np.ascontiguousarray(column)
                        if h.encoding == BOCU:
                            codes = bocu1.number_bytes(raw.view(np.uint8).reshape(b - a, f.width))
                            raw = np.ascontiguousarray(codes).view(f"S{f.width}").ravel()
                        values, lo, hi, wrong_rows = micromine._numbers(raw)
                        chunk.numbers[name] = values
                        below[name], above[name] = below.get(name, 0) + lo, above.get(name, 0) + hi
                        if wrong_rows:
                            count, example = bad.get(name, (0, raw[wrong_rows[0]].decode("latin-1").strip()))
                            bad[name] = (count + len(wrong_rows), example)
                    else:
                        text, broken, _ = micromine._text_chunk(np.ascontiguousarray(column), h.encoding)
                        chunk.texts[name] = _text_of(text)
                        damaged[name] = damaged.get(name, 0) + broken
                yield chunk
        for name, (count, example) in bad.items():
            self.notes.append(f"Поле «{name}»: {_n(count)} знач. не являются числами (например «{example}») — "
                              "прочитаны как пустые.")  # fmt: skip
        for name, count in below.items():
            if count:
                self.notes.append(f"{name}: {_n(count)} знач. вида «<x» заменены на x/2.")
        for name, count in above.items():
            if count:
                self.notes.append(f"{name}: {_n(count)} знач. вида «>x» заменены на x.")
        for name, count in damaged.items():
            if count:
                self.notes.append(f"Поле «{name}»: {_n(count)} знач. содержат символы, которые не читаются в "
                                  f"кодировке {h.encoding}.")  # fmt: skip
        if wrong_eol:
            self.notes.append(f"{_n(wrong_eol)} записей не заканчиваются CRLF: возможно, файл повреждён.")
        if tick is not None:
            tick(1.0, f"Прочитано записей: {_n(total)}")


# --- CSV, CSV Micromine ----------------------------------------------------------------------------------------


class _Counts:
    """What `text.line_blocks` counts while it decodes."""

    replaced = 0
    nul = 0


def _columns(lines: list[str], delimiter: str, width: int, quoted: bool) -> list[list[str]]:
    """The lines as `width` columns of cells (a short line is filled with empty cells, a long one is cut)."""
    from bf_core.formats.text import DELIMITERS, split_lines

    sep = DELIMITERS[delimiter]
    if sep is not None and not quoted:
        if set(map(str.count, lines, repeat(sep))) == {width - 1}:  # every line has `width` cells
            flat = sep.join(lines).split(sep)
            return [flat[j::width] for j in range(width)]
    out: list[list[str]] = [[] for _ in range(width)]
    for cells in split_lines(lines, delimiter, quoted):
        if len(cells) < width:
            cells = [*cells, *[""] * (width - len(cells))]
        for j in range(width):
            out[j].append(cells[j])
    return out


class CsvSource(BlockSource):
    format, label = "csv", "CSV / TXT"

    def __init__(self, path: str | Path, encoding: str | None = None, delimiter: str | None = None) -> None:
        from bf_core.formats import text
        from bf_core.formats.numbers import BAD

        super().__init__()
        self.path = Path(path)
        size = self.path.stat().st_size
        if size == 0:
            raise FormatError("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
        if encoding is not None and encoding not in text.ENCODINGS:
            raise FormatError("unknown_encoding", f"Неизвестная кодировка: {encoding}", "Выберите кодировку из списка.")
        if delimiter is not None and delimiter not in text.DELIMITERS:
            raise FormatError("unknown_delimiter", f"Неизвестный разделитель: {delimiter!r}",
                              "Допустимы «;», «,», табуляция, «|» и пробелы.")  # fmt: skip
        self.encoding = encoding or text.sniff_encoding(self.path, limit=16 << 20)
        head = text.head_lines(self.path, self.encoding, SAMPLE_LINES + 3)
        if not head:
            raise FormatError("empty_file", "В файле нет строк с данными.", "Проверьте, что файл скопирован полностью.")
        self.delimiter = delimiter or text.detect_delimiter(head[:200])
        cells = [text.split_lines([line], self.delimiter, '"' in line)[0] for line in head[:3]]
        strip = [[c.strip() for c in row] for row in cells]
        names = strip[0]
        if all(_is_number(c) for c in names if c):
            raise FormatError("no_header", "В первой строке файла нет названий столбцов.",
                              "Блочный CSV начинается со строки названий; затем по желанию идут строка типов и "
                              "строка сетки Micromine.")  # fmt: skip
        self.names = unique_names([n or f"Поле {j + 1}" for j, n in enumerate(names)])
        skip = 1
        second = strip[1] if len(strip) > 1 else []
        filled = [t for t in second if t]
        if filled and len(filled) * 2 >= len(names) and all(t.upper() in CSV_TYPES for t in filled):
            from bf_core.formats.blocks_roles import micromine_grid_line

            self.format, self.label = "csv_micromine", "CSV Micromine"
            self.types = [t.upper() for t in second] + [""] * (len(names) - len(second))
            skip = 2
            self.hint = micromine_grid_line(strip[2]) if len(strip) > 2 else None
            skip += int(self.hint is not None)
        self._skip = skip
        sample = head[skip : skip + SAMPLE_LINES]
        width = len(self.names)
        quoted = any('"' in line for line in sample)
        columns = _columns(sample, self.delimiter, width, quoted) if sample else [[] for _ in range(width)]
        for j, name in enumerate(self.names):
            forced = self.types[j] if self.types else ""
            as_text = forced in CSV_TEXT_TYPES
            if not as_text and not forced and columns[j]:
                _, kinds = _classify(columns[j])
                as_text = bool((kinds == BAD).any())
            self.kinds[name] = TEXT if as_text else NUMBER
        per_line = max(1, sum(len(x) for x in sample) // max(len(sample), 1) + 1) if sample else 0
        self.rows = None
        self._estimate = size // per_line if per_line else 0
        self._size = size

    @property
    def rows_estimate(self) -> int:
        return int(self.rows if self.rows is not None else self._estimate)

    def chunks(
        self, want: Sequence[str] | None = None, rows: int = CHUNK_ROWS, tick: Tick | None = None,
        limit: int | None = None,
    ) -> Iterator[BlockChunk]:
        from bf_core.formats import numbers as nm
        from bf_core.formats import text

        width = len(self.names)
        wanted = set(self.names if want is None else want)
        picks = [j for j, n in enumerate(self.names) if n in wanted]
        counts = _Counts()
        pending: list[str] = []
        quoted = False
        skip = self._skip
        done_rows = 0
        bad: dict[str, tuple[int, str]] = {}
        below: dict[str, int] = {}
        above: dict[str, int] = {}
        if self._size >= 2 * text.BLOCK:
            nm.load_kernel()

        def make(lines: list[str], is_quoted: bool) -> BlockChunk:
            cols = _columns(lines, self.delimiter, width, is_quoted)
            chunk = BlockChunk(done_rows, len(lines))
            for j in picks:
                name = self.names[j]
                if self.kinds[name] == TEXT:
                    import pyarrow as pa

                    chunk.texts[name] = _text_of(pa.array([c.strip() for c in cols[j]], type=pa.string()))
                    continue
                values, kinds = _classify(cols[j])
                chunk.numbers[name] = values
                k = np.bincount(kinds, minlength=8)
                if k[nm.BAD]:
                    first = int(np.flatnonzero(kinds == nm.BAD)[0])
                    n0, ex = bad.get(name, (0, cols[j][first].strip()))
                    bad[name] = (n0 + int(k[nm.BAD]), ex)
                below[name] = below.get(name, 0) + int(k[nm.BELOW])
                above[name] = above.get(name, 0) + int(k[nm.ABOVE])
            return chunk

        finished = False
        for lines, has_quote, at in text.line_blocks(self.path, self.encoding or "utf-8", counts):  # type: ignore[arg-type]
            if skip:  # the names, the types and the grid are not blocks
                drop = min(skip, len(lines))
                lines, skip = lines[drop:], skip - drop
            pending += lines
            quoted = quoted or has_quote
            while pending and (len(pending) >= rows or (limit is not None and done_rows + len(pending) >= limit)):
                room = rows if limit is None else min(rows, limit - done_rows)
                take, pending = pending[:room], pending[room:]
                if take:
                    yield make(take, quoted)
                    done_rows += len(take)
                if limit is not None and done_rows >= limit:
                    finished = True
                    break
            if tick is not None:
                tick(min(at / self._size, 1.0), f"Чтение строк: {_n(done_rows)}")
            if finished:
                break
        if pending and not finished:
            take = pending if limit is None else pending[: max(0, limit - done_rows)]
            if take:
                yield make(take, quoted)
                done_rows += len(take)
        if limit is None:
            self.rows = done_rows
        if not done_rows and limit is None:
            raise FormatError("empty_file", "В файле нет строк с данными.", "Проверьте, что файл скопирован полностью.")
        if counts.replaced:
            self.notes.append(f"{_n(counts.replaced)} символов не читаются в кодировке {self.encoding}: проверьте "
                              "выбор кодировки.")  # fmt: skip
        if counts.nul:
            self.notes.append(f"В файле {_n(counts.nul)} нулевых символов: похоже, это не текстовая таблица.")
        for name, (count, example) in bad.items():
            self.notes.append(f"Поле «{name}»: {_n(count)} знач. не являются числами (например «{example}») — "
                              "прочитаны как пустые.")  # fmt: skip
        for name, count in below.items():
            if count:
                self.notes.append(f"{name}: {_n(count)} знач. вида «<x» заменены на x/2.")
        for name, count in above.items():
            if count:
                self.notes.append(f"{name}: {_n(count)} знач. вида «>x» заменены на x.")
        if tick is not None:
            tick(1.0, f"Прочитано строк: {_n(done_rows)}")


def _classify(cells: list[str]) -> tuple[np.ndarray, np.ndarray]:
    from bf_core.formats import numbers as nm

    values, kinds = nm.classify(cells, plain=True)
    # a marker («-», «нет», «NA») is empty; a number written like an identifier (007) is a number here
    kinds = np.where(kinds == nm.MARKER, nm.EMPTY, kinds).astype(np.uint8)
    return values, kinds


def _is_number(cell: str) -> bool:
    from bf_core.formats import numbers as nm

    value, _ = nm.parse_value(cell)
    return value is not None


# --- opening ---------------------------------------------------------------------------------------------------


def peek_names(path: str | Path, encoding: str | None = None) -> tuple[str, list[str]] | None:
    """("dat" | "csv", the names of the columns) of a table file from its start, None — not a table. Cheap: the
    wizard and the import look here to tell a block file from a table of points."""
    from bf_core.formats import registry, text

    path = Path(path)
    try:
        kind = registry.detect(path)
        if kind == registry.DAT:
            from bf_core.formats.micromine_header import read_header

            h = read_header(path, encoding)
            return "dat", [h.names[i] for i, f in enumerate(h.fields) if f.kind in ("C", "N", "R", "F", "L", "S", "B")]
        if kind == registry.CSV:
            enc = encoding or text.sniff_encoding(path, limit=1 << 20)
            first = text.head_lines(path, enc, 1)
            if not first:
                return None
            delimiter = text.detect_delimiter(text.head_lines(path, enc, 50))
            row = next(csv.reader([first[0]], delimiter=text.DELIMITERS[delimiter] or " "))
            return "csv", unique_names([c.strip() for c in row])
    except (FormatError, OSError, csv.Error, StopIteration):
        return None
    return None


def open_block_file(
    path: str | Path, *, encoding: str | None = None, delimiter: str | None = None
) -> DatSource | CsvSource:
    """The source of a DAT or CSV file (`FormatError` — not a readable table)."""
    from bf_core.formats import registry

    path = Path(path)
    kind = registry.detect(path)
    if kind == registry.DAT:
        return DatSource(path, encoding)
    if kind == registry.CSV:
        return CsvSource(path, encoding, delimiter)
    raise FormatError("unsupported", f"Блочная модель читается из Micromine DAT, CSV или Datamine: «{path.name}» не "
                      "подходит.", "Сохраните модель в DAT или CSV.")  # fmt: skip
