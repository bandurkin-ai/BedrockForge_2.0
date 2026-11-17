"""ESRI Shapefile reader: every geometry type, Z and M, parts and rings, the attributes of the `.dbf`, code pages,
`.prj`, negative cases and speed (ТЗ 7.35 Д-13; ADR 0009 «Проверка», ADR 0011 §2).

References: the files are built byte by byte from the ESRI Shapefile Technical Description (`shp_builder`) —
independent of the reader and of pyshp — or written by pyshp (ADR 0009). Coordinates are doubles in the file, so
the tolerance is none: every value comes back bit for bit.
"""

from __future__ import annotations

import math
import struct
import time
from pathlib import Path

import numpy as np
import pytest
import shapefile

from bf_core.formats import shp as shp_module
from bf_core.formats.dbf import codec_of
from bf_core.formats.errors import FormatError
from bf_core.formats.shp import read_shp
from tests.formats.shp_builder import (
    NO_DATA,
    NULL,
    X0,
    Y0,
    big_points,
    dbf_bytes,
    ids,
    point,
    poly,
    shp_bytes,
    write_set,
)

NAN = math.nan


def error(path: Path, **options: object) -> FormatError:
    with pytest.raises(FormatError) as info:
        read_shp(path, **options)  # type: ignore[arg-type]
    err = info.value
    assert err.message and err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    return err


def same(a: list[float], b: list[float]) -> bool:
    """Equal lists of floats where NaN equals NaN."""
    return len(a) == len(b) and all((x != x and y != y) or x == y for x, y in zip(a, b, strict=True))


# ---------------------------------------------------------------- geometry


def test_point_z_and_attributes_written_by_pyshp(tmp_path: Path) -> None:
    pts = [(X0, Y0, 100.25), (X0 + 9.877, Y0 + 8.012, -35.75), (7466590.5, 5085080.25, 0.0)]
    with shapefile.Writer(str(tmp_path / "скважины"), shapeType=shapefile.POINTZ, encoding="cp1251") as w:
        w.field("ИМЯ", "C", 20)
        w.field("AU", "N", 12, 3)
        w.field("N", "N", 6, 0)
        for i, (x, y, z) in enumerate(pts):
            w.pointz(x, y, z)
            w.record(["скв-1", "скв-2", ""][i], [1.5, None, 0.0][i], i + 1)
    (tmp_path / "скважины.cpg").write_text("1251")
    read = read_shp(tmp_path / "скважины.shp")
    layer = read.layers[0]
    assert (read.format, read.records, read.encoding, read.warnings) == ("shp", 3, "cp1251", [])
    assert read.point_count == 3 and read.line_count == 0 and read.crs is None
    assert layer.name == "скважины" and layer.points_only and layer.offsets is None and layer.closed is None
    assert layer.xyz.tolist() == [list(p) for p in pts]  # bit for bit
    assert layer.seq.tolist() == [0, 1, 2]
    assert layer.attrs.column_names == ["ИМЯ", "AU", "N"] and layer.vattrs is None  # M of pyshp is «no data»
    rows = layer.attrs.to_pylist()
    assert rows[0] == {"ИМЯ": "скв-1", "AU": 1.5, "N": 1.0}
    assert rows[1]["ИМЯ"] == "скв-2" and math.isnan(rows[1]["AU"])  # an empty number is NaN, never 0
    assert rows[2]["ИМЯ"] is None and rows[2]["AU"] == 0.0  # and a zero stays a zero
    assert (read.header.shape_type, read.header.type_name) == (11, "PointZ")
    assert [f.as_dict() for f in read.header.fields] == [
        {"name": "ИМЯ", "type": "C", "width": 20, "decimals": 0},
        {"name": "AU", "type": "N", "width": 12, "decimals": 3},
        {"name": "N", "type": "N", "width": 6, "decimals": 0},
    ]
    assert layer.attr_schema()[1] == {"name": "AU", "kind": "number", "unit": None, "scope": "feature"}


@pytest.mark.parametrize("shape_type", [1, 21, 11])
def test_points_of_every_flavour(tmp_path: Path, shape_type: int) -> None:
    values = {
        1: [(X0, Y0), (1.5, -2.5)],
        21: [(X0, Y0, 7.25), (1.5, -2.5, NO_DATA)],
        11: [(X0, Y0, 3.5, 7.25), (1.5, -2.5, 0.0, NO_DATA)],
    }
    read = read_shp(
        write_set(tmp_path, shp_bytes(shape_type, [point(shape_type, *v) for v in values[shape_type]]), ids(2))
    )
    layer = read.layers[0]
    z = {1: [NAN, NAN], 21: [NAN, NAN], 11: [3.5, 0.0]}[shape_type]  # no Z in the file: NaN, not 0
    assert layer.xyz[:, :2].tolist() == [[X0, Y0], [1.5, -2.5]] and same(layer.xyz[:, 2].tolist(), z)
    rows = layer.attrs.to_pylist()
    if shape_type == 1:
        assert layer.attrs.column_names == ["ID"]
    else:  # the measure of a point is its attribute; «no data» is NaN
        assert layer.attrs.column_names == ["ID", "M"] and rows[0]["M"] == 7.25 and math.isnan(rows[1]["M"])
    assert [r["ID"] for r in rows] == [1.0, 2.0] and read.warnings == []


def test_point_z_without_measures(tmp_path: Path) -> None:
    """PointZ records may stop after Z (36 bytes instead of 44)."""
    records = [struct.pack("<i3d", 11, X0, Y0, 12.5), struct.pack("<i3d", 11, 1.0, 2.0, -3.0)]
    layer = read_shp(write_set(tmp_path, shp_bytes(11, records), ids(2))).layers[0]
    assert layer.xyz.tolist() == [[X0, Y0, 12.5], [1.0, 2.0, -3.0]] and layer.attrs.column_names == ["ID"]


def test_polyline_parts_z_and_measures(tmp_path: Path) -> None:
    first = poly(
        13,
        [0, 3],
        [(X0, Y0), (X0 + 10, Y0), (X0 + 10, Y0 + 10), (20.5, 20.25), (30.125, 30.0)],
        z=[1.0, 2.0, 3.0, 4.0, 5.0],
        m=[0.5, NO_DATA, 1.5, 2.5, 3.5],
    )
    loop = poly(13, [0], [(0, 0), (0, 5), (5, 5), (5, 0), (0, 0)], z=[7.0] * 5, m=[NO_DATA] * 5)
    dbf = dbf_bytes([(b"NAME", "C", 8, 0), (b"LEN", "N", 8, 2)], [[b"fault", b"12.50"], [b"pit", b""]])
    read = read_shp(write_set(tmp_path, shp_bytes(13, [first, loop]), dbf))
    layer = read.layers[0]
    assert read.records == 2 and read.warnings == [] and not layer.points_only
    assert layer.offsets.tolist() == [0, 3, 5, 10]  # every part is a line
    assert layer.closed.tolist() == [0, 0, 1]  # a line that returns to its first vertex is closed
    assert layer.xyz[:5].tolist() == [
        [X0, Y0, 1.0], [X0 + 10, Y0, 2.0], [X0 + 10, Y0 + 10, 3.0], [20.5, 20.25, 4.0], [30.125, 30.0, 5.0]
    ]  # fmt: skip
    assert layer.xyz[5:].tolist() == [[0, 0, 7], [0, 5, 7], [5, 5, 7], [5, 0, 7], [0, 0, 7]]
    assert layer.seq.tolist() == list(range(10))
    assert layer.vattrs.column_names == ["M"]  # the measure is a vertex attribute
    assert same(layer.vattrs.column("M").to_pylist(), [0.5, NAN, 1.5, 2.5, 3.5] + [NAN] * 5)
    rows = layer.attrs.to_pylist()  # both parts of a record have its attributes
    assert [r["NAME"] for r in rows] == ["fault", "fault", "pit"]
    assert rows[0]["LEN"] == 12.5 and rows[1]["LEN"] == 12.5 and math.isnan(rows[2]["LEN"])
    assert {f["name"]: f["scope"] for f in layer.attr_schema()} == {"NAME": "feature", "LEN": "feature", "M": "vertex"}
    expected = [2 * math.sqrt(101.0), math.hypot(9.625, 9.75, 1.0), 20.0]  # 3D lengths of the three lines
    assert layer.lengths().tolist() == pytest.approx(expected, abs=1e-9)


def test_polyline_without_z_and_without_measures(tmp_path: Path) -> None:
    line = poly(23, [0], [(1, 2), (3, 4)], m=[NO_DATA, NO_DATA])  # PolyLineM whose measures are all «no data»
    layer = read_shp(write_set(tmp_path, shp_bytes(23, [line]), ids(1))).layers[0]
    assert layer.xyz[:, :2].tolist() == [[1, 2], [3, 4]] and np.isnan(layer.xyz[:, 2]).all()
    assert layer.vattrs is None and layer.closed.tolist() == [0]


def test_polygon_rings_are_closed_lines(tmp_path: Path) -> None:
    square = [(X0, Y0), (X0, Y0 + 10), (X0 + 10, Y0 + 10), (X0 + 10, Y0), (X0, Y0)]
    hole = [(2, 2), (4, 2), (4, 4)]  # the file does not repeat the first vertex
    stub = [(8, 8), (9, 9)]  # not a ring at all
    record = poly(5, [0, 5, 8], square + hole + stub)
    read = read_shp(write_set(tmp_path, shp_bytes(5, [record, poly(5, [0], square)]), ids(2)))
    layer = read.layers[0]
    assert layer.offsets.tolist() == [0, 5, 9, 11, 16]
    assert layer.closed.tolist() == [1, 1, 0, 1]
    assert layer.xyz[5:9, :2].tolist() == [[2, 2], [4, 2], [4, 4], [2, 2]]  # the first vertex is repeated
    assert np.isnan(layer.xyz[:, 2]).all()  # a 2D polygon has no Z: NaN, not 0
    assert layer.seq.tolist() == [
        0,
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        5,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
    ]  # the added one: seq of the first
    assert layer.attrs.column_names == ["ID", "кольцо"]
    assert layer.attrs.column("кольцо").to_pylist() == [1.0, 2.0, 3.0, 1.0]
    assert layer.attrs.column("ID").to_pylist() == [1.0, 1.0, 1.0, 2.0]
    assert read.warnings == ["1 колец из менее чем трёх вершин оставлены незамкнутыми"]
    assert layer.closed_count == 3 and layer.stats()["line_count"] == 4


def test_polygon_z_measures_follow_the_added_vertex(tmp_path: Path) -> None:
    record = poly(15, [0], [(0, 0), (0, 4), (3, 4)], z=[1.0, 2.0, 3.0], m=[10.0, 20.0, 30.0])
    layer = read_shp(write_set(tmp_path, shp_bytes(15, [record]))).layers[0]
    assert layer.xyz.tolist() == [[0, 0, 1], [0, 4, 2], [3, 4, 3], [0, 0, 1]] and layer.closed.tolist() == [1]
    assert layer.vattrs.column("M").to_pylist() == [10.0, 20.0, 30.0, 10.0]
    assert layer.attrs.column_names == ["кольцо"]  # no .dbf: only the ring number


def test_multipoint_gives_its_attributes_to_every_point(tmp_path: Path) -> None:
    a = poly(18, [], [(X0, Y0), (1, 2), (3, 4)], z=[10.0, 20.0, 30.0], m=[1.0, NO_DATA, 3.0])
    b = poly(18, [], [(5, 6)], z=[40.0], m=[4.0])
    dbf = dbf_bytes([(b"NAME", "C", 6, 0)], [[b"a"], [b"b"]])
    read = read_shp(write_set(tmp_path, shp_bytes(18, [a, b]), dbf))
    layer = read.layers[0]
    assert layer.points_only and read.records == 2 and layer.point_count == 4
    assert layer.xyz.tolist() == [[X0, Y0, 10.0], [1, 2, 20.0], [3, 4, 30.0], [5, 6, 40.0]]
    assert layer.attrs.column("NAME").to_pylist() == ["a", "a", "a", "b"]
    assert same(layer.attrs.column("M").to_pylist(), [1.0, NAN, 3.0, 4.0])
    plain = read_shp(write_set(tmp_path, shp_bytes(8, [poly(8, [], [(1, 2), (3, 4)])]), name="mp")).layers[0]
    assert plain.xyz[:, :2].tolist() == [[1, 2], [3, 4]] and np.isnan(plain.xyz[:, 2]).all() and plain.attrs is None


def test_records_without_geometry_deleted_records_and_broken_vertices(tmp_path: Path) -> None:
    records = [
        NULL,
        poly(13, [0], [(1, 1), (NAN, 2), (3, 3)], z=[1.0, 2.0, 3.0]),  # a vertex without X
        poly(13, [0], [(4, 4), (5, 5)], z=[4.0, 5.0]),  # deleted in the .dbf
        poly(13, [0], [(NO_DATA, 6), (7, NO_DATA)], z=[6.0, 7.0]),  # nothing is left of it
        poly(13, [0], [(8, 8), (9, 9)], z=[NO_DATA, math.inf]),  # Z «no data» and infinite: NaN
        poly(13, [0, 0], [(10, 10), (11, 11)], z=[1.0, 1.0]),  # an empty part before a real one
    ]
    read = read_shp(write_set(tmp_path, shp_bytes(13, records), dbf_bytes(
        [(b"ID", "N", 6, 0)], [[str(i + 1).encode()] for i in range(6)], deleted=(2,)
    )))  # fmt: skip
    layer = read.layers[0]
    assert read.records == 6 and layer.offsets.tolist() == [0, 2, 4, 6]
    assert layer.xyz[:2].tolist() == [[1, 1, 1], [3, 3, 3]]
    assert layer.xyz[2:4, :2].tolist() == [[8, 8], [9, 9]] and np.isnan(layer.xyz[2:4, 2]).all()
    assert layer.seq.tolist() == [0, 2, 7, 8, 9, 10]  # numbers of the vertices in the file
    assert layer.attrs.column("ID").to_pylist() == [2.0, 5.0, 6.0]
    assert read.warnings == [
        "1 записей без геометрии пропущены",
        "1 записей помечены в .dbf как удалённые: пропущены",
        "3 вершин без координаты X или Y пропущены",
    ]


def test_random_lines_against_the_generator(tmp_path: Path) -> None:
    """Hard data: random numbers of parts and vertices, huge and tiny coordinates, «no data» measures, nulls."""
    rng = np.random.default_rng(20261117)
    records: list[bytes] = []
    xyz: list[np.ndarray] = []
    ms: list[np.ndarray] = []
    counts: list[int] = []
    owner: list[int] = []
    for index in range(400):
        if rng.random() < 0.05:
            records.append(NULL)
            continue
        sizes = rng.integers(1, 9, size=int(rng.integers(1, 5)))
        n = int(sizes.sum())
        scale = 10.0 ** rng.integers(-3, 8)
        pts = rng.normal(size=(n, 3)) * scale + [X0, Y0, 300.0]
        m = np.where(rng.random(n) < 0.3, NO_DATA, rng.normal(size=n) * 1e3)
        parts = np.concatenate([[0], np.cumsum(sizes)[:-1]]).tolist()
        records.append(poly(13, parts, [tuple(p) for p in pts[:, :2].tolist()], z=pts[:, 2].tolist(), m=m.tolist()))
        xyz.append(pts)
        ms.append(np.where(m < -1e38, np.nan, m))
        counts += sizes.tolist()
        owner += [index] * len(sizes)
    read = read_shp(write_set(tmp_path, shp_bytes(13, records), ids(400)))
    layer = read.layers[0]
    assert layer.xyz.tobytes() == np.vstack(xyz).tobytes()  # tolerance: none
    assert layer.offsets.tolist() == np.concatenate([[0], np.cumsum(counts)]).tolist()
    assert layer.vattrs.column("M").to_numpy().tobytes() == np.concatenate(ms).tobytes()
    assert layer.attrs.column("ID").to_pylist() == [float(i + 1) for i in owner]
    assert layer.seq.tolist() == list(range(len(layer.xyz)))


@pytest.mark.parametrize("shape_type", [1, 21, 11])
def test_table_reading_of_points_equals_record_by_record(
    tmp_path: Path, shape_type: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = np.random.default_rng(shape_type)
    n = 5000
    values = rng.normal(size=(n, 4)) * 1e5 + [X0, Y0, 250.0, 0.0]
    values[rng.random(n) < 0.2, 3] = NO_DATA
    width = {1: 2, 21: 4, 11: 4}[shape_type]
    columns = values[:, [0, 1, 3]] if shape_type == 21 else values[:, :width]
    path = write_set(tmp_path, shp_bytes(shape_type, [point(shape_type, *row) for row in columns.tolist()]), ids(n))
    fast = read_shp(path)
    monkeypatch.setattr(shp_module, "_points_fast", lambda *args: None)
    slow = read_shp(path)
    a, b = fast.layers[0], slow.layers[0]
    assert a.xyz.tobytes() == b.xyz.tobytes() and a.seq.tolist() == b.seq.tolist()
    assert a.attrs.column_names == b.attrs.column_names == (["ID"] if shape_type == 1 else ["ID", "M"])
    for name in a.attrs.column_names:  # NaN is equal to NaN here: compared as bytes
        assert a.attrs.column(name).to_numpy().tobytes() == b.attrs.column(name).to_numpy().tobytes()
    if shape_type != 1:
        expected = np.where(values[:, 3] < -1e38, np.nan, values[:, 3])
        assert a.attrs.column("M").to_numpy().tobytes() == expected.tobytes()
    assert fast.warnings == slow.warnings == [] and a.point_count == n
    assert a.xyz[:, :2].tobytes() == np.ascontiguousarray(values[:, :2]).tobytes()


def test_points_with_a_record_without_geometry_are_read_record_by_record(tmp_path: Path) -> None:
    """11 null records are as long as 3 points: the size of the file alone does not tell them apart."""
    records = [point(1, 1.0, 2.0)] + [NULL] * 7 + [point(1, 3.0, 4.0)]
    assert (len(shp_bytes(1, records)) - 100) % 28 == 0
    read = read_shp(write_set(tmp_path, shp_bytes(1, records), ids(9)))
    assert read.layers[0].xyz[:, :2].tolist() == [[1, 2], [3, 4]] and read.records == 9
    assert read.layers[0].attrs.column("ID").to_pylist() == [1.0, 9.0]
    assert read.warnings == ["7 записей без геометрии пропущены"]


# ---------------------------------------------------------------- .dbf, code pages, .prj


def test_dbf_field_types_and_empty_values(tmp_path: Path) -> None:
    fields = [
        (b"NAME", "C", 10, 0), (b"VAL", "N", 8, 2), (b"F", "F", 12, 0), (b"D", "D", 8, 0), (b"L", "L", 1, 0),
        (b"ID", "N", 18, 0), (b"MEMO", "M", 10, 0), (b"name", "C", 3, 0), (b"CNT", "N", 18, 0),
    ]  # fmt: skip
    rows = [
        [b"ab", b"12.50", b"1.5e3", b"20261117", b"T", b"123456789012345678", b"", b"x", b"9007199254740991"],
        [b"", b"", b"", b"", b"?", b"", b"", b"", b""],
        [b"c d", b"********", b"abc", b"00000000", b"n", b"7", b"", b"y", b"7"],
        [b" lead", b"1,5", b"-0.0", b"2026xx17", b"Y", b"12", b"", b"z", b"-12"],
    ]
    shp = shp_bytes(1, [point(1, float(i), float(i)) for i in range(4)])
    read = read_shp(write_set(tmp_path, shp, dbf_bytes(fields, rows)))
    table = read.layers[0].attrs
    assert table.column_names == ["NAME", "VAL", "F", "D", "L", "ID", "name_2", "CNT"]  # names differ beyond case
    assert table.column("NAME").to_pylist() == ["ab", None, "c d", "lead"]  # blank text is null
    assert same(table.column("VAL").to_pylist(), [12.5, NAN, NAN, 1.5])  # blank and *** are NaN, never 0
    f = table.column("F").to_pylist()
    assert same(f, [1500.0, NAN, NAN, -0.0]) and math.copysign(1.0, f[3]) == -1.0
    assert table.column("D").to_pylist() == ["2026-11-17", None, None, "2026xx17"]
    assert same(table.column("L").to_pylist(), [1.0, NAN, 0.0, 1.0])
    assert table.column("ID").to_pylist() == ["123456789012345678", None, "7", "12"]  # too long for a float64
    # 18 digits wide (every 64-bit integer field of QGIS) but exact in a float64: a number
    assert same(table.column("CNT").to_pylist(), [9007199254740991.0, NAN, 7.0, -12.0])
    assert table.column("name_2").to_pylist() == ["x", None, "y", "z"]
    assert read.warnings == [
        "Поле «MEMO» типа «M» в .dbf не поддерживается: пропущено",
        "Поле «F»: 1 знач. не являются числами — прочитаны как пустые",
    ]
    assert [f.kind for f in read.header.fields] == ["C", "N", "F", "D", "L", "N", "M", "C", "N"]  # as in the file


def _cyrillic_set(tmp_path: Path, codec: str, ldid: int = 0, **more: bytes) -> Path:
    dbf = dbf_bytes([("ПРОБА".encode(codec), "C", 16, 0)], [["руда".encode(codec)], ["Ёж".encode(codec)]], ldid=ldid)
    return write_set(tmp_path, shp_bytes(1, [point(1, 1.0, 2.0), point(1, 3.0, 4.0)]), dbf, **more)


def test_code_page_from_cpg(tmp_path: Path) -> None:
    read = read_shp(_cyrillic_set(tmp_path, "utf-8", cpg=b"UTF-8\r\n"))
    assert read.encoding == "utf-8" and read.warnings == []
    assert read.layers[0].attrs.to_pylist() == [{"ПРОБА": "руда"}, {"ПРОБА": "Ёж"}]


def test_code_page_from_the_language_driver(tmp_path: Path) -> None:
    read = read_shp(_cyrillic_set(tmp_path, "cp866", ldid=0x65))
    assert read.encoding == "cp866" and read.warnings == []
    assert read.layers[0].attrs.to_pylist() == [{"ПРОБА": "руда"}, {"ПРОБА": "Ёж"}]


def test_code_page_defaults_to_windows_1251_with_a_warning(tmp_path: Path) -> None:
    read = read_shp(_cyrillic_set(tmp_path, "cp1251", ldid=0x57))  # «ANSI» says nothing
    assert read.encoding == "cp1251" and read.layers[0].attrs.to_pylist() == [{"ПРОБА": "руда"}, {"ПРОБА": "Ёж"}]
    assert read.warnings == ["Кодировка не указана (нет файла .cpg): текст прочитан как Windows-1251"]
    ascii_only = read_shp(write_set(tmp_path, shp_bytes(1, [point(1, 1.0, 2.0)]), ids(1), name="plain"))
    assert ascii_only.warnings == []  # nothing to warn about: the code page does not matter


def test_code_page_given_or_unknown(tmp_path: Path) -> None:
    path = _cyrillic_set(tmp_path, "cp866", cpg=b"KLINGON")
    read = read_shp(path)
    assert read.warnings[0] == "Кодировка «KLINGON» из файла .cpg неизвестна: текст прочитан как Windows-1251"
    given = read_shp(path, encoding="cp866")  # the choice of the user wins over the files
    assert given.encoding == "cp866" and given.layers[0].attrs.to_pylist()[0] == {"ПРОБА": "руда"}
    assert given.warnings == []
    assert error(path, encoding="no-such-codec").code == "unknown_encoding"


def test_unreadable_characters_are_reported(tmp_path: Path) -> None:
    dbf = dbf_bytes([(b"NAME", "C", 8, 0)], [[b"\xd0\xb0\xff"], [b"ok"]])
    read = read_shp(write_set(tmp_path, shp_bytes(1, [point(1, 1.0, 2.0), point(1, 3.0, 4.0)]), dbf, cpg=b"utf8"))
    assert read.layers[0].attrs.column("NAME").to_pylist() == ["а�", "ok"]
    assert read.warnings == ["Поле «NAME»: 1 знач. содержат символы, которые не читаются в кодировке utf-8"]


@pytest.mark.parametrize(
    ("label", "codec"),
    [
        ("UTF-8", "utf-8"), ("utf8", "utf-8"), ("65001", "utf-8"), ("1251", "cp1251"), ("CP1251", "cp1251"),
        ("ANSI 1251", "cp1251"), ("windows-1251", "cp1251"), ("OEM 866", "cp866"), ("ibm866", "cp866"),
        ("88595", "iso8859-5"), ("ISO 88591", "iso8859-1"), ("iso-8859-5", "iso8859-5"), ("koi8-r", "koi8-r"),
        (" 1252\r\n", "cp1252"), ("", None), ("LDID/87", None), ("кракозябры", None),
    ],
)  # fmt: skip
def test_codec_of_a_cpg(label: str, codec: str | None) -> None:
    assert codec_of(label) == codec


def test_prj_gives_the_coordinate_system(tmp_path: Path) -> None:
    from pyproj import CRS

    shp = shp_bytes(1, [point(1, X0, Y0)])
    wkt = CRS.from_epsg(32637).to_wkt("WKT1_ESRI").encode()
    read = read_shp(write_set(tmp_path, shp, prj=wkt))
    assert read.crs is not None and read.crs.epsg == 32637 and read.crs.name == "WGS 84 / UTM zone 37N"
    assert read.crs.projected and "PROJCRS" in read.crs.wkt
    assert read.warnings == ["Рядом с файлом нет таблицы .dbf: атрибуты не загружены"]
    broken = read_shp(write_set(tmp_path, shp, name="other", prj=b"PROJCS[garbage"))
    assert broken.crs is None and broken.layers[0].point_count == 1  # a warning, not an error
    assert broken.warnings[1].startswith("Система координат из файла .prj не распознана: объект загружен без СК")


def test_files_of_the_set_in_any_case_and_given_by_hand(tmp_path: Path) -> None:
    (tmp_path / "DATA.SHP").write_bytes(shp_bytes(1, [point(1, 1.0, 2.0)]))
    (tmp_path / "Data.Dbf").write_bytes(ids(1))
    assert read_shp(tmp_path / "DATA.SHP").layers[0].attrs.column("ID").to_pylist() == [1.0]
    elsewhere = tmp_path / "upload-17.bin"
    elsewhere.write_bytes(dbf_bytes([(b"K", "N", 3, 0)], [[b"42"]]))
    assert read_shp(tmp_path / "DATA.SHP", dbf=elsewhere).layers[0].attrs.to_pylist() == [{"K": 42.0}]


# ---------------------------------------------------------------- negative cases


def test_empty_and_foreign_files(tmp_path: Path) -> None:
    assert error(write_set(tmp_path, b"")).code == "empty_file"
    assert error(write_set(tmp_path, b"THIS IS MICROMINE EXTENDED DATA FILE" * 4, name="b")).code == "not_shp"
    assert error(write_set(tmp_path, shp_bytes(1, [])[:60], name="c")).code == "truncated"
    assert error(write_set(tmp_path, shp_bytes(1, []), name="d")).code == "no_lines"
    assert error(write_set(tmp_path, shp_bytes(0, [NULL, NULL]), name="e")).code == "no_lines"
    assert error(write_set(tmp_path, shp_bytes(1, [NULL, NULL]), name="f")).code == "no_lines"
    patch = error(write_set(tmp_path, shp_bytes(31, [NULL]), name="g"))
    assert patch.code == "unsupported" and "MultiPatch" in patch.message and "DXF" in patch.hint
    assert error(write_set(tmp_path, shp_bytes(77, [NULL]), name="h")).code == "broken"


def test_truncated_shp(tmp_path: Path) -> None:
    line = poly(3, [0], [(0, 0), (1, 1), (2, 2)])
    whole = shp_bytes(3, [line, line, line])
    cut = error(write_set(tmp_path, whole[:-20], ids(3)))  # in the middle of the last record
    assert cut.code == "truncated" and "запись 3" in cut.message and ".shx" in cut.hint
    assert error(write_set(tmp_path, whole[:103], ids(3), name="b")).code == "truncated"  # in a record header
    record = 8 + len(line)
    lost = error(write_set(tmp_path, whole[:-record], ids(3), name="c"))  # exactly one record is gone
    assert lost.code == "broken" and "(3)" in lost.message and "(2)" in lost.message
    alone = read_shp(write_set(tmp_path, whole[:-record], name="d"))  # without a .dbf nothing else tells
    assert alone.layers[0].line_count == 2
    assert alone.warnings[0].startswith("Длина файла в заголовке .shp (412 байт) не равна настоящей (308 байт)")
    points = shp_bytes(1, [point(1, 1.0, 2.0)] * 3)
    assert error(write_set(tmp_path, points[:-5], ids(3), name="e")).code == "truncated"


def test_no_dbf_is_a_warning_and_a_wrong_dbf_is_an_error(tmp_path: Path) -> None:
    shp = shp_bytes(1, [point(1, 1.0, 2.0), point(1, 3.0, 4.0)])
    read = read_shp(write_set(tmp_path, shp))
    assert read.layers[0].attrs is None and read.layers[0].point_count == 2
    assert read.warnings == ["Рядом с файлом нет таблицы .dbf: атрибуты не загружены"]
    more = error(write_set(tmp_path, shp, ids(3), name="b"))
    assert more.code == "broken" and "(3)" in more.message and "(2)" in more.message and ".dbf" in more.hint
    assert error(write_set(tmp_path, shp, ids(2)[:-9], name="c")).code == "truncated"
    assert error(write_set(tmp_path, shp, ids(2)[:20], name="d")).code == "truncated"
    assert error(write_set(tmp_path, shp, b"\x03" + bytes(31), name="e")).code == "broken"
    wide = dbf_bytes([(b"A", "C", 200, 0)], [[b"x"], [b"y"]])
    damaged = wide[:10] + struct.pack("<H", 50) + wide[12:]  # the record is shorter than its field
    assert error(write_set(tmp_path, shp, damaged, name="f")).code == "broken"


def test_bombs_fail_before_anything_is_allocated(tmp_path: Path) -> None:
    shp = shp_bytes(1, [point(1, 1.0, 2.0)])
    started = time.perf_counter()
    huge = error(write_set(tmp_path, shp, dbf_bytes([(b"ID", "N", 6, 0)], [[b"1"]], declared=4_000_000_000)))
    assert huge.code == "truncated" and "4 000 000 000" in huge.message
    # a record that promises two milliard vertices and parts
    lying = struct.pack("<i4d2i", 3, 0, 0, 0, 0, 2_000_000_000, 2_000_000_000) + bytes(64)
    assert error(write_set(tmp_path, shp_bytes(3, [lying]), name="b")).code == "broken"
    lying_points = struct.pack("<i4di", 8, 0, 0, 0, 0, 2_000_000_000) + bytes(32)
    assert error(write_set(tmp_path, shp_bytes(8, [lying_points]), name="c")).code == "broken"
    for k, length in enumerate((0, -5, 1, 2_000_000_000)):  # the length of a record in its header
        body = struct.pack(">2i", 1, length) + point(1, 1.0, 2.0)
        bad = error(write_set(tmp_path, shp_bytes(1, [])[:100] + body + bytes(8), name=f"d{k}"))
        assert bad.code in ("broken", "truncated")
    mixed = error(write_set(tmp_path, shp_bytes(3, [point(1, 1.0, 2.0)]), name="e"))  # a point in a line file
    assert mixed.code == "broken" and "запись 1" in mixed.message
    backwards = poly(3, [0, 5, 2], [(float(i), 0.0) for i in range(6)])
    assert error(write_set(tmp_path, shp_bytes(3, [backwards]), name="f")).code == "broken"
    line = poly(3, [0], [(0.0, 0.0), (1.0, 1.0)])
    for k, parts in enumerate(([2, 4], [0, 9], [0, -1])):  # parts that do not start at 0 or leave the object
        shifted = poly(3, parts, [(float(i), 0.0) for i in range(6)])
        assert error(write_set(tmp_path, shp_bytes(3, [line, shifted, line]), name=f"g{k}")).code == "broken"
    assert time.perf_counter() - started < 5


# ---------------------------------------------------------------- progress, cancellation, speed


@pytest.mark.windows  # the files must be free after a cancelled reading (maps, handles)
def test_progress_and_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shp_module, "CHUNK_ROWS", 4096)
    path, xyz = big_points(tmp_path, 50_000)
    seen: list[float] = []
    read = read_shp(path, progress=lambda fraction, message: seen.append(fraction))
    assert read.layers[0].xyz.tobytes() == xyz.tobytes()
    assert len(seen) > 10 and seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None) -> None:
        if fraction > 0.1:
            raise Cancelled

    with pytest.raises(Cancelled):
        read_shp(path, progress=cancel)
    path.unlink()  # nothing keeps the files open after a cancelled reading
    (tmp_path / "big.dbf").unlink()

    line = poly(3, [0], [(0.0, 0.0), (1.0, 1.0)])
    lines = write_set(tmp_path, shp_bytes(3, [line] * 40_000), name="lines")
    seen.clear()
    assert read_shp(lines, progress=lambda fraction, message: seen.append(fraction)).line_count == 40_000
    assert len(seen) > 4 and seen == sorted(seen) and seen[-1] == 1.0
    with pytest.raises(Cancelled):
        read_shp(lines, progress=cancel)


@pytest.mark.slow
def test_speed_one_million_points_with_attributes(tmp_path: Path) -> None:
    n = 1_000_000
    path, xyz = big_points(tmp_path, n)
    started = time.perf_counter()
    read = read_shp(path)
    seconds = time.perf_counter() - started
    layer = read.layers[0]
    assert layer.point_count == n and layer.xyz.tobytes() == xyz.tobytes() and read.warnings == []
    assert layer.attrs.num_rows == n and layer.attrs.column("NAME")[n - 1].as_py() == f"BH-{(n - 1) % 5000}"
    assert layer.attrs.column("AU")[n - 1].as_py() == (n - 1) * 0.001 and layer.attrs.column("ID")[7].as_py() == 7.0
    assert seconds < 10, f"1 млн точек SHP с атрибутами: {seconds:.1f} с (бюджет Д-13 — 10 с)"


@pytest.mark.slow
@pytest.mark.parametrize(("per_line", "shape_type"), [(100, 13), (4, 5)])
def test_speed_one_million_vertices_of_lines(tmp_path: Path, per_line: int, shape_type: int) -> None:
    """Long lines with Z (10 000 × 100) and many small polygons (250 000 × 4): the hard case for pyshp."""
    lines = 1_000_000 // per_line
    has_z = shape_type == 13
    rng = np.random.default_rng(11)
    xyz = rng.normal(size=(lines, per_line, 3)) * 50 + [X0, Y0, 300.0]
    if not has_z:
        xyz[:, -1] = xyz[:, 0]  # rings repeat their first vertex
    tail = [("zbox", "<f8", 2), ("z", "<f8", per_line), ("mbox", "<f8", 2), ("m", "<f8", per_line)] if has_z else []
    records = np.zeros(lines, dtype=[
        ("number", ">i4"), ("length", ">i4"), ("type", "<i4"), ("box", "<f8", 4), ("parts", "<i4"), ("points", "<i4"),
        ("start", "<i4"), ("xy", "<f8", (per_line, 2)), *tail,
    ])  # fmt: skip
    records["number"], records["length"] = np.arange(1, lines + 1), (records.itemsize - 8) // 2
    records["type"], records["parts"], records["points"], records["xy"] = shape_type, 1, per_line, xyz[:, :, :2]
    if has_z:
        records["z"], records["m"] = xyz[:, :, 2], NO_DATA
    path = tmp_path / "lines.shp"
    path.write_bytes(shp_bytes(shape_type, [], declared=(100 + records.nbytes) // 2) + records.tobytes())
    table = np.zeros(lines, dtype=[("del", "S1"), ("ID", "S8")])
    table["del"], table["ID"] = b" ", np.char.rjust(np.char.mod(b"%d", np.arange(lines)), 8)
    (tmp_path / "lines.dbf").write_bytes(
        dbf_bytes([(b"ID", "N", 8, 0)], [], declared=lines)[:-1] + table.tobytes() + b"\x1a"
    )
    started = time.perf_counter()
    read = read_shp(path)
    seconds = time.perf_counter() - started
    layer = read.layers[0]
    assert layer.point_count == 1_000_000 and layer.line_count == lines and read.warnings == []
    assert layer.xyz[:, :2].tobytes() == np.ascontiguousarray(xyz[:, :, :2]).tobytes()
    assert layer.closed_count == (0 if has_z else lines)
    assert layer.attrs.column("ID")[lines - 1].as_py() == float(lines - 1)
    assert seconds < 10, f"1 млн вершин SHP ({lines} линий): {seconds:.1f} с (бюджет Д-13 — 10 с)"
