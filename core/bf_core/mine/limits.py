"""The size limits of the pit jobs, checked EARLY (подшаг 21.21б-2; ADR 0028 решение 30, 422 `pit_too_large`).

A job that finds out in its last stage that the model is too large has spent hours: the limits are checked by the route
of the run (`check_run`), by the stage «Чтение» of the jobs and before the z-grids are read. Pure: no numpy, no numba
(the process of the API imports it).

    check_size(counts, n_shells)      the plan of the grid, the columns, the z-grids of N shells (422 `pit_too_large`)
    check_zgrid(columns, n_shells)    only the z-grids (the jobs `pit.surfaces`, `pit.solid`: the shells they read)
    hints(counts, n_shells)           {size_cells, size_columns, size_shells} — the route gives them to `memory_mb`
    run_memory_mb(params), view_memory_mb(params)       the estimate of the memory of the jobs (MB)

The z-grids (`sidecar:zgrid`) are float64 and stay so (the format is not changed: old files are read as they were): the
array of all shells is ONE copy in memory (`MAX_ZGRID` values = 2 GiB) and the Parquet file is written in row groups
(`store_data.stage_zgrid`), not as a table of copies.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .spec_parts import refuse

MAX_COLUMNS = 1 << 26  # the plan of the grid: 67 million columns (the centres of the columns, the mesh of a shell)
MAX_ZGRID = 1 << 28  # values of z of all the shells held at once: float64 — 2 GiB
_MB = 1 << 20
#: (code, message, hint) of the failure of any pit job that ran out of memory (`MemoryError`): one text for all of them
OUT_OF_MEMORY = (
    "out_of_memory",
    "Не хватило памяти на расчёт карьера.",
    "Закройте другие программы или укрупните модель регуляризацией.",
)


def check_plan(counts: Sequence[int]) -> None:
    """nx·ny·nz within the dense arrays of the graph (`plan.MAX_PLAN_CELLS`)."""
    from .plan import MAX_PLAN_CELLS

    nx, ny, nz = (int(c) for c in counts)
    if nx * ny * nz > MAX_PLAN_CELLS:
        raise refuse("pit_too_large", f"Сетка модели слишком велика для оптимизации карьера: {nx}×{ny}×{nz} = "
                     f"{nx * ny * nz:,} ячеек, допустимо до {MAX_PLAN_CELLS:,}.".replace(",", " "),
                     "Укрупните модель регуляризацией.")  # fmt: skip


def check_columns(counts: Sequence[int]) -> None:
    """nx·ny — the columns of the plan: the centres and the mesh of a shell."""
    nx, ny = int(counts[0]), int(counts[1])
    if nx * ny > MAX_COLUMNS:
        raise refuse("pit_too_large", f"План модели слишком велик для поверхностей оболочек: {nx}×{ny} = "
                     f"{nx * ny:,} колонок, допустимо до {MAX_COLUMNS:,}.".replace(",", " "),
                     "Укрупните модель регуляризацией.")  # fmt: skip


def check_zgrid(columns: int, n_shells: int) -> None:
    """N shells × columns values of z (8 bytes each) must fit in memory."""
    if int(columns) * int(n_shells) > MAX_ZGRID:
        total = int(columns) * int(n_shells)
        raise refuse("pit_too_large", f"Поверхности {n_shells} оболочек на плане из {int(columns):,} колонок не "
                     f"поместятся в память: {total:,} значений, допустимо до {MAX_ZGRID:,}.".replace(",", " "),
                     "Укрупните модель регуляризацией или задайте меньше значений RF.")  # fmt: skip


def check_size(counts: Sequence[int], n_shells: int) -> None:
    """The three checks of the run: the plan, the columns, the z-grids of `n_shells` shells."""
    check_plan(counts)
    check_columns(counts)
    check_zgrid(int(counts[0]) * int(counts[1]), n_shells)


def hints(counts: Sequence[int], n_shells: int) -> dict[str, int]:
    """What the route puts into the params of the job for the estimate of the memory (`run_memory_mb`)."""
    nx, ny, nz = (int(c) for c in counts)
    return {"size_cells": nx * ny * nz, "size_columns": nx * ny, "size_shells": int(n_shells)}


def run_memory_mb(params: Mapping[str, Any] | None = None) -> int:
    """`pit.run`: 1500 MB (the pass of the economics, the solver on ≈ 1 million nodes) + the dense arrays of the graph
    (≈ 16 bytes a cell: the index, the air, the table of the cones) + the z-grids (8 bytes × shells × columns, two
    copies at the write). Without the sizes in the params — 1500."""
    p = params or {}
    cells, columns, shells = (int(p.get(k) or 0) for k in ("size_cells", "size_columns", "size_shells"))
    return 1500 + cells * 16 // _MB + columns * shells * 16 // _MB


def view_memory_mb(params: Mapping[str, Any] | None = None) -> int:
    """`pit.surfaces`, `pit.solid`: 1024 MB + the shells read (8 bytes × columns each) + the centres, the nodes and the
    triangles of the mesh being built (≈ 120 bytes a column)."""
    p = params or {}
    columns, shells = int(p.get("size_columns") or 0), max(int(p.get("size_shells") or 1), 1)
    return 1024 + columns * (8 * shells + 120) // _MB
