"""Columns of parents: which parents are inside a closed wireframe (ТЗ 8.7.3, steps 1, 2 and 6; ADR 0023 §6).

    mesh = Mesh.of(grid, grid.to_local(v), t, name, stats=…, index=N)   # checks the body (422), builds the XY index
    c = inside_columns(grid, mesh, threads=…, tick=…)                  # Columns: inside[P] = 1 by the centre ray
    flags = inside_points(mesh, q_local, threads=…)                    # the same for any points (sub-cells, rows)

`v` — the vertices in the local axes of the grid (`grid.to_local`, float64), `t` — int32 triangles. A regular
grid only (`variable_grid` otherwise). A parent is (i, j, k), P = i + nx·(j + ny·k); its centre is q = ((i + ½)·dx,
(j + ½)·dy, (k + ½)·dz). Parents that no triangle touches (`boundary.boundary_parents`) are wholly inside or
wholly outside; this module tells which by the parity of the crossings of a vertical ray through the centre.

Rays (step 6). The ray of a point goes up (+Z) from its (x, y) shifted by (√2·1e−6·ux, √3·1e−6·uy), u — the size
of the sub-cell (`Mesh.unit`, the sub-cell of the grid by default): a ray never goes through an edge or a vertex
that is a node of the block grid (a mesh is drawn on round numbers). A point is inside when an odd number of
crossings lie below its z (a crossing at the very z of the point is not below). Should a ray still hit an edge
exactly, the crossing is taken by the same rule for both triangles of the edge (the point is on the same side of
the line of the edge for both: the sides are computed from the edge with its ends in a fixed order), so it is
counted once. Vertical triangles are never crossed.

The index. Triangles by the cells of the XY plane, CSR (`RayIndex`): the cell is `stride` parents wide (1, grown
by 2 while the lists would be over `MAX_ENTRIES`), only over the XY box of the body. A ray reads the list of its
cell — the triangles near it, not the whole body.

Orientation (step 1). The crossings of a ray with a closed solid alternate: the same sign twice in a row (the
sign — the side the triangle looks to), an odd number of them, or rays starting with both signs mean that the
normals disagree or the shells overlap — 422 `wireframe_orientation`. A body wholly inward is a solid like any
other (the parity does not care). Open or inconsistent meshes are refused before: `check_body`.

Threads. Strips of rows of columns / chunks of points are fixed (not by the number of threads); a column is
computed alone and written to its own cells, the counters are integers: the result is the same bit for bit with
1, 2, 4, 8 threads. Kernels are `nogil` (ADR 0014).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks.schema import GridV2
from bf_core.composites import strips
from bf_core.geo.checks import orientation_conflicts
from bf_core.geo.meshes import edge_summary
from bf_core.geo.tri_kernels import mesh_arrays
from bf_core.jit import njit

RAY_OFFSET = (math.sqrt(2.0) * 1e-6, math.sqrt(3.0) * 1e-6)  # of the size of the sub-cell, along X and Y
MAX_ENTRIES = 50_000_000  # (triangle, cell) pairs of the XY index: 4 bytes each
STRIP_COLUMNS = 2048  # columns in a strip of the dense stage
POINT_CHUNK = 1 << 16  # points in a chunk of `inside_points`

FIX_HINT = (
    "Заполнять можно только замкнутыми телами: проверьте каркас («Проверить каркас») и запустите «Исправить каркас»."
)

Tick = Callable[[float], None]


# --- errors ------------------------------------------------------------------------------------------------


def _wire_error(code: str, message: str, index: int, name: str) -> ApiError:
    return ApiError(422, code, message, FIX_HINT, extra={"param": f"wireframes.{index}.id", "name": name})


def check_body(
    name: str, v: Any, t: Any, *, stats: dict[str, Any] | None = None, index: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """Vertices float64 n×3 and triangles int32 m×3 of a body that can fill (step 1) — or 422 `wireframe_open`
    (an open edge, an edge of three triangles, nothing but a surface, no triangles, a value that is not a number)
    / `wireframe_orientation` (neighbouring triangles wound opposite ways). `stats` — `wireframes.stats` when
    it is stored: a body it says is closed is not scanned for edges again. `index` — the place of the body in the
    list of the request (`param: wireframes.<index>.id`)."""
    try:
        va, ta = mesh_arrays(v, t)
    except ValueError as exc:
        raise _wire_error(
            "wireframe_open",
            f"Каркас «{name}» задан неверно: нужны вершины (n × 3) и номера вершин треугольников (m × 3).",
            index,
            name,
        ) from exc
    if len(ta) == 0:
        raise _wire_error("wireframe_open", f"Каркас «{name}» не замкнут: в нём нет треугольников.", index, name)
    if not np.isfinite(va).all():
        raise _wire_error("wireframe_open", f"Каркас «{name}» не замкнут: неопределённые координаты.", index, name)
    s = stats or {}
    if s.get("closed"):
        opened = nonmanifold = 0
    elif stats is not None and "closed" in stats:
        opened, nonmanifold = int(s.get("open_edges") or 0), int(s.get("nonmanifold_edges") or 0)
        if not opened and not nonmanifold:
            raise _wire_error("wireframe_open", f"Каркас «{name}» не замкнут: это поверхность, а не тело.", index, name)
    else:
        opened, nonmanifold = edge_summary(ta, len(va))
    if opened or nonmanifold:
        detail = f"открытых рёбер — {opened}" if opened else f"рёбер с тремя и более треугольниками — {nonmanifold}"
        raise _wire_error("wireframe_open", f"Каркас «{name}» не замкнут: {detail}.", index, name)
    conflicts = orientation_conflicts(ta, len(va))
    if conflicts:
        raise _wire_error(
            "wireframe_orientation",
            f"Каркас «{name}»: нормали соседних треугольников не согласованы (рёбер — {conflicts}).",
            index,
            name,
        )
    return va, ta


# --- kernels -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _cells(v, t, m, gx, gy, ci0, cj0, ncx, ncy):  # type: ignore[no-untyped-def]
    """The cells (i0, i1, j0, j1) of the XY index that the box of triangle m covers, clipped; i1 < i0 — none
    (also for a triangle without an area in plan: no ray crosses it)."""
    a, b, c = t[m, 0], t[m, 1], t[m, 2]
    ax, ay, bx, by, cx, cy = v[a, 0], v[a, 1], v[b, 0], v[b, 1], v[c, 0], v[c, 1]
    if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) == 0.0:
        return 0, -1, 0, -1
    i0 = int(math.floor(min(ax, bx, cx) / gx)) - ci0
    i1 = int(math.floor(max(ax, bx, cx) / gx)) - ci0
    j0 = int(math.floor(min(ay, by, cy) / gy)) - cj0
    j1 = int(math.floor(max(ay, by, cy) / gy)) - cj0
    return max(i0, 0), min(i1, ncx - 1), max(j0, 0), min(j1, ncy - 1)


@njit(cache=True, nogil=True)
def _index_count(v, t, gx, gy, ci0, cj0, ncx, ncy, counts):  # type: ignore[no-untyped-def]
    for m in range(t.shape[0]):
        i0, i1, j0, j1 = _cells(v, t, m, gx, gy, ci0, cj0, ncx, ncy)
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                counts[i + ncx * j] += 1


@njit(cache=True, nogil=True)
def _index_fill(v, t, gx, gy, ci0, cj0, ncx, ncy, ptr, idx):  # type: ignore[no-untyped-def]
    cursor = ptr[:-1].copy()
    for m in range(t.shape[0]):
        i0, i1, j0, j1 = _cells(v, t, m, gx, gy, ci0, cj0, ncx, ncy)
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                c = i + ncx * j
                idx[cursor[c]] = m
                cursor[c] += 1


@njit(cache=True, nogil=True)
def _side(ax, ay, bx, by, px, py, ccw):  # type: ignore[no-untyped-def]
    """Is p on the inner side of the edge a → b of a triangle wound ccw (seen from above) or not? The side is
    taken from the line with its ends in a fixed order, a tie counts as the positive side: two triangles that
    share the edge get the same answer for it, so a ray on the edge is in exactly one of them."""
    lo_first = ax < bx or (ax == bx and ay < by)
    if lo_first:
        val = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    else:
        val = (ax - bx) * (py - by) - (ay - by) * (px - bx)
    return (val >= 0.0) == (lo_first == ccw)


@njit(cache=True, nogil=True)
def _hit(v, t, m, px, py):  # type: ignore[no-untyped-def]
    """(sign, z): the vertical line through (px, py) crosses triangle m at height z; sign +1 — the triangle
    looks down (the ray enters the solid of an outward mesh), −1 — up; 0 — no crossing."""
    a, b, c = t[m, 0], t[m, 1], t[m, 2]
    ax, ay, az = v[a, 0], v[a, 1], v[a, 2]
    bx, by, bz = v[b, 0], v[b, 1], v[b, 2]
    cx, cy, cz = v[c, 0], v[c, 1], v[c, 2]
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if area == 0.0:
        return 0, 0.0
    ccw = area > 0.0
    if not (_side(ax, ay, bx, by, px, py, ccw) and _side(bx, by, cx, cy, px, py, ccw)
            and _side(cx, cy, ax, ay, px, py, ccw)):  # fmt: skip
        return 0, 0.0
    nx = (by - ay) * (cz - az) - (bz - az) * (cy - ay)
    ny = (bz - az) * (cx - ax) - (bx - ax) * (cz - az)
    z = az - (nx * (px - ax) + ny * (py - ay)) / area
    z = min(max(z, min(az, bz, cz)), max(az, bz, cz))
    return (-1 if ccw else 1), z


@njit(cache=True, nogil=True)
def ray_crossings(v, t, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, px, py, zs, ss, ts):  # type: ignore[no-untyped-def]
    """The crossings of the vertical line through (px, py) with the triangles of the index: n of them, sorted
    by (z, triangle) into zs (z), ss (sign, see `_hit`), ts (triangle). The scratch arrays are `RayIndex.cap`
    long. A point off the index has none."""
    ci = int(math.floor(px / gx)) - ci0
    cj = int(math.floor(py / gy)) - cj0
    if ci < 0 or ci >= ncx or cj < 0 or cj >= ncy:
        return 0
    cell = ci + ncx * cj
    n = 0
    for e in range(ptr[cell], ptr[cell + 1]):
        m = idx[e]
        s, z = _hit(v, t, m, px, py)
        if s != 0:
            q = n
            while q > 0 and (zs[q - 1] > z or (zs[q - 1] == z and ts[q - 1] > m)):
                zs[q], ss[q], ts[q] = zs[q - 1], ss[q - 1], ts[q - 1]
                q -= 1
            zs[q], ss[q], ts[q] = z, s, m
            n += 1
    return n


@njit(cache=True, nogil=True)
def _k_above(z, dz):  # type: ignore[no-untyped-def]
    """The least k whose centre (k + ½)·dz is above z."""
    k = int(math.floor(z / dz - 0.5))
    while (k + 0.5) * dz <= z:
        k += 1
    while (k - 0.5) * dz > z:
        k -= 1
    return k


@njit(cache=True, nogil=True)
def _k_upto(z, dz):  # type: ignore[no-untyped-def]
    """The greatest k whose centre is not above z."""
    k = int(math.floor(z / dz - 0.5))
    while (k + 1.5) * dz <= z:
        k += 1
    while (k + 0.5) * dz > z:
        k -= 1
    return k


@njit(cache=True, nogil=True)
def _rows(  # type: ignore[no-untyped-def]
    v, t, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, cap, nx, ny, nz, dx, dy, dz, ex, ey, c0, c1, r0, r1, inside,
):  # fmt: skip
    """Columns (i, j), i in [c0, c1], j in [r0, r1): the cells whose centre is inside, by the parity of the
    crossings below it, are set in `inside`; (rays, crossings, bad rays, rays that start with +, with −)."""
    zs = np.empty(cap, np.float64)
    ss = np.empty(cap, np.int8)
    ts = np.empty(cap, np.int32)
    stat = np.zeros(5, np.int64)
    top = (nz + 1) * dz
    for j in range(r0, r1):
        py = (j + 0.5) * dy + ey
        for i in range(c0, c1 + 1):
            px = (i + 0.5) * dx + ex
            n = ray_crossings(v, t, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, px, py, zs, ss, ts)
            stat[0] += 1
            if n == 0:
                continue
            stat[1] += n
            bad = n % 2 == 1
            for q in range(1, n):
                if ss[q] == ss[q - 1]:
                    bad = True
            if bad:
                stat[2] += 1
            if ss[0] > 0:
                stat[3] += 1
            else:
                stat[4] += 1
            base = i + nx * j
            q = 0
            while q < n:  # inside between the crossings 2m and 2m + 1 (the last one of an odd list — to the top)
                za = min(max(zs[q], -dz), top)
                zb = top if q + 1 >= n else min(max(zs[q + 1], -dz), top)
                k0 = max(_k_above(za, dz), 0)
                k1 = min(_k_upto(zb, dz), nz - 1)
                for k in range(k0, k1 + 1):
                    inside[base + nx * ny * k] = 1
                q += 2
    return stat


@njit(cache=True, nogil=True)
def _points(  # type: ignore[no-untyped-def]
    v, t, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, cap, q, ex, ey, lo, hi, out,
):  # fmt: skip
    zs = np.empty(cap, np.float64)
    ss = np.empty(cap, np.int8)
    ts = np.empty(cap, np.int32)
    for m in range(lo, hi):
        n = ray_crossings(v, t, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, q[m, 0] + ex, q[m, 1] + ey, zs, ss, ts)
        below = 0
        for e in range(n):
            if zs[e] < q[m, 2]:
                below += 1
        out[m] = below % 2


# --- the mesh ----------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class RayIndex:
    """Triangles by the XY cells (CSR): the cells `ci0 + i`, `cj0 + j` of the size (gx, gy) from the local zero,
    i < ncx, j < ncy; `cap` — the longest list. Empty (`ptr` of one 0) when the body is off the grid."""

    ptr: np.ndarray  # int64 [ncx·ncy + 1]
    idx: np.ndarray  # int32
    ncx: int
    ncy: int
    ci0: int
    cj0: int
    gx: float
    gy: float
    stride: int
    cap: int


@dataclass(frozen=True, eq=False)
class Mesh:
    """A body in the local axes of a grid, ready for rays: `v` float64 n×3, `t` int32 m×3, `lo`/`hi` its box,
    `index`; `grid`, the parent size `size` and the unit `unit` (the sub-cell along X and Y) of the rays."""

    name: str
    v: np.ndarray
    t: np.ndarray
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]
    grid: GridV2
    size: tuple[float, float, float]
    unit: tuple[float, float]
    index: RayIndex

    @classmethod
    def of(
        cls,
        grid: GridV2,
        v: Any,
        t: Any,
        name: str = "",
        *,
        stats: dict[str, Any] | None = None,
        index: int = 0,
        check: bool = True,
        unit: tuple[float, float] | None = None,
        max_entries: int = MAX_ENTRIES,
    ) -> Mesh:
        """`v` — the vertices in the local axes (`grid.to_local`). `check=False` — the body was checked."""
        size = grid.regular("Заполнение")
        va, ta = check_body(name, v, t, stats=stats, index=index) if check else mesh_arrays(v, t)
        sub = grid.subcell()
        u = (float(unit[0]), float(unit[1])) if unit is not None else (sub[0], sub[1])
        lo = tuple(float(x) for x in va.min(axis=0))
        hi = tuple(float(x) for x in va.max(axis=0))
        return cls(name, va, ta, lo, hi, grid, size, u, _build_index(grid, size, va, ta, lo, hi, max_entries))  # type: ignore[arg-type]

    @classmethod
    def of_project(cls, grid: GridV2, v: Any, t: Any, name: str = "", **kw: Any) -> Mesh:
        """The same from vertices in the axes of the project."""
        return cls.of(grid, grid.to_local(v), t, name, **kw)

    def offsets(self, unit: tuple[float, float] | None = None) -> tuple[float, float]:
        u = self.unit if unit is None else unit
        return RAY_OFFSET[0] * u[0], RAY_OFFSET[1] * u[1]


def _build_index(
    grid: GridV2,
    size: tuple[float, float, float],
    v: np.ndarray,
    t: np.ndarray,
    lo: Sequence[float],
    hi: Sequence[float],
    max_entries: int,
) -> RayIndex:
    nx, ny, _ = grid.counts
    stride = 1
    while True:
        gx, gy = size[0] * stride, size[1] * stride
        ci0, cj0 = max(0, math.floor(lo[0] / gx)), max(0, math.floor(lo[1] / gy))
        ci1 = min(-(-nx // stride) - 1, math.floor(hi[0] / gx))
        cj1 = min(-(-ny // stride) - 1, math.floor(hi[1] / gy))
        ncx, ncy = ci1 - ci0 + 1, cj1 - cj0 + 1
        if ncx <= 0 or ncy <= 0:
            return RayIndex(np.zeros(1, np.int64), np.zeros(0, np.int32), 0, 0, 0, 0, gx, gy, stride, 1)
        counts = np.zeros(ncx * ncy, np.int64)
        _index_count(v, t, gx, gy, ci0, cj0, ncx, ncy, counts)
        total = int(counts.sum())
        if total <= max_entries or (ncx == 1 and ncy == 1):
            break
        stride *= 2
    ptr = np.zeros(ncx * ncy + 1, np.int64)
    np.cumsum(counts, out=ptr[1:])
    idx = np.empty(total, np.int32)
    _index_fill(v, t, gx, gy, ci0, cj0, ncx, ncy, ptr, idx)
    return RayIndex(ptr, idx, ncx, ncy, ci0, cj0, gx, gy, stride, max(int(counts.max()), 1))


def _args(mesh: Mesh) -> tuple[Any, ...]:
    ix = mesh.index
    return mesh.v, mesh.t, ix.ptr, ix.idx, ix.ncx, ix.ncy, ix.ci0, ix.cj0, ix.gx, ix.gy


# --- the dense stage ---------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Columns:
    """`inside[P]` — 1: the centre of the parent P is inside the body (parity of the centre ray), uint8 over the
    whole grid; `rays`, `crossings` — what was cast and found; `orientation` — "outward" / "inward" (the side the
    normals look to; None — the body is not under any ray)."""

    inside: np.ndarray
    rays: int
    crossings: int
    orientation: str | None

    @property
    def count(self) -> int:
        return int(np.count_nonzero(self.inside))


def orientation_error(mesh: Mesh, bad: int, index: int = 0) -> ApiError:
    return _wire_error(
        "wireframe_orientation",
        f"Каркас «{mesh.name}»: нормали треугольников не согласованы или оболочки каркаса пересекаются "
        f"(вертикальных лучей с нарушением — {bad}).",
        index,
        mesh.name,
    )


def inside_columns(
    grid: GridV2,
    mesh: Mesh,
    *,
    unit: tuple[float, float] | None = None,
    threads: int = 1,
    tick: Tick | None = None,
    strip: int = STRIP_COLUMNS,
    index: int = 0,
) -> Columns:
    """Rays through the centres of all columns of the grid in the box of the body; the centre of every parent by
    the parity of the crossings below it. 422 `wireframe_orientation` when the crossings disagree."""
    nx, ny, nz = grid.counts
    dx, dy, dz = mesh.size
    inside = np.zeros(nx * ny * nz, np.uint8)
    ex, ey = mesh.offsets(unit)
    # the columns whose ray can meet the body: its box widened by the offset
    c0 = max(0, math.floor((mesh.lo[0] - ex) / dx - 0.5))
    c1 = min(nx - 1, math.ceil((mesh.hi[0] - ex) / dx - 0.5))
    r0 = max(0, math.floor((mesh.lo[1] - ey) / dy - 0.5))
    r1 = min(ny - 1, math.ceil((mesh.hi[1] - ey) / dy - 0.5)) + 1
    if c1 < c0 or r1 <= r0 or mesh.index.ncx == 0:
        return Columns(inside, 0, 0, None)
    args = _args(mesh)
    rows_per = max(1, int(strip) // (c1 - c0 + 1))

    def one(lo: int, hi: int) -> np.ndarray:
        return _rows(*args, mesh.index.cap, nx, ny, nz, dx, dy, dz, ex, ey, c0, c1, r0 + lo, r0 + hi, inside)  # type: ignore[call-arg, no-any-return]

    stat = np.zeros(5, np.int64)
    for part in strips.run(one, r1 - r0, threads=threads, tick=tick, size=rows_per):
        stat += part
    rays, crossings, bad, pos, neg = (int(x) for x in stat)
    if bad or (pos and neg):
        raise orientation_error(mesh, bad or min(pos, neg), index)
    return Columns(inside, rays, crossings, None if not (pos or neg) else ("outward" if pos else "inward"))


# --- any points --------------------------------------------------------------------------------------------


def inside_points(
    mesh: Mesh,
    q: Any,
    *,
    unit: tuple[float, float] | None = None,
    threads: int = 1,
    tick: Tick | None = None,
    chunk: int = POINT_CHUNK,
) -> np.ndarray:
    """bool [n]: is each point q (n×3, local axes, float64) inside the body — by the parity of the crossings of
    its own ray (shifted as above, `unit` — the sub-cell the offset is a share of) below it."""
    pts = np.ascontiguousarray(q, dtype=np.float64).reshape(-1, 3)
    out = np.zeros(len(pts), np.uint8)
    if len(pts) == 0 or mesh.index.ncx == 0:
        return out.astype(bool)
    ex, ey = mesh.offsets(unit)
    args = _args(mesh)

    def one(lo: int, hi: int) -> None:
        _points(*args, mesh.index.cap, pts, ex, ey, lo, hi, out)  # type: ignore[call-arg]

    strips.run(one, len(pts), threads=threads, tick=tick, size=int(chunk))
    return out.astype(bool)
