"""The registry of object formats with wireframes (ADR 0013 §2, 8.10): every wireframe format behind the one
signature of `read_objects` — the wireframes one at a time or as a composition without geometry; a drawing gives
its lines and its wireframes from one pass; `.dm` told by its fields; pairs of files (`.dtm` + `.str`, `…tr.dm` +
`…pt.dm`). Reference: the arrays the files were written from and the readers of the formats — bit for bit; the
real files of `tests/data/real` — their known counts (66 / 3 590, 55 / 128 994, 7 444 / 7 417)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats import registry
from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshEntry, MeshSet
from bf_core.formats.registry import detect_object, group_files, read_objects
from tests.formats.dm_builder import F, build
from tests.formats.test_datamine_objects import STR, write_pair
from tests.formats.tridb_builder import build as build_tridb
from tests.meshutil import UTM, cube, grid

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real"
MAGIC = b"AC1027".ljust(128, b"\x00")  # the "DWG" of the fake converter: this and the DXF it was made from


def bits(a: Any) -> bytes:
    return np.ascontiguousarray(a, dtype=np.float64).tobytes()


def solids() -> list[MeshSet]:
    """A closed cube of 1000 m³ and an open surface, on UTM-sized coordinates, triangles shuffled."""
    v, t = cube(4, 10.0)
    gv, gt = grid(7, 5, lambda x, y: 0.25 * x - 0.5 * y, offset=UTM + [100.0, 0.0, 0.0])
    return [
        MeshSet("Куб", v, t, color="#3366cc", attrs=[{"name": "Руда", "value": "Au"}]),
        MeshSet("Рельеф", gv, gt, color="#88aa44"),
    ]


def drawing(path: Path) -> Path:
    """A DXF with a line, a point and two layers of meshes (3DFACE and a polyface)."""
    import ezdxf

    doc = ezdxf.new("R2004")
    msp = doc.modelspace()
    a = (466570.5, 4584699.75, 100.0)
    msp.add_line(a, (466580.5, 4584710.25, 101.0), dxfattribs={"layer": "FAULT"})
    msp.add_point((466575.0, 4584705.0, 99.5), dxfattribs={"layer": "FAULT"})
    v, t = cube(1, 10.0)
    for i, j, k in t.tolist():
        msp.add_3dface([tuple(v[i]), tuple(v[j]), tuple(v[k])], dxfattribs={"layer": "ORE"})
    mesh = msp.add_polyface(dxfattribs={"layer": "TOPO"})
    gv, gt = grid(3, 2, lambda x, y: 0.5 * x)
    mesh.append_faces([[tuple(gv[c]) for c in face] for face in gt.tolist()])
    doc.saveas(path)
    return path


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    from bf_core.formats.dtm_surpac import write_dtm
    from bf_core.formats.obj import write_obj
    from bf_core.formats.stl import write_stl
    from bf_core.formats.tridb_write import write_tridb

    out = {name: tmp_path / name for name in ("body.tridb", "body.obj", "body.stl", "pit.dtm", "mixed.dxf")}
    write_tridb(out["body.tridb"], solids(), now=1_795_000_000, author="test")
    write_obj(out["body.obj"], solids())
    write_stl(out["body.stl"], solids(), binary=False)
    write_dtm(out["pit.dtm"], solids())
    out["pit.str"] = tmp_path / "pit.str"
    drawing(out["mixed.dxf"])
    v, t = cube(2, 10.0)
    out["oretr.dm"], out["orept.dm"] = write_pair(tmp_path, v, t)
    rows = [[float(k + 1), 1.0, *(UTM + [k, 2 * k, 0.5]).tolist()] for k in range(4)]
    out["strings.dm"] = tmp_path / "strings.dm"
    out["strings.dm"].write_bytes(build(STR, rows))
    out["model.dm"] = tmp_path / "model.dm"
    out["model.dm"].write_bytes(build([F("XC"), F("YC"), F("ZC"), F("XINC")], [[1.0, 2.0, 3.0, 4.0]]))
    out["table.dm"] = tmp_path / "table.dm"
    out["table.dm"].write_bytes(build([F("A"), F("B")], [[1.0, 2.0]]))
    return out


def test_wireframe_formats_by_extension_and_dm_by_content(files: dict[str, Path]) -> None:
    expected = {
        "body.tridb": "tridb", "body.obj": "obj", "body.stl": "stl", "pit.dtm": "surpac_dtm", "mixed.dxf": "dxf",
        "oretr.dm": "datamine", "orept.dm": "datamine", "strings.dm": "datamine_str",
    }  # fmt: skip
    assert {name: detect_object(files[name]).key for name in expected} == expected
    assert detect_object(files["body.tridb"]).kind == "wireframes" and detect_object(files["mixed.dxf"]).kind == "lines"
    assert detect_object(files["strings.dm"]).kind == "lines" and detect_object(files["oretr.dm"]).meshes
    assert detect_object(Path("чертёж.DWG")).key == "dwg"  # by the name: a DWG is not opened to tell
    block = detect_object(files["model.dm"])  # a block model goes to the import of blocks (Э17 17.38)
    assert block.key == "datamine_blocks" and block.kind == "block_model" and not block.meshes
    with pytest.raises(FormatError) as info:  # the reader of wireframes, lines and points does not take it
        read_objects(files["model.dm"])
    assert info.value.code == "block_model" and info.value.hint
    with pytest.raises(FormatError) as info:
        detect_object(files["table.dm"])
    assert info.value.code == "not_wireframe" and "PID1" in info.value.message
    with pytest.raises(FormatError) as info:
        detect_object(files["body.obj"].with_suffix(".bmf"))
    assert info.value.code == "unsupported" and ".tridb" in info.value.hint and ".dwg" in info.value.hint
    stored = files["body.obj"].rename(files["body.obj"].with_name("upload-3"))  # an upload under another name
    assert detect_object(stored, filename="Тело.OBJ").key == "obj"
    faces = read_objects(stored, filename="Грани.obj", inspect=True).entries
    assert [e.name for e in faces] == ["Куб", "Рельеф"]


def test_wireframes_one_at_a_time_and_as_a_composition(files: dict[str, Path]) -> None:
    """Every wireframe format: `meshes` gives what the file was written from bit for bit, the composition — the
    same wireframes without geometry; the progress goes from 0 to 1 and never back."""
    want = solids()
    seen: list[float] = []
    for name in ("body.tridb", "body.obj", "body.stl", "pit.dtm"):
        seen.clear()
        out = read_objects(files[name], lambda fraction, message: seen.append(fraction))
        assert out.layers == [] and out.rasters == [] and out.entries == [] and out.meshes is not None, name
        assert not isinstance(out.meshes, list)  # nothing is read until a wireframe is asked for
        got = list(out.meshes)
        assert out.unread == [] and len(got) == 2, name
        for mesh, ref in zip(got, want, strict=True):
            assert mesh.tri.dtype == np.int32 and mesh.xyz.dtype == np.float64, name
            # float64 bit for bit: the corners of every triangle in the order of the file
            assert bits(mesh.xyz[mesh.tri]) == bits(ref.xyz[ref.tri]), name
            if name != "body.stl":  # an STL has no vertex list: its vertices are numbered as the corners come
                assert mesh.tri.tolist() == ref.tri.tolist() and bits(mesh.xyz) == bits(ref.xyz), name
        if name != "pit.dtm":  # a Surpac pair has no names
            assert [m.name for m in got] == ["Куб", "Рельеф"], name
        assert seen and seen == sorted(seen) and seen[-1] == 1.0 and seen[0] >= 0.0, name

        shown = read_objects(files[name], inspect=True)
        assert shown.meshes is None and shown.layers == [] and shown.format == out.format
        assert all(isinstance(e, MeshEntry) and e.error is None for e in shown.entries)
        assert [(e.name, e.triangles) for e in shown.entries] == [(m.name, m.triangle_count) for m in got], name
    tridb = read_objects(files["body.tridb"], inspect=True).entries
    assert [(e.points, e.color, e.attrs) for e in tridb] == [
        (98, "#3366cc", [{"name": "Руда", "value": "Au"}]),
        (48, "#88aa44", []),
    ]


@pytest.mark.parametrize(("name", "count", "points", "triangles"), [
    ("ORE SEP 2025.tridb", 66, 1925, 3590), ("Rock Model.tridb", 55, 64_991, 128_994),
])  # fmt: skip
def test_real_tridb_through_the_registry(name: str, count: int, points: int, triangles: int) -> None:
    from bf_core.formats.tridb import read_tridb

    out = read_objects(REAL / name)
    got = list(out.meshes or [])
    assert (len(got), sum(m.point_count for m in got), sum(m.triangle_count for m in got)) == (count, points, triangles)
    for mesh, ref in zip(got, read_tridb(REAL / name), strict=True):
        assert mesh.name == ref.name and mesh.source == ref.source
        assert bits(mesh.xyz) == bits(ref.xyz) and mesh.tri.tobytes() == ref.tri.tobytes()
    shown = read_objects(REAL / name, inspect=True)
    assert [(e.key, e.name, e.points, e.triangles) for e in shown.entries] == [
        (m.source["tridb"]["id"], m.name, m.point_count, m.triangle_count) for m in got
    ]


def test_drawing_gives_lines_and_wireframes_from_one_pass(files: dict[str, Path]) -> None:
    out = read_objects(files["mixed.dxf"])
    assert out.format == "dxf" and [(ls.name, ls.line_count, ls.point_count) for ls in out.layers] == [("FAULT", 2, 3)]
    assert isinstance(out.meshes, list) and out.meshes is out.detail.wireframes
    assert [(m.name, m.point_count, m.triangle_count) for m in out.meshes] == [("ORE", 8, 12), ("TOPO", 12, 12)]
    v, t = cube(1, 10.0)
    ore = out.meshes[0]
    assert bits(ore.xyz[ore.tri]) == bits(v[t])  # the corners of every triangle, in the order of the file
    shown = read_objects(files["mixed.dxf"], inspect=True)
    assert [(e.key, e.name, e.points, e.triangles) for e in shown.entries] == [(0, "ORE", 8, 12), (1, "TOPO", 12, 12)]
    assert shown.meshes is None and shown.detail.wireframes == [] and len(shown.layers) == 1  # no geometry kept

    real = read_objects(REAL / "TST-FAULTS.DXF")  # the sample of the owner: one polyface, no lines
    assert real.layers == [] and isinstance(real.meshes, list)
    assert [(m.name, m.point_count, m.triangle_count) for m in real.meshes] == [("FLT-Combined_TST", 7444, 7417)]


def test_dwg_goes_through_the_converter_of_the_settings(
    files: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ezdxf.addons import odafc

    from bf_core.formats import dwg
    from tests.formats.test_dwg import Oda, _no_display

    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.delenv(dwg.ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing here"))
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.setenv(name, str(tmp_path / "Program Files"))
    monkeypatch.setattr(odafc, "_linux_dummy_display", _no_display, raising=False)
    drawing_dwg = tmp_path / "Карьер.dwg"
    drawing_dwg.write_bytes(MAGIC + files["mixed.dxf"].read_bytes())
    with pytest.raises(FormatError) as info:  # no converter: the text of Д-13, never a crash
        read_objects(drawing_dwg)
    assert info.value.code == "oda_not_found" and "ODA File Converter" in info.value.hint
    oda = Oda(tmp_path / "oda")
    out = read_objects(drawing_dwg, settings={"converter": str(oda.exe), "split_by": "GROUP"})
    assert out.format == "dwg" and [ls.name for ls in out.layers] == ["FAULT"]
    assert [(m.name, m.triangle_count) for m in out.meshes or []] == [("ORE", 12), ("TOPO", 12)]
    want = read_objects(files["mixed.dxf"])
    assert bits(out.layers[0].xyz) == bits(want.layers[0].xyz)
    assert all(bits(a.xyz) == bits(b.xyz) for a, b in zip(out.meshes or [], want.meshes or [], strict=True))


def test_datamine_pair_strings_and_choices(files: dict[str, Path], tmp_path: Path) -> None:
    v, t = cube(2, 10.0)
    for given in ("oretr.dm", "orept.dm"):  # either table of the pair: the other one is found next to it
        (mesh,) = list(read_objects(files[given]).meshes or [])
        assert bits(mesh.xyz) == bits(v) and mesh.tri.tolist() == t.tolist() and mesh.source["format"] == "datamine"
        assert any("предварительно" in w for w in mesh.warnings)  # the warnings of the file: with the first one
    shown = read_objects(files["oretr.dm"], inspect=True)
    assert shown.preliminary and [(e.name, e.points, e.triangles) for e in shown.entries] == [("ore", 26, 48)]
    assert shown.choices == {"split_by": []} and any("предварительно" in w for w in shown.warnings)
    # the points table kept somewhere else under another name; a field to divide by, chosen in the wizard
    side = tmp_path / "side"
    values = [[float(k % 2 + 1)] for k in range(len(t))]
    tr, pt = write_pair(side, v, t, names=("a.dm", "b.dm"), fields=(F("GROUP"),), values=values)
    moved = pt.rename(tmp_path / "0007")
    with pytest.raises(FormatError) as info:
        list(read_objects(tr).meshes or [])
    assert info.value.code == "pair_missing"
    halves = list(read_objects(tr, sidecars={".dm": moved}, settings={"split_by": "GROUP"}).meshes or [])
    assert [m.triangle_count for m in halves] == [24, 24]
    assert read_objects(tr, sidecars={".dm": moved}, inspect=True).choices == {"split_by": ["GROUP"]}

    lines = read_objects(files["strings.dm"], inspect=True)
    assert lines.format == "datamine_str" and lines.preliminary and lines.meshes is None and lines.entries == []
    assert [(ls.line_count, ls.point_count) for ls in lines.layers] == [(1, 4)] and "layer_field" in lines.choices
    assert registry.OBJECT_FORMATS["tridb"].kind == "wireframes" and not read_objects(files["body.obj"]).preliminary


def test_one_broken_wireframe_does_not_lose_the_others(tmp_path: Path) -> None:
    """TRIDB: a wireframe that cannot be read goes to `unread` with its reason; the others are read."""
    v, t = cube(1, 10.0)
    path = build_tridb(
        tmp_path / "three.tridb",
        [
            {"id": 1, "name": "Первый", "xyz": v, "tri": t},
            {"id": 2, "name": "Битый", "xyz": v, "tri": t, "blob": b"\xff\xff\xff\xff" + b"\x00" * 20},
            {"id": 3, "name": "Третий", "xyz": v + 50.0, "tri": t},
        ],
    )
    out = read_objects(path)
    got = list(out.meshes or [])
    assert [m.name for m in got] == ["Первый", "Третий"] and bits(got[1].xyz) == bits(v + 50.0)
    assert [(e.name, e.error["code"]) for e in out.unread] == [("Битый", out.unread[0].error["code"])]
    assert out.unread[0].error["message"] and out.unread[0].error["hint"]
    shown = read_objects(path, inspect=True)
    assert [e.name for e in shown.entries] == ["Первый", "Битый", "Третий"]


def test_errors_of_a_file_come_with_the_first_wireframe(files: dict[str, Path], tmp_path: Path) -> None:
    bad = tmp_path / "bad.tridb"
    bad.write_bytes("это не TRIDB".encode())
    empty = tmp_path / "empty.obj"
    empty.write_bytes(b"")
    for path, code in ((bad, "not_tridb"), (empty, "empty_file")):
        out = read_objects(path)  # nothing is opened yet
        with pytest.raises(FormatError) as info:
            next(iter(out.meshes or []))
        assert info.value.code == code and info.value.hint, path.name
        with pytest.raises(FormatError):
            read_objects(path, inspect=True)

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None) -> None:
        raise Cancelled

    for name in ("body.tridb", "body.obj", "body.stl", "pit.dtm", "oretr.dm"):
        with pytest.raises(Cancelled):
            list(read_objects(files[name], cancel).meshes or [])
        if name != "pit.dtm":  # the composition of a small DTM is one step: nothing to stop
            with pytest.raises(Cancelled):
                read_objects(files[name], cancel, inspect=True)
    with pytest.raises(Cancelled):
        read_objects(files["mixed.dxf"], cancel)


def test_pairs_of_a_selection() -> None:
    names = [
        "pit.dtm", "Pit.STR", "road.str", "topo.dtm", "oretr.dm", "OREPT.DM", "wastept.dm", "lines.dm",
        "ore.tridb", "ore.obj", "ore.stl", "plan.dwg", "plan.dxf", "model.omf", "pit.prj",
    ]  # fmt: skip
    groups, rest = group_files(names)
    found = {g.main: (g.formats, g.sidecars) for g in groups}
    both = ("datamine", "datamine_str")
    assert found == {
        "pit.dtm": (("surpac_dtm",), {".str": "Pit.STR"}),  # the points of the surface, not strings to load
        "road.str": (("micromine_str", "surpac_str"), {}),
        "topo.dtm": (("surpac_dtm",), {}),  # its `.str` is looked for next to it by the reader
        "oretr.dm": (both, {".dm": "OREPT.DM"}),
        "wastept.dm": (both, {}),  # points without their triangles in the selection: read by itself
        "lines.dm": (both, {}),
        "ore.tridb": (("tridb",), {}),
        "ore.obj": (("obj",), {}),
        "ore.stl": (("stl",), {}),
        "plan.dwg": (("dwg",), {}),
        "plan.dxf": (("dxf",), {}),
        "model.omf": (("omf",), {}),
    }
    assert rest == ["pit.prj"]
    assert {".tridb", ".obj", ".stl", ".dtm", ".dm", ".dwg"} <= set(registry.OBJECT_SUFFIXES)
    # a `.str` alone is strings, as before; twice the same name is one file
    alone, _ = group_files(["pit.str", "pit.str"])
    assert [(g.main, g.sidecars) for g in alone] == [("pit.str", {})]


def test_surpac_pair_given_by_hand(files: dict[str, Path], tmp_path: Path) -> None:
    """The string file of a DTM kept somewhere else: without it — an error, with it — the same wireframes."""
    want = [bits(m.xyz) for m in read_objects(files["pit.dtm"]).meshes or []]
    moved = files["pit.str"].rename(tmp_path / "0001")
    with pytest.raises(FormatError) as info:
        list(read_objects(files["pit.dtm"]).meshes or [])
    assert info.value.code == "no_strings"
    got = [bits(m.xyz) for m in read_objects(files["pit.dtm"], sidecars={".STR": moved}).meshes or []]
    assert got == want and len(got) == 2
