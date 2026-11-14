"""Stage 10: large wireframes — readers, checks, spatial structure, levels of detail, exact sections."""

import io
import struct

import ezdxf
import numpy as np
import pytest
from ezdxf.render import forms

from app.formats.dxf_stream import read_dxf_meshes
from app.formats.wireframes import _read_dxf_ezdxf, read_dxf, read_obj, read_stl, read_tridb_geometry
from app.geo import meshes
from app.geo.checks import _crosses, orientation_conflicts, self_intersections
from app.geo.section import plane_section
from bench.synthetic import subdivided_cube, tridb_geometry, write_dxf_polyface
from tests.test_datasets import CUBE_T, CUBE_V, make_tridb


def _key(mesh_list):
    return {m.name: (len(m.vertices), len(m.triangles), m.color, round(float(m.vertices.sum()), 6)) for m in mesh_list}


# ---- readers


def test_streamed_tridb_geometry_equals_the_layout(tmp_path):
    v, t = subdivided_cube(6)
    blob = tridb_geometry(v, t)
    got_v, got_t = read_tridb_geometry(io.BytesIO(blob), len(blob), len(v))
    assert got_v.tobytes() == v.astype("<f8").tobytes() and np.array_equal(got_t, t)


def test_streamed_dxf_matches_ezdxf_for_every_face_entity(tmp_path):
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    doc.layers.add("FAULTS", color=1)
    doc.layers.add("SURF", color=3)
    cube = forms.cube().scale_uniform(10).translate(512000, 4512000, 300)
    msp.add_polyface(dxfattribs={"layer": "FAULTS"}).append_faces([[cube.vertices[i] for i in f] for f in cube.faces])
    mesh = msp.add_polymesh((3, 4), dxfattribs={"layer": "SURF"})
    for i in range(3):
        for j in range(4):
            mesh.set_mesh_vertex((i, j), (i * 10.0, j * 10.0, float(i * j)))
    msp.add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], dxfattribs={"layer": "P", "color": 5})
    m = msp.add_mesh(dxfattribs={"layer": "M"})
    with m.edit_data() as data:
        data.vertices = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 1)]
        data.faces = [(0, 1, 2), (0, 2, 3)]
    msp.add_lwpolyline([(0, 0), (5, 5)], dxfattribs={"layer": "LINES"})
    path = tmp_path / "all.dxf"
    doc.saveas(path)

    streamed = _key(read_dxf(path))
    assert streamed == _key(_read_dxf_ezdxf(path))
    assert streamed["FAULTS"][:3] == (8, 12, "#ff0000") and streamed["SURF"][1] == 12 and "LINES" not in streamed
    _, counts = read_dxf_meshes(path)
    assert counts == {"3DFACE": 1, "POLYFACE": 1, "POLYMESH": 1, "MESH": 1}


def test_streamed_dxf_of_many_polyfaces_keeps_the_mesh(tmp_path):
    v, t = subdivided_cube(20, size=100.0)
    write_dxf_polyface(tmp_path / "pit.dxf", v, t, per_entity=500)  # many entities, shared seams
    (mesh,) = read_dxf(tmp_path / "pit.dxf")
    from app.formats.wireframes import mesh_stats

    s = mesh_stats(mesh.vertices, mesh.triangles)
    assert len(mesh.triangles) == len(t) and len(mesh.vertices) == len(v)  # seams welded back
    assert s["closed"] and s["volume"] == pytest.approx(1e6, rel=1e-9)


def test_obj_groups_polygons_and_negative_indices(tmp_path):
    path = tmp_path / "a.obj"
    path.write_text(
        "# test\no pit\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1/1/1 2/2/2 3/3/3 4/4/4\n"
        "g dump\nv 5 5 5\nv 6 5 5\nv 5 6 5\nf -3 -2 -1\n"
    )
    pit, dump = read_obj(path)
    assert (pit.name, len(pit.vertices), len(pit.triangles)) == ("pit", 4, 2)
    assert (dump.name, len(dump.vertices), len(dump.triangles)) == ("dump", 3, 1)
    assert dump.vertices.min() == 5


def test_stl_binary_and_ascii(tmp_path):
    soup = CUBE_V[CUBE_T.reshape(-1)].astype("<f4")
    binary = bytearray(b"solid-less header".ljust(80, b"\x00") + struct.pack("<I", len(CUBE_T)))
    for k in range(len(CUBE_T)):
        binary += struct.pack("<3f", 0, 0, 0) + soup[3 * k:3 * k + 3].tobytes() + b"\x00\x00"
    (tmp_path / "b.stl").write_bytes(bytes(binary))
    text = "solid cube\n" + "".join(
        "facet normal 0 0 0\nouter loop\n" + "".join(f"vertex {x} {y} {z}\n" for x, y, z in CUBE_V[tri]) + "endloop\nendfacet\n"
        for tri in CUBE_T) + "endsolid cube\n"
    (tmp_path / "a.stl").write_text(text)
    for name in ("a.stl", "b.stl"):
        (m,) = read_stl(tmp_path / name)
        assert (len(m.vertices), len(m.triangles)) == (8, 12) and "float32" in m.attributes["note"]
    assert read_stl(tmp_path / "a.stl")[0].name == "cube"


# ---- checks


def test_self_intersections_match_brute_force_on_a_random_soup():
    rng = np.random.default_rng(3)
    v = rng.uniform(0, 100, (300, 3)) + [500000, 4500000, 0]
    t = np.arange(300).reshape(-1, 3)
    centre = (v.min(axis=0) + v.max(axis=0)) / 2
    vv = v - centre
    brute = sum(_crosses(vv, t[i], t[j], 1e-9) for i in range(len(t)) for j in range(i + 1, len(t)))
    assert brute > 0
    assert self_intersections(v, t)["pairs"] == brute


def test_closed_cube_is_clean_and_two_crossing_cubes_are_not():
    v, t = subdivided_cube(6)
    assert self_intersections(v, t)["pairs"] == 0 and orientation_conflicts(t, len(v)) == 0
    both_v = np.concatenate([v, v + [511.3, 307.7, 203.1]])
    found = self_intersections(both_v, np.concatenate([t, t + len(v)]))
    assert found["pairs"] > 0 and len(found["samples"]) > 0
    flipped = t.copy()
    flipped[0] = flipped[0][::-1]
    assert orientation_conflicts(flipped, len(v)) == 3


# ---- spatial structure, levels of detail


def test_morton_chunks_cover_every_triangle_once():
    v, t = subdivided_cube(30)
    order = meshes.morton_order(v, t)
    assert sorted(order.tolist()) == list(range(len(t)))
    t2 = t[order]
    origin = np.round((v.min(axis=0) + v.max(axis=0)) / 2)
    table = meshes.chunk_table(v, t2, origin, size=1000)
    assert sum(c[1] for c in table) == len(t) and [c[0] for c in table] == list(range(0, len(t), 1000))
    for first, count, *box in table:
        p = v[t2[first:first + count].reshape(-1)] - origin
        assert np.all(p >= np.array(box[:3]) - 1e-3) and np.all(p <= np.array(box[3:]) + 1e-3)


def test_simplify_in_parts_keeps_the_shape():
    v, t = subdivided_cube(60)  # 43 200 triangles
    t = t[meshes.morton_order(v, t)]
    sv, st = meshes.simplify(v, t, len(t) // 8, part=10_000)
    assert len(st) < len(t) // 4
    assert np.allclose(sv.min(axis=0), v.min(axis=0)) and np.allclose(sv.max(axis=0), v.max(axis=0))
    assert meshes.lod_targets(100_000) == [] and meshes.lod_targets(10_000_000) == [1_250_000, 156_250]


def test_derive_job_builds_levels_of_detail(db_client, run_jobs, tmp_path, monkeypatch):
    monkeypatch.setattr(meshes, "LOD_MIN_TRIANGLES", 1000)
    monkeypatch.setattr(meshes, "LOD_FLOOR", 10)
    pid = db_client.post("/api/projects", json={"name": "Карьер"}).json()["id"]
    v, t = subdivided_cube(20)
    path = tmp_path / "c.tridb"
    make_tridb(path, [("куб", None, v, t)])
    body = db_client.post(f"/api/projects/{pid}/wireframes", files={"file": ("c.tridb", path.read_bytes())}).json()
    wid = body["wireframes"][0]["id"]
    assert db_client.get(f"/api/wireframes/{wid}/geometry").json()["lods"] == []  # before the job
    assert run_jobs() == 1
    g = db_client.get(f"/api/wireframes/{wid}/geometry").json()
    assert [lod["level"] for lod in g["lods"]] == [1, 2]
    assert g["lods"][0]["triangles"] < len(t) and g["lods"][1]["triangles"] < g["lods"][0]["triangles"]
    raw = np.frombuffer(db_client.get(g["lods"][0]["triangles_url"]).content, dtype="<u4")
    assert len(raw) == 3 * g["lods"][0]["triangles"]
    # A part of the full triangle buffer, as the viewer loads it.
    first, count = g["chunks"][0][:2]
    part = db_client.get(g["triangles"], headers={"Range": f"bytes={first * 12}-{(first + count) * 12 - 1}"})
    assert part.status_code == 206 and len(part.content) == count * 12
    stats = db_client.get(f"/api/projects/{pid}/datasets").json()[0]["wireframes"][0]["stats"]
    assert stats["checked"] and stats["self_intersections"]["pairs"] == 0


# ---- sections


def test_section_of_a_cube_is_one_exact_closed_square():
    v, t = subdivided_cube(10, size=100.0)
    origin = np.round((v.min(axis=0) + v.max(axis=0)) / 2)
    x = v[0, 0] + 37.25
    (line,) = plane_section(v, t, origin, [x, 0, 0], [1, 0, 0])
    pts = np.array(line["points"]) + origin
    assert line["closed"] and np.allclose(pts[:, 0], x, atol=1e-9)
    closed = np.vstack([pts, pts[:1]])
    perimeter = np.linalg.norm(np.diff(closed, axis=0), axis=1).sum()
    assert perimeter == pytest.approx(400.0, abs=1e-6)
    # Through grid vertices exactly (a degenerate plane): still one closed loop.
    (on_grid,) = plane_section(v, t, origin, [v[0, 0] + 30.0, 0, 0], [1, 0, 0])
    assert on_grid["closed"]
    # With the stored parts the result is the same.
    t2 = t[meshes.morton_order(v, t)]
    chunks = meshes.chunk_table(v, t2, origin, size=64)
    (same,) = plane_section(v, t2, origin, [x, 0, 0], [1, 0, 0], chunks)
    assert sorted(map(tuple, same["points"])) == sorted(map(tuple, line["points"]))
    assert plane_section(v, t, origin, [v[0, 0] - 1, 0, 0], [1, 0, 0]) == []


def test_sections_endpoint(db_client, tmp_path):
    pid = db_client.post("/api/projects", json={"name": "Разрезы"}).json()["id"]
    path = tmp_path / "c.tridb"
    make_tridb(path, [("куб", None, CUBE_V * 10 + [500000, 4500000, 100], CUBE_T)])
    w = db_client.post(f"/api/projects/{pid}/wireframes", files={"file": ("c.tridb", path.read_bytes())}).json()["wireframes"][0]
    r = db_client.post("/api/sections", json={"wireframe_ids": [w["id"]], "point": [500005, 4500000, 0], "normal": [1, 0, 0]}).json()
    (s,) = r["sections"]
    (line,) = s["polylines"]
    pts = np.array(line["points"]) + s["origin"]
    # A square 10 × 10 m; the diagonals of the faces add points along its sides.
    assert line["closed"] and np.allclose(pts[:, 0], 500005)
    closed = np.vstack([pts, pts[:1]])
    assert np.linalg.norm(np.diff(closed, axis=0), axis=1).sum() == pytest.approx(40.0, abs=1e-9)
    assert db_client.post("/api/sections", json={"wireframe_ids": [w["id"]], "point": [0, 0, 0], "normal": [0, 0, 0]}).status_code == 422


def test_check_again_endpoint(db_client, run_jobs, tmp_path):
    pid = db_client.post("/api/projects", json={"name": "Повтор"}).json()["id"]
    path = tmp_path / "c.tridb"
    make_tridb(path, [("куб", None, CUBE_V, CUBE_T)])
    d = db_client.post(f"/api/projects/{pid}/wireframes", files={"file": ("c.tridb", path.read_bytes())}).json()
    run_jobs()
    job = db_client.post(f"/api/datasets/{d['id']}/check")
    assert job.status_code == 202 and job.json()["kind"] == "wireframes.derive"
    assert run_jobs() == 1
    sets = db_client.post(f"/api/projects/{pid}/sets", json={"name": "Скв"}).json()
    assert db_client.post(f"/api/datasets/{sets['id']}/check").status_code == 422
