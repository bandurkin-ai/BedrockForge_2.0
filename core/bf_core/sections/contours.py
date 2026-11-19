"""The contours of a series of sections (ТЗ 8.1.3–8.1.4; ADR 0016 §5, §7, §8, решение 3): closed lines of layers,
each on the section whose window holds all its vertices (`geometry.line_sections` — one rule for the filter of the
editor, the shadow and «Каркас по серии»).

    LayerLines                 the closed lines of one version of a layer: xyz (no repeated last vertex), offsets,
                               lid; the numbers of open lines and of closed ones with fewer than 3 vertices
    layer_lines(conn, bs, uid, layer_id)    the current version, cached by (project, layer, rev, blobs)
    lines_of(layer_id, name, rev, lines)    the lines an operation read (`ops/sources.Line`)
    SeriesContours             the contours of several layers on one series: section k of every one (−1 — in no
                               window) and the largest |t| of its vertices from the plane of that section
    series_contours(geom, layers, tick)     → SeriesContours
    cached_contours(conn, bs, uid, series row, layer_ids)   the endpoints: cached by the version of the series
    off_plane_warnings(sc, names)           vertices farther than OFF_PLANE = 1e−3 m from t = 0 (ADR 0016 §8)
    encode(sc, picked, header)              BFA1 `series_contours` (`GET /section-series/{id}/contours`)

A closed line is stored with its first vertex repeated at the end; that vertex is dropped (as `ops/sources.py`),
so the window, the shadow and the operation see the same polygon. A vertex with a coordinate that the depth
does not depend on left empty (NaN) does not move the contour out of its window (`geometry.dot`).

The caches are per process (the API: the shadow and the table of links while the window steps through a
series): up to `CACHE_BYTES` of arrays in both of them, every buffer counted once (the vertices of a series of
several layers are a copy; a series holds its layers), least recently used dropped first; a new `rev` of a layer
or a new version of a series is another key, so nothing stale is served.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.viz import binary

Tick = Callable[[float | None, str | None], None]
OFF_PLANE = 1e-3  # m: a vertex farther from the plane of its section is reported
KIND = "series_contours"
CACHE_BYTES = 256 << 20
CACHE_ENTRIES = 64
MAX_PICKED = 8  # sections in one request of contours


@dataclass(frozen=True, eq=False)
class LayerLines:
    layer_id: int
    name: str
    rev: int
    xyz: np.ndarray  # n×3 float64: the closed lines with at least 3 vertices, no repeated last vertex
    offsets: np.ndarray  # m + 1 int64
    lid: np.ndarray  # m int64
    open_lines: int = 0
    short_lines: int = 0

    @property
    def count(self) -> int:
        return len(self.offsets) - 1

    @property
    def nbytes(self) -> int:
        return int(self.xyz.nbytes + self.offsets.nbytes + self.lid.nbytes)


@dataclass(frozen=True, eq=False)
class SeriesContours:
    xyz: np.ndarray  # n×3 float64
    offsets: np.ndarray  # m + 1 int64
    layer: np.ndarray  # m int64: the layer id
    lid: np.ndarray  # m int64
    section: np.ndarray  # m int64: k, −1 — in no window
    off: np.ndarray  # m float64: the largest |t| from the plane of the section (NaN — none or not finite)
    layers: list[LayerLines] = field(default_factory=list)
    open_lines: int = 0
    short_lines: int = 0

    @property
    def count(self) -> int:
        return len(self.offsets) - 1

    def label(self, i: int) -> str:
        """«линия 12 слоя «Контуры»» — the number of the line as the window shows it (lid + 1)."""
        names = {layer.layer_id: layer.name for layer in self.layers}
        return f"линия {int(self.lid[i]) + 1} слоя «{names.get(int(self.layer[i]), '?')}»"

    def points(self, i: int) -> np.ndarray:
        return self.xyz[self.offsets[i] : self.offsets[i + 1]]


def _ranges(starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Concatenated aranges [starts[i], starts[i] + counts[i])."""
    total = int(counts.sum())
    if total == 0:
        return np.zeros(0, dtype=np.int64)
    owner = np.repeat(np.arange(len(counts)), counts)
    before = np.concatenate([[0], np.cumsum(counts)[:-1]])
    return (starts[owner] + np.arange(total) - before[owner]).astype(np.int64)


def from_stored(layer_id: int, name: str, rev: int, xyz: Any, offsets: Any, closed: Any, lid: Any) -> LayerLines:
    """LayerLines of the storage form: closed lines repeat their first vertex, open ones are counted only."""
    xyz = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    offs = np.asarray(offsets, dtype=np.int64)
    shut = np.asarray(closed).astype(bool)
    counts = np.diff(offs) - 1  # without the repeated vertex
    keep = shut & (counts >= 3)
    starts = offs[:-1][keep]
    kept = counts[keep]
    out_offsets = np.concatenate([[0], np.cumsum(kept)]).astype(np.int64)
    return LayerLines(
        int(layer_id), str(name), int(rev), np.ascontiguousarray(xyz[_ranges(starts, kept)]), out_offsets,
        np.asarray(lid, dtype=np.int64)[keep], int((~shut).sum()), int((shut & (counts < 3)).sum()),
    )  # fmt: skip


def lines_of(layer_id: int, name: str, rev: int, lines: Sequence[Any]) -> LayerLines:
    """LayerLines of the lines of an operation (`ops/sources.Line`: a closed one already without its repeated
    vertex)."""
    closed = [line for line in lines if line.closed and len(line.points) >= 3]
    counts = np.array([len(line.points) for line in closed], dtype=np.int64)
    xyz = np.vstack([line.points for line in closed]) if closed else np.zeros((0, 3))
    return LayerLines(
        int(layer_id), str(name), int(rev), np.ascontiguousarray(xyz, dtype=np.float64),
        np.concatenate([[0], np.cumsum(counts)]).astype(np.int64),
        np.array([line.lid for line in closed], dtype=np.int64),
        sum(1 for line in lines if not line.closed), sum(1 for line in lines if line.closed and len(line.points) < 3),
    )  # fmt: skip


def series_contours(geom: Any, layers: Sequence[LayerLines], tick: Tick | None = None) -> SeriesContours:
    """The contours of `layers` on the sections of `geom` (`SeriesGeometry` or `PolylineSection`)."""
    from .geometry import SeriesGeometry

    if len(layers) == 1:
        xyz = layers[0].xyz  # one layer: its vertices, no copy (the cache counts the buffer once)
    else:
        xyz = np.vstack([layer.xyz for layer in layers]) if layers else np.zeros((0, 3))
    counts = np.concatenate([np.diff(layer.offsets) for layer in layers]) if layers else np.zeros(0, np.int64)
    offsets = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
    layer = np.concatenate([np.full(layer.count, layer.layer_id, dtype=np.int64) for layer in layers]) \
        if layers else np.zeros(0, np.int64)  # fmt: skip
    lid = np.concatenate([layer.lid for layer in layers]) if layers else np.zeros(0, np.int64)
    m = len(counts)
    section = geom.line_sections(xyz, offsets, tick=tick) if m else np.zeros(0, np.int64)
    off = np.full(m, np.nan)
    on = np.flatnonzero(section >= 0)
    if len(on):
        idx = _ranges(offsets[:-1][on], counts[on])
        owner = np.repeat(section[on], counts[on])
        if isinstance(geom, SeriesGeometry):
            t = geom.depth(xyz[idx]) - geom.offs[owner]
        else:
            t = geom.local(xyz[idx])[:, 2]
        starts = np.concatenate([[0], np.cumsum(counts[on])[:-1]])
        off[on] = np.maximum.reduceat(np.abs(t), starts)
    return SeriesContours(
        np.ascontiguousarray(xyz), offsets, layer, lid, section.astype(np.int64), off, list(layers),
        sum(layer.open_lines for layer in layers), sum(layer.short_lines for layer in layers),
    )  # fmt: skip


def off_plane_warnings(sc: SeriesContours, names: list[str], limit: int = 20) -> list[str]:
    """«Разрез 5050N, линия 12 слоя «Контуры»: вершины до 0,0035 м от плоскости разреза (больше 1 мм)»."""
    far = np.flatnonzero((sc.section >= 0) & (sc.off > OFF_PLANE))
    out = []
    for i in far[:limit].tolist():
        far_by = f"{float(sc.off[i]):.4g}".replace(".", ",")
        out.append(
            f"Разрез {names[int(sc.section[i])]}, {sc.label(i)}: вершины до {far_by} м от плоскости разреза "
            "(больше 1 мм)."
        )
    if len(far) > limit:
        out.append(f"Ещё контуров с вершинами дальше 1 мм от плоскости разреза: {len(far) - limit}.")
    return out


# --- the caches of the endpoints ----------------------------------------------------------------------------

_lock = threading.Lock()
_lines: OrderedDict[tuple[Any, ...], LayerLines] = OrderedDict()
_series: OrderedDict[tuple[Any, ...], SeriesContours] = OrderedDict()


def _remember(cache: OrderedDict[tuple[Any, ...], Any], key: tuple[Any, ...], value: Any) -> None:
    """Into `cache`, then the least recently used go until both caches hold at most `CACHE_BYTES` (every buffer
    once: a series refers to its layers, and of one layer shares their vertices) and `CACHE_ENTRIES` each; the
    entry just put stays, even alone over the limit."""
    other = _series if cache is _lines else _lines
    with _lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > CACHE_ENTRIES:
            cache.popitem(last=False)
        while _held() > CACHE_BYTES:
            if len(cache) > 1:
                cache.popitem(last=False)
            elif other:
                other.popitem(last=False)
            else:
                break


def _arrays(v: Any) -> list[np.ndarray]:
    if isinstance(v, LayerLines):
        return [v.xyz, v.offsets, v.lid]
    out = [v.xyz, v.offsets, v.layer, v.lid, v.section, v.off]
    for layer in v.layers:  # held by the series even when `_lines` dropped them
        out += _arrays(layer)
    return out


def _held() -> int:
    """Bytes of the arrays of both caches, every buffer once (a view counts as its base)."""
    seen: dict[int, int] = {}
    for cache in (_lines, _series):
        for v in cache.values():
            for a in _arrays(v):
                while isinstance(a.base, np.ndarray):
                    a = a.base
                seen[id(a)] = a.nbytes
    return sum(seen.values())


def _recall(cache: OrderedDict[tuple[Any, ...], Any], key: tuple[Any, ...]) -> Any:
    with _lock:
        found = cache.get(key)
        if found is not None:
            cache.move_to_end(key)
        return found


def clear_caches() -> None:
    with _lock:
        _lines.clear()
        _series.clear()


def layer_key(conn: Any, uid: str, layer_id: int) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """The key of the current version of a layer and its head; ApiError for a missing layer or a cloud."""
    from bf_core.api.errors import ApiError
    from bf_core.lines import store, versions
    from bf_core.lines.cloud import cloud_meta

    head = versions.layer_head(conn, layer_id)  # 404 line_layer_not_found
    if cloud_meta(head["meta"]) is not None:
        raise ApiError(
            422, "not_lines", f"«{head['name']}» — облако точек, а нужен слой линий.", "Выберите слой контуров."
        )
    shas = store.layer_blobs(conn, layer_id)
    return (uid, int(layer_id), int(head["rev"] or 1), tuple(sorted(shas.items()))), head


def layer_lines(conn: Any, bs: Any, uid: str, layer_id: int) -> LayerLines:
    from bf_core.lines import versions
    from bf_core.lines.assemble import as_lines

    key, head = layer_key(conn, uid, layer_id)
    found = _recall(_lines, key)
    if found is not None:
        return found  # type: ignore[no-any-return]
    _head, ls, lid = versions.read_current(conn, bs, layer_id)
    offsets, closed = as_lines(ls)  # a layer of points: lines of one vertex, open
    lines = from_stored(layer_id, head["name"], key[2], ls.xyz, offsets, closed, lid)
    _remember(_lines, key, lines)
    return lines


def cached_contours(conn: Any, bs: Any, uid: str, series: Any, layer_ids: Sequence[int]) -> SeriesContours:
    """The contours of the layers on the series `series` (its row in `objects`), cached by the version of the
    series and the versions of the layers."""
    from .geometry import series_geometry

    keys = [layer_key(conn, uid, i)[0] for i in layer_ids]
    key = (uid, int(series.id), int(series.version), json.dumps(keys))
    found = _recall(_series, key)
    if found is not None:
        return found  # type: ignore[no-any-return]
    layers = [layer_lines(conn, bs, uid, i) for i in layer_ids]
    sc = series_contours(series_geometry(series.meta if isinstance(series.meta, dict) else {}), layers)
    _remember(_series, key, sc)
    return sc


def picked(sc: SeriesContours, ks: Sequence[int]) -> np.ndarray:
    """The contours on the sections `ks`, in their order in `sc`."""
    return np.flatnonzero(np.isin(sc.section, np.asarray(list(ks), dtype=np.int64)))


def encode(sc: SeriesContours, idx: np.ndarray, header: dict[str, Any]) -> bytes:
    """BFA1 `series_contours`: `xyz <f8` (closed lines, no repeated vertex), `offsets <u4`, `layer <u4`,
    `lid <u4`, `section <u4` (k); the header — {kind, format, lines, points, layers [{id, name, rev}], …}."""
    counts = np.diff(sc.offsets)[idx]
    xyz = sc.xyz[_ranges(sc.offsets[:-1][idx], counts)]
    head = {
        "kind": KIND, "format": binary.FORMAT, **header, "lines": int(len(idx)), "points": int(len(xyz)),
        "layers": [{"id": layer.layer_id, "name": layer.name, "rev": layer.rev} for layer in sc.layers],
    }  # fmt: skip
    return binary.encode(
        head,
        [
            ("xyz", xyz, "<f8"), ("offsets", np.concatenate([[0], np.cumsum(counts)]), "<u4"),
            ("layer", sc.layer[idx], "<u4"), ("lid", sc.lid[idx], "<u4"), ("section", sc.section[idx], "<u4"),
        ],
    )  # fmt: skip
