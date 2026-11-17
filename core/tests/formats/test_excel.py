"""Excel workbooks: .xlsx/.xlsm (openpyxl) and .xls (xlrd) — sheets, header row, dates, hostile files."""

from __future__ import annotations

import math
import struct
import zipfile
from datetime import datetime, time
from pathlib import Path

import numpy as np
import pytest
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference

from bf_core.formats.errors import FormatError
from bf_core.formats.excel import read_excel
from bf_core.formats.table import ReadOptions

DATA = Path(__file__).parent / "data"


def _workbook(tmp_path: Path, name: str = "book.xlsx") -> Path:
    wb = Workbook()
    wb.active.title = "Пусто"
    sheet = wb.create_sheet("Устья")
    sheet.append(["Сводка по скважинам"])
    sheet.append(["Скважина", "X, м", "Y, м", "Z, м", "Глубина (м)", "Дата", None, "AU", "Флаг", "Код"])
    sheet.append(["С-1", 466570.586, 4584699.697, 557.95, 300.3, datetime(2024, 5, 17), None, "<0.01", True, "007"])
    sheet.append(
        ["С-2", "466543,962", 4584688.653, 561.438, 250, datetime(2024, 6, 1, 14, 30), None, 0.5, False, "012"]
    )
    sheet.append(["С-3", 466500, 4584600.5, 560, "н/д", None, None, "#N/A", None, "100"])
    sheet.append([None] * 10)
    other = wb.create_sheet("Замеры")
    other.append(["Скважина", "Глубина", "Азимут", "Наклон", "Время"])
    other.append(["С-1", 0, 10, -60, time(8, 30)])
    other.append(["С-1", 50, 12.5, -61, time(9, 0)])
    chart = BarChart()
    chart.add_data(Reference(other, min_col=2, min_row=1, max_row=3), titles_from_data=True)
    wb.create_chartsheet("Диаграмма").add_chart(chart)  # not a table: it is not offered as a sheet
    path = tmp_path / name
    wb.save(path)
    return path


def _check_collars(path: Path, fmt: str) -> None:
    table = read_excel(path, ReadOptions(header_row=2))
    assert table.info.format == fmt and table.info.sheet == "Устья" and table.info.header_row == 2
    assert table.info.sheets == ["Пусто", "Устья", "Замеры"]
    assert table.names == ["Скважина", "X, м", "Y, м", "Z, м", "Глубина (м)", "Дата", "AU", "Флаг", "Код"]
    assert [c.kind for c in table.columns] == [
        "text",
        "number",
        "number",
        "number",
        "number",
        "text",
        "number",
        "text",
        "text",
    ]
    assert table.rows == 3  # the empty trailing row is dropped
    assert table.column("X, м").values.tolist() == [466570.586, 466543.962, 466500.0]  # a text cell with a comma too
    depth = table.column("Глубина (м)").values
    assert depth[:2].tolist() == [300.3, 250.0] and math.isnan(depth[2])  # «н/д» is missing, not 0
    assert table.column("Дата").to_pylist() == ["2024-05-17", "2024-06-01 14:30:00", None]
    au = table.column("AU")
    assert au.values[:2].tolist() == [0.005, 0.5] and math.isnan(au.values[2]) and au.below_limit == 1
    assert table.column("Флаг").to_pylist() == ["True", "False", None]
    assert table.column("Код").to_pylist() == ["007", "012", "100"]
    assert any("<x" in w and "AU" in w for w in table.warnings)


@pytest.mark.parametrize("name", ["book.xlsx", "book.xlsm"])
def test_xlsx_first_sheet_with_data_header_row_dates_and_mixed_cells(tmp_path: Path, name: str) -> None:
    _check_collars(_workbook(tmp_path, name), "xlsx")


def test_xls_reads_the_same(tmp_path: Path) -> None:
    _check_collars(DATA / "book.xls", "xls")
    survey = read_excel(DATA / "book.xls", ReadOptions(sheet="Замеры"))
    assert survey.column("Время").to_pylist() == ["08:30:00", "09:00:00"]
    assert survey.column("Азимут").values.tolist() == [10.0, 12.5]


def test_sheet_choice_and_errors(tmp_path: Path) -> None:
    path = _workbook(tmp_path)
    survey = read_excel(path, ReadOptions(sheet="Замеры"))
    assert survey.info.sheet == "Замеры" and survey.column("Наклон").values.tolist() == [-60.0, -61.0]
    assert survey.column("Время").to_pylist() == ["08:30:00", "09:00:00"]
    for source in (path, DATA / "book.xls"):
        with pytest.raises(FormatError) as info:
            read_excel(source, ReadOptions(sheet="Нет такого"))
        assert info.value.code == "no_sheet" and "Замеры" in info.value.hint
        with pytest.raises(FormatError) as info:
            read_excel(source, ReadOptions(header_row=50))
        assert info.value.code == "header_row"
        empty = read_excel(source, ReadOptions(sheet="Пусто", header_row=0))
        assert empty.rows == 0 and empty.columns == []


def test_same_data_in_csv_excel_and_dat_reads_the_same(tmp_path: Path) -> None:
    import pyarrow as pa

    from bf_core.formats.micromine import Field, read_dat, write_dat
    from bf_core.formats.table import Column
    from bf_core.formats.text import read_text

    holes = ["С-1", "С-1", "С-22"]
    x = [466570.586, 4584699.697, 0.1]
    au = [0.005, math.nan, 12.5]
    csv = tmp_path / "t.csv"
    csv.write_bytes("BHID;X;AU\r\nС-1;466570,586;<0,01\r\nС-1;4 584 699,697;н/д\r\nС-22;0,1;12,5\r\n".encode("cp1251"))
    wb = Workbook()
    wb.active.append(["BHID", "X", "AU"])
    for row in (["С-1", 466570.586, "<0,01"], ["С-1", 4584699.697, None], ["С-22", 0.1, 12.5]):
        wb.active.append(row)
    wb.save(tmp_path / "t.xlsx")
    columns = [
        Column("BHID", "text", pa.chunked_array([pa.array(holes)])),
        Column("X", "number", np.array(x)),
        Column("AU", "number", np.array(au)),
    ]
    write_dat(
        tmp_path / "t.DAT", columns, fields=[Field("BHID", "C", 10, 0), Field("X", "R", 8, 3), Field("AU", "R", 8, 3)]
    )
    tables = [read_text(csv), read_excel(tmp_path / "t.xlsx"), read_dat(tmp_path / "t.DAT")]
    for table in tables:
        assert table.names == ["BHID", "X", "AU"] and [c.kind for c in table.columns] == ["text", "number", "number"]
        assert table.column("BHID").to_pylist() == holes
        assert table.column("X").values.tobytes() == np.array(x).tobytes()
        assert table.column("AU").values.tobytes() == np.array(au).tobytes()


def _rezip(source: Path, target: Path, change: dict[str, object]) -> None:
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            edit = change.get(item.filename)
            if edit is None and item.filename in change:
                continue
            dst.writestr(item, edit(data) if callable(edit) else data)
        for name, data in change.items():
            if isinstance(data, bytes):
                dst.writestr(name, data)


def test_wrong_dimension_and_many_rows_in_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bf_core.formats import excel

    wb = Workbook()
    wb.active.append(["HOLE", "FROM", "AU"])
    for i in range(5000):
        wb.active.append([f"H{i % 7}", i, "abc" if i == 4000 else i / 8])
    wb.save(tmp_path / "raw.xlsx")
    path = tmp_path / "dim.xlsx"
    _rezip(
        tmp_path / "raw.xlsx", path, {"xl/worksheets/sheet1.xml": lambda d: d.replace(b'ref="A1:C5001"', b'ref="A1"')}
    )
    xml = zipfile.ZipFile(path).read("xl/worksheets/sheet1.xml")
    assert b'<dimension ref="A1"' in xml and b"A1:C5001" not in xml  # as some exporters write it
    monkeypatch.setattr(excel, "CHUNK_ROWS", 512)
    seen: list[float] = []
    table = read_excel(path, progress=lambda f, m: seen.append(f))
    assert table.rows == 5000 and table.column("FROM").values[-1] == 4999.0
    au = table.column("AU")  # text found late: the sheet is read once more, nothing is lost
    assert au.kind == "text" and au.to_pylist()[3999:4002] == ["499.875", "abc", "500.125"]
    assert any("abc" in w for w in table.warnings)
    assert len(seen) > 10 and seen == sorted(seen) and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None) -> None:
        if len(seen) > 5:
            raise Cancelled
        seen.append(fraction)

    seen.clear()
    with pytest.raises(Cancelled):
        read_excel(path, progress=cancel)
    path.unlink()  # the workbook is closed


def test_broken_workbooks_are_refused_with_advice(tmp_path: Path) -> None:
    good = _workbook(tmp_path)

    def code(name: str, data: bytes) -> str:
        path = tmp_path / name
        path.write_bytes(data)
        with pytest.raises(FormatError) as info:
            read_excel(path)
        assert info.value.message and info.value.hint
        return info.value.code

    assert code("empty.xlsx", b"") == "empty_file"
    assert code("cut.xlsx", good.read_bytes()[:3000]) == "broken"
    assert code("text.xlsx", b"HOLE;X\nA;1\n") == "broken"
    assert code("cut.xls", (DATA / "book.xls").read_bytes()[:3000]) == "broken"
    assert code("ole.xls", bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 2000) == "broken"
    _rezip(good, tmp_path / "noworkbook.xlsx", {"xl/workbook.xml": None})
    assert code("nw.xlsx", (tmp_path / "noworkbook.xlsx").read_bytes()) == "broken"
    _rezip(good, tmp_path / "bin.xlsx", {"xl/workbook.xml": None, "xl/workbook.bin": b"\x00"})
    assert code("xlsb.xlsx", (tmp_path / "bin.xlsx").read_bytes()) == "unsupported"
    _rezip(good, tmp_path / "sheet.xlsx", {"xl/worksheets/sheet2.xml": lambda d: d[: len(d) // 2]})
    assert code("badsheet.xlsx", (tmp_path / "sheet.xlsx").read_bytes()) == "broken"
    _rezip(good, tmp_path / "bomb.xlsx", {"xl/media/zeros.bin": b"\x00" * (80 << 20)})
    assert (tmp_path / "bomb.xlsx").stat().st_size < 1 << 20
    assert code("b.xlsx", (tmp_path / "bomb.xlsx").read_bytes()) == "too_large"


def _biff5(codepage: int, sheet: str, rows: list[list[object]]) -> bytes:
    """A bare BIFF5 stream (Excel 5.0/95 without the OLE2 container): 8-bit strings in the workbook's code page."""

    def rec(kind: int, data: bytes) -> bytes:
        return struct.pack("<HH", kind, len(data)) + data

    name = sheet.encode("cp1251")
    globals_ = rec(0x0809, struct.pack("<HHHH", 0x0500, 0x0005, 0, 0)) + rec(0x0042, struct.pack("<H", codepage))
    bound_len = 4 + 4 + 2 + 1 + len(name)
    offset = len(globals_) + bound_len + 4
    globals_ += rec(0x0085, struct.pack("<IHB", offset, 0, len(name)) + name) + rec(0x000A, b"")
    body = rec(0x0809, struct.pack("<HHHH", 0x0500, 0x0010, 0, 0))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if isinstance(value, str):
                raw = value.encode("cp1251")
                body += rec(0x0204, struct.pack("<HHHH", r, c, 0, len(raw)) + raw)
            elif value is not None:
                body += rec(0x0203, struct.pack("<HHHd", r, c, 0, float(value)))
    return globals_ + body + rec(0x000A, b"")


def test_xls_of_excel_95_with_a_russian_code_page(tmp_path: Path) -> None:
    path = tmp_path / "old.xls"
    path.write_bytes(_biff5(1251, "Пробы", [["Скважина", "От", "Au, г/т"], ["С-1", 0, 1.25], ["С-1", 1.5, "<0,1"]]))
    table = read_excel(path)
    assert table.info.format == "xls" and table.info.sheets == ["Пробы"]
    assert table.names == ["Скважина", "От", "Au, г/т"] and table.column("Скважина").to_pylist() == ["С-1", "С-1"]
    assert table.column("От").values.tolist() == [0.0, 1.5] and table.column("Au, г/т").values.tolist() == [1.25, 0.05]
