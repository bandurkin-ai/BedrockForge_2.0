"""The fit of a variogram model (ТЗ 8.6.3; ADR 0022 §7; e16-api.md `POST /variography/fit`; Э16, 16.8).

Т8.6-5: points computed by a known model without noise (along the three axes, h̄ off the lag centres) — the fit
restores every parameter to 1e−4 relative. The bounds (the sill ≤ 1,2·σ², L/2 ≤ a ≤ 2·h_max), the fixed
parameters (kept bit for bit), the three rules of the weights, the start of the nugget from the variogram along
the holes, the gaps (never a zero), the warnings and the refusals {code, message, hint, param}. The calc is
in the form of the API (`directions` with `axis`, `h`, `n`, `gamma`, `rho`, `pairwise`, `n_pw`, `few`).
"""

from __future__ import annotations

import math
import struct
import time
from typing import Any

import numpy as np
import pytest

from bf_core.geostat import rotation as rot
from bf_core.geostat.variogram import fit as vf
from bf_core.geostat.variogram.model import Model, Structure
from bf_core.stats.weighted import StatsError

ANGLES = (30.0, -10.0, 20.0)
LAG, NLAG = 10.0, 20  # h_max = 205 m: the ranges lie in [5; 410] m


def bits(x: float) -> bytes:
    return struct.pack("<d", x)


def axis_directions(angles: tuple[float, float, float]) -> list[tuple[float, float]]:
    """(azimuth, plunge) of the axes a1, a2, a3 — as the calc writes them."""
    out = []
    for x, y, z in rot.rotation_matrix(*angles):
        az = math.degrees(math.atan2(x, y)) % 360.0
        out.append((az, -math.degrees(math.asin(max(-1.0, min(1.0, z))))))
    return out


def calc_result(
    model: Model,
    *,
    angles: tuple[float, float, float] = ANGLES,
    sigma2: float = 1.0,
    noise: float = 0.0,
    seed: int = 7,
    jitter: bool = True,
    downhole: dict[str, Any] | None = None,
    transform: str = "none",
    min_pairs: int = 30,
    nlag: int = NLAG,
) -> dict[str, Any]:
    """The form of a calc (`job.result`) whose points lie on `model` (plus a relative noise)."""
    rng = np.random.default_rng(seed)
    directions = []
    for axis, (az, plunge) in enumerate(axis_directions(angles), 1):
        k = np.arange(1, nlag + 1, dtype=np.float64)
        h = k * LAG + (0.31 * LAG * np.sin(1.7 * k + axis) if jitter else 0.0)
        g = model.gamma_along(h, az, plunge) * (1.0 + noise * rng.standard_normal(nlag))
        n = (600 - 25 * k).astype(int)
        directions.append({
            "axis": axis, "az": az, "plunge": plunge, "az_tol": 22.5, "dip_tol": 22.5, "band_h": 30.0,
            "band_v": 20.0, "lags": (k * LAG).tolist(), "h": h.tolist(), "n": n.tolist(), "gamma": g.tolist(),
            "rho": (1.0 - g).tolist(), "pairwise": g.tolist(), "n_pw": n.tolist(), "few": (n < min_pairs).tolist(),
        })  # fmt: skip
    return {
        "key": "ab" * 32, "set_id": 3, "table": "12", "field": "AU", "transform": transform, "sigma2": sigma2,
        "params": {"lag": LAG, "nlag": nlag, "lag_tol": LAG / 2, "min_pairs": min_pairs,
                   "downhole": downhole is not None},
        "directions": directions, "downhole": downhole, "warnings": [],
    }  # fmt: skip


def along_holes(c0: float, slope: float, *, n: int = 15, lag: float = 2.0) -> dict[str, Any]:
    h = lag * np.arange(1, n + 1, dtype=np.float64)
    g = c0 + slope * h
    counts = [1200] * n
    return {"lag": lag, "lag_tol": lag / 2, "lags": h.tolist(), "h": h.tolist(), "n": counts, "gamma": g.tolist(),
            "rho": (1.0 - g).tolist(), "pairwise": g.tolist(), "n_pw": counts, "few": [False] * n}  # fmt: skip


def params(m: Model) -> list[float]:
    return [m.c0, *[x for s in m.structures for x in (s.c, *s.ranges)]]


def assert_restored(got: dict[str, Any], truth: Model, rel: float = 1e-4) -> None:
    model = got["model"]
    fitted = Model(model["c0"], tuple(Structure(**s) for s in model["structures"]), model["common_axes"])
    for a, b in zip(params(fitted), params(truth), strict=True):
        assert abs(a - b) <= rel * max(abs(b), 1e-3), (params(fitted), params(truth))
    for s, t in zip(fitted.structures, truth.structures, strict=True):
        assert s.type == t.type and s.angles == t.angles  # the angles are fixed — bit for bit


def start_of(truth: Model, *, c0: float = 0.3, scale: float = 1.7) -> dict[str, Any]:
    """A start far from the truth: the types of the truth, equal contributions, other ranges, nugget 0.3."""
    n = len(truth.structures)
    structures = [{**s.as_dict(), "c": 0.4, "a1": s.a1 * scale, "a2": s.a2 / scale, "a3": s.a3 * scale}
                  for s in truth.structures]  # fmt: skip
    return {"type": "semivariogram", "c0": c0, "common_axes": True, "structures": structures[:n]}


def nested(c0: float, *items: tuple[str, float, float, float, float]) -> Model:
    return Model(c0, tuple(Structure(t, c, a1, a2, a3, *ANGLES) for t, c, a1, a2, a3 in items))


TWO_SPHERICAL = nested(0.1, ("spherical", 0.3, 40.0, 30.0, 32.0), ("spherical", 0.5, 120.0, 80.0, 45.0),
                       ("exponential", 0.6, 180.0, 150.0, 60.0))  # fmt: skip
THREE_TYPES = nested(0.05, ("gaussian", 0.25, 35.0, 31.0, 30.0), ("exponential", 0.45, 95.0, 60.0, 40.0),
                     ("spherical", 0.7, 190.0, 110.0, 75.0))  # fmt: skip


# --- Т8.6-5 ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("truth", [TWO_SPHERICAL, THREE_TYPES], ids=["two-spherical", "three-types"])
def test_t8_6_5_three_structures_are_restored(truth: Model) -> None:
    """Т8.6-5: three nested structures (ranges ≥ 3 lags on every axis), one type twice with other contributions."""
    got = vf.fit_variogram(calc_result(truth, sigma2=1.3), start_of(truth))
    assert_restored(got, truth)
    assert got["n"] == 3 * NLAG and got["starts"] == 8 and got["warnings"] == [] and got["order"] == [0, 1, 2]
    assert 0.0 <= got["residual"] < 1e-16 and list(got["model"]) == ["type", "c0", "common_axes", "structures"]


def test_t8_6_5_on_awkward_points() -> None:
    """Points on the lag centres, an anisotropy of other axes, a nugget from the holes, a start from zero sills."""
    angles = (300.0, 25.0, -70.0)
    truth = Model(0.2, (Structure("exponential", 0.5, 60.0, 45.0, 30.0, *angles),
                        Structure("spherical", 0.8, 150.0, 70.0, 35.0, *angles)))  # fmt: skip
    calc = calc_result(truth, angles=angles, sigma2=1.5, jitter=False, downhole=along_holes(0.2, 0.004))
    start = {"c0": 0.0, "structures": [{**s.as_dict(), "c": 0.0, "a1": 100.0, "a2": 100.0, "a3": 100.0}
                                       for s in truth.structures]}  # fmt: skip
    assert_restored(vf.fit_variogram(calc, start), truth)


def test_one_structure_and_a_pure_nugget() -> None:
    truth = nested(0.25, ("spherical", 0.9, 90.0, 60.0, 40.0))
    assert_restored(vf.fit_variogram(calc_result(truth), start_of(truth)), truth)
    flat = Model(0.7)
    got = vf.fit_variogram(calc_result(flat), {"c0": 0.1, "structures": []})
    assert abs(got["model"]["c0"] - 0.7) < 1e-12 and got["model"]["structures"] == []


def test_nscore_keeps_the_sill_at_one() -> None:
    """`type: nscore` — the sill S = 1 is fixed (ТЗ 8.6.3)."""
    truth = nested(0.15, ("spherical", 0.35, 50.0, 40.0, 30.0), ("exponential", 0.5, 160.0, 90.0, 45.0))
    calc = calc_result(truth, transform="nscore")
    got = vf.fit_variogram(calc, start_of(truth), kind="nscore")
    assert_restored(got, truth) and got["model"]["type"] == "nscore"
    sill = got["model"]["c0"] + sum(s["c"] for s in got["model"]["structures"])
    assert abs(sill - 1.0) < 1e-12
    off = vf.fit_variogram(calc_result(nested(0.1, ("spherical", 0.6, 80.0, 60.0, 40.0)), transform="nscore"),
                           start_of(truth), kind="nscore")  # fmt: skip
    assert abs(off["model"]["c0"] + sum(s["c"] for s in off["model"]["structures"]) - 1.0) < 1e-12


def test_correlogram_and_pairwise_fit_their_values() -> None:
    """correlogram — 1 − ρ, the sill near 1; pairwise — its own values and `n_pw`."""
    truth = nested(0.1, ("spherical", 0.4, 60.0, 40.0, 30.0), ("exponential", 0.5, 150.0, 100.0, 50.0))
    calc = calc_result(truth, sigma2=7.0)
    for kind in ("correlogram", "pairwise"):
        got = vf.fit_variogram(calc, start_of(truth), kind=kind)
        assert_restored(got, truth) and got["model"]["type"] == kind
    calc["directions"][0]["n_pw"] = [0] * NLAG  # no pair of the relative variogram along a1
    got = vf.fit_variogram(calc, start_of(truth), kind="pairwise")
    assert got["n"] == 2 * NLAG and any("Ось 1" in w for w in got["warnings"])


# --- bounds, fixes, weights -------------------------------------------------------------------------------------------


def test_the_fit_holds_its_bounds() -> None:
    """C0 + Σc ≤ 1,2·σ²; L/2 ≤ a ≤ 2·h_max — with a warning on the bound (ADR 0022 §7)."""
    high = nested(0.2, ("spherical", 1.8, 100.0, 70.0, 40.0))
    got = vf.fit_variogram(calc_result(high, sigma2=1.0), start_of(high))
    sill = got["model"]["c0"] + sum(s["c"] for s in got["model"]["structures"])
    assert abs(sill - 1.2) < 1e-9 and any("1,2·σ²" in w for w in got["warnings"])
    far = nested(0.1, ("spherical", 0.3, 40.0, 30.0, 30.0), ("exponential", 0.8, 2000.0, 120.0, 60.0))
    got = vf.fit_variogram(calc_result(far, sigma2=1.2), start_of(far))
    s = got["model"]["structures"][1]
    hi = 2.0 * (NLAG + 0.5) * LAG
    assert abs(s["a1"] - hi) < 1e-6 * hi and s["a2"] < hi
    assert any("2·h_max" in w and "структура 2" in w and "ось 1" in w for w in got["warnings"]), got["warnings"]
    for st in got["model"]["structures"]:
        assert all(LAG / 2 - 1e-9 <= st[k] <= hi + 1e-9 for k in ("a1", "a2", "a3"))


def test_fixed_parameters_are_kept_bit_for_bit() -> None:
    truth = TWO_SPHERICAL
    start = start_of(truth)
    start["c0"] = truth.c0
    start["structures"][1]["a2"] = truth.structures[1].a2
    got = vf.fit_variogram(calc_result(truth, sigma2=1.3), start, fix=["c0", "structures.1.a2"])
    assert bits(got["model"]["c0"]) == bits(truth.c0)
    assert bits(got["model"]["structures"][1]["a2"]) == bits(truth.structures[1].a2)
    assert_restored(got, truth) and got["fixed"] == ["c0", "structures.1.a2"]
    # Fixed at a wrong value: kept, the rest adapts; `fix_nugget` is the same as "c0" in `fix`.
    wrong = {**start_of(truth), "c0": 0.3}
    wrong["structures"][0]["c"] = 0.1 + 0.2
    one = vf.fit_variogram(calc_result(truth, sigma2=1.3), wrong, fix=["structures.0.c"], fix_nugget=True)
    two = vf.fit_variogram(calc_result(truth, sigma2=1.3), wrong, fix=["c0", "structures.0.c", "c0"])
    assert one == two and bits(one["model"]["c0"]) == bits(0.3)
    assert bits(one["model"]["structures"][0]["c"]) == bits(0.1 + 0.2) and one["residual"] > 1e-6
    # Everything fixed: nothing to fit, the residual of the model as it is.
    paths = ["c0", *[f"structures.{i}.{k}" for i in range(3) for k in ("c", "a1", "a2", "a3")]]
    exact = vf.fit_variogram(calc_result(truth, sigma2=1.3), truth.as_dict(), fix=paths)
    assert exact["residual"] < 1e-25 and exact["model"]["structures"] == truth.as_dict()["structures"]


def test_three_rules_of_the_weights() -> None:
    """w = N / h̄ (by default), N, N / γ² of the model of the pass before (three passes)."""
    n, h, g = np.array([100.0, 50.0, 10.0]), np.array([10.0, 20.0, 40.0]), np.array([0.2, 0.5, 2.0])
    assert np.array_equal(vf.point_weights("n_h", n, h), n / h)
    assert np.array_equal(vf.point_weights("n", n, h), n)
    assert np.array_equal(vf.point_weights("n_g2", n, h, g), n / (g * g))
    truth = THREE_TYPES
    exact = calc_result(truth, sigma2=1.3)
    for rule in vf.WEIGHTS:
        assert_restored(vf.fit_variogram(exact, start_of(truth), weights=rule), truth)
    noisy = calc_result(truth, sigma2=1.3, noise=0.04)
    got = {rule: vf.fit_variogram(noisy, start_of(truth), weights=rule) for rule in vf.WEIGHTS}
    assert got["n_h"]["model"] != got["n"]["model"] != got["n_g2"]["model"]
    assert vf.fit_variogram(noisy, start_of(truth)) == got["n_h"]  # n_h by default
    for rule in ("n_h", "n"):  # the residual is Σ w·(γ̂ − γ)² / σ⁴ of the answer
        m = got[rule]["model"]
        model = Model(m["c0"], tuple(Structure(**s) for s in m["structures"]))
        total = 0.0
        for d in noisy["directions"]:
            hh, nn, gg = np.array(d["h"]), np.array(d["n"], float), np.array(d["gamma"])
            fit = model.gamma_along(hh, d["az"], d["plunge"])
            total += float(np.sum(vf.point_weights(rule, nn, hh) * (gg - fit) ** 2))
        assert abs(got[rule]["residual"] - total / 1.3**2) <= 1e-9 * total


def test_the_jacobian_is_that_of_the_residuals() -> None:
    """The analytic Jacobian against central differences — free, fixed and nested ranges, the shares, S."""
    truth = THREE_TYPES
    pts, info = vf.points_of(calc_result(truth, sigma2=1.3), "semivariogram")
    rng = np.random.default_rng(5)
    for fixed in (set(), {0}, {2, 1 + 3 + 3 * 1 + 1}, {1, 2, 3}, {1 + 3 + 0, 1 + 3 + 3 * 2 + 2}):
        layout = vf._Layout(truth, fixed, sill_fixed=None, scale=info["scale"], lo=info["lo"], hi=info["hi"])
        problem = vf._Problem(pts, truth, layout)
        problem.weigh(vf.point_weights("n_h", pts.n, pts.h))
        for _ in range(5):
            theta = rng.uniform(0.05, 0.95, layout.size) * layout.ub
            jac = problem.jacobian(theta)
            num = np.empty_like(jac)
            for j in range(layout.size):
                step = np.zeros(layout.size)
                step[j] = 1e-6
                num[:, j] = (problem.residuals(theta + step) - problem.residuals(theta - step)) / 2e-6
            assert np.allclose(jac, num, rtol=1e-5, atol=1e-7 * np.abs(num).max()), fixed
        p, _ = layout.natural(theta)
        for j in fixed:
            assert bits(p[j]) == bits(layout.p0[j])  # a fixed parameter is not recomputed


def test_a_start_keeps_the_model_of_the_window() -> None:
    """The first start is the model of the request: its nugget, its shares of S*, its growing ranges; ranges that
    do not grow are spread to (s + 1)/n of the largest."""
    truth = TWO_SPHERICAL
    layout = vf._Layout(truth, set(), sill_fixed=None, scale=1.5, lo=5.0, hi=410.0)
    p, _ = layout.natural(layout.start(0.1, 1.0, truth))
    assert abs(p[0] - 0.1) < 1e-12 and abs(sum(p[:4]) - 1.5) < 1e-12
    assert np.allclose(p[1:4] / sum(p[1:4]), [0.3 / 1.4, 0.5 / 1.4, 0.6 / 1.4], rtol=1e-12)
    assert np.allclose(p[4:], [a for st in truth.structures for a in st.ranges], rtol=1e-12)
    half, _ = layout.natural(layout.start(0.1, 0.5, truth))
    assert np.allclose(half[4:], 0.5 * p[4:], rtol=1e-12)
    flat = Model(0.0, tuple(Structure("spherical", 0.5, 90.0, 60.0, 30.0, *ANGLES) for _ in range(3)))
    p, _ = layout.natural(layout.start(0.0, 1.0, flat))
    assert np.allclose(p[4:].reshape(3, 3), [[30.0, 20.0, 10.0], [60.0, 40.0, 20.0], [90.0, 60.0, 30.0]], rtol=1e-12)


# --- the points ------------------------------------------------------------------------------------------------------


def test_the_nugget_starts_from_the_holes() -> None:
    """The first three points along the holes extrapolated to h = 0 (≥ 0); gaps are skipped, not zeros."""
    assert abs(vf.downhole_nugget(along_holes(0.2, 0.01), "semivariogram") - 0.2) < 1e-12
    assert vf.downhole_nugget(along_holes(-0.3, 0.05), "semivariogram") == 0.0
    dh = along_holes(0.2, 0.01)
    dh["gamma"][0] = None
    dh["n"][1] = 3  # fewer than the threshold
    assert abs(vf.downhole_nugget(dh, "semivariogram", min_pairs=30) - 0.2) < 1e-12  # lags 3, 4, 5
    dh["gamma"] = [None, None, 0.5] + [None] * 12
    assert vf.downhole_nugget(dh, "semivariogram") is None and vf.downhole_nugget(None, "semivariogram") is None
    corr = along_holes(0.2, 0.01)
    corr["rho"] = (1.0 - np.array(corr["gamma"]) * 2.0).tolist()
    assert abs(vf.downhole_nugget(corr, "correlogram") - 0.4) < 1e-12


def test_points_skip_gaps_and_few_pairs() -> None:
    truth = nested(0.1, ("spherical", 0.9, 90.0, 60.0, 40.0))
    calc = calc_result(truth, min_pairs=200)  # n = 575 … 100: 16 lags of 20 on every axis
    d = calc["directions"][1]
    d["gamma"][0], d["h"][1], d["gamma"][2] = None, None, math.nan
    d["h"][3] = 0.0  # coincident points only
    pts, info = vf.points_of(calc, "semivariogram")
    assert pts.h.size == 3 * 16 - 4 and np.all(pts.h > 0) and np.all(np.isfinite(pts.value))
    assert info["lo"] == LAG / 2 and info["hi"] == 2 * (NLAG + 0.5) * LAG and info["scale"] == 1.0
    assert_restored(vf.fit_variogram(calc, start_of(truth)), truth)


def test_warnings_of_the_fit() -> None:
    truth = TWO_SPHERICAL
    calc = calc_result(truth, sigma2=1.3)
    calc["directions"][2]["n"] = [5] * NLAG  # the axis a3 without points
    got = vf.fit_variogram(calc, start_of(truth))
    assert any(w.startswith("Ось 3:") for w in got["warnings"]) and got["n"] == 2 * NLAG
    short = calc_result(truth, sigma2=1.3, nlag=4)  # 12 points, 13 unknowns
    got = vf.fit_variogram(short, start_of(truth))
    assert any("меньше, чем неизвестных" in w and "12" in w and "13" in w for w in got["warnings"])


def test_structures_come_by_the_major_range() -> None:
    """The structures are fitted nested in the order of a1 of the start and answered by ascending a1; `order` —
    the places in the request, `fixed` — the paths in the answer."""
    truth = nested(0.1, ("spherical", 0.4, 50.0, 40.0, 35.0), ("exponential", 0.6, 170.0, 100.0, 50.0))
    start = start_of(truth)
    start["structures"].reverse()  # the long one first
    start["structures"][0]["a3"] = 50.0
    got = vf.fit_variogram(calc_result(truth), start, fix=["structures.0.a3"])
    assert got["order"] == [1, 0] and got["fixed"] == ["structures.1.a3"]
    assert [s["type"] for s in got["model"]["structures"]] == ["spherical", "exponential"]
    assert_restored(got, truth)


def test_the_fit_is_deterministic_and_does_not_touch_its_arguments() -> None:
    calc, start = calc_result(THREE_TYPES, sigma2=1.3, noise=0.05), start_of(THREE_TYPES)
    before = repr((calc, start))
    first = vf.fit_variogram(calc, start, weights="n_g2")
    assert first == vf.fit_variogram(calc, start, weights="n_g2") and repr((calc, start)) == before
    assert first["residual"] > 0 and all(math.isfinite(x) for x in params(Model(
        first["model"]["c0"], tuple(Structure(**s) for s in first["model"]["structures"]))))  # fmt: skip


def test_hard_data_give_a_valid_model() -> None:
    """Strong noise, a short spherical structure between two lags, few points: a model inside the bounds."""
    truth = nested(0.0, ("spherical", 0.5, 12.0, 11.0, 15.0), ("spherical", 0.7, 300.0, 40.0, 25.0))
    calc = calc_result(truth, sigma2=1.0, noise=0.3, seed=11)
    got = vf.fit_variogram(calc, start_of(truth), kind="semivariogram", weights="n")
    m = got["model"]
    sill = m["c0"] + sum(s["c"] for s in m["structures"])
    assert 0.0 <= m["c0"] and all(s["c"] >= 0.0 for s in m["structures"]) and sill <= 1.2 + 1e-12
    for k in ("a1", "a2", "a3"):
        assert m["structures"][0][k] <= m["structures"][1][k] + 1e-9  # nested
    assert math.isfinite(got["residual"])


def test_ticks_between_the_starts() -> None:
    calls: list[int] = []
    vf.fit_variogram(calc_result(THREE_TYPES), start_of(THREE_TYPES), tick=lambda: calls.append(1))
    assert len(calls) >= 8

    def stop() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        vf.fit_variogram(calc_result(THREE_TYPES), start_of(THREE_TYPES), tick=stop)


# --- refusals --------------------------------------------------------------------------------------------------------


def test_no_points_to_fit() -> None:
    calc = calc_result(TWO_SPHERICAL, min_pairs=1000)
    with pytest.raises(StatsError) as e:
        vf.fit_variogram(calc, start_of(TWO_SPHERICAL))
    assert e.value.code == "too_few_points" and "1000" in e.value.message and e.value.hint


@pytest.mark.parametrize(
    ("change", "param"),
    [
        ({"model": {"c0": 0.1, "structures": [{"type": "spherical", "c": 1, "a1": 0}]}}, "model.structures.0.a1"),
        ({"model": {"c0": 0.1, "structures": [{"type": "cubic", "c": 1, "a1": 5}]}}, "model.structures.0.type"),
        ({"model": [1, 2]}, "model"),
        ({"weights": "n_h2"}, "weights"),
        ({"fix": ["structures.5.c"]}, "fix"),
        ({"fix": ["structures.0.range"]}, "fix"),
        ({"fix": "c0"}, "fix"),
        ({"fix_nugget": "да"}, "fix_nugget"),
        ({"kind": "variogram"}, "type"),
        ({"kind": "nscore"}, "type"),  # the calc is not of normal scores
        ({"result": {"directions": [{"axis": None, "az": 0, "plunge": 0, "h": [1], "n": [50], "gamma": [1]}]}},
         "key"),
        ({"result": {"sigma2": 1.0}}, "key"),
        ({"result": {"directions": [{"axis": 1, "az": None, "plunge": 0, "h": [1], "n": [50], "gamma": [1]}]}},
         "key"),
        ({"result": {"directions": [{"axis": 1, "az": 0, "plunge": 0, "h": [1, 2], "n": [50], "gamma": [1]}]}},
         "key"),
    ],
)  # fmt: skip
def test_refusals_name_their_parameter(change: dict[str, Any], param: str) -> None:
    args: dict[str, Any] = {"result": calc_result(TWO_SPHERICAL), "model": start_of(TWO_SPHERICAL), **change}
    with pytest.raises(StatsError) as e:
        vf.fit_variogram(args.pop("result"), args.pop("model"), **args)
    assert e.value.code == "bad_param" and e.value.param == param, (e.value.param, e.value.message)
    assert e.value.message and e.value.hint


def test_fit_of_three_structures_takes_at_most_three_seconds() -> None:
    """ТЗ 8.6.4: «подбор 3 структур — ≤ 3 с» (3 оси × 20 лагов, 8 стартов; the worst rule — three passes)."""
    calc = calc_result(THREE_TYPES, sigma2=1.3, noise=0.03)
    vf.fit_variogram(calc, start_of(THREE_TYPES))  # warm
    for rule in vf.WEIGHTS:
        t0 = time.perf_counter()
        vf.fit_variogram(calc, start_of(THREE_TYPES), weights=rule)
        elapsed = time.perf_counter() - t0
        print(f"fit 3 structures, {rule}: {elapsed:.3f} s")
        assert elapsed <= 3.0
