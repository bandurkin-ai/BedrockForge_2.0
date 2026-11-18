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

Levels. 0 — every triangle; 1 and 2 — about 1/8 and 1/64 of them (pyfqmr, `geo/meshes.py::levels`). The
vertices on the border of a part are the same float32 numbers at every level, so parts drawn at different
levels join without cracks; that is also why a level cannot go below what the border of its part needs
(`triangles` of the answer is what it holds, `simplified` — whether it is fewer than `source_triangles`).
Levels 1 and 2 are files `cache\\mesh\\<key>\\<k>-<lod>.bfa` (the answers themselves): built for the whole
wireframe by the job that creates it (`build_cache`), or for one part on its first request; the folder is
derived data and may be deleted. The key is the SHA-256 of the blobs, the part table and the limits above.

The ETag of a part is the SHA-256 of its key: project, wireframe, creation time, blobs, part, level and the
limits — not `objects.version`, so a new name, colour or DTM setting re-reads the manifest only.
numpy and pyfqmr load with this module and its first part: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
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
ALGO = 1  # the way levels are made: a change here makes new cache folders
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


def cache_key(blobs: tuple[tuple[str, str], ...], render: dict[str, Any] | None) -> str:
    """The name of the cache folder of a geometry: its blobs ((role, sha256), sorted), its part table and the
    limits the levels are made with."""
    table = json.dumps(render, sort_keys=True, separators=(",", ":"))
    limits = [gm.PART_TRIANGLES, gm.MAX_SPAN_M, gm.LOD_RATIO, gm.LOD_LEVELS, gm.LOD_MIN_TRIANGLES]
    text = json.dumps([FORMAT, ALGO, [list(b) for b in blobs], hashlib.sha256(table.encode()).hexdigest(), limits])
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


def encode_part(part: gm.MeshPart, lod: int, local: np.ndarray, tri: np.ndarray) -> bytes:
    """BFA1 `mesh` of a part at a level: `local` — float64 offsets from the origin of the part."""
    dtype = "<u2" if len(local) <= MAX_U2_VERTICES else "<u4"
    header = {
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
) -> dict[str, Any]:
    """The simplified levels of every part into `cache\\mesh\\<key>\\`. `progress` may raise to cancel: the
    files already written stay (they are whole and belong to this geometry). Returns the triangles of every
    level and the bytes written."""
    counts = [0] * gm.LOD_LEVELS
    size = 0
    for p in parts:
        bodies = part_bodies(v, t, p, SIMPLIFIED)
        counts[0] += p.count
        for lod, body in bodies.items():
            _write(_cache_file(folder, key, p.k, lod), body)
            counts[lod] += int(binary.decode(body)[0]["triangles"])
            size += len(body)
        if progress:
            progress((p.k + 1) / len(parts))
    return {"parts": len(parts), "triangles": counts, "bytes": size}


def drop_cache(folder: Path, key: str) -> None:
    shutil.rmtree(cache_dir(folder, key), ignore_errors=True)


# --- the wireframe of a project --------------------------------------------------------------------------

_lock = threading.Lock()
_building = threading.Lock()
_tables: OrderedDict[tuple[Any, ...], list[gm.MeshPart]] = OrderedDict()


def forget() -> None:
    with _lock:
        _tables.clear()


def _paths(project: Any, state: MeshState) -> tuple[Path, Path]:
    from bf_core.storage.blobs import BlobStore

    store = BlobStore(project.folder)
    xyz, tri = state.sha("xyz"), state.sha("tri")
    if xyz is None or tri is None or not store.exists(xyz) or not store.exists(tri):
        raise broken()
    return store.path_of(xyz), store.path_of(tri)


def parts_of(project: Any, state: MeshState) -> list[gm.MeshPart]:
    """The part table: `wireframes.render`, or cut from the triangles as stored when a writer left it empty."""
    table = (state.render or {}).get("parts")
    if table is not None:
        return [gm.make_part(k, row[0], row[1], row[2:8]) for k, row in enumerate(table)]
    key = (project.uid, *state.key())
    with _lock:
        hit = _tables.get(key)
    if hit is not None:
        return hit
    xyz, tri = _paths(project, state)
    v = np.fromfile(xyz, dtype="<f8").reshape(-1, 3)
    t = np.fromfile(tri, dtype="<i4").reshape(-1, 3)
    built = gm.parts_table(v, t)
    with _lock:
        _tables[key] = built
        while len(_tables) > TABLES_KEPT:
            _tables.popitem(last=False)
    return built


def _bodies(project: Any, state: MeshState, part: gm.MeshPart, lods: tuple[int, ...]) -> dict[int, bytes]:
    """Read what a part needs — its rows of the triangle file and the vertices they use — and encode it."""
    xyz, tri = _paths(project, state)
    t = np.fromfile(tri, dtype="<i4", count=3 * part.count, offset=12 * part.first).reshape(-1, 3)
    if len(t) != part.count:
        raise broken()
    v = np.memmap(xyz, dtype="<f8", mode="r").reshape(-1, 3)  # only the pages of this part are read
    try:
        if int(t.max()) >= len(v) or int(t.min()) < 0:
            raise broken()
        whole = gm.make_part(part.k, 0, part.count, part.bbox)  # `t` holds the rows of this part only
        return part_bodies(v, t, whole, lods)
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


def part(project: Any, state: MeshState, k: int, lod: int) -> bytes:
    if lod not in range(gm.LOD_LEVELS):
        raise bad_lod()
    parts = parts_of(project, state)
    if not 0 <= k < len(parts):
        raise part_not_found(k)
    p = parts[k]
    if lod == 0:
        return _bodies(project, state, p, (0,))[0]
    key = state.cache_key()
    path = _cache_file(project.folder, key, k, lod)
    hit = _read(path, k, lod)
    if hit is not None:
        return hit
    with _building:  # four parts asked at once: pyfqmr holds the interpreter anyway
        hit = _read(path, k, lod)
        if hit is not None:
            return hit
        bodies = _bodies(project, state, p, SIMPLIFIED)
        for level, body in bodies.items():
            _write(_cache_file(project.folder, key, k, level), body)
    return bodies[lod]
