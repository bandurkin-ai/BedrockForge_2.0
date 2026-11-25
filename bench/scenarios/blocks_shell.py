"""The shell of a block model: the filter, the exposed faces, the merge of the faces and LOD (ТЗ 8.7.6, 5.3; Т8.7-9;
ADR 0023 §11 and «Замеры»: ci — 2 млн блоков ≤ 0,6 с, full — «фильтр + оболочка 20 млн ≤ 3 с»).

A regular model of nx × ny × nz parents (no sub-blocks), all rows, in memory — what the route `shell` computes after
the columns are read (`blocks_view` is the whole route over a model of the project). The field is smooth with noise
(waves of 100–400 m and 0.12 of noise: the filter at 0.78 takes bodies with rough sides; as `blocks_view`):
  filter_s     the filter `fld > 0.78` and the classes of the colour of AU (`scale_class`, 256 classes)
  mask_s       the mask of the exposed faces of the blocks that passed and their count (`shell._mask_sorted`)
  view_s       `lod.view` — the shell with the merge of the faces and the limit of 5 млн (the whole computation of the
               display on the threads of the params);  shell_s — `filter_s` + `view_s`, the figure of ТЗ
  exposed_s, merge_s   the faces before the merge (`shell.exposed`) and the merge of them (`shell_merge.merge_faces`)
  box_s        the shell of the whole model of one class (no filter: six faces of a box merged to six rectangles)
  lod_s        `lod.view` with the limit of a quarter of the faces (the coarse grid): the level of LOD `lod`
  t1_s         `lod.view` of one thread;  `threads` — those of the params
What is checked (a failure stops the run): the faces before the merge are those of the model counted by numpy (the
blocks that passed with a neighbour that did not, six directions, the border of the model — a face); the merge
keeps the area (Σ of the cells of the merged faces = the faces before); a solid box is six rectangles (Т8.7-9);
the answer of one thread equals that of the params bit by bit (8.0.2); the limit of LOD is kept.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

LEVEL = 0.78
LIMIT = 5_000_000
QUARTER = 4  # the limit of the second view: a quarter of the faces of the first


def prepare(params: dict[str, Any], work: Path) -> None:
    pass  # the model is made in `run` from the numbers of the grid: no file


def load() -> None:
    bc.warm()


def field(counts: list[int]) -> np.ndarray:
    nx, ny, nz = counts
    rng = np.random.default_rng(1)
    i, j, k = np.arange(nx)[None, None, :], np.arange(ny)[None, :, None], np.arange(nz)[:, None, None]
    noise = 0.12 * rng.standard_normal((nz, ny, nx))
    return (np.sin(i / 37.0) * np.cos(j / 29.0) + 0.5 * np.sin(k / 11.0) + noise).ravel()


def exposed_count(passed: np.ndarray, counts: list[int]) -> int:
    """The faces of the blocks that passed that have no passed block behind them, by numpy (X fastest in `passed`)."""
    nx, ny, nz = counts
    b = passed.reshape(nz, ny, nx)
    n = 0
    for axis in range(3):
        pad = [(0, 0)] * 3
        pad[axis] = (1, 1)
        p = np.pad(b, pad)
        lo = [slice(None)] * 3
        hi = [slice(None)] * 3
        lo[axis], hi[axis] = slice(0, -2), slice(2, None)
        n += int((b & ~p[tuple(lo)]).sum()) + int((b & ~p[tuple(hi)]).sum())
    return n


def same(a: Any, b: Any) -> bool:
    return all(np.array_equal(getattr(a, x), getattr(b, x)) for x in ("c", "h", "a", "k", "row", "g"))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import shell
    from bf_core.blocks.lod import view
    from bf_core.blocks.schema import GridV2
    from bf_core.blocks.shell_merge import merge_faces, scale_class

    threads = bc.limit_threads(params)
    counts = [int(v) for v in params["counts"]]
    nx, ny, nz = counts
    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": counts, "size": [10.0, 10.0, 5.0], "div": [1, 1, 1],
                      "rotation": 0.0, "order": "F"})  # fmt: skip
    n = nx * ny * nz
    p = np.arange(n, dtype=np.uint64)
    fld = field(counts)
    au = np.exp(fld * 1.3)
    clock: dict[str, Any] = {"cells": n, "threads": threads}
    t0 = time.perf_counter()
    passed = fld > LEVEL
    klass = scale_class(au, 0.1, 10.0)
    clock["filter_s"] = round(time.perf_counter() - t0, 3)
    t0 = time.perf_counter()
    mask, _rows = shell._mask_sorted(grid, p, None, None, passed, True, threads, None)
    raw = shell.count_faces(mask)
    clock["mask_s"] = round(time.perf_counter() - t0, 3)
    t0 = time.perf_counter()
    v = view(grid, p, None, None, passed, klass, limit=LIMIT, threads=threads)
    clock["view_s"] = round(time.perf_counter() - t0, 3)
    clock["shell_s"] = round(clock["filter_s"] + clock["view_s"], 3)
    t0 = time.perf_counter()
    faces = shell.exposed(grid, p, None, None, passed, klass, threads=threads)
    clock["exposed_s"] = round(time.perf_counter() - t0, 3)
    t0 = time.perf_counter()
    merged = merge_faces(grid, faces)
    clock["merge_s"] = round(time.perf_counter() - t0, 3)
    t0 = time.perf_counter()
    box = view(grid, p, None, None, np.ones(n, bool), np.zeros(n, np.uint16), threads=threads)
    clock["box_s"] = round(time.perf_counter() - t0, 3)
    t0 = time.perf_counter()
    small = max(1000, int(v.faces_raw) // QUARTER)
    coarse = view(grid, p, None, None, passed, klass, limit=small, threads=threads)
    clock["lod_s"] = round(time.perf_counter() - t0, 3)
    t0 = time.perf_counter()
    one = view(grid, p, None, None, passed, klass, limit=LIMIT, threads=1)
    clock["t1_s"] = round(time.perf_counter() - t0, 3)
    want = exposed_count(passed, counts)
    clock.update(passed=int(passed.sum()), faces_raw=int(v.faces_raw), faces=len(v.faces), lod=int(coarse.lod),
                 coarse_faces=len(coarse.faces))  # fmt: skip
    assert raw == want == len(faces) == v.faces_raw, (raw, want, len(faces), v.faces_raw)
    area = int(((merged.g[:, 2] - merged.g[:, 1]) * (merged.g[:, 4] - merged.g[:, 3])).sum())
    assert area == want and len(merged) == len(v.faces) <= want, (area, want, len(merged), len(v.faces))
    assert box.faces_raw == 2 * (nx * ny + ny * nz + nx * nz) and len(box.faces) == 6, (box.faces_raw, len(box.faces))
    assert same(one.faces, v.faces), "the shell of one thread differs from that of the params"
    assert coarse.lod > 0 and len(coarse.faces) <= small, (coarse.lod, len(coarse.faces), small)
    clock["peak_mb"] = bc.peak_mb()
    return clock
