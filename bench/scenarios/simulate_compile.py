"""The first compilation of the kernels of the simulation (ADR 0024 «Замеры»; ТЗ 8.0.2: «первая компиляция ядер модуля
≤ 30 с, в фоне после запуска»; the lesson 17.39б: a kernel that the warm-up compiles for other arrays than the job
gives it is compiled again inside the first job).

Fresh processes, as a worker that has just started, each with a cache of numba the scenario gives it (the method of
`estimate_compile`), on a small project of `simulate_job` (a set of one-sample holes, the models of the variogram, a
block model with DOM and the secondary SEC, four stored rows: SGS, SIS by thresholds, by categories, co-SGS):
  stage   an empty cache. The steps of the warm-up of the workers before `simulate` (`ops/warm.py::QUICK`; `prior`
          of the params — those of them to run) go first: what other modules compile is not this module's. Then the step
          `simulate` — `warm_sgs`, `warm_sis`, `warm_cosgs` — and every operation of the module once on that project:
          the job `simulate.run` of the four rows and `simulate.post` of the SGS (the statistics and P(Z ≥ t)) and of
          the categories (`PROB_<code>`, `MODE`);
  loaded  the cache `stage` left: the same step and the same operations, nothing compiled;
  empty   the step `simulate` alone on an empty cache (`empty` of the params, by default true): what a worker pays
          when the warm-up begins with this step.
  compile_s     the compilation of the kernels of the module — the target of ТЗ 8.0.2: the step `simulate` on an empty
                cache, `empty` (nothing compiled before it);  stage_s — the same after the steps before it;
                sgs_s, sis_s, cosgs_s — the parts of it
  left_s        what the operations still waited for after it: their time in `stage` less their time in `loaded`
  left_own      the kernels of the module (`simulation/…`) that the operations compiled after the step — the lesson
                17.39б: none must be there (limit 0); left_shared — the kernels of the others (`estimate/…`, `stats/…`,
                `blocks/…`, …) the operations compiled after it; the names of all that were compiled late are in `left`
  own, shared   the kernels the step compiled: of the module and the others
  load_s        the step from the filled cache: what every start pays in the background
  jobs_s        the operations with everything loaded;  process_mb — the peak of the process `empty`;  cache_mb
What is checked (a failure stops the run): `loaded` compiles nothing and loads the step in under a half of the cold
time; `stage` compiled the kernels of the module; the files of the four rows have the same bits in both processes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc
from . import intercept_compile as ic
from . import simulate_common as sc
from . import simulate_job as sj

ROOT = Path(__file__).resolve().parents[2]
STEP = "simulate"  # of `ops/warm.py`
OWN = "simulation/"  # `<package>/<module>.<function>#k` of the cache
PARTS = ("sgs", "sis", "cosgs")
ROWS = ("sgs", "sis", "sis_cat", "cosgs")


def prepare(params: dict[str, Any], work: Path) -> None:
    meta = sj.build(work, params, lith=True)
    w = sc.World.open(work)
    ctx = bc.Ctx.open(work)
    try:
        th = [float(x) for x in np.quantile(w.z, (0.25, 0.5, 0.75))]
        median = {"variogram": {"model_id": meta["vz"]}}
        meta["sis"] = sj.create(ctx, sj.body(meta, name="SIS пороги", method={"ik": {"thresholds": th,
                                                                                   "models": "median"}}, **median))
        cat = {"samples": {"set_id": meta["set_id"], "table": str(meta["lith"]), "field": "LITH"}}
        meta["sis_cat"] = sj.create(ctx, sj.body(meta, name="SIS категории", method={"ik": {
            "categories": list(sc.CODES), "labels": ["А", "Б", "В"], "models": "median"}}, **median, **cat))
        meta["cosgs"] = sj.create(ctx, sj.body(meta, name="Ко-SGS", secondary={"field": "SEC", "rho": None}))
        meta["thresholds"] = th
        ctx.keep(**meta)
    finally:
        ctx.close()


def every(ctx: bc.Ctx, meta: dict[str, Any]) -> list[str]:
    """Every operation of the module on the project → the SHA-256 of the file of every row (the answers)."""
    from bf_core.db import dialect
    from bf_core.geostat.simulation import job, post
    from bf_core.geostat.simulation import store as st

    shas = []
    for key in ROWS:
        out = job.run_simulate(ctx.engine, ctx.folder, {"simulation_id": meta[key]}, threads=2)
        assert out["nodes"] > 0 and out["realizations"] == meta["n_real"], out
        with dialect.read_tx(ctx.engine) as conn:
            shas.append(str(st.real_sha(conn, meta[key])))
    for key, over in (("sgs", {"thresholds": [2.0]}), ("sis_cat", {})):
        done = post.run_post(ctx.engine, ctx.folder, {"simulation_id": meta[key], "replace": True, **over,
                                                       "expected_version": ctx.version(meta["model_id"])})  # fmt: skip
        assert len(done["fields"]) >= 2, done
    return shas


def fresh(prior: list[str], jobs: bool, work: str) -> None:
    """The process: prints {import_s, prior_s, warm_s, parts, jobs_s, process_mb, compiled, left, answers}."""
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
    from bf_core.geostat.simulation.cosgs import warm_cosgs
    from bf_core.geostat.simulation.sgs import warm_sgs
    from bf_core.geostat.simulation.sis import warm_sis

    parts: dict[str, float] = {}
    for name, fn in zip(PARTS, (warm_sgs, warm_sis, warm_cosgs), strict=True):
        p0 = time.perf_counter()
        fn()
        parts[name] = time.perf_counter() - p0
    t3 = time.perf_counter()
    warmed = ic._kernels(cache)
    shas: list[str] = []
    if jobs:
        ctx = bc.Ctx.open(Path(work))
        try:
            shas = every(ctx, ctx.meta())
        finally:
            ctx.close()
    t4 = time.perf_counter()
    out: dict[str, Any] = {"import_s": t1 - t0, "prior_s": t2 - t1, "warm_s": t3 - t2, "parts": parts,
                           "jobs_s": t4 - t3, "answers": shas, "compiled": sorted(warmed - before),
                           "left": sorted(ic._kernels(cache) - warmed)}  # fmt: skip
    try:
        import resource

        out["process_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except ImportError:  # Windows
        out["process_mb"] = None
    print(json.dumps(out))


def _process(cache: Path, prior: list[str], work: Path, jobs: bool = True) -> dict[str, Any]:
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache), "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "core")])}
    code = f"from bench.scenarios.simulate_compile import fresh; fresh({prior!r}, {jobs!r}, {str(work)!r})"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    if out.returncode:
        raise RuntimeError(out.stderr[-4000:])
    return dict(json.loads(out.stdout.strip().splitlines()[-1]))


def before_step(prior: Any = None) -> list[str]:
    """The steps of the warm-up of a worker before `simulate` (`ops/warm.py::QUICK`), or those of them named."""
    from bf_core.ops.warm import QUICK

    steps = list(QUICK[: QUICK.index(STEP)])
    if prior is None:
        return steps
    unknown = [s for s in prior if s not in steps]
    if unknown:
        raise ValueError(f"not a step of the warm-up before {STEP}: {unknown}")
    return [s for s in steps if s in prior]


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    cache = work / "numba"
    shutil.rmtree(cache, ignore_errors=True)
    prior = before_step(params.get("prior"))
    stage = _process(cache, prior, work)
    files = [f for f in cache.rglob("*") if f.is_file()]
    loaded = _process(cache, [], work)
    assert loaded["warm_s"] < 0.5 * stage["warm_s"] and not loaded["compiled"] and not loaded["left"], loaded
    own = [k for k in stage["compiled"] if k.startswith(OWN)]
    assert own, stage["compiled"]
    assert len(stage["answers"]) == len(ROWS), stage["answers"]
    assert stage["answers"] == loaded["answers"], "the files of the rows differ between the processes"
    left = max(0.0, stage["jobs_s"] - loaded["jobs_s"])
    left_own = [k for k in stage["left"] if k.startswith(OWN)]
    left_shared = [k for k in stage["left"] if not k.startswith(OWN)]
    shared = [k for k in stage["compiled"] if not k.startswith(OWN)]
    info: dict[str, Any] = {
        "stage_s": round(stage["warm_s"], 3),
        **{f"stage_{part}_s": round(seconds, 3) for part, seconds in sorted(stage["parts"].items())},
        "left_s": round(left, 3),
        "left_own": len(left_own),
        "left_shared": len(left_shared),
        "left": ic._names(stage["left"]),
        "own": len(own),
        "shared": len(shared),
        "shared_names": ic._names(shared),
        "load_s": round(loaded["warm_s"], 3),
        "prior_s": round(stage["prior_s"], 3),
        "prior": ", ".join(prior),
        "jobs_s": round(loaded["jobs_s"], 3),
        "import_s": round(loaded["import_s"], 3),
        "cache_mb": round(sum(f.stat().st_size for f in files) / 2**20, 1),
        "cache_files": len(files),
    }
    if params.get("empty", True):
        alone = work / "numba-empty"
        shutil.rmtree(alone, ignore_errors=True)
        empty = _process(alone, [], work, jobs=False)
        info.update(compile_s=round(empty["warm_s"], 3), empty_kernels=len(empty["compiled"]),
                    process_mb=empty["process_mb"],
                    **{f"{part}_s": round(seconds, 3) for part, seconds in sorted(empty["parts"].items())})  # fmt: skip
    return info
