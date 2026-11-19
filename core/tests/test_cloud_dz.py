"""The map of differences of a point cloud (ADR 0014 §5 «Карта разностей»; `geo/dz_kernels.py`,
`wireframes/cloud_dz.py`): `dz` = Z of a point − the surface under or over it, NaN — no surface there.

The reference is the slow way written here: every triangle in exact rational arithmetic (`fractions`) — a point
is on a triangle when it is inside it or on its border in plan, the height is linear over the triangle, of
several surfaces the nearest along the vertical counts (of two equally near — the lower one). Tolerance: 1e-9 m
for the kernel (float64); the column of the cloud is the same number as float32. A vertex of the surface that is
a point of the cloud has `dz` exactly 0."""

from __future__ import annotations

import threading
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.geo import dz_kernels as dk
from bf_core.geo.dtm import build_dtm
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import HANDLER_MODULES, load_handlers
from bf_core.lines import cloud as lc
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from bf_core.wireframes import cloud_dz as cd
from bf_core.wireframes import jobs as dtm_jobs
from bf_core.wireframes import store as ws
from tests.test_archive import export
from tests.test_cloud_dtm import UTM, add_cloud, build, ok, opened, refused, row_hash, scene, set_values, tmp_files
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows

TOL = 1e-9  # m: the kernel against exact arithmetic


@pytest.fixture(scope="module")
def numba_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("numba")


@pytest.fixture()
def api(pclient, numba_cache: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(numba_cache))
    r = pclient.post(f"{API}/projects", json={"name": "Карта разностей"})
    assert r.status_code == 200, r.text
    return pclient, projects_of(pclient.core).require_current()


# --- the reference ---------------------------------------------------------------------------------------


def _orient(a: Any, b: Any, p: Any) -> Fraction:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def exact_dz(v: np.ndarray, tri: np.ndarray, points: np.ndarray) -> list[float | None]:
    """`dz` of every point the slow way; None — no triangle under or over the point."""
    lo = v[tri].min(axis=1)[:, :2] - 1e-6
    hi = v[tri].max(axis=1)[:, :2] + 1e-6
    out: list[float | None] = []
    for p in points:
        near = np.flatnonzero((lo[:, 0] <= p[0]) & (p[0] <= hi[:, 0]) & (lo[:, 1] <= p[1]) & (p[1] <= hi[:, 1]))
        q = [Fraction(float(c)) for c in p]
        best: Fraction | None = None
        for t in near.tolist():
            a, b, c = ([Fraction(float(x)) for x in v[i]] for i in tri[t])
            d0, d1, d2 = _orient(a, b, q), _orient(b, c, q), _orient(c, a, q)
            s = d0 + d1 + d2
            if s == 0 or not ((d0 >= 0 and d1 >= 0 and d2 >= 0) or (d0 <= 0 and d1 <= 0 and d2 <= 0)):
                continue
            d = q[2] - (d1 * a[2] + d2 * b[2] + d0 * c[2]) / s
            if best is None or abs(d) < abs(best) or (abs(d) == abs(best) and d > best):
                best = d
        out.append(None if best is None else float(best))
    return out


def same(got: np.ndarray, want: list[float | None], tol: float = TOL) -> None:
    assert len(got) == len(want)
    empty = np.array([w is None for w in want])
    assert np.array_equal(np.isnan(got), empty), np.flatnonzero(np.isnan(got) != empty)[:10]
    ref = np.array([0.0 if w is None else w for w in want])
    err = np.abs(np.where(empty, 0.0, got) - ref)
    assert err.max(initial=0.0) <= tol, (err.max(), int(err.argmax()))


def surface(seed: int, n: int = 1500) -> tuple[np.ndarray, np.ndarray]:
    """A relief at the coordinates of a real site: a ragged outline (the long triangles are peeled) and a hole
    in the middle (the triangles around the centre are taken out)."""
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, 1.0, (n, 2)) * [120.0, 90.0]
    z = 5.0 * np.sin(xy[:, 0] / 17.0) + 0.08 * xy[:, 1] + rng.normal(0.0, 0.05, len(xy))
    v, t, _report = build_dtm(np.column_stack([xy, z]) + UTM, max_edge=9.0)
    v, t = np.asarray(v, dtype=np.float64), np.asarray(t, dtype=np.int32)
    centre = v[t].mean(axis=1)[:, :2] - UTM[:2] - [60.0, 45.0]
    return v, np.ascontiguousarray(t[np.hypot(centre[:, 0], centre[:, 1]) > 18.0])


def interior_edge_midpoints(v: np.ndarray, t: np.ndarray, k: int) -> np.ndarray:
    edges = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    inner = uniq[counts == 2][:k]
    return np.asarray((v[inner[:, 0]] + v[inner[:, 1]]) / 2.0)


# --- the kernel ------------------------------------------------------------------------------------------


def test_kernel_against_exact_arithmetic_on_a_surface_with_a_hole() -> None:
    v, t = surface(51)
    rng = np.random.default_rng(52)
    lo, hi = v.min(axis=0), v.max(axis=0)
    anywhere = rng.uniform(lo - 6.0, hi + 6.0, (3000, 3))  # inside, in the hole, beyond the outline
    used = np.unique(t)
    on_vertices = v[used[::3]] + [0.0, 0.0, 1.25]
    on_edges = interior_edge_midpoints(v, t, 400) + [0.0, 0.0, -0.5]
    centres = v[t[::5]].mean(axis=1)
    points = np.concatenate([anywhere, on_vertices, on_edges, centres])
    index = dk.surface_index(v, t)
    got = index.dz(points)
    want = exact_dz(v, t, points)
    same(got, want)
    one_cell = dk.surface_index(v, t, block_bits=0)  # blocks of one cell: the same answers, bit for bit
    assert (index.bits, one_cell.bits) == (3, 0) and one_cell.dz(points).tobytes() == got.tobytes()
    inside = np.array([w is not None for w in want])
    assert 0.3 < inside[: len(anywhere)].mean() < 0.8  # the scene has both: points on the surface and off it
    hole = np.hypot(anywhere[:, 0] - UTM[0] - 60.0, anywhere[:, 1] - UTM[1] - 45.0) < 10.0
    assert hole.sum() > 30 and np.isnan(got[: len(anywhere)][hole]).all()  # a walk over the triangles stops here
    a = len(anywhere)
    # at a vertex — its own height, no interpolation: the very number Z − Z of the vertex
    assert np.array_equal(got[a : a + len(on_vertices)], on_vertices[:, 2] - v[used[::3], 2])
    assert not np.isnan(got[a + len(on_vertices) :]).any()  # an edge shared by two triangles is not a crack
    # the points of the surface themselves: exactly 0, whatever their coordinates
    assert np.array_equal(index.dz(v[used]), np.zeros(len(used)))
    assert index.triangles == len(t) and index.levels >= 1


def test_no_cracks_between_triangles_that_do_not_share_vertex_numbers() -> None:
    """A «soup» (every triangle has its own three vertices, as an STL gives it) over a grid with a step that is
    not a binary fraction: points on the lines and at the nodes of the grid lie on a border of 2–6 triangles."""
    nx, ny, step = 41, 33, 0.7
    gx, gy = UTM[0] + step * np.arange(nx), UTM[1] + step * np.arange(ny)
    zz = np.sin(gx[None, :] / 3.0) * 2.0 + np.cos(gy[:, None] / 5.0)  # (ny, nx)
    quads = [(i, j) for j in range(ny - 1) for i in range(nx - 1)]
    tris = []
    for i, j in quads:
        p00, p10 = (gx[i], gy[j], zz[j, i]), (gx[i + 1], gy[j], zz[j, i + 1])
        p01, p11 = (gx[i], gy[j + 1], zz[j + 1, i]), (gx[i + 1], gy[j + 1], zz[j + 1, i + 1])
        tris += [(p00, p10, p11), (p00, p11, p01)]
    v = np.array(tris, dtype=np.float64).reshape(-1, 3)
    t = np.arange(len(v), dtype=np.int32).reshape(-1, 3)
    index = dk.surface_index(v, t)
    rng = np.random.default_rng(53)
    inside = np.column_stack([rng.uniform(gx[0], gx[-1], 200_000), rng.uniform(gy[0], gy[-1], 200_000)])
    on_x = np.column_stack([rng.choice(gx, 20_000), rng.uniform(gy[0], gy[-1], 20_000)])  # on the lines of the grid
    on_y = np.column_stack([rng.uniform(gx[0], gx[-1], 20_000), rng.choice(gy, 20_000)])
    nodes = np.column_stack([np.repeat(gx, ny), np.tile(gy, nx)])
    xy = np.concatenate([inside, on_x, on_y, nodes])
    got = index.dz(np.column_stack([xy, np.zeros(len(xy))]))
    assert not np.isnan(got).any()
    # the height of the surface by the formula of the grid: the triangle of the cell the point is in
    i = np.clip(np.searchsorted(gx, xy[:, 0], side="right") - 1, 0, nx - 2)
    j = np.clip(np.searchsorted(gy, xy[:, 1], side="right") - 1, 0, ny - 2)
    u, w = (xy[:, 0] - gx[i]) / (gx[i + 1] - gx[i]), (xy[:, 1] - gy[j]) / (gy[j + 1] - gy[j])
    z00, z10, z01, z11 = zz[j, i], zz[j, i + 1], zz[j + 1, i], zz[j + 1, i + 1]
    lower = z00 + u * (z10 - z00) + w * (z11 - z10)  # the triangle 00, 10, 11: w ≤ u
    upper = z00 + u * (z11 - z01) + w * (z01 - z00)  # the triangle 00, 11, 01
    want = -np.where(w <= u, lower, upper)
    assert np.abs(got - want).max() <= TOL
    outside = np.array([[gx[0] - 1e-6, gy[3], 0.0], [gx[-1] + 1e-6, gy[3], 0.0], [gx[5], gy[-1] + 1e-6, 0.0]])
    assert np.isnan(index.dz(outside)).all()


def test_solids_walls_broken_triangles_and_triangles_of_every_size() -> None:
    # a box 10 × 10 × 10: the top and the bottom one over another, vertical walls
    here = np.array([UTM[0], UTM[1], 0.0])  # whole heights: the differences below are exact
    c = np.array([[x, y, z] for z in (0.0, 10.0) for y in (0.0, 10.0) for x in (0.0, 10.0)]) + here
    box = np.array(
        [[0, 1, 3], [0, 3, 2], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4], [2, 3, 7], [2, 7, 6],
         [0, 2, 6], [0, 6, 4], [1, 3, 7], [1, 7, 5], [0, 0, 1], [3, 3, 3]], dtype=np.int32,
    )  # fmt: skip
    # the walls and the last two triangles have no area in plan
    index = dk.surface_index(c, box)
    assert index.triangles == 4  # the walls and the broken triangles are not a surface in plan
    q = np.array([[5.0, 5.0, 9.0], [5.0, 5.0, 2.0], [5.0, 5.0, 5.0], [5.0, 5.0, 14.0], [5.0, 5.0, -3.0],
                  [0.0, 0.0, 1.0], [10.0, 10.0, 8.0], [10.5, 5.0, 5.0], [5.0, 0.0, 6.0]]) + here  # fmt: skip
    assert index.dz(q).tolist()[:7] == [-1.0, 2.0, 5.0, 4.0, -3.0, 1.0, -2.0]  # the nearest; equally near — the lower
    assert np.isnan(index.dz(q)[7]) and index.dz(q)[8] == -4.0  # beside the box; on its side — the top is nearer
    same(index.dz(q), exact_dz(c, box, q))

    # one triangle over the whole site and four thousand small ones in a corner: the index has several levels
    rng = np.random.default_rng(54)
    pts = np.column_stack([rng.uniform(0, 20, (2500, 2)), rng.normal(3.0, 0.2, 2500)]) + UTM
    small_v, small_t, _report = build_dtm(pts)
    big = np.array([[-500.0, -400.0, 40.0], [2500.0, -300.0, 55.0], [300.0, 2600.0, 70.0]]) + UTM
    v = np.concatenate([np.asarray(small_v, dtype=np.float64), big])
    t = np.concatenate([np.asarray(small_t, dtype=np.int32), [[len(v) - 3, len(v) - 2, len(v) - 1]]]).astype(np.int32)
    index = dk.surface_index(v, t)
    assert index.levels >= 3 and index.triangles == len(t)
    where = np.concatenate([rng.uniform([-600, -500, 0], [2700, 2700, 100], (1500, 3)),
                            rng.uniform([-2, -2, 0], [22, 22, 60], (1500, 3))]) + UTM  # fmt: skip
    same(index.dz(where), exact_dz(v, t, where))

    # triangles far from one another: blocks of 8 × 8 cells would be all but empty — the index takes blocks of
    # one cell by itself and stays small
    k = 4000
    corner = np.column_stack([rng.uniform(0, 4e5, (k, 2)), rng.uniform(0, 50, k)]) + UTM
    v = (corner[:, None, :] + rng.uniform(0.2, 1.5, (k, 3, 3))).reshape(-1, 3)
    t = np.arange(3 * k, dtype=np.int32).reshape(-1, 3)
    index = dk.surface_index(v, t)
    assert index.bits == 0 and index.triangles == k and index.nbytes < 1 << 20
    where = np.concatenate([v.reshape(k, 3, 3).mean(axis=1) + [0.0, 0.0, 2.0], corner[:500] - [3.0, 3.0, 0.0]])
    got = index.dz(where)
    same(got[:300], exact_dz(v, t, where[:300]))
    assert not np.isnan(got[:k]).any() and np.isnan(got[k:]).all()

    # nothing to measure against: an empty surface, walls only; no points
    for vv, tt in ((np.zeros((0, 3)), np.zeros((0, 3), np.int32)), (c, box[4:12])):
        empty = dk.surface_index(vv, tt)
        assert empty.triangles == 0 and np.isnan(empty.dz(q)).all()
    assert index.dz(np.zeros((0, 3))).shape == (0,)
    with pytest.raises(ValueError):
        dk.surface_index(c, np.array([[0, 1, 99]], dtype=np.int32))  # a vertex that is not there


# --- the column of a cloud -------------------------------------------------------------------------------


def wireframe(project: Any, wf_id: int) -> tuple[np.ndarray, np.ndarray]:
    with dialect.read_tx(project.engine) as conn:
        return ws.read_wireframe(conn, BlobStore(project.folder), wf_id)


def put_surface(project: Any, v: np.ndarray, t: np.ndarray, name: str, dtm: bool = True) -> int:
    with dialect.write_tx(project.engine) as conn:
        return ws.put_wireframe(conn, BlobStore(project.folder), v, t, name=name, dtm=dtm)


def check_column(cloud: lc.Cloud, v: np.ndarray, t: np.ndarray, sample: int = 500, seed: int = 1) -> np.ndarray:
    """The column `dz` of the cloud against the reference on a sample of its points; the whole column back."""
    dz = cloud.read("dz")
    assert dz.dtype == np.float32 and dz.shape == (cloud.count,)
    rows = np.sort(np.random.default_rng(seed).choice(cloud.count, sample, replace=False))
    want = exact_dz(v, t, cloud.read("xyz")[rows])
    as_stored = [None if w is None else float(np.float32(w)) for w in want]
    same(dz[rows].astype(np.float64), as_stored, tol=1e-6)  # float32 of a number within 1e-9 of the reference
    return dz


def run_dz(project: Any, params: dict[str, Any], cancel_at: str | None = None) -> tuple[Any, list[str], list[str]]:
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
                cd.cloud_dz(ctx)
            return None, seen, journal
        return cd.cloud_dz(ctx), seen, journal
    finally:
        ctx.close()


def request(project: Any, cloud_id: int, wireframe_id: int, **kw: Any) -> dict[str, Any]:
    with dialect.read_tx(project.engine) as conn:
        return cd.request(conn, cloud_id, wireframe_id, **kw)


def test_the_job_is_registered_by_an_explicit_import() -> None:
    assert "bf_core.wireframes.cloud_dz" in HANDLER_MODULES
    kind = load_handlers()["cloud.dz"]
    assert kind.queue == "heavy" and kind.title == "Карта разностей" and kind.run is cd.cloud_dz
    assert not kind.public and not kind.instance
    from bf_core.jobs.scheduler import OBJECT_JOBS

    assert "cloud.dz" in OBJECT_JOBS  # its end is announced: the cloud is read again with the mode `dz`
    assert 512 <= kind.estimate_memory({"points": 100_000, "triangles": 1000}) <= 1024
    assert kind.estimate_memory({"points": 50_000_000, "triangles": 4_000_000}) <= 2048


def test_dz_of_a_cloud_through_the_api(api) -> None:  # type: ignore[no-untyped-def]
    c, project = api
    cloud_id = add_cloud(project, scene(61, 40_000))
    before = opened(project, cloud_id)
    assert not before.has("dz") and ok(c.get(f"{API}/lines/layers/{cloud_id}"))["cloud"]["modes"] == ["class"]
    job = build(c, "from-points", {"cloud_id": cloud_id, "dz": False, "name": "Земля"})
    assert job["status"] == "done", job
    wf_id = job["result"]["wireframe_id"]
    assert not opened(project, cloud_id).has("dz") and "dz" not in job["result"]  # asked not to

    started = ok(c.post(f"{API}/lines/layers/{cloud_id}/dz", json={"wireframe_id": wf_id}))
    job = wait_job(c, started["source"], started["id"], timeout=240)
    assert job["status"] == "done" and job["title"] == "Карта разностей", job
    cloud = opened(project, cloud_id)
    v, t = wireframe(project, wf_id)
    dz = check_column(cloud, v, t)
    res = job["result"]
    assert res["cloud_id"] == cloud_id and res["wireframe_id"] == wf_id and res["version"] == before.version + 1
    assert cloud.version == before.version + 1 and cloud.shas["xyz"] == before.shas["xyz"]
    assert res["points"] == cloud.count and res["with_dz"] == int(np.isfinite(dz).sum()) > 0.5 * cloud.count
    assert res["outside"] == cloud.count - res["with_dz"] > 0  # long triangles at the outline were peeled
    # the points the surface was built from: exactly on it
    xyz, cls = cloud.read("xyz"), cloud.read("class")
    mine = np.isin(row_hash(xyz), row_hash(v))
    assert mine.sum() == len(v) and np.array_equal(dz[mine], np.zeros(len(v), dtype=np.float32))
    # the journal and the record of the cloud: the ground against the surface
    ground = np.abs(dz[(cls == 2) & np.isfinite(dz)].astype(np.float64))
    meta = cloud.meta["dz"]
    assert meta["wireframe_id"] == wf_id and meta["name"] == "Земля" and meta["by"] == "ground"
    assert meta["count"] == len(ground) and meta["points"] == res["with_dz"] and meta["at"]
    assert meta["rms"] == pytest.approx(float(np.sqrt(np.mean(ground**2))), abs=6e-5)  # kept to 0.1 mm
    assert meta["p95"] == pytest.approx(float(np.percentile(ground, 95, method="lower")), abs=6e-5)
    assert meta["max"] == pytest.approx(float(ground.max()), abs=6e-5)
    log = [row["text"] for row in ok(c.get(f"{API}/jobs/{started['source']}/{started['id']}/log"))["lines"]]
    line = next(text for text in log if "СКО" in text)
    assert "по земле" in line and "P95" in line and "наибольшее" in line and "Земля" in "".join(log)
    # the interface gets the mode and the attribute
    layer = ok(c.get(f"{API}/lines/layers/{cloud_id}"))
    assert layer["cloud"]["modes"] == ["class", "dz"] and layer["cloud"]["dz"]["wireframe_id"] == wf_id
    assert [f["name"] for f in layer["attr_schema"]][-1] == "Отклонение от ЦТМ, м"
    manifest = binary.decode(c.get(f"{API}/viz/lines/{cloud_id}").content)[0]
    assert [m["mode"] for m in manifest["cloud"]["modes"]] == ["class", "dz"]
    assert c.get(f"{API}/viz/lines/{cloud_id}/parts/0", params={"lod": 0, "color": "dz"}).status_code == 200
    assert tmp_files(project) == []

    # against another surface, a wireframe that is not a DTM: the column and the record are replaced
    flat = np.array([[0.0, 0.0, 1.0], [100.0, 0.0, 1.0], [100.0, 80.0, 1.0], [0.0, 80.0, 1.0]]) + UTM
    plane = put_surface(project, flat, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32), "Плоскость", dtm=False)
    res2, seen, journal = run_dz(project, request(project, cloud_id, plane, threads=3))
    again = opened(project, cloud_id)
    dz2 = again.read("dz")
    assert res2["version"] == cloud.version + 1 and again.meta["dz"]["name"] == "Плоскость"
    assert again.meta["dz"]["wireframe_id"] == plane and seen[-1] == "Готово"
    assert np.isfinite(dz2).all() and res2["outside"] == 0
    assert np.array_equal(dz2, (xyz[:, 2] - (UTM[2] + 1.0)).astype(np.float32))  # a level plane: Z − its height
    assert any("Плоскость" in line for line in journal)


def test_refusals_cancel_and_a_cloud_that_changed(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c, project = api
    cloud_id = add_cloud(project, scene(62, 12_000))
    v, t = surface(63, 600)
    wf_id = put_surface(project, v - [10.0, 5.0, 0.0], t, "Рельеф")
    from bf_core.formats.lineset import from_points
    from bf_core.lines import store as ls_store

    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        file_id = ls_store.create_file(conn, store, name="пикеты.csv", meta={"format": "csv"})
        pts = ls_store.put_layer(conn, store, from_points("Пикеты", scene(64, 300).xyz), file_id=file_id)
    url = f"{API}/lines/layers"
    refused(c.post(f"{url}/999999/dz", json={"wireframe_id": wf_id}), 404, "line_layer_not_found")
    refused(c.post(f"{url}/{pts}/dz", json={"wireframe_id": wf_id}), 409, "not_a_cloud")
    refused(c.post(f"{url}/{cloud_id}/dz", json={"wireframe_id": 999_999}), 404, "wireframe_not_found")
    assert c.post(f"{url}/{cloud_id}/dz", json={}).status_code == 422
    params = request(project, cloud_id, wf_id)
    assert params["points"] == 12_700 and params["triangles"] == len(t) and params["name"] == "Съёмка"
    assert params["surface"] == "Рельеф" and 1 <= params["threads"] <= 8

    # a cancel at any stage leaves the cloud as it was and no file; the cloud is read by slices
    monkeypatch.setattr(cd, "ROWS", 4096)
    before = opened(project, cloud_id)
    for stage in ("Чтение каркаса", "Карта разностей", "Запись отклонений", "Сохранение отклонений в проект"):
        _res, seen, _log = run_dz(project, params, cancel_at=stage)
        assert stage in seen and "Готово" not in seen
        now = opened(project, cloud_id)
        assert now.version == before.version and not now.has("dz") and tmp_files(project) == []
    # the threads give the same bytes
    columns = []
    for threads in (1, 2, 4):
        run_dz(project, {**params, "threads": threads})
        columns.append(opened(project, cloud_id).read("dz").tobytes())
    assert columns[0] == columns[1] == columns[2]
    check_column(opened(project, cloud_id), *wireframe(project, wf_id), sample=300)

    # another calculation wrote its columns while this one ran: nothing is written over
    stale = opened(project, cloud_id)
    real = lc.stage_values

    def meanwhile(store: Any, cloud: lc.Cloud, values: Any, tick: Any = None) -> Any:
        other = real(store, cloud, {"class": np.full(cloud.count, 5, dtype=np.uint8)})
        with dialect.write_tx(project.engine) as conn:
            lc.replace_values(conn, store, cloud, other)
        return real(store, cloud, values, tick)

    monkeypatch.setattr(lc, "stage_values", meanwhile)
    with pytest.raises(JobError) as err:
        run_dz(project, params)
    assert err.value.code == "cloud_changed" and tmp_files(project) == []
    monkeypatch.setattr(lc, "stage_values", real)
    now = opened(project, cloud_id)
    assert now.version == stale.version + 1 and now.shas["dz"] == stale.shas["dz"]  # only the classes moved
    # no ground among the classes now: the measures are of every point
    res, _seen, journal = run_dz(project, params)
    assert opened(project, cloud_id).meta["dz"]["by"] == "all" and any("по всем точкам" in line for line in journal)
    # a surface that lies elsewhere in plan: nothing to write, the cloud keeps the column it has
    far = put_surface(project, v + [5000.0, 0.0, 0.0], t, "Соседний участок")
    kept = opened(project, cloud_id)
    with pytest.raises(JobError) as err:
        run_dz(project, request(project, cloud_id, far))
    assert err.value.code == "no_overlap" and "Соседний участок" in err.value.message and err.value.hint
    assert opened(project, cloud_id).shas == kept.shas and tmp_files(project) == []
    # the wireframe went while the job waited; the cloud keeps the column and the name of the surface, not its
    # number — SQLite may give it to the next object of the project
    assert opened(project, cloud_id).meta["dz"]["wireframe_id"] == wf_id
    ok(c.delete(f"{API}/wireframes/{wf_id}"))
    with pytest.raises(JobError) as err:
        run_dz(project, params)
    assert err.value.code == "wireframe_not_found"
    left = opened(project, cloud_id)
    assert left.meta["dz"]["wireframe_id"] is None and left.meta["dz"]["name"] == "Рельеф"
    assert left.shas == kept.shas and left.version == kept.version
    assert ok(c.get(f"{API}/lines/layers/{cloud_id}"))["cloud"]["modes"] == ["class", "dz"]


def run_dtm(project: Any, params: dict[str, Any], cancel: threading.Event | None = None, at: str = "") -> Any:
    """`model.dtm` in this process; `cancel` is set when the stage `at` begins (a real cancel request)."""
    journal: list[str] = []
    event = cancel or threading.Event()

    class Ctx(JobContext):
        def tick(self, progress: float | None = None, message: str | None = None) -> None:
            if at and message == at:
                event.set()
            super().tick(progress, message)

        def log(self, text: str, level: str = "info") -> None:
            journal.append(f"{level}: {text}")

    ctx = Ctx({"id": 1, "params": params, "project_folder": str(project.folder)}, lambda _m: None, event)
    try:
        return dtm_jobs.model_dtm(ctx), journal
    finally:
        ctx.close()


def test_a_dtm_built_from_a_cloud_gives_the_cloud_its_dz(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c, project = api
    cloud_id = add_cloud(project, scene(65, 30_000))
    other_id = add_cloud(project, scene(66, 9000), "Юг")
    before = opened(project, cloud_id)
    job = build(c, "from-points", {"cloud_id": cloud_id})
    assert job["status"] == "done", job
    wf_id = job["result"]["wireframe_id"]
    cloud = opened(project, cloud_id)
    v, t = wireframe(project, wf_id)
    dz = check_column(cloud, v, t, sample=300)
    assert cloud.version == before.version + 1 and cloud.meta["dz"]["wireframe_id"] == wf_id
    assert cloud.meta["dz"]["name"] == job["result"]["name"] == "ЦТМ: Съёмка"
    (one,) = job["result"]["dz"]
    assert one["cloud_id"] == cloud_id and one["with_dz"] == int(np.isfinite(dz).sum()) and one["rms"] > 0
    assert any("Карта разностей" in text and "СКО" in text for text in job["log"])
    assert not opened(project, other_id).has("dz") and tmp_files(project) == []  # not a source of the surface
    assert ok(c.get(f"{API}/lines/layers/{cloud_id}"))["cloud"]["modes"] == ["class", "dz"]

    # two clouds in one surface: each gets its column; the parameters stored before `dz` came mean «yes»
    with dialect.read_tx(project.engine) as conn:
        params = dtm_jobs.request(conn, clouds=[{"layer_id": cloud_id}, {"layer_id": other_id}], name="Обе")
    assert params["dz"] is True and params["dz_points"] == cloud.count + 9700
    res, _journal = run_dtm(project, {k: v for k, v in params.items() if k != "dz"})
    assert [d["cloud_id"] for d in res["dz"]] == [cloud_id, other_id]
    for layer_id in (cloud_id, other_id):
        assert opened(project, layer_id).meta["dz"] == {**opened(project, layer_id).meta["dz"], "name": "Обе"}
    check_column(opened(project, other_id), *wireframe(project, res["wireframe_id"]), sample=200)

    # a cancel while the differences are counted: the surface is in the project, the cloud is as it was
    was = opened(project, cloud_id)
    res, journal = run_dtm(project, {**params, "name": "Отмена"}, at="Карта разностей")
    assert res["dz"] == [] and ok(c.get(f"{API}/wireframes/{res['wireframe_id']}"))["name"] == "Отмена"
    assert any(line.startswith("warn:") and "Карта разностей" in line for line in journal)
    assert opened(project, cloud_id).shas == was.shas and tmp_files(project) == []

    # the cloud was classified anew while the surface was being built: the surface stays, the journal says why
    real = cd.compute

    def meanwhile(cloud: lc.Cloud, *args: Any, **kw: Any) -> Any:
        set_values(project, cloud, **{"class": np.full(cloud.count, 2, dtype=np.uint8)})
        return real(cloud, *args, **kw)

    monkeypatch.setattr(cd, "compute", meanwhile)
    with dialect.read_tx(project.engine) as conn:
        params = dtm_jobs.request_from_points(conn, cloud_id=cloud_id, name="Гонка")
    res, journal = run_dtm(project, params)
    assert res["dz"] == [] and res["wireframe_id"] > 0
    assert any(line.startswith("warn:") and "изменилось" in line for line in journal)
    assert opened(project, cloud_id).meta["dz"]["name"] == "Обе" and tmp_files(project) == []


# --- the archive -------------------------------------------------------------------------------------------


def test_bfggis_cycle_renumbers_the_surface_of_dz(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """`meta.cloud.dz.wireframe_id` is an object id: in a copy of the project the surface has another number,
    and a surface deleted before the export must not come back as some other object."""
    from bf_core.archive.view_remap import remap_cloud_dz

    meta = {"cloud": {"format": 1, "dz": {"wireframe_id": 7, "name": "Рельеф", "rms": 0.1}}, "source": {"a": 1}}
    assert remap_cloud_dz(meta, {7: 12}.get)["cloud"]["dz"] == {"wireframe_id": 12, "name": "Рельеф", "rms": 0.1}
    assert remap_cloud_dz(meta, {}.get)["cloud"]["dz"] == {"wireframe_id": None, "name": "Рельеф", "rms": 0.1}
    assert remap_cloud_dz(meta, {7: 12}.get)["source"] == {"a": 1} and meta["cloud"]["dz"]["wireframe_id"] == 7
    for other in ({"cloud": {"dz": None}}, {"built": {}}, None, {"cloud": {"dz": {"name": "x"}}}):
        assert remap_cloud_dz(other, {7: 12}.get) is other  # nothing to renumber: not written again
    c, project = api
    spare = put_surface(project, *surface(67, 200), "Лишний")  # a gap in the ids of the copy
    first, second = add_cloud(project, scene(68, 6000), "Север"), add_cloud(project, scene(69, 5000), "Юг")
    kept = put_surface(project, *surface(70, 400), "Остаётся")
    gone = put_surface(project, *surface(71, 400), "Удалён")
    run_dz(project, request(project, first, kept))
    run_dz(project, request(project, second, gone))
    ok(c.delete(f"{API}/wireframes/{spare}"))
    ok(c.delete(f"{API}/wireframes/{gone}"))
    column = opened(project, first).read("dz").tobytes()
    record = opened(project, first).meta["dz"]

    job = export(c, tmp_path / "разности.bfggis")
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(c, r["source"], r["id"])
    assert job["status"] == "done", job
    new = {int(k): v for k, v in job["result"]["id_maps"]["objects"].items()}
    assert new[kept] != kept and gone not in new
    ok(c.post(f"{API}/projects/{job['result']['uid']}/open"))
    copy = projects_of(c.core).require_current()
    assert copy.uid != project.uid
    back = opened(copy, new[first])
    assert back.meta["dz"] == {**record, "wireframe_id": new[kept]} and back.read("dz").tobytes() == column
    assert ok(c.get(f"{API}/wireframes/{new[kept]}"))["name"] == "Остаётся"
    lost = opened(copy, new[second]).meta["dz"]
    assert lost["wireframe_id"] is None and lost["name"] == "Удалён"  # the name stays, the number does not
    assert ok(c.get(f"{API}/lines/layers/{new[second]}"))["cloud"]["modes"] == ["class", "dz"]
