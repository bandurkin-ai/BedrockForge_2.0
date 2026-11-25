"""The first compilation of the kernels of the block models (ТЗ 8.0.2: «первая компиляция ядер модуля ≤ 30 с, в фоне
после запуска»; ADR 0023 «Замеры»: full ≤ 30 с; the kernels are compiled once into NUMBA_CACHE_DIR and loaded from
it afterwards).

Fresh processes, as a worker that has just started, each with a cache of numba the scenario gives it (the method of
`variogram_compile`):
  stage   an empty cache. The step `blocks` of the warm-up of the workers (`ops/warm.py`: calc, report, slice, fill,
          the kernels of the shell, the merge, LOD and the regularization, the view) and the kernels of the swath
          of sub-blocks of the step `stats` (`swath_blocks.warm`, `swath_sub.warm`) are run first; then every
          operation of the stage runs once on a tiny model — the scenarios `blocks_*` themselves with a few thousand
          rows (the import of a DAT and a DM, the checks, the calculator, the restrictor, the filling, the
          regularization with categories, the density and the categories, the shell, the slice, the point, the report,
          the export, the swath of sub-blocks): a kernel the warm-up does not reach is compiled by the first job that
          needs it;
  loaded  the cache `stage` left: the same warm-up and the same operations, nothing compiled.
  compile_s     the compilation of the kernels of the stage — the target of ТЗ 8.0.2: `warm_s` of `stage` + `left_s`;
  warm_s        the warm-up on the empty cache (`blocks` — its steps, the swath kernels);  parts — their seconds
  left_s        what the operations still waited for after it: their time in `stage` less their time in `loaded`;
                `left` — those kernels by name («package/module.function»; empty — the warm-up reaches them all and
                `left_s` is the first run of the operations against a repeated one, not a compilation)
  compiled      the kernels the warm-up compiled (a signature of a function is a kernel);  load_s — the warm-up from
                the filled cache: what every start of the program pays in the background;  uncached — the kernels
                the process `loaded` compiled all the same (numba does not serve a function defined inside another
                one from the cache), `uncached_s` — the seconds of the warm-up beyond the step `blocks` there
  jobs_s        the operations with everything loaded;  import_s — numba, numpy, pandas, pyarrow;  process_mb — the peak
                of the process `stage` (Linux);  cache_mb, cache_files — the cache `stage` left.
The process `loaded` must not compile: it adds nothing to the cache and `load_s` is under a half of `warm_s` (checked).
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import intercept_compile as ic

ROOT = Path(__file__).resolve().parents[2]
SMALL = {
    "blocks_import_dat": {"rows": 20_000, "fields": 20},
    "blocks_import_dm": {"rows": 20_000, "fields": 20},
    "blocks_check": {"rows": 20_000},
    "blocks_calc": {"rows": 20_000},
    "blocks_restrict": {"rows": 20_000},
    "blocks_classify": {"rows": 20_000},
    "blocks_regularize": {"rows": 20_000},
    "blocks_report": {"rows": 20_000},
    "blocks_view": {"rows": 20_000},
    "blocks_slice": {"rows": 20_000},
    "blocks_pick": {"rows": 20_000},
    "blocks_export": {"rows": 20_000, "formats": ["csv", "dat", "parquet"]},
    "blocks_swath_sub": {"rows": 20_000},
    "blocks_shell": {"counts": [20, 20, 20]},
    "blocks_prototype": {"counts": [10, 10, 10]},
    "blocks_fill": {"counts": [12, 12, 12], "triangles": 2_000},
}


def every() -> None:
    """Every operation of the stage once on a tiny model: the scenarios with small params, each in its own folder."""
    for name, params in SMALL.items():
        mod = importlib.import_module(f"bench.scenarios.{name}")
        with tempfile.TemporaryDirectory(prefix="bf-bench-compile-") as tmp:
            work = Path(tmp)
            mod.prepare({**params, "threads": 1}, work)
            mod.run({**params, "threads": 1}, work)


def fresh(jobs: bool) -> None:
    """The process: prints {import_s, warm_s, parts, jobs_s, process_mb, compiled, left}."""
    from bf_core.paths import configure_numba_cache

    cache = configure_numba_cache()
    t0 = time.perf_counter()
    import numba  # noqa: F401
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401

    t1 = time.perf_counter()
    from bf_core.ops.warm import warm as warm_ops
    from bf_core.stats import swath_blocks, swath_sub

    before = ic._kernels(cache)
    parts = warm_ops(["blocks"])
    assert list(parts) == ["blocks"], parts  # a step that failed is missing
    t2 = time.perf_counter()
    swath_blocks.warm()
    swath_sub.warm()
    parts["swath"] = time.perf_counter() - t2
    t3 = time.perf_counter()
    warmed = ic._kernels(cache)
    if jobs:
        every()
    t4 = time.perf_counter()
    left = sorted(ic._kernels(cache) - warmed)
    out: dict[str, Any] = {"import_s": t1 - t0, "warm_s": t3 - t1, "parts": parts, "jobs_s": t4 - t3,
                           "compiled": sorted(warmed - before), "left": left}  # fmt: skip
    try:
        import resource

        out["process_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except ImportError:  # Windows
        out["process_mb"] = None
    print(json.dumps(out))


def _process(cache: Path, jobs: bool = True) -> dict[str, Any]:
    path = os.pathsep.join([str(ROOT), str(ROOT / "core")])
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache), "PYTHONPATH": path}
    code = f"from bench.scenarios.blocks_compile import fresh; fresh({jobs!r})"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    if out.returncode:
        raise RuntimeError(out.stderr[-4000:])
    return dict(json.loads(out.stdout.strip().splitlines()[-1]))


def prepare(params: dict[str, Any], work: Path) -> None:
    pass


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    cache = work / "numba"
    shutil.rmtree(cache, ignore_errors=True)
    stage = _process(cache)
    files = [f for f in cache.rglob("*") if f.is_file()]
    loaded = _process(cache)
    # a function defined inside another one is not served from the cache: numba makes it anew in every process
    uncached = [k for k in loaded["compiled"] + loaded["left"] if ".locals." in k]
    assert not [k for k in loaded["compiled"] + loaded["left"] if k not in uncached], loaded
    assert loaded["parts"]["blocks"] < 0.5 * stage["parts"]["blocks"], (loaded["parts"], stage["parts"])
    left = max(0.0, stage["jobs_s"] - loaded["jobs_s"])
    return {
        "compile_s": round(stage["warm_s"] + left, 3),
        "warm_s": round(stage["warm_s"], 3),
        **{f"{part}_s": round(seconds, 3) for part, seconds in sorted(stage["parts"].items())},
        "left_s": round(left, 3),
        "left": ic._names(stage["left"]),
        "compiled": len(stage["compiled"]),
        "uncached": ic._names(uncached),
        "uncached_s": round(loaded["warm_s"] - loaded["parts"]["blocks"], 3),
        "load_s": round(loaded["warm_s"], 3),
        "jobs_s": round(loaded["jobs_s"], 3),
        "import_s": round(loaded["import_s"], 3),
        "process_mb": stage["process_mb"],
        "cache_mb": round(sum(f.stat().st_size for f in files) / 2**20, 1),
        "cache_files": len(files),
    }
