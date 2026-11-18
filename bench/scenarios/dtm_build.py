"""«Построить ЦТМ» and the parts and levels of detail of its wireframe (ADR 0011 «Замеры»: «Построить ЦТМ»
100 тыс. / 1 млн точек; ТЗ 5.3 «первое изображение каркаса ≤ 3 с»).

A layer of survey points on UTM coordinates (a relief with noise, the way a survey gives it: millimetres) and
a layer of breaklines across it → the job `model.dtm` into a project database (`build_s`):
  read          the layers from their blobs;
  triangulate   `geo/dtm.py::build_dtm` (PythonCDT);
  store         spatial order of the triangles, parts, measures, the blobs (`wireframes/store.py`);
  lod           levels 1/8 and 1/64 of every part into `cache\\mesh\\` (pyfqmr, `viz/meshes.py`);
then, as the 3D window opens the DTM in a fresh process:
  first_frame_s the manifest and every part at level 1/64 (from the cache);
  full_s        every part at level 0 (from the blobs).
`max_node_error_m` — the largest distance between a source point and the vertex of the stored surface at its
place: the surface passes through the points exactly (0).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

BREAKLINES = 20
STAGES = {
    "Чтение исходных данных": "read_s",
    "Триангуляция": "triangulate_s",
    "Проверка каркаса": "store_s",
    "Уровни детализации": "lod_s",
    "Сохранение каркаса в проект": "commit_s",
}


def _points(n: int) -> Any:
    import numpy as np

    rng = np.random.default_rng(19)
    side = 10.0 * np.sqrt(n)  # about one point per 100 m²: 1 million points — 10 km × 10 km
    xy = rng.random((n, 2)) * side
    z = 300.0 + 50.0 * np.sin(xy[:, 0] / 700.0) * np.cos(xy[:, 1] / 900.0) + rng.normal(0.0, 0.3, n)
    pts = np.column_stack([xy + [400_000.0, 4_600_000.0], z])
    return np.round(pts, 3), side


def _breaklines(side: float) -> list[Any]:
    """Lines across the area, every one at its own level (they cross nothing: parallel)."""
    import numpy as np

    out = []
    for k in range(BREAKLINES):
        s = np.linspace(0.02, 0.98, 400)
        y = side * (k + 0.5) / BREAKLINES + 3.0 * np.sin(s * 40.0)
        line = np.column_stack([400_000.0 + side * s, 4_600_000.0 + y, np.full(len(s), 320.0 + k)])
        out.append(np.round(line, 3))
    return out


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    from bf_core.db import dialect
    from bf_core.formats.lineset import from_lines, from_points
    from bf_core.lines import store as ls_store
    from bf_core.projects.project import create_folder
    from bf_core.storage.blobs import BlobStore

    pts, side = _points(int(params["points"]))
    np.save(work / "points.npy", pts)
    folder = create_folder(work, "ЦТМ", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.write_tx(engine) as conn:
            store = BlobStore(folder)
            file_id = ls_store.create_file(conn, store, name="съёмка.csv", meta={"format": "csv"})
            layers = [
                ls_store.put_layer(conn, store, from_points("Пикеты", pts), file_id=file_id),
                ls_store.put_layer(conn, store, from_lines("Бровки", _breaklines(side)), file_id=file_id),
            ]
    finally:
        engine.dispose()
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")
    (work / "layers.txt").write_text(",".join(str(i) for i in layers), encoding="utf-8")
    load()  # compile into the cache outside the timing


def load() -> None:
    import numpy as np
    import pyarrow  # noqa: F401
    import pyfqmr  # noqa: F401
    import pythoncdt  # noqa: F401

    import bf_core.lines.store  # noqa: F401
    import bf_core.viz.meshes  # noqa: F401
    import bf_core.wireframes.jobs  # noqa: F401
    from bf_core.geo.dtm import build_dtm

    rng = np.random.default_rng(1)
    pts = rng.random((200, 3)) * 100.0
    line = np.array([[5.0, 5.0, 50.0], [95.0, 90.0, 50.0]])
    ring = np.array([[1.0, 1.0], [99.0, 1.0], [99.0, 99.0], [1.0, 99.0]])
    build_dtm(pts, [line], boundary=ring, max_edge=80.0)  # every kernel of the build


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time
    from types import SimpleNamespace

    import numpy as np

    from bf_core.db import dialect
    from bf_core.jobs.context import JobContext
    from bf_core.storage.blobs import BlobStore
    from bf_core.viz import binary
    from bf_core.viz import meshes as viz
    from bf_core.wireframes import jobs as dtm_jobs
    from bf_core.wireframes import store as wf_store

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    layers = [int(i) for i in (work / "layers.txt").read_text(encoding="utf-8").split(",")]
    stamp = time.time_ns() // 1_000_000 % 1_000_000_000  # a repeated run is a new job and a new wireframe
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    marks: dict[str, float] = {}

    class Ctx(JobContext):
        def tick(self, progress: float | None = None, message: str | None = None) -> None:
            if message in STAGES and message not in marks:
                marks[str(message)] = time.perf_counter()

    try:
        with dialect.read_tx(engine) as conn:
            job_params = dtm_jobs.request(conn, name=f"ЦТМ {stamp}", layer_ids=layers)
        ctx = Ctx(
            {"id": stamp, "params": job_params, "project_folder": str(folder)}, lambda _m: None, threading.Event()
        )
        t0 = time.perf_counter()
        try:
            result = dtm_jobs.model_dtm(ctx)
        finally:
            ctx.close()
        t1 = time.perf_counter()

        project = SimpleNamespace(engine=engine, uid=f"bench-{stamp}", folder=folder)
        viz.forget()
        with dialect.read_tx(engine) as conn:
            state = viz.mesh_state(conn, result["wireframe_id"])
        head = binary.decode(viz.manifest(project, state))[0]
        coarse = [viz.part(project, state, p["k"], 2) for p in head["parts"]]
        t2 = time.perf_counter()
        full = [viz.part(project, state, p["k"], 0) for p in head["parts"]]
        t3 = time.perf_counter()
        with dialect.read_tx(engine) as conn:
            v, _t = wf_store.read_wireframe(conn, BlobStore(folder), result["wireframe_id"])
    finally:
        engine.dispose()

    pts = np.load(work / "points.npy")
    assert result["points"] == len(v) == len(pts) + BREAKLINES * 400, result
    error = float(np.abs(v[: len(pts)] - pts).max())  # the points come first, in their order
    assert error == 0.0, error
    edges = [marks[m] for m in STAGES] + [t1]
    stages = {name: round(b - a, 3) for name, a, b in zip(STAGES.values(), edges, edges[1:], strict=False)}
    return {
        "points": len(pts),
        "triangles": result["triangles"],
        "parts": len(head["parts"]),
        "lod_triangles": result["lod_triangles"],
        "build_s": round(t1 - t0, 3),
        **stages,
        "first_frame_s": round(t2 - t1, 3),
        "full_s": round(t3 - t2, 3),
        "coarse_mb": round(sum(len(b) for b in coarse) / 1e6, 2),
        "full_mb": round(sum(len(b) for b in full) / 1e6, 2),
        "max_node_error_m": error,
    }
