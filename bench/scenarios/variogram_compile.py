"""The first compilation of the kernels of the variography (ADR 0022 «Замеры», решение 5; ТЗ 8.0.2: «первая
компиляция ядер модуля ≤ 30 с, в фоне после запуска»; the kernels are compiled once into NUMBA_CACHE_DIR and
loaded from it afterwards).

Fresh processes, as a worker that has just started, each with a cache of numba the scenario gives it (the method
of `intercept_compile`):
  stage   an empty cache. The steps of the warm-up of the workers that go before `geostat` (`ops/warm.py::QUICK`;
          `prior` of the params — those of them to run, by default all) are run first: the trajectories, the
          reading of the columns, the domains and the normal scores of `stats` are compiled by them. Then the step
          `geostat` — `geostat.variogram.warm.warm()` — compiles the kernels of this stage alone, and every
          operation of the stage is run once on the set of 24 holes of `_samples.warm` («Пробы» of 1 m: AU,
          DENSITY, ROCK; «Литология»);
  loaded  the cache `stage` left: the same step and the same operations, nothing compiled;
  empty   (`empty` of the params, by default true) an empty cache and the step `geostat` alone: with the start of
          numba and the kernels shared with the stages before — what a worker pays when the warm-up begins with
          this step.
The operations (the functions their handlers and routes call): `variography.experimental` by the axes (the
values and the normal scores), by two given directions (a vertical one) with the domain of «Литология», along
the holes; `variography.map` — the fan, the rose and the map in a turned plane; `/preview`; `/fit` of the axes;
`/back` of the normal scores; `/curve`.
  compile_s     the compilation of the kernels of the stage — the target of ТЗ 8.0.2: `stage_s` + `left_s`;
  stage_s       `warm()` after the steps before it;  axes_s, downhole_s, fan_s, map_s, frozen_s — its parts;
  left_s        what the operations still waited for after it: their time in `stage` less their time in `loaded`
                — a kernel the warm-up does not reach is compiled inside the first job that needs it;  left —
                those kernels by name («package/module.function»; empty — the warm-up reaches them all, and
                `left_s` is the first run of the operations against a repeated one, not a compilation);
  own, shared   kernels the step compiled (a signature of a function is a kernel): of this stage
                (`variogram/…`) and of the stages before it — those the steps of `prior` do not reach (their
                names — `shared_names`);
  empty_s       the step on the empty cache;  empty_kernels — the kernels it compiled;
  load_s        the step from the filled cache: what every start of the program pays in the background;
  prior_s       the steps before `geostat` on the empty cache;  jobs_s — the operations with everything loaded;
  import_s      numba, numpy, pandas, pyarrow;  process_mb — the peak of the process `empty` (of `stage` when it
                is not run; Linux);  cache_mb, cache_files — the cache `stage` left.
The process `loaded` must not compile: it adds nothing to the cache and `load_s` is under a half of `stage_s`
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

from . import intercept_compile as ic

ROOT = Path(__file__).resolve().parents[2]
STEP = "geostat"  # of `ops/warm.py`
PARTS = ["axes", "downhole", "fan", "frozen", "map"]


def every(meta: dict[str, Any], engine: Any, folder: Path) -> None:
    """Every operation of the stage on the set of `_samples.warm`."""
    from bf_core.geostat.variogram import jobs as vj
    from bf_core.geostat.variogram import jobs_map, routes

    from . import _samples

    base = {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": "AU", "seed": 7}
    axes = {**base, "axes": {"ang1": 30.0, "ang2": -10.0, "ang3": 0.0}, "nlag": 8, "dh_nlag": 8}
    scores = {**axes, "transform": "nscore"}
    domain = {"table": str(meta["litho"]), "field": "ROCK", "values": ["ПЕС", "ГРН", "КВЦ"]}
    given = {**base, "directions": [{"az": 45.0, "plunge": 0.0}, {"az": 0.0, "plunge": 90.0}], "domain": domain,
             "nlag": 6, "downhole": False}  # fmt: skip
    forms = [vj.run_experimental(engine, folder, {"body": b}, tick=_samples._noop) for b in (axes, scores, given)]
    plane = {**base, "plane": {"ang1": 30.0, "ang2": -20.0, "ang3": 10.0}, "nlag": 6, "fan": {"n": 12},
             "map": {"nu": 6, "nv": 6}}  # fmt: skip
    jobs_map.run_map(engine, folder, {"body": plane}, tick=_samples._noop)
    svc = _samples.service(engine, folder)
    routes.preview(svc, axes)
    sigma2 = forms[0]["sigma2"]
    start = {"type": "semivariogram", "c0": 0.2 * sigma2, "common_axes": True,
             "structures": [{"type": t, "c": 0.4 * sigma2, "a1": a, "a2": 0.7 * a, "a3": 0.4 * a,
                             **axes["axes"]} for t, a in (("spherical", 15.0), ("exponential", 40.0))]}  # fmt: skip
    fitted = routes.fit(svc, {"key": forms[0]["key"], "model": start})["model"]
    y = {**start, "type": "nscore", "c0": 0.2, "structures": [{**s, "c": 0.4} for s in start["structures"]]}
    routes.back(svc, {"key": forms[1]["key"], "model": routes.fit(svc, {"key": forms[1]["key"], "type": "nscore",
                                                                         "model": y})["model"]})  # fmt: skip
    routes.curve(svc, {"model": fitted})


def _own(kernel: str) -> bool:
    return kernel.startswith("variogram/")


def fresh(prior: list[str], jobs: bool) -> None:
    """The process: prints {import_s, prior_s, warm_s, parts, jobs_s, process_mb, compiled, left}."""
    from bf_core.paths import configure_numba_cache

    cache = configure_numba_cache()
    t0 = time.perf_counter()
    import numba  # noqa: F401
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401

    t1 = time.perf_counter()
    if prior:
        from bf_core.ops.warm import warm as warm_ops

        done = warm_ops(prior)
        assert sorted(done) == sorted(prior), (sorted(done), prior)  # a step that failed is missing
    t2 = time.perf_counter()
    before = ic._kernels(cache)
    from bf_core.geostat.variogram.warm import warm

    parts = warm()
    t3 = time.perf_counter()
    warmed = ic._kernels(cache)
    assert sorted(parts) == PARTS, parts
    if jobs:
        from . import _samples

        _samples.warm(every, fields=3)
    t4 = time.perf_counter()
    out: dict[str, Any] = {"import_s": t1 - t0, "prior_s": t2 - t1, "warm_s": t3 - t2, "parts": parts,
                           "jobs_s": t4 - t3, "compiled": sorted(warmed - before),
                           "left": sorted(ic._kernels(cache) - warmed)}  # fmt: skip
    try:
        import resource

        out["process_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except ImportError:  # Windows
        out["process_mb"] = None
    print(json.dumps(out))


def _process(cache: Path, prior: list[str], jobs: bool = True) -> dict[str, Any]:
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache), "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "core")])}
    code = f"from bench.scenarios.variogram_compile import fresh; fresh({prior!r}, {jobs!r})"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    if out.returncode:
        raise RuntimeError(out.stderr[-4000:])
    return dict(json.loads(out.stdout.strip().splitlines()[-1]))


def before_step(prior: Any = None) -> list[str]:
    """The steps of the warm-up of a worker before `geostat` (`ops/warm.py::QUICK`), or those of them named."""
    from bf_core.ops.warm import QUICK

    steps = list(QUICK[: QUICK.index(STEP)])
    if prior is None:
        return steps
    unknown = [s for s in prior if s not in steps]
    if unknown:
        raise ValueError(f"not a step of the warm-up before {STEP}: {unknown}")
    return [s for s in steps if s in prior]


def prepare(params: dict[str, Any], work: Path) -> None:
    pass


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    cache = work / "numba"
    shutil.rmtree(cache, ignore_errors=True)
    prior = before_step(params.get("prior"))
    stage = _process(cache, prior)
    files = [f for f in cache.rglob("*") if f.is_file()]
    loaded = _process(cache, [])
    assert loaded["warm_s"] < 0.5 * stage["warm_s"] and not loaded["compiled"] and not loaded["left"], loaded
    assert any(_own(k) for k in stage["compiled"]), stage["compiled"]
    left = max(0.0, stage["jobs_s"] - loaded["jobs_s"])
    shared = [k for k in stage["compiled"] if not _own(k)]
    info: dict[str, Any] = {
        "compile_s": round(stage["warm_s"] + left, 3),
        "stage_s": round(stage["warm_s"], 3),
        **{f"{part}_s": round(seconds, 3) for part, seconds in sorted(stage["parts"].items())},
        "left_s": round(left, 3),
        "left": ic._names(stage["left"]),
        "own": len(stage["compiled"]) - len(shared),
        "shared": len(shared),
        "shared_names": ic._names(shared),
        "load_s": round(loaded["warm_s"], 3),
        "prior_s": round(stage["prior_s"], 3),
        "prior": ", ".join(prior),
        "jobs_s": round(loaded["jobs_s"], 3),
        "import_s": round(loaded["import_s"], 3),
        "process_mb": stage["process_mb"],
        "cache_mb": round(sum(f.stat().st_size for f in files) / 2**20, 1),
        "cache_files": len(files),
    }
    if params.get("empty", True):
        alone = work / "numba-empty"
        shutil.rmtree(alone, ignore_errors=True)
        empty = _process(alone, [], jobs=False)
        info.update(empty_s=round(empty["warm_s"], 3), empty_kernels=len(empty["compiled"]),
                    process_mb=empty["process_mb"])  # fmt: skip
    return info
