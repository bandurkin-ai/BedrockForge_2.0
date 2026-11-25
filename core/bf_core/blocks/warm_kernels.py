"""The warm-up of the kernels of the shell, of the merging of faces, of the levels of detail and of the
regularization of block models (ADR 0023 §7, §9; подшаг 17.21). `blocks/warm.py` calls `warm()` as its step `shell`.

    warm() -> {part: seconds}
        shell       `shell.exposed` / `face_mask` over whole parents and over rows with sub-blocks, classes,
                    `shell=False` (all blocks) — the kernels `_parents`, `_neighbours`, `_starts`, `_fill`
        merge       `shell_merge.merge_faces` (the runs along u and v) and the legends
        lod         `lod.view` over a limit below the faces: `coarsen` and the shell of the coarse grid
        regularize  `regularize.regularize` on a model of a few rows in a temporary project: `regularize_kernels`
                    with a number, a category, FRAC and a density, and the writer of the result

The dtypes are those of the routes (`routes_viz`, jobs): uint64 P, uint8 s and w, bool mask, uint16 classes. A part
that fails is logged and the next goes on — a warm-up never stops a worker, the job compiles its kernel itself
then. numpy and numba are imported only inside `warm()`.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

PARTS = ("shell", "merge", "lod", "regularize")
PREFIX = "bf-warm-blocks-"


def _data() -> dict[str, Any]:
    """A grid of 3 × 3 × 2 parents (2 × 2 × 2 sub-blocks) with a hole and rows of two shapes."""
    import numpy as np

    from bf_core.blocks.schema import GridV2

    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [3, 3, 2], "size": [4.0, 4.0, 4.0], "div": [2, 2, 2],
                      "rotation": 0.0, "order": "F"})  # fmt: skip
    p = np.array([i for i in range(18) if i != 4], dtype=np.uint64)  # a hole: the inner faces are shown
    n = len(p)
    klass = (np.arange(n) % 3).astype(np.uint16)
    s = np.zeros((n, 3), np.uint8)
    w = np.full((n, 3), 2, np.uint8)
    s[1], w[1] = (1, 0, 1), (1, 2, 1)  # a row of sub-blocks: the rows of a parent go by (sz, sy, sx)
    s[2], w[2] = (0, 0, 0), (1, 2, 2)
    s_rw, w_rw = s.copy(), w.copy()
    s.setflags(write=False)  # the columns of the cache of a model are read-only: numba compiles a kernel anew for
    w.setflags(write=False)  # a read-only array (the first display of a model took 1 s for it, 17.39b)
    return {"s_rw": s_rw, "w_rw": w_rw, "grid": grid, "p": p, "s": s, "w": w, "ok": np.ones(n, dtype=np.bool_),
            "klass": klass, "values": np.linspace(0.0, 1.0, n)}  # fmt: skip


def _shell(d: dict[str, Any]) -> None:
    from . import shell

    g, p, s, w, ok, k = d["grid"], d["p"], d["s"], d["w"], d["ok"], d["klass"]
    shell.exposed(g, p, s, w, ok, k, shell=True, threads=2)  # read-only s, w: what the display passes
    shell.exposed(g, p, s, w, ok, None, shell=False)
    shell.exposed(g, p, d["s_rw"], d["w_rw"], ok, k, shell=True)  # writable ones (`faces_of_block`, the tests)
    shell.exposed(g, p, None, None, ok, k, shell=True)  # whole parents
    shell.exposed(g, p[::-1].copy(), None, None, ok, None, shell=True)  # unsorted input: the order is restored
    shell.count_faces(shell.face_mask(g, p, s, w, ok, shell=True))
    shell.faces_of_block(g, 5)
    shell.faces_of_block(g, 5, (0, 1, 0), (1, 1, 2))


def _merge(d: dict[str, Any]) -> None:
    import numpy as np

    from . import shell, shell_merge

    g = d["grid"]
    f = shell.exposed(g, d["p"], None, None, d["ok"], d["klass"])
    shell_merge.merge_faces(g, f)
    shell_merge.merge_faces(g, shell.exposed(g, d["p"], d["s"], d["w"], d["ok"], None))
    v = np.asarray(d["values"])
    shell_merge.scale_class(v, 0.0, 1.0)
    shell_merge.scale_class(v + 1.0, 1.0, 3.0, log=True)
    shell_merge.break_class(v, [0.25, 0.5])
    shell_merge.scale_bins(0.0, 1.0)
    shell_merge.break_bins([0.25, 0.5])


def _lod(d: dict[str, Any]) -> None:
    from . import lod

    g, p, s, w, ok, k = d["grid"], d["p"], d["s"], d["w"], d["ok"], d["klass"]
    lod.view(g, p, None, None, ok, k, limit=30)  # raw faces over the limit: one coarse level, then the shell
    lod.view(g, p, s, w, ok, k, limit=30, merge=False)
    lod.view(g, p, None, None, ok, None, limit=30, kind="mean", values=d["values"],
             klass_of=lambda m: (m * 2.0).astype("uint16"))  # fmt: skip


def _model_fields() -> list[dict[str, Any]]:
    return [{"name": "AU", "dtype": "f8", "unit": "г/т", "role": "grade"},
            {"name": "SG", "dtype": "f8", "role": "density"},
            {"name": "DOM", "dtype": "cat", "labels": ["руда", "порода"], "role": "code"},
            {"name": "FRAC", "dtype": "f8", "role": "frac"}]  # fmt: skip


def _regularize(d: dict[str, Any]) -> None:
    import numpy as np

    from bf_core.blocks import regularize as rg
    from bf_core.blocks import store as bm
    from bf_core.blocks.model import Model
    from bf_core.db import dialect, migrate
    from bf_core.storage.blobs import BlobStore

    folder = Path(tempfile.mkdtemp(prefix=PREFIX))
    engine = None
    try:
        engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
        migrate.upgrade(engine, "project")
        store = BlobStore(folder)
        grid = {"corner": [0.0, 0.0, 0.0], "counts": [3, 3, 2], "size": [1.0, 1.0, 1.0], "rotation": 0}
        p = np.arange(18, dtype=np.uint64)
        rng = np.random.default_rng(1)
        n = len(p)
        au = rng.lognormal(0.0, 0.5, n)
        au[3] = np.nan  # a gap is not a zero
        values = {"AU": au, "SG": rng.normal(2.7, 0.1, n), "DOM": (np.arange(n) % 3 - 1).astype(np.int32),
                  "FRAC": rng.uniform(0.3, 1.0, n)}  # fmt: skip
        with bm.CellsWriterV2(store, grid, _model_fields()) as writer:
            writer.write(p, values)
            staged = writer.finish()
        try:
            with dialect.write_tx(engine) as conn:
                mid = bm.commit_block_model(conn, store, staged, name="warm")
        finally:
            staged.discard()
        with dialect.read_tx(engine) as conn:
            model = Model.open(conn, store, mid)
        res = rg.regularize(model, store, [2.0, 2.0, 2.0], density="SG", threads=2)
        res.staged.discard()
        res = rg.regularize(model, store, [1.5, 1.5, 1.5], density=2.7, threads=1)
        res.staged.discard()
    finally:
        if engine is not None:
            engine.dispose()
        shutil.rmtree(folder, ignore_errors=True)


def warm() -> dict[str, float]:
    """Every part of `PARTS` in turn. → seconds a part (a part that failed is missing). Never raises."""
    log = logging.getLogger("bf_core.blocks.warm_kernels")
    took: dict[str, float] = {}
    runs: dict[str, Callable[[dict[str, Any]], None]] = {"shell": _shell, "merge": _merge, "lod": _lod,
                                                          "regularize": _regularize}  # fmt: skip
    data: dict[str, Any] | None = None
    for name in PARTS:
        t0 = time.perf_counter()
        try:
            if data is None:
                data = _data()
            runs[name](data)
        except Exception:  # noqa: BLE001 - a warm-up never stops a worker; the job compiles its kernel then
            log.exception("warm-up of the blocks (%s) failed", name)
            continue
        took[name] = time.perf_counter() - t0
    return took
