"""The display of a list of blocks (ADR 0030 decision 8; Д-45 А6): BFA1 `blocks` of a model that has no grid of parents
(`viz_data.shell_bytes` calls it for `model.listed`).

    v = view(grid, idx, lc, ld, passed, klass, limit=…, shell=True, kind="mean", values=None, klass_of=None)

Six faces of every block that passes (`shell=False` — «все блоки»), the faces that two blocks share — the high face of
one and the low face of another with the same plane and the same extents, to a micron — are hidden in both (`shell`;
a hair of a difference only leaves a face drawn: a visible face is never lost); no merge of the faces. The decision is
taken before the faces are built, as `lod.view`: above `limit` faces the blocks are coarsened to cells of 2^ℓ cells of
the enclosing grid (the class of a coarse cell is the mean over the volume of its blocks, a category — the mode), the
shell of that regular grid is built and merged (`View.simplified`). `ListRows` are the columns of a model read once for
one request (the cache of columns of `stats/blocks_cache` has no place for the boxes); `at_answer` is the block under a
point (`POST /viz/blocks/{id}/at`): the cells of the point ± 1 are looked up in the row groups whose `idx` statistics
hold them, and the box that holds the point (cells are [lo; hi)) is the answer.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.blocks import list_kernels as lk
from bf_core.blocks import pick, restrict
from bf_core.blocks import schema as bs
from bf_core.blocks.lod import View, coarse_grid
from bf_core.blocks.lod import _mean as lod_mean  # noqa: PLC2701
from bf_core.blocks.lod import _mode as lod_mode  # noqa: PLC2701
from bf_core.blocks.model import CHUNK, LC, LD, Chunk, Model
from bf_core.blocks.model import P as COL_P
from bf_core.blocks.schema import GridV2
from bf_core.blocks.shell import Faces, count_faces, exposed, face_mask
from bf_core.blocks.shell_merge import merge_faces
from bf_core.blocks.viz_data import Rows
from bf_core.blocks.viz_spec import Token
from bf_core.composites.strips import Tick

_ZERO_G = np.zeros((1, 5), dtype=np.int64)


class ListRows(Rows):
    """The columns of a list of blocks for one request: `idx`, the boxes `lc`, `ld`, the fields asked for."""

    def __init__(self, project: Any, model: Model, r: restrict.Restrictor, names: Sequence[str] = ()) -> None:
        self.model, self.r = model, r
        want = list(dict.fromkeys([*r.fields, *model.reader.canonical(names)]))
        got = model.reader.fill(want, p=True, geo=True)
        self.idx, self.lc, self.ld = got[COL_P], got[LC], got[LD]
        self.s = self.w = None
        self._cols = {n: got[n] for n in want}

    def values(self, name: str) -> np.ndarray:
        return self._cols[name]  # type: ignore[no-any-return]

    def chunk(self, lo: int, hi: int, names: Sequence[str]) -> Chunk:
        sl = slice(lo, hi)
        return Chunk(lo, self.idx[sl], None, None, {n: self._cols[n][sl] for n in names}, self.lc[sl], self.ld[sl])

    def passed_all(self, tok: Token, box: Sequence[float] | None = None) -> np.ndarray:
        """The mask of all the rows; `box` — project (xmin, ymin, zmin, xmax, ymax, zmax) or a rectangle: the centres
        inside only."""
        from bf_core.blocks import geometry

        n = self.model.rows
        out = np.empty(n, dtype=np.bool_)
        b = None if box is None else geometry._box(list(box))  # noqa: SLF001
        grid = self.model.grid
        for lo in range(0, n, CHUNK):
            tok.check()
            hi = min(lo + CHUNK, n)
            out[lo:hi] = self.passed(lo, hi)
            if b is not None and out[lo:hi].any():
                c = grid.to_project(self.lc[lo:hi])
                out[lo:hi] &= ((c[:, 0] >= b[0]) & (c[:, 0] <= b[3]) & (c[:, 1] >= b[1]) & (c[:, 1] <= b[4])
                               & (c[:, 2] >= b[2]) & (c[:, 2] <= b[5]))  # fmt: skip
        return out


# --- the faces ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ListView(View):
    """`lod.View` with the extent of the boxes the faces belong to (the answer takes its `origin` from it: no pass over
    millions of faces); None — the extent of the faces (the coarse shell)."""

    extent: tuple[np.ndarray, np.ndarray] | None = None


def hidden(c: np.ndarray, d: np.ndarray) -> np.ndarray:
    """uint8 [n]: the bits (2·axis + side) of the faces a neighbour shares, for the blocks (centres `c`, sizes `d`)."""
    n = len(c)
    hide = np.zeros(n, dtype=np.uint8)
    if n < 2:
        return hide
    hl, hh = np.empty(3 * n, dtype=np.int64), np.empty(3 * n, dtype=np.int64)
    lk.face_hashes(c, d, hl, hh)
    lk.hide_faces(c, d, hl, hh, hide)
    return hide


def _sorted(fc: np.ndarray, fh: np.ndarray, fa: np.ndarray, fk: np.ndarray, fp: np.ndarray,
            row: np.ndarray) -> Faces:  # fmt: skip
    """The faces by (side, plane): the faces of one plane are one run — the index of a click needs it."""
    m = len(fa)
    if m:
        q = np.rint(fp * lk.QUANT)
        span = float(q.max() - q.min())
        if span < float(1 << 40):
            key = (fa.astype(np.int64) << 40) | (q - q.min()).astype(np.int64)
            pos = np.argsort(key)  # the order inside a plane does not matter
        else:
            pos = np.lexsort((fp, fa))
        fc, fh, fa, fk, row = fc[pos], fh[pos], fa[pos], fk[pos], row[pos]
    return Faces(c=fc, h=fh, a=fa, k=fk, row=row, g=np.broadcast_to(_ZERO_G, (m, 5)))


def faces(c: np.ndarray, d: np.ndarray, klass: np.ndarray, hide: np.ndarray, starts: np.ndarray) -> Faces:
    m = int(starts[-1])
    fc, fh = np.empty((m, 3)), np.empty((m, 2))
    fa, fk, fp = np.empty(m, dtype=np.uint8), np.empty(m, dtype=np.uint16), np.empty(m)
    lk.build_faces(c, d, klass, hide, starts, fc, fh, fa, fk, fp)
    row = np.repeat(np.arange(len(c), dtype=np.int64), np.diff(starts))
    return _sorted(fc, fh, fa, fk, fp, row)


def coarsen(grid: GridV2, idx: np.ndarray, vol: np.ndarray, klass: np.ndarray, level: int, *, kind: str,
            values: np.ndarray | None,
            klass_of: Callable[[np.ndarray], Any] | None) -> tuple[GridV2, np.ndarray, np.ndarray]:  # fmt: skip
    """The blocks as cells of 2^level cells of the enclosing grid a side: (the coarse grid, the cells, their classes);
    the weight of a block is its volume."""
    new = coarse_grid(grid, level)
    nx, ny = np.uint64(grid.counts[0]), np.uint64(grid.counts[1])
    i, j, k = idx % nx, (idx // nx) % ny, idx // (nx * ny)
    sh, cx, cy = np.uint64(level), np.uint64(new.counts[0]), np.uint64(new.counts[1])
    cell = (i >> sh) + cx * ((j >> sh) + cy * (k >> sh))
    if kind == "mode":
        cells, cls = lod_mode(cell, klass, vol)
    else:
        cells, cls = lod_mean(cell, klass, vol, values, klass_of)
    return new, cells, cls


def view(grid: GridV2, idx: np.ndarray, lc: np.ndarray, ld: np.ndarray, passed: np.ndarray,
         klass: np.ndarray | None = None, *, limit: int = 5_000_000, shell: bool = True, kind: str = "mean",
         values: np.ndarray | None = None, klass_of: Callable[[np.ndarray], Any] | None = None, threads: int = 1,
         tick: Tick | None = None) -> View:  # fmt: skip
    """The faces to draw (module docstring)."""
    from bf_core.blocks.lod import _first_level

    sel = np.flatnonzero(passed)
    c, d = np.ascontiguousarray(lc[sel]), np.ascontiguousarray(ld[sel])
    k = np.zeros(len(sel), dtype=np.uint16) if klass is None else np.ascontiguousarray(klass[sel], dtype=np.uint16)
    hide = hidden(c, d) if shell else np.zeros(len(sel), dtype=np.uint8)
    starts = np.empty(len(sel) + 1, dtype=np.int64)
    raw = int(lk.face_starts(hide, starts))
    if raw <= limit:
        box = (np.min(c - d / 2, axis=0), np.max(c + d / 2, axis=0)) if len(c) else None
        return ListView(faces(c, d, k, hide, starts), raw, raw, 0, False, limit, box)
    vol = (d[:, 0] * d[:, 1]) * d[:, 2]
    ii = np.ascontiguousarray(idx[sel])
    vv = None if values is None else np.asarray(values, dtype=np.float64)[sel]
    top = max(grid.counts).bit_length()
    level = min(_first_level(raw, limit, shell), top)
    while True:
        new, cells, cls = coarsen(grid, ii, vol, k, level, kind=kind, values=vv, klass_of=klass_of)
        ones = np.ones(cells.shape[0], dtype=bool)
        if level >= top or count_faces(face_mask(new, cells, None, None, ones, shell=shell)) <= limit:
            break
        level += 1
    f = exposed(new, cells, None, None, ones, cls, shell=shell, threads=threads, tick=tick)
    return View(merge_faces(new, f), raw, len(f), level, True, limit)


# --- the block under a point ---------------------------------------------------------------------------------

_WINDOWS: OrderedDict[tuple[str, int, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = OrderedDict()
_KEEP = 4  # the clicks of one session are near each other: the same few row groups
_LOCK = threading.Lock()


def _group_rows(model: Model) -> list[tuple[int, int, int, int]]:
    """(first row, rows, least idx, greatest idx) of the row groups of `cells` (statistics; without them — any idx)."""
    import pyarrow.parquet as pq

    md = pq.ParquetFile(model.reader._path(bs.ROLE)).metadata  # noqa: SLF001
    col = md.schema.names.index(bs.IDX)
    out, off = [], 0
    for g in range(md.num_row_groups):
        rg = md.row_group(g)
        st = rg.column(col).statistics
        lo, hi = (int(st.min), int(st.max)) if st is not None and st.has_min_max else (0, 1 << 62)
        out.append((off, rg.num_rows, lo, hi))
        off += rg.num_rows
    return out


def _window(model: Model, start: int, stop: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    key = (model.shas().get(bs.ROLE, ""), start, stop)
    with _LOCK:
        got = _WINDOWS.get(key)
        if got is not None:
            _WINDOWS.move_to_end(key)
            return got
    read = model.reader.fill(p=True, geo=True, start=start, stop=stop)
    got = (read[COL_P], read[LC], read[LD])
    if key[0]:
        with _LOCK:
            _WINDOWS[key] = got
            while len(_WINDOWS) > _KEEP:
                _WINDOWS.popitem(last=False)
    return got


def find(model: Model, point: Sequence[float], normal: Sequence[float] | None = None) -> pick.Hit | None:
    """The block of the list that holds the project point (moved a hair inside along the normal; cells are [lo; hi)),
    the lowest row if several do; None — in no block."""
    grid = model.grid
    pt = pick.shifted(grid, point, normal, sub=False)
    q = grid.to_local(pt[None, :])[0]
    size = np.asarray(grid.regular(), dtype=np.float64)
    counts = np.asarray(grid.counts, dtype=np.int64)
    home = np.floor(q / size).astype(np.int64)
    ranges = [np.arange(max(int(home[a]) - 1, 0), min(int(home[a]) + 1, int(counts[a]) - 1) + 1) for a in range(3)]
    if any(len(r) == 0 for r in ranges):
        return None
    zz, yy, xx = np.meshgrid(ranges[2], ranges[1], ranges[0], indexing="ij")
    targets = np.sort((xx + counts[0] * (yy + counts[1] * zz)).reshape(-1)).astype(np.uint64)
    for first, rows, lo, hi in _group_rows(model):
        if not ((targets >= lo) & (targets <= hi)).any():
            continue
        idx, lc, ld = _window(model, first, first + rows)
        for t in targets[(targets >= lo) & (targets <= hi)]:
            a, b = int(np.searchsorted(idx, t, side="left")), int(np.searchsorted(idx, t, side="right"))
            if b <= a:
                continue
            c, d = lc[a:b], ld[a:b]
            inside = ((q >= c - d / 2) & (q < c + d / 2)).all(axis=1)
            if inside.any():
                at = a + int(np.argmax(inside))
                nx, ny = int(counts[0]), int(counts[1])
                p = int(idx[at])
                ijk = (p % nx, (p // nx) % ny, p // (nx * ny))
                centre = grid.to_project(lc[at : at + 1])[0]
                return pick.Hit(
                    first + at,
                    p,
                    ijk,
                    None,
                    (float(centre[0]), float(centre[1]), float(centre[2])),
                    (float(ld[at, 0]), float(ld[at, 1]), float(ld[at, 2])),
                    (float(pt[0]), float(pt[1]), float(pt[2])),
                    (float(lc[at, 0]), float(lc[at, 1]), float(lc[at, 2])),
                    (float(ld[at, 0]), float(ld[at, 1]), float(ld[at, 2])),
                )
    return None


def at_answer(model: Model, r: restrict.Restrictor, raw_fields: list[str] | None, point: Any,
              normal: Any) -> dict[str, Any]:  # fmt: skip
    """The body of the answer of `/at` for a list of blocks (`pick.describe`)."""
    hit = find(model, point, normal)
    if hit is None:
        return {"found": False}
    names = pick.fields_of(model, raw_fields)
    need = list(dict.fromkeys([*r.fields, *names]))
    ch = pick.fetch_row(model, hit, need)
    f = None
    if r.fields or r._fn is not None:  # noqa: SLF001
        f = float(r.weights(Chunk(ch.start, ch.p, None, None, {n: ch.cols[n] for n in r.fields}, ch.lc, ch.ld))[0])
    return pick.describe(model, hit, Chunk(ch.start, ch.p, None, None, {n: ch.cols[n] for n in names}), f)
