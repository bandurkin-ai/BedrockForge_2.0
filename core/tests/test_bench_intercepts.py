"""The benchmarks of the true thickness and of the ore intercepts (ADR 0021 «Замеры», подшаг 15.19) on small
volumes: what their references are made of against the formulas of ТЗ 8.5.1 and a full search, and the
scenarios `true_thickness`, `true_thickness_mesh`, `intercepts` from `prepare` to the numbers they report
(`intercept_compile` compiles every kernel twice — it is run by `bench.run` only).

References and tolerances:
  * the reference of TT (`true_thickness.sum_along`: numpy, the arrays of the generator) — on a straight hole
    l·|n·d| to 1e−12·l; on the curved holes of the generator |n·ΔP| of `Trajectories.positions` within the
    error of the parts of 1 m (ТЗ 8.5.1): ψ²/24 of a part on an arc, and where a station falls inside a part —
    the jump of the curvature there × 1 m² / 8, 5e−7 m on these holes (1e−6 m is asked); with parts of 0,1 m
    — a hundred times less; its normal is `formulas.plane_normal` to 1e−15;
  * the veins of `true_thickness_mesh` — closed plates with the normals outward, their volume the area of the
    faces × the thickness to 1e−9, the number of triangles asked for within 5 % (tens of thousands; 1 % from
    a million); the distance to them (a vein is a box) is that to the nearest triangle of a full search, 1e−9 m;
  * the scenarios — the limits the budgets give them: TT against the reference 1e−9 m (by wireframes 1e−6 m,
    Т8.5-13 on the veins 1e−6 m), one hash with 1, 2, 4 and 8 threads, the balance of Σ LEN_G 1e−9.
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

from bench.scenarios import true_thickness as tt  # noqa: E402
from bench.scenarios import true_thickness_mesh as mesh  # noqa: E402

from bf_core.drillholes.desurvey import Collars, Surveys, desurvey  # noqa: E402
from bf_core.geo.meshes import mesh_stats  # noqa: E402
from bf_core.intercepts import formulas as fm  # noqa: E402
from tests.test_geo_nearest import ref_distances  # noqa: E402

SMALL = {"holes": 240, "per_hole": 100, "fields": 10, "shown": 5}


def run(name: str, params: dict[str, Any], work: Path) -> dict[str, Any]:
    """A scenario as `bench.run` runs it, in this process."""
    mod = importlib.import_module(f"bench.scenarios.{name}")
    mod.prepare(params, work)
    mod.load()
    info: dict[str, Any] = mod.run(params, work)
    json.dumps(info)  # what `bench.run` prints
    return info


# --- the references ----------------------------------------------------------------------------------------


def test_reference_normal_and_straight_holes_are_the_formulas_of_the_tz() -> None:
    rng = np.random.default_rng(5)
    alpha, dipdir = rng.uniform(0.0, 90.0, 200), rng.uniform(0.0, 360.0, 200)
    mine = np.array([tt.plane_normal(a, b) for a, b in zip(alpha, dipdir, strict=True)])
    assert np.abs(mine - fm.plane_normal(alpha, dipdir)).max() <= 1e-15
    # Т8.5-1: a vertical hole and a plane that dips 30° — TT = l·cos 30°; straight inclined holes — l·|n·d|
    dirs = np.zeros((3, 2, 3))
    dirs[0] = (0.0, 0.0, -1.0)
    dirs[1] = fm.hole_direction(40.0, 75.0)
    dirs[2] = fm.hole_direction(100.0, 300.0)  # goes up
    hole, frm, to = np.array([0, 1, 2, 1]), np.array([3.0, 10.25, 0.0, 49.5]), np.array([13.0, 12.75, 7.3, 50.5])
    n = tt.plane_normal(30.0, 120.0)
    got = tt.sum_along(dirs, np.zeros(3, dtype=bool), hole, frm, to, n)
    assert got[0] == pytest.approx(10.0 * math.cos(math.radians(30.0)), abs=1e-12)
    assert np.abs(got - (to - frm) * np.abs(dirs[hole, 0] @ n)).max() <= 1e-12
    # no trajectory, TO ≤ FROM, no normal — NaN, never a zero
    lost = tt.sum_along(dirs, np.array([False, True, False]), hole, frm, to, n)
    assert np.isnan(lost[[1, 3]]).all() and not np.isnan(lost[[0, 2]]).any()
    assert np.isnan(tt.sum_along(dirs, np.zeros(3, dtype=bool), [0, 0], [5.0, 5.0], [5.0, 4.0], n)).all()
    assert np.isnan(tt.sum_along(dirs, np.zeros(3, dtype=bool), [0], [5.0], [6.0], [np.nan, 0.0, 1.0])).all()


def test_reference_on_the_curved_holes_of_the_generator_is_the_path_of_positions() -> None:
    d = tt.generated({"holes": 120, "per_hole": 100})
    with_path = np.flatnonzero(~np.isnan(d["x"]))
    sv = Surveys(d["s_hole"], d["s_depth"], d["s_az"], d["s_dip"])
    traj = desurvey(Collars(np.arange(120), d["x"], d["y"], d["z"], d["td"]), sv, n_holes=120)
    rng = np.random.default_rng(6)
    hole = rng.choice(with_path, 4000)
    frm = rng.uniform(0.0, 95.0, 4000)
    to = frm + rng.uniform(0.2, 5.0, 4000)  # through the stations at 50 m and to the bottom at 100 m
    n = tt.plane_normal(75.0, 200.0)
    got = tt.sum_along(tt.station_dirs(d), np.isnan(d["x"]), hole, frm, to, n)
    edges = np.linspace(0.0, 1.0, 6)  # a part of ≤ 1 m: n·d keeps its sign within it or the row is left out
    at = frm[:, None] + (to - frm)[:, None] * edges
    along = (traj.positions(np.repeat(hole, 6), at.ravel()) @ n).reshape(-1, 6)
    step = np.diff(along, axis=1)
    one_way = (step > 0.0).all(axis=1) | (step < 0.0).all(axis=1)
    assert one_way.mean() > 0.9
    exact = np.abs(along[:, -1] - along[:, 0])
    assert np.abs(got - exact)[one_way].max() <= 1e-6
    fine = tt.sum_along(tt.station_dirs(d), np.isnan(d["x"]), hole, frm, to, n, step=0.1)
    assert np.abs(fine - exact)[one_way].max() <= 1e-8
    assert np.isnan(tt.sum_along(tt.station_dirs(d), np.isnan(d["x"]), [5], [1.0], [2.0], n)).all()  # no collar


def test_veins_are_closed_plates_and_the_distance_to_them_is_that_of_a_full_search() -> None:
    holes, asked = 300, 40_000
    made, fr = mesh.plan(holes), mesh.frame(holes)
    bodies = mesh.veins(holes, asked, 4)
    assert len(bodies) == 4 and len(made) >= 4
    total = sum(len(b["tri"]) for b in bodies)
    assert abs(total - asked) <= 0.05 * asked  # a row of cells more or less on every face
    area = sum((p["u1"] - p["u0"]) * (p["v1"] - p["v0"]) for p in made)
    volume = 0.0
    for b in bodies:
        stats = mesh_stats(b["xyz"], b["tri"])
        assert stats["closed"] and stats["orientation"] == "outward" and stats["degenerate"] == 0, stats
        assert b["xyz"].dtype == np.float64 and b["tri"].dtype == np.int32
        volume += stats["volume"]
    assert volume == pytest.approx(area * mesh.THICK, rel=1e-9)
    # points around the field, inside the veins, between them and beyond the zone
    rng = np.random.default_rng(7)
    points = fr["c0"] + np.column_stack([rng.uniform(-1.3, 1.3, (150, 2)) * fr["half"], rng.uniform(-110, 110, 150)])
    xyz = np.concatenate([b["xyz"] for b in bodies])
    first = np.cumsum([0] + [len(b["xyz"]) for b in bodies])
    tri = np.concatenate([b["tri"] + first[k] for k, b in enumerate(bodies)])
    want = ref_distances(points, xyz, tri).min(axis=1)
    got = mesh.distance(made, *mesh.local(fr, points))
    near = want < 2.0 * mesh.GAP  # farther the distance is not looked at: it is beyond any d_max
    assert near.sum() > 100 and (got[~near] > mesh.D_MAX).all()
    assert np.abs(got - want)[near].max() <= 1e-9
    assert (want < 0.5 * mesh.THICK).any() and (want[near] > mesh.D_MAX).any()  # inside a vein; beyond d_max


# --- the scenarios -----------------------------------------------------------------------------------------


def test_true_thickness_scenario_checks_both_jobs_against_the_reference(tmp_path: Path) -> None:
    info = run("true_thickness", SMALL, tmp_path)
    assert info["tt_error_m"] <= 1e-9 and info["hashes"] == 1 and info["rows"] == 24_000
    assert info["global_empty"] == info["domain_empty"] == 200  # the two holes without a collar
    assert info["domain_along"] > 0 and info["tt_s"] == max(info["global_s"], info["domain_s"])
    assert all(info[f"{j}_{k}_s"] >= 0.0 for j in tt.JOBS for k in ("read", "traj", "hash", "kernel", "write"))
    assert 0.0 < info["preview_s"] <= info["preview_max_s"]


def test_true_thickness_mesh_scenario_checks_the_veins_and_t8_5_13(tmp_path: Path) -> None:
    info = run("true_thickness_mesh", {**SMALL, "triangles": 20_000, "bodies": 4}, tmp_path)
    assert info["tt_error_m"] <= 1e-6 and info["plate_error_m"] <= 1e-6 and info["hashes"] == 1
    assert info["missed"] == info["extra"] == info["odd"] == 0 and info["plate_rows"] > 100
    assert abs(info["triangles"] - 20_000) <= 2000 and info["wireframes"] == 4
    assert info["share_crossing"] > 0.0 and info["share_nearest"] > 0.5 and info["share_none"] > 0.0
    assert info["share_crossing"] + info["share_nearest"] + info["share_none"] == pytest.approx(1.0, abs=2e-4)
    assert info["farthest_m"] <= mesh.D_MAX and info["nearest_us"] > 0.0


def test_intercepts_scenario_balances_the_table_with_the_generator(tmp_path: Path) -> None:
    info = run("intercepts", SMALL, tmp_path)
    assert max(info[k] for k in ("balance_len_g", "balance_len", "balance_miss", "grade_error")) <= 1e-9
    assert info["tt_error_m"] <= 1e-9 and info["hashes"] == 1
    assert info["icpt_rows"] == info["icpt_main"] + info["icpt_included"] and info["icpt_included"] > 0
    assert info["no_tt"] > 0 and info["icpt_rule_s"] >= 0.0 and info["icpt_tt_s"] >= 0.0
