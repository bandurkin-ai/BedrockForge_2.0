"""Exact queries on the wireframes of a project for the 3D window (ADR 0013 §5, ADR 0007 §5; ТЗ 7.14, 7.15,
7.21): the triangle under a click, the lines a wireframe is shown by when the view is clipped by a section, and
the marks where the holes of a set cross wireframes. All of it in float64 project coordinates, from the blobs of
the wireframe as they are — the levels of detail of the 3D window are never used here.

    pick(project, origin, direction, wireframe_ids, clip)   `POST /wireframes/pick`
    parse_plane(…), section_etag(…), section(project, state, plane)
                                                            `GET /viz/wireframes/{id}/section` (BFA1 `section`)
    parse_ids(…), set_targets(…), intersections_etag(…), intersections(project, state, method, targets)
                                                            `GET /viz/sets/{id}/intersections` (BFA1 `intersections`)

The geometry of a wireframe never changes, so its arrays and what is derived from them are kept by the hash of
the geometry (`MeshState.cache_key`: the blobs and the part table): the vertices and the triangles in memory —
not mapped, a mapped blob could not be deleted on Windows — the finer part table of the pick
(`geo.pick.fine_parts`: a click on 10 million triangles in under 2 ms) and the tree of boxes of the crossings
(`geo.intersect.MeshTarget`). The cache holds at most `CACHE_BYTES`, the least recently used wireframe goes
first, the last one asked for always stays; a deleted wireframe leaves it at once (`drop`), and so does
everything of another project when the project is changed. The marks of a set are kept too, by its
trajectories and the geometries: a new name or colour of a wireframe only writes the answer again. They are
found a wireframe at a time, so the marks of every wireframe of a project need the memory of the largest one
and of what the cache keeps, not of all of them.

numpy and numba are imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import copy
import math
import threading
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, objects, wireframes
from bf_core.geo import intersect as gi
from bf_core.geo import pick as gp
from bf_core.geo import section as gs
from bf_core.geo.tri_kernels import BOX_PAD_M, half_spaces
from bf_core.viz import binary
from bf_core.viz import meshes as vm
from bf_core.viz.keys import FORMAT, SetState, _stamp, etag
from bf_core.wireframes import files

CACHE_BYTES = 768 << 20  # of vertices, triangles and trees kept between requests
FINE_FROM = 8192  # triangles: a smaller wireframe is scanned by the part table of the store
MAX_IDS = 2000  # wireframes in one request
MARKS_KEPT = 8  # answers of `intersections` kept (their crossings, not their bytes)


# --- errors ------------------------------------------------------------------------------------------------


def bad_ray() -> ApiError:
    return ApiError(
        422,
        "bad_ray",
        "Луч выбора задан неверно: нужны три числа начала и ненулевое направление.",
        "Щёлкните по каркасу ещё раз; если ошибка повторяется — отправьте журнал разработчику.",
    )


def bad_clip() -> ApiError:
    return ApiError(
        422,
        "bad_clip",
        "Плоскости окна разреза заданы неверно: у каждой четыре числа и ненулевая нормаль.",
        "Проведите линию разреза заново.",
    )


def bad_plane(what: str) -> ApiError:
    return ApiError(
        422,
        "bad_plane",
        f"Плоскость сечения задана неверно: {what}.",
        "Укажите точку и нормаль (point=x,y,z и normal=x,y,z) или линию разреза (x0, y0, az и, если нужно, "
        "length и offset).",
    )


def bad_wireframes() -> ApiError:
    return ApiError(
        422,
        "bad_wireframes",
        f"Список каркасов задан неверно: нужны номера через запятую, не больше {MAX_IDS}.",
        "Обновите дерево проекта и повторите.",
    )


# --- the geometry of a wireframe, kept by its hash ---------------------------------------------------------


@dataclass
class Geometry:
    """The arrays of a wireframe as the kernels take them and what is derived from them."""

    key: str
    uid: str  # the project it was read from
    xyz: np.ndarray  # float64 n×3, project coordinates
    tri: np.ndarray  # int32 m×3, in the order of the store (spatial)
    parts: np.ndarray  # float64 p×8: the part table of the store
    nbytes: int
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    fine: np.ndarray | None = None  # the finer part table of the pick
    tree: gi.MeshTarget | None = None  # the tree of boxes of the crossings (its `solid` is set by the request)

    def pick_parts(self) -> np.ndarray:
        if len(self.tri) < FINE_FROM:
            return self.parts
        with self.lock:
            if self.fine is None:
                self.fine = gp.fine_parts(self.xyz, self.tri, self.parts)
                _grown(self, self.fine.nbytes)
            return self.fine

    def target(self, stats: Any) -> gi.MeshTarget:
        """The wireframe ready for many holes: ENTER and LEAVE only for a closed solid with one orientation."""
        with self.lock:
            if self.tree is None:
                self.tree = gi.MeshTarget(self.xyz, self.tri, ordered=True)
                _grown(self, self.tree.box.nbytes, trees=1)
            out = copy.copy(self.tree)  # the arrays are shared
        out.solid = _solid(stats)
        return out


_lock = threading.Lock()
_cache: OrderedDict[str, Geometry] = OrderedDict()
_gates: dict[str, threading.Lock] = {}
_counts = {"loads": 0, "trees": 0}
_marks: OrderedDict[tuple[Any, ...], gi.Crossings] = OrderedDict()


def forget() -> None:
    with _lock:
        _cache.clear()
        _gates.clear()
        _marks.clear()
        _counts.update(loads=0, trees=0)


def drop(key: str) -> None:
    """The geometry `key` (`viz.meshes.cache_key`) is nobody's any more: a deleted wireframe leaves the cache
    with the marks found on it."""
    with _lock:
        _cache.pop(key, None)
        for marks_key in [k for k in _marks if any(mesh == key for mesh, _solid in k[1])]:
            del _marks[marks_key]


def cache_info() -> dict[str, int]:
    """What the cache holds: wireframes, their bytes, how many were read from the blobs and how many trees of
    boxes were built since `forget()`."""
    with _lock:
        return {"entries": len(_cache), "bytes": sum(g.nbytes for g in _cache.values()), **_counts}


def _trim() -> None:
    """Under `_lock`: the least recently used wireframes go until the rest fits; the last one stays."""
    total = sum(g.nbytes for g in _cache.values())
    while total > CACHE_BYTES and len(_cache) > 1:
        _key, gone = _cache.popitem(last=False)
        total -= gone.nbytes


def _grown(g: Geometry, nbytes: int, trees: int = 0) -> None:
    """A wireframe got its finer part table or its tree: the account of the cache follows."""
    with _lock:
        g.nbytes += int(nbytes)
        _counts["trees"] += trees
        _trim()


def _solid(stats: Any) -> str | None:
    """`outward` or `inward` for a closed solid with one orientation (`wireframes.stats`), else None."""
    orientation = (stats or {}).get("orientation") if (stats or {}).get("closed") else None
    return orientation if orientation in ("outward", "inward") else None


def _load(project: Any, state: vm.MeshState, key: str) -> Geometry:
    from bf_core.storage.blobs import BlobStore

    found = vm.files_of(BlobStore(project.folder), state)  # its own blobs or its entry of a pack
    xyz, tri = found.array("xyz"), found.array("tri")
    if xyz is None or tri is None or len(xyz) != 3 * state.points or len(tri) != 3 * state.triangles:
        raise vm.broken()
    xyz, tri = xyz.reshape(-1, 3), tri.reshape(-1, 3)
    table = [[p.first, p.count, *p.bbox] for p in vm.parts_of(project, state)]
    parts = np.array(table, dtype=np.float64).reshape(-1, 8)
    return Geometry(key, project.uid, xyz, tri, parts, xyz.nbytes + tri.nbytes + parts.nbytes)


def geometry(project: Any, state: vm.MeshState) -> Geometry:
    """The arrays of a wireframe: from the cache, or read from its blobs (once, whoever asks at the same time)."""
    key = state.cache_key()
    with _lock:
        for other in [k for k, g in _cache.items() if g.uid != project.uid]:
            del _cache[other]  # the project was changed: one project is open at a time
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            _trim()
            return hit
        gate = _gates.setdefault(key, threading.Lock())
    try:
        with gate:
            with _lock:
                hit = _cache.get(key)
            if hit is None:
                hit = _load(project, state, key)
                with _lock:
                    _cache[key] = hit
                    _counts["loads"] += 1
    finally:
        with _lock:
            _gates.pop(key, None)
            if key in _cache:
                _cache.move_to_end(key)
            _trim()
    return hit


def _states(conn: sa.Connection, ids: Sequence[int] | None) -> list[tuple[vm.MeshState, Any]]:
    """(state, measures) of the ready wireframes among `ids` (None — all of the project) that have triangles,
    by id: two queries however many they are (a click must not wait for a query a wireframe). A wireframe that
    is gone is left out: its layer leaves the 3D window by the event of its removal. The settings of a DTM
    are not read (`dtm` None): they are no part of the geometry."""
    from bf_core.wireframes import store

    query = (
        sa.select(
            objects.c.id,
            objects.c.name,
            objects.c.version,
            objects.c.meta,
            objects.c.extent,
            objects.c.created_at,
            wireframes.c.point_count,
            wireframes.c.triangle_count,
            wireframes.c.origin,
            wireframes.c.render,
            wireframes.c.stats,
        )
        .join(wireframes, wireframes.c.object_id == objects.c.id)
        .where(objects.c.kind == vm.WIREFRAME, objects.c.state == "ready", wireframes.c.triangle_count > 0)
        .order_by(objects.c.id)
    )
    owned = sa.select(blob_owners.c.owner_id, blob_owners.c.role, blob_owners.c.sha256).where(
        blob_owners.c.owner_kind == store.OWNER,
        blob_owners.c.role.in_(list(store.ARRAYS)) | blob_owners.c.role.like("pack:%"),
    )
    if ids is not None:
        wanted = sorted({int(i) for i in ids})
        if not wanted:
            return []
        query = query.where(objects.c.id.in_(wanted))
        owned = owned.where(blob_owners.c.owner_id.in_(wanted))
    rows = conn.execute(query).all()
    shas: dict[int, dict[str, str]] = {}
    for owner, role, sha in conn.execute(owned).all() if rows else []:
        shas.setdefault(int(owner), {})[str(role)] = str(sha)
    out: list[tuple[vm.MeshState, Any]] = []
    for r in rows:
        blobs = files.geometry(shas.get(int(r.id), {}))  # `xyz` and `tri`, or an entry of a pack
        if not files.complete(blobs):
            raise vm.broken()
        state = vm.MeshState(
            int(r.id),
            r.name,
            int(r.version),
            (r.meta or {}).get("color"),
            None,
            int(r.point_count),
            int(r.triangle_count),
            r.extent,
            list(r.origin or [0.0, 0.0, 0.0]),
            r.render,
            tuple(sorted(blobs.items())),
            _stamp(r.created_at),
        )
        out.append((state, r.stats))
    return out


# --- the triangle under a click ----------------------------------------------------------------------------


def _numbers(values: Any, n: int) -> np.ndarray | None:
    try:
        out = np.asarray(values, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        return None
    return out if out.shape == (n,) and bool(np.isfinite(out).all()) else None


def _entry(extent: Sequence[float] | None, o: np.ndarray, d: np.ndarray) -> float:
    """Where the ray o + t · d, t ≥ 0, enters the extent of a wireframe (grown a little); inf — it misses."""
    if extent is None or len(extent) != 6 or any(v is None for v in extent):
        return 0.0  # no extent to tell by: the wireframe is tested
    t0, t1 = 0.0, math.inf
    for c in range(3):
        lo, hi = float(extent[c]) - 2 * BOX_PAD_M, float(extent[c + 3]) + 2 * BOX_PAD_M
        if d[c] == 0.0:
            if o[c] < lo or o[c] > hi:
                return math.inf
            continue
        a, b = (lo - o[c]) / d[c], (hi - o[c]) / d[c]
        t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
        if t0 > t1:
            return math.inf
    return t0


def pick(
    project: Any, origin: Any, direction: Any, wireframe_ids: Sequence[int] | None = None, clip: Any = None
) -> dict[str, Any]:
    """The nearest triangle of the wireframes `wireframe_ids` (None — all of the project) the ray meets.

    `origin`, `direction` — project coordinates; `clip` — the planes of the section window, k×4 [nx, ny, nz, c]
    with n·p + c ≥ 0 inside: only the piece of the ray inside counts. The answer: `{hit, tested}`, `hit` —
    null or {wireframe_id, name, triangle (its number in the store), distance, point, vertices, indices,
    normal, area, dip, dip_direction (null for a level triangle)}; `tested` — wireframes whose triangles were
    looked at."""
    o, d = _numbers(origin, 3), _numbers(direction, 3)
    if o is None or d is None or not float(np.linalg.norm(d)) > 0.0:
        raise bad_ray()
    d = d / float(np.linalg.norm(d))
    try:
        planes = half_spaces(clip) if clip is not None else None
    except (TypeError, ValueError) as exc:
        raise bad_clip() from exc
    if wireframe_ids is not None and len(wireframe_ids) > MAX_IDS:
        raise bad_wireframes()
    with dialect.read_tx(project.engine) as conn:
        states = [s for s, _stats in _states(conn, wireframe_ids)]
    entered = sorted(((_entry(s.extent, o, d), s.wireframe_id, s) for s in states), key=lambda e: e[:2])
    best: tuple[vm.MeshState, gp.PickHit] | None = None
    reach = math.inf
    tested = 0
    for entry, _id, state in entered:
        if entry == math.inf or entry > reach:  # the ray misses it, or it starts behind the hit already found
            break  # … and so does every later one
        g = geometry(project, state)
        try:
            hit = gp.ray_pick(g.xyz, g.tri, o, d, parts=g.pick_parts(), clip=planes, max_distance=reach)
        except ValueError as exc:  # a triangle of a vertex that is not there
            raise vm.broken() from exc
        tested += 1
        if hit is not None and hit.distance < reach:
            best, reach = (state, hit), hit.distance
    if best is None:
        return {"hit": None, "tested": tested}
    state, hit = best
    return {
        "hit": {
            "wireframe_id": state.wireframe_id,
            "name": state.name,
            "triangle": hit.triangle,
            "distance": hit.distance,
            "point": list(hit.point),
            "vertices": [list(p) for p in hit.vertices],
            "indices": list(hit.indices),
            "normal": None if hit.normal is None else list(hit.normal),
            "area": hit.area,
            "dip": hit.dip,
            "dip_direction": hit.dip_direction,
        },
        "tested": tested,
    }


# --- the section of a wireframe by a plane -----------------------------------------------------------------


@dataclass(frozen=True)
class Plane:
    """The plane of a section and the planes its lines are cut by (the ends of a section line with a length)."""

    point: tuple[float, float, float]
    normal: tuple[float, float, float]  # unit
    ends: tuple[tuple[float, float, float, float], ...] = ()

    def key(self) -> list[Any]:
        return [
            [repr(x) for x in self.point],
            [repr(x) for x in self.normal],
            [[repr(x) for x in e] for e in self.ends],
        ]


def _triple(text: str, name: str) -> np.ndarray:
    try:
        values = [float(part) for part in text.split(",")]
    except ValueError as exc:
        raise bad_plane(f"«{name}» — три числа через запятую") from exc
    out = _numbers(values, 3)
    if out is None:
        raise bad_plane(f"«{name}» — три числа через запятую")
    return out


def _number(text: str | None, name: str, default: float | None = None) -> float | None:
    if text is None or not text.strip():
        return default
    try:
        value = float(text)
    except ValueError as exc:
        raise bad_plane(f"«{name}» — число") from exc
    if not math.isfinite(value):
        raise bad_plane(f"«{name}» — число")
    return value


def parse_plane(
    point: str | None = None,
    normal: str | None = None,
    x0: str | None = None,
    y0: str | None = None,
    az: str | None = None,
    length: str | None = None,
    offset: str | None = None,
) -> Plane:
    """The plane of `GET /viz/wireframes/{id}/section`: either `point` and `normal` («x,y,z», project
    coordinates), or a section line (ADR 0007 §4) — `x0`, `y0`, `az` (degrees clockwise from north; the normal
    looks to the right of the line), `length` (the lines are cut at its ends) and `offset` (the plane moved
    along the normal: `towards` and `-away` are the faces of the window)."""
    by_plane = point is not None or normal is not None
    by_line = any(v is not None for v in (x0, y0, az, length, offset))
    if by_plane == by_line:
        raise bad_plane("нужна либо точка с нормалью, либо линия разреза")
    if by_plane:
        if point is None or normal is None:
            raise bad_plane("нужны и точка, и нормаль")
        p, n = _triple(point, "point"), _triple(normal, "normal")
        norm = float(np.linalg.norm(n))
        if not norm > 0.0:
            raise bad_plane("нормаль не может быть нулевой")
        n = n / norm + 0.0
        return Plane((float(p[0]), float(p[1]), float(p[2])), (float(n[0]), float(n[1]), float(n[2])))
    px, py, azimuth = _number(x0, "x0"), _number(y0, "y0"), _number(az, "az")
    if px is None or py is None or azimuth is None:
        raise bad_plane("у линии разреза нужны x0, y0 и az")
    try:
        window = gs.section_window(px, py, azimuth, length=_number(length, "length"))
    except ValueError as exc:
        raise bad_plane("длина линии разреза — положительное число") from exc
    at = window.point + window.normal * (_number(offset, "offset", 0.0) or 0.0)
    ends = tuple((float(e[0]), float(e[1]), float(e[2]), float(e[3])) for e in window.ends().tolist())
    n = window.normal
    return Plane((float(at[0]), float(at[1]), float(at[2])), (float(n[0]), float(n[1]), float(n[2])), ends)


def section_etag(uid: str, state: vm.MeshState, plane: Plane) -> str:
    """The geometry and the plane: a new name, colour or DTM setting of the wireframe changes nothing."""
    return etag("section", uid, state, plane.key())


def section(project: Any, state: vm.MeshState, plane: Plane) -> bytes:
    """BFA1 `section`: the polylines where the plane cuts the wireframe — `xyz <f8` k×3 (project coordinates),
    `offsets <u4` n + 1 (line i is xyz[offsets[i]:offsets[i + 1]]), `closed <u1` n (a closed line does not
    repeat its first point). Only the parts of the wireframe the plane touches are looked at."""
    cut = gs.Section(np.zeros((0, 3)), np.zeros(1, dtype=np.int64), np.zeros(0, dtype=np.bool_))
    if state.triangles:
        g = geometry(project, state)
        try:
            cut = gs.plane_section(g.xyz, g.tri, plane.point, plane.normal, parts=g.parts, clip=plane.ends or None)
        except ValueError as exc:  # a triangle of a vertex that is not there
            raise vm.broken() from exc
    header = {
        "kind": "section",
        "format": FORMAT,
        "wireframe": state.wireframe_id,
        "point": list(plane.point),
        "normal": list(plane.normal),
        "lines": len(cut),
        "points": len(cut.xyz),
    }
    arrays = [("xyz", cut.xyz, "<f8"), ("offsets", cut.offsets, "<u4"), ("closed", cut.closed, "<u1")]
    return binary.encode(header, arrays)


# --- where the holes of a set cross wireframes -------------------------------------------------------------


def parse_ids(text: str | None) -> list[int] | None:
    """`?wireframes=1,2,3` → ids; None (no parameter) — every wireframe of the project."""
    if text is None:
        return None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) > MAX_IDS or not all(p.isascii() and p.isdigit() and len(p) <= 18 for p in parts):
        raise bad_wireframes()
    return [int(p) for p in parts]


def set_targets(conn: sa.Connection, ids: Sequence[int] | None) -> list[tuple[vm.MeshState, Any]]:
    """The wireframes the holes are tested against: (state, measures), by id."""
    return _states(conn, ids)


def intersections_etag(uid: str, state: SetState, method: str, targets: Sequence[tuple[vm.MeshState, Any]]) -> str:
    """The set, the method and, of every wireframe, its geometry, its solidity and its version (the answer
    carries its name and colour)."""
    return etag("intersections", uid, state, method, [[*t.key(), t.version, _solid(stats)] for t, stats in targets])


def intersections(project: Any, state: SetState, method: str, targets: Sequence[tuple[vm.MeshState, Any]]) -> bytes:
    """BFA1 `intersections` (ADR 0007 §5): `objects [{kind, id, name, color}]` in the header and, a mark per
    crossing sorted by hole, depth, object: `hole <u4` (dh_holes.id), `depth <f8` (along the hole, m),
    `xyz <f8` k×3 (`Trajectories.positions(hole, depth)`), `obj <u4` (the place in `objects`), `event <u1`
    (0 a crossing, 1 into and 2 out of a closed solid)."""
    from bf_core.drillholes.trajectory import trajectories

    st = trajectories(project, state.set_id, method)
    key = (st.key, tuple((t.cache_key(), _solid(stats)) for t, stats in targets))
    with _lock:
        hits = _marks.get(key)
        if hits is not None:
            _marks.move_to_end(key)
    if hits is None:
        try:  # a wireframe at a time: only the one at hand is held, the cache lets the others go as it fills
            found = [gi.hole_crossings(st.traj, [geometry(project, t).target(stats)]) for t, stats in targets]
        except ValueError as exc:  # a triangle of a vertex that is not there
            raise vm.broken() from exc
        hits = gi.join(found)
        with _lock:
            _marks[key] = hits
            while len(_marks) > MARKS_KEPT:
                _marks.popitem(last=False)
    header = {
        "kind": "intersections",
        "format": FORMAT,
        "set": state.set_id,
        "version": state.version,
        "method": method,
        "objects": [
            {"kind": vm.WIREFRAME, "id": t.wireframe_id, "name": t.name, "color": t.color} for t, _s in targets
        ],
        "n": len(hits),
    }
    arrays = [
        ("hole", st.hole_ids[hits.hole], "<u4"),
        ("depth", hits.depth, "<f8"),
        ("xyz", hits.xyz, "<f8"),
        ("obj", hits.mesh, "<u4"),
        ("event", hits.event, "<u1"),
    ]
    return binary.encode(header, arrays)
