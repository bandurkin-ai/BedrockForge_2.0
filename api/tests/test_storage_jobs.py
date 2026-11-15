"""Stage 7: object store, resumable uploads, background jobs, garbage collection, tree folders."""

import hashlib
import json
import time
from datetime import timedelta

import ezdxf
import numpy as np
import pytest
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.formats.registry import detect
from app.jobs import queue
from app.jobs.worker import HANDLERS, JobContext, run_job
from app.models import Job
from app.storage import blobs, gc, geometry
from tests.test_datasets import CUBE_T, CUBE_V, make_tridb


def _project(client, name="Проект") -> int:
    return client.post("/api/projects", json={"name": name}).json()["id"]


def upload(client, project_id: int, filename: str, data: bytes, chunk: int = 7) -> str:
    """Sends a file in chunks with checksums, as the web client does."""
    created = client.post(f"/api/projects/{project_id}/uploads", json={"filename": filename, "size": len(data)})
    assert created.status_code == 201, created.text
    upload_id = created.json()["id"]
    offset = 0
    while offset < len(data):
        part = data[offset:offset + chunk]
        r = client.put(f"/api/uploads/{upload_id}?offset={offset}", content=part,
                       headers={"X-Chunk-SHA256": hashlib.sha256(part).hexdigest()})
        assert r.status_code == 200, r.text
        offset = r.json()["received"]
    assert client.get(f"/api/uploads/{upload_id}").json()["status"] == "complete"
    return upload_id


# ---- store


def test_store_keeps_float64_bit_for_bit(db_engine):
    v = np.array([[512345.123456789012, 4512345.987654321, 1234.000000001]] * 3) + np.random.default_rng(1).random((3, 3))
    with Session(db_engine) as s:
        sha = blobs.put_array(s, v, "<f8")
        s.commit()
        assert s.execute(text("SELECT size FROM blob WHERE sha256 = :s"), {"s": sha}).scalar() == v.nbytes
    back = blobs.read_array(sha, "<f8", 3)
    assert back.tobytes() == v.astype("<f8").tobytes()  # not "close": identical
    assert sha == hashlib.sha256(v.astype("<f8").tobytes()).hexdigest()
    with pytest.raises(ValueError):
        blobs.path_of("../../etc/passwd")


def test_render_copy_keeps_half_a_millimetre_across_16_km(db_engine):
    """docs/ROADMAP.md 2.3: float32 relative to the centre origin, 16 km object at UTM coordinates."""
    rng = np.random.default_rng(7)
    v = np.column_stack([
        rng.uniform(500000, 516000, 200000), rng.uniform(4500000, 4516000, 200000), rng.uniform(-500, 1500, 200000)
    ])
    t = np.arange(len(v) - len(v) % 3, dtype=np.int32).reshape(-1, 3)
    with Session(db_engine) as s:
        stored = geometry.store_mesh(s, v, t)
        s.commit()
    render = blobs.read_array(stored.blobs["render_vertices"], "<f4", 3).astype(np.float64)
    error = np.abs(render + stored.origin - v).max()
    assert error <= 0.0005
    assert stored.extent["min"] == v.min(axis=0).tolist()  # exact, not rounded
    exact, _ = geometry.mesh_arrays(stored.blobs)
    assert exact.tobytes() == v.tobytes()


# ---- uploads


def test_chunked_upload_offsets_checksums_and_resume(db_client):
    pid = _project(db_client)
    data = bytes(range(256)) * 3
    created = db_client.post(f"/api/projects/{pid}/uploads", json={"filename": "a/b/big.tridb", "size": len(data)}).json()
    uid = created["id"]
    assert created["filename"] == "big.tridb" and created["received"] == 0 and created["chunk_size"] > 0

    assert db_client.put(f"/api/uploads/{uid}?offset=0", content=data[:100]).json()["received"] == 100
    # A repeated or skipped chunk is refused; the answer says where to continue.
    for offset in (0, 150):
        r = db_client.put(f"/api/uploads/{uid}?offset={offset}", content=data[offset:offset + 10])
        assert r.status_code == 409 and r.json()["detail"]["received"] == 100
    # A corrupted chunk is dropped.
    bad = db_client.put(f"/api/uploads/{uid}?offset=100", content=data[100:200],
                        headers={"X-Chunk-SHA256": "0" * 64})
    assert bad.status_code == 422
    assert db_client.get(f"/api/uploads/{uid}").json()["received"] == 100
    # Beyond the declared size.
    assert db_client.put(f"/api/uploads/{uid}?offset=100", content=data[100:] + b"x").status_code == 413
    # Resume and finish.
    done = db_client.put(f"/api/uploads/{uid}?offset=100", content=data[100:]).json()
    assert done["received"] == len(data) and done["status"] == "complete"
    from app.routers.uploads import part_path

    assert part_path(uid).read_bytes() == data
    assert db_client.put(f"/api/uploads/{uid}?offset={len(data)}", content=b"x").status_code == 409

    assert db_client.delete(f"/api/uploads/{uid}").status_code == 204
    assert not part_path(uid).exists()
    assert db_client.get(f"/api/uploads/{uid}").status_code == 404


def test_upload_size_limit(db_client):
    pid = _project(db_client)
    r = db_client.post(f"/api/projects/{pid}/uploads", json={"filename": "huge.dm", "size": 5 << 40})
    assert r.status_code == 413


# ---- import jobs


def test_import_job_from_upload(db_client, run_jobs, tmp_path):
    pid = _project(db_client)
    path = tmp_path / "Pit.tridb"
    make_tridb(path, [("выемка", 0x0080FF, CUBE_V + [500000, 4500000, 100], CUBE_T)])
    data = path.read_bytes()
    uid = upload(db_client, pid, "Pit.tridb", data, chunk=4096)

    detected = db_client.post(f"/api/projects/{pid}/detect", json={"uploads": [uid]}).json()
    assert detected[0]["kind"] == "wireframes" and detected[0]["format"] == "Micromine TRIDB"

    job = db_client.post(f"/api/projects/{pid}/imports", json={"uploads": [uid], "folder": " Карьер / 2026 "})
    assert job.status_code == 202, job.text
    job = job.json()
    assert job["status"] == "queued" and job["kind"] == "import.objects" and "Pit.tridb" in job["title"]
    assert db_client.get(f"/api/projects/{pid}/datasets").json() == []  # nothing before the worker runs

    assert run_jobs() == 2  # the import, then checks and levels of detail of its wireframes
    done = db_client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done" and done["progress"] == 1.0, done
    assert any("Pit.tridb" in e["text"] for e in done["log"])
    (dataset,) = db_client.get(f"/api/projects/{pid}/datasets").json()
    assert done["result"]["dataset_ids"] == [dataset["id"]]
    assert dataset["folder"] == "Карьер/2026" and dataset["wireframes"][0]["stats"]["volume"] == 1.0
    # The upload became the object's source file: moved, not copied.
    assert db_client.get(f"/api/uploads/{uid}").status_code == 404
    with db_client.app.dependency_overrides[next(iter(db_client.app.dependency_overrides))]().__next__() as s:
        source = s.execute(text("SELECT blobs->>'source' FROM dataset WHERE id = :i"), {"i": dataset["id"]}).scalar()
    assert blobs.read_bytes(source) == data

    listed = db_client.get(f"/api/projects/{pid}/jobs").json()
    assert [j["kind"] for j in listed] == ["wireframes.derive", "import.objects"]
    stats = db_client.get(f"/api/projects/{pid}/datasets").json()[0]["wireframes"][0]["stats"]
    assert stats["checked"] and stats["orientation_conflicts"] == 0 and stats["self_intersections"]["pairs"] == 0


def test_import_job_failure_keeps_uploads(db_client, run_jobs):
    pid = _project(db_client)
    uid = upload(db_client, pid, "broken.tridb", b"not a database at all")
    job = db_client.post(f"/api/projects/{pid}/imports", json={"uploads": [uid]}).json()
    run_jobs()
    failed = db_client.get(f"/api/jobs/{job['id']}").json()
    assert failed["status"] == "failed" and "TRIDB" in failed["error"]
    assert db_client.get(f"/api/uploads/{uid}").json()["status"] == "complete"  # can be retried
    assert db_client.get(f"/api/projects/{pid}/datasets").json() == []


def test_import_requires_complete_uploads_and_a_kind(db_client):
    pid = _project(db_client)
    partial = db_client.post(f"/api/projects/{pid}/uploads", json={"filename": "x.tridb", "size": 10}).json()["id"]
    assert db_client.post(f"/api/projects/{pid}/imports", json={"uploads": [partial]}).status_code == 409
    unknown = upload(db_client, pid, "notes.docx", b"hello")
    assert db_client.post(f"/api/projects/{pid}/imports", json={"uploads": [unknown]}).status_code == 422
    other = _project(db_client, "Другой")
    assert db_client.post(f"/api/projects/{other}/imports", json={"uploads": [unknown]}).status_code == 404


def test_raster_import_job_pairs_georeference(db_client, run_jobs):
    import io

    from PIL import Image

    pid = _project(db_client)
    out = io.BytesIO()
    Image.new("RGB", (40, 20), "#336699").save(out, "PNG")
    ids = [upload(db_client, pid, "map.png", out.getvalue(), chunk=50),
           upload(db_client, pid, "map.pgw", b"2\n0\n0\n-2\n1001\n2999\n")]
    job = db_client.post(f"/api/projects/{pid}/imports", json={"uploads": ids}).json()
    run_jobs()
    assert db_client.get(f"/api/jobs/{job['id']}").json()["status"] == "done"
    (d,) = db_client.get(f"/api/projects/{pid}/datasets").json()
    assert d["kind"] == "rasters" and d["rasters"][0]["extent"] == {"min": [1000.0, 2960.0], "max": [1080.0, 3000.0]}
    image = db_client.get(f"/api/rasters/{d['rasters'][0]['id']}/image")
    assert image.status_code == 200 and image.headers["content-type"].startswith("image/") and len(image.content) > 100


def test_detect_dxf_faces_or_lines(tmp_path):
    faces = ezdxf.new()
    faces.modelspace().add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 0)])
    faces.saveas(tmp_path / "f.dxf")
    lines = ezdxf.new()
    lines.modelspace().add_lwpolyline([(0, 0), (1, 1)])
    lines.saveas(tmp_path / "l.dxf")
    assert detect("f.dxf", tmp_path / "f.dxf").kind == "wireframes"
    assert detect("l.dxf", tmp_path / "l.dxf").kind == "lines"
    assert detect("x.STR").kind == "lines" and detect("a.jgw").kind == "sidecar"
    assert detect("holes.csv").kind == "drillholes" and detect("x.ply").kind == "unknown" and detect("x.OBJ").kind == "wireframes"


# ---- job lifecycle


@pytest.fixture
def factory(db_engine):
    return lambda: Session(db_engine, expire_on_commit=False)


@pytest.fixture
def test_handlers():
    saved = dict(HANDLERS)

    def slow(ctx: JobContext, params: dict) -> dict:
        ctx.session.execute(text("INSERT INTO project (name) VALUES ('из задания')"))
        for i in range(1000):
            ctx.progress(i / 1000, f"шаг {i}", force=True)
        return {"ok": True}

    def boom(ctx: JobContext, params: dict) -> dict:
        raise RuntimeError("сломалось")

    HANDLERS["test.slow"] = slow
    HANDLERS["test.boom"] = boom
    yield
    HANDLERS.clear()
    HANDLERS.update(saved)


def _enqueue(factory, kind: str, project_id=None) -> int:
    with factory() as s:
        job = queue.enqueue(s, kind, {}, project_id=project_id)
        s.commit()
        return job.id


def _job(factory, job_id: int) -> Job:
    with factory() as s:
        return s.get(Job, job_id)


def test_cancel_queued_and_running_jobs(db_client, factory, test_handlers):
    queued = _enqueue(factory, "test.slow")
    assert db_client.post(f"/api/jobs/{queued}/cancel").json()["status"] == "cancelled"

    running = _enqueue(factory, "test.slow")
    with factory() as s:
        job = queue.claim(s, "w1")
    assert job.id == running and job.status == "running" and job.attempts == 1
    assert db_client.post(f"/api/jobs/{running}/cancel").json()["cancel_requested"] is True
    assert run_job(factory, job) == "cancelled"
    assert _job(factory, running).status == "cancelled"
    with factory() as s:  # the handler's writes were rolled back
        assert s.execute(text("SELECT count(*) FROM project WHERE name = 'из задания'")).scalar() == 0
    # Cancelling a finished job changes nothing.
    assert db_client.post(f"/api/jobs/{running}/cancel").json()["status"] == "cancelled"


def test_results_commit_with_done_status_and_errors_are_reported(db_client, factory, test_handlers, run_jobs):
    ok = _enqueue(factory, "test.slow")
    bad = _enqueue(factory, "test.boom")
    unknown = _enqueue(factory, "test.nothing")
    run_jobs()
    assert _job(factory, ok).status == "done" and _job(factory, ok).result == {"ok": True}
    with factory() as s:
        assert s.execute(text("SELECT count(*) FROM project WHERE name = 'из задания'")).scalar() == 1
    failed = _job(factory, bad)
    assert failed.status == "failed" and "RuntimeError: сломалось" in failed.error
    assert any(e["level"] == "error" and "Traceback" in e["text"] for e in failed.log)
    assert _job(factory, unknown).status == "failed"


def test_lost_worker_job_is_requeued_then_failed(db_client, factory):
    job_id = _enqueue(factory, "test.slow")
    for attempt in (1, 2, 3):
        with factory() as s:
            job = queue.claim(s, f"w{attempt}")
            assert job.id == job_id and job.attempts == attempt
            s.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=queue.now() - timedelta(minutes=10)))
            s.commit()
            assert queue.requeue_stale(s) == [job_id]
        expected = "queued" if attempt < 3 else "failed"
        assert _job(factory, job_id).status == expected
    assert "попытки исчерпаны" in _job(factory, job_id).error


def test_job_events_stream_until_the_end(db_client, run_jobs, tmp_path):
    pid = _project(db_client)
    path = tmp_path / "p.tridb"
    make_tridb(path, [("a", None, CUBE_V, CUBE_T)])
    uid = upload(db_client, pid, "p.tridb", path.read_bytes(), chunk=1 << 20)
    job = db_client.post(f"/api/projects/{pid}/imports", json={"uploads": [uid]}).json()
    run_jobs()
    with db_client.stream("GET", f"/api/jobs/{job['id']}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert events[0]["status"] == "done" and events[-1] == {"id": job["id"]}

    with db_client.stream("GET", f"/api/projects/{pid}/jobs/events?seconds=0.6") as r:
        body = "".join(r.iter_text())
    assert f'"id": {job["id"]}' in body


# ---- garbage collection


def test_gc_removes_unreferenced_blobs_stray_files_and_stale_uploads(db_client, factory, tmp_path):
    pid = _project(db_client)
    path = tmp_path / "p.tridb"
    make_tridb(path, [("a", None, CUBE_V, CUBE_T)])
    kept = db_client.post(f"/api/projects/{pid}/wireframes", files={"file": ("p.tridb", path.read_bytes())}).json()
    with factory() as s:
        orphan = blobs.put_bytes(s, b"nobody uses me")
        s.commit()
        kept_shas = list(s.execute(text("SELECT blobs FROM wireframe")).scalar().values())
    stray = blobs.path_of("ab" * 32)
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"crashed writer")
    old = time.time() - 7200
    import os

    os.utime(stray, (old, old))
    stale_upload = db_client.post(f"/api/projects/{pid}/uploads", json={"filename": "x.dxf", "size": 5}).json()["id"]
    with factory() as s:
        s.execute(text("UPDATE upload SET updated_at = now() - interval '3 days' WHERE id = :i"), {"i": stale_upload})
        s.commit()

    with factory() as s:  # within the grace period nothing registered goes away
        first = gc.collect(s)
    assert first.blobs == 0 and blobs.exists(orphan)
    assert first.stray_files == 1 and first.uploads == 1 and not stray.exists()  # older than the grace period
    with factory() as s:
        assert gc.collect(s, grace=timedelta(0)).blobs >= 1
    assert not blobs.exists(orphan)
    assert all(blobs.exists(sha) for sha in kept_shas)
    assert db_client.get(f"/api/uploads/{stale_upload}").status_code == 404
    # The kept wireframe still loads.
    g = db_client.get(f"/api/wireframes/{kept['wireframes'][0]['id']}/geometry").json()
    assert db_client.get(g["vertices"]).status_code == 200

    # Deleting the object releases its files.
    db_client.delete(f"/api/datasets/{kept['id']}")
    with factory() as s:
        gc.collect(s, grace=timedelta(0))
    assert not any(blobs.exists(sha) for sha in kept_shas)


# ---- tree


def test_dataset_folders(db_client):
    pid = _project(db_client)
    d = db_client.post(f"/api/projects/{pid}/sets", json={"name": "Скважины"}).json()
    moved = db_client.patch(f"/api/datasets/{d['id']}", json={"folder": "/ Разведка\\2026 / "}).json()
    assert moved["folder"] == "Разведка/2026" and moved["name"] == "Скважины"
    renamed = db_client.patch(f"/api/datasets/{d['id']}", json={"name": "Скв. 2026"}).json()
    assert renamed["folder"] == "Разведка/2026"
    assert db_client.patch(f"/api/datasets/{d['id']}", json={"folder": ""}).json()["folder"] == ""


# ---- import wizard previews (7.6)


def test_detect_previews_tridb_dxf_str_and_images(db_client, tmp_path):
    import io

    from PIL import Image

    from tests.samples import collar_dat

    pid = _project(db_client)
    tridb = tmp_path / "Pit.tridb"
    make_tridb(tridb, [("выемка", None, CUBE_V, CUBE_T), ("насыпь", None, CUBE_V, CUBE_T[:4])])
    doc = ezdxf.new("R2004")
    msp = doc.modelspace()
    msp.add_polyface(dxfattribs={"layer": "FAULTS"}).append_faces([[(0, 0, 0), (1, 0, 0), (1, 1, 0)]])
    msp.add_lwpolyline([(0, 0), (1, 1)], dxfattribs={"layer": "ROADS"})
    doc.saveas(tmp_path / "f.dxf")
    png = io.BytesIO()
    Image.new("RGB", (30, 20)).save(png, "PNG")
    ids = [
        upload(db_client, pid, "Pit.tridb", tridb.read_bytes(), chunk=1 << 20),
        upload(db_client, pid, "f.dxf", (tmp_path / "f.dxf").read_bytes(), chunk=1 << 20),
        upload(db_client, pid, "map.png", png.getvalue(), chunk=1 << 20),
        upload(db_client, pid, "map.pgw", b"1\n0\n0\n-1\n0\n0\n"),
        upload(db_client, pid, "lonely.tab", b"!table"),
        upload(db_client, pid, "holes.dat", collar_dat(), chunk=1 << 20),
    ]
    rows = {r["filename"]: r for r in db_client.post(f"/api/projects/{pid}/detect", json={"uploads": ids}).json()}

    pit = rows["Pit.tridb"]["preview"]
    assert [(i["name"], i["triangles"]) for i in pit["items"]] == [("выемка", 12), ("насыпь", 4)]
    assert "треугольников: 16" in pit["summary"]
    dxf = rows["f.dxf"]
    assert dxf["kind"] == "wireframes"
    assert dxf["preview"]["entities"] == {"POLYFACE": 1, "LWPOLYLINE": 1} and dxf["preview"]["layers"] == 2
    assert rows["map.png"]["preview"]["width"] == 30 and rows["map.png"]["sidecars"] == ["map.pgw"]
    assert "Нет изображения" in rows["lonely.tab"]["note"]
    assert rows["holes.dat"]["kind"] == "drillholes"


def test_preview_of_micromine_str(tmp_path):
    from app.formats.inspect import inspect_str
    from tests.dat_builder import build

    data = build("ascii", [(b"EAST", "R", 8, 3), (b"NORTH", "R", 8, 3), (b"RL", "R", 8, 2), (b"JOIN", "N", 12, 0)],
                 [[1.0, 2.0, 3.0, "1"], [4.0, 5.0, 6.0, "1"], [7.0, 8.0, 9.0, "2"]])
    path = tmp_path / "a.str"
    path.write_bytes(data)
    p = inspect_str(path)
    assert p["format"] == "Micromine STR" and p["fields"] == ["EAST", "NORTH", "RL", "JOIN"]
    assert "точек ≈ 3" in p["summary"]


def test_unknown_handlers_reload_and_failed_job_retry(db_client, run_jobs, tmp_path, monkeypatch):
    """A worker that started with no handlers loads them before failing a job; a failed import
    can be run again with the same uploads."""
    from app.jobs import worker

    pid = _project(db_client)
    path = tmp_path / "Pit.tridb"
    make_tridb(path, [("выемка", 0x0080FF, CUBE_V + [500000, 4500000, 100], CUBE_T)])
    uid = upload(db_client, pid, "Pit.tridb", path.read_bytes(), chunk=4096)
    job = db_client.post(f"/api/projects/{pid}/imports", json={"uploads": [uid]}).json()

    saved = dict(worker.HANDLERS)
    worker.HANDLERS.clear()  # as after a half-finished import of app.jobs.handlers
    monkeypatch.setattr(worker, "_ensure_handlers", lambda: worker.HANDLERS.update(saved))
    run_jobs()
    assert db_client.get(f"/api/jobs/{job['id']}").json()["status"] == "done"

    # A job that failed keeps its uploads and can be retried.
    job2 = db_client.post(f"/api/projects/{pid}/imports", json={
        "uploads": [upload(db_client, pid, "Pit2.tridb", path.read_bytes(), chunk=4096)]}).json()
    db_client.post(f"/api/jobs/{job2['id']}/cancel")
    run_jobs()
    assert db_client.get(f"/api/jobs/{job2['id']}").json()["status"] == "cancelled"
    again = db_client.post(f"/api/jobs/{job2['id']}/retry")
    assert again.status_code == 200 and again.json()["status"] == "queued"
    run_jobs()
    assert db_client.get(f"/api/jobs/{job2['id']}").json()["status"] == "done"
    assert db_client.post(f"/api/jobs/{job2['id']}/retry").status_code == 409  # done: nothing to retry
