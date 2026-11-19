"""Format detection by content and extension, the common entry point and previews; the registry of object
formats (lines, points, rasters): one signature for every reader, `.str` told by its content (ADR 0011, 7.9)."""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from openpyxl import Workbook
from PIL import Image

from bf_core.formats import registry
from bf_core.formats.errors import FormatError
from bf_core.formats.inspect import inspect_table
from bf_core.formats.registry import detect, detect_object, group_files, read_objects, read_table
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
        "heavy=[m for m in ('numba','numpy','pandas','pyarrow','openpyxl','xlrd','scipy','PIL','tifffile','ezdxf',"
        "'shapefile','pyproj','bf_core.formats.shp','bf_core.formats.raster_image','bf_core.formats.points')"
        " if m in sys.modules];"
        "print(','.join(heavy))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported with bf_core.formats: {r.stdout.strip()}"


# ---------------------------------------------------------------- object formats: lines, points, rasters (Э7)

SURPAC = "\r\n".join(
    [
        "pit2026,17-Nov-26,pit design,ssi_styles:styles.ssi",
        "0,           0.000,           0.000,           0.000,           0.000,           0.000,           0.000",
        "1, 4585061.988, 466570.123, 100.000, борт, 1",
        "1, 4585061.988, 466580.123, 100.000,",
        "1, 4585071.988, 466580.123, 100.500,",
        "0, 0.000, 0.000, 0.000,",
        "2, 4585000.000, 466000.000, -35.750, разлом",
        "2, 4585001.250, 466002.500, -36.000, разлом",
        "0, 0.000, 0.000, 0.000,",
        "0, 0.000, 0.000, 0.000, END",
        "",
    ]
)
MIF = """Version 300
Charset "WindowsCyrillic"
Delimiter ","
CoordSys Earth Projection 8, 104, "m", 45, 0, 0.9996, 500000, 0
Columns 1
  NAME Char(20)
Data

Pline 3
466570.5 4584699.75
466580.5 4584710.25
466590 4584720.125
    Pen (1,2,0)
Point 466600 4584730
"""


def _shp(folder: Path) -> Path:
    """A PolyLine shapefile of one line of three vertices, with its .shx and .dbf (one text field)."""
    xy = [(466570.5, 4584699.75), (466580.5, 4584710.25), (466590.0, 4584720.125)]
    record = struct.pack("<i4d2i", 3, 0.0, 0.0, 0.0, 0.0, 1, 3) + struct.pack("<i", 0)
    record += struct.pack("<6d", *[v for p in xy for v in p])
    body = struct.pack(">2i", 1, len(record) // 2) + record
    head = struct.pack(">7i", 9994, 0, 0, 0, 0, 0, (100 + len(body)) // 2) + struct.pack("<2i8d", 1000, 3, *[0.0] * 8)
    (folder / "roads.shp").write_bytes(head + body)
    shx = struct.pack(">7i", 9994, 0, 0, 0, 0, 0, 54) + struct.pack("<2i8d", 1000, 3, *[0.0] * 8)
    (folder / "roads.shx").write_bytes(shx + struct.pack(">2i", 50, len(record) // 2))
    field = b"NAME".ljust(11, b"\x00") + b"C" + b"\x00" * 4 + bytes([10, 0]) + b"\x00" * 14
    dbf = (
        struct.pack("<4BIHH20x", 3, 126, 11, 17, 1, 32 + 32 + 1, 11)
        + field
        + b"\r"
        + b" "
        + b"road".ljust(10)
        + b"\x1a"
    )
    (folder / "roads.dbf").write_bytes(dbf)
    return folder / "roads.shp"


@pytest.fixture
def objects(tmp_path: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}

    def put(name: str, data: bytes) -> Path:
        out[name] = tmp_path / name
        out[name].write_bytes(data)
        return out[name]

    put("contours.STR", (REAL / "Без имени.STR").read_bytes())
    put("design.pit", (REAL / "Без имени.STR").read_bytes())
    put("surpac.str", SURPAC.encode("cp1251"))
    put("binary.str", bytes(range(256)) * 8)
    put("empty.str", b"")
    put("map.mif", MIF.encode("cp1251"))
    put("map.mid", '"дорога"\r\n"знак"\r\n'.encode("cp1251"))
    put("survey.xyz", b"466570.586 4584699.697 557.95\n466580.5 4584710.25 560\n")
    put("survey.csv", "X;Y;Z;Код\n466570,586;4584699,697;557,95;a\n466580,5;4584710,25;;b\n".encode("cp1251"))
    put("ortho.jgw", b"2.0\n0.0\n0.0\n-2.0\n1001.0\n2999.0\n")
    put("lost.tab", b"!table\n")
    put("notes.docx", b"PK\x03\x04")
    Image.fromarray(np.zeros((20, 40, 3), dtype=np.uint8)).save(tmp_path / "ortho.jpg")
    out["ortho.jpg"] = tmp_path / "ortho.jpg"
    out["roads.shp"] = _shp(tmp_path)
    return out


def test_object_format_by_extension_and_str_by_content(objects: dict[str, Path]) -> None:
    expected = {
        "contours.STR": "micromine_str", "design.pit": "micromine_str", "surpac.str": "surpac_str",
        "map.mif": "mif", "survey.xyz": "points", "survey.csv": "points", "ortho.jpg": "raster", "roads.shp": "shp",
    }  # fmt: skip
    assert {name: detect_object(objects[name]).key for name in expected} == expected
    assert detect_object(objects["surpac.str"]).kind == "lines" and detect_object(objects["ortho.jpg"]).kind == "raster"
    # The same content under the other name: the content decides, not the case of the extension.
    swapped = objects["surpac.str"].with_name("as_micromine.STR")
    swapped.write_bytes(objects["contours.STR"].read_bytes())
    assert detect_object(swapped).key == "micromine_str"
    # An upload stored under another name.
    stored = objects["surpac.str"].rename(objects["surpac.str"].with_name("upload-17"))
    assert detect_object(stored, filename="pit design.STR").key == "surpac_str"
    for name, code in (
        ("binary.str", "unsupported"), ("empty.str", "unsupported"), ("notes.docx", "unsupported"),
        ("ortho.jgw", "sidecar"), ("map.mid", "sidecar"), ("lost.tab", "sidecar"),
    ):  # fmt: skip
        with pytest.raises(FormatError) as info:
            detect_object(objects[name])
        assert info.value.code == code and info.value.message and info.value.hint, name
    assert ".jpg" in pytest.raises(FormatError, detect_object, objects["ortho.jgw"]).value.hint


def test_every_reader_has_the_same_signature(objects: dict[str, Path]) -> None:
    """path → layers and rasters + warnings; progress from 0 to 1."""
    expected = {
        "contours.STR": ("micromine_str", 1, 0), "design.pit": ("micromine_str", 1, 0),
        "surpac.str": ("surpac_str", 2, 0), "map.mif": ("mif", 2, 0), "survey.xyz": ("points", 1, 0),
        "survey.csv": ("points", 1, 0), "ortho.jpg": ("raster", 0, 1), "roads.shp": ("shp", 1, 0),
    }  # fmt: skip
    seen: list[float] = []
    for name, (key, layers, rasters) in expected.items():
        seen.clear()
        out = read_objects(objects[name], lambda fraction, message: seen.append(fraction))
        assert (out.format, len(out.layers), len(out.rasters)) == (key, layers, rasters), name
        assert out.label == registry.OBJECT_FORMATS[key].label and isinstance(out.warnings, list)
        assert seen and seen[0] == 0.0 and seen[-1] == 1.0 and min(seen) >= 0.0 and max(seen) <= 1.0, name
        assert out.detail is not None and all(layer.point_count for layer in out.layers)
    pit = read_objects(objects["design.pit"])
    assert pit.detail.format == "micromine_pit" and any("Road" in w for w in pit.warnings)
    surpac = read_objects(objects["surpac.str"])
    assert [layer.name for layer in surpac.layers] == ["1", "2"] or len(surpac.layers) == 2
    assert surpac.layers[0].xyz[0].tolist() == [466570.123, 4585061.988, 100.0]  # Surpac stores Y before X
    mif = read_objects(objects["map.mif"])
    assert mif.crs is not None and mif.crs.epsg == 32638
    assert sorted(layer.points_only for layer in mif.layers) == [False, True]
    shp = read_objects(objects["roads.shp"])
    assert shp.layers[0].line_count == 1 and shp.layers[0].attrs.column("NAME").to_pylist() == ["road"]
    points = read_objects(objects["survey.csv"])
    assert points.layers[0].points_only and np.isnan(points.layers[0].xyz[1, 2])
    raster = read_objects(objects["ortho.jpg"]).rasters[0]
    assert (raster.width, raster.height) == (40, 20) and raster.georef.affine == (2.0, 0.0, 1000.0, 0.0, -2.0, 3000.0)


def test_options_companions_and_cancellation(objects: dict[str, Path], tmp_path: Path) -> None:
    # Companion files given by hand: stored somewhere else under other names.
    elsewhere = tmp_path / "store"
    elsewhere.mkdir()
    mid = objects["map.mid"].rename(elsewhere / "0002")
    alone = read_objects(objects["map.mif"])  # the .mid is not next to the .mif any more: no attributes
    assert all(layer.attrs is None or "NAME" not in layer.attrs.column_names for layer in alone.layers)
    assert any(".mid" in w.lower() or "mid" in w.lower() for w in alone.warnings)
    mif = read_objects(objects["map.mif"], sidecars={".MID": mid})
    assert mif.layers[0].attrs.column("NAME").to_pylist() in (["дорога"], ["знак"])
    world = objects["ortho.jgw"].rename(elsewhere / "0003")
    with pytest.raises(FormatError) as info:
        read_objects(objects["ortho.jpg"])
    assert info.value.code == "no_georef"
    image = objects["ortho.jpg"].rename(elsewhere / "0004")
    raster = read_objects(image, filename="Орто 2026.JPG", sidecars={".jgw": world}).rasters[0]
    assert raster.name == "Орто 2026" and raster.georef.source == "world"
    bare = read_objects(image, filename="x.jpg", sidecars={}, options={"require_georef": False, "displays": False})
    assert bare.rasters[0].georef is None and bare.rasters[0].displays == [] and bare.crs is None
    # The encoding goes to the readers that have one.
    points = read_objects(objects["survey.csv"], encoding="cp1251", filename="Точки.csv")
    assert points.layers[0].name == "Точки" and points.layers[0].attrs.column_names == ["Код"]
    assert read_objects(objects["surpac.str"], encoding="cp866").detail.encoding == "cp866"

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None) -> None:
        raise Cancelled

    for name in ("contours.STR", "surpac.str", "map.mif", "survey.xyz", "roads.shp"):
        with pytest.raises(Cancelled):
            read_objects(objects[name], cancel, sidecars={".mid": mid})
    with pytest.raises(Cancelled):
        read_objects(image, cancel, filename="x.jpg", sidecars={".jgw": world})


def test_dxf_reader_is_connected_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ezdxf

    doc = ezdxf.new("R2004")
    doc.modelspace().add_line(
        (466570.5, 4584699.75, 100.0), (466580.5, 4584710.25, 101.0), dxfattribs={"layer": "FAULT"}
    )
    path = tmp_path / "faults.dxf"
    doc.saveas(path)
    assert detect_object(path).key == "dxf"
    pytest.importorskip("bf_core.formats.dxf_lines")
    out = read_objects(path)
    assert out.format == "dxf" and [layer.name for layer in out.layers] == ["FAULT"]
    assert out.layers[0].xyz.tolist() == [[466570.5, 4584699.75, 100.0], [466580.5, 4584710.25, 101.0]]
    # A reader that is not there yet is a clear error, not a crash.
    missing = registry.ObjectFormat("dxf", "AutoCAD DXF", "lines", (".dxf",), "bf_core.formats.no_such_module", "read")
    monkeypatch.setitem(registry._BY_OBJECT_SUFFIX, ".dxf", missing)
    with pytest.raises(FormatError) as info:
        read_objects(path)
    assert info.value.code == "unsupported" and "DXF" in info.value.message and info.value.hint


def test_files_of_a_selection_are_grouped() -> None:
    names = [
        "Roads.SHP", "roads.dbf", "roads.shx", "ROADS.prj", "roads.cpg", "map.mif", "Map.MID", "Карта.JPG",
        "карта.jgw", "карта.tab", "Карта.jpg.grf", "карта.prj", "pit.str", "pit.dxf", "points.xyz", "lost.tfw",
        "readme.docx", "other.mid", "scan.png",
    ]  # fmt: skip
    groups, rest = group_files(names)
    found = {g.main: (g.formats, g.sidecars) for g in groups}
    assert found == {
        "Roads.SHP": (("shp",), {".dbf": "roads.dbf", ".shx": "roads.shx", ".prj": "ROADS.prj", ".cpg": "roads.cpg"}),
        "map.mif": (("mif",), {".mid": "Map.MID"}),
        "Карта.JPG": (
            ("raster",),
            {".jgw": "карта.jgw", ".tab": "карта.tab", ".grf": "Карта.jpg.grf", ".prj": "карта.prj"},
        ),
        "pit.str": (("micromine_str", "surpac_str"), {}),
        "pit.dxf": (("dxf",), {}),
        "points.xyz": (("points",), {}),
        "scan.png": (("raster",), {}),
    }
    assert rest == ["lost.tfw", "readme.docx", "other.mid"]
    assert set(registry.OBJECT_SUFFIXES) >= {
        ".str",
        ".pit",
        ".dxf",
        ".shp",
        ".dbf",
        ".shx",
        ".prj",
        ".cpg",
        ".mif",
        ".mid",
    }
