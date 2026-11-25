"""Bricks of the isosurface (ТЗ 8.9.3 steps 3–5; ADR 0026 §4 решения 12, 13): the grid, the values of a brick, the
triangles of a set of bricks, the bricks the surface runs out into. `geo.iso` drives them.

    Grid.from_box(lo, hi, h, levels)   the grid of step h (project coordinates, float64), bricks of 2^levels cells
    Grid.token()                       a stable string of (lo, h, levels): the part of the hash of a stored brick
    BrickCache                         the protocol of the store of node values: `get(index)` / `put(index, values)`
    brick_values(grid, bricks, field, cache, counters)   the values (m×n×n×n) of the nodes of the bricks
    mesh_bricks(grid, bricks, values, scale, ...)        the triangles of the bricks, vertices as edge keys
    exit_bricks(grid, values)          the bricks beyond a brick face the surface of a brick crosses into
    IsoError(code, message, hint)      in Russian, `.as_dict()`

A brick is the cube of 2^L × 2^L × 2^L cells of the step h with the layer of nodes it shares with its neighbours
(n = 2^L + 1 nodes an axis). Its index is that of the coarse cell (step H = 2^L·h) it refines: c = −1 … M an axis.
The nodes of the model are 0 … N = M·2^L; every node beyond them (the layer of the bricks −1 and M) has the value
+H, so the surface is closed one cell beyond the box. A value that is not finite is +H; a value of exactly 0 is
+1e-9·h (never a node on the surface). What a store keeps is that array of values, so a brick read from it is
the brick that was computed.

Triangles. `skimage.measure.marching_cubes` (method 'lewiner') takes the signs and the ambiguity tests; the
bricks of one call lie in one volume, each in its own tile, the cells between the tiles masked out. The
volume skimage takes is float32, and the positions of its vertices are float32 in the volume's index space, so
the vertices are not taken from it but re-made: a vertex of an edge is found by the one coordinate that is not an
integer, its edge is told by the integers (the key: axis and the lower node, in the whole grid — the same
integer for the neighbouring brick), its place is the float64 `i + f_a / (f_a − f_b)` of the true values. So that
the edge a vertex lies on is told surely, the volume of the call is the field `clip(f / scale, ±16)` whose
absolute value is at least 5e-3 (the surface stays at least 5e-3 of an edge from a node in skimage's eyes; it
changes the ambiguous cases only where a node lies that close to the surface, the same in every brick as it is the
function of the node alone). A vertex inside a cell (the centre vertex of the ambiguous cases of Lewiner) has
its cell as the key. The triangles are in the order (cell of the centroid, brick, the number in skimage's output).

numpy only; skimage is imported when a brick is meshed (the module may reach the process of the API).
"""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

__all__ = [
    "BrickCache",
    "BrickMesh",
    "Grid",
    "IsoError",
    "brick_values",
    "exit_bricks",
    "mesh_bricks",
    "sanitize",
]

Field = Callable[[np.ndarray], np.ndarray]

ZERO_SHIFT = 1e-9  # · h: the value of a node that is exactly 0
FLOOR = 5e-3  # the least |value| of the volume skimage takes (in units of `scale`)
CAP = 16.0  # the greatest of it
TOL = 1e-4  # a float32 coordinate this far from an integer is not an integer
NODE_BUDGET = 1 << 19  # nodes (field points) of one chunk of bricks
TILE_SIDE_MAX = 160  # nodes an axis of the volume of one call
SNAP = 1e-6  # of an edge: a vertex this close to a node is that node (8.9.3 step 4: «1e−6·h»)
_KIND_CENTRE = 3
_KIND_NODE = 4
_KEY_LIMIT = 1 << 62


class IsoError(ValueError):
    """A grid or a field the isosurface cannot be made from: {code, message, hint} in Russian."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


class BrickCache(Protocol):
    """The store of the node values of bricks (the files `cache\\implicit\\<модель>\\<хеш кирпича>.npy` of 19.12).
    The key is the index of the brick (its coarse cell, −1 … M an axis); the caller adds what else the hash of a
    brick holds (patches, parameters, `Grid.token()`). `get` gives the array (n, n, n) float64 as it was put, or
    None; both are called from the threads of the run when `threads` > 1."""

    def get(self, index: tuple[int, int, int]) -> np.ndarray | None: ...

    def put(self, index: tuple[int, int, int], values: np.ndarray) -> None: ...


@dataclass(frozen=True)
class Grid:
    lo: tuple[float, float, float]  # project coordinates of node (0, 0, 0)
    h: float
    levels: int
    blocks: tuple[int, int, int]  # coarse cells an axis, M

    @classmethod
    def from_box(cls, lo: Any, hi: Any, h: float, levels: int) -> Grid:
        a = np.asarray(lo, dtype=np.float64).reshape(-1)
        b = np.asarray(hi, dtype=np.float64).reshape(-1)
        if a.shape != (3,) or b.shape != (3,) or not (np.isfinite(a).all() and np.isfinite(b).all()):
            raise IsoError("bad_box", "Охват модели задан неверно: нужны шесть конечных чисел.", "Проверьте охват.")
        if not (h > 0.0 and math.isfinite(h)):
            raise IsoError("bad_param", "Шаг сетки должен быть положительным числом.", "Задайте шаг h > 0.")
        if not 1 <= levels <= 5:
            raise IsoError("bad_param", "Число уровней кирпича — от 1 до 5.", "Задайте L от 1 до 5 (по умолчанию 2).")
        if (b < a).any():
            raise IsoError("bad_box", "Охват модели вывернут: максимум меньше минимума.", "Проверьте охват.")
        step = 1 << levels
        cells = np.maximum(np.ceil((b - a) / h - 1e-9), 1.0)
        blocks = tuple(int(math.ceil(c / step)) for c in cells)
        grid = cls((float(a[0]), float(a[1]), float(a[2])), float(h), int(levels), blocks)  # type: ignore[arg-type]
        dims = grid.key_dims
        if 8 * dims[0] * dims[1] * dims[2] >= _KEY_LIMIT:
            raise IsoError(
                "iso_too_large",
                "Охват модели слишком велик для шага сетки.",
                "Увеличьте шаг h или уменьшите охват модели.",
            )
        return grid

    @property
    def step(self) -> int:
        return 1 << self.levels

    @property
    def n(self) -> int:
        """Nodes of a brick an axis."""
        return self.step + 1

    @property
    def big_h(self) -> float:
        """H = 2^L·h: the value beyond the model."""
        return self.step * self.h

    @property
    def cells(self) -> tuple[int, int, int]:
        s = self.step
        return (self.blocks[0] * s, self.blocks[1] * s, self.blocks[2] * s)

    @property
    def brick_dims(self) -> tuple[int, int, int]:
        """The bricks an axis: the index −1 … M, stored as 0 … M + 1."""
        return (self.blocks[0] + 2, self.blocks[1] + 2, self.blocks[2] + 2)

    @property
    def key_dims(self) -> tuple[int, int, int]:
        s = self.step
        c = self.cells
        return (c[0] + 2 * s + 1, c[1] + 2 * s + 1, c[2] + 2 * s + 1)

    def token(self) -> str:
        """A stable 16-hex string of (lo, h, levels, blocks): add it to the hash of a stored brick."""
        raw = struct.pack("<3ddi3q", *self.lo, self.h, self.levels, *self.blocks)
        return hashlib.sha256(raw).hexdigest()[:16]

    def lin(self, bricks: np.ndarray) -> np.ndarray:
        """The linear number of bricks (m×3 indices) in the order of the index (x slowest)."""
        d = self.brick_dims
        b = np.asarray(bricks, dtype=np.int64) + 1
        return (b[..., 0] * d[1] + b[..., 1]) * d[2] + b[..., 2]

    def unlin(self, lin: np.ndarray) -> np.ndarray:
        d = self.brick_dims
        lin = np.asarray(lin, dtype=np.int64)
        z = lin % d[2]
        y = (lin // d[2]) % d[1]
        x = lin // (d[1] * d[2])
        return np.stack([x, y, z], axis=-1) - 1

    def points(self, idx: np.ndarray) -> np.ndarray:
        """Project coordinates (float64) of the nodes with the integer indices `idx` (…×3)."""
        return np.asarray(self.lo, dtype=np.float64) + np.asarray(idx, dtype=np.float64) * self.h


def sanitize(values: Any, grid: Grid, count: int) -> np.ndarray:
    """The values of a field for the iso: float64 (`count` of them), not finite → +H, 0 → +1e-9·h."""
    try:
        f = np.array(values, dtype=np.float64, copy=True).reshape(-1)
    except (TypeError, ValueError) as e:
        raise IsoError("bad_field", "Поле вернуло не числа.", "Поле должно давать число для каждой точки.") from e
    if f.shape != (count,):
        raise IsoError(
            "bad_field",
            f"Поле вернуло {f.size} значений для {count} точек.",
            "Поле должно давать по одному числу на каждую точку.",
        )
    f[~np.isfinite(f)] = grid.big_h
    f[f == 0.0] = ZERO_SHIFT * grid.h
    return f


def _offsets(n: int) -> np.ndarray:
    g = np.arange(n, dtype=np.int64)
    return np.stack(np.meshgrid(g, g, g, indexing="ij"), axis=-1).reshape(-1, 3)  # (n³, 3), z fastest


def brick_values(
    grid: Grid, bricks: np.ndarray, field: Field, cache: BrickCache | None = None, counters: dict | None = None
) -> np.ndarray:
    """The values (m×n×n×n float64) of the nodes of `bricks` (m×3): from the store, else the field at the nodes
    of the model (the points of the bricks in a row, a brick's n³ together, the nodes beyond the model left out);
    the nodes beyond the model are +H. What was computed is put into the store."""
    n = grid.n
    m = len(bricks)
    out = np.empty((m, n, n, n), dtype=np.float64)
    todo = np.ones(m, dtype=bool)
    if cache is not None:
        for j in range(m):
            got = cache.get((int(bricks[j, 0]), int(bricks[j, 1]), int(bricks[j, 2])))
            if got is not None and getattr(got, "shape", None) == (n, n, n):
                out[j] = got
                todo[j] = False
        if counters is not None:
            counters["cache_hits"] = counters.get("cache_hits", 0) + int(m - todo.sum())
    sel = np.flatnonzero(todo)
    if len(sel):
        idx = (bricks[sel, None, :] * grid.step + _offsets(n)[None, :, :]).reshape(-1, 3)
        high = np.asarray(grid.cells, dtype=np.int64)
        real = ((idx >= 0) & (idx <= high)).all(axis=1)
        flat = np.full(len(idx), grid.big_h, dtype=np.float64)
        count = int(real.sum())
        if count:
            flat[real] = sanitize(field(grid.points(idx[real])), grid, count)
            if counters is not None:
                counters["nodes"] = counters.get("nodes", 0) + count
        out[sel] = flat.reshape(len(sel), n, n, n)
        if cache is not None:
            for j in sel:
                cache.put((int(bricks[j, 0]), int(bricks[j, 1]), int(bricks[j, 2])), out[j].copy())
    return out


def has_crossing(values: np.ndarray) -> np.ndarray:
    """Per brick: the values of its nodes are of both signs."""
    pos = values > 0.0
    flat = pos.reshape(len(values), -1)
    return np.asarray(flat.any(axis=1) & ~flat.all(axis=1))


def exit_bricks(grid: Grid, bricks: np.ndarray, values: np.ndarray) -> np.ndarray:
    """The linear numbers of the bricks (inside the grid) next to `bricks` that the surface runs into: a brick's edge
    that lies in one of its faces and changes the sign has cells of other bricks around it (8.9.3 step 5)."""
    n = grid.n
    step = grid.step
    pos = values > 0.0
    uv = np.zeros((n, n), dtype=bool)
    uv[0, :] = uv[-1, :] = uv[:, 0] = uv[:, -1] = True
    found: list[np.ndarray] = []
    for axis in range(3):
        others = [a for a in range(3) if a != axis]
        s = np.moveaxis(pos, 1 + axis, -1)  # (m, u, v, t)
        cross = (s[..., :-1] != s[..., 1:]) & uv[None, :, :, None]
        b, u, v, _ = np.nonzero(cross)
        if not len(b):
            continue
        for cu in (u - 1, u):
            for cv in (v - 1, v):
                du = np.where(cu < 0, -1, np.where(cu >= step, 1, 0))
                dv = np.where(cv < 0, -1, np.where(cv >= step, 1, 0))
                keep = (du != 0) | (dv != 0)
                if not keep.any():
                    continue
                nb = bricks[b[keep]].copy()
                nb[:, others[0]] += du[keep]
                nb[:, others[1]] += dv[keep]
                found.append(nb)
    if not found:
        return np.zeros(0, dtype=np.int64)
    nb = np.concatenate(found)
    low = np.array([-1, -1, -1], dtype=np.int64)
    high = np.asarray(grid.blocks, dtype=np.int64)
    inside = ((nb >= low) & (nb <= high)).all(axis=1)
    return np.unique(grid.lin(nb[inside]))


@dataclass
class BrickMesh:
    """The triangles of a set of bricks before the vertices of the neighbours are joined (`geo.iso` joins them).
    `keys` — the integer key of every vertex (an edge of the grid or the cell of a centre vertex), `pos` — its place
    in the index space of the grid (float64: the node i is at lo + i·h), `brick` — the number of its brick,
    `faces` — three vertex numbers per triangle, in the output order of skimage within a brick; `cell`, `brick_of`
    — the sort keys of a triangle (the cell of its centroid, its brick), `order` — its number in the output."""

    keys: np.ndarray
    pos: np.ndarray
    brick: np.ndarray
    faces: np.ndarray
    cell: np.ndarray
    brick_of: np.ndarray
    order: np.ndarray


def empty_mesh() -> BrickMesh:
    z = np.zeros(0, dtype=np.int64)
    return BrickMesh(z, np.zeros((0, 3)), z, np.zeros((0, 3), dtype=np.int64), z, z, z)


def topology_volume(values: np.ndarray, scale: float) -> np.ndarray:
    """What skimage takes: float32, the sign of the value, the size clipped to [FLOOR, CAP] of `scale`."""
    g = np.abs(values) / scale
    np.clip(g, FLOOR, CAP, out=g)
    return np.where(values > 0.0, g, -g).astype(np.float32)


def _tiles(k: int) -> int:
    t = int(round(k ** (1.0 / 3.0)))
    while t**3 < k:
        t += 1
    return max(t, 1)


def mesh_bricks(grid: Grid, bricks: np.ndarray, values: np.ndarray, scale: float, *, ascent: bool = False) -> BrickMesh:
    """The triangles of `bricks` (k×3 indices, each with a change of sign among its nodes; the values k×n×n×n).
    The result does not depend on the order of the bricks, nor on which of them are in the same call."""
    k = len(bricks)
    if k == 0:
        return empty_mesh()
    n = grid.n
    side = n + 1
    if side * _tiles(k) > TILE_SIDE_MAX:  # too many for one volume: halves
        half = k // 2
        a = mesh_bricks(grid, bricks[:half], values[:half], scale, ascent=ascent)
        b = mesh_bricks(grid, bricks[half:], values[half:], scale, ascent=ascent)
        return _join(a, b)
    from skimage.measure import marching_cubes

    t = _tiles(k)
    vol = np.ones((t * side, t * side, t * side), dtype=np.float32)
    mask = np.zeros(vol.shape, dtype=bool)
    v6 = vol.reshape(t, side, t, side, t, side)
    m6 = mask.reshape(t, side, t, side, t, side)
    j = np.arange(k)
    tx, ty, tz = j // (t * t), (j // t) % t, j % t
    v6[tx, :n, ty, :n, tz, :n] = topology_volume(values, scale)
    m6[tx, 1:n, ty, 1:n, tz, 1:n] = True  # skimage takes a cell by its upper corner
    try:
        verts, faces, _, _ = marching_cubes(
            vol, 0.0, mask=mask, allow_degenerate=True, gradient_direction="ascent" if ascent else "descent"
        )
    except RuntimeError:  # "No surface found": cannot be, a crossing brick has one
        return empty_mesh()
    return _decode(grid, bricks, values, np.asarray(verts, dtype=np.float32), np.asarray(faces, dtype=np.int64), t)


def _decode(
    grid: Grid, bricks: np.ndarray, values: np.ndarray, verts: np.ndarray, faces: np.ndarray, t: int
) -> BrickMesh:
    n, step = grid.n, grid.step
    side = n + 1
    far = np.abs(verts - np.rint(verts)) > TOL
    count = far.sum(axis=1)
    if (count == 0).any():
        raise IsoError(
            "iso_vertex",
            "Не удалось определить ребро вершины изоповерхности.",
            "Это внутренняя ошибка: сообщите в поддержку, приложив параметры модели.",
        )
    kind = np.where(count == 1, far.argmax(axis=1), _KIND_CENTRE).astype(np.int64)
    base = np.floor(verts).astype(np.int64)
    tile = base // side
    local = base - tile * side
    j = (tile[:, 0] * t + tile[:, 1]) * t + tile[:, 2]
    if (j >= len(bricks)).any() or (local > n - 1).any():
        raise IsoError("iso_vertex", "Вершина изоповерхности вне своего кирпича.", "Это внутренняя ошибка программы.")
    node = bricks[j] * step + local  # the lower node of the edge / the cell, in the grid
    pos = node.astype(np.float64)
    edge = np.flatnonzero(kind < _KIND_CENTRE)
    if len(edge):
        e_axis = kind[edge]
        lo_idx = local[edge]
        hi_idx = lo_idx.copy()
        hi_idx[np.arange(len(edge)), e_axis] += 1
        je = j[edge]
        fa = values[je, lo_idx[:, 0], lo_idx[:, 1], lo_idx[:, 2]]
        fb = values[je, hi_idx[:, 0], hi_idx[:, 1], hi_idx[:, 2]]
        if ((fa > 0.0) == (fb > 0.0)).any():
            raise IsoError(
                "iso_vertex",
                "Вершина изоповерхности лежит на ребре без смены знака.",
                "Это внутренняя ошибка программы.",
            )
        t_edge = fa / (fa - fb)
        pos[edge, e_axis] += t_edge
        # a vertex within SNAP of a node of its edge is that node: the vertices of the edges round a node on the
        # surface join (the key of the node), their slivers vanish, nothing is left open (8.9.3 steps 4 and 7)
        for part, shift_up in ((t_edge < SNAP, 0), (t_edge > 1.0 - SNAP, 1)):
            snap = edge[part]
            if len(snap):
                up = np.zeros((len(snap), 3), dtype=np.int64)
                up[np.arange(len(snap)), e_axis[part]] = shift_up
                node[snap] += up
                pos[snap] = node[snap]
                kind[snap] = _KIND_NODE
    centre = np.flatnonzero(kind == _KIND_CENTRE)
    if len(centre):
        pos[centre] = _centre_vertices(values, j[centre], local[centre], node[centre])
    dims = grid.key_dims
    ig = node + step
    keys = ((ig[:, 0] * dims[1] + ig[:, 1]) * dims[2] + ig[:, 2]) * 8 + kind
    brick_lin = grid.lin(bricks)[j]
    # the faces: drop those with a repeated vertex (a sliver after the snap), keep skimage's order
    order = np.arange(len(faces), dtype=np.int64)
    kf = keys[faces]
    ok = (kf[:, 0] != kf[:, 1]) & (kf[:, 1] != kf[:, 2]) & (kf[:, 0] != kf[:, 2])
    p = pos[faces]
    faces, order, p = faces[ok], order[ok], p[ok]
    cen = np.floor(p.mean(axis=1)).astype(np.int64) + step
    cell = (cen[:, 0] * dims[1] + cen[:, 1]) * dims[2] + cen[:, 2]
    used = np.zeros(len(verts), dtype=bool)
    used[faces.reshape(-1)] = True
    renum = np.cumsum(used) - 1
    return BrickMesh(keys[used], pos[used], brick_lin[used], renum[faces], cell, brick_lin[faces[:, 0]], order)


# the 12 edges of a cell as pairs of its corners (the corner c is (c >> 2 & 1, c >> 1 & 1, c & 1))
_CORNER = np.array([[(c >> 2) & 1, (c >> 1) & 1, c & 1] for c in range(8)], dtype=np.int64)
_EDGES = [(a, b) for a in range(8) for b in range(a + 1, 8) if bin(a ^ b).count("1") == 1]


def _centre_vertices(values: np.ndarray, j: np.ndarray, local: np.ndarray, node: np.ndarray) -> np.ndarray:
    """The centre vertex of an ambiguous cell, as Lewiner takes it: the mean of the points of its cut edges — here
    of the float64 points of the true values, so it is the same whatever the volume the cell was meshed in."""
    corner = np.stack(
        [values[j, local[:, 0] + c[0], local[:, 1] + c[1], local[:, 2] + c[2]] for c in _CORNER], axis=1
    )  # (k, 8)
    total = np.zeros((len(j), 3))
    count = np.zeros(len(j))
    for a, b in _EDGES:
        fa, fb = corner[:, a], corner[:, b]
        cut = (fa > 0.0) != (fb > 0.0)
        t = np.where(cut, fa / np.where(cut, fa - fb, 1.0), 0.0)
        point = _CORNER[a][None, :] + t[:, None] * (_CORNER[b] - _CORNER[a])[None, :]
        total += np.where(cut[:, None], point, 0.0)
        count += cut
    return node.astype(np.float64) + total / np.maximum(count, 1.0)[:, None]  # type: ignore[no-any-return]


def _join(a: BrickMesh, b: BrickMesh) -> BrickMesh:
    shift = len(a.keys)
    return BrickMesh(
        np.concatenate([a.keys, b.keys]),
        np.concatenate([a.pos, b.pos]),
        np.concatenate([a.brick, b.brick]),
        np.concatenate([a.faces, b.faces + shift]),
        np.concatenate([a.cell, b.cell]),
        np.concatenate([a.brick_of, b.brick_of]),
        np.concatenate([a.order, b.order + (a.order.max() + 1 if len(a.order) else 0)]),
    )
