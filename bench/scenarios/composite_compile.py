"""The first compilation of the kernels of sample processing (ADR 0018 решение 27: «первая компиляция ядер этапа —
отдельной строкой bench»; §5: the kernels are compiled once into NUMBA_CACHE_DIR and loaded from it afterwards).

Two fresh processes, as a worker that has just started, with a cache of numba of their own: the first finds it
empty (the first start after an install), the second finds what the first one left. Each imports numba, runs
`composites.warm.warm()` — the step `composites` of the warm-up of the workers and of the API process
(`ops/warm.py`) — and then every operation of the stage once on a set of 24 holes: composites by length, by
domains and by benches, merging, coordinates, the analysis of the top cut, the top cut, the equivalent,
declustering (the functions their handlers call) and the previews of one hole.
  compile_s     the compilation of the kernels: `warm_cold_s` + `left_s`;
  warm_cold_s   `warm()` with the empty cache;
  left_s        what the operations still waited for after it: their time with the empty cache less their time
                with the filled one — kernels the warm-up does not reach are compiled inside the first job that
                needs them (0 — it reaches them all);  left — those kernels by name («module.function»);
  load_s        `warm()` from the filled cache: what every start of the program pays in the background;
  jobs_s        the operations on the 24 holes with everything loaded;  import_s — numba, numpy, pandas, pyarrow;
  process_mb    the peak of the compiling process (Linux);  cache_mb, cache_files — the cache it left.
The second process must not compile: it adds nothing to the cache and `load_s` is under a half of `warm_cold_s`
(checked).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def every(meta: dict[str, Any], engine: Any, folder: Path) -> None:
    """Every operation of the stage on the set of `_samples.warm`."""
    from bf_core.composites import jobs as cj
    from bf_core.composites import jobs_prep

    from . import _samples, composite_merge, composite_preview, composites, declus, topcut_analysis

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        pass

    table = str(meta["samples"])
    for k, method in enumerate(("length", "domain", "bench")):
        composites.job(meta, engine, folder, method, {}, job_id=k + 1)
    composite_merge.job(meta, engine, folder, job_id=4)
    cj.run_tables(engine, folder, {"body": {"set_id": meta["set_id"], "table": table}}, "coords", job_id=5, tick=tick)
    for domain in (False, True):
        topcut_analysis.analyse(meta, engine, folder, domain)
    for k, op in enumerate(("topcut", "equivalent")):
        body = composite_preview.request(meta, op, {})
        jobs_prep.run_prep(engine, folder, {"body": body}, op, job_id=6 + k, tick=tick)
    declus.job(meta, engine, folder, {"cmin": 5.0, "cmax": 50.0}, job_id=8)
    svc = _samples.service(engine, folder)
    hole = composite_preview.hole_ids(engine, meta["set_id"], 1)[0]
    for op in composite_preview.OPS:
        composite_preview.call(svc, op, {"params": composite_preview.request(meta, op, {}), "hole_id": hole})


def _kernels(cache: Path) -> set[str]:
    """The functions that have an index in the cache of numba («module.function-line.py312.nbi»)."""
    return {f.name.split("-")[0] for f in cache.rglob("*.nbi")}


def fresh() -> None:
    """The process: prints {import_s, warm_s, jobs_s, process_mb, left}."""
    from bf_core.paths import configure_numba_cache

    cache = configure_numba_cache()
    t0 = time.perf_counter()
    import numba  # noqa: F401
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401

    t1 = time.perf_counter()
    from bf_core.composites.warm import warm

    warm()
    t2 = time.perf_counter()
    warmed = _kernels(cache)
    from . import _samples

    _samples.warm(every)
    t3 = time.perf_counter()
    out: dict[str, Any] = {"import_s": t1 - t0, "warm_s": t2 - t1, "jobs_s": t3 - t2,
                           "left": sorted(_kernels(cache) - warmed)}  # fmt: skip
    try:
        import resource

        out["process_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except ImportError:  # Windows
        out["process_mb"] = None
    print(json.dumps(out))


def _process(cache: Path) -> dict[str, Any]:
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache), "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "core")])}
    code = "from bench.scenarios.composite_compile import fresh; fresh()"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    if out.returncode:
        raise RuntimeError(out.stderr[-4000:])
    return dict(json.loads(out.stdout.strip().splitlines()[-1]))


def prepare(params: dict[str, Any], work: Path) -> None:
    pass


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    cache = work / "numba"
    shutil.rmtree(cache, ignore_errors=True)
    cold = _process(cache)
    files = [f for f in cache.rglob("*") if f.is_file()]
    loaded = _process(cache)
    assert loaded["warm_s"] < 0.5 * cold["warm_s"] and not loaded["left"], (loaded, cold["warm_s"])
    left = max(0.0, cold["jobs_s"] - loaded["jobs_s"])
    return {
        "compile_s": round(cold["warm_s"] + left, 3),
        "warm_cold_s": round(cold["warm_s"], 3),
        "left_s": round(left, 3),
        "left": ", ".join(cold["left"]),
        "load_s": round(loaded["warm_s"], 3),
        "jobs_s": round(loaded["jobs_s"], 3),
        "import_s": round(loaded["import_s"], 3),
        "process_mb": cold["process_mb"],
        "cache_mb": round(sum(f.stat().st_size for f in files) / 2**20, 1),
        "cache_files": len(files),
    }
