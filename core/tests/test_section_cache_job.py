"""The job `sections.cache` — the sections of the wireframes of a series in files (ADR 0016 §6, решение 9; ТЗ 8.1.5),
with the `spawn` workers of a test core:

* after the job every section is answered from the files (`X-BF-Cache: hit`) with the bytes and the ETag of the
  answer cut from the geometry; the end of the job is the event `job` with its result; a second request finds
  everything ready and starts nothing;
* a cancel leaves no file and no folder — through the scheduler, and at every tick of the handler;
* one job a series: the same request is the same job, another one replaces it (the first is cancelled);
* a new step of the series or a new geometry of a wireframe — a new key: the files of the old one are not read;
* K > 500: k ± 50 around the section of the window; the limit of 2 GB a project goes by the least recently used.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.jobs.context import JobCancelled, JobContext
from bf_core.projects.service import projects_of
from bf_core.sections import cache_files, cache_job
from bf_core.sections import wf_sections as wf
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import exact
from bf_core.wireframes import store as ws
from tests.meshutil import UTM, cube, grid, icosphere
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows
URL = f"{API}/section-series"
LO = UTM.copy()
OBLIQUE = {"type": "vertical", "p1": [LO[0] - 40.0, LO[1] + 10.0, 0.0], "az": 63, "s0": -20, "s1": 120, "step": 17.5}


@pytest.fixture()
def job_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the core starts: its workers load the handler (until `jobs/registry.py` lists it)."""
    monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.sections.cache_job")


@pytest.fixture()
def cj(job_module, pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Кеш сечений"}).status_code == 200
    project = projects_of(pclient.core).require_current()
    cv, ct = cube(30, 100.0, offset=LO)
    fv, ft = grid(20, 20, lambda x, y: 0.4 * x - 0.15 * y, step=8.0, offset=LO + [-30.0, -30.0, 10.0])
    sv, st = icosphere(3, 40.0, centre=LO + [170.0, 50.0, 50.0])
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        ids = {
            "cube": ws.put_wireframe(conn, store, cv, ct, name="Куб"),
            "fault": ws.put_wireframe(conn, store, fv, ft, name="Разлом"),
            "sphere": ws.put_wireframe(conn, store, sv, st, name="Шар"),
        }
    exact.forget()
    return pclient, project, ids


def create(c: Any, params: dict[str, Any]) -> dict[str, Any]:
    r = c.post(URL, json={"params": params})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def get(c: Any, sid: int, k: int, ids: Any, **params: Any) -> Any:
    r = c.get(f"{URL}/{sid}/sections/{k}/wireframes", params={"ids": ",".join(map(str, ids)), **params})
    assert r.status_code == 200, r.text
    return r


def ask(c: Any, sid: int, ids: Any, around: int | None = None) -> dict[str, Any]:
    r = c.post(f"{URL}/{sid}/cache", json={"wireframe_ids": list(ids), "around": around})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def files_under(folder: Path) -> list[Path]:
    base = cache_files.root(folder)
    return sorted(p for p in base.rglob("*")) if base.exists() else []


def events(c: Any) -> list[tuple[str, dict[str, Any]]]:
    return [(name, json.loads(data)) for _id, name, data in c.core.events._recent]


def test_answers_from_the_files_are_the_live_ones_bit_for_bit(cj) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = cj
    s = create(c, OBLIQUE)
    every = sorted(ids.values())
    live = []
    for k in range(s["count"]):
        r = get(c, s["id"], k, every)
        assert r.headers["x-bf-cache"] == "miss"
        live.append((r.content, r.headers["etag"]))
    started = ask(c, s["id"], every)
    assert started["id"] and not started["ready"] and started["first"] == 0 and started["count"] == s["count"]
    assert started["axis"] == "k" and started["total"] == s["count"] and started["missing"] == []
    job = wait_job(c, started["source"], started["id"])
    assert job["status"] == "done" and job["kind"] == "sections.cache", job
    res = job["result"]
    assert res["series_id"] == s["id"] and res["key"] == started["key"] and res["wireframe_ids"] == every
    assert res["files"] == 3 and res["first"] == 0 and res["count"] == s["count"] and res["bytes"] > 0
    done = [e for name, e in events(c) if name == "job" and e.get("id") == started["id"] and e["status"] == "done"]
    assert done and done[-1]["result"]["key"] == started["key"]  # the window learns of the end by this event
    names = sorted(p.name for p in files_under(project.folder) if p.is_file())
    with dialect.read_tx(project.engine) as conn:
        keys = sorted(st.cache_key() + ".bfa" for st in wf.states(conn, every)[0])
    assert names == keys  # no temporary file is left
    exact.forget()
    for k in range(s["count"]):
        r = get(c, s["id"], k, every)
        assert r.headers["x-bf-cache"] == "hit" and (r.content, r.headers["etag"]) == live[k]
    assert exact.cache_info()["loads"] == 0  # not a blob was read
    state = c.get(f"{URL}/{s['id']}/cache", params={"ids": ",".join(map(str, every))}).json()
    assert state["key"] == started["key"] and state["ready"] == s["count"] and state["first"] == 0
    assert state["job"] is None and state["bytes"] == res["bytes"]
    assert [w["count"] for w in state["wireframes"]] == [s["count"]] * 3
    again = ask(c, s["id"], every)
    assert again["id"] is None and again["ready"]
    # a damaged file is a miss, not an error: the answer is cut again
    victim = cache_files.file_path(project.folder, started["key"], keys[0][:-4])
    victim.write_bytes(victim.read_bytes()[:200])
    r = get(c, s["id"], 2, every)
    assert r.headers["x-bf-cache"] == "partial" and r.content == live[2][0]


def test_a_new_step_or_a_new_geometry_is_a_new_key(cj) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = cj
    s = create(c, OBLIQUE)
    every = sorted(ids.values())
    first = ask(c, s["id"], every)
    assert wait_job(c, first["source"], first["id"])["status"] == "done"
    assert get(c, s["id"], 1, every).headers["x-bf-cache"] == "hit"
    # a new name of the series keeps the key
    assert c.patch(f"{URL}/{s['id']}", json={"expected_version": 1, "name": "Профили"}).status_code == 200
    assert ask(c, s["id"], every)["key"] == first["key"]
    r = c.patch(f"{URL}/{s['id']}", json={"expected_version": 2, "params": {"step": 17.0}})
    assert r.status_code == 200
    state = c.get(f"{URL}/{s['id']}/cache", params={"ids": ",".join(map(str, every))}).json()
    assert state["key"] != first["key"] and state["ready"] == 0
    assert get(c, s["id"], 1, every).headers["x-bf-cache"] == "miss"
    second = ask(c, s["id"], every)
    assert wait_job(c, second["source"], second["id"])["status"] == "done"
    assert get(c, s["id"], 1, every).headers["x-bf-cache"] == "hit"
    # another geometry under a new wireframe: its file is not there
    v, t = cube(12, 60.0, offset=LO + [10.0, 10.0, 10.0])
    with dialect.write_tx(project.engine) as conn:
        other = ws.put_wireframe(conn, BlobStore(project.folder), v, t, name="Куб малый")
    assert get(c, s["id"], 1, [*every, other]).headers["x-bf-cache"] == "partial"
    third = ask(c, s["id"], [*every, other])
    job = wait_job(c, third["source"], third["id"])
    assert job["result"]["wireframe_ids"] == [other]  # only what was missing
    assert get(c, s["id"], 1, [*every, other]).headers["x-bf-cache"] == "hit"
    assert len([p for p in files_under(project.folder) if p.suffix == ".bfa"]) == 3 + 3 + 1


@pytest.fixture()
def slow_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every plane of the workers takes >= 10 ms (`tests/slow_section_cut.py`): the job of 2 x 499 planes cannot end
    in under 10 s. Before the core starts, like `job_module`."""
    monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.sections.cache_job,tests.slow_section_cut")
    monkeypatch.setenv("BF_TEST_CUT_DELAY_S", "0.01")


def test_one_job_a_series_and_a_cancel_leaves_no_file(slow_cut, pclient) -> None:  # type: ignore[no-untyped-def]
    """The job `b` has to be met running with a progress: the scheduler writes it to the database at most once a
    second, so a job that is done in about that time shows `running` with 0.0 and then `done` - a race of the test
    with the speed of the machine, not a fault of the job. The workers cut slowly (`slow_cut`): `b` runs >= 10 s,
    the cancel comes in the middle of it."""
    c = pclient
    assert c.post(f"{API}/projects", json={"name": "Отмена кеша"}).status_code == 200
    project = projects_of(c.core).require_current()
    gv, gt = grid(700, 700, lambda x, y: 0.05 * x + 3.0 * np.sin(y / 40.0), step=2.0, offset=LO)  # 980 000
    sv, st = icosphere(3, 40.0, centre=LO + [700.0, 700.0, 0.0])
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        small = ws.put_wireframe(conn, store, sv, st, name="Шар")  # its file is written first, then the long one
        big = ws.put_wireframe(conn, store, gv, gt, name="Рельеф")
    series = {"type": "vertical", "p1": [LO[0], LO[1] + 1.0, 0.0], "az": 90, "s0": 0, "s1": 1397, "step": 2.8}
    s = create(c, series)
    assert s["count"] == 499
    a = ask(c, s["id"], [big])
    assert ask(c, s["id"], [big])["id"] == a["id"]  # the same request: the same job
    b = ask(c, s["id"], [big, small])  # another one replaces it
    assert b["id"] != a["id"]
    assert wait_job(c, a["source"], a["id"])["status"] == "cancelled"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = c.get(f"{API}/jobs/{b['source']}/{b['id']}").json()
        if job["status"] == "running" and job["progress"] > 0.0:
            break
        assert job["status"] in ("queued", "running"), job
        time.sleep(0.01)
    state = c.get(f"{URL}/{s['id']}/cache", params={"ids": str(big)}).json()
    assert state["job"]["id"] == b["id"] and state["job"]["status"] == "running" and state["ready"] == 0
    assert c.post(f"{API}/jobs/{b['source']}/{b['id']}/cancel").status_code == 200
    assert wait_job(c, b["source"], b["id"])["status"] == "cancelled"
    assert files_under(project.folder) == []  # not a file, not a folder


def _ctx(project: Any, params: dict[str, Any], cancel_at: int | None = None) -> tuple[JobContext, list[int]]:
    """A context of the handler in this process; the cancel is asked at tick number `cancel_at`."""
    cancelled = threading.Event()
    ticks = [0]
    ctx = JobContext({"id": 7, "params": params, "project_folder": str(project.folder)}, lambda _m: None, cancelled)
    tick = ctx.tick

    def counted(progress: float | None = None, message: str | None = None) -> None:
        ticks[0] += 1
        if cancel_at is not None and ticks[0] >= cancel_at:
            cancelled.set()
        tick(progress, message)

    ctx.tick = counted  # type: ignore[method-assign]
    return ctx, ticks


def test_a_cancel_at_any_tick_leaves_no_file(cj) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = cj
    s = create(c, OBLIQUE)
    params = {"series_id": s["id"], "wireframe_ids": sorted(ids.values()), "first": 0, "count": s["count"]}
    ctx, ticks = _ctx(project, params)
    assert cache_job._run(ctx)["files"] == 3
    ctx.close()
    assert ticks[0] == 1 + 3 * s["count"] + 2  # the start, before every plane, before the files go in place, the end
    assert len(cache_files.trim(project.folder, limit=0)) == 1 and files_under(project.folder) == []
    for at in (1, 2, s["count"] + 2, 3 * s["count"] + 1, 3 * s["count"] + 2):
        ctx, _ticks = _ctx(project, params, cancel_at=at)
        with pytest.raises(JobCancelled):
            cache_job._run(ctx)
        ctx.close()
        assert files_under(project.folder) == [], at


def test_many_sections_are_cached_around_the_window(cj) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = cj
    assert cache_job.window(500, None) == (0, 500) and cache_job.window(501, 0) == (0, 51)
    assert cache_job.window(600, 300) == (250, 101) and cache_job.window(600, 599) == (549, 51)
    assert cache_job.window(600, 9999) == (549, 51)
    s = create(c, {**OBLIQUE, "s0": -20, "s1": 130, "step": 0.25})
    assert s["count"] == 601
    every = sorted(ids.values())
    a = ask(c, s["id"], every, around=300)
    assert (a["first"], a["count"], a["total"]) == (250, 101, 601)
    assert wait_job(c, a["source"], a["id"])["status"] == "done"
    assert get(c, s["id"], 300, every).headers["x-bf-cache"] == "hit"
    assert get(c, s["id"], 350, every).headers["x-bf-cache"] == "hit"
    assert get(c, s["id"], 249, every).headers["x-bf-cache"] == "miss"
    state = c.get(f"{URL}/{s['id']}/cache", params={"ids": ",".join(map(str, every))}).json()
    assert state["first"] == 250 and state["ready"] == 101
    assert ask(c, s["id"], every, around=320)["id"] is not None  # 270…370 is not all there


def test_the_limit_goes_by_the_least_recently_used(tmp_path: Path) -> None:
    base = cache_files.root(tmp_path)
    for i, key in enumerate(["a" * 32, "b" * 32, "c" * 32]):
        d = base / key
        d.mkdir(parents=True)
        (d / "x.bfa").write_bytes(b"\0" * 1000)
        os.utime(d, (1000.0 + i, 1000.0 + i))
    stale = base / ("c" * 32) / "y.job-1.tmp"
    stale.write_bytes(b"\0" * 10)
    os.utime(stale, (1.0, 1.0))
    os.utime(base / ("c" * 32), (1002.0, 1002.0))
    assert cache_files.trim(tmp_path, keep={"a" * 32}, limit=2000) == ["b" * 32]  # the oldest but the kept one
    assert sorted(p.name for p in base.iterdir()) == ["a" * 32, "c" * 32] and not stale.exists()
    os.utime(base / ("c" * 32), (1002.0, 1002.0))  # (the stale file went: the folder was changed just now)
    cache_files.touch(tmp_path, "a" * 32)  # read just now: the other one goes
    assert cache_files.trim(tmp_path, limit=1000) == ["c" * 32]
    assert cache_files.trim(tmp_path, limit=0) == ["a" * 32] and list(base.iterdir()) == []


def test_wrong_requests(cj) -> None:  # type: ignore[no-untyped-def]
    c, _project, _ids = cj
    s = create(c, OBLIQUE)
    r = c.post(f"{URL}/99999/cache", json={"wireframe_ids": []})
    assert r.status_code == 404 and r.json()["code"] == "series_not_found"
    r = c.post(f"{URL}/{s['id']}/cache", json={"wireframe_ids": list(range(201))})
    assert r.status_code == 422
    r = c.get(f"{URL}/{s['id']}/cache", params={"ids": "1,x"})
    assert r.status_code == 422 and r.json()["code"] == "bad_wireframes"
    state = c.get(f"{URL}/{s['id']}/cache").json()
    assert state["ready"] is None and state["wireframes"] == [] and state["job"] is None
    assert ask(c, s["id"], [])["ready"]  # nothing to cut
