"""Parts and levels of detail of a line layer for 3D (ADR 0011 §3; ADR 0004 §2, ADR 0007 §1 «Части», «ETag»).

`GET /viz/lines/{id}` — the manifest, BFA1 `line_layer` (header only): the layer, its extent, the levels and
`parts [{k, n, points, bbox, origin, etags[3]}]`. `GET /viz/lines/{id}/parts/{k}?lod=0|1|2` — BFA1 `lines`:
`offsets u4` (n + 1), `xyz f4×3` from the origin of the part, `line u4` — the line of the layer (the row of its
attributes) every polyline belongs to, `closed u1`. A layer of points has no `offsets` and `closed`: `xyz` and
`line` (the point, the row of its attributes). Data for display only; calculations use float64 in the core.

Parts. A line is first cut into pieces of at most `MAX_VERTICES` vertices and `MAX_SPAN_M` along any axis; two
pieces of a line share one vertex, so every segment is in exactly one piece (`closed` is 1 only for a whole closed
line: the pieces of a cut ring are open and still join). One segment longer than 16 km stays a piece of its own —
there is nothing to cut it at, and only that piece loses float32 precision. The pieces go in the Morton order of
the centres of their extents (50 m cells, `parts.morton`) and are packed into parts of at most `MAX_VERTICES`
vertices; a part wider than 16 km is halved until it fits or holds one piece. Points are ordered and packed the
same way. The origin of a part is the centre of its extent rounded to a whole metre (float64), so the float32
offsets stay below 8000.5 m: a step of 2⁻¹¹ m, an error ≤ 0.25 mm per axis.

Levels. 0 — every vertex; 1 and 2 — Douglas–Peucker with 4 and 32 m (`geo/simplify.py`, float64, before the
float32 offsets): the ends, closed rings and the vertices around an empty height stay. Points: one point in
every cell of 4 or 32 m of the project grid (X, Y, Z; the first in the order of the part; points without a
height — in cells of their own).

An empty height is NaN in `xyz`, never 0 (the extent and the origin of a part ignore it; `bbox` has null when no
vertex of the part has a height). Text (DXF TEXT/MTEXT, MIF Text: a feature with the attribute `text`) goes into
the header: `labels {at, text, height?, rotation?, total}` — `at` is the polyline (the point) of this answer the
text stands at; at most `MAX_LABELS` a part, `total` — how many there are.

The ETag is the SHA-256 of the key (project, layer, `objects.version`, creation time, the blobs of the layer,
part, level and the limits above), so a repeated request is answered 304 before anything is read.
numba is imported with this module (through `geo.simplify`): import it lazily, never before BF-READY.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import line_layers, objects
from bf_core.jit import njit

from . import binary
from .keys import FORMAT, _stamp, etag
from .parts import CELL_M, MAX_SPAN_M, Part, morton

MAX_VERTICES = 256_000
LOD_TOL = (0.0, 4.0, 32.0)  # metres: Douglas–Peucker of lines, the cell of points
MAX_LABELS = 5000
MAX_LABEL_CHARS = 256
TEXT, HEIGHT, ROTATION = "text", "height", "rotation"
CACHE_VERTICES = 8_000_000  # vertices of the layouts kept in memory (≈ 200 MB), least recently used dropped
LAYER = "line_layer"


def layer_not_found() -> ApiError:
    return ApiError(404, "line_layer_not_found", "Слой линий не найден.", "Обновите дерево проекта.")


def part_not_found(k: int) -> ApiError:
    return ApiError(
        404, "part_not_found", f"Части {k} в слое нет.", "Слой изменился: запросите список частей слоя заново."
    )


def bad_lod() -> ApiError:
    return ApiError(
        422, "bad_lod", "Неизвестный уровень детализации слоя.", "Укажите уровень 0 (полный), 1 или 2 (упрощённые)."
    )


# --- the state of a layer and its ETags ------------------------------------------------------------------


@dataclass(frozen=True)
class LayerState:
    layer_id: int
    name: str
    version: int
    file_id: int | None
    color: str | None
    points_only: bool
    line_count: int
    point_count: int
    blobs: tuple[tuple[str, str], ...]  # (role, sha256), sorted
    stamp: str  # creation time: SQLite gives a deleted layer's id to the next one
    fields: tuple[str, ...]  # feature attributes of the layer

    def key(self) -> list[Any]:
        digest = hashlib.sha256(";".join(f"{r}:{s}" for r, s in self.blobs).encode()).hexdigest()[:16]
        return [self.layer_id, self.version, self.stamp, digest]

    def sha(self, role: str) -> str | None:
        return dict(self.blobs).get(role)


def layer_state(conn: sa.Connection, layer_id: int) -> LayerState:
    from bf_core.lines.store import layer_blobs

    row = conn.execute(
        sa.select(
            objects.c.name,
            objects.c.version,
            objects.c.parent_id,
            objects.c.created_at,
            line_layers.c.color,
            line_layers.c.points_only,
            line_layers.c.line_count,
            line_layers.c.point_count,
            line_layers.c.attr_schema,
        )
        .join(line_layers, line_layers.c.object_id == objects.c.id)
        .where(objects.c.id == layer_id, objects.c.kind == LAYER, objects.c.state == "ready")
    ).first()
    if row is None:
        raise layer_not_found()
    fields = tuple(str(f["name"]) for f in row.attr_schema or [] if f.get("scope", "feature") == "feature")
    return LayerState(
        layer_id,
        row.name,
        int(row.version),
        row.parent_id,
        row.color,
        bool(row.points_only),
        int(row.line_count),
        int(row.point_count),
        tuple(sorted(layer_blobs(conn, layer_id).items())),
        _stamp(row.created_at),
        fields,
    )


def layer_etag(uid: str, state: LayerState) -> str:
    return etag(LAYER, uid, state, MAX_VERTICES, MAX_SPAN_M, list(LOD_TOL))


def part_etag(uid: str, state: LayerState, k: int, lod: int) -> str:
    return etag("lines", uid, state, MAX_VERTICES, MAX_SPAN_M, LOD_TOL[lod], MAX_LABELS, k, lod)


# --- pieces and parts ----------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _cut(xyz, offsets, max_vertices, max_span, line, start, stop, box):  # type: ignore[no-untyped-def]
    """Pieces of the lines (see the module docstring). With empty outputs only counts them."""
    fill = len(line) > 0
    n = 0
    lo = np.empty(3)
    hi = np.empty(3)
    for ln in range(len(offsets) - 1):
        a = offsets[ln]
        b = offsets[ln + 1]
        if b <= a:
            continue
        s = a
        for c in range(3):
            v = xyz[a, c]
            lo[c] = math.inf if math.isnan(v) else v
            hi[c] = -math.inf if math.isnan(v) else v
        for i in range(a + 1, b):
            span = 0.0
            for c in range(3):
                v = xyz[i, c]
                if math.isnan(v):
                    width = hi[c] - lo[c]
                else:
                    width = max(hi[c], v) - min(lo[c], v)
                if width > span:
                    span = width
            if (i - s + 1 > max_vertices or span > max_span) and i - 1 > s:
                if fill:
                    line[n] = ln
                    start[n] = s
                    stop[n] = i
                    for c in range(3):
                        box[n, c] = lo[c] if lo[c] <= hi[c] else math.nan
                        box[n, 3 + c] = hi[c] if lo[c] <= hi[c] else math.nan
                n += 1
                s = i - 1
                for c in range(3):
                    v = xyz[s, c]
                    lo[c] = math.inf if math.isnan(v) else v
                    hi[c] = -math.inf if math.isnan(v) else v
            for c in range(3):
                v = xyz[i, c]
                if not math.isnan(v):
                    if v < lo[c]:
                        lo[c] = v
                    if v > hi[c]:
                        hi[c] = v
        if fill:
            line[n] = ln
            start[n] = s
            stop[n] = b
            for c in range(3):
                box[n, c] = lo[c] if lo[c] <= hi[c] else math.nan
                box[n, 3 + c] = hi[c] if lo[c] <= hi[c] else math.nan
        n += 1
    return n


def _extent(lo: np.ndarray, hi: np.ndarray) -> tuple[float, float, float, float, float, float]:
    """The extent of rows with their own extents; a height that is empty everywhere stays NaN."""
    a, b = np.fmin.reduce(lo, axis=0), np.fmax.reduce(hi, axis=0)
    return (float(a[0]), float(a[1]), float(a[2]), float(b[0]), float(b[1]), float(b[2]))


def _span(box: tuple[float, ...]) -> float:
    return max(box[3 + c] - box[c] for c in range(3) if not math.isnan(box[c]))


def _origin(box: tuple[float, ...]) -> tuple[float, float, float]:
    """The centre of the extent rounded to a whole metre; 0 for a height that is empty everywhere."""
    out = [0.0 if math.isnan(box[c]) else float(np.round((box[c] + box[3 + c]) * 0.5)) + 0.0 for c in range(3)]
    return out[0], out[1], out[2]


def _split(counts: np.ndarray | None, lo: np.ndarray, hi: np.ndarray, max_vertices: int, max_span: float) -> list[Part]:
    """Parts of the rows (pieces with their vertex `counts`, or points) already in order."""
    n = len(lo)
    cum = np.arange(1, n + 1, dtype=np.int64) if counts is None else np.cumsum(counts, dtype=np.int64)
    stack: list[tuple[int, int]] = []
    a = 0
    while a < n:
        base = int(cum[a - 1]) if a else 0
        b = max(a + 1, int(np.searchsorted(cum, base + max_vertices, side="right")))
        stack.append((a, b))
        a = b
    stack.reverse()
    out: list[Part] = []
    while stack:
        a, b = stack.pop()
        box = _extent(lo[a:b], hi[a:b])
        if b - a > 1 and _span(box) > max_span:
            mid = (a + b) // 2
            stack.extend(((mid, b), (a, mid)))  # the lower half first: the order is kept
            continue
        out.append(Part(len(out), a, b, box, _origin(box)))
    return out


@dataclass
class Labels:
    text: Any  # pyarrow string array, a row per feature
    shown: np.ndarray  # bool per feature: it has a text
    height: np.ndarray | None  # float64 per feature, NaN — empty
    rotation: np.ndarray | None


@dataclass
class Layout:
    """A layer cut for display. Lines: the pieces in part order (`line`, `start`, `stop`, `closed`, `head`) over
    the vertices as stored. Points: `xyz` in part order, `order` — the point of the layer at every place."""

    xyz: np.ndarray
    order: np.ndarray
    parts: list[Part]
    part_points: list[int]  # vertices of every part at level 0
    bbox: tuple[float, float, float, float, float, float] | None
    line: np.ndarray | None = None
    start: np.ndarray | None = None
    stop: np.ndarray | None = None
    closed: np.ndarray | None = None
    head: np.ndarray | None = None  # the piece starts its line (a text stands at the first vertex of a feature)
    labels: Labels | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def pieces(self) -> int:
        return len(self.order)


def build(
    xyz: Any, offsets: Any, closed: Any, *, max_vertices: int = MAX_VERTICES, max_span: float = MAX_SPAN_M
) -> Layout:
    """The layout of a layer from its arrays (`offsets` None — a layer of points)."""
    pts = np.ascontiguousarray(np.asarray(xyz, dtype=np.float64)).reshape(-1, 3)
    if max_vertices < 2 or not max_span > 0:
        raise ValueError("lines: a part holds at least two vertices and has a positive span")
    if offsets is None:
        order = np.argsort(morton(pts[:, 0], pts[:, 1], CELL_M), kind="stable")
        pts = pts[order]
        parts = _split(None, pts, pts, max_vertices, max_span)
        bbox = _extent(pts, pts) if len(pts) else None
        return Layout(pts, order, parts, [p.n for p in parts], bbox)
    off = np.ascontiguousarray(np.asarray(offsets, dtype=np.int64))
    flags = np.ascontiguousarray(np.asarray(closed, dtype=np.uint8))
    if off.ndim != 1 or len(off) < 1 or off[0] != 0 or off[-1] != len(pts) or len(flags) != len(off) - 1:
        raise ValueError("lines: offsets and closed flags do not match the vertices")
    empty_i, empty_f = np.zeros(0, dtype=np.int64), np.zeros((0, 6))
    n = int(_cut(pts, off, max_vertices, max_span, empty_i, empty_i, empty_i, empty_f))
    line, start, stop = (np.empty(n, dtype=np.int64) for _ in range(3))
    box = np.empty((n, 6))
    if n:
        _cut(pts, off, max_vertices, max_span, line, start, stop, box)
    order = np.argsort(morton((box[:, 0] + box[:, 3]) * 0.5, (box[:, 1] + box[:, 4]) * 0.5, CELL_M), kind="stable")
    line, start, stop, box = line[order], start[order], stop[order], box[order]
    counts = stop - start
    parts = _split(counts, box[:, :3], box[:, 3:], max_vertices, max_span)
    cum = np.concatenate([np.zeros(1, dtype=np.int64), np.cumsum(counts, dtype=np.int64)])
    head = start == off[line]
    whole = head & (stop == off[line + 1])
    return Layout(
        pts,
        order,
        parts,
        [int(cum[p.stop] - cum[p.start]) for p in parts],
        _extent(box[:, :3], box[:, 3:]) if n else None,
        line,
        start,
        stop,
        ((flags[line] != 0) & whole).astype(np.uint8),
        head,
    )


# --- one part ------------------------------------------------------------------------------------------


def _thin(pts: np.ndarray, cell: float) -> np.ndarray:
    """Indices (ascending) of one point in every cell of `cell` metres: the first in the order given."""
    if not len(pts):
        return np.zeros(0, dtype=np.int64)
    grid = np.floor(pts / cell)  # cells of the project grid: a point keeps its cell whatever else is in the part
    low = np.fmin.reduce(grid, axis=0)
    # whole numbers from 1 along every axis; 0 along Z — the points without a height
    rel = np.where(np.isnan(grid), 0.0, grid - np.where(np.isnan(low), 0.0, low) + 1.0)
    size = [float(v) + 1.0 for v in rel.max(axis=0)]
    if size[0] * size[1] * size[2] < float(1 << 62):
        idx = rel.astype(np.int64)
        cells = (idx[:, 2] * int(size[1]) + idx[:, 1]) * int(size[0]) + idx[:, 0]
        first = np.unique(cells, return_index=True)[1]
    else:  # one point of a part with a huge extent (junk coordinates): no single number for a cell
        first = np.unique(rel, axis=0, return_index=True)[1]
    return np.sort(first)


def _null(values: np.ndarray) -> list[float | None]:
    return [None if math.isnan(v) else v for v in values.tolist()]


def _labels(lay: Layout, feature: np.ndarray, at_start: np.ndarray | None) -> dict[str, Any] | None:
    """Texts of the features of an answer: `feature` — the feature of every polyline (point) of it."""
    lab = lay.labels
    if lab is None:
        return None
    shown = lab.shown[feature] if at_start is None else lab.shown[feature] & at_start
    at = np.flatnonzero(shown)
    total = len(at)
    at = at[:MAX_LABELS]
    rows = feature[at]
    out: dict[str, Any] = {
        "at": at.tolist(),
        "text": [str(t)[:MAX_LABEL_CHARS] for t in lab.text.take(rows).to_pylist()],
        "total": total,
    }
    if lab.height is not None:
        out[HEIGHT] = _null(lab.height[rows])
    if lab.rotation is not None:
        out[ROTATION] = _null(lab.rotation[rows])
    return out


def _json_box(box: tuple[float, ...] | None) -> list[float | None] | None:
    return None if box is None else [None if math.isnan(v) else v for v in box]


def part_arrays(lay: Layout, k: int, lod: int) -> tuple[dict[str, Any], list[tuple[str, Any, str]]]:
    """Header fields and arrays of part k at a level (see the module docstring)."""
    if not 0 <= k < len(lay.parts):
        raise part_not_found(k)
    if lod not in range(len(LOD_TOL)):
        raise bad_lod()
    p = lay.parts[k]
    tol = LOD_TOL[lod]
    origin = np.array(p.origin)
    head: dict[str, Any] = {
        "kind": "lines",
        "format": FORMAT,
        **lay.meta,
        "k": k,
        "lod": lod,
        "tol": tol,
        "points_only": lay.line is None,
        "origin": list(p.origin),
        "bbox": _json_box(p.bbox),
        "source_points": lay.part_points[k],
    }
    if lay.line is None or lay.start is None or lay.stop is None or lay.closed is None:
        pts = lay.xyz[p.start : p.stop]
        feature = lay.order[p.start : p.stop]
        if lod:
            keep = _thin(pts, tol)
            pts, feature = pts[keep], feature[keep]
        head.update(n=len(pts), points=len(pts), labels=_labels(lay, feature, None))
        head["simplified"] = len(pts) < p.n
        return head, [("xyz", (pts - origin).astype("<f4"), "<f4"), ("line", feature, "<u4")]
    rows = slice(p.start, p.stop)
    start = lay.start[rows]
    counts = lay.stop[rows] - start
    offsets = np.zeros(p.n + 1, dtype=np.int64)
    np.cumsum(counts, out=offsets[1:])
    pts = lay.xyz[np.repeat(start - offsets[:-1], counts) + np.arange(offsets[-1], dtype=np.int64)]
    closed = lay.closed[rows]
    if lod:
        from bf_core.geo.simplify import compact, keep_mask

        keep = keep_mask(pts, offsets, tol, closed)
        pts, offsets = pts[keep], compact(offsets, keep)
    feature = lay.line[rows]
    head.update(n=p.n, points=len(pts), labels=_labels(lay, feature, None if lay.head is None else lay.head[rows]))
    head["simplified"] = len(pts) < lay.part_points[k]
    return head, [
        ("offsets", offsets, "<u4"),
        ("xyz", (pts - origin).astype("<f4"), "<f4"),
        ("line", feature, "<u4"),
        ("closed", closed, "<u1"),
    ]


def encode_part(lay: Layout, k: int, lod: int) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Part k at a level as the reference decoder gives it (tests, замеры)."""
    return binary.decode(binary.encode(*part_arrays(lay, k, lod)))


# --- the layer of a project ----------------------------------------------------------------------------

_lock = threading.Lock()
_building = threading.Lock()
_cache: OrderedDict[tuple[Any, ...], Layout] = OrderedDict()


def forget() -> None:
    with _lock:
        _cache.clear()


def _read_labels(store: Any, state: LayerState) -> Labels | None:
    """The text of every feature of a layer that has the attribute `text` (only these columns are read)."""
    sha = state.sha("attrs")
    if sha is None or TEXT not in state.fields:
        return None
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    wanted = [name for name in (TEXT, HEIGHT, ROTATION) if name in state.fields]
    table = pq.read_table(store.path_of(sha), columns=wanted)
    text = table.column(TEXT)
    if not pa.types.is_string(text.type):
        return None  # a number field that happens to be called «text»
    text = text.combine_chunks()
    shown = np.asarray(pc.fill_null(pc.greater(pc.utf8_length(text), 0), False))
    if not shown.any():
        return None

    def numbers(name: str) -> np.ndarray | None:
        if name not in wanted or not pa.types.is_floating(table.column(name).type):
            return None
        return np.asarray(table.column(name).to_numpy(), dtype=np.float64)

    return Labels(text, shown, numbers(HEIGHT), numbers(ROTATION))


def _load(project: Any, state: LayerState) -> Layout:
    from bf_core.lines.store import ARRAYS
    from bf_core.storage.blobs import BlobStore

    store = BlobStore(project.folder)

    def array(role: str) -> np.ndarray | None:
        sha = state.sha(role)
        return None if sha is None else np.fromfile(store.path_of(sha), dtype=ARRAYS[role])

    xyz, offsets, closed = array("xyz"), array("offsets"), array("closed")
    if xyz is None or (not state.points_only and (offsets is None or closed is None)):
        raise ApiError(
            500, "line_layer_broken", "Файлы геометрии слоя линий не найдены.", "Загрузите файл линий заново."
        )
    lay = build(xyz, None if state.points_only else offsets, None if state.points_only else closed)
    lay.labels = _read_labels(store, state)
    lay.meta = {"layer": state.layer_id, "version": state.version}
    return lay


def layout(project: Any, state: LayerState) -> Layout:
    """The layout of a layer, kept in memory by the key of its state (the manifest and the parts agree)."""
    key = (project.uid, *state.key())
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    with _building:  # four parts asked at once build the layout once
        with _lock:
            hit = _cache.get(key)
        if hit is not None:
            return hit
        built = _load(project, state)
        with _lock:
            for old in [c for c in _cache if c[:2] == key[:2]]:  # older versions of the layer
                del _cache[old]
            _cache[key] = built
            while len(_cache) > 1 and sum(len(v.xyz) for v in _cache.values()) > CACHE_VERTICES:
                _cache.popitem(last=False)
    return built


def manifest(project: Any, state: LayerState) -> bytes:
    lay = layout(project, state)
    header = {
        "kind": LAYER,
        "format": FORMAT,
        "layer": {"id": state.layer_id, "name": state.name, "file_id": state.file_id},
        "version": state.version,
        "points_only": state.points_only,
        "color": state.color,
        "lines": state.line_count,
        "points": state.point_count,
        "pieces": lay.pieces,
        "bbox": _json_box(lay.bbox),
        "lods": [{"lod": i, "tol": tol} for i, tol in enumerate(LOD_TOL)],
        "max_vertices": MAX_VERTICES,
        "labels": lay.labels is not None,
        "parts": [
            {
                "k": p.k,
                "n": p.n,
                "points": lay.part_points[p.k],
                "bbox": _json_box(p.bbox),
                "origin": list(p.origin),
                "etags": [part_etag(project.uid, state, p.k, lod) for lod in range(len(LOD_TOL))],
            }
            for p in lay.parts
        ],
    }
    return binary.encode(header, [])


def part(project: Any, state: LayerState, k: int, lod: int) -> bytes:
    return binary.encode(*part_arrays(layout(project, state), k, lod))
