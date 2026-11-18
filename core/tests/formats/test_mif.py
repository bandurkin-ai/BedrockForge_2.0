"""MapInfo MIF/MID reader: every kind of object, attributes of the MID, `Charset`, `Delimiter`, `CoordSys`,
negative cases and speed (ТЗ 7.35 Д-13; ADR 0009 «Проверка», ADR 0011 §2).

References: the files are written here as text by hand or from random numbers printed with `repr` (the shortest
text of the same double), so the tolerance is none: every coordinate comes back bit for bit. EPSG codes of the
coordinate systems are checked against the registry of pyproj (names of the zones).
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pytest

from bf_core.formats import mif as mif_module
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet
from bf_core.formats.mif import charset_codec, read_mif

NAN = math.nan
X0, Y0 = 7466570.125, 5085061.5  # Gauss–Krüger zone 7 sized coordinates
GK7 = 'CoordSys Earth Projection 8, 1001, "m", 39, 0, 1, 7500000, 0 Bounds (-1000, -1000) (9000000, 9000000)'


def head(
    *columns: str, charset: str | None = "WindowsCyrillic", delimiter: str | None = ",", coordsys: str = ""
) -> str:
    lines = ["Version 300"]
    lines += [f'Charset "{charset}"'] if charset is not None else []
    lines += [f'Delimiter "{delimiter}"'] if delimiter is not None else []
    lines += [coordsys] if coordsys else []
    return "\n".join([*lines, f"Columns {len(columns)}", *(f"  {c}" for c in columns), "Data", "", ""])


def write(
    tmp_path: Path, mif: str, mid: str | None = None, name: str = "t", codec: str = "cp1251", newline: str = "\r\n"
) -> Path:
    path = tmp_path / f"{name}.mif"
    path.write_bytes(mif.replace("\n", newline).encode(codec))
    if mid is not None:
        (tmp_path / f"{name}.mid").write_bytes(mid.replace("\n", newline).encode(codec))
    return path


def error(path: Path, **options: object) -> FormatError:
    with pytest.raises(FormatError) as info:
        read_mif(path, **options)  # type: ignore[arg-type]
    err = info.value
    assert err.message and err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    return err


def same(a: list[float], b: list[float]) -> bool:
    return len(a) == len(b) and all((x != x and y != y) or x == y for x, y in zip(a, b, strict=True))


def xy(layer: LineSet) -> list[list[float]]:
    assert np.isnan(layer.xyz[:, 2]).all()  # MIF has no Z: NaN, never 0
    return layer.xyz[:, :2].tolist()  # type: ignore[no-any-return]


# ---------------------------------------------------------------- objects

EVERYTHING = (
    head("Имя Char(20)", "Сод Float", "N Integer", "Дата Date", coordsys=GK7)
    + """Point 7466570.125 5085061.5
    Symbol (35,0,12)
Line 7466570.125 5085061.5 7466580 5085070
    Pen (1,2,0)
Pline 3
7466570.125 5085061.5
7466580 5085070
7466590.5 5085080.25
    Pen (1,2,0)
    Smooth
Pline Multiple 2
  2
1 2
3 4
  3
5 6
7 8
9 10
Region 2
  5
0 0
0 10
10 10
10 0
0 0
  4
2 2
4 2
4 4
2 4
    Pen (1,2,0)
    Brush (2,16777215,16777215)
    Center 5 5
Text "Скв. 12\\nгор. +100"
    100 200 160 220
    Font ("Arial",0,0,0)
    Angle 30
None
Rect 0 0 5 3
Multipoint 2
1.5 2.5
3.5 4.5
Arc 0 0 1 1
  0 90
"""
)
EVERYTHING_MID = """"точка",1.5,1,20261117
"отрезок",,2,
"линия, с запятой",3.25,3,20260102
"две части",4,4,
"полигон ""А\""",5,5,
"подпись",6,6,
"пусто",7,7,
"рамка",8,8,
"мульти",9,9,
"дуга",10,10,
"""


def test_every_kind_of_object(tmp_path: Path) -> None:
    read = read_mif(write(tmp_path, EVERYTHING, EVERYTHING_MID, name="карта"))
    assert (read.format, read.records, read.encoding) == ("mif", 10, "cp1251")
    assert read.warnings == [
        "1 объектов без геометрии (None) пропущены",
        "Объекты этих типов не поддерживаются и пропущены: Arc — 1",
    ]
    assert read.crs is not None and read.crs.epsg == 28407 and read.crs.name == "Pulkovo 1942 / Gauss-Kruger zone 7"
    assert [layer.name for layer in read.layers] == ["карта (линии)", "карта (точки)", "карта (текст)"]
    lines, points, text = read.layers

    assert not lines.points_only and lines.offsets.tolist() == [0, 2, 5, 7, 10, 15, 20, 25]
    assert lines.closed.tolist() == [0, 0, 0, 0, 1, 1, 1]  # the polygons of the Region and the Rect
    assert xy(lines) == [
        [X0, Y0], [7466580, 5085070],
        [X0, Y0], [7466580, 5085070], [7466590.5, 5085080.25],
        [1, 2], [3, 4],
        [5, 6], [7, 8], [9, 10],
        [0, 0], [0, 10], [10, 10], [10, 0], [0, 0],
        [2, 2], [4, 2], [4, 4], [2, 4], [2, 2],  # the first vertex is repeated: the file did not close the ring
        [0, 0], [5, 0], [5, 3], [0, 3], [0, 0],
    ]  # fmt: skip
    # numbers of the vertices in the file; the added one has the number of the first vertex of its ring
    assert lines.seq.tolist() == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        16,
        21,
        22,
        23,
        24,
        25,
    ]
    assert lines.attrs.column_names == ["Имя", "Сод", "N", "Дата", "кольцо"]
    names = ["отрезок", "линия, с запятой", "две части", "две части", 'полигон "А"', 'полигон "А"', "рамка"]
    assert lines.attrs.column("Имя").to_pylist() == names  # the parts of an object share its row
    assert same(lines.attrs.column("Сод").to_pylist(), [NAN, 3.25, 4.0, 4.0, 5.0, 5.0, 8.0])  # empty is NaN, not 0
    assert lines.attrs.column("Дата").to_pylist() == [None, "2026-01-02", None, None, None, None, None]
    assert same(lines.attrs.column("кольцо").to_pylist(), [NAN, NAN, NAN, NAN, 1.0, 2.0, NAN])

    assert (
        points.points_only and xy(points) == [[X0, Y0], [1.5, 2.5], [3.5, 4.5]] and points.seq.tolist() == [0, 26, 27]
    )
    assert points.attrs.column("Имя").to_pylist() == ["точка", "мульти", "мульти"]
    assert points.attrs.column("Дата").to_pylist() == ["2026-11-17", None, None]
    assert points.attrs.column("N").to_pylist() == [1.0, 9.0, 9.0]

    assert text.points_only and xy(text) == [[100, 200]] and text.seq.tolist() == [20]
    assert text.attrs.to_pylist() == [
        {
            "Имя": "подпись",
            "Сод": 6.0,
            "N": 6.0,
            "Дата": None,
            "text": "Скв. 12\nгор. +100",
            "height": 10.0,
            "rotation": 30.0,
        }
    ]
    header = read.header
    assert (header.version, header.charset, header.delimiter) == ("300", "WindowsCyrillic", ",")
    assert header.coordsys == GK7 and [c.as_dict() for c in header.columns][:2] == [
        {"name": "Имя", "type": "char", "width": 20, "decimals": 0},
        {"name": "Сод", "type": "float", "width": 0, "decimals": 0},
    ]
    assert read.line_count == 7 and read.point_count == 29


def test_one_kind_of_object_gives_one_layer_named_after_the_file(tmp_path: Path) -> None:
    read = read_mif(write(tmp_path, head() + "Pline 2\n1 2\n3 4\nPline\n  2\n5 6\n7 8\n", name="горизонтали"))
    assert [layer.name for layer in read.layers] == ["горизонтали"] and read.layers[0].attrs is None
    assert xy(read.layers[0]) == [[1, 2], [3, 4], [5, 6], [7, 8]] and read.layers[0].offsets.tolist() == [0, 2, 4]
    assert read.warnings == ["В файле не указана система координат: объект загружен без системы координат"]


def test_pline_and_region_details(tmp_path: Path) -> None:
    body = (
        "PLINE 5\n0 0\n\n0\t4\n  4   4  \n4 0\n0 0\n"  # a blank line, a tab, spaces; returns to its first vertex
        "pline multiple 2\n1\n9 9\n3\n1 1\n2 2\n3 3\n"  # a section of one vertex is kept
        "Region 3\n3\n0 0\n0 1\n1 1\n2\n5 5\n6 6\n0\n"  # a triangle, two vertices and an empty polygon
        "Pline 0\nRegion 0\n"  # objects without vertices take their rows
        "Line 1.5e3 -2.5E-1 +7 .5\n"
    )
    mid = "".join(f"{i}\n" for i in range(1, 7))
    read = read_mif(write(tmp_path, head("N Integer") + body, mid, newline="\n"))
    layer = read.layers[0]
    assert layer.offsets.tolist() == [0, 5, 6, 9, 13, 15, 17] and read.records == 6
    assert layer.closed.tolist() == [1, 0, 0, 1, 0, 0]  # a triangle is closed, two vertices are not a ring
    assert xy(layer)[9:13] == [[0, 0], [0, 1], [1, 1], [0, 0]] and xy(layer)[15:] == [[1500, -0.25], [7, 0.5]]
    assert layer.attrs.column("N").to_pylist() == [1.0, 2.0, 2.0, 3.0, 3.0, 6.0]
    assert same(layer.attrs.column("кольцо").to_pylist(), [NAN, NAN, NAN, 1.0, 2.0, NAN])
    assert read.warnings[0] == "1 полигонов из менее чем трёх вершин оставлены незамкнутыми"


def test_text_variants(tmp_path: Path) -> None:
    body = (
        'Text\n  "на следующей строке"\n  10 20 30 25\n'
        'Text "в одной строке" 30 25 10 20\n    Angle -45.5\n    Justify Center\n'
        'Text "в ""кавычках"" и с пробелами  "\n 0 0 4 9\n    Spacing 1.5\n    Label Line Simple 1 1\n'
        'Text "a\\nb\\nc"\n 0 0 4 9\n'
    )
    text = read_mif(write(tmp_path, head() + body)).layers[0]
    rows = text.attrs.to_pylist()
    assert xy(text) == [[10, 20], [10, 20], [0, 0], [0, 0]]  # the lower left corner whatever the order of corners
    assert [r["text"] for r in rows] == [
        "на следующей строке",
        "в одной строке",
        'в ""кавычках"" и с пробелами  ',
        "a\nb\nc",
    ]
    assert [r["height"] for r in rows] == [5.0, 5.0, 9.0, 3.0]  # of one line of the text
    assert [r["rotation"] for r in rows] == [0.0, -45.5, 0.0, 0.0]  # no Angle clause is 0° by the format
    assert error(write(tmp_path, head() + "Text без кавычек\n 0 0 1 1\n", name="b")).code == "broken"
    assert error(write(tmp_path, head() + 'Text "a"\n 0 0 1\n', name="c")).code == "broken"


def test_collection_shares_the_row_of_the_object(tmp_path: Path) -> None:
    body = (
        "Collection 3\nRegion 1\n4\n0 0\n0 1\n1 1\n0 0\n    Pen (1,2,0)\nPline 2\n5 5\n6 6\nMultipoint 2\n7 7\n8 8\n"
        "Point 9 9\n"
    )
    read = read_mif(write(tmp_path, head("NAME Char(5)") + body, '"coll"\n"pt"\n'))
    lines, points = read.layers
    assert read.records == 2 and lines.attrs.column("NAME").to_pylist() == ["coll", "coll"]
    assert points.attrs.column("NAME").to_pylist() == ["coll", "coll", "pt"] and xy(points) == [[7, 7], [8, 8], [9, 9]]


def test_random_objects_against_the_generator_in_any_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hard data: sizes from millimetres to 1e9 m, lines longer than a chunk, the same result for chunks of
    97 bytes. (Below 1e-6 m a line whose ends coincide within `lineset.CLOSE_TOL` is closed by the model.)"""
    rng = np.random.default_rng(20261117)
    out: list[str] = []
    line_xy: list[np.ndarray] = []
    counts: list[int] = []
    closed: list[int] = []
    owner: list[int] = []
    point_xy: list[list[float]] = []
    point_owner: list[int] = []
    for index in range(300):
        scale = 10.0 ** rng.integers(-3, 10)
        kind = rng.integers(0, 3)
        if kind == 0:
            x, y = (rng.normal(size=2) * scale).tolist()
            out.append(f"Point {x!r} {y!r}\n    Symbol (35,0,12)")
            point_xy.append([x, y])
            point_owner.append(index)
            continue
        sections = int(rng.integers(1, 4))
        out.append(f"Region {sections}" if kind == 1 else f"Pline Multiple {sections}")
        for _ in range(sections):
            n = int(rng.integers(4, 90))
            pts = rng.normal(size=(n, 2)) * scale + [X0, Y0]
            if kind == 1:
                pts[-1] = pts[0]
            out.append(f"  {n}")
            out += [f"{x!r} {y!r}" for x, y in pts.tolist()]
            line_xy.append(pts)
            counts.append(n)
            closed.append(int(kind == 1))
            owner.append(index)
        out.append("    Pen (1,2,0)")
    path = write(tmp_path, head("ID Integer") + "\n".join(out) + "\n", "".join(f"{i}\n" for i in range(300)))
    whole = read_mif(path)
    monkeypatch.setattr(mif_module, "CHUNK_BYTES", 97)
    pieces = read_mif(path)
    for read in (whole, pieces):
        lines, points = read.layers
        assert lines.xyz[:, :2].tobytes() == np.vstack(line_xy).tobytes()  # tolerance: none
        assert lines.offsets.tolist() == np.concatenate([[0], np.cumsum(counts)]).tolist()
        assert lines.closed.tolist() == closed
        assert lines.attrs.column("ID").to_pylist() == [float(i) for i in owner]
        assert points.xyz[:, :2].tolist() == point_xy
        assert points.attrs.column("ID").to_pylist() == [float(i) for i in point_owner]
        assert sorted(lines.seq.tolist() + points.seq.tolist()) == list(range(sum(counts) + len(point_xy)))
        assert read.records == 300 and len(read.warnings) == 1  # only «no CoordSys»


# ---------------------------------------------------------------- .mid, Delimiter, Charset


def test_mid_types_and_empty_values(tmp_path: Path) -> None:
    columns = [
        "Name Char(10)", "I Integer", "S SmallInt", "F Float", "D Decimal(10, 2)", "L Logical", "Dt Date",
        "Big LargeInt", "Wide Decimal(18,0)", "name Char(3)", "Cnt LargeInt",
    ]  # fmt: skip
    rows = [
        ['"abc"', "1", "2", "3.5", "4.25", "T", "20261117", "123456789012345678", "123456789012345678", '"x"', "8"],
        ['""', "", "", "", "", "", "", "", "", "", ""],
        ['"таб\tвнутри"', "abc", " 7 ", "1e3", "-0.5", "F", "2026", "5", "6", '"y"', "9007199254740991"],
    ]
    mid = "".join("\t".join(row) + "\n" for row in rows)
    read = read_mif(write(tmp_path, head(*columns, delimiter=None) + "Point 1 1\nPoint 2 2\nPoint 3 3\n", mid))
    table = read.layers[0].attrs
    assert read.header.delimiter == "\t"  # the default of the format
    assert table.column_names == ["Name", "I", "S", "F", "D", "L", "Dt", "Big", "Wide", "name_2", "Cnt"]
    assert table.column("Name").to_pylist() == ["abc", None, "таб\tвнутри"]  # empty text is null
    assert same(table.column("I").to_pylist(), [1.0, NAN, NAN])  # empty and not a number: NaN, never 0
    assert same(table.column("S").to_pylist(), [2.0, NAN, 7.0])
    assert same(table.column("F").to_pylist(), [3.5, NAN, 1000.0])
    assert same(table.column("D").to_pylist(), [4.25, NAN, -0.5])
    assert same(table.column("L").to_pylist(), [1.0, NAN, 0.0])
    assert table.column("Dt").to_pylist() == ["2026-11-17", None, "2026"]
    assert table.column("Big").to_pylist() == ["123456789012345678", None, "5"]  # too long for a float64: text
    assert table.column("Wide").to_pylist() == ["123456789012345678", None, "6"]
    assert same(table.column("Cnt").to_pylist(), [8.0, NAN, 9007199254740991.0])  # fits exactly: a number
    assert "Поле «I»: 1 знач. не являются числами — прочитаны как пустые" in read.warnings
    assert read.layers[0].attr_schema()[1] == {"name": "I", "kind": "number", "unit": None, "scope": "feature"}


def test_mid_of_one_column_keeps_its_empty_rows(tmp_path: Path) -> None:
    body = "Point 1 1\nPoint 2 2\nPoint 3 3\n"
    read = read_mif(write(tmp_path, head("NAME Char(5)") + body, "a\n\nb\n\n\n"))
    assert read.layers[0].attrs.column("NAME").to_pylist() == ["a", None, "b"]


def test_mid_missing_or_not_matching(tmp_path: Path) -> None:
    body = "Point 1 1\nPoint 2 2\n"
    alone = read_mif(write(tmp_path, head("N Integer") + body))
    assert alone.layers[0].attrs is None and alone.layers[0].point_count == 2
    assert alone.warnings[0] == "Рядом с файлом нет таблицы .mid: атрибуты не загружены"
    short = error(write(tmp_path, head("N Integer") + body, "1\n", name="b"))
    assert short.code == "broken" and "(1)" in short.message and "(2)" in short.message and ".mid" in short.hint
    assert error(write(tmp_path, head("N Integer") + body, "1\n2\n3\n", name="c")).code == "broken"
    assert error(write(tmp_path, head("N Integer") + body, "", name="d")).code == "broken"
    ragged = error(write(tmp_path, head("A Integer", "B Integer") + body, "1,2\n3\n", name="e"))
    assert ragged.code == "broken" and "«3»" in ragged.message and "2" in ragged.message
    elsewhere = tmp_path / "upload-9.bin"
    elsewhere.write_bytes(b"5\n6\n")
    given = read_mif(write(tmp_path, head("N Integer") + body, name="f"), mid=elsewhere)
    assert given.layers[0].attrs.column("N").to_pylist() == [5.0, 6.0]


@pytest.mark.parametrize(
    ("name", "codec"),
    [
        ("WindowsCyrillic", "cp1251"), ("windowslatin1", "cp1252"), ("WindowsLatin2", "cp1250"),
        ("CodePage866", "cp866"), ("CodePage437", "cp437"), ("ISO8859_5", "iso8859-5"), ("MacCyrillic", "mac-cyrillic"),
        ("UTF-8", "utf-8"), ("Neutral", None), ("Klingon", None), ("", None),
    ],
)  # fmt: skip
def test_charset_names(name: str, codec: str | None) -> None:
    assert charset_codec(name) == codec


def test_charset_of_the_file(tmp_path: Path) -> None:
    body = "Point 1 1\n"
    dos = read_mif(write(tmp_path, head("Имя Char(9)", charset="CodePage866") + body, '"руда"\n', codec="cp866"))
    assert dos.encoding == "cp866" and dos.layers[0].attrs.to_pylist() == [{"Имя": "руда"}]
    bom = tmp_path / "bom.mif"
    bom.write_bytes(b"\xef\xbb\xbf" + (head("Имя Char(9)", charset=None) + body).encode("utf-8"))
    (tmp_path / "bom.mid").write_bytes(b"\xef\xbb\xbf" + '"Ёж"\n'.encode())
    with_bom = read_mif(bom)
    assert with_bom.encoding == "utf-8" and with_bom.layers[0].attrs.to_pylist() == [{"Имя": "Ёж"}]
    assert len(with_bom.warnings) == 1  # only «no CoordSys»
    for k, charset in enumerate((None, "Neutral")):  # nothing declared: Windows-1251 and a warning
        read = read_mif(write(tmp_path, head("Имя Char(9)", charset=charset) + body, '"руда"\n', name=f"n{k}"))
        assert read.encoding == "cp1251" and read.layers[0].attrs.to_pylist() == [{"Имя": "руда"}]
        assert "Кодировка не указана (нет Charset в заголовке MIF): текст прочитан как Windows-1251" in read.warnings
    plain = read_mif(write(tmp_path, head("N Integer", charset=None) + body, "1\n", name="ascii"))
    assert not any("Кодировка" in w for w in plain.warnings)  # ASCII only: the code page does not matter
    unknown = read_mif(write(tmp_path, head(charset="Klingon") + body, name="k"))
    assert unknown.warnings[0] == "Кодировка «Klingon» из заголовка MIF неизвестна: текст прочитан как Windows-1251"
    path = write(tmp_path, head("Имя Char(9)", charset="WindowsLatin1") + body, '"руда"\n', name="g", codec="cp866")
    assert read_mif(path, encoding="cp866").layers[0].attrs.to_pylist() == [{"Имя": "руда"}]  # the user's choice wins
    assert error(path, encoding="no-such-codec").code == "unknown_encoding"


def test_line_ends_of_old_macintosh(tmp_path: Path) -> None:
    read = read_mif(write(tmp_path, head("N Integer") + "Pline 2\n1 2\n3 4\nPoint 5 6\n", "1\n2\n", newline="\r"))
    assert [xy(layer) for layer in read.layers] == [[[1, 2], [3, 4]], [[5, 6]]]


# ---------------------------------------------------------------- CoordSys (the clause itself: test_mif_crs.py)


def test_coordsys_of_the_file(tmp_path: Path) -> None:
    utm = 'CoordSys Earth Projection 8, 104, "m", 39, 0, 0.9996, 500000, 0\n  Bounds (-1, -1) (1, 1)'
    read = read_mif(write(tmp_path, head(coordsys=utm) + "Point 466570.5 5085061.25\n"))
    assert read.crs is not None and read.crs.epsg == 32637 and read.warnings == []
    assert read.header.coordsys == utm.replace("\n ", "")  # the clause went on to the next line
    local = read_mif(write(tmp_path, head(coordsys='CoordSys NonEarth Units "m"') + "Point 1 2\n", name="b"))
    assert local.crs is None and local.layers[0].point_count == 1  # loaded without a CRS
    assert local.warnings == ["Система координат файла условная (NonEarth): объект загружен без системы координат"]
    lambert = 'CoordSys Earth Projection 3, 74, "m", -96, 23, 29.5, 45.5, 0, 0'
    other = read_mif(write(tmp_path, head(coordsys=lambert) + "Point 1 2\n", name="c"))
    assert other.crs is None and "проекция 3" in other.warnings[0] and other.layers[0].point_count == 1


# ---------------------------------------------------------------- negative cases


def test_empty_and_foreign_files(tmp_path: Path) -> None:
    assert error(write(tmp_path, "")).code == "empty_file"
    assert error(write(tmp_path, "Version 300\nColumns 0\n", name="b")).code == "not_mif"  # no Data section
    tab = tmp_path / "c.mif"
    tab.write_bytes(b"\x00\x01\x02\x03MAPINFO binary" + bytes(600))
    binary = error(tab)
    assert binary.code == "not_mif" and "MIF/MID" in binary.hint
    utf16 = tmp_path / "d.mif"
    utf16.write_bytes((head() + "Point 1 2\n").encode("utf-16"))
    assert error(utf16).code == "unsupported"
    assert error(write(tmp_path, head(), name="e")).code == "no_lines"  # a header without objects
    nothing = error(write(tmp_path, head() + "None\nArc 0 0 1 1\n 0 90\n", name="f"))
    assert nothing.code == "no_lines" and "нет линий, точек и текста" in nothing.message
    assert error(write(tmp_path, 'Version 300\nDelimiter "abc"\nColumns 0\nData\n', name="g")).code == "broken"
    assert error(write(tmp_path, "Version 300\nColumns 3\n  A Integer\nData\nPoint 1 2\n", name="h")).code == "broken"


def test_truncated_and_broken_objects(tmp_path: Path) -> None:
    cut = error(write(tmp_path, head() + "Point 1 2\nPline 5\n0 0\n1 1\n"))
    assert cut.code == "truncated" and "объект 2" in cut.message
    assert error(write(tmp_path, head() + "Region 2\n  3\n0 0\n0 1\n1 1\n", name="b")).code == "truncated"
    assert error(write(tmp_path, head() + "Pline Multiple 2\n", name="c")).code == "truncated"
    assert error(write(tmp_path, head() + 'Text "abc"\n', name="d")).code == "truncated"
    bad = error(write(tmp_path, head() + "Pline 3\n0 0\n1 x\n2 2\n", name="e"))
    assert bad.code == "broken" and "Строка 9" in bad.message and "«1 x»" in bad.message  # line of the file
    long_block = "".join(f"{i} {i}\n" for i in range(50)) + "oops\n" + "".join(f"{i} {i}\n" for i in range(49))
    far = error(write(tmp_path, head() + "Pline 100\n" + long_block, name="f"))
    assert far.code == "broken" and "Строка 58" in far.message and "«oops»" in far.message  # 7 + 1 + 50
    assert error(write(tmp_path, head() + "Pline 3\n0 0\n1\n2 2\n", name="g")).code == "broken"
    assert error(write(tmp_path, head() + "Pline 2\n0 0\nnan 1\n", name="h")).code == "broken"  # not a coordinate
    assert error(write(tmp_path, head() + "Point 1\n", name="i")).code == "broken"
    assert error(write(tmp_path, head() + "Line 1 2 3\n", name="j")).code == "broken"
    assert error(write(tmp_path, head() + "Pline many\n", name="k")).code == "broken"
    assert error(write(tmp_path, head() + "Region -2\n", name="l")).code == "broken"
    assert error(write(tmp_path, head() + "Point 1 inf\n", name="m")).code == "broken"


def test_bombs_do_not_allocate(tmp_path: Path) -> None:
    started = time.perf_counter()
    assert error(write(tmp_path, head() + "Pline 2000000000\n0 0\n1 1\n")).code == "truncated"
    assert error(write(tmp_path, head() + "Region 2000000000\n  3\n0 0\n0 1\n1 1\n", name="b")).code == "truncated"
    assert error(write(tmp_path, head() + "Multipoint 999999999999\n0 0\n", name="c")).code == "truncated"
    columns = error(write(tmp_path, "Version 300\nColumns 99999999\n  A Integer\nData\n", name="d"))
    assert columns.code == "too_large"
    garbage = tmp_path / "e.mif"
    garbage.write_bytes((head() + "Pline 3\n").encode() + b"1" * (3 << 20))  # megabytes without a line end
    assert error(garbage).code == "too_large"
    endless = tmp_path / "f.mif"
    endless.write_bytes(b"Version 300\n" + b"Index 1,2,3\n" * 200_000)  # the header never ends
    assert error(endless).code == "not_mif"
    assert time.perf_counter() - started < 5


# ---------------------------------------------------------------- progress, cancellation, speed


def _text_of(values: np.ndarray) -> list[str]:
    return [f"{x!r} {y!r}" for x, y in values.tolist()]


@pytest.mark.windows  # the files must be free after a cancelled reading (maps, handles)
def test_progress_and_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mif_module, "CHUNK_BYTES", 1 << 16)
    pts = np.random.default_rng(3).normal(size=(60_000, 2)) * 1e3 + [X0, Y0]
    body = "".join(f"Pline 100\n{chr(10).join(rows)}\n" for rows in np.array_split(_text_of(pts), 600))
    path = write(tmp_path, head("N Integer") + body, "".join(f"{i}\n" for i in range(600)), newline="\n")
    seen: list[float] = []
    read = read_mif(path, progress=lambda fraction, message: seen.append(fraction))
    assert read.layers[0].xyz[:, :2].tobytes() == pts.tobytes() and read.line_count == 600
    assert len(seen) > 15 and seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None) -> None:
        if fraction > 0.2:
            raise Cancelled

    with pytest.raises(Cancelled):
        read_mif(path, progress=cancel)
    path.unlink()  # nothing keeps the file open after a cancelled reading


@pytest.mark.slow
@pytest.mark.parametrize(("kind", "per_object"), [("Pline", 100), ("Region", 5)])
def test_speed_one_million_vertices_of_lines(tmp_path: Path, kind: str, per_object: int) -> None:
    """Long polylines (10 000 × 100) and many small polygons (200 000 × 5) with a MID row each."""
    objects = 1_000_000 // per_object
    pts = np.random.default_rng(5).normal(size=(objects, per_object, 2)) * 50 + [X0, Y0]
    region = kind == "Region"
    if region:
        pts[:, -1] = pts[:, 0]
    start = f"Region 1\n  {per_object}\n" if region else f"Pline {per_object}\n"
    tail = "\n    Pen (1,2,0)\n    Brush (2,16777215,16777215)\n" if region else "\n    Pen (1,2,0)\n"
    rows = np.array(_text_of(pts.reshape(-1, 2)), dtype=object).reshape(objects, per_object)
    body = "".join(start + "\n".join(row) + tail for row in rows.tolist())
    mid = "".join(f'"L-{i}",{i * 0.5},{i}\n' for i in range(objects))
    path = write(tmp_path, head("NAME Char(12)", "AU Float", "ID Integer", coordsys=GK7) + body, mid, newline="\n")
    started = time.perf_counter()
    read = read_mif(path)
    seconds = time.perf_counter() - started
    layer = read.layers[0]
    assert layer.point_count == 1_000_000 and layer.line_count == objects and read.warnings == []
    assert layer.xyz[:, :2].tobytes() == pts.tobytes() and layer.closed_count == (objects if region else 0)
    assert layer.attrs.column("NAME")[objects - 1].as_py() == f"L-{objects - 1}" and read.crs.epsg == 28407  # type: ignore[union-attr]
    assert seconds < 10, f"1 млн вершин MIF ({objects} объектов {kind}): {seconds:.1f} с (бюджет Д-13 — 10 с)"


@pytest.mark.slow
def test_speed_one_million_points_with_attributes(tmp_path: Path) -> None:
    """The hard case: an object and a MID row per vertex, a Symbol clause after every point."""
    n = 1_000_000
    pts = np.random.default_rng(6).normal(size=(n, 2)) * 1e4 + [X0, Y0]
    body = "".join(f"Point {row}\n    Symbol (35,0,12)\n" for row in _text_of(pts))
    mid = "".join(f'"BH-{i % 5000}",{i * 0.001!r},{i}\n' for i in range(n))
    path = write(tmp_path, head("NAME Char(12)", "AU Float", "ID Integer", coordsys=GK7) + body, mid, newline="\n")
    started = time.perf_counter()
    read = read_mif(path)
    seconds = time.perf_counter() - started
    layer = read.layers[0]
    assert layer.points_only and layer.point_count == n and read.warnings == []
    assert layer.xyz[:, :2].tobytes() == pts.tobytes()
    assert layer.attrs.column("AU")[n - 1].as_py() == (n - 1) * 0.001 and layer.attrs.column("ID")[7].as_py() == 7.0
    assert seconds < 10, f"1 млн точек MIF с атрибутами: {seconds:.1f} с (бюджет Д-13 — 10 с)"
