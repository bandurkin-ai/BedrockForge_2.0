"""The job `geometry.operation` (ТЗ 7.23; ADR 0015 §6, `ops/job.py`, `ops/run_meshes.py`).

With real `spawn` workers: cube − cube → a new closed wireframe of the right volume with `meta.built`, the
sources unchanged; a failure in the worker leaves no object; an operation is not held by a long `diag.sleep`
in the shared queue. In this process: a cancel at every step and a failure of the write leave neither objects
nor temporary files; the other operations on wireframes give their results."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

import bf_core.ops.job as ops_job  # registers the kinds in this process (the workers: BF_EXTRA_JOB_MODULES)
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.service import jobs_of
from bf_core.ops import registry
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as ws
from tests.opsutil import API, make_project, ok
from tests.solidutil import OFFSET
from tests.test_import_job import wait_job

REL = 1e-9


@pytest.fixture(scope="module")
def numba_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("numba")


@pytest.fixture()
def ops_env(numba_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the core starts: its workers re-import the handlers of the operations by name, and its app has the
    router of the operations (until `api/modules.py` and `jobs/registry.py` list them)."""
    import bf_core.api.app as app_module

    monkeypatch.setenv("NUMBA_CACHE_DIR", str(numba_cache))
    monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.ops.job")
    if "ops" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "ops"))


@pytest.fixture()
def oc(ops_env, pclient):  # type: ignore[no-untyped-def]
    project, ids = make_project(pclient)
    return pclient, project, ids


def checked(project: Any, op: str, params: dict[str, Any]) -> registry.Checked:
    with dialect.read_tx(project.engine) as conn:
        return registry.precheck(conn, BlobStore(project.folder), op, params)


def wireframe_names(c: Any) -> list[str]:
    return [w["name"] for w in ok(c.get(f"{API}/wireframes"))["wireframes"]]


def nothing_left(c: Any, project: Any, before: list[str]) -> None:
    assert wireframe_names(c) == before
    tmp = BlobStore(project.folder).tmp_dir
    assert not tmp.exists() or not list(tmp.iterdir())


def shas(project: Any, wf_id: int) -> dict[str, str]:
    with dialect.read_tx(project.engine) as conn:
        return ws.blob_shas(conn, wf_id)


class Ctx(JobContext):
    """The handler in this process; `stop_at` — the message of the step that is cancelled."""

    def __init__(self, project: Any, params: dict[str, Any], stop_at: str | None = None) -> None:
        self.seen: list[str] = []
        self.logs: list[tuple[str, str]] = []
        self.stop_at = stop_at

        def send(msg: tuple[Any, ...]) -> None:
            if msg[0] == "log":
                self.logs.append((msg[2], msg[3]))

        super().__init__({"id": 1, "params": params, "project_folder": str(project.folder)}, send, threading.Event())

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        self.seen.append(str(message))
        if self.stop_at is not None and message == self.stop_at:
            raise JobCancelled()


def run_here(project: Any, op: str, params: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], Ctx]:
    ctx = Ctx(project, ops_job.job_params(checked(project, op, params), **kw))
    try:
        return ops_job.run(ctx), ctx
    finally:
        ctx.close()


# --- real workers -------------------------------------------------------------------------------------------


def test_cube_minus_cube_makes_a_new_wireframe(oc) -> None:  # type: ignore[no-untyped-def]
    """The acceptance of 7.23: a closed solid of the right volume; the sources are only read."""
    c, project, ids = oc
    before = {k: (shas(project, ids[k]), ok(c.get(f"{API}/wireframes/{ids[k]}"))) for k in ("a", "b")}
    ch = checked(project, "boolean", {"op": "difference", "a": ids["a"], "b": ids["b"]})
    key, jid = ops_job.submit(jobs_of(c.core), ch)
    job = wait_job(c, key, jid, timeout=120)
    assert job["status"] == "done", job
    assert job["kind"] == registry.JOB and job["queue"] == "quick"
    (wf_id,) = job["result"]["wireframe_ids"]
    w = ok(c.get(f"{API}/wireframes/{wf_id}"))
    assert w["name"] == "Куб A − Куб B" and w["stats"]["closed"] and w["stats"]["orientation"] == "outward"
    assert abs(w["stats"]["volume"] - 875.0) <= REL * 875.0
    assert job["result"]["volumes"][0] == w["stats"]["volume"]
    built = w["built"]
    assert built["kind"] == "operation" and built["op"] == "boolean" and built["title"] == "Булева операция"
    assert built["params"] == {"op": "difference", "a": ids["a"], "b": ids["b"]}
    assert [(s["role"], s["id"], s["name"], s["version"]) for s in built["sources"]] == [
        ("a", ids["a"], "Куб A", 1), ("b", ids["b"], "Куб B", 1),
    ]  # fmt: skip
    assert built["algo"] >= 1 and built["at"] and built["rebuild_of"] is None
    assert built["report"]["volume"] == pytest.approx(875.0, rel=REL) and built["tolerances"]["manifold"] >= 0
    for k in ("a", "b"):  # the sources are not changed
        assert shas(project, ids[k]) == before[k][0]
        now = ok(c.get(f"{API}/wireframes/{ids[k]}"))
        assert (now["version"], now["stats"], now["name"]) == (before[k][1]["version"], before[k][1]["stats"],
                                                               before[k][1]["name"])  # fmt: skip
    # the vertices of the result that come from the sources are theirs, bit for bit
    with dialect.read_tx(project.engine) as conn:
        v, _t = ws.read_wireframe(conn, BlobStore(project.folder), wf_id)
        va, _ = ws.read_wireframe(conn, BlobStore(project.folder), ids["a"])
    assert {r.tobytes() for r in va[[0, 1, 2, 3]]} <= {r.tobytes() for r in v}


def test_a_failure_in_the_worker_leaves_nothing(oc) -> None:  # type: ignore[no-untyped-def]
    """The job checks again: the second solid became open after the request (here: given as it is)."""
    c, project, ids = oc
    before = wireframe_names(c)
    ch = checked(project, "boolean", {"op": "union", "a": ids["a"], "b": ids["b"]})
    ch.params["b"] = ids["open"]
    key, jid = ops_job.submit(jobs_of(c.core), ch)
    job = wait_job(c, key, jid, timeout=120)
    assert job["status"] == "failed" and job["attempts"] == 1, job
    assert job["error"]["code"] == "wireframe_open" and "«Тело 2»" in job["error"]["message"]
    nothing_left(c, project, before)


def test_an_operation_does_not_wait_for_a_long_job(oc) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = oc
    r = c.post(f"{API}/jobs", json={"kind": "diag.sleep", "params": {"steps": 400, "step_s": 0.1}})
    key, sleep_id = r.json()["source"], r.json()["id"]
    deadline = time.monotonic() + 30
    while c.get(f"{API}/jobs/{key}/{sleep_id}").json()["status"] != "running":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    started = time.monotonic()
    ch = checked(project, "boolean", {"op": "intersection", "a": ids["a"], "b": ids["b"]})
    _key, jid = ops_job.submit(jobs_of(c.core), ch)
    job = wait_job(c, key, jid, timeout=60)
    assert job["status"] == "done", job
    assert time.monotonic() - started < 30
    assert c.get(f"{API}/jobs/{key}/{sleep_id}").json()["status"] == "running"  # still there
    w = ok(c.get(f"{API}/wireframes/{job['result']['wireframe_ids'][0]}"))
    assert w["name"] == "Куб A ∩ Куб B" and abs(w["stats"]["volume"] - 125.0) <= REL * 125.0
    c.post(f"{API}/jobs/{key}/{sleep_id}/cancel")


# --- in this process --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stage",
    ["Чтение исходных", "Проверка тел", "Проверка каркаса", "Запись каркаса", "Сохранение результата в проект"],
)
def test_a_cancel_at_any_step_leaves_nothing(oc, stage: str) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = oc
    before = wireframe_names(c)
    ctx = Ctx(project, ops_job.job_params(checked(project, "boolean", {"op": "union", "a": ids["a"],
                                                                        "b": ids["b"]})), stage)  # fmt: skip
    try:
        with pytest.raises(JobCancelled):
            ops_job.run(ctx)
    finally:
        ctx.close()
    assert stage in ctx.seen and "Готово" not in ctx.seen
    nothing_left(c, project, before)


def test_a_failed_write_leaves_nothing(oc, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = oc
    before = wireframe_names(c)
    calls: list[int] = []
    real = ws.commit_wireframe

    def second_fails(*a: Any, **kw: Any) -> int:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("диск переполнен")
        return real(*a, **kw)

    monkeypatch.setattr(ws, "commit_wireframe", second_fails)
    with pytest.raises(RuntimeError):
        run_here(project, "clip_plane", {"wireframe_id": ids["a"], "point": [0, 0, OFFSET[2] + 5], "normal": [0, 0, 1]})
    assert len(calls) == 2  # the first part was in the transaction that rolled back
    nothing_left(c, project, before)


def test_operations_on_wireframes(oc) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = oc
    z5 = float(ok(c.get(f"{API}/wireframes/{ids['a']}"))["extent"][2]) + 5.0
    out, ctx = run_here(project, "clip_plane", {"wireframe_id": ids["a"], "point": [0, 0, z5], "normal": [0, 0, 1]},
                        folder="Тела/Срезы")  # fmt: skip
    parts = [ok(c.get(f"{API}/wireframes/{i}")) for i in out["wireframe_ids"]]
    assert [p["name"] for p in parts] == ["Куб A: выше уровня", "Куб A: ниже уровня"]
    assert all(p["stats"]["closed"] and p["folder"] == "Тела/Срезы" for p in parts)
    assert sum(p["stats"]["volume"] for p in parts) == pytest.approx(1000.0, rel=REL)
    assert parts[0]["built"] == parts[1]["built"] and parts[0]["built"]["op"] == "clip_plane"
    assert any("Создан каркас" in text for _level, text in ctx.logs)

    out, _ = run_here(project, "repair", {"wireframe_id": ids["open"]}, name="Тело 2 целое")
    w = ok(c.get(f"{API}/wireframes/{out['wireframe_ids'][0]}"))
    assert w["name"] == "Тело 2 целое" and w["stats"]["closed"]
    assert w["stats"]["volume"] == pytest.approx(1000.0, rel=REL)
    assert out["report"]["before"]["open_edges"] == 4 and w["built"]["tolerances"]["weld"] == 1e-6

    out, _ = run_here(project, "merge", {"wireframe_ids": [ids["a"], ids["b"]]})
    (merged,) = out["wireframe_ids"]
    assert ok(c.get(f"{API}/wireframes/{merged}"))["triangle_count"] == 24
    out, _ = run_here(project, "split", {"wireframe_id": merged})
    assert [ok(c.get(f"{API}/wireframes/{i}"))["triangle_count"] for i in out["wireframe_ids"]] == [12, 12]
    with pytest.raises(JobError) as e:
        run_here(project, "split", {"wireframe_id": ids["a"]})
    assert e.value.code == "single_part"

    out, _ = run_here(project, "edit_triangles", {"wireframe_id": ids["a"], "remove": [0, 1]})
    w = ok(c.get(f"{API}/wireframes/{out['wireframe_ids'][0]}"))
    assert w["triangle_count"] == 10 and not w["stats"]["closed"] and w["name"] == "Куб A (правка)"
    # «Править треугольники» (9.33): the two back by their exact corners, as the panel sends the vertices of the pick
    with dialect.read_tx(project.engine) as conn:
        va, ta = ws.read_wireframe(conn, BlobStore(project.folder), ids["a"])
    corners = [va[ta[k]].tolist() for k in (0, 1)]
    out, _ = run_here(project, "edit_triangles", {"wireframe_id": w["id"], "add": corners})
    back = ok(c.get(f"{API}/wireframes/{out['wireframe_ids'][0]}"))
    assert back["triangle_count"] == 12 and back["point_count"] == 8 and back["stats"]["closed"]
    assert back["stats"]["volume"] == pytest.approx(1000.0, rel=REL) and back["built"]["op"] == "edit_triangles"
    with dialect.read_tx(project.engine) as conn:
        vb, _ = ws.read_wireframe(conn, BlobStore(project.folder), back["id"])
    assert {r.tobytes() for r in vb} == {r.tobytes() for r in va}

    out, _ = run_here(project, "smooth", {"wireframe_id": ids["relief"], "iterations": 3})
    assert ok(c.get(f"{API}/wireframes/{out['wireframe_ids'][0]}"))["triangle_count"] == 200
    out, _ = run_here(project, "simplify", {"wireframe_id": ids["relief"], "target_triangles": 50})
    w = ok(c.get(f"{API}/wireframes/{out['wireframe_ids'][0]}"))
    assert 4 <= w["triangle_count"] <= 200 and "max_deviation" in w["built"]["report"]

    poly = {"layer_id": ids["polygons"], "line": None}
    out, _ = run_here(project, "clip_polygon", {"wireframe_id": ids["relief"], "polygon": poly})
    w = ok(c.get(f"{API}/wireframes/{out['wireframe_ids'][0]}"))
    assert w["stats"]["plan_area"] == pytest.approx(16.0, rel=1e-9)
    assert w["built"]["params"]["polygon"] == {"layer_id": ids["polygons"], "line": 0}

    out, _ = run_here(project, "clip_surface", {"wireframe_id": ids["b"], "surface_id": ids["relief"]})
    vols = [ok(c.get(f"{API}/wireframes/{i}"))["stats"]["volume"] for i in out["wireframe_ids"]]
    assert sum(vols) == pytest.approx(1000.0, rel=1e-9) and len(vols) == 2
    with dialect.read_tx(project.engine) as conn:
        kinds = set(conn.execute(sa.select(objects.c.kind)).scalars())
    assert "line_file" in kinds  # only the layers of the fixture: no operation here made lines
    assert np.isfinite(vols).all()
