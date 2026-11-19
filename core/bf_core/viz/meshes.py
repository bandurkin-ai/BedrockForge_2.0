"""Parts and levels of detail of a wireframe for 3D (ADR 0011 §3; ADR 0004 §2–3; ADR 0007 §1 «Части», «ETag»).

`GET /viz/wireframes/{id}` — the manifest, BFA1 `wireframe` (header only): the wireframe, its colour, the
settings of a DTM (`dtm` {raster_id, clip, tint, opacity, shade} or null), its extent, the levels and
`parts [{k, first, n, bbox, origin, etags[3]}]`. `GET /viz/wireframes/{id}/parts/{k}?lod=0|1|2` — BFA1 `mesh`:
`xyz f4×3` from the origin of the part and `idx` — three vertices of the part per triangle, `u2` when the part
has at most 65 536 vertices, else `u4`. No normals: the window shades flat. Data for display only; calculations
use the float64 blobs of the wireframe.

Parts. The triangles are stored along a Z-order curve (`wireframes/store.py`), a part is a run of at most
64 000 of them, none wider than 16 km (`geo/meshes.py::parts_table`); the table is `wireframes.render`. The
origin of a part is the centre of its extent rounded to a whole metre (float64), so the float32 offsets stay
below 8000.5 m: a step of 2⁻¹¹ m, an error ≤ 0.25 mm per axis.

Levels. 0 — every triangle; 1 and 2 — about 1/8 and 1/64 of them (`geo/meshes.py::levels`: flat regions
are thinned in their planes, `geo/flat.py`, the rest is decimated by pyfqmr). The vertices on the border of a
part are the same float32 numbers at every level, so parts drawn at different
levels join without cracks; that is also why a level cannot go below what the border of its part needs
(`triangles` of the answer is what it holds, `simplified` — whether it is fewer than `source_triangles`).
Levels 1 and 2 are files `cache\\mesh\\<key>\\<k>-<lod>.bfa` (the answers themselves), made only by jobs, never
in the process of the API (ADR 0015 §8): the job that creates a wireframe, «Проверить» (`wireframe.check`) and
`wireframe.lod` (`wireframes/lod_job.py`) call `build_cache`; the folder is derived data and may be deleted.
The key is the SHA-256 of the blobs, the part table, the limits above and `ALGO` — the way the levels are
made: levels cached by an earlier way are not used (the job removes their folder).

A stand-in. A part asked at level 1 or 2 whose file is not in the cache is answered whole — every triangle,
`simplified` false, `standin` true — and the router asks for `wireframe.lod` (no ETag, `no-store`: the window
asks again after the event `wireframes.lod`). A part of fewer than `geo.meshes.LOD_MIN_TRIANGLES` is drawn as
it is at every level: its answer is the level itself, with its ETag, and no job is asked for it.

The ETag of a part is the SHA-256 of its key: project, wireframe, creation time, blobs, part, level and the
limits — not `objects.version`, so a new name, colour or DTM setting re-reads the manifest only.
numpy loads with this module: import it lazily, never before BF-READY. pyfqmr and the numba kernels of the
levels load only with `build_cache` — in a worker.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import objects, wireframes
from bf_core.geo import meshes as gm

from . import binary
from .keys import FORMAT, _stamp, etag

WIREFRAME = "wireframe"
ALGO = 2  # the way levels are made: a change here makes new cache folders (2 — flat regions first, ADR 0013)
MAX_U2_VERTICES = 65_536
TABLES_KEPT = 8


def not_found() -> ApiError:
    return ApiError(404, "wireframe_not_found", "Каркас не найден.", "Обновите дерево проекта.")


def broken() -> ApiError:
    return ApiError(500, "wireframe_broken", "Файлы геометрии каркаса не найдены.", "Загрузите каркас заново.")


def part_not_found(k: int) -> ApiError:
    return ApiError(
        404, "part_not_found", f"Части {k} в каркасе нет.", "Каркас изменился: запросите список частей заново."
    )


def bad_lod() -> ApiError:
    return ApiError(
        422,
        "bad_lod",
        "Неизвестный уровень детализации каркаса.",
        "Укажите уровень 0 (полный), 1 или 2 (упрощённые).",
    )


# --- the state of a wireframe and its ETags --------------------------------------------------------------


def cache_key(blobs: tuple[tuple[str, str], ...], render: dict[str, Any] | None, algo: int = ALGO) -> str:
    """The name of the cache folder of a geometry: its blobs ((role, sha256), sorted), its part table, the
    limits the levels are made with and the way they are made (`algo`: an earlier one names a stale folder)."""
    table = json.dumps(render, sort_keys=True, separators=(",", ":"))
    limits = [gm.PART_TRIANGLES, gm.MAX_SPAN_M, gm.LOD_RATIO, gm.LOD_LEVELS, gm.LOD_MIN_TRIANGLES]
    text = json.dumps([FORMAT, algo, [list(b) for b in blobs], hashlib.sha256(table.encode()).hexdigest(), limits])
    return hashlib.sha256(text.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class MeshState:
    wireframe_id: int
    name: str
    version: int
    color: str | None
    dtm: dict[str, Any] | None
    points: int
    triangles: int
    extent: list[float] | None
    origin: list[float]
    render: dict[str, Any] | None
    blobs: tuple[tuple[str, str], ...]  # (role, sha256), sorted
    stamp: str  # creation time: SQLite gives the id of a deleted wireframe to the next one

    def key(self) -> list[Any]:
        """The geometry only: the version of the object is not a part of it."""
        return [self.wireframe_id, self.stamp, self.cache_key()]

    def sha(self, role: str) -> str | None:
        return dict(self.blobs).get(role)

    def cache_key(self) -> str:
        return cache_key(self.blobs, self.render)


def mesh_state(conn: sa.Connection, wf_id: int) -> MeshState:
    from bf_core.wireframes import store

    row = conn.execute(
        sa.select(
            objects.c.name,
            objects.c.version,
            objects.c.meta,
            objects.c.extent,
            objects.c.created_at,
            wireframes.c.point_count,
            wireframes.c.triangle_count,
            wireframes.c.origin,
            wireframes.c.render,
            wireframes.c.dtm,
        )
        .join(wireframes, wireframes.c.object_id == objects.c.id)
        .where(objects.c.id == wf_id, objects.c.kind == WIREFRAME, objects.c.state == "ready")
    ).first()
    if row is None:
        raise not_found()
    dtm = store.dtm_public(row.dtm, store.drapes(conn, [wf_id]).get(wf_id) if row.dtm is not None else None)
    shas = store.blob_shas(conn, wf_id)
    if set(shas) != set(store.ARRAYS):
        raise broken()
    return MeshState(
        wf_id,
        row.name,
        int(row.version),
        (row.meta or {}).get("color"),
        dtm,
        int(row.point_count),
        int(row.triangle_count),
        row.extent,
        list(row.origin or [0.0, 0.0, 0.0]),
        row.render,
        tuple(sorted(shas.items())),
        _stamp(row.created_at),
    )


def manifest_etag(uid: str, state: MeshState) -> str:
    return etag(WIREFRAME, uid, state, state.version, state.dtm)


def part_etag(uid: str, state: MeshState, k: int, lod: int) -> str:
    return etag("mesh", uid, state, k, lod)


# --- one part --------------------------------------------------------------------------------------------


def encode_part(
    part: gm.MeshPart, lod: int, local: np.ndarray, tri: np.ndarray, *, standin: bool = False
) -> bytes:
    """BFA1 `mesh` of a part at a level: `local` — float64 offsets from the origin of the part; `standin` — the
    whole part answers for a level not made yet (the key is in the header of a stand-in only)."""
    dtype = "<u2" if len(local) <= MAX_U2_VERTICES else "<u4"
    header: dict[str, Any] = {
        "kind": "mesh",
        "format": FORMAT,
        "k": part.k,
        "lod": lod,
        "ratio": gm.LOD_RATIO**lod,
        "origin": list(part.origin),
        "bbox": list(part.bbox),
        "triangles": len(tri),
        "vertices": len(local),
        "source_triangles": part.count,
        "simplified": len(tri) < part.count,
        "index": dtype,
    }
    if standin:
        header["standin"] = True
    return binary.encode(header, [("xyz", local.astype("<f4"), "<f4"), ("idx", tri, dtype)])


def part_bodies(v: np.ndarray, t: np.ndarray, part: gm.MeshPart, lods: tuple[int, ...]) -> dict[int, bytes]:
    """The answers of a part at the levels asked for; the simplified ones are made together (2 from 1)."""
    local, tri = gm.part_mesh(v, t, part)
    if max(lods) == 0:
        return {0: encode_part(part, 0, local, tri)}
    made = gm.levels(local, tri)
    return {lod: encode_part(part, lod, *made[lod]) for lod in lods}


# --- the cache of the simplified levels ------------------------------------------------------------------

SIMPLIFIED = tuple(range(1, gm.LOD_LEVELS))


def cache_dir(folder: Path, key: str) -> Path:
    return Path(folder) / "cache" / "mesh" / key


def _cache_file(folder: Path, key: str, k: int, lod: int) -> Path:
    return cache_dir(folder, key) / f"{k}-{lod}.bfa"


def _write(path: Path, data: bytes) -> None:
    """A cache file appears under its name whole or not at all; a folder that cannot be written is not an
    error (a project opened read-only): the part is simply made again next time."""
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def _read(path: Path, k: int, lod: int) -> bytes | None:
    try:
        data = path.read_bytes()
        header, arrays = binary.decode(data)
    except (OSError, ValueError, KeyError, TypeError):
        return None  # no file, or a damaged one: it is made again
    ok = header.get("kind") == "mesh" and header.get("k") == k and header.get("lod") == lod and "idx" in arrays
    return data if ok else None


def build_cache(
    folder: Path,
    key: str,
    v: np.ndarray,
    t: np.ndarray,
    parts: list[gm.MeshPart],
    progress: Callable[[float], None] | None = None,
    *,
    missing_only: bool = False,
) -> dict[str, Any]:
    """The simplified levels of every part into `cache\\mesh\\<key>\\`. `progress` may raise to cancel: the
    files already written stay (they are whole and belong to this geometry). With `missing_only` a part whose
    levels are in the folder, whole, is not made again («Проверить», ADR 0013 §5). Returns the triangles of
    every level, the bytes of the levels and `built` — the parts that were made."""
    counts = [0] * gm.LOD_LEVELS
    size = built = 0
    for p in parts:
        found = {lod: _read(_cache_file(folder, key, p.k, lod), p.k, lod) for lod in SIMPLIFIED if missing_only}
        ready = {lod: body for lod, body in found.items() if body is not None}
        bodies = ready if len(ready) == len(SIMPLIFIED) else part_bodies(v, t, p, SIMPLIFIED)
        built += bodies is not ready
        counts[0] += p.count
        for lod, body in bodies.items():
            if bodies is not ready:
                _write(_cache_file(folder, key, p.k, lod), body)
            counts[lod] += int(binary.decode(body)[0]["triangles"])
            size += len(body)
        if progress:
            progress((p.k + 1) / len(parts))
    return {"parts": len(parts), "triangles": counts, "bytes": size, "built": built}


def drop_cache(folder: Path, key: str) -> None:
    shutil.rmtree(cache_dir(folder, key), ignore_errors=True)


# --- the wireframe of a project --------------------------------------------------------------------------

_lock = threading.Lock()
_tables: OrderedDict[tuple[Any, ...], list[gm.MeshPart]] = OrderedDict()


def forget() -> None:
    with _lock:
        _tables.clear()


def _paths(store: Any, state: MeshState) -> tuple[Path, Path]:
    xyz, tri = state.sha("xyz"), state.sha("tri")
    if xyz is None or tri is None or not store.exists(xyz) or not store.exists(tri):
        raise broken()
    return store.path_of(xyz), store.path_of(tri)


@contextmanager
def mapped(store: Any, state: MeshState) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """The vertices (float64 n×3) and the triangles (int32 m×3) of a wireframe as stored, mapped from its blobs:
    only the pages a caller touches are read. The caller drops its own references to them before the block
    ends (Windows keeps a mapped file locked)."""
    xyz, tri = _paths(store, state)
    v = np.memmap(xyz, dtype="<f8", mode="r")
    t = np.memmap(tri, dtype="<i4", mode="r")
    try:
        if len(v) != 3 * state.points or len(t) != 3 * state.triangles:
            raise broken()
        yield v.reshape(-1, 3), t.reshape(-1, 3)
    finally:
        del v, t


def _stored_table(state: MeshState) -> list[gm.MeshPart] | None:
    table = (state.render or {}).get("parts")
    return None if table is None else [gm.make_part(k, row[0], row[1], row[2:8]) for k, row in enumerate(table)]


def table_of(state: MeshState, v: np.ndarray, t: np.ndarray) -> list[gm.MeshPart]:
    """The part table: `wireframes.render`, or cut from the triangles as stored when a writer left it empty."""
    stored = _stored_table(state)
    return stored if stored is not None else gm.parts_table(v, t)


def parts_of(project: Any, state: MeshState) -> list[gm.MeshPart]:
    """The part table of a wireframe of the project (cut from the blobs once and kept: `TABLES_KEPT`)."""
    from bf_core.storage.blobs import BlobStore

    stored = _stored_table(state)
    if stored is not None:
        return stored
    key = (project.uid, *state.key())
    with _lock:
        hit = _tables.get(key)
    if hit is not None:
        return hit
    with mapped(BlobStore(project.folder), state) as (v, t):
        built = table_of(state, v, t)
        del v, t
    with _lock:
        _tables[key] = built
        while len(_tables) > TABLES_KEPT:
            _tables.popitem(last=False)
    return built


def _whole(project: Any, state: MeshState, part: gm.MeshPart, lod: int, *, standin: bool = False) -> bytes:
    """Read what a part needs — its rows of the triangle file and the vertices they use — and encode it whole
    at the level `lod` (`standin` — for a level not made yet)."""
    from bf_core.storage.blobs import BlobStore

    xyz, tri = _paths(BlobStore(project.folder), state)
    t = np.fromfile(tri, dtype="<i4", count=3 * part.count, offset=12 * part.first).reshape(-1, 3)
    if len(t) != part.count:
        raise broken()
    v = np.memmap(xyz, dtype="<f8", mode="r").reshape(-1, 3)  # only the pages of this part are read
    try:
        if int(t.max()) >= len(v) or int(t.min()) < 0:
            raise broken()
        local, local_t = gm.part_mesh(v, t, gm.make_part(part.k, 0, part.count, part.bbox))  # `t`: this part only
        return encode_part(part, lod, local, local_t, standin=standin)
    finally:
        del v  # the mapping closes with its last reference (Windows keeps a mapped file locked)


def manifest(project: Any, state: MeshState) -> bytes:
    parts = parts_of(project, state)
    header = {
        "kind": WIREFRAME,
        "format": FORMAT,
        "wireframe": {"id": state.wireframe_id, "name": state.name},
        "version": state.version,
        "color": state.color,
        "dtm": state.dtm,
        "points": state.points,
        "triangles": state.triangles,
        "bbox": state.extent,
        "origin": state.origin,
        "part_triangles": gm.PART_TRIANGLES,
        "lods": [{"lod": i, "ratio": gm.LOD_RATIO**i} for i in range(gm.LOD_LEVELS)],
        "parts": [
            {
                "k": p.k,
                "first": p.first,
                "n": p.count,
                "bbox": list(p.bbox),
                "origin": list(p.origin),
                "etags": [part_etag(project.uid, state, p.k, lod) for lod in range(gm.LOD_LEVELS)],
            }
            for p in parts
        ],
    }
    return binary.encode(header, [])


def part_answer(project: Any, state: MeshState, k: int, lod: int) -> tuple[bytes, bool]:
    """BFA1 `mesh` of part k at a level and whether it is a stand-in (see the module docstring): level 0 from
    the blobs, levels 1–2 from the cache; never simplified here."""
    if lod not in range(gm.LOD_LEVELS):
        raise bad_lod()
    parts = parts_of(project, state)
    if not 0 <= k < len(parts):
        raise part_not_found(k)
    p = parts[k]
    if lod == 0:
        return _whole(project, state, p, 0), False
    hit = _read(_cache_file(project.folder, state.cache_key(), k, lod), k, lod)
    if hit is not None:
        return hit, False
    if p.count < gm.LOD_MIN_TRIANGLES:  # `geo.meshes.levels` gives it back as it is: the answer is the level
        return _whole(project, state, p, lod), False
    return _whole(project, state, p, lod, standin=True), True


def part(project: Any, state: MeshState, k: int, lod: int) -> bytes:
    return part_answer(project, state, k, lod)[0]
