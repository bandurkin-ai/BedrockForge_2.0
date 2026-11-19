"""API of the operations (ТЗ 7.23; ADR 0015 §2, §6, `ops/router.py`, `ops/rebuild.py`, `ops/preview.py`):
`GET /operations`, `POST /operations/check`, `POST /operations` (422 before anything is queued), «Перестроить» —
after an edit of a contour a new object with a new volume, the old one unchanged; a source deleted → 422 naming
it; a layer of a result and a DTM rebuilt; the preview of a stitch and its limits."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.lines import versions
from bf_core.lines.assemble import Edit
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from tests.opsutil import API, make_project, ok, refused, square
from tests.test_import_job import wait_job
from tests.test_ops_job import numba_cache, ops_env  # noqa: F401 - fixtures


@pytest.fixture()
def oc(ops_env, pclient):  # type: ignore[no-untyped-def]  # noqa: F811 - the fixture of test_ops_job
    project, ids = make_project(pclient)
    return pclient, project, ids


def run(c: Any, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    started = ok(c.post(url, json=body or {}))
    job: dict[str, Any] = wait_job(c, started["source"], started["id"], timeout=180)
    assert job["status"] == "done", job
    job["started"] = started
    return job


def jobs_count(c: Any) -> int:
    return len(ok(c.get(f"{API}/jobs"))["jobs"])


def wf(c: Any, wf_id: int) -> dict[str, Any]:
    return ok(c.get(f"{API}/wireframes/{wf_id}"))  # type: ignore[no-any-return]


def test_listing_check_and_refusals_before_queueing(oc) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = oc
    ops = ok(c.get(f"{API}/operations"))["operations"]
    assert len(ops) == 17 and ops[0]["op"] == "stitch" and ops[0]["fields"][0]["type"] == "lines"
    body = {"op": "boolean", "params": {"op": "difference", "a": ids["a"], "b": ids["open"]}}
    e = refused(c.post(f"{API}/operations/check", json=body), 422, "wireframe_open")
    assert e["message"] == "Каркас «Тело 2» не замкнут: открытых рёбер — 4." and e["fix"]["op"] == "repair"
    before = jobs_count(c)
    refused(c.post(f"{API}/operations", json=body), 422, "wireframe_open")
    refused(c.post(f"{API}/operations", json={"op": "shell", "params": {}}), 422, "unknown_operation")
    assert jobs_count(c) == before  # nothing was queued
    s = ok(c.post(f"{API}/operations/check", json={"op": "boolean", "params": {"a": ids["a"], "b": ids["b"]}}))
    assert s["queue"] == "quick" and s["job"] == "geometry.operation" and s["warning"] is None and s["size"] == 24


def test_operation_by_the_api(oc) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = oc
    job = run(c, f"{API}/operations", {"op": "boolean", "params": {"op": "union", "a": ids["a"], "b": ids["b"]},
                                       "name": "Объединённое тело", "folder": "Тела"})  # fmt: skip
    w = wf(c, job["result"]["wireframe_ids"][0])
    assert w["name"] == "Объединённое тело" and w["folder"] == "Тела"
    assert w["stats"]["volume"] == pytest.approx(1000.0 + 1000.0 - 125.0, rel=1e-9)


def edit_contour(project: Any, layer_id: int, z: float, *, drop_tie: bool = False) -> int:
    """A save of the editor: the second contour (and the end of the tie line) moved to the height z."""
    top = square(z)
    top = np.vstack([top, top[:1]])
    tie = np.array([square(0.0)[0], square(z)[0]])
    bs = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        base = int(versions.layer_head(conn, layer_id)["rev"])
    if drop_tie:
        edit = Edit(np.array([0, 1]), np.array([0, 1]), np.array([1, 1]), np.array([5, 5]), top)
    else:
        edit = Edit(np.array([0, 1, 2]), np.array([0, 1, 1]), np.array([1, 1, 0]), np.array([5, 5, 2]),
                    np.vstack([top, tie]))  # fmt: skip
    return versions.save(project.engine, bs, layer_id, edit, base_version=base, note="контур поднят")


def test_rebuild_after_an_edit_of_a_contour(oc) -> None:  # type: ignore[no-untyped-def]
    """The acceptance of 7.23: a new object with a new volume, the old one does not change."""
    c, project, ids = oc
    job = run(c, f"{API}/operations", {"op": "stitch", "params": {"contours": [{"layer_id": ids["contours"]}]},
                                       "name": "Рудное тело"})  # fmt: skip
    old_id = job["result"]["wireframe_ids"][0]
    old = wf(c, old_id)
    assert old["stats"]["volume"] == pytest.approx(1000.0, rel=1e-9)
    assert edit_contour(project, ids["contours"], 20.0) == 2
    job = run(c, f"{API}/wireframes/{old_id}/rebuild")
    assert job["started"]["rebuild_of"] == old_id and job["result"]["rebuild_of"] == old_id
    new = wf(c, job["result"]["wireframe_ids"][0])
    assert new["id"] != old_id and new["name"] == "Рудное тело (2)"
    assert new["stats"]["volume"] == pytest.approx(2000.0, rel=1e-9) and new["built"]["report"]["ties"] == 1
    assert new["built"]["rebuild_of"] == old_id and new["built"]["sources"][0]["version"] == 2
    assert new["built"]["params"] == old["built"]["params"]
    assert wf(c, old_id) == old  # the old one is as it was
    # the tie line went away in the next version: the rebuild reads the layer again
    edit_contour(project, ids["contours"], 30.0, drop_tie=True)
    job = run(c, f"{API}/wireframes/{new['id']}/rebuild")
    third = wf(c, job["result"]["wireframe_ids"][0])
    assert third["stats"]["volume"] == pytest.approx(3000.0, rel=1e-9) and third["built"]["report"]["ties"] == 0


def test_rebuild_refusals(oc) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = oc
    picked = {"op": "stitch", "params": {"contours": [{"layer_id": ids["contours"], "lines": [0, 1, 2]}]}}
    solid = run(c, f"{API}/operations", picked)["result"]["wireframe_ids"][0]
    refused(c.post(f"{API}/wireframes/{ids['a']}/rebuild"), 422, "not_rebuildable")  # not made by an operation
    refused(c.post(f"{API}/wireframes/999999/rebuild"), 404, "wireframe_not_found")
    edit_contour(project, ids["contours"], 20.0, drop_tie=True)  # the line 2 is gone
    e = refused(c.post(f"{API}/wireframes/{solid}/rebuild"), 422, "line_missing")
    assert "«Контуры»" in e["message"] and e["lines"] == [2]
    ok(c.delete(f"{API}/lines/files/{ids['file']}"))
    before = jobs_count(c)
    e = refused(c.post(f"{API}/wireframes/{solid}/rebuild"), 422, "source_missing")
    assert e["message"] == "Исходный объект «Контуры» (слой линий) удалён из проекта."
    assert jobs_count(c) == before


def test_rebuild_of_a_layer_and_of_a_dtm(oc) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = oc
    job = run(c, f"{API}/operations", {"op": "contours", "params": {"wireframe_id": ids["relief"], "interval": 1}})
    layer = job["result"]["layer_ids"][0]
    again = run(c, f"{API}/lines/layers/{layer}/rebuild")
    files = {f["id"]: f["name"] for f in ok(c.get(f"{API}/lines/files"))["files"]}
    assert files[again["result"]["file_id"]] == "Изолинии: Рельеф (2)"
    dtm = run(c, f"{API}/operations", {"op": "dtm", "params": {"layers": [ids["polygons"]]}, "name": "Кровля"})
    assert dtm["kind"] == "model.dtm" and dtm["started"]["queue"] == "heavy"
    rebuilt = run(c, f"{API}/wireframes/{dtm['result']['wireframe_id']}/rebuild")
    assert rebuilt["kind"] == "model.dtm"
    w = wf(c, rebuilt["result"]["wireframe_id"])
    assert w["name"] == "Кровля (2)" and w["built"]["kind"] == "dtm" and w["dtm"] is not None


def decoded(r: Any) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    assert r.status_code == 200, r.text
    return binary.decode(r.content)


def test_stitch_preview_and_its_limits(oc) -> None:  # type: ignore[no-untyped-def]
    c, _project, _ids = oc
    body = {"contours": [square(0.0).tolist(), square(10.0).tolist()], "ties": [], "end_cap": "flat"}
    header, arrays = decoded(c.post(f"{API}/geometry/stitch/preview", json=body))
    assert header["kind"] == "mesh" and header["closed"] and header["volume"] == pytest.approx(1000.0, rel=1e-9)
    xyz = arrays["xyz"].reshape(-1, 3).astype(np.float64) + header["origin"]
    assert np.abs(xyz[:4] - square(0.0)).max() < 1e-3 and len(arrays["idx"]) == 3 * header["triangles"]
    assert header["origin"] == [float(round(v)) for v in header["origin"]]
    open_ = {**body, "start_cap": "none", "end_cap": "none"}
    header, _ = decoded(c.post(f"{API}/geometry/stitch/preview", json=open_))
    assert not header["closed"] and header["volume"] is None
    many = {"contours": [square(float(z)).tolist() for z in range(501)]}
    refused(c.post(f"{API}/geometry/stitch/preview", json=many), 422, "preview_too_large")
    ring = [[np.cos(a) * 10, np.sin(a) * 10, 0.0] for a in np.linspace(0, 2 * np.pi, 25_001)[:-1]]
    big = {"contours": [ring, [[x, y, 5.0] for x, y, _z in ring], [[x, y, 9.0] for x, y, _z in ring]]}
    refused(c.post(f"{API}/geometry/stitch/preview", json=big), 422, "preview_too_large")
    gap = square(10.0).tolist()
    gap[1][2] = None
    refused(c.post(f"{API}/geometry/stitch/preview", json={"contours": [square(0.0).tolist(), gap]}), 422, "empty_z")
    refused(c.post(f"{API}/geometry/stitch/preview", json={"contours": [square(0.0).tolist()]}), 422,
            "too_few_contours")  # fmt: skip
    refused(c.post(f"{API}/geometry/stitch/preview", json={**body, "end_cap": "dome"}), 422, "bad_choice")


def test_stitch_preview_of_20_contours_of_500_vertices(oc) -> None:  # type: ignore[no-untyped-def]
    """The measure of ADR 0015 (budget 100 ms in the process of the API; `bench/scenarios/stitch.py` holds it):
    here only an upper bound against a regression, the time goes to the log."""
    c, _project, _ids = oc
    a = np.linspace(0, 2 * np.pi, 501)[:-1]
    rings = [np.c_[(10 + k % 3) * np.cos(a), (10 + k % 3) * np.sin(a), np.full(500, 5.0 * k)] + [5e5, 7e6, 0.0]
             for k in range(20)]  # fmt: skip
    body = {"contours": [r.tolist() for r in rings]}
    decoded(c.post(f"{API}/geometry/stitch/preview", json=body))  # the first call loads the kernels
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        header, _ = decoded(c.post(f"{API}/geometry/stitch/preview", json=body))
        times.append(time.perf_counter() - t0)
    assert header["closed"] and header["contours"] == 20
    print(f"preview 20 × 500: median {sorted(times)[2] * 1000:.0f} ms")
    assert sorted(times)[2] < 2.0


def test_warm_up_of_the_kernels_of_the_stitch() -> None:
    """`preview.warm()` — called by the quick worker at start (`jobs/worker.py::_warm_kernels`)."""
    from bf_core.ops.preview import warm

    warm()
