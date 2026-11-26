"""The z-grids of the shells — the surfaces of the pit (подшаг 21.9; ТЗ 8.11.4; ADR 0028 decisions 21, 25).

    zg = z_grids(grid, smap, relief)               # every shell 1…N in ONE pass over the mined parents
    zg.z[n - 1]                                    # float64 [ny·nx]: the surface of the shell n, column c = i + nx·j
    data = zg.to_parquet()                         # `sidecar:zgrid`: i, j, z1 … zN (Parquet bytes)
    zg = ZGrids.from_parquet(data)

The nodes are the CENTRES of the columns (i, j) of the parent grid, in the project axes (a model turned about Z gets
turned nodes: the column of the local grid is kept, its centre goes through `grid.to_project`). The z of the column
(i, j) in the surface of the shell n:
  * the column has a parent of the pit C_n = {PIT <= n} — the BOTTOM of the LOWEST mined parent of the column
    (`corner_z + edges_z[k_min]`; the minimum over the shells 1…n is kept as the shells grow: «наименьший уровень на
    оболочку + накопленный минимум по n»);
  * otherwise the relief at the centre of the column (`dz_kernels.surface_index`: z of the surface; no relief at all —
    the TOP of the grid); the centre outside the plan of the relief — NaN (a node that is not built; a gap is never 0).
No database here: the write of `sidecar:zgrid` is 21.10 / 21.11, the wireframes — 21.12.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks.schema import GridV2, ijk_of
from bf_core.geo import dz_kernels
from bf_core.mine.fields import ShellMap

MAX_COLUMNS = 1 << 26  # the plan of the grid: 67 million columns


def too_many_shells(n: int, limit: int) -> ApiError:
    return ApiError(422, "bad_param", f"Оболочки {n} нет: в сценарии их {limit}.",
                    "Выберите номера от 1 до числа оболочек.", {"param": "shells"})  # fmt: skip


def centres(grid: GridV2) -> np.ndarray:
    """float64 [ny·nx, 2]: the centres of the columns in the project axes, column c = i + nx·j."""
    nx, ny, _ = grid.counts
    if nx * ny > MAX_COLUMNS:
        raise ApiError(422, "pit_too_large", "План модели слишком велик для поверхностей оболочек.",
                       "Укрупните модель регуляризацией.")  # fmt: skip
    local = geometry.row_local_centres(grid, np.arange(nx * ny, dtype=np.uint64))  # the level k = 0 of each column
    local[:, 2] = 0.0
    return grid.to_project(local)[:, :2]


@dataclass(frozen=True)
class ZGrids:
    """The surfaces of the shells `shells` (numbers 1…N) over the plan nx × ny: `z` float64 [len(shells), ny·nx]."""

    nx: int
    ny: int
    shells: tuple[int, ...]
    z: np.ndarray

    def __post_init__(self) -> None:
        if self.z.shape != (len(self.shells), self.nx * self.ny):
            raise ValueError("z must have one row per shell and one column per node of the plan")

    def of(self, shell: int) -> np.ndarray:
        """The surface of one shell (a view, float64 [ny·nx])."""
        return self.z[self.shells.index(int(shell))]

    # --- `sidecar:zgrid`: Parquet (i, j, z1 … zN) --------------------------------------------------------------------

    def to_parquet(self) -> bytes:
        import pyarrow as pa
        import pyarrow.parquet as pq

        c = np.arange(self.nx * self.ny, dtype=np.int64)
        cols = {"i": (c % self.nx).astype(np.int32), "j": (c // self.nx).astype(np.int32)}
        cols.update({f"z{n}": self.z[a] for a, n in enumerate(self.shells)})
        out = io.BytesIO()
        pq.write_table(pa.table(cols), out, compression="zstd")
        return out.getvalue()

    @classmethod
    def from_parquet(cls, data: bytes) -> ZGrids:
        import pyarrow.parquet as pq

        t = pq.read_table(io.BytesIO(data))
        i, j = t.column("i").to_numpy(), t.column("j").to_numpy()
        nx, ny = (int(i.max()) + 1, int(j.max()) + 1) if len(i) else (0, 0)
        if nx * ny != len(i) or not np.array_equal(i + nx * j, np.arange(len(i))):
            raise ValueError("the grid of the surfaces is not a full plan in the order i + nx·j")
        names = sorted((n for n in t.column_names if n[:1] == "z" and n[1:].isdigit()), key=lambda n: int(n[1:]))
        z = np.empty((len(names), len(i)), dtype=np.float64)
        for a, n in enumerate(names):
            z[a] = t.column(n).to_numpy()
        return cls(nx, ny, tuple(int(n[1:]) for n in names), z)


# --- the pass ------------------------------------------------------------------------------------------------------


def base_surface(grid: GridV2, relief: dz_kernels.SurfaceIndex | None) -> np.ndarray:
    """float64 [ny·nx]: z of a column without a mined parent — the relief at its centre (NaN outside its plan), no
    relief — the top of the grid."""
    nx, ny, _ = grid.counts
    if relief is None:
        return np.full(nx * ny, grid.corner[2] + float(grid.edges(2)[-1]))
    xy = centres(grid)
    pts = np.zeros((len(xy), 3))
    pts[:, :2] = xy  # z = 0: dz = 0 − z of the surface
    dz = relief.dz(pts)
    return np.where(np.isnan(dz), np.nan, 0.0 - dz)


def z_grids(
    grid: GridV2, smap: ShellMap, relief: dz_kernels.SurfaceIndex | None = None, *,
    n_shells: int | None = None, shells: Sequence[int] | None = None,
) -> ZGrids:  # fmt: skip
    """The surfaces of `shells` (default — all of 1…`n_shells`; `n_shells` — default the largest PIT of `smap`) in one
    pass: the parents of `smap` by growing shell, a running minimum of the level per column, a grid for a shell wanted.
    422 `bad_param {shells}` for a number outside 1…N; a variable grid is fine (the edges of the grid are used)."""
    nx, ny, nz = grid.counts
    total = max(int(n_shells if n_shells is not None else smap.count), smap.count)
    want = tuple(range(1, total + 1)) if shells is None else tuple(sorted({int(s) for s in shells}))
    for s in want:
        if not 1 <= s <= total:
            raise too_many_shells(s, total)
    base = base_surface(grid, relief)
    bottom = grid.corner[2] + np.asarray(grid.edges(2), dtype=np.float64)[:nz]  # the bottom of every level
    p = smap.cells
    i, j, k = ijk_of(p, grid.counts)
    col = (i + nx * j).astype(np.int64)
    k = k.astype(np.int64)
    order = np.argsort(smap.shell, kind="stable")
    col, k, shell = col[order], k[order], smap.shell[order]
    bounds = np.searchsorted(shell, np.arange(1, total + 2))
    lowest = np.full(nx * ny, nz, dtype=np.int64)  # nz — no mined parent yet
    out = np.empty((len(want), nx * ny), dtype=np.float64)
    at = 0
    for n in range(1, total + 1):
        lo, hi = int(bounds[n - 1]), int(bounds[n])
        if hi > lo:
            np.minimum.at(lowest, col[lo:hi], k[lo:hi])
        if at < len(want) and want[at] == n:
            mined = lowest < nz
            out[at] = np.where(mined, bottom[np.minimum(lowest, nz - 1)], base)
            at += 1
    return ZGrids(nx, ny, want, out)
