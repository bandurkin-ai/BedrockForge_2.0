"""The first compilation of the kernels of the implicit model (ADR 0026 «Замеры»; ТЗ 8.0.2: «первая компиляция ядер
модуля ≤ 30 с, в фоне после запуска»; из кеша ≤ 1 с; the lesson 17.39б: a kernel that the warm-up compiles for other
arrays than the job gives it is compiled again inside the first job; подшаг 19.21).

Fresh processes, as a worker that has just started, each with a cache of numba the scenario gives it (the method of
`estimate_compile`), on a small project (the fixture scene of `tests/fixtures/implicit`: 100 holes, the set,
«Литология», «Пробы»; a model of three domains, a vein and a numeric shell, the step 8 m):
  stage   an empty cache. The steps of the warm-up of the workers before `implicit` (`ops/warm.py::QUICK`; `prior` of
          the params — those of them to run) go first. Then the step `implicit` — `model.warm.warm()` — and the jobs
          `implicit.build` and `implicit.update` of that model (the data, the patches with the smoothing, the field,
          the isosurface, the vein, the shell, the bodies, the checks);
  loaded  the cache `stage` left: the same step and the same jobs, nothing compiled;
  empty   the step `implicit` alone on an empty cache (`empty` of the params, by default true): what a worker pays when
          the warm-up begins with this step.
  compile_s  the compilation of the kernels of the module — the target of ТЗ 8.0.2: `warm()` on an empty cache
  stage_s    the same, after the steps before it;  parts — the seconds of every part of it (`warm.PARTS`)
  left_s     what the jobs still waited for after it: their time in `stage` less their time in `loaded`; with
             `prior` = [] it is the kernels of OTHER modules the jobs need (the trajectories of `drillholes`,
             `stats/weighted`: compiled by the steps `composites`, `stats` of the warm-up, which `full` runs first),
             so only `left_own` is a limit
  left_own   the kernels of the module (`model/…`, `geo/iso…`) that the jobs compiled after `warm()` — the lesson
             17.39б: none must be there (limit 0); the names of all that were compiled late are in `left`
  own, shared   the kernels `warm()` compiled: of the module and the others
  empty_s    the step on the empty cache;  load_s — from the filled cache: what every start pays in the background;
  jobs_s     the jobs with everything loaded;  process_mb — the peak of the process `empty`;  cache_mb
What is checked (a failure stops the run): `loaded` compiles nothing and loads `warm()` in under a half of the cold
time; `stage` compiled the kernels of the module and every part of `warm()` ran; the jobs gave the same bits in both
processes (the geometry of the outputs and the files of the model)."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import blocks_common as bc
from . import implicit_common as ic

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "implicit"
STEP = "implicit"  # of `ops/warm.py`
OWN = ("model/", "geo/iso")  # `<package>/<module>.<function>#k` of the cache
PARTS = ["data", "field", "iso", "solve", "veins"]
BOX = [0.0, 0.0, 120.0, 420.0, 420.0, 330.0]


def _kernels(cache: Path) -> set[str]:
    """The compiled kernels in the cache of numba, one for every signature of a function:
    «package/module.function#k» (`<package>_<hash>/<module>.<function>-<line>.py312.<k>.nbc`)."""
    return {f"{f.parent.name.rsplit('_', 1)[0]}/{f.name.split('-')[0]}#{f.name.split('.')[-2]}"
            for f in cache.rglob("*.nbc")}  # fmt: skip


def _names(kernels: list[str]) -> str:
    return ", ".join(sorted({k.split("#")[0] for k in kernels}))


def _own(kernel: str) -> bool:
    return kernel.startswith(OWN)


def rows(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def fixture_project(ctx: bc.Ctx) -> dict[str, Any]:
    """The fixture scene in the project: the set, «Литология» (CODE), «Пробы» (AU) → ids."""
    from bf_core.db import dialect
    from bf_core.drillholes import store as dh

    collars = rows("collars.csv")
    with dialect.write_tx(ctx.engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        dh.replace_collars(conn, set_id, [dh.CollarRow(r["HOLE"], float(r["X"]), float(r["Y"]), float(r["Z"]),
                                                       float(r["TD"]), float(r["AZIMUTH"]), float(r["DIP"]))
                                          for r in collars])  # fmt: skip

    def table(name: str, kind: str, got: list[dict[str, str]], column: str, ctype: str) -> int:
        vals = [r[column] for r in got] if ctype == "text" else [float(r[column]) if r[column] else None for r in got]
        data = dh.TableData(name, kind, [r["HOLE"] for r in got], [float(r["FROM"]) for r in got],
                            [float(r["TO"]) for r in got], [dh.ColumnSpec(column, ctype)], [vals])  # fmt: skip
        return int(dh.write_table(ctx.engine, set_id, data, job_id=None, tick=ic.noop)["table_id"])

    return {"set_id": set_id, "litho": table("Литология", "interval", rows("lithology.csv"), "CODE", "text"),
            "assays": table("Пробы", "assay", rows("assays.csv"), "AU", "number"), "box": BOX, "thresholds": [0.5]}


def groups(meta: dict[str, Any]) -> list[dict[str, Any]]:
    names = ((2, "ALV"), (3, "SS"), (4, "SH"))
    domain = [{"id": gid, "name": code, "type": "domain", "codes": [code]} for gid, code in names]
    vein = {"id": 8, "name": "Жила", "type": "vein", "codes": ["VN"], "params": {"bound": "hull", "offset": 0.0}}
    return [*domain, ic.shell(meta, 6), vein]


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        meta = fixture_project(ctx)
        pou = {"n_patch": 300, "n_min": 100}
        meta["model"] = ic.create_model(ctx, meta, "Компиляция", groups(meta), h=8.0, pou=pou)
    finally:
        ctx.close()
    ic.keep(work, meta)


def every(ctx: Any, meta: dict[str, Any]) -> dict[str, Any]:
    """The jobs on the project → the bits of what they wrote (the same in every process)."""
    mid = int(meta["model"])
    first = ic.run_job(ctx, mid, threads=2)
    assert first["state"] == "ready" and len(first["outputs"]) >= 5, first["outputs"]
    assert first["stats"]["patches"] > 0
    after = ic.fingerprint(ctx, mid)
    upd = ic.run_job(ctx, mid, update=True, threads=2)
    assert upd["mode"] == "update" and upd["reused"]["patches"] == first["stats"]["patches"], upd["reused"]
    assert ic.fingerprint(ctx, mid) == after, "the update of nothing changed the bits"
    return after


def fresh(prior: list[str], jobs: bool, work: str) -> None:
    """The process: prints {import_s, prior_s, warm_s, parts, jobs_s, process_mb, compiled, left, bits}."""
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
    from bf_core.model.warm import warm

    parts = warm()
    t3 = time.perf_counter()
    warmed = _kernels(cache)
    assert sorted(parts) == PARTS, parts
    bits: dict[str, Any] = {}
    if jobs:
        ctx = bc.Ctx.open(Path(work))
        try:
            bits = every(ctx, ic.read_meta(Path(work)))
        finally:
            ctx.close()
    t4 = time.perf_counter()
    out: dict[str, Any] = {"import_s": t1 - t0, "prior_s": t2 - t1, "warm_s": t3 - t2, "parts": parts,
                           "jobs_s": t4 - t3, "bits": bits, "compiled": sorted(warmed - before),
                           "left": sorted(_kernels(cache) - warmed)}  # fmt: skip
    try:
        import resource

        out["process_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except ImportError:  # Windows
        out["process_mb"] = None
    print(json.dumps(out))


def _process(cache: Path, prior: list[str], work: Path, jobs: bool = True) -> dict[str, Any]:
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache), "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "core")])}
    code = f"from bench.scenarios.implicit_compile import fresh; fresh({prior!r}, {jobs!r}, {str(work)!r})"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    if out.returncode:
        raise RuntimeError(out.stderr[-4000:])
    return dict(json.loads(out.stdout.strip().splitlines()[-1]))


def before_step(prior: Any = None) -> list[str]:
    """The steps of the warm-up of a worker before `implicit` (`ops/warm.py::QUICK`), or those of them named."""
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
    own = [k for k in stage["compiled"] if _own(k)]
    assert own, stage["compiled"]
    assert stage["bits"] == loaded["bits"] and stage["bits"]["geometry"], "the jobs gave other bits in another process"
    left = max(0.0, stage["jobs_s"] - loaded["jobs_s"])
    left_own = [k for k in stage["left"] if _own(k)]
    shared = [k for k in stage["compiled"] if not _own(k)]
    info: dict[str, Any] = {
        "compile_s": round(stage["warm_s"], 3),  # the target: `warm()` on an empty cache, ТЗ 8.0.2 (≤ 30 с)
        "stage_s": round(stage["warm_s"], 3),
        **{f"{part}_s": round(seconds, 3) for part, seconds in sorted(stage["parts"].items())},
        "left_s": round(left, 3),
        "left_own": len(left_own),
        "left": _names(stage["left"]),
        "own": len(own),
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
        empty = _process(alone, [], work, jobs=False)
        info.update(empty_s=round(empty["warm_s"], 3), empty_kernels=len(empty["compiled"]),
                    process_mb=empty["process_mb"])  # fmt: skip
    return info

