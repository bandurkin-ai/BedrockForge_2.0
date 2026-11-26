"""The cells absent from a sparse model, for the economics of the pit (подшаг 21.21б; ADR 0028 decision 7; review
finding 4). Below the relief an absent cell is a waste of the rock «(пусто)», above it «воздух»; to tell them apart the
pass must not walk over EVERY cell of the grid (up to 2·10⁹: the time and the memory grew with the size of the grid,
not with the model).

    kmax = relief_levels(model, relief)        # int32 [nx·ny]: the highest level of each column that can be below
    r = BoundedRestrictor(r.fields, r.warnings, r._fn, kmax)    # the restrictor of the scenario that knows it
    for share, chunk in empty_cells(model, names, CHUNK, kmax): ...

THE BOUND. The leaf «поверхность, ниже, по доле» (`blocks/restrict.py`) gives a block of the centre height z_c and the
height h the share clip((h/2 − (z_c − z_s))/h; 0; 1), z_s — the surface above the centre in the plan: it is positive
only while z_s > z_c − h/2, the bottom of the block. For a column that is a level bound: `k < (h/2 − dz₀)/h`, dz₀ = the
centre of the level 0 minus z_s. The restrictor of a scenario is a PRODUCT of the user's restriction and that leaf, so
a cell above the bound has f = 0 — «воздух» — whatever else is in the tree, and need not be visited. The bound is
taken one level wider (a float is never the reason to lose a cell): the real f of every visited cell is still computed
by the restrictor. A column with no surface in its plan (NaN) has no level.

THE LIMIT. Every cell below the relief is a node of the graph, and a graph holds ≤ `MAX_EMPTY_CELLS` nodes (ADR 0028
decision 13): more absent cells below the relief than that is 422 `pit_too_large` at once — before a single portion is
read (the count is the sum of the bounds). Without a bound (`kmax` None — a restrictor with no relief in the tree,
tests only) the whole grid is walked as before.

The order is the order of P (a level, then its columns): the cells of one portion are ascending and the portions follow
each other, so the sums of «Контроль» (Neumaier, in the order of the rows) do not depend on how the cells are cut into
portions. `rows` and `air` of «Контроль» count the cells that are visited: an absent cell above the bound is not a row
of the pass.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks import schema as bs
from bf_core.blocks.model import Chunk, Model, empty_of
from bf_core.blocks.restrict import Restrictor

MAX_EMPTY_CELLS = 10_000_000  # the nodes of a graph (`graph.MAX_NODES`): the absent cells below the relief are nodes
COLUMN_CHUNK = 1 << 20  # columns of one portion when the bound of the levels is made


@dataclass
class BoundedRestrictor(Restrictor):
    """A restrictor that also knows, per column of the parent grid, the highest level that can have f > 0 (the relief
    of the scenario is in its tree): `kmax` int32 [nx·ny] (−1 — none). Everything else is `Restrictor`."""

    kmax: NDArray[np.int32] | None = None


def relief_levels(model: Model, relief: Any) -> NDArray[np.int32]:
    """`kmax` of the columns of the parent grid for the surface index `relief` (`dz_kernels.SurfaceIndex`), see the
    module docstring. The grid is regular (the pit needs it); a surface with no triangles in the plan gives −1."""
    grid = model.grid
    nx, ny, nz = (int(c) for c in grid.counts)
    ncol = nx * ny
    h = float(grid.regular("Оптимизация карьера")[2])
    out = np.full(ncol, -1, dtype=np.int32)
    for lo in range(0, ncol, COLUMN_CHUNK):
        p = np.arange(lo, min(lo + COLUMN_CHUNK, ncol), dtype=np.uint64)  # P of the level 0 = the number of the column
        dz0 = relief.dz(geometry.centres(grid, p))  # the centre of the level 0 minus the surface; NaN — no surface
        known = ~np.isnan(dz0)
        # k < (h/2 − dz₀)/h: the levels 0…⌈x⌉ − 1; one more level for the float (a clip keeps k inside the grid)
        x = np.floor((h * 0.5 - dz0[known]) / h)
        out[lo : lo + len(p)][known] = np.clip(x + 1.0, -1.0, float(nz - 1)).astype(np.int32)
    return out


def too_many(n: int) -> ApiError:
    return ApiError(
        422,
        "pit_too_large",
        f"Пустых ячеек разреженной модели ниже рельефа — {n:,}: больше предела графа ({MAX_EMPTY_CELLS:,}).".replace(
            ",", " "
        ),
        "Укрупните модель регуляризацией: Блочные модели → Регуляризация.",
        {"empty_cells": int(n), "limit": MAX_EMPTY_CELLS},
    )


def _chunk_of(cand: NDArray[np.uint64], specs: dict[str, Any]) -> Chunk:
    cols = {}
    for name, spec in specs.items():
        col = empty_of(spec, len(cand))
        col[:] = np.nan if spec["kind"] == bs.NUMBER else -1
        cols[name] = col
    return Chunk(-1, cand, None, None, cols)


def _absent(cand: NDArray[np.uint64], present: NDArray[np.uint64]) -> NDArray[np.uint64]:
    """The cells of `cand` (ascending) that are not rows of the file (`present`, ascending)."""
    if not len(present) or not len(cand):
        return cand
    pos = np.searchsorted(present, cand)
    return cand[~((pos < len(present)) & (present[np.minimum(pos, len(present) - 1)] == cand))]


def empty_cells(
    model: Model, names: Sequence[str], chunk: int, kmax: NDArray[np.int32] | None = None
) -> Iterator[tuple[float, Chunk]]:
    """The cells absent from the file in the order of P: (the share of the walk done, a portion of whole parents whose
    fields are empty — NaN of a number, −1 of a category). `kmax` — the bound of the levels per column (module
    docstring); None — every cell of the grid. 422 `pit_too_large` — too many cells below the bound."""
    present = model.p()
    specs = {name: model.field(name) for name in names}
    nx, ny, nz = (int(c) for c in model.grid.counts)
    ncol = nx * ny
    if kmax is None:
        total = max(model.grid.cells, 1)
        for lo in range(0, model.grid.cells, chunk):
            cand = _absent(np.arange(lo, min(lo + chunk, model.grid.cells), dtype=np.uint64), present)
            if len(cand):
                yield (lo + chunk) / total, _chunk_of(cand, specs)
        return
    km = np.asarray(kmax, dtype=np.int64)
    if km.shape != (ncol,):
        raise ValueError("kmax must hold one level per column of the grid")
    walk = int(np.clip(km + 1, 0, nz).sum())  # the cells below the bound (it is one level wider than the relief)
    if walk - ncol > MAX_EMPTY_CELLS + len(present):
        raise too_many(walk - len(present))
    order = np.argsort(-km, kind="stable")  # the columns by the bound, the highest first
    desc = -km[order]  # ascending
    done = 0
    for k in range(nz):
        m = int(np.searchsorted(desc, -k, side="right"))  # the columns whose bound reaches the level k
        if not m:
            break
        cols = np.sort(order[:m]).astype(np.uint64)  # the columns of the level in ascending order
        for lo in range(0, m, chunk):
            part = cols[lo : lo + chunk]
            cand = _absent(part + np.uint64(k * ncol), present)
            done += len(part)
            if len(cand):
                yield done / max(walk, 1), _chunk_of(cand, specs)
