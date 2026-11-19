"""Job `sections.cache` — the sections of the wireframes on every section of a series, into files (ADR 0016 §6,
решение 9; ТЗ 8.1.5 «кеш 100 разрезов в фоне ≤ 60 с»).

`POST /section-series/{id}/cache {wireframe_ids, around}` asks for it (`request`):

* the sections — all of them when the series has at most `ALL_UP_TO` (the segments of a polyline), else
  `AROUND` on both sides of `around`, the section of the window;
* the wireframes — those of `wireframe_ids` that have triangles and whose file does not hold these sections
  yet; none — no job (`ready: true`);
* one job a series: a queued or running one with the same series key, sections and wireframes is the answer;
  any other of the series is cancelled — the new one replaces it (a cancel leaves no file);
* `sections.cache` goes to the queue `quick` up to `QUICK_TRIANGLES` triangles in all, `sections.cache.heavy`
  (the same handler) to `heavy` above: a kind has one queue (`jobs/registry.py`).

The job maps the blobs of a wireframe (only the pages of the parts a plane touches are read), cuts it by every
plane (`wf_sections.cut` — the call of the requests, so a file holds the very lines a request would cut) and
writes its file under a temporary name; all of them go in place at the end (`cache_files.Staged`), then the
folders over 2 GB a project go (`cache_files.trim`). A tick between two planes: a cancel within ~0.1 s of
10 million triangles. The window learns of the end by the event `job` (its `result`: series_id, key, first,
count, wireframe_ids) and reads the sections again (the ETag of an answer does not depend on where it came from).

`cache_state` — `GET …/cache`: the key, how many sections are ready for the wireframes asked, the job.

numpy loads with this module, numba with the first cut: the router imports it lazily.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import jobs
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import handler

from . import cache_files
from . import store as series_store
from . import wf_sections as wf
from .geometry import SeriesError, series_geometry

if TYPE_CHECKING:
    from bf_core.jobs.scheduler import Scheduler

KIND = "sections.cache"
KIND_HEAVY = "sections.cache.heavy"
KINDS = (KIND, KIND_HEAVY)
QUICK_TRIANGLES = 2_000_000
ALL_UP_TO = 500  # sections: all of them up to this many
AROUND = 50  # … else this many on both sides of the section of the window
TITLE = "Сечения каркасов серии"
STEP = "сечения каркасов"

_lock = threading.Lock()


def window(total: int, around: int | None) -> tuple[int, int]:
    """(first, count) of the sections to cache of `total` (ADR 0016 решение 9)."""
    if total <= ALL_UP_TO:
        return 0, total
    at = min(max(int(around or 0), 0), total - 1)
    first = max(0, at - AROUND)
    return first, min(total, at + AROUND + 1) - first


def _active(conn: sa.Connection, series_id: int) -> list[Any]:
    """The jobs of the series that are queued or running and not being cancelled, oldest first."""
    rows = conn.execute(
        sa.select(jobs.c.id, jobs.c.kind, jobs.c.status, jobs.c.progress, jobs.c.params)
        .where(jobs.c.kind.in_(KINDS), jobs.c.status.in_(("queued", "running")), ~jobs.c.cancel_requested)
        .order_by(jobs.c.id)
    ).all()
    return [r for r in rows if isinstance(r.params, Mapping) and r.params.get("series_id") == series_id]


def _same(params: Any, want: Mapping[str, Any]) -> bool:
    keys = ("key", "first", "count")
    return all(params.get(k) == want[k] for k in keys) and sorted(params.get("wireframe_ids") or []) == sorted(
        want["wireframe_ids"]
    )


def _geometry(meta: Any) -> Any:
    try:
        return series_geometry(meta if isinstance(meta, Mapping) else {})
    except SeriesError as e:
        raise ApiError(422, e.code, e.message, e.hint or "Проверьте параметры серии.") from e


def request(
    sched: Scheduler, project: Any, series_id: int, wireframe_ids: Sequence[int], around: int | None
) -> dict[str, Any]:
    """`POST …/cache`: {source, id (null — nothing to do), ready, key, axis, first, count, total, missing}.
    `series_store.NotFound` — no such series; ApiError 422 — parameters the geometry cannot take."""
    ids = wf.check_ids(wireframe_ids)
    uid = str(project.uid)
    with _lock:
        with dialect.read_tx(project.engine) as conn:
            r = series_store.row(conn, series_id)
            found, _empty, missing = wf.states(conn, ids)
            running = _active(conn, series_id)
        geom = _geometry(r.meta)
        key, total = wf.series_key(geom), wf.slot_count(geom)
        first, count = window(total, around)
        todo = [s for s in found if not cache_files.covers(project.folder, key, s.cache_key(), first, count)]
        params = {
            "series_id": int(series_id),
            "wireframe_ids": [s.wireframe_id for s in todo],
            "key": key,
            "first": first,
            "count": count,
            "triangles": sum(s.triangles for s in todo),
        }
        out = {"source": uid, "key": key, "axis": wf.slot_axis(geom), "first": first, "count": count}
        out |= {"total": total, "missing": missing}
        same = [j for j in running if todo and _same(j.params, params)]
        if same:
            return {**out, "id": int(same[0].id), "ready": False}
        for j in running:  # the new one replaces it
            with suppress(LookupError):
                sched.cancel(uid, int(j.id))
        if not todo:
            return {**out, "id": None, "ready": True}
        kind = KIND if params["triangles"] <= QUICK_TRIANGLES else KIND_HEAVY
        try:
            _src, job_id = sched.submit(kind, params, title=f"{TITLE}: «{r.name}»", source=uid)
        except LookupError as exc:  # the project is not the one the scheduler holds (closed meanwhile)
            raise ApiError(409, "no_project", "Проект не открыт.", "Откройте проект и повторите.") from exc
        return {**out, "id": job_id, "ready": False}


def cache_state(project: Any, series_id: int, ids: Sequence[int] | None) -> dict[str, Any]:
    """`GET …/cache`: {key, axis, total, ready — sections every wireframe of `ids` with triangles has in its file
    (null without `ids`), first, wireframes [{id, first, count}], missing, bytes, job — the queued or running one
    {source, id, kind, status, progress} or null}."""
    with dialect.read_tx(project.engine) as conn:
        r = series_store.row(conn, series_id)
        found, _empty, missing = wf.states(conn, ids or [])
        running = _active(conn, series_id)
    geom = _geometry(r.meta)
    key, total = wf.series_key(geom), wf.slot_count(geom)
    per = []
    lo, hi = 0, total
    for st in found:
        cov = cache_files.coverage(project.folder, key, st.cache_key())
        first, count = cov if cov is not None else (None, 0)
        per.append({"id": st.wireframe_id, "first": first, "count": count})
        lo, hi = (max(lo, first), min(hi, first + count)) if first is not None else (0, 0)
    job = running[-1] if running else None
    return {
        "key": key,
        "axis": wf.slot_axis(geom),
        "total": total,
        "ready": None if ids is None else max(0, hi - lo),
        "first": None if ids is None or hi <= lo else lo,
        "wireframes": per,
        "missing": missing,
        "bytes": cache_files.size(project.folder, key),
        "job": None
        if job is None
        else {
            "source": str(project.uid),
            "id": int(job.id),
            "kind": job.kind,
            "status": job.status,
            "progress": float(job.progress or 0.0),
        },
    }


# --- the job -----------------------------------------------------------------------------------------------


def _memory(params: dict[str, Any]) -> int:
    """MB: the blobs are mapped; the edges of the parts one plane touches (~32 bytes a triangle, at most a
    million at a time) and the lines of one wireframe on all its sections."""
    return 384


def _gone(engine: sa.Engine, state: Any) -> bool:
    """The wireframe was deleted (or its id is another geometry's) since the job read it."""
    from bf_core.viz import meshes as vm

    with dialect.read_tx(engine) as conn:
        try:
            return bool(vm.mesh_state(conn, state.wireframe_id).key() != state.key())
        except ApiError:
            return True


def _cuts(blobs: Any, state: Any, slots: Sequence[wf.Slot], step: Callable[[], None]) -> list[Any]:
    """The lines of one wireframe on every plane, from its mapped blobs and its part table — the arrays
    `wireframes/exact.py` keeps, so the numbers of a request."""
    from bf_core.viz import meshes as vm

    with vm.mapped(blobs, state) as (v, t):
        table = [[p.first, p.count, *p.bbox] for p in vm.table_of(state, v, t)]
        parts = np.array(table, dtype=np.float64).reshape(-1, 8)
        out = []
        try:
            for s in slots:
                step()
                out.append(wf.cut(v, t, parts, s))
        except ValueError as exc:  # a triangle of a vertex that is not there
            raise vm.broken() from exc
        finally:
            del v, t
    return out


def _run(ctx: JobContext) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    folder = ctx.project_folder
    if folder is None:
        raise RuntimeError("sections.cache is a project job")
    series_id = int(ctx.params["series_id"])
    ids = [int(i) for i in ctx.params.get("wireframe_ids") or []]
    ctx.tick(0.0, STEP)
    engine = ctx.engine()
    with dialect.read_tx(engine) as conn:
        try:
            r = series_store.row(conn, series_id)
        except series_store.NotFound:
            ctx.log(f"Серия №{series_id} удалена до начала задания: сечения не нужны.", "warn")
            return {"series_id": series_id, "key": None, "wireframe_ids": [], "files": 0, "bytes": 0}
        found, _empty, missing = wf.states(conn, ids)
    try:
        geom = series_geometry(r.meta if isinstance(r.meta, Mapping) else {})
    except SeriesError as e:
        raise JobError(e.code, e.message, e.hint, retry=False) from e
    key, total, axis = wf.series_key(geom), wf.slot_count(geom), wf.slot_axis(geom)
    first = min(max(int(ctx.params.get("first") or 0), 0), total)
    count = min(max(int(ctx.params.get("count") or total), 0), total - first)
    slots = [wf.slot(geom, i) for i in range(first, first + count)]
    geometries = {s.cache_key(): s for s in reversed(found)}  # wireframes of one geometry share their file
    work = max(1, sum(s.triangles for s in geometries.values()) * len(slots))
    done = [0]
    staged = cache_files.Staged(folder, key, ctx.id)
    written: list[int] = []
    blobs = BlobStore(folder)
    try:
        for st in found:
            if st.cache_key() in staged.keys:
                written.append(st.wireframe_id)
                continue

            def step(st: Any = st) -> None:
                ctx.tick(0.98 * done[0] / work, STEP)
                done[0] += st.triangles

            try:
                cuts = _cuts(blobs, st, slots, step)
            except (OSError, ApiError):
                if not _gone(engine, st):
                    raise
                ctx.log(f"«{st.name}»: каркас удалён во время задания — пропущен.", "warn")
                missing.append(st.wireframe_id)
                continue
            staged.add(st.cache_key(), cache_files.encode_file(key, st.cache_key(), axis, first, total, cuts))
            written.append(st.wireframe_id)
            del cuts
        ctx.tick(0.99, STEP)  # the last chance to cancel: nothing is in place yet
        staged.commit()
    except BaseException:
        staged.discard()
        raise
    removed = cache_files.trim(folder, keep={key})
    ctx.log(f"«{r.name}»: сечения {len(written)} каркасов на {count} разрезах, {staged.bytes / 1e6:.1f} МБ")
    with suppress(JobCancelled):  # the files are in place: a cancel this late undoes nothing
        ctx.tick(1.0, "Готово")
    return {
        "series_id": series_id,
        "key": key,
        "axis": axis,
        "first": first,
        "count": count,
        "total": total,
        "wireframe_ids": written,
        "missing": sorted(missing),
        "files": len(staged.keys),
        "bytes": staged.bytes,
        "removed": len(removed),
    }


sections_cache = handler(KIND, TITLE, queue="quick", estimate_memory=_memory)(_run)
sections_cache_heavy = handler(KIND_HEAVY, TITLE, queue="heavy", estimate_memory=_memory)(_run)
