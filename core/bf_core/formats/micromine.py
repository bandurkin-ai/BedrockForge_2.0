"""Native reader and writer of Micromine data files (.DAT; .STR is the same format) — ТЗ 9.2, 9.12.

`read_dat(path, progress, encoding=None)` → `table.TableRead`: the records are a `numpy.memmap`
with a structured dtype, converted a chunk at a time (memory is bounded by the result itself):
  * R, F, L, S, B → number columns (float64; a NaN in the file stays NaN);
  * N (a number written as text) → number column; blank = NaN; text that is not a number becomes
    NaN and is reported in `warnings` with a count and an example; `<x` / `>x` as in CSV;
  * C → text column, decoded by the code page of the header (BOCU-1 included), every distinct
    value once; blank = null.
`info.header` is the `DatHeader` (fields with types, widths, decimals, descriptions; title; raw XML):
pass it as `like=` to `write_dat` to write the table back without loss.
`read_header(path)` reads only the header. `write_dat` lives in `micromine_write.py`.

Ported from BF `formats/micromine.py` (`read_micromine`, `read_columns`). Differences: one reader
(columns, not a pandas frame of Python rows); progress and cancellation; an unknown field type is
skipped with a warning and an unknown code page is read as Windows-1251 with a warning (BF: error /
silent "?"); unreadable characters are counted; the CRLF after each record is checked; a file that
is not a multiple of the record length is an error (as BF's table reader).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from bf_core.formats import bocu1
from bf_core.formats.errors import FormatError
from bf_core.formats.micromine_header import (
    BINARY_TYPES,
    BOCU,
    FIELD_TYPES,
    MAGIC,
    DatHeader,
    Field,
    decode_text,
    parse_header,
    read_header,
)
from bf_core.formats.micromine_write import write_dat
from bf_core.formats.table import Column, Progress, ReadInfo, TableRead, no_progress

if TYPE_CHECKING:
    import numpy as np

__all__ = ["MAGIC", "DatHeader", "Field", "parse_header", "read_dat", "read_header", "write_dat"]

MAX_RECORD = 16 << 20
CHUNK_ROWS = 1 << 17
CHUNK_BYTES = 8 << 20
_HINT = "Откройте файл в Micromine и сохраните его заново или экспортируйте таблицу в CSV."


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def _numbers(text: np.ndarray) -> tuple[np.ndarray, int, int, list[int]]:
    """Fixed-width ASCII numbers (an S-dtype array) → float64, counts of `<x` and `>x`, rows that are not numbers."""
    import numpy as np

    blank = np.char.strip(text) == b""
    try:
        values = np.where(blank, b"nan", text).astype(np.float64)
        if not np.isfinite(values[~blank]).all():
            raise ValueError("inf or nan written as text")
        return values, 0, 0, []
    except ValueError:
        from bf_core.formats import numbers

        cells = [t.decode("latin-1") for t in text.tolist()]
        values, kinds = numbers.classify(cells)
        bad = np.flatnonzero(kinds == numbers.BAD).tolist()
        return values, int((kinds == numbers.BELOW).sum()), int((kinds == numbers.ABOVE).sum()), bad


def _text_chunk(raw: np.ndarray, encoding: str) -> tuple[Any, int, bool]:
    """A chunk of a C field (S-dtype) → pyarrow strings; cells with unreadable characters; any non-ASCII."""
    import numpy as np
    import pyarrow as pa

    uniques, inverse = np.unique(raw, return_inverse=True)
    labels: list[str | None] = []
    broken = np.zeros(len(uniques), dtype=bool)
    plain = True
    for i, u in enumerate(uniques.tolist()):
        label = decode_text(u, encoding)
        plain = plain and label.isascii()
        broken[i] = "\ufffd" in label
        labels.append(label or None)
    inverse = inverse.ravel()
    damaged = int(broken[inverse].sum()) if broken.any() else 0
    return pa.array(labels, type=pa.string()).take(pa.array(inverse, type=pa.int64())), damaged, plain


def read_dat(
    path: str | Path,
    progress: Progress | None = None,
    *,
    encoding: str | None = None,
    chunk_rows: int | None = None,
    max_rows: int | None = None,
) -> TableRead:
    """Read a Micromine DAT/STR file. `encoding` overrides the code page of the header (a Python codec
    name or "bocu-1"); `max_rows` reads only the first records (previews); `chunk_rows` — records
    converted at a time (by default about 8 МБ of the file, so that progress is reported often)."""
    import numpy as np
    import pyarrow as pa

    path = Path(path)
    tick = progress or no_progress
    tick(0.0, "Чтение заголовка Micromine")
    header = read_header(path, encoding)
    warnings = list(header.warnings)
    record = header.record_size
    if record > MAX_RECORD:
        raise FormatError("too_large", f"Длина записи {_n(record)} байт больше допустимой {_n(MAX_RECORD)}.", _HINT)
    body = path.stat().st_size - header.data_offset
    rows, rest = divmod(body, record)
    if body < 0 or rest:
        raise FormatError(
            "truncated",
            f"Файл обрезан или повреждён: длина данных {_n(max(body, 0))} байт не кратна длине записи {record}.",
            "Скопируйте файл заново; если он такой же — сохраните его заново в Micromine.",
        )
    if max_rows is not None:
        rows = min(rows, max(0, max_rows))
    step = max(1, chunk_rows) if chunk_rows else max(1024, min(CHUNK_ROWS, CHUNK_BYTES // record))
    names = header.names
    dtype = np.dtype(
        {
            "names": [f"f{i}" for i in range(len(header.fields))] + ["eol"],
            "formats": [BINARY_TYPES.get(f.kind, f"S{f.width}") for f in header.fields] + ["S2"],
        }
    )
    used = [i for i, f in enumerate(header.fields) if f.kind in FIELD_TYPES]
    for i, f in enumerate(header.fields):
        if f.kind not in FIELD_TYPES:
            warnings.append(f"Поле «{names[i]}» неизвестного типа «{f.kind}» (ширина {f.width}) пропущено")
    numeric = {i: np.empty(rows, dtype=np.float64) for i in used if header.fields[i].kind != "C"}
    texts: dict[int, list[Any]] = {i: [] for i in used if header.fields[i].kind == "C"}
    below = dict.fromkeys(numeric, 0)
    above = dict.fromkeys(numeric, 0)
    bad: dict[int, tuple[int, str]] = {}
    damaged = dict.fromkeys(texts, 0)
    non_ascii = False
    bad_eol = 0

    records: Any = np.memmap(path, dtype=dtype, mode="r", offset=header.data_offset, shape=(rows,)) if rows else None
    try:
        for a in range(0, rows, step):
            b = min(rows, a + step)
            tick(a / rows, f"Чтение записей: {_n(a)} из {_n(rows)}")
            block = np.array(records[a:b])  # one chunk of records in memory
            wrong = block["eol"] != b"\r\n"
            if wrong.any():
                if a == 0 and wrong[0]:
                    raise FormatError(
                        "broken",
                        "Первая запись не заканчивается CRLF: структура файла не совпадает с заголовком.",
                        _HINT,
                    )
                bad_eol += int(wrong.sum())
            for i in used:
                f = header.fields[i]
                column = block[f"f{i}"]
                if f.kind in BINARY_TYPES:
                    numeric[i][a:b] = column
                elif f.kind == "N":
                    raw = np.ascontiguousarray(column)
                    if header.encoding == BOCU:
                        codes = bocu1.number_bytes(raw.view(np.uint8).reshape(b - a, f.width))
                        raw = np.ascontiguousarray(codes).view(f"S{f.width}").ravel()
                    numeric[i][a:b], lo, hi, wrong_rows = _numbers(raw)
                    below[i] += lo
                    above[i] += hi
                    if wrong_rows:
                        count, example = bad.get(i, (0, raw[wrong_rows[0]].decode("latin-1").strip()))
                        bad[i] = (count + len(wrong_rows), example)
                else:
                    chunk, broken, plain = _text_chunk(np.ascontiguousarray(column), header.encoding)
                    texts[i].append(chunk)
                    damaged[i] += broken
                    non_ascii = non_ascii or not plain
    finally:
        if records is not None:  # close the map now: on Windows an open map keeps the file locked
            mm = getattr(records, "_mmap", None)
            del records
            if mm is not None:
                mm.close()

    columns: list[Column] = []
    for i in used:
        f = header.fields[i]
        if i in numeric:
            columns.append(Column(names[i], "number", numeric[i], below[i], above[i], source=f.kind))
            if i in bad:
                count, example = bad[i]
                what = f"{_n(count)} знач. не являются числами (например «{example}»)"
                warnings.append(f"Поле «{names[i]}»: {what} — прочитаны как пустые")
            if below[i]:
                warnings.append(f"{names[i]}: {below[i]} знач. вида «<x» (ниже предела обнаружения) заменены на x/2")
            if above[i]:
                warnings.append(f"{names[i]}: {above[i]} знач. вида «>x» заменены на x")
        else:
            columns.append(Column(names[i], "text", pa.chunked_array(texts[i], type=pa.string()), source="C"))
            if damaged[i]:
                warnings.append(
                    f"Поле «{names[i]}»: {_n(damaged[i])} знач. содержат символы, которые не читаются "
                    f"в кодировке {header.encoding}"
                )
    if non_ascii and not header.declared and not header.codepage and header.encoding != BOCU:
        warnings.append("В заголовке не указана кодовая страница: текст прочитан как Windows-1251")
    if bad_eol:
        warnings.append(f"{_n(bad_eol)} записей не заканчиваются CRLF: возможно, файл повреждён")
    tick(1.0, f"Прочитано записей: {_n(rows)}")
    info = ReadInfo("dat", encoding=header.encoding, header=header)
    return TableRead(columns, rows, info, warnings)
