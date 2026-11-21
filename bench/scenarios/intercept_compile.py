"""The first compilation of the kernels of the true thickness and of the ore intercepts (ADR 0021 «Замеры»,
решение 20; ТЗ 8.0.2: «первая компиляция ядер модуля ≤ 30 с, в фоне после запуска»; the kernels are compiled
once into NUMBA_CACHE_DIR and loaded from it afterwards).

Fresh processes, as a worker that has just started, each with a cache of numba the scenario gives it:
  stage   an empty cache. The steps of the warm-up of the workers that go before `intercepts`
          (`ops/warm.py::QUICK`; `prior` of the params — those of them to run, by default all) are run first: the
          crossings of holes with wireframes and the trees of boxes (Э4, Э8), the trajectories, the interval
          engine (Э12) are compiled by them. Then the step `intercepts` — `intercepts.warm.warm()` — compiles
          the kernels of this stage alone, and every operation of the stage is run once on a set of 24 holes;
  loaded  the cache `stage` left: the same step and the same operations, nothing compiled;
  empty   (`empty` of the params, by default true) an empty cache and the step `intercepts` alone: with the start
          of numba and the kernels shared with the stages before — what a worker pays when the warm-up begins
          with this step.
The operations (the functions their handlers and routes call): «Истинная мощность» by a plane, by domains with
the chain, by wireframes (r_n = 0 and 5 m), by the oriented core with the chain; «Рудные пересечения» accepted by
the true thickness under the chain of domains and by the length under wireframes; the previews of one hole;
«Взять с каркаса».
  compile_s     the compilation of the kernels of the stage — the target of ТЗ 8.0.2: `stage_s` + `left_s`;
  stage_s       `intercepts.warm.warm()` after the steps before it;  tt_s, mesh_s, table_s — its parts;
  left_s        what the operations still waited for after it: their time in `stage` less their time in `loaded`
                — a kernel the warm-up does not reach is compiled inside the first job that needs it;  left —
                those kernels by name («package/module.function»; empty — the warm-up reaches them all, and
                `left_s` is the first run of the operations against a repeated one, not a compilation);
  own, shared   kernels the step compiled (a signature of a function is a kernel): of this stage
                (`intercepts/…`, `geo/nearest.…`) and of the stages before it — those the steps of `prior` do
                not reach (their names — `shared_names`);
  empty_s       the step on the empty cache;  empty_kernels — the kernels it compiled;
  load_s        the step from the filled cache: what every start of the program pays in the background;
  prior_s       the steps before `intercepts` on the empty cache;  jobs_s — the operations with everything loaded;
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

ROOT = Path(__file__).resolve().parents[2]
STEP = "intercepts"  # of `ops/warm.py`
CORE = "Керн"


def store_all(meta: dict[str, Any], engine: Any, folder: Path) -> dict[str, Any]:
    """`more` of `_samples.warm`: the veins and «Пласт» of `true_thickness_mesh`, and «Керн» — two measurements
    of the oriented core a hole (a table of events)."""
    from bf_core.drillholes import store

    from . import _samples, true_thickness_mesh

    out = true_thickness_mesh.store_veins({})(meta, engine, folder)
    names = [f"СКВ-{j + 1:05d}" for j in range(meta["holes"]) for _ in range(2)]
    depth = [d for _ in range(meta["holes"]) for d in (10.5, 30.5)]
    cols = [store.ColumnSpec("ALPHA_K", "number", "°"), store.ColumnSpec("BETA_K", "number", "°")]
    angles = [[35.0 + (j % 40) for j in range(len(depth))], [float(7 * j % 360) for j in range(len(depth))]]
    data = store.TableData(CORE, "event", names, depth, [None] * len(depth), cols, angles)
    core = store.write_table(engine, meta["set_id"], data, job_id=None, tick=_samples._noop)
    return {**out, "core": int(core["table_id"])}


def every(meta: dict[str, Any], engine: Any, folder: Path) -> None:
    """Every operation of the stage on the set of `_samples.warm` with what `store_all` added."""
    from bf_core.intercepts import jobs_run, router, routes_preview

    from . import _samples, intercepts, true_thickness, true_thickness_mesh
    from .composite_preview import hole_ids

    plane = true_thickness.orientation("global")[0]
    wires = true_thickness_mesh.body(meta, "mesh")["orientation"][0]
    core = {"source": "core", "table": str(meta["core"]), "alpha": "ALPHA_K", "beta": "BETA_K", "line": "boh",
            "d_core": 10}  # fmt: skip
    samples = true_thickness.body(meta, "global")
    requests = [samples, true_thickness.body(meta, "domain"), {**samples, "orientation": [wires]},
                {**samples, "orientation": [{**wires, "r_n": 5}]}, {**samples, "orientation": [core, plane]},
                {**true_thickness_mesh.body(meta, "plate"), "angles": False}]  # fmt: skip
    for k, request in enumerate(requests):
        calls = true_thickness.job(meta, engine, folder, request, job_id=k + 1)[3]
        if k == 1:
            true_thickness.threads_hashes(calls[0], (2,))
    intercepts.threads_hashes(intercepts.job(meta, engine, folder, {}, job_id=11)[3][0])
    by_wires = {**intercepts.body(meta, {}), "orientation": [wires], "min_by": "length", "name": "Замер: по каркасу"}
    jobs_run.run_intercepts(engine, folder, {"body": by_wires, "rows": meta["rows"]}, job_id=12, tick=_samples._noop)
    svc = _samples.service(engine, folder)
    hole = hole_ids(engine, meta["set_id"], 1)[0]
    for request in requests[1:3]:
        routes_preview.tt_preview(svc, {"params": request, "hole_id": hole})
    for request in (intercepts.body(meta, {}), by_wires):
        routes_preview.run_preview(svc, {"params": request, "hole_id": hole})
    router.orientation_plane(svc, {"wireframe_ids": wires["wireframe_ids"]})


def _kernels(cache: Path) -> set[str]:
    """The compiled kernels in the cache of numba, one for every signature of a function:
    «package/module.function#k» (`<package>_<hash>/<module>.<function>-<line>.py312.<k>.nbc`)."""
    return {f"{f.parent.name.rsplit('_', 1)[0]}/{f.name.split('-')[0]}#{f.name.split('.')[-2]}"
            for f in cache.rglob("*.nbc")}  # fmt: skip


def _own(kernel: str) -> bool:
    return kernel.startswith(("intercepts/", "geo/nearest."))


def _names(kernels: list[str]) -> str:
    return ", ".join(sorted({k.split("#")[0] for k in kernels}))


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
    before = _kernels(cache)
    from bf_core.intercepts.warm import warm

    parts = warm()
    t3 = time.perf_counter()
    warmed = _kernels(cache)
    assert sorted(parts) == ["mesh", "table", "tt"], parts
    if jobs:
        from . import _samples

        _samples.warm(every, more=store_all)
    t4 = time.perf_counter()
    out: dict[str, Any] = {"import_s": t1 - t0, "prior_s": t2 - t1, "warm_s": t3 - t2, "parts": parts,
                           "jobs_s": t4 - t3, "compiled": sorted(warmed - before),
                           "left": sorted(_kernels(cache) - warmed)}  # fmt: skip
    try:
        import resource

        out["process_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except ImportError:  # Windows
        out["process_mb"] = None
    print(json.dumps(out))


def _process(cache: Path, prior: list[str], jobs: bool = True) -> dict[str, Any]:
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache), "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "core")])}
    code = f"from bench.scenarios.intercept_compile import fresh; fresh({prior!r}, {jobs!r})"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    if out.returncode:
        raise RuntimeError(out.stderr[-4000:])
    return dict(json.loads(out.stdout.strip().splitlines()[-1]))


def before_step(prior: Any = None) -> list[str]:
    """The steps of the warm-up of a worker before `intercepts` (`ops/warm.py::QUICK`), or those of them named."""
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
        "left": _names(stage["left"]),
        "own": len(stage["compiled"]) - len(shared),
        "shared": len(shared),
        "shared_names": _names(shared),
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
