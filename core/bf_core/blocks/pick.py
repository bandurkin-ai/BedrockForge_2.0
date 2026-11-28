"""The block under a point: `POST /viz/blocks/{id}/at` (подшаг 17.24; ТЗ 8.7.6, ADR 0023 decision 18,
docs/progress/e17-api.md §12). The layer of the scene finds the face under the cursor from its own index of faces
(`ui/src/viz/blocks/pickIndex.ts`); the core finds the ROW of the file the face belongs to, in float64.

    hit = find(grid, p, s, w, point, normal)          # whole arrays of a model (the cache of columns), ascending P
    hit = find_in_model(model, point, normal)         # a model not in memory: reads one row group of `cells`
    chunk = fetch_row(model, hit, names)              # a one-row `Chunk` (P, s, w and the fields): `restrict` takes it
    body = describe(model, hit, chunk, f)             # the JSON of the answer; `{"found": False}` for hit None

The point is the point on the face the layer gave (`pick.inside` — already moved into the block by a micron — and the
normal of the face); the core moves it by −1e-6 of the smallest sub-cell against the normal (inside the block that
owns the face, whatever the size of the neighbour: a face on a boundary of two parents, of two sub-blocks, of the
sub-cell grid of a turned model). Cells are [lo; hi), so a point exactly on an edge lies in the cell above it
(`geometry.locate`); without a normal the point is not moved. A point in no row (a gap of a sparse model, outside the
grid) gives `found: false`.

`find_in_model` needs the rows of one parent only: the groups of `cells` are ascending by P, so the row groups whose
`idx` statistics hold P are the window (one group, two when the rows of a parent cross a boundary); a file written
without statistics reads whole. The answer of a number is a float (NaN → null, never 0), of a category — its label
(a code outside the labels, −1 → null). `size` is along the axes of the model, `centre` in the project axes.
Errors — 404 `block_field_not_found` for a field that does not exist, 422 `bad_param {param}` for a point.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks import schema as bs
from bf_core.blocks.model import Chunk, Model
from bf_core.blocks.model import P as COL_P
from bf_core.blocks.model import S as COL_S
from bf_core.blocks.model import W as COL_W
from bf_core.blocks.schema import GridV2
from bf_core.blocks.slice import min_subcell

SHIFT = 1e-6  # of the smallest sub-cell: how far the point goes inside the block (decision 18)


@dataclass(frozen=True)
class Hit:
    """The row under a point: its number in the file, P, the parent (i, j, k), the sub-block `(sx sy sz wx wy wz)` or
    None, the centre (project axes), the size along the axes of the model, the point that was looked up."""

    row: int
    p: int
    ijk: tuple[int, int, int]
    sub: tuple[int, ...] | None
    centre: tuple[float, float, float]
    size: tuple[float, float, float]
    point: tuple[float, float, float]
    lc: tuple[float, float, float] | None = None  # a list of blocks: the centre and the size, local axes
    ld: tuple[float, float, float] | None = None


def bad_point(param: str = "point") -> ApiError:
    return ApiError(422, "bad_param", "Точка задана неверно: нужны три конечных числа.",
                    "Задайте координаты x, y, z числами.", {"param": param})  # fmt: skip


def _vec(raw: Any, param: str) -> np.ndarray:
    try:
        v = np.asarray([float(x) for x in raw], dtype=np.float64)
    except (TypeError, ValueError):
        v = np.empty(0)
    if v.shape != (3,) or not np.all(np.isfinite(v)):
        raise bad_point(param)
    return v


def shifted(grid: GridV2, point: Sequence[float], normal: Sequence[float] | None = None, *,
            sub: bool = True) -> np.ndarray:  # fmt: skip
    """The point of the lookup, project axes: `point` − 1e-6·(the smallest sub-cell)·n̂; no (or a zero) normal — the
    point as it is."""
    pt = _vec(point, "point")
    if normal is None:
        return pt
    n = _vec(normal, "normal")
    length = float(np.sqrt(np.dot(n, n)))
    if not length > 0.0:
        return pt
    return pt - (SHIFT * min_subcell(grid, sub) / length) * n


def _t3(a: np.ndarray) -> tuple[float, float, float]:
    return float(a[0]), float(a[1]), float(a[2])


def _hit(grid: GridV2, p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None, local: int, row: int,
         pt: np.ndarray, has_sub: bool) -> Hit:  # fmt: skip
    p1 = np.asarray(p[local : local + 1], dtype=np.uint64)
    s1 = None if s is None else s[local : local + 1]
    w1 = None if w is None else w[local : local + 1]
    i, j, k = (int(a[0]) for a in geometry.row_ijk(grid, p1))
    centre = geometry.centres(grid, p1, s1, w1)[0]
    size = geometry.row_widths(grid, p1, s1, w1)[0]
    sub = None
    if has_sub and s1 is not None and w1 is not None:
        sub = tuple(int(x) for x in (*s1[0], *w1[0]))
    return Hit(row, int(p1[0]), (i, j, k), sub, _t3(centre), _t3(size), _t3(pt))


def row_at(grid: GridV2, p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None, pt: np.ndarray) -> int:
    """The row of the arrays (ascending P) that holds the project point `pt` ([lo; hi) cells), −1 — none. As
    `geometry.locate_rows` for one point, but touches only the rows of the parent (`locate_rows` turns the whole of
    `s` and `w` into int64 on every call — seconds on 20 million rows)."""
    valid, qp, qs = geometry.locate(grid, pt[None, :])
    if not bool(valid[0]):
        return -1
    lo = int(np.searchsorted(p, qp[0], side="left"))
    hi = int(np.searchsorted(p, qp[0], side="right"))
    if hi <= lo:
        return -1
    if s is None or w is None:
        return lo
    at = qs[0].astype(np.int64)
    s0, w0 = s[lo:hi].astype(np.int64), w[lo:hi].astype(np.int64)
    inside = ((at >= s0) & (at < s0 + w0)).all(axis=1)
    return lo + int(np.argmax(inside)) if bool(inside.any()) else -1


def find(grid: GridV2, p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None, point: Sequence[float],
         normal: Sequence[float] | None = None, *, sub: bool = True) -> Hit | None:  # fmt: skip
    """The row of the arrays (ascending by P, `s`, `w` as `Model.iter` gives them) that holds the point; None —
    in no row. `sub` False: the rows are whole parents (a model without sub-blocks)."""
    pt = shifted(grid, point, normal, sub=sub)
    has_sub = bool(sub and s is not None and w is not None)
    row = row_at(grid, p, s if has_sub else None, w if has_sub else None, pt)
    if row < 0:
        return None
    return _hit(grid, p, s, w, row, row, pt, has_sub)


# --- a model that is not in memory --------------------------------------------------------------------------


def _window(model: Model, target: int) -> tuple[int, int] | None:
    """The rows [start, stop) of the row groups of `cells` whose `idx` statistics may hold P = `target`; None — no group
    can (the parent is empty). Without statistics (or a missing column) — every group."""
    import pyarrow.parquet as pq

    md = pq.ParquetFile(model.reader._path(bs.ROLE)).metadata
    try:
        col = md.schema.names.index(bs.IDX)
    except ValueError:
        col = -1
    first = last = None
    off = 0
    for g in range(md.num_row_groups):
        rows = md.row_group(g).num_rows
        stats = md.row_group(g).column(col).statistics if col >= 0 else None
        if stats is None or not stats.has_min_max or (int(stats.min) <= target <= int(stats.max)):
            first = off if first is None else first
            last = off + rows
        off += rows
    return None if first is None or last is None else (first, last)


_WINDOWS: OrderedDict[tuple[str, int, int], tuple[np.ndarray, np.ndarray | None, np.ndarray | None]] = OrderedDict()
_WINDOWS_KEEP = 3  # the clicks of one session are near each other: the same row group (≈ 14 MB of P, s, w)
_LOCK = threading.Lock()


def _rows_of(model: Model, start: int, stop: int) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    """P, s, w of the rows [start, stop) of the file `cells` (the last few windows are kept; the file of a model
    never changes in place — a new file is a new SHA)."""
    key = (model.shas().get(bs.ROLE, ""), start, stop)
    with _LOCK:
        got = _WINDOWS.get(key)
        if got is not None:
            _WINDOWS.move_to_end(key)
            return got
    read = model.reader.fill(p=True, sub=True, start=start, stop=stop)
    got = (read[COL_P], read.get(COL_S), read.get(COL_W))
    if key[0]:
        with _LOCK:
            _WINDOWS[key] = got
            while len(_WINDOWS) > _WINDOWS_KEEP:
                _WINDOWS.popitem(last=False)
    return got


def find_in_model(model: Model, point: Sequence[float], normal: Sequence[float] | None = None) -> Hit | None:
    """`find` on a model read from its files: one row group of P, s, w — not the whole model."""
    grid = model.grid
    pt = shifted(grid, point, normal, sub=model.sub)
    valid, pp, _ = geometry.locate(grid, pt[None, :])
    if not bool(valid[0]):
        return None
    win = _window(model, int(pp[0]))
    if win is None:
        return None
    p, s, w = _rows_of(model, *win)
    row = row_at(grid, p, s, w, pt)
    if row < 0:
        return None
    return _hit(grid, p, s, w, row, win[0] + row, pt, model.sub)


def fields_of(model: Model, fields: Sequence[str] | None) -> list[str]:
    """The stored names of the fields asked for (None — all of the model); 404 `block_field_not_found`."""
    if fields is None:
        return [str(f["name"]) for f in model.fields]
    return model.reader.canonical([str(x) for x in fields])


def _boxes_of(hit: Hit) -> tuple[np.ndarray | None, np.ndarray | None]:
    if hit.lc is None or hit.ld is None:
        return None, None
    return np.array([hit.lc]), np.array([hit.ld])


def fetch_row(model: Model, hit: Hit, names: Sequence[str] = ()) -> Chunk:
    """The row as a one-row `Chunk(start=row, p, s, w, cols)` with the fields `names` in the memory form; what
    `restrict.Restrictor.weights` takes (its `fields` first), so the answer can say `f` and `passed`. Reads the
    fields only: P, s and w are the hit's."""
    cols = {k: v for k, v in model.reader.fill(names, start=hit.row, stop=hit.row + 1).items()} if names else {}
    s = w = None
    if hit.sub is not None:
        s, w = np.array([hit.sub[:3]], dtype=np.uint8), np.array([hit.sub[3:]], dtype=np.uint8)
    lc, ld = _boxes_of(hit)
    return Chunk(hit.row, np.array([hit.p], dtype=np.uint64), s, w, cols, lc, ld)


def chunk_of(hit: Hit, columns: Mapping[str, np.ndarray], names: Sequence[str] | None = None) -> Chunk:
    """The one-row `Chunk` of the row of the hit from whole columns in the memory form (the cache of the route, a
    memmap): `names` — which of them (None — all). The same as `fetch_row`, no file is read."""
    keep = list(columns) if names is None else list(names)
    s = w = None
    if hit.sub is not None:
        s, w = np.array([hit.sub[:3]], dtype=np.uint8), np.array([hit.sub[3:]], dtype=np.uint8)
    cols = {k: np.asarray(columns[k][hit.row : hit.row + 1]) for k in keep}
    lc, ld = _boxes_of(hit)
    return Chunk(hit.row, np.array([hit.p], dtype=np.uint64), s, w, cols, lc, ld)


def value_of(spec: Mapping[str, Any], v: Any) -> float | str | None:
    """A value of the memory form for the JSON: a number — float (NaN → None), a category — its label (−1 → None)."""
    if spec.get("kind") == bs.CATEGORY:
        code = int(v)
        labels = spec.get("labels") or []
        return str(labels[code]) if 0 <= code < len(labels) else None
    x = float(v)
    return None if x != x else x


def describe(model: Model, hit: Hit | None, chunk: Chunk | None = None,
             f: float | None = None) -> dict[str, Any]:  # fmt: skip
    """The body of the answer of `/at`. `chunk` — `fetch_row` (or a one-row `Chunk` of the cache): its `cols` are the
    `values`; `f` — the share of the filter (None — no filter: 1.0, the block passes)."""
    if hit is None:
        return {"found": False}
    share = 1.0 if f is None else float(f)
    values: dict[str, Any] = {}
    if chunk is not None:
        for name, col in chunk.cols.items():
            values[name] = value_of(model.field(name), col[0])
    return {"found": True, "row": hit.row, "p": hit.p, "ijk": list(hit.ijk),
            "sub": None if hit.sub is None else list(hit.sub), "centre": list(hit.centre), "size": list(hit.size),
            "passed": share > 0.0, "f": share, "values": values}  # fmt: skip
