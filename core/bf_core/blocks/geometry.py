"""Geometry of block models v2 (ТЗ 8.7.2, ADR 0023 decisions 3, 5, 6): rows (P, s, w) → centres, boundaries and volumes
in the local axes q and in the project axes p = O + R_z(φ)·q; the row ↔ cell index; the intersection with a
rectangle or a box. All float64; the turn is element by element (`GridV2.to_project`), so a turn by a multiple of 90°
carries no rounding error and the result does not depend on the thread count.

A row is (P, s, w): P = i + nx·(j + ny·k) (uint64), s = sx sy sz and w = wx wy wz (uint8, n×3, in sub-cells); `s`,
`w` = None — a model without sub-blocks (s = 0, w = div = 1). The local boundaries of a regular grid are the products
(i + s/div)·d, never a running sum, so the cell edges do not drift; the centre is (i + (s + w/2)/div)·d. A variable
step (`tensors`) has no sub-blocks: the edges are the cumulative sums `GridV2.edges`. The functions work on whole
arrays (chunks of the caller; `row_corners` makes n×8×3 numbers — give it a chunk, not 20 million rows).
"""

from __future__ import annotations

import numpy as np

from .schema import GridV2, bad_model, ijk_of, variable_grid

KEY_SHIFT = 21  # P << 21 | sz << 14 | sy << 7 | sx: s < 128 per axis (L ≤ 7), P < 2^31 (nx·ny·nz ≤ 2·10⁹)


def _sub(grid: GridV2, s: np.ndarray | None, w: np.ndarray | None, n: int) -> tuple[np.ndarray, np.ndarray]:
    """(s, w) as float64 n×3; None → s = 0, w = div (a whole parent). Rows off the sub-cells → 422."""
    div = np.asarray(grid.sub.div, dtype=np.float64)
    if s is None and w is None:
        return np.zeros((n, 3)), np.broadcast_to(div, (n, 3))
    if s is None or w is None:
        raise bad_model("Смещение и размер субблоков заданы не вместе.")
    if grid.tensors is not None:
        raise variable_grid("Субблоки")
    s, w = np.asarray(s).reshape(-1, 3), np.asarray(w).reshape(-1, 3)
    if len(s) != n or len(w) != n:
        raise bad_model("Число строк субблоков не совпадает с числом блоков.")
    if grid.sub.bad_rows(s, w).any():
        raise bad_model("Субблок выходит за границы своего родителя.", "Проверьте столбцы sx…wz исходной модели.")
    return s.astype(np.float64), w.astype(np.float64)


def row_ijk(grid: GridV2, p: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Parent indices (i, j, k) of the rows `p` (intp); a number beyond the grid → 422."""
    p = np.asarray(p, dtype=np.uint64).reshape(-1)
    if len(p) and int(p.max()) >= grid.cells:
        raise bad_model("Номер блока выходит за пределы сетки модели.", "Проверьте столбец idx исходной модели.")
    i, j, k = ijk_of(p, grid.counts)
    return i.astype(np.intp), j.astype(np.intp), k.astype(np.intp)


def parent_p(grid: GridV2, i: np.ndarray, j: np.ndarray, k: np.ndarray) -> np.ndarray:
    """P = i + nx·(j + ny·k) (uint64) of the parents (i, j, k); an index off the grid → 422."""
    ijk = [np.asarray(v, dtype=np.int64).reshape(-1) for v in (i, j, k)]
    for a, v in enumerate(ijk):
        if len(v) and (int(v.min()) < 0 or int(v.max()) >= grid.counts[a]):
            raise bad_model("Индекс ячейки выходит за пределы сетки модели.")
    nx, ny = np.uint64(grid.counts[0]), np.uint64(grid.counts[1])
    return ijk[0].astype(np.uint64) + nx * (ijk[1].astype(np.uint64) + ny * ijk[2].astype(np.uint64))  # type: ignore[no-any-return]


def row_keys(p: np.ndarray, s: np.ndarray | None = None) -> np.ndarray:
    """The sort key of the rows (P, sz, sy, sx) as one uint64; the files go in ascending order of it."""
    key = np.asarray(p, dtype=np.uint64).reshape(-1) << np.uint64(KEY_SHIFT)
    if s is not None:
        s = np.asarray(s).reshape(-1, 3).astype(np.uint64)
        key |= (s[:, 2] << np.uint64(14)) | (s[:, 1] << np.uint64(7)) | s[:, 0]
    return key


def find_rows(p: np.ndarray, s: np.ndarray | None, q_p: np.ndarray, q_s: np.ndarray | None = None) -> np.ndarray:
    """Row numbers (intp, −1 — none) of the cells (P, s) in the file rows `p`, `s` (ascending by `row_keys`): the
    exact match of the offset; a point inside a bigger sub-block — `locate_rows`."""
    keys, want = row_keys(p, s), row_keys(q_p, q_s)
    pos = np.minimum(np.searchsorted(keys, want), max(len(keys) - 1, 0))
    if not len(keys):
        return np.full(len(want), -1, dtype=np.intp)
    return np.where(keys[pos] == want, pos, -1).astype(np.intp)  # type: ignore[no-any-return]


def _edge(grid: GridV2, a: int, i: np.ndarray, off: np.ndarray) -> np.ndarray:
    """Local boundary along the axis `a` at the parent `i` plus `off` sub-cells (regular grid): (i + off/div)·d."""
    return (i + off / grid.sub.div[a]) * grid.regular()[a]  # type: ignore[no-any-return]


def row_boxes(grid: GridV2, p: np.ndarray, s: np.ndarray | None = None, w: np.ndarray | None = None) -> np.ndarray:
    """Local boundaries of the rows, n×6: (qx0, qy0, qz0, qx1, qy1, qz1)."""
    ijk = row_ijk(grid, p)
    n = len(ijk[0])
    out = np.empty((n, 6), dtype=np.float64)
    if grid.tensors is not None:
        _sub(grid, s, w, n)
        for a in range(3):
            e = np.asarray(grid.edges(a), dtype=np.float64)
            out[:, a], out[:, a + 3] = e[ijk[a]], e[ijk[a] + 1]
        return out
    sf, wf = _sub(grid, s, w, n)
    for a in range(3):
        i = ijk[a].astype(np.float64)
        out[:, a] = _edge(grid, a, i, sf[:, a])
        out[:, a + 3] = _edge(grid, a, i, sf[:, a] + wf[:, a])
    return out


def row_local_centres(
    grid: GridV2, p: np.ndarray, s: np.ndarray | None = None, w: np.ndarray | None = None
) -> np.ndarray:
    """Centres of the rows in the local axes, n×3: q_a = (i_a + (s_a + w_a/2)/div_a)·d_a (the middle of the edges
    on a variable step)."""
    ijk = row_ijk(grid, p)
    n = len(ijk[0])
    out = np.empty((n, 3), dtype=np.float64)
    if grid.tensors is not None:
        _sub(grid, s, w, n)
        for a in range(3):
            e = np.asarray(grid.edges(a), dtype=np.float64)
            out[:, a] = (e[ijk[a]] + e[ijk[a] + 1]) * 0.5
        return out
    sf, wf = _sub(grid, s, w, n)
    for a in range(3):
        out[:, a] = _edge(grid, a, ijk[a].astype(np.float64), sf[:, a] + wf[:, a] * 0.5)
    return out


def centres(grid: GridV2, p: np.ndarray, s: np.ndarray | None = None, w: np.ndarray | None = None) -> np.ndarray:
    """Centres of the rows in the project axes, n×3 (Т8.7-4: 1e−9 м; a turn by a multiple of 90° is exact)."""
    return grid.to_project(row_local_centres(grid, p, s, w))


def row_widths(grid: GridV2, p: np.ndarray, s: np.ndarray | None = None, w: np.ndarray | None = None) -> np.ndarray:
    """Sizes of the rows along the local axes, n×3: w_a·d_a/div_a (the step of the parent on a variable step)."""
    ijk = row_ijk(grid, p)
    n = len(ijk[0])
    if grid.tensors is not None:
        _sub(grid, s, w, n)
        return np.stack([np.asarray(grid.steps(a), dtype=np.float64)[ijk[a]] for a in range(3)], axis=1)
    _, wf = _sub(grid, s, w, n)
    d, div = grid.regular(), grid.sub.div
    return np.stack([wf[:, a] * d[a] / div[a] for a in range(3)], axis=1)


def row_volumes(
    grid: GridV2,
    p: np.ndarray,
    s: np.ndarray | None = None,
    w: np.ndarray | None = None,
    frac: np.ndarray | None = None,
) -> np.ndarray:
    """Volumes of the rows, n: Π_a(w_a·d_a/div_a)·FRAC. `frac` None — the model has no FRAC field (the whole block);
    FRAC empty (NaN) — the volume is NaN, never 0 (the block is not in the sum, `volume_sum` counts it)."""
    wd = row_widths(grid, p, s, w)
    vol = (wd[:, 0] * wd[:, 1]) * wd[:, 2]
    if frac is None:
        return vol  # type: ignore[no-any-return]
    frac = np.asarray(frac, dtype=np.float64).reshape(-1)
    if len(frac) != len(vol):
        raise bad_model("Число значений FRAC не совпадает с числом блоков.")
    return vol * frac  # type: ignore[no-any-return]


def volume_sum(vol: np.ndarray) -> tuple[float, int]:
    """(Σ of the volumes without the empty ones, the number of the empty ones): a gap is not a zero."""
    vol = np.asarray(vol, dtype=np.float64).reshape(-1)
    ok = ~np.isnan(vol)
    return float(np.add.reduce(vol[ok])), int(len(vol) - ok.sum())


def parent_volume(grid: GridV2) -> float:
    """The volume of the whole grid, m³ (Σ of the parents; the turn does not change it)."""
    lx, ly, lz = grid.lengths()
    return float((lx * ly) * lz)


def row_corners(grid: GridV2, p: np.ndarray, s: np.ndarray | None = None, w: np.ndarray | None = None) -> np.ndarray:
    """The eight corners of the rows in the project axes, n×8×3; the order — z (bottom, top) × the turn about the
    footprint: (x0,y0), (x1,y0), (x1,y1), (x0,y1) in the local axes, the bottom four, then the top four."""
    b = row_boxes(grid, p, s, w)
    n = len(b)
    xs, ys = (0, 3, 3, 0), (1, 1, 4, 4)
    q = np.empty((n, 8, 3), dtype=np.float64)
    for t in range(4):
        q[:, t, 0], q[:, t, 1], q[:, t, 2] = b[:, xs[t]], b[:, ys[t]], b[:, 2]
        q[:, t + 4, 0], q[:, t + 4, 1], q[:, t + 4, 2] = b[:, xs[t]], b[:, ys[t]], b[:, 5]
    return grid.to_project(q.reshape(-1, 3)).reshape(n, 8, 3)


def row_bounds(grid: GridV2, p: np.ndarray, s: np.ndarray | None = None, w: np.ndarray | None = None) -> np.ndarray:
    """The box of the rows in the project axes (round the turned footprint), n×6: min xyz, max xyz."""
    c = row_corners(grid, p, s, w)
    return np.concatenate([c.min(axis=1), c.max(axis=1)], axis=1)


def extent(grid: GridV2) -> list[float]:
    """[xmin, ymin, zmin, xmax, ymax, zmax] of the whole grid in the project axes (`objects.extent`)."""
    return grid.extent()


def _box(box: np.ndarray | list[float] | tuple[float, ...]) -> tuple[float, float, float, float, float, float]:
    """A rectangle (xmin, ymin, xmax, ymax) or a box (xmin, ymin, zmin, xmax, ymax, zmax) of the project; an
    empty or inverted one → 422. A rectangle has no limit along Z."""
    v = [float(x) for x in np.asarray(box, dtype=np.float64).reshape(-1)]
    if len(v) == 4:
        v = [v[0], v[1], -np.inf, v[2], v[3], np.inf]
    if len(v) != 6 or any(np.isnan(v)) or v[0] > v[3] or v[1] > v[4] or v[2] > v[5]:
        raise bad_model(
            "Прямоугольник или параллелепипед задан неверно.", "Нужно 4 или 6 чисел: минимумы, затем максимумы."
        )
    return v[0], v[1], v[2], v[3], v[4], v[5]


def _box_local_xy(grid: GridV2, b: tuple[float, ...]) -> tuple[float, float, float, float]:
    """The box of the footprint of the project rectangle in the local axes (round its corners)."""
    if not (np.isfinite(b[0]) and np.isfinite(b[1]) and np.isfinite(b[3]) and np.isfinite(b[4])):
        return -np.inf, -np.inf, np.inf, np.inf
    q = grid.to_local([[b[0], b[1], 0.0], [b[3], b[1], 0.0], [b[3], b[4], 0.0], [b[0], b[4], 0.0]])
    return float(q[:, 0].min()), float(q[:, 1].min()), float(q[:, 0].max()), float(q[:, 1].max())


def intersects_box(
    grid: GridV2,
    p: np.ndarray,
    s: np.ndarray | None,
    w: np.ndarray | None,
    box: np.ndarray | list[float] | tuple[float, ...],
    *,
    touch: bool = False,
) -> np.ndarray:
    """Which rows meet the project rectangle (4 numbers) or box (6): bool n. The rows are turned rectangles, the box
    is not, so the test is the separating axes (the project x, y and the local u, v, then z). `touch` False — the
    common part has a volume (a shared face is not a meeting); True — a shared face or edge counts."""
    b = _box(box)
    lq = _box_local_xy(grid, b)
    rb, pb = row_boxes(grid, p, s, w), row_bounds(grid, p, s, w)
    lt = (lambda lo, hi: lo <= hi) if touch else (lambda lo, hi: lo < hi)  # noqa: E731
    ok = lt(pb[:, 0], b[3]) & lt(b[0], pb[:, 3]) & lt(pb[:, 1], b[4]) & lt(b[1], pb[:, 4])  # project x, y
    ok &= lt(rb[:, 0], lq[2]) & lt(lq[0], rb[:, 3]) & lt(rb[:, 1], lq[3]) & lt(lq[1], rb[:, 4])  # local u, v
    zlo, zhi = grid.corner[2] + rb[:, 2], grid.corner[2] + rb[:, 5]
    ok &= lt(zlo, b[5]) & lt(b[2], zhi)
    return ok  # type: ignore[no-any-return]


def centres_in_box(
    grid: GridV2,
    p: np.ndarray,
    s: np.ndarray | None,
    w: np.ndarray | None,
    box: np.ndarray | list[float] | tuple[float, ...],
) -> np.ndarray:
    """Which row centres lie in the project rectangle or box (the faces count: min ≤ c ≤ max), bool n."""
    b = _box(box)
    c = centres(grid, p, s, w)
    return (
        (c[:, 0] >= b[0])
        & (c[:, 0] <= b[3])
        & (c[:, 1] >= b[1])
        & (c[:, 1] <= b[4])  # type: ignore[no-any-return]
        & (c[:, 2] >= b[2])
        & (c[:, 2] <= b[5])
    )


def parent_ranges(
    grid: GridV2, box: np.ndarray | list[float] | tuple[float, ...]
) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
    """The (i, j, k) range of the parents, inclusive, that may meet the project rectangle or box — the box of its
    footprint in the local axes cut to the grid; None if nothing can (the rows of the parents inside are then
    filtered with `intersects_box`)."""
    b = _box(box)
    lo, hi = list(_box_local_xy(grid, b)[:2]), list(_box_local_xy(grid, b)[2:])
    lo.append(b[2] - grid.corner[2])
    hi.append(b[5] - grid.corner[2])
    first, last = [], []
    for a in range(3):
        e = np.asarray(grid.edges(a), dtype=np.float64) if grid.tensors is not None else None
        n = grid.counts[a]
        if hi[a] < 0.0 or lo[a] > grid.lengths()[a]:
            return None
        if e is None:
            d = grid.regular()[a]
            f0 = 0 if lo[a] <= 0.0 else min(int(lo[a] // d), n - 1)
            f1 = n - 1 if hi[a] >= d * n else max(int(hi[a] // d), 0)
        else:
            f0 = max(int(np.searchsorted(e, lo[a], side="right")) - 1, 0)
            f1 = min(int(np.searchsorted(e, hi[a], side="right")) - 1, n - 1)
        first.append(f0)
        last.append(f1)
    return (first[0], first[1], first[2]), (last[0], last[1], last[2])


def locate(grid: GridV2, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The unit sub-cell of each project point (cells are half-open: [lo; hi)): (valid bool n, P uint64 n, s uint8
    n×3). A point outside the grid — valid False, P = 0, s = 0. Boundaries are decided by the same products that
    `row_boxes` gives, so a point exactly on an edge lies in the cell above it."""
    q = grid.to_local(pts)
    n = len(q)
    valid = np.ones(n, dtype=bool)
    ijk, off = [], []
    for a in range(3):
        x = q[:, a]
        if grid.tensors is not None:
            e = np.asarray(grid.edges(a), dtype=np.float64)
            i = np.searchsorted(e, x, side="right") - 1
            valid &= (x >= e[0]) & (x < e[-1])
            i = np.clip(i, 0, grid.counts[a] - 1)
            ijk.append(i.astype(np.intp))
            off.append(np.zeros(n, dtype=np.intp))
            continue
        d, div = grid.regular()[a], grid.sub.div[a]
        ok = np.isfinite(x) & (x >= 0.0) & (x < d * grid.counts[a])
        valid &= ok
        xs = np.where(ok, x, 0.0)
        i = np.clip(np.floor(xs / d).astype(np.intp), 0, grid.counts[a] - 1)
        i -= (xs < _edge(grid, a, i.astype(np.float64), np.zeros(n))).astype(np.intp)
        i += (xs >= _edge(grid, a, i.astype(np.float64), np.full(n, float(div)))).astype(np.intp)
        i = np.clip(i, 0, grid.counts[a] - 1)
        t = np.clip(np.floor((xs - i * d) / (d / div)).astype(np.intp), 0, div - 1)
        t -= ((xs < _edge(grid, a, i.astype(np.float64), t.astype(np.float64))) & (t > 0)).astype(np.intp)
        t += ((xs >= _edge(grid, a, i.astype(np.float64), t + 1.0)) & (t < div - 1)).astype(np.intp)
        ijk.append(i)
        off.append(t)
    pp = parent_p(grid, *(np.where(valid, v, 0) for v in ijk))
    s = np.stack([np.where(valid, o, 0) for o in off], axis=1).astype(np.uint8)
    return valid, pp, s


def locate_rows(p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None, grid: GridV2, pts: np.ndarray) -> np.ndarray:
    """The row of the file (`p`, `s`, `w`, ascending) that holds each project point: intp n, −1 — in no row (a gap
    of the sparse model or outside the grid). A sub-block holds every unit sub-cell of s … s + w."""
    valid, qp, qs = locate(grid, pts)
    p = np.asarray(p, dtype=np.uint64).reshape(-1)
    lo = np.searchsorted(p, qp, side="left")
    hi = np.searchsorted(p, qp, side="right")
    out = np.full(len(qp), -1, dtype=np.intp)
    if s is None or w is None:
        hit = valid & (hi > lo)
        out[hit] = lo[hit]
        return out
    s64, w64 = np.asarray(s).reshape(-1, 3).astype(np.int64), np.asarray(w).reshape(-1, 3).astype(np.int64)
    qs64 = qs.astype(np.int64)
    todo = np.flatnonzero(valid & (hi > lo))
    t = 0
    while len(todo):
        row = lo[todo] + t
        live = row < hi[todo]
        todo, row = todo[live], row[live]
        inside = ((qs64[todo] >= s64[row]) & (qs64[todo] < s64[row] + w64[row])).all(axis=1)
        out[todo[inside]] = row[inside]
        todo = todo[~inside]
        t += 1
    return out
