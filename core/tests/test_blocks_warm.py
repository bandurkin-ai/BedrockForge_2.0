"""The warm-up of the kernels of the block models (подшаг 17.21; ADR 0023 «Замеры», «CI»): every `warm()` runs and
gives seconds, a second call is safe and cheap, the kernels loaded from the cache of numba are fast, a warm-up
leaves no specialization for the jobs to compile (the dtypes of the warm-up are those of the jobs), and importing the
handlers and the warm-ups pulls no numba (numpy — not before BF-READY: the modules of the jobs are imported by a
worker, never by the API)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.blocks import warm as blocks_warm
from bf_core.blocks import warm_kernels
from bf_core.blocks.fill import columns as fc
from bf_core.blocks.fill import octree
from bf_core.blocks.fill import warm as fill_warm
from bf_core.blocks.schema import GridV2
from tests.test_blocks_fields_store import bc, blocks_router  # noqa: F401  (fixtures)
from tests.test_blocks_regularize import FIELDS, PLAIN, bp, build, random_values, run, sample  # noqa: F401
from tests.test_fill_octree import box

KERNEL_MODULES = (
    "bf_core.blocks.fill.columns",
    "bf_core.blocks.fill.boundary",
    "bf_core.blocks.fill.octree",
    "bf_core.blocks.fill.frac",
    "bf_core.blocks.shell",
    "bf_core.blocks.shell_merge",
    "bf_core.blocks.regularize_kernels",
    "bf_core.blocks.slice",
    "bf_core.blocks.slice_poly",
    "bf_core.blocks.report_kernels",
)
COLD_LIMIT_S = 60.0  # the target of the ТЗ is 30 s on the machine of the tests; a CI runner may be twice as slow
CACHE_LIMIT_S = 15.0  # loading all of it from the cache of numba (the imports of pandas and pyarrow included)


def dispatchers() -> Iterator[tuple[str, Any]]:
    for name in KERNEL_MODULES:
        mod = import_module(name)
        for attr, obj in vars(mod).items():
            if hasattr(obj, "signatures") and hasattr(obj, "py_func"):
                yield f"{name}.{attr}", obj


def signatures() -> dict[str, int]:
    return {name: len(d.signatures) for name, d in dispatchers()}


def check_seconds(took: dict[str, float], parts: tuple[str, ...]) -> None:
    assert tuple(took) == parts
    assert all(isinstance(v, float) and v >= 0.0 for v in took.values())


# --- every warm() -------------------------------------------------------------------------------------------------


def test_fill_warm_runs_and_a_second_call_is_safe_and_cheap() -> None:
    check_seconds(fill_warm.warm(), fill_warm.PARTS)
    t0 = time.perf_counter()
    again = fill_warm.warm()
    check_seconds(again, fill_warm.PARTS)
    assert time.perf_counter() - t0 < 5.0  # nothing compiled again


def test_kernels_warm_runs_and_a_second_call_is_safe_and_cheap() -> None:
    check_seconds(warm_kernels.warm(), warm_kernels.PARTS)
    t0 = time.perf_counter()
    check_seconds(warm_kernels.warm(), warm_kernels.PARTS)
    assert time.perf_counter() - t0 < 5.0
    # the temporary project of the regularization is gone
    tmp = Path(__import__("tempfile").gettempdir())
    assert not list(tmp.glob(warm_kernels.PREFIX + "*"))


def test_module_warms_of_the_stage_return_seconds() -> None:
    from bf_core.blocks.calc_job import warm as calc
    from bf_core.blocks.report import warm as report

    assert set(calc()) == {"calc"} and set(report()) == {"report"}
    blocks_warm.warm()  # the step of `ops/warm.py`: never raises, whatever is registered


def test_a_failing_part_is_skipped_and_the_next_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_d: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(fill_warm, "_rays", boom)
    took = fill_warm.warm()
    assert tuple(took) == ("boundary", "octree", "points")


# --- no kernel is compiled again by a job -------------------------------------------------------------------------


def test_the_warm_up_leaves_no_specialization_to_the_jobs(bp) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    fill_warm.warm()
    warm_kernels.warm()
    from bf_core.blocks.slice import warm as slice_warm

    slice_warm()
    from bf_core.blocks.report import warm as report_warm

    report_warm()
    before = signatures()
    assert before and sum(before.values()) > 20
    # a fill as the job does it: another grid, another body, priorities, the three modes, 1 and 4 threads
    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [5, 4, 3], "size": [10.0, 10.0, 10.0], "div": [4, 4, 4],
                      "rotation": 0.0, "order": "F"})  # fmt: skip
    meshes = [fc.Mesh.of_project(grid, *box((3.0, 4.0, 5.0), (31.0, 22.0, 18.0)), "A"),
              fc.Mesh.of_project(grid, *box((20.0, 10.0, 8.0), (44.0, 33.0, 27.0)), "B")]  # fmt: skip
    for mode, threads, prio in (("inside", 1, None), ("both", 4, [2, 1]), ("outside", 2, [None, None])):
        octree.fill_grid(grid, meshes, priorities=prio, mode=mode, m=3, threads=threads)
    # a regularization as the job does it: sub-blocks and without, a field density, a number, FRAC, categories
    rng = np.random.default_rng(3)
    p = sample(20, rng)
    model = build(bp, PLAIN, FIELDS, p, random_values(20, rng))
    run(bp, model, [2.0, 2.0, 2.0], density="SG", threads=2)
    run(bp, model, [1.5, 1.5, 1.5], threads=1)
    # the same without FRAC (the model of a DAT/DM import) with a density field: the field read from the file is
    # read-only and FRAC is not — a specialization of its own that the bench of a cold cache found (17.39b)
    plain = [f for f in FIELDS if f["name"] != "FRAC"]
    values = {k: v for k, v in random_values(20, rng).items() if k != "FRAC"}
    model = build(bp, PLAIN, plain, p, values)
    run(bp, model, [2.0, 2.0, 2.0], density="SG", categories=["DOM"], threads=2)
    run(bp, model, [2.0, 2.0, 2.0], density=2.7, threads=1)
    assert signatures() == before


def test_the_shell_and_the_pick_of_a_job_find_the_kernels_compiled() -> None:
    from bf_core.blocks import lod, shell, shell_merge

    warm_kernels.warm()
    before = signatures()
    grid = GridV2.of({"corner": [10.0, 20.0, 5.0], "counts": [6, 5, 4], "size": [5.0, 5.0, 2.5], "div": [2, 2, 2],
                      "rotation": 30.0, "order": "F"})  # fmt: skip
    rng = np.random.default_rng(5)
    p = np.sort(rng.choice(6 * 5 * 4, size=60, replace=False)).astype(np.uint64)
    s, w = np.zeros((60, 3), np.uint8), np.full((60, 3), 2, np.uint8)
    ok = rng.random(60) > 0.2
    k = rng.integers(0, 5, 60).astype(np.uint16)
    s.setflags(write=False)  # the columns of the cache of a model: what the display passes (17.39b)
    w.setflags(write=False)
    for sub in (False, True):
        f = shell.exposed(grid, p, s if sub else None, w if sub else None, ok, k, shell=True, threads=4)
        shell_merge.merge_faces(grid, f)
        lod.view(grid, p, s if sub else None, w if sub else None, ok, k, limit=40)
    assert signatures() == before


# --- time: cold and from the cache --------------------------------------------------------------------------------

SCRIPT = """
import json, sys, time
t00 = time.perf_counter()
from bf_core.blocks.calc_job import warm as calc
from bf_core.blocks.fill.warm import warm as fill
from bf_core.blocks.report import warm as report
from bf_core.blocks.slice import warm as slice_
from bf_core.blocks.warm_kernels import warm as kernels
took = {}
for name, fn in (("calc", calc), ("report", report), ("slice", slice_), ("fill", fill), ("kernels", kernels)):
    t0 = time.perf_counter()
    r = fn()
    took[name] = time.perf_counter() - t0
    if name in ("fill", "kernels"):
        took.update({name + "." + k: v for k, v in r.items()})
print(json.dumps({"total": time.perf_counter() - t00, "took": took}))
"""


def _fresh(cache: Path) -> dict[str, Any]:
    import os

    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache)}
    r = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]


def test_first_compilation_is_within_the_target_and_the_cache_is_fast(tmp_path: Path) -> None:
    cache = tmp_path / "numba"
    cache.mkdir()
    cold = _fresh(cache)
    cached = _fresh(cache)
    print("cold", json.dumps(cold["took"], indent=1))  # for the table of the report (-s)
    print("cached", json.dumps(cached["took"], indent=1))
    assert cold["total"] <= COLD_LIMIT_S
    assert cached["total"] <= CACHE_LIMIT_S
    assert cached["total"] < cold["total"] / 2
    for part in ("fill.rays", "fill.boundary", "fill.octree", "fill.points", "kernels.shell", "kernels.merge"):
        assert cached["took"][part] < 2.0  # the kernels themselves load in tens of ms


# --- the imports ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module, numpy_too",
    [
        ("bf_core.blocks.fill.jobs", True),
        ("bf_core.blocks.fill.warm", True),
        ("bf_core.blocks.warm_kernels", True),
        ("bf_core.blocks.warm", True),
        ("bf_core.blocks.calc_job", False),  # numpy at module level (the kernel of numexpr); a worker only
        ("bf_core.blocks.report", False),
    ],
)
def test_importing_the_handlers_and_the_warm_ups_does_not_pull_numba(module: str, numpy_too: bool) -> None:
    heavy = ("numba", "pandas", "pyarrow", "duckdb", "scipy") + (("numpy",) if numpy_too else ())
    code = (f"import sys, importlib; importlib.import_module({module!r});"
            f"print(','.join(m for m in {heavy!r} if m in sys.modules))")  # fmt: skip
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"{module} imported: {r.stdout.strip()}"


def test_the_slice_of_a_job_finds_the_kernels_compiled() -> None:
    from bf_core.blocks import slice as sl
    from bf_core.blocks.slice_poly import section_polygons

    sl.warm()
    before = signatures()
    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [3, 3, 2], "size": [4.0, 4.0, 4.0], "div": [2, 2, 2],
                      "rotation": 20.0, "order": "F"})  # fmt: skip
    p = np.arange(10, dtype=np.uint64)
    s, w = np.zeros((10, 3), np.uint8), np.full((10, 3), 2, np.uint8)
    num, cat = np.linspace(0.0, 1.0, 10), (np.arange(10) % 3).astype(np.int32)
    for a in (s, w, num, cat):
        a.setflags(write=False)  # the cache of columns gives read-only ones
    ok = np.ones(10, dtype=np.bool_)
    for normal in ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (1.0, 2.0, 3.0)):
        pl = sl.plane_of(grid, (6.0, 6.0, 4.0), normal)
        for values, kind in ((num, sl.NUMBER), (cat, sl.CATEGORY), (None, None)):
            sl.slice_rows(grid, pl, p, s, w, ok, values, kind)
        section_polygons(grid, pl, p, s, w, ok)
    assert signatures() == before


def test_the_kernels_of_the_swath_of_sub_blocks_come_from_the_cache(tmp_path: Path) -> None:
    """They were closures (`_build`), which numba compiles anew in every process (≈ 5.7 s); now module-level."""
    import os

    cache = tmp_path / "numba"
    cache.mkdir()
    script = "import time\nt = time.perf_counter()\nfrom bf_core.stats import swath_sub\nswath_sub.warm()\n" \
             "print(time.perf_counter() - t)"
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache)}
    took = []
    for _ in range(2):
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300, env=env)
        assert r.returncode == 0, r.stderr
        took.append(float(r.stdout.strip().splitlines()[-1]))
    assert took[1] < 2.5 and took[1] < took[0] / 2, took
