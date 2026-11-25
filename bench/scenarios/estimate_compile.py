"""The first compilation of the kernels of the estimation (ADR 0024 «Замеры»; ТЗ 8.0.2: «первая компиляция ядер модуля
≤ 30 с, в фоне после запуска»; the lesson 17.39б: a kernel that the warm-up compiles for other arrays than the job
gives it is compiled again inside the first job).

Fresh processes, as a worker that has just started, each with a cache of numba the scenario gives it (the method of
`variogram_compile`), on a small project of `estimate_world` (the set, the model of the variogram, a block model, a row
OK of a block and a row IK):
  stage   an empty cache. The steps of the warm-up of the workers before `estimate` (`ops/warm.py::QUICK`; `prior` of
          the params — those of them to run) go first: what other modules compile is not this module's. Then the step
          `estimate` — `geostat.estimate.warm.warm()` — and every operation of the module once, on that project: the
          job `estimate.run` of both rows, `estimate.global`, the Q-Q, `estimate.xval` by holes and by samples,
          `estimate.kna`, the preview of a block with the inspector (`kernel.estimate_block`);
  loaded  the cache `stage` left: the same step and the same operations, nothing compiled;
  empty   the step `estimate` alone on an empty cache (`empty` of the params, by default true): what a worker pays
          when the warm-up begins with this step.
  compile_s     the compilation of the kernels of the module — the target of ТЗ 8.0.2: `warm()` on an empty cache
  stage_s       the same, after the steps before it;  parts — the seconds of every part of it (`warm.PARTS`)
  left_s        what the operations still waited for after it: their time in `stage` less their time in `loaded`;
                with `prior` = [] it is the kernels of OTHER modules the jobs need (the trajectories of `drillholes`,
                `stats/qq`, `stats/weighted`: compiled by the steps `composites`, `stats` of the warm-up, which `full`
                runs first — there `left_s` is ≈ 0), so only `left_own` is a limit
  left_own      the kernels of the module (`estimate/…`) that the operations compiled after `warm()` — the lesson
                17.39б: none must be there (limit 0); the names of all that were compiled late are in `left`
  own, shared   the kernels `warm()` compiled: of the module and the others
  empty_s       the step on the empty cache;  load_s — from the filled cache: what every start pays in the background;
  jobs_s        the operations with everything loaded;  process_mb — the peak of the process `empty`;  cache_mb
What is checked (a failure stops the run): `loaded` compiles nothing and loads `warm()` in under a half of the cold
time; `stage` compiled the kernels of the module; the operations gave the same answers in both processes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import estimate_common as ec
from . import estimate_world as ew
from . import intercept_compile as ic

ROOT = Path(__file__).resolve().parents[2]
STEP = "estimate"  # of `ops/warm.py`
OWN = "estimate/"  # `<package>/<module>.<function>#k` of the cache
PARTS = ["cov", "indicator", "kernel", "krige", "methods", "search"]


def every(ctx: Any, meta: dict[str, Any]) -> int:
    """Every operation of the module on the project; → the number of answers checked."""
    from bf_core.geostat.estimate import checks, job, kna, xval
    from bf_core.geostat.estimate import kernel as kn

    engine, folder = ctx.engine, ctx.folder
    version = ctx.version(meta["model_id"])
    ids = [meta["estimation"], meta["estimation_ik"]]
    out = job.run_estimate(engine, folder, {"expected_version": version, "replace": True, "estimation_ids": ids},
                           threads=2)  # fmt: skip
    assert len(out["fields"]) >= 12 and out["rows"][0]["counts"]["by_status"][0] > 0, out["rows"]
    checked = 1
    one = {"estimation_id": meta["estimation"]}
    assert checks.run_global(engine, folder, one, threads=2)["estimation_id"] == meta["estimation"]
    assert checks.run_qq(engine, folder, one)["estimation_id"] == meta["estimation"]
    for by in ("hole", "sample"):
        got = xval.run_xval(engine, folder, {**one, "by": by}, threads=2)
        assert got["n"] > 0 and got["by"] == by
    assert len(kna.run_kna(engine, folder, one)["blocks"]) >= 1
    _, s, rk = ew.look(ctx, meta)
    dep = ec.Deposit.of(meta["params"])
    d = kn.estimate_block(rk, dep.centres_of(dep.probes(1))[0], pool=s)  # the preview and the inspector
    assert d.counts["candidates"] > 0
    return checked + 4


def fresh(prior: list[str], jobs: bool, work: str) -> None:
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
    from bf_core.geostat.estimate.warm import warm

    parts = warm()
    t3 = time.perf_counter()
    warmed = ic._kernels(cache)
    assert sorted(parts) == PARTS, parts
    answers = 0
    if jobs:
        ctx, meta = ew.open_world(Path(work))
        try:
            answers = every(ctx, meta)
        finally:
            ctx.close()
    t4 = time.perf_counter()
    out: dict[str, Any] = {"import_s": t1 - t0, "prior_s": t2 - t1, "warm_s": t3 - t2, "parts": parts,
                           "jobs_s": t4 - t3, "answers": answers, "compiled": sorted(warmed - before),
                           "left": sorted(ic._kernels(cache) - warmed)}  # fmt: skip
    try:
        import resource

        out["process_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except ImportError:  # Windows
        out["process_mb"] = None
    print(json.dumps(out))


def _process(cache: Path, prior: list[str], work: Path, jobs: bool = True) -> dict[str, Any]:
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache), "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "core")])}
    code = f"from bench.scenarios.estimate_compile import fresh; fresh({prior!r}, {jobs!r}, {str(work)!r})"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    if out.returncode:
        raise RuntimeError(out.stderr[-4000:])
    return dict(json.loads(out.stdout.strip().splitlines()[-1]))


def before_step(prior: Any = None) -> list[str]:
    """The steps of the warm-up of a worker before `estimate` (`ops/warm.py::QUICK`), or those of them named."""
    from bf_core.ops.warm import QUICK

    steps = list(QUICK[: QUICK.index(STEP)])
    if prior is None:
        return steps
    unknown = [s for s in prior if s not in steps]
    if unknown:
        raise ValueError(f"not a step of the warm-up before {STEP}: {unknown}")
    return [s for s in steps if s in prior]


def prepare(params: dict[str, Any], work: Path) -> None:
    meta = ew.build(work, params)
    meta["params"] = {k: params[k] for k in ("rows", "samples")}
    (work / "blocks.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


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
    assert stage["answers"] == loaded["answers"] > 0, (stage["answers"], loaded["answers"])
    left = max(0.0, stage["jobs_s"] - loaded["jobs_s"])
    left_own = [k for k in stage["left"] if k.startswith(OWN)]
    shared = [k for k in stage["compiled"] if not k.startswith(OWN)]
    info: dict[str, Any] = {
        "compile_s": round(stage["warm_s"], 3),  # the target: `warm()` on an empty cache, ТЗ 8.0.2 (≤ 30 с)
        "stage_s": round(stage["warm_s"], 3),
        **{f"{part}_s": round(seconds, 3) for part, seconds in sorted(stage["parts"].items())},
        "left_s": round(left, 3),
        "left_own": len(left_own),
        "left": ic._names(stage["left"]),
        "own": len(own),
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
        empty = _process(alone, [], work, jobs=False)
        info.update(empty_s=round(empty["warm_s"], 3), empty_kernels=len(empty["compiled"]),
                    process_mb=empty["process_mb"])  # fmt: skip
    return info
