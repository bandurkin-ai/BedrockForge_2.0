"""The slice of a block model by a plane: a raster of values and the polygons of the section (подшаг 17.24;
ТЗ 8.7.5–8.7.6, ADR 0023 decision 19, docs/progress/e17-api.md §12 BFA1 `blocks_slice`, `slice/polygons`).

    pl = plane_of(grid, origin, normal)                 # the plane in the local axes; 422 bad_param {param}
    sl = Slicer(grid, pl, sub=model.sub, max_px=4096, kind="number" | "category" | None, hidden=())
    sl.add(p, s, w, passed, values)                      # any number of portions of rows, in the order of the file
    r = sl.finish() -> Raster                            # value f4 [H·W], m u1 [H·W], frame, kind, passed
    r = slice_rows(grid, pl, p, s, w, passed, values, kind, sub=…)      # the same for whole arrays
    poly = Sectioner(grid, pl, limit).add(p, s, w, passed) … finish() -> Polygons     # for the print of Э22
    poly = section_polygons(grid, pl, p, s, w, passed, limit=200000)

The raster (the texture). A plane ⟂ an axis of the model (|n·e| ≥ 1 − 1e-12 in the local axes) gives pixels = cells
(`cells`: a model without sub-blocks) or sub-cells (`subcells`): the plane axes are u = (a + 1) mod 3, v = (a + 2) mod 3
of the faces (X → (Y, Z), Y → (Z, X), Z → (X, Y)), u × v = +e_a whatever the sign of the normal, the raster covers
the whole grid. Any other plane — `sampled`: u horizontal, v = n × u (u × v = n), the pixel is the smallest sub-cell
(or the smallest step of a variable grid), the raster is the box of the section of the grid. The side of the raster
is cut to `max_px` (64–4096) by a coarser pixel (then `sampled`, `du`, `dv` tell the step). A pixel takes the block
that holds its CENTRE (a block is [lo; hi) on every axis, so a plane on the shared face takes the block above it and
the pixels never leave a gap or count a block twice); with more than one row on a pixel (overlaps — `checks`) the
last row of the file wins. `m`: 0 — no block (or it does not pass the filter, or its category is hidden), 1 — a value,
2 — a block without a value (NaN, code < 0; the value is NaN there, never 0). Without a field (`kind` None) the
value is NaN and `m` is 1 for every block. The values go to float32 (a category code is exact).

The frame (project axes): `origin` — the corner of pixel (0, 0), `u`, `v` — unit vectors (the turn of the model is in
them), `du`, `dv` — the pixel sizes; pixel (i, j) is the rectangle origin + u·i·du + v·j·dv … (i + 1, j + 1).
Row j goes up along v, column i along u; the flat index is j·W + i.

The polygons (the print, Э22): the convex polygon plane ∩ block, in the project axes, counter-clockwise seen from the
end of the normal (u × v = n); `area` is the sum over every row the plane crosses (compensated, the same bits in any
portions), `count` — the polygons, `truncated` — more than `limit`. The same half-open rule as the raster: a plane on
a shared face belongs to the block above it (in the sense of the normal turned to +e_a for a plane ⟂ an axis), a
plane that only touches a block gives no polygon.

The kernels are numba `cache=True, nogil=True`; the geometry is float64; the edges of a regular grid are the same
products (i + s/div)·d as `geometry.row_boxes`, so a cell edge of the raster is exactly the edge of the rows. A plane
⟂ Z reads only the rows of one parent layer (P is ascending: two binary searches), `Slicer.p_range` tells the range of
P, a caller that reads the file by row groups reads the groups with `k0 ≤ k ≤ k1` only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks.schema import GridV2
from bf_core.jit import njit

MAX_PX = 4096
MIN_PX = 64
LIMIT = 200_000  # polygons of the print (e17-api §12)
MAX_LIMIT = 2_000_000
ALIGN = 1.0 - 1e-12  # |n·e| for «the plane is ⟂ an axis of the model»
NUMBER, CATEGORY = "number", "category"


HINT = "Задайте начало и ненулевую нормаль плоскости тремя числами."


def bad_plane(message: str, param: str, hint: str = HINT) -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


# --- the plane ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Plane:
    """The plane in the local axes of the model: a point `o`, the unit normal `n` (canonical: +e_a for a plane ⟂ the
    axis a, `flip` — the given normal was −e_a), `axis` (−1 — oblique). `origin`, `normal` — as given (project)."""

    o: np.ndarray
    n: np.ndarray
    axis: int
    flip: bool
    origin: np.ndarray
    normal: np.ndarray


def _vec(raw: Any, param: str) -> np.ndarray:
    try:
        v = np.asarray([float(x) for x in raw], dtype=np.float64)
    except (TypeError, ValueError):
        v = np.empty(0)
    if v.shape != (3,) or not np.all(np.isfinite(v)):
        raise bad_plane("Плоскость среза задана неверно: нужны три конечных числа.", param)
    return v


def plane_of(grid: GridV2, origin: Sequence[float], normal: Sequence[float]) -> Plane:
    """The plane through `origin` ⟂ `normal` (project axes) in the local axes of `grid`. 422 `bad_param`."""
    o, nrm = _vec(origin, "plane.origin"), _vec(normal, "plane.normal")
    length = float(np.sqrt(np.dot(nrm, nrm)))
    if not length > 0.0:
        raise bad_plane("Плоскость среза задана неверно: нормаль нулевая.", "plane.normal")
    nrm = nrm / length
    sn, cs = grid.sin_cos()
    nq = np.array([cs * nrm[0] + sn * nrm[1], cs * nrm[1] - sn * nrm[0], nrm[2]])
    oq = grid.to_local([o])[0]
    axis, flip = -1, False
    for a in range(3):
        if abs(nq[a]) >= ALIGN:
            axis, flip = a, bool(nq[a] < 0.0)
            nq = np.zeros(3)
            nq[a] = 1.0
            break
    if axis < 0:
        nq = nq / float(np.sqrt(np.dot(nq, nq)))
    return Plane(oq, nq, axis, flip, o, nrm)


def _to_project_dir(grid: GridV2, q: np.ndarray) -> np.ndarray:
    sn, cs = grid.sin_cos()
    return np.array([cs * q[0] - sn * q[1], sn * q[0] + cs * q[1], q[2]])


def min_subcell(grid: GridV2, sub: bool = True) -> float:
    """The smallest sub-cell (`sub` False: the smallest cell) of the grid, m; the smallest step on a variable grid."""
    if grid.tensors is not None:
        return float(min(min(t) for t in grid.tensors))
    d, div = grid.regular(), grid.sub.div if sub else (1, 1, 1)
    return float(min(d[a] / div[a] for a in range(3)))


# --- the edges ----------------------------------------------------------------------------------------------


def _tables(grid: GridV2) -> tuple[bool, np.ndarray, np.ndarray, np.ndarray]:
    """(regular, size d, edge tables of a variable grid, their starts)."""
    if grid.tensors is None:
        return True, np.asarray(grid.regular(), dtype=np.float64), np.zeros(1), np.zeros(3, dtype=np.int64)
    e = [np.asarray(grid.edges(a), dtype=np.float64) for a in range(3)]
    off = np.array([0, len(e[0]), len(e[0]) + len(e[1])], dtype=np.int64)
    return False, np.ones(3), np.concatenate(e), off


def _parent_at(grid: GridV2, a: int, t: float) -> int | None:
    """The parent of the axis `a` that holds the local coordinate `t` ([lo; hi)), None outside the grid."""
    n = grid.counts[a]
    if grid.tensors is not None:
        e = np.asarray(grid.edges(a), dtype=np.float64)
        if not (e[0] <= t < e[-1]):
            return None
        return min(max(int(np.searchsorted(e, t, side="right")) - 1, 0), n - 1)
    d = grid.regular()[a]
    if not (0.0 <= t < d * n):
        return None
    i = min(max(int(t // d), 0), n - 1)
    div = float(grid.sub.div[a])

    def edge(i: int, off: float) -> float:
        return float(geometry._edge(grid, a, np.full(1, float(i)), np.full(1, off))[0])

    while i > 0 and t < edge(i, 0.0):
        i -= 1
    while i < n - 1 and t >= edge(i, div):
        i += 1
    return i


# --- the kernels --------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _box(reg, d, ed, tab, toff, ijk, s, w, has_sub, r, lo, hi):  # type: ignore[no-untyped-def]
    """The local box of the row `r` into lo, hi (the products of `geometry.row_boxes`)."""
    for a in range(3):
        i = ijk[a]
        if reg:
            so = np.float64(s[r, a]) if has_sub else 0.0
            wo = np.float64(w[r, a]) if has_sub else 1.0
            lo[a] = (i + so / ed[a]) * d[a]
            hi[a] = (i + (so + wo) / ed[a]) * d[a]
        else:
            lo[a] = tab[toff[a] + i]
            hi[a] = tab[toff[a] + i + 1]


@njit(cache=True, nogil=True)
def _raster(
    p,
    s,
    w,
    has_sub,
    keep,
    has_keep,
    val,
    has_val,
    miss,
    nx,
    ny,
    reg,
    d,
    ed,
    tab,
    toff,  # type: ignore[no-untyped-def]
    axis,
    pa,
    o,
    n,
    fo,
    uq,
    vq,
    du,
    dv,
    width,
    height,
    out_v,
    out_m,
):
    cnt = 0
    nxy = nx * ny
    ijk = np.empty(3, dtype=np.int64)
    lo = np.empty(3)
    hi = np.empty(3)
    for r in range(p.shape[0]):
        if has_keep and not keep[r]:
            continue
        pi = np.int64(p[r])
        kk = pi // nxy
        rem = pi - kk * nxy
        jj = rem // nx
        ii = rem - jj * nx
        ijk[0] = ii
        ijk[1] = jj
        ijk[2] = kk
        if axis >= 0 and ijk[axis] != pa:
            continue
        _box(reg, d, ed, tab, toff, ijk, s, w, has_sub, r, lo, hi)
        if axis >= 0:
            if not (lo[axis] <= o[axis] and o[axis] < hi[axis]):
                continue
        else:
            dist = 0.0
            rad = 0.0
            for a in range(3):
                dist += n[a] * (0.5 * (lo[a] + hi[a]) - o[a])
                rad += abs(n[a]) * 0.5 * (hi[a] - lo[a])
            if abs(dist) >= rad:
                continue
        cnt += 1
        cu = 0.0
        cv = 0.0
        hu = 0.0
        hv = 0.0
        for a in range(3):
            c = 0.5 * (lo[a] + hi[a]) - fo[a]
            h = 0.5 * (hi[a] - lo[a])
            cu += uq[a] * c
            cv += vq[a] * c
            hu += abs(uq[a]) * h
            hv += abs(vq[a]) * h
        i0 = max(np.int64(np.floor((cu - hu) / du - 0.5)) - 1, 0)
        i1 = min(np.int64(np.ceil((cu + hu) / du - 0.5)) + 1, width - 1)
        j0 = max(np.int64(np.floor((cv - hv) / dv - 0.5)) - 1, 0)
        j1 = min(np.int64(np.ceil((cv + hv) / dv - 0.5)) + 1, height - 1)
        if has_val:
            v = np.float64(val[r])
            gap = v != v or v == miss
        for j in range(j0, j1 + 1):
            pv = (j + 0.5) * dv
            for i in range(i0, i1 + 1):
                pu = (i + 0.5) * du
                inside = True
                for a in range(3):
                    q = fo[a] + uq[a] * pu + vq[a] * pv
                    if q < lo[a] or q >= hi[a]:
                        inside = False
                        break
                if not inside:
                    continue
                k = j * width + i
                if has_val:
                    if gap:
                        out_v[k] = np.nan
                        out_m[k] = 2
                    else:
                        out_v[k] = v
                        out_m[k] = 1
                else:
                    out_v[k] = np.nan
                    out_m[k] = 1
    return cnt


_CORNER = np.array([[(c >> 0) & 1, (c >> 1) & 1, (c >> 2) & 1] for c in range(8)], dtype=np.int64)
_EDGES = np.array([(c, c | b) for b in (1, 2, 4) for c in range(8) if not c & b], dtype=np.int64)


# --- the layout ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """The raster of a plane: the size, the pixel, the frame (local `fo`, `uq`, `vq`; project `origin`, `u`, `v`)."""

    kind: str  # cells | subcells | sampled
    width: int
    height: int
    du: float
    dv: float
    fo: np.ndarray
    uq: np.ndarray
    vq: np.ndarray
    origin: np.ndarray
    u: np.ndarray
    v: np.ndarray
    pa: int  # the parent of the plane along its axis (aligned planes), else −1

    def frame(self) -> dict[str, Any]:
        return {"origin": self.origin.tolist(), "u": self.u.tolist(), "v": self.v.tolist(),
                "du": self.du, "dv": self.dv}  # fmt: skip


def _empty(grid: GridV2, pl: Plane) -> Layout:
    z = np.zeros(3)
    return Layout("sampled", 0, 0, 1.0, 1.0, z, z, z, z, z, z, -1)


def _px(max_px: int) -> int:
    try:
        v = int(max_px)
    except (TypeError, ValueError):
        v = 0
    if not MIN_PX <= v <= MAX_PX:
        raise bad_plane(f"Размер растра среза: от {MIN_PX} до {MAX_PX} пикселей.", "max_px",
                        f"Задайте число от {MIN_PX} до {MAX_PX}.")  # fmt: skip
    return v


def _cells(length: float, step: float) -> int:
    """How many pixels of `step` cover `length` (a rounding error of the division is not a pixel)."""
    return max(int(np.ceil(length / step * (1.0 - 1e-12))), 1)


def layout(grid: GridV2, pl: Plane, *, sub: bool = True, max_px: int = MAX_PX) -> Layout:
    """The raster of the plane over the grid (width = height = 0 when the plane misses the grid)."""
    max_px = _px(max_px)
    if pl.axis >= 0:
        a = pl.axis
        t = float(pl.o[a])
        pa = _parent_at(grid, a, t)
        if pa is None:
            return _empty(grid, pl)
        ua, va = (a + 1) % 3, (a + 2) % 3
        uq, vq = np.zeros(3), np.zeros(3)
        uq[ua], vq[va] = 1.0, 1.0
        fo = np.zeros(3)
        fo[a] = t
        if grid.tensors is None:
            ed = grid.sub.div if sub else (1, 1, 1)
            step = [grid.regular()[x] / ed[x] for x in (ua, va)]
            total = [grid.counts[x] * ed[x] for x in (ua, va)]
            k = [max(-(-total[i] // max_px), 1) for i in (0, 1)]
            du, dv = k[0] * step[0], k[1] * step[1]
            width, height = -(-total[0] // k[0]), -(-total[1] // k[1])
            kind = ("subcells" if sub else "cells") if k == [1, 1] else "sampled"
        else:
            ln = [grid.lengths()[ua], grid.lengths()[va]]
            fine = min(min(grid.tensors[ua]), min(grid.tensors[va]))
            du = dv = max(fine, max(ln) / max_px)
            width, height = min(_cells(ln[0], du), max_px), min(_cells(ln[1], dv), max_px)
            kind = "sampled"
        origin = grid.to_project([fo])[0]
        return Layout(kind, int(width), int(height), float(du), float(dv), fo, uq, vq, origin,
                      _to_project_dir(grid, uq), _to_project_dir(grid, vq), pa)  # fmt: skip
    n = pl.n
    h = float(np.hypot(n[0], n[1]))
    uq = np.array([-n[1] / h, n[0] / h, 0.0])
    vq = np.cross(n, uq)
    box = np.asarray(grid.lengths(), dtype=np.float64)
    hits = []
    for c0, c1 in _EDGES:
        e0 = _CORNER[c0] * box
        e1 = _CORNER[c1] * box
        d0, d1 = float(np.dot(n, e0 - pl.o)), float(np.dot(n, e1 - pl.o))
        if (d0 > 0.0) != (d1 > 0.0):
            hits.append(e0 + (e1 - e0) * (d0 / (d0 - d1)))
    if len(hits) < 3:
        return _empty(grid, pl)
    cu = np.array([np.dot(x - pl.o, uq) for x in hits])
    cv = np.array([np.dot(x - pl.o, vq) for x in hits])
    umin, vmin = float(cu.min()), float(cv.min())
    lu, lv = float(cu.max()) - umin, float(cv.max()) - vmin
    fine = min_subcell(grid, sub)
    du = dv = max(fine, max(lu, lv) / max_px)
    width, height = min(_cells(lu, du), max_px), min(_cells(lv, dv), max_px)
    fo = pl.o + uq * umin + vq * vmin
    origin = grid.to_project([fo])[0]
    return Layout("sampled", width, height, float(du), float(dv), fo, uq, vq, origin,
                  _to_project_dir(grid, uq), _to_project_dir(grid, vq), -1)  # fmt: skip


# --- the raster ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Raster:
    """The answer: `value` float32 [H·W] (j·W + i), `m` uint8 [H·W] (0 / 1 / 2), the layout, `passed` — the rows the
    plane crosses that pass; `kind` — number | category | None."""

    value: np.ndarray
    m: np.ndarray
    layout: Layout
    kind: str | None
    passed: int
    plane: Plane

    @property
    def width(self) -> int:
        return self.layout.width

    @property
    def height(self) -> int:
        return self.layout.height

    def header(self) -> dict[str, Any]:
        """The part of the BFA1 header the raster knows (the route adds model, version, field, labels)."""
        lay = self.layout
        return {"plane": {"origin": self.plane.origin.tolist(), "normal": self.plane.normal.tolist()},
                "kind": self.kind, "raster": lay.kind, "width": lay.width, "height": lay.height,
                "frame": lay.frame(), "passed": self.passed}  # fmt: skip


class Slicer:
    """The raster of a plane filled by portions of rows (module docstring). Not shared between threads."""

    def __init__(self, grid: GridV2, plane: Plane, *, sub: bool = True, max_px: int = MAX_PX, kind: str | None = None,
                 hidden: Sequence[int] = ()) -> None:  # fmt: skip
        if kind not in (None, NUMBER, CATEGORY):
            raise ValueError("kind: number, category or None")
        self.grid, self.plane, self.kind, self.sub = grid, plane, kind, bool(sub)
        self.hidden = np.asarray(sorted({int(h) for h in hidden}), dtype=np.int64)
        self.layout = layout(grid, plane, sub=sub, max_px=max_px)
        n = self.layout.width * self.layout.height
        self.value = np.full(n, np.nan, dtype=np.float32)
        self.m = np.zeros(n, dtype=np.uint8)
        self.rows = 0
        self._reg, self._d, self._tab, self._toff = _tables(grid)
        self._ed = np.asarray(grid.sub.div if sub else (1, 1, 1), dtype=np.float64)

    def p_range(self) -> tuple[int, int] | None:
        """[lo, hi) of P the plane can meet (a plane ⟂ Z: one parent layer), None — any P (or nothing: width 0)."""
        if self.plane.axis != 2 or not self.layout.width:
            return None
        layer = self.grid.counts[0] * self.grid.counts[1]
        return self.layout.pa * layer, (self.layout.pa + 1) * layer

    def add(self, p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None, passed: np.ndarray | None = None,
            values: np.ndarray | None = None) -> None:  # fmt: skip
        """One portion of rows (ascending P, as `Model.iter`): `passed` bool [n] (None — all), `values` [n] (float of
        a number, int codes of a category; None only when `kind` is None)."""
        if not self.layout.width:
            return
        p = np.asarray(p)
        n = len(p)
        if (values is None) != (self.kind is None):
            raise ValueError("values are given exactly when kind is")
        for name, a in (("s", s), ("w", w), ("passed", passed), ("values", values)):
            if a is not None and len(a) != n:
                raise ValueError(f"{name}: {len(a)} rows, expected {n}")
        rng = self.p_range()
        if rng is not None:
            lo, hi = int(np.searchsorted(p, np.uint64(rng[0]))), int(np.searchsorted(p, np.uint64(rng[1])))
            p = p[lo:hi]
            s, w = (None if s is None else s[lo:hi]), (None if w is None else w[lo:hi])
            passed = None if passed is None else passed[lo:hi]
            values = None if values is None else values[lo:hi]
        if not len(p):
            return
        has_sub = self.sub and s is not None and w is not None
        keep = None if passed is None else np.asarray(passed, dtype=np.bool_)
        if self.hidden.size and values is not None and self.kind == CATEGORY:
            visible = ~np.isin(np.asarray(values), self.hidden)
            keep = visible if keep is None else keep & visible
        lay, grid, pl = self.layout, self.grid, self.plane
        one = np.zeros(1, dtype=np.uint8)
        miss = -1.0 if self.kind == CATEGORY else np.nan
        self.rows += int(
            _raster(
                np.ascontiguousarray(p, dtype=np.uint64),
                np.ascontiguousarray(s) if has_sub else one.reshape(1, 1),
                np.ascontiguousarray(w) if has_sub else one.reshape(1, 1),
                has_sub,
                keep if keep is not None else np.zeros(1, dtype=np.bool_),
                keep is not None,
                values if values is not None else np.zeros(1),
                values is not None,
                miss,
                grid.counts[0],
                grid.counts[1],
                self._reg,
                self._d,
                self._ed,
                self._tab,
                self._toff,
                pl.axis,
                lay.pa,
                pl.o,
                pl.n,
                lay.fo,
                lay.uq,
                lay.vq,
                lay.du,
                lay.dv,
                lay.width,
                lay.height,
                self.value,
                self.m,
            )  # fmt: skip
        )

    def finish(self) -> Raster:
        return Raster(self.value, self.m, self.layout, self.kind, self.rows, self.plane)


def slice_rows(grid: GridV2, plane: Plane, p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None,
               passed: np.ndarray | None = None, values: np.ndarray | None = None, kind: str | None = None, *,
               sub: bool = True, max_px: int = MAX_PX, hidden: Sequence[int] = ()) -> Raster:  # fmt: skip
    """The raster of the whole arrays of a model (one portion)."""
    sl = Slicer(grid, plane, sub=sub, max_px=max_px, kind=kind, hidden=hidden)
    sl.add(p, s, w, passed, values)
    return sl.finish()


def warm() -> None:
    """Compiles (or loads from the cache) the kernels of the raster and the polygons on one cell (`ops/warm.py`)."""
    from bf_core.blocks.slice_poly import section_polygons

    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [1, 1, 1], "size": [1.0, 1.0, 1.0], "div": [2, 2, 2],
                      "rotation": 0.0, "order": "F"})  # fmt: skip
    p = np.zeros(1, dtype=np.uint64)
    s, w = np.zeros((1, 3), dtype=np.uint8), np.full((1, 3), 2, dtype=np.uint8)
    ok = np.ones(1, dtype=np.bool_)
    for normal in ((0.0, 0.0, 1.0), (1.0, 2.0, 3.0)):
        pl = plane_of(grid, (0.5, 0.5, 0.5), normal)
        for values, kind in ((np.zeros(1), NUMBER), (np.zeros(1, dtype=np.int32), CATEGORY), (None, None)):
            slice_rows(grid, pl, p, s, w, ok, values, kind)
        section_polygons(grid, pl, p, s, w, ok)
