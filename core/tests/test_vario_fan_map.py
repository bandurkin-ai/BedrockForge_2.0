"""The fan, the rose and the variogram map (Э16, 16.4; ADR 0022 §5, решение 8; ТЗ 8.6.1, 8.6.4).

The fan: in the horizontal plane — every pair on NumPy (`ref_bf_variogram.brute_force`) with the rays in the
world (θ from a1 = north towards a2 = west: az = −θ), N exactly, γ, 1 − ρ, pairwise 1e−12; in a turned plane —
the fan of the data turned the same way (a rotated plane = rotated data). The rays in the world, the rose (the
crossing of q·σ²), `turn` (a click keeps the plane), the JSON of the answer.

The map: BF `variogram_map` (`ref_bf_variogram`) and its tests (the symmetry, the centre, the east strip) — N
exactly, γ 1e−12; Д-21 GeostatsPy `varmapv` with the rule `trunc` (the rows of the reference flipped by Y, a
cell with npairs ≤ minnp — empty: min_pairs = minnp + 1): `tests/data/ref/geostatspy` and
`core/tests/data/variography/varmap_geostatspy.json` (six cases, a lattice with shifts of k + ½ cells and
repeated points; the throw-away generator is kept in the file): N exactly, γ 1e−9; a 3D frame with a thickness of
the layer against every ordered pair on NumPy. Hard data: far coordinates, duplicates, a lattice on the edges of
the cells, missing values. One hash with 1, 2, 4, 8 threads, any grid. The job `calculate`: the sample above the
limit, the stages, `what`.
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
from bf_core.geostat.variogram import fan as fn
from bf_core.geostat.variogram import pairs, spec
from bf_core.geostat.variogram import varmap as vm
from bf_core.geostat.variogram.points import Points
from bf_core.stats.weighted import StatsError
from tests import ref_bf_variogram as ref

REF = Path(__file__).resolve().parents[2] / "tests" / "data" / "ref" / "geostatspy" / "geostatspy_reference.json"
VARMAP = Path(__file__).parent / "data" / "variography" / "varmap_geostatspy.json"
INF = math.inf

# the data of BF tests/test_stats_variogram.py: the same generator, the same order of draws
_rng = np.random.default_rng(11)
N = 250
XY = _rng.uniform(0, 100, (N, 2))
V = np.sin(XY[:, 0] / 15) + 0.5 * np.cos(XY[:, 1] / 25) + _rng.normal(0, 0.2, N)


def close(a, b, tol=1e-12):
    """Equal within `tol` (relative to the size of the number, absolute below 1); NaN only where NaN."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    assert a.shape == b.shape
    assert np.array_equal(np.isnan(a), np.isnan(b))
    ok = ~np.isnan(b)
    err = np.abs(a[ok] - b[ok]) / np.maximum(1.0, np.abs(b[ok]))
    assert err.size == 0 or float(err.max()) <= tol, float(err.max())


def cloud(n=900, seed=1, flat=40.0):
    r = np.random.default_rng(seed)
    x, y, z = r.uniform(0, 240, n), r.uniform(0, 180, n), r.uniform(0, flat, n)
    return x, y, z, np.exp(r.normal(0, 0.7, n) + np.sin(x / 35.0) + 0.5 * np.cos(y / 50.0))


def brute_map(x, y, z, v, cell, frame=None, nu=5, nv=5, thickness=INF, min_pairs=1, rule="round"):
    """Every ordered pair (i, j), the self-pairs too, on NumPy: the cell of p_j − p_i in the system of `frame`."""
    f = np.eye(3) if frame is None else np.asarray(frame, dtype=float)
    z = np.zeros_like(x) if z is None else z
    pu, pv, pn = (fn.project(np.asarray(x, float), np.asarray(y, float), np.asarray(z, float), f[k]) for k in range(3))
    qu, qv = (pu[None, :] - pu[:, None]) / cell, (pv[None, :] - pv[:, None]) / cell
    iu, iv = (np.rint(qu), np.rint(qv)) if rule == "round" else (np.trunc(qu), np.trunc(qv))
    ok = (np.abs(iu) <= nu) & (np.abs(iv) <= nv) & (np.abs(pn[None, :] - pn[:, None]) <= thickness)
    k = ((iv + nv) * (2 * nu + 1) + iu + nu)[ok].astype(np.int64)
    d2 = ((v[None, :] - v[:, None]) ** 2)[ok]
    size = (2 * nv + 1) * (2 * nu + 1)
    n = np.bincount(k, minlength=size).reshape(2 * nv + 1, 2 * nu + 1)
    s = np.bincount(k, weights=d2, minlength=size).reshape(n.shape)
    gamma = np.full(n.shape, np.nan)
    np.divide(s, 2 * n, out=gamma, where=n >= min_pairs)
    return n, gamma


def digest_map(m: vm.VarMap) -> str:
    return hashlib.sha256(m.n.tobytes() + m.s2.tobytes()).hexdigest()


def digest_fan(f: fn.Fan) -> str:
    s = f.e.sums
    return hashlib.sha256(s.n.tobytes() + s.n_rel.tobytes() + s.sums.tobytes()).hexdigest()


# ---- the map: BF -------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("nx", "ny", "d", "minnp"), [(5, 5, 10.0, 3), (4, 4, 10.0, 1), (7, 3, 8.5, 5), (2, 6, 30.0, 1)]
)
def test_map_equals_bf_variogram_map(nx, ny, d, minnp):
    gam, npp = ref.variogram_map(XY, V, nx, ny, d, d, minnp=minnp)
    m = vm.varmap(XY[:, 0], XY[:, 1], None, V, d, nu=nx, nv=ny, min_pairs=minnp)
    assert m.n.shape == (2 * ny + 1, 2 * nx + 1) and m.n.dtype == np.int64
    assert np.array_equal(m.n, npp)
    close(m.gamma, gam)
    assert m.n_points == N and m.n_missing == 0


def test_bf_map_symmetry_center_and_counts():
    m = vm.varmap(XY[:, 0], XY[:, 1], None, V, 10.0, nu=5, nv=5, min_pairs=3)
    gam, npp = m.gamma, m.n
    assert gam.shape == npp.shape == (11, 11)
    assert npp[5, 5] >= N and gam[5, 5] < 0.1  # self pairs + |lag| < half cell
    assert np.array_equal(npp, npp[::-1, ::-1])  # point symmetry
    ok = ~np.isnan(gam)
    assert np.array_equal(gam[ok], gam[::-1, ::-1][ok])  # bit for bit
    assert np.all(npp[np.isnan(gam)] < 3)


def test_bf_map_east_strip_is_populated():
    m = vm.varmap(XY[:, 0], XY[:, 1], None, V, 10.0, nu=4, nv=4, min_pairs=1)
    assert np.isfinite(m.gamma[4, 5:]).all() and (m.n[4, 5:] > 0).all()  # the row v = 0: the shifts east


# ---- the map: Д-21 GeostatsPy varmapv (trunc) ---------------------------------------------------------------
def _against_varmapv(x, y, v, p, gamma, npairs):
    m = vm.varmap(x, y, None, v, p["dxlag"], nu=p["nxlag"], nv=p["nylag"], min_pairs=p["minnp"] + 1, rule="trunc")
    want_n = np.asarray(npairs, dtype=float)[::-1]  # row 0 of varmapv is the largest y
    want_g = np.asarray(gamma, dtype=float)[::-1]
    assert np.array_equal(m.n, want_n.astype(np.int64))
    close(m.gamma, np.where(want_g == -999.0, np.nan, want_g), 1e-9)
    return m


def test_d21_varmap_of_geostatspy():
    r = json.loads(REF.read_text(encoding="utf-8"))
    d = r["data"]
    m = _against_varmapv(np.array(d["X"]), np.array(d["Y"]), np.array(d["V"]), r["varmap"]["params"],
                         r["varmap"]["gamma"], r["varmap"]["npairs"])  # fmt: skip
    assert m.n[5, 5] == 1074 and m.rule == "trunc"


@pytest.mark.parametrize("case", json.loads(VARMAP.read_text(encoding="utf-8"))["cases"], ids=lambda c: c["id"])
def test_d21_varmap_cases(case):
    d = json.loads(VARMAP.read_text(encoding="utf-8"))[case["data"]]
    x, y, v = (np.array(d[k]) for k in ("X", "Y", "V"))
    m = _against_varmapv(x, y, v, case["params"], case["gamma"], case["npairs"])
    n_round = vm.varmap(x, y, None, v, case["params"]["dxlag"], nu=case["params"]["nxlag"],
                        nv=case["params"]["nylag"], min_pairs=1).n  # fmt: skip
    assert n_round.sum() <= m.n.sum()  # rounding: the centre is one cell wide, not two


def test_reference_files_reproduce_each_other():
    r = json.loads(REF.read_text(encoding="utf-8"))
    g = json.loads(VARMAP.read_text(encoding="utf-8"))
    assert g["reproduces_d21_varmap"] is True and g["geostatspy_version"] == "0.0.46"
    assert r["varmap"]["params"]["dxlag"] == r["varmap"]["params"]["dylag"]


# ---- the map: 3D, a frame, the layer ------------------------------------------------------------------------
@pytest.mark.parametrize("rule", vm.RULES)
@pytest.mark.parametrize(("plane", "thickness"), [((0, 0, 0), 8.0), ((35, -25, 10), 6.0), ((120, 60, -40), INF),
                                                   ((90, 0, 90), 5.0)])  # fmt: skip
def test_map_in_a_plane_equals_every_pair(plane, thickness, rule):
    x, y, z, v = cloud(700, seed=5)
    frame = fn.plane_frame(plane)
    m = vm.varmap(x, y, z, v, 12.0, frame=frame, nu=6, nv=4, thickness=thickness, min_pairs=3, rule=rule)
    n, gamma = brute_map(x, y, z, v, 12.0, frame, 6, 4, thickness, 3, rule)
    assert np.array_equal(m.n, n)
    close(m.gamma, gamma)
    assert np.array_equal(m.n, m.n[::-1, ::-1]) and np.array_equal(m.s2, m.s2[::-1, ::-1])


@pytest.mark.parametrize("cells", [1, 2, 3, 4, 6, 9])
def test_any_grid_gives_the_same_map(cells):
    x, y, z, v = cloud(800, seed=6)
    frame = fn.plane_frame((20, -10, 5))
    base = vm.varmap(x, y, z, v, 9.0, frame=frame, nu=8, nv=8, thickness=7.0, cells=4)
    m = vm.varmap(x, y, z, v, 9.0, frame=frame, nu=8, nv=8, thickness=7.0, cells=cells)
    assert np.array_equal(m.n, base.n)
    close(m.gamma, base.gamma)


def test_lattice_on_the_edges_of_the_cells_and_duplicates():
    gx, gy, gz = np.meshgrid(np.arange(8) * 2.5, np.arange(6) * 2.5, np.arange(3) * 2.0)
    x, y, z = (np.r_[a.ravel(), 5.0, 5.0] for a in (gx, gy, gz))  # shifts of k + ½ cells of 5 m; a repeated point
    v = np.cos(x / 3.0) + np.sin(y / 4.0) + 0.1 * z
    for rule in vm.RULES:
        for th in (2.0, 4.0, INF):  # |Δn| = thickness is in the layer
            m = vm.varmap(x, y, z, v, 5.0, nu=3, nv=2, thickness=th, min_pairs=1, rule=rule)
            n, gamma = brute_map(x, y, z, v, 5.0, None, 3, 2, th, 1, rule)
            assert np.array_equal(m.n, n), (rule, th)
            close(m.gamma, gamma)
    assert vm.varmap(x, y, z, v, 5.0, nu=3, nv=2, rule="round").n[2, 3] >= len(x)  # the centre: self-pairs


def test_far_coordinates_are_taken_as_they_are():
    x, y, z, v = cloud(500, seed=7)
    x, y = x + 512_345.678, y + 6_789_012.345
    m = vm.varmap(x, y, z, v, 10.0, nu=6, nv=6, thickness=10.0)
    n, gamma = brute_map(x, y, z, v, 10.0, None, 6, 6, 10.0, 5)
    assert np.array_equal(m.n, n)
    close(m.gamma, gamma)


def test_missing_rows_take_no_part_and_are_counted():
    x, y, z, v = cloud(400, seed=8)
    v2, x2 = v.copy(), x.copy()
    v2[[3, 50]] = np.nan
    x2[7] = np.inf
    m = vm.varmap(x2, y, z, v2, 10.0, nu=4, nv=4)
    keep = np.ones(400, dtype=bool)
    keep[[3, 50, 7]] = False
    w = vm.varmap(x[keep], y[keep], z[keep], v[keep], 10.0, nu=4, nv=4)
    assert m.n_missing == 3 and m.n_points == 397
    assert np.array_equal(m.n, w.n) and np.array_equal(m.s2, w.s2)


def test_one_hash_with_1_2_4_8_threads(monkeypatch):
    x, y, z, v = cloud(5000, seed=9)
    frame = fn.plane_frame((30, -15, 0))
    monkeypatch.setattr(pairs, "STRIP_CHECKS", 20_000)
    runs = [vm.varmap(x, y, z, v, 6.0, frame=frame, nu=15, nv=15, thickness=10.0, threads=t) for t in (1, 2, 4, 8)]
    assert runs[0].strips >= 100 and runs[0].n.sum() > 1_000_000
    assert len({digest_map(m) for m in runs}) == 1


def test_tick_reports_progress_and_cancels(monkeypatch):
    x, y, z, v = cloud(2000, seed=10)
    monkeypatch.setattr(pairs, "STRIP_CHECKS", 20_000)
    seen: list[float] = []
    m = vm.varmap(x, y, z, v, 6.0, nu=10, nv=10, threads=2, tick=seen.append)
    assert len(seen) == m.strips > 10 and seen == sorted(seen) and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    def stop(f: float) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        vm.varmap(x, y, z, v, 6.0, nu=10, nv=10, threads=2, tick=stop)


@pytest.mark.parametrize(("kw", "param"), [({"nu": 0}, "map.nu"), ({"nv": 101}, "map.nv"), ({"nu": 2.5}, "map.nu"),
                                           ({"cell": 0.0}, "map.cell"), ({"cell": INF}, "map.cell"),
                                           ({"thickness": 0.0}, "map.thickness"), ({"min_pairs": 0}, "map.min_pairs"),
                                           ({"rule": "floor"}, "rule"), ({"cells": 0}, "cells")])  # fmt: skip
def test_bad_parameters_name_the_parameter(kw, param):
    x, y, z, v = cloud(50, seed=11)
    args = {"cell": 10.0} | kw
    with pytest.raises(StatsError) as e:
        vm.varmap(x, y, z, v, args.pop("cell"), **args)
    assert e.value.code == "bad_param" and e.value.param == param and e.value.message


def test_too_few_points_and_a_cell_too_fine():
    with pytest.raises(StatsError) as e:
        vm.varmap([1.0, np.nan], [1.0, 2.0], None, [1.0, 2.0], 10.0)
    assert e.value.code == "too_few_values"
    with pytest.raises(StatsError) as e:
        vm.varmap([0.0, 1e9], [0.0, 0.0], [0.0, 1e9], [1.0, 2.0], 1e-3, nu=1, nv=1, thickness=1e-3)
    assert e.value.param == "map.cell"


def test_map_json_is_the_form_of_the_answer():
    x, y, z, v = cloud(300, seed=12)
    m = vm.varmap(x, y, z, v, 10.0, nu=3, nv=2, thickness=INF, min_pairs=5)
    j = m.to_json()
    assert set(j) == {"nu", "nv", "cell", "thickness", "min_pairs", "u", "v", "gamma", "n"}
    assert j["thickness"] == "inf" and j["u"] == [-30.0, -20.0, -10.0, 0.0, 10.0, 20.0, 30.0]
    assert j["v"] == [-20.0, -10.0, 0.0, 10.0, 20.0] and len(j["gamma"]) == 5 and len(j["gamma"][0]) == 7
    assert all(g is None for row, nr in zip(j["gamma"], j["n"], strict=True) for g, k in zip(row, nr, strict=True)
               if k < 5)  # fmt: skip
    assert json.loads(json.dumps(j)) == j


# ---- the fan -----------------------------------------------------------------------------------------------
def _brute_rays(f: fn.Fan):
    return [(d.az, d.plunge, d.az_tol, d.dip_tol, d.band_h, d.band_v) for d in f.directions]


@pytest.mark.parametrize(("n", "tol", "dip_tol", "band_v"), [(12, None, 22.5, 8.0), (4, 30.0, 90.0, INF),
                                                             (18, 4.0, 10.0, 3.0), (72, None, 45.0, 20.0)])  # fmt: skip
def test_fan_of_the_horizontal_plane_equals_every_pair(n, tol, dip_tol, band_v):
    x, y, z, v = cloud(600, seed=13)
    f = fn.fan(x, y, z, v, 10.0, 12, plane=(0, 0, 0), n=n, tol=tol, dip_tol=dip_tol, band_v=band_v)
    assert [d.az for d in f.directions] == [(-t) % 360.0 + 0.0 for t in f.thetas.tolist()]  # a2 — west
    want = ref.brute_force(x, y, z, v, _brute_rays(f), 10.0, 12)
    for k, w in enumerate(want):
        assert np.array_equal(f.e.n[k], w["n"]) and np.array_equal(f.e.n_rel[k], w["n_rel"])
        close(f.e.gamma[k], w["gamma"])
        close(f.e.one_minus_rho[k], 1.0 - w["rho"])
        close(f.e.pairwise[k], w["pairwise"])
        close(f.e.h[k], w["h"])


@pytest.mark.parametrize("plane", [(30, -20, 15), (125, 40, -70), (90, 0, 0), (200, -90, 30), (0, 0, 90)])
def test_fan_in_a_turned_plane_equals_the_fan_of_turned_data(plane):
    r = np.random.default_rng(14)
    n = 700
    s1, s2, s3 = r.uniform(0, 250, n), r.uniform(0, 200, n), r.uniform(0, 30, n)  # along a1, a2, a3
    v = np.exp(r.normal(0, 0.6, n) + np.sin(s1 / 40.0))
    u = fn.plane_frame(plane)
    world = [s1 * u[0][k] + s2 * u[1][k] + s3 * u[2][k] for k in range(3)]
    a = fn.fan(*world, v, 12.0, 10, plane=plane, n=12, dip_tol=20.0, band_v=9.0)
    b = fn.fan(-s2, s1, s3, v, 12.0, 10, plane=(0, 0, 0), n=12, dip_tol=20.0, band_v=9.0)  # a1 north, a2 west
    assert np.array_equal(a.e.n, b.e.n) and np.array_equal(a.e.n_rel, b.e.n_rel)
    for curve in ("gamma", "h", "one_minus_rho", "pairwise"):
        close(getattr(a.e, curve), getattr(b.e, curve))
    assert a.e.n.sum() > 10_000


def _lattice_3d():
    """Pairs at 0°, 45°, 90° … in the plane and straight along the normal (d_xy = 0), repeated points."""
    gx, gy, gz = np.meshgrid(np.arange(9) * 5.0, np.arange(9) * 5.0, np.arange(4) * 5.0)
    x, y, z = (np.r_[a.ravel(), 10.0, 10.0] for a in (gx, gy, gz))
    return x, y, z, np.cos(x / 7.0) + np.sin(y / 9.0) + 0.2 * z + 1.0


@pytest.mark.parametrize(("n", "tol", "dip_tol", "band_v"), [(12, None, 22.5, 8.0), (4, 30.0, 90.0, INF),
                                                             (8, 90.0, 22.5, 6.0), (6, 45.0, 30.0, INF),
                                                             (18, 4.0, 10.0, 3.0), (72, None, 90.0, 20.0),
                                                             (9, 22.5, 45.0, 0.5)])  # fmt: skip
@pytest.mark.parametrize("data", ["cloud", "lattice"])
def test_the_kernel_of_the_rays_is_the_pair_kernel_bit_for_bit(n, tol, dip_tol, band_v, data):
    x, y, z, v = cloud(900, seed=20) if data == "cloud" else _lattice_3d()
    plane = (35.0, -25.0, 10.0) if data == "cloud" else (0.0, 0.0, 0.0)
    for m in (1, 3, 6):
        f = fn.fan(x, y, z, v, 5.0, 8, plane=plane, n=n, tol=tol, dip_tol=dip_tol, band_v=band_v, cells=m)
        X, Y, Z = fn.to_plane(x, y, z, fn.plane_frame(plane))
        e = ex.experimental(X, Y, Z, v, f.e.directions, 5.0, 8, cells=m)
        assert digest_fan(f) == hashlib.sha256(e.sums.n.tobytes() + e.sums.n_rel.tobytes() +
                                               e.sums.sums.tobytes()).hexdigest()  # fmt: skip
        assert (f.e.checks, f.e.strips, f.e.cells) == (e.checks, e.strips, e.cells) and f.e.n.sum() > 0


@pytest.mark.parametrize("plane", [(0, 0, 0), (30, -20, 15), (125, 40, -70), (200, -90, 30), (310, 89.9, 0)])
def test_rays_in_the_world(plane):
    p, th, world, local = fn.rays(plane, 12)
    u = rotation.rotation_matrix(*p)
    assert th.tolist() == [15.0 * k for k in range(12)] and [d.az for d in local] == th.tolist()
    assert (world[0].az, world[0].plunge) == (p[0], -p[1] + 0.0)  # θ = 0 is a1 itself
    for t, d in zip(th.tolist(), world, strict=True):
        w = math.cos(math.radians(t)) * u[0] + math.sin(math.radians(t)) * u[1]
        close(rotation.direction(d.az, d.plunge) * (1.0 if w @ rotation.direction(d.az, d.plunge) > 0 else -1.0), w)
        assert d.band_h == INF and d.az_tol == 7.5
    assert [d.az for d in fn.rays((0, 0, 0), 4)[2]] == [0.0, 315.0, 270.0, 225.0]


def test_fan_threads_and_json(monkeypatch):
    x, y, z, v = cloud(4000, seed=15)
    monkeypatch.setattr(pairs, "STRIP_CHECKS", 20_000)
    runs = [fn.fan(x, y, z, v, 8.0, 12, plane=(40, -30, 20), band_v=16.0, threads=t) for t in (1, 2, 4, 8)]
    assert runs[0].e.strips >= 20 and len({digest_fan(f) for f in runs}) == 1
    j = runs[0].to_json(min_pairs=30)
    ray = j["directions"][1]
    assert set(ray) == {"theta", "az", "plunge", "az_tol", "dip_tol", "band_h", "band_v", "lags", "h", "n", "gamma",
                        "rho", "pairwise", "n_pw", "few"}  # fmt: skip
    assert ray["theta"] == 15.0 and ray["band_h"] == "inf" and ray["band_v"] == 16.0 and len(ray["lags"]) == 12
    assert json.loads(json.dumps(j)) == j


def test_fan_bad_parameters():
    x, y, z, v = cloud(60, seed=16)
    for kw, param in (({"n": 3}, "fan.n"), ({"n": 73}, "fan.n"), ({"tol": 0.0}, "fan.tol"), ({"tol": 91.0}, "fan.tol"),
                      ({"plane": (0, 95, 0)}, "ang2")):  # fmt: skip
        with pytest.raises(StatsError) as e:
            fn.fan(x, y, z, v, 10.0, 5, **kw)
        assert e.value.param == param


# ---- the rose ----------------------------------------------------------------------------------------------
def test_crossing_rules():
    h = np.array([10.0, 20.0, 30.0, 40.0, np.nan])
    g = np.array([0.4, 0.8, 1.2, 1.5, np.nan])
    n = np.array([100, 100, 100, 100, 0])
    assert fn.crossing(h, g, n, 1.0, 30) == pytest.approx(25.0, abs=1e-12)  # between 20 and 30
    assert fn.crossing(h, g, n, 0.3, 30) == 10.0  # the first point is at the level already
    assert math.isnan(fn.crossing(h, g, n, 2.0, 30))  # never reached
    few = np.array([100, 5, 100, 100, 0])  # the point at 20 m has few pairs: from 10 m to 30 m
    assert fn.crossing(h, g, few, 1.0, 30) == pytest.approx(10.0 + 20.0 * 0.6 / 0.8, abs=1e-12)
    assert fn.crossing(h, g, n, 1.2, 30) == 30.0  # exactly at the level


def test_rose_of_stripes_along_a1():
    r = np.random.default_rng(17)
    n = 2500
    s1, s2 = r.uniform(0, 400, n), r.uniform(0, 400, n)
    v = np.where(np.floor(s2 / 30.0) % 2 == 0, 1.0, 0.0) + r.normal(0, 0.05, n)  # stripes across a2
    plane = (60, -20, 10)
    u = fn.plane_frame(plane)
    world = [s1 * u[0][k] + s2 * u[1][k] for k in range(3)]
    f = fn.fan(*world, v, 10.0, 15, plane=plane, n=12, band_v=5.0)
    ro = fn.rose(f, float(v.var()), q=1.0, min_pairs=30)
    # along a1 — never; across: a pair h apart differs with the chance h / 30 — γ = h / 60 reaches σ² ≈ ¼ at 15 m
    assert math.isnan(ro.ranges[0]) and 13.0 < ro.ranges[6] < 17.0
    j = ro.to_json()
    assert j["rays"][0] == {"theta": 0.0, "range": None, "beyond": True} and j["rays"][6]["beyond"] is False
    assert j["q"] == 1.0 and j["level"] == pytest.approx(float(v.var()))
    with pytest.raises(StatsError) as e:
        fn.rose(f, 1.0, q=1.3)
    assert e.value.param == "fan.q"


@pytest.mark.parametrize("plane", [(0, 0, 0), (30, -20, 15), (125, 40, -70), (0, -90, 0)])
@pytest.mark.parametrize("theta", [0.0, 15.0, 90.0, 137.5])
def test_turn_keeps_the_plane(plane, theta):
    u = rotation.rotation_matrix(*plane)
    t = fn.turn(plane, theta)
    w = rotation.rotation_matrix(*t)
    close(w[0], math.cos(math.radians(theta)) * u[0] + math.sin(math.radians(theta)) * u[1])
    close(w[2], u[2])
    if theta == 0.0:
        assert t == rotation.normalize_angles(*plane)


# ---- the job ------------------------------------------------------------------------------------------------
def points_of(xyz, v) -> Points:
    n = len(v)
    counts = {"rows": n, "missing": 0, "no_position": 0, "outside_domain": 0, "used": n, "no_weight": 0}
    return Points(xyz=xyz, origin=np.zeros(3), values=v, z=v, weights=None, hole=np.arange(n, dtype=np.int32),
                  holes=[str(i) for i in range(n)], depth=np.zeros(n), length=np.ones(n),
                  row_ids=np.arange(n, dtype=np.int64), counts=counts, sigma2=float(v.var()),
                  sigma2_z=float(v.var()), median_length=1.0)  # fmt: skip


def map_spec(what=("fan", "map"), seed=12345, plane=(30.0, -10.0, 0.0)) -> spec.MapSpec:
    return spec.MapSpec(data=None, plane=plane, what=what, lags=spec.Lags(10.0, 12, 5.0),  # type: ignore[arg-type]
                        dip_tol=22.5, band_v=20.0, min_pairs=30, fan_n=12, fan_tol=7.5, q=1.0, nu=8, nv=8, cell=10.0,
                        thickness=20.0, map_min_pairs=5, seed=seed)  # fmt: skip


def test_calculate_fan_and_map_and_their_stages():
    x, y, z, v = cloud(1500, seed=18)
    p = points_of(np.stack([x, y, z], axis=1), v)
    seen: list[tuple[float | None, str | None]] = []
    r = vm.calculate(p, map_spec(), threads=2, tick=lambda f, s: seen.append((f, s)))
    f = fn.fan(x, y, z, v, 10.0, 12, 5.0, plane=(30, -10, 0), n=12, tol=7.5, dip_tol=22.5, band_v=20.0)
    m = vm.varmap(x, y, z, v, 10.0, frame=fn.plane_frame((30, -10, 0)), nu=8, nv=8, thickness=20.0)
    assert r.sampled is None and r.estimate == f.e.checks + m.checks
    assert digest_fan(r.fan) == digest_fan(f) and digest_map(r.map) == digest_map(m)
    j = r.to_json()
    assert set(j) == {"plane", "sampled", "fan", "rose", "map", "warnings"} and j["warnings"] == []
    assert j["plane"] == {"ang1": 30.0, "ang2": -10.0, "ang3": 0.0} and len(j["rose"]["rays"]) == 12
    assert j["rose"]["level"] == pytest.approx(float(v.var()))
    stages = [s for _f, s in seen]
    assert stages[0] == vm.STAGE_FAN and stages[-1] == vm.STAGE_MAP and set(stages) == {vm.STAGE_FAN, vm.STAGE_MAP}
    fr = [fv for fv, _s in seen]
    assert fr == sorted(fr) and fr[-1] == pytest.approx(1.0)
    only = vm.calculate(p, map_spec(what=("map",)))
    assert only.fan is None and only.rose is None and only.to_json()["fan"] is None
    assert digest_map(only.map) == digest_map(m) and only.estimate == m.checks
    assert vm.calculate(p, map_spec(what=("fan",))).to_json()["map"] is None


def test_calculate_samples_above_the_limit():
    x, y, z, v = cloud(3000, seed=19)
    v = v - 1.5  # some z_i + z_j ≤ 0: the warning of the pairwise relative
    p = points_of(np.stack([x, y, z], axis=1), v)
    full = vm.calculate(p, map_spec())
    limit = full.estimate / 4
    a = vm.calculate(p, map_spec(), limit=limit)
    b = vm.calculate(p, map_spec(), limit=limit, threads=4)
    c = vm.calculate(p, map_spec(seed=7), limit=limit)
    n_new = math.floor(3000 * math.sqrt(limit / full.estimate))
    assert a.sampled == {"n": n_new, "of": 3000, "seed": 12345} and a.estimate == full.estimate
    assert digest_fan(a.fan) == digest_fan(b.fan) and digest_map(a.map) == digest_map(b.map)
    assert digest_map(a.map) != digest_map(c.map) and c.sampled["seed"] == 7
    assert a.map.n[8, 8] >= n_new and a.map.n_points == n_new  # the self-pairs of the sample
    assert a.to_json()["warnings"] and a.to_json()["sampled"] == a.sampled


# ---- the warm-up (16.5) ------------------------------------------------------------------------------------
def test_nothing_compiles_after_the_warm_up():
    """`geostat/variogram/warm.py` runs every kernel of the variography with the dtypes of the jobs and of the
    tab: the calls after it — on writable and read-only columns — compile nothing, one specialization a kernel."""
    import sys

    from numba.core.registry import CPUDispatcher

    from bf_core.geostat.variogram import downhole
    from bf_core.geostat.variogram.warm import PARTS, frozen, warm

    def compiled() -> dict[str, int]:
        return {f"{name}.{key}": len(f.signatures) for name, mod in list(sys.modules.items())
                if name.startswith("bf_core.geostat.") for key, f in list(vars(mod).items())
                if isinstance(f, CPUDispatcher)}  # fmt: skip

    took = warm()
    assert set(took) == set(PARTS)
    before = compiled()
    for kernel in ("pairs._cell_checks", "pairs._strip", "downhole._holes", "fan._rays_strip", "varmap._map_strip"):
        assert before[f"bf_core.geostat.variogram.{kernel}"] == 1, kernel
    for wrap in (np.asarray, frozen):
        x, y, z, v = (wrap(a) for a in cloud(800, seed=30))
        p = points_of(wrap(np.stack([x, y, z], axis=1)), wrap(v))
        p = Points(**{**p.__dict__, "hole": wrap(np.repeat(np.arange(80, dtype=np.int32), 10)),
                      "depth": wrap(np.tile(np.arange(10) * 2.0, 80))})  # fmt: skip
        ex.calculate(p, ex.axes(30.0, -10.0, 0.0, 22.5, 22.5, 30.0, 20.0), 10.0, 12, threads=4)
        ex.calculate(p, ex.axes(0.0), 10.0, 12, limit=1e5)
        downhole.calculate(p, 2.0, 10, threads=4)
        vm.calculate(p, map_spec(), threads=4)
        vm.calculate(p, map_spec(), limit=1e5)
        ex.experimental(x, y, None, v, [pairs.Direction(45.0 * k, 0.0, 22.5, 90.0) for k in range(4)], 10.0, 10)
        vm.varmap(x, y, None, v, 10.0, nu=5, nv=5)
        vm.varmap(x, y, None, v, 10.0, nu=5, nv=5, rule="trunc", min_pairs=2)
        fn.fan(x, y, z, v, 10.0, 8, plane=(10.0, 0.0, 0.0), n=6, tol=45.0, dip_tol=90.0)
    after = compiled()
    grown = {k: (before.get(k, 0), n) for k, n in after.items() if n > before.get(k, 0)}
    assert not grown, grown
