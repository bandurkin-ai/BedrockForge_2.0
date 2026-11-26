"""The warm-up of the kernels of the resource report (подшаг 20.11; ТЗ 8.10: компиляция ≤ 30 с; ADR 0027 решение 28):
`warm()` runs, gives seconds and a second call is cheap, a part that fails does not stop the others, the warm-up leaves
NO specialization for the jobs to compile (reports on models of several layouts — one row group or joined portions, f4
and f8 fields, sub-blocks, benches, a number as the density — add no signature), `compute._w` changes no bit and
copies only when it must, the first compilation is within 30 s and the cache is fast, and importing the warm-up pulls
no numba."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.projects.service import projects_of
from bf_core.resources import compute as cp
from bf_core.resources import job
from bf_core.resources import warm as rw
from bf_core.storage.blobs import BlobStore
from tests.test_blocks_report import bf_blocks, bf_model, put

KERNEL_MODULES = ("bf_core.resources.kernels",)
COLD_LIMIT_S = 30.0  # the target of the ТЗ (a CI runner may be slower: the real figure is ~3 s)
CACHE_LIMIT_S = 10.0


@pytest.fixture()
def rc(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post("/api/v1/projects", json={"name": "Ресурсы"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    return pclient


def dispatchers() -> Iterator[tuple[str, Any]]:
    for name in KERNEL_MODULES:
        for attr, obj in vars(import_module(name)).items():
            if hasattr(obj, "signatures") and hasattr(obj, "py_func"):
                yield f"{name}.{attr}", obj


def signatures() -> dict[str, list[str]]:
    return {name: sorted(str(s) for s in d.signatures) for name, d in dispatchers()}


# --- warm() ---------------------------------------------------------------------------------------------------------


def test_warm_runs_and_a_second_call_is_safe_and_cheap() -> None:
    took = rw.warm()
    assert tuple(took) == rw.PARTS and all(isinstance(v, float) and v >= 0.0 for v in took.values())
    t0 = time.perf_counter()
    assert tuple(rw.warm()) == rw.PARTS
    assert time.perf_counter() - t0 < 5.0  # nothing compiled again
    assert sum(len(v) for v in signatures().values()) >= 3  # prepare, accumulate, accumulate_bench


def test_a_failing_part_is_skipped_and_the_next_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(rw.WARMS, "prepare", boom)
    assert tuple(rw.warm()) == ("imports", "sums", "bench")


def test_the_step_of_the_workers_calls_the_kernels() -> None:
    """`ops/warm.py` runs the step `resources` for the quick and the heavy worker."""
    from bf_core.ops import warm as ops_warm

    assert "resources" in ops_warm.QUICK and "resources" in ops_warm.HEAVY


# --- no kernel is compiled again by a job ---------------------------------------------------------------------------

FIELDS = [
    {"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"},
    {"name": "G4", "dtype": "f4", "unit": "%", "role": "grade"},  # f4: a field of another dtype
    {"name": "DENS", "dtype": "f4", "role": "density"},
    {"name": "DENS8", "dtype": "f8"},
    {"name": "FRAC", "dtype": "f8", "role": "frac"},
    {"name": "DOM", "dtype": "cat", "labels": ["A", "B", "C"]},
]
GRID = {"corner": [0.0, 0.0, 0.0], "counts": [20, 10, 15], "size": [10.0, 10.0, 5.0]}
N = 3000  # the file has row groups of 1000 rows (`put`)


def plain_model(c: Any) -> int:
    rng = np.random.default_rng(7)
    au = rng.lognormal(0.0, 1.0, N)
    au[rng.random(N) < 0.05] = np.nan
    cols = {"AU": au, "G4": rng.random(N).astype(np.float32), "DENS": rng.normal(2.7, 0.1, N).astype(np.float32),
            "DENS8": rng.normal(2.7, 0.1, N), "FRAC": rng.uniform(0.5, 1.0, N),
            "DOM": rng.integers(-1, 3, N).astype(np.int32)}  # fmt: skip
    return put(c, GRID, FIELDS, np.arange(N, dtype=np.uint64), cols)


def report(c: Any, mid: int, chunk: int | None, **body: Any) -> dict[str, Any]:
    params = {"model_id": mid, "cutoffs": [0.0, 0.5, 1.0], "curve": 10, **body}
    return job.run_report(c.project.engine, c.project.folder, {"params": params, "save": False}, chunk=chunk)


def test_the_warm_up_leaves_no_specialization_to_the_jobs(rc) -> None:  # type: ignore[no-untyped-def]
    rw.warm()
    before = signatures()
    assert before["bf_core.resources.kernels.prepare"] and before["bf_core.resources.kernels.accumulate_bench"]
    mid = plain_model(rc)
    # a portion = a row group of the file (read-only columns), joined row groups (writable), a part of a row group
    for chunk in (1000, None, 700):
        for body in (
            {"elements": [{"field": "AU"}, {"field": "G4"}], "density": "DENS"},  # f4 density, f4 grade
            {"elements": [{"field": "G4"}], "cutoff_field": "G4", "density": "DENS8", "groups": ["DOM"]},
            {"elements": [{"field": "AU"}], "density": 2.7, "cutoffs": [None, 0.5], "groups": [{"kind": "bench"}]},
            {
                "elements": [{"field": "AU"}],
                "density": "DENS",
                "groups": ["DOM", {"kind": "bench"}],
                "restrict": {"cond": {"field": "G4", "op": ">", "value": 0.3}},
            },
        ):
            out = report(rc, mid, chunk, **body)
            assert out["rows"] and out["control"] is not None
    # sub-blocks: the extent of every row, the volume by `row_volumes`
    sub = bf_model(rc, bf_blocks())
    for chunk in (30, None):
        for body in (
            {"elements": [{"field": "AU_CUT"}, {"field": "CU_CUT"}], "density": "SG", "groups": ["ALT"]},
            {"elements": [{"field": "AU_CUT"}], "density": "DENSITY", "groups": [{"kind": "bench"}], "bench": {"h": 3}},
        ):
            assert report(rc, sub, chunk, **body)["rows"]
    assert signatures() == before


def test_one_specialization_per_kernel() -> None:
    """What the warm-up compiles is what the jobs use: one signature per kernel (the types of `compute._w`)."""
    rw.warm()
    sig = signatures()
    for name in ("prepare", "accumulate", "accumulate_bench"):
        assert len(sig[f"bf_core.resources.kernels.{name}"]) == 1, name


# --- compute._w ------------------------------------------------------------------------------------------------------


def test_w_copies_only_what_the_kernels_cannot_take() -> None:
    a = np.arange(5, dtype=np.float64)
    assert cp._w(a) is a  # writable f8: the same array
    ro = a.copy()
    ro.setflags(write=False)
    got = cp._w(ro)
    assert got is not ro and got.flags.writeable and got.dtype == np.float64 and np.array_equal(got, ro)
    f4 = np.array([0.1, 0.5], dtype=np.float32)
    got = cp._w(f4)
    assert got.dtype == np.float64 and np.array_equal(got, f4.astype(np.float64))  # exactly the f4 value, widened
    strided = np.arange(10.0)[::2]
    assert cp._w(strided).flags.c_contiguous
    p = np.arange(3, dtype=np.uint64)
    p.setflags(write=False)
    assert cp._w(p, np.uint64).dtype == np.uint64 and cp._w(p, np.uint64).flags.writeable
    assert cp._w(np.empty(0)).shape == (0,)


# --- time: cold and from the cache -----------------------------------------------------------------------------------

SCRIPT = """
import json, time
t00 = time.perf_counter()
from bf_core.resources.warm import warm
took = warm()
print(json.dumps({"total": time.perf_counter() - t00, "took": took}))
"""


def _fresh(cache: Path) -> dict[str, Any]:
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
    assert tuple(cold["took"]) == rw.PARTS
    assert cold["total"] <= COLD_LIMIT_S
    assert cached["total"] <= CACHE_LIMIT_S
    for part in ("prepare", "sums", "bench"):
        assert cached["took"][part] < cold["took"][part]  # loaded from the cache of numba, not compiled again
        assert cached["took"][part] < 1.5


# --- the imports -----------------------------------------------------------------------------------------------------


def test_importing_the_warm_up_does_not_pull_numba() -> None:
    heavy = ("numba", "numpy", "pandas", "pyarrow", "duckdb", "scipy")
    code = ("import sys, importlib; importlib.import_module('bf_core.resources.warm');"
            f"print(','.join(m for m in {heavy!r} if m in sys.modules))")  # fmt: skip
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported: {r.stdout.strip()}"
