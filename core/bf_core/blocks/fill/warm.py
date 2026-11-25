"""The warm-up of the kernels of the fill and of the coding of block models (ADR 0023 §6; подшаг 17.21).

    warm() -> {part: seconds}
        rays      `columns` — the index of the triangles, the centre rays (`inside_columns`) and the points
                  (`inside_points`, the leaf «каркас» of a filter)
        boundary  `boundary_parents`: the test «triangle — box» of the parents
        octree    `split_rows` with its walk down the sub-blocks over the rays of `frac` — through `fill_grid` (two
                  bodies with priorities, the modes «inside» and «both») and, as the job `blocks.code` calls it,
                  over rows with sub-blocks (`split=False, keep=True`)
        points    `frac.class_points` and `frac.box_codes` (the coding by the centre and the share of a box)

A part that fails is logged and the next goes on — a warm-up never stops a worker, the job compiles its kernel
itself then. Every kernel is `cache=True`: the first call fills NUMBA_CACHE_DIR (`bf_core/paths.py`; in the .exe —
`bf_core/numba_cache.py`), the next ones load from it. The dtypes are those of the jobs (`jobs.py`): float64
vertices, int32 triangles, uint64 P, uint8 s and w, int16 codes; threads > 1 changes nothing for numba. The module
imports numpy and numba only inside `warm()`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

PARTS = ("rays", "boundary", "octree", "points")
SIZE = 4.0  # the parent, m
CELLS = (3, 3, 3)
LEVELS = (2, 2, 2)  # sub-blocks per axis of a parent


def _box(lo: float, hi: float) -> tuple[Any, Any]:
    """An axis-aligned box in the axes of the project, outward normals: a body that crosses parents and sub-blocks."""
    import numpy as np

    v = np.array([[x, y, z] for z in (lo, hi) for y in (lo, hi) for x in (lo, hi)], dtype=np.float64)
    t = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
                  [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]], dtype=np.int32)  # fmt: skip
    return v, t


def _setup() -> dict[str, Any]:
    from bf_core.blocks.fill import columns as fc
    from bf_core.blocks.schema import GridV2

    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": list(CELLS), "size": [SIZE] * 3, "div": list(LEVELS),
                      "rotation": 0.0, "order": "F"})  # fmt: skip
    meshes = [fc.Mesh.of_project(grid, *_box(1.3, 8.9), "A"), fc.Mesh.of_project(grid, *_box(5.1, 11.2), "B")]
    return {"grid": grid, "meshes": meshes}


def _rays(d: dict[str, Any]) -> None:
    import numpy as np

    from bf_core.blocks.fill import columns as fc

    grid, mesh = d["grid"], d["meshes"][0]
    fc.inside_columns(grid, mesh, threads=2)
    q = np.array([[2.0, 2.0, 2.0], [10.5, 10.5, 10.5], [0.1, 0.1, 0.1]], dtype=np.float64)
    fc.inside_points(mesh, q, threads=2)


def _boundary(d: dict[str, Any]) -> None:
    from bf_core.blocks.fill.boundary import boundary_parents

    boundary_parents(d["grid"], d["meshes"][0], threads=2)


def _octree(d: dict[str, Any]) -> None:
    import numpy as np

    from bf_core.blocks.fill import octree
    from bf_core.blocks.fill.boundary import boundary_parents
    from bf_core.blocks.fill.frac import Bodies

    grid, meshes = d["grid"], d["meshes"]
    octree.fill_grid(grid, meshes, priorities=[1, None], mode="inside", m=2, threads=2)
    octree.fill_grid(grid, meshes, mode="both", m=4, threads=1)
    bodies = Bodies.of(grid, meshes, [None, 1])  # the job `blocks.code`: rows with sub-blocks, nothing dropped
    joint = octree.join_boundaries(bodies, [boundary_parents(grid, m, threads=2) for m in meshes])
    p = np.array([0, 5, 13, 26], dtype=np.uint64)
    s = np.zeros((4, 3), np.uint8)
    w = np.full((4, 3), 2, np.uint8)
    s[1, 0], w[1, 0] = 1, 1
    octree.split_rows(grid, bodies, joint, p, s, w, m=3, mode="inside", split=False, keep=True, threads=2)
    octree.split_rows(grid, bodies, joint, p, None, None, m=1, mode="outside", threads=1)


def _points(d: dict[str, Any]) -> None:
    import numpy as np

    from bf_core.blocks.fill import frac

    bodies = frac.Bodies.of(d["grid"], d["meshes"])
    q = np.array([[2.0, 2.0, 2.0], [6.0, 6.0, 6.0], [11.0, 11.0, 11.5]], dtype=np.float64)
    frac.class_points(bodies, q, threads=2)
    boxes = np.array([[1.0, 1.0, 1.0, 3.0, 3.0, 3.0], [4.0, 4.0, 4.0, 6.0, 6.0, 6.0]], dtype=np.float64)
    frac.box_codes(bodies, boxes, m=2, mode="inside", threads=2)
    frac.box_codes(bodies, boxes, m=4, mode="both", threads=1)


def warm() -> dict[str, float]:
    """Every part of `PARTS` in turn. → seconds a part (a part that failed is missing). Never raises."""
    log = logging.getLogger("bf_core.blocks.fill.warm")
    took: dict[str, float] = {}
    runs: dict[str, Callable[[dict[str, Any]], None]] = {"rays": _rays, "boundary": _boundary, "octree": _octree,
                                                          "points": _points}  # fmt: skip
    data: dict[str, Any] | None = None
    for name in PARTS:
        t0 = time.perf_counter()
        try:
            if data is None:
                data = _setup()
            runs[name](data)
        except Exception:  # noqa: BLE001 - a warm-up never stops a worker; the job compiles its kernel then
            log.exception("warm-up of the fill (%s) failed", name)
            continue
        took[name] = time.perf_counter() - t0
    return took
