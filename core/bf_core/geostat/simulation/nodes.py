"""The nodes of a simulation: the grid, the spiral template, the search of simulated nodes (ТЗ 8.8.1; ADR 0024 §9;
Д-23; Э18б, 18.30).

    grid = NodeGrid.of(counts, p)                  the targets: parents P = i + nx·(j + ny·k) of a regular model (a node
                                                   is a target, its number is its position in `p`)
    tpl = spiral_template(steps, ell, cm, limit=…, shape=…)   i4 [k, 3]: the offsets of the grid inside the search
                                                   ellipsoid, nearest first
    got = nearest_nodes(grid, simulated, tpl, node, n)        the numbers of the ≤ n nearest simulated nodes of a node
    n, idx = sample_neighbours(searcher, centres)            the conditioning samples of every node (pass 1 of the
                                                   `Searcher` of 18.6; the search is not repeated here)
    find_nodes(index, nx, ny, nz, simulated, tpl, cell, n, out) -> int    the kernel (njit, nogil): for SGS / SIS

The template (GSLIB `sgsim`: the spiral search of the previously simulated nodes). The offsets (di, dj, dk) ≠ 0 of the
grid with the anisotropic distance of the search ellipsoid h_s = ‖S·(di·dx, dj·dy, dk·dz)‖ ≤ r1 (the bound of
`Searcher`: r1·(1 + 1e−9) + 1e−9), sorted by the proximity — by h_s ascending; with a covariance model `cm` — by C(Δ)
descending, as GSLIB does (the nested structures of different anisotropy order the nodes differently from the
ellipsoid). Equal keys (the keys differing by ≤ 1e−12 relative are equal, the symmetric offsets ±Δ are equal to the
bit) go by the offset itself: (di, dj, dk) ascending. So the table does not depend on the platform order of the sort.
A node takes the first `n` simulated nodes of the template in this order; the offsets off the grid or on a cell that
is not a target are skipped.

Limits: `limit` offsets (the nearest; the template of a huge ellipsoid on a fine grid is cut by it — 131072 ≈ a
sphere of 31 cells), the box of the enumeration ≤ 2²¹ cells (a larger ellipsoid is shrunk, the same radius scale for
the three axes, to fit); `shape` cuts the box to the grid (a 100×100×1 grid has no offsets in k). The number of nodes
`n` is 0–64 (Д-23; 0 — SGS from the samples alone).

The arrays of the kernels are writable (the wrappers copy a read-only input once), so every kernel has one compiled
signature (lesson 17.39б); `warm_nodes()` compiles them and runs the wrappers on read-only arrays.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

from bf_core.geostat.estimate.cov import CovModel, cov_c
from bf_core.geostat.estimate.search import Ellipsoid, Searcher, _own
from bf_core.geostat.estimate.store_base import bad
from bf_core.jit import njit

Tick = Callable[[float | None, str | None], None]

NODES_MAX = 64  # Д-23: nodes 0–64
TEMPLATE_MAX = 1 << 17
BOX_MAX = 1 << 21
CELLS_MAX = 2**31 - 1  # the index of the grid is i4
PORTION = 1 << 16  # nodes per query of the samples
TIE = 1e-12  # relative: the keys closer than this are equal


def check_nodes(nodes: object) -> int:
    """The number of simulated nodes of the neighbourhood: an integer 0–64; 422 `bad_param` {nodes}."""
    if isinstance(nodes, bool) or not isinstance(nodes, int | np.integer) or not 0 <= int(nodes) <= NODES_MAX:
        raise bad("nodes", f"Число ранее смоделированных узлов: нужно целое число от 0 до {NODES_MAX}.",
                  "Задайте, например, 12.")  # fmt: skip
    return int(nodes)


# --- the grid of the targets -----------------------------------------------------------------------------------------


class NodeGrid:
    """The targets of a simulation on the regular grid: `cell[node]` = P of the node, `index[P]` = the node or −1.
    A node number is the position in the order given (the order of the model); the cells that are not targets
    (another domain, outside) are −1 and are never simulated."""

    def __init__(self, shape: tuple[int, int, int], cell: np.ndarray, index: np.ndarray) -> None:
        self.shape = shape
        self.cell = cell  # int64 [n]
        self.index = index  # int32 [cells]

    @classmethod
    def of(cls, counts: Sequence[int], p: np.ndarray) -> NodeGrid:
        """`counts` = (nx, ny, nz) of the model, `p` — the parents of the targets, distinct, in the order of the
        model. 422 `bad_param` {targets}: a number off the grid, a repeat, a grid of more than 2³¹−1 cells."""
        nx, ny, nz = (int(c) for c in counts)
        if min(nx, ny, nz) < 1:
            raise bad("targets", "Сетка модели пуста.", "Проверьте число ячеек модели по осям.")
        cells = nx * ny * nz
        if cells > CELLS_MAX:
            raise bad("targets", f"Сетка из {cells} ячеек слишком велика для моделирования.",
                      "Моделируйте часть модели (ограничитель) или укрупните ячейку.")  # fmt: skip
        cell = np.asarray(p).reshape(-1).astype(np.int64)
        if cell.size and (int(cell.min()) < 0 or int(cell.max()) >= cells):
            raise bad("targets", "Номер блока выходит за пределы сетки модели.", "Проверьте столбец idx модели.")
        index = np.full(cells, -1, dtype=np.int32)
        index[cell] = np.arange(cell.size, dtype=np.int32)
        if cell.size and not np.array_equal(index[cell], np.arange(cell.size, dtype=np.int32)):
            raise bad("targets", "Родители целей повторяются.", "Проверьте столбец idx модели.")
        return cls((nx, ny, nz), cell, index)

    @property
    def n(self) -> int:
        return int(self.cell.shape[0])

    def ijk(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The indices (i, j, k) of every node (int64)."""
        nx, ny, _ = self.shape
        k, rest = np.divmod(self.cell, nx * ny)
        j, i = np.divmod(rest, nx)
        return i, j, k


# --- the template ----------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _cov_keys(kinds: np.ndarray, pars: np.ndarray, head: np.ndarray, d: np.ndarray, out: np.ndarray) -> None:
    for a in range(d.shape[0]):
        out[a] = cov_c(kinds, pars, head, d[a, 0], d[a, 1], d[a, 2])


def _steps(steps: Sequence[float]) -> np.ndarray:
    s = np.asarray(steps, dtype=np.float64).reshape(-1)
    if s.shape != (3,) or not np.isfinite(s).all() or not (s > 0.0).all():
        raise bad("steps", "Шаг сетки: нужны три положительных числа.", "Проверьте размеры блока модели.")
    return s


def _half_extents(ell: Ellipsoid, steps: np.ndarray, shape: Sequence[int] | None) -> tuple[np.ndarray, float]:
    """Half-sizes of the box of the template in cells (the ellipsoid inside, the grid cut) and the scale of the
    radius (1, or less when the box of the enumeration did not fit into `BOX_MAX`)."""
    inv = np.linalg.inv(ell.S)
    reach = ell.r1 * np.sqrt((inv * inv).sum(axis=1))  # the half-sizes of the ellipsoid along the axes (world)
    cap = None if shape is None else np.asarray(shape, dtype=np.int64) - 1
    scale = 1.0
    while True:
        nh = np.floor(reach * scale / steps + 1e-9).astype(np.int64)
        if cap is not None:
            nh = np.minimum(nh, np.maximum(cap, 0))
        if float(np.prod(2.0 * nh + 1.0)) <= BOX_MAX:
            return nh, scale
        scale *= 0.9


def spiral_template(steps: Sequence[float], ell: Ellipsoid, cm: CovModel | None = None, *,
                    limit: int = TEMPLATE_MAX, shape: Sequence[int] | None = None) -> np.ndarray:  # fmt: skip
    """The offsets (di, dj, dk) of the grid [k, 3] int32 inside the search ellipsoid, nearest first (see the module).
    `steps` — the cell (dx, dy, dz) in the local frame of the model; `ell` — the ellipsoid in the same frame; `cm` —
    the covariance model (the order by C descending) or None (by h_s); `shape` — the grid, to cut the box."""
    st = _steps(steps)
    if isinstance(limit, bool) or int(limit) < 1:
        raise bad("limit", "Размер шаблона: нужно целое число, не меньше 1.", "Проверьте параметры поиска.")
    nh, scale = _half_extents(ell, st, shape)
    rng = [np.arange(-int(h), int(h) + 1, dtype=np.int32) for h in nh]
    di, dj, dk = (a.reshape(-1) for a in np.meshgrid(*rng, indexing="ij"))
    d = np.empty((di.size, 3), dtype=np.float64)
    d[:, 0], d[:, 1], d[:, 2] = di * st[0], dj * st[1], dk * st[2]
    h = ell.h(d)
    keep = (h <= (ell.r1 * scale) * (1.0 + 1e-9) + 1e-9) & ((di != 0) | (dj != 0) | (dk != 0))
    di, dj, dk, d, h = di[keep], dj[keep], dk[keep], d[keep], h[keep]
    if cm is None:
        key = h / ell.r1
    else:
        kinds, pars, head = _own(cm.kinds, np.int64), _own(cm.pars, np.float64), _own(cm.head, np.float64)
        c = np.empty(d.shape[0], dtype=np.float64)
        _cov_keys(kinds, pars, head, np.ascontiguousarray(d), c)
        key = -c / float(head[1])
    group = _tie_groups(key)
    order = np.lexsort((dk, dj, di, group))[: int(limit)]
    return np.ascontiguousarray(np.stack((di[order], dj[order], dk[order]), axis=1), dtype=np.int32)


def _tie_groups(key: np.ndarray) -> np.ndarray:
    """The rank of every key where the keys closer than `TIE` (relative to the larger of 1 and |key|) are one."""
    o = np.argsort(key, kind="stable")
    ks = key[o]
    new = np.zeros(ks.size, dtype=np.int64)
    if ks.size > 1:
        new[1:] = np.diff(ks) > TIE * np.maximum(1.0, np.abs(ks[1:]))
    rank = np.empty(ks.size, dtype=np.int64)
    rank[o] = np.cumsum(new)
    return rank


# --- the search of the simulated nodes ---------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def find_nodes(index: np.ndarray, nx: int, ny: int, nz: int, simulated: np.ndarray, template: np.ndarray, cell: int,
               n_want: int, out: np.ndarray) -> int:  # fmt: skip
    """The first `n_want` simulated nodes of the template around the cell `cell` (P) → `out` (int64, the numbers of the
    nodes, nearest first); returns how many. `index` — `NodeGrid.index` (int32 [cells]), `simulated` — bool [nodes]."""
    found = 0
    if n_want <= 0:
        return found
    plane = nx * ny
    k = cell // plane
    rest = cell - k * plane
    j = rest // nx
    i = rest - j * nx
    for t in range(template.shape[0]):
        kk = k + template[t, 2]
        if kk < 0 or kk >= nz:
            continue
        jj = j + template[t, 1]
        if jj < 0 or jj >= ny:
            continue
        ii = i + template[t, 0]
        if ii < 0 or ii >= nx:
            continue
        node = index[ii + nx * (jj + ny * kk)]
        if node >= 0 and simulated[node]:
            out[found] = node
            found += 1
            if found >= n_want:
                break
    return found


@njit(cache=True, nogil=True)
def replay(index: np.ndarray, nx: int, ny: int, nz: int, cells: np.ndarray, order: np.ndarray, template: np.ndarray,
           n_want: int, out_nodes: np.ndarray, out_n: np.ndarray) -> None:  # fmt: skip
    """The nodes found at every step of a path (the node is marked simulated after its search): `out_nodes[s]` [n_want]
    and `out_n[s]` for the step s. What SGS sees, without the values (the tests, the measure)."""
    simulated = np.zeros(cells.shape[0], dtype=np.bool_)
    for s in range(order.shape[0]):
        node = order[s]
        out_n[s] = find_nodes(index, nx, ny, nz, simulated, template, cells[node], n_want, out_nodes[s])
        simulated[node] = True


def nearest_nodes(grid: NodeGrid, simulated: np.ndarray, template: np.ndarray, node: int,
                  n_nodes: int) -> np.ndarray:  # fmt: skip
    """The numbers (int64) of the ≤ `n_nodes` simulated nodes nearest to `node` by the template, nearest first."""
    n_nodes = check_nodes(n_nodes)
    if not 0 <= int(node) < grid.n:
        raise bad("node", f"Узел {node}: допустимо 0–{grid.n - 1}.", "Проверьте путь.")
    sim = _own(simulated, np.bool_)
    if sim.shape != (grid.n,):
        raise bad("simulated", "Признак «смоделирован»: по одному значению на узел.", "Сообщите разработчику.")
    out = np.empty(max(n_nodes, 1), dtype=np.int64)
    nx, ny, nz = grid.shape
    got = find_nodes(_own(grid.index, np.int32), nx, ny, nz, sim, _own(template, np.int32), int(grid.cell[node]),
                     n_nodes, out)  # fmt: skip
    return out[:got].copy()


def replay_neighbours(grid: NodeGrid, order: np.ndarray, template: np.ndarray,
                      n_nodes: int) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    """(nodes int64 [steps, n_nodes] with −1 after the last, count int64 [steps]) — what each step of the path `order`
    finds among the nodes simulated before it."""
    n_nodes = check_nodes(n_nodes)
    order = _own(order, np.int64)
    if order.size and (int(order.min()) < 0 or int(order.max()) >= grid.n):
        raise bad("path", "Путь содержит несуществующий узел.", "Сообщите разработчику.")
    out = np.full((order.size, max(n_nodes, 1)), -1, dtype=np.int64)
    cnt = np.zeros(order.size, dtype=np.int64)
    nx, ny, nz = grid.shape
    replay(_own(grid.index, np.int32), nx, ny, nz, _own(grid.cell, np.int64), order, _own(template, np.int32),
           n_nodes, out, cnt)  # fmt: skip
    return out[:, :n_nodes], cnt


# --- the conditioning samples ----------------------------------------------------------------------------------------


def sample_neighbours(sr: Searcher, centres: np.ndarray, *, portion: int = PORTION,
                      tick: Tick | None = None) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    """The samples of every node: (idx int32 [m, n_max] — the numbers of the samples of the row, −1 after the last,
    in the order (h_s, number); n int16 [m]) by pass 1 of `sr`. A node of a NaN centre has none. The samples do not
    depend on the path, so they are found once for all the realizations (the portions bound the memory); the passes
    n_min, k_oct… of the estimation are not applied: SGS takes what is there (`n` may be 0). `tick(progress, None)`
    after a portion — it raises to cancel."""
    c = np.asarray(centres, dtype=np.float64).reshape(-1, 3)
    m = c.shape[0]
    width = sr.passes[0].n_max
    idx = np.full((m, width), -1, dtype=np.int32)
    cnt = np.zeros(m, dtype=np.int16)
    step = max(int(portion), 1)
    for a in range(0, m, step):
        nb = sr.gather(c[a : a + step], 0)
        idx[a : a + step] = nb.idx
        cnt[a : a + step] = nb.n
        if tick is not None:
            tick(min(a + step, m) / m, None)
    return idx, cnt


# --- the warm-up -------------------------------------------------------------------------------------------------


def warm_nodes() -> None:
    """Compiles the kernels (one signature each) and runs the wrappers on writable and read-only arrays."""
    from bf_core.geostat.estimate.cov import cov_model
    from bf_core.geostat.variogram.model import Model, Structure

    ell = Ellipsoid.of((20.0, 10.0, 5.0), (30.0, 20.0, 10.0))
    model = Model(0.1, (Structure("spherical", 0.9, 30.0, 20.0, 10.0, 20.0, 10.0, 5.0),))
    p = np.array([0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 14, 16, 17, 18, 20, 21, 22, 24, 25, 26, 28, 29], dtype=np.uint64)
    order = np.random.default_rng(1).permutation(p.size).astype(np.int64)
    for read_only in (False, True):
        grid = NodeGrid.of((4, 3, 3), p)
        cm = cov_model(model)
        tpl = spiral_template((5.0, 5.0, 5.0), ell, cm, shape=grid.shape)
        sim = np.zeros(grid.n, dtype=np.bool_)
        sim[order[: grid.n // 2]] = True
        if read_only:
            for a in (cm.kinds, cm.pars, cm.head, grid.index, grid.cell, tpl, sim, order):
                a.setflags(write=False)
            tpl = spiral_template((5.0, 5.0, 5.0), ell, cm, shape=grid.shape)
        nearest_nodes(grid, sim, tpl, int(order[-1]), 12)
        replay_neighbours(grid, order, tpl, 12)
        spiral_template((5.0, 5.0, 5.0), ell, shape=grid.shape)
