"""Why a job waits in the queue: no worker running, or the jobs ahead of it."""

import dataclasses
import json
import time

import pytest

from app.jobs import beats
from tests.test_storage_jobs import _project, upload
from tests.test_datasets import CUBE_T, CUBE_V, make_tridb


@pytest.fixture
def beat_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(beats, "settings", dataclasses.replace(beats.settings, data_dir=tmp_path))
    return tmp_path / "workers"


def test_beats_alive_stopped_and_kinds(beat_folder):
    beats.write("w1", None, 7, time.time() - 100)
    beats.write("quick", ["geometry.operation"], None, time.time())
    workers = beats.read_all()
    assert [w["name"] for w in workers] == ["quick", "w1"]
    assert all(w["alive"] for w in workers)
    assert beats.takes(workers[1], "import.objects") and not beats.takes(workers[0], "import.objects")
    later = beats.read_all(now=time.time() + 60)
    assert not any(w["alive"] for w in later)
    beats.remove("w1")
    assert [w["name"] for w in beats.read_all()] == ["quick"]


def _queued_job(db_client, tmp_path, pid=None):
    pid = pid or _project(db_client)
    path = tmp_path / f"a{time.monotonic_ns()}.tridb"
    make_tridb(path, [("Тело", 255, CUBE_V, CUBE_T)])
    data = path.read_bytes()
    uid = upload(db_client, pid, "a.tridb", data, chunk=len(data))
    return db_client.post(f"/api/projects/{pid}/imports", json={"uploads": [uid]}).json()


def test_a_queued_job_says_no_worker_is_running(db_client, beat_folder, tmp_path):
    job = _queued_job(db_client, tmp_path)
    # Only a worker for operations is alive, and the general one stopped a minute ago while importing.
    beats.write("quick", ["geometry.operation"], None, time.time())
    (beat_folder / "worker-1.json").write_text(json.dumps({"name": "worker-1", "kinds": None, "job": 19, "started": time.time() - 600,
                                                            "seen": time.time() - 60}), encoding="utf-8")
    detail = db_client.get(f"/api/jobs/{job['id']}").json()
    assert detail["status"] == "queued"
    assert "Нет работающего обработчика" in detail["waiting"]
    assert "worker-1" in detail["waiting"] and "№19" in detail["waiting"] and "docker compose up -d worker" in detail["waiting"]


def test_a_queued_job_names_the_jobs_ahead(db_client, beat_folder, tmp_path):
    first = _queued_job(db_client, tmp_path)
    second = _queued_job(db_client, tmp_path, first["project_id"])
    beats.write("worker-1", None, None, time.time())
    state = db_client.get("/api/jobs/queue").json()
    assert [w["name"] for w in state["workers"]] == ["worker-1"]
    assert [a["id"] for a in state["active"]] == [first["id"], second["id"]]
    assert state["waiting"][str(second["id"])].startswith("Впереди заданий: 1.")
    assert db_client.get(f"/api/jobs/{first['id']}").json()["waiting"].startswith("Ждёт свободного обработчика")
