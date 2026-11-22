"""The benchmarks of the variography (ADR 0022 «Замеры», подшаг 16.28) on small volumes: what their references are
made of, and the scenarios `variogram_axes`, `variogram_map`, `variogram_fit`, `variogram_preview`,
`stats_tab_vario` from `prepare` to the numbers they report (`variogram_compile` compiles every kernel twice — it
is run by `bench.run` only; here its operations on the set of the warm-up).

References and tolerances:
  * the axes and along the holes — every pair on NumPy (`tests/ref_bf_variogram`, tested in `test_vario_pairs`
    and `test_vario_downhole`); the scenario holds the kernels to 1e−12 on a sample of its points, N exactly;
  * the map — `variogram_map._brute_map`, every ordered pair on NumPy written apart from the kernel: the same N
    and γ (1e−12) as `test_vario_fan_map.brute_map` (another hand) and the kernel, in a turned plane and a thin
    layer;
  * the fit — `variogram_fit.on_model` puts γ of a model at h̄ of the lags of a form (1e−15), `restored` is 0 for
    the model itself and sees a change of one range;
  * the scenarios — the limits the budgets give them: one hash with 1, 2, 4 and 8 threads, the map symmetric bit
    for bit, the model restored to 1e−4, the preview over a sample of 20 000 points, the tab against BF 1e−12.
"""

from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.scenarios import _samples  # noqa: E402
from bench.scenarios import variogram_compile as vc  # noqa: E402
from bench.scenarios import variogram_fit as vfit  # noqa: E402
from bench.scenarios import variogram_map as vmap  # noqa: E402

from bf_core.geostat.variogram import fan, varmap  # noqa: E402
from bf_core.geostat.variogram.model import Model, Structure  # noqa: E402
from tests.test_vario_fan_map import brute_map  # noqa: E402

SMALL = {"holes": 60, "per_hole": 100}


def run(name: str, params: dict[str, Any], work: Path) -> dict[str, Any]:
    """A scenario as `bench.run` runs it, in this process."""
    mod = importlib.import_module(f"bench.scenarios.{name}")
    mod.prepare(params, work)
    mod.load()
    info: dict[str, Any] = mod.run(params, work)
    json.dumps(info)  # what `bench.run` prints
    return info


# --- the references ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("plane", "thickness"), [((0.0, 0.0, 0.0), math.inf), ((30.0, -40.0, 10.0), 6.0)])
def test_brute_map_is_every_ordered_pair_and_the_kernel(plane: tuple[float, float, float], thickness: float) -> None:
    r = np.random.default_rng(3)
    n = 500
    x, y, z = r.uniform(-120, 120, n), r.uniform(-90, 90, n), r.uniform(-30, 30, n)
    v = np.exp(r.normal(0.0, 0.8, n) + np.sin(x / 30.0))
    frame = fan.plane_frame(plane)
    every = brute_map(x, y, z, v, 10.0, frame, nu=6, nv=5, thickness=thickness)[0]
    few = int(np.median(every))  # a half of the cells under `min_pairs`
    m = varmap.varmap(x, y, z, v, 10.0, frame=frame, nu=6, nv=5, thickness=thickness, min_pairs=few)
    n_mine, g_mine = vmap._brute_map(x, y, z, v, m)
    n_other, g_other = brute_map(x, y, z, v, 10.0, frame, nu=6, nv=5, thickness=thickness, min_pairs=few)
    assert np.array_equal(n_mine, n_other) and np.array_equal(n_mine, m.n)
    assert np.isnan(g_mine).any() and np.array_equal(np.isnan(g_mine), np.isnan(m.gamma))  # few pairs — no value
    assert vmap.axes._error(g_mine, g_other, "other") <= 1e-12 and vmap.axes._error(m.gamma, g_mine, "kernel") <= 1e-12


def test_the_model_under_the_points_and_its_restoration() -> None:
    sigma2 = 2.5
    truth = vfit.model(vfit.TRUTH, sigma2)
    assert truth["c0"] + sum(s["c"] for s in truth["structures"]) == pytest.approx(sigma2)  # the sill σ²
    m = Model(truth["c0"], tuple(Structure(**s) for s in truth["structures"]), True)
    h = [None, 12.5, 31.0, None, 250.0]
    lags = {"az": 30.0, "plunge": 10.0, "h": h, "gamma": [0.0] * 5, "n": [0, 5, 9, 0, 3]}
    form = {"sigma2": sigma2, "directions": [lags]}
    got = vfit.on_model(form, truth)["directions"][0]
    assert got["n"] == [0, 5, 9, 0, 3] and got["gamma"][0] is None and got["gamma"][3] is None
    want = m.gamma_along(np.array([12.5, 31.0, 250.0]), 30.0, 10.0)
    assert np.allclose([got["gamma"][k] for k in (1, 2, 4)], want, rtol=1e-15, atol=0.0)
    assert vfit.restored(truth, truth, sigma2) == 0.0
    moved = {**truth, "structures": [*truth["structures"][:2], {**truth["structures"][2], "a2": 201.0}]}
    assert vfit.restored(moved, truth, sigma2) == pytest.approx(1.0 / 200.0)
    turned = {**truth, "structures": [{**truth["structures"][0], "ang1": 31.0}, *truth["structures"][1:]]}
    with pytest.raises(AssertionError):
        vfit.restored(turned, truth, sigma2)


# --- the scenarios -----------------------------------------------------------------------------------------


def test_axes_scenario_checks_the_job_against_every_pair(tmp_path: Path) -> None:
    info = run("variogram_axes", {**SMALL, "brute": 800}, tmp_path)
    assert info["brute_error"] <= 1e-12 and info["dh_brute_error"] <= 1e-12 and info["hashes"] == 1
    assert info["brute_pairs"] > 0 and info["dh_brute_pairs"] > 0 and info["dh_brute_points"] >= 800
    assert info["points"] + info["missing"] + info["no_position"] == info["composites"] > info["rows"] // 2 - 10
    assert info["no_position"] == 50 and info["missing"] > 0  # the hole without a collar; no grade — never 0
    assert info["sampled_n"] == info["points"] and info["checks"] == info["estimate"] > 0  # no sample
    assert info["axes_kernel_s"] > 0.0 and info["axes_ns_check"] > 0.0 and info["axes_mb"] > 0.0
    assert all(f"kernel_t{n}_s" in info for n in (1, 2, 4, 8)) and info["repeat_s"] < info["axes_s"]


def test_map_scenario_is_symmetric_and_equals_every_pair(tmp_path: Path) -> None:
    info = run("variogram_map", {**SMALL, "brute": 800}, tmp_path)
    assert info["asymmetric"] == 0 and info["centre_n"] >= info["points"] and info["map_pairs"] > 0
    assert info["map_brute_error"] <= 1e-12 and info["fan_brute_error"] <= 1e-12 and info["fan_brute_pairs"] > 0
    assert info["rose_rays"] == 12 and info["fan_checks"] + info["map_checks"] == info["estimate"]
    assert info["map_fan_kernel_s"] > 0.0 and info["map_grid_kernel_s"] > 0.0


def test_map_scenario_in_a_turned_plane(tmp_path: Path) -> None:
    info = run("variogram_map", {**SMALL, "brute": 500, "plane": {"ang1": 30.0, "ang2": -40.0, "ang3": 10.0}}, tmp_path)
    assert info["asymmetric"] == 0 and info["map_brute_error"] <= 1e-12 and info["fan_brute_error"] is None


def test_fit_scenario_restores_the_model(tmp_path: Path) -> None:
    info = run("variogram_fit", SMALL, tmp_path)
    assert info["restored_error"] <= 1e-4 and info["residual_exact"] < 1e-12
    assert info["fit_s"] == max(info[f"fit_{rule}_s"] for rule in vfit.RULES) and info["points"] > 30


def test_preview_scenario_samples_20000_points(tmp_path: Path) -> None:
    info = run("variogram_preview", {"holes": 220, "per_hole": 200, "shown": 2}, tmp_path)
    assert info["sampled_n"] == 20_000 < info["of"] and info["checks"] > 0
    assert info["first_load_s"] > info["load_s"]  # then the columns of the process
    assert all(info[f"first_{k}_s"] >= 0.0 for k in ("load", "sample", "plan", "kernel", "dh"))


def test_stats_tab_vario_scenario_checks_the_answers_against_bf(tmp_path: Path) -> None:
    info = run("stats_tab_vario", {"holes": 60, "per_hole": 100, "fields": 3, "ref": 600}, tmp_path)
    assert info["ref_error"] <= 1e-12 and info["ref_points"] == 600 and 600 < info["points"] < info["rows"]
    assert info["slowest_s"] == max(info[f"{k}_s"] for k in ("omni", "four", "map", "local")) > 0.0
    assert info["tab_s"] >= info["slowest_s"] and info["omni_read_s"] > 0.0 and info["local_empty"] >= 0


def test_compile_scenario_runs_every_operation_of_the_stage() -> None:
    _samples.warm(vc.every, fields=3)
    steps = vc.before_step()
    assert steps[-1] == "stats" and "composites" in steps and vc.before_step(["stats", "view"]) == ["view", "stats"]
    with pytest.raises(ValueError, match="geostat"):
        vc.before_step(["geostat"])
