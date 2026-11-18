"""DXF meshes → wireframes: the four kinds of entities against ezdxf, the order of the file, welding, broken faces,
limits and hostile files (ТЗ 7.21, 9.5, 9.12; ADR 0013 §2, substep 8.4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
import pytest
from ezdxf.render import MeshBuilder

from bf_core.formats import dxf_stream as ds
from bf_core.formats.dxf_lines import Limits, read_dxf
from bf_core.formats.dxf_meshes import fan4, inspect_dxf_meshes, read_dxf_meshes
from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshSet

from .dxf_builder import drawing, tags

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real" / "TST-FAULTS.DXF"
X0, Y0, Z0 = 466570.58600000001, 4584699.697000001, 412.30000000000001  # every digit of a double in use


def _write(tmp_path: Path, content: str, name: str = "a.dxf") -> Path:
    path = tmp_path / name
    path.write_bytes(content.encode("cp1251"))
    return path


def _fan(corners: list[Any]) -> list[tuple[Any, Any, Any]]:
    """The reference: BF `_fan` — repeats of the corner before (and of the first at the end) are left out."""
    if len(corners) == 3:
        return [(corners[0], corners[1], corners[2])]
    unique: list[Any] = []
    for c in corners:
        if not unique or unique[-1] != c:
            unique.append(c)
    while len(unique) > 1 and unique[0] == unique[-1]:
        unique.pop()
    return [(unique[0], unique[k], unique[k + 1]) for k in range(1, len(unique) - 1)]


def _soup(mesh: MeshSet) -> np.ndarray:
    """The corners of the triangles in their order: triangles × 3 × 3."""
    return mesh.xyz[mesh.tri]


def _expected(faces: list[list[tuple[float, float, float]]]) -> np.ndarray:
    return np.array([t for face in faces for t in _fan(face)], dtype=np.float64).reshape(-1, 3, 3)


def _same(mesh: MeshSet, faces: list[list[tuple[float, float, float]]]) -> None:
    want = _expected(faces)
    got = _soup(mesh)
    assert got.shape == want.shape and (got.view(np.uint64) == want.view(np.uint64)).all()
    assert mesh.point_count == len({tuple(p) for p in want.reshape(-1, 3).tolist()})  # welded, nothing unused
    first_use = np.unique(mesh.tri.reshape(-1), return_index=True)[1]
    assert (np.diff(first_use) > 0).all()  # the vertices are numbered in the order the triangles use them


def _by_name(path: Path, **options: Any) -> dict[str, MeshSet]:
    read = read_dxf(path, **options)
    return {m.name: m for m in read.wireframes}


# --- the real drawing ---------------------------------------------------------------------------------------


@pytest.mark.skipif(not REAL.is_file(), reason="no real data")
def test_the_real_drawing_is_one_wireframe() -> None:
    read = read_dxf(REAL, recover=False)
    assert read.meshes == 1 and len(read.wireframes) == 1 and read.layers == [] and read.warnings == []
    mesh = read.wireframes[0]
    # (ТЗ 9.5 and ADR 0013 say 7 443 vertices: the file has 7 444 vertex records, all different and all used)
    assert (mesh.name, mesh.point_count, mesh.triangle_count) == ("FLT-Combined_TST", 7444, 7417)
    assert mesh.color == f"#{16742314:06x}" and mesh.warnings == [] and mesh.attrs == []
    assert mesh.source == {
        "format": "dxf", "name": "FLT-Combined_TST", "layer": "FLT-Combined_TST", "entities": {"POLYFACE": 1}
    }  # fmt: skip
    assert 182.0 < mesh.extent()[2] < 183.0 and 587.0 < mesh.extent()[5] < 589.0
    doc = ezdxf.readfile(str(REAL))
    polyface = doc.modelspace().query("POLYLINE")[0]
    builder = MeshBuilder.from_polyface(polyface)
    faces = [[tuple(builder.vertices[i]) for i in face] for face in builder.faces]
    _same(mesh, faces)
    entries, warnings = inspect_dxf_meshes(REAL)
    assert [(e.key, e.name, e.points, e.triangles, e.color) for e in entries] == [
        (0, "FLT-Combined_TST", 7444, 7417, mesh.color)
    ] and warnings == []
    assert entries[0].extent == mesh.extent()


# --- the four kinds, written by ezdxf ------------------------------------------------------------------------


def _points(rng: np.random.Generator, n: int) -> list[tuple[float, float, float]]:
    xyz = rng.random((n, 3)) * 1000.0 + [X0, Y0, Z0]
    return [tuple(p) for p in xyz.tolist()]


def test_the_four_kinds_as_ezdxf_writes_them(tmp_path: Path) -> None:
    rng = np.random.default_rng(8)
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    p = _points(rng, 40)
    # a polyface: triangles and quads sharing vertices, written with and without welding
    pface_faces = [[p[0], p[1], p[2]], [p[0], p[2], p[3], p[4]], [p[2], p[1], p[5], p[6]], [p[6], p[5], p[7]]]
    pface = msp.add_polyface(dxfattribs={"layer": "Грани", "color": 3})
    pface.append_faces(pface_faces)
    welded = msp.add_polyface(dxfattribs={"layer": "Грани сшитые"})
    welded.append_faces(pface_faces)
    welded.optimize()
    # a polygon mesh 4 × 5, and one closed in both directions
    grid = np.array(_points(rng, 20)).reshape(4, 5, 3)
    pmesh = msp.add_polymesh(size=(4, 5), dxfattribs={"layer": "Сетка"})
    ring = msp.add_polymesh(size=(4, 5), dxfattribs={"layer": "Кольцо"})
    ring.close(True, True)
    for i in range(4):
        for j in range(5):
            pmesh.set_mesh_vertex((i, j), tuple(grid[i, j]))
            ring.set_mesh_vertex((i, j), tuple(grid[i, j]))
    # 3DFACE: a quad, a triangle with the third corner repeated, a triangle of three corners
    faces3d = [[p[10], p[11], p[12], p[13]], [p[11], p[14], p[12], p[12]], [p[20], p[21], p[22]]]
    for corners in faces3d:
        msp.add_3dface(corners, dxfattribs={"layer": "3D", "true_color": 0x102030})
    # MESH: triangles, a quad, a pentagon; and a smoothed one
    subd_v = p[25:33]
    subd_f = [[0, 1, 2], [0, 2, 3, 4], [4, 3, 5, 6, 7]]
    for level, layer in ((0, "Поверхность"), (2, "Гладкая")):
        entity = msp.add_mesh(dxfattribs={"layer": layer, "subdivision_levels": level})
        with entity.edit_data() as data:
            data.vertices = list(subd_v)
            data.faces = [list(f) for f in subd_f]
    path = tmp_path / "kinds.dxf"
    doc.saveas(path)

    read = read_dxf(path, recover=False)
    assert read.meshes == 4 + 3 + 2 and read.layers == [] and read.warnings == []
    found = {m.name: m for m in read.wireframes}
    assert list(found) == ["Грани", "Грани сшитые", "Сетка", "Кольцо", "3D", "Поверхность", "Гладкая"]  # file order
    _same(found["Грани"], pface_faces)
    _same(found["Грани сшитые"], pface_faces)
    assert (found["Грани"].tri == found["Грани сшитые"].tri).all()  # welding does not depend on the vertex list
    cells = [[grid[i, j], grid[i, j + 1], grid[i + 1, j + 1], grid[i + 1, j]] for i in range(3) for j in range(4)]
    _same(found["Сетка"], [[tuple(c) for c in cell] for cell in cells])
    closed = [
        [grid[i, j], grid[i, (j + 1) % 5], grid[(i + 1) % 4, (j + 1) % 5], grid[(i + 1) % 4, j]]
        for i in range(4)
        for j in range(5)
    ]
    _same(found["Кольцо"], [[tuple(c) for c in cell] for cell in closed])
    assert found["Кольцо"].triangle_count == 40 and found["Сетка"].triangle_count == 24
    _same(found["3D"], faces3d)
    assert found["3D"].triangle_count == 4
    subd = [[subd_v[i] for i in f] for f in subd_f]
    _same(found["Поверхность"], subd)
    _same(found["Гладкая"], subd)
    assert found["Поверхность"].triangle_count == 1 + 2 + 3 and found["Поверхность"].warnings == []
    assert len(found["Гладкая"].warnings) == 1 and "без сглаживания" in found["Гладкая"].warnings[0]
    assert found["Грани"].color == "#00ff00" and found["3D"].color == "#102030" and found["Сетка"].color is None
    assert found["Грани"].source["entities"] == {"POLYFACE": 1} and found["3D"].source["entities"] == {"3DFACE": 3}
    assert found["Сетка"].source["entities"] == {"POLYMESH": 1} and found["Гладкая"].source["entities"] == {"MESH": 1}
    # the same triangles as ezdxf makes of every entity
    for entity in msp:
        kind = entity.dxftype()
        if kind == "POLYLINE" and entity.is_poly_face_mesh:
            builder = MeshBuilder.from_polyface(entity)
            _same(found[entity.dxf.layer], [[tuple(builder.vertices[i]) for i in f] for f in builder.faces])
        elif kind == "MESH":
            builder = MeshBuilder.from_mesh(entity)
            _same(found[entity.dxf.layer], [[tuple(builder.vertices[i]) for i in f] for f in builder.faces])
    # wireframes only, by their numbers
    assert [m.name for m in read_dxf_meshes(path, keys=[4, 1])] == ["Грани сшитые", "3D"]
    assert [e.triangles for e in inspect_dxf_meshes(path)[0]] == [6, 6, 24, 40, 4, 6, 6]
    counted = read_dxf(path, wireframes=False)
    assert counted.meshes == 9 and counted.wireframes == []


# --- written by hand: what ezdxf would not write ---------------------------------------------------------------


def _vertex(layer: str, x: float, y: float, z: float, flags: int = 192) -> str:
    return tags(0, "VERTEX", 8, layer, 10, x, 20, y, 30, z, 70, flags)


def _face(layer: str, *numbers: int) -> str:
    items: list[object] = [0, "VERTEX", 8, layer, 10, 0.0, 20, 0.0, 30, 0.0, 70, 128]
    for code, number in zip((71, 72, 73, 74), numbers, strict=False):
        items += [code, number]
    return tags(*items)


def _polyface(layer: str, points: list[tuple[float, float, float]], faces: list[tuple[int, ...]], *more: Any) -> str:
    head = tags(0, "POLYLINE", 8, layer, 66, 1, 70, 64, 71, len(points), 72, len(faces), *more)
    return head + "".join(_vertex(layer, *p) for p in points) + "".join(_face(layer, *f) for f in faces) + tags(
        0, "SEQEND", 8, layer
    )


def _face3d(layer: str, *corners: tuple[float, float, float]) -> str:
    items: list[object] = [0, "3DFACE", 8, layer]
    for j, (x, y, z) in enumerate(corners):
        items += [10 + j, x, 20 + j, y, 30 + j, z]
    return tags(*items)


SQUARE = [(X0, Y0, Z0), (X0 + 1.5, Y0, Z0), (X0 + 1.5, Y0 + 2.5, Z0 + 0.1), (X0, Y0 + 2.5, Z0)]


def test_invisible_edges_missing_corners_and_the_order_of_the_file(tmp_path: Path) -> None:
    a, b, c, d = SQUARE
    e, f = (X0 + 3.0, Y0, Z0), (X0 + 3.0, Y0 + 2.5, Z0)
    body = (
        _face3d("A", e, f, b)
        # a quad with invisible edges, a triangle with a 0 for the fourth corner, a vertex no face uses
        + _polyface("A", [a, b, c, d, (1.0, 2.0, 3.0)], [(1, -2, 3, -4), (-1, 3, 4, 0), (2, 0, 1, 3)])
        + _face3d("B", a, b, c, d)
        + _face3d("A", c, f, e, e)
        + tags(0, "LINE", 8, "A", 10, 1.0, 20, 2.0, 30, 3.0, 11, 4.0, 21, 5.0, 31, 6.0)
    )
    read = read_dxf(_write(tmp_path, drawing(body, layers=[("A", 1), ("B", 5)])), recover=False)
    assert read.meshes == 4 and [m.name for m in read.wireframes] == ["A", "B"] and read.warnings == []
    assert [layer.name for layer in read.layers] == ["A"] and read.records == 1  # the lines are read as before
    mesh_a, mesh_b = read.wireframes
    # the triangles of a layer come in the order of the file whatever entities they are in
    _same(mesh_a, [[e, f, b], [a, b, c, d], [a, c, d], [b, a, c], [c, f, e]])
    assert mesh_a.tri.tolist() == [[0, 1, 2], [3, 2, 4], [3, 4, 5], [3, 4, 5], [2, 3, 4], [4, 1, 0]]
    assert mesh_a.point_count == 6 and mesh_a.warnings == []  # the vertex no face uses is not a vertex
    _same(mesh_b, [[a, b, c, d]])
    assert (mesh_a.color, mesh_b.color) == ("#ff0000", "#0000ff")  # by layer: the LAYER table
    assert mesh_a.xyz.dtype == np.float64 and mesh_a.tri.dtype == np.int32


def test_faces_that_cannot_be_read_are_left_out_and_counted(tmp_path: Path) -> None:
    a, b, c, d = SQUARE
    no_x = tags(0, "VERTEX", 8, "P", 20, 5.0, 30, 6.0, 70, 192)
    not_a_number = tags(0, "VERTEX", 8, "P", 10, "abc", 20, 5.0, 30, 6.0, 70, 192)
    faces = [(1, 2, 3), (1, 2, 5), (1, 6, 3), (3, 4, 9), (1, 1, 2, 2), (4, 0, 0, 0), (1, 3, 4, 1)]
    head = tags(0, "POLYLINE", 8, "P", 66, 1, 70, 64, 71, 6, 72, len(faces))
    polyface = (
        head
        + "".join(_vertex("P", *p) for p in (a, b, c, d))
        + no_x
        + not_a_number
        + "".join(_face("P", *f) for f in faces)
        + tags(0, "SEQEND", 8, "P")
    )
    body = (
        polyface
        + _face3d("P", a, b)  # two corners
        + tags(0, "3DFACE", 8, "P", 10, 1.0, 20, 2.0, 30, "x", 11, 1.0, 21, 2.0, 31, 3.0, 12, 4.0, 22, 5.0, 32, 6.0)
        + _face3d("P", a, a, b, b)  # no area
        + _face3d("Q", a, b)  # a layer that gives nothing
        # polygon meshes: the counts do not fit the vertices; a "bomb" of 30 000 × 30 000 on four vertices
        + tags(0, "POLYLINE", 8, "P", 66, 1, 70, 16, 71, 3, 72, 3)
        + "".join(_vertex("P", *p, flags=64) for p in SQUARE)
        + tags(0, "SEQEND", 8, "P")
        + tags(0, "POLYLINE", 8, "P", 66, 1, 70, 16, 71, 30000, 72, 30000)
        + "".join(_vertex("P", *p, flags=64) for p in SQUARE)
        + tags(0, "SEQEND", 8, "P")
        + tags(0, "MESH", 8, "P", 91, 0, 92, 3, 10, a[0], 20, a[1], 30, a[2], 10, b[0], 20, b[1], 30, b[2], 10,
               c[0], 20, c[1], 30, c[2], 93, 8, 90, 3, 90, 0, 90, 1, 90, 2, 90, 3, 90, 0, 90, 1, 90, 7)  # fmt: skip
        + tags(0, "MESH", 8, "P", 92, 2, 10, 1.0, 20, 2.0, 30, 3.0, 93, 4, 90, 3, 90, 0, 90, 1, 90, 2)
    )
    read = read_dxf(_write(tmp_path, drawing(body)), recover=False)
    assert read.meshes == 9 and [m.name for m in read.wireframes] == ["P"]
    assert len(read.warnings) == 1 and "«Q»" in read.warnings[0] and "без единого треугольника" in read.warnings[0]
    mesh = read.wireframes[0]
    _same(mesh, [[a, b, c], [a, c, d], [a, b, c]])
    # left out: three faces on vertices that are not there or have no coordinate, two 3DFACE, two polygon
    # meshes, a MESH polygon on vertex 7, a MESH with a broken vertex list; without an area: two faces and a 3DFACE
    assert len(mesh.warnings) == 2 and ": 9 — пропущены" in mesh.warnings[0] and ": 3 — пропущены" in mesh.warnings[1]
    assert mesh.source["entities"] == {"3DFACE": 3, "MESH": 2, "POLYFACE": 1, "POLYMESH": 2}


def test_exact_welding_and_the_systems_of_coordinates(tmp_path: Path) -> None:
    near = float(np.nextafter(X0, np.inf))
    body = (
        _face3d("W", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        + _face3d("W", (-0.0, 0.0, -0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0))  # −0.0 is 0.0: one vertex
        + _face3d("W", (X0, Y0, Z0), (near, Y0, Z0), (X0, Y0 + 1.0, Z0))  # one unit of the last place apart: two
        # a mesh entity is in the world system whatever its extrusion is
        + _polyface("E", SQUARE, [(1, 2, 3, 4)], 210, 0.0, 220, 0.0, 230, -1.0)
        + tags(0, "3DFACE", 8, "sheet", 67, 1, 10, 0.0, 20, 0.0, 30, 0.0, 11, 1.0, 21, 0.0, 31, 0.0, 12, 0.0, 22,
               1.0, 32, 0.0)  # fmt: skip
    )
    read = read_dxf(_write(tmp_path, drawing(body)), recover=False)
    welded, turned = read.wireframes
    assert read.meshes == 4 and read.skipped == {"PAPERSPACE": 1}  # paper space is not a wireframe
    assert welded.tri.tolist() == [[0, 1, 2], [0, 2, 3], [4, 5, 6]] and welded.point_count == 7
    assert welded.xyz[0].tolist() == [0.0, 0.0, 0.0] and not np.signbit(welded.xyz[0]).any()  # the first one stays
    assert welded.xyz[4, 0] == X0 and welded.xyz[5, 0] == near
    _same(turned, [list(SQUARE)])


@pytest.mark.parametrize("block", [200, 700, 4096])
def test_blocks_do_not_change_the_wireframes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block: int) -> None:
    rng = np.random.default_rng(3)
    pts = _points(rng, 60)
    faces = [tuple(int(v) + 1 for v in rng.choice(60, 3, replace=False)) for _ in range(90)]
    quads = [tuple(int(v) + 1 for v in rng.choice(60, 4, replace=False)) for _ in range(30)]
    small = [f for f in faces if max(f) <= 30]
    body = (
        _polyface("L", pts, faces + quads)
        + "".join(_face3d("L", *(pts[int(i)] for i in rng.choice(60, 4, replace=False))) for _ in range(40))
        + tags(0, "POLYLINE", 8, "G", 66, 1, 70, 16, 71, 6, 72, 10)
        + "".join(_vertex("G", *p, flags=64) for p in pts)
        + tags(0, "SEQEND", 8, "G")
        + _polyface("L", pts[:30], small)
    )
    path = _write(tmp_path, drawing(body))
    whole = read_dxf(path, recover=False).wireframes
    monkeypatch.setattr(ds, "BLOCK_BYTES", block)  # a polyface goes on over many blocks
    monkeypatch.setattr(ds, "SMALL_BYTES", 0)
    cut = read_dxf(path, recover=False)
    assert cut.meshes == 43 and [m.name for m in cut.wireframes] == ["L", "G"]
    for a, b in zip(whole, cut.wireframes, strict=True):
        assert a.xyz.tobytes() == b.xyz.tobytes() and a.tri.tobytes() == b.tri.tobytes()
        assert a.source == b.source and a.warnings == b.warnings == []
    assert len(small) > 5 and whole[0].triangle_count == 90 + 60 + 80 + len(small)
    assert whole[1].triangle_count == 5 * 9 * 2


def test_fan4() -> None:
    c = np.array([[5, 6, 7, -1], [5, 6, 7, 8], [5, 5, 6, 7], [5, 6, 7, 5], [5, 6, 6, 5], [-1, 6, -1, 7],
                  [5, -1, 6, 7], [5, 5, 6, -1], [5, 6, 5, 6], [5, 6, 7, 7]])  # fmt: skip
    tri, made = fan4(c)
    assert made.tolist() == [1, 2, 1, 1, 0, 0, 1, 1, 2, 1]
    assert tri.tolist() == [[5, 6, 7], [5, 6, 7], [5, 7, 8], [5, 6, 7], [5, 6, 7], [5, 6, 7], [5, 5, 6], [5, 6, 5],
                            [5, 5, 6], [5, 6, 7]]  # fmt: skip
    for row, n in zip(c.tolist(), made.tolist(), strict=True):
        corners = [v for v in row if v >= 0]
        assert n == (len(_fan(corners)) if len(corners) >= 3 else 0)


# --- limits, damage, cancellation ----------------------------------------------------------------------------


def test_limits_stop_a_bomb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    faces = "".join(_face3d("B", *SQUARE) for _ in range(40))
    path = _write(tmp_path, drawing(faces))
    assert read_dxf(path, limits=Limits(triangles=80)).wireframes[0].triangle_count == 80
    with pytest.raises(FormatError) as info:
        read_dxf(path, limits=Limits(triangles=79))
    assert info.value.code == "too_large" and "треугольников" in info.value.message and info.value.hint
    assert read_dxf(path, limits=Limits(triangles=1), wireframes=False).meshes == 40  # counted only: no limit
    # a polyface without an end: its records are counted while they come
    long = _polyface("B", list(SQUARE) * 50, [(1, 2, 3)] * 50)
    monkeypatch.setattr(ds, "BLOCK_BYTES", 512)
    with pytest.raises(FormatError) as info:
        read_dxf(_write(tmp_path, drawing(long), "long.dxf"), limits=Limits(triangles=100))
    assert info.value.code == "too_large"


def test_a_file_cut_short_and_a_binary_one(tmp_path: Path) -> None:
    a, b, c, d = SQUARE
    body = _face3d("T", a, b, c) + _polyface("T", list(SQUARE), [(1, 2, 3), (1, 3, 4)])
    text = drawing(body)
    cut = text[: text.rindex("VERTEX") + 30]  # inside the last face of the polyface: it and the end are lost
    path = _write(tmp_path, cut, "cut.dxf")
    with pytest.raises(FormatError) as info:
        read_dxf(path, recover=False)
    assert info.value.code == "truncated"
    read = read_dxf(path)
    assert read.recovered and "обрезан" in read.warnings[0] and read.meshes == 2
    _same(read.wireframes[0], [[a, b, c], [a, b, c]])
    # a binary DXF goes through ezdxf and gives the same wireframes
    doc = ezdxf.new("R2010")
    doc.modelspace().add_3dface([a, b, c, d], dxfattribs={"layer": "Bin"})
    binary = tmp_path / "bin.dxf"
    doc.saveas(binary, fmt="bin")
    read = read_dxf(binary)
    assert read.recovered and [m.name for m in read.wireframes] == ["Bin"]
    _same(read.wireframes[0], [[a, b, c, d]])


def test_no_meshes_and_cancellation(tmp_path: Path) -> None:
    lines = _write(tmp_path, drawing(tags(0, "LINE", 8, "A", 10, 1.0, 20, 2.0, 30, 3.0, 11, 4.0, 21, 5.0, 31, 6.0)))
    with pytest.raises(FormatError) as info:
        list(read_dxf_meshes(lines))
    error = info.value
    assert error.code == "no_meshes" and "каркас" in error.message and error.hint
    assert inspect_dxf_meshes(lines) == ([], [])

    class Cancelled(Exception):
        pass

    seen: list[float] = []

    def tick(fraction: float, message: str | None = None) -> None:
        seen.append(fraction)
        if len(seen) > 3:
            raise Cancelled

    path = _write(tmp_path, drawing("".join(_face3d("B", *SQUARE) for _ in range(2000))), "many.dxf")
    with pytest.raises(Cancelled):
        read_dxf(path, tick)
    fractions: list[float] = []
    meshes = list(read_dxf_meshes(path, progress=lambda f, m=None: fractions.append(f)))
    assert len(meshes) == 1 and meshes[0].triangle_count == 4000 and meshes[0].point_count == 4
    assert fractions == sorted(fractions) and fractions[0] == 0.0 and fractions[-1] == 1.0
