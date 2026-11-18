"""Job `wireframe.check` — «Проверка и копии для показа» of wireframes — and the layer of their crossing
triangles for the 3D window (ТЗ 7.21; ADR 0013 §1, §3, §5).

What is measured when a wireframe is stored (`store.stage_wireframe` → `geo.meshes.mesh_stats`): area, volume
of a closed solid, closedness, orientation, open and non-manifold edges, degenerate triangles. The job adds,
for every wireframe of `wireframe_ids` in turn:

1. the levels of detail of every part into `cache\\mesh\\<key>\\` (`viz.meshes.build_cache`; a part whose
   levels are there, whole, is not made again — «Проверить» of a checked wireframe builds nothing);
2. the consistency of the normals and the self-intersections on the float64 blobs (`geo.checks.run_checks`);
3. the crossing triangles for the 3D window — the file `cache\\mesh\\<key>\\checks.bfa` — and the record
   `wireframes.stats.checks`: {state "done", normal_conflicts, pairs, triangles (how many cross), listed (how
   many of them are in the file: at most 200 000), points (at most 200, project coordinates), seconds,
   checked_at (UTC, ISO)}, or {state "failed", error {code, message, hint}, checked_at} for a mesh no check
   can work on.

The record is derived data, like the levels: `objects.version` does not change, so an edit in «Свойства» is
not refused because of a check. It is written in one short transaction per wireframe, only to the wireframe
the checks were made on — a wireframe deleted meanwhile (or whose id another one has taken) gets nothing, and
the folder of a geometry nobody has any more is removed. A cancel keeps the wireframes checked so far and
leaves nothing half made: whole levels of a geometry stay, they are its own. Wireframes of one geometry (the
same blobs) are checked once. The job fails only when none of its wireframes could be checked.

The layer — `GET /wireframes/{id}/checks`, BFA1 `checks` (`viz/binary.py`), for display only:

    header  {kind "checks", format, state, triangles, crossing, groups [{origin [x, y, z], first, n}], bbox}
    xyz     <f4 9 per triangle: its three corners from the origin of its group (whole metres, float64)
    tri     <u4 the number of the triangle in the wireframe as stored, ascending

`state`: "done" — the triangles that cross (`crossing` — how many cross in all, `triangles` — how many are
here); "none" — the wireframe was not checked; "failed" — it could not be; "missing" — it was checked and has
crossing triangles, but their file is not in the cache (a deleted cache, a project from a `.bfggis`): the job
makes it again. The file is the answer itself and depends on the geometry only, so the ETag is the key of the
geometry, the numbers of the record and whether the file is there — a repeated check with the same result is
a 304. A group is a run of triangles no wider than a part may be (16 km): float32 offsets from its origin are
within a quarter of a millimetre.

numpy is imported with this module, numba and pyfqmr with the first job: import it lazily in the API.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects, wireframes
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import handler

from . import store as ws

if TYPE_CHECKING:
    from bf_core.viz.meshes import MeshState

KIND = "wireframe.check"
CHECKS_FILE = "checks.bfa"
LAYER = "checks"  # the kind of the BFA1 answer
MAX_IDS = 2000  # wireframes of one job
MAX_GROUPS = 64  # groups of the layer: a draw call each
DEFECTS_LISTED = 200  # ids of wireframes with defects in the result of a job
STEP_LOD, STEP_CHECKS, STEP_SAVE = "копии для показа", "нормали и самопересечения", "запись результата"
LOD_SHARE = 0.7  # of the progress of a wireframe: the levels of detail take most of the time

Tick = Callable[[float, str], None]


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def checks_file(folder: Path, key: str) -> Path:
    from bf_core.viz import meshes as vm

    return vm.cache_dir(folder, key) / CHECKS_FILE


# --- the request -----------------------------------------------------------------------------------------


def _named(conn: sa.Connection, ids: list[int]) -> dict[int, tuple[str, int]]:
    """id → (name, triangles) of the wireframes of the project among `ids`."""
    out: dict[int, tuple[str, int]] = {}
    for start in range(0, len(ids), 500):
        rows = conn.execute(
            sa.select(objects.c.id, objects.c.name, wireframes.c.triangle_count)
            .join(wireframes, wireframes.c.object_id == objects.c.id)
            .where(
                objects.c.id.in_(ids[start : start + 500]), objects.c.kind == ws.WIREFRAME, objects.c.state == "ready"
            )
        ).all()
        out.update({int(r.id): (str(r.name), int(r.triangle_count)) for r in rows})
    return out


def request(conn: sa.Connection, wireframe_ids: Any) -> tuple[dict[str, Any], str]:
    """Checked parameters of the job and its title, for whoever starts it («Проверить», an import):
    {wireframe_ids, triangles — of the largest wireframe, for the estimate of memory}; ApiError otherwise."""
    ids = list(dict.fromkeys(int(i) for i in wireframe_ids or []))
    if not ids:
        raise ApiError(422, "no_wireframes", "Не выбран каркас для проверки.", "Выберите каркас в дереве проекта.")
    if len(ids) > MAX_IDS:
        raise ApiError(
            422,
            "too_many_wireframes",
            f"Каркасов для проверки больше {_n(MAX_IDS)}.",
            "Проверьте каркасы несколькими заданиями.",
        )
    found = _named(conn, ids)
    if len(found) != len(ids):
        raise ws.not_found()
    title = f"Проверка каркаса «{found[ids[0]][0]}»" if len(ids) == 1 else f"Проверка каркасов: {len(ids)}"
    return {"wireframe_ids": ids, "triangles": max(count for _name, count in found.values())}, title


def _memory(params: dict[str, Any]) -> int:
    """MB. One wireframe at a time: its arrays (about 24 bytes a triangle with its share of the vertices), the
    keys of the normals, then the boxes and cells of the crossings (`geo/checks.py`: about 50 bytes a triangle
    and the vertices once more), one part being simplified, the libraries. Measured, the peak of the process:
    1 million triangles — 310 MB (3.7 s; 0.7 s when the levels are there), 10 million — 1.2 GB (28 s; 7.5 s)."""
    return 300 + int(int(params.get("triangles") or 0) * 110 / 2**20)


# --- the layer of crossing triangles ---------------------------------------------------------------------


def _groups(lo: np.ndarray, hi: np.ndarray) -> list[tuple[int, int, tuple[float, ...]]]:
    """Runs of consecutive triangles (their boxes `lo`, `hi`: n×3) no wider than a part of a wireframe may be
    (`geo.meshes.MAX_SPAN_M`): [(first, count, box)]. The triangles come in the order of the store — along a
    Z-order curve — so a wireframe within 16 km is one run. At most `MAX_GROUPS` runs: the last one takes what
    is left (a mesh of triangles tens of kilometres wide is not drawn to the millimetre anyway)."""
    from bf_core.geo import meshes as gm

    out: list[tuple[int, int, tuple[float, ...]]] = []
    n = len(lo)
    first = 0
    while first < n:
        a, b = lo[first], hi[first]
        end = first + 1
        step = 64  # doubles while the run goes on: a run of one triangle costs 64 rows, a long one — its length
        while end < n and len(out) < MAX_GROUPS - 1:
            low = np.minimum(np.minimum.accumulate(lo[end : end + step], axis=0), a)
            high = np.maximum(np.maximum.accumulate(hi[end : end + step], axis=0), b)
            fits = int(np.searchsorted((high - low).max(axis=1), gm.MAX_SPAN_M, side="right"))  # spans only grow
            if fits:
                a, b = low[fits - 1], high[fits - 1]
                end += fits
            if fits < len(low):
                break
            step = min(step * 2, 1 << 16)
        if len(out) == MAX_GROUPS - 1:
            a, b, end = lo[first:].min(axis=0), hi[first:].max(axis=0), n
        out.append((first, end - first, (*(float(x) for x in a), *(float(x) for x in b))))
        first = end
    return out


def encode_layer(v: np.ndarray, t: np.ndarray, listed: np.ndarray, crossing: int) -> bytes:
    """BFA1 `checks` of the triangles `listed` (numbers in `t`, ascending) of a mesh as stored."""
    from bf_core.geo import meshes as gm
    from bf_core.viz import binary
    from bf_core.viz.keys import FORMAT

    which = np.asarray(listed, dtype=np.int64)
    corners = np.asarray(v, dtype=np.float64)[np.asarray(t)[which]]  # n × 3 corners × 3 coordinates
    xyz = np.empty(corners.shape, dtype="<f4")
    groups = []
    for first, count, box in _groups(corners.min(axis=1), corners.max(axis=1)):
        origin = gm.origin_of(box)
        xyz[first : first + count] = corners[first : first + count] - np.array(origin)  # float64, then float32
        groups.append({"origin": list(origin), "first": first, "n": count})
    flat = corners.reshape(-1, 3)
    header = {
        "kind": LAYER,
        "format": FORMAT,
        "state": "done",
        "triangles": int(len(which)),
        "crossing": int(crossing),
        "groups": groups,
        "bbox": [*flat.min(axis=0).tolist(), *flat.max(axis=0).tolist()] if len(which) else None,
    }
    return binary.encode(header, [("xyz", xyz, "<f4"), ("tri", which, "<u4")])


def _answer(state: str, crossing: int) -> bytes:
    from bf_core.viz import binary
    from bf_core.viz.keys import FORMAT

    header = {
        "kind": LAYER, "format": FORMAT, "state": state, "triangles": 0, "crossing": int(crossing), "groups": [],
        "bbox": None,
    }  # fmt: skip
    return binary.encode(header, [("xyz", np.zeros(0, dtype="<f4"), "<f4"), ("tri", np.zeros(0, dtype="<u4"), "<u4")])


def _read_file(path: Path, listed: int) -> bytes | None:
    """The file of the layer when it is whole and holds the triangles of the record; else None."""
    from bf_core.viz import binary

    try:
        data = path.read_bytes()
        header, arrays = binary.decode(data)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    ok = header.get("kind") == LAYER and header.get("state") == "done" and header.get("triangles") == listed
    return data if ok and len(arrays.get("xyz", ())) == 9 * listed else None


def _listed(checks: dict[str, Any] | None) -> int:
    return int(checks.get("listed") or 0) if checks and checks.get("state") == "done" else 0


def layer_state(conn: sa.Connection, wf_id: int) -> tuple[MeshState, dict[str, Any] | None]:
    """The geometry of a wireframe and its record of checks (None — it was not checked)."""
    from bf_core.viz import meshes as vm

    state = vm.mesh_state(conn, wf_id)
    stats = conn.execute(sa.select(wireframes.c.stats).where(wireframes.c.object_id == wf_id)).scalar() or {}
    checks = stats.get("checks")
    return state, checks if isinstance(checks, dict) else None


def layer_etag(project: Any, state: MeshState, checks: dict[str, Any] | None) -> str:
    from bf_core.viz.keys import etag

    numbers = None if checks is None else [checks.get(k) for k in ("state", "pairs", "triangles", "listed")]
    there = _listed(checks) > 0 and checks_file(project.folder, state.cache_key()).is_file()
    return etag(LAYER, project.uid, state, numbers, there)


def layer(project: Any, state: MeshState, checks: dict[str, Any] | None) -> bytes:
    """BFA1 `checks` of a wireframe (see the module docstring)."""
    if checks is None:
        return _answer("none", 0)
    if checks.get("state") != "done":
        return _answer("failed", 0)
    listed, crossing = _listed(checks), int(checks.get("triangles") or 0)
    if not listed:
        return _answer("done", crossing)
    data = _read_file(checks_file(project.folder, state.cache_key()), listed)
    return data if data is not None else _answer("missing", crossing)


# --- the job ---------------------------------------------------------------------------------------------


def _failed(exc: Any) -> dict[str, Any]:
    return {"state": "failed", "error": {"code": exc.code, "message": exc.message, "hint": exc.hint}}


def _arrays(store: Any, state: MeshState) -> tuple[np.ndarray, np.ndarray]:
    """The vertices and the triangles of a wireframe as stored, in memory."""
    xyz, tri = state.sha("xyz"), state.sha("tri")
    if xyz is None or tri is None or not store.exists(xyz) or not store.exists(tri):
        raise ws.broken()
    v, t = np.fromfile(store.path_of(xyz), dtype="<f8"), np.fromfile(store.path_of(tri), dtype="<i4")
    if len(v) != 3 * state.points or len(t) != 3 * state.triangles:
        raise ws.broken()
    return v.reshape(-1, 3), t.reshape(-1, 3)


def _save(engine: sa.Engine, folder: Path, state: MeshState, record: dict[str, Any]) -> bool:
    """One short write transaction: the record into `wireframes.stats.checks` of the wireframe the checks were
    made on. False — it is gone, or its id belongs to another wireframe now (SQLite gives the id of a deleted
    object to the next one): nothing is written, and the derived files of a geometry nobody has are removed."""
    from bf_core.viz import meshes as vm

    wf_id = state.wireframe_id
    with dialect.write_tx(engine) as conn:
        try:
            same = vm.mesh_state(conn, wf_id).key() == state.key()
        except ApiError:
            same = False
        if same:
            own = wireframes.c.object_id == wf_id
            stats = dict(conn.execute(sa.select(wireframes.c.stats).where(own)).scalar() or {})
            stats["checks"] = record
            conn.execute(sa.update(wireframes).where(own).values(stats=stats))
        orphan = not same and not ws.geometry_shared(conn, dict(state.blobs))
    if orphan:
        vm.drop_cache(folder, state.cache_key())
    return same


def _check_one(
    ctx: JobContext, store: Any, wf_id: int, tick: Tick, records: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any] | None, int]:
    """One wireframe: its levels of detail, its checks, the file of its crossing triangles and its record.
    Returns the record (None — the wireframe is gone, nothing was written) and the parts whose levels were
    made. `records` — by the key of the geometry: wireframes of one geometry are checked once. ApiError — the
    files of the geometry are not there."""
    from bf_core.geo import checks as gc
    from bf_core.geo import meshes as gm
    from bf_core.viz import meshes as vm

    folder = ctx.project_folder
    assert folder is not None
    try:
        with dialect.read_tx(ctx.engine()) as conn:
            state = vm.mesh_state(conn, wf_id)
    except ApiError as exc:
        if exc.status == 404:
            return None, 0
        raise
    key = state.cache_key()
    record = records.get(key)
    built = 0
    if record is None:
        tick(0.0, STEP_LOD)
        v, t = _arrays(store, state)
        table = (state.render or {}).get("parts")
        parts = (
            [gm.make_part(k, row[0], row[1], row[2:8]) for k, row in enumerate(table)]
            if table is not None
            else gm.parts_table(v, t)
        )
        lod = vm.build_cache(folder, key, v, t, parts, lambda f: tick(LOD_SHARE * f, STEP_LOD), missing_only=True)
        built = int(lod["built"])
        tick(LOD_SHARE, STEP_CHECKS)
        body = None
        try:
            found = gc.run_checks(v, t, lambda f: tick(LOD_SHARE + (0.98 - LOD_SHARE) * f, STEP_CHECKS))
        except gm.MeshError as exc:
            record = _failed(exc)
        else:
            record = dict(found.stats)
            if len(found.triangles):
                body = encode_layer(v, t, found.triangles, int(found.stats["triangles"]))
        del v, t
        tick(0.98, STEP_SAVE)  # the last place a cancel is heard: the file and the record go together
        if body is None:
            checks_file(folder, key).unlink(missing_ok=True)
        else:
            vm._write(checks_file(folder, key), body)
        records[key] = record
    else:
        tick(0.98, STEP_SAVE)
    record = {**record, "checked_at": dialect.utcnow().isoformat(timespec="milliseconds")}
    return (record if _save(ctx.engine(), folder, state, record) else None), built


@handler(KIND, "Проверка каркасов", queue="heavy", estimate_memory=_memory)
def wireframe_check(ctx: JobContext) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    if ctx.project_folder is None:
        raise RuntimeError("wireframe.check is a project job")
    store = BlobStore(ctx.project_folder)
    ids = list(dict.fromkeys(int(i) for i in ctx.params.get("wireframe_ids") or []))
    ctx.tick(0.0, "Проверка каркасов")
    with dialect.read_tx(ctx.engine()) as conn:
        known = _named(conn, ids)
    total = max(1, sum(count for _name, count in known.values()))
    out: dict[str, Any] = {
        "checked": 0, "failed": 0, "skipped": 0, "lod_parts": 0, "normal_conflicts": 0, "pairs": 0, "triangles": 0,
        "with_defects": [],
    }  # fmt: skip
    records: dict[str, dict[str, Any]] = {}
    error: dict[str, str] | None = None
    done = 0
    for wf_id in ids:
        if wf_id not in known:
            ctx.log(f"Каркас №{wf_id} не найден: удалён до начала проверки.", "warn")
            out["skipped"] += 1
            continue
        name, count = known[wf_id]
        start, share = done / total, count / total
        done += count

        def tick(f: float, step: str, name: str = name, start: float = start, share: float = share) -> None:
            ctx.tick(start + share * min(max(f, 0.0), 1.0), f"«{name}»: {step}")

        try:
            record, built = _check_one(ctx, store, wf_id, tick, records)
        except ApiError as exc:  # the files of its geometry are not there: the others are still checked
            record, built = _failed(exc), 0
        out["lod_parts"] += built
        if record is None:
            ctx.log(f"«{name}»: каркас удалён во время проверки — результат не записан.", "warn")
            out["skipped"] += 1
        elif record["state"] != "done":
            error = record["error"]
            ctx.log(f"«{name}»: проверка не выполнена. {error['message']} {error['hint']}", "error")
            out["failed"] += 1
        else:
            conflicts, pairs, crossing = int(record["normal_conflicts"]), int(record["pairs"]), int(record["triangles"])
            out["checked"] += 1
            out["normal_conflicts"] += conflicts
            out["pairs"] += pairs
            out["triangles"] += crossing
            if conflicts or pairs:
                if len(out["with_defects"]) < DEFECTS_LISTED:
                    out["with_defects"].append(wf_id)
                ctx.log(
                    f"«{name}»: конфликтов нормалей: {_n(conflicts)}; самопересечений — пар: {_n(pairs)}, "
                    f"треугольников: {_n(crossing)}.",
                    "warn",
                )
            else:
                ctx.log(f"«{name}»: замечаний нет — нормали согласованы, самопересечений нет ({_n(count)} треуг.).")
    if error is not None and not out["checked"]:
        raise JobError(error["code"], error["message"], error["hint"])
    with suppress(JobCancelled):  # the results are in the project: a cancel this late undoes nothing
        ctx.tick(1.0, "Готово")
    return out
