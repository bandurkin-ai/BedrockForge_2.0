"""Job `wireframe.lod` — the levels of detail of a wireframe for the 3D window (ADR 0015 §8, debt of Э7–Э8).

The process of the API never simplifies a mesh (pyfqmr held its interpreter: requests of the core waited for
seconds). A part asked at level 1 or 2 whose file is not in `cache\\mesh\\<key>\\` is answered whole, as a
stand-in (`viz/meshes.py::part_answer`, `standin: true`, no ETag), and the router asks for this job (`request`):

* one job a wireframe: none is queued while a `wireframe.check` or a `wireframe.lod` of that wireframe is queued
  or running (the check makes the levels too); at most `MAX_REQUESTS` a geometry while the core runs, so a
  folder that cannot be written (the job is done, the files are not there) does not start a job a frame;
* `wireframe.lod` goes to the queue `quick` up to `QUICK_TRIANGLES` triangles, `wireframe.lod.heavy` (the same
  handler) to `heavy` above: a kind has one queue (`jobs/registry.py`);
* the job: `viz.meshes.build_cache(missing_only=True)` over the blobs mapped from the disk (only the pages of
  the part being made are read), the levels made an earlier way (`ALGO`) removed. A cancel keeps the parts
  made so far (their files are whole). A wireframe deleted meanwhile, or whose id another wireframe has taken,
  leaves no folder behind when nobody else has its geometry.

Its end is the event `wireframes.lod` {source, job, wireframe_ids} (`jobs/scheduler.py`): the window reads the
levels 1–2 of the parts it holds as stand-ins again (9.16).

numpy and the viz modules load with the job, pyfqmr and numba with its first part: the router imports this
module lazily.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import jobs
from bf_core.jobs.context import JobCancelled, JobContext
from bf_core.jobs.registry import handler

from . import store as ws

if TYPE_CHECKING:
    from bf_core.jobs.scheduler import Scheduler
    from bf_core.viz.meshes import MeshState

KIND = "wireframe.lod"
KIND_HEAVY = "wireframe.lod.heavy"
CHECK = "wireframe.check"
QUICK_TRIANGLES = 1_000_000
MAX_REQUESTS = 3  # jobs a geometry of a wireframe while the core runs
TITLE = "Копии каркаса для показа"
STEP = "копии для показа"

_lock = threading.Lock()
_asked: dict[tuple[str, int, str], int] = {}


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def _covers(params: Any, wf_id: int) -> bool:
    if not isinstance(params, Mapping):
        return False
    ids = params.get("wireframe_ids")
    return params.get("wireframe_id") == wf_id or (isinstance(ids, list) and wf_id in ids)


def active_job(conn: sa.Connection, wf_id: int) -> int | None:
    """The id of a `wireframe.check` or `wireframe.lod` of the wireframe that is queued or running."""
    rows = conn.execute(
        sa.select(jobs.c.id, jobs.c.params).where(
            jobs.c.status.in_(("queued", "running")), jobs.c.kind.in_((CHECK, KIND, KIND_HEAVY))
        )
    ).all()
    return next((int(r.id) for r in rows if _covers(r.params, wf_id)), None)


def forget() -> None:
    with _lock:
        _asked.clear()


def request(sched: Scheduler, project: Any, state: MeshState) -> int | None:
    """Ask for the levels of a wireframe shown with stand-ins: the id of the job that makes them (one queued
    or running already, or a new one); None — none is asked (a project opened read-only, a project the
    scheduler does not hold, `MAX_REQUESTS` spent). Parts asked at once start one job: the lock and the row
    written by `submit` before it returns."""
    if getattr(project, "read_only", False):
        return None
    wf_id = state.wireframe_id
    seen = (str(project.uid), wf_id, state.cache_key())
    with _lock:
        with dialect.read_tx(project.engine) as conn:
            running = active_job(conn, wf_id)
        if running is not None:
            return running
        if _asked.get(seen, 0) >= MAX_REQUESTS:
            return None
        kind = KIND if state.triangles <= QUICK_TRIANGLES else KIND_HEAVY
        params = {"wireframe_id": wf_id, "key": state.cache_key(), "triangles": state.triangles}
        try:
            _key, job_id = sched.submit(kind, params, title=f"{TITLE}: «{state.name}»", source=str(project.uid))
        except LookupError:  # the project is not the one the scheduler holds (closed meanwhile)
            return None
        _asked[seen] = _asked.get(seen, 0) + 1
        return job_id


def _memory(params: dict[str, Any]) -> int:
    """MB: the part table and one part being simplified (its vertices from the mapped blobs, the copies of
    pyfqmr and of the flat regions) over the libraries; the blobs are mapped, not read."""
    return 300 + int(int(params.get("triangles") or 0) * 16 / 2**20)


def _gone(engine: sa.Engine, state: MeshState) -> bool:
    """The wireframe was deleted, or its id belongs to another geometry now, and no object of the project has
    the geometry of these blobs: the folder of its levels is nobody's."""
    from bf_core.viz import meshes as vm

    with dialect.read_tx(engine) as conn:
        try:
            same = vm.mesh_state(conn, state.wireframe_id).key() == state.key()
        except ApiError:
            same = False
        return not same and not ws.geometry_shared(conn, dict(state.blobs))


def _run(ctx: JobContext) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore
    from bf_core.viz import meshes as vm

    folder = ctx.project_folder
    if folder is None:
        raise RuntimeError("wireframe.lod is a project job")
    wf_id = int(ctx.params["wireframe_id"])
    ctx.tick(0.0, STEP)
    engine = ctx.engine()
    try:
        with dialect.read_tx(engine) as conn:
            state = vm.mesh_state(conn, wf_id)
    except ApiError as exc:
        if exc.status != 404:
            raise
        ctx.log(f"Каркас №{wf_id} удалён до начала задания: копии не нужны.", "warn")
        return {"wireframe_id": wf_id, "wireframe_ids": [], "parts": 0, "built": 0}
    key = state.cache_key()
    with vm.mapped(BlobStore(folder), state) as (v, t):
        parts = vm.table_of(state, v, t)
        made = vm.build_cache(folder, key, v, t, parts, lambda f: ctx.tick(0.98 * f, STEP), missing_only=True)
    for algo in range(1, vm.ALGO):  # the levels of this geometry made an earlier way: nobody reads them
        vm.drop_cache(folder, vm.cache_key(state.blobs, state.render, algo))
    if _gone(engine, state):
        vm.drop_cache(folder, key)
        ctx.log(f"«{state.name}»: каркас удалён во время задания — копии убраны.", "warn")
        return {"wireframe_id": wf_id, "wireframe_ids": [], "parts": made["parts"], "built": made["built"]}
    counts = " / ".join(_n(n) for n in made["triangles"])
    ctx.log(f"«{state.name}»: частей {_n(made['parts'])}, сделано {_n(made['built'])}; треугольников по уровням {counts}")
    with suppress(JobCancelled):  # the files are written: a cancel this late undoes nothing
        ctx.tick(1.0, "Готово")
    return {
        "wireframe_id": wf_id,
        "wireframe_ids": [wf_id],
        "key": key,
        "parts": made["parts"],
        "built": made["built"],
        "triangles": made["triangles"],
    }


wireframe_lod = handler(KIND, TITLE, queue="quick", estimate_memory=_memory)(_run)
wireframe_lod_heavy = handler(KIND_HEAVY, TITLE, queue="heavy", estimate_memory=_memory)(_run)
