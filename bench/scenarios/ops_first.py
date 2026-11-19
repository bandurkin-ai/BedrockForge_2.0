"""The first operation after a start (10.11; PROGRESS «Э9 9.36»: ~13 s — the kernels of the stitch, of the checks of
a body and of the store compiled while the user waited; criterion ≤ 3 s on Linux).

A fresh process as a quick worker starts (`jobs/worker.py`): the handlers, the warm-up in a thread from a cache of
numba filled once in `prepare` (`ops/warm.py`, as after the first start of the program), and at once the first
operation as its job (`ops/warm.py::run_step`: the preconditions, then `ops/job.py::run`) on a project of
`contours` contours of `vertices` vertices — while the warm-up loads the kernels beside it; then a boolean.
  first_stitch_s    the first operation of the process: «Сшить контуры» to its result in the project;
  first_boolean_s   then «Булева операция» (cube − cube);
  warm_s            the warm-up of the quick worker from the cache, all of its steps;
  process_s         the process from its start to the end of its warm-up; process_mb — its peak memory (Linux).
With `cold: true` the cache is empty at every run (the first start after an install): the stitch waits for the
kernels the warm-up is compiling. What is checked: both operations give a closed body of the right volume.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _rings(contours: int, vertices: int) -> list[Any]:
    import numpy as np

    from bf_core.ops.warm import X0, Y0, Z0

    a = np.linspace(0.0, 2.0 * np.pi, vertices, endpoint=False)
    return [np.column_stack([20.0 * np.cos(a) + 0.2 * k, 20.0 * np.sin(a), np.full(vertices, 5.0 * k)]) + (X0, Y0, Z0)
            for k in range(contours)]  # fmt: skip


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.db import dialect
    from bf_core.formats.lineset import from_lines
    from bf_core.lines import store as ls_store
    from bf_core.ops import warm
    from bf_core.storage.blobs import BlobStore

    ids = warm.project(work)
    folder = Path(ids["folder"])
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.write_tx(engine) as conn:
            store = BlobStore(folder)
            file_id = ls_store.create_file(conn, store, name="тело.str", meta={"format": "micromine_str"})
            rings = _rings(int(params["contours"]), int(params["vertices"]))
            ids["body"] = ls_store.put_layer(conn, store, from_lines("Тело", rings, closed=[True] * len(rings)),
                                             file_id=file_id)  # fmt: skip
    finally:
        engine.dispose()
    (work / "ids.json").write_text(json.dumps(ids), encoding="utf-8")
    if not params.get("cold"):  # the cache of a program started once before
        _process(work, work / "numba", warm_only=True)


def _process(work: Path, cache: Path, *, warm_only: bool = False) -> dict[str, Any]:
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache), "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "core")])}
    code = "import sys; from bench.scenarios.ops_first import fresh; fresh(sys.argv[1], sys.argv[2] == 'warm')"
    out = subprocess.run(
        [sys.executable, "-c", code, str(work), "warm" if warm_only else "run"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return dict(json.loads(out.stdout.strip().splitlines()[-1]))


def fresh(work_dir: str, warm_only: bool) -> None:
    """The process of a quick worker: the handlers, the warm-up in a thread, and at once the operations."""
    t_start = time.perf_counter()
    from bf_core.paths import configure_numba_cache

    configure_numba_cache()
    from bf_core.jobs.registry import load_handlers
    from bf_core.jobs.worker import _warm_kernels
    from bf_core.ops import warm

    load_handlers()
    ids = json.loads((Path(work_dir) / "ids.json").read_text(encoding="utf-8"))
    folder = Path(ids["folder"])
    running = threading.Event()
    took: dict[str, float] = {}

    def warm_up() -> None:
        t0 = time.perf_counter()
        _warm_kernels("quick", running.is_set)
        took["warm_s"] = time.perf_counter() - t0

    thread = threading.Thread(target=warm_up, daemon=True)
    thread.start()
    out: dict[str, Any] = {}
    if not warm_only:
        for key, op, params in (
            ("stitch", "stitch", {"contours": [{"layer_id": ids["body"]}]}),
            ("boolean", "boolean", {"op": "difference", "a": ids["a"], "b": ids["b"]}),
        ):
            running.set()
            t0 = time.perf_counter()
            try:
                result = warm.run_step(folder, op, params, job_id=int(time.time_ns() % 1_000_000_000))
            finally:
                running.clear()
            out[f"first_{key}_s"] = round(time.perf_counter() - t0, 3)
            out[f"{key}_volume"] = result["volumes"][0]
    thread.join()
    out["warm_s"] = round(took.get("warm_s", -1.0), 3)
    out["process_s"] = round(time.perf_counter() - t_start, 3)
    try:
        import resource

        out["process_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except ImportError:  # Windows
        out["process_mb"] = None
    print(json.dumps(out))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import math
    import shutil

    cache = work / ("numba-cold" if params.get("cold") else "numba")
    if params.get("cold"):
        shutil.rmtree(cache, ignore_errors=True)
    got = _process(work, cache)
    n, r = int(params["vertices"]), 20.0
    ring = 0.5 * n * r * r * math.sin(2.0 * math.pi / n)  # the area of a contour
    want = ring * 5.0 * (int(params["contours"]) - 1)  # prisms between equal contours, each shifted 0.2 m
    errors = abs(got["stitch_volume"] - want) / want + abs(got["boolean_volume"] - 875.0) / 875.0
    assert errors < 1e-6, (got, want)
    return {**got, "volume_error": errors}
