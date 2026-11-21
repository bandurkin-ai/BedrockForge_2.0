"""Normal score transform and back-transform (ТЗ 7.29, 10.2; ADR 0019 §4; Э13, 13.12): the tests of BF
`test_stats_transforms.py` (those that need no geostatspy), the stored reference of GeostatsPy `nscore` and
`backtr` (`tests/data/ref/geostatspy`, 1e−6), `backtr(nscore(x)) = x` to 1e−12, the weighted table against the
rule P = (C − w / 2) / W, the tails against their closed forms, «hard» data: ties, zeros, zero weights, one value.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import norm

from bf_core.stats.transforms import LOWER_TAILS, UPPER_TAILS, NormalScoreTransform, backtr, nscore
from bf_core.stats.weighted import StatsError

REF = Path(__file__).resolve().parents[2] / "tests" / "data" / "ref" / "geostatspy" / "geostatspy_reference.json"

rng = np.random.default_rng(1)
X = rng.lognormal(mean=1.0, sigma=0.6, size=300)
W = rng.uniform(0.5, 2.0, size=300)


# --- BF ------------------------------------------------------------------------------------------------------


def test_roundtrip_unweighted_and_weighted() -> None:
    """BF `test_roundtrip_unweighted_and_weighted`; `backtr(nscore(x)) = x` to 1e−12 (ADR 0019 §4) — with any
    tails and with trimming limits beyond the data too."""
    for w in (None, W):
        y, tr = nscore(X, wts=w)
        assert np.allclose(backtr(y, tr), X, rtol=1e-12, atol=0)
        assert np.array_equal(backtr(y, tr, None, None), X)  # the table itself: exact
        wide = nscore(X, wts=w, zmin=-1e9, zmax=1e12)[1]
        for tails in (("linear", "linear", None, None), ("power", "power", 2.0, 1.5), (None, "hyperbolic", None, 1.5)):
            assert np.allclose(backtr(y, wide, *tails), X, rtol=1e-12, atol=0)
        assert np.array_equal(tr.forward(X), y)  # distinct values: the score of a value is its row of the table


def test_scores_approximately_standard_normal() -> None:
    y, _ = nscore(X)
    assert abs(y.mean()) < 0.01 and abs(y.std() - 1) < 0.05


def test_hyperbolic_upper_tail_monotone_and_above_max() -> None:
    _, tr = nscore(X)
    y = np.array([3.0, 4.0, 5.0])
    z = backtr(y, tr, "linear", "hyperbolic", None, 1.5)
    assert np.all(np.diff(z) > 0) and z[0] >= X.max() - 1e-9


def test_clamp_and_reference_distribution() -> None:
    _, tr = nscore(X)
    assert backtr([-9, 9], tr, None, None).tolist() == [X.min(), X.max()]
    ref = rng.normal(size=500)
    ns, rtr = nscore(X, reference=ref)
    assert ns.shape == X.shape and np.all(np.isfinite(ns))
    assert rtr.values.size == 500
    # The table is that of the reference, with its weights; the scores of the data are read from it.
    wr = rng.uniform(0.2, 3.0, 500)
    ns_w, wtr = nscore(X, wts=W, reference=ref, reference_wts=wr)
    assert np.array_equal(wtr.values, np.sort(ref)) and np.array_equal(ns_w, wtr.forward(X))
    assert np.array_equal(nscore(ref, wts=wr)[1].scores, wtr.scores) and not np.array_equal(wtr.scores, rtr.scores)


def test_ties_get_distinct_scores() -> None:
    y, _ = nscore(np.array([1.0, 1.0, 2.0, 2.0]))
    assert len(set(y)) == 4
    assert y[0] < y[1] < y[2] < y[3]  # by the order of the data


def test_validation() -> None:
    """BF `test_validation`; the refusals are {code, message, hint} in Russian."""
    with pytest.raises(ValueError):
        nscore([[1, 2], [3, 4]])
    with pytest.raises(ValueError):
        nscore([1.0, np.nan])
    _, tr = nscore(X)
    with pytest.raises(ValueError):
        backtr([0.0], tr, "power", "linear", None, None)
    with pytest.raises(ValueError):
        backtr([0.0], tr, "cubic", "linear")
    cases = [
        (lambda: nscore([]), "no_values", None),
        (lambda: nscore([1.0, math.inf]), "bad_input", None),
        (lambda: nscore(X, wts=W[:-1]), "bad_input", None),
        (lambda: nscore(X, wts=-W), "bad_input", None),
        (lambda: nscore(X, wts=np.where(np.arange(300) == 7, np.nan, W)), "bad_input", None),
        (lambda: nscore(X, wts=np.zeros(300)), "no_values", None),
        (lambda: nscore(X, reference=[]), "no_values", None),
        (lambda: nscore(X, zmin=X.min() + 0.1), "bad_param", "zmin"),
        (lambda: nscore(X, zmax=X.max() - 0.1), "bad_param", "zmax"),
        (lambda: nscore(X, zmin=math.nan), "bad_param", "zmin"),
        (lambda: backtr([0.0], tr, "hyperbolic", "linear"), "bad_param", "ltail"),
        (lambda: backtr([0.0], tr, "linear", "square"), "bad_param", "utail"),
        (lambda: backtr([0.0], tr, "power", "linear", 0.0), "bad_param", "ltpar"),
        (lambda: backtr([0.0], tr, "linear", "power", None, -1.0), "bad_param", "utpar"),
        (lambda: backtr([0.0], tr, "linear", "hyperbolic", None, 0.5), "bad_param", "utpar"),
        (lambda: backtr([0.0, math.nan], tr), "bad_input", None),
        (lambda: backtr([[0.0]], tr), "bad_input", None),
        (lambda: backtr([3.0], nscore(X - 100.0)[1], "linear", "hyperbolic", None, 1.5), "bad_param", "utail"),
    ]
    for call, code, param in cases:
        with pytest.raises(StatsError) as e:
            call()
        assert (e.value.code, e.value.param) == (code, param), e.value.message
        assert e.value.message and e.value.hint and e.value.message[0].isupper()
    assert LOWER_TAILS == (None, "linear", "power") and UPPER_TAILS == (None, "linear", "power", "hyperbolic")


# --- GeostatsPy (10.2, ADR 0019 §4: 1e−6) ------------------------------------------------------------------


def test_reference_of_geostatspy_nscore_and_backtr() -> None:
    """The stored result of `geostatspy.geostats.nscore` on 80 values: the table (the values exactly, the scores
    to 1e−6 — GSLIB `gauinv` is an approximation of Φ⁻¹) and the score of every sample. One sample differs by
    design: GeostatsPy reads the score of the minimum from the next two rows of its table (`max(1, j)`), the
    first score of the table is the right one — as BF. The round trip gives the data back."""
    ref = json.loads(REF.read_text(encoding="utf-8"))
    v = np.array(ref["data"]["V"])
    ns, vr, vrg = (np.array(ref["nscore"][k]) for k in ("ns", "vr", "vrg"))
    y, tr = nscore(v)
    assert np.array_equal(tr.values, vr) and tr.zmin == v.min() and tr.zmax == v.max()
    assert np.allclose(tr.scores, vrg, rtol=1e-6, atol=0)
    low = int(np.argmin(v))
    rest = np.arange(80) != low
    assert np.allclose(y[rest], ns[rest], rtol=1e-6, atol=0)
    assert y[low] == tr.scores[0] and tr.scores[0] == pytest.approx(vrg[0], rel=1e-6) and ns[low] > vrg[0] + 0.2
    # backtr: our scores give the data back to 1e−12; the table and the scores of GeostatsPy — to 1e−6.
    assert np.allclose(backtr(y, tr), v, rtol=1e-12, atol=0)
    theirs = NormalScoreTransform(vr, vrg, float(v.min()), float(v.max()))
    back = backtr(ns[rest], theirs)
    assert np.allclose(back, np.array(ref["nscore"]["backtr_roundtrip"])[rest], rtol=1e-6, atol=0)
    assert np.allclose(back, v[rest], rtol=1e-6, atol=0)


# --- the rule of the table, the tails -------------------------------------------------------------------------


def test_weighted_table_is_the_middle_of_the_cumulative_weight() -> None:
    """The score of a row of the table is Φ⁻¹((C − w / 2) / W) — the rule of the percentiles and of Q-Q
    (ADR 0019 §4); without weights it is Φ⁻¹((i − ½) / n). Weights are relative: their scale does not matter."""
    y, tr = nscore(X, wts=W)
    order = np.argsort(X, kind="stable")
    w = W[order]
    total = math.fsum(w.tolist())
    p = np.array([(math.fsum(w[: i + 1].tolist()) - w[i] / 2) / total for i in range(300)])
    assert np.array_equal(tr.values, X[order]) and np.allclose(tr.scores, norm.ppf(p), rtol=1e-12, atol=1e-13)
    assert np.allclose(y[order], tr.scores, rtol=0, atol=0) and np.all(np.diff(tr.scores) > 0)
    assert np.allclose(nscore(X, wts=W * 1e-7)[0], y, rtol=1e-12, atol=1e-13)
    plain = nscore(X)[1]
    assert np.allclose(plain.scores, norm.ppf((np.arange(300) + 0.5) / 300), rtol=1e-12, atol=1e-13)
    assert np.allclose(nscore(X, wts=np.full(300, 7.0))[1].scores, plain.scores, rtol=1e-12, atol=1e-13)
    d = tr.as_dict()
    assert d["values"] == tr.values.tolist() and d["scores"] == tr.scores.tolist() and d["zmin"] == X.min()


def test_tails_against_their_closed_forms() -> None:
    """GSLIB `backtr`: below the first score and above the last one the value is read from the tail between
    the end of the table and the trimming limit; in between — linearly in the table."""
    _, tr = nscore(X, zmin=X.min() * 0.5, zmax=X.max() * 2)
    v0, vn, g0, gn = tr.values[0], tr.values[-1], tr.scores[0], tr.scores[-1]
    y = np.array([-6.0, -4.0, -3.0, g0 - 1e-3, -0.5, 0.0, 1.2, gn + 1e-3, 3.0, 4.0, 6.0])
    lo, hi = y < g0, y > gn
    mid = np.interp(y[~lo & ~hi], tr.scores, tr.values)
    for lt, lp, up in (("linear", None, None), ("power", 2.0, 1.5), ("power", 0.5, 3.0)):
        z = backtr(y, tr, lt, lt, lp, up)
        pl, pu = (1.0, 1.0) if lp is None or up is None else (1.0 / lp, 1.0 / up)
        want_lo = tr.zmin + (v0 - tr.zmin) * (norm.cdf(y[lo]) / norm.cdf(g0)) ** pl
        want_hi = vn + (tr.zmax - vn) * ((norm.cdf(y[hi]) - norm.cdf(gn)) / (1.0 - norm.cdf(gn))) ** pu
        assert np.allclose(z[lo], want_lo, rtol=1e-9) and np.allclose(z[hi], want_hi, rtol=1e-9)
        assert np.array_equal(z[~lo & ~hi], mid)
        assert np.all(np.diff(z) >= 0) and z[0] >= tr.zmin and z[-1] <= tr.zmax
    z = backtr(y, tr, None, "hyperbolic", None, 2.0)
    want = (vn**2.0 * (1.0 - norm.cdf(gn)) / (1.0 - norm.cdf(y[hi]))) ** 0.5
    assert np.allclose(z[hi], want, rtol=1e-7) and (z[lo] == v0).all() and z[-1] > tr.zmax  # no upper limit
    # A hair beyond the end scores the tails start at the end values (1 − Φ is the survival function: the
    # difference of two Φ near 1 would lose its digits there).
    joint = backtr([g0 - 1e-9, gn + 1e-9], tr, "power", "power", 2.0, 1.5)
    assert v0 * (1 - 1e-4) < joint[0] <= v0 and vn <= joint[1] < vn * (1 + 1e-4)
    # Far in the tails the linear tail reaches the limits and stays finite; the hyperbolic one keeps growing.
    far = backtr([-40.0, 40.0], tr)
    assert far.tolist() == [tr.zmin, tr.zmax]
    grow = backtr([8.0, 12.0, 30.0], tr, "linear", "hyperbolic", None, 1.5)
    assert np.isfinite(grow).all() and np.all(np.diff(grow) > 0)
    # Monotone over the whole range, the joints included.
    dense = np.linspace(-7.0, 7.0, 20_001)
    for tails in (("linear", "linear", None, None), ("power", "hyperbolic", 3.0, 1.2), (None, None, None, None)):
        assert np.all(np.diff(backtr(dense, tr, *tails)) >= 0)


# --- «hard» data ------------------------------------------------------------------------------------------------


def test_ties_zeros_and_negative_values() -> None:
    """Assays as they are: 40 % at the detection limit written as 0, «<0.01» written as −0.01, ten orders of
    magnitude. Equal values get distinct scores in the order of the rows and come back as themselves."""
    g = np.random.default_rng(5)
    x = g.lognormal(0.0, 2.5, 5000) * 10.0 ** g.integers(-4, 5, 5000)
    x[g.random(5000) < 0.4] = 0.0
    x[g.integers(0, 5000, 150)] = -0.01
    w = g.uniform(0.1, 5.0, 5000)
    for wts in (None, w):
        y, tr = nscore(x, wts=wts)
        assert len(np.unique(y)) == 5000 and np.all(np.diff(tr.scores) > 0) and np.all(np.diff(tr.values) >= 0)
        assert np.array_equal(backtr(y, tr), x)  # exact: a score of the table is its value
        zero = np.flatnonzero(x == 0.0)
        assert np.all(np.diff(y[zero]) > 0)  # the order of the rows
        assert tr.forward([0.0])[0] == y[zero].max()  # a tied value read back: the last row of its run
        assert abs(float(np.average(y, weights=wts))) < 0.01
    one, tr1 = nscore([7.5])
    assert one.tolist() == [0.0] and backtr([-3.0, 0.0, 3.0], tr1).tolist() == [7.5, 7.5, 7.5]
    same, trc = nscore(np.full(50, 2.0))
    assert len(set(same)) == 50 and np.array_equal(backtr(same, trc), np.full(50, 2.0))
    y2, tr2 = nscore([-5.0, 3.0])
    assert y2.tolist() == [norm.ppf(0.25), norm.ppf(0.75)] and backtr(y2, tr2).tolist() == [-5.0, 3.0]


def test_zero_weights_are_left_out_of_the_table() -> None:
    """A weight of 0 (a sample whose polygon is smaller than a node of the grid of declustering) takes no part
    in the table — it would get a score of ±∞ at its ends (BF did); its own score is read from the table."""
    x = np.array([5.0, 1.0, 3.0, 9.0, 2.0, 7.0])
    w = np.array([1.0, 0.0, 2.0, 0.0, 1.0, 0.0])
    y, tr = nscore(x, wts=w)
    assert tr.values.tolist() == [2.0, 3.0, 5.0] and np.isfinite(y).all() and np.isfinite(tr.scores).all()
    assert np.allclose(tr.scores, norm.ppf([0.125, 0.5, 0.875]), rtol=1e-12, atol=1e-15)
    assert y[[4, 2, 0]].tolist() == tr.scores.tolist()
    assert y[1] == tr.scores[0] and y[3] == y[5] == tr.scores[2]  # beyond the table: its end scores
    assert tr.zmin == 1.0 and tr.zmax == 9.0  # the limits are those of all the data
    assert backtr(y, tr, None, None).tolist() == [5.0, 2.0, 3.0, 5.0, 2.0, 5.0]
    inside, _ = nscore(np.array([1.0, 2.0, 4.0]), wts=np.array([1.0, 0.0, 1.0]))
    assert inside[1] == pytest.approx((inside[0] * 2 + inside[2]) / 3)  # linear in the table


def test_twenty_thousand_values_in_time() -> None:
    """The limit of the tab «Статистика»: 20 000 values — the table and the round trip in well under a second."""
    g = np.random.default_rng(9)
    x, w = g.lognormal(0.0, 1.0, 20_000), g.uniform(0.2, 4.0, 20_000)
    t0 = time.perf_counter()
    y, tr = nscore(x, wts=w)
    back = backtr(y, tr)
    tails = backtr(g.normal(0.0, 2.0, 20_000), tr, "power", "hyperbolic", 2.0, 1.5)
    seconds = time.perf_counter() - t0
    assert np.allclose(back, x, rtol=1e-12, atol=0) and np.isfinite(tails).all()
    assert seconds < 1.0, f"{seconds:.3f} s"
