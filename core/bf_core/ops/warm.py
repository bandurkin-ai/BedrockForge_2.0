"""The warm-up of the operations in the workers (10.11; PROGRESS «Э9 9.36»: the first operation after a start took
~13 s — the numba kernels of the stitch, of the checks of a body and of the store compiled while the user waited).

`warm(order)` runs every step of `order` once as its job runs (`ops/job.py::run`: the preconditions, the sources read
from their blobs, the kernels, `stage_wireframe`, the files, the write) on a tiny project in a temporary folder —
the same calls with the same dtypes, so whatever kernel an operation needs is compiled into NUMBA_CACHE_DIR, or
loaded from it, and stays in the memory of the process; no list of kernels is kept by hand. The operations are
`STEPS` (every one of `registry.PREPARE`; «Построить ЦТМ» is the job `model.dtm`); beside them (`OTHER`) the checks
of a body, the job `wireframe.check` the window starts after an operation, the simplified copies of the view, the
preview of a stitch, the section and the click of the view (the last three run in the process of the API, which
loads them from the cache).

The workers call it in a thread after «ready» (`jobs/worker.py::_warm_kernels`), the quick one in the order `QUICK`
— the checks of a body, then the operations, the stitch first —, the heavy one in `HEAVY` — the strips of a stitch
(`preview`) meanwhile, then what follows an operation and what the API needs, then the operations backwards: on a
cold cache the two halves of the first stitch compile at once and each worker finds the other's in the cache. The
quick worker does not start a step while one of its jobs runs. Linux, 4 cores, the first stitch after a start:
cold cache ~13 s → ~3 s in the E2E (the warm-up ready for it in ~7 s, all of it in ~30 s); from the cache the
warm-up takes ~3 s and the stitch ~0.07 s (was ~0.9 s). A step that fails is logged; the next one goes on.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

Params = Callable[[Mapping[str, Any]], dict[str, Any]]

#: step → (operation, its parameters from the ids of the warm-up project); `view` — the section and the click
STEPS: dict[str, tuple[str, Params]] = {
    "stitch": ("stitch", lambda d: {"contours": [{"layer_id": d["rings"]}]}),
    "stitch_caps": (
        "stitch",
        lambda d: {"contours": [{"layer_id": d["rings"]}], "start_cap": "point", "end_cap": "extend"},
    ),  # fmt: skip
    "boolean": ("boolean", lambda d: {"op": "difference", "a": d["a"], "b": d["b"]}),
    "clip_plane": ("clip_plane", lambda d: {"wireframe_id": d["a"], "point": d["middle"], "normal": [0, 0, 1]}),
    "extrude": ("extrude", lambda d: {"contours": [{"layer_id": d["polygon"]}], "distance": 5, "vertical": True}),
    "split": ("split", lambda d: {"wireframe_id": d["two"]}),
    "merge": ("merge", lambda d: {"wireframe_ids": [d["a"], d["b"]]}),
    "edit_triangles": ("edit_triangles", lambda d: {"wireframe_id": d["a"], "remove": [0, 1]}),
    "repair": ("repair", lambda d: {"wireframe_id": d["open"]}),
    "smooth": ("smooth", lambda d: {"wireframe_id": d["relief"], "iterations": 2}),
    "simplify": ("simplify", lambda d: {"wireframe_id": d["relief"], "target_triangles": 50}),
    "clip_polygon": (
        "clip_polygon",
        lambda d: {"wireframe_id": d["relief"], "polygon": {"layer_id": d["polygon"], "line": None}},
    ),  # fmt: skip
    "clip_surface": ("clip_surface", lambda d: {"wireframe_id": d["b"], "surface_id": d["relief"]}),
    "contours": ("contours", lambda d: {"wireframe_id": d["relief"], "interval": 1, "major_every": 5}),
    "intersection_lines": ("intersection_lines", lambda d: {"a": d["a"], "b": d["relief"]}),
    "volume_level": ("volume_between", lambda d: {"top": d["relief"], "level": d["level"]}),
    "volume_between": ("volume_between", lambda d: {"top": d["relief"], "bottom": d["flat"]}),
    # the windows and the automatic links of a series (`sections/link_kernels.py`, ~0.4 s at the first call; the
    # same kernels serve `POST /section-series/{id}/links` of the process of the API)
    "stitch_series": ("stitch_series", lambda d: {"series_id": d["series"]}),
}
OPS = tuple(STEPS)
#: steps that are not operations: the checks of a body (`checks`: those of the stitch and of `wireframe.check`),
#: the job `wireframe.check` itself (`check`: the levels of detail and the checks — the window starts it after an
#: operation), the simplified copies of the view (`levels`), the preview of a stitch (`preview`: its strips, without
#: the checks), the section and the click of the view (`view`)
OTHER = ("checks", "check", "levels", "preview", "view", "composites", "intercepts", "stats")
#: The quick worker: the checks of a body, then the operations — the stitch first; the heavy one meanwhile the strips
#: of a stitch (`preview`), so that on a cold cache the first stitch has its two halves compiled at once (~5 s, not
#: ~8 s), then what follows an operation and what the process of the API needs, then the operations backwards.
QUICK = ("checks", *OPS, "levels", "preview", "view", "check", "composites", "intercepts", "stats")
HEAVY = ("preview", "check", "levels", "view", *reversed(OPS), "checks", "composites", "intercepts", "stats")
X0, Y0, Z0 = 512_345.678, 6_789_012.345, 250.0  # a UTM position, as the data of a deposit
PREFIX = "bf-warm-"  # the temporary folder of the warm-up project
STALE_S = 24 * 3600


def _cube(lo: float, hi: float) -> tuple[Any, Any]:
    import numpy as np

    v = np.array([[x, y, z] for z in (lo, hi) for y in (lo, hi) for x in (lo, hi)], dtype=np.float64) + (X0, Y0, Z0)
    t = np.array(
        [[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
         [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]], dtype=np.int32,
    )  # fmt: skip
    return v, t


def _surface(z: Callable[[Any, Any], Any]) -> tuple[Any, Any]:
    """z(x, y) over (-5, 25)² in 10 × 10 cells, normals up."""
    import numpy as np

    xs = np.linspace(-5.0, 25.0, 11)
    gx, gy = np.meshgrid(xs, xs, indexing="ij")
    v = np.column_stack([gx.ravel(), gy.ravel(), np.asarray(z(gx, gy), dtype=np.float64).ravel()]) + (X0, Y0, Z0)
    i = (np.arange(10)[:, None] * 11 + np.arange(10)[None, :]).ravel()
    t = np.concatenate([np.stack([i, i + 11, i + 12], axis=1), np.stack([i, i + 12, i + 1], axis=1)])
    return v, t.astype(np.int32)


def project(root: Path) -> dict[str, Any]:
    """The warm-up project in `root`: two crossing cubes, a cube without a face, two cubes in one wireframe, a
    relief and a flat surface, three contours with a tie line, a polygon, a series of three plans through the
    contours. → its folder and the ids."""
    import numpy as np

    from bf_core.db import dialect
    from bf_core.formats.lineset import from_lines
    from bf_core.lines import store as ls_store
    from bf_core.projects.project import create_folder
    from bf_core.sections import store as series_store
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as ws

    folder = create_folder(root, "Прогрев", {})
    a, b = _cube(0.0, 10.0), _cube(5.0, 15.0)
    far = _cube(30.0, 35.0)
    ring = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])
    rings = [
        np.column_stack([ring, np.zeros(4)]) + (X0, Y0, Z0),
        np.column_stack([ring + 0.5, np.full(4, 10.0)]) + (X0, Y0, Z0),
        np.column_stack([np.insert(ring, 2, [10.0, 5.0], axis=0), np.full(5, 20.0)]) + (X0, Y0, Z0),
    ]
    tie = np.array([rings[0][0], rings[1][0]])
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.write_tx(engine) as conn:
            store = BlobStore(folder)
            ids: dict[str, Any] = {
                "a": ws.put_wireframe(conn, store, *a, name="Куб A"),
                "b": ws.put_wireframe(conn, store, *b, name="Куб B"),
                "open": ws.put_wireframe(conn, store, a[0], a[1][:-2], name="Без грани"),
                "two": ws.put_wireframe(
                    conn, store, np.vstack([a[0], far[0]]), np.vstack([a[1], far[1] + 8]), name="Два куба"
                ),  # fmt: skip
                "relief": ws.put_wireframe(conn, store, *_surface(lambda x, y: 5.0 + 0.1 * x + 0.05 * y), name="Р"),
                "flat": ws.put_wireframe(conn, store, *_surface(lambda x, y: 0.0 * x + 6.0), name="Подошва"),
            }
            file_id = ls_store.create_file(conn, store, name="контуры.str", meta={"format": "micromine_str"})
            ids["rings"] = ls_store.put_layer(
                conn, store,
                from_lines("Контуры", [*rings, tie], closed=[True, True, True, False],
                           attrs={"ROLE": [None, None, None, "TIE"]}),
                file_id=file_id,
            )  # fmt: skip
            square = np.column_stack([ring * 0.4 + 3.0, np.zeros(4)]) + (X0, Y0, Z0)
            ids["polygon"] = ls_store.put_layer(conn, store, from_lines("Полигон", [square], closed=[True]),
                                                file_id=file_id)  # fmt: skip
            plans = {"type": "plan", "p1": [X0, Y0, Z0 + 20.0], "s0": 0.0, "s1": 20.0, "step": 10.0,
                     "towards": 5.0, "away": 5.0, "contour_layer": ids["rings"]}  # the contours on k = 2, 1, 0
            ids["series"] = series_store.create(conn, plans, "Планы")["id"]  # fmt: skip
    finally:
        engine.dispose()
    ids["middle"] = [X0, Y0, Z0 + 5.0]
    ids["level"] = Z0 + 4.0
    ids["folder"] = str(folder)
    return ids


def _quiet(_message: tuple[Any, ...]) -> None:
    pass


def run_step(folder: Path, op: str, params: dict[str, Any], job_id: int = 1) -> dict[str, Any]:
    """One operation as its job, in this thread: the preconditions as the API checks them, then `ops/job.py::run`."""
    from bf_core.db import dialect
    from bf_core.jobs.context import JobContext
    from bf_core.storage.blobs import BlobStore

    from . import job as ops_job
    from . import registry

    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.read_tx(engine) as conn:
            checked = registry.precheck(conn, BlobStore(folder), op, params)
    finally:
        engine.dispose()
    job = {"id": job_id, "params": ops_job.job_params(checked), "project_folder": str(folder)}
    ctx = JobContext(job, _quiet, threading.Event())
    try:
        return ops_job.run(ctx)
    finally:
        ctx.close()


def view(folder: Path, wf_id: int) -> None:
    """The section of a wireframe by a plane and by a section line with its ends, and a click on it — as the
    process of the API makes them (`viz/router.py`), its geometry from the blobs."""
    from types import SimpleNamespace

    from bf_core.db import dialect
    from bf_core.viz import meshes as vm
    from bf_core.wireframes import exact

    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    proj = SimpleNamespace(folder=folder, uid=f"warm-{id(engine)}", engine=engine)
    try:
        with dialect.read_tx(engine) as conn:
            state = vm.mesh_state(conn, wf_id)
        at = f"{X0 + 5.0!r},{Y0 + 5.0!r},{Z0 + 5.0!r}"
        exact.section(proj, state, exact.parse_plane(point=at, normal="1,0,0"))
        line = exact.parse_plane(x0=repr(X0 - 1.0), y0=repr(Y0 + 4.0), az="80", length="12", offset="0.5")
        exact.section(proj, state, line)
        exact.pick(proj, [X0 + 5.0, Y0 + 5.0, Z0 + 100.0], [0.0, 0.0, -1.0], [wf_id])
        exact.pick(proj, [X0 + 5.0, Y0 - 50.0, Z0 + 5.0], [0.0, 1.0, 0.0], [wf_id], clip=[[0.0, 1.0, 0.0, -Y0]])
    finally:
        exact.forget()  # the arrays of the warm-up project: not kept beside those of the open one
        engine.dispose()


def checks(folder: Path, wf_id: int) -> None:
    """The checks of a closed body on a wireframe as stored (`geo/checks.py::run_checks`, as `wireframe.check` calls
    it; the stitch calls the same kernels on its result)."""
    from bf_core.db import dialect
    from bf_core.geo import checks as gc
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as ws

    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.read_tx(engine) as conn:
            v, t = ws.read_wireframe(conn, BlobStore(folder), wf_id)
    finally:
        engine.dispose()
    gc.run_checks(v, t)


def check(folder: Path, wf_id: int, job_id: int = 1) -> None:
    """The job `wireframe.check` of a wireframe, as a worker runs it: its levels of detail and its checks."""
    from bf_core.jobs.context import JobContext
    from bf_core.wireframes import check as wf_check

    job = {"id": job_id, "params": {"wireframe_ids": [wf_id]}, "project_folder": str(folder)}
    ctx = JobContext(job, _quiet, threading.Event())
    try:
        wf_check.wireframe_check(ctx)
    finally:
        ctx.close()


def levels() -> None:
    """The simplified copies of the view (`viz/meshes.py`: `wireframe.lod`, the parts of the 3D view in the process
    of the API — made there cold, they held its requests for ~7 s at the first view): a tilted plane (flat areas)
    and a bumpy one, through the same calls and dtypes as a view."""
    import numpy as np

    from bf_core.geo import meshes as gm

    n = 9
    x, y = np.meshgrid(np.arange(n, dtype=float), np.arange(n, dtype=float))
    idx = np.arange(n * n).reshape(n, n)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    tri = np.r_[np.c_[a, b, d], np.c_[a, d, c]]
    for z in (0.1 * x, np.sin(x) * np.cos(y)):
        v, t = gm.check_mesh(np.c_[x.ravel() + 500_000.0, y.ravel() + 7_000_000.0, z.ravel()], tri)
        for p in gm.parts_table(v, t):
            gm.levels(*gm.part_mesh(v, t, p))


def _sweep(root: Path) -> None:
    """Warm-up folders left by a worker that was stopped in the middle (a day old: never one being used)."""
    now = time.time()
    for old in root.glob(f"{PREFIX}*"):
        try:
            if old.is_dir() and now - old.stat().st_mtime > STALE_S:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            continue


def warm(order: Iterable[str] = QUICK, *, busy: Callable[[], bool] | None = None) -> dict[str, float]:
    """Every step of `order` (`STEPS` and `OTHER`) on a warm-up project in a temporary folder, removed at the end.
    `busy()` true — a job of the worker runs: the next step waits for its end (the quick worker: its jobs are not
    slowed by the warm-up, but by a step already going). → seconds a step (a failed step is logged and missing).
    Never raises but for the project itself."""
    from .preview import warm as warm_preview

    log = logging.getLogger("bf_core.ops.warm")
    took: dict[str, float] = {}
    _sweep(Path(tempfile.gettempdir()))
    with tempfile.TemporaryDirectory(prefix=PREFIX, ignore_cleanup_errors=True) as tmp:
        ids = project(Path(tmp))
        folder = Path(ids["folder"])
        for k, name in enumerate(order):
            while busy is not None and busy():
                time.sleep(0.05)
            t0 = time.perf_counter()
            try:
                if name == "levels":
                    levels()
                elif name == "checks":
                    checks(folder, int(ids["a"]))
                elif name == "check":
                    check(folder, int(ids["a"]), job_id=k + 1)
                elif name == "preview":
                    warm_preview()
                elif name == "view":
                    view(folder, int(ids["a"]))
                elif name == "composites":
                    from bf_core.composites.warm import warm as warm_composites

                    warm_composites()
                elif name == "intercepts":
                    from bf_core.intercepts.tt import warm as warm_intercepts

                    warm_intercepts()
                elif name == "stats":
                    from bf_core.stats.warm import warm as warm_stats

                    warm_stats()
                else:
                    op, params = STEPS[name]
                    run_step(folder, op, params(ids), job_id=k + 1)
            except Exception:  # noqa: BLE001 - a warm-up never stops a worker; the job compiles it itself then
                log.exception("warm-up step %s failed", name)
                continue
            took[name] = time.perf_counter() - t0
    return took
