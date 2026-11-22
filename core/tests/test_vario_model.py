"""The variogram model (ТЗ 8.6.3; ADR 0022 §6; Э16, 16.6): Т8.6-4 (1e−12), the tests of the model of BF
(`api/tests/test_stats_variogram.py`), GeostatsPy `vmodel` (ТЗ 7.35 Д-21: 1e−9), the 2D model of the tab
«Статистика» against the formula of BF, validation as `{code, message, hint}`.

GeostatsPy is not a dependency (ADR 0012). Its numbers are data: `tests/data/ref/geostatspy` (Д-21, one azimuth)
and `tests/data/variography/vmodel_geostatspy.json` — the model of the BF test and two more at several azimuths,
computed by the same package (it reproduces the Д-21 vector bit for bit); the throw-away script is kept in the
file itself (`generator_source`).

The shared answers `tests/data/variography/model_curves.json` are read here and by vitest
(`ui/src/modules/variography/model.ts`, ТД14-3). They are computed by the core; rewrite the file after a
deliberate change: `BF_REGEN_VARIO_CASES=1 uv run pytest -q tests/test_vario_model.py`.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.geostat import rotation as rot
from bf_core.geostat.variogram import model as vm
from bf_core.geostat.variogram.model import Model, Structure
from bf_core.stats.weighted import StatsError

HERE = Path(__file__).parent
DATA = HERE / "data" / "variography" / "model_curves.json"
VMODEL = HERE / "data" / "variography" / "vmodel_geostatspy.json"
D21 = HERE.parents[1] / "tests" / "data" / "ref" / "geostatspy" / "geostatspy_reference.json"
REGEN = os.environ.get("BF_REGEN_VARIO_CASES") == "1"
E3 = 1.0 - math.exp(-3.0)  # the share of the contribution at the practical range


def bf_gamma_xy(nug: float, structures: list[dict[str, Any]], dx: Any, dy: Any) -> np.ndarray:
    """BF `VariogramModel.gamma_xy` (api/app/stats/variogram.py), word for word — the reference of the 2D model."""
    dx, dy = np.broadcast_arrays(np.asarray(dx, float), np.asarray(dy, float))
    out = np.zeros(dx.shape)
    for s in structures:
        it, cc, azi, hmaj = s["it"], float(s["cc"]), float(s.get("azi", 0.0)), float(s["hmaj"])
        hmin = float(s.get("hmin", hmaj))
        a = np.deg2rad(90.0 - azi)
        u = dx * np.cos(a) + dy * np.sin(a)
        w = (-dx * np.sin(a) + dy * np.cos(a)) * (hmaj / hmin)
        hr = np.hypot(u, w) / hmaj
        if it in (1, "spherical"):
            out += cc * np.where(hr < 1.0, 1.0 - hr * (1.5 - 0.5 * hr * hr), 0.0)
        elif it in (2, "exponential"):
            out += cc * np.exp(-3.0 * hr)
        else:
            out += cc * np.exp(-3.0 * hr * hr)
    sill = nug + sum(float(s["cc"]) for s in structures)
    zero = (dx == 0) & (dy == 0)
    return sill - np.where(zero, sill, out)


# --- Т8.6-4 and the shapes --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("angles", [(0.0, 0.0, 0.0), (30.0, -20.0, 40.0), (200.0, 35.0, -70.0), (40.0, -90.0, 25.0)])
def test_t8_6_4_the_sill_is_reached_at_the_range_of_each_axis(angles: tuple[float, float, float]) -> None:
    """Т8.6-4: at h_r = 1 a spherical structure gives c, an exponential and a gaussian — c·(1 − e^(−3)), 1e−12;
    along the minor axis the sill is reached at a3."""
    c, a = 0.8, (120.0, 60.0, 20.0)
    axes = rot.rotation_matrix(*angles)
    for kind, want in (("spherical", c), ("exponential", c * E3), ("gaussian", c * E3)):
        m = Model(0.0, (Structure(kind, c, *a, *angles),))
        for k in range(3):
            assert abs(float(m.gamma(a[k] * axes[k])) - want) <= 1e-12, (kind, k)
            assert abs(float(m.gamma(-a[k] * axes[k])) - want) <= 1e-12
            assert float(vm.reduced_distance(m.structures[0], a[k] * axes[k])) == pytest.approx(1.0, abs=1e-15)
    sph = Model(0.0, (Structure("spherical", c, *a, *angles),))
    minor = np.array([0.0, 5.0, 19.99, 20.0, 20.01, 60.0, 500.0])[:, None] * axes[2]
    g = sph.gamma(minor)
    assert g[0] == 0.0 and np.all(np.diff(g[:4]) > 0) and g[2] < c - 1e-9  # below a3 — below the sill …
    assert np.abs(g[3:] - c).max() <= 1e-12  # … from a3 on — the sill
    assert float(sph.gamma(20.0 * axes[0])) < 0.3 * c and float(sph.gamma(60.0 * axes[0])) < 0.75 * c  # not along a1
    assert float(sph.gamma(119.9 * axes[0])) < c - 1e-9 and abs(float(sph.gamma(120.0 * axes[0])) - c) <= 1e-12


def test_reduced_distance_is_the_formula_of_8_6_3() -> None:
    """h_r = ‖diag(1, a1/a2, a1/a3)·R·Δ‖ / a1; isotropic — |Δ| / a."""
    rng = np.random.default_rng(863)
    for _ in range(100):
        ang = (float(rng.uniform(0, 360)), float(rng.uniform(-90, 90)), float(rng.uniform(-180, 180)))
        a = [float(x) for x in rng.uniform(5.0, 500.0, 3)]
        s = Structure("spherical", 1.0, *a, *ang)
        d = rng.normal(0.0, 150.0, (40, 3))
        want = np.linalg.norm(d @ rot.anisotropy_matrix(*ang, *a).T, axis=1) / a[0]
        assert np.allclose(vm.reduced_distance(s, d), want, rtol=1e-12, atol=0.0)
        iso = Structure("gaussian", 1.0, a[0], a[0], a[0], *ang)
        assert np.allclose(vm.reduced_distance(iso, d), np.linalg.norm(d, axis=1) / a[0], rtol=1e-12, atol=0.0)
    s = Structure("spherical", 1.0, 100.0, 50.0, 25.0)
    assert vm.reduced_distance(s, [[0.0, 100.0, 0.0], [-50.0, 0.0, 0.0], [0.0, 0.0, 25.0]]).tolist() == [1.0, 1.0, 1.0]
    assert vm.reduced_distance(s, np.zeros((2, 4, 3))).shape == (2, 4)
    with pytest.raises(ValueError):
        vm.reduced_distance(s, [1.0, 2.0])


def test_shapes_and_their_slopes() -> None:
    h = np.array([0.0, 1e-9, 0.25, 0.5, 0.999999, 1.0, 1.5, 40.0])
    assert vm.shape("spherical", h).tolist() == pytest.approx([0, 1.5e-9, 0.3671875, 0.6875, 1.0, 1, 1, 1], abs=1e-11)
    assert vm.shape("spherical", h)[5:].tolist() == [1.0, 1.0, 1.0] and vm.shape("spherical", 0.0) == 0.0
    assert np.allclose(vm.shape("exponential", h), 1.0 - np.exp(-3.0 * h), rtol=0, atol=1e-15)
    assert np.allclose(vm.shape("gaussian", h), 1.0 - np.exp(-3.0 * h * h), rtol=0, atol=1e-15)
    assert float(vm.shape("exponential", 1e-12)) == pytest.approx(3e-12, rel=1e-9)  # no loss of digits near zero
    assert float(vm.shape("gaussian", 1e-7)) == pytest.approx(3e-14, rel=1e-9)
    for kind in vm.TYPES:  # the slope is the derivative (central difference), 0 beyond the range of a sphere
        x = np.array([0.05, 0.3, 0.7, 0.95, 1.3, 2.5])
        num = (vm.shape(kind, x + 1e-6) - vm.shape(kind, x - 1e-6)) / 2e-6
        assert np.allclose(vm.shape_slope(kind, x), num, rtol=0, atol=1e-8), kind
        assert np.isnan(vm.shape(kind, np.nan)) and np.isnan(vm.shape_slope(kind, np.nan))
    assert vm.shape_slope("spherical", np.array([1.0, 2.0])).tolist() == [0.0, 0.0]
    with pytest.raises(StatsError):
        vm.shape("cubic", 0.5)


# --- the tests of the model of BF -------------------------------------------------------------------------------------


def test_model_basic_properties() -> None:
    """BF `test_model_basic_properties`."""
    m = vm.model_2d(0.1, [{"it": "spherical", "cc": 0.9, "hmaj": 30}])
    assert m.sill == pytest.approx(1.0)
    assert m.gamma_along(0.0) == 0.0 and m.cov_along(0.0) == pytest.approx(1.0)
    assert m.gamma_along(1e-9) == pytest.approx(0.1, abs=1e-6)  # the nugget jump
    assert m.gamma_along(30.0) == pytest.approx(1.0) and m.gamma_along(100.0) == pytest.approx(1.0)
    h = np.linspace(0.1, 29, 50)
    assert np.all(np.diff(m.gamma_along(h)) > 0)
    assert m.gamma([0.0, 0.0, 0.0]) == 0.0 and m.cov([0.0, 0.0, 0.0]) == m.sill


@pytest.mark.parametrize("it", ["exponential", "gaussian"])
def test_practical_range_is_95pct(it: str) -> None:
    """BF `test_practical_range_is_95pct`."""
    m = vm.model_2d(0.0, [{"it": it, "cc": 1.0, "hmaj": 20}])
    assert m.gamma_along(20.0) == pytest.approx(1 - np.exp(-3), rel=1e-9)
    assert m.gamma_along(20.0, 77.0, 30.0) == pytest.approx(E3, abs=1e-12)  # isotropic: any direction


def test_model_validation() -> None:
    """BF `test_model_validation`, the refusals as {code, message, hint} with the place of the fault."""
    for nug, structures, param in (
        (0, [{"it": "cubic", "cc": 1, "hmaj": 1}], "structures.0.it"),
        (0, [{"it": 1, "cc": 1, "hmaj": 0}], "structures.0.hmaj"),
        (-1, [], "nug"),
        (0, [{"it": 1, "cc": -0.1, "hmaj": 5}], "structures.0.cc"),
        (0, [{"it": 1, "cc": 1, "hmaj": 5}, {"it": 2, "cc": 1, "hmaj": 5, "hmin": -1}], "structures.1.hmin"),
        (0, [{"it": 1, "cc": 1, "hmaj": 5, "azi": math.inf}], "structures.0.azi"),
        (0, [{"it": 1, "cc": 1}], "structures.0.hmaj"),
        (math.nan, [], "nug"),
        (0, [{"it": 1, "cc": 1, "hmaj": 5}] * 5, "structures"),
        (0, "spherical", "structures"),
        (0, [7], "structures.0"),
    ):
        with pytest.raises(StatsError) as e:
            vm.model_2d(nug, structures)
        assert e.value.code == "bad_param" and e.value.param == param, param
        assert e.value.message and e.value.hint and set(e.value.as_dict()) == {"code", "message", "hint"}
    with pytest.raises(StatsError) as e:
        vm.model_2d(0, [{"it": 1, "cc": 1, "hmaj": 5}, {"it": 4, "cc": 1, "hmaj": 5}])
    assert e.value.message.startswith("Структура 2: Неизвестный тип структуры «4»") and "gaussian" in e.value.hint
    assert len(vm.model_2d(0, [{"it": 1, "cc": 1, "hmaj": 5}] * 4).structures) == 4  # the tab: up to 4 (ADR §11)


# --- GeostatsPy vmodel (Д-21) -----------------------------------------------------------------------------------------


def test_d21_vmodel_of_geostatspy() -> None:
    """ТЗ 7.35 Д-21: `vmodel` of GeostatsPy, ≤ 1e−9 (in fact 1e−15: the azimuth is along the major axes)."""
    ref = json.loads(D21.read_text(encoding="utf-8"))["vmodel"]
    v = ref["model"]
    assert v["nst"] == 2
    m = vm.model_2d(v["nug"], [{"it": v[f"it{i}"], "cc": v[f"cc{i}"], "azi": v[f"azi{i}"], "hmaj": v[f"hmaj{i}"],
                                "hmin": v[f"hmin{i}"]} for i in (1, 2)])  # fmt: skip
    _, h, gam, cov, ro = (np.array(x) for x in ref["result"])
    assert len(h) == ref["nlag"] + 1 and h[1] == ref["xlag"]
    g = m.gamma_along(h, ref["azm"])
    assert np.abs(g - gam).max() <= 1e-9 and np.abs(g - gam).max() <= 1e-14
    assert np.abs(m.cov_along(h, ref["azm"]) - cov).max() <= 1e-14
    assert np.abs(m.cov_along(h, ref["azm"]) / m.sill - ro).max() <= 1e-14 and g[0] == 0.0


def _vmodel_cases() -> list[dict[str, Any]]:
    return list(json.loads(VMODEL.read_text(encoding="utf-8"))["cases"])


def test_model_matches_legacy_vmodel_anisotropic() -> None:
    """BF `test_model_matches_legacy_vmodel_anisotropic` on stored numbers of GeostatsPy (+ a gaussian structure,
    azimuths beyond 180°). GeostatsPy truncates π to 9 digits (`known_defects`): with the lag vectors it really
    used and the azimuths of the structures it really applied the agreement is 1e−12; as given — 1e−8."""
    cases = _vmodel_cases()
    assert [c["id"] for c in cases] == ["bf_anisotropic", "gaussian_spherical", "exponential_isotropic"]
    bf = cases[0]  # the model of the BF test
    assert bf["nug"] == 0.1 and [r["azm"] for r in bf["runs"]] == [0.0, 30.0, 75.0, 120.0]
    assert bf["structures"] == [{"it": 1, "cc": 0.5, "azi": 30, "hmaj": 40, "hmin": 15},
                                {"it": 2, "cc": 0.4, "azi": 120, "hmaj": 80, "hmin": 50}]  # fmt: skip
    squeeze = 3.14159265 / math.pi
    worst = raw = 0.0
    for case in cases:
        m = vm.model_2d(case["nug"], case["structures"])
        applied = vm.model_2d(
            case["nug"], [{**s, "azi": 90.0 - (90.0 - s["azi"]) * squeeze} for s in case["structures"]]
        )
        for run in case["runs"]:
            h, gam, cov = (np.array(run[k]) for k in ("h", "gamma", "cov"))
            d = np.column_stack([run["dx"], run["dy"], np.zeros(len(h))])
            assert gam[0] == 0.0 and cov[0] == pytest.approx(m.sill, abs=1e-15)
            worst = max(worst, float(np.abs(applied.gamma(d) - gam).max()), float(np.abs(applied.cov(d) - cov).max()))
            # As the BF test: the model at the distance h along the azimuth (BF: atol 1e−6).
            raw = max(raw, float(np.abs(m.gamma_along(h, run["azm"]) - gam).max()),
                      float(np.abs(m.cov_along(h[1:], run["azm"]) - cov[1:]).max()))  # fmt: skip
    assert worst <= 1e-12 and raw <= 1e-8, (worst, raw)


def test_2d_model_is_the_formula_of_bf() -> None:
    """The model of the tab «Статистика» (ADR 0022 §6, §11): ang1 = azi, a1 = hmaj, a2 = a3 = hmin."""
    rng = np.random.default_rng(29)
    for _ in range(60):
        n = int(rng.integers(0, 5))
        structures = [{"it": int(rng.integers(1, 4)), "cc": float(rng.uniform(0, 2)),
                       "azi": float(rng.uniform(-90, 400)), "hmaj": float(rng.uniform(5, 200)),
                       "hmin": float(rng.uniform(5, 200))} for _ in range(n)]  # fmt: skip
        nug = float(rng.uniform(0, 1))
        m = vm.model_2d(nug, structures)
        dx, dy = rng.normal(0, 80, 200), rng.normal(0, 80, 200)
        dx[:3], dy[:3] = 0.0, 0.0
        d = np.column_stack([dx, dy, np.zeros(200)])
        assert np.abs(m.gamma(d) - bf_gamma_xy(nug, structures, dx, dy)).max() <= 1e-12 * max(1.0, m.sill)
        assert m.sill == pytest.approx(nug + sum(s["cc"] for s in structures), abs=1e-14)
    m = vm.model_2d(0.1, [{"it": "gaussian", "cc": 0.9, "hmaj": 40, "hmin": 15, "azi": 30}])
    s = m.structures[0]
    assert (s.type, s.c, s.ranges, s.angles) == ("gaussian", 0.9, (40.0, 15.0, 15.0), (30.0, 0.0, 0.0))
    assert vm.model_2d(0.0, [{"it": 2, "cc": 1, "hmaj": 7}]).structures[0].ranges == (7.0, 7.0, 7.0)  # hmin = hmaj
    assert vm.model_2d(0.2).structures == () and vm.model_2d(0.2).gamma_along([0.0, 3.0]).tolist() == [0.0, 0.2]


# --- the model as an object -------------------------------------------------------------------------------------------

THREE = {
    "c0": 0.05,
    "structures": [
        {"type": "spherical", "c": 0.25, "ang1": 30.0, "ang2": -20.0, "ang3": 40.0, "a1": 40.0, "a2": 25.0, "a3": 8.0},
        {"type": "exponential", "c": 0.3, "ang1": 30.0, "ang2": -20.0, "ang3": 40.0, "a1": 90.0, "a2": 60.0,
         "a3": 15.0},
        {"type": "gaussian", "c": 0.4, "ang1": 30.0, "ang2": -20.0, "ang3": 40.0, "a1": 220.0, "a2": 130.0,
         "a3": 30.0},
    ],
    "common_axes": True,
}  # fmt: skip


def test_gamma_and_covariance_of_lag_vectors() -> None:
    m = vm.parse_model(THREE)
    assert m.sill == 0.05 + 0.25 + 0.3 + 0.4 and len(m.structures) == 3
    rng = np.random.default_rng(6)
    d = rng.normal(0.0, 60.0, (5, 7, 3))
    g = m.gamma(d)
    assert g.shape == (5, 7) and g.dtype == np.float64 and np.all((g > 0.05) & (g < m.sill))
    assert np.array_equal(m.gamma(-d), g)  # γ(−Δ) = γ(Δ), bit for bit
    assert np.array_equal(m.cov(d), m.sill - g) and np.all(m.cov(d) >= 0.0)
    want = 0.05 + sum(s.c * vm.shape(s.type, vm.reduced_distance(s, d)) for s in m.structures)
    assert np.allclose(g, want, rtol=0, atol=1e-15)
    # γ(0) = 0 and C(0) is the sill; next to zero — the nugget.
    assert m.gamma(np.zeros(3)) == 0.0 and m.cov(np.zeros(3)) == m.sill and m.gamma(np.zeros(3)).shape == ()
    assert m.gamma([[0.0, 0.0, 0.0], [0.0, 1e-9, 0.0], [-0.0, 0.0, -0.0]]).tolist() == pytest.approx(
        [0.0, 0.05, 0.0], abs=1e-9
    )
    # A missing lag is missing, not zero and not the nugget.
    gaps = m.gamma([[np.nan, 0.0, 0.0], [1.0, np.nan, 2.0], [0.0, 0.0, np.nan], [3.0, 4.0, 0.0]])
    assert np.isnan(gaps[:3]).all() and np.isfinite(gaps[3]) and np.isnan(m.cov([np.nan, 0.0, 0.0]))
    assert np.isnan(Model(0.3).gamma([np.nan, 0.0, 0.0])) and Model(0.3).gamma([[1.0, 0.0, 0.0]]).tolist() == [0.3]
    # Far away — the sill of the bounded part and almost the sill of the others.
    assert m.gamma([1e6, 0.0, 0.0]) == pytest.approx(m.sill, abs=1e-12)
    # Along a direction: Δ = h·u.
    h = np.array([0.0, 10.0, 55.5, 300.0])
    assert np.array_equal(m.gamma_along(h, 123.0, 35.0), m.gamma(h[:, None] * rot.direction(123.0, 35.0)))
    assert np.array_equal(m.gamma_along(h, 30.0, 20.0), m.gamma(h[:, None] * m.structures[0].axes[0]))  # the axis a1
    assert m.gamma_along(-h, 30.0, 20.0).tolist() == m.gamma_along(h, 30.0, 20.0).tolist()
    assert (
        m.gamma_along(5.0).shape == ()
        and m.cov_along(h, 1.0, 2.0).tolist() == (m.sill - m.gamma_along(h, 1.0, 2.0)).tolist()
    )


def test_parse_and_as_dict() -> None:
    m = vm.parse_model(THREE)
    assert m.as_dict() == THREE and vm.parse_model(m.as_dict()) == m
    assert json.loads(json.dumps(m.as_dict())) == THREE  # plain numbers: goes to `meta` as it is
    # Defaults: a2 = a3 = a1, the angles 0, C0 = 0; the codes of GSLIB; the angles are normalised.
    second = {"type": "3", "c": "0.5", "a1": 80, "a3": 10, "ang1": -30, "ang3": 540}
    short = vm.parse_model({"structures": [{"type": 2, "c": 1, "a1": 50}, second]})
    assert short.c0 == 0.0 and short.structures[0] == Structure("exponential", 1.0, 50.0, 50.0, 50.0)
    assert short.structures[1].as_dict() == {"type": "gaussian", "c": 0.5, "ang1": 330.0, "ang2": 0.0, "ang3": 180.0,
                                             "a1": 80.0, "a2": 80.0, "a3": 10.0}  # fmt: skip
    assert short.common_axes is False  # not said: common when the angles are equal
    assert vm.parse_model({"c0": 1}).as_dict() == {"c0": 1.0, "structures": [], "common_axes": True}
    assert vm.parse_model({"c0": None, "structures": None}).sill == 0.0
    # «Общие оси»: the angles of the first structure are the angles of all.
    own = {**THREE, "common_axes": False, "structures": [dict(s) for s in THREE["structures"]]}  # type: ignore[call-overload]
    own["structures"][1].update(ang1=75.0, ang2=10.0)
    assert vm.parse_model(own).structures[1].angles == (75.0, 10.0, 40.0)
    assert vm.parse_model({**own, "common_axes": True}).structures[1].angles == (30.0, -20.0, 40.0)
    assert vm.parse_model({**own, "common_axes": True}) == m
    # a2 > a1 and c = 0 are allowed (ADR 0022 §16).
    odd = Model(0.0, (Structure("spherical", 0.0, 10.0, 40.0, 5.0),))
    assert odd.sill == 0.0 and odd.gamma([[0.0, 3.0, 0.0]]).tolist() == [0.0]
    assert Structure(1, 1, 10, 20, 30).type == "spherical" and Structure("GAUSSIAN ", 1, 10, 20, 30).type == "gaussian"


def test_model_refusals_name_the_parameter() -> None:
    ok = {"type": "spherical", "c": 1.0, "a1": 10.0}
    for obj, param in (
        ({"c0": -0.1}, "c0"),
        ({"c0": "x"}, "c0"),
        ({"c0": True}, "c0"),
        ({"structures": [ok, {**ok, "type": "power"}]}, "structures.1.type"),
        ({"structures": [{**ok, "c": -1}]}, "structures.0.c"),
        ({"structures": [{**ok, "a1": 0}]}, "structures.0.a1"),
        ({"structures": [{**ok, "a2": math.nan}]}, "structures.0.a2"),
        ({"structures": [ok, ok, {**ok, "a3": -5}]}, "structures.2.a3"),
        ({"structures": [{**ok, "ang2": 95}]}, "structures.0.ang2"),
        ({"structures": [{**ok, "ang1": "north"}]}, "structures.0.ang1"),
        ({"structures": [{"type": "spherical", "c": 1.0}]}, "structures.0.a1"),
        ({"structures": [{"c": 1.0, "a1": 5}]}, "structures.0.type"),
        ({"structures": [ok] * 4}, "structures"),
        ({"structures": {"type": "spherical"}}, "structures"),
        ({"structures": ["spherical"]}, "structures.0"),
        ({"structures": [ok], "common_axes": "yes"}, "common_axes"),
        ([ok], "model"),
        (None, "model"),
    ):
        with pytest.raises(StatsError) as e:
            vm.parse_model(obj)
        assert e.value.code == "bad_param" and e.value.param == param, (obj, e.value.param)
        assert e.value.message and e.value.hint
    assert len(vm.parse_model({"structures": [ok] * 4}, max_structures=4).structures) == 4
    with pytest.raises(StatsError) as e:
        vm.parse_model({"structures": [ok, {**ok, "a1": -3}]})
    assert e.value.message == "Структура 2: Дальность a1: значение должно быть больше нуля."
    for make in (lambda: Structure("spherical", 1.0, 0.0, 1.0, 1.0), lambda: Model(-1.0), lambda: Model(0.0, [3])):
        with pytest.raises(StatsError):
            make()


def test_no_numba_is_imported_with_the_model() -> None:
    """The rotation and the model are plain numpy: nothing heavy before a kernel is needed (ADR 0002)."""
    code = (
        "import sys; import bf_core.geostat.rotation, bf_core.geostat.variogram.model as m;"
        "m.parse_model({'c0': 0.1, 'structures': [{'type': 1, 'c': 1, 'a1': 5}]}).gamma_along([1.0, 2.0], 30, 10);"
        "heavy=[x for x in ('numba','scipy','pandas','pyarrow','duckdb','sqlalchemy','fastapi') if x in sys.modules];"
        "print(heavy)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# --- the shared answers -----------------------------------------------------------------------------------------------

RULES = [
    "model = {c0, structures: [{type, c, ang1, ang2, ang3, a1, a2, a3}], common_axes} (ADR 0022, 12): c0 - the"
    " nugget, c - the contribution, a1..a3 - the ranges along the axes of the structure, angles of GSLIB in degrees."
    " With common_axes the angles of every structure are those of the first.",
    "Axes of a structure: the rows u1, u2, u3 of R(ang1, ang2, ang3) - frames.json `rules` (sin/cos of degrees exact"
    " at the multiples of 90).",
    "Reduced distance of the lag vector d = (x, y, z) in a structure: q_i = (x*u_i.x + y*u_i.y + z*u_i.z) / a_i"
    " (the sum left to right), hr = sqrt(q1*q1 + q2*q2 + q3*q3).",
    "Shapes g(hr): spherical - hr*(1.5 - 0.5*hr*hr) when hr < 1, else 1; exponential - 1 - exp(-3*hr), computed as"
    " -expm1(-3*hr); gaussian - -expm1(-3*hr*hr). The ranges are practical: g(1) = 1 - exp(-3) for the last two.",
    "gamma(d) = 0 when d = (0, 0, 0); otherwise c0 + c_1*g_1(hr_1) + c_2*g_2(hr_2) + ... (left to right)."
    " sill = c0 + c_1 + c_2 + ... (left to right); cov(d) = sill - gamma(d).",
    "curves: gamma at d = (h*u.x, h*u.y, h*u.z) for every h; `axis` 1..3 - u is the axis of the first"
    " structure (of zero angles when there is none), null - the direction (cos p*sin Az, cos p*cos Az, -sin p) of"
    " `azimuth` Az and `plunge` p (positive down). The second h is 1e-6: the nugget jump. points: single lag vectors"
    " `delta` with hr of every structure, gamma and cov.",
    "form2d (the model of the tab «Статистика», as BF): {nug, structures: [{it, cc, hmaj, hmin, azi}]} is the model"
    " with c0 = nug and type = it (1 - spherical, 2 - exponential, 3 - gaussian), c = cc, ang1 = azi (reduced to"
    " [0, 360)), ang2 = ang3 = 0, a1 = hmaj, a2 = a3 = hmin; common_axes - whether all azi are equal.",
    "Tolerances: gamma and cov - 1e-12*max(1, sill); hr - 1e-12 relative.",
]

CURVE_CASES: list[dict[str, Any]] = [
    {"id": "t864-spherical", "title": "Т8.6-4: сферическая, порог на дальности каждой оси",
     "model": {"c0": 0.0, "structures": [{"type": "spherical", "c": 0.8, "a1": 120.0, "a2": 60.0, "a3": 20.0}]}},
    {"id": "t864-exponential", "title": "Т8.6-4: экспоненциальная, c·(1 − e^(−3)) на дальности",
     "model": {"c0": 0.0, "structures": [{"type": "exponential", "c": 0.8, "a1": 120.0, "a2": 60.0, "a3": 20.0}]}},
    {"id": "t864-gaussian", "title": "Т8.6-4: гауссова, оси повёрнуты",
     "model": {"c0": 0.0, "structures": [{"type": "gaussian", "c": 0.8, "ang1": 30.0, "ang2": -20.0, "ang3": 40.0,
                                          "a1": 120.0, "a2": 60.0, "a3": 20.0}]}},
    {"id": "three", "title": "Самородок и три структуры, общие оси", "model": THREE},
    {"id": "own-axes", "title": "Три структуры со своими осями",
     "model": {"c0": 0.1, "common_axes": False, "structures": [
         {"type": "exponential", "c": 0.2, "ang1": 75.0, "ang2": 10.0, "ang3": 0.0, "a1": 30.0, "a2": 30.0,
          "a3": 12.0},
         {"type": "spherical", "c": 0.3, "ang1": 200.0, "ang2": 35.0, "ang3": -70.0, "a1": 80.0, "a2": 45.0,
          "a3": 20.0},
         {"type": "spherical", "c": 0.4, "ang1": 300.0, "ang2": -55.5, "ang3": 110.0, "a1": 150.0, "a2": 150.0,
          "a3": 40.0}]}},
    {"id": "variance", "title": "Порог в единицах дисперсии, a2 больше a1, структура с нулевым вкладом",
     "model": {"c0": 40.0, "structures": [
         {"type": "spherical", "c": 0.0, "ang1": 90.0, "ang2": 0.0, "ang3": 0.0, "a1": 25.0, "a2": 25.0, "a3": 25.0},
         {"type": "gaussian", "c": 210.0, "ang1": 90.0, "ang2": 0.0, "ang3": 0.0, "a1": 300.0, "a2": 450.0,
          "a3": 55.0}]}},
    {"id": "vertical", "title": "Большая ось вертикальна",
     "model": {"c0": 0.2, "structures": [{"type": "spherical", "c": 0.8, "ang1": 40.0, "ang2": -90.0, "ang3": 25.0,
                                          "a1": 60.0, "a2": 45.0, "a3": 30.0}]}},
    {"id": "nugget", "title": "Чистый самородок", "model": {"c0": 1.0, "structures": []}},
    {"id": "tab-2d", "title": "Модель вкладки «Статистика» (форма BF), структуры с разными азимутами",
     "form2d": {"nug": 0.1, "structures": [{"it": 1, "cc": 0.5, "azi": 30.0, "hmaj": 40.0, "hmin": 15.0},
                                           {"it": 2, "cc": 0.4, "azi": 120.0, "hmaj": 80.0, "hmin": 50.0}]}},
]  # fmt: skip
OBLIQUE = [(90.0, 0.0), (123.0, 35.0), (310.0, -15.0)]
POINTS = [(0.0, 0.0, 0.0), (0.0, 1e-9, 0.0), (10.0, 0.0, 0.0), (0.0, -10.0, 0.0), (0.0, 0.0, 10.0),
          (12.5, -40.0, 7.25), (-60.0, 85.0, -20.0), (250.0, 250.0, 100.0), (1e-3, -2e-3, 5e-4)]  # fmt: skip


def _model_of(case: dict[str, Any]) -> Model:
    if "form2d" in case:
        return vm.model_2d(case["form2d"]["nug"], case["form2d"]["structures"])
    return vm.parse_model(case["model"])


def _distances(m: Model) -> list[float]:
    """0, next to 0, 20 steps to one and a half of the largest range, and every range itself."""
    ranges = sorted({a for s in m.structures for a in s.ranges}) or [10.0]
    grid = [ranges[-1] * 1.5 * k / 20.0 for k in range(1, 21)]
    return sorted({0.0, 1e-6, *grid, *ranges})


def build() -> dict[str, Any]:
    """The shared answers, by the core."""
    cases = []
    for spec in CURVE_CASES:
        m = _model_of(spec)
        h = np.array(_distances(m))
        axes = m.structures[0].axes if m.structures else rot.rotation_matrix(0.0)
        curves = []
        for axis, az, p in [(1, None, None), (2, None, None), (3, None, None), *((None, a, b) for a, b in OBLIQUE)]:
            u = axes[axis - 1] if axis else rot.direction(az, p)
            d = h[:, None] * u
            curves.append({"axis": axis, "azimuth": az, "plunge": p, "u": [float(x) for x in u], "h": h.tolist(),
                           "gamma": m.gamma(d).tolist()})  # fmt: skip
        points = [{"delta": list(d), "hr": [float(vm.reduced_distance(s, d)) for s in m.structures],
                   "gamma": float(m.gamma(d)), "cov": float(m.cov(d))} for d in POINTS]  # fmt: skip
        case = {"id": spec["id"], "title": spec["title"], "model": m.as_dict(), "sill": m.sill}
        if "form2d" in spec:
            case["form2d"] = spec["form2d"]
        cases.append({**case, "curves": curves, "points": points})
    return {
        "about": "Общие эталоны модели вариограммы (ТЗ 8.6.3; ADR 0022 §6; ТД14-3): ядро —"
        " bf_core/geostat/variogram/model.py (tests/test_vario_model.py), окно — ui/src/modules/variography/model.ts",
        "rules": "\n".join(RULES),
        "tolerance": {"gamma": 1e-12, "hr": 1e-12},
        "cases": cases,
    }


def _dump(obj: Any) -> str:
    """JSON with every list of numbers on one line."""
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    return re.sub(r"\[\s+((?:-?[\d.e+-]+,?\s*)+)\]", lambda m: "[" + " ".join(m.group(1).split()) + "]", text) + "\n"


def _close(a: Any, b: Any, tol: float) -> bool:
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(_close(a[k], b[k], tol) for k in a)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b, strict=True))
    if isinstance(a, bool | str) or a is None:
        return bool(a == b)
    return isinstance(b, int | float) and not isinstance(b, bool) and abs(float(a) - float(b)) <= tol * max(1.0, abs(a))


def test_shared_answers_are_what_the_core_computes() -> None:
    built = build()
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(_dump(built), encoding="utf-8")
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    assert stored["rules"] == built["rules"] and stored["tolerance"] == built["tolerance"]
    assert [c["id"] for c in stored["cases"]] == [c["id"] for c in CURVE_CASES]
    assert _close(stored, built, 1e-12)  # exp of libm may differ in the last bit between platforms
    for case in stored["cases"]:
        m = vm.parse_model(case["model"], max_structures=4)
        assert m.as_dict() == case["model"] and m.sill == case["sill"]
        tol = 1e-12 * max(1.0, m.sill)
        for curve in case["curves"]:  # what the window checks: γ(h·u) of the model of the file
            h, u = np.array(curve["h"]), np.array(curve["u"])
            assert h[0] == 0.0 and curve["gamma"][0] == 0.0 and h[1] == 1e-6
            assert np.abs(m.gamma(h[:, None] * u) - curve["gamma"]).max() <= tol, case["id"]
            assert curve["gamma"][1] == pytest.approx(m.c0, abs=1e-6 * max(1.0, m.sill))  # the nugget jump
            assert abs(float(np.linalg.norm(u)) - 1.0) <= 1e-15 and all(math.isfinite(x) for x in curve["gamma"])
            if curve["axis"] is None:
                assert np.array_equal(u, rot.direction(curve["azimuth"], curve["plunge"]))
        for point in case["points"]:
            assert abs(float(m.gamma(point["delta"])) - point["gamma"]) <= tol and len(point["hr"]) == len(m.structures)
            assert abs(point["gamma"] + point["cov"] - m.sill) <= tol


def test_shared_answers_hold_the_acceptance_numbers() -> None:
    """Т8.6-4 as the window reads it: on the curve of axis k the value at h = a_k."""
    stored = {c["id"]: c for c in json.loads(DATA.read_text(encoding="utf-8"))["cases"]}
    for ident, want in (("t864-spherical", 0.8), ("t864-exponential", 0.8 * E3), ("t864-gaussian", 0.8 * E3)):
        case = stored[ident]
        s = case["model"]["structures"][0]
        for k in (1, 2, 3):
            curve = next(c for c in case["curves"] if c["axis"] == k)
            at = curve["h"].index(s[f"a{k}"])
            assert curve["gamma"][at] == pytest.approx(want, abs=1e-12), (ident, k)
    minor = next(c for c in stored["t864-spherical"]["curves"] if c["axis"] == 3)
    assert all(g == pytest.approx(0.8, abs=1e-12) for h, g in zip(minor["h"], minor["gamma"], strict=True) if h >= 20.0)
    assert stored["tab-2d"]["model"]["common_axes"] is False and stored["tab-2d"]["sill"] == pytest.approx(1.0)
    assert stored["tab-2d"]["model"]["structures"][1] == {"type": "exponential", "c": 0.4, "ang1": 120.0, "ang2": 0.0,
                                                          "ang3": 0.0, "a1": 80.0, "a2": 50.0, "a3": 50.0}  # fmt: skip
    assert stored["nugget"]["curves"][0]["gamma"][:3] == [0.0, 1.0, 1.0]
