"""Micromine TRIDB reading (ТЗ 7.21, 9.4, 9.12; ADR 0013 §2).

Reference values. The real files of `tests/data/real` (66 wireframes / 3 590 triangles and 55 / 128 994) are
decoded here a second time, independently: the whole BLOB in memory and `numpy.frombuffer` at the offsets of ТЗ
9.4 — every vertex and triangle must be the same bit for bit, in the order of the file. `Volume` and
`SurfaceArea` of the file are compared with `geo.meshes.mesh_stats` to 1e-9 (relative; measured 1.4e-13 and
2.9e-16). Synthetic files come from `tridb_builder` (independent of the writer); a cube 10 × 10 × 10 has the
volume 1000 (1e-12). Negative cases of ТЗ 9.12: an empty, a cut and a broken file, another version, a broken
encoding, «bombs» (lengths and counts a file only declares).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats import tridb, tridb_blob
from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshEntry, MeshSet, name_in_file
from bf_core.formats.tridb import check_measures, declared, inspect_tridb, open_tridb, read_tridb
from bf_core.geo.meshes import mesh_stats
from tests.formats import tridb_builder as tb
from tests.test_viz_mesh import cube, grid, hills

pytestmark = pytest.mark.windows

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real"
REAL_FILES = [("ORE SEP 2025.tridb", 66, 1925, 3590), ("Rock Model.tridb", 55, 64_991, 128_994)]
RU = "абвгдежзийклмнопрстуфхцчшщъыьэюя"


def fails(fn: Any, *args: Any, **kw: Any) -> FormatError:
    with pytest.raises(FormatError) as info:
        out = fn(*args, **kw)
        if hasattr(out, "__next__"):
            list(out)
    err = info.value
    assert err.message and err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    assert any(ch in RU for ch in err.message.lower())  # Russian text for the user
    return err


def decode(blob: bytes) -> tuple[str, np.ndarray, np.ndarray]:
    """The reference decoder of a geometry BLOB (ТЗ 9.4): fixed offsets, the whole BLOB in memory."""
    assert blob[:8] == b"\xff\xff\xff\xff\x00\x00\x05\x00" and struct.unpack_from("<qq", blob, 8) == (36, 9)
    pos, data = 60, {}
    while pos < len(blob):
        length, number = struct.unpack_from("<qq", blob, pos)
        data[number] = blob[pos + 16 : pos + 16 + length]
        pos += 16 + length
    assert pos == len(blob) and sorted(data) == [1, 2, 3, 4]
    xyz = np.column_stack([np.frombuffer(data[k], dtype="<f8") for k in (1, 2, 3)])
    return blob[24:60].decode("ascii"), xyz, np.frombuffer(data[4], dtype="<i4").reshape(-1, 3)


def hex_colour(bgr: int) -> str:
    return f"#{bgr & 255:02x}{bgr >> 8 & 255:02x}{bgr >> 16 & 255:02x}"


def box(v: np.ndarray, t: np.ndarray, **more: Any) -> dict[str, Any]:
    return {"xyz": v, "tri": t, **more}


# ---------------------------------------------------------------- real files


@pytest.mark.parametrize(("name", "count", "points", "triangles"), REAL_FILES)
def test_real_files_bit_for_bit(name: str, count: int, points: int, triangles: int) -> None:
    conn = sqlite3.connect(REAL / name)
    rows = conn.execute(
        "SELECT g.ID, i.Name, i.Colour, g.Geometry FROM Geometry g JOIN GeneralInformation i ON i.ID = g.ID "
        "ORDER BY g.ID"
    ).fetchall()
    defs = [r[0] for r in conn.execute("SELECT Name FROM UserAttributeDefinition ORDER BY ID")]
    conn.close()
    entries, warnings = inspect_tridb(REAL / name)
    assert warnings == [] and len(entries) == len(rows) == count
    meshes = list(read_tridb(REAL / name))
    assert sum(m.point_count for m in meshes) == points and sum(m.triangle_count for m in meshes) == triangles
    for entry, mesh, (wf_id, wf_name, colour, blob) in zip(entries, meshes, rows, strict=True):
        guid, xyz, tri = decode(blob)
        assert (entry.key, entry.name, entry.color) == (wf_id, wf_name, hex_colour(colour))
        assert (entry.points, entry.triangles, entry.error, entry.warnings) == (len(xyz), len(tri), None, [])
        assert entry.extent == mesh.extent()  # the extent the file declares is the extent of its geometry
        assert (mesh.name, mesh.color, mesh.warnings) == (wf_name, entry.color, [])
        assert mesh.xyz.tobytes() == xyz.tobytes() and mesh.tri.tobytes() == tri.tobytes()
        assert mesh.xyz.dtype == np.float64 and mesh.tri.dtype == np.int32
        assert mesh.attrs == entry.attrs == [{"name": d, "value": None} for d in defs]  # every text is empty
        src = mesh.source
        assert (src["format"], src["name"], name_in_file(mesh)) == ("tridb", wf_name, wf_name) and "as" not in src
        assert (src["tridb"]["id"], src["tridb"]["guid"]) == (wf_id, guid)
        assert (src["tridb"]["points"], src["tridb"]["triangles"]) == (len(xyz), len(tri))
    assert len({m.source["tridb"]["guid"] for m in meshes}) == count


@pytest.mark.parametrize(("name", "volumes", "areas"), [("ORE SEP 2025.tridb", 66, 66), ("Rock Model.tridb", 12, 21)])
def test_real_files_volume_and_area(name: str, volumes: int, areas: int) -> None:
    compared = {"volume": 0, "area": 0}
    worst = {"volume": 0.0, "area": 0.0}
    for mesh in read_tridb(REAL / name):
        stats = mesh_stats(mesh.xyz, mesh.tri)
        file = declared(mesh.source)
        assert check_measures(mesh.source, stats) == []
        for key in compared:
            if stats[key] is not None and file[key] is not None:
                compared[key] += 1
                worst[key] = max(worst[key], abs(stats[key] - file[key]) / abs(file[key]))
    assert compared == {"volume": volumes, "area": areas}  # closed solids with a volume in the file; every area
    assert worst["volume"] <= 1e-9 and worst["area"] <= 1e-9  # ТЗ 9.12
    assert worst["volume"] < 1e-12 and worst["area"] < 1e-14  # measured: 1.4e-13 and 2.9e-16


def test_source_rows_of_a_real_file() -> None:
    conn = sqlite3.connect(REAL / "Rock Model.tridb")
    general = conn.execute("SELECT * FROM GeneralInformation WHERE ID = 55").fetchone()
    props = conn.execute(
        "SELECT rowid, Property, TextValue, RealValue FROM GeometryProperties WHERE TriangulationID = 55 ORDER BY rowid"
    ).fetchall()
    defs = conn.execute("SELECT * FROM UserAttributeDefinition ORDER BY ID").fetchall()
    values = conn.execute(
        "SELECT rowid, AttributeID, ValueText, ValueInteger, ValueReal FROM UserAttributeValue "
        "WHERE TriangulationID = 55 ORDER BY rowid"
    ).fetchall()
    conn.close()
    (mesh,) = read_tridb(REAL / "Rock Model.tridb", keys=[55])
    t = mesh.source["tridb"]
    assert list(t["general"]) == [c for c in tridb.GENERAL if c not in ("ID", "Name")]
    assert [mesh.source["name"], *t["general"].values()] == list(general[1:])
    assert [tuple(r) for r in t["properties"]] == props and len(props) == 11  # PointCount three times
    assert [tuple(r) for r in t["attr_defs"]] == defs and len(defs) == 11
    assert [tuple(r) for r in t["attr_values"]] == values and [v[1] for v in values][:3] == [8, 3, 4]
    assert "display" not in t and "database" not in t  # the tables are empty in the real files
    assert declared(mesh.source) == {"volume": 218167.8776134904, "area": 143628.5509995077}
    (open_solid,) = read_tridb(REAL / "Rock Model.tridb", keys=[1])  # Volume and SurfaceArea are NULL
    assert declared(open_solid.source) == {"volume": None, "area": None}


def test_the_file_is_only_read(tmp_path: Path) -> None:
    folder = tmp_path / ("Каркасы #1 100% & (копия)" + ("" if os.name == "nt" else " ?"))  # «?» is not a Windows name
    folder.mkdir()
    path = folder / "Рудные тела & co.tridb"
    shutil.copyfile(REAL / "ORE SEP 2025.tridb", path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(list(read_tridb(path))) == 66
    assert [p.name for p in folder.iterdir()] == [path.name]  # no journal, no lock file
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    path.unlink()  # the file is closed


# ---------------------------------------------------------------- synthetic files


def test_cube_names_colours_and_attributes(tmp_path: Path) -> None:
    v, t = cube(10, 10.0)
    gv, gt = grid(30, 20, hills)
    defs = [(1, "UNIT", 0, 2, "", 0, 0.0), (2, "Плотность", 2, 3, "", 0, 0.0), (3, "Блок", 1, 0, "", 0, 0.0)]
    defs += [(4, "Пусто", 0, 2, "", 0, 0.0), (5, " unit ", 0, 2, "", 0, 0.0)]
    path = tb.build(
        tmp_path / "куб.tridb",
        [
            box(v, t, id=3, name="Куб 10 м 🧊", general={"Colour": 0x0000FF, "Volume": 1000.0, "SurfaceArea": 600.0},
                attrs=[(2, "", None, 2.75), (1, " Руда ", None, None), (4, "", None, None), (3, None, 12, None),
                       (9, "x", None, None)]),
            box(gv, gt, id=7, name=None, general={"Colour": 0xFF8040}, attrs=[(5, "да", None, None)]),
        ],
        defs=defs,
    )  # fmt: skip
    entries, warnings = inspect_tridb(path)
    assert [(e.key, e.name, e.color, e.points, e.triangles) for e in entries] == [
        (3, "Куб 10 м 🧊", "#ff0000", len(v), len(t)),  # 0x00BBGGRR
        (7, "Каркас 7", "#4080ff", len(gv), len(gt)),
    ]
    assert len(warnings) == 2 and "1, 2" in warnings[0] and "без определения" in warnings[1]
    a, b = read_tridb(path)
    assert a.attrs == [  # in the order of the definitions; yes/no and numbers keep their type, empty is None
        {"name": "UNIT", "value": "Руда"},
        {"name": "Плотность", "value": 2.75},
        {"name": "Блок", "value": 12},
        {"name": "Пусто", "value": None},
    ]
    assert b.attrs == [{"name": "unit", "value": "да"}] and b.name == "Каркас 7" and b.color == "#4080ff"
    assert (b.source["name"], b.source["as"], name_in_file(b)) == (None, "Каркас 7", None)
    stats = mesh_stats(a.xyz, a.tri)
    assert stats["closed"] and stats["volume"] == pytest.approx(1000.0, rel=1e-12)
    assert stats["area"] == pytest.approx(600.0, rel=1e-12) and check_measures(a.source, stats) == []
    assert a.xyz.tobytes() == np.ascontiguousarray(v).tobytes() and a.tri.tobytes() == t.tobytes()
    assert a.warnings[:2] == warnings  # the warnings of the file come with the first wireframe
    wrong = dict(a.source, tridb={"general": {"Volume": 1000.0 * (1 + 3e-9), "SurfaceArea": 600.0 * (1 - 3e-9)}})
    notes = check_measures(wrong, stats)
    assert len(notes) == 2 and "Объём" in notes[0] and "Площадь" in notes[1]
    assert check_measures(dict(a.source, tridb={"general": {"Volume": -1.0, "SurfaceArea": None}}), stats) == []


def test_source_keeps_rows_the_writer_needs(tmp_path: Path) -> None:
    v, t = cube(2, 10.0)
    other = "11111111-2222-3333-4444-555555555555"
    props = [("PointCount", None, float(len(v))), ("TriangleCount", None, float(len(t))), ("Примечание", "текст", None)]
    script = (
        f"INSERT INTO GeometryProperties VALUES (5, '{other}', 'Volume', NULL, 999.0);"
        "INSERT INTO DisplayProperties VALUES (5, 'штрих', 'контур', 2, 1.5);"
        "INSERT INTO DatabaseInformation VALUES (3, 1700000000, 'Проект');"
        "INSERT INTO PointAttributeValue VALUES (5, 1, X'00');"
        "CREATE TABLE Extra (a);"
        "ALTER TABLE GeneralInformation ADD COLUMN Future TEXT;"
    )
    path = tb.build(tmp_path / "s.tridb", [box(v, t, id=5, name="Тело", props=props)], script=script)
    entries, warnings = inspect_tridb(path)
    assert len(warnings) == 2 and "Extra" in warnings[0] and "Future" in warnings[1]
    assert entries[0].warnings == ["Атрибуты точек и треугольников каркаса не загружаются"]
    (mesh,) = read_tridb(path)
    t_src = mesh.source["tridb"]
    assert t_src["properties"] == [
        [1, "PointCount", None, float(len(v))],
        [2, "TriangleCount", None, float(len(t))],
        [3, "Примечание", "текст", None],
        [4, "Volume", None, 999.0, other],  # a row of another GUID keeps its GUID
    ]
    assert t_src["display"] == ["штрих", "контур", 2, 1.5] and t_src["database"] == [[3, 1700000000, "Проект"]]
    assert t_src["attr_defs"] == [] and t_src["attr_values"] == []


def test_one_broken_wireframe_does_not_lose_the_others(tmp_path: Path) -> None:
    v, t = cube(3, 10.0)
    bad = t.copy()
    bad[4, 2] = len(v)
    nan = v.copy()
    nan[5, 1] = np.nan
    path = tb.build(
        tmp_path / "m.tridb",
        [
            box(v, t, id=1, name="Целый"),
            box(v, bad, id=2, name="Номера"),
            box(nan, t, id=3, name="Пропуск", general={"YMinimum": 0.0}),
            box(v, t, id=4, name="Без геометрии", blob=None),
            box(v, np.zeros((0, 3), dtype=np.int32), id=5, name="Без треугольников"),
            box(v, t, id=6, name="Не BLOB", blob="text"),
            box(v, t, id=7, name="Чужой", blob=b"\x00" * 200),
            box(v, t, id=8, name=None, general=None),
            box(v, t, id=9, name="Последний"),
        ],
    )
    with open_tridb(path) as f:
        by_name = {e.name: e for e in f.entries}
        assert list(by_name) == [
            "Целый", "Номера", "Пропуск", "Без треугольников", "Не BLOB", "Чужой", "Каркас 8", "Последний",
            "Без геометрии",
        ]  # fmt: skip
        assert {n for n, e in by_name.items() if e.error} == {"Без геометрии", "Не BLOB", "Чужой"}
        assert by_name["Без геометрии"].error["code"] == "broken"  # type: ignore[index]
        codes = {}
        for entry in f.entries:
            try:
                mesh = f.read(entry)
                codes[entry.name] = "ok"
                assert mesh.xyz.tobytes() == np.ascontiguousarray(v).tobytes() and np.array_equal(mesh.tri, t)
            except FormatError as exc:
                codes[entry.name] = exc.code
                assert f"«{entry.name}»" in exc.message and exc.hint
        assert codes == {
            "Целый": "ok", "Номера": "bad_mesh", "Пропуск": "bad_coordinates", "Без треугольников": "empty_mesh",
            "Не BLOB": "broken", "Чужой": "broken", "Каркас 8": "ok", "Последний": "ok", "Без геометрии": "broken",
        }  # fmt: skip
        assert f.read(9).name == "Последний" and fails(f.read, 77).code == "broken"
        assert fails(f.read, MeshEntry(77, "Чужая запись", 1, 1)).code == "broken"
    assert fails(read_tridb, path).code == "bad_mesh"  # the strict reading stops at the first one
    assert [m.name for m in read_tridb(path, keys=[9, 1, 8])] == ["Целый", "Каркас 8", "Последний"]


def test_counts_and_extent_are_compared_with_the_geometry(tmp_path: Path) -> None:
    v, t = cube(2, 10.0)
    props = [("PointCount", None, 1e15), ("TriangleCount", None, float(len(t)))]  # a count the file only declares
    path = tb.build(
        tmp_path / "c.tridb",
        [box(v, t, id=1, name="Счёт", props=props), box(v, t, id=2, name="Охват", general={"ZMaximum": 1e6})],
    )
    a, b = read_tridb(path)
    assert (
        len(a.warnings) == 1 and "1 000 000 000 000 000 вершин" in a.warnings[0] and "взята геометрия" in a.warnings[0]
    )
    assert len(b.warnings) == 1 and "Охват" in b.warnings[0]
    assert a.point_count == b.point_count == len(v)
    # an older file: a column per property
    wide = [s for s in tb.DDL if "GeometryProperties" not in s]
    wide.append("CREATE TABLE GeometryProperties (ID INTEGER, PointCount INTEGER, TriangleCount INTEGER, Volume REAL)")
    script = f"INSERT INTO GeometryProperties VALUES (1, {len(v)}, {len(t)}, 1000.0), (2, {len(v) + 1}, {len(t)}, 1.0)"
    path = tb.build(
        tmp_path / "w.tridb", [box(v, t, id=k, name=f"К{k}", props=[]) for k in (1, 2)], ddl=wide, script=script
    )
    a, b = read_tridb(path)
    assert a.warnings == [] and len(b.warnings) == 1 and "взята геометрия" in b.warnings[0]
    assert "properties" not in a.source["tridb"]


# ---------------------------------------------------------------- the geometry BLOB


def blob_case(tmp_path: Path, blob: bytes, **general: Any) -> Path:
    v, t = cube(2, 10.0)
    return tb.build(tmp_path / "b.tridb", [box(v, t, id=1, name="Тело", blob=blob, general=general)])


def test_blob_structure_is_checked(tmp_path: Path) -> None:
    v, t = cube(2, 10.0)
    good = tb.geometry(v, t)
    x, y, z = (np.ascontiguousarray(v[:, k]).tobytes() for k in range(3))
    head, tt = good[:60], t.tobytes()
    cases = {
        "magic": (tb.geometry(v, t, magic=b"\xfe\xff\xff\xff"), "broken"),
        "short": (good[:20], "broken"),
        "cut in the head of a block": (good[:70], "truncated"),
        "cut in half": (good[: len(good) // 2], "truncated"),
        "one byte short": (good[:-1], "truncated"),
        "length bomb": (head + struct.pack("<qq", 1 << 62, 1) + x, "truncated"),
        "negative length": (head + struct.pack("<qq", -8, 1) + x, "truncated"),
        "guid bomb": (good[:8] + struct.pack("<qq", 1 << 40, 9) + good[24:], "broken"),
        "no triangles block": (head + tb.block(1, x) + tb.block(2, y) + tb.block(3, z), "broken"),
        "x shorter": (head + tb.block(1, x[:-8]) + tb.block(2, y) + tb.block(3, z) + tb.block(4, tt), "broken"),
        "odd triangles": (head + tb.block(1, x) + tb.block(2, y) + tb.block(3, z) + tb.block(4, tt[:-4]), "broken"),
        "block twice": (good + tb.block(4, tt), "broken"),
    }  # fmt: skip
    for what, (blob, code) in cases.items():
        path = blob_case(tmp_path, blob)
        entries, _warnings = inspect_tridb(path)  # the composition does not fail: the wireframe does
        err = fails(read_tridb, path)
        assert err.code == code, what
        assert "«Тело»" in err.message, what
        assert entries[0].name == "Тело", what


def test_extra_blocks_and_other_versions(tmp_path: Path) -> None:
    v, t = cube(2, 10.0)
    (mesh,) = read_tridb(blob_case(tmp_path, tb.geometry(v, t, extra=[(7, b"\x01" * 40)])))
    assert len(mesh.warnings) == 1 and "№ 7" in mesh.warnings[0] and np.array_equal(mesh.tri, t)
    # a version that was not seen: taken with a warning when every check holds…
    newer = tb.geometry(v, t, version=b"\x00\x00\x06\x00")
    (mesh,) = read_tridb(blob_case(tmp_path, newer))
    assert len(mesh.warnings) == 1 and "Версия геометрии TRIDB 6" in mesh.warnings[0]
    assert mesh.xyz.tobytes() == np.ascontiguousarray(v).tobytes()
    (mesh,) = read_tridb(blob_case(tmp_path, tb.geometry(v, t, tag=10)))
    assert len(mesh.warnings) == 1 and "Версия геометрии" in mesh.warnings[0]
    # …and refused when anything is off: its layout may be another
    err = fails(read_tridb, blob_case(tmp_path, newer, XMinimum=0.0))
    assert err.code == "unsupported" and "версия геометрии TRIDB 6" in err.message and "Micromine" in err.hint
    assert fails(read_tridb, blob_case(tmp_path, newer[:-1])).code == "unsupported"
    # nothing to compare with (the file states neither the counts nor the extent) is not an agreement
    silent = tb.build(tmp_path / "silent.tridb", [box(v, t, id=1, name="Тело", blob=newer, props=[])])
    assert fails(read_tridb, silent).code == "unsupported"
    no_extent = blob_case(tmp_path, newer, XMinimum=None)
    assert fails(read_tridb, no_extent).code == "unsupported"
    (mesh,) = read_tridb(blob_case(tmp_path, tb.geometry(v, t), XMinimum=None, ZMaximum=None))  # version 5: no remark
    assert mesh.warnings == [] and "as" not in mesh.source
    assert fails(
        read_tridb, blob_case(tmp_path, tb.geometry(v, t, version=b"\x00\x00\x04\x00", extra=[(9, b"")]))
    ).code == ("unsupported")


def test_layout_of_a_blob_in_a_file(tmp_path: Path) -> None:
    """`blob_layout` reads any binary file: nothing but seeks and the 16 bytes of every block header."""
    v, t = grid(40, 30, hills)
    raw = tb.geometry(v, t)
    path = tmp_path / "g.bin"
    path.write_bytes(raw)
    with path.open("rb") as f:
        layout = tridb_blob.blob_layout(f, len(raw))
    assert (layout.points, layout.triangles, layout.guid, layout.standard) == (len(v), len(t), tb.GUID, True)
    assert layout.blocks[4] == (len(raw) - 12 * len(t), 12 * len(t))
    assert tridb_blob.blob_size(len(v), len(t)) == len(raw)
    assert tridb_blob.blob_head(tb.GUID) == raw[:60] and tridb_blob.block_head(1, 8 * len(v)) == raw[60:76]


# ---------------------------------------------------------------- broken files


def test_not_a_tridb(tmp_path: Path) -> None:
    empty = tmp_path / "empty.tridb"
    empty.write_bytes(b"")
    assert fails(inspect_tridb, empty).code == "empty_file"
    text = tmp_path / "text.tridb"
    text.write_bytes("Это не база данных, а текст.\n".encode() * 300)
    assert fails(inspect_tridb, text).code == "not_tridb"
    short = tmp_path / "short.tridb"
    short.write_bytes(b"SQLite format 3\x00\x10\x00")
    assert fails(inspect_tridb, short).code == "truncated"
    other = tmp_path / "other.tridb"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE Holes (id INTEGER, name TEXT)")
    conn.commit()
    conn.close()
    assert fails(inspect_tridb, other).code == "not_tridb"
    none = tb.build(tmp_path / "none.tridb", [])
    assert fails(inspect_tridb, none).code == "no_meshes"
    # a view (or a virtual table) with the name of a table is not read: only real tables are
    v, t = cube(2, 10.0)
    ddl = [s for s in tb.DDL if not s.startswith("CREATE TABLE Geometry ")]
    ddl.append("CREATE TABLE Hidden (ID INTEGER, Geometry BLOB)")
    ddl.append("CREATE VIEW Geometry AS SELECT ID, Geometry FROM Hidden")
    view = tb.build(tmp_path / "view.tridb", [box(v, t, id=1, name="Тело", blob=None)], ddl=ddl)
    assert fails(inspect_tridb, view).code == "not_tridb"


@pytest.mark.parametrize("keep", [100, 4095, 4096, 40_000, 262_143])
def test_cut_file(tmp_path: Path, keep: int) -> None:
    data = (REAL / "ORE SEP 2025.tridb").read_bytes()
    path = tmp_path / "cut.tridb"
    path.write_bytes(data[:keep])
    err = fails(read_tridb, path)
    assert err.code == "truncated" and "обрезан" in err.message


def test_garbage_inside_the_file_is_an_error_or_a_warning(tmp_path: Path) -> None:
    """Pages overwritten with noise: the reader answers with FormatError (the file or one wireframe) or reads
    what is left; it never fails with anything else and never hangs."""
    data = bytearray((REAL / "ORE SEP 2025.tridb").read_bytes())
    rng = np.random.default_rng(5)
    outcomes = {"file": 0, "wireframes": 0, "read": 0}
    for trial in range(60):
        broken = bytearray(data)
        at = int(rng.integers(100, len(data) - 4096))
        size = int(rng.choice([8, 512, 4096]))
        broken[at : at + size] = rng.integers(0, 256, size, dtype=np.uint8).tobytes()
        path = tmp_path / f"g{trial}.tridb"
        path.write_bytes(bytes(broken))
        try:
            with open_tridb(path) as f:
                for entry in f.entries:
                    try:
                        mesh = f.read(entry)
                        assert isinstance(mesh, MeshSet) and np.isfinite(mesh.xyz).all()
                        outcomes["read"] += 1
                    except FormatError as exc:
                        assert exc.message and exc.hint
                        outcomes["wireframes"] += 1
        except FormatError as exc:
            assert exc.code in ("broken", "truncated", "not_tridb", "no_meshes") and exc.hint
            outcomes["file"] += 1
        path.unlink()
    assert outcomes["read"] > 500 and outcomes["file"] + outcomes["wireframes"] > 0, outcomes


def test_broken_encoding_does_not_stop_the_reading(tmp_path: Path) -> None:
    v, t = cube(2, 10.0)
    script = "UPDATE GeneralInformation SET Name = CAST(X'D0A0D183FF64D0B0' AS TEXT), Code = CAST(X'FFFE' AS TEXT)"
    path = tb.build(tmp_path / "enc.tridb", [box(v, t, id=1, name="x")], encoding="UTF-8", script=script)
    (mesh,) = read_tridb(path)
    assert mesh.name == "Ру�dа" and mesh.source["name"] == "Ру�dа" and np.array_equal(mesh.tri, t)
    # UTF-16 with a lone surrogate: SQLite gives it as it can, the reader does not fail
    script = "UPDATE GeneralInformation SET Name = CAST(X'1204D83D4004' AS TEXT)"
    path = tb.build(tmp_path / "enc16.tridb", [box(v, t, id=1, name="x")], script=script)
    (mesh,) = read_tridb(path)
    assert mesh.name.startswith("В") and len(mesh.name) >= 2 and mesh.xyz.tobytes() == np.ascontiguousarray(v).tobytes()


# ---------------------------------------------------------------- bombs


def test_limits_bound_what_a_file_can_make_us_keep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    v, t = cube(2, 10.0)
    defs = [(k, f"A{k}", 0, 2, "", 0, 0.0) for k in range(1, 251)]
    attrs = [(k, f"значение {k}", None, None) for k in range(1, 251)]
    props = [("PointCount", None, float(len(v)))] * 80
    path = tb.build(
        tmp_path / "many.tridb",
        [box(v, t, id=k, name=f"Тело {k}", attrs=attrs, props=props if k == 1 else []) for k in (1, 2, 3, 4, 5)],
        defs=defs,
    )
    meshes = list(read_tridb(path))
    assert all(len(m.attrs) == 200 for m in meshes)  # the limit of the store; the rest is named in a warning
    assert all(any("50 не загружено" in w for w in m.warnings) for m in meshes)
    assert "properties" not in meshes[0].source["tridb"] and meshes[1].source["tridb"]["properties"] == []
    assert len(meshes[0].source["tridb"]["attr_values"]) == 250  # the rows stay for the writer
    monkeypatch.setattr(tridb, "MAX_SOURCE_BYTES", 2000)
    trimmed = next(read_tridb(path))
    assert set(trimmed.source["tridb"]) == {"id", "guid", "points", "triangles", "general"}
    assert any("сохранены не полностью" in w for w in trimmed.warnings)
    monkeypatch.setattr(tridb, "MAX_SOURCE_BYTES", 300)
    assert set(next(read_tridb(path)).source["tridb"]) == {"id", "guid", "points", "triangles"}
    monkeypatch.setattr(tridb, "MAX_VALUES", 1000)
    entries, warnings = inspect_tridb(path)
    assert all(e.attrs == [] for e in entries) and any("атрибуты не загружены" in w for w in warnings)
    monkeypatch.setattr(tridb, "MAX_DEFS", 100)
    entries, warnings = inspect_tridb(path)
    assert all(e.attrs == [] for e in entries) and any("Определений атрибутов больше 100" in w for w in warnings)
    monkeypatch.setattr(tridb, "MAX_MESHES", 4)
    err = fails(inspect_tridb, path)
    assert err.code == "too_large" and "больше 4 каркасов" in err.message


# ---------------------------------------------------------------- progress and cancellation


class Cancelled(Exception):
    pass


def test_progress_and_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tridb_blob, "READ", 4096)
    v, t = grid(120, 100, hills)
    path = tb.build(tmp_path / "p.tridb", [box(v, t, id=k, name=f"Рельеф {k}") for k in (1, 2, 3)])
    seen: list[tuple[float, str | None]] = []
    meshes = list(read_tridb(path, progress=lambda f, m=None: seen.append((f, m))))
    fractions = [f for f, _m in seen]
    assert len(meshes) == 3 and fractions == sorted(fractions) and fractions[0] == 0.0 and fractions[-1] == 1.0
    assert len(seen) > 300 and any(m and "Рельеф 2" in m for _f, m in seen)

    def cancel_at(limit: float) -> Any:
        def progress(fraction: float, _message: str | None = None) -> None:
            if fraction >= limit:
                raise Cancelled

        return progress

    for limit in (0.0, 0.02, 0.3, 0.99):
        with pytest.raises(Cancelled):
            list(read_tridb(path, progress=cancel_at(limit)))
    # a cancel while SQLite works on a statement stops the statement and comes out as itself
    monkeypatch.setattr(tridb, "STEPS", 1)
    calls = {"n": 0}

    def late(_fraction: float, _message: str | None = None) -> None:
        calls["n"] += 1
        if calls["n"] > 3:
            raise Cancelled

    with pytest.raises(Cancelled):
        inspect_tridb(path, late)
    assert len(list(read_tridb(path))) == 3  # the file is not left locked or changed
    path.unlink()


def test_journal_next_to_the_file_is_reported(tmp_path: Path) -> None:
    v, t = cube(2, 10.0)
    path = tb.build(tmp_path / "j.tridb", [box(v, t, id=1, name="Тело")])
    Path(str(path) + "-journal").write_bytes(b"\x00" * 512)
    entries, warnings = inspect_tridb(path)
    assert len(entries) == 1 and len(warnings) == 1 and "-journal" in warnings[0]
    assert Path(str(path) + "-journal").read_bytes() == b"\x00" * 512  # left alone
