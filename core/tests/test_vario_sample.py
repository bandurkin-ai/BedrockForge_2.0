"""The estimate of the pairs and the sample (Э16, 16.3; ТЗ 8.6.2; ADR 0022 §4).

The estimate is the number of pair checks of the grid by the counts of its cells: here it is counted again over
every pair on NumPy (the cells of the two points at an offset of the plan). Above the limit `calculate` takes
n′ = ⌊n·√(limit / estimate)⌋ points (`points.sample_size`, `points.sample`: PCG64, seed 12345): the same seed —
the same sample and the same result bit for bit, another seed — another sample; the run equals the kernel over
the sample; `sampled {n, of, seed}`. Below the limit — no sample, the same result as `experimental`.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np
import pytest

from bf_core.geostat.variogram import experimental as ex
from bf_core.geostat.variogram import pairs, points, spec
from bf_core.geostat.variogram.pairs import Direction
from bf_core.geostat.variogram.points import Points

INF = math.inf
DIRS = [Direction(30.0, 10.0, 22.5, 22.5, 15.0, 10.0), Direction(120.0, 0.0, 22.5, 22.5, 15.0, 10.0),
        Direction(30.0, -80.0, 22.5, 22.5, 15.0, 10.0)]  # fmt: skip


def cloud(n=1500, seed=1):
    r = np.random.default_rng(seed)
    xyz = np.stack([r.uniform(0, 200, n), r.uniform(0, 150, n), r.uniform(0, 60, n)], axis=1)
    v = np.exp(r.normal(0, 1, n) + np.sin(xyz[:, 0] / 30.0))
    return xyz, v


def points_of(xyz, v) -> Points:
    n = len(v)
    counts = {"rows": n, "missing": 0, "no_position": 0, "outside_domain": 0, "used": n, "no_weight": 0}
    return Points(xyz=xyz, origin=np.zeros(3), values=v, z=v, weights=None, hole=np.arange(n, dtype=np.int32),
                  holes=[str(i) for i in range(n)], depth=np.zeros(n), length=np.ones(n),
                  row_ids=np.arange(100, 100 + n, dtype=np.int64), counts=counts, sigma2=float(v.var()),
                  sigma2_z=float(v.var()), median_length=1.0)  # fmt: skip


def digest(e: ex.Experimental) -> str:
    s = e.sums
    return hashlib.sha256(s.n.tobytes() + s.n_rel.tobytes() + s.sums.tobytes()).hexdigest()


# ---- the estimate --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("m", [1, 2, 4, 6])
def test_estimate_is_the_pair_checks_of_the_grid(m):
    xyz, v = cloud()
    lag, nlag, tol = 5.0, 12, 2.5
    x, y, z = xyz.T
    est = ex.estimate(x, y, z, DIRS, lag, nlag, cells=m)
    plan = pairs.prepare(x, y, z, pairs.check_directions(DIRS), lag, nlag, tol, m)
    assert est == plan.checks == ex.experimental(x, y, z, v, DIRS, lag, nlag, cells=m).checks
    # again on NumPy: the pairs whose cells lie at an offset of the plan (the half-space, the cell itself first)
    cell = (nlag * lag + tol) / m * (1.0 + pairs._GROW)
    idx = np.floor((xyz - xyz.min(axis=0)) / cell).astype(np.int64)
    i, j = np.triu_indices(len(v), 1)
    d = idx[j] - idx[i]
    neg = (d[:, 2] < 0) | ((d[:, 2] == 0) & (d[:, 1] < 0)) | ((d[:, 2] == 0) & (d[:, 1] == 0) & (d[:, 0] < 0))
    d[neg] = -d[neg]
    code = lambda o: (o[:, 0] + 64) + 129 * ((o[:, 1] + 64) + 129 * (o[:, 2] + 64))  # noqa: E731
    assert est == int(np.isin(code(d), code(plan.off)).sum())
    taken = ex.experimental(x, y, z, v, DIRS, lag, nlag, cells=m).n.sum(axis=1)
    assert est <= len(i) and (taken <= est).all()  # an upper bound of the pairs of a direction


def test_estimate_falls_with_m_and_does_not_read_the_values():
    xyz, _ = cloud(3000, seed=2)
    x, y, z = xyz.T
    est = [ex.estimate(x, y, z, DIRS, 5.0, 12, cells=m) for m in (1, 2, 4, 6)]
    assert est == sorted(est, reverse=True) and est[0] > 2 * est[-1]
    xn = x.copy()
    xn[5] = np.nan  # a row without a position is not a point
    assert ex.estimate(xn, y, z, DIRS, 5.0, 12) <= ex.estimate(x, y, z, DIRS, 5.0, 12)


# ---- the sample ----------------------------------------------------------------------------------------------
def test_below_the_limit_no_sample():
    xyz, v = cloud(seed=3)
    p = points_of(xyz, v)
    e = ex.calculate(p, DIRS, 5.0, 12)
    f = ex.experimental(*xyz.T, v, DIRS, 5.0, 12)
    assert e.sampled is None and e.estimate == e.checks == f.checks and digest(e) == digest(f)
    assert e.n_points == p.n and e.n_missing == 0


def test_above_the_limit_a_reproducible_sample():
    xyz, v = cloud(2000, seed=4)
    p = points_of(xyz, v)
    est = ex.estimate(*xyz.T, DIRS, 5.0, 12)
    limit = est / 9.0
    e = ex.calculate(p, DIRS, 5.0, 12, limit=limit, threads=2)
    n_new = math.floor(2000 * math.sqrt(limit / est))
    assert e.sampled == {"n": n_new, "of": 2000, "seed": 12345} and n_new == points.sample_size(2000, est, limit)
    assert e.n_points == n_new and e.estimate == est and e.checks < est
    q, sampled = points.sample(p, n_new, 12345)  # the very points of `points.sample`
    assert sampled == e.sampled
    assert digest(ex.experimental(*q.xyz.T, q.values, DIRS, 5.0, 12)) == digest(e)
    again = ex.calculate(p, DIRS, 5.0, 12, limit=limit, threads=4)
    assert digest(again) == digest(e)  # the same seed — the same result, whatever the threads
    other = ex.calculate(p, DIRS, 5.0, 12, limit=limit, seed=7)
    assert other.sampled == {"n": n_new, "of": 2000, "seed": 7} and digest(other) != digest(e)
    assert not np.array_equal(points.sample(p, n_new, 7)[0].row_ids, q.row_ids)


def test_defaults_are_those_of_the_spec():
    import inspect

    sig = inspect.signature(ex.calculate).parameters
    assert sig["limit"].default == spec.PAIRS_MAX == 2_000_000_000 and sig["seed"].default == spec.SEED == 12345


def test_a_tiny_limit_keeps_two_points():
    xyz, v = cloud(300, seed=5)
    e = ex.calculate(points_of(xyz, v), [Direction(0.0, 0.0, 90.0, 90.0)], 50.0, 3, limit=1.0)
    assert e.sampled is not None and e.sampled["n"] == 2 and e.n_points == 2 and e.n.sum() <= 2


def test_points_of_a_calculation_must_be_finite():
    xyz, v = cloud(50, seed=6)
    xyz[3, 1] = np.nan
    with pytest.raises(ValueError):
        ex.calculate(points_of(xyz, v), DIRS, 5.0, 5)
