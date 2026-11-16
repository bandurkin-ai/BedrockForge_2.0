"""OMF v1: reader, writer, import into datasets, export job and download."""

from __future__ import annotations

import json
import os
import struct
import time
import uuid
import zlib
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.exporting.files import export_path
from app.formats.omf import MAGIC, OmfFormatError, OmfWriter, describe, read_omf
from app.formats.registry import detect
from app.storage import blobs, gc
from tests.test_storage_jobs import upload

REFERENCE = Path(__file__).parent / "fixtures" / "omf_v1_test_file.omf"


def _project(client, name="Проект OMF") -> int:
    return client.post("/api/projects", json={"name": name}).json()["id"]


# ------------------------------------------------------------------- a file written by hand
class RawOmf:
    """An OMF v1 file assembled from JSON and zlib blocks, independent of OmfWriter."""

    def __init__(self) -> None:
        self.body = bytearray()
        self.objects: dict[str, dict] = {}
        self.elements: list[str] = []

    def add(self, cls: str, **fields) -> str:
        uid = str(uuid.uuid4())
        self.objects[uid] = {"date_created": "2026-01-01T00:00:00Z", "date_modified": "2026-01-01T00:00:00Z", **fields, "__class__": cls}
        return uid

    def array(self, cls: str, values, dtype: str) -> str:
        data = zlib.compress(np.asarray(values, dtype=dtype).tobytes())
        start = 60 + len(self.body)
        self.body += data
        return self.add(cls, array={"start": start, "dtype": dtype, "length": len(data)})

    def element(self, cls: str, name: str, geometry: str, data=(), **extra) -> str:
        uid = self.add(cls, name=name, description="", data=list(data), color=[10, 20, 30], subtype="x", geometry=geometry, **extra)
        self.elements.append(uid)
        return uid

    def write(self, path: Path, version: bytes = b"OMF-v0.9.0", origin=(0.0, 0.0, 0.0)) -> Path:
        project = str(uuid.uuid4())
        self.objects[project] = {"name": "Ручной", "description": "", "author": "", "revision": "", "units": "",
                                 "elements": self.elements, "origin": list(origin), "__class__": "Project",
                                 "date_created": "2026-01-01T00:00:00Z", "date_modified": "2026-01-01T00:00:00Z"}
        js = json.dumps(self.objects).encode()
        path.write_bytes(MAGIC + struct.pack("<32s", version) + uuid.UUID(project).bytes
                         + struct.pack("<Q", 60 + len(self.body)) + bytes(self.body) + js)
        return path


def test_reference_file_from_omf_python():
    c = read_omf(REFERENCE)
    assert c.project == "Test project"
    tri, grid = c.meshes
    assert (tri.name, tri.vertices.shape, tri.triangles.shape, tri.color) == ("trisurf", (100, 3), (50, 3), "#64c8c8")
    # 10 × 15 cells: 11 × 16 nodes, two triangles per cell; offset_w moves the nodes along u × v = −Y.
    assert grid.vertices.shape == (176, 3) and grid.triangles.shape == (300, 3)
    assert grid.vertices[:, 0].min() == 50 and grid.vertices[:, 0].max() == 60
    assert grid.vertices[:, 2].min() == 50 and grid.vertices[:, 2].max() == 65
    assert grid.vertices[:, 1].max() <= 50 and grid.vertices[:, 1].min() < 50
    (layer,) = c.line_layers
    assert layer.name == "Random Line" and layer.color == "#0000ff"
    # 50 segments; two of them follow each other and make one line of three points.
    assert len(layer.lines) == 49 and sum(len(x.vertices) - 1 for x in layer.lines) == 50
    ((name, table, _color, _units),) = c.blocks
    assert name == "vol" and len(table.centres) == 3000 and table.sizes.min() == table.sizes.max() == 1.0
    assert table.centres.min(axis=0).tolist() == [10.5, 10.5, -9.5]
    assert table.grid_hint == {"origin": [10.0, 10.0, -10.0], "size": [1.0, 1.0, 1.0], "at": "corner"}
    (col,) = table.attributes
    assert col.name == "Random Data" and col.kind == "number" and np.isfinite(col.values).all()
    text_ = "\n".join(c.warnings)
    assert "Точки «Random Points»" in text_ and "текстуры (2)" in text_ and "текстуры (1)" in text_
    assert "rand face data" in text_ and "rand segment data" in text_


def test_cells_and_nodes_are_in_fortran_order(tmp_path):
    """Cell n of a 2 × 3 × 4 grid is i + 2·(j + 3·k): u changes fastest, then v, then w."""
    raw = RawOmf()
    nu, nv, nw = 2, 3, 4
    tu, tv, tw = [1.0, 2.0], [1.0, 1.0, 3.0], [0.5] * 4
    values = raw.array("ScalarArray", np.arange(nu * nv * nw), "<f8")
    data = raw.add("ScalarData", name="N", description="", array=values, location="cells")
    geom = raw.add("VolumeGridGeometry", origin=[100.0, 200.0, 300.0], tensor_u=tu, tensor_v=tv, tensor_w=tw,
                   axis_u=[1.0, 0.0, 0.0], axis_v=[0.0, 1.0, 0.0], axis_w=[0.0, 0.0, 1.0])
    raw.element("VolumeElement", "vol", geom, [data])
    c = read_omf(raw.write(tmp_path / "f.omf", origin=(1000.0, 0.0, 0.0)))
    ((_, t, _, _),) = c.blocks
    v = t.attributes[0].values
    cu, cv, cw = np.cumsum(tu) - np.array(tu) / 2, np.cumsum(tv) - np.array(tv) / 2, np.cumsum(tw) - np.array(tw) / 2
    for i in range(nu):
        for j in range(nv):
            for k in range(nw):
                n = i + nu * (j + nv * k)
                row = int(np.flatnonzero(v == n)[0])
                assert t.centres[row].tolist() == [1100.0 + cu[i], 200.0 + cv[j], 300.0 + cw[k]]
                assert t.sizes[row].tolist() == [tu[i], tv[j], tw[k]]
    assert t.grid_hint is None and "переменный" in t.notes[0]


def test_axes_along_negative_and_swapped_coordinates_and_rotated_model(tmp_path):
    raw = RawOmf()
    g1 = raw.add("VolumeGridGeometry", origin=[0.0, 0.0, 0.0], tensor_u=[2.0], tensor_v=[1.0, 1.0], tensor_w=[1.0],
                 axis_u=[0.0, -1.0, 0.0], axis_v=[1.0, 0.0, 0.0], axis_w=[0.0, 0.0, 1.0])
    raw.element("VolumeElement", "swapped", g1)
    g2 = raw.add("VolumeGridGeometry", origin=[0.0, 0.0, 0.0], tensor_u=[1.0], tensor_v=[1.0], tensor_w=[1.0],
                 axis_u=[0.7071067811865476, 0.7071067811865476, 0.0], axis_v=[-0.7071067811865476, 0.7071067811865476, 0.0],
                 axis_w=[0.0, 0.0, 1.0])
    raw.element("VolumeElement", "rotated", g2)
    path = raw.write(tmp_path / "axes.omf")
    c = read_omf(path)
    ((name, t, _, _),) = c.blocks
    assert name == "swapped"
    assert sorted(t.centres.tolist()) == [[0.5, -1.0, 0.5], [1.5, -1.0, 0.5]]
    assert t.sizes.tolist() == [[1.0, 2.0, 1.0], [1.0, 2.0, 1.0]]
    assert any("«rotated»" in w and "повёрнута" in w for w in c.warnings)
    items = {i["name"]: i for i in describe(path)["items"]}
    assert items["rotated"]["skip"] == "модель повёрнута" and items["swapped"]["cells"] == 2


def test_writer_and_reader_round_trip_bit_for_bit(tmp_path):
    rng = np.random.default_rng(7)
    v = rng.uniform(0, 1, (50, 3)) + [512345.678901234, 4512345.12345678, 812.5]
    t = rng.integers(0, 50, (80, 3))
    square = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [0, 0, 0]], dtype=np.float64) + [5e5, 4.5e6, 100]
    open_line = rng.uniform(0, 100, (4, 3))
    lines = [(square, True), (open_line, False), (open_line[:1], False)]
    attrs = [{"Участок": "Восточный", "N": 1}, {"Участок": "Западный", "N": 2.5}, {"N": 3}]
    counts = [3, 2, 2]
    cells = np.array([0, 1, 5, 11])
    au = np.array([0.5, np.nan, 2.0, 3.25])
    zone = np.array([1, 0, -1, 1], dtype=np.int32)
    path = tmp_path / "rt.omf"
    with OmfWriter(path, "Проект", "описание") as w:
        w.add_surface("Рудное тело", v, t, "#e8873a")
        warnings = w.add_lines("Контуры", lines, attrs, "#4f9dde")
        w.add_volume("Модель", [100.0, 200.0, 300.0], [5.0, 5.0, 2.5], counts, cells,
                     [("AU", "number", au, None, "g/t"), ("ZONE", "category", zone, ["окисл.", "первич."], None)], None)
    assert warnings and "одной точки" in warnings[0]

    c = read_omf(path)
    assert c.project == "Проект" and c.description == "описание"
    (mesh,) = c.meshes
    assert mesh.name == "Рудное тело" and mesh.color == "#e8873a"
    assert mesh.vertices.tobytes() == v.tobytes() and (mesh.triangles == t).all()
    (layer,) = c.line_layers
    assert layer.color == "#4f9dde" and len(layer.lines) == 2
    sq, op = layer.lines
    assert sq.closed and sq.vertices.tobytes() == square.tobytes()
    assert not op.closed and op.vertices.tobytes() == open_line.tobytes()
    assert sq.attributes == {"Участок": "Восточный", "N": 1.0} and op.attributes == {"Участок": "Западный", "N": 2.5}
    ((name, table, _, units),) = c.blocks
    assert name == "Модель" and units == {"AU": "g/t"}
    # 12 cells, 3 without any value are dropped (cell 1 has ZONE only, cell 5 AU only).
    assert len(table.centres) == 4 and any("без значений" in n for n in table.notes)
    assert table.grid_hint == {"origin": [100.0, 200.0, 300.0], "size": [5.0, 5.0, 2.5], "at": "corner"}
    a, z = table.attributes
    assert a.values.tolist()[0] == 0.5 and np.isnan(a.values[1]) and a.values.tolist()[2:] == [2.0, 3.25]
    assert z.labels == ["окисл.", "первич."] and z.values.tolist() == [1, 0, -1, 1]
    assert table.centres[3].tolist() == [112.5, 207.5, 303.75]  # cell 11 = i 2, j 1, k 1


def test_files_that_are_not_omf_v1(tmp_path):
    good = REFERENCE.read_bytes()
    cases = {
        "text.omf": (b"hello world, not a file" * 10, "сигнатуры"),
        "v2.omf": (MAGIC + struct.pack("<32s", b"OMF-v2.0") + good[36:], "Версия OMF"),
        "cut.omf": (good[:5000], "JSON"),
        "nojson.omf": (good[:-200], "JSON"),
    }
    for name, (data, message) in cases.items():
        p = tmp_path / name
        p.write_bytes(data)
        with pytest.raises(OmfFormatError, match=message):
            read_omf(p)
    broken = bytearray(good)
    broken[38100:38140] = b"\x00" * 40  # inside the vertices of "trisurf" (start 38042)
    p = tmp_path / "zlib.omf"
    p.write_bytes(bytes(broken))
    with pytest.raises(OmfFormatError, match="повреждён|обрезан"):
        read_omf(p)
    zipped = tmp_path / "omf2.omf"
    zipped.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    d = detect("omf2.omf", zipped)
    assert d.kind == "unknown" and "OMF 2" in d.format
    assert detect("x.omf", REFERENCE).kind == "omf"
    assert describe(tmp_path / "text.omf")["error"].startswith("Это не файл OMF")


# ---------------------------------------------------------------------------------- API
def _run_import(db_client, run_jobs, pid: int, data: bytes, name: str = "Leapfrog.omf") -> dict:
    uid = upload(db_client, pid, name, data, chunk=1 << 15)
    (det,) = db_client.post(f"/api/projects/{pid}/detect", json={"uploads": [uid]}).json()
    assert det["kind"] == "omf" and det["format"] == "OMF v1", det
    assert "элементов 5" in det["preview"]["summary"]
    job = db_client.post(f"/api/projects/{pid}/imports", json={"uploads": [uid], "folder": "OMF"})
    assert job.status_code == 202, job.text
    run_jobs()
    done = db_client.get(f"/api/jobs/{job.json()['id']}").json()
    assert done["status"] == "done", done
    return done


def test_import_job_creates_wireframes_lines_and_blocks(db_client, run_jobs, db_engine):
    pid = _project(db_client)
    data = REFERENCE.read_bytes()
    done = _run_import(db_client, run_jobs, pid, data)
    datasets = db_client.get(f"/api/projects/{pid}/datasets").json()
    by_kind = {d["kind"]: d for d in datasets}
    assert sorted(by_kind) == ["blocks", "lines", "wireframes"] and len(datasets) == 3
    assert sorted(done["result"]["dataset_ids"]) == sorted(d["id"] for d in datasets)
    assert all(d["folder"] == "OMF" for d in datasets)
    assert [w["name"] for w in by_kind["wireframes"]["wireframes"]] == ["trisurf", "gridsurf"]
    assert by_kind["lines"]["lines"][0]["name"] == "Random Line"
    blocks = by_kind["blocks"]
    assert blocks["name"] == "Leapfrog: vol" and blocks["meta"]["block_count"] == 3000
    assert blocks["meta"]["grid"]["counts"] == [10, 15, 20] and not blocks["meta"]["grid"]["subblocked"]
    assert any("Точки «Random Points»" in w for w in blocks["meta"]["warnings"])
    warnings = [e["text"] for e in done["log"] if e["level"] == "warning"]
    assert any("текстуры" in w for w in warnings) and any("Random Points" in w for w in warnings)
    # One source file for the three datasets, moved out of the uploads.
    with Session(db_engine) as s:
        sources = {r[0] for r in s.execute(text("SELECT blobs->>'source' FROM dataset WHERE project_id = :p"), {"p": pid})}
    assert len(sources) == 1 and blobs.read_bytes(sources.pop()) == data


def test_import_of_a_file_with_points_only_fails_with_the_reason(db_client, run_jobs, tmp_path):
    raw = RawOmf()
    geom = raw.add("PointSetGeometry", origin=[0.0, 0.0, 0.0], vertices=raw.array("Vector3Array", np.zeros((2, 3)), "<f8"))
    raw.element("PointSetElement", "Устья", geom)
    pid = _project(db_client)
    uid = upload(db_client, pid, "points.omf", raw.write(tmp_path / "p.omf").read_bytes(), chunk=1 << 15)
    job = db_client.post(f"/api/projects/{pid}/imports", json={"uploads": [uid]}).json()
    run_jobs()
    done = db_client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "failed" and "нет объектов" in done["error"] and "Устья" in done["error"]
    assert db_client.get(f"/api/uploads/{uid}").status_code == 200  # kept for another attempt


def test_export_job_download_and_import_back(db_client, run_jobs):
    pid = _project(db_client)
    _run_import(db_client, run_jobs, pid, REFERENCE.read_bytes())
    datasets = db_client.get(f"/api/projects/{pid}/datasets").json()
    ids = [d["id"] for d in datasets]

    r = db_client.post(f"/api/projects/{pid}/exports", json={"format": "omf", "dataset_ids": ids, "filename": "Мой/экспорт"})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["kind"] == "export.omf"
    assert db_client.get(f"/api/jobs/{job['id']}/download").status_code == 409
    run_jobs()
    done = db_client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    assert done["result"]["filename"] == "Мой_экспорт.omf" and done["result"]["elements"] == 4
    got = db_client.get(f"/api/jobs/{job['id']}/download")
    assert got.status_code == 200
    assert got.headers["content-disposition"] == "attachment; filename=\"export.omf\"; filename*=utf-8''%D0%9C%D0%BE%D0%B9_%D1%8D%D0%BA%D1%81%D0%BF%D0%BE%D1%80%D1%82.omf"
    assert got.content[:4] == MAGIC and len(got.content) == done["result"]["size"]

    # The exported file imported again: the same geometry and block values.
    pid2 = _project(db_client, "Второй")
    uid = upload(db_client, pid2, "back.omf", got.content, chunk=1 << 16)
    job2 = db_client.post(f"/api/projects/{pid2}/imports", json={"uploads": [uid]}).json()
    run_jobs()
    assert db_client.get(f"/api/jobs/{job2['id']}").json()["status"] == "done"
    again = {d["kind"]: d for d in db_client.get(f"/api/projects/{pid2}/datasets").json()}
    first = {d["kind"]: d for d in datasets}
    for a, b in zip(first["wireframes"]["wireframes"], again["wireframes"]["wireframes"]):
        assert (a["name"], a["point_count"], a["triangle_count"], a["extent"]) == (b["name"], b["point_count"], b["triangle_count"], b["extent"])
    m1, m2 = first["blocks"]["meta"], again["blocks"]["meta"]
    assert m1["block_count"] == m2["block_count"] and m1["grid"]["counts"] == m2["grid"]["counts"]
    assert m1["attributes"][0]["stats"] == m2["attributes"][0]["stats"]


def test_export_refuses_subblocked_models_and_other_kinds(db_client, run_jobs, db_engine, tmp_path):
    from tests.test_blockmodels import _import, make_blocks

    pid, model = _import(db_client, run_jobs, tmp_path, make_blocks())
    r = db_client.post(f"/api/projects/{pid}/exports", json={"dataset_ids": [model["id"]]})
    assert r.status_code == 202
    run_jobs()
    failed = db_client.get(f"/api/jobs/{r.json()['id']}").json()
    assert failed["status"] == "failed" and "субблочная" in failed["error"]

    drill = db_client.post(f"/api/projects/{pid}/sets", json={"name": "Скважины"}).json()
    r = db_client.post(f"/api/projects/{pid}/exports", json={"dataset_ids": [drill["id"]]})
    assert r.status_code == 422 and "Скважины" in r.json()["detail"]
    assert db_client.post(f"/api/projects/{pid}/exports", json={"dataset_ids": [999999]}).status_code == 404
    assert db_client.post(f"/api/projects/{pid}/exports", json={"dataset_ids": []}).status_code == 422


def test_gc_removes_old_export_files(db_client, db_engine):
    old, fresh = export_path(900001, ".omf"), export_path(900002, ".omf")
    old.write_bytes(b"old")
    fresh.write_bytes(b"new")
    past = time.time() - 3 * 24 * 3600
    os.utime(old, (past, past))
    with Session(db_engine) as s:
        report = gc.collect(s, export_ttl=timedelta(hours=48))
    assert report.exports == 1 and not old.exists() and fresh.exists()
    fresh.unlink()
