"""Micromine TRIDB writing (ТЗ 9.4, 9.12; ADR 0013 §2).

Reference values. The real files of `tests/data/real` are read and written again: every table has the same
rows (with their rowids and the types of the values) and every geometry BLOB is the same byte for byte; the
statements of the file, UTF-16LE and the page size are those of the real files; `PRAGMA integrity_check` is ok;
a second export is the first one byte for byte. New wireframes are checked through a decoder of the BLOB that
does not share code with the writer (`test_tridb.decode`) and through plain SQL: a cube 10 × 10 × 10 is written
with the volume 1000 and the area 600 (1e-12), a surface — with no volume (NULL, never 0 or −1). Tolerance of
the geometry: none.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats import tridb_write
from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshSet
from bf_core.formats.tridb import read_tridb
from bf_core.formats.tridb_write import write_tridb
from bf_core.geo.meshes import mesh_stats
from tests.formats import tridb_builder as tb
from tests.formats.test_tridb import REAL, decode
from tests.formats.tridb_builder import schema, table_rows
from tests.test_viz_mesh import cube, grid, hills

pytestmark = pytest.mark.windows

NOW = 1_795_000_000


def sql(path: Path, query: str, *args: Any) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(query, args).fetchall()
    finally:
        conn.close()


def same_meshes(a: MeshSet, b: MeshSet) -> None:
    assert a.xyz.tobytes() == b.xyz.tobytes() and a.tri.tobytes() == b.tri.tobytes()
    assert (a.name, a.color, a.attrs, a.source, a.warnings) == (b.name, b.color, b.attrs, b.source, b.warnings)


# ---------------------------------------------------------------- real files


@pytest.mark.parametrize(
    ("name", "count", "triangles"), [("ORE SEP 2025.tridb", 66, 3590), ("Rock Model.tridb", 55, 128_994)]
)
def test_real_cycle_blobs_byte_for_byte_and_equal_rows(tmp_path: Path, name: str, count: int, triangles: int) -> None:
    source = REAL / name
    out = tmp_path / "Экспорт" / name
    out.parent.mkdir()
    written = write_tridb(out, read_tridb(source), total=count)
    assert (written.meshes, written.triangles, written.warnings) == (count, triangles, [])
    assert written.path == out and written.size == out.stat().st_size
    assert [p.name for p in out.parent.iterdir()] == [name]  # no `.part`, no journal
    before, after = table_rows(source), table_rows(out)
    assert list(after) == list(before)
    for table in before:
        assert after[table] == before[table], table
    blobs = sql(source, "SELECT ID, Geometry FROM Geometry ORDER BY ID")
    assert sql(out, "SELECT ID, Geometry FROM Geometry ORDER BY ID") == blobs and len(blobs) == count
    made, real = schema(out), schema(source)
    assert made["master"] == real["master"]  # the same CREATE statements, in their order
    assert (made["encoding"], made["page_size"]) == (real["encoding"], real["page_size"]) == ("UTF-16le", 4096)
    assert made["integrity"] == ["ok"] and made["foreign_keys"] == []
    # import → export → import: the same wireframes bit for bit; a second export is the first one
    first, second = list(read_tridb(source)), list(read_tridb(out))
    assert len(first) == len(second) == count
    for a, b in zip(first, second, strict=True):
        same_meshes(a, b)
    again = tmp_path / "again.tridb"
    write_tridb(again, second)
    assert again.read_bytes() == out.read_bytes()


# ---------------------------------------------------------------- new wireframes


def new_meshes() -> list[MeshSet]:
    v, t = cube(10, 10.0)
    gv, gt = grid(60, 40, hills)
    return [
        MeshSet("Куб 10 м «руда» 🧊", v, t, color="#ff8000", attrs=[{"name": "Сорт", "value": "Богатая"}]),
        MeshSet("Рельеф", gv, gt),
    ]


def test_new_wireframes_are_written_as_micromine_writes_them(tmp_path: Path) -> None:
    meshes = new_meshes()
    out = tmp_path / "новые каркасы.tridb"
    written = write_tridb(out, meshes, now=NOW, author="Геолог")
    assert (written.meshes, written.points, written.triangles) == (2, sum(m.point_count for m in meshes), 1200 + 4800)
    assert written.warnings == [] and schema(out)["integrity"] == ["ok"]
    rows = sql(out, "SELECT * FROM GeneralInformation ORDER BY ID")
    (c_row, g_row), (cube_m, relief) = rows, meshes
    lo, hi = cube_m.xyz.min(axis=0), cube_m.xyz.max(axis=0)
    assert c_row[:13] == (1, "Куб 10 м «руда» 🧊", "", "", 0x0080FF, NOW, "Геолог", "", NOW, "Геолог", "", "", 0)
    assert c_row[13:19] == (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])  # the extent, bit for bit
    assert (
        c_row[19] == 0
        and c_row[20] == pytest.approx(1000.0, rel=1e-12)
        and c_row[21] == pytest.approx(600.0, rel=1e-12)
    )
    assert c_row[22:] == (0, 0, "", "", 0)  # not validated, not locked
    assert g_row[:5] == (2, "Рельеф", "", "", tridb_write.DEFAULT_COLOUR)
    assert g_row[20] is None  # a surface has no volume: NULL, never 0 or −1
    assert g_row[21] == pytest.approx(mesh_stats(relief.xyz, relief.tri)["area"], rel=1e-12)
    guids = []
    for mesh, (wf_id, blob) in zip(meshes, sql(out, "SELECT ID, Geometry FROM Geometry ORDER BY ID"), strict=True):
        guid, xyz, tri = decode(blob)  # the independent decoder
        assert xyz.tobytes() == mesh.xyz.tobytes() and tri.tobytes() == mesh.tri.tobytes()
        assert str(uuid.UUID(guid)) == guid
        props = sql(
            out, "SELECT GUID, Property, TextValue, RealValue FROM GeometryProperties WHERE TriangulationID = ?", wf_id
        )
        assert {p[0] for p in props} == {guid}
        guids.append(guid)
        expected = [("PointCount", None, float(mesh.point_count)), ("TriangleCount", None, float(mesh.triangle_count))]
        expected += [("Volume", None, c_row[20])] if wf_id == 1 else []
        expected += [("SurfaceArea", None, (c_row if wf_id == 1 else g_row)[21])]
        assert [p[1:] for p in props] == expected
    assert guids[0] != guids[1]
    assert sql(out, "SELECT * FROM UserAttributeDefinition") == [(1, "Сорт", 0, 2, "", 0, 0.0, None, None)]
    assert sql(out, "SELECT * FROM UserAttributeValue") == [(1, 1, "Богатая", None, None)]
    # the reader takes the file without a remark and gives the same wireframes
    back = list(read_tridb(out))
    for mesh, read in zip(meshes, back, strict=True):
        assert read.xyz.tobytes() == mesh.xyz.tobytes() and read.tri.tobytes() == mesh.tri.tobytes()
        assert (read.name, read.attrs, read.warnings) == (mesh.name, mesh.attrs, [])
    assert back[0].color == "#ff8000" and back[1].color == "#808080"
    # the same wireframes give the same file again (the GUID comes from the geometry); and then it cycles
    again = tmp_path / "again.tridb"
    write_tridb(again, new_meshes(), now=NOW, author="Геолог")
    assert again.read_bytes() == out.read_bytes()
    write_tridb(again, back, now=NOW + 99, author="Другой")  # everything comes from the source rows now
    assert table_rows(again) == table_rows(out)


def test_measures_given_are_not_computed_again(tmp_path: Path) -> None:
    v, t = cube(4, 10.0)
    out = tmp_path / "s.tridb"
    write_tridb(out, [MeshSet("Куб", v, t, stats={"area": 601.5, "volume": 1002.5, "closed": True})], now=NOW)
    assert sql(out, "SELECT Volume, SurfaceArea FROM GeneralInformation") == [(1002.5, 601.5)]
    write_tridb(out, [MeshSet("Куб", v, t, stats={"closed": True})], now=NOW)  # not the measures: computed
    ((volume, area),) = sql(out, "SELECT Volume, SurfaceArea FROM GeneralInformation")
    assert volume == pytest.approx(1000.0, rel=1e-12) and area == pytest.approx(600.0, rel=1e-12)


# ---------------------------------------------------------------- changes made in the project


def test_changes_of_the_user_reach_the_file_and_nothing_else_moves(tmp_path: Path) -> None:
    source = REAL / "Rock Model.tridb"
    meshes = list(read_tridb(source, keys=[1, 2, 3, 4]))
    a, b, c, d = meshes
    a.name = "Переименованный"
    b.color = "#123456"
    c.attrs[0]["value"] = "Гранит"  # UNIT: a text
    c.attrs[4]["value"] = 2.7  # SG: a number goes as text
    c.attrs[5]["value"] = True
    del c.attrs[9]  # TYPE is deleted
    c.attrs.append({"name": "Новый", "value": "да"})
    c.attrs.append({"name": "Ещё", "value": None})
    out = tmp_path / "changed.tridb"
    written = write_tridb(out, meshes, now=NOW)
    assert len(written.warnings) == 1 and "записаны текстом: 2" in written.warnings[0]
    before, after = table_rows(source), table_rows(out)
    ids = (1, 2, 3, 4)
    assert after["Geometry"] == [r for r in before["Geometry"] if r[1][1] in ids]  # the BLOBs did not move
    assert after["GeometryProperties"] == [r for r in before["GeometryProperties"] if r[1][1] in ids]
    old = {r[1][1]: r for r in before["GeneralInformation"]}
    new = {r[1][1]: r for r in after["GeneralInformation"]}
    assert new[1][2] == ("str", "Переименованный") and new[1][3:] == old[1][3:]
    assert new[2][5] == ("int", 0x563412) and new[2][:5] == old[2][:5] and new[2][6:] == old[2][6:]
    assert new[3] == old[3] and new[4] == old[4]
    assert after["UserAttributeDefinition"] == [
        *before["UserAttributeDefinition"],
        (("int", 12), ("int", 12), ("str", "Новый"), ("int", 0), ("int", 2), ("str", ""), ("int", 0), ("float", 0.0),
         ("NoneType", None), ("NoneType", None)),
        (("int", 13), ("int", 13), ("str", "Ещё"), ("int", 0), ("int", 2), ("str", ""), ("int", 0), ("float", 0.0),
         ("NoneType", None), ("NoneType", None)),
    ]  # fmt: skip
    values = {(r[1][1], r[2][1]): r for r in after["UserAttributeValue"]}
    source_values = {(r[1][1], r[2][1]): r for r in before["UserAttributeValue"] if r[1][1] in ids}
    assert (3, 10) not in values and len(values) == 44 - 1 + 2
    changed = {(3, 1): "Гранит", (3, 5): "2.7", (3, 6): "Да", (3, 12): "да", (3, 13): ""}
    for key, row in values.items():
        if key in changed:
            assert row[3:] == (("str", changed[key]), ("NoneType", None), ("NoneType", None)), key
            assert key[1] > 11 or row[0] == source_values[key][0]  # a changed value stays in its row
        else:
            assert row == source_values[key], key
    assert values[(3, 12)][0][1] > max(r[0][1] for r in source_values.values())  # new rows go after the old ones
    # the changed file reads back as the wireframes were given
    back = list(read_tridb(out))
    assert [m.name for m in back] == ["Переименованный", b.name, c.name, d.name] and back[1].color == "#123456"
    assert back[2].attrs == [
        *({"name": x["name"], "value": {"SG": "2.7", "STYLE": "Да"}.get(x["name"], x["value"])} for x in c.attrs[:-2]),
        {"name": "Новый", "value": "да"},
        {"name": "Ещё", "value": None},
    ]


def test_geometry_that_is_not_the_source_is_measured_anew(tmp_path: Path) -> None:
    (mesh,) = read_tridb(REAL / "ORE SEP 2025.tridb", keys=[5])
    moved = MeshSet(mesh.name, mesh.xyz * [1.0, 1.0, 2.0], mesh.tri, color=mesh.color, source=mesh.source)
    out = tmp_path / "moved.tridb"
    write_tridb(out, [moved], now=NOW)
    general = mesh.source["tridb"]["general"]
    ((wf_id, name, code, created, volume, area, validated),) = sql(
        out, "SELECT ID, Name, Code, CreateDateTime, Volume, SurfaceArea, Validated FROM GeneralInformation"
    )
    assert (wf_id, name, code, created) == (
        5,
        mesh.name,
        general["Code"],
        general["CreateDateTime"],
    )  # who and when stay
    stats = mesh_stats(moved.xyz, moved.tri)
    assert (volume, area, validated) == (stats["volume"], stats["area"], 0)
    assert volume == pytest.approx(2.0 * general["Volume"], rel=1e-9)  # twice as high: not the volume of the source
    ((blob,),) = sql(out, "SELECT Geometry FROM Geometry")
    guid, xyz, _tri = decode(blob)
    assert guid != mesh.source["tridb"]["guid"] and xyz.tobytes() == moved.xyz.tobytes()
    props = sql(out, "SELECT GUID, Property, RealValue FROM GeometryProperties ORDER BY rowid")
    assert props == [
        (guid, "PointCount", float(moved.point_count)),
        (guid, "TriangleCount", float(moved.triangle_count)),
        (guid, "Volume", volume),
        (guid, "SurfaceArea", area),
    ]
    (back,) = read_tridb(out)
    assert back.warnings == [] and back.xyz.tobytes() == moved.xyz.tobytes()


# ---------------------------------------------------------------- wireframes of several files


def test_wireframes_of_several_files_go_into_one(tmp_path: Path) -> None:
    v, t = cube(3, 10.0)
    defs = [(1, "Сорт", 0, 2, "", 0, 0.0), (2, "WEATHERING", 0, 2, "", 0, 0.0), (7, "Блок", 0, 2, "", 0, 0.0)]
    third = tb.build(
        tmp_path / "third.tridb",
        [
            tb_box(
                v,
                t,
                id=1,
                name="Body_1",
                attrs=[(1, "Первый", None, None), (2, "Слабое", None, None), (7, "12", None, None)],
            )
        ],
        defs=defs,
    )
    rock = list(read_tridb(REAL / "Rock Model.tridb", keys=[1, 2]))
    ore = list(read_tridb(REAL / "ORE SEP 2025.tridb", keys=[1, 2]))
    (other,) = read_tridb(third)
    meshes = [*rock, *ore, other, MeshSet("Body_2", v, t), MeshSet("Новый", v + 1.0, t)]
    out = tmp_path / "all.tridb"
    written = write_tridb(out, meshes, now=NOW)
    assert written.meshes == 7 and schema(out)["integrity"] == ["ok"] and schema(out)["foreign_keys"] == []
    assert len(written.warnings) == 1 and "одинаковыми названиями: 2" in written.warnings[0]
    names = sql(out, "SELECT ID, Name FROM GeneralInformation ORDER BY ID")
    assert names == [
        (1, rock[0].name), (2, rock[1].name),  # ids of the first file stay
        (3, "Body_1"), (4, "Body_2"),  # 1 and 2 are taken: the next free ones
        (5, "Body_1 (2)"), (6, "Body_2 (2)"), (7, "Новый"),
    ]  # fmt: skip
    blobs = dict(sql(out, "SELECT ID, Geometry FROM Geometry"))
    for wf_id, mesh in zip((1, 2, 3, 4, 5), [*rock, *ore, other], strict=True):
        guid, xyz, tri = decode(blobs[wf_id])
        assert (
            guid == mesh.source["tridb"]["guid"]
            and xyz.tobytes() == mesh.xyz.tobytes()
            and tri.tobytes() == mesh.tri.tobytes()
        )
    # property rows keep their rowids where they are free; rows of one wireframe keep their order
    rowids = [r[0] for r in sql(out, "SELECT rowid FROM GeometryProperties WHERE TriangulationID = 3 ORDER BY rowid")]
    assert rowids == [r[0] for r in ore[0].source["tridb"]["properties"]]
    taken = sql(out, "SELECT rowid, Property FROM GeometryProperties WHERE TriangulationID = 5 ORDER BY rowid")
    assert [p for _r, p in taken] == ["PointCount", "TriangleCount"] and len(set(r for r, _p in taken)) == 2
    # definitions: an equal one is shared, another one under a taken id gets a new id, a free id stays
    defs_out = {r[0]: r[1] for r in sql(out, "SELECT ID, Name FROM UserAttributeDefinition")}
    assert [defs_out[k] for k in range(1, 12)] == [d[1] for d in rock[0].source["tridb"]["attr_defs"]]
    assert defs_out[2] == "WEATHERING" and defs_out[7] == "PRIME MINERALIZATION"
    assert sorted(set(defs_out) - set(range(1, 12))) == [12, 13] and {defs_out[12], defs_out[13]} == {"Сорт", "Блок"}
    by_name = {v_: k for k, v_ in defs_out.items()}
    values = sql(
        out, "SELECT AttributeID, ValueText FROM UserAttributeValue WHERE TriangulationID = 5 ORDER BY AttributeID"
    )
    assert values == sorted([(2, "Слабое"), (by_name["Сорт"], "Первый"), (by_name["Блок"], "12")])
    back = {m.name: m for m in read_tridb(out)}
    assert back["Body_1 (2)"].attrs == [
        {"name": "WEATHERING", "value": "Слабое"}, {"name": "Сорт", "value": "Первый"}, {"name": "Блок", "value": "12"},
    ]  # fmt: skip
    assert all(m.warnings == [] for m in back.values())


def tb_box(v: np.ndarray, t: np.ndarray, **more: Any) -> dict[str, Any]:
    return {"xyz": v, "tri": t, **more}


def test_everything_a_file_may_have_cycles(tmp_path: Path) -> None:
    """What the real files do not have: rows of DisplayProperties and DatabaseInformation, a property of another
    GUID and a text property, attributes with values and of other types, a wireframe without a name, a name
    with spaces, more attributes than the model shows."""
    v, t = cube(3, 10.0)
    gv, gt = grid(12, 9, hills)
    defs = [(k, f"A{k}", 0, 2, "", 0, 0.0, None, b"\x01\x02") for k in range(1, 231)]
    defs += [
        (300, "Плотность", 2, 3, "нет", 7, 2.5),
        (301, "Блок", 1, 0, None, None, None),
        (302, "Без значений", 0, 2, "", 0, 0.0),
    ]
    attrs = [(k, f"значение {k}" if k % 3 else "", None, None) for k in range(230, 0, -1)]
    attrs += [(300, "", None, 2.75), (301, None, 12, None)]
    props = [("PointCount", None, float(len(v))), ("Заметка", "текст", None), ("TriangleCount", None, float(len(t)))]
    script = (
        "INSERT INTO GeometryProperties VALUES (4, '11111111-2222-3333-4444-555555555555', 'Volume', NULL, 999.0);"
        "INSERT INTO GeometryProperties VALUES (9, NULL, 'PointCount', NULL, 130.0);"
        "INSERT INTO DisplayProperties VALUES (9, 'штрих', NULL, 2, 1.5);"
        "INSERT INTO DatabaseInformation VALUES (3, 1700000000, 'Проект «Север»');"
    )
    source = tb.build(
        tmp_path / "full.tridb",
        [
            tb_box(
                v,
                t,
                id=4,
                name="  Рудное   тело ",
                props=props,
                attrs=attrs,
                general={"Volume": 1000.0, "Validated": 15147},
            ),
            tb_box(
                gv,
                gt,
                id=9,
                name=None,
                attrs=[(301, None, 5, None)],
                general={"Volume": -1.0, "MetaNotes": "строка\nвторая"},
            ),
            tb_box(v + 5.0, t, id=2, name="тело", general={"Colour": 0x7F112233}),
        ],
        defs=defs,
    )
    meshes = list(read_tridb(source))
    assert [m.name for m in meshes] == ["тело", "Рудное тело", "Каркас 9"] and len(meshes[1].attrs) == 200
    out = tmp_path / "out.tridb"
    written = write_tridb(out, meshes, now=NOW)
    assert written.warnings == []
    before, after = table_rows(source), table_rows(out)
    for table in before:
        assert after[table] == before[table], table
    assert schema(out)["integrity"] == ["ok"] and schema(out)["foreign_keys"] == []


# ---------------------------------------------------------------- portions, progress, refusals


class Cancelled(Exception):
    pass


def test_portions_progress_and_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tridb_write, "ROWS", 1000)
    gv, gt = grid(120, 100, hills)  # 24 000 triangles: 25 portions of the triangles, 13 of every coordinate
    meshes = [MeshSet(f"Рельеф {k}", gv + k, gt) for k in range(3)]
    out = tmp_path / "p.tridb"
    seen: list[tuple[float, str | None]] = []
    write_tridb(out, (m for m in meshes), lambda f, m=None: seen.append((f, m)), total=3, now=NOW)
    fractions = [f for f, _m in seen]
    assert fractions == sorted(fractions) and fractions[0] == 0.0 and fractions[-1] == 1.0 and len(seen) > 150
    assert any(m and "Рельеф 1" in m for _f, m in seen)
    for mesh, (blob,) in zip(meshes, sql(out, "SELECT Geometry FROM Geometry ORDER BY ID"), strict=True):
        guid, xyz, tri = decode(blob)
        assert xyz.tobytes() == mesh.xyz.tobytes() and tri.tobytes() == mesh.tri.tobytes()
        assert blob == tb.geometry(mesh.xyz, mesh.tri, guid)  # the bytes of the independent packer
    good = out.read_bytes()
    seen.clear()
    write_tridb(tmp_path / "g.tridb", (m for m in meshes), lambda f, m=None: seen.append((f, m)), now=NOW)
    assert {f for f, _m in seen} == {0.0, 1.0}  # a generator without `total`: messages, no fraction to give
    assert (tmp_path / "g.tridb").read_bytes() == good

    def cancel_at(limit: float) -> Any:
        def progress(fraction: float, _message: str | None = None) -> None:
            if fraction >= limit:
                raise Cancelled

        return progress

    for limit in (0.0, 0.01, 0.5, 0.999, 1.0):
        with pytest.raises(Cancelled):
            write_tridb(out, meshes, cancel_at(limit), now=NOW)
        if limit < 1.0:  # the last tick comes after the file is in place
            assert out.read_bytes() == good  # the file that was there stays whole
        assert sorted(p.name for p in tmp_path.iterdir()) == ["g.tridb", "p.tridb"]


def test_refusals_leave_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "x.tridb"
    with pytest.raises(FormatError) as e:
        write_tridb(out, [])
    assert e.value.code == "no_meshes" and e.value.message and e.value.hint
    with pytest.raises(FormatError) as e:
        write_tridb(out, iter(()))
    assert e.value.code == "no_meshes" and list(tmp_path.iterdir()) == []
    v, t = cube(6, 10.0)
    monkeypatch.setattr(tridb_write, "MAX_BLOB", 5000)
    with pytest.raises(FormatError) as e:
        write_tridb(out, [MeshSet("Малый", v[:3], t[:1] * 0 + [[0, 1, 2]]), MeshSet("Большой", v, t)])
    assert e.value.code == "too_large" and "«Большой»" in e.value.message and e.value.hint
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(OSError):
        write_tridb(tmp_path / "нет такой папки" / "x.tridb", [MeshSet("Куб", v, t)])
