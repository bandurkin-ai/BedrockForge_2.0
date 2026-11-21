"""A streaming XLSX writer for the protocol of ore intervals (ТЗ 7.35 Д-26 «Результат» (4); ADR 0018 решение 10):
the long form of 5 000 holes is about half a million rows — `openpyxl` (without lxml) writes ~10 000 rows a
second, this writes the sheet XML directly into the zip (≈ 15 times as fast).

    with Book(path) as book:
        with book.sheet("Интервалы", widths=[12, 8, 8]) as sh:
            sh.row(["HOLE", "FROM", "TO"], style=HEAD)
            sh.row(["DH-1", 3.0, 11.0])
            sh.merge(row0, row1, col)          # 0-based lines of the sheet, inclusive

Values: None — an empty cell; str — a shared string; bool, int, float — a number (NaN, ±inf — empty). Styles:
`HEAD` (bold, Montserrat, stone fill), `NOTE` (italic), `tone_style(tone)` — the fill of a class of the form
И1, сл. 17 in the brand colours (ADR 0006: those of `charts/column/classes.ts`). Sheet names are made valid
(≤ 31 UTF-16 units, no `[]:*?/\\`, no «'» at the ends, not «History», unique; `"` and `'` escaped in the
attribute). Rows carry `r`, cells do not (positional, as the format allows): an empty value is `<c/>`. The
workbook is readable by Excel, LibreOffice and `openpyxl`.
"""

from __future__ import annotations

import math
import re
import zipfile
from pathlib import Path
from types import TracebackType
from typing import IO, Any

__all__ = ["HEAD", "NOTE", "Book", "Sheet", "sheet_name", "tone_style"]

HEAD, NOTE = 1, 2
#: tones of the form (`ore_protocol` cells) → fill (ARGB); the classes in the brand colours mixed with white
_TONES = {
    "ore": "FFFBC3A3",
    "subgrade": "FFFBE1CF",
    "waste_in": "FFE3E7EC",
    "interlayer": "FFB3CDFF",
    "waste": "FFC9CFD6",
    "frame": "FFEEF0F3",
    "pass": "FFD6E8D4",
    "reject": "FFF6D2D2",
    "none": "FFF4F5F7",
}
_TONE_IDS = {t: 3 + k for k, t in enumerate(_TONES)}
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")


def tone_style(tone: str | None) -> int:
    """The style of a cell of the form with this tone (0 — none)."""
    return _TONE_IDS.get(tone or "", 0)


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


class Sheet:
    """A sheet being written: rows go straight into the zip entry."""

    def __init__(self, book: Book, path: str, widths: list[float] | None, freeze: int) -> None:
        self._book = book
        self._fh: IO[bytes] = book._zip.open(path, "w", force_zip64=True)
        self._buf: list[str] = []
        self._size = 0
        self.rows = 0
        self._merges: list[str] = []
        head = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        ]
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

    def _flush(self) -> None:
        if self._buf:
            self._fh.write("".join(self._buf).encode("utf-8"))
            self._buf, self._size = [], 0

    def row(self, values: Any, style: int | list[int] | None = None) -> None:
        """One row; `style` — of every cell, or per cell (0 — none)."""
        self.rows += 1
        strings = self._book._strings
        out = [f'<row r="{self.rows}">']
        for k, v in enumerate(values):
            st = style[k] if isinstance(style, list) else style
            s = f' s="{st}"' if st else ""
            if v is None:
                out.append(f"<c{s}/>")
            elif isinstance(v, str):
                if not v:
                    out.append(f"<c{s}/>")
                    continue
                i = strings.get(v)
                if i is None:
                    i = strings[v] = len(strings)
                out.append(f'<c{s} t="s"><v>{i}</v></c>')
            elif isinstance(v, bool):
                out.append(f'<c{s} t="b"><v>{int(v)}</v></c>')
            elif isinstance(v, int):
                out.append(f"<c{s}><v>{v}</v></c>")
            else:
                x = float(v)
                out.append(f"<c{s}><v>{x!r}</v></c>" if math.isfinite(x) else f"<c{s}/>")
        out.append("</row>")
        self._write("".join(out))

    def merge(self, row0: int, row1: int, col: int, col1: int | None = None) -> None:
        """Merges lines row0…row1 (0-based, inclusive) of column `col` (…`col1`)."""
        c1 = col if col1 is None else col1
        self._merges.append(f'<mergeCell ref="{_col(col)}{row0 + 1}:{_col(c1)}{row1 + 1}"/>')

    def close(self) -> None:
        tail = ["</sheetData>"]
        if self._merges:
            tail.append(f'<mergeCells count="{len(self._merges)}">{"".join(self._merges)}</mergeCells>')
        tail.append("</worksheet>")
        self._write("".join(tail))
        self._flush()
        self._fh.close()

    def __enter__(self) -> Sheet:
        return self

    def __exit__(self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        if kind is None:
            self.close()
        else:  # the book is dropped by the caller
            self._fh.close()


def _styles() -> str:
    fills = [
        '<fill><patternFill patternType="none"/></fill>',
        '<fill><patternFill patternType="gray125"/></fill>',
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE6E9EE"/></patternFill></fill>',
    ]
    fills += [
        f'<fill><patternFill patternType="solid"><fgColor rgb="{c}"/></patternFill></fill>' for c in _TONES.values()
    ]
    xfs = [
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>',
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" applyFont="1" applyFill="1"><alignment wrapText="1"'
        ' vertical="top"/></xf>',
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" applyFont="1"/>',
    ]
    xfs += [
        f'<xf numFmtId="0" fontId="0" fillId="{3 + k}" borderId="1" applyFill="1" applyBorder="1">'
        '<alignment wrapText="1" vertical="top"/></xf>'
        for k in range(len(_TONES))
    ]
    edge = '<{0} style="thin"><color rgb="FF8A99A8"/></{0}>'
    border = "".join(edge.format(s) for s in ("left", "right", "top", "bottom"))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="3"><font><sz val="10"/><color rgb="FF1B222B"/><name val="Inter"/></font>'
        '<font><b/><sz val="10"/><color rgb="FF1B222B"/><name val="Montserrat"/></font>'
        '<font><i/><sz val="10"/><color rgb="FF8A99A8"/><name val="Inter"/></font></fonts>'
        f'<fills count="{len(fills)}">{"".join(fills)}</fills>'
        f'<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border>{border}<diagonal/>'
        "</border></borders>"
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    )


class Book:
    """An XLSX file being written (a zip): sheets one after another, then the shared strings and the parts."""

    def __init__(self, path: Path) -> None:
        self._zip = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=3)
        self._strings: dict[str, int] = {}
        self._names: list[str] = []
        self._taken: set[str] = set()
        self._open: Sheet | None = None

    def sheet(self, name: str, *, widths: list[float] | None = None, freeze: int = 1) -> Sheet:
        """A new sheet (the previous one must be closed); `freeze` — lines kept on top."""
        assert self._open is None or self._open._fh.closed
        self._names.append(sheet_name(name, self._taken))
        self._open = Sheet(self, f"xl/worksheets/sheet{len(self._names)}.xml", widths, freeze)
        return self._open

    def close(self) -> None:
        n = len(self._names)
        z = self._zip
        sheets = "".join(
            f'<sheet name="{_esc(s)}" sheetId="{k + 1}" r:id="rId{k + 1}"/>' for k, s in enumerate(self._names)
        )
        z.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{sheets}</sheets></workbook>",
        )
        rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
        rels = "".join(
            f'<Relationship Id="rId{k + 1}" Type="{rel}worksheet" Target="worksheets/sheet{k + 1}.xml"/>'
            for k in range(n)
        )
        rels += f'<Relationship Id="rId{n + 1}" Type="{rel}styles" Target="styles.xml"/>'
        rels += f'<Relationship Id="rId{n + 2}" Type="{rel}sharedStrings" Target="sharedStrings.xml"/>'
        pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{pkg}">{rels}</Relationships>',
        )
        z.writestr(
            "_rels/.rels",
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{pkg}">'
            f'<Relationship Id="rId1" Type="{rel}officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        z.writestr("xl/styles.xml", _styles())
        with z.open("xl/sharedStrings.xml", "w", force_zip64=True) as fh:
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
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{ct}.sheet.main+xml"/>{over}'
            f'<Override PartName="/xl/styles.xml" ContentType="{ct}.styles+xml"/>'
            f'<Override PartName="/xl/sharedStrings.xml" ContentType="{ct}.sharedStrings+xml"/></Types>',
        )
        z.close()

    def abort(self) -> None:
        self._zip.close()

    def __enter__(self) -> Book:
        return self

    def __exit__(self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        if kind is None:
            self.close()
        else:
            self.abort()
