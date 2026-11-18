"""DXF writer of wireframes: the cycle write → read bit for bit, polyfaces of at most 32 767 vertices and faces,
the drawing as ezdxf reads it, names, colours, failures (ТЗ 9.5, 9.12; ADR 0013 §2, substep 8.5)."""

from __future__ import annotations

from pathlib import Path

import ezdxf
import numpy as np
import pytest
from ezdxf.render import MeshBuilder

from bf_core.formats.dxf_lines import read_dxf
from bf_core.formats.dxf_meshes import read_dxf_meshes
from bf_core.formats.dxf_write_mesh import MAX_FACES, MAX_VERTICES, blocks, in_order_of_use, write_dxf_meshes
from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshSet

from ..meshutil import UTM, cube, grid, icosphere

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real" / "TST-FAULTS.DXF"


def by_use(xyz: np.ndarray, tri: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A mesh with its vertices numbered in the order its triangles use them, unused ones left out."""
    used, local = in_order_of_use(tri)
    return xyz[used], (local - 1).astype(np.int32)


def same(a: MeshSet, b: MeshSet) -> None:
    assert a.xyz.tobytes() == b.xyz.tobytes() and a.tri.tobytes() == b.tri.tobytes()
    assert (a.name, a.color) == (b.name, b.color)


def relief(n: int, seed: int | None = 0) -> tuple[np.ndarray, np.ndarray]:
    return by_use(*grid(n, n, lambda x, y: np.sin(x / 7.0) * 30.0 + np.cos(y / 5.0) * 20.0, step=12.5, seed=seed))


def entities(path: Path) -> bytes:
    """The ENTITIES section of a drawing (the frame of ezdxf carries GUIDs and the time of writing)."""
    raw = path.read_bytes()
    start = raw.index(b"  2\r\nENTITIES\r\n")
    return raw[start : raw.index(b"  0\r\nENDSEC\r\n", start)]


def test_cycle_is_bit_for_bit_and_ezdxf_reads_the_drawing(tmp_path: Path) -> None:
    meshes = [
        MeshSet("Куб", *by_use(*cube(4, 10.0)), color="#ff792a"),
        MeshSet("Рельеф", *relief(20), color=None, attrs=[{"name": "Тип", "value": "ЦТМ"}]),
        MeshSet("Sphere", *by_use(*icosphere(2, 50.0)), color="#0000ff"),
    ]
    path = tmp_path / "out.dxf"
    seen: list[tuple[float, str | None]] = []
    written = write_dxf_meshes(path, meshes, lambda f, m=None: seen.append((f, m)))
    assert (written.meshes, written.triangles, written.files) == (3, sum(m.triangle_count for m in meshes), [path])
    assert written.points == sum(m.point_count for m in meshes) and written.size == path.stat().st_size
    assert written.warnings == ["Атрибуты каркасов в DXF не записываются: каркасов с атрибутами 1"]
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[0] == 0.0 and fractions[-1] == 1.0
    assert not list(tmp_path.glob("*.part"))

    read = read_dxf(path, recover=False)
    assert read.meshes == 3 and read.layers == [] and read.warnings == [] and not read.recovered
    assert (read.header.version, read.header.codepage) == ("AC1018", "ANSI_1251")
    for a, b in zip(meshes, read.wireframes, strict=True):
        same(a, b)
        assert b.warnings == [] and b.source == {
            "format": "dxf", "name": a.name, "layer": a.name, "entities": {"POLYFACE": 1}
        }  # fmt: skip
    # written again from what was read: the same bytes
    again = tmp_path / "again.dxf"
    write_dxf_meshes(again, read.wireframes)
    assert entities(again) == entities(path) and len(entities(path)) > 1000
    raw = path.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") and "Куб".encode("cp1251") in raw

    # ezdxf: no errors of the audit, the same faces, the colours and the extent
    doc = ezdxf.readfile(str(path))
    auditor = doc.audit()
    assert not auditor.errors and not auditor.fixes
    found = list(doc.modelspace())
    assert [e.dxftype() for e in found] == ["POLYLINE"] * 3 and all(e.is_poly_face_mesh for e in found)
    for mesh, entity in zip(meshes, found, strict=True):
        builder = MeshBuilder.from_polyface(entity)
        faces = np.array([[tuple(builder.vertices[i]) for i in f] for f in builder.faces])
        assert faces.tobytes() == mesh.xyz[mesh.tri].tobytes() and entity.dxf.layer == mesh.name
        assert (entity.dxf.m_count, entity.dxf.n_count) == (mesh.point_count, mesh.triangle_count)
    assert found[0].dxf.true_color == 0xFF792A and doc.layers.get("Куб").rgb == (255, 121, 42)
    assert not found[1].dxf.hasattr("true_color") and found[2].dxf.color == 5
    handles = [e.dxf.handle for e in doc.entitydb.values()]
    assert len(handles) == len(set(handles)) and int(doc.header["$HANDSEED"], 16) > max(int(h, 16) for h in handles)
    low = np.min([m.xyz.min(axis=0) for m in meshes], axis=0)
    assert tuple(doc.header["$EXTMIN"]) == tuple(low.tolist())


def test_more_than_32767_vertices_are_several_polyfaces(tmp_path: Path) -> None:
    xyz, tri = relief(210, seed=None)  # 44 521 vertices, 88 200 triangles, row by row
    assert len(xyz) > MAX_VERTICES and len(tri) > 2 * MAX_FACES
    mixed_v, mixed_t = relief(210)  # the same triangles in a random order: a polyface is full of vertices sooner
    soup_v, soup_t = by_use(*icosphere(5, 100.0))  # 20 480 triangles …
    soup_v = soup_v[soup_t.reshape(-1)] + np.arange(3 * len(soup_t))[:, None] * 1e-3  # … on vertices of their own
    soup_t = np.arange(3 * len(soup_t), dtype=np.int32).reshape(-1, 3)
    meshes = [MeshSet("Большой", xyz, tri, color="#00ff00"), MeshSet("Россыпь", soup_v, soup_t)]
    meshes.append(MeshSet("Вперемешку", mixed_v, mixed_t))
    path = tmp_path / "big.dxf"
    written = write_dxf_meshes(path, meshes)
    assert (written.points, written.triangles) == (2 * len(xyz) + len(soup_v), 2 * 88200 + 20480)
    assert written.warnings == []

    read = read_dxf(path, recover=False)
    for a, b in zip(meshes, read.wireframes, strict=True):
        same(a, b)  # the polyfaces are welded back: the same vertex numbers
    assert read.wireframes[0].source["entities"] == {"POLYFACE": 3}
    assert read.wireframes[1].source["entities"] == {"POLYFACE": 2}  # 10 922 triangles of 3 vertices each, + 9 558
    assert blocks(soup_t) == [(0, 10922), (10922, 20480)]
    dense = np.repeat(np.array([[0, 1, 2]]), 70000, axis=0)
    assert blocks(dense) == [(0, MAX_FACES), (MAX_FACES, 2 * MAX_FACES), (2 * MAX_FACES, 70000)]
    assert len(blocks(tri)) == 3
    cuts = blocks(mixed_t)
    assert len(cuts) == read.wireframes[2].source["entities"]["POLYFACE"] > 3 and cuts[-1][1] == 88200
    for a, b in [*cuts[:-1], *blocks(tri)[:-1]]:  # every polyface is as full as the limits let it be
        t = mixed_t if (a, b) in cuts else tri
        assert len(np.unique(t[a:b])) <= MAX_VERTICES and b - a <= MAX_FACES
        assert b - a == MAX_FACES or len(np.unique(t[a : b + 1])) > MAX_VERTICES
    # ezdxf reads a wireframe of more than 32 767 vertices: two polyfaces within the limits of AutoCAD
    small = tmp_path / "small.dxf"
    write_dxf_meshes(small, [MeshSet("Россыпь", soup_v[: 3 * 10930], soup_t[:10930])])
    doc = ezdxf.readfile(str(small))
    assert not doc.audit().errors
    found = list(doc.modelspace())
    assert [(e.dxf.m_count, e.dxf.n_count) for e in found] == [(32766, 10922), (24, 8)]
    for e in found:
        assert e.is_poly_face_mesh and len(e.vertices) == e.dxf.m_count + e.dxf.n_count
    faces = [[tuple(v) for v in face] for e in found for face in MeshBuilder.from_polyface(e).faces_as_vertices()]
    assert np.array(faces).tobytes() == soup_v[: 3 * 10930].reshape(-1, 3, 3).tobytes()
    again = tmp_path / "again.dxf"
    write_dxf_meshes(again, iter(read.wireframes), total=3)  # a generator
    assert entities(again) == entities(path) and len(entities(path)) > 1000


def test_any_numbering_gives_the_same_file(tmp_path: Path) -> None:
    v, t = cube(3, 7.0, seed=5)
    rng = np.random.default_rng(1)
    mixed = rng.permutation(len(v))
    extra = np.vstack([v[mixed], UTM + 5000.0, v[mixed][:10]])  # shuffled, an unused vertex, repeated vertices
    number = np.empty(len(v), dtype=np.int64)
    number[mixed] = np.arange(len(v))
    t2 = number[t]
    t2[::3] = np.where(t2[::3] < 10, t2[::3] + len(v) + 1, t2[::3])  # some corners on the repeats
    a, b = tmp_path / "a.dxf", tmp_path / "b.dxf"
    write_dxf_meshes(a, [MeshSet("M", *by_use(v, t))])
    write_dxf_meshes(b, [MeshSet("M", extra, t2.astype(np.int32))])
    assert entities(a) == entities(b)  # the file is the corners of the triangles in their order
    assert read_dxf(a).header.layers == read_dxf(b).header.layers
    back = next(read_dxf_meshes(a))
    assert back.xyz[back.tri].tobytes() == v[t].tobytes() and back.point_count == len(v)
    # a triangle whose corners are one vertex stays what it is
    flat = MeshSet("F", v[:4], np.array([[0, 1, 2], [3, 3, 3], [2, 1, 3]], dtype=np.int32))
    write_dxf_meshes(a, [flat])
    assert next(read_dxf_meshes(a)).tri.tolist() == [[0, 1, 2], [3, 3, 3], [2, 1, 3]]


def test_names_colours_and_what_dxf_cannot_keep(tmp_path: Path) -> None:
    v, t = by_use(*cube(1, 2.0))
    meshes = [
        MeshSet("Тело/1: руда", v, t, color="#123456"),
        MeshSet("тело_1_ руда", v + 10.0, t),  # the same layer name once cleaned, in another case
        MeshSet("日本 100%", v + 20.0, t),  # outside the code page: \U+XXXX in the file
        MeshSet("0", v + 30.0, t),
    ]
    path = tmp_path / "names.dxf"
    written = write_dxf_meshes(path, meshes)
    assert len(written.warnings) == 1 and "«Тело/1: руда» → «Тело_1_ руда»" in written.warnings[0]
    assert "«тело_1_ руда» → «тело_1_ руда_2»" in written.warnings[0]
    read = read_dxf(path, recover=False)
    assert [m.name for m in read.wireframes] == ["Тело_1_ руда", "тело_1_ руда_2", "日本 100%", "0"]
    assert read.wireframes[0].color == "#123456" and read.wireframes[1].color is None
    assert not ezdxf.readfile(str(path)).audit().errors
    # a wireframe read from a DXF keeps the name of its layer while it is called so
    source = read.wireframes[2]
    renamed = MeshSet("Другое", source.xyz, source.tri, source=source.source)
    write_dxf_meshes(path, [source, renamed])
    assert [m.name for m in read_dxf_meshes(path)] == ["日本 100%", "Другое"]


def test_nothing_to_write_and_failures_leave_no_file(tmp_path: Path) -> None:
    path = tmp_path / "none.dxf"
    with pytest.raises(FormatError) as info:
        write_dxf_meshes(path, [])
    assert info.value.code == "no_meshes" and info.value.hint
    v, t = by_use(*cube(2, 1.0))

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None = None) -> None:
        if fraction > 0.5:
            raise Cancelled

    path.write_bytes(b"old")
    with pytest.raises(Cancelled):
        write_dxf_meshes(path, [MeshSet("A", v, t), MeshSet("B", v, t), MeshSet("C", v, t)], cancel)
    assert path.read_bytes() == b"old" and sorted(p.name for p in tmp_path.iterdir()) == ["none.dxf"]
    with pytest.raises(OSError):
        write_dxf_meshes(tmp_path / "missing" / "a.dxf", [MeshSet("A", v, t)])


@pytest.mark.skipif(not REAL.is_file(), reason="no real data")
def test_the_real_wireframe_survives_the_cycle(tmp_path: Path) -> None:
    first = read_dxf(REAL).wireframes
    path = tmp_path / "faults.dxf"
    write_dxf_meshes(path, first)
    second = read_dxf(path, recover=False).wireframes
    for a, b in zip(first, second, strict=True):
        same(a, b)
        assert a.source == b.source
    again = tmp_path / "again.dxf"
    write_dxf_meshes(again, second)
    assert entities(again) == entities(path) and len(entities(path)) > 1000
