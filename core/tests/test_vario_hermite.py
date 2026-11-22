"""The back-transform of a model of normal scores — the Hermite expansion (ТЗ 8.6.3; ADR 0022 §8; e16-api.md
`POST /variography/back`; Э16, 16.9).

Т8.6-6: Z = e^Y analytically — φ_n = e^½ / √n!, C_Z(ρ) = e·(e^ρ − 1) at ρ = 0,3; 0,7; 0,95 (1e−6 relative; the
quadrature of 200 nodes gives ~1e−15), Var(Z) = e² − e = 4,670774270; by a sample of 10⁵ values through the table
of normal scores (`stats.transforms.nscore`, the tails of `backtr`) — ≤ 2 %. Then the model of Z: a linear Z is
restored exactly (the same model × b²), the nugget of Z is exact, the curves of the three axes, the share and its
warning on a heavy tail, ties (a detection limit), weights and tails, the refusals {code, message, hint, param},
the gaps (NaN stays NaN), the bits of two runs, `tick`.
"""

from __future__ import annotations

import json
import math
import time

import numpy as np
import pytest
from numpy.polynomial.hermite_e import hermeval
from scipy.stats import norm

from bf_core.geostat.variogram import hermite as vh
from bf_core.geostat.variogram.exchange import model_form
from bf_core.geostat.variogram.model import Model, Structure
from bf_core.stats.transforms import nscore
from bf_core.stats.weighted import StatsError

E = math.e
VAR_LOGNORMAL = E * E - E  # Var(e^Y), Y ~ N(0, 1)
RHOS = (0.3, 0.7, 0.95)
SEED = 12345  # the seed of the project (`spec.SEED`)


def c_lognormal(rho: float) -> float:
    return E * math.expm1(rho)


def model_y(*, c0: float = 0.1, angles: tuple[float, float, float] = (30.0, -10.0, 20.0)) -> Model:
    """A model of normal scores: the sill 1, two nested structures, anisotropic."""
    return Model(
        c0,
        (
            Structure("spherical", 0.5, 40.0, 25.0, 8.0, *angles),
            Structure("exponential", 0.5 - c0, 150.0, 90.0, 20.0, *angles),
        ),
    )


def relerr(a: float, b: float) -> float:
    return abs(a - b) / abs(b)


def bits_of(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, default=float)


# --- the polynomials and the quadrature ---------------------------------------------------------------------------


def test_polynomials_are_he_over_sqrt_factorial() -> None:
    y = np.linspace(-6.0, 6.0, 25)
    h = vh.hermite(y, 12)
    assert h.shape == (13, 25)
    for n in range(13):
        coef = np.zeros(n + 1)
        coef[n] = 1.0
        ref = hermeval(y, coef) / math.sqrt(math.factorial(n))
        assert np.allclose(h[n], ref, rtol=1e-12, atol=1e-12)


def test_quadrature_orthonormal() -> None:
    x, w = vh.quadrature()
    assert x.size == vh.NODES == 200
    assert math.isclose(float(w.sum()), 1.0, rel_tol=0.0, abs_tol=1e-15)
    h = vh.hermite(x, vh.TERMS)
    gram = (h * w) @ h.T
    assert np.max(np.abs(gram - np.eye(vh.TERMS + 1))) < 1e-12


# --- Т8.6-6 ---------------------------------------------------------------------------------------------------------


def test_t866_lognormal_analytic() -> None:
    ana = vh.anamorphosis(np.exp)
    assert ana.terms == 30 and ana.nodes == 200
    phi_ref = np.array([math.exp(0.5) / math.sqrt(math.factorial(n)) for n in range(31)])
    assert np.max(np.abs(ana.phi - phi_ref)) < 1e-14  # absolute: φ_30 ~ 1e−16 is at the rounding of φ_0
    assert abs(ana.var_z - 4.670774270) < 1e-9
    assert relerr(ana.var_z, VAR_LOGNORMAL) < 1e-12
    assert relerr(ana.mean, math.exp(0.5)) < 1e-14
    for rho in RHOS:
        got = float(ana.cov(rho))
        assert relerr(got, c_lognormal(rho)) < 1e-6  # Т8.6-6
        assert relerr(got, c_lognormal(rho)) < 1e-12  # what the quadrature gives
        g = float(ana.gamma(1.0 - rho))  # γ_Z = C_Z(0) − C_Z(ρ)
        assert relerr(g, E * (E - math.exp(rho))) < 1e-12
    assert relerr(ana.sill, VAR_LOGNORMAL) < 1e-12  # 30 terms: the rest is e·Σ_(n>30) 1/n! ~ 1e−33
    assert 1.0 - 1e-12 < ana.share <= 1.0 + 1e-15


def test_t866_sample_through_the_table() -> None:
    """10⁵ values e^Y, the table of normal scores with the tails of `backtr` (flat to the extremes)."""
    z = np.exp(np.random.default_rng(SEED).standard_normal(100_000))
    _, table = nscore(z)
    ana = vh.anamorphosis(table)
    for rho in RHOS:
        assert relerr(float(ana.cov(rho)), c_lognormal(rho)) <= 0.02  # Т8.6-6: ≤ 2 %
    assert relerr(ana.var_z, VAR_LOGNORMAL) <= 0.02
    assert relerr(ana.var_z, float(np.var(z))) <= 0.002  # the expansion keeps the variance of the data
    assert ana.share > 0.999


def test_quantile_sample_isolates_the_table() -> None:
    """The quantiles of e^Y (no sampling noise): only the interpolation and the flat tails differ, < 1 %."""
    n = 100_000
    z = np.exp(norm.ppf((np.arange(n) + 0.5) / n))
    _, table = nscore(z)
    ana = vh.anamorphosis(table)
    for rho in RHOS:
        assert relerr(float(ana.cov(rho)), c_lognormal(rho)) < 0.01
    assert relerr(ana.var_z, float(np.var(z))) < 0.002


# --- the model of Z -------------------------------------------------------------------------------------------------


def test_linear_z_restores_the_model_scaled() -> None:
    """Z = 3 + 2Y: φ_1 = 2, the rest 0 — γ_Z = 4·γ_Y exactly; the fit gives back the same model × 4."""
    my = model_y()
    ana = vh.anamorphosis(lambda y: 3.0 + 2.0 * y)
    assert abs(ana.phi[1] - 2.0) < 1e-13 and np.max(np.abs(ana.phi[2:])) < 1e-12
    assert abs(ana.share - 1.0) < 1e-12
    back = vh.back_transform(my, ana)
    assert back.warnings == []
    mz = back.model
    assert relerr(mz.c0, 4.0 * my.c0) < 1e-13
    assert len(mz.structures) == 2
    for sz, sy in zip(mz.structures, sorted(my.structures, key=lambda s: s.a1), strict=True):
        assert sz.type == sy.type and sz.angles == sy.angles
        assert relerr(sz.c, 4.0 * sy.c) < 1e-6
        for a, b in zip(sz.ranges, sy.ranges, strict=True):
            assert relerr(a, b) < 1e-6
    assert back.residual < 1e-12
    assert relerr(mz.sill, 4.0) < 1e-12


def test_lognormal_curves_nugget_and_sill_exact() -> None:
    my = model_y(c0=0.2)
    ana = vh.anamorphosis(np.exp)
    back = vh.back_transform(my, ana)
    # the nugget of Z: γ_Z just above 0 = e·(e − e^(1 − C0))
    assert relerr(back.model.c0, E * (E - math.exp(1.0 - 0.2))) < 1e-12
    assert relerr(back.model.sill, VAR_LOGNORMAL) < 1e-12
    axes = my.structures[0].axes
    assert [c.axis for c in back.curves] == [1, 2, 3]
    for c, top in zip(back.curves, (150.0, 90.0, 20.0), strict=True):
        assert c.h.size == vh.CURVE_POINTS == 100
        assert relerr(float(c.h[-1]), 1.5 * top) < 1e-12 and relerr(float(c.h[0]), 1.5 * top / 100) < 1e-12
        gy = my.gamma(c.h[:, None] * axes[c.axis - 1])
        ref = E * (E - np.exp(1.0 - gy))
        assert np.max(np.abs(c.gamma_z - ref) / ref) < 1e-12
        assert np.allclose(c.gamma_fit, back.model.gamma(c.h[:, None] * axes[c.axis - 1]), rtol=0, atol=0)
    rms = math.sqrt(back.residual / back.n) / back.model.sill
    assert rms < 0.02  # the model of the same types approximates the curves within 2 % of the sill


def test_three_structures_fast_and_close() -> None:
    angles = (75.0, 15.0, -30.0)
    my = Model(0.05, (Structure("gaussian", 0.25, 30.0, 20.0, 10.0, *angles),
                      Structure("spherical", 0.4, 90.0, 60.0, 15.0, *angles),
                      Structure("exponential", 0.3, 250.0, 120.0, 40.0, *angles)))  # fmt: skip
    _, table = nscore(np.exp(1.3 * np.random.default_rng(SEED).standard_normal(20_000)))
    t0 = time.perf_counter()
    back = vh.back_transform(my, vh.anamorphosis(table))
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0
    mz = back.model
    assert [s.type for s in mz.structures] == ["gaussian", "spherical", "exponential"]
    assert all(s.angles == mz.structures[0].angles for s in mz.structures)
    assert all(s.c >= 0.0 for s in mz.structures) and mz.c0 >= 0.0
    assert math.sqrt(back.residual / back.n) / mz.sill < 0.02
    worst = max(float(np.max(np.abs(c.gamma_fit - c.gamma_z))) for c in back.curves)
    assert worst / mz.sill < 0.05


def test_non_common_axes() -> None:
    my = Model(0.0, (Structure("spherical", 0.4, 50.0, 30.0, 10.0, 0.0, 0.0, 0.0),
                     Structure("spherical", 0.6, 200.0, 100.0, 30.0, 45.0, -20.0, 0.0)), common_axes=False)  # fmt: skip
    back = vh.back_transform(my, vh.anamorphosis(np.exp))
    assert back.model.common_axes is False
    assert back.model.structures[1].angles == (45.0, -20.0, 0.0)
    assert back.model.c0 == 0.0
    assert math.sqrt(back.residual / back.n) / back.model.sill < 0.03


# --- difficult data -------------------------------------------------------------------------------------------------


def test_heavy_tail_share_below_095_warns() -> None:
    """One outlier 1000 × the largest value: the 30 terms keep < 95 % of Var(Z) — a warning, the model is given."""
    rng = np.random.default_rng(0)
    z = rng.lognormal(0.0, 1.0, 1000)
    z[np.argmax(z)] *= 1000.0
    _, table = nscore(z)
    ana = vh.anamorphosis(table)
    assert 0.5 < ana.share < 0.95
    assert ana.share <= 1.0
    back = vh.back_transform(model_y(), ana)
    assert len(back.warnings) == 1 and "95 %" in back.warnings[0] and "30 членов" in back.warnings[0]
    answer = back.answer()
    assert answer["warning"] == back.warnings[0]
    assert answer["share"] == ana.share
    assert all(math.isfinite(s.c) for s in back.model.structures)
    assert relerr(back.model.sill, ana.sill) < 1e-12  # the sill of Z — C_Z(0), not Var(Z)


def test_ties_of_a_detection_limit() -> None:
    """Half the values at the detection limit: Φ_Z is flat on the lower half, the expansion still holds."""
    rng = np.random.default_rng(SEED)
    z = rng.lognormal(0.0, 1.0, 5000)
    z[: z.size // 2] = 0.005
    _, table = nscore(z)
    ana = vh.anamorphosis(table)
    assert ana.share > 0.95
    # 200 nodes on a steep top segment: −1,3 % from the exact Var(Φ_Z(Y)), which is −1,2 % from the data
    assert relerr(ana.var_z, float(np.var(z))) < 0.05
    back = vh.back_transform(model_y(), ana)
    assert back.warnings == []
    for c in back.curves:
        assert np.all(np.diff(c.gamma_z) >= -1e-12)  # γ_Z does not decrease along an axis
        assert np.all(np.isfinite(c.gamma_fit))


def test_weights_and_tails_of_the_table() -> None:
    rng = np.random.default_rng(3)
    z = rng.lognormal(0.0, 1.0, 400)
    w = rng.uniform(0.5, 2.0, z.size)
    _, plain = nscore(z)
    _, weighted = nscore(z, w, zmax=float(z.max()) * 3.0)
    a, b = vh.anamorphosis(plain), vh.anamorphosis(weighted)
    assert not np.array_equal(a.phi, b.phi)
    linear = vh.anamorphosis(weighted, utail="linear")
    hyper = vh.anamorphosis(weighted, utail="hyperbolic", utpar=1.5)
    assert linear.var_z != hyper.var_z and hyper.share <= 1.0 + 1e-15
    with pytest.raises(StatsError) as e:
        vh.anamorphosis(weighted, utail="power")  # no utpar
    assert e.value.code == "bad_param" and e.value.param == "utpar"


def test_sill_of_y_not_one_warns_and_out_of_range_refused() -> None:
    ana = vh.anamorphosis(np.exp)
    s = Structure("spherical", 1.0, 100.0, 100.0, 100.0)
    back = vh.back_transform(Model(0.1, (s,)), ana)
    assert len(back.warnings) == 1 and "не 1" in back.warnings[0]
    zero = Model(0.0, (Structure("spherical", 0.0, 10.0, 10.0, 10.0),))
    above = Model(0.5, (Structure("spherical", 1.6, 10.0, 10.0, 10.0),))  # the sill 2,1 > 2
    for bad in (zero, above):
        with pytest.raises(StatsError) as e:
            vh.back_transform(bad, ana)
        assert e.value.code == "bad_param" and e.value.param == "model" and e.value.hint


def test_pure_nugget() -> None:
    ana = vh.anamorphosis(np.exp)
    back = vh.back_transform(Model(1.0), ana)
    assert back.model.structures == () and back.curves == [] and back.n == 0 and back.residual == 0.0
    assert relerr(back.model.c0, VAR_LOGNORMAL) < 1e-12
    assert back.answer()["curves"] == []


def test_gaps_stay_gaps() -> None:
    ana = vh.anamorphosis(np.exp)
    out = ana.gamma(np.array([0.0, np.nan, 0.5]))
    assert out[0] == 0.0 and math.isnan(out[1]) and math.isfinite(out[2])
    assert math.isnan(float(ana.cov(np.nan)))


def test_nonfinite_transform_refused() -> None:
    with pytest.raises(StatsError) as e:
        vh.anamorphosis(lambda y: np.where(y > 20.0, np.inf, y))
    assert e.value.code == "bad_param" and e.value.param == "transform" and e.value.hint
    with pytest.raises(StatsError) as e:
        vh.anamorphosis("exp")
    assert e.value.param == "transform"


# --- the API: POST /variography/back --------------------------------------------------------------------------------


def _table() -> object:
    return nscore(np.exp(np.random.default_rng(SEED).standard_normal(5000)))[1]


def test_api_form() -> None:
    model = model_form("nscore", model_y())
    answer = vh.back_variogram({"transform": "nscore"}, model, _table())
    assert set(answer) == {"model", "share", "warning", "residual", "curves"}
    assert answer["model"]["type"] == "semivariogram"
    assert set(answer["model"]) == {"type", "c0", "common_axes", "structures"}
    assert answer["warning"] is None and 0.95 <= answer["share"] <= 1.0
    assert [c["axis"] for c in answer["curves"]] == [1, 2, 3]
    for c in answer["curves"]:
        assert set(c) == {"axis", "h", "gamma_z", "gamma_fit"}
        assert len(c["h"]) == len(c["gamma_z"]) == len(c["gamma_fit"]) == 100
        assert all(type(v) is float for v in c["h"] + c["gamma_z"] + c["gamma_fit"])
        assert c["h"][0] > 0.0 and all(a < b for a, b in zip(c["h"], c["h"][1:], strict=False))
    assert bits_of(answer) == bits_of(json.loads(json.dumps(answer)))  # JSON keeps every number
    again = vh.back_variogram({"transform": "nscore"}, model, _table())
    assert bits_of(again) == bits_of(answer)  # bit for bit


def test_api_types_of_the_model() -> None:
    table = _table()
    for kind in ("nscore", "semivariogram", "correlogram"):
        vh.back_variogram({"transform": "nscore"}, model_form(kind, model_y()), table)
    with pytest.raises(StatsError) as e:
        vh.back_variogram({"transform": "nscore"}, model_form("pairwise", model_y()), table)
    assert e.value.code == "bad_param" and e.value.param == "model.type" and e.value.hint


def test_api_refusals() -> None:
    model, table = model_form("nscore", model_y()), _table()
    for result in ({"transform": "none"}, {}, None):
        with pytest.raises(StatsError) as e:
            vh.back_variogram(result, model, table)
        assert e.value.code == "bad_param" and e.value.param == "key" and e.value.hint
    with pytest.raises(StatsError) as e:
        vh.back_variogram({"transform": "nscore"}, model, None)
    assert e.value.param == "key"
    broken = {**model, "structures": [{**model["structures"][0], "a1": -5}]}
    with pytest.raises(StatsError) as e:
        vh.back_variogram({"transform": "nscore"}, broken, table)
    assert e.value.param == "model.structures.0.a1"
    with pytest.raises(StatsError) as e:
        vh.back_variogram({"transform": "nscore"}, model, table, ltail="cubic")
    assert e.value.param == "ltail"


def test_tick_called_and_cancels() -> None:
    calls: list[int] = []
    vh.back_transform(model_y(), vh.anamorphosis(np.exp), tick=lambda: calls.append(1))
    assert len(calls) >= 2

    class Cancelled(Exception):
        pass

    def stop() -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        vh.back_variogram({"transform": "nscore"}, model_form("nscore", model_y()), _table(), tick=stop)
