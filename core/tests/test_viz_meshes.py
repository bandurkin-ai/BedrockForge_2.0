"""Levels of detail of wireframes outside the process of the API (ADR 0015 §8, 9.15): a part whose level is not
in the cache is answered whole as a stand-in (`standin`, no ETag, `no-store`) and asks for one job
`wireframe.lod` a wireframe; the job fills the cache with the same answers `build_cache` makes, and the parts
then come with their ETags; a tiny part is its own level; pyfqmr is never loaded in the process of the API."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.jobs.context import JobCancelled
from bf_core.jobs.service import jobs_of
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from bf_core.viz import meshes as vm
from bf_core.wireframes import lod_job
from bf_core.wireframes import store as wf_store
from tests.test_import_job import API, wait_job
from tests.test_viz_mesh import cube, grid, hills

pytestmark = pytest.mark.windows

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def lod_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The workers know the job (until `jobs/registry.py::HANDLER_MODULES` names its module)."""
    monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.wireframes.lod_job")
    lod_job.forget()


@pytest.fixture()
def stored(lod_worker, pclient):  # type: ignore[no-untyped-def]
    """A relief of 96 000 shuffled triangles (2 parts), a small closed cube (48) and a grid of 800 (one part)."""
    r = pclient.post(f"{API}/projects", json={"name": "Копии для показа"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        relief = wf_store.put_wireframe(conn, store, *grid(240, 200, hills), name="Рельеф")
        box = wf_store.put_wireframe(conn, store, *cube(2, 10.0), name="Куб")
        small = wf_store.put_wireframe(conn, store, *grid(20, 20, hills), name="Площадка")
    vm.forget()
    return pclient, project, relief, box, small


def _state(project: Any, wf_id: int) -> vm.MeshState:
    with dialect.read_tx(project.engine) as conn:
        return vm.mesh_state(conn, wf_id)


def _lod_jobs(c: Any, wf_id: int) -> list[dict[str, Any]]:
    rows = c.get(f"{API}/jobs").json()["jobs"]
    return [j for j in rows if j["kind"].startswith(lod_job.KIND) and (j["params"] or {}).get("wireframe_id") == wf_id]


def _part(c: Any, wf_id: int, k: int, lod: int) -> tuple[Any, dict[str, Any], dict[str, np.ndarray]]:
    r = c.get(f"{API}/viz/wireframes/{wf_id}/parts/{k}", params={"lod": lod})
    assert r.status_code == 200, r.text
    return r, *binary.decode(r.content)


def test_a_part_without_its_level_is_a_standin_and_the_job_fills_the_cache(stored) -> None:  # type: ignore[no-untyped-def]
    c, project, relief, _box, _small = stored
    state = _state(project, relief)
    head = binary.decode(c.get(f"{API}/viz/wireframes/{relief}").content)[0]
    whole = [_part(c, relief, p["k"], 0)[2] for p in head["parts"]]
    for p in head["parts"]:
        for lod in (1, 2):
            r, ph, pa = _part(c, relief, p["k"], lod)
            assert "etag" not in r.headers and r.headers["cache-control"] == "no-store"
            assert ph["standin"] is True and ph["lod"] == lod and not ph["simplified"]
            assert ph["triangles"] == ph["source_triangles"] == p["n"]
            assert all(np.array_equal(pa[key], whole[p["k"]][key]) for key in pa)  # the part whole, bit for bit
    found = _lod_jobs(c, relief)
    assert len(found) == 1 and found[0]["kind"] == lod_job.KIND and found[0]["queue"] == "quick"
    assert found[0]["params"] == {"wireframe_id": relief, "key": state.cache_key(), "triangles": 96_000}
    job = wait_job(c, found[0]["source"], found[0]["id"])
    assert job["status"] == "done", job
    assert job["result"]["wireframe_ids"] == [relief] and job["result"]["built"] == job["result"]["parts"] == 2
    folder = vm.cache_dir(project.folder, state.cache_key())
    assert sorted(f.name for f in folder.iterdir()) == ["0-1.bfa", "0-2.bfa", "1-1.bfa", "1-2.bfa"]
    with dialect.read_tx(project.engine) as conn:
        v, t = wf_store.read_wireframe(conn, BlobStore(project.folder), relief)
    for p in head["parts"]:
        made = vm.part_bodies(v, t, vm.parts_of(project, state)[p["k"]], vm.SIMPLIFIED)
        for lod in (1, 2):
            r, ph, _pa = _part(c, relief, p["k"], lod)
            assert r.headers["etag"] == p["etags"][lod] and r.headers["cache-control"] == "no-cache"
            assert "standin" not in ph and ph["simplified"] and r.content == made[lod]  # what `build_cache` makes
    assert len(_lod_jobs(c, relief)) == 1  # nothing more is asked once the levels are there


def test_parts_asked_at_once_start_one_job(stored) -> None:  # type: ignore[no-untyped-def]
    c, project, relief, _box, _small = stored
    state = _state(project, relief)
    sched = jobs_of(c.core)
    got: list[int | None] = []
    gate = threading.Barrier(8)

    def ask(i: int) -> None:
        gate.wait()
        got.append(lod_job.request(sched, project, state, (i % 2, 1 + i % 2)))

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    found = _lod_jobs(c, relief)
    assert len(found) == 1 and set(got) <= {found[0]["id"], None} and found[0]["id"] in got
    assert wait_job(c, found[0]["source"], found[0]["id"])["status"] == "done"
    assert lod_job.request(sched, project, state, (0, 1)) is None  # made: no other job
    assert len(_lod_jobs(c, relief)) == 1


def test_no_job_while_a_check_of_the_wireframe_is_queued_or_running(stored) -> None:  # type: ignore[no-untyped-def]
    c, project, relief, _box, small = stored
    r = c.post(f"{API}/wireframes/check", json={"wireframe_ids": [small, relief]})
    assert r.status_code == 200, r.text
    _r, ph, _ = _part(c, relief, 0, 2)
    if ph.get("standin"):  # the check had not made it yet: the check is the job that makes it
        assert _lod_jobs(c, relief) == []
    job = wait_job(c, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and job["result"]["wireframe_ids"] == [small, relief]
    r2, ph2, _ = _part(c, relief, 0, 2)
    assert "standin" not in ph2 and r2.headers["etag"] and _lod_jobs(c, relief) == []


def test_a_tiny_part_is_its_own_level_and_a_small_one_waits_for_the_job(stored, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    c, project, _relief, box, small = stored

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("levels are never made in the process of the API")

    monkeypatch.setattr(vm.gm, "levels", boom)
    monkeypatch.setattr(vm.gm, "simplify", boom)
    head = binary.decode(c.get(f"{API}/viz/wireframes/{box}").content)[0]
    r0, whole, arrays0 = _part(c, box, 0, 0)
    for lod in (1, 2):  # 48 triangles: `levels` gives the part back as it is, so the answer is the level
        r, ph, pa = _part(c, box, 0, lod)
        assert r.headers["etag"] == head["parts"][0]["etags"][lod] and "standin" not in ph
        assert ph["triangles"] == whole["triangles"] == 48 and all(np.array_equal(pa[k], arrays0[k]) for k in pa)
    assert _lod_jobs(c, box) == [] and not vm.cache_dir(project.folder, _state(project, box).cache_key()).exists()
    r, ph, _ = _part(c, small, 0, 1)  # 800 triangles: simplified by the job, a stand-in until then
    assert ph["standin"] and "etag" not in r.headers and len(_lod_jobs(c, small)) == 1


def test_a_standin_takes_at_most_50_ms_for_64_thousand_triangles(stored) -> None:  # type: ignore[no-untyped-def]
    c, _project, relief, _box, _small = stored
    c.get(f"{API}/viz/wireframes/{relief}")
    times = []
    for _ in range(5):  # the job may make the level meanwhile: only stand-ins are timed
        t0 = time.perf_counter()
        _r, ph, _ = _part(c, relief, 0, 2)
        if ph.get("standin"):
            times.append(time.perf_counter() - t0)
    assert ph["source_triangles"] == 64_000 and times and min(times) <= 0.05, times


def test_requests_are_bounded_read_only_and_unattached_projects_ask_nothing(stored) -> None:  # type: ignore[no-untyped-def]
    _c, project, relief, _box, _small = stored
    state = _state(project, relief)
    asked: list[tuple[str, dict[str, Any]]] = []

    class Sched:  # takes the job and forgets it: the folder stays empty, as one that cannot be written
        def submit(self, kind: str, params: dict[str, Any], **kw: Any) -> tuple[str, int]:
            asked.append((kind, params))
            return kw["source"], len(asked)

    lod_job.forget()
    got = [lod_job.request(Sched(), project, state, (0, 1)) for _ in range(5)]  # type: ignore[arg-type]
    assert got == [1, 2, 3, None, None] and len(asked) == lod_job.MAX_REQUESTS
    assert lod_job.request(Sched(), SimpleNamespace(read_only=True), state, (0, 1)) is None  # type: ignore[arg-type]

    class Gone:
        def submit(self, *_a: Any, **_k: Any) -> tuple[str, int]:
            raise LookupError("no project")

    lod_job.forget()
    assert lod_job.request(Gone(), project, state, (0, 1)) is None  # type: ignore[arg-type]
    big = dataclasses.replace(state, triangles=lod_job.QUICK_TRIANGLES + 1)
    lod_job.request(Sched(), project, big, (0, 1))  # type: ignore[arg-type]
    assert asked[-1][0] == lod_job.KIND_HEAVY


class Ctx:
    """What the handler uses of a JobContext; `stop_after` — ticks before a cancel."""

    def __init__(self, project: Any, params: dict[str, Any], stop_after: int | None = None) -> None:
        self.project_folder, self.params, self._engine = project.folder, params, project.engine
        self.ticks: list[float] = []
        self.logs: list[tuple[str, str]] = []
        self.stop_after = stop_after

    def engine(self) -> Any:
        return self._engine

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        if self.stop_after is not None and len(self.ticks) >= self.stop_after:
            raise JobCancelled()
        self.ticks.append(float(progress or 0.0))

    def log(self, text: str, level: str = "info") -> None:
        self.logs.append((level, text))


def test_the_job_removes_old_levels_keeps_what_a_cancel_made_and_leaves_nothing_of_a_deleted_one(  # type: ignore[no-untyped-def]
    stored, monkeypatch
) -> None:
    _c, project, relief, box, _small = stored
    state = _state(project, relief)
    folder = vm.cache_dir(project.folder, state.cache_key())
    stale = vm.cache_dir(project.folder, vm.cache_key(state.blobs, state.render, 1))
    stale.mkdir(parents=True)
    (stale / "0-1.bfa").write_bytes(b"made the way of E7")
    with pytest.raises(JobCancelled):  # the first part is made, the cancel comes before the second
        lod_job._run(Ctx(project, {"wireframe_id": relief}, stop_after=1))  # type: ignore[arg-type]
    assert sorted(f.name for f in folder.iterdir()) == ["0-1.bfa", "0-2.bfa"] and stale.exists()
    ctx = Ctx(project, {"wireframe_id": relief})
    out = lod_job._run(ctx)  # type: ignore[arg-type]
    assert out["built"] == 1 and out["parts"] == 2 and out["wireframe_ids"] == [relief] and not stale.exists()
    assert len(list(folder.iterdir())) == 4 and ctx.ticks[-1] == 1.0
    with dialect.write_tx(project.engine) as conn:
        gone = wf_store.put_wireframe(conn, BlobStore(project.folder), *grid(30, 30, hills), name="Удалённый")
        wf_store.delete_wireframe(conn, gone)
    out = lod_job._run(Ctx(project, {"wireframe_id": gone}))  # type: ignore[arg-type]
    assert out["wireframe_ids"] == [] and out["parts"] == 0  # deleted before the job started
    monkeypatch.setattr(lod_job, "_gone", lambda *_a: True)  # deleted while the job ran
    out = lod_job._run(Ctx(project, {"wireframe_id": box}))  # type: ignore[arg-type]
    assert out["wireframe_ids"] == [] and not vm.cache_dir(project.folder, _state(project, box).cache_key()).exists()


SCRIPT = textwrap.dedent(
    """
    import json, sys
    from fastapi.testclient import TestClient
    from bf_core.api.app import CoreState, create_app
    from bf_core.db import dialect
    from bf_core.projects.service import projects_of
    from bf_core.storage.blobs import BlobStore
    from tests.test_viz_mesh import grid, hills

    API = "/api/v1"
    st = CoreState(token="t" * 20, projects_dir=sys.argv[1], data_dir=sys.argv[2])
    with TestClient(create_app(st), base_url="http://127.0.0.1:5555", headers={"X-BF-Token": "t" * 20}) as c:
        assert c.post(f"{API}/projects", json={"name": "Без pyfqmr"}).status_code == 200
        project = projects_of(st).require_current()
        from bf_core.wireframes import store as ws
        with dialect.write_tx(project.engine) as conn:
            wf = ws.put_wireframe(conn, BlobStore(project.folder), *grid(240, 200, hills), name="Рельеф")
        head = c.get(f"{API}/viz/wireframes/{wf}")
        codes = [c.get(f"{API}/viz/wireframes/{wf}/parts/{k}", params={"lod": lod}).status_code
                 for k in (0, 1) for lod in (0, 1, 2)]
        loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("pyfqmr", "numba") or m == "bf_core.geo.flat")
        print(json.dumps({"head": head.status_code, "codes": codes, "loaded": loaded}))
    """
)


def test_pyfqmr_is_not_loaded_in_the_process_of_the_api(tmp_path: Path) -> None:
    env = {**os.environ, "BF_LOCAL_DIR": str(tmp_path / "local"), "BF_NO_WARMUP": "1", "PYTHONPATH": str(ROOT)}
    out = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(tmp_path / "Проекты"), str(tmp_path / "roaming")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=True,
    )  # fmt: skip
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got == {"head": 200, "codes": [200] * 6, "loaded": []}, got
