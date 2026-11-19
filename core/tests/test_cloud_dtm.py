"""A DTM from a point cloud (ADR 0014 §5; `geo/cloud_thin.py`, `wireframes/cloud_source.py`, the job `model.dtm`):
the thinning against one written here the slow way — one source point in every cell of the plan, the lower
median of the height above the ground (of Z when the cloud has none), the smaller number among equals; only the
classes asked for; at most 2 million vertices; the vertices of the surface are source points bit for bit.
Tolerance: none (the points are compared by their bytes)."""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.formats.lineset import from_lines, from_points
from bf_core.geo import cloud_thin
from bf_core.jobs.context import JobCancelled, JobContext
from bf_core.lines import cloud as lc
from bf_core.lines import cloud_build as cb
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import cloud_source as cs
from bf_core.wireframes import jobs as dtm_jobs
from bf_core.wireframes import store as ws
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows

UTM = np.array([512_345.678_901, 6_234_567.123_456, 412.345_678])


@pytest.fixture(scope="module")
def numba_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("numba")


@pytest.fixture()
def api(pclient, numba_cache: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """An empty project; the workers of the jobs share one numba cache."""
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(numba_cache))
    r = pclient.post(f"{API}/projects", json={"name": "ЦТМ из облака"})
    assert r.status_code == 200, r.text
    return pclient, projects_of(pclient.core).require_current()


def ok(r: Any) -> Any:
    assert r.status_code == 200, r.text
    return r.json()


def refused(r: Any, status: int, code: str) -> dict[str, Any]:
    body: dict[str, Any] = r.json()
    assert r.status_code == status and body["code"] == code, r.text
    assert body["message"] and body["hint"]
    return body


def rows_of(a: np.ndarray) -> set[bytes]:
    return {r.tobytes() for r in np.ascontiguousarray(a)}


def row_hash(a: np.ndarray) -> np.ndarray:
    """A 64-bit hash of the bits of every row of float64 triples (to compare millions of points)."""
    bits = np.ascontiguousarray(a, dtype="<f8").view(np.uint64).reshape(-1, 3)
    h = bits[:, 0] * np.uint64(0x9E3779B97F4A7C15) ^ bits[:, 1] * np.uint64(0xC2B2AE3D27D4EB4F)
    h ^= bits[:, 2] * np.uint64(0x165667B19E3779F9)
    return np.asarray(h ^ (h >> np.uint64(29)))


def scene(seed: int, n: int, w: float = 100.0, h: float = 80.0) -> lc.CloudChunk:
    """A slope with noise; «buildings» (6), «vegetation» above the ground (5), single outliers (7), unclassified
    points (1); a vertical wall — points one above another, the same X and Y twice; a clump of 300 points in one
    place (a cell with more points than the short sort of the kernel takes)."""
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, 1.0, (n, 2)) * [w, h]
    z = 0.05 * xy[:, 0] + rng.normal(0.0, 0.02, n)
    cls = np.full(n, 2, dtype=np.uint8)
    roof = (xy[:, 0] > 20) & (xy[:, 0] < 35) & (xy[:, 1] > 30) & (xy[:, 1] < 50)
    z[roof] += 5.0
    cls[roof] = 6
    veg = rng.random(n) < 0.2
    z[veg & ~roof] += rng.uniform(0.3, 6.0, int((veg & ~roof).sum()))
    cls[veg & ~roof] = 5
    stray = rng.choice(n, 40, replace=False)
    z[stray] += 50.0
    cls[stray] = 7
    cls[rng.choice(n, 200, replace=False)] = 1
    wall = np.column_stack([np.full(400, 60.0), np.repeat(rng.uniform(5, 75, 200), 2), rng.uniform(3.0, 18.0, 400)])
    clump = np.column_stack([rng.uniform(70.2, 70.4, (300, 2)), np.round(rng.normal(3.5, 0.02, 300), 2)])  # equal Z
    xyz = np.vstack([np.column_stack([xy, z]), wall, clump]) + UTM
    cls = np.concatenate([cls, np.full(len(wall) + len(clump), 2, dtype=np.uint8)])
    return lc.CloudChunk(xyz, cls)


def add_cloud(project: Any, chunk: lc.CloudChunk, name: str = "Съёмка") -> int:
    store = BlobStore(project.folder)
    with dialect.write_tx(project.engine) as conn:
        file_id = ls_store.create_file(conn, store, name=f"{name}.las", meta={"format": "las"})
        return cb.put_cloud(conn, store, [chunk], file_id=file_id, name=name, source={"kind": "las", "every": 1})


def opened(project: Any, cloud_id: int) -> lc.Cloud:
    with dialect.read_tx(project.engine) as conn:
        return lc.open_cloud(conn, BlobStore(project.folder), cloud_id)


def set_values(project: Any, cloud: lc.Cloud, ground: dict[str, Any] | None = None, **values: Any) -> lc.Cloud:
    store = BlobStore(project.folder)
    staged = lc.stage_values(store, cloud, values)
    with dialect.write_tx(project.engine) as conn:
        lc.replace_values(conn, store, cloud, staged, ground=ground)
    return opened(project, cloud.layer_id)


def ref_thin(xyz: np.ndarray, cls: np.ndarray, key: np.ndarray, classes: list[int] | None, cell: float) -> list[int]:
    """One point in every cell of the plan, the slow way: the lower median of (key, number) of the points of
    the classes; a key that is not a number goes last."""
    cells: dict[tuple[int, int], list[tuple[float, int]]] = {}
    for i, (x, y, _z) in enumerate(xyz.tolist()):
        code = int(cls[i])
        if code not in classes if classes is not None else code == 7:
            continue
        k = float(key[i])
        cells.setdefault((math.floor(x / cell), math.floor(y / cell)), []).append((math.inf if k != k else k, i))
    return sorted(sorted(group)[(len(group) - 1) // 2][1] for group in cells.values())


def tmp_files(project: Any) -> list[Any]:
    tmp = BlobStore(project.folder).tmp_dir
    return list(tmp.iterdir()) if tmp.exists() else []


def run_job(project: Any, params: dict[str, Any], cancel_at: str | None = None) -> tuple[Any, list[str], list[str]]:
    """The handler itself, in this process: (result or None when cancelled, messages of the progress, journal)."""
    seen: list[str] = []
    journal: list[str] = []

    class Ctx(JobContext):
        def tick(self, progress: float | None = None, message: str | None = None) -> None:
            seen.append(str(message))
            if message == cancel_at:
                raise JobCancelled()

        def log(self, text: str, level: str = "info") -> None:
            journal.append(f"{level}: {text}")

    ctx = Ctx({"id": 1, "params": params, "project_folder": str(project.folder)}, lambda _m: None, threading.Event())
    try:
        if cancel_at is not None:
            with pytest.raises(JobCancelled):
                dtm_jobs.model_dtm(ctx)
            return None, seen, journal
        return dtm_jobs.model_dtm(ctx), seen, journal
    finally:
        ctx.close()


# --- the kernel ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("dtype", [np.float64, np.float32])
def test_cell_medians_are_the_lower_medians_whatever_the_order_inside_a_cell(dtype: Any) -> None:
    rng = np.random.default_rng(31)
    sizes = np.concatenate([rng.integers(1, 9, 400), [1, 2, 31, 32, 33, 34, 257, 900], rng.integers(1, 70, 60)])
    cell = np.repeat(rng.permutation(len(sizes)).astype(np.int64) * 7 - 500, sizes)
    key = np.round(rng.normal(0.0, 1.0, len(cell)), 1).astype(dtype)  # one decimal: many equal keys
    key[rng.choice(len(cell), 300, replace=False)] = np.inf  # «no height»: after every number
    mix = rng.permutation(len(cell))  # the points of a cell are not together in the cloud
    cell, key = cell[mix], key[mix]
    want: dict[int, int] = {}
    for c in np.unique(cell).tolist():
        group = sorted((float(key[p]), int(p)) for p in np.flatnonzero(cell == c).tolist())
        want[c] = group[(len(group) - 1) // 2][1]
    order = np.argsort(cell, kind="stable")
    shuffled = order.copy()
    bounds = np.flatnonzero(np.diff(cell[order])) + 1
    for a, b in zip([0, *bounds.tolist()], [*bounds.tolist(), len(order)], strict=True):
        shuffled[a:b] = rng.permutation(order[a:b])
    for grouped in (order, shuffled):
        for step in (len(cell), 1, 97):  # in one go and by portions: a cell is finished where it began
            out = np.full(len(sizes), -1, dtype=np.int64)
            at = count = 0
            while at < len(grouped):
                at, count = cloud_thin.cell_medians(cell, key, grouped, at, at + step, out, count)
            assert count == len(sizes)
            assert {int(cell[p]): int(p) for p in out.tolist()} == want
    none = np.empty(0, dtype=np.int64)
    assert cloud_thin.cell_medians(none, none.astype(dtype), none, 0, 10, none, 0) == (0, 0)


# --- the thinning ----------------------------------------------------------------------------------------


def test_thinning_keeps_one_source_point_a_cell(api) -> None:  # type: ignore[no-untyped-def]
    _c, project = api
    cloud = opened(project, add_cloud(project, scene(32, 30_000)))
    xyz, cls = cloud.read("xyz"), cloud.read("class")
    assert len(xyz) == 30_700 and not cloud.has("h")
    for classes, cell in (([2], 1.0), ([2], 0.37), ([2, 5], 2.5), (None, 1.0), ([6], 4.0)):
        got = cs.thin(cloud, classes, cell)
        rows = ref_thin(xyz, cls, xyz[:, 2], classes, cell)
        assert got.rows.tolist() == rows and got.by == "z"
        assert got.xyz.tobytes() == xyz[rows].tobytes()  # the source points, bit for bit, in the storage order
        assert got.xyz.dtype == np.float64 and got.cell == cell and got.asked == cell
        keep = np.isin(cls, classes) if classes is not None else cls != 7
        assert got.of == int(keep.sum()) and keep[got.rows].all()
    # two points of the wall with the same X and Y: one vertex, so the triangulation is never refused
    plan = cs.thin(cloud, [2], 1.0).xyz[:, :2]
    assert len(np.unique(plan, axis=0)) == len(plan)
    assert len(cs.thin(cloud, [9], 1.0).rows) == 0 and cs.thin(cloud, [9], 1.0).of == 0

    # after «Фильтр земли» the cloud has the heights above the ground: the median is theirs
    rng = np.random.default_rng(33)
    h = np.round(rng.normal(0.0, 0.3, cloud.count), 1).astype(np.float32)
    h[rng.choice(cloud.count, 3000, replace=False)] = np.nan
    cloud = set_values(project, cloud, h=h)
    for classes, cell in (([2], 1.0), (None, 3.0)):
        got = cs.thin(cloud, classes, cell)
        rows = ref_thin(xyz, cls, h, classes, cell)
        assert got.rows.tolist() == rows and got.by == "h"
        assert got.xyz.tobytes() == xyz[rows].tobytes()
    assert ref_thin(xyz, cls, h, [2], 1.0) != ref_thin(xyz, cls, xyz[:, 2], [2], 1.0)
    assert tmp_files(project) == []


def test_default_cell_and_the_limit_of_vertices(api) -> None:  # type: ignore[no-untyped-def]
    _c, project = api
    cloud = opened(project, add_cloud(project, scene(34, 30_000)))
    xyz, cls = cloud.read("xyz"), cloud.read("class")
    area = (cloud.bbox[3] - cloud.bbox[0]) * (cloud.bbox[4] - cloud.bbox[1])
    assert 7990 < area <= 8000
    # no filter yet: half of the default CR; 2 million vertices are far away
    assert cs.default_cell(cloud) == 0.5 and cs.MAX_VERTICES == 2_000_000
    got = cs.thin(cloud, [2])
    assert got.cell == 0.5 and got.asked is None and got.rows.tolist() == ref_thin(xyz, cls, xyz[:, 2], [2], 0.5)
    # the limit: √(area / limit); the step grows a little while the cells at the edges give more than the limit
    for limit in (5000, 400, 7):
        cell = cs.default_cell(cloud, limit)
        assert cell == math.sqrt(area / limit)
        got = cs.thin(cloud, [2], budget=limit)
        assert cell <= got.cell <= 1.6 * cell and got.asked is None and 0.5 * limit < len(got.rows) <= limit
        assert got.rows.tolist() == ref_thin(xyz, cls, xyz[:, 2], [2], got.cell)
    # a step of the user that gives more vertices than the limit grows until they fit
    got = cs.thin(cloud, [2], 0.25, budget=1000)
    assert got.asked == 0.25 and got.cell > 0.25 and 500 < len(got.rows) <= 1000
    assert got.rows.tolist() == ref_thin(xyz, cls, xyz[:, 2], [2], got.cell)
    # after the filter: half of its CR
    done = set_values(project, cloud, ground={"params": {"cr": 3.0}}, h=np.zeros(cloud.count, dtype=np.float32))
    assert cs.default_cell(done) == 1.5 and cs.thin(done, [2]).cell == 1.5


def test_thinning_reports_its_progress_and_can_be_cancelled(api) -> None:  # type: ignore[no-untyped-def]
    _c, project = api
    cloud = opened(project, add_cloud(project, scene(35, 20_000)))
    shares: list[float] = []
    cs.thin(cloud, [2], 1.0, tick=shares.append)
    assert shares[0] == 0.0 and shares[-1] == 1.0 and shares == sorted(shares) and len(shares) >= 4

    def stop(share: float) -> None:
        if share > 0.5:
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        cs.thin(cloud, [2], 1.0, tick=stop)


# --- the job ---------------------------------------------------------------------------------------------


def build(c: Any, url: str, body: dict[str, Any]) -> dict[str, Any]:
    started = ok(c.post(f"{API}/wireframes/{url}", json=body))
    job: dict[str, Any] = wait_job(c, started["source"], started["id"], timeout=240)
    job["log"] = [row["text"] for row in ok(c.get(f"{API}/jobs/{started['source']}/{started['id']}/log"))["lines"]]
    return job


def vertices(project: Any, wf_id: int) -> np.ndarray:
    with dialect.read_tx(project.engine) as conn:
        return ws.read_wireframe(conn, BlobStore(project.folder), wf_id)[0]


def test_dtm_from_a_cloud_takes_the_ground_only(api) -> None:  # type: ignore[no-untyped-def]
    c, project = api
    cloud_id = add_cloud(project, scene(36, 40_000))
    cloud = opened(project, cloud_id)
    xyz, cls = cloud.read("xyz"), cloud.read("class")
    rows = ref_thin(xyz, cls, xyz[:, 2], [2], 0.5)

    job = build(c, "build-dtm", {"clouds": [{"layer_id": cloud_id, "classes": [2]}]})
    assert job["status"] == "done", job
    res = job["result"]
    w = ok(c.get(f"{API}/wireframes/{res['wireframe_id']}"))
    assert w["name"] == res["name"] == "ЦТМ: Съёмка" and w["dtm"] is not None
    v = vertices(project, res["wireframe_id"])
    assert rows_of(v) == rows_of(xyz[rows]) and len(v) == len(rows) == res["points"]  # bit for bit, ground only
    assert res["merged_duplicates"] == 0 and res["skipped_empty"] == 0
    assert w["built"]["sources"] == [
        {
            "kind": "line_layer",
            "id": cloud_id,
            "name": "Съёмка",
            "points": len(rows),
            "classes": [2],
            "cell": 0.5,
            "class_points": int((cls == 2).sum()),
        }
    ]
    assert w["built"]["params"]["max_edge"] is None
    line = next(text for text in job["log"] if "шаг прореживания" in text)
    assert "Съёмка" in line and "0,5 м" in line and "класс 2" in line
    assert tmp_files(project) == []

    # `terrain.from_points`: the ground, the longest edge 5 m while the cloud was never filtered
    job = build(c, "from-points", {"cloud_id": cloud_id})
    assert job["status"] == "done", job
    w2 = ok(c.get(f"{API}/wireframes/{job['result']['wireframe_id']}"))
    assert w2["name"] == "ЦТМ: Съёмка (2)" and w2["built"]["params"]["max_edge"] == 5.0
    assert w2["built"]["sources"][0]["classes"] == [2] and w2["built"]["sources"][0]["cell"] == 0.5
    assert rows_of(vertices(project, w2["id"])) <= rows_of(xyz[rows])  # long triangles at the edge are peeled
    assert job["result"]["points"] > 0.9 * len(rows)

    # a cloud among the plain layers: every class but the noise; a step and an outline of the user
    outline = np.array([[10.0, 10.0, 0.0], [90.0, 10.0, 0.0], [90.0, 70.0, 0.0], [10.0, 70.0, 0.0]]) + [*UTM[:2], 0]
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        file_id = ls_store.create_file(conn, store, name="контур.str", meta={"format": "micromine_str"})
        ring = ls_store.put_layer(conn, store, from_lines("Контур", [outline], closed=[True]), file_id=file_id)
    job = build(c, "build-dtm", {"layer_ids": [cloud_id], "name": "Все классы", "to_dtm": False})
    assert job["status"] == "done", job
    every = ref_thin(xyz, cls, xyz[:, 2], None, 0.5)
    v3 = vertices(project, job["result"]["wireframe_id"])
    assert rows_of(v3) == rows_of(xyz[every]) and not (rows_of(v3) & rows_of(xyz[cls == 7]))
    w3 = ok(c.get(f"{API}/wireframes/{job['result']['wireframe_id']}"))
    assert w3["dtm"] is None and w3["built"]["sources"][0]["classes"] is None
    body = {"cloud_id": cloud_id, "classes": [2, 6], "cell": "2,5", "l_max": "12,5", "boundary_id": ring}
    job = build(c, "from-points", body)
    assert job["status"] == "done", job
    w4 = ok(c.get(f"{API}/wireframes/{job['result']['wireframe_id']}"))
    assert w4["built"]["sources"][0]["cell"] == 2.5 and w4["built"]["sources"][0]["classes"] == [2, 6]
    assert w4["built"]["params"]["max_edge"] == 12.5 and w4["built"]["params"]["boundary"]["layer_id"] == ring
    assert job["result"]["area_plan"] == pytest.approx(80.0 * 60.0, rel=1e-9)


def test_refusals_of_a_cloud_source(api) -> None:  # type: ignore[no-untyped-def]
    c, project = api
    cloud_id = add_cloud(project, scene(37, 5000))
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        file_id = ls_store.create_file(conn, store, name="пикеты.csv", meta={"format": "csv"})
        pts = ls_store.put_layer(conn, store, from_points("Пикеты", scene(38, 500).xyz), file_id=file_id)
    url = f"{API}/wireframes/build-dtm"
    refused(c.post(url, json={"clouds": [{"layer_id": 999_999}]}), 404, "line_layer_not_found")
    refused(c.post(url, json={"clouds": [{"layer_id": pts}]}), 409, "not_a_cloud")
    refused(c.post(url, json={"clouds": [{"layer_id": cloud_id, "classes": []}]}), 422, "bad_classes")
    refused(c.post(url, json={"clouds": [{"layer_id": cloud_id, "cell": 0}]}), 422, "bad_cell")
    refused(c.post(url, json={"clouds": [{"layer_id": cloud_id, "cell": "5000"}]}), 422, "bad_cell")
    refused(c.post(url, json={"clouds": [{"layer_id": cloud_id, "cell": "мелко"}]}), 422, "not_a_number")
    assert c.post(url, json={"clouds": [{"layer_id": cloud_id, "classes": [256]}]}).status_code == 422
    body = refused(c.post(url, json={"clouds": [{"layer_id": cloud_id, "classes": [9, 17]}]}), 422, "no_ground")
    assert "9, 17" in body["message"]
    # a cloud that was never filtered and came without classes: the hint names the command
    bare = scene(39, 3000)
    bare_id = add_cloud(project, lc.CloudChunk(bare.xyz), "Без классов")
    url = f"{API}/wireframes/from-points"
    body = refused(c.post(url, json={"cloud_id": bare_id}), 422, "no_ground")
    assert "Без классов" in body["message"] and "Фильтр земли" in body["hint"]
    refused(c.post(url, json={"cloud_id": pts}), 409, "not_a_cloud")
    refused(c.post(url, json={"cloud_id": cloud_id, "l_max": -1}), 422, "bad_max_edge")
    refused(c.post(url, json={"cloud_id": cloud_id, "boundary_id": pts}), 422, "bad_boundary")
    refused(c.post(url, json={"cloud_id": cloud_id, "boundary_id": cloud_id}), 422, "bad_boundary")
    assert ok(c.get(f"{API}/wireframes"))["wireframes"] == [] and tmp_files(project) == []


def test_request_counts_the_points_of_the_classes_and_the_memory(api) -> None:  # type: ignore[no-untyped-def]
    _c, project = api
    chunk = scene(40, 20_000)
    first, second = add_cloud(project, chunk, "Север"), add_cloud(project, scene(41, 8000), "Юг")
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        file_id = ls_store.create_file(conn, store, name="пикеты.csv", meta={"format": "csv"})
        pts = ls_store.put_layer(conn, store, from_points("Пикеты", scene(42, 500).xyz), file_id=file_id)
    ground, south = int((chunk.cls == 2).sum()), int((scene(41, 8000).cls != 7).sum())
    with dialect.read_tx(project.engine) as conn:
        p = dtm_jobs.request(
            conn, layer_ids=[pts, second, first], clouds=[{"layer_id": first, "classes": [2, 2], "cell": 0.75}]
        )
        again = dtm_jobs.request(conn, **{k: p[k] for k in ("layer_ids", "clouds")})
        one = dtm_jobs.request_from_points(conn, cloud_id=first)
    # a cloud among the layers becomes a cloud source of every class but the noise; named twice — once, as asked
    assert p["layer_ids"] == [pts]
    assert p["clouds"] == [
        {"layer_id": first, "classes": [2], "cell": 0.75},
        {"layer_id": second, "classes": None, "cell": None},
    ]
    assert p["name"] == "ЦТМ: Пикеты, Север, Юг" and again == p
    assert p["cloud_points"] == ground + south and p["points"] == 1200 + p["cloud_points"]
    assert one["clouds"] == [{"layer_id": first, "classes": [2], "cell": None}] and one["max_edge"] == 5.0
    # 10 million points of the ground: the thinning and the 2 million vertices it leaves, not 10 million vertices
    big = {"points": cs.MAX_VERTICES, "cloud_points": 10_000_000}
    assert 1400 <= dtm_jobs._memory(big) <= 1700 and dtm_jobs._memory({"points": 1_000_000}) == 872


def test_cloud_is_read_by_slices_and_a_cancel_leaves_nothing(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, project = api
    cloud_id = add_cloud(project, scene(43, 20_000))
    with dialect.read_tx(project.engine) as conn:
        params = dtm_jobs.request(conn, layer_ids=[cloud_id])
        old = {"layer_ids": [cloud_id], "set_ids": [], "name": "ЦТМ"}  # parameters stored before the clouds came
    whole = ls_store.read_layer

    def read_layer(conn: Any, store: Any, layer_id: int) -> Any:
        assert layer_id != cloud_id, "a cloud is never read as a whole layer"
        return whole(conn, store, layer_id)

    monkeypatch.setattr(ls_store, "read_layer", read_layer)
    for stage in ("Прореживание облака", "Триангуляция", "Сохранение каркаса в проект"):
        _res, seen, _log = run_job(project, params, cancel_at=stage)
        assert stage in seen and "Готово" not in seen
        assert tmp_files(project) == [] and ok(c.get(f"{API}/wireframes"))["wireframes"] == []
        mesh_cache = project.folder / "cache" / "mesh"
        assert not mesh_cache.exists() or not list(mesh_cache.iterdir())
    res, seen, journal = run_job(project, params)
    assert seen[-1] == "Готово" and "Прореживание облака" in seen
    again, _seen, _log = run_job(project, old)
    assert again["points"] == res["points"] > 5000 and again["triangles"] == res["triangles"]
    assert vertices(project, again["wireframe_id"]).tobytes() == vertices(project, res["wireframe_id"]).tobytes()
    assert any("все классы, кроме шума" in line and "шаг прореживания 0,5 м" in line for line in journal)
    # a longest edge shorter than two steps of the thinning cuts the surface: the journal says so
    with dialect.read_tx(project.engine) as conn:
        short = dtm_jobs.request_from_points(conn, cloud_id=cloud_id, cell=4.0, l_max=5.0)
    _res, _seen, journal = run_job(project, short)
    assert any(line.startswith("warn:") and "длина ребра" in line for line in journal)
    # no points of the class by the time the job runs (the cloud was filtered anew): the job says what to do
    cloud = opened(project, cloud_id)
    set_values(project, cloud, **{"class": np.full(cloud.count, 5, dtype=np.uint8)})
    from bf_core.jobs.context import JobError

    with pytest.raises(JobError) as err:
        run_job(project, {**short, "name": "Нет земли"})
    assert err.value.code == "no_ground" and "Фильтр земли" in err.value.hint
    assert tmp_files(project) == []


# --- 10 million points -----------------------------------------------------------------------------------


def survey(n: int, seed: int = 44, portion: int = 1_000_000) -> Any:
    """A pit 60 m deep in a plain, 20 points a square metre, by flight strips: 70 % ground, vegetation above
    it, roofs, noise. The same portions at every call."""
    side = math.sqrt(n / 20.0)

    def portions() -> Any:
        rng = np.random.default_rng(seed)
        for a in range(0, n, portion):
            m = min(portion, n - a)
            x = np.sort(rng.uniform(0.0, side, m))
            y = (a // portion + rng.uniform(0.0, 1.0, m)) * (side / math.ceil(n / portion))
            r = np.hypot(x - 0.5 * side, y - 0.5 * side)
            z = rng.normal(0.0, 0.02, m) - np.clip(0.3 * side - r, 0.0, 50.0) * 1.19
            cls = np.full(m, 2, dtype=np.uint8)
            veg = rng.random(m) < 0.27
            z[veg] += rng.uniform(0.3, 8.0, int(veg.sum()))
            cls[veg] = rng.choice(np.array([3, 4, 5], dtype=np.uint8), int(veg.sum()))
            cls[rng.random(m) < 0.02] = 6
            stray = rng.random(m) < 1e-4
            z[stray] += 80.0
            cls[stray] = 7
            yield lc.CloudChunk(np.column_stack([x, y, z]) + UTM, cls)

    return portions


@pytest.mark.slow
def test_ten_million_points_give_two_million_vertices_of_the_ground(api) -> None:  # type: ignore[no-untyped-def]
    import tracemalloc

    _c, project = api
    n = 10_000_000
    store = BlobStore(project.folder)
    staged = cb.stage_cloud(store, survey(n), expected=n)
    with dialect.write_tx(project.engine) as conn:
        file_id = ls_store.create_file(conn, store, name="карьер.las", meta={"format": "las"})
        cloud_id = lc.commit_cloud(conn, store, staged, file_id=file_id, name="Карьер", source={"kind": "las"})
    cloud = opened(project, cloud_id)
    tracemalloc.start()
    try:
        got = cs.thin(cloud, [2])
        peak = tracemalloc.get_traced_memory()[1] / 2**20
    finally:
        tracemalloc.stop()
    assert got.cell == 0.5 and got.of > 7_000_000
    assert 1_500_000 < len(got.rows) <= cs.MAX_VERTICES
    # against numpy: the lower median of Z of every cell, the smaller number among equals
    xyz, cls = cloud.memmap("xyz"), cloud.read("class")
    ground = np.flatnonzero(cls == 2)
    ix = np.floor(xyz[ground, 0] / 0.5).astype(np.int64)
    iy = np.floor(xyz[ground, 1] / 0.5).astype(np.int64)
    code = (iy - iy.min()) * (ix.max() - ix.min() + 1) + (ix - ix.min())
    order = np.lexsort((ground, xyz[ground, 2], code))
    starts = np.flatnonzero(np.diff(code[order], prepend=-1))
    sizes = np.diff(np.append(starts, len(order)))
    want = np.sort(ground[order[starts + (sizes - 1) // 2]])
    assert np.array_equal(got.rows, want)
    assert got.xyz.tobytes() == np.ascontiguousarray(xyz[want]).tobytes()
    assert peak < 400, peak  # about 32 bytes a point of the classes, the vertices and one portion of the cloud
    del xyz

    with dialect.read_tx(project.engine) as conn:
        params = dtm_jobs.request_from_points(conn, cloud_id=cloud_id)
    assert params["max_edge"] == 5.0 and dtm_jobs._memory(params) < 2048
    res, _seen, journal = run_job(project, params)
    assert res["points"] <= cs.MAX_VERTICES and res["points"] > 0.99 * len(want)
    v = vertices(project, res["wireframe_id"])
    assert np.isin(row_hash(v), row_hash(got.xyz)).all()  # points of the ground, bit for bit
    assert any("шаг прореживания 0,5 м" in line for line in journal)
