"""The sections of wireframes on a section of a series (ADR 0016 §3, §6; ТЗ 8.1.5, 5.3): every wireframe the 3D
window shows cut by the plane t = 0 of section k (of segment `seg` of a polyline) in one answer.

The plane of section k is O_k with the normal v̂ of the series, taken as `GET /viz/wireframes/{id}/section`
takes `point=O_k&normal=v̂` (`wireframes.exact.parse_plane`: the normal divided by its length), and cut as it
cuts (`geo.section.plane_section` over the part table of the geometry): the lines of every wireframe are those
of that endpoint bit for bit. A segment of a polyline is the plane of the segment cut by the half-spaces of its
two ends (`PolylineSection.plane(0, seg).bounds`: the bisectors of the bends, the planes across the free ends).

The lines come from the file of the series cache (`cache_files.py`, the job `sections.cache`), when it holds
the section, or are cut from the geometry kept by `wireframes/exact.py` (its cache of 768 MB) — the same
numbers either way: the job cuts the same arrays by the same call.

    series_key(geom)                 the key of the planes of a series: its cache folder, its part of the ETag
    slot_of(geom, k, seg) → Slot     slot(geom, i) → Slot     slot_count(geom), slot_axis(geom)
    parse_ids(text), states(conn, ids) → (states, empty, missing)
    answer_etag(…), answer(project, series_id, key, slot, states, empty, missing) → (BFA1, hits)

BFA1 `series_section`: `xyz <f8` n×3 (project coordinates), `offsets <u4` lines + 1 (line i is
xyz[offsets[i]:offsets[i + 1]]), `closed <u1` lines (a closed line does not repeat its first point), `wf <u4`
lines (the wireframe of the line); the header — {kind, format, series, key, k, seg, point, normal, clip,
wireframes (answered, by id), missing (not a ready wireframe: deleted, never one), lines, points}.

numpy is imported with this module, numba with the first cut: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import objects, wireframes

from .geometry import SeriesGeometry
from .polyline import PolylineSection

CACHE_FORMAT = 1  # of the planes, the files and the answers: a change makes new folders and ETags
MAX_WIREFRAMES = 200  # in one request (ADR 0016 §3)
KIND = "series_section"

Geom = SeriesGeometry | PolylineSection


# --- errors ------------------------------------------------------------------------------------------------


def bad_wireframes() -> ApiError:
    return ApiError(
        422,
        "bad_wireframes",
        f"Список каркасов задан неверно: нужны номера через запятую, не больше {MAX_WIREFRAMES}.",
        "Покажите на разрезе меньше каркасов или обновите дерево проекта и повторите.",
    )


def bad_section(count: int, what: str = "разреза") -> ApiError:
    return ApiError(
        422,
        "bad_section",
        f"Такого {what} в серии нет: номера от 1 до {count}.",
        "Обновите таблицу разрезов серии и выберите разрез из неё.",
    )


# --- the planes of a series --------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Slot:
    """Section k (`seg` — the segment of a polyline, else None) as it is cut: `point` and `normal` — the plane
    as `plane_section` takes it (the normal of unit length, as `exact.parse_plane` makes it); `axis` — v̂ of the
    series as it is; `clip` — the half-spaces the lines are cut by (None — none)."""

    index: int  # the place of the section in the files of the cache: k, or seg of a polyline
    k: int
    seg: int | None
    point: tuple[float, float, float]
    normal: tuple[float, float, float]
    axis: tuple[float, float, float]
    clip: np.ndarray | None


def slot_count(geom: Geom) -> int:
    """Sections of the series as the cache holds them: K, or the segments of a polyline (K = 1)."""
    return geom.segments if isinstance(geom, PolylineSection) else geom.count


def slot_axis(geom: Geom) -> str:
    return "seg" if isinstance(geom, PolylineSection) else "k"


def _triple(v: Any) -> tuple[float, float, float]:
    return float(v[0]), float(v[1]), float(v[2])


def slot(geom: Geom, index: int) -> Slot:
    """The plane of section `index` (a segment of a polyline)."""
    if isinstance(geom, PolylineSection):
        plane, k, seg = geom.plane(0, index), 0, int(index)
        clip: np.ndarray | None = np.ascontiguousarray(plane.bounds, dtype=np.float64)
    else:
        plane, k, seg = geom.plane(index), int(index), None
        clip = None
    v = np.array(_triple(plane.v))
    n = v / float(np.linalg.norm(v)) + 0.0  # as `exact.parse_plane` takes `normal=`
    return Slot(int(index), k, seg, _triple(plane.origin), _triple(n), _triple(v), clip)


def slot_of(geom: Geom, k: int, seg: int | None) -> Slot:
    """Section k (from 0) of the request; a polyline has one section (k = 0) and the segment `seg` (0 when not
    given); 422 `bad_section` for a section or a segment the series has not."""
    if isinstance(geom, PolylineSection):
        if k != 0:
            raise bad_section(1)
        seg = 0 if seg is None else seg
        if not 0 <= seg < geom.segments:
            raise bad_section(geom.segments, "звена ломаного разреза")
        return slot(geom, seg)
    if not 0 <= k < geom.count or seg not in (None, 0):
        raise bad_section(geom.count)
    return slot(geom, k)


def _digest(a: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(a, dtype="<f8").tobytes()).hexdigest()


def series_key(geom: Geom) -> str:
    """32 hex of SHA-256 of what the planes are made of: P1, v̂ and s_k of a series, the vertices of a polyline.
    Names, windows (but through the step they give) and the shadow are not in it."""
    if isinstance(geom, PolylineSection):
        parts = ["polyline", _digest(geom.points), int(geom.segments)]
    else:
        parts = ["series", _digest(geom.p1), _digest(geom.v), _digest(geom.offs), int(geom.count)]
    text = json.dumps([KIND, CACHE_FORMAT, *parts], separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


# --- the wireframes of a request ---------------------------------------------------------------------------


def parse_ids(text: str | None) -> list[int]:
    """`?ids=3,1,2` → [1, 2, 3] (repeats once); empty — no wireframe; 422 `bad_wireframes` otherwise."""
    if text is None:
        raise bad_wireframes()
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not all(p.isascii() and p.isdigit() and len(p) <= 18 for p in parts):
        raise bad_wireframes()
    out = sorted({int(p) for p in parts})
    if len(out) > MAX_WIREFRAMES:
        raise bad_wireframes()
    return out


def check_ids(ids: Sequence[int]) -> list[int]:
    out = sorted({int(i) for i in ids})
    if len(out) > MAX_WIREFRAMES or any(i < 0 for i in out):
        raise bad_wireframes()
    return out


def states(conn: sa.Connection, ids: Sequence[int]) -> tuple[list[Any], list[list[int]], list[int]]:
    """(the states of the ready wireframes among `ids` that have triangles, by id; [id, version] of the ready
    ones without triangles; the ids that are no ready wireframe — deleted, being built, never one)."""
    from bf_core.viz import meshes as vm
    from bf_core.wireframes import exact

    found = [s for s, _stats in exact.set_targets(conn, ids)]
    rest = sorted(set(ids) - {s.wireframe_id for s in found})
    empty: list[list[int]] = []
    if rest:
        rows = conn.execute(
            sa.select(objects.c.id, objects.c.version)
            .join(wireframes, wireframes.c.object_id == objects.c.id)
            .where(objects.c.id.in_(rest), objects.c.kind == vm.WIREFRAME, objects.c.state == "ready")
            .order_by(objects.c.id)
        ).all()
        empty = [[int(r.id), int(r.version)] for r in rows]
    missing = sorted(set(rest) - {e[0] for e in empty})
    return found, empty, missing


# --- the answer --------------------------------------------------------------------------------------------


def answer_etag(uid: str, key: str, s: Slot, found: Sequence[Any], empty: Sequence[Any], missing: Sequence[int]) -> str:
    """The planes of the series, the section, and of every wireframe its geometry (`MeshState.key`): a new name or
    colour of a wireframe or of the series changes nothing."""
    body = [KIND, CACHE_FORMAT, uid, key, s.k, s.seg, [st.key() for st in found], list(empty), list(missing)]
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return '"' + hashlib.sha256(text.encode("utf-8")).hexdigest() + '"'


def cut(xyz: Any, tri: Any, parts: Any, s: Slot) -> Any:
    """The lines of one wireframe on the section: `geo.section.plane_section` as `exact.section` calls it."""
    from bf_core.geo import section as gs

    return gs.plane_section(xyz, tri, s.point, s.normal, parts=parts, clip=s.clip)


def live(project: Any, state: Any, s: Slot) -> Any:
    """The lines cut from the geometry kept by `wireframes/exact.py` (read from the blobs once)."""
    from bf_core.viz import meshes as vm
    from bf_core.wireframes import exact

    g = exact.geometry(project, state)
    try:
        return cut(g.xyz, g.tri, g.parts, s)
    except ValueError as exc:  # a triangle of a vertex that is not there
        raise vm.broken() from exc


def encode(series_id: int, key: str, s: Slot, ids: Sequence[int], cuts: Sequence[Any], missing: Sequence[int]) -> bytes:
    """BFA1 `series_section` of the lines of the wireframes `ids` (`cuts` — their `geo.section.Section`s)."""
    from bf_core.viz import binary

    counts = [len(c) for c in cuts]
    points = [len(c.xyz) for c in cuts]
    xyz = np.concatenate([c.xyz for c in cuts]) if cuts else np.zeros((0, 3))
    starts = np.concatenate([[0], np.cumsum(points)]).astype(np.int64)
    offsets = np.concatenate([[0], *[c.offsets[1:] + starts[i] for i, c in enumerate(cuts)]]).astype(np.int64)
    closed = np.concatenate([c.closed for c in cuts]) if cuts else np.zeros(0, dtype=np.bool_)
    wf = np.repeat(np.asarray(ids, dtype=np.int64), counts)
    header = {
        "kind": KIND,
        "format": CACHE_FORMAT,
        "series": int(series_id),
        "key": key,
        "k": s.k,
        "seg": s.seg,
        "point": list(s.point),
        "normal": list(s.axis),
        "clip": None if s.clip is None else s.clip.tolist(),
        "wireframes": [int(i) for i in ids],
        "missing": [int(i) for i in missing],
        "lines": int(sum(counts)),
        "points": int(sum(points)),
    }
    arrays = [("xyz", xyz, "<f8"), ("offsets", offsets, "<u4"), ("closed", closed, "<u1"), ("wf", wf, "<u4")]
    return binary.encode(header, arrays)


def answer(
    project: Any,
    series_id: int,
    key: str,
    s: Slot,
    found: Sequence[Any],
    empty: Sequence[Sequence[int]],
    missing: Sequence[int],
) -> tuple[bytes, int]:
    """The answer and how many of the wireframes with triangles were read from the files of the cache."""
    from bf_core.geo import section as gs

    from . import cache_files

    by_id: dict[int, Any] = {}
    hits = 0
    for st in found:
        c = cache_files.read_slot(project.folder, key, st.cache_key(), s.index)
        if c is None:
            c = live(project, st, s)
        else:
            hits += 1
        by_id[st.wireframe_id] = c
    blank = gs.Section(np.zeros((0, 3)), np.zeros(1, dtype=np.int64), np.zeros(0, dtype=np.bool_))
    for wf_id, _version in empty:
        by_id[int(wf_id)] = blank
    ids = sorted(by_id)
    if hits:
        cache_files.touch(project.folder, key)
    return encode(series_id, key, s, ids, [by_id[i] for i in ids], missing), hits
