"""The variograms of the tab «Статистика» (ТЗ 7.29; ADR 0022 §11, решение 21; 16.10): the experimental variogram
in plan — omnidirectional or 0°, 45°, 90°, 135° (22,5°) — with the model of BF and its curve without data, the
variogram map (ΔX, ΔY), the limit of 20 000 intervals, an empty lag or cell is null, never 0.

The references are BF `api/app/stats/variogram.py` (`tests/ref_bf_variogram.py`) and the tests of BF
`api/tests/test_stats_variogram.py` on the same data (the generator 11, 250 points): N exactly, γ and h 1e−12.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from bf_core.stats import tab
from bf_core.stats import tab_vario as tv
from bf_core.stats.weighted import StatsError
from tests import ref_bf_variogram as ref

# the data of BF tests/test_stats_variogram.py: the same generator, the same order of draws
_rng = np.random.default_rng(11)
N = 250
XY = _rng.uniform(0, 100, (N, 2))
V = np.sin(XY[:, 0] / 15) + 0.5 * np.cos(XY[:, 1] / 25) + _rng.normal(0, 0.2, N)
POINTS = tab.tab_points(np.column_stack([XY, np.zeros(N)]), V)


def close(a, b, tol=1e-12):
    """Equal within `tol` (relative to the size of the number, absolute below 1); None/NaN only where NaN."""
    a = np.array([math.nan if x is None else x for x in np.ravel(a)], dtype=float)
    b = np.asarray(b, dtype=float).ravel()
    assert a.shape == b.shape
    assert np.array_equal(np.isnan(a), np.isnan(b))
    ok = ~np.isnan(b)
    err = np.abs(a[ok] - b[ok]) / np.maximum(1.0, np.abs(b[ok]))
    assert err.size == 0 or float(err.max()) <= tol, float(err.max())


def strict_json(out):
    """The answer is JSON as it is: no NaN, no infinity (an empty value is null)."""
    return json.loads(json.dumps(out, allow_nan=False))


# ---- the experimental variogram: BF ------------------------------------------------------------------------
@pytest.mark.parametrize(("lag", "nlag", "tol"), [(8.0, 6, None), (8.0, 40, None), (5.0, 12, 5.0), (7.5, 9, 1.0)])
@pytest.mark.parametrize("directions", ["omni", "four"])
def test_variogram_equals_bf(lag, nlag, tol, directions):
    out = strict_json(tv.tab_variogram(POINTS, lag=lag, nlag=nlag, lag_tol=tol, directions=directions))
    want = [(0.0, 90.0)] if directions == "omni" else [(a, 22.5) for a in (0.0, 45.0, 90.0, 135.0)]
    assert [(d["az"], d["az_tol"]) for d in out["directions"]] == want
    assert out["lags"] == (lag * np.arange(1, nlag + 1)).tolist()
    for d, (az, az_tol) in zip(out["directions"], want, strict=True):
        h, g, n = ref.experimental_variogram(XY, V, lag, nlag, lag_tol=tol, azimuth=az, azimuth_tol=az_tol)
        assert d["n"] == n.tolist()  # N exactly
        close(d["gamma"], g)  # a lag without pairs is null here and NaN in BF
        close(d["h"], h)
        assert d["omni"] is (az_tol >= 90.0)
    assert out["sigma2"] == float(V.var())
    assert out["count"] == N and out["lag"] == lag and out["nlag"] == nlag
    assert out["lag_tol"] == (lag / 2 if tol is None else tol)


def test_bf_empty_lags_are_null_and_counts_unordered_pairs():
    out = tv.tab_variogram(POINTS, lag=8.0, nlag=40)
    d = out["directions"][0]
    assert d["n"][-1] == 0 and d["gamma"][-1] is None and d["h"][-1] is None  # BF: NaN, here null — never 0
    assert d["few"][-1] is True and out["min_pairs"] == 30
    out = tv.tab_variogram(POINTS, lag=10.0, nlag=5)
    h = np.hypot(*(XY[:, None] - XY[None]).transpose(2, 0, 1))
    iu = np.triu_indices(N, 1)
    assert out["directions"][0]["n"] == [int((np.abs(h[iu] - 10.0 * k) <= 5.0).sum()) for k in range(1, 6)]


def test_bf_standardize_divides_by_the_variance():
    plain = tv.tab_variogram(POINTS, lag=8.0, nlag=6, directions="four")
    std = tv.tab_variogram(POINTS, lag=8.0, nlag=6, directions="four", standardize=True)
    assert std["standardize"] is True and plain["standardize"] is False
    for a, b in zip(plain["directions"], std["directions"], strict=True):
        _, gs, _ = ref.experimental_variogram(XY, V, 8.0, 6, azimuth=a["az"], azimuth_tol=22.5, standardize=True)
        close(b["gamma"], gs)
        assert b["n"] == a["n"]


def test_bf_recovers_a_pure_nugget():
    r = np.random.default_rng(3)
    pts = r.uniform(0, 100, (400, 2))
    z = r.normal(0, 1, 400)
    p = tab.tab_points(pts, z)
    out = tv.tab_variogram(p, lag=8.0, nlag=6)
    assert np.allclose(out["directions"][0]["gamma"], 1.0, atol=0.2)
    std = tv.tab_variogram(p, lag=8.0, nlag=6, standardize=True)
    assert np.allclose(std["directions"][0]["gamma"], np.array(out["directions"][0]["gamma"]) / z.var())


# ---- the model: BF ------------------------------------------------------------------------------------------
MODEL = {"nugget": 0.1, "structures": [{"it": "spherical", "cc": 0.5, "hmaj": 40, "hmin": 15, "azi": 30},
                                       {"it": 2, "cc": 0.4, "hmaj": 80, "hmin": 50, "azi": 120}]}  # fmt: skip
BF_MODEL = ref.VariogramModel(0.1, [{"it": 1, "cc": 0.5, "azi": 30, "hmaj": 40, "hmin": 15},
                                    {"it": 2, "cc": 0.4, "azi": 120, "hmaj": 80, "hmin": 50}])  # fmt: skip


def test_variogram_with_the_model_of_bf_at_the_lag_centres():
    out = strict_json(tv.tab_variogram(POINTS, lag=8.0, nlag=10, directions="four", model=MODEL))
    lags = 8.0 * np.arange(1, 11)
    for d in out["directions"]:
        close(d["model_gamma"], BF_MODEL.gamma(lags, d["az"]))  # BF: model.gamma(lags, d.azimuth)
    assert out["model"]["sill"] == pytest.approx(1.0, abs=1e-15) and out["model"]["nugget"] == 0.1
    assert out["model"]["structures"][1] == {"it": "exponential", "cc": 0.4, "hmaj": 80.0, "hmin": 50.0,
                                             "azi": 120.0}  # fmt: skip
    omni = tv.tab_variogram(POINTS, lag=8.0, nlag=10, model={"nug": 0.1, "structures": MODEL["structures"]})
    close(omni["directions"][0]["model_gamma"], BF_MODEL.gamma(lags, 0.0))  # the BF name `nug` too
    assert "model_gamma" not in tv.tab_variogram(POINTS, lag=8.0, nlag=10)["directions"][0]
    assert tv.tab_variogram(POINTS, lag=8.0, nlag=10)["model"] is None


def test_model_curve_without_data_equals_bf():
    out = strict_json(tv.tab_variogram_model(MODEL["nugget"], MODEL["structures"], h_max=120.0, n=61,
                                             directions="four"))  # fmt: skip
    h = np.linspace(0.0, 120.0, 61)
    assert out["sill"] == pytest.approx(BF_MODEL.sill, abs=1e-15) and out["n"] == 61 and out["h_max"] == 120.0
    for d in out["directions"]:
        assert d["h"] == h.tolist()
        close(d["gamma"], BF_MODEL.gamma(h, d["az"]))
        close(d["cov"], BF_MODEL.cov(h, d["az"]))
        assert d["gamma"][0] == 0.0 and d["cov"][0] == pytest.approx(1.0, abs=1e-15)  # γ(0) = 0, C(0) — the sill
    assert [d["az"] for d in out["directions"]] == [0.0, 45.0, 90.0, 135.0]


def test_bf_model_basic_properties():
    out = tv.tab_variogram_model(0.1, [{"it": "spherical", "cc": 0.9, "hmaj": 30}], h_max=100.0, n=1001)
    g, h = np.array(out["directions"][0]["gamma"]), np.array(out["directions"][0]["h"])
    assert out["sill"] == pytest.approx(1.0)
    assert g[0] == 0.0 and out["directions"][0]["cov"][0] == pytest.approx(1.0)
    assert g[1] == pytest.approx(0.1 + 0.9 * (1.5 * 0.1 / 30 - 0.5 * (0.1 / 30) ** 3), abs=1e-12)  # the nugget jump
    assert g[h == 30.0][0] == pytest.approx(1.0) and g[-1] == pytest.approx(1.0)
    inside = (h > 0) & (h < 29)
    assert np.all(np.diff(g[inside]) > 0)
    for it in ("exponential", "gaussian"):  # the practical range: 95 % of the contribution, as GSLIB and BF
        out = tv.tab_variogram_model(0.0, [{"it": it, "cc": 1.0, "hmaj": 20}], h_max=20.0, n=2)
        assert out["directions"][0]["gamma"][1] == pytest.approx(1 - np.exp(-3), rel=1e-9)
    default = tv.tab_variogram_model(0.0, [{"it": 1, "cc": 1.0, "hmaj": 20, "hmin": 50}])
    assert default["h_max"] == 75.0 and default["n"] == 101  # 1,5 × the largest range


@pytest.mark.parametrize(
    ("kw", "param"),
    [
        ({"nugget": 0, "structures": [{"it": "cubic", "cc": 1, "hmaj": 1}]}, "structures.0.it"),
        ({"nugget": 0, "structures": [{"it": 1, "cc": 1, "hmaj": 0}]}, "structures.0.hmaj"),
        ({"nugget": -1, "structures": []}, "nugget"),
        ({"nugget": 0, "structures": [{"it": 1, "cc": 1, "hmaj": 5}] * 5}, "structures"),
        ({"nugget": 0.5, "structures": []}, "h_max"),  # no range to take the length of the curve from
        ({"nugget": 0, "structures": [{"it": 1, "cc": 1, "hmaj": 5}], "h_max": -1}, "h_max"),
        ({"nugget": 0, "structures": [{"it": 1, "cc": 1, "hmaj": 5}], "n": 1}, "n"),
        ({"nugget": 0, "structures": [{"it": 1, "cc": 1, "hmaj": 5}], "n": 2001}, "n"),
        ({"nugget": 0, "structures": [{"it": 1, "cc": 1, "hmaj": 5}], "directions": "six"}, "directions"),
    ],
)
def test_bf_model_validation_names_the_parameter(kw, param):
    with pytest.raises(StatsError) as e:
        tv.tab_variogram_model(**kw)
    assert e.value.code == "bad_param" and e.value.param == param
    assert e.value.message and e.value.hint  # in Russian, with the advice
    if param in ("h_max", "n", "directions"):
        return  # the curve without data only: the variogram draws the model at its lags
    with pytest.raises(StatsError) as e:
        tv.tab_variogram(POINTS, lag=8.0, model={"nugget": kw["nugget"], "structures": kw["structures"]})
    assert e.value.code == "bad_param" and e.value.param == "model." + param


# ---- the map: BF ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("n", "cell", "minnp"), [(5, 10.0, 3), (4, 10.0, 1), (7, 8.5, 5), (2, 30.0, 1)])
def test_map_equals_bf_variogram_map(n, cell, minnp):
    out = strict_json(tv.tab_map(POINTS, cell=cell, n=n, min_pairs=minnp))
    gam, npp = ref.variogram_map(XY, V, n, n, cell, cell, minnp=minnp)
    assert out["n"] == npp.tolist()  # [row][column]: rows by ΔY from the south, columns by ΔX from the west
    close(out["gamma"], gam)
    assert out["u"] == (cell * np.arange(-n, n + 1)).tolist() == out["v"]
    assert out["nu"] == out["nv"] == n and out["cell"] == cell and out["min_pairs"] == minnp
    assert out["count"] == N and out["sigma2"] == float(V.var())


def test_bf_map_symmetry_centre_and_empty_cells():
    out = tv.tab_map(POINTS, cell=10.0, n=5, min_pairs=3)
    n = np.array(out["n"])
    g = np.array([[math.nan if x is None else x for x in row] for row in out["gamma"]])
    assert n.shape == g.shape == (11, 11)
    assert n[5, 5] >= N and g[5, 5] < 0.1  # self pairs + |lag| < half a cell
    assert np.array_equal(n, n[::-1, ::-1])  # point symmetry
    ok = ~np.isnan(g)
    assert np.array_equal(g[ok], g[::-1, ::-1][ok])
    assert np.all(n[np.isnan(g)] < 3)  # a cell with fewer pairs than the threshold is empty, never 0
    sparse = tv.tab_map(POINTS, cell=1.0, n=3, min_pairs=50)
    assert any(x is None for row in sparse["gamma"] for x in row)


def test_bf_map_east_strip_is_populated_and_standardize():
    out = tv.tab_map(POINTS, cell=10.0, n=4, min_pairs=1)
    g, n = np.array(out["gamma"], dtype=float), np.array(out["n"])
    assert np.isfinite(g[4, 5:]).all() and (n[4, 5:] > 0).all()  # the row ΔY = 0: the shifts to the east
    gam, _ = ref.variogram_map(XY, V, 4, 4, 10.0, 10.0, minnp=1, standardize=True)
    close(tv.tab_map(POINTS, cell=10.0, n=4, min_pairs=1, standardize=True)["gamma"], gam)


# ---- the data of the tab: the limit, missing values, the defaults ------------------------------------------------
def test_20001_intervals_are_refused_with_the_advice_to_composite():
    rng = np.random.default_rng(1)
    pos, v = rng.uniform(0.0, 5000.0, (20_001, 3)), rng.lognormal(0.0, 1.0, 20_001)
    with pytest.raises(StatsError) as e:
        tab.tab_points(pos, v)
    assert e.value.code == "too_many_samples" and "Композиты" in e.value.hint
    big = tab.TabPoints(pos[:, 0], pos[:, 1], v, np.arange(20_001), {"rows": 20_001, "missing": 0, "no_position": 0,
                                                                      "used": 20_001})  # fmt: skip
    for call in (lambda: tv.tab_variogram(big, lag=50.0), lambda: tv.tab_map(big, cell=50.0)):
        with pytest.raises(StatsError) as e:
            call()
        assert e.value.code == "too_many_samples" and e.value.param == "table" and "20 000" in e.value.message
        assert "Скомпозитируйте" in e.value.hint


def test_exactly_20000_intervals_are_computed():
    rng = np.random.default_rng(2)
    pos, v = rng.uniform(0.0, 2000.0, (20_000, 3)), rng.lognormal(0.0, 1.0, 20_000)
    p = tab.tab_points(pos, v)
    out = tv.tab_variogram(p, lag=25.0, nlag=10, directions="four", threads=2)
    assert all(sum(d["n"]) > 0 for d in out["directions"])
    m = tv.tab_map(p, cell=25.0, n=10, threads=2)
    assert len(m["n"]) == 21 and m["n"][10][10] >= 20_000


def test_missing_values_take_no_part_and_are_counted():
    v = V.copy()
    v[[3, 17, 99]] = np.nan
    pos = np.column_stack([XY, np.zeros(N)])
    pos[5, 0] = np.nan  # no place on a hole path
    p = tab.tab_points(pos, v)
    keep = np.isfinite(v) & np.isfinite(pos[:, 0])
    out = tv.tab_variogram(p, lag=8.0, nlag=6)
    _, g, n = ref.experimental_variogram(XY[keep], V[keep], 8.0, 6, azimuth_tol=90.0)
    assert out["directions"][0]["n"] == n.tolist()
    close(out["directions"][0]["gamma"], g)
    assert out["count"] == N - 4 and out["counts"] == {"rows": N, "missing": 3, "no_position": 1, "used": N - 4}
    m = tv.tab_map(p, cell=10.0, n=3)
    gam, npp = ref.variogram_map(XY[keep], V[keep], 3, 3, 10.0, 10.0, minnp=5)
    assert m["n"] == npp.tolist() and m["counts"] == out["counts"]
    close(m["gamma"], gam)


def test_too_few_values_and_no_spread():
    one = tab.tab_points(np.array([[0.0, 0.0, 0.0]]), np.array([1.0]))
    flat = tab.tab_points(np.column_stack([XY, np.zeros(N)]), np.full(N, 2.5))
    for call in (tv.tab_variogram, tv.tab_map):
        with pytest.raises(StatsError) as e:
            call(one)
        assert e.value.code == "too_few_values"
        with pytest.raises(StatsError) as e:
            call(flat)
        assert e.value.code == "no_spread" and e.value.hint


def test_defaults_are_those_of_bf():
    out = tv.tab_variogram(POINTS)
    span = max(np.ptp(XY[:, 0]), np.ptp(XY[:, 1]))  # ≈ 99,6 m: span / 20 ≈ 4,98 → the round 5
    assert span / 20 == pytest.approx(4.98, abs=0.02)
    assert out["lag"] == 5.0 and out["nlag"] == 10 and out["lag_tol"] == 2.5 and out["min_pairs"] == 30
    assert len(out["directions"]) == 1 and out["directions"][0]["omni"] is True
    m = tv.tab_map(POINTS)
    assert m["cell"] == 5.0 and m["nu"] == 10 and m["min_pairs"] == 5
    assert tv.default_lag(POINTS) == 5.0
    assert [tv.nice_step(x) for x in (1.0, 1.6, 3.4, 3.6, 7.4, 7.6, 0.042)] == [1.0, 2.0, 2.0, 5.0, 5.0, 10.0, 0.05]
    same = tab.tab_points(np.zeros((3, 3)), np.array([1.0, 2.0, 3.0]))
    assert tv.default_lag(same) == 50.0  # no extent in plan: BF `defaultLag(null)`
    text = tv.tab_variogram(POINTS, lag="8,0", nlag="6", lag_tol="4.0", min_pairs=10.0)
    assert (text["lag"], text["nlag"], text["lag_tol"], text["min_pairs"]) == (8.0, 6, 4.0, 10)


@pytest.mark.parametrize(
    ("kw", "param"),
    [
        ({"lag": 0}, "lag"),
        ({"lag": -5}, "lag"),
        ({"lag": "пять"}, "lag"),
        ({"lag": math.inf}, "lag"),
        ({"lag": True}, "lag"),
        ({"nlag": 0}, "nlag"),
        ({"nlag": 201}, "nlag"),
        ({"nlag": 2.5}, "nlag"),
        ({"lag": 8.0, "lag_tol": 9.0}, "lag_tol"),
        ({"lag_tol": -1}, "lag_tol"),
        ({"directions": "six"}, "directions"),
        ({"directions": ["omni"]}, "directions"),
        ({"min_pairs": 0}, "min_pairs"),
        ({"standardize": "да"}, "standardize"),
        ({"model": [1, 2]}, "model"),
    ],
)
def test_bad_parameters_of_the_variogram(kw, param):
    with pytest.raises(StatsError) as e:
        tv.tab_variogram(POINTS, **kw)
    assert e.value.code == "bad_param" and e.value.param == param and e.value.hint


@pytest.mark.parametrize(
    ("kw", "param"),
    [
        ({"cell": 0}, "cell"),
        ({"cell": "∞"}, "cell"),
        ({"n": 0}, "n"),
        ({"n": 101}, "n"),
        ({"min_pairs": 1001}, "min_pairs"),
        ({"cell": 1e-9, "n": 100}, "cell"),  # too fine for the extent of the data (the grid of the kernel)
        ({"standardize": 1}, "standardize"),
    ],
)
def test_bad_parameters_of_the_map(kw, param):
    with pytest.raises(StatsError) as e:
        tv.tab_map(POINTS, **kw)
    assert e.value.code == "bad_param" and e.value.param == param and e.value.hint


def test_threads_give_the_same_answer_and_tick_cancels():
    rng = np.random.default_rng(4)
    pos, v = rng.uniform(0.0, 1000.0, (6000, 3)), rng.lognormal(0.0, 1.0, 6000)
    p = tab.tab_points(pos, v)
    one = tv.tab_variogram(p, lag=20.0, nlag=15, directions="four", threads=1)
    assert tv.tab_variogram(p, lag=20.0, nlag=15, directions="four", threads=4) == one
    assert tv.tab_map(p, cell=20.0, n=10, threads=1) == tv.tab_map(p, cell=20.0, n=10, threads=4)
    seen: list[float] = []
    tv.tab_variogram(p, lag=20.0, nlag=15, directions="four", tick=seen.append)
    assert seen and seen == sorted(seen) and seen[-1] == pytest.approx(1.0)
    seen.clear()
    tv.tab_map(p, cell=20.0, n=10, tick=seen.append)
    assert seen and seen[-1] == pytest.approx(1.0)

    def cancel(f: float) -> None:
        raise KeyboardInterrupt  # what a cancelled job raises stops the run

    for call in (tv.tab_variogram, tv.tab_map):
        with pytest.raises(KeyboardInterrupt):
            call(p, tick=cancel)
