"""The job `wireframe.check`, «Проверить» and the red layer of crossing triangles (ТЗ 7.21; ADR 0013 §3, §5).

References:
  * a closed cube 10 × 10 × 10 m on UTM-sized coordinates: volume 1000 within 1e-9 (the acceptance of 7.21),
    no open edges, no conflicts of normals, no crossings;
  * one triangle of the cube turned over: 3 conflicts of normals — each of its edges is walked the same way
    by it and by its neighbour;
  * two cubes pushed into each other and `TST-FAULTS.DXF` (2 723 pairs, 2 057 triangles — BF): the pairs and
    the triangles of `geo.checks.self_intersections` on the mesh as the file has it; the job works on the
    triangles as stored (Z-order), so the same triangles must come out;
  * the red layer: float32 from whole-metre origins — within 1e-5 m of the float64 corners for a mesh under
    130 m, within 2.5e-4 m (a quarter of a millimetre, ADR 0004) for groups up to 16 km wide.
Hard data: a wireframe 40 km wide (two groups of the layer), degenerate triangles, a span no check can work
on, a wireframe deleted while it is checked (and its id taken by another one), two wireframes of one geometry.
Cancel at every step, a repeated check, a deleted cache.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import tracemalloc
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.geo.checks import self_intersections
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from bf_core.viz import meshes as vm
from bf_core.wireframes import check
from bf_core.wireframes import store as ws
from tests.meshutil import UTM, cube, grid
from tests.test_geo_checks import FAULTS, faults_mesh
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows

STEPS = ("копии для показа", "нормали и самопересечения", "запись результата")
LEVELS = ["0-1.bfa", "0-2.bfa"]


def two_cubes(offset: Any = UTM) -> tuple[np.ndarray, np.ndarray]:
    """Two closed cubes of 60 m, the second moved by (31, 32, 33): their faces cross."""
    va, ta = cube(4, 60.0, offset=offset)
    return np.concatenate([va, va + [31.0, 32.0, 33.0]]), np.concatenate([ta, ta + len(va)])


def turned(v: np.ndarray, t: np.ndarray, k: int = 7) -> tuple[np.ndarray, np.ndarray]:
    out = t.copy()
    out[k] = out[k][::-1]
    return v, out


@pytest.fixture(scope="module")
def numba_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("numba")


@pytest.fixture()
def api(pclient, numba_cache: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A project with a sound cube, a cube with one triangle turned over and two cubes that cross. The worker
    of the job shares one numba cache with the other tests of the module."""
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(numba_cache))
    r = pclient.post(f"{API}/projects", json={"name": "Проверки"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    meshes = {
        "cube": cube(5, 10.0),
        "turned": turned(*cube(5, 10.0, offset=UTM + [50.0, 0.0, 0.0])),
        "pair": two_cubes(),
    }
    names = {"cube": "Куб", "turned": "Куб с перевёрнутым треугольником", "pair": "Два куба"}
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        ids = {key: ws.put_wireframe(conn, store, v, t, name=names[key]) for key, (v, t) in meshes.items()}
    vm.forget()
    return pclient, project, ids, meshes


def add(project: Any, name: str, v: Any, t: Any) -> int:
    with dialect.write_tx(project.engine) as conn:
        return ws.put_wireframe(conn, BlobStore(project.folder), v, t, name=name)


def ok(r: Any) -> Any:
    assert r.status_code == 200, r.text
    return r.json()


def refused(r: Any, status: int, code: str) -> dict[str, Any]:
    body: dict[str, Any] = r.json()
    assert r.status_code == status and body["code"] == code, r.text
    assert body["message"] and body["hint"]
    return body


class Ctx(JobContext):
    """The context of a job run in this process: what the handler said, and a hook on every tick."""

    def __init__(self, project: Any, params: dict[str, Any], on_tick: Any = None) -> None:
        self.sent: list[tuple[Any, ...]] = []
        self.said: list[str] = []
        self.shares: list[float] = []
        self.on_tick = on_tick
        job = {"id": 1, "params": params, "project_folder": str(project.folder)}
        super().__init__(job, self.sent.append, threading.Event())

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        if message is not None and (not self.said or self.said[-1] != message):
            self.said.append(message)
        if progress is not None:
            self.shares.append(progress)
        if self.on_tick and self.on_tick(str(message)):
            raise JobCancelled()

    def logs(self, level: str | None = None) -> list[str]:
        return [m[3] for m in self.sent if m[0] == "log" and (level is None or m[2] == level)]


def run(project: Any, ids: list[int], on_tick: Any = None) -> tuple[dict[str, Any], Ctx]:
    """The handler itself with the parameters the endpoint would give it."""
    with dialect.read_tx(project.engine) as conn:
        params, _title = check.request(conn, ids)
    ctx = Ctx(project, params, on_tick)
    try:
        return check.wireframe_check(ctx), ctx
    finally:
        ctx.close()


def info(c: Any, wf_id: int) -> dict[str, Any]:
    out: dict[str, Any] = ok(c.get(f"{API}/wireframes/{wf_id}"))
    return out


def layer(c: Any, wf_id: int, **headers: str) -> tuple[Any, dict[str, Any], dict[str, np.ndarray]]:
    r = c.get(f"{API}/wireframes/{wf_id}/checks", headers=headers)
    assert r.status_code == 200, r.text
    head, arrays = binary.decode(r.content)
    return r, head, arrays


def folder_of(project: Any, wf_id: int) -> Path:
    with dialect.read_tx(project.engine) as conn:
        return vm.cache_dir(project.folder, vm.mesh_state(conn, wf_id).cache_key())


def stored(project: Any, wf_id: int) -> tuple[np.ndarray, np.ndarray]:
    with dialect.read_tx(project.engine) as conn:
        return ws.read_wireframe(conn, BlobStore(project.folder), wf_id)


def rows(t: np.ndarray, which: Any) -> set[tuple[int, ...]]:
    return {tuple(int(x) for x in row) for row in np.asarray(t)[np.asarray(which)]}


def corners_of(head: dict[str, Any], arrays: dict[str, np.ndarray]) -> np.ndarray:
    """The corners of the red triangles in project coordinates: float32 from the origin of their group."""
    xyz = arrays["xyz"].astype(np.float64).reshape(-1, 3, 3)
    covered = 0
    for g in head["groups"]:
        assert g["first"] == covered and all(float(o).is_integer() for o in g["origin"])
        xyz[g["first"] : g["first"] + g["n"]] += np.array(g["origin"])
        covered += g["n"]
    assert covered == head["triangles"] == len(xyz) == len(arrays["tri"])
    return xyz


def no_leftovers(project: Any) -> None:
    tmp = BlobStore(project.folder).tmp_dir
    assert not tmp.exists() or not list(tmp.iterdir())
    assert not list((project.folder / "cache").rglob("*.tmp"))


# --- «Проверить» through the scheduler and a worker ------------------------------------------------------


def test_check_button_runs_the_job_and_gives_the_red_layer(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, meshes = api
    v, t = meshes["pair"]
    want = self_intersections(v, t)
    assert want.pairs > 0 and want.triangle_count == len(want.triangles) > 0
    before = info(c, ids["pair"])
    assert "checks" not in before["stats"] and before["stats"]["closed"] is True  # measured when it was stored
    r, head, arrays = layer(c, ids["pair"])
    assert (head["kind"], head["state"], head["triangles"], head["groups"]) == ("checks", "none", 0, [])
    assert len(arrays["xyz"]) == 0 and r.headers["cache-control"] == "no-cache"
    not_checked = r.headers["etag"]

    started = ok(c.post(f"{API}/wireframes/{ids['pair']}/check"))
    job = wait_job(c, started["source"], started["id"], timeout=300)
    assert job["status"] == "done", job
    assert job["kind"] == "wireframe.check" and job["title"] == "Проверка каркаса «Два куба»"
    assert job["params"]["wireframe_ids"] == [ids["pair"]]
    assert job["result"] == {
        "checked": 1, "failed": 0, "skipped": 0, "lod_parts": 1, "normal_conflicts": 0, "pairs": want.pairs,
        "triangles": want.triangle_count, "with_defects": [ids["pair"]], "wireframe_ids": [ids["pair"]],
    }  # fmt: skip
    said = [row["text"] for row in ok(c.get(f"{API}/jobs/{started['source']}/{started['id']}/log"))["lines"]]
    assert any("«Два куба»" in line and "самопересечений" in line for line in said)
    recent = [(name, json.loads(data)) for _id, name, data in c.core.events._recent]
    assert ("objects.changed", {"kind": "job", "job": "wireframe.check", "id": started["id"]}) in recent

    after = info(c, ids["pair"])
    checks = after["stats"]["checks"]
    assert set(checks) == {
        "state", "normal_conflicts", "pairs", "triangles", "listed", "points", "seconds", "checked_at"
    }  # fmt: skip
    assert (checks["state"], checks["normal_conflicts"], checks["pairs"]) == ("done", 0, want.pairs)
    assert checks["triangles"] == checks["listed"] == want.triangle_count
    assert 0 < len(checks["points"]) <= 200 and checks["seconds"] >= 0
    assert abs((datetime.now().astimezone() - datetime.fromisoformat(checks["checked_at"])).total_seconds()) < 600
    assert after["version"] == before["version"]  # derived data: an edit in «Свойства» is not refused for it
    assert {k: x for k, x in after["stats"].items() if k != "checks"} == before["stats"]
    assert sorted(f.name for f in folder_of(project, ids["pair"]).iterdir()) == [*LEVELS, "checks.bfa"]

    r, head, arrays = layer(c, ids["pair"])
    assert (head["kind"], head["format"], head["state"]) == ("checks", 1, "done")
    assert head["triangles"] == head["crossing"] == want.triangle_count and len(head["groups"]) == 1
    sv, st = stored(project, ids["pair"])
    listed = arrays["tri"]
    assert listed.dtype == np.dtype("<u4") and np.all(np.diff(listed.astype(np.int64)) > 0)
    assert rows(st, listed) == rows(t, want.triangles)  # the same triangles, whatever the order of the store
    assert arrays["xyz"].dtype == np.dtype("<f4")
    assert np.abs(corners_of(head, arrays) - sv[st[listed]]).max() < 1e-5
    lo, hi = sv[st[listed]].reshape(-1, 3).min(axis=0), sv[st[listed]].reshape(-1, 3).max(axis=0)
    assert head["bbox"] == pytest.approx([*lo, *hi], abs=1e-9)
    tag = r.headers["etag"]
    assert tag != not_checked
    again = c.get(f"{API}/wireframes/{ids['pair']}/checks", headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == tag
    assert layer(c, ids["cube"])[0].headers["etag"] not in (tag, not_checked)  # of another wireframe

    refused(c.post(f"{API}/wireframes/999999/check"), 404, "wireframe_not_found")
    refused(c.get(f"{API}/wireframes/999999/checks"), 404, "wireframe_not_found")


# --- the handler: what is found --------------------------------------------------------------------------


def test_cube_is_clean_a_turned_triangle_is_three_conflicts(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, _meshes = api
    result, ctx = run(project, [ids["cube"], ids["turned"], ids["pair"], ids["cube"]])  # a repeated id is one
    assert (result["checked"], result["failed"], result["skipped"]) == (3, 0, 0)
    assert result["normal_conflicts"] == 3 and result["with_defects"] == [ids["turned"], ids["pair"]]
    assert ctx.shares == sorted(ctx.shares) and ctx.shares[0] == 0.0 and ctx.shares[-1] == 1.0
    for name in ("Куб", "Куб с перевёрнутым треугольником", "Два куба"):
        assert [m for m in ctx.said if m.startswith(f"«{name}»: ")] == [f"«{name}»: {step}" for step in STEPS]
    assert ctx.said[-1] == "Готово"

    solid = info(c, ids["cube"])["stats"]
    assert solid["volume"] == pytest.approx(1000.0, rel=1e-9) and solid["area"] == pytest.approx(600.0, rel=1e-9)
    assert solid["closed"] and solid["orientation"] == "outward"
    assert (solid["open_edges"], solid["nonmanifold_edges"], solid["degenerate"]) == (0, 0, 0)
    checks = solid["checks"]
    assert (checks["state"], checks["normal_conflicts"], checks["pairs"], checks["triangles"]) == ("done", 0, 0, 0)
    assert checks["listed"] == 0 and checks["points"] == []
    _r, head, arrays = layer(c, ids["cube"])
    assert (head["state"], head["triangles"], head["crossing"], head["groups"], head["bbox"]) == (
        "done",
        0,
        0,
        [],
        None,
    )
    assert len(arrays["xyz"]) == len(arrays["tri"]) == 0
    assert sorted(f.name for f in folder_of(project, ids["cube"]).iterdir()) == LEVELS  # no file for an empty list

    bad = info(c, ids["turned"])["stats"]
    assert bad["closed"] and bad["checks"]["normal_conflicts"] == 3 and bad["checks"]["pairs"] == 0
    warned = ctx.logs("warn")
    assert len(warned) == 2 and "конфликтов нормалей: 3" in warned[0] and "«Два куба»" in warned[1]
    assert any("«Куб»" in line and "замечаний нет" in line for line in ctx.logs("info"))


def test_tst_faults_has_2723_pairs_and_its_triangles_are_red(api) -> None:  # type: ignore[no-untyped-def]
    if not FAULTS.is_file():
        pytest.skip("tests/data/real/TST-FAULTS.DXF is not here")
    c, project, _ids, _meshes = api
    v, t = faults_mesh()
    wf = add(project, "TST-FAULTS", v, t)
    result, _ctx = run(project, [wf])
    assert (result["pairs"], result["triangles"], result["normal_conflicts"]) == (2723, 2057, 0)
    stats = info(c, wf)["stats"]
    assert (stats["open_edges"], stats["closed"], stats["volume"]) == (7471, False, None)
    assert (stats["checks"]["pairs"], stats["checks"]["triangles"], stats["checks"]["listed"]) == (2723, 2057, 2057)
    assert 100 <= len(stats["checks"]["points"]) <= 200
    _r, head, arrays = layer(c, wf)
    assert head["triangles"] == 2057 and len(head["groups"]) == 1
    sv, st = stored(project, wf)
    assert rows(st, arrays["tri"]) == rows(t, self_intersections(v, t).triangles)
    worst = np.abs(corners_of(head, arrays) - sv[st[arrays["tri"]]]).max()
    assert worst < 2.5e-4  # the faults lie within 16 km: one group, a quarter of a millimetre


def test_hard_meshes_wide_degenerate_and_unmeasurable(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, _meshes = api
    # 40 km wide: the crossing triangles of the two ends cannot share one float32 origin
    va, ta = two_cubes()
    vb, tb = two_cubes(UTM + [40_000.0, 0.0, 0.0])
    wide = add(project, "Два участка", np.concatenate([va, vb]), np.concatenate([ta, tb + len(va)]))
    # a degenerate triangle and a sliver among crossing ones
    dv, dt = two_cubes(UTM + [0.0, 500.0, 0.0])
    spare = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 1e-7, 0.0]]) + UTM + [0.0, 700.0, 0.0]
    dv, dt = np.concatenate([dv, spare]), np.concatenate([dt, np.array([[0, 1, 2], [0, 1, 3]]) + len(dv)])
    odd = add(project, "С вырожденными", dv, dt.astype(np.int32))
    # no check can work on a mesh 10¹⁶ m wide: the wireframe says so, the others are checked
    far = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [2e16, 0.0, 0.0], [2e16, 1.0, 0.0], [2e16, 0.0, 1.0]]
    )
    huge = add(project, "Без меры", far, np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32))

    result, ctx = run(project, [wide, huge, odd])
    assert (result["checked"], result["failed"], result["skipped"]) == (2, 1, 0)
    one = self_intersections(va, ta)
    stats = info(c, wide)["stats"]["checks"]
    assert stats["pairs"] == 2 * one.pairs and stats["triangles"] == 2 * one.triangle_count
    _r, head, arrays = layer(c, wide)
    assert len(head["groups"]) == 2 and [g["n"] for g in head["groups"]] == [one.triangle_count] * 2
    assert abs(head["groups"][1]["origin"][0] - head["groups"][0]["origin"][0]) == pytest.approx(40_000.0, abs=1.0)
    assert np.abs(arrays["xyz"]).max() < 8000.5
    sv, st = stored(project, wide)
    assert np.abs(corners_of(head, arrays) - sv[st[arrays["tri"]]]).max() < 1e-5

    measures = info(c, odd)["stats"]
    want = self_intersections(dv, dt.astype(np.int32))
    assert measures["degenerate"] == 1 and measures["checks"]["pairs"] == want.pairs == one.pairs  # the sliver has area

    failed = info(c, huge)["stats"]["checks"]
    assert failed["state"] == "failed" and set(failed) == {"state", "error", "checked_at"}
    assert failed["error"]["code"] == "bad_coordinates" and failed["error"]["message"] and failed["error"]["hint"]
    assert layer(c, huge)[1]["state"] == "failed"
    assert any("«Без меры»" in line for line in ctx.logs("error"))
    # the only wireframe of a job cannot be checked: the job fails with the reason
    with pytest.raises(JobError) as caught:
        run(project, [huge])
    assert caught.value.code == "bad_coordinates" and caught.value.hint and not caught.value.retry
    assert info(c, ids["cube"])["stats"].get("checks") is None  # nobody asked for it


# --- cancel, repeat, a deleted cache ---------------------------------------------------------------------


@pytest.mark.parametrize("step", STEPS)
def test_cancel_keeps_what_is_done_and_leaves_nothing_half_made(api, step: str) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, _meshes = api
    with pytest.raises(JobCancelled):
        run(project, [ids["turned"], ids["pair"]], on_tick=lambda message: message == f"«Два куба»: {step}")
    assert info(c, ids["turned"])["stats"]["checks"]["normal_conflicts"] == 3  # checked before the cancel: kept
    assert "checks" not in info(c, ids["pair"])["stats"]
    assert layer(c, ids["pair"])[1]["state"] == "none"
    no_leftovers(project)
    made = sorted(f.name for f in folder_of(project, ids["pair"]).iterdir()) if step != STEPS[0] else []
    assert made == ([] if step == STEPS[0] else LEVELS)  # whole levels of this geometry stay, no list of triangles
    # the next «Проверить» finishes the work and builds no level twice
    result, _ctx = run(project, [ids["pair"]])
    assert result["checked"] == 1 and result["lod_parts"] == (1 if step == STEPS[0] else 0)
    assert layer(c, ids["pair"])[1]["state"] == "done"


def test_repeat_gives_the_same_result_and_builds_nothing_again(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, _meshes = api
    first, _ctx = run(project, [ids["pair"]])
    folder = folder_of(project, ids["pair"])
    files = {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in folder.iterdir()}
    before = info(c, ids["pair"])["stats"]["checks"]
    tag = layer(c, ids["pair"])[0].headers["etag"]
    time.sleep(0.02)
    second, _ctx = run(project, [ids["pair"]])
    assert first["lod_parts"] == 1 and second == {**first, "lod_parts": 0}
    after = info(c, ids["pair"])["stats"]["checks"]
    same = lambda d: {k: x for k, x in d.items() if k not in ("seconds", "checked_at")}  # noqa: E731
    assert same(after) == same(before) and after["checked_at"] > before["checked_at"]
    assert {f.name: f.read_bytes() for f in folder.iterdir()} == {name: data for name, (data, _at) in files.items()}
    assert all(f.stat().st_mtime_ns == files[f.name][1] for f in folder.iterdir() if f.name in LEVELS)
    # the same triangles: the window keeps the layer it has
    assert c.get(f"{API}/wireframes/{ids['pair']}/checks", headers={"If-None-Match": tag}).status_code == 304


def test_deleted_cache_the_layer_is_missing_until_checked_again(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, _meshes = api
    run(project, [ids["pair"]])
    r, head, arrays = layer(c, ids["pair"])
    whole, tag = r.content, r.headers["etag"]
    listed = head["triangles"]
    shutil.rmtree(project.folder / "cache")  # derived data: may be deleted, a `.bfggis` does not carry it
    r, head, arrays = layer(c, ids["pair"], **{"If-None-Match": tag})
    assert (head["state"], head["triangles"], head["crossing"], head["groups"]) == ("missing", 0, listed, [])
    assert len(arrays["xyz"]) == 0 and r.headers["etag"] != tag
    assert info(c, ids["pair"])["stats"]["checks"]["listed"] == listed  # the numbers of «Свойства» stay
    # a damaged file is not served either
    folder = folder_of(project, ids["pair"])
    folder.mkdir(parents=True)
    (folder / "checks.bfa").write_bytes(whole[: len(whole) // 2])
    assert layer(c, ids["pair"])[1]["state"] == "missing"
    result, _ctx = run(project, [ids["pair"]])
    assert result["lod_parts"] == 1 and sorted(f.name for f in folder.iterdir()) == [*LEVELS, "checks.bfa"]
    r, head, _arrays = layer(c, ids["pair"])
    assert r.content == whole and r.headers["etag"] == tag and head["state"] == "done"


# --- wireframes that change under the job ----------------------------------------------------------------


def test_wireframe_deleted_while_checked_leaves_no_folder(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, meshes = api
    twin = add(project, "Два куба (копия)", *meshes["pair"])
    folder = folder_of(project, ids["pair"])
    assert folder == folder_of(project, twin)  # one geometry: one folder of derived files

    def delete(wf: int) -> Any:
        done: list[int] = []

        def on_tick(message: str) -> bool:
            if message.endswith(STEPS[1]) and not done:
                done.append(ok(c.delete(f"{API}/wireframes/{wf}"))["deleted"])
            return False

        return on_tick

    # the twin still has the geometry: its levels and its list stay, nothing is written to the deleted one
    result, ctx = run(project, [ids["pair"]], on_tick=delete(ids["pair"]))
    assert (result["checked"], result["skipped"], result["with_defects"]) == (0, 1, [])
    assert any("«Два куба»" in line and "удалён" in line for line in ctx.logs("warn"))
    assert sorted(f.name for f in folder.iterdir()) == [*LEVELS, "checks.bfa"]
    assert "checks" not in info(c, twin)["stats"]

    # the id of the deleted wireframe is taken by another one before the result is written: not its result
    def swap(message: str) -> bool:
        if message.endswith(STEPS[1]) and not taken:
            ok(c.delete(f"{API}/wireframes/{twin}"))
            taken.append(add(project, "Другой куб", *cube(3, 7.0)))
        return False

    taken: list[int] = []
    result, _ctx = run(project, [twin], on_tick=swap)
    assert (result["checked"], result["skipped"]) == (0, 1)
    assert not folder.exists()  # nobody has the geometry any more
    if taken[0] == twin:  # SQLite gives the id of a deleted object to the next one
        assert "checks" not in info(c, twin)["stats"]
    # a wireframe that is gone before the job starts
    with dialect.read_tx(project.engine) as conn:
        params, _title = check.request(conn, [ids["cube"], ids["turned"]])
    ok(c.delete(f"{API}/wireframes/{ids['cube']}"))
    ctx = Ctx(project, params)
    try:
        result = check.wireframe_check(ctx)
    finally:
        ctx.close()
    assert (result["checked"], result["skipped"], result["normal_conflicts"]) == (1, 1, 3)
    no_leftovers(project)


def test_two_wireframes_of_one_geometry_are_checked_once(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, meshes = api
    twin = add(project, "Два куба (копия)", *meshes["pair"])
    from bf_core.geo import checks as gc

    calls: list[int] = []
    real = gc.run_checks
    monkeypatch.setattr(gc, "run_checks", lambda v, t, progress=None: (calls.append(len(t)), real(v, t, progress))[1])
    result, _ctx = run(project, [ids["pair"], twin])
    assert len(calls) == 1 and result["checked"] == 2 and result["with_defects"] == [ids["pair"], twin]
    a, b = info(c, ids["pair"])["stats"]["checks"], info(c, twin)["stats"]["checks"]
    assert {k: x for k, x in a.items() if k != "checked_at"} == {k: x for k, x in b.items() if k != "checked_at"}
    assert layer(c, twin)[0].content == layer(c, ids["pair"])[0].content
    assert layer(c, twin)[0].headers["etag"] != layer(c, ids["pair"])[0].headers["etag"]


# --- the request -----------------------------------------------------------------------------------------


def test_request_names_the_job_and_refuses_bad_lists(api) -> None:  # type: ignore[no-untyped-def]
    _c, project, ids, meshes = api
    with dialect.read_tx(project.engine) as conn:
        params, title = check.request(conn, [ids["pair"], ids["cube"], ids["pair"]])
        assert title == "Проверка каркасов: 2"
        assert params == {
            "wireframe_ids": [ids["pair"], ids["cube"]], "triangles": len(meshes["pair"][1]),
        }  # fmt: skip
        assert check._memory(params) >= 256 and check._memory({"triangles": 10_000_000}) >= 1000
        # a wireframe deleted after the import is left out; none of them found — 404
        params, title = check.request(conn, [999999, ids["cube"]])
        assert params["wireframe_ids"] == [ids["cube"]] and title == "Проверка каркаса «Куб»"
        for bad, status, code in (([], 422, "no_wireframes"), ([999999, 999998], 404, "wireframe_not_found"),
                                  (list(range(1, check.MAX_IDS + 2)), 422, "too_many_wireframes")):  # fmt: skip
            with pytest.raises(ApiError) as caught:
                check.request(conn, bad)
            assert (caught.value.status, caught.value.code) == (status, code) and caught.value.hint
    from bf_core.jobs.registry import HANDLER_MODULES, KINDS, load_handlers

    assert "bf_core.wireframes.check" in HANDLER_MODULES
    kind = load_handlers()["wireframe.check"]
    assert kind is KINDS["wireframe.check"] and kind.queue == "heavy" and not kind.public and not kind.instance


def test_source_of_an_imported_wireframe_is_told(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, _meshes = api
    assert info(c, ids["cube"])["source"] is None  # made in the project
    v, t = cube(2, 5.0)
    source = {"format": "stl", "name": "solid ore", "precision": "float32", "file": "ore.stl", "header": "x" * 80}
    with dialect.write_tx(project.engine) as conn:
        wf = ws.put_wireframe(conn, BlobStore(project.folder), v, t, name="Руда", meta={"source": source})
    want = {"format": "stl", "name": "solid ore", "precision": "float32", "file": "ore.stl"}
    assert info(c, wf)["source"] == want  # what «Свойства» show; the rows a writer needs stay in the project
    assert [w["source"] for w in ok(c.get(f"{API}/wireframes"))["wireframes"] if w["id"] == wf] == [want]


def test_groups_of_the_layer_stay_within_the_span() -> None:
    rng = np.random.default_rng(3)
    centres = np.sort(rng.random(5000) * 100_000.0)  # along a line of 100 km, in the order of the store
    lo = np.column_stack([centres, np.zeros(5000), np.zeros(5000)])
    hi = lo + 10.0
    groups = check._groups(lo, hi)
    assert [g[0] for g in groups] == list(np.cumsum([0] + [g[1] for g in groups[:-1]])) and sum(
        g[1] for g in groups
    ) == 5000
    assert 7 <= len(groups) <= 8
    for first, count, box in groups:
        assert box[3] - box[0] <= 16_000.0 and box[0] == lo[first, 0] and box[3] == hi[first + count - 1, 0]
    # every triangle wider than the span: a group each up to the limit, the rest in the last one
    big = np.column_stack([np.arange(200) * 50_000.0, np.zeros(200), np.zeros(200)])
    many = check._groups(big, big + 20_000.0)
    assert (
        len(many) == check.MAX_GROUPS and sum(g[1] for g in many) == 200 and many[-1][1] == 200 - check.MAX_GROUPS + 1
    )
    assert check._groups(np.zeros((0, 3)), np.zeros((0, 3))) == []


# --- time and memory -------------------------------------------------------------------------------------


@pytest.mark.slow
def test_a_million_triangles_time_and_memory(api) -> None:  # type: ignore[no-untyped-def]
    """ADR 0013 «Замеры»: `wireframe.check` of 1 million triangles — levels of detail, normals, crossings."""
    c, project, _ids, _meshes = api
    v, t = grid(708, 708, lambda x, y: np.sin(x / 40.0) * 15.0 + np.cos(y / 55.0) * 10.0)
    wall_v, wall_t = grid(700, 20, lambda x, y: x * 0.0, seed=5)
    wall_v = np.column_stack(
        [wall_v[:, 0], wall_v[:, 2] - UTM[2] + UTM[1] + 350.0, wall_v[:, 1] - UTM[1] + UTM[2] - 10]
    )  # a vertical wall through the relief
    wf = add(project, "Миллион", np.concatenate([v, wall_v]), np.concatenate([t, wall_t + len(v)]))
    run(project, [_ids["cube"]])  # the kernels are compiled
    tracemalloc.start()
    started = time.perf_counter()
    result, _ctx = run(project, [wf])
    seconds = time.perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1] / 2**20
    tracemalloc.stop()
    stats = info(c, wf)["stats"]["checks"]
    print(f"\nwireframe.check 1M: {seconds:.1f} s (checks {stats['seconds']} s), peak {peak:.0f} MB, {result}")
    assert result["pairs"] > 1000 and stats["listed"] == stats["triangles"] > 1000
    assert seconds < 120 and peak < check._memory({"triangles": 1_030_000})
    started = time.perf_counter()
    again, _ctx = run(project, [wf])
    assert again["lod_parts"] == 0 and time.perf_counter() - started < 20  # «Проверить»: the levels are there


def test_imported_wireframes_are_checked_in_one_job(api) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids, _meshes = api
    started = ok(c.post(f"{API}/wireframes/check", json={"wireframe_ids": [ids["pair"], ids["cube"]]}))
    job = wait_job(c, started["source"], started["id"], timeout=300)
    assert job["status"] == "done", job
    assert job["title"] == "Проверка каркасов: 2" and job["params"]["wireframe_ids"] == [ids["pair"], ids["cube"]]
    assert job["result"]["checked"] == 2
    assert "checks" in info(c, ids["cube"])["stats"] and "checks" in info(c, ids["pair"])["stats"]
    assert c.post(f"{API}/wireframes/check", json={"wireframe_ids": []}).status_code == 422
    # one of them deleted after the import: the others are checked in the same job
    started = ok(c.post(f"{API}/wireframes/check", json={"wireframe_ids": [ids["cube"], 999999, ids["turned"]]}))
    job = wait_job(c, started["source"], started["id"], timeout=300)
    assert job["status"] == "done" and job["params"]["wireframe_ids"] == [ids["cube"], ids["turned"]], job
    assert job["result"]["checked"] == 2 and "checks" in info(c, ids["turned"])["stats"]
    refused(c.post(f"{API}/wireframes/check", json={"wireframe_ids": [999999]}), 404, "wireframe_not_found")
    refused(c.post(f"{API}/wireframes/999999/check"), 404, "wireframe_not_found")


def test_missing_files_of_the_geometry_are_recorded_as_failed(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids, _meshes = api
    with dialect.read_tx(project.engine) as conn:
        BlobStore(project.folder).path_of(str(vm.mesh_state(conn, ids["turned"]).sha("xyz"))).unlink()
    result, ctx = run(project, [ids["turned"], ids["cube"]])
    assert (result["checked"], result["failed"]) == (1, 1)
    record = info(c, ids["turned"])["stats"]["checks"]
    assert record["state"] == "failed" and record["error"]["code"] == "wireframe_broken" and record["checked_at"]
    assert any("Файлы геометрии каркаса не найдены" in line for line in ctx.logs("error"))
    assert layer(c, ids["turned"])[1]["state"] == "failed"
    with pytest.raises(JobError):  # the only wireframe of a job: the job fails, the record is there all the same
        run(project, [ids["turned"]])
