"""Swath plot of a block model with a turn or sub-blocks, and the filter of the library (ТЗ 7.30, 7.31, 7.35 Д-02;
ADR 0023 decision 21, подшаг 17.10). `swath_blocks` (Э13) reads a regular model as boxes along the axes by the
numbers of its cells and stays as it is; here the rows of a model v2 — (P, s, w) — are read as the columns of
`blocks_cache` (and the sub-cells `s`, `w` kept beside them), their boxes are turned about Z and **split between
the swaths exactly** (the kernels — `swath_sub_kernels.py`):

    open_source(project, model) -> Source               the model (`blocks_cache.Model` or its id) for reading
    bounds(src, f, mask=, category=, keep=, threads=) -> 3×2         the extent of the rows taken along u, v, w
    series(src, f, cuboid, field=, weight=, density=, mask=, category=, keep=, threads=) -> SeriesResult
    block_side(f, src) -> float                         the side of the parent block along the swaths (the width)
    filter_mask(project, model_id, filter_id) -> (bool[rows], warnings)    the filter of the library: f > 0 passes
    fold_selection(mask, codes, keep) -> (codes, keep)  a mask into the selection by categories of `swath_blocks`
    cdf(x, b) -> float                                  the share of a box below a plane (tests)
    warm()

Rule of the swaths. A block of a model without a turn, sub-blocks and FRAC goes whole into the swath of its centre
(Э13, `swath_blocks`). Here a block is cut: its volume is divided between the swaths in proportion to the
intersection of the box with the slab of every swath, so a model of sub-blocks gives, to 1e−9, what the same
model cut into its sub-cells (a regular model) gives, whatever the swaths are, and the sum over the swaths is the
volume of the model. The share of a box below the plane t = s along u: the box is a sum of three uniform terms
t = t₀ + Σ bᵢ·Uᵢ, bᵢ = |u·aᵢ|·dᵢ (aᵢ — the axis of the model, dᵢ — the size of the block along it), so

    share(s) = P(Σ bᵢ·Uᵢ ≤ s − t₀)

— the distribution function of a sum of uniform terms, a piecewise polynomial of degree 3 at most, taken as nested
integrals on the pieces (`_f1`, `_f2`, `_f3`: exact rules on the pieces, no cancellation of terms of the formula
with a small bᵢ). The sides v, w of the cuboid keep the rule of the centre (as the samples and Э13): a block
whose centre is outside takes no part. The tail of the cuboid along u (its custom limits) cuts the box exactly.

The weight of a piece: the volume of the block (Π of the sizes of its box, × the share FRAC when the model has a
field of that role; an empty FRAC — no weight, counted `bad_weight`), the tonnage — the volume × the density,
or 1 (no weight) — × the share of the box in the swath; `count` — the blocks that have a piece in the swath.
The sums are those of Э13: groups of `GROUP` rows from the first row of the model, each by one thread with the
cascade of `weighted.pair_sums`, the groups added pairwise — the same bit for bit with 1, 2, 4, 8 threads.

Selection. A row is taken when the filter passes it (the mask of `filter_mask`, f > 0 — ADR §8 «Применение») and
its category is among the labels chosen; the others are `filtered_out`. A cell without a value is `missing`.

The filter of a library on a model of Э13 (no turn, no sub-blocks): `fold_selection` turns the mask into a
selection by categories that `swath_blocks` takes as it is (the result is that of the blocks selected beforehand
to 1e−12). The mask of a filter is kept (`MASKS`, ≤ 256 МБ, by the files of the model and of what the filter
reads): a turn of the cuboid does not count the filter again.

Errors — `StatsError` `bad_param`; ApiError of `blocks.restrict` (`filter_field_missing`, `filter_rules`) and
`filters.store` (`filter_not_found`) as they are."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import blocks_cache as bc
from .swath import Cuboid, SeriesResult, series_result
from .swath_blocks import GROUP, WEIGHTS, _bad_cells, _run
from .swath_frame import Frame
from .swath_sub_kernels import _BAD_CELL, _BAD_WEIGHT, _FILTERED, _KERNELS, _MISSING, _OUTSIDE, _amps, _cdf
from .weighted import StatsError

MASK_LIMIT = 256 * 1024 * 1024
SUBS_LIMIT = 512 * 1024 * 1024
_NO_ROWS = np.zeros(0, dtype=np.float64)
_NO_SUB = np.zeros((0, 3), dtype=np.uint8)
_NO_SEL = np.zeros(0, dtype=np.bool_)
for _a in (_NO_ROWS, _NO_SUB, _NO_SEL):  # read-only as the columns of `stats.blocks_cache`: one specialization
    _a.setflags(write=False)


def cdf(x: float, b: Sequence[float]) -> float:
    """The share of a box with the amplitudes `b` (three numbers ≥ 0, one at least above 0) whose coordinate is
    ≤ x, x counted from the low end of the box (for tests)."""
    m, c1, c2, c3 = _amps(float(b[0]), float(b[1]), float(b[2]))
    return float(_cdf(float(x), m, c1, c2, c3)) if m else float(x >= 0.0)


# --- the model and the frame -------------------------------------------------------------------------------------


@dataclass
class Source:
    """A model for swath: its rows as the columns of `stats.blocks_cache` (`cached`: idx, fields, codes — in the
    memory of the process, on the disk) and the sub-cells `s`, `w` of its rows (`SUBS`)."""

    project: Any
    model: Any  # blocks.model.Model — the grid, the labels
    cached: Any  # blocks_cache.Model — the files
    frac: str | None  # the field with the role `frac`

    @property
    def grid(self) -> Any:
        return self.model.grid

    def columns(self, names: Sequence[str]) -> Any:
        """`blocks_cache.ModelColumns` of the fields `names` (stored names)."""
        return bc.columns(self.project, self.cached, list(dict.fromkeys(names)))

    def subs(self) -> tuple[np.ndarray, np.ndarray]:
        """(s, w) uint8 [rows, 3], read-only; empty — a model without sub-blocks (kept by the file of cells)."""
        if not self.model.sub:
            return _NO_SUB, _NO_SUB
        key = (self.project.uid, self.cached.id, self.cached.sha)
        with _SUBS_LOCK:
            hit = SUBS.get(key)
            if hit is None:
                s, w = self.model.subs()  # type: ignore[misc]
                for a in (s, w):
                    a.setflags(write=False)
                for k in [k for k in SUBS if k[0] != key[0] or k[1] == key[1]]:
                    del SUBS[k]
                SUBS[key] = hit = (s, w)
                while len(SUBS) > 1 and sum(a.nbytes + b.nbytes for a, b in SUBS.values()) > SUBS_LIMIT:
                    SUBS.popitem(last=False)
            SUBS.move_to_end(key)
            return hit


SUBS: OrderedDict[tuple[Any, ...], tuple[np.ndarray, np.ndarray]] = OrderedDict()
_SUBS_LOCK = threading.Lock()


def open_source(project: Any, model: Any) -> Source:
    """The model for reading: `blocks_cache.Model` (plain or not) or its id; 404 `block_model_not_found`, 409
    `block_model_broken`."""
    from bf_core.blocks.model import Model
    from bf_core.db import dialect
    from bf_core.storage.blobs import BlobStore

    with dialect.read_tx(project.engine) as conn:
        cached = bc.model_of(conn, int(model)) if isinstance(model, int) else model
        m = Model.open(conn, BlobStore(project.folder), cached.id)
    fr = m.role_field("frac")
    return Source(project, m, cached, None if fr is None else str(fr["name"]))


@dataclass
class _Geo:
    g: np.ndarray  # corner (3), cos, sin, size (3), div (3), 1/div (3)
    ni: np.ndarray  # nx, ny, nz, variable step, has sub-blocks
    edges: np.ndarray
    steps: np.ndarray
    ax: np.ndarray  # the axes of the frame, rows u, v, w
    amp: np.ndarray  # amp[e, i] = e · (axis i of the model): the extent of a box of size d along e is Σ |amp·d|
    cells: int
    kind: str  # `plain`, `sub` (sub-blocks), `tensors`: the kernels of `_KERNELS`


def _geo(src: Source, f: Frame) -> _Geo:
    grid = src.grid
    sin, cos = grid.sin_cos()  # (sin φ, cos φ)
    nx, ny, nz = grid.counts
    tens = grid.tensors is not None
    if tens:
        n = max(grid.counts)
        edges, steps = np.zeros((3, n + 1)), np.zeros((3, n))
        for a in range(3):
            ed = np.asarray(grid.edges(a), dtype=np.float64)
            edges[a, : ed.size], steps[a, : ed.size - 1] = ed, np.asarray(grid.steps(a), dtype=np.float64)
        size: Sequence[float] = (1.0, 1.0, 1.0)
        div: Sequence[float] = (1.0, 1.0, 1.0)
    else:
        edges, steps = np.zeros((3, 2)), np.zeros((3, 1))
        size, div = grid.regular(), [float(x) for x in grid.sub.div]
    g = np.array([*grid.corner, cos, sin, *size, *div, *(1.0 / d for d in div)], dtype=np.float64)
    ni = np.array([nx, ny, nz, int(tens), int(bool(src.model.sub))], dtype=np.int64)
    ax = np.ascontiguousarray(f.axes, dtype=np.float64)
    amp = np.empty((3, 3))
    for r in range(3):
        amp[r] = (ax[r, 0] * cos + ax[r, 1] * sin, -ax[r, 0] * sin + ax[r, 1] * cos, ax[r, 2])
    kind = "tensors" if tens else "sub" if src.model.sub else "plain"
    return _Geo(g, ni, edges, steps, ax, amp, int(nx) * int(ny) * int(nz), kind)


def block_side(f: Frame, src: Source) -> float:
    """The side of the parent block along the axis of the model nearest to the swaths (as `swath_frame.block_side`,
    the turn of the model counted); a variable step — the median of the steps."""
    grid = src.grid
    if grid.tensors is not None:
        sides = [float(np.median(np.asarray(t, dtype=np.float64))) for t in grid.tensors]
    else:
        sides = [float(x) for x in grid.regular()]
    e = np.abs(_geo(src, f).amp[0])
    near = e >= e.max() - 1e-12
    return max(s for s, yes in zip(sides, near, strict=True) if yes)


def _selection(codes: Any, keep: Any, mask: Any) -> np.ndarray | None:
    """bool [rows], read-only: the rows taken (the mask of the filter, the labels chosen); None — all."""
    sel = mask
    if codes is not None and keep is not None:
        flags = np.asarray(keep, dtype=np.bool_)
        ok = (codes >= 0) & flags[np.clip(codes, 0, max(len(flags) - 1, 0))]
        sel = ok if sel is None else (sel & ok)
    if sel is not None and sel.flags.writeable:
        sel.setflags(write=False)
    return sel  # type: ignore[no-any-return]


def bounds(src: Source, f: Frame, *, mask: Any = None, category: str | None = None, keep: Any = None,
           threads: int = 1) -> np.ndarray:  # fmt: skip
    """The extent of the rows taken along u, v, w (rows (min, max); NaN — no rows): what `swath.cuboid` takes as
    `models`. A block reaches half of its extent from its centre — the box of the row turned, not its centre."""
    geo, cols = _geo(src, f), src.columns([category] if category else [])
    p, (s, w) = cols.idx, src.subs()
    sel = _selection(cols.codes(category) if category else None, keep, mask)
    out, taken = np.column_stack([np.full(3, np.inf), np.full(3, -np.inf)]), 0

    extent_kernel = _KERNELS[geo.kind][0]

    def part(a: int) -> Any:
        b = a + GROUP
        return extent_kernel(p[a:b], s[a:b] if s.size else s, w[a:b] if w.size else w,
                             _NO_SEL if sel is None else sel[a:b], sel is not None, geo.g, geo.ni, geo.edges,
                             geo.steps, geo.ax, geo.amp, geo.cells)  # fmt: skip

    def fold(res: Any) -> None:
        nonlocal taken
        ext, n, bad = res
        if bad:
            raise _bad_cells()
        np.minimum(out[:, 0], ext[:, 0], out=out[:, 0])
        np.maximum(out[:, 1], ext[:, 1], out=out[:, 1])
        taken += int(n)

    _run(part, range(0, cols.n, GROUP), max(1, int(threads)), fold)
    return out if taken else np.full((3, 2), np.nan)


def series(src: Source, f: Frame, cuboid: Cuboid, *, field: str, weight: str = "volume", density: str | None = None,
           mask: Any = None, category: str | None = None, keep: Any = None,
           threads: int = 1) -> SeriesResult:  # fmt: skip
    """A series of the model by swath (module docstring): `field` — the values, `density` — the field of the
    density (the tonnage), `mask` — the filter of the library (bool [rows]), `category` and `keep` — the labels."""
    if weight not in WEIGHTS:
        raise StatsError("bad_param", f"Вес блоков «{weight}» неизвестен.",
                         "Допустимо: volume — объём, tonnage — тоннаж (объём × плотность), none — без веса.",
                         param="weight")  # fmt: skip
    if weight == "tonnage" and density is None:
        raise StatsError("bad_param", "Для веса по тоннажу не задано поле плотности.",
                         "Выберите поле плотности модели или вес «объём».", param="density")  # fmt: skip
    mode, geo = WEIGHTS.index(weight), _geo(src, f)
    lim = np.ascontiguousarray(cuboid.limits, dtype=np.float64)
    tol = np.ascontiguousarray(cuboid.tol, dtype=np.float64)
    start, width, n = float(cuboid.start), float(cuboid.width), int(cuboid.count)
    frac = src.frac if mode != 2 else None
    dens = density if mode == 1 else None
    cols = src.columns([x for x in (field, dens, frac, category) if x])
    p, (s, w) = cols.idx, src.subs()
    v = cols.number(field)
    d = cols.number(dens) if dens else _NO_ROWS
    fr = cols.number(frac) if frac else _NO_ROWS
    sel = _selection(cols.codes(category) if category else None, keep, mask)
    stack: list[tuple[int, np.ndarray, np.ndarray, np.ndarray]] = []  # the binary counter of the groups
    cnt = np.zeros(5, dtype=np.int64)

    sums_kernel = _KERNELS[geo.kind][1]

    def part(a: int) -> Any:
        b = a + GROUP
        return sums_kernel(p[a:b], s[a:b] if s.size else s, w[a:b] if w.size else w, v[a:b],
                           d[a:b] if d.size else d, fr[a:b] if fr.size else fr,
                           _NO_SEL if sel is None else sel[a:b], sel is not None, bool(frac), mode, geo.g, geo.ni,
                           geo.edges, geo.steps, geo.ax, geo.amp, geo.cells, lim, tol, start, width, n)  # fmt: skip

    def fold(res: Any) -> None:
        wt, ws, c, k = res
        if k[_BAD_CELL]:
            raise _bad_cells()
        cnt[:] += k
        level = 0
        while stack and stack[-1][0] == level:
            _, w0, s0, c0 = stack.pop()
            wt, ws, c = w0 + wt, s0 + ws, c0 + c
            level += 1
        stack.append((level, wt, ws, c))

    _run(part, range(0, cols.n, GROUP), max(1, int(threads)), fold)
    if stack:
        _, wt, ws, c = stack.pop()
        while stack:
            _, w0, s0, c0 = stack.pop()
            wt, ws, c = w0 + wt, s0 + ws, c0 + c
    else:
        wt, ws, c = np.zeros(n), np.zeros(n), np.zeros(n, dtype=np.int64)
    left, rows = [int(x) for x in cnt], cols.n
    counts = {"rows": rows, "filtered_out": left[_FILTERED], "missing": left[_MISSING],
              "bad_weight": left[_BAD_WEIGHT], "selected": rows - left[_FILTERED] - left[_MISSING] - left[_BAD_WEIGHT],
              "outside_box": left[_OUTSIDE]}  # fmt: skip
    return series_result(wt, ws, c, counts)


# --- the filter of the library ----------------------------------------------------------------------------------

MASKS: OrderedDict[tuple[Any, ...], tuple[np.ndarray, list[str]]] = OrderedDict()
_MASK_LOCK = threading.Lock()
_KEY_LOCKS: dict[tuple[Any, ...], threading.Lock] = {}  # a mask being made: the lock of its key


def filter_mask(project: Any, model_id: int, filter_id: int) -> tuple[np.ndarray, list[str]]:
    """The filter `filter_id` of the library on the model: bool [rows] in the order of the file (f > 0 passes,
    ADR 0023 §8), read-only, and the warnings of its leaves. 404 `filter_not_found`; 422 `filter_field_missing`,
    `filter_rules` (`blocks.restrict`)."""
    from bf_core.blocks import calc_job, restrict
    from bf_core.blocks.filters import store as filters
    from bf_core.blocks.model import Model
    from bf_core.db import dialect
    from bf_core.storage.blobs import BlobStore

    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        model = Model.open(conn, store, model_id)
        rules = filters.rules_of(conn, filter_id)
        r = restrict.prepare(model, rules, conn=conn, store=store)
        key = (project.uid, model_id, model.shas().get("cells"),
               *calc_job.inputs_of(conn, model, r.fields, rules, None))  # fmt: skip
    with _MASK_LOCK:  # only the table: a mask is made under the lock of its own key
        hit = MASKS.get(key)
        if hit is not None:
            MASKS.move_to_end(key)
            return hit
        key_lock = _KEY_LOCKS.setdefault(key, threading.Lock())
    with key_lock:
        with _MASK_LOCK:
            hit = MASKS.get(key)  # made by the thread that held the key lock before
            if hit is not None:
                MASKS.move_to_end(key)
                return hit
        try:
            out = np.empty(model.rows, dtype=np.bool_)
            for ch, share in r.iter(model):
                out[ch.start : ch.start + ch.n] = share > 0.0
            out.setflags(write=False)
            with _MASK_LOCK:
                for k in [k for k in MASKS if k[0] != key[0] or (k[1] == key[1] and k[2] != key[2])]:
                    del MASKS[k]  # another project, an older file of the model
                MASKS[key] = (out, list(r.warnings))
                while len(MASKS) > 1 and sum(m.nbytes for m, _ in MASKS.values()) > MASK_LIMIT:
                    MASKS.popitem(last=False)
                return MASKS[key]
        finally:
            with _MASK_LOCK:
                _KEY_LOCKS.pop(key, None)


def fold_selection(mask: np.ndarray, codes: Any, keep: Any) -> tuple[np.ndarray, np.ndarray]:
    """The mask of a filter as the selection by categories that `swath_blocks` takes: the codes of the rows the
    filter rejects are −1 (empty, counted `filtered_out`); without a category — one label that every row takes."""
    if codes is None:
        out = np.subtract(mask, 1, dtype=np.int32)  # passes: 0, rejected: −1
        keep = np.array([True])
    else:
        out = np.where(mask, np.asarray(codes, dtype=np.int32), np.int32(-1))
    out.setflags(write=False)
    return out, np.asarray(keep, dtype=np.bool_)


def warm() -> None:
    """Compile the kernels on a few rows, read-only as `stats.blocks_cache` and `filter_mask` give the columns
    (`idx` is uint32: a model has at most 2·10⁹ cells): the extent and the series (the three weights) of the three
    kinds of grid, the share of a box."""
    from . import swath_frame as sf
    from .swath import cuboid
    from .warm import frozen

    n = 5
    s, w = frozen(np.zeros((n, 3), dtype=np.uint8)), frozen(np.ones((n, 3), dtype=np.uint8))
    g = np.array([0.0, 0.0, 0.0, 1.0, 0.0, 5.0, 5.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    ni = np.array([3, 2, 1, 0, 1], dtype=np.int64)
    edges, steps = np.zeros((3, 4)), np.ones((3, 3))
    fr = sf.frame(30.0, -10.0, 5.0)
    ax = np.ascontiguousarray(fr.axes)
    sel = frozen(np.array([True, True, False, True, True]))
    values, ones = frozen([1.0, 2.0, np.nan, 4.0, 5.0]), frozen(np.ones(n))
    p = frozen(np.arange(n, dtype=np.uint32))
    for kind, (extent, sums) in _KERNELS.items():
        sub = (s, w) if kind == "sub" else (_NO_SUB, _NO_SUB)
        ext = extent(p, *sub, sel, True, g, ni, edges, steps, ax, ax.copy(), 6)[0]
        box = cuboid(fr, models=[ext], width=2.0)
        lim, tol = np.ascontiguousarray(box.limits), np.ascontiguousarray(box.tol)
        for mode in (0, 1, 2):
            sums(p, *sub, values, ones, ones, sel, True, True, mode, g, ni, edges, steps, ax, ax.copy(), 6, lim, tol,
                 box.start, box.width, box.count)  # fmt: skip
    cdf(1.0, (1.0, 2.0, 3.0))
    cdf(1.0, (1.0, 2.0, 0.01))
