"""The isosurface f = 0 of a field in bricks (ТЗ 8.9.3 steps 1–7; ADR 0026 §4 решения 12, 13). One pipeline for every
field: the RBF of the implicit model, the signed distance of the expansion of a wireframe (`geo.sdf`), the field of
a wing of a fault.

    isosurface(field, lo, hi, h, *, levels=2, near=None, dilate=1, threads=1, tick=None, cache=None) -> IsoMesh
    IsoMesh(vertices float64 V×3, triangles int32 T×3, grid, stats)       f < 0 inside, normals outward
    IsoStats(coarse_nodes, band_bricks, extra_bricks, meshed_bricks, nodes, cache_hits, waves)
    Grid, BrickCache, IsoError      (`geo.iso_bricks`)

`field(points) -> f`: points float64 n×3 in project coordinates, f float64 (n,) — the value at every point and
the same bits for the same point whatever the other points are (the bricks share nodes; a store of values is
read in their place). A value that is not finite is +H (H = 2^L·h; 8.9.3 step 6: beyond the patches, beyond the
model the surface is closed), 0 is +1e-9·h. The grid starts at `lo` with the step h and covers `hi` with a whole number
of bricks of 2^L cells; the nodes beyond are +H, so a body that reaches the box is closed one cell beyond it.

The steps. 1: the values at the nodes of the coarse grid (step H). 2: the band — cells with a change of sign, those
with min |f| < `near`·H (for a domain: a field that is a distance) and every cell within `dilate` cells of them
(26-neighbours; 0 leaves only the first two and the exits of step 5 find the rest). 3: a brick of 2^L × 2^L × 2^L
cells for a cell of the band — `iso_bricks.brick_values` (the store `cache` is asked first, one brick by the index).
4: `marching_cubes` in the bricks, the vertices of the shared edges joined by the integer key of the edge (not by a
tolerance). 5: a brick whose surface crosses a face towards a brick that is not in the band brings that one in;
repeated until none is left. 7: triangles under 1e-12·h² are dropped. The checks (closed, manifold, no self-
intersections, the volume) and the steps of 8 are `geo.solids`, `geo.checks`, `geo.iso_post`.

The result is a function of the field, the box, h and L: not of the order of the bricks, the size of the chunks,
nor of `threads` (bit for bit). The vertices are numbered by their first use in the triangles, the triangles
are in the order (the cell of the centroid, the brick, the number in the output of marching_cubes). `tick(fraction,
message)` is called in the calling thread and may raise to cancel; with `threads` > 1 the field and the store are
called from the threads of the pool. Memory: the coarse grid (4 bytes a node, at most 1e8 nodes) and the triangles
(about 100 bytes a triangle at the end of the run); a chunk of bricks is 2^19 field points.

Time and memory (подшаг 19.5; a sphere of 2.06 M triangles, 1.03 M vertices, h = 1, the field a norm — the cost of
the field itself is not in it; 4 cores): L = 2, dilate = 1: 5.6 s on one thread (2.7 s on 4), peak 0.7 GB, 25.9 M field
points (1.8 M of them the coarse grid, 24.2 M in 193 000 bricks, 64 000 of them with the surface); dilate = 0: 3.5 s,
9.8 M points; L = 3: 6.9 s / 3.9 s, 35.3 M / 12.0 M points; L = 1, dilate = 0: 5.3 s, 21.0 M points. `IsoStats.nodes`
+ `coarse_nodes` is the number of points the field is asked for: the time of the surface of a model is that
number × the time of a point of its field (19.4: ns a term).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

import numpy as np

from .iso_bricks import (
    NODE_BUDGET,
    BrickCache,
    BrickMesh,
    Grid,
    IsoError,
    brick_values,
    exit_bricks,
    has_crossing,
    mesh_bricks,
    sanitize,
)

__all__ = ["BrickCache", "Grid", "IsoError", "IsoMesh", "IsoStats", "isosurface"]

Tick = Callable[[float, str], None] | None
Field = Callable[[np.ndarray], np.ndarray]

MAX_COARSE_NODES = 100_000_000
_CORNERS = [(a, b, c) for a in (0, 1) for b in (0, 1) for c in (0, 1)]


@dataclass
class IsoStats:
    coarse_nodes: int = 0  # field points of step 1
    band_bricks: int = 0  # bricks of the band (steps 2–3)
    extra_bricks: int = 0  # bricks the surface ran out into (step 5)
    meshed_bricks: int = 0  # bricks with a change of sign
    nodes: int = 0  # field points of the bricks (without the store's, without the nodes beyond the model)
    cache_hits: int = 0  # bricks read from the store
    waves: int = 0  # rounds of step 5 (the first is the band)
    scale: float = 0.0  # the scale of the volume skimage takes


@dataclass
class IsoMesh:
    vertices: np.ndarray
    triangles: np.ndarray
    grid: Grid
    stats: IsoStats = dc_field(default_factory=IsoStats)

    @property
    def empty(self) -> bool:
        return len(self.triangles) == 0


def _tick(tick: Tick, fraction: float, message: str) -> None:
    if tick is not None:
        tick(float(min(max(fraction, 0.0), 1.0)), message)


# --- step 1–2: the coarse grid and the band --------------------------------------------------------------


def _coarse_field(grid: Grid, field: Field, tick: Tick, stats: IsoStats) -> np.ndarray:
    """f at the nodes of the coarse grid, float32, with the layer of +H around: indices 0 … M + 2."""
    m = grid.blocks
    count = (m[0] + 1) * (m[1] + 1) * (m[2] + 1)
    if count > MAX_COARSE_NODES:
        raise IsoError(
            "iso_too_large",
            f"Грубая сетка изоповерхности слишком велика ({count:,} узлов).".replace(",", " "),
            "Увеличьте шаг h или число уровней L, либо уменьшите охват модели.",
        )
    fc = np.full((m[0] + 3, m[1] + 3, m[2] + 3), grid.big_h, dtype=np.float32)
    shape = (m[0] + 1, m[1] + 1, m[2] + 1)
    for start in range(0, count, NODE_BUDGET):
        flat = np.arange(start, min(start + NODE_BUDGET, count), dtype=np.int64)
        c = np.stack(np.unravel_index(flat, shape), axis=-1)
        f = sanitize(field(grid.points(c * grid.step)), grid, len(c))
        fc[c[:, 0] + 1, c[:, 1] + 1, c[:, 2] + 1] = np.clip(f, -1e30, 1e30).astype(np.float32)
        stats.coarse_nodes += len(c)
        _tick(tick, 0.08 * (start + len(c)) / count, "Изоповерхность: грубая сетка")
    return fc


def _corner(a: np.ndarray, d: tuple[int, int, int]) -> np.ndarray:
    x, y, z = a.shape
    return a[d[0] : x - 1 + d[0], d[1] : y - 1 + d[1], d[2] : z - 1 + d[2]]


def _band(fc: np.ndarray, grid: Grid, near: float | None, dilate: int) -> tuple[np.ndarray, float]:
    """The bricks (cells −1 … M, stored 0 … M + 1) of the band, and the scale of the field in them."""
    pos = fc > 0.0
    any_pos = np.zeros(tuple(s - 1 for s in fc.shape), dtype=bool)
    all_pos = np.ones_like(any_pos)
    for d in _CORNERS:
        c = _corner(pos, d)
        any_pos |= c
        all_pos &= c
    band = any_pos & ~all_pos
    if near is not None and near > 0.0:
        low = np.full(band.shape, np.inf, dtype=np.float32)
        absf = np.abs(fc)
        for d in _CORNERS:
            np.minimum(low, _corner(absf, d), out=low)
        band |= low < np.float32(near * grid.big_h)
    for _ in range(max(int(dilate), 0)):
        band = _box3(band)
    scale = float(grid.big_h)
    if band.any():
        top = np.zeros(band.shape, dtype=np.float32)
        absf = np.abs(fc)
        for d in _CORNERS:
            np.maximum(top, _corner(absf, d), out=top)
        scale = max(scale, float(top[band].max()))
    return band, scale


def _box3(band: np.ndarray) -> np.ndarray:
    """`band` grown to the 26-neighbours: the 1-D growth along every axis in turn."""
    out = band
    for ax in range(3):
        a = [slice(None)] * 3
        b = [slice(None)] * 3
        a[ax], b[ax] = slice(1, None), slice(None, -1)
        grown = out.copy()
        grown[tuple(a)] |= out[tuple(b)]
        grown[tuple(b)] |= out[tuple(a)]
        out = grown
    return out


# --- the run ---------------------------------------------------------------------------------------------


def _assemble(grid: Grid, parts: list[BrickMesh]) -> tuple[np.ndarray, np.ndarray]:
    """Joins the vertices of the bricks by their keys, orders the triangles, numbers the vertices by first use."""
    parts = [p for p in parts if len(p.faces)]
    if not parts:
        return np.zeros((0, 3), dtype=np.float64), np.zeros((0, 3), dtype=np.int32)
    shifts = np.cumsum([0] + [len(p.keys) for p in parts[:-1]])
    keys = np.concatenate([p.keys for p in parts])
    pos = np.concatenate([p.pos for p in parts])
    brick = np.concatenate([p.brick for p in parts])
    faces = np.concatenate([p.faces + s for p, s in zip(parts, shifts, strict=True)])
    cell = np.concatenate([p.cell for p in parts])
    brick_of = np.concatenate([p.brick_of for p in parts])
    number = np.concatenate([p.order for p in parts])
    # one vertex of an edge: the position of the lowest brick that has it (the same bits whatever the order)
    o = np.lexsort((brick, keys))
    ks = keys[o]
    first = np.ones(len(ks), dtype=bool)
    first[1:] = ks[1:] != ks[:-1]
    inv = np.empty(len(ks), dtype=np.int64)
    inv[o] = np.cumsum(first) - 1
    upos = pos[o][first]
    tri = inv[faces]
    # the triangles: (cell of the centroid, brick, the number in the output of its call); a brick is in one call
    f = np.lexsort((number, brick_of, cell))
    tri = tri[f]
    flat = tri.reshape(-1)
    uniq, first_idx = np.unique(flat, return_index=True)
    used = uniq[np.argsort(first_idx, kind="stable")]
    renum = np.empty(len(upos), dtype=np.int64)
    renum[used] = np.arange(len(used))
    tri = renum[tri]
    verts = np.asarray(grid.lo, dtype=np.float64) + upos[used] * grid.h
    if len(verts) >= 2**31:
        raise IsoError("iso_too_large", "Изоповерхность слишком велика.", "Увеличьте шаг h.")
    return np.ascontiguousarray(verts), np.ascontiguousarray(tri.astype(np.int32))


def isosurface(
    field: Field,
    lo: Any,
    hi: Any,
    h: float,
    *,
    levels: int = 2,
    near: float | None = None,
    dilate: int = 1,
    threads: int = 1,
    tick: Tick = None,
    cache: BrickCache | None = None,
    chunk_bricks: int | None = None,
    brick_order: Callable[[np.ndarray], np.ndarray] | None = None,
) -> IsoMesh:
    """The surface f = 0 of `field` in the box [lo, hi] of the step h (see the module). `near` — the share of H under
    which |f| puts a cell into the band (for a field that is a distance); `chunk_bricks` and `brick_order` (a
    permutation of the linear numbers of the bricks of a round) are for the tests: the result does not depend
    on them."""
    grid = Grid.from_box(lo, hi, h, levels)
    stats = IsoStats()
    workers = max(int(threads), 1)
    per_chunk = max(int(chunk_bricks) if chunk_bricks else NODE_BUDGET // grid.n**3, 1)
    _tick(tick, 0.0, "Изоповерхность: грубая сетка")
    fc = _coarse_field(grid, field, tick, stats)
    band, scale = _band(fc, grid, near, dilate)
    del fc
    stats.scale = scale
    queue = np.flatnonzero(band.reshape(-1)).astype(np.int64)  # the linear numbers in the order of the index
    del band
    stats.band_bricks = len(queue)
    seen = np.zeros(math.prod(grid.brick_dims), dtype=bool)
    seen[queue] = True
    parts: list[BrickMesh] = []
    done = 0
    _tick(tick, 0.08, "Изоповерхность: кирпичи")

    def work(lin: np.ndarray) -> tuple[BrickMesh, np.ndarray, dict, int]:
        bricks = grid.unlin(lin)
        counters: dict[str, int] = {}
        vals = brick_values(grid, bricks, field, cache, counters)
        cross = has_crossing(vals)
        bricks, vals = bricks[cross], vals[cross]
        return mesh_bricks(grid, bricks, vals, scale), exit_bricks(grid, bricks, vals), counters, int(cross.sum())

    while len(queue):
        stats.waves += 1
        order = queue if brick_order is None else np.asarray(brick_order(queue), dtype=np.int64)
        chunks = [order[i : i + per_chunk] for i in range(0, len(order), per_chunk)]
        wave_total = done + len(order)
        exits: list[np.ndarray] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, c) for c in chunks] if workers > 1 else None
            for i, c in enumerate(chunks):
                mesh, ex, counters, crossing = futures[i].result() if futures else work(c)
                parts.append(mesh)
                exits.append(ex)
                stats.nodes += counters.get("nodes", 0)
                stats.cache_hits += counters.get("cache_hits", 0)
                stats.meshed_bricks += crossing
                done += len(c)
                _tick(tick, 0.08 + 0.8 * done / max(wave_total, 1), "Изоповерхность: кирпичи")
        new = np.unique(np.concatenate(exits)) if exits else np.zeros(0, dtype=np.int64)
        new = new[~seen[new]]
        seen[new] = True
        stats.extra_bricks += len(new)
        queue = new
    _tick(tick, 0.9, "Изоповерхность: сшивка кирпичей")
    vertices, triangles = _assemble(grid, parts)
    _tick(tick, 1.0, "Изоповерхность готова")
    return IsoMesh(vertices, triangles, grid, stats)
