"""Format detection by content and extension, the common entry point and previews."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

from bf_core.formats.errors import FormatError
from bf_core.formats.inspect import inspect_table
from bf_core.formats.registry import detect, read_table
from bf_core.formats.table import ReadOptions
from tests.formats.dat_builder import build

DATA = Path(__file__).parent / "data"
REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real"
CP = "ibm-5347_P100-1998"


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}

    def put(name: str, data: bytes) -> None:
        out[name] = tmp_path / name
        out[name].write_bytes(data)

    dat = build(CP, [(b"BHID", "C", 6, 0), (b"X", "R", 8, 3)], [["A-1", 1.5], ["A-2", 2.5]])
    put("collar.DAT", dat)
    put("standard.dat", build(None, [(b"BHID", "C", 6, 0)], [["A-1"]], standard=True))
    put("dat_named.csv", dat)
    put("text.dat", "BHID;X\nA-1;1,5\n".encode("cp1251"))
    put("t.csv", "Скважина;X\nС-1;1,5\nС-2;2,5\n".encode("cp1251"))
    put("t.txt", b"HOLE\tX\nA\t1\n")
    put("utf16.csv", "Скважина;X\nС-1;1,5\n".encode("utf-16"))
    put("tabs.xls", b"HOLE\tX\nA\t1\n")  # "Excel" exports of web systems
    put("page.xls", b"<html><table><tr><td>HOLE</td></tr></table></html>")
    put("empty.csv", b"")
    put("empty.xlsx", b"")
    put("empty.bin", b"")
    put("random.dat", bytes(range(256)) * 8)
    put("note.md", b"# title\ntext\n")
    put("lines.STR", (REAL / "Без имени.STR").read_bytes())
    wb = Workbook()
    wb.active.append(["Скважина", "X"])
    wb.active.append(["С-1", 1.5])
    wb.active.append(["С-2", 2.5])
    wb.save(tmp_path / "book.xlsx")
    out["book.xlsx"] = tmp_path / "book.xlsx"
    put("book_named.xls", out["book.xlsx"].read_bytes())
    put("old_named.xlsx", (DATA / "book.xls").read_bytes())
    put("archive.zip", b"PK\x03\x04" + b"\x00" * 100)
    put("ole.doc", bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 600)
    return out


def test_detect_by_content_then_extension(files: dict[str, Path]) -> None:
    expected = {
        "collar.DAT": "dat", "standard.dat": "dat", "dat_named.csv": "dat", "text.dat": "csv", "t.csv": "csv",
        "t.txt": "csv", "utf16.csv": "csv", "tabs.xls": "csv", "page.xls": "unknown", "empty.csv": "csv",
        "empty.xlsx": "xlsx", "empty.bin": "unknown", "random.dat": "unknown", "note.md": "unknown",
        "lines.STR": "unknown", "book.xlsx": "xlsx", "book_named.xls": "xlsx", "old_named.xlsx": "xls",
        "archive.zip": "unknown", "ole.doc": "unknown",
    }  # fmt: skip
    assert {name: detect(path) for name, path in files.items()} == expected
    assert detect(DATA / "book.xls") == "xls"
    # An upload stored under another name: the original name gives the extension.
    assert detect(files["t.csv"].rename(files["t.csv"].with_name("data")), filename="t.csv") == "csv"
    assert detect(files["note.md"], filename="note.txt") == "csv"


def test_read_table_reads_every_format_the_same_way(files: dict[str, Path]) -> None:
    for name in ("collar.DAT", "dat_named.csv"):
        table = read_table(files[name])
        assert table.info.format == "dat" and table.column("X").values.tolist() == [1.5, 2.5]
    for name, fmt in (("t.csv", "csv"), ("book.xlsx", "xlsx"), ("book_named.xls", "xlsx")):
        table = read_table(files[name])
        assert table.info.format == fmt and table.names == ["Скважина", "X"]
        assert table.column("Скважина").to_pylist() == ["С-1", "С-2"] and table.column("X").values.tolist() == [
            1.5,
            2.5,
        ]
    assert read_table(files["text.dat"]).column("X").values.tolist() == [1.5]
    assert read_table(files["old_named.xlsx"], ReadOptions(sheet="Замеры")).info.format == "xls"
    assert read_table(files["collar.DAT"], ReadOptions(encoding="cp866")).info.encoding == "cp866"
    seen: list[float] = []
    read_table(files["t.csv"], progress=lambda f, m: seen.append(f))
    assert seen[0] == 0.0 and seen[-1] == 1.0
    for name, code in (
        ("note.md", "unsupported"),
        ("page.xls", "unsupported"),
        ("empty.csv", "empty_file"),
        ("empty.xlsx", "empty_file"),
        ("random.dat", "unsupported"),
        ("lines.STR", "unsupported"),
    ):
        with pytest.raises(FormatError) as info:
            read_table(files[name])
        assert info.value.code == code and info.value.hint, name


def test_previews_show_the_first_rows_and_how_the_file_is_read(files: dict[str, Path], tmp_path: Path) -> None:
    dat = inspect_table(files["collar.DAT"])
    assert dat.kind == "dat" and dat.estimated_rows == 2 and dat.rows == [["A-1", "1.5"], ["A-2", "2.5"]]
    assert dat.fields == [
        {"name": "BHID", "type": "C", "width": 6, "decimals": 0, "description": ""},
        {"name": "X", "type": "R", "width": 8, "decimals": 3, "description": ""},
    ]
    assert "записей 2" in dat.summary and "BHID, X" in dat.summary

    lines = ["Протокол № 15", "", "Скважина;От;До"] + [f"С-{i};{i};{i + 1}" for i in range(20_000)]
    big = tmp_path / "big.csv"
    big.write_bytes("\r\n".join(lines).encode("cp1251"))
    csv = inspect_table(big, limit=5)
    assert csv.info.encoding == "cp1251" and csv.info.delimiter == ";"
    assert csv.numbers == [1, 2, 3, 4, 5]  # the empty line has no number: header_row=2 is the header
    assert csv.rows[:3] == [["Протокол № 15"], ["Скважина", "От", "До"], ["С-0", "0", "1"]]
    assert csv.estimated_rows is not None and abs(csv.estimated_rows - 20_002) < 2_000
    assert read_table(big, ReadOptions(header_row=2)).names == csv.rows[1]

    book = inspect_table(DATA / "book.xls", limit=3)
    assert book.kind == "xls" and book.info.sheet == "Устья" and book.info.sheets == ["Пусто", "Устья", "Замеры"]
    assert book.numbers == [1, 2, 3] and book.rows[0] == ["Сводка по скважинам"]
    assert book.rows[2][:6] == ["С-1", "466570.586", "4584699.697", "557.95", "300.3", "2024-05-17"]
    other = inspect_table(files["book.xlsx"], ReadOptions(sheet="Sheet"))
    assert other.kind == "xlsx" and other.rows == [["Скважина", "X"], ["С-1", "1.5"], ["С-2", "2.5"]]
    with pytest.raises(FormatError):
        inspect_table(files["note.md"])


def test_formats_package_does_not_load_heavy_libraries_on_import() -> None:
    code = (
        "import sys;"
        "import bf_core.formats.registry, bf_core.formats.inspect, bf_core.formats.micromine, bf_core.formats.text,"
        "bf_core.formats.excel, bf_core.formats.numbers, bf_core.formats.table, bf_core.formats.bocu1;"
        "heavy=[m for m in ('numba','numpy','pandas','pyarrow','openpyxl','xlrd','scipy') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported with bf_core.formats: {r.stdout.strip()}"
