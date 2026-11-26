"""The warm-up of the kernels of the pit optimisation (подшаг 21.14; ТЗ 8.11.5: компиляция ≤ 30 с; ADR 0028 решение
28): `warm()` runs, gives seconds and a second call is cheap, a part that fails does not stop the others, the warm-up
leaves NO specialization for the job `pit.run` to compile (runs on models of several layouts — cubes, a turned model
with gaps in the grade, f4 columns in portions of a few rows, a sparse file under a terrain, sub-blocks, both solvers,
the check of the template — add no signature), one specialization per kernel of the entry, the first compilation is
within 30 s and the cache is fast, and importing the warm-up pulls no numba."""

# ruff: noqa: F811 - the fixture `pw` is imported, and the tests take it by its name
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

from bf_core.db import dialect
from bf_core.mine import job as pj
from bf_core.mine import store as st
from bf_core.mine import warm as mw
from tests.pitjob import SMALL, direct, terrain
from tests.pitworld import COUNTS, FIELDS, blocks, grid_of, make_model, new, params, pw  # noqa: F401 - the fixture
from tests.test_blocks_report import put

KERNEL_MODULES = (
    "bf_core.mine.econ_kernels",
    "bf_core.mine.template_kernels",
    "bf_core.mine.graph_kernels",
    "bf_core.mine.hpf_kernels",
    "bf_core.mine.report_kernels",
    "bf_core.geo.dz_kernels",
)
COLD_LIMIT_S = 30.0  # the target of the ТЗ (a CI runner may be slower: the real figure is ~15 s with the imports)
CACHE_LIMIT_S = 10.0
ENTRY = (  # the kernels the pass calls from Python: ONE signature each
    "econ_kernels.row_econ", "econ_kernels.control", "econ_kernels.terms", "econ_kernels.node_values",
    "econ_kernels.abs_sum", "econ_kernels.scale_round", "template_kernels.cone_grid",
    "template_kernels.build_offsets", "template_kernels.effective_angles", "template_kernels.phi_row",
    "template_kernels.phi_at", "graph_kernels.cone_kernel", "graph_kernels.closure_violations",
    "graph_kernels.degrees", "graph_kernels.fill_arcs", "hpf_kernels.init_state", "hpf_kernels.run",
    "hpf_kernels.extract", "report_kernels.accumulate", "report_kernels.cumulate",
    "report_kernels.cumulate_benches",
)  # fmt: skip


def dispatchers() -> Iterator[tuple[str, Any]]:
    for name in KERNEL_MODULES:
        for attr, obj in vars(import_module(name)).items():
            if hasattr(obj, "signatures") and hasattr(obj, "py_func"):
                yield f"{name.removeprefix('bf_core.').removeprefix('mine.')}.{attr}", obj


def signatures() -> dict[str, list[str]]:
    return {name: sorted(str(s) for s in d.signatures) for name, d in dispatchers()}


# --- warm() ---------------------------------------------------------------------------------------------------------


def test_warm_runs_and_a_second_call_is_safe_and_cheap() -> None:
    took = mw.warm()
    assert tuple(took) == mw.PARTS and all(isinstance(v, float) and v >= 0.0 for v in took.values())
    t0 = time.perf_counter()
    assert tuple(mw.warm()) == mw.PARTS
    assert time.perf_counter() - t0 < 5.0  # nothing compiled again
    sig = signatures()
    for name in ENTRY:
        assert len(sig[name]) == 1, (name, sig[name])  # ONE specialization of every kernel of the entry


def test_a_failing_part_is_skipped_and_the_next_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(mw.WARMS, "template", boom)
    monkeypatch.setitem(mw.WARMS, "econ", boom)
    assert tuple(mw.warm()) == tuple(p for p in mw.PARTS if p not in ("template", "econ"))


def test_parts_are_the_kernels_of_the_entry() -> None:
    assert tuple(mw.WARMS) == mw.PARTS


# --- no kernel is compiled again by a job ---------------------------------------------------------------------------

F4_FIELDS = [
    {"name": "AU", "dtype": "f4", "unit": "g/t", "role": "grade"},  # f4: a column of another dtype
    {"name": "SG", "dtype": "f4", "role": "density"},
    FIELDS[2],
]


def f4_model(c: Any) -> int:
    b = blocks(SMALL, seed=9)
    return put(c, grid_of(SMALL), F4_FIELDS, b["p"],
               {"AU": b["AU"].astype(np.float32), "SG": b["SG"].astype(np.float32), "ROCK": b["ROCK"]})  # fmt: skip


def sparse_model(c: Any, counts: tuple[int, int, int] = (10, 9, 8)) -> int:
    """A sparse file: the cells of the upper levels are not in it (a terrain makes them «воздух» or an empty waste)."""
    b = blocks(counts, seed=4)
    keep = (b["p"] // np.uint64(counts[0] * counts[1])) < 6
    sparse = {k: v[keep] for k, v in b.items()}
    cols = {k: v for k, v in sparse.items() if k != "p"}
    return put(c, grid_of(counts), FIELDS, sparse["p"], cols, name="Разрежена")


def test_the_warm_up_leaves_no_specialization_to_the_job(pw) -> None:  # type: ignore[no-untyped-def]
    mw.warm()
    before = signatures()
    assert before["hpf_kernels.run"] and before["report_kernels.accumulate"] and before["econ_kernels.row_econ"]

    def run(mid: int, **kw: Any) -> dict[str, Any]:
        sid = new(pw, mid, kw.pop("name", None), **kw.pop("over", {}))["id"]
        return direct(pw, sid, **kw)

    # cubes: a portion = the whole file; several threads
    out = run(make_model(pw), threads=4)
    assert out["shells"][-1]["blocks"] > 0
    # a turned model with gaps in the grade (the control counts them), portions of a few rows (joined, partial)
    turned = make_model(pw, SMALL, seed=5, rotation=30.0, gaps=0.1, name="Повёрнутая")
    assert run(turned, chunk=7, threads=3)["control"]["no_grade"]["AU"]["blocks"] > 0
    # f4 columns of the grade and the density, portions of 100 rows
    assert run(f4_model(pw), chunk=100)["shells"]
    # a sparse file under a terrain: the cells above are «воздух», the absent cells below are an empty waste
    sp = sparse_model(pw)
    assert run(sp, over={"terrain_id": terrain(pw, 60.0)}, chunk=64)["nodes"]["air"] > 0
    assert run(sp, name="Без рельефа", over={"prefix": "C_"})["nodes"]["graph"] > 0
    # the other solver: the arcs and HPF on them are not used by the first run; OR-Tools is
    assert run(make_model(pw, SMALL, seed=6), over={"prefix": "B_", "solver": "ortools"})["solver"] == "ortools"
    # sub-blocks: a node is a parent, the volume of a row is its own
    from tests.test_pit_econ import sub_model

    mid, _parts = sub_model(pw)
    body = {"model_id": mid, "elements": [{"field": "AU", "unit": "g/t", "price": 2000.0}], "density": "SG",
            "rocks": {"types": [{"name": "Все породы", "c_p": 20.0, "rec": {"AU": 0.9}}]},
            "mining": {"c_m0": 2.5}, "rf": [0.8, 1.2]}  # fmt: skip
    with dialect.write_tx(pw.project.engine) as conn:
        sub = st.create(conn, "Субблоки", body)["id"]
    assert direct(pw, sub)["nodes"]["graph"] == 4
    # the check of the template (the job `pit.check`)
    chk = pj.run_check(pw.project.engine, pw.project.folder, {"params": params(make_model(pw, SMALL, seed=7))})
    assert chk
    assert signatures() == before


def test_the_job_with_the_warm_up_is_no_different_from_the_job_without(pw) -> None:  # type: ignore[no-untyped-def]
    """The warm-up changes no bit: the result of a run is the same before and after `warm()` (the same kernels)."""
    mid = make_model(pw, SMALL, seed=5)
    a = direct(pw, new(pw, mid, "A", prefix="A_")["id"])
    mw.warm()
    b = direct(pw, new(pw, mid, "B", prefix="B_")["id"])
    assert a["shells"] == b["shells"] and a["q"] == b["q"] and a["totals"] == b["totals"]


# --- time: cold and from the cache -----------------------------------------------------------------------------------

SCRIPT = """
import json, time
t00 = time.perf_counter()
from bf_core.mine.warm import warm
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
    assert tuple(cold["took"]) == mw.PARTS
    assert cold["total"] <= COLD_LIMIT_S
    assert cached["total"] <= CACHE_LIMIT_S
    for part in ("template", "graph", "econ", "shells", "report"):
        assert cached["took"][part] < cold["took"][part]  # loaded from the cache of numba, not compiled again
        assert cached["took"][part] < 2.0


# --- the imports -----------------------------------------------------------------------------------------------------


def test_importing_the_warm_up_does_not_pull_numba() -> None:
    heavy = ("numba", "numpy", "pandas", "pyarrow", "duckdb", "scipy")
    code = ("import sys, importlib; importlib.import_module('bf_core.mine.warm');"
            f"print(','.join(m for m in {heavy!r} if m in sys.modules))")  # fmt: skip
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported: {r.stdout.strip()}"


def test_the_step_imports_what_a_request_needs() -> None:
    """After `warm()` the modules of a request are loaded: pyarrow (`store_data`), `report_view`, `plan`."""
    code = ("import sys; from bf_core.mine.warm import warm; warm();"
            "print(','.join(m for m in ('pyarrow', 'bf_core.mine.plan', 'bf_core.mine.report_view',"
            "'bf_core.mine.store_data') if m not in sys.modules))")  # fmt: skip
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"not imported: {r.stdout.strip()}"
