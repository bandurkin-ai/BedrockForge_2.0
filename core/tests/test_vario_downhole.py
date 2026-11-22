"""The variogram along the holes (Э16, 16.3; ТЗ 8.6.1, Т8.6-7; ADR 0022 §5).

Т8.6-7 — a hole 0, 1, 0, 1 … every 1 m: γ(1) = 0.5, γ(2) = 0. Against every pair of a hole on NumPy
(`tests/ref_bf_variogram.brute_downhole`): N exactly, γ, ρ, pairwise relative 1e−12 — rows in any order, holes of
one sample, two samples at one depth, a lag tolerance of a whole lag. Missing rows take no part and are counted;
one hash with 1, 2, 4 and 8 threads; the answer `downhole` of docs/progress/e16-api.md; progress and cancel.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from bf_core.geostat.variogram import downhole as dh
from bf_core.geostat.variogram.points import Points
from bf_core.stats.weighted import StatsError
from tests import ref_bf_variogram as ref


def close(a, b, tol=1e-12):
    """Equal within `tol` (relative to the size of the number, absolute below 1); NaN only where NaN."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    assert a.shape == b.shape
    assert np.array_equal(np.isnan(a), np.isnan(b))
    ok = ~np.isnan(b)
    err = np.abs(a[ok] - b[ok]) / np.maximum(1.0, np.abs(b[ok]))
    assert err.size == 0 or float(err.max()) <= tol, float(err.max())


def holes(n_holes=25, seed=1):
    """Holes of 1–90 samples of unequal length with gaps; rows shuffled; skewed values, some below zero."""
    r = np.random.default_rng(seed)
    hole, depth = [], []
    for h in range(n_holes):
        k = int(r.integers(1, 91))
        top = np.cumsum(r.uniform(0.3, 2.5, k) + np.where(r.random(k) < 0.1, 1.0, 0.0))
        length = r.uniform(0.2, 1.0, k)
        hole += [h] * k
        depth += list(top + length / 2.0)
    hole, depth = np.array(hole), np.array(depth)
    v = np.exp(r.normal(0, 1, hole.size) + np.sin(depth / 7.0)) - 0.5
    order = r.permutation(hole.size)
    return hole[order], depth[order], v[order]


def against_brute(hole, depth, v, lag, nlag, tol=None, **kw) -> dh.Downhole:
    d = dh.downhole(hole, depth, v, lag, nlag, tol, **kw)
    w = ref.brute_downhole(hole, depth, v, lag, nlag, tol)
    assert np.array_equal(d.n, w["n"]) and np.array_equal(d.n_rel, w["n_rel"])
    close(d.h, w["h"])
    close(d.gamma, w["gamma"])
    close(d.rho, w["rho"])
    close(d.one_minus_rho, 1.0 - w["rho"])
    close(d.pairwise, w["pairwise"])
    close(d.sums.tail[0], w["tail"])
    close(d.sums.head[0], w["head"])
    return d


def digest(d: dh.Downhole) -> str:
    s = d.sums
    return hashlib.sha256(s.n.tobytes() + s.n_rel.tobytes() + s.sums.tobytes()).hexdigest()


# ---- Т8.6-7 --------------------------------------------------------------------------------------------------
def test_t867_alternating_hole():
    depth = np.arange(40) + 0.5
    v = (np.arange(40) % 2).astype(float)  # 0, 1, 0, 1 … every 1 m
    d = dh.downhole(np.zeros(40), depth, v, 1.0, 3)
    assert d.gamma[0] == 0.5 and d.gamma[1] == 0.0 and d.gamma[2] == 0.5
    assert d.n.tolist() == [39, 38, 37] and d.h.tolist() == [1.0, 2.0, 3.0]
    assert d.rho[0] == pytest.approx(-1.0, abs=1e-12) and d.rho[1] == pytest.approx(1.0, abs=1e-12)
    assert d.n_rel.tolist() == [39, 19, 37]  # the pairs 0–0 at 2 m are out of the pairwise relative
    against_brute(np.zeros(40), depth, v, 1.0, 3)


# ---- against every pair --------------------------------------------------------------------------------------
@pytest.mark.parametrize("lag, nlag, tol", [(1.0, 15, None), (2.0, 10, 2.0), (0.7, 30, 0.2), (5.0, 4, 3.0)])
def test_every_pair_of_a_hole_all_types(lag, nlag, tol):
    hole, depth, v = holes()
    d = against_brute(hole, depth, v, lag, nlag, tol)
    assert d.n.sum() > 1000 and d.holes == 25 and d.n_points == hole.size
    assert (d.n_rel < d.n).any()  # pairs with z_i + z_j ≤ 0 are left out of the pairwise relative and counted


def test_lattice_on_the_edges_and_samples_at_one_depth():
    depth = np.r_[np.arange(30) * 0.5, [3.0, 3.0, 7.5]]  # pairs on the edges of the windows; three repeated depths
    hole = np.r_[np.zeros(30), [0.0, 0.0, 0.0]]
    v = np.random.default_rng(2).normal(3, 1, depth.size)
    a = against_brute(hole, depth, v, 1.0, 6)  # h = 1.5, 2.5 … sit on the edges: in both lags (as BF)
    b = against_brute(hole, depth, v, 1.0, 6, 1.0)  # the window of the first lag starts at 0: h = 0 enters
    assert (b.n > a.n).all() and b.n[0] - a.n[0] >= 4


def test_holes_far_apart_in_depth_never_pair():
    hole = np.array([0, 0, 1, 1])
    depth = np.array([1.0, 2.0, 1.5, 2.5])  # 0.5 m between the holes, but they are different holes
    d = dh.downhole(hole, depth, np.array([1.0, 2.0, 3.0, 5.0]), 0.5, 3)
    assert d.n.tolist() == [0, 2, 0] and d.gamma[1] == (1.0 + 4.0) / 4.0
    assert np.isnan(d.gamma[0]) and np.isnan(d.h[2]) and np.isnan(d.rho[0])  # no pairs — empty, not 0


def test_order_of_the_rows_changes_nothing():
    hole, depth, v = holes(seed=3)
    a = dh.downhole(hole, depth, v, 1.5, 12)
    o = np.argsort(hole * 1e4 + depth, kind="stable")
    b = dh.downhole(hole[o], depth[o], v[o], 1.5, 12)
    assert np.array_equal(a.n, b.n) and np.array_equal(a.sums.sums, b.sums.sums) and a.sums.shift == b.sums.shift


# ---- missing rows, threads -----------------------------------------------------------------------------------
def test_missing_rows_take_no_part_and_are_counted():
    hole, depth, v = holes(seed=4)
    hf, dm, vm = hole.astype(float), depth.copy(), v.copy()
    hf[3], dm[[10, 11]], vm[[20, 21]] = np.nan, np.nan, np.inf
    hf[30] = -1.0
    keep = np.ones(hole.size, dtype=bool)
    keep[[3, 10, 11, 20, 21, 30]] = False
    a = dh.downhole(hf, dm, vm, 1.0, 10)
    b = dh.downhole(hole[keep], depth[keep], v[keep], 1.0, 10)
    assert (a.n_missing, a.n_points, b.n_missing) == (6, keep.sum(), 0)
    assert np.array_equal(a.n, b.n) and np.array_equal(a.sums.sums, b.sums.sums)


def test_one_hash_with_1_2_4_8_threads(monkeypatch):
    hole, depth, v = holes(200, seed=5)
    monkeypatch.setattr(dh, "STRIP_HOLES", 3)  # many strips
    runs = [dh.downhole(hole, depth, v, 1.0, 15, threads=t) for t in (1, 2, 4, 8)]
    assert len({digest(d) for d in runs}) == 1 and runs[0].n.sum() > 50_000
    monkeypatch.undo()
    other = dh.downhole(hole, depth, v, 1.0, 15, threads=4)  # other strips: the same counts, sums within 1e−12
    assert np.array_equal(other.n, runs[0].n)
    close(other.gamma, runs[0].gamma)


def test_tick_reports_progress_and_cancels(monkeypatch):
    hole, depth, v = holes(100, seed=6)
    monkeypatch.setattr(dh, "STRIP_HOLES", 10)
    seen: list[float] = []
    dh.downhole(hole, depth, v, 1.0, 5, threads=2, tick=seen.append)
    assert len(seen) == 10 and seen == sorted(seen) and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    def stop(f: float) -> None:
        if f >= 0.3:
            raise Cancelled

    with pytest.raises(Cancelled):
        dh.downhole(hole, depth, v, 1.0, 5, tick=stop)


def test_bad_lags_name_the_parameter():
    hole, depth, v = holes(5)
    for args, param in (((0.0, 5), "lag"), ((1.0, 0), "nlag"), ((1.0, 5, 1.5), "lag_tol")):
        with pytest.raises(StatsError) as err:
            dh.downhole(hole, depth, v, *args)
        assert err.value.code == "bad_param" and err.value.param == param and err.value.hint


def test_no_points_no_pairs():
    d = dh.downhole([], [], [], 1.0, 3)
    assert d.n.tolist() == [0, 0, 0] and np.isnan(d.gamma).all() and d.holes == 0


# ---- the answer, the points of a calculation -----------------------------------------------------------------
def test_to_json_is_the_form_of_the_answer():
    hole, depth, v = holes(seed=7)
    d = dh.downhole(hole, depth, v, 5.0, 60, 2.5)  # the holes are shorter than 300 m: the last lags are empty
    out = d.to_json(min_pairs=30)
    keys = ["lag", "lag_tol", "lags", "h", "n", "gamma", "rho", "pairwise", "n_pw", "few"]
    assert list(out) == keys and (out["lag"], out["lag_tol"]) == (5.0, 2.5) and out["lags"][:2] == [5.0, 10.0]
    assert json.loads(json.dumps(out)) == out
    assert out["n"] == d.n.tolist() and out["few"] == (d.n < 30).tolist() and out["n_pw"] == d.n_rel.tolist()
    assert 0 in out["n"] and all((g is None) == (c == 0) for g, c in zip(out["gamma"], out["n"], strict=True))
    assert d.pw_skipped == int((d.n - d.n_rel).sum())


def points_of(hole, depth, v) -> Points:
    n = len(v)
    xyz = np.stack([np.zeros(n), hole * 30.0, -depth], axis=1)
    counts = {"rows": n, "missing": 0, "no_position": 0, "outside_domain": 0, "used": n, "no_weight": 0}
    return Points(xyz=xyz, origin=np.zeros(3), values=v, z=v, weights=None, hole=hole.astype(np.int32),
                  holes=[f"H{h}" for h in range(int(hole.max()) + 1)], depth=depth, length=np.ones(n),
                  row_ids=np.arange(n, dtype=np.int64), counts=counts, sigma2=float(v.var()),
                  sigma2_z=float(v.var()), median_length=1.0)  # fmt: skip


def test_calculate_takes_the_points_of_a_calculation():
    hole, depth, v = holes(seed=8)
    o = np.lexsort((depth, hole))
    p = points_of(hole[o], depth[o], v[o])
    a = dh.calculate(p, 1.0, 12, threads=2)
    b = dh.downhole(hole, depth, v, 1.0, 12)
    assert digest(a) == digest(b) and a.n_points == p.n
