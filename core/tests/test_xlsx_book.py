"""The streaming XLSX book with pictures and number formats (подшаг 14.11; ADR 0020 §8, решение 15): the parts
of a book of Э12 stay byte for byte, the same calls give the same bytes, `openpyxl` reads the pictures on their
sheets, the formats and the dates; `Sheet.table` writes the cells of `Sheet.row`."""

from __future__ import annotations

import datetime as dt
import hashlib
import struct
import zipfile
import zlib
from pathlib import Path

import numpy as np
import openpyxl
import pytest

from bf_core.composites.ore_xlsx import (
    DATE,
    DATETIME,
    HEAD,
    MAX_ROWS,
    NOTE,
    Book,
    num_fmt,
    png_size,
    tone_style,
)

#: sha256 of the parts of `_e12_book` as the writer of Э12 (before 14.11) made them
E12_PARTS = {
    "xl/worksheets/sheet1.xml": "150e0f27402710f872b09869fba8ddb42ef8ec319917b586f27c89583eaee2bc",
    "xl/worksheets/sheet2.xml": "364add1e75b8fa02161ebd18dedcac07f07613389481f687b8a0821bbed6feba",
    "xl/worksheets/sheet3.xml": "aef8f65b3a17d0bddbe2d62738b9b3e81b7b5a75edf44927dde1c518f3969b51",
    "xl/workbook.xml": "2b1916909a04c061482219b1e091da685d97ea94d7506ecf14d445b0123134cf",
    "xl/_rels/workbook.xml.rels": "2877b8c818647324e29316b9787b3c269b21414d70be6eed3fcc4ad192a3bed2",
    "_rels/.rels": "9f96bb683e71c45171c7ae310bf6c37fb5a834bb9738497ac7084c42ac9c2d9a",
    "xl/styles.xml": "2a22e0d24ac1c259dcfcd61befa729edefb606d9ef428daee38273aa84b69bdf",
    "xl/sharedStrings.xml": "587659c9dd5bbe26a3e3ff8d077d0bdf0f20d4cf0ce2afbf8ab2610407bc849d",
    "[Content_Types].xml": "610a99b1fb41b575d88fe8680802773c12b8d2d4552d3dc9c2e6693b36bb1bd3",
}


def png(w: int, h: int, rgb: tuple[int, int, int] = (30, 80, 200)) -> bytes:
    """A plain RGB PNG of w × h pixels."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _e12_book(path: Path) -> None:
    with Book(path) as book:
        with book.sheet("Интервалы", widths=[12, 8, 8]) as sh:
            sh.row(["HOLE", "FROM", "TO", "Au"], style=HEAD)
            sh.row(["DH-1", 3.0, 11.0, float("nan")], style=[0, 0, 0, tone_style("ore")])
            sh.row([" lead", True, 7, None, ""], style=NOTE)
            sh.row(["a\nb", 1e-300, -0.1, float("inf"), "=x"])
            sh.merge(1, 2, 0)
        with book.sheet("Лист'с\"кав[ы]чками", freeze=0) as sh:
            for t in ["ore", "subgrade", "waste_in", "interlayer", "waste", "frame", "pass", "reject", "none"]:
                sh.row([t, 1.5], style=tone_style(t))
            sh.merge(0, 1, 0, 1)
        with book.sheet("history"):
            pass


def _parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_book_of_e12_keeps_its_parts(tmp_path: Path) -> None:
    _e12_book(tmp_path / "a.xlsx")
    got = {n: hashlib.sha256(b).hexdigest() for n, b in _parts(tmp_path / "a.xlsx").items()}
    assert got == E12_PARTS


def _report(path: Path, pics: list[bytes]) -> None:
    with Book(path, guard=True) as book:
        num, pct, units = (
            book.style(fmt=num_fmt(3)),
            book.style(fmt=num_fmt(1, "percent")),
            book.style(fmt=num_fmt(2, "units")),
        )
        warn = book.style(fill="#F4B13A")
        for k, image in enumerate(pics):
            with book.sheet(f"Лист {k + 1}", widths=[14, 10, 10, 10, 12]) as sh:
                sh.row(["Проба", "Au", "HARD", "Откл.", "Дата"], style=HEAD)
                sh.row(["S-1", 1 / 3, 0.0909090909, 9.0909090909, dt.date(2024, 3, 5)], style=[0, num, pct, units, 0])
                sh.row(
                    ["=SUM(A1)", None, float("nan"), -1.5, dt.datetime(2024, 3, 5, 12, 30)], style=[warn, 0, 0, 0, 0]
                )
                sh.image(image, 0, 6, scale=0.5, name=f"График {k + 1}")


def test_three_pictures_on_their_sheets_formats_and_dates(tmp_path: Path) -> None:
    pics = [png(80, 60), png(120, 40, (229, 72, 58)), png(64, 64, (244, 177, 58))]
    path = tmp_path / "отчёт с рисунками.xlsx"
    _report(path, pics)
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Лист 1", "Лист 2", "Лист 3"]
    for k, ws in enumerate(wb.worksheets):
        assert len(ws._images) == 1  # type: ignore[attr-defined]
        img = ws._images[0]  # type: ignore[attr-defined]
        assert (img.anchor._from.row, img.anchor._from.col) == (0, 6)
        w, h = png_size(pics[k])
        assert (img.anchor.ext.width, img.anchor.ext.height) == (w // 2 * 9525, h // 2 * 9525)
        assert img._data() == pics[k]
        b, c, d, e = ws["B2"], ws["C2"], ws["D2"], ws["E2"]
        assert b.value == 1 / 3 and b.number_format == "0.000"  # full precision, rounded by the format
        assert c.value == 0.0909090909 and c.number_format == "0.0%"
        assert d.value == 9.0909090909 and d.number_format == '0.00"%"'
        assert e.value == dt.datetime(2024, 3, 5) and e.number_format == DATE
        assert ws["E3"].value == dt.datetime(2024, 3, 5, 12, 30) and ws["E3"].number_format == DATETIME
        assert ws["A3"].value == "'=SUM(A1)" and ws["A3"].fill.fgColor.rgb == "FFF4B13A"
        assert ws["B3"].value is None and ws["C3"].value is None  # a gap is an empty cell, not 0
        assert ws["A1"].font.b and ws["A1"].font.name == "Montserrat"
    parts = _parts(path)
    assert {n for n in parts if n.startswith("xl/media/")} == {f"xl/media/image{k}.png" for k in (1, 2, 3)}
    assert "xl/drawings/drawing2.xml" in parts and "xl/worksheets/_rels/sheet3.xml.rels" in parts
    ct = parts["[Content_Types].xml"].decode()
    assert 'Extension="png"' in ct and ct.count("drawing+xml") == 3


def test_same_calls_give_same_bytes(tmp_path: Path) -> None:
    pics = [png(40, 30)]
    _report(tmp_path / "1.xlsx", pics)
    _report(tmp_path / "2.xlsx", pics)
    assert (tmp_path / "1.xlsx").read_bytes() == (tmp_path / "2.xlsx").read_bytes()
    with zipfile.ZipFile(tmp_path / "1.xlsx") as z:
        assert {i.date_time for i in z.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        assert z.getinfo("xl/media/image1.png").compress_type == zipfile.ZIP_STORED


def _sheet_xml(path: Path) -> bytes:
    with zipfile.ZipFile(path) as z:
        return z.read("xl/worksheets/sheet1.xml") + z.read("xl/styles.xml") + z.read("xl/sharedStrings.xml")


def test_table_writes_the_cells_of_row(tmp_path: Path) -> None:
    n = 7
    cols: list[object] = [
        np.array([0.1, np.nan, 2.5e-17, np.inf, -3.0, 1e300, 7.0]),
        np.arange(n, dtype=np.int64) - 3,
        np.array([True, False] * 3 + [True]),
        np.array(["a", "", "=b", "a", None, "c", "-1"], dtype=object),
        np.array(["2024-01-01", "NaT", "1999-12-31", "2024-02-29", "NaT", "2000-01-01", "2030-06-30"], "datetime64[D]"),
        np.array(["2024-01-01T06:00:00", "NaT", *["2024-01-02T00:00:01"] * 5], "datetime64[s]"),
        [dt.date(2024, 1, k + 1) for k in range(n)],
        [
            None,
            1,
            2.5,
            "x",
            True,
            dt.datetime(2024, 1, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=3))),
            np.float32(0.1),
        ],
        np.arange(n, dtype=np.float32) / 3,
    ]
    all_styles = [-1, 0, NOTE, 0, 0, tone_style("pass"), HEAD, 0, -1]  # -1: the percent style

    def write(path: Path, by_table: bool, keep: list[int]) -> None:
        with Book(path, guard=True) as book:
            pct = book.style(fmt=num_fmt(1, "percent"))
            styles = [pct if all_styles[k] < 0 else all_styles[k] for k in keep]
            with book.sheet("T") as sh:
                sh.row(["шапка"] * len(keep), style=HEAD)
                if by_table:
                    sh.table([cols[k] for k in keep], style=styles, chunk=3)
                else:
                    for i in range(n):
                        sh.row([cols[k][i] for k in keep], style=styles)  # type: ignore[index]
                assert sh.rows == n + 1

    def cells(path: Path) -> list[tuple[object, ...]]:
        ws = openpyxl.load_workbook(path).active
        assert ws is not None
        return [(c.value, c.number_format, c.fill.fgColor.rgb, c.font.b) for r in ws.iter_rows() for c in r]

    numeric = [0, 1, 2, 8]  # no strings, no dates: the same bytes
    write(tmp_path / "t.xlsx", True, numeric)
    write(tmp_path / "r.xlsx", False, numeric)
    assert _sheet_xml(tmp_path / "t.xlsx") == _sheet_xml(tmp_path / "r.xlsx")
    every = list(range(len(cols)))  # shared strings and date styles are made a column at a time: the same cells
    write(tmp_path / "t.xlsx", True, every)
    write(tmp_path / "r.xlsx", False, every)
    assert cells(tmp_path / "t.xlsx") == cells(tmp_path / "r.xlsx")
    ws = openpyxl.load_workbook(tmp_path / "t.xlsx").active
    assert ws is not None
    assert [ws.cell(r, 5).value for r in (2, 3)] == [dt.datetime(2024, 1, 1), None]
    assert ws.cell(2, 6).value == dt.datetime(2024, 1, 1, 6) and ws.cell(2, 6).number_format == DATETIME
    assert ws.cell(2, 6).fill.fgColor.rgb == "FFD6E8D4"  # the tone kept, the date format added
    assert ws.cell(7, 8).value == dt.datetime(2023, 12, 31, 22)  # an aware datetime in UTC
    assert ws.cell(4, 4).value == "'=b" and ws.cell(3, 1).value is None and ws.cell(5, 1).value is None
    assert ws.cell(2, 1).number_format == "0.0%" and ws.cell(2, 3).value is True
    assert ws.cell(2, 1).value == 0.1 and ws.cell(4, 1).value == 2.5e-17  # full precision


def test_styles_and_pictures_api(tmp_path: Path) -> None:
    with Book(tmp_path / "s.xlsx") as book:
        a = book.style(fmt="0.00", base=HEAD)
        assert book.style(fmt="0.00", base=HEAD) == a and a > 11
        assert book.style() == 0 and book.style(base=HEAD) == HEAD and book.style(fill="pass", base=0) != 0
        assert book.style(fmt="General") == 0
        with pytest.raises(ValueError, match="colour"):
            book.style(fill="зелёный")
        with pytest.raises(ValueError, match="style"):
            book.style(base=999)
        with book.sheet("Рисунки") as sh:
            assert sh.image(png(100, 50), 2, 3) == 2 + 3  # 50 px → 3 lines of 20 px
            assert sh.image(png(100, 50), 6, 3, width=40) == 6 + 1  # 40 × 20
            with pytest.raises(ValueError, match="PNG"):
                sh.image(b"GIF89a", 0, 0)
            sh.rows = MAX_ROWS - 1
            sh.row([1])
            with pytest.raises(ValueError, match="at most"):
                sh.row([1])
    with pytest.raises(ValueError):
        num_fmt(2, "money")
    assert [num_fmt(0), num_fmt(2, "sci"), num_fmt(3, "int")] == ["0", "0.00E+00", "0"]


def test_failure_inside_a_sheet_drops_the_book(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="стоп"), Book(tmp_path / "x.xlsx") as book, book.sheet("A") as sh:
        sh.row([1.0] * 10)
        sh.image(png(4, 4), 0, 0)
        raise RuntimeError("стоп")
    with pytest.raises(IndexError):
        with Book(tmp_path / "y.xlsx") as book, book.sheet("A") as sh:
            sh.row([1, 2, 3], style=[0, 0])
