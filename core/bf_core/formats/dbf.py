"""dBASE tables of a shapefile (`.dbf`) as columns (ТЗ 7.35 Д-13; ADR 0011 §2: «быстрый путь numpy для колонок DBF»).

`read_dbf(path, codec, progress, warnings)` → `DbfTable`: the fields as the file has them and the columns read:
  * N and F — float64; blank, `***` (the null of QGIS) and text that is not a number are NaN, never 0;
    a field of whole numbers with a value of 2**53 or more is text: a float64 does not keep an identifier
    that long (QGIS and GDAL write every 64-bit integer field 18 digits wide, so the width alone says nothing);
  * C — pyarrow strings without the padding, blank is null; D — text `YYYY-MM-DD` (blank and zeros are null,
    anything that is not a date stays as written); L — 1 (T, Y), 0 (F, N) or NaN;
  * other field types (M, B, G, I, …) are left out with a warning;
  * `deleted` — records flagged as deleted: the reader of the shapefile skips their objects.
Records have a fixed length, so they are read as a numpy table a chunk at a time (1 млн records × 3 fields in
about 0,5 s; pyshp builds a Python list per record: 2,6 s). The size of the file is checked against the header
before anything is allocated. `codec_of` turns the text of a `.cpg` into a Python codec, `language_codec` — the
language driver byte of the header (Cyrillic code pages only). Error codes: truncated, broken.
"""

from __future__ import annotations

import codecs
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.str_layers import n_text, scaled
from bf_core.formats.table import Progress

CHUNK_ROWS = 1 << 17
CHUNK_BYTES = 8 << 20
MAX_DIGITS = 15  # a whole number of this many digits is always exact in a float64
EXACT = 2.0**53  # from here on a float64 does not keep every whole number
#: language driver of a .dbf → code page; only the Cyrillic ones: «ANSI» (0x57) is written for any text
_LDID = {0x26: "cp866", 0x65: "cp866", 0xC9: "cp1251"}
_HEAD = struct.Struct("<B3xIHH17xB2x")  # version, records, header length, record length, language driver
_HINT = "Скопируйте заново все файлы набора (.shp, .shx, .dbf, .prj, .cpg) или сохраните слой заново в ГИС."
_CPG = re.compile(r"(?:ansi|oem|cp|windows|win|ibm|ms)?[\s_-]*(\d{3,5})")
_ISO = re.compile(r"iso[\s_-]*8859[\s_-]*(\d{1,2})")
_HOW = {"C": "text", "D": "date", "L": "logical", "N": "number", "F": "number"}
_TRUE = np.frombuffer(b"TtYy", dtype=np.uint8)
_FALSE = np.frombuffer(b"FfNn", dtype=np.uint8)


@dataclass(frozen=True)
class DbfField:
    name: str
    kind: str  # C, N, F, D, L, … as in the file
    width: int
    decimals: int
    offset: int = 0  # of the field within a record

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.kind, "width": self.width, "decimals": self.decimals}


@dataclass
class DbfTable:
    fields: list[DbfField]  # every field of the file, in its order
    names: list[str]  # of the columns read (unique without regard to case)
    columns: list[Any]  # float64 arrays or pyarrow ChunkedArrays of strings
    deleted: np.ndarray  # bool per record
    rows: int
    plain: bool  # every name and every text is ASCII: the code page does not matter


@dataclass(frozen=True)
class DbfLayout:
    rows: int
    header_size: int
    record_size: int
    language: int  # the language driver byte
    fields: list[tuple[bytes, str, int, int, int]]  # name as bytes, type, width, decimals, offset


def _fail(code: str, message: str) -> FormatError:
    return FormatError(code, message, _HINT)


def codec_of(label: str) -> str | None:
    """The Python codec a `.cpg` names («UTF-8», «1251», «ANSI 1251», «OEM 866», «ISO 88595»); None — unknown."""
    text = label.strip().strip("\x00").lower()
    if not text:
        return None
    name = text
    if m := _ISO.fullmatch(text):
        name = f"iso8859_{int(m.group(1))}"
    elif m := _CPG.fullmatch(text):
        number = int(m.group(1))
        if number == 65001:
            name = "utf-8"
        elif 88591 <= number <= 88599:  # ESRI writes ISO 8859-5 as «88595»
            name = f"iso8859_{number - 88590}"
        else:
            name = f"cp{number}"
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None


def language_codec(path: Path) -> str | None:
    """The code page the header of a `.dbf` declares, when it is a Cyrillic one; None otherwise."""
    return _LDID.get(layout(path).language)


def unique_name(name: str, taken: set[str]) -> str:
    """A column name not used yet (names differ without regard to case, as LineSet wants)."""
    base = result = " ".join(name.split()) or "FIELD"
    k = 1
    while result.casefold() in taken:
        k += 1
        result = f"{base}_{k}"
    taken.add(result.casefold())
    return result


def layout(path: Path) -> DbfLayout:
    """The header of a `.dbf`, checked against the size of the file."""
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(32)
        if len(head) < 32:
            raise _fail("truncated", "Файл .dbf обрезан: в нём нет заголовка.")
        _version, rows, header_size, record_size, language = _HEAD.unpack(head)
        if header_size < 33 or record_size < 1:
            raise _fail("broken", "Файл .dbf повреждён: неверная длина заголовка или записи.")
        body = f.read(header_size - 32)
    if len(body) < header_size - 32:
        raise _fail("truncated", "Файл .dbf обрезан: заголовок короче, чем в нём указано.")
    if size < header_size + rows * record_size:
        have = max(0, size - header_size) // record_size
        raise _fail(
            "truncated",
            f"Файл .dbf обрезан: в заголовке {n_text(rows)} записей, в файле помещается {n_text(have)}.",
        )
    fields: list[tuple[bytes, str, int, int, int]] = []
    offset = 1  # the deletion flag
    for a in range(0, len(body) - 31, 32):
        if body[a] == 0x0D:
            break
        raw_name = body[a : a + 11].split(b"\x00", 1)[0]
        kind = chr(body[a + 11]).upper()
        width, decimals = body[a + 16], body[a + 17]
        if offset + width > record_size:
            raise _fail("broken", "Файл .dbf повреждён: поля не помещаются в длину записи.")
        fields.append((raw_name, kind, width, decimals if kind in "NF" else 0, offset))
        offset += width
    return DbfLayout(rows, header_size, record_size, language, fields)


def _numbers(raw: np.ndarray) -> tuple[np.ndarray, int]:
    """Fixed-width ASCII numbers → float64 (blank and `***` are NaN) and how many are not numbers."""
    text = np.strings.strip(raw)
    blank = (text == b"") | np.strings.startswith(text, b"*")
    try:
        values = np.where(blank, b"nan", text).astype(np.float64)
        if np.isfinite(values[~blank]).all():
            return values, 0
    except ValueError:
        pass
    values = np.full(len(text), np.nan)
    bad = 0
    for i in np.flatnonzero(~blank).tolist():
        cell = bytes(text[i]).split(b"\x00", 1)[0].strip()
        try:
            v = float(cell.replace(b",", b"."))
        except ValueError:
            v = np.nan
        if not np.isfinite(v):
            bad += bool(cell)
            v = np.nan
        values[i] = v
    return values, bad


def _date(text: str) -> str:
    ok = len(text) == 8 and text.isdigit() and 1 <= int(text[4:6]) <= 12 and 1 <= int(text[6:]) <= 31
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if ok else text


def _text(raw: np.ndarray, codec: str, date: bool) -> tuple[Any, int, bool]:
    """A chunk of a C or D field → pyarrow strings (blank — null); unreadable cells; all ASCII."""
    import pyarrow as pa

    uniques, inverse = np.unique(raw, return_inverse=True)
    labels: list[str | None] = []
    broken = np.zeros(len(uniques), dtype=bool)
    plain = True
    for i, u in enumerate(uniques.tolist()):
        label = u.split(b"\x00", 1)[0].strip(b" ").decode(codec, "replace")
        if date:
            label = "" if not label.strip(" 0") else _date(label)
        plain = plain and label.isascii()
        broken[i] = "�" in label
        labels.append(label or None)
    inverse = inverse.ravel()
    damaged = int(broken[inverse].sum()) if broken.any() else 0
    return pa.array(labels, type=pa.string()).take(pa.array(inverse, type=pa.int64())), damaged, plain


@dataclass
class _Pass:
    """What one pass over the records gives."""

    numbers: dict[int, np.ndarray]
    texts: dict[int, list[Any]]
    bad: dict[int, int]  # cells of a number field that are not numbers
    damaged: dict[int, int]  # cells of a text field with characters the code page does not have
    deleted: np.ndarray
    plain: bool = True
    long: set[int] = field(default_factory=set)  # whole-number fields with values a float64 does not keep


def _load(
    path: Path, form: DbfLayout, fields: list[DbfField], used: list[tuple[int, str, str]], codec: str, tick: Progress
) -> _Pass:
    """The columns `used` — (field, column name, how it is read) — of every record, a chunk at a time."""
    rows = form.rows
    dtype = np.dtype(
        {
            "names": ["del"] + [f"f{i}" for i, _, _ in used],
            "formats": ["S1"] + [f"S{fields[i].width}" for i, _, _ in used],
            "offsets": [0] + [fields[i].offset for i, _, _ in used],
            "itemsize": form.record_size,
        }
    )
    numbers = {i: np.empty(rows, dtype=np.float64) for i, _, how in used if how in ("number", "logical")}
    texts: dict[int, list[Any]] = {i: [] for i, _, how in used if how in ("text", "date")}
    out = _Pass(numbers, texts, dict.fromkeys(numbers, 0), dict.fromkeys(texts, 0), np.zeros(rows, dtype=bool))
    whole = {i for i, _, _ in used if fields[i].kind == "N" and not fields[i].decimals and fields[i].width > MAX_DIGITS}
    records: Any = np.memmap(path, dtype=dtype, mode="r", offset=form.header_size, shape=(rows,)) if rows else None
    step = max(1024, min(CHUNK_ROWS, CHUNK_BYTES // form.record_size))
    try:
        for a in range(0, rows, step):
            b = min(rows, a + step)
            tick(a / rows, f"Чтение атрибутов: {n_text(a)} из {n_text(rows)}")
            block = np.array(records[a:b])  # one chunk of records in memory
            out.deleted[a:b] = block["del"] == b"*"
            for i, _column_name, how in used:
                column = np.ascontiguousarray(block[f"f{i}"])
                if how == "number":
                    values, wrong = _numbers(column)
                    numbers[i][a:b] = values
                    out.bad[i] += wrong
                    if i in whole and i not in out.long and (np.abs(values) >= EXACT).any():
                        out.long.add(i)
                elif how == "logical":
                    code = column.view(np.uint8)
                    numbers[i][a:b] = np.where(np.isin(code, _TRUE), 1.0, np.where(np.isin(code, _FALSE), 0.0, np.nan))
                else:
                    chunk, broken, ascii_only = _text(column, codec, how == "date")
                    texts[i].append(chunk)
                    out.damaged[i] += broken
                    out.plain = out.plain and ascii_only
    finally:
        if records is not None:  # close the map now: on Windows an open map keeps the file locked
            mm = getattr(records, "_mmap", None)
            del records
            if mm is not None:
                mm.close()
    return out


def read_dbf(path: Path, codec: str, progress: Progress, warnings: list[str]) -> DbfTable:
    """Read every record of a `.dbf` (see the module docstring); `warnings` gets Russian texts for the log."""
    import pyarrow as pa

    form = layout(path)
    taken: set[str] = set()
    fields: list[DbfField] = []
    plain = True
    for raw_name, kind, width, decimals, offset in form.fields:
        label = raw_name.decode(codec, "replace").strip()
        plain = plain and label.isascii()
        fields.append(DbfField(label, kind, width, decimals, offset))
    used: list[tuple[int, str, str]] = []  # (field, column name, how it is read)
    for i, f in enumerate(fields):
        how = _HOW.get(f.kind)
        if how is None or f.width == 0:
            warnings.append(f"Поле «{f.name}» типа «{f.kind}» в .dbf не поддерживается: пропущено")
            continue
        used.append((i, unique_name(f.name, taken), how))
    read = _load(path, form, fields, used, codec, scaled(progress, 0.0, 0.9))
    if read.long:  # identifiers longer than a float64 keeps: these fields once more, as text
        again = [(i, column_name, "text") for i, column_name, _ in used if i in read.long]
        text = _load(path, form, fields, again, codec, scaled(progress, 0.9, 1.0))
        for i in read.long:
            del read.numbers[i]
            read.texts[i], read.damaged[i] = text.texts[i], text.damaged[i]
    columns: list[Any] = []
    for i, column_name, _how in used:
        if i in read.numbers:
            columns.append(read.numbers[i])
            if read.bad[i]:
                warnings.append(
                    f"Поле «{column_name}»: {n_text(read.bad[i])} знач. не являются числами — прочитаны как пустые"
                )
        else:
            columns.append(pa.chunked_array(read.texts[i], type=pa.string()))
            if read.damaged[i]:
                warnings.append(
                    f"Поле «{column_name}»: {n_text(read.damaged[i])} знач. содержат символы, которые не читаются "
                    f"в кодировке {codec}"
                )
    names = [column_name for _, column_name, _ in used]
    return DbfTable(fields, names, columns, read.deleted, form.rows, plain and read.plain)


__all__ = ["DbfField", "DbfLayout", "DbfTable", "codec_of", "language_codec", "layout", "read_dbf", "unique_name"]
