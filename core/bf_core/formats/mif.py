"""MapInfo Interchange Format (`.mif` + `.mid`) → layers of lines, points and text (ТЗ 7.35 Д-13; ADR 0009, 0011 §2).

`read_mif(path, progress)` → `MifRead` (a `str_layers.StrRead` with `crs`). A MIF file mixes kinds of objects, a
`lineset.LineSet` holds lines or points, so a file gives up to three layers, named after the file (with
« (линии)», « (точки)», « (текст)» when there is more than one):
  * lines: `Line`, `Pline` (every section of `Pline Multiple` is a line), `Region` (every polygon is a closed
    line with the attribute «кольцо» — its number within the object, from 1; the first vertex is repeated at
    the end when the file does not repeat it), `Rect` (a closed line of four corners);
  * points: `Point`, `Multipoint` (every point gets the attributes of the object);
  * text: `Text` — a point at the lower left corner of the rectangle of the file with the attributes `text`
    (`\\n` of MapInfo is a line break), `height` (the height of the rectangle per line of text, in map units)
    and `rotation` (`Angle`, degrees counter-clockwise; 0 when the clause is absent).
  MIF has no Z: it is NaN, never 0. `None` objects, `Arc`, `Ellipse` and `Roundrect` are skipped and reported;
  the parts of a `Collection` share its attributes. Styles (`Pen`, `Brush`, `Symbol`, `Font`, …) are not read.
  * Attributes — the rows of the `.mid` in the order of the objects: `Char`, `Date` (`YYYY-MM-DD`), `DateTime`,
    `Time` are text (empty — null), `Integer`, `SmallInt`, `Float`, `Decimal` are numbers (empty or not a number
    — NaN, reported), `Logical` is 1 / 0 / NaN; a `LargeInt` or a whole `Decimal` column with a value of 2**53
    or more is text (identifiers a float64 does not keep).
    No `.mid` — a warning; a `.mid` with another number of rows than objects — an error (rows cannot be matched).
  * Code page: `encoding`, else a UTF-8 BOM, else `Charset` of the header (`WindowsCyrillic` → cp1251, …), else
    Windows-1251 with a warning. `Delimiter` of the header (TAB by default) separates the fields of the `.mid`.
  * `CoordSys` → `crs` through `mif_crs.parse_coordsys`; an unknown one is a warning, not an error.
`seq` numbers the vertices of all layers in the order of the file. `header` is a `MifHeader`. The file is read
in blocks; a count in the file is never used to allocate. Error codes: empty_file, not_mif, unsupported,
too_large, truncated, broken, no_lines, unknown_encoding.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.formats.dbf import unique_name
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet, close, detect_closed
from bf_core.formats.mif_objects import MifObjects
from bf_core.formats.shp import sidecar
from bf_core.formats.str_layers import StrRead, n_text, ranges, scaled
from bf_core.formats.table import Progress, no_progress

if TYPE_CHECKING:
    from bf_core.crs import CrsInfo

DEFAULT_ENCODING = "cp1251"
CHUNK_BYTES = 4 << 20
HEADER_BYTES = 1 << 20  # the header (up to the Data line) is within the first megabyte
MAX_LINE = 1 << 20
MAX_COLUMNS = 4096
MAX_DIGITS = 15  # a whole number of this many digits is always exact in a float64
EXACT = 2.0**53  # from here on a float64 does not keep every whole number
RING, TEXT, HEIGHT, ROTATION = "кольцо", "text", "height", "rotation"
#: `Charset` of MapInfo → Python codec («Neutral» says nothing about the code page)
CHARSETS = {
    "windowslatin1": "cp1252",
    "windowslatin2": "cp1250",
    "windowscyrillic": "cp1251",
    "windowsgreek": "cp1253",
    "windowsturkish": "cp1254",
    "windowshebrew": "cp1255",
    "windowsarabic": "cp1256",
    "windowsbalticrim": "cp1257",
    "windowsvietnamese": "cp1258",
    "windowsthai": "cp874",
    "windowsjapanese": "cp932",
    "windowssimpchinese": "cp936",
    "windowskorean": "cp949",
    "windowstradchinese": "cp950",
    "maccyrillic": "mac_cyrillic",
    "macroman": "mac_roman",
    "utf-8": "utf-8",
    "utf8": "utf-8",
}
_CODEPAGE = re.compile(r"codepage(\d{3,4})")
_ISO = re.compile(r"iso[_-]?8859[_-]?(\d{1,2})")
_COLUMN = re.compile(r"(\S+)\s+([A-Za-z]+)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?")
_NUMBERS = ("integer", "smallint", "float", "decimal")
_HINT = "Сохраните таблицу в MapInfo заново (Таблица → Экспорт → MapInfo Interchange) и загрузите оба файла."


@dataclass(frozen=True)
class MifColumn:
    name: str
    kind: str  # char, integer, smallint, largeint, decimal, float, date, logical, datetime, time
    width: int = 0
    decimals: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.kind, "width": self.width, "decimals": self.decimals}


@dataclass
class MifHeader:
    version: str = ""
    charset: str = ""
    delimiter: str = "\t"
    coordsys: str | None = None  # the clause as the file has it
    columns: list[MifColumn] = field(default_factory=list)


@dataclass
class MifRead(StrRead):
    """`records` — the objects of the Data section; `crs` — from `CoordSys`, None when it is not recognised."""

    crs: CrsInfo | None = None


def _fail(code: str, message: str, hint: str = _HINT) -> FormatError:
    return FormatError(code, message, hint)


def charset_codec(name: str) -> str | None:
    """The Python codec of a MapInfo `Charset`; None — unknown or «Neutral»."""
    key = name.strip().lower()
    found = CHARSETS.get(key)
    if found is None and (m := _CODEPAGE.fullmatch(key)):
        found = f"cp{m.group(1)}"
    if found is None and (m := _ISO.fullmatch(key)):
        found = f"iso8859_{m.group(1)}"
    try:
        return codecs.lookup(found).name if found else None
    except LookupError:
        return None


def _unquote(text: str) -> str:
    a, b = text.find('"'), text.rfind('"')
    return text[a + 1 : b] if 0 <= a < b else text.strip()


def _header(head: bytes, whole: bool) -> tuple[MifHeader, list[str], int, int]:
    """(header, column lines as Latin-1, offset of the first byte after the Data line, lines of the header)."""
    text = head.decode("latin-1")
    newline = "\r" if "\n" not in text and "\r" in text else "\n"
    header = MifHeader()
    column_lines: list[str] = []
    pos = line_no = columns_left = 0
    in_coordsys = False
    while pos < len(text):
        end = text.find(newline, pos)
        if end < 0:
            if not whole:
                break
            end = len(text)
        raw, pos = text[pos:end].strip(), end + 1
        line_no += 1
        if not raw:
            continue
        words = raw.split(None, 1)
        key, rest = words[0].lower(), words[1].strip() if len(words) > 1 else ""
        if columns_left:
            if key == "data" and not rest:
                raise _fail(
                    "broken", f"В заголовке MIF описано меньше полей, чем указано в Columns (строка {line_no})."
                )
            column_lines.append(raw)
            columns_left -= 1
        elif key == "data" and not rest:
            return header, column_lines, min(pos, len(text)), line_no
        elif key == "version":
            header.version, in_coordsys = rest, False
        elif key == "charset":
            header.charset, in_coordsys = _unquote(rest), False
        elif key == "delimiter":
            header.delimiter, in_coordsys = {"": "\t", "\\t": "\t"}.get(_unquote(rest), _unquote(rest)), False
            if len(header.delimiter) != 1:
                raise _fail("broken", f"Неверный разделитель полей в заголовке MIF: «{header.delimiter[:10]}».")
        elif key == "coordsys":
            header.coordsys, in_coordsys = raw, True
        elif key == "columns":
            in_coordsys = False
            if not rest.isdigit() or int(rest) > MAX_COLUMNS:
                raise _fail(
                    "too_large" if rest.isdigit() else "broken", f"Неверное число полей в заголовке MIF: «{rest}»."
                )
            columns_left = int(rest)
        elif key in ("unique", "index", "transform"):
            in_coordsys = False
        elif in_coordsys and header.coordsys is not None:
            header.coordsys += " " + raw  # the clause goes on (Bounds on its own line)
    raise _fail(
        "not_mif",
        "Это не файл MapInfo MIF: в нём нет раздела Data.",
        "Выберите файл .mif; таблицу MapInfo .tab сохраните как MIF/MID (Таблица → Экспорт).",
    )


def _columns(lines: list[str], codec: str, warnings: list[str]) -> list[MifColumn]:
    columns: list[MifColumn] = []
    for raw in lines:
        text = raw.encode("latin-1").decode(codec, "replace")
        m = _COLUMN.fullmatch(text.strip())
        if m is None:
            warnings.append(
                f"Описание поля «{text.strip()[:40]}» в заголовке MIF не разобрано: поле прочитано как текст"
            )
            columns.append(MifColumn(text.split()[0], "char"))
            continue
        columns.append(MifColumn(m.group(1), m.group(2).lower(), int(m.group(3) or 0), int(m.group(4) or 0)))
    return columns


# ---------------------------------------------------------------- .mid


class _Utf8:
    """A file re-encoded to UTF-8 while pyarrow reads it; bytes the code page does not have become U+FFFD."""

    def __init__(self, raw: Any, codec: str) -> None:
        self.raw = raw
        self.decoder = codecs.getincrementaldecoder("utf-8-sig" if codec == "utf-8" else codec)("replace")
        self.buffer, self.done, self.closed, self.plain = b"", False, False, True

    def read(self, size: int = -1) -> bytes:
        while not self.done and (size < 0 or len(self.buffer) < size):
            data = self.raw.read(1 << 20)
            self.done = not data
            self.plain = self.plain and data.isascii()
            self.buffer += self.decoder.decode(data, final=self.done).encode("utf-8")
        cut = len(self.buffer) if size < 0 else size
        out, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return out


def _date(text: str | None) -> str | None:
    if text is not None and len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _number_column(values: Any, name: str, warnings: list[str]) -> np.ndarray:
    import pyarrow as pa
    import pyarrow.compute as pc

    trimmed = pc.utf8_trim_whitespace(values)
    try:
        out = pc.cast(trimmed, pa.float64()).to_numpy(zero_copy_only=False).astype(np.float64)
        bad = int((~np.isfinite(out)).sum()) - trimmed.null_count  # «nan», «inf» written as text
    except pa.ArrowInvalid:
        cells = trimmed.to_pylist()
        out = np.full(len(cells), np.nan)
        bad = 0
        for i, cell in enumerate(cells):
            if cell:
                try:
                    out[i] = float(cell)
                except ValueError:
                    bad += 1
    if bad:
        out[~np.isfinite(out)] = np.nan
        warnings.append(f"Поле «{name}»: {n_text(bad)} знач. не являются числами — прочитаны как пустые")
    return out


def _read_mid(
    path: Path, header: MifHeader, codec: str, objects: int, tick: Progress, warnings: list[str]
) -> tuple[list[Any], int, bool]:
    """Columns of the `.mid` (float64 arrays or pyarrow strings), its number of rows and «all ASCII»."""
    import pyarrow as pa
    import pyarrow.compute as pc
    from pyarrow import csv

    names = [f"c{i}" for i in range(len(header.columns))]
    wrong: list[Any] = []

    def invalid(row: Any) -> str:
        wrong.append(row)
        return "error"

    batches = []
    rows = 0
    plain = True
    try:
        with path.open("rb") as raw:
            source = _Utf8(raw, codec)
            reader = csv.open_csv(
                source,
                read_options=csv.ReadOptions(column_names=names, block_size=CHUNK_BYTES),
                parse_options=csv.ParseOptions(
                    delimiter=header.delimiter,
                    quote_char='"',
                    double_quote=True,
                    escape_char=False,
                    ignore_empty_lines=len(names) > 1,
                    invalid_row_handler=invalid,
                ),
                convert_options=csv.ConvertOptions(
                    column_types=dict.fromkeys(names, pa.string()), strings_can_be_null=True, null_values=[""]
                ),
            )
            for batch in reader:
                batches.append(batch)
                rows += batch.num_rows
                tick(min(rows / max(objects, 1), 1.0), f"Чтение атрибутов: {n_text(rows)}")
            plain = source.plain
    except pa.ArrowInvalid as exc:
        if path.stat().st_size == 0:
            return [], 0, True
        if wrong:
            got, text = wrong[0].actual_columns, str(wrong[0].text)[:40]
            message = f"Файл .mid: в строке «{text}» значений {got}, а полей в заголовке MIF — {len(names)}."
        else:
            message = "Файл .mid не читается как таблица с разделителем из заголовка MIF."
        raise _fail("broken", message) from exc
    table = pa.Table.from_batches(batches, schema=pa.schema([(n, pa.string()) for n in names]))
    if rows > objects and all(table.column(n).slice(objects).null_count == rows - objects for n in names):
        table, rows = table.slice(0, objects), objects  # empty lines at the end of the file
    columns: list[Any] = []
    for name, column in zip(names, header.columns, strict=True):
        values = table.column(name)
        wide = column.kind == "largeint" or (
            column.kind == "decimal" and not column.decimals and column.width > MAX_DIGITS
        )
        if column.kind in _NUMBERS or wide:
            notes: list[str] = []
            numbers = _number_column(values, column.name, notes)
            if wide and (np.abs(numbers) >= EXACT).any():
                columns.append(pc.utf8_trim_whitespace(values))  # identifiers a float64 does not keep: text
            else:
                columns.append(numbers)
                warnings += notes
        elif column.kind == "logical":
            cells = pc.utf8_lower(pc.utf8_trim_whitespace(values)).to_pylist()
            yes, no = ("t", "y", "1", "true"), ("f", "n", "0", "false")
            columns.append(np.array([1.0 if c in yes else 0.0 if c in no else np.nan for c in cells], dtype=np.float64))
        elif column.kind == "date":
            encoded = values.combine_chunks().dictionary_encode()
            labels = pa.array([_date(v) for v in encoded.dictionary.to_pylist()], type=pa.string())
            columns.append(labels.take(encoded.indices))
        else:
            columns.append(values)
    return columns, rows, plain


# ---------------------------------------------------------------- layers


def _attrs(header: MifHeader, columns: list[Any] | None, index: np.ndarray, extra: list[tuple[str, Any]]) -> Any:
    """Rows of the `.mid` of the objects `index` plus the columns of the reader itself."""
    import pyarrow as pa

    names: list[str] = []
    arrays: list[Any] = []
    taken: set[str] = set()
    if columns is not None:
        take = pa.array(index, type=pa.int64())
        for column, values in zip(header.columns, columns, strict=True):
            names.append(unique_name(column.name, taken))
            arrays.append(pa.array(values[index]) if isinstance(values, np.ndarray) else values.take(take))
    for name, values in extra:
        names.append(unique_name(name, taken))
        arrays.append(values)
    return pa.table(arrays, names=names) if arrays else None


def _points(name: str, x: list[float], y: list[float], seq: list[int], attrs: Any) -> LineSet:
    xyz = np.column_stack([x, y, np.full(len(x), np.nan)])
    return LineSet(name, xyz, None, None, np.asarray(seq, dtype=np.uint32), attrs)


def _layers(
    stem: str, p: MifObjects, header: MifHeader, columns: list[Any] | None, warnings: list[str]
) -> list[LineSet]:
    import pyarrow as pa

    kinds = [bool(p.counts), bool(p.px), bool(p.tx)]
    labels = ["", "", ""] if sum(kinds) <= 1 else [" (линии)", " (точки)", " (текст)"]
    layers: list[LineSet] = []
    if p.counts:
        xy = p.lines_xy()
        counts = np.asarray(p.counts, dtype=np.int64)
        offsets = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
        seq = ranges(np.asarray(p.line_seq, dtype=np.int64), counts)
        xyz = np.column_stack([xy, np.full(len(xy), np.nan)])
        flagged = np.asarray(p.line_closed, dtype=bool)
        xyz, offsets, closed, take = close(xyz, offsets, flagged)
        if take is not None:
            seq = seq[take]
        still_open = int(flagged.sum()) - int(closed.sum())
        if still_open:
            warnings.append(f"{n_text(still_open)} полигонов из менее чем трёх вершин оставлены незамкнутыми")
        closed = np.maximum(closed, detect_closed(xyz, offsets))  # a Pline that returns to its first vertex
        ring = np.asarray(p.line_ring, dtype=np.float64)
        extra = [(RING, pa.array(ring, type=pa.float64()))] if np.isfinite(ring).any() else []
        attrs = _attrs(header, columns, np.asarray(p.line_obj, dtype=np.int64), extra)
        layers.append(LineSet(stem + labels[0], xyz, offsets, closed, seq.astype(np.uint32), attrs))
    if p.px:
        attrs = _attrs(header, columns, np.asarray(p.p_obj, dtype=np.int64), [])
        layers.append(_points(stem + labels[1], p.px, p.py, p.p_seq, attrs))
    if p.tx:
        extra = [
            (TEXT, pa.array(p.t_text, type=pa.string())),
            (HEIGHT, pa.array(p.t_height, type=pa.float64())),
            (ROTATION, pa.array(p.t_rotation, type=pa.float64())),
        ]
        attrs = _attrs(header, columns, np.asarray(p.t_obj, dtype=np.int64), extra)
        layers.append(_points(stem + labels[2], p.tx, p.ty, p.t_seq, attrs))
    return layers


def read_mif(
    path: str | Path, progress: Progress | None = None, *, encoding: str | None = None, mid: str | Path | None = None
) -> MifRead:
    """Read a MIF file and its MID into layers (see the module docstring). `mid` — the attribute file when it
    is not next to the `.mif`; `encoding` overrides `Charset` of the header."""
    path = Path(path)
    tick = progress or no_progress
    tick(0.0, "Чтение MapInfo MIF")
    size = path.stat().st_size
    if size == 0:
        raise _fail("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    warnings: list[str] = []
    parser = MifObjects()
    plain = True
    with path.open("rb") as f:
        head = f.read(HEADER_BYTES)
        if head.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise _fail("unsupported", "Файл MIF в кодировке UTF-16 не поддерживается.", "Сохраните его в UTF-8.")
        bom = 3 if head.startswith(b"\xef\xbb\xbf") else 0
        if b"\x00" in head[:4096]:
            raise _fail(
                "not_mif",
                "Это не файл MapInfo MIF: файл двоичный.",
                "Выберите файл .mif; таблицу MapInfo .tab сохраните как MIF/MID (Таблица → Экспорт).",
            )
        header, column_lines, data_at, line_no = _header(head[bom:], len(head) < HEADER_BYTES)
        codec, declared = DEFAULT_ENCODING, False
        if encoding:
            try:
                codec, declared = codecs.lookup(encoding).name, True
            except LookupError as exc:
                hint = "Выберите кодировку из списка."
                raise _fail("unknown_encoding", f"Неизвестная кодировка «{encoding}».", hint) from exc
        elif bom:
            codec, declared = "utf-8", True
        elif header.charset and header.charset.lower() != "neutral":
            found = charset_codec(header.charset)
            if found is None:
                warnings.append(
                    f"Кодировка «{header.charset}» из заголовка MIF неизвестна: текст прочитан как Windows-1251"
                )
            else:
                codec, declared = found, True
        header.columns = _columns(column_lines, codec, warnings)
        cr_only = b"\n" not in head and b"\r" in head
        f.seek(bom + data_at)
        done, rest = bom + data_at, b""
        while True:
            chunk = f.read(CHUNK_BYTES)
            done += len(chunk)
            data = rest + chunk
            if not data:
                break
            if cr_only:
                data = data.replace(b"\r", b"\n")
            cut = data.rfind(b"\n") + 1 if chunk else len(data)
            rest, data = data[cut:], data[:cut]
            if len(rest) > MAX_LINE:
                raise _fail("too_large", f"Строка {n_text(line_no + 1)} файла .mif длиннее {n_text(MAX_LINE)} байт.")
            plain = plain and data.isascii()
            lines = data.decode(codec, "replace").replace("\r\n", "\n").split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            parser.feed(lines, line_no + 1)
            line_no += len(lines)
            tick(0.6 * done / size, f"Прочитано объектов: {n_text(parser.obj + 1)}")
            if not chunk:
                break
    parser.finish()
    objects = parser.obj + 1
    if objects == 0:
        raise _fail("no_lines", "В файле MIF нет ни одного объекта.", "Проверьте, что таблица не пуста.")
    if parser.nones:
        warnings.append(f"{n_text(parser.nones)} объектов без геометрии (None) пропущены")
    if parser.skipped:
        kinds = ", ".join(f"{name} — {n_text(count)}" for name, count in sorted(parser.skipped.items()))
        warnings.append(f"Объекты этих типов не поддерживаются и пропущены: {kinds}")

    columns: list[Any] | None = None
    mid_path = Path(mid) if mid else sidecar(path, ".mid")
    if header.columns and mid_path is None:
        warnings.append("Рядом с файлом нет таблицы .mid: атрибуты не загружены")
    elif header.columns and mid_path is not None:
        columns, rows, ascii_only = _read_mid(mid_path, header, codec, objects, scaled(tick, 0.6, 0.85), warnings)
        if rows != objects:
            raise _fail(
                "broken",
                f"Число строк в .mid ({n_text(rows)}) не равно числу объектов в .mif ({n_text(objects)}): "
                "атрибуты нельзя сопоставить с объектами.",
                _HINT + " Чтобы загрузить только геометрию, уберите файл .mid.",
            )
        plain = plain and ascii_only
    if not declared and not plain:
        warnings.append("Кодировка не указана (нет Charset в заголовке MIF): текст прочитан как Windows-1251")
    tick(0.85, "Сборка слоёв")
    layers = _layers(path.stem, parser, header, columns, warnings)
    if not layers:
        raise _fail(
            "no_lines",
            "В файле MIF нет линий, точек и текста, которые можно загрузить.",
            "Проверьте, что в таблице есть объекты: точки, линии, полилинии, полигоны или подписи.",
        )

    from bf_core.formats.mif_crs import parse_coordsys

    system = parse_coordsys(header.coordsys)
    if system.warning:
        warnings.append(system.warning)
    tick(1.0, f"Прочитано вершин: {n_text(sum(layer.point_count for layer in layers))}")
    return MifRead("mif", layers, header, codec, objects, warnings, system.crs)


__all__ = ["CHARSETS", "MifColumn", "MifHeader", "MifRead", "charset_codec", "read_mif"]
