"""A streaming XLSX writer for the protocol of ore intervals (ТЗ 7.35 Д-26 «Результат» (4); ADR 0018 решение 10)
and the QAQC report (ТЗ 8.4.3; ADR 0020 §8, решение 15): `openpyxl` (without lxml) writes ~10 000 rows a second,
this writes the sheet XML directly into the zip (≈ 15 times as fast; the deflate runs in a worker thread while
the next rows are built — zlib frees the GIL).

    with Book(path) as book:
        pct = book.style(fmt=num_fmt(1, "percent"), base=HEAD)
        with book.sheet("Интервалы", widths=[12, 8, 8]) as sh:
            sh.row(["HOLE", "FROM", "TO"], style=HEAD)
            sh.row(["DH-1", 3.0, 11.0])
            sh.table([holes, froms, tos])      # columns (numpy arrays, lists): the cells of `row`, faster
            sh.merge(row0, row1, col)          # 0-based lines of the sheet, inclusive
            sh.image(png, row, col, scale=0.5) # a PNG at a cell (`xlsx_drawing`); returns the line below it

Values: None — an empty cell; str — a shared string; bool, int, float — a number (NaN, ±inf — empty); `date`,
`datetime`, `numpy.datetime64` — the serial day of Excel (from 1899-12-30; an aware `datetime` in UTC; NaT —
empty) in the cell's style with a date format added (DATE, or DATETIME for a time of day). Styles: `HEAD` (bold,
Montserrat, stone fill), `NOTE` (italic), `tone_style(tone)` — the fill of a class of the form И1, сл. 17 in the
brand colours (ADR 0006: those of `charts/column/classes.ts`), `Book.style(...)` — any of them with a number
format (`num_fmt`, DATE, DATETIME) or a fill; new styles go after the fixed ones, so a book that adds none (and
no picture) has the parts of Э12 byte for byte. Sheet names are made valid (≤ 31 UTF-16 units, no `[]:*?/\\`,
no «'» at the ends, not «History», unique; `"` and `'` escaped in the attribute). Rows carry `r`, cells do not
(positional, as the format allows): an empty value is `<c/>`. Every part of the zip has the date 1980-01-01, so
the same calls give the same bytes. The workbook is readable by Excel, LibreOffice and `openpyxl`.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import zipfile
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from itertools import repeat
from pathlib import Path
from types import TracebackType
from typing import IO, Any

import numpy as np

from . import xlsx_drawing as drw
from .xlsx_drawing import png_size

__all__ = [
    "DATE",
    "DATETIME",
    "HEAD",
    "MAX_ROWS",
    "NOTE",
    "Book",
    "Sheet",
    "num_fmt",
    "png_size",
    "sheet_name",
    "tone_style",
]

HEAD, NOTE = 1, 2
MAX_ROWS = 1_048_576  # lines of a sheet in Excel
DATE, DATETIME = "yyyy-mm-dd", "yyyy-mm-dd hh:mm:ss"
_TONE_IDS = drw.TONE_IDS
_EPOCH = dt.date(1899, 12, 30).toordinal()
_NP_EPOCH = np.datetime64("1899-12-30T00:00:00", "us")
_DAY_US = np.timedelta64(86_400_000_000, "us")
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")
_FORMULA = ("=", "+", "-", "@")
_END = "</v></c>"
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_Pre = tuple[str, str, str, str, int]  # the heads of a number, an empty cell, a shared string, a bool; the style


def tone_style(tone: str | None) -> int:
    """The style of a cell of the form with this tone (0 — none)."""
    return _TONE_IDS.get(tone or "", 0)


def num_fmt(decimals: int = 2, kind: str = "fixed") -> str:
    """A number format code: `fixed` — 0.00; `percent` — a fraction shown ×100 with «%»; `units` — a value
    already in per cent, «%» after it (no ×100); `sci` — 0.00E+00; `int` — 0."""
    d = "0" if decimals <= 0 else "0." + "0" * decimals
    codes = {"fixed": d, "percent": d + "%", "units": d + '"%"', "sci": d + "E+00", "int": "0"}
    if kind not in codes:
        raise ValueError(f"unknown kind of number format: {kind}")
    return codes[kind]


def _esc(s: str) -> str:
    s = _BAD_XML.sub("", s)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace('"', "&quot;").replace("'", "&apos;")  # also safe inside an attribute (a sheet name)


def _col(n: int) -> str:
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _cut(s: str, units: int) -> str:
    """The longest head of `s` of at most `units` UTF-16 code units (the length Excel counts)."""
    out, n = [], 0
    for ch in s:
        n += 2 if ord(ch) > 0xFFFF else 1
        if n > units:
            break
        out.append(ch)
    return "".join(out)


def _bare(s: str) -> str:
    return s.strip().strip("'").strip()


def sheet_name(name: str, taken: set[str]) -> str:
    """A valid, unique name of a sheet (the case does not matter to Excel): ≤ 31 UTF-16 units, cut first and
    stripped of «'» after (no «'» at either end), not the reserved «History»."""
    base = _bare(_cut(_bare(_BAD_SHEET.sub("_", name)), 31)) or "Лист"
    if base.casefold() == "history":
        base = "History_"
    out, k = base, 2
    while out.casefold() in taken:
        tail = f" ({k})"
        out, k = _bare(_cut(base, 31 - _u16(tail))) + tail, k + 1
    taken.add(out.casefold())
    return out


def _info(name: str, *, stored: bool = False) -> zipfile.ZipInfo:
    """A zip entry with the fixed date (1980-01-01) and the attributes `ZipFile.writestr` gives a name."""
    zi = zipfile.ZipInfo(name)
    zi.external_attr = 0o600 << 16
    zi.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    zi._compresslevel = None if stored else 3  # type: ignore[attr-defined]  # (`compress_level` from 3.13)
    return zi


def _serial(v: Any) -> tuple[float | int | None, bool]:
    """The serial day of a date (None — NaT) and whether it has a time of day."""
    if isinstance(v, np.datetime64):
        if np.isnat(v):
            return None, False
        x = (v.astype("datetime64[us]") - _NP_EPOCH) / _DAY_US
        return (int(x), False) if np.datetime_data(v.dtype)[0] in ("Y", "M", "W", "D") else (float(x), True)
    if isinstance(v, dt.datetime):
        if v != v:  # pandas NaT
            return None, False
        if v.tzinfo is not None:
            v = v.astimezone(dt.UTC).replace(tzinfo=None)
        sec = v.hour * 3600 + v.minute * 60 + v.second + v.microsecond / 1e6
        return v.toordinal() - _EPOCH + sec / 86_400, True
    return v.toordinal() - _EPOCH, False


class Sheet:
    """A sheet being written: rows go straight into the zip entry (deflated by the book's worker thread)."""

    def __init__(self, book: Book, index: int, widths: list[float] | None, freeze: int) -> None:
        self._book = book
        self.index = index
        self._fh: IO[bytes] = book._zip.open(_info(f"xl/worksheets/sheet{index}.xml"), "w", force_zip64=True)
        self._buf: list[str] = []
        self._size = 0
        self._job: Future[int] | None = None
        self.rows = 0
        self._merges: list[str] = []
        self._pics: list[drw.Picture] = []
        head = [f'{_XML}<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
        if freeze:
            head.append(
                f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="{freeze}" topLeftCell="A{freeze + 1}"'
                ' activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            )
        if widths:
            cols = "".join(
                f'<col min="{k + 1}" max="{k + 1}" width="{w:g}" customWidth="1"/>' for k, w in enumerate(widths)
            )
            head.append(f"<cols>{cols}</cols>")
        head.append("<sheetData>")
        self._write("".join(head))

    def _write(self, s: str) -> None:
        self._buf.append(s)
        self._size += len(s)
        if self._size > 1 << 20:
            self._flush()

    def _wait(self) -> None:
        if self._job is not None:
            job, self._job = self._job, None
            job.result()  # re-raises an error of the writing

    def _flush(self) -> None:
        if self._buf:
            data = "".join(self._buf).encode("utf-8")
            self._buf, self._size = [], 0
            self._wait()  # one chunk in flight: the memory stays bounded, the order kept
            self._job = self._book._pool.submit(self._fh.write, data)

    def _lines(self, n: int) -> int:
        if self.rows + n > MAX_ROWS:
            raise ValueError(f"a sheet holds at most {MAX_ROWS} lines")
        self.rows += n
        return self.rows

    def row(self, values: Sequence[Any], style: int | list[int] | None = None) -> None:
        """One row; `style` — of every cell, or per cell (0 — none)."""
        book = self._book
        if isinstance(style, list):
            pres: Any = book._row_pres(style)
            if len(pres) < len(values):
                raise IndexError("fewer cell styles than values in a row")
        else:
            pres = repeat(book._pre(style))
        strings, guard = book._strings, book._guard
        out = [f'<row r="{self._lines(1)}">']
        add = out.append
        for v, p in zip(values, pres, strict=False):  # `pres` may be endless
            t = type(v)
            if t is float:
                add(p[0] + repr(v) + _END if v - v == 0.0 else p[1])  # NaN, ±inf — empty
            elif t is str:
                if not v:
                    add(p[1])
                    continue
                if guard and v[0] in _FORMULA:
                    v = "'" + v
                i = strings.get(v)
                if i is None:
                    i = strings[v] = len(strings)
                add(p[2] + str(i) + _END)
            elif v is None:
                add(p[1])
            elif t is int:
                add(p[0] + str(v) + _END)
            elif t is dt.date:
                add(book._pre(book._dated(p[4], False))[0] + str(v.toordinal() - _EPOCH) + _END)
            else:
                add(self._cell(v, p))
        add("</row>")
        self._write("".join(out))

    def _cell(self, v: Any, p: _Pre) -> str:
        """A cell of any value — as `row` writes it."""
        if v is None:
            return p[1]
        if isinstance(v, str):
            return self._book._cell_str(str(v), p)
        if isinstance(v, bool | np.bool_):
            return f"{p[3]}{int(v)}{_END}"
        if isinstance(v, int | np.integer):
            return f"{p[0]}{int(v)}{_END}"
        if isinstance(v, dt.date | np.datetime64):
            x, timed = _serial(v)
            if x is None:
                return p[1]
            return f"{self._book._pre(self._book._dated(p[4], timed))[0]}{x!r}{_END}"
        x = float(v)
        return f"{p[0]}{x!r}{_END}" if math.isfinite(x) else p[1]

    def table(self, columns: Sequence[Any], style: int | list[int] | None = None, *, chunk: int = 65_536) -> None:
        """Rows from columns of one length (numpy arrays, lists): the cells `row` would write for each line,
        built a column at a time — numeric and date arrays at the speed of C."""
        n = len(columns[0]) if len(columns) else 0
        if any(len(c) != n for c in columns):
            raise ValueError("the columns of a table differ in length")
        styles = style if isinstance(style, list) else [style] * len(columns)
        if len(styles) < len(columns):
            raise IndexError("fewer cell styles than columns")
        pres = [self._book._pre(s) for s in styles[: len(columns)]]
        for a in range(0, n, chunk):
            b = min(n, a + chunk)
            r0 = self.rows + 1
            cells = [self._column(c[a:b], p) for c, p in zip(columns, pres, strict=True)]
            heads = map('<row r="{}">'.format, range(r0, self._lines(b - a) + 1))
            self._write("".join(map("".join, zip(heads, *cells, repeat("</row>"), strict=False))))

    def _column(self, col: Any, p: _Pre) -> list[str]:
        book = self._book
        kind = col.dtype.kind if isinstance(col, np.ndarray) and col.ndim == 1 else ""
        if kind == "f":
            x = col.astype(np.float64, copy=False)
            out = list(map((p[0] + "{!r}" + _END).format, x.tolist()))
            for i in np.flatnonzero(~np.isfinite(x)).tolist():
                out[i] = p[1]
            return out
        if kind in ("i", "u"):
            return list(map((p[0] + "{}" + _END).format, col.tolist()))
        if kind == "b":
            return list(map((p[3] + "{:d}" + _END).format, col.tolist()))
        if kind == "M":
            nat = np.isnat(col)
            if nat.all():
                return [p[1]] * len(col)
            timed = np.datetime_data(col.dtype)[0] not in ("Y", "M", "W", "D")
            x = (col.astype("datetime64[us]") - _NP_EPOCH) / _DAY_US
            head = book._pre(book._dated(p[4], timed))[0]
            vals = x.tolist() if timed else np.where(nat, 0, x).astype(np.int64).tolist()
            out = list(map((head + "{!r}" + _END).format, vals))
            for i in np.flatnonzero(nat).tolist():
                out[i] = p[1]
            return out
        cell = self._cell
        return [cell(v, p) for v in (col.tolist() if kind else col)]

    def merge(self, row0: int, row1: int, col: int, col1: int | None = None) -> None:
        """Merges lines row0…row1 (0-based, inclusive) of column `col` (…`col1`)."""
        c1 = col if col1 is None else col1
        self._merges.append(f'<mergeCell ref="{_col(col)}{row0 + 1}:{_col(c1)}{row1 + 1}"/>')

    def image(
        self,
        png: bytes,
        row: int,
        col: int,
        *,
        scale: float = 1.0,
        width: int | None = None,
        height: int | None = None,
        name: str = "",
    ) -> int:
        """Puts a PNG with its top left corner at the cell (`row`, `col`; 0-based), `width` × `height` pixels
        (one of them — the aspect kept; none — the image's own size × `scale`: 0.5 for a picture rendered at
        2×). Returns the first line below the picture at the default line height — the anchor of the next one
        in a column of pictures. Pictures go into the file when the sheet is closed."""
        pic = drw.picture(png, row, col, scale, width, height, name or f"Рисунок {len(self._pics) + 1}")
        self._pics.append(pic)
        return row + -(-pic[4] // drw.LINE_PX)

    def close(self) -> None:
        tail = ["</sheetData>"]
        if self._merges:
            tail.append(f'<mergeCells count="{len(self._merges)}">{"".join(self._merges)}</mergeCells>')
        if self._pics:
            tail.append(drw.sheet_drawing())
        tail.append("</worksheet>")
        self._write("".join(tail))
        self._flush()
        self._wait()
        self._fh.close()
        if self._pics:
            self._book._pictures(self.index, self._pics)
            self._pics = []

    def __enter__(self) -> Sheet:
        return self

    def __exit__(self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        if kind is None:
            self.close()
        else:  # the book is dropped by the caller
            try:
                self._wait()
            finally:
                self._fh.close()


class Book:
    """An XLSX file being written (a zip): sheets one after another, then the shared strings and the parts.
    `guard` — text that starts like a formula (= + - @) gets «'» in front (as `audit/report._cell`)."""

    def __init__(self, path: Path, *, guard: bool = False) -> None:
        self._zip = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=3)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xlsx")
        self._guard = guard
        self._strings: dict[str, int] = {}
        self._names: list[str] = []
        self._taken: set[str] = set()
        self._open: Sheet | None = None
        self._styles = drw.Styles()
        self._pres: dict[int | None, _Pre] = {}
        self._rows: dict[tuple[int, ...], list[_Pre]] = {}
        self._dates: dict[tuple[int, bool], int] = {}
        self._media = 0
        self._drawn: list[int] = []  # sheets with a drawing (its number is the sheet's)

    def sheet(self, name: str, *, widths: list[float] | None = None, freeze: int = 1) -> Sheet:
        """A new sheet (the previous one must be closed); `freeze` — lines kept on top."""
        assert self._open is None or self._open._fh.closed
        self._names.append(sheet_name(name, self._taken))
        self._open = Sheet(self, len(self._names), widths, freeze)
        return self._open

    def style(self, *, fmt: str | None = None, fill: str | None = None, base: int = 0, quote: bool = False) -> int:
        """A cell style: `base` (0, HEAD, NOTE, `tone_style`, one made here) with the number format `fmt` (a
        code: `num_fmt(...)`, DATE, DATETIME) and the fill `fill` (a tone of `tone_style`, «#RRGGBB» or ARGB);
        `quote` — Excel's «'» prefix of text. The same arguments give the same id."""
        return self._styles.make(fmt, fill, base, quote)

    def _pre(self, st: int | None) -> _Pre:
        p = self._pres.get(st)
        if p is None:
            s = f' s="{st}"' if st else ""
            p = self._pres[st] = (f"<c{s}><v>", f"<c{s}/>", f'<c{s} t="s"><v>', f'<c{s} t="b"><v>', st or 0)
        return p

    def _row_pres(self, styles: list[int]) -> list[_Pre]:
        key = tuple(styles)
        p = self._rows.get(key)
        if p is None:
            p = self._rows[key] = [self._pre(s) for s in styles]
        return p

    def _cell_str(self, v: str, p: _Pre) -> str:
        if not v:
            return p[1]
        if self._guard and v[0] in _FORMULA:
            v = "'" + v
        i = self._strings.get(v)
        if i is None:
            i = self._strings[v] = len(self._strings)
        return f"{p[2]}{i}{_END}"

    def _dated(self, st: int, timed: bool) -> int:
        """Style `st`, or its twin with a date format when it has no number format of its own."""
        k = self._dates.get((st, timed))
        if k is None:
            k = st if self._styles.xfs[st][0] else self.style(fmt=DATETIME if timed else DATE, base=st)
            self._dates[(st, timed)] = k
        return k

    def _put(self, name: str, data: bytes | str, stored: bool) -> None:
        self._zip.writestr(_info(name, stored=stored), data)

    def _pictures(self, index: int, pics: list[drw.Picture]) -> None:
        self._media = drw.write_pictures(self._put, index, pics, self._media)
        self._drawn.append(index)

    def close(self) -> None:
        try:
            self._parts()
        finally:
            self._pool.shutdown()

    def _parts(self) -> None:
        n = len(self._names)
        put = self._put
        sheets = "".join(
            f'<sheet name="{_esc(s)}" sheetId="{k + 1}" r:id="rId{k + 1}"/>' for k, s in enumerate(self._names)
        )
        put(
            "xl/workbook.xml",
            f'{_XML}<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            f'xmlns:r="{drw.REL}"><sheets>{sheets}</sheets></workbook>',
            False,
        )
        rel, pkg = drw.REL + "/", "http://schemas.openxmlformats.org/package/2006/relationships"
        rels = "".join(
            f'<Relationship Id="rId{k + 1}" Type="{rel}worksheet" Target="worksheets/sheet{k + 1}.xml"/>'
            for k in range(n)
        )
        rels += f'<Relationship Id="rId{n + 1}" Type="{rel}styles" Target="styles.xml"/>'
        rels += f'<Relationship Id="rId{n + 2}" Type="{rel}sharedStrings" Target="sharedStrings.xml"/>'
        put("xl/_rels/workbook.xml.rels", f'{_XML}<Relationships xmlns="{pkg}">{rels}</Relationships>', False)
        put(
            "_rels/.rels",
            f'{_XML}<Relationships xmlns="{pkg}">'
            f'<Relationship Id="rId1" Type="{rel}officeDocument" Target="xl/workbook.xml"/></Relationships>',
            False,
        )
        put("xl/styles.xml", self._styles.xml(), False)
        with self._zip.open(_info("xl/sharedStrings.xml"), "w", force_zip64=True) as fh:
            fh.write(
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<sst xmlns="http://schemas.openxmlformats.'
                b'org/spreadsheetml/2006/main" count="%d" uniqueCount="%d">' % (len(self._strings), len(self._strings))
            )
            buf: list[str] = []
            for s in self._strings:  # in the order of their indices (insertion)
                sp = ' xml:space="preserve"' if s[:1].isspace() or s[-1:].isspace() or "\n" in s else ""
                buf.append(f"<si><t{sp}>{_esc(s)}</t></si>")
                if len(buf) >= 20_000:
                    fh.write("".join(buf).encode("utf-8"))
                    buf = []
            fh.write(("".join(buf) + "</sst>").encode("utf-8"))
        ct = "application/vnd.openxmlformats-officedocument.spreadsheetml"
        over = "".join(
            f'<Override PartName="/xl/worksheets/sheet{k + 1}.xml" ContentType="{ct}.worksheet+xml"/>' for k in range(n)
        )
        over += "".join(drw.content_override(k) for k in self._drawn)
        put(
            "[Content_Types].xml",
            f'{_XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>{drw.CONTENT_PNG if self._drawn else ""}'
            f'<Override PartName="/xl/workbook.xml" ContentType="{ct}.sheet.main+xml"/>{over}'
            f'<Override PartName="/xl/styles.xml" ContentType="{ct}.styles+xml"/>'
            f'<Override PartName="/xl/sharedStrings.xml" ContentType="{ct}.sharedStrings+xml"/></Types>',
            False,
        )
        self._zip.close()

    def abort(self) -> None:
        try:
            if self._open is not None and not self._open._fh.closed:
                try:
                    self._open._wait()
                finally:
                    self._open._fh.close()
        finally:
            self._pool.shutdown()
            self._zip.close()

    def __enter__(self) -> Book:
        return self

    def __exit__(self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        if kind is None:
            self.close()
        else:
            self.abort()
