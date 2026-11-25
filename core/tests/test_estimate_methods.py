"""The point methods of the estimation (ТЗ 8.8.1–8.8.2; ADR 0024 §6; Э18, 18.7): NN (the nearest, ties by the number),
IDW (weights, the samples of one place, NaN), Т8.8-12 — IDW isotropic in 2D equals BF `idw` (`stats.local.idw`) to
1e−12 and an anisotropic one equals a full enumeration in NumPy; the row loop of SK / OK equals `krige_one` bit for bit;
the NaN branches; read-only inputs."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.geostat.estimate import kernel as kn
from bf_core.geostat.estimate import krige as kg
from bf_core.geostat.estimate import methods as mt
from bf_core.geostat.estimate.cov import cov_bb, cov_model, disc_points
from bf_core.geostat.estimate.samples import prepare
from bf_core.geostat.estimate.spec import parse_estimation
from bf_core.geostat.variogram.model import Model, Structure
from bf_core.stats.local import idw as bf_idw

SPH = {"c0": 0.1, "structures": [{"type": "spherical", "c": 0.9, "a1": 30, "a2": 30, "a3": 30}]}


def samples_of(xyz: np.ndarray, z: np.ndarray, hole: np.ndarray | None = None):
    n = xyz.shape[0]
    hole = np.zeros(n, np.int32) if hole is None else hole
    names = [f"h{i}" for i in range(int(hole.max()) + 1)]
    return prepare(xyz, z, None, None, hole, names, np.zeros(n, np.int16), np.arange(n, dtype=np.int64),
                   np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip


def spec_of(kind: str, *, r1: float = 100.0, radii=None, angles=None, n_max: int = 24, n_min: int = 1,
            power: float = 2.0, disc=(1, 1, 1), variogram=SPH, passes=None):  # fmt: skip
    search = {"from": "manual", "r1": r1, "passes": passes or [{"factor": 1, "n_min": n_min, "n_max": n_max}]}
    if radii is not None:
        search.update(r1=radii[0], r2=radii[1], r3=radii[2])
    if angles is not None:
        search.update(ang1=angles[0], ang2=angles[1], ang3=angles[2])
    body = {"model_id": 1, "samples": {"set_id": 1, "table": "t", "field": "z"},
            "method": {"kind": kind, "power": power}, "disc": list(disc), "search": search}  # fmt: skip
    if variogram is not None:
        body["variogram"] = {"model_id": 1, "snapshot": variogram}
    return parse_estimation(body)


# --- NN --------------------------------------------------------------------------------------------------------------


def test_nn_is_the_nearest_and_ties_go_to_the_smaller_number():
    z = np.array([5.0, 7.0, 9.0])
    h = np.array([2.0, 1.0, 1.0])
    idx = np.array([4, 9, 3], dtype=np.int32)
    assert mt.nn(z, h, idx) == 9.0  # h = 1 twice: the sample with the number 3
    assert mt.nn(z[:1], h[:1], idx[:1]) == 5.0
    assert math.isnan(mt.nn(z[:0], h[:0], idx[:0]))  # none — NaN, not 0
    assert math.isnan(mt.nn(z, np.full(3, math.nan), idx))
    assert math.isnan(mt.nn(z, h, np.full(3, -1, dtype=np.int32)))  # padding


# --- IDW -------------------------------------------------------------------------------------------------------------


def test_idw_weights_and_the_formula():
    z = np.array([1.0, 2.0, 4.0])
    h = np.array([1.0, 2.0, 4.0])
    est, w = mt.idw(z, h, 2.0, 100.0)
    raw = 1.0 / h**2
    assert est == pytest.approx(float((raw * z).sum() / raw.sum()), rel=1e-15)
    assert w.sum() == pytest.approx(1.0, rel=1e-15)
    assert w == pytest.approx(raw / raw.sum(), rel=1e-15)
    assert mt.idw(z, h, 1.0, 100.0)[0] == pytest.approx(float((z / h).sum() / (1 / h).sum()), rel=1e-15)


def test_idw_samples_of_one_place_make_their_mean():
    z = np.array([2.0, 4.0, 9.0])
    h = np.array([0.0, 5e-8, 3.0])  # < 1e−9·r1 with r1 = 100: the first two are one place
    est, w = mt.idw(z, h, 2.0, 100.0)
    assert est == 3.0
    assert list(w) == [0.5, 0.5, 0.0]
    est2, w2 = mt.idw(z, h, 2.0, 1.0)  # r1 = 1: 5e−8 is not one place any more — only the sample at h = 0 is
    assert est2 == 2.0 and list(w2) == [1.0, 0.0, 0.0]


def test_idw_nan_and_empty():
    est, w = mt.idw(np.array([1.0, math.nan]), np.array([1.0, 2.0]), 2.0, 10.0)
    assert math.isnan(est) and np.isnan(w).all()
    est, w = mt.idw(np.array([1.0, 2.0]), np.array([1.0, math.nan]), 2.0, 10.0)
    assert math.isnan(est)
    est, w = mt.idw(np.empty(0), np.empty(0), 2.0, 10.0)
    assert math.isnan(est) and w.size == 0


@pytest.mark.parametrize("k, power, radius", [(8, 2.0, 400.0), (1, 2.0, 600.0), (5, 1.0, 150.0), (24, 3.5, 5000.0),
                                              (12, 0.5, 250.0)])  # fmt: skip
def test_t8_8_12_idw_isotropic_2d_equals_bf_idw(k, power, radius):
    """Т8.8-12: the estimate of IDW in the plane (isotropic ellipsoid, one pass) equals BF `idw` (the k nearest data
    within the radius, weights 1/d^p) to 1e−12; a node with no datum within the radius is NaN, not 0."""
    rng = np.random.default_rng(10 + k)
    n = 400
    xy = rng.uniform(0.0, 1000.0, (n, 2))
    v = rng.lognormal(0.5, 0.8, n)
    nodes = rng.uniform(-50.0, 1050.0, (300, 2))
    xyz = np.column_stack([xy, np.zeros(n)])
    s = samples_of(xyz, v)
    rk = kn.compile_row(spec_of("idw", r1=radius, n_max=k, power=power), s)
    res = kn.estimate(rk, np.column_stack([nodes, np.zeros(len(nodes))]))
    # the samples were put in the canonical order: the BF estimate is computed on the same samples
    want = bf_idw(s.xyz[:, :2], s.z, nodes, k=k, power=power, radius=radius)
    got = res.values["EST"]
    nan = np.isnan(want)
    assert (np.isnan(got) == nan).all()  # the same nodes are empty
    assert (res.status[nan] == kn.ST_FEW).all()
    assert np.abs(got[~nan] - want[~nan]).max() <= 1e-12 * max(1.0, np.abs(want[~nan]).max())


def test_idw_anisotropic_equals_a_full_enumeration():
    """IDW with h_s of a rotated ellipsoid: the k nearest by h_s, weights 1/h_s^p (NumPy, from the definition)."""
    rng = np.random.default_rng(3)
    n = 300
    xyz = rng.uniform(-100.0, 100.0, (n, 3))
    z = rng.uniform(0.0, 10.0, n)
    s = samples_of(xyz, z)
    angles, radii = (30.0, 20.0, 10.0), (80.0, 40.0, 20.0)
    rk = kn.compile_row(spec_of("idw", radii=radii, angles=angles, n_max=10, power=1.5), s)
    centres = rng.uniform(-80.0, 80.0, (60, 3))
    got = kn.estimate(rk, centres).values["EST"]
    for i, c in enumerate(centres):
        h = rk.ell.h(s.local - c)
        order = np.lexsort((np.arange(n), h))
        take = [j for j in order if h[j] <= radii[0]][:10]
        if not take:
            assert math.isnan(got[i])
            continue
        w = 1.0 / h[take] ** 1.5
        assert got[i] == pytest.approx(float((w * s.z[take]).sum() / w.sum()), rel=1e-13)


def test_nn_through_the_kernel_is_the_nearest_by_h_s():
    rng = np.random.default_rng(4)
    xyz = rng.uniform(-50.0, 50.0, (200, 3))
    s = samples_of(xyz, rng.uniform(0.0, 5.0, 200))
    rk = kn.compile_row(spec_of("nn", r1=40.0, radii=(40.0, 20.0, 10.0), angles=(45.0, 10.0, 0.0)), s)
    centres = rng.uniform(-40.0, 40.0, (80, 3))
    res = kn.estimate(rk, centres)
    for i, c in enumerate(centres):
        h = rk.ell.h(s.local - c)
        j = int(np.lexsort((np.arange(200), h))[0])
        if h[j] <= 40.0:
            assert res.values["EST"][i] == s.z[j] and res.values["DMIN"][i] > 0.0
        else:
            assert math.isnan(res.values["EST"][i]) and res.status[i] == kn.ST_FEW


# --- the row loop of the kriging -------------------------------------------------------------------------------------


@pytest.mark.parametrize("ok", [1, 0])
@pytest.mark.parametrize("block", [False, True])
def test_krige_rows_equals_krige_one_bit_for_bit(ok, block):
    model = Model(0.2, (Structure("spherical", 0.5, 40.0, 25.0, 10.0, 20.0, 10.0, 5.0),
                        Structure("exponential", 0.3, 90.0, 60.0, 30.0)))  # fmt: skip
    cov = cov_model(model)
    rng = np.random.default_rng(7)
    local = rng.uniform(-60.0, 60.0, (50, 3))
    rows_n = [6, 12, 1, 20]
    width = 24
    idx = np.full((4, width), -1, dtype=np.int32)
    zz = np.full((4, width), math.nan)
    for r, k in enumerate(rows_n):
        pick = rng.choice(50, k, replace=False)
        idx[r, :k] = pick
        zz[r, :k] = rng.uniform(0.0, 5.0, k)
    n = np.array(rows_n, dtype=np.int16)
    centres = rng.uniform(-20.0, 20.0, (4, 3))
    offs = disc_points((4.0, 4.0, 2.0), (3, 3, 2), 15.0) if block else np.zeros((1, 3))
    bv = float(cov_bb(*cov.args, offs)) if block else cov.sill
    cols = np.full((4, mt.N_COLS), math.nan)
    status = np.full(4, 9, dtype=np.int64)
    lam = np.full((4, width), math.nan)
    mt.krige_rows(*cov.args, local, idx, zz, n, centres, np.arange(4, dtype=np.int64), offs, bv, 1.3, ok, cols,
                  status, lam, True)  # fmt: skip
    work = kg.KrigeWork()
    for r, k in enumerate(rows_n):
        st = work.run(cov, local[idx[r, :k]], zz[r, :k], centres[r], offs, bv, 1.3, ok)
        out = work.out
        assert status[r] == st == kg.KR_OK
        assert cols[r, mt.K_EST] == out[kg.O_EST] and cols[r, mt.K_KV] == out[kg.O_KV]
        assert cols[r, mt.K_SLOPE] == out[kg.O_SLOPE] and cols[r, mt.K_KE] == out[kg.O_KE]
        assert cols[r, mt.K_SNW] == out[kg.O_SNW]
        assert (lam[r, :k] == work.lam(k)).all() and np.isnan(lam[r, k:]).all()
        if ok:
            assert cols[r, mt.K_LAGR] == work.mu(k)
        else:
            assert math.isnan(cols[r, mt.K_LAGR])  # SK has no multiplier


def test_krige_rows_a_failed_block_is_nan_and_the_others_are_solved():
    cov = cov_model(Model(0.0, (Structure("spherical", 1.0, 30.0, 30.0, 30.0),)))
    local = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [5.0, 0.0, 0.0], [9.0, 3.0, 0.0]])  # 0 and 1 coincide
    idx = np.array([[0, 1, -1], [0, 2, 3]], dtype=np.int32)
    zz = np.array([[1.0, 2.0, math.nan], [1.0, 2.0, 3.0]])
    n = np.array([2, 3], dtype=np.int16)
    centres = np.array([[2.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    cols = np.full((2, mt.N_COLS), 7.0)
    status = np.zeros(2, dtype=np.int64)
    lam = np.empty((1, 1))
    mt.krige_rows(*cov.args, local, idx, zz, n, centres, np.arange(2, dtype=np.int64), np.zeros((1, 3)), cov.sill,
                  0.0, 1, cols, status, lam, False)  # fmt: skip
    assert status[0] == kg.KR_SINGULAR and np.isnan(cols[0]).all()  # NaN, not 0
    assert status[1] == kg.KR_OK and np.isfinite(cols[1]).all()


def test_row_kernels_work_on_read_only_arrays_and_warm_up():
    z, h, idx = np.array([1.0, 2.0]), np.array([1.0, 2.0]), np.array([0, 1], dtype=np.int32)
    for a in (z, h, idx):
        a.setflags(write=False)
    assert mt.nn(z, h, idx) == 1.0
    assert mt.idw(z, h, 2.0, 10.0)[0] == pytest.approx(1.2, rel=1e-15)
    mt.warm_methods()
