"""Project tree: independent drillhole sets and wireframes (TRIDB, DXF)."""

import base64
import sqlite3
import struct
import uuid

import ezdxf
import numpy as np
import pytest

from app.formats.wireframes import WireframeFormatError, mesh_stats, parse_tridb_geometry, read_tridb, weld
from tests.samples import collar_dat

CUBE_V = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=float)
CUBE_T = np.array([
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
    [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
], dtype=np.int32)


def tridb_blob(vertices: np.ndarray, triangles: np.ndarray, header_in_length: bool = False) -> bytes:
    """Geometry BLOB laid out as described in docs/FORMATS.md."""
    guid = str(uuid.uuid4()).encode()
    out = b"\xff\xff\xff\xff\x00\x00\x05\x00" + struct.pack("<qq", len(guid), 9) + guid
    for number, data in enumerate(
        [vertices[:, 0].astype("<f8").tobytes(), vertices[:, 1].astype("<f8").tobytes(),
         vertices[:, 2].astype("<f8").tobytes(), triangles.astype("<i4").tobytes()],
        start=1,
    ):
        length = len(data) + (16 if header_in_length else 0)
        out += struct.pack("<qq", length, number) + data
    return out


def make_tridb(path, items) -> None:
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA encoding = "UTF-16le"')
    conn.execute("CREATE TABLE GeneralInformation (ID INTEGER PRIMARY KEY, Name TEXT, Colour INTEGER)")
    conn.execute("CREATE TABLE Geometry (ID INTEGER PRIMARY KEY, Geometry BLOB)")
    conn.execute("CREATE TABLE GeometryProperties (ID INTEGER PRIMARY KEY, GUID TEXT, PointCount INTEGER, TriangleCount INTEGER)")
    for i, (name, colour, v, t) in enumerate(items, start=1):
        conn.execute("INSERT INTO GeneralInformation VALUES (?, ?, ?)", (i, name, colour))
        conn.execute("INSERT INTO Geometry VALUES (?, ?)", (i, tridb_blob(v, t)))
        conn.execute("INSERT INTO GeometryProperties VALUES (?, ?, ?, ?)", (i, "g", len(v), len(t)))
    conn.commit()
    conn.close()


def _project(client, name="Проект") -> int:
    return client.post("/api/projects", json={"name": name}).json()["id"]


# ---- geometry helpers


def test_mesh_stats_closed_cube_and_open_surface():
    cube = mesh_stats(CUBE_V + 500000, CUBE_T)  # UTM-sized coordinates keep full precision
    surface = mesh_stats(CUBE_V[:4], CUBE_T[:2])

    assert cube == {"area": 6.0, "volume": 1.0, "closed": True, "degenerate": 0, "open_edges": 0}
    assert surface["closed"] is False and surface["volume"] is None and surface["open_edges"] == 4


def test_weld_merges_repeated_corners():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    welded, tris = weld(vertices, np.array([[0, 1, 2], [3, 4, 5]]))

    assert len(welded) == 4
    assert mesh_stats(welded, tris)["open_edges"] == 4  # the shared edge is now shared


@pytest.mark.parametrize("header_in_length", [False, True])
def test_tridb_blob_both_length_conventions(header_in_length):
    vertices, triangles = parse_tridb_geometry(tridb_blob(CUBE_V, CUBE_T, header_in_length), point_count=8)

    assert np.array_equal(vertices, CUBE_V) and np.array_equal(triangles, CUBE_T)


def test_read_tridb_names_colours_and_errors(tmp_path):
    path = tmp_path / "pit.tridb"
    make_tridb(path, [("выемка", 0x0000FF, CUBE_V, CUBE_T), ("насыпь", 0xFF0000, CUBE_V + 10, CUBE_T[:2])])

    meshes = read_tridb(path)

    assert [m.name for m in meshes] == ["выемка", "насыпь"]
    assert [m.color for m in meshes] == ["#ff0000", "#0000ff"]  # COLORREF is 0x00BBGGRR
    assert len(meshes[1].triangles) == 2
    (tmp_path / "bad.tridb").write_bytes(b"not a database at all" * 10)
    with pytest.raises(WireframeFormatError):
        read_tridb(tmp_path / "bad.tridb")


# ---- API


def test_sets_are_independent(db_client):
    pid = _project(db_client)
    first = db_client.post(f"/api/projects/{pid}/sets", json={"name": "Кампания 2024"}).json()["id"]

    def commit(body):
        up = db_client.post("/api/import/upload", files={"file": ("collar.DAT", collar_dat())}).json()
        sug = up["suggestions"]["collar"]
        return db_client.post("/api/import/commit", json={
            "file_id": up["file_id"], "kind": "collar", "mapping": sug["mapping"], "components": [], "attributes": [], **body,
        })

    into_first = commit({"set_id": first}).json()
    new_set = commit({"project_id": pid, "set_name": "Кампания 2025"}).json()
    again = commit({"set_id": first}).json()  # re-import replaces only this set

    assert into_first["set_id"] == first and new_set["set_id"] != first
    assert again["replaced"] == 2
    tree = db_client.get(f"/api/projects/{pid}/datasets").json()
    assert [(d["name"], d["counts"]["collar"]) for d in tree] == [("Кампания 2024", 2), ("Кампания 2025", 2)]
    # The same hole ids live in both sets.
    holes = {d["id"]: [h["hole_id"] for h in db_client.get(f"/api/sets/{d['id']}/holes").json()] for d in tree}
    assert holes[first] == holes[new_set["set_id"]]
    assert commit({}).status_code == 422


def test_rename_and_delete_dataset(db_client):
    pid = _project(db_client)
    sid = db_client.post(f"/api/projects/{pid}/sets", json={"name": "A"}).json()["id"]

    assert db_client.patch(f"/api/datasets/{sid}", json={"name": "B"}).json()["name"] == "B"
    assert db_client.delete(f"/api/datasets/{sid}").status_code == 204
    assert db_client.get(f"/api/projects/{pid}/datasets").json() == []
    assert db_client.delete(f"/api/datasets/{sid}").status_code == 404


def test_upload_tridb_geometry_patch_and_delete(db_client, tmp_path):
    pid = _project(db_client)
    path = tmp_path / "Pit.tridb"
    make_tridb(path, [("выемка", 0x0080FF, CUBE_V + [500000, 4500000, 100], CUBE_T), ("насыпь", None, CUBE_V, CUBE_T[:4])])

    created = db_client.post(f"/api/projects/{pid}/wireframes", files={"file": ("Pit.tridb", path.read_bytes())})

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "wireframes" and body["name"] == "Pit.tridb" and body["meta"]["format"] == "Micromine TRIDB"
    pit, dump = body["wireframes"]
    assert (pit["name"], pit["color"], pit["point_count"], pit["triangle_count"]) == ("выемка", "#ff8000", 8, 12)
    assert pit["stats"]["closed"] is True and pit["stats"]["volume"] == 1.0
    assert pit["extent"] == {"min": [500000.0, 4500000.0, 100.0], "max": [500001.0, 4500001.0, 101.0]}
    assert dump["color"].startswith("#") and dump["stats"]["closed"] is False

    geometry = db_client.get(f"/api/wireframes/{pit['id']}/geometry").json()
    vertices = np.frombuffer(base64.b64decode(geometry["vertices"]), dtype="<f4").reshape(-1, 3)
    triangles = np.frombuffer(base64.b64decode(geometry["triangles"]), dtype="<u4").reshape(-1, 3)
    assert geometry["origin"] == [500000.0, 4500000.0, 100.0]
    assert np.allclose(vertices + geometry["origin"], CUBE_V + [500000, 4500000, 100])
    assert np.array_equal(triangles, CUBE_T)

    patched = db_client.patch(f"/api/wireframes/{pit['id']}", json={"name": "Карьер", "color": "#00AA00"}).json()
    assert (patched["name"], patched["color"]) == ("Карьер", "#00aa00")
    assert db_client.patch(f"/api/wireframes/{pit['id']}", json={"color": "red"}).status_code == 422

    assert db_client.delete(f"/api/wireframes/{pit['id']}").status_code == 204
    assert db_client.delete(f"/api/wireframes/{dump['id']}").status_code == 204
    assert db_client.get(f"/api/projects/{pid}/datasets").json() == []  # the file entry went with its last wireframe


def test_upload_dxf_polyface_and_3dface(db_client, tmp_path):
    pid = _project(db_client)
    doc = ezdxf.new("R2004")
    msp = doc.modelspace()
    doc.layers.add("FAULTS", color=1)
    face = msp.add_polyface(dxfattribs={"layer": "FAULTS"})
    face.append_faces([[tuple(CUBE_V[i]) for i in tri] for tri in CUBE_T])
    msp.add_3dface([(10, 0, 0), (11, 0, 0), (11, 1, 0), (10, 1, 0)], dxfattribs={"layer": "PLANE", "color": 3})
    msp.add_line((0, 0, 0), (5, 5, 5), dxfattribs={"layer": "LINES"})
    path = tmp_path / "faults.dxf"
    doc.saveas(path)

    body = db_client.post(f"/api/projects/{pid}/wireframes", files={"file": ("faults.dxf", path.read_bytes())}).json()

    items = {w["name"]: w for w in body["wireframes"]}
    assert set(items) == {"FAULTS", "PLANE"}  # one wireframe per layer; plain lines are not wireframes
    assert (items["FAULTS"]["point_count"], items["FAULTS"]["triangle_count"]) == (8, 12)  # corners welded
    assert items["FAULTS"]["color"] == "#ff0000" and items["FAULTS"]["stats"]["closed"] is True
    assert items["PLANE"]["triangle_count"] == 2 and items["PLANE"]["color"] == "#00ff00"


def test_upload_rejects_unknown_and_broken_files(db_client):
    pid = _project(db_client)

    assert db_client.post(f"/api/projects/{pid}/wireframes", files={"file": ("a.obj", b"v 0 0 0")}).status_code == 415
    broken = db_client.post(f"/api/projects/{pid}/wireframes", files={"file": ("a.tridb", b"garbage")})
    assert broken.status_code == 422 and "TRIDB" in broken.json()["detail"]
    empty = ezdxf.new()
    empty.modelspace().add_line((0, 0), (1, 1))
    from io import StringIO

    text = StringIO()
    empty.write(text)
    no_mesh = db_client.post(f"/api/projects/{pid}/wireframes", files={"file": ("lines.dxf", text.getvalue().encode())})
    assert no_mesh.status_code == 422 and "каркас" in no_mesh.json()["detail"]
    assert db_client.get(f"/api/projects/{pid}/datasets").json() == []


def test_assistant_tools_need_set_id_when_ambiguous(db_client, db_engine):
    from sqlalchemy.orm import Session

    from app.assistant import tools

    pid = _project(db_client)
    first = db_client.post(f"/api/projects/{pid}/sets", json={"name": "A"}).json()["id"]
    with Session(db_engine) as s:
        assert tools.resolve_set(s, pid, None) == first
        db_client.post(f"/api/projects/{pid}/sets", json={"name": "B"})
        text, ok = tools.run_data_tool(s, pid, "data_list_holes", {})
        assert not ok and "set_id" in text and "«A»" in text
        assert tools.run_data_tool(s, pid, "data_list_holes", {"set_id": first})[1] is True
        assert tools.run_data_tool(s, pid, "data_list_holes", {"set_id": 999999})[1] is False
        tree, ok = tools.run_data_tool(s, pid, "data_list_datasets", {})
        assert ok and '"name": "B"' in tree
