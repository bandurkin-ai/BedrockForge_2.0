"""Declustering (ТЗ 8.2.1, 8.2.4; ADR 0018 §7, решение 28): the tests of BF `test_stats_declustering.py` (those
that need no geostatspy), the weights of GeostatsPy `declus` from the stored reference, Т8.2-11, cells in 3D
against a direct numpy computation, threads, the weights `DECL_W` of a table.

Tolerance — 1e−12 relative (8.2.4): the kernel, BF `cell_declus`, GeostatsPy and the direct computation count
the same cells and differ only in the last bits of the sums."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from bf_core.stats import declus3d as d3
from bf_core.stats.declustering import cell_declus, pick_size, polygonal_declus
from bf_core.stats.weighted import StatsError

REL = 1e-12
REF = Path(__file__).resolve().parents[2] / "tests" / "data" / "ref" / "geostatspy" / "geostatspy_reference.json"

rng = np.random.default_rng(7)
# clustered sampling: high values sampled densely (the data of the BF tests)
Xc = np.r_[rng.uniform(0, 100, 60), rng.normal(30, 4, 80)]
Yc = np.r_[rng.uniform(0, 100, 60), rng.normal(30, 4, 80)]
V = 1.0 + 0.05 * (100 - np.hypot(Xc - 30, Yc - 30)) + rng.normal(0, 0.1, 140)


def brute_3d(x, y, z, v, cmin, cmax, ncell, noff, minmax, anisy=1.0, anisz=1.0):  # type: ignore[no-untyped-def]
    """GSLIB declus straight from `declus.f`: the cells of every size and offset by `np.unique`."""
    n = len(x)
    lo = [x.min() - 0.01, y.min() - 0.01, z.min() - 0.01]
    span = [np.ptp(x), np.ptp(y), np.ptp(z)]
    sizes = cmin + np.arange(ncell + 1) * (cmax - cmin) / ncell
    all_wts = []
    for cs in sizes:
        cell = [cs, cs * anisy, cs * anisz]
        fac = [min(c / noff, 0.5 * s) for c, s in zip(cell, span, strict=True)]
        wt = np.zeros(n)
        for o in range(noff):
            idx = np.column_stack([np.floor((a - (lo[k] - o * fac[k])) / cell[k]) for k, a in enumerate((x, y, z))])
            _, inv, counts = np.unique(idx, axis=0, return_inverse=True, return_counts=True)
            w = 1.0 / counts[np.ravel(inv)]
            wt += w / w.sum()
        all_wts.append(wt)
    means = np.array([math.fsum(w * v) / math.fsum(w) for w in all_wts])
    best = pick_size(means, float(v.mean()), minmax, ncell)
    wopt = np.ones(n) if best is None else all_wts[best]
    return wopt * n / wopt.sum(), np.r_[0.0, sizes], np.r_[v.mean(), means]


def cloud(n: int, seed: int, origin: tuple[float, float, float] = (0.0, 0.0, 0.0)):  # type: ignore[no-untyped-def]
    """Clustered samples in 3D: a sparse background, two dense clusters of high values, holes with many samples."""
    g = np.random.default_rng(seed)
    k = n // 4
    holes = np.repeat(g.uniform(0, 1, (5, 2)), k // 5 + 1, axis=0)[:k]  # five holes, their samples at one place
    x = np.r_[g.uniform(0, 1000, n - 3 * k), g.normal(300, 25, k), g.normal(700, 15, k), 1000 * holes[:, 0]]
    y = np.r_[g.uniform(0, 800, n - 3 * k), g.normal(250, 25, k), g.normal(600, 15, k), 800 * holes[:, 1]]
    z = np.r_[g.uniform(-300, 0, n - 3 * k), g.normal(-100, 20, k), g.normal(-220, 10, k), g.uniform(-300, 0, k)]
    v = np.exp(g.normal(0, 0.5, n) + 1.5 * np.exp(-((x - 300) ** 2 + (y - 250) ** 2) / 5000.0))
    return x + origin[0], y + origin[1], z + origin[2], v


# --- BF tests ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fn", ["bf", "kernel"])
def test_cell_declus_weights_sum_and_reduce_bias(fn: str) -> None:
    if fn == "bf":
        w, sizes, means = cell_declus(Xc, Yc, V, 5, 60, ncell=20, noff=4)
    else:
        w, sizes, means = d3.cell_declus_3d(Xc, Yc, None, V, 5, 60, ncell=20, noff=4)
    assert w.sum() == pytest.approx(len(V)) and np.all(w > 0)
    assert np.average(V, weights=w) < V.mean()  # clustered highs down-weighted
    assert sizes[0] == 0 and means[0] == pytest.approx(V.mean())
    assert len(sizes) == len(means) == 22


@pytest.mark.parametrize("fn", ["bf", "kernel"])
def test_cell_declus_max_and_uniform_fallback(fn: str) -> None:
    def run(x, y, v, *args, **kw):  # type: ignore[no-untyped-def]
        return cell_declus(x, y, v, *args, **kw) if fn == "bf" else d3.cell_declus_3d(x, y, None, v, *args, **kw)

    w, _, _ = run(Xc, Yc, V, 5, 60, ncell=20, minmax="max")
    assert np.average(V, weights=w) >= V.mean() - 1e-12
    # constant property: declustering can't change the mean -> equal weights
    gx, gy = np.meshgrid(np.arange(10.0), np.arange(10.0))
    w, _, _ = run(gx.ravel(), gy.ravel(), np.ones(100), 1, 5, ncell=4)
    assert np.allclose(w, 1.0)


def test_polygonal_isolated_sample_gets_large_weight() -> None:
    x = np.r_[np.random.default_rng(0).uniform(0, 10, 30), 90.0]
    y = np.r_[np.random.default_rng(1).uniform(0, 10, 30), 90.0]
    w = polygonal_declus(x, y, bounds=(0, 100, 0, 100))
    assert w[-1] > 5 * np.median(w[:-1]) and w.sum() == pytest.approx(31)


def test_validation() -> None:
    with pytest.raises(ValueError):
        cell_declus(Xc, Yc[:-1], V, 5, 60)
    with pytest.raises(ValueError):
        cell_declus(Xc, Yc, V, 0, 60)
    with pytest.raises(ValueError):
        cell_declus(Xc, Yc, V, 5, 60, minmax="avg")
    with pytest.raises(ValueError):
        polygonal_declus([1, 1], [2, 2])
    for bad in (dict(cmin=0), dict(cmax=1), dict(ncell=0), dict(noff=0), dict(minmax="avg"), dict(anisy=0),
                dict(anisz=-1), dict(anisy=math.inf)):  # fmt: skip
        kw = {"cmin": 5, "cmax": 60, **bad}
        with pytest.raises(StatsError) as err:
            d3.cell_declus_3d(Xc, Yc, np.zeros(140), V, **kw)
        assert err.value.code == "bad_param" and err.value.message and err.value.hint
    with pytest.raises(StatsError):
        d3.cell_declus_3d(Xc, Yc, None, np.r_[V[:-1], np.nan], 5, 60)
    with pytest.raises(StatsError):
        d3.cell_declus_3d(Xc[:0], Yc[:0], None, V[:0], 5, 60)


# --- GeostatsPy (ADR 0018, решение 28) ---------------------------------------------------------------------


@pytest.mark.parametrize("fn", ["bf", "kernel", "kernel3d"])
def test_weights_of_geostatspy_declus(fn: str) -> None:
    """The stored result of `geostatspy.geostats.declus` (80 points, 40 of them clustered): the weights, the
    cell sizes and the declustered means — the origin of the cells and the rule of the choice are the same."""
    ref = json.loads(REF.read_text(encoding="utf-8"))
    x, y, v = (np.array(ref["data"][k]) for k in ("X", "Y", "V"))
    p = ref["declus"]["params"]
    args = (p["cmin"], p["cmax"], p["ncell"], p["noff"], "min" if p["iminmax"] else "max")
    if fn == "bf":
        w, sizes, means = cell_declus(x, y, v, *args)
    else:
        z = None if fn == "kernel" else np.full(80, 125.0)
        w, sizes, means = d3.cell_declus_3d(x, y, z, v, *args, anisy=1.0, anisz=3.0)
    assert w == pytest.approx(ref["declus"]["weights"], rel=REL)
    assert sizes == pytest.approx(ref["declus"]["cell_size"], rel=REL)
    assert means == pytest.approx(ref["declus"]["declustered_mean"], rel=REL)
    assert w.sum() == pytest.approx(80, rel=REL)


# --- Т8.2-11 -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("anisz", [math.inf, 1e12, 1.0])
@pytest.mark.parametrize("minmax", ["min", "max"])
def test_t8_2_11_flat_data_equal_bf_cell_declus(anisz: float, minmax: str) -> None:
    """Т8.2-11. Декластеризация 3D на данных z = 0 с anisz → ∞ совпадает с BF `cell_declus`."""
    w0, s0, m0 = cell_declus(Xc, Yc, V, 5, 60, ncell=20, noff=4, minmax=minmax)
    w, s, m = d3.cell_declus_3d(Xc, Yc, np.zeros(140), V, 5, 60, ncell=20, noff=4, minmax=minmax, anisz=anisz)
    assert w == pytest.approx(w0, rel=REL) and np.array_equal(s, s0) and m == pytest.approx(m0, rel=REL)
    x, y, _, v = cloud(3000, 5)
    w0, s0, m0 = cell_declus(x, y, v, 10, 400, ncell=24, noff=8, minmax=minmax)
    w, s, m = d3.cell_declus_3d(x, y, np.zeros(3000), v, 10, 400, ncell=24, noff=8, minmax=minmax, anisz=anisz)
    assert w == pytest.approx(w0, rel=REL) and np.array_equal(s, s0) and m == pytest.approx(m0, rel=REL)
    plan = d3.cell_declus_3d(x, y, None, v, 10, 400, ncell=24, noff=8, minmax=minmax)
    assert np.array_equal(plan[0], w) and np.array_equal(plan[2], m)  # z = None is one layer of cells too


# --- cells in 3D -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("anisy", "anisz", "noff", "minmax", "origin"),
    [
        (1.0, 1.0, 8, "min", (0.0, 0.0, 0.0)),
        (0.5, 0.2, 5, "min", (0.0, 0.0, 0.0)),
        (2.0, 0.05, 3, "max", (0.0, 0.0, 0.0)),
        (1.0, 0.25, 4, "min", (512_345.67, 6_234_567.89, 1450.0)),  # project coordinates far from zero
        (1.0, 0.25, 1, "min", (-2_000_000.0, -7_000_000.0, -5000.0)),
    ],
)
def test_cells_3d_against_definition(anisy: float, anisz: float, noff: int, minmax: str, origin: tuple) -> None:
    x, y, z, v = cloud(4000, 9, origin)
    want = brute_3d(x, y, z, v, 20.0, 300.0, 12, noff, minmax, anisy, anisz)
    got = d3.cell_declus_3d(x, y, z, v, 20.0, 300.0, 12, noff, minmax, anisy, anisz)
    assert got[0] == pytest.approx(want[0], rel=REL) and got[0].sum() == pytest.approx(4000, rel=REL)
    assert np.array_equal(got[1], want[1]) and got[2] == pytest.approx(want[2], rel=REL)
    if minmax == "min":
        assert np.average(v, weights=got[0]) < v.mean()  # the dense clusters of high values weigh less


def test_regular_grid_gives_weights_of_one() -> None:
    """Регулярная сетка — веса 1: every cell of a grid aligned with the samples holds the same number of them;
    and whatever the cells, a size that does not move the mean leaves the weights uniform."""
    gx, gy, gz = np.meshgrid(np.arange(12.0) * 25, np.arange(10.0) * 25, np.arange(6.0) * 5, indexing="ij")
    x, y, z = gx.ravel(), gy.ravel(), gz.ravel()
    v = np.random.default_rng(2).lognormal(0, 1, x.size)
    w, sizes, means = d3.cell_declus_3d(x, y, z, v, 50.0, 50.0, ncell=1, noff=1, anisy=1.0, anisz=0.2)
    assert w == pytest.approx(np.ones(x.size), rel=REL) and means[1:] == pytest.approx(v.mean(), rel=REL)
    assert sizes.tolist() == [0.0, 50.0, 50.0]
    w, _, _ = d3.cell_declus_3d(x, y, z, np.full(x.size, 3.5), 10.0, 200.0, ncell=10, noff=8, anisz=0.2)
    assert np.array_equal(w, np.ones(x.size))
    p = d3.DeclusParams.of({"method": "cell3d", "cmin": 10, "cmax": 200})
    rep = d3.declus_weights(x, y, z, np.full(x.size, 3.5), p)
    assert rep.report["uniform"] and rep.report["best_size"] is None and np.array_equal(rep.weights, np.ones(x.size))


def test_threads_give_the_same_bits() -> None:
    """1, 2, 4, 8 потоков — побитно."""
    x, y, z, v = cloud(20_000, 17)
    base = d3.cell_declus_3d(x, y, z, v, 15.0, 400.0, ncell=24, noff=8, anisz=0.3, threads=1)
    assert base[0].sum() == pytest.approx(20_000, rel=REL) and len(set(base[2][1:].tolist())) > 20
    for threads in (2, 4, 8):
        again = d3.cell_declus_3d(x, y, z, v, 15.0, 400.0, ncell=24, noff=8, anisz=0.3, threads=threads)
        assert all(np.array_equal(a, b) for a, b in zip(base, again, strict=True)), threads
    p = d3.DeclusParams.of({"method": "polygon"})
    w1 = d3.declus_weights(x, y, None, v, p, threads=1).weights
    assert np.array_equal(w1, d3.declus_weights(x, y, None, v, p, threads=4).weights)


def test_hard_points() -> None:
    """One point, points on a line, all at one place, many at one place, a cell far too small."""
    one = d3.cell_declus_3d([5.0], [7.0], [1.0], [2.0], 1.0, 10.0, ncell=3)
    assert one[0].tolist() == [1.0] and one[2].tolist() == [2.0] * 5
    x = np.arange(50.0)
    v = np.r_[np.full(25, 1.0), np.full(25, 3.0)]
    line = d3.cell_declus_3d(x, np.zeros(50), np.zeros(50), v, 2.0, 20.0, ncell=6, noff=4)
    assert line[0] == pytest.approx(brute_3d(x, np.zeros(50), np.zeros(50), v, 2.0, 20.0, 6, 4, "min")[0], rel=REL)
    same = d3.cell_declus_3d(np.full(9, 3.0), np.full(9, 4.0), np.full(9, 5.0), np.arange(9.0), 1.0, 5.0, ncell=2)
    assert same[0] == pytest.approx(np.ones(9), rel=REL)
    # 300 samples at one place and 20 apart: the crowd shares one cell
    xs = np.r_[np.full(300, 10.0), np.linspace(100, 2000, 20)]
    vs = np.r_[np.full(300, 9.0), np.full(20, 1.0)]
    w, _, m = d3.cell_declus_3d(xs, xs * 0.5, xs * 0.1, vs, 50.0, 50.0, ncell=1, noff=1)
    assert w[:300] == pytest.approx(np.full(300, 320 / 21 / 300), rel=REL)
    assert w[300:] == pytest.approx(320 / 21, rel=REL)
    assert m[1] == pytest.approx((9.0 + 20.0) / 21, rel=REL)
    with pytest.raises(StatsError) as err:
        d3.cell_declus_3d([0.0, 1e12], [0.0, 1e12], [0.0, 1e12], [1.0, 2.0], 1e-3, 1.0, ncell=2)
    assert err.value.code == "bad_param" and err.value.param == "cmin" and "Увеличьте" in err.value.hint


def test_tick_reports_progress_and_cancels() -> None:
    x, y, z, v = cloud(2000, 3)
    for threads in (1, 4):
        seen: list[float] = []
        d3.cell_declus_3d(x, y, z, v, 20.0, 300.0, ncell=9, threads=threads, tick=seen.append)
        assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) == 11

        class Stop(Exception):
            pass

        calls = []

        def stop(fraction: float, calls: list = calls) -> None:
            calls.append(fraction)
            if len(calls) == 3:
                raise Stop

        with pytest.raises(Stop):
            d3.cell_declus_3d(x, y, z, v, 20.0, 300.0, ncell=9, threads=threads, tick=stop)
        assert len(calls) == 3


# --- polygons ----------------------------------------------------------------------------------------------


def test_polygons_share_one_place_and_query_by_rows() -> None:
    g = np.random.default_rng(6)
    x, y = g.uniform(0, 100, 400), g.uniform(0, 100, 400)
    fine = polygonal_declus(x, y, bounds=(0, 100, 0, 100), nx=700, ny=600)  # 420 000 nodes — several queries
    from scipy.spatial import cKDTree

    gx = (np.arange(700) + 0.5) * 100 / 700
    gy = (np.arange(600) + 0.5) * 100 / 600
    nodes = np.column_stack([np.tile(gx, 600), np.repeat(gy, 700)])
    counts = np.bincount(cKDTree(np.column_stack([x, y])).query(nodes)[1], minlength=400)
    assert np.array_equal(fine, counts * 400 / counts.sum())  # BF, bit for bit
    assert fine == pytest.approx(polygonal_declus(x, y, bounds=(0, 100, 0, 100), nx=700, ny=600, workers=4), rel=0)
    # a vertical hole: 6 samples at one place in plan share the polygon of the place
    xs, ys = np.r_[x, np.full(5, x[0])], np.r_[y, np.full(5, y[0])]
    w = polygonal_declus(xs, ys, bounds=(0, 100, 0, 100), nx=700, ny=600)
    place = np.r_[0, np.arange(400, 405)]
    assert w[place] == pytest.approx(np.full(6, fine[0] / 6 * 405 / 400), rel=REL)
    assert w[1:400] == pytest.approx(fine[1:] * 405 / 400, rel=REL) and w.sum() == pytest.approx(405, rel=REL)


# --- the weights of a table --------------------------------------------------------------------------------


def test_declus_weights_of_a_table() -> None:
    """Нет координат или значения — вес NaN; Σ весов = числу остальных проб."""
    x, y, z, v = cloud(5000, 23)
    x, y, z, v = x.copy(), y.copy(), z.copy(), v.copy()
    x[::50], y[7::90], z[3::70], v[11::40] = np.nan, np.nan, np.nan, np.nan
    for body in ({"method": "cell3d", "cmin": 20, "cmax": 300, "ncell": 12, "noff": 4, "anisz": 0.25},
                 {"method": "cell", "cmin": "20", "cmax": "300,0", "ncell": 12},
                 {"method": "polygon"}):  # fmt: skip
        p = d3.DeclusParams.of(body)
        need = [x, y, v] + ([z] if p.method == "cell3d" else [])
        ok = np.logical_and.reduce([np.isfinite(a) for a in need])
        out = d3.declus_weights(x, y, z, v, p, threads=2)
        assert np.isnan(out.weights[~ok]).all() and not np.isnan(out.weights[ok]).any()
        assert out.weights[ok].sum() == pytest.approx(ok.sum(), rel=REL)
        rep = out.report
        assert rep["method"] == p.method and rep["n"] == int(ok.sum()) and rep["n_missing"] == int((~ok).sum())
        assert rep["naive_mean"] == pytest.approx(v[ok].mean(), rel=REL)
        assert rep["mean"] == pytest.approx(np.average(v[ok], weights=out.weights[ok]), rel=REL)
        assert rep["mean"] < rep["naive_mean"] and rep["min_weight"] <= 1.0 <= rep["max_weight"]
        if p.method == "polygon":
            places = np.unique(np.column_stack([x[ok], y[ok]]), axis=0).shape[0]  # the holes have many samples
            assert rep["sizes"] == [] and rep["best_size"] is None and rep["nx"] == rep["ny"] == d3.polygon_grid(places)
            assert places < rep["n"] and rep["n_zero"] == int((out.weights == 0).sum())
        else:
            want = d3.cell_declus_3d(x[ok], y[ok], z[ok] if p.method == "cell3d" else None, v[ok], 20.0, 300.0, 12,
                                     p.noff, "min", p.anisy, p.anisz)  # fmt: skip
            assert np.array_equal(out.weights[ok], want[0])
            assert len(rep["sizes"]) == len(rep["means"]) == 13 and rep["best_size"] in rep["sizes"]
            assert rep["means"][rep["sizes"].index(rep["best_size"])] == pytest.approx(rep["mean"], rel=REL)
    with pytest.raises(StatsError) as err:
        d3.declus_weights(x, y, z, np.full(5000, np.nan), d3.DeclusParams.of({"method": "polygon"}))
    assert err.value.code == "no_data"
    with pytest.raises(StatsError):
        d3.declus_weights(x, y, None, v, d3.DeclusParams.of({"method": "cell3d", "cmin": 1, "cmax": 2}))


def test_polygon_grid_follows_the_number_of_points() -> None:
    assert [d3.polygon_grid(n) for n in (1, 100, 2500, 40_000, 1_000_000, 10**8)] == [200, 200, 200, 800, 4000, 4000]


@pytest.mark.parametrize(
    ("body", "param"),
    [
        ({"method": "voronoi"}, "method"),
        ({"method": "cell"}, "cmin"),
        ({"method": "cell", "cmin": 5}, "cmax"),
        ({"method": "cell", "cmin": 50, "cmax": 5}, "cmin"),
        ({"method": "cell", "cmin": 0, "cmax": 5}, "cmin"),
        ({"method": "cell", "cmin": 5, "cmax": "много"}, "cmax"),
        ({"method": "cell", "cmin": 5, "cmax": 50, "ncell": 0}, "ncell"),
        ({"method": "cell", "cmin": 5, "cmax": 50, "ncell": 2.5}, "ncell"),
        ({"method": "cell", "cmin": 5, "cmax": 50, "noff": 65}, "noff"),
        ({"method": "cell", "cmin": 5, "cmax": 50, "minmax": "avg"}, "minmax"),
        ({"method": "cell3d", "cmin": 5, "cmax": 50, "anisy": 0}, "anisy"),
        ({"method": "cell3d", "cmin": 5, "cmax": 50, "anisz": 1e7}, "anisz"),
        ({"method": "polygon", "nx": 0}, "nx"),
    ],
)
def test_bad_parameters_are_errors_with_a_hint(body: dict, param: str) -> None:
    with pytest.raises(StatsError) as err:
        d3.DeclusParams.of(body)
    assert err.value.code == "bad_param" and err.value.param == param
    assert err.value.message and err.value.hint and err.value.as_dict().keys() == {"code", "message", "hint"}


def test_defaults_are_those_of_the_window() -> None:
    p = d3.DeclusParams.of({"method": "cell", "cmin": 5, "cmax": 50, "ncell": None, "noff": "", "anisz": 0.1})
    assert (p.ncell, p.noff, p.minmax, p.anisy, p.anisz) == (20, 8, "min", 1.0, 1.0)  # anisz — of cell3d only
    assert d3.DeclusParams.of({"cmin": 5, "cmax": 50}).method == "cell"
    d3.warm()
