"""«Построить ЦТМ из облака» (ADR 0014 «Замеры»: 1 млн / 10 млн точек — ≤ 10 с, 800 МБ / ≤ 60 с, 2 ГБ; вершин
≤ 2 млн; СКО по нормали ≤ 0,15 м).

The scene «карьер» (`tools/gen/cloud.py::quarry`, true labels: pits with a 50° wall and with vertical benches, a
dump, a plain with trees and buildings) is stored as a cloud of the project (`stage_cloud`), then classified by
the job `ground.classify`, profile «Карьер» — outside the timing (`params.classes = "truth"` gives the cloud
the true labels instead: the surface without the filter). Measured — the job `model.dtm` as the command
`terrain.from_points {cloud_id}` starts it (`build_s`):
  thin          the points of the ground by slices of the cloud, one in every cell of the plan — the lower
                median of the height above the ground (`wireframes/cloud_source.py`);
  triangulate   `geo/dtm.py::build_dtm` over the vertices left;
  store, lod    the wireframe and its levels of detail (as in `dtm_build`);
  dz            the map of differences of the cloud against the surface built — the last step of the job
                (`wireframes/cloud_dz.py`): every point of the cloud, not only its ground (`dz_s`; `dz_rms_m`,
                `dz_p95_m`, `dz_max_m` — |dz| of the ground, `dz_outside` — points beside the surface).
Checked after the job, outside `build_s` (`verify_s`):
  vertices      of the surface — at most 2 million; `cell` — the step of the thinning the job chose;
  foreign       vertices that are not points of the cloud bit for bit (must be 0);
  not_ground    vertices that are not of class 2 in the cloud (must be 0); `truth_ground` — the share of the
                vertices that are true ground;
  rms_normal_m  the root mean square of the distance between the surface and the true ground along its normal,
                at the nodes of a 1 m grid over the site (as Т-З2 measures it; `p95_normal_m`, `max_normal_m` —
                for the report). The nodes within `WALL_ZONE` of a vertical wall of a bench are left out
                (`wall_nodes` — their share; `rms_all_m` — with them): a surface that has one height at every
                place cannot follow a wall between two points a step apart.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

SEED = 17
THREADS = 4
PORTION = 1_000_000
NODE = 1.0  # the grid the surface is compared with the true ground on, m
WALL_ZONE = 2  # nodes around a jump of the true ground that are left out
WALL_JUMP = 2.0  # a jump: the true ground differs by more than this between two nodes, m
STAGES = {
    "Прореживание облака": "thin_s",
    "Триангуляция": "triangulate_s",
    "Проверка каркаса": "store_s",
    "Уровни детализации": "lod_s",
    "Сохранение каркаса в проект": "commit_s",
    "Карта разностей": "dz_s",
}


def _gen() -> Any:
    import sys

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools.gen import cloud

    return cloud


def _hash(xyz: Any) -> Any:
    """A 64-bit hash of the bits of every point: the same point — the same hash."""
    import numpy as np

    bits = np.ascontiguousarray(xyz, dtype="<f8").view(np.uint64).reshape(-1, 3)
    h = bits[:, 0] * np.uint64(0x9E3779B97F4A7C15) ^ bits[:, 1] * np.uint64(0xC2B2AE3D27D4EB4F)
    h ^= bits[:, 2] * np.uint64(0x165667B19E3779F9)
    h ^= h >> np.uint64(29)
    h *= np.uint64(0xBF58476D1CE4E5B9)
    return h ^ (h >> np.uint64(32))


def _ctx(job: dict[str, Any], marks: dict[str, float]) -> Any:
    """A `JobContext` that remembers when every stage began."""
    import time

    from bf_core.jobs.context import JobContext

    class Ctx(JobContext):
        def tick(self, progress: float | None = None, message: str | None = None) -> None:
            if message is not None and message not in marks:
                marks[str(message)] = time.perf_counter()

    return Ctx(job, lambda _m: None, threading.Event())


_KERNELS: dict[str, Any] = {}


def _raster() -> Any:
    """The kernel that gives the heights of a surface at the nodes of a grid (NaN outside it); compiled once
    a process — by `load`, outside the timing."""
    if "raster" in _KERNELS:
        return _KERNELS["raster"]
    import numpy as np

    from bf_core.jit import njit

    @njit(nogil=True)
    def raster(vx, vy, vz, tri, x0, y0, step, nx, ny):  # type: ignore[no-untyped-def]
        out = np.full((ny, nx), np.nan)
        for t in range(len(tri)):
            a, b, c = tri[t, 0], tri[t, 1], tri[t, 2]
            ax, ay, bx, by, cx, cy = vx[a], vy[a], vx[b], vy[b], vx[c], vy[c]
            det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
            if det == 0.0:
                continue
            i0 = max(int(np.ceil((min(ax, bx, cx) - x0) / step)), 0)
            i1 = min(int(np.floor((max(ax, bx, cx) - x0) / step)), nx - 1)
            j0 = max(int(np.ceil((min(ay, by, cy) - y0) / step)), 0)
            j1 = min(int(np.floor((max(ay, by, cy) - y0) / step)), ny - 1)
            for j in range(j0, j1 + 1):
                y = y0 + j * step
                for i in range(i0, i1 + 1):
                    x = x0 + i * step
                    wb = ((x - ax) * (cy - ay) - (cx - ax) * (y - ay)) / det
                    wc = ((bx - ax) * (y - ay) - (x - ax) * (by - ay)) / det
                    if wb >= -1e-12 and wc >= -1e-12 and wb + wc <= 1.0 + 1e-12:
                        out[j, i] = vz[a] + wb * (vz[b] - vz[a]) + wc * (vz[c] - vz[a])
        return out

    _KERNELS["raster"] = raster
    return raster


def _nodes(extent: Any) -> tuple[float, float, int, int]:
    x0, y0, x1, y1 = (float(v) for v in extent)
    return x0 + 0.5 * NODE, y0 + 0.5 * NODE, int((x1 - x0) / NODE), int((y1 - y0) / NODE)


def prepare(params: dict[str, Any], work: Path) -> None:
    import json

    import numpy as np

    from bf_core.db import dialect
    from bf_core.lines import cloud as lc
    from bf_core.lines import cloud_build as cb
    from bf_core.lines import ground_job
    from bf_core.lines import store as ls_store
    from bf_core.projects.project import create_folder
    from bf_core.storage.blobs import BlobStore

    scene = _gen().quarry(int(params["points"]), SEED, order="storage")
    n = len(scene)
    folder = create_folder(work, "ЦТМ из облака", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    store = BlobStore(folder)

    def portions() -> Any:
        for a in range(0, n, PORTION):
            yield lc.CloudChunk(scene.xyz[a : a + PORTION], None, scene.ret[a : a + PORTION])

    try:
        staged = cb.stage_cloud(store, portions, expected=n)
        with dialect.write_tx(engine) as conn:
            file_id = ls_store.create_file(conn, store, name="карьер.las", meta={"format": "las"})
            cloud_id = lc.commit_cloud(conn, store, staged, file_id=file_id, name="Карьер", source={"kind": "las"})
        with dialect.read_tx(engine) as conn:
            cloud = lc.open_cloud(conn, store, cloud_id)
        seq = cloud.read("seq")  # the stored point i is the point seq[i] of the scene
        if params.get("classes", "filter") == "truth":
            values = lc.stage_values(store, cloud, {"class": scene.truth[seq]})
            with dialect.write_tx(engine) as conn:
                lc.replace_values(conn, store, cloud, values)
        else:
            with dialect.read_tx(engine) as conn:
                job = ground_job.request(conn, cloud_id, threads=THREADS)
            ctx = _ctx({"id": 1, "params": job, "project_folder": str(folder)}, {})
            try:
                ground_job.ground_classify(ctx)
            finally:
                ctx.close()
        # what the check of the vertices needs, by the stored points: their hashes in rising order, the place of
        # each in the cloud, whether it is true ground
        hashes = _hash(cloud.read("xyz"))
        order = np.argsort(hashes, kind="stable")
        np.save(work / "hash.npy", hashes[order])
        np.save(work / "row.npy", order.astype(np.uint32))
        np.save(work / "truth.npy", scene.truth[seq] == _gen().GROUND)
        # the true ground and the cosine of its slope at the nodes; the nodes at a vertical wall
        x0, y0, nx, ny = _nodes(scene.extent)
        gy, gx = np.mgrid[0:ny, 0:nx]
        gx, gy = x0 + gx * NODE, y0 + gy * NODE
        z = scene.ground_z(gx, gy)
        jump = np.zeros(z.shape, dtype=bool)
        jump[:, 1:] |= np.abs(np.diff(z, axis=1)) > WALL_JUMP
        jump[1:, :] |= np.abs(np.diff(z, axis=0)) > WALL_JUMP
        wall = jump.copy()
        for dj in range(-WALL_ZONE, WALL_ZONE + 1):
            for di in range(-WALL_ZONE, WALL_ZONE + 1):
                wall[max(dj, 0) : ny + min(dj, 0), max(di, 0) : nx + min(di, 0)] |= jump[
                    max(-dj, 0) : ny + min(-dj, 0), max(-di, 0) : nx + min(-di, 0)
                ]
        np.save(work / "ground_z.npy", z)
        np.save(work / "ground_cos.npy", scene.ground_cos(gx, gy))
        np.save(work / "wall.npy", wall)
    finally:
        engine.dispose()
    prepared = {"folder": str(folder), "cloud": cloud_id, "nodes": [x0, y0, nx, ny]}
    (work / "prepared.json").write_text(json.dumps(prepared), encoding="utf-8")
    load()  # compile into the cache outside the timing


def load() -> None:
    import numpy as np
    import pyfqmr  # noqa: F401
    import pythoncdt  # noqa: F401

    import bf_core.viz.meshes  # noqa: F401
    import bf_core.wireframes.jobs  # noqa: F401
    from bf_core.geo import cloud_thin, dz_kernels, meshes
    from bf_core.geo.dtm import build_dtm

    rng = np.random.default_rng(1)
    pts = rng.random((400, 3)) * 100.0
    v, t, _report = build_dtm(pts, max_edge=80.0)  # the kernels of the triangulation
    assert len(t) >= meshes.LOD_MIN_TRIANGLES
    meshes.levels(np.asarray(v, dtype=np.float64), np.asarray(t, dtype=np.int32))  # and of the levels of detail
    cell = np.repeat(np.arange(40, dtype=np.int64), 5)
    cell[:80] = 0  # a cell with more points than the short sort takes
    order = np.argsort(cell)
    out = np.empty(len(cell), dtype=np.int64)
    for key in (pts[:200, 2].copy(), pts[:200, 2].astype(np.float32)):  # Z and the height above the ground
        cloud_thin.cell_medians(cell, key, order, 0, len(cell), out, 0)
    _raster()(v[:, 0], v[:, 1], v[:, 2], np.asarray(t, dtype=np.int64), 0.5, 0.5, 1.0, 4, 4)
    dz_kernels.warm()  # the map of differences after the surface


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import json
    import time

    import numpy as np

    from bf_core.db import dialect
    from bf_core.lines import cloud as lc
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import jobs as dtm_jobs
    from bf_core.wireframes import store as wf_store

    prepared = json.loads((work / "prepared.json").read_text(encoding="utf-8"))
    folder = Path(prepared["folder"])
    stamp = time.time_ns() // 1_000_000 % 1_000_000_000  # a repeated run is a new job and a new wireframe
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    marks: dict[str, float] = {}
    try:
        with dialect.read_tx(engine) as conn:
            job = dtm_jobs.request_from_points(conn, cloud_id=prepared["cloud"], name=f"ЦТМ {stamp}")
        ctx = _ctx({"id": stamp, "params": job, "project_folder": str(folder)}, marks)
        t0 = time.perf_counter()
        try:
            result = dtm_jobs.model_dtm(ctx)
        finally:
            ctx.close()
        t1 = time.perf_counter()

        store = BlobStore(folder)
        with dialect.read_tx(engine) as conn:
            v, tri = wf_store.read_wireframe(conn, store, result["wireframe_id"])
            built = wf_store.wireframe_info(conn, result["wireframe_id"])["built"]
            cloud = lc.open_cloud(conn, store, prepared["cloud"])
        source = built["sources"][0]
    dz = (result.get("dz") or [{}])[0]
        hashes = np.load(work / "hash.npy", mmap_mode="r")
        mine = _hash(v)
        at = np.minimum(np.searchsorted(hashes, mine), len(hashes) - 1)
        found = np.asarray(hashes[at]) == mine
        rows = np.load(work / "row.npy", mmap_mode="r")[at[found]].astype(np.int64)
        classes = cloud.read("class")[rows]
        truth = np.load(work / "truth.npy", mmap_mode="r")[rows]
        x0, y0, nx, ny = prepared["nodes"]
        z = _raster()(v[:, 0], v[:, 1], v[:, 2], np.asarray(tri, dtype=np.int64), x0, y0, NODE, nx, ny)
        dev = (z - np.load(work / "ground_z.npy")) * np.load(work / "ground_cos.npy")
        covered = np.isfinite(dev)
        wall = np.load(work / "wall.npy") & covered
        every, dev = dev[covered], np.abs(dev[covered & ~wall])
        t2 = time.perf_counter()
    finally:
        engine.dispose()

    edges = [marks[m] for m in STAGES] + [t1]
    stages = {name: round(b - a, 3) for name, a, b in zip(STAGES.values(), edges, edges[1:], strict=False)}
    return {
        "points": cloud.count,
        "class_points": source["class_points"],
        "vertices": len(v),
        "cell": source["cell"],
        "max_edge": job["max_edge"],
        "triangles": result["triangles"],
        "lod_triangles": result["lod_triangles"],
        "build_s": round(t1 - t0, 3),
        **stages,
        "verify_s": round(t2 - t1, 3),
        "dz_points": dz.get("with_dz"),
        "dz_outside": dz.get("outside"),
        "dz_rms_m": dz.get("rms"),
        "dz_p95_m": dz.get("p95"),
        "dz_max_m": dz.get("max"),
        "foreign": int(len(v) - np.count_nonzero(found)),
        "not_ground": int(np.count_nonzero(classes != 2)),
        "truth_ground": round(float(np.count_nonzero(truth) / max(len(truth), 1)), 4),
        "nodes": int(np.count_nonzero(covered)),
        "wall_nodes": round(float(np.count_nonzero(wall) / max(np.count_nonzero(covered), 1)), 4),
        "rms_normal_m": round(float(np.sqrt(np.mean(dev * dev))), 4),
        "p95_normal_m": round(float(np.percentile(dev, 95)), 4),
        "max_normal_m": round(float(dev.max()), 3),
        "rms_all_m": round(float(np.sqrt(np.mean(every * every))), 4),
    }
