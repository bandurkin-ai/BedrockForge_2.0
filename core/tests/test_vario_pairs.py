"""Pair kernel of the experimental variogram (Э16, 16.2; ADR 0022 §3–4).

Т8.6-1 — the data of BF `tests/test_stats_variogram.py` (z = 0, dip tolerance 90°) against BF
`experimental_variogram`: γ 1e−12, N exactly. Т8.6-2 — 500 points in 3D against every pair on NumPy
(`tests/ref_bf_variogram.brute_force`): N exactly; γ, 1 − ρ, pairwise relative 1e−12 — for every grid `m`, so the
mask of cell offsets changes nothing. Д-21 — `gamv` of GeostatsPy: the vectors of `tests/data/ref/geostatspy` and
`tests/data/variography/gamv_geostatspy.json` (eight 2D cases on 300 points, three 3D horizontal ones; the
throw-away generator is kept in the file, `generator_source`): 1e−9, N exactly. One hash with 1, 2, 4 and 8
threads. Hard data: far coordinates, duplicates, a lattice with pairs on the edges of the lags and of the
tolerances, one vertical hole, a pair closer than 1e−20 across, missing values. The axes of R as directions and
the JSON of the answer.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from bf_core.geostat import rotation
from bf_core.geostat.variogram import experimental as ex
from bf_core.geostat.variogram import pairs
from bf_core.geostat.variogram.experimental import Experimental, experimental
from bf_core.geostat.variogram.pairs import Direction
from bf_core.stats.weighted import StatsError
from tests import ref_bf_variogram as ref

REF = Path(__file__).resolve().parents[2] / "tests" / "data" / "ref" / "geostatspy" / "geostatspy_reference.json"
GAMV = Path(__file__).parent / "data" / "variography" / "gamv_geostatspy.json"
INF = math.inf

# the data of BF tests/test_stats_variogram.py: the same generator, the same order of draws
_rng = np.random.default_rng(11)
N = 250
XY = _rng.uniform(0, 100, (N, 2))
V = np.sin(XY[:, 0] / 15) + 0.5 * np.cos(XY[:, 1] / 25) + _rng.normal(0, 0.2, N)

DIRS = [  # az, plunge, az_tol, dip_tol, band_h, band_v
    (0.0, 0.0, 22.5, 22.5, 15.0, 10.0),
    (90.0, 0.0, 22.5, 22.5, 15.0, 10.0),
    (0.0, 90.0, 22.5, 22.5, 15.0, 10.0),  # straight down
    (37.0, 25.0, 30.0, 20.0, 12.0, 8.0),
    (200.0, -40.0, 15.0, 35.0, INF, 6.0),
    (0.0, 0.0, 90.0, 90.0, INF, INF),  # omnidirectional
    (120.0, 0.0, 90.0, 15.0, INF, 5.0),  # any azimuth, near the horizontal
    (300.0, 60.0, 45.0, 90.0, 9.0, INF),
    (45.0, -90.0, 22.5, 10.0, 5.0, 5.0),  # straight up
    (180.0, 89.0, 5.0, 5.0, 2.0, 2.0),  # next to the vertical: the azimuth still counts
]


def close(a, b, tol=1e-12):
    """Equal within `tol` (relative to the size of the number, absolute below 1); NaN only where NaN."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    assert a.shape == b.shape
    assert np.array_equal(np.isnan(a), np.isnan(b))
    ok = ~np.isnan(b)
    err = np.abs(a[ok] - b[ok]) / np.maximum(1.0, np.abs(b[ok]))
    assert err.size == 0 or float(err.max()) <= tol, float(err.max())


def against_brute(x, y, z, v, dirs, lag, nlag, tol=None, **kw) -> Experimental:
    e = experimental(x, y, z, v, [Direction(*d) for d in dirs], lag, nlag, tol, **kw)
    zz = np.zeros_like(np.asarray(x, dtype=float)) if z is None else z
    want = ref.brute_force(x, y, zz, v, dirs, lag, nlag, tol)
    for k, w in enumerate(want):
        assert np.array_equal(e.n[k], w["n"]), (k, e.n[k], w["n"])
        assert np.array_equal(e.n_rel[k], w["n_rel"])
        close(e.h[k], w["h"])
        close(e.gamma[k], w["gamma"])
        close(e.rho[k], w["rho"])
        close(e.one_minus_rho[k], 1.0 - w["rho"])
        close(e.pairwise[k], w["pairwise"])
        close(e.tail[k], w["tail"])
        close(e.head[k], w["head"])
    return e


def cloud(n=500, seed=5):
    r = np.random.default_rng(seed)
    x, y, z = r.uniform(0, 100, n), r.uniform(0, 80, n), r.uniform(0, 40, n)
    v = np.exp(r.normal(0, 1, n) + np.sin(x / 20)) - 0.6  # skewed, some values below zero
    return x, y, z, v


# ---- Т8.6-1: BF ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("azm, atol, bw", [(0.0, 22.5, 1e5), (45.0, 20.0, 15.0), (0.0, 90.0, 1e5), (135.0, 30.0, 8.0)])
@pytest.mark.parametrize("lag, nlag, tol", [(8.0, 6, None), (8.0, 40, None), (5.0, 12, 5.0), (7.5, 9, 1.0)])
def test_t861_equals_bf_experimental_variogram(azm, atol, bw, lag, nlag, tol):
    d, g, n = ref.experimental_variogram(XY, V, lag, nlag, lag_tol=tol, azimuth=azm, azimuth_tol=atol, bandwidth=bw)
    e = experimental(XY[:, 0], XY[:, 1], None, V, [Direction(azm, 0.0, atol, 90.0, bw, INF)], lag, nlag, tol)
    assert n.sum() > 100
    assert np.array_equal(e.n[0], n)  # N exactly
    close(e.gamma[0], g)  # γ 1e−12; a lag without pairs is NaN in both
    close(e.h[0], d)


def test_t861_all_directions_in_one_pass_and_standardize():
    dirs = [Direction(a, 0.0, 22.5, 90.0, 20.0, INF) for a in (0.0, 45.0, 90.0, 135.0)]
    e = experimental(XY[:, 0], XY[:, 1], np.zeros(N), V, dirs, 8.0, 6)
    for k, d in enumerate(dirs):
        dist, g, n = ref.experimental_variogram(XY, V, 8.0, 6, azimuth=d.az, azimuth_tol=22.5, bandwidth=20.0)
        assert np.array_equal(e.n[k], n)
        close(e.gamma[k], g)
        _, gs, _ = ref.experimental_variogram(XY, V, 8.0, 6, azimuth=d.az, bandwidth=20.0, standardize=True)
        close(e.gamma[k] / V.var(), gs)


def test_unordered_pair_count_omni_as_bf():
    e = experimental(XY[:, 0], XY[:, 1], None, V, [Direction(0.0, 0.0, 90.0, 90.0)], 10.0, 5)
    h = np.hypot(*(XY[:, None] - XY[None]).transpose(2, 0, 1))
    iu = np.triu_indices(N, 1)
    assert e.n[0].tolist() == [int((np.abs(h[iu] - 10.0 * k) <= 5.0).sum()) for k in range(1, 6)]


# ---- Т8.6-2: every pair on NumPy -----------------------------------------------------------------------------
@pytest.mark.parametrize("m", [1, 2, 3, 4, 6])
def test_t862_500_points_3d_all_types_any_grid(m):
    x, y, z, v = cloud()
    e = against_brute(x, y, z, v, DIRS, 5.0, 12, cells=m)
    assert e.cells == m and e.n.sum() > 50_000
    assert (e.n_rel < e.n).any()  # pairs with z_i + z_j ≤ 0 are left out of the pairwise relative and counted


@pytest.mark.parametrize("lag, nlag, tol", [(5.0, 12, 5.0), (4.0, 10, 0.8), (9.0, 3, None), (2.0, 100, None)])
def test_t862_lag_windows(lag, nlag, tol):
    x, y, z, v = cloud(seed=6)
    against_brute(x, y, z, v, DIRS[:6], lag, nlag, tol)


def test_t862_random_directions_keep_the_mask_honest():
    r = np.random.default_rng(77)
    x, y, z, v = cloud(400, seed=8)
    for m in (2, 4, 6, 8):
        dirs = [
            (
                float(r.uniform(0, 360)),
                float(r.uniform(-90, 90)),
                float(r.uniform(3, 90)),
                float(r.uniform(3, 90)),
                float(r.choice([2.0, 7.0, 20.0, INF])),
                float(r.choice([2.0, 7.0, 20.0, INF])),
            )
            for _ in range(12)
        ]
        against_brute(x, y, z, v, dirs, 6.0, 9, cells=m)


# ---- Д-21: GeostatsPy gamv -----------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["az0", "az90", "omni", "az45_bw150"])
def test_d21_gamv_of_geostatspy(case):
    data = json.loads(REF.read_text(encoding="utf-8"))
    x, y, v = (np.array(data["data"][k]) for k in ("X", "Y", "V"))
    g = data["gamv"][case]
    p = g["params"]
    # index il of the reference is centred on (il − 1)·xlag: its 2 … nlag are our lags 1 … nlag − 1
    nlag = int(p["nlag"]) - 1
    d = Direction(p["azm"], 0.0, p["atol"], 90.0, p["bandwh"], INF)
    e = experimental(x, y, None, v, [d], p["xlag"], nlag, p["xltol"])
    twice = 4 if p["atol"] >= 90.0 else 2  # the reference counts a pair both ways, an omnidirectional one — again
    want_n = np.array(g["npairs"][2 : nlag + 2]) / twice
    assert want_n.sum() > 300
    assert np.array_equal(e.n[0], want_n.astype(np.int64)) and np.array_equal(want_n, np.floor(want_n))
    close(e.gamma[0], g["gamma"][2 : nlag + 2], 1e-9)
    close(e.h[0], g["lag"][2 : nlag + 2], 1e-9)


@pytest.mark.parametrize("case", [c["id"] for c in json.loads(GAMV.read_text(encoding="utf-8"))["cases"]])
def test_d21_gamv_cases_2d_and_3d(case):
    data = json.loads(GAMV.read_text(encoding="utf-8"))
    assert data["reproduces_d21_gamv"] is True
    g = next(c for c in data["cases"] if c["id"] == case)
    pts = data[g["data"]]
    x, y, v = (np.array(pts[k]) for k in ("X", "Y", "V"))
    z = np.array(pts["Z"]) if "Z" in pts else None
    p = g["params"]
    nlag = int(p["nlag"]) - 1  # as above: index il of the reference is centred on (il − 1)·xlag
    d = Direction(p["azm"], 0.0, p["atol"], 90.0, p["bandwh"], INF)
    e = experimental(x, y, z, v, [d], p["xlag"], nlag, p["xltol"])
    twice = 4 if p["atol"] >= 90.0 else 2
    want_n = np.array(g["npairs"][2 : nlag + 2]) / twice
    assert np.array_equal(want_n, np.floor(want_n)) and want_n.sum() > 1000
    assert np.array_equal(e.n[0], want_n.astype(np.int64))
    close(e.gamma[0], g["gamma"][2 : nlag + 2], 1e-9)
    close(e.h[0], g["lag"][2 : nlag + 2], 1e-9)
    if z is not None:  # the 3D distance takes pairs out of the window that the plan would keep
        flat = experimental(x, y, None, v, [d], p["xlag"], nlag, p["xltol"])
        assert not np.array_equal(flat.n[0], e.n[0])


# ---- threads -------------------------------------------------------------------------------------------------
def digest(e: Experimental) -> str:
    s = e.sums
    return hashlib.sha256(s.n.tobytes() + s.n_rel.tobytes() + s.sums.tobytes()).hexdigest()


def test_one_hash_with_1_2_4_8_threads(monkeypatch):
    r = np.random.default_rng(3)
    n = 6000
    x, y, z = r.uniform(0, 300, n), r.uniform(0, 300, n), r.uniform(0, 60, n)
    v = r.lognormal(0, 1, n)
    dirs = [Direction(*d) for d in DIRS[:6]]
    monkeypatch.setattr(pairs, "STRIP_CHECKS", 20_000)  # many strips on a small set
    runs = [experimental(x, y, z, v, dirs, 6.0, 15, threads=t) for t in (1, 2, 4, 8)]
    assert runs[0].strips >= 100 and runs[0].n.sum() > 1_000_000
    assert len({digest(e) for e in runs}) == 1
    monkeypatch.undo()
    few = experimental(x, y, z, v, dirs, 6.0, 15, threads=4)  # other strips: the same counts, sums within 1e−12
    assert few.strips < runs[0].strips
    assert np.array_equal(few.n, runs[0].n)
    close(few.gamma, runs[0].gamma)
    assert digest(few) == digest(experimental(x, y, z, v, dirs, 6.0, 15, threads=1))


def test_strips_do_not_depend_on_threads_and_cover_the_checks():
    x, y, z, v = cloud(3000, seed=9)
    a = experimental(x, y, z, v, [Direction(*DIRS[5])], 5.0, 10, threads=1)
    b = experimental(x, y, z, v, [Direction(*DIRS[5])], 5.0, 10, threads=8)
    assert (a.strips, a.checks, a.cells) == (b.strips, b.checks, b.cells)
    assert a.checks >= a.n.sum() / 2  # a check may fill more than one lag; never fewer checks than pairs in a lag
    assert a.checks <= 3000 * 2999 // 2


# ---- hard data -----------------------------------------------------------------------------------------------
def test_far_coordinates_and_shifted_values():
    x, y, z, v = cloud(seed=12)
    against_brute(x + 512_345.678, y + 7_123_456.789, z - 250.5, v + 1000.0, DIRS, 5.0, 12)


def test_lattice_pairs_on_the_edges_of_lags_and_tolerances():
    gx, gy, gz = np.meshgrid(np.arange(9) * 3.0, np.arange(8) * 4.0, np.arange(4) * 5.0, indexing="ij")
    x, y, z = gx.ravel(), gy.ravel(), gz.ravel()
    v = np.random.default_rng(1).normal(5, 2, x.size)
    dirs = [
        (0.0, 0.0, 45.0, 45.0, 6.0, 5.0),  # pairs exactly on both cones and both bands
        (90.0, 0.0, 22.5, 22.5, 3.0, 5.0),
        (0.0, 90.0, 22.5, 45.0, INF, 3.0),
        (0.0, 0.0, 90.0, 90.0, INF, INF),
        (36.86989764584402, 0.0, 10.0, 90.0, 4.0, INF),  # along (3; 4)
    ]
    for m in (1, 3, 6):
        e = against_brute(x, y, z, v, dirs, 5.0, 6, cells=m)  # h = 5, 10, 15 … sit on the centres; 7,5 would be an edge
        e2 = against_brute(x, y, z, v, dirs, 10.0, 3, 5.0, cells=m)  # h = 5 and 15: on the edges, in both lags (as BF)
        assert e.n.sum() > 0 and e2.n[3].sum() > e.n[3][1::2].sum()


def test_duplicates_enter_only_with_a_tolerance_of_a_whole_lag():
    x, y, z, v = cloud(200, seed=2)
    x, y, z = np.r_[x, x[:50]], np.r_[y, y[:50]], np.r_[z, z[:50]]  # 50 pairs with h = 0
    v = np.r_[v, v[:50] + 0.5]
    dirs = [DIRS[0], DIRS[2], DIRS[5]]
    a = against_brute(x, y, z, v, dirs, 6.0, 5)
    b = against_brute(x, y, z, v, dirs, 6.0, 5, 6.0)  # the window of the first lag starts at 0
    assert (b.n[:, 0] - a.n[:, 0] >= 50).all()  # h = 0 passes every direction: it is not checked for the angles


def test_one_vertical_hole_and_plane_data():
    z = -np.arange(0.0, 120.0, 2.0)
    v = np.random.default_rng(4).lognormal(0, 0.5, z.size)
    x, y = np.full(z.size, 1000.0), np.full(z.size, 2000.0)
    e = against_brute(x, y, z, v, [DIRS[0], DIRS[2], DIRS[5]], 2.0, 10)
    assert e.n[0].sum() == 0 and np.isnan(e.gamma[0]).all() and np.isnan(e.h[0]).all()  # no pairs — empty, not 0
    assert np.array_equal(e.n[1], e.n[2]) and e.n[1][0] == z.size - 1
    x2, y2, _, v2 = cloud(300, seed=3)
    against_brute(x2, y2, None, v2, [DIRS[0], DIRS[3], DIRS[5]], 5.0, 8)


def test_missing_rows_take_no_part_and_are_counted():
    x, y, z, v = cloud(300, seed=14)
    keep = np.ones(300, dtype=bool)
    keep[[3, 50, 51, 200, 299]] = False
    xm, vm, zm = x.copy(), v.copy(), z.copy()
    xm[[3, 50]] = np.nan
    vm[[51, 200]] = np.nan
    zm[299] = np.inf
    d = [Direction(*DIRS[5]), Direction(*DIRS[0])]
    a = experimental(xm, y, zm, vm, d, 5.0, 8)
    b = experimental(x[keep], y[keep], z[keep], v[keep], d, 5.0, 8)
    assert (a.n_points, a.n_missing, b.n_missing) == (295, 5, 0)
    assert np.array_equal(a.n, b.n) and np.array_equal(a.sums.sums, b.sums.sums)
    assert not np.isnan(a.gamma[0]).any()


def test_few_pairs_flag_and_empty_lags():
    x, y, z, v = cloud(60, seed=15)
    e = experimental(x, y, z, v, [Direction(*DIRS[0])], 5.0, 40)
    few = e.few(30)
    assert few.shape == e.n.shape and np.array_equal(few, e.n < 30)  # a lag without pairs is «few» too
    few5 = e.few(5)
    assert few5.any() and (~few5).any() and few5[0, 30:].all()
    assert (e.n[0, 30:] == 0).all() and np.isnan(e.gamma[0, 30:]).all() and np.isnan(e.pairwise[0, 30:]).all()
    const = experimental(x, y, z, np.full(60, 2.5), [Direction(*DIRS[5])], 5.0, 5)
    assert (const.gamma[0] == 0.0).all() and np.isnan(const.rho[0]).all()  # σ = 0: no correlogram


# ---- parameters, progress ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kw, param",
    [
        ({"lag": 0.0}, "lag"),
        ({"lag": math.nan}, "lag"),
        ({"nlag": 0}, "nlag"),
        ({"tol": -1.0}, "lag_tol"),
        ({"tol": 5.1}, "lag_tol"),
        ({"dirs": [Direction(0.0, 0.0, 0.0)]}, "az_tol"),
        ({"dirs": [Direction(0.0, 0.0, 22.5, 91.0)]}, "dip_tol"),
        ({"dirs": [Direction(0.0, 95.0)]}, "plunge"),
        ({"dirs": [Direction(0.0, 0.0, 22.5, 22.5, 0.0)]}, "band_h"),
        ({"dirs": [Direction(0.0, 0.0, 22.5, 22.5, 1.0, -2.0)]}, "band_v"),
        ({"dirs": []}, "directions"),
        ({"cells": 0}, "cells"),
    ],
)
def test_bad_parameters_name_the_parameter(kw, param):
    x, y, z, v = cloud(50)
    args = {"dirs": [Direction()], "lag": 5.0, "nlag": 5, "tol": None, "cells": None} | kw
    with pytest.raises(StatsError) as err:
        experimental(x, y, z, v, args["dirs"], args["lag"], args["nlag"], args["tol"], cells=args["cells"])
    assert err.value.code == "bad_param" and err.value.param == param
    assert err.value.message and err.value.hint


def test_too_few_points_and_unequal_lengths():
    with pytest.raises(StatsError) as err:
        experimental([1.0, np.nan], [1.0, 2.0], None, [1.0, 2.0], [Direction()], 1.0, 3)
    assert err.value.code == "too_few_values"
    with pytest.raises(ValueError):
        experimental([1.0, 2.0, 3.0], [1.0, 2.0], None, [1.0, 2.0, 3.0], [Direction()], 1.0, 3)


def test_tick_reports_progress_and_cancels(monkeypatch):
    x, y, z, v = cloud(2000, seed=21)
    monkeypatch.setattr(pairs, "STRIP_CHECKS", 20_000)
    seen: list[float] = []
    e = experimental(x, y, z, v, [Direction(*DIRS[5])], 5.0, 10, threads=2, tick=seen.append)
    assert len(seen) == e.strips > 10 and seen == sorted(seen) and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    calls = []

    def stop(f: float) -> None:
        calls.append(f)
        if len(calls) == 3:
            raise Cancelled

    with pytest.raises(Cancelled):
        experimental(x, y, z, v, [Direction(*DIRS[5])], 5.0, 10, threads=2, tick=stop)
    assert len(calls) == 3


def test_direction_unit_vector_and_omni():
    d = Direction(90.0, 30.0)
    close(d.unit, (math.cos(math.radians(30.0)), 0.0, -0.5))
    assert Direction(0.0, 0.0).unit == (0.0, 1.0, -0.0) and Direction(0.0, 90.0).unit == (0.0, 0.0, -1.0)
    assert Direction(0.0, 0.0, 90.0, 90.0).omni and not Direction(0.0, 0.0, 90.0, 45.0).omni


def test_pairs_closer_than_1e_20_across_pass_the_cone():
    x = np.array([0.0, 1e-21, 0.0, 3.0, 7.0])  # point 1 is 1e−21 m east of point 0, 4 m above it
    y = np.array([0.0, 0.0, 9.0, 4.0, 1.0])
    z = np.array([0.0, 4.0, 2.0, 0.0, 5.0])
    v = np.array([1.0, 2.0, 4.0, 3.0, 5.0])
    dirs = [(0.0, 0.0, 10.0, 90.0, INF, INF), (0.0, 45.0, 5.0, 90.0, 1.0, INF), (90.0, 0.0, 22.5, 90.0, 0.5, INF)]
    e = against_brute(x, y, z, v, dirs, 4.0, 3)
    assert (e.n[:, 0] >= 1).all()  # GSLIB: d_xy < 1e−20 is not checked for the azimuth


# ---- the axes and the answer ---------------------------------------------------------------------------------
@pytest.mark.parametrize("angles", [(0.0, 0.0, 0.0), (30.0, -10.0, 0.0), (120.0, -35.0, 40.0), (275.0, 60.0, -150.0),
                                    (45.0, -90.0, 30.0), (360.0, 0.0, 180.0)])  # fmt: skip
def test_axes_are_the_rows_of_r(angles):
    dirs = ex.axes(*angles, 15.0, 20.0, 30.0, INF)
    r = rotation.rotation_matrix(*angles)
    a1, a2, a3 = rotation.normalize_angles(*angles)
    assert (dirs[0].az, dirs[0].plunge) == (a1, -a2 + 0.0)
    assert dirs[0].unit == tuple(r[0].tolist())  # the major axis bit for bit
    for d, u in zip(dirs, r, strict=True):
        close(d.unit, u)
        assert 0.0 <= d.az < 360.0 and -90.0 <= d.plunge <= 90.0
        assert (d.az_tol, d.dip_tol, d.band_h, d.band_v) == (15.0, 20.0, 30.0, INF)
    assert [(d.az, d.plunge) for d in ex.axes(0.0)] == [(0.0, 0.0), (270.0, 0.0), (0.0, -90.0)]


def test_to_json_is_the_form_of_the_answer():
    x, y, z, v = cloud(80, seed=16)
    v[:10] = -v[:10] - 5.0  # pairs with z_i + z_j ≤ 0
    e = experimental(x, y, z, v, ex.axes(30.0, -10.0, 0.0, 22.5, 22.5, 30.0, INF), 5.0, 30)
    out = e.to_json(min_pairs=3)
    assert [d["axis"] for d in out] == [1, 2, 3] and [d["axis"] for d in e.to_json(axis=False)] == [None] * 3
    d = out[0]
    keys = ["axis", "az", "plunge", "az_tol", "dip_tol", "band_h", "band_v", "lags", "h", "n", "gamma", "rho",
            "pairwise", "n_pw", "few"]  # fmt: skip
    assert list(d) == keys and (d["az"], d["plunge"], d["band_h"], d["band_v"]) == (30.0, 10.0, 30.0, "inf")
    assert d["lags"] == [5.0 * k for k in range(1, 31)] and json.loads(json.dumps(out)) == out  # plain JSON
    for k in range(3):
        n = e.n[k]
        assert out[k]["n"] == n.tolist() and out[k]["few"] == (n < 3).tolist()
        assert all((g is None) == (c == 0) for g, c in zip(out[k]["gamma"], n, strict=True))  # empty is null, not 0
        assert out[k]["gamma"] == [None if c == 0 else float(g) for g, c in zip(e.gamma[k], n, strict=True)]
    assert e.pw_skipped == int((e.n - e.n_rel).sum()) > 0
    assert ex.pairwise_warning(e.pw_skipped) == f"Парная относительная: пропущено {e.pw_skipped} пар с z_i + z_j ≤ 0."
    assert ex.pairwise_warning(0) is None


def test_the_grid_of_the_measurement():
    assert pairs.CELLS == 6  # m chosen by the measurement of 16.2 (2, 3, 4, 6, 8 — the fastest)
