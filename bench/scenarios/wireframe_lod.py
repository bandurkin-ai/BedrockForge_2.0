"""The levels of detail of a wireframe outside the process of the API (ADR 0015 §8 and «Замеры»: «Часть каркаса
без кеша уровней; `GET /health` во время `wireframe.lod` — часть 64 тыс.; каркас 1 млн — ≤ 50 мс; ≤ 100 мс;
задание ≤ 8 с»).

One wireframe — a pit surface (`tools/gen/wireframes.py`: relief with noise, level berms and plane faces) — in a
project without its cache of levels, as a `.bfggis` brings it. A core in this process (the API through
`TestClient`, its scheduler with real worker processes) opens the project, and as the 3D window does:
  standin_s       every part asked at level 1/64 — none is in the cache: the whole part comes back as a stand-in
                  and the first one asks for `wireframe.lod`; the median of a part (`standin_max_s` — the worst);
  health_max_s    `GET /health` every 20 ms while the job makes the levels in the worker `quick`: the worst;
  job_s           from the first stand-in to the end of the job;
  cached_s        every part at level 1/64 again: from the cache now, with their ETags.
What is checked: one job for all the parts, every part a stand-in before and a cached level after it, pyfqmr
not loaded here; `worker_peak_mb` — the peak of the worker that made the levels (Linux).
"""

from __future__ import annotations

import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any

TOKEN = "bench-token-0123456789"


def prepare(params: dict[str, Any], work: Path) -> None:
    from tools.gen.wireframes import pit_surface

    from bf_core.db import dialect
    from bf_core.projects.project import create_folder
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as wf_store

    v, t = pit_surface(int(params["triangles"])).mesh()
    root = work / "Проекты"
    root.mkdir()
    folder = create_folder(root, "Копии для показа", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        blobs = BlobStore(folder)
        staged = wf_store.stage_wireframe(blobs, v, t)
        try:
            with dialect.write_tx(engine) as conn:
                wf_id = wf_store.commit_wireframe(conn, blobs, staged, name="Карьер", color="#8a99a8")
        finally:
            staged.discard()
    finally:
        engine.dispose()
    (work / "wireframe.txt").write_text(str(wf_id), encoding="utf-8")


def load() -> None:
    import numpy  # noqa: F401
    from fastapi.testclient import TestClient  # noqa: F401

    import bf_core.api.app  # noqa: F401
    import bf_core.viz.meshes  # noqa: F401
    import bf_core.wireframes.lod_job  # noqa: F401


def _peak_mb(pid: int) -> float | None:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except OSError:
        pass
    return None


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import os

    from fastapi.testclient import TestClient

    from bf_core.api.app import CoreState, create_app
    from bf_core.jobs.service import jobs_of
    from bf_core.viz import binary
    from bf_core.wireframes import lod_job

    os.environ.setdefault("BF_EXTRA_JOB_MODULES", "bf_core.wireframes.lod_job")  # until HANDLER_MODULES has it
    os.environ["BF_LOCAL_DIR"] = str(work / "local")
    api = "/api/v1"
    wf_id = int((work / "wireframe.txt").read_text(encoding="utf-8"))
    state = CoreState(token=TOKEN, projects_dir=str(work / "Проекты"), data_dir=str(work / "roaming"))
    headers = {"X-BF-Token": TOKEN}
    with TestClient(create_app(state), base_url="http://127.0.0.1:5555", headers=headers) as c:
        (listed,) = c.get(f"{api}/projects").json()["projects"]
        shutil.rmtree(Path(listed["path"]) / "cache" / "mesh", ignore_errors=True)  # a repeat starts without them
        assert c.post(f"{api}/projects/{listed['uid']}/open").status_code == 200
        before = max([j["id"] for j in c.get(f"{api}/jobs").json()["jobs"] if j["source"] != "instance"], default=0)
        sched = jobs_of(state)
        deadline = time.monotonic() + 60
        while not all(w.ready for w in sched.workers):  # the workers start with the project (spawn, imports)
            assert time.monotonic() < deadline, "workers did not start"
            time.sleep(0.02)
        head = binary.decode(c.get(f"{api}/viz/wireframes/{wf_id}").content)[0]
        parts = head["parts"]

        times: list[float] = []
        t_job = time.perf_counter()
        for p in parts:
            t0 = time.perf_counter()
            r = c.get(f"{api}/viz/wireframes/{wf_id}/parts/{p['k']}", params={"lod": 2})
            times.append(time.perf_counter() - t0)
            part_head = binary.decode(r.content)[0]
            assert r.status_code == 200 and "etag" not in r.headers and part_head["standin"], part_head
            assert part_head["triangles"] == p["n"]
        jobs = [
            j
            for j in c.get(f"{api}/jobs").json()["jobs"]
            if j["kind"].startswith(lod_job.KIND) and j["source"] != "instance" and j["id"] > before
        ]
        assert len(jobs) == 1, jobs
        job = jobs[0]
        pid = next(w.process.pid for w in sched.workers if w.queue == job["queue"])

        health: list[float] = []
        while True:
            t0 = time.perf_counter()
            assert c.get(f"{api}/health").status_code == 200
            health.append(time.perf_counter() - t0)
            row = c.get(f"{api}/jobs/{job['source']}/{job['id']}").json()
            if row["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.02)
        job_s = time.perf_counter() - t_job
        assert row["status"] == "done", row

        t0 = time.perf_counter()
        for p in parts:
            r = c.get(f"{api}/viz/wireframes/{wf_id}/parts/{p['k']}", params={"lod": 2})
            assert r.headers["etag"] == p["etags"][2] and "standin" not in binary.decode(r.content)[0]
        cached_s = time.perf_counter() - t0
        worker_peak = _peak_mb(pid)
        loaded = sorted(m for m in sys.modules if m.split(".")[0] == "pyfqmr" or m == "bf_core.geo.flat")
        assert loaded == [], loaded
    result = row["result"]
    return {
        "triangles": head["triangles"],
        "parts": len(parts),
        "part_triangles_max": max(p["n"] for p in parts),
        "queue": job["queue"],
        "lod_jobs": len(jobs),
        "lod_triangles": result["triangles"],
        "standin_s": round(statistics.median(times), 4),
        "standin_max_s": round(max(times), 4),
        "health_max_s": round(max(health), 4),
        "health_median_s": round(statistics.median(health), 4),
        "health_requests": len(health),
        "job_s": round(job_s, 3),
        "cached_s": round(cached_s, 3),
        "worker_peak_mb": worker_peak,
    }
