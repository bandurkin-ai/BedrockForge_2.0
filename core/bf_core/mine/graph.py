"""The closure graph of the pit optimiser on the regular grid of PARENT blocks (подшаг 21.4; ТЗ 8.11.3; ADR 0028
decisions 13, 16). Pure functions over explicit arrays: the module knows nothing of the economics, the slope template
or the project — the inputs are integers per cell, an «air» mask, the template index of a cell and the offset tables.

CONVENTION (the same in the template of 21.3, the solvers and the references of the tests). The grid is
(nx, ny, nz), the parent index P = i + nx·(j + ny·k); the level k grows UP (k = 0 is the bottom level). An offset is a
row (Δi, Δj, m), m ≥ 1 (int32), and points from a block to its PREDECESSOR — the block that must be mined before it:
(i + Δi, j + Δj, k + m), m levels UP. The arc of the network is `block → predecessor` with an infinite capacity; the
table of a block is the table of ITS template index (the domain of the LOWER block, ТЗ 8.11.2). Offsets of one table
need not be sorted or free of repeats beyond what the caller wants (a repeat is a repeated arc, harmless).

NODES are the cells that are not «air». A predecessor beyond the grid or in «air» — no arc (free). Nodes are numbered
by growing P; the dense index (int32) covers only the TRIMMED BOX of the nodes (the bounding box of the non-air cells),
−1 — no node: `index[(i − i0) + mx·((j − j0) + my·(k − k0))]`. Limits (ADR 0028 decision 13): cells of the box
≤ 2³¹ − 1 and the index ≤ 1 GB (so ≤ 2²⁸ cells), nodes ≤ 10 million — otherwise 422 `pit_too_large`.

`PitGraph` holds no values of a particular RF beyond `value` (int64, the v′ of the solver): `with_values` keeps the
structure and swaps the values (shells re-use one index for every RF). All functions are side-effect free; the arrays
of a graph must be treated as read-only.

Public: `build_graph`, `restrict_graph` (a subgraph of kept nodes — the cones, the shells), `cone_mask` (union of the
cones over the nodes with v′ > 0), `predecessors` (CSR of the arcs, for OR-Tools and the explicit-arcs mode of HPF),
`closure_violations` / `is_closed`, `parent_index` / `expand_mask` (back to the parent grid).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError

MAX_NODES = 10_000_000
MAX_BOX_CELLS = 2**31 - 1
MAX_INDEX_BYTES = 1 << 30  # the dense int32 index of the box


@dataclass(frozen=True, eq=False)
class PitGraph:
    """The graph of a grid of parents. All arrays are numpy; the node order is the order of growing P."""

    dims: tuple[int, int, int]  # (nx, ny, nz) of the parent grid
    origin: tuple[int, int, int]  # (i0, j0, k0) of the trimmed box in the parent grid
    shape: tuple[int, int, int]  # (mx, my, mz) of the trimmed box
    pos: NDArray[np.int32]  # (n,) linear index of a node in the box: i + mx·(j + my·k)
    index: NDArray[np.int32]  # (mx·my·mz,) node of a box cell or −1
    value: NDArray[np.int64]  # (n,) v′ of the solver
    tpl: NDArray[np.uint8]  # (n,) the offset table of a node
    tab_ptr: NDArray[np.int64]  # (T + 1,) table t is tab_off[tab_ptr[t]:tab_ptr[t + 1]]
    tab_off: NDArray[np.int32]  # (E, 3) (Δi, Δj, m) rows of all tables
    level_ptr: NDArray[np.int64]  # (mz + 1,) the nodes of the level k are level_ptr[k]:level_ptr[k + 1]

    @property
    def n(self) -> int:
        return int(self.pos.shape[0])

    @property
    def kernel_args(self) -> tuple[NDArray[np.int32], NDArray[np.uint8], NDArray[np.int32], NDArray[np.int64],
                                   NDArray[np.int32], int, int, int]:  # fmt: skip
        """(pos, tpl, index, tab_ptr, tab_off, mx, my, mz) — the head of the arguments of every kernel."""
        mx, my, mz = self.shape
        return self.pos, self.tpl, self.index, self.tab_ptr, self.tab_off, mx, my, mz

    def with_values(self, value: NDArray[np.int64]) -> PitGraph:
        """The same structure with other v′ (int64, per node)."""
        v = np.asarray(value)
        if v.shape != (self.n,) or not np.issubdtype(v.dtype, np.integer):
            raise ValueError("value must be an integer array with one entry per node")
        return replace(self, value=v.astype(np.int64, copy=False))


# --- limits ----------------------------------------------------------------------------------------------------------


def check_size(n_nodes: int, box_cells: int) -> None:
    """422 `pit_too_large` when the graph does not fit the limits (nodes, cells of the box, bytes of the index)."""
    if n_nodes > MAX_NODES:
        _too_large(f"в графе {n_nodes:,} блоков, предел — {MAX_NODES:,}".replace(",", " "), n_nodes, box_cells)
    if box_cells > MAX_BOX_CELLS or box_cells * 4 > MAX_INDEX_BYTES:
        _too_large(f"охват блоков содержит {box_cells:,} ячеек, индекс графа не поместится в память".replace(",", " "),
                   n_nodes, box_cells)  # fmt: skip


def _too_large(why: str, n_nodes: int, box_cells: int) -> None:
    raise ApiError(
        422,
        "pit_too_large",
        f"Модель слишком велика для оптимизации карьера: {why}.",
        "Укрупните модель регуляризацией: Блочные модели → Регуляризация.",
        {"nodes": int(n_nodes), "box_cells": int(box_cells)},
    )


# --- tables ----------------------------------------------------------------------------------------------------------


def pack_tables(tables: Sequence[NDArray[np.int32]]) -> tuple[NDArray[np.int64], NDArray[np.int32]]:
    """(tab_ptr, tab_off) of the list of offset tables; a table is an int array (n, 3) of (Δi, Δj, m), m ≥ 1."""
    if not 1 <= len(tables) <= 256:
        raise ValueError("1…256 offset tables are expected")
    parts = []
    ptr = [0]
    for t in tables:
        a = np.asarray(t)
        if a.ndim != 2 or a.shape[1] != 3 or not np.issubdtype(a.dtype, np.integer):
            raise ValueError("an offset table is an integer array (n, 3) of (Δi, Δj, m)")
        if a.shape[0] and int(a[:, 2].min()) < 1:
            raise ValueError("an offset must point up: m >= 1")
        if a.size and (int(np.abs(a).max()) > 2**30):
            raise ValueError("an offset is too large")
        parts.append(a.astype(np.int32, copy=False))
        ptr.append(ptr[-1] + a.shape[0])
    return np.asarray(ptr, dtype=np.int64), np.ascontiguousarray(np.concatenate(parts, axis=0))


# --- building --------------------------------------------------------------------------------------------------------


def _assemble(
    dims: tuple[int, int, int],
    i: NDArray[np.int64],
    j: NDArray[np.int64],
    k: NDArray[np.int64],
    value: NDArray[np.int64],
    tpl: NDArray[np.uint8],
    tab_ptr: NDArray[np.int64],
    tab_off: NDArray[np.int32],
) -> PitGraph:
    """The graph of nodes with grid coordinates (i, j, k) sorted by growing P (the box and the index are made here)."""
    n = len(i)
    if n == 0:
        z = np.zeros(0, dtype=np.int32)
        return PitGraph(dims, (0, 0, 0), (0, 0, 0), z, z.copy(), value[:0], tpl[:0], tab_ptr, tab_off,
                        np.zeros(1, dtype=np.int64))  # fmt: skip
    i0, i1 = int(i.min()), int(i.max()) + 1
    j0, j1 = int(j.min()), int(j.max()) + 1
    k0, k1 = int(k.min()), int(k.max()) + 1
    mx, my, mz = i1 - i0, j1 - j0, k1 - k0
    check_size(n, mx * my * mz)
    kk = k - k0
    pos64 = (i - i0) + mx * ((j - j0) + my * kk)
    index = np.full(mx * my * mz, -1, dtype=np.int32)
    index[pos64] = np.arange(n, dtype=np.int32)
    level_ptr = np.searchsorted(kk, np.arange(mz + 1)).astype(np.int64)
    return PitGraph(dims, (i0, j0, k0), (mx, my, mz), pos64.astype(np.int32), index, value, tpl, tab_ptr, tab_off,
                    level_ptr)  # fmt: skip


def build_graph(
    dims: tuple[int, int, int],
    value: NDArray[np.int64],
    air: NDArray[np.bool_],
    tpl: NDArray[np.uint8] | None,
    tables: Sequence[NDArray[np.int32]],
) -> PitGraph:
    """The graph of a grid (nx, ny, nz) of parents; every array is 1-D of length nx·ny·nz in the order of P.

    `value` — integer v′ per cell (the value of an «air» cell is ignored); `air` — True: no node; `tpl` — the index of
    the offset table of a cell (None — table 0 everywhere); `tables` — the offset tables (see the module docstring).
    422 `pit_too_large` — see `check_size`."""
    nx, ny, nz = (int(d) for d in dims)
    total = nx * ny * nz
    v = np.asarray(value)
    a = np.asarray(air)
    if v.shape != (total,) or a.shape != (total,) or a.dtype != np.bool_:
        raise ValueError("value and air must be 1-D arrays of length nx·ny·nz (air — bool)")
    if not np.issubdtype(v.dtype, np.integer):
        raise ValueError("value must be integer (v′ of the solver)")
    t = None if tpl is None else np.asarray(tpl)
    if t is not None and t.shape != (total,):
        raise ValueError("tpl must be a 1-D array of length nx·ny·nz")
    tab_ptr, tab_off = pack_tables(tables)
    if t is not None and total and int(t.max()) >= len(tab_ptr) - 1:
        raise ValueError("the index of a table is out of the list of tables")
    m3 = ~a.reshape(nz, ny, nx)
    n = int(np.count_nonzero(m3))
    if n == 0:
        return _assemble((nx, ny, nz), *(np.zeros(0, dtype=np.int64),) * 3, np.zeros(0, dtype=np.int64),
                         np.zeros(0, dtype=np.uint8), tab_ptr, tab_off)  # fmt: skip
    ki = np.flatnonzero(m3.any(axis=(1, 2)))
    ji = np.flatnonzero(m3.any(axis=(0, 2)))
    ii = np.flatnonzero(m3.any(axis=(0, 1)))
    box = (slice(int(ki[0]), int(ki[-1]) + 1), slice(int(ji[0]), int(ji[-1]) + 1), slice(int(ii[0]), int(ii[-1]) + 1))
    mb = m3[box]
    check_size(n, mb.size)  # before the index is allocated
    vb = v.reshape(nz, ny, nx)[box][mb].astype(np.int64, copy=False)
    tb = np.zeros(n, dtype=np.uint8) if t is None else t.astype(np.uint8, copy=False).reshape(nz, ny, nx)[box][mb]
    kz, jy, ix = np.nonzero(mb)  # C order of (k, j, i) == the order of growing P
    return _assemble((nx, ny, nz), ix.astype(np.int64) + box[2].start, jy.astype(np.int64) + box[1].start,
                     kz.astype(np.int64) + box[0].start, vb, tb, tab_ptr, tab_off)  # fmt: skip


def _coords(g: PitGraph) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.int64]]:
    """Grid coordinates (i, j, k) of the nodes in the PARENT grid."""
    mx, my, _ = g.shape
    p = g.pos.astype(np.int64)
    k = p // (mx * my)
    r = p - k * (mx * my)
    j = r // mx
    i = r - j * mx
    return i + g.origin[0], j + g.origin[1], k + g.origin[2]


def restrict_graph(g: PitGraph, keep: NDArray[np.bool_]) -> tuple[PitGraph, NDArray[np.int64]]:
    """The subgraph of the nodes `keep` (bool per node) and `ids`, the nodes of `g` it holds (new u ↔ `ids[u]`).

    A node that is dropped is as «air»: the arcs to it are gone (the cones — the dropped nodes are outside every cone,
    so nothing that is kept needs them; the shells — nodes of the pit above are mined, the ones outside are free).
    The box is trimmed again, the index and `level_ptr` are rebuilt; values and tables are kept."""
    keep = np.asarray(keep)
    if keep.shape != (g.n,) or keep.dtype != np.bool_:
        raise ValueError("keep must be a bool array with one entry per node")
    ids = np.flatnonzero(keep)
    i, j, k = _coords(g)
    sub = _assemble(g.dims, i[ids], j[ids], k[ids], g.value[ids], g.tpl[ids], g.tab_ptr, g.tab_off)
    return sub, ids


def parent_index(g: PitGraph) -> NDArray[np.int64]:
    """P = i + nx·(j + ny·k) of every node in the parent grid (growing)."""
    i, j, k = _coords(g)
    nx, ny, _ = g.dims
    return i + nx * (j + ny * k)


def expand_mask(g: PitGraph, mask: NDArray[np.bool_]) -> NDArray[np.bool_]:
    """A bool per node → a bool per parent cell (nx·ny·nz, order of P); the cells without a node are False."""
    out = np.zeros(g.dims[0] * g.dims[1] * g.dims[2], dtype=bool)
    out[parent_index(g)[np.asarray(mask, dtype=bool)]] = True
    return out


# --- arcs, cones, closure --------------------------------------------------------------------------------------------


def predecessors(g: PitGraph) -> tuple[NDArray[np.int64], NDArray[np.int32]]:
    """CSR of the arcs: the predecessors of node u are `indices[indptr[u]:indptr[u + 1]]` (int32, in table order)."""
    from bf_core.mine import graph_kernels as K

    deg = K.degrees(*g.kernel_args)
    indptr = np.zeros(g.n + 1, dtype=np.int64)
    np.cumsum(deg, out=indptr[1:])
    indices = np.empty(int(indptr[-1]), dtype=np.int32)
    K.fill_arcs(*g.kernel_args, indptr, indices)
    return indptr, indices


def cone_mask(g: PitGraph, seed: NDArray[np.bool_] | None = None) -> NDArray[np.bool_]:
    """The union of the cones over the seeds (default: the nodes with v′ > 0), the seeds included — bool per node.

    A block outside it cannot be in the optimal pit: nothing positive needs it (ТЗ 8.11.3, the preprocessing). Bottom to
    top by levels, `needed[k] |= dilate(needed[k − m], the table of level m)`; parallel by the nodes of a level, the
    logical OR — the same bits on any number of threads."""
    from bf_core.mine import graph_kernels as K

    s = (g.value > 0) if seed is None else np.asarray(seed, dtype=bool)
    if s.shape != (g.n,):
        raise ValueError("seed must be a bool array with one entry per node")
    needed = s.astype(np.uint8)
    if g.n:
        K.cone_kernel(*g.kernel_args, g.level_ptr, needed)
    return needed.view(np.bool_)


def closure_violations(g: PitGraph, mined: NDArray[np.bool_]) -> tuple[int, int, int]:
    """(arcs u → q with u mined and q not, the first u, the first q); (0, −1, −1) — the set is closed."""
    from bf_core.mine import graph_kernels as K

    m = np.asarray(mined)
    if m.shape != (g.n,) or m.dtype != np.bool_:
        raise ValueError("mined must be a bool array with one entry per node")
    bad, u, q = K.closure_violations(*g.kernel_args, m)
    return int(bad), int(u), int(q)


def is_closed(g: PitGraph, mined: NDArray[np.bool_]) -> bool:
    """No mined block has an unmined predecessor."""
    return closure_violations(g, mined)[0] == 0
