"""LOD of the block shell and the whole view: faces → limit → merge (подшаг 17.23; ADR 0023 §11, decision 17;
ТЗ 8.7.6: «выше лимита — уровни 2×2×2, 4×4×4 … родителей: среднее по объёму, категория — мода»).

    v = view(grid, p, s, w, passed, klass, limit=5_000_000, shell=True, merge=True, kind="mean",
             values=None, klass_of=None, threads=1, tick=None) -> View
    c = coarsen(grid, p, s, w, passed, klass, level, kind="mean", values=None, klass_of=None) -> Coarse
    g = coarse_grid(grid, level) -> GridV2                      the grid of the cells of 2^level parents a side

The input is that of `shell.exposed` (rows of a model: `p`, `s`, `w`, `passed`, `klass`). The decision is taken
BEFORE the faces are built (≈ 91 bytes a face): `face_mask` + `count_faces` give the number of faces of the shell;
up to `limit` the faces are built and merged, above it the rows are coarsened to cells of 2^ℓ parents a side
(ℓ = 1, 2, …; the first guess from the ratio of the counts, then +1 until the count of the coarse shell is within
the limit, or the whole model is one cell) and the shell of the coarse model is built — it has no more than
`limit` faces, merged or not. `View.simplified` — ℓ > 0; `faces_raw` — the count of the shell before LOD and
merge (the header of BFA1 `blocks`), `faces_built` — before the merge.

A coarse cell. It is drawn when any of its rows passes; it holds only the rows that pass. Its class:
  · `kind="mean"` — the mean over the volume of the class (the rows without a class, `NO_CLASS`, do not count;
    rounded half up), or — with `values` (float64 per row of the input, NaN = missing) and `klass_of` (values →
    classes, e.g. `lambda v: scale_class(v, lo, hi)`) — the class of the mean over the volume of the VALUES (NaN
    does not count; a cell without a value gets what `klass_of` gives for NaN);
  · `kind="mode"` (categories) — the class that takes the most volume; equal volumes — the lower code.
The weight of a row is its volume (the sub-block `Π w`; a variable step — the volume of the parent): in the
regular grid whole numbers, so the sums are exact and do not depend on the order of the rows. The coarse grid has
the same corner, rotation and edges (the last cell of an axis is cut where the model ends), so its faces lie in
the same local axes; their `row` is the row of the COARSE input and means nothing for a pick of a block.
Errors — `block_model_invalid` 422 {code, message, hint} in Russian.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.blocks.schema import GridV2, SubblockSpec, bad_model
from bf_core.blocks.shell import (
    _POP,
    STRIP_ROWS,
    Faces,
    _axes,
    _fill,
    _mask_sorted,
    _sort_key,
    _starts,
    count_faces,
    exposed,
    face_mask,
)
from bf_core.blocks.shell_merge import NO_CLASS, merge_faces
from bf_core.composites import strips
from bf_core.composites.strips import Tick

DEFAULT_LIMIT = 5_000_000  # ТЗ 6.8; the setting is 0.5–10·10⁶


@dataclass(frozen=True)
class Coarse:
    """The rows of a coarsened model: `p` — the cells (P of the coarse grid, ascending), `klass` — their classes."""

    grid: GridV2
    p: np.ndarray  # uint64 [m]
    klass: np.ndarray  # uint16 [m]
    level: int


@dataclass(frozen=True)
class View:
    """What the viewer draws: the faces and how they were got (module docstring)."""

    faces: Faces
    faces_raw: int  # the shell before LOD and merge
    faces_built: int  # built (after LOD, before merge)
    lod: int  # ℓ: 0 — the model as it is
    simplified: bool
    limit: int


def coarse_grid(grid: GridV2, level: int) -> GridV2:
    """The grid of the cells of 2^level parents a side: counts ⌈n / 2^ℓ⌉, no sub-blocks; the edges are those of
    the model (the regular step is multiplied, not summed; the last cell is cut where the model ends)."""
    f = 1 << level
    counts = tuple((n + f - 1) // f for n in grid.counts)
    steps: list[np.ndarray] = []
    for a in range(3):
        if grid.tensors is not None:
            t = np.asarray(grid.tensors[a], dtype=np.float64)
            steps.append(np.add.reduceat(t, np.arange(0, t.shape[0], f)))
        else:
            st = np.full(counts[a], grid.regular()[a] * f, dtype=np.float64)
            st[-1] = grid.regular()[a] * (grid.counts[a] - f * (counts[a] - 1))
            steps.append(st)
    c3 = (counts[0], counts[1], counts[2])
    if grid.tensors is None and all(n % f == 0 for n in grid.counts):
        size = grid.regular()
        return GridV2(grid.corner, c3, size=(size[0] * f, size[1] * f, size[2] * f), rotation=grid.rotation,
                      sub=SubblockSpec())  # fmt: skip
    t3 = (tuple(steps[0].tolist()), tuple(steps[1].tolist()), tuple(steps[2].tolist()))
    return GridV2(grid.corner, c3, tensors=t3, rotation=grid.rotation, sub=SubblockSpec())


def _weights(grid: GridV2, ps: np.ndarray, w: Any, sel: Any, ijk: tuple[np.ndarray, np.ndarray, np.ndarray]) -> Any:
    """The volume of every selected row: Π w (whole numbers) or, for a variable step, the volume of the parent."""
    if grid.tensors is not None:
        t = [np.asarray(grid.tensors[a], dtype=np.float64) for a in range(3)]
        return t[0][ijk[0]] * t[1][ijk[1]] * t[2][ijk[2]]
    if w is None:
        return np.ones(ps.shape[0], dtype=np.float64)
    ww = np.asarray(w, dtype=np.uint8).reshape(-1, 3)[sel].astype(np.float64)
    return ww[:, 0] * ww[:, 1] * ww[:, 2]


def coarsen(
    grid: GridV2,
    p: Any,
    s: Any,
    w: Any,
    passed: Any,
    klass: Any,
    level: int,
    *,
    kind: str = "mean",
    values: Any = None,
    klass_of: Callable[[np.ndarray], Any] | None = None,
) -> Coarse:
    """The rows of the model as cells of 2^level parents a side, with the class of every cell (module docstring)."""
    if kind not in ("mean", "mode"):
        raise bad_model(f"Неизвестный способ укрупнения «{kind}».", "Допустимы «mean» (среднее) и «mode» (мода).")
    if (values is None) != (klass_of is None):
        raise bad_model("Для укрупнения по значениям нужны и значения, и правило классов.",
                        "Передайте values и klass_of вместе или не передавайте ни то, ни другое.")  # fmt: skip
    pp = np.ascontiguousarray(p, dtype=np.uint64).reshape(-1)
    n = pp.shape[0]
    ok = np.ascontiguousarray(passed, dtype=np.bool_).reshape(-1)
    kl = np.zeros(n, dtype=np.uint16) if klass is None else np.ascontiguousarray(klass, dtype=np.uint16).reshape(-1)
    vv = None if values is None else np.ascontiguousarray(values, dtype=np.float64).reshape(-1)
    if ok.shape[0] != n or kl.shape[0] != n or (vv is not None and vv.shape[0] != n):
        raise bad_model("Маска, классы или значения не совпадают с числом строк модели.",
                        "Передайте по одному значению на строку.")  # fmt: skip
    new = coarse_grid(grid, level)
    sel = np.flatnonzero(ok)
    ps, ks = pp[sel], kl[sel]
    nx, ny = np.uint64(grid.counts[0]), np.uint64(grid.counts[1])
    i, j, k = ps % nx, (ps // nx) % ny, ps // (nx * ny)
    wt = _weights(grid, ps, w, sel, (i.astype(np.int64), j.astype(np.int64), k.astype(np.int64)))
    sh, cx, cy = np.uint64(level), np.uint64(new.counts[0]), np.uint64(new.counts[1])
    cell = (i >> sh) + cx * ((j >> sh) + cy * (k >> sh))
    if kind == "mode":
        cells, cls = _mode(cell, ks, wt)
    else:
        cells, cls = _mean(cell, ks, wt, None if vv is None else vv[sel], klass_of)
    return Coarse(new, cells, cls, level)


def _groups(key: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(order, starts): the stable order that sorts `key` and the first position of every run of equal keys."""
    order = np.argsort(key, kind="stable")
    ks = key[order]
    if ks.shape[0] == 0:
        return order, np.zeros(0, dtype=np.int64)
    return order, np.flatnonzero(np.concatenate(([True], ks[1:] != ks[:-1])))


def _mean(
    cell: np.ndarray, ks: np.ndarray, wt: np.ndarray, vals: np.ndarray | None, klass_of: Any
) -> tuple[np.ndarray, np.ndarray]:
    order, starts = _groups(cell)
    cells, wo = cell[order][starts], wt[order]
    if vals is None:
        x = ks[order].astype(np.float64)
        use = ks[order] != NO_CLASS
    else:
        x = vals[order]
        use = ~np.isnan(x)
    wu = np.where(use, wo, 0.0)
    sw = np.add.reduceat(wu, starts) if starts.shape[0] else np.zeros(0)
    sx = np.add.reduceat(np.where(use, x, 0.0) * wu, starts) if starts.shape[0] else np.zeros(0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(sw > 0, sx / np.where(sw > 0, sw, 1.0), np.nan)
    if vals is None:
        cls = np.where(sw > 0, np.floor(np.where(sw > 0, mean, 0.0) + 0.5), NO_CLASS).astype(np.uint16)
    else:
        cls = np.asarray(klass_of(mean), dtype=np.uint16).reshape(-1)
        if cls.shape[0] != mean.shape[0]:
            raise bad_model("Правило классов вернуло не по одному классу на ячейку.", "Проверьте правило классов.")
    return cells, cls


def _mode(cell: np.ndarray, ks: np.ndarray, wt: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    key = (cell << np.uint64(16)) | ks.astype(np.uint64)
    order, starts = _groups(key)
    if starts.shape[0] == 0:
        return np.zeros(0, dtype=np.uint64), np.zeros(0, dtype=np.uint16)
    gkey = key[order][starts]
    wsum = np.add.reduceat(wt[order], starts)
    gcell, gcls = gkey >> np.uint64(16), (gkey & np.uint64(0xFFFF)).astype(np.uint16)
    first = np.flatnonzero(np.concatenate(([True], gcell[1:] != gcell[:-1])))
    top = np.maximum.reduceat(wsum, first)
    cand = np.flatnonzero(wsum == np.repeat(top, np.diff(np.append(first, gcell.shape[0]))))
    _, at = np.unique(gcell[cand], return_index=True)  # the first candidate of a cell: the lowest class
    pick = cand[at]
    return gcell[pick], gcls[pick]


def _build(grid: GridV2, mask: np.ndarray, rows: tuple[Any, ...], klass: Any, threads: int, tick: Tick | None) -> Faces:
    """`shell.exposed` from a mask already computed (the count of faces was needed first): the same code."""
    pp, ss, ww, has_sub, _ok, order = rows
    n = pp.shape[0]
    cls = np.zeros(n, dtype=np.uint16) if klass is None else np.ascontiguousarray(klass, dtype=np.uint16).reshape(-1)
    if cls.shape[0] != n:
        raise bad_model("Классы строк не совпадают с числом строк модели.", "Передайте по одному классу на строку.")
    m_sorted = mask if order is None else mask[order]
    cls_sorted = cls if order is None else cls[order]
    starts = np.empty(n + 1, dtype=np.int64)
    m = int(_starts(m_sorted, _POP, starts))
    g = np.empty((m, 5), dtype=np.int64)
    ac = np.empty(m, dtype=np.uint8)
    row = np.empty(m, dtype=np.int64)
    cls_rows = np.empty(m, dtype=np.uint16)
    c = np.empty((m, 3), dtype=np.float64)
    h = np.empty((m, 2), dtype=np.float64)
    if m:
        div = np.asarray(grid.sub.div, dtype=np.int64)
        counts = np.asarray(grid.counts, dtype=np.int64)
        tens, edges, size = _axes(grid)
        strips.run(lambda lo, hi: _fill(lo, hi, pp, ss, ww, has_sub, m_sorted, cls_sorted, starts, counts, div,
                                        tens, edges, size, g, ac, row, cls_rows, c, h),
                   n, threads=threads, tick=tick, size=STRIP_ROWS)  # fmt: skip
        if order is not None:
            row = order[row]
        pos = _sort_key(grid, ac, g, cls_rows)
        c, h, ac, cls_rows, row, g = c[pos], h[pos], ac[pos], cls_rows[pos], row[pos], g[pos]
    return Faces(c=c, h=h, a=ac, k=cls_rows, row=row, g=g)


def _first_level(raw: int, limit: int, shell: bool) -> int:
    """The first guess of ℓ: a shell loses ≈ 4× of its faces per level, all the blocks — 8×."""
    step, level, left = (4 if shell else 8), 1, raw / max(1, limit)
    while left > step:
        left /= step
        level += 1
    return level


def view(
    grid: GridV2,
    p: Any,
    s: Any,
    w: Any,
    passed: Any,
    klass: Any = None,
    *,
    limit: int = DEFAULT_LIMIT,
    shell: bool = True,
    merge: bool = True,
    kind: str = "mean",
    values: Any = None,
    klass_of: Callable[[np.ndarray], Any] | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> View:
    """The faces to draw: the shell (or all blocks) of the rows that pass, LOD above `limit`, merged (module
    docstring). `limit` ≥ 1."""
    if limit < 1:
        raise bad_model("Лимит граней должен быть положительным.", "Задайте лимит от 500 000 до 10 000 000 граней.")
    mask, rows = _mask_sorted(grid, p, s, w, passed, shell, threads, tick)
    raw = count_faces(mask)
    if raw <= limit:
        f = _build(grid, mask, rows, klass, threads, tick)
        level = 0
    else:
        top = max(grid.counts).bit_length()
        level = min(_first_level(raw, limit, shell), top)
        while True:
            cs = coarsen(grid, p, s, w, passed, klass, level, kind=kind, values=values, klass_of=klass_of)
            ones = np.ones(cs.p.shape[0], dtype=bool)
            if level >= top or count_faces(face_mask(cs.grid, cs.p, None, None, ones, shell=shell)) <= limit:
                break
            level += 1
        f = exposed(cs.grid, cs.p, None, None, ones, cs.klass, shell=shell, threads=threads, tick=tick)
        grid = cs.grid
    built = len(f)
    return View(merge_faces(grid, f) if merge else f, raw, built, level, level > 0, limit)
