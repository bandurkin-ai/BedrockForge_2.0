"""Correlations and the dendrogram (ТЗ 7.27; ADR 0019 §4): Т7.27-1, Т7.27-2 and the tests of BF
`test_dendrogram.py` — correlations against numpy / scipy, weights, missing values, clustering.

Tolerance — 1e−12 for r against a direct count (numpy, scipy, exact fractions); Т7.27-1 — the height of the
join ≤ 1e−12 (0 exactly for equal columns)."""

from __future__ import annotations

import json
import math
from fractions import Fraction

import numpy as np
import pytest
from scipy.cluster.hierarchy import dendrogram as scipy_dendrogram
from scipy.cluster.hierarchy import fcluster
from scipy.stats import rankdata, spearmanr

from bf_core.stats import correlation as corr_mod
from bf_core.stats import dendrogram as tree_mod
from bf_core.stats.correlation import MIN_PAIRS, CorrSpec, average_ranks, block_rows, correlations, pair_moments
from bf_core.stats.dendrogram import LINKAGES, TreeSpec, cluster, dendrogram_chart
from bf_core.stats.weighted import StatsError

NAN = math.nan
ABS = 1e-12
PLAIN = CorrSpec(log=False, negatives="keep")


def refused(code: str, param: str | None, call) -> StatsError:  # type: ignore[no-untyped-def]
    with pytest.raises(StatsError) as e:
        call()
    assert e.value.code == code and e.value.param == param and e.value.message and e.value.hint
    return e.value


def weighted_corr(a: np.ndarray, b: np.ndarray, w: np.ndarray) -> float:
    """The formula of BF `stats/dendrogram.py`, as it is."""
    total = w.sum()
    ma, mb = np.sum(w * a) / total, np.sum(w * b) / total
    da, db = a - ma, b - mb
    den = np.sqrt(np.sum(w * da * da) * np.sum(w * db * db))
    return float(np.sum(w * da * db) / den) if den > 0 else NAN


def bf_matrix(v: np.ndarray, w: np.ndarray, method: str = "pearson", missing: str = "pairwise"):  # type: ignore[no-untyped-def]
    """BF `correlation_matrix`, as it is: a pass per pair."""
    k = v.shape[1]
    present = np.isfinite(v)
    if missing == "complete":
        keep = present.all(axis=1)
        v, w, present = v[keep], w[keep], present[keep]
    r, n = np.eye(k), np.zeros((k, k), dtype=np.int64)
    for i in range(k):
        n[i, i] = int(present[:, i].sum())
        for j in range(i + 1, k):
            both = present[:, i] & present[:, j]
            n[i, j] = n[j, i] = int(both.sum())
            if n[i, j] < MIN_PAIRS:
                r[i, j] = r[j, i] = NAN
                continue
            a, b = v[both, i], v[both, j]
            if method == "spearman":
                a, b = rankdata(a), rankdata(b)
            r[i, j] = r[j, i] = weighted_corr(a, b, w[both])
    return r, n


def exact_r(a, b, w) -> float:  # type: ignore[no-untyped-def]
    """r of a few rows in exact fractions."""
    fa, fb, fw = ([Fraction(float(x)) for x in col] for col in (a, b, w))
    total = sum(fw)
    ma = sum(x * y for x, y in zip(fw, fa, strict=True)) / total
    mb = sum(x * y for x, y in zip(fw, fb, strict=True)) / total
    sab = sum(x * (p - ma) * (q - mb) for x, p, q in zip(fw, fa, fb, strict=True))
    saa = sum(x * (p - ma) ** 2 for x, p in zip(fw, fa, strict=True))
    sbb = sum(x * (q - mb) ** 2 for x, q in zip(fw, fb, strict=True))
    return float(sab) / math.sqrt(float(saa) * float(sbb))


def table(rows: int = 400, seed: int = 5, gaps: float = 0.15):  # type: ignore[no-untyped-def]
    """Lognormal fields AU, AG (follows AU), CU, PB (against AU) with gaps, and lengths of intervals."""
    rng = np.random.default_rng(seed)
    au = rng.lognormal(0.0, 1.0, rows)
    cols = [au, au * rng.lognormal(1.0, 0.2, rows), rng.lognormal(0.0, 1.0, rows), rng.lognormal(0.5, 0.3, rows) / au]
    for c in cols:
        c[rng.random(rows) < gaps] = NAN
    return ["AU", "AG", "CU", "PB"], cols, rng.choice([0.5, 1.0, 1.5, 2.0], rows)


# --- acceptance of ТЗ 7.27 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["pearson", "spearman"])
@pytest.mark.parametrize("linkage", LINKAGES)
def test_t727_1_fields_with_r_one_join_at_height_zero(method: str, linkage: str) -> None:
    """Т7.27-1: fields with r = 1 join first, at the height 0."""
    rng = np.random.default_rng(7271)
    au = rng.lognormal(0.0, 1.0, 500)
    w = rng.choice([1.0, 2.0, 3.5], 500)
    cols = [rng.lognormal(0.0, 1.0, 500), au, rng.lognormal(1.0, 0.5, 500), 2.5 * au, au.copy()]
    names = ["CU", "AU", "ZN", "AU_X", "AU_COPY"]
    r = dendrogram_chart(names, cols, w, CorrSpec(method=method), TreeSpec(linkage=linkage, cut=0.0))
    first, second = r["linkage"][0], r["linkage"][1]
    assert {int(first[0]), int(first[1])} <= {1, 3, 4} and {int(second[0]), int(second[1])} <= {1, 3, 4, 5}
    assert 0.0 <= first[2] <= ABS and 0.0 <= second[2] <= ABS and second[3] == 3
    assert r["correlation"][1][4] == 1.0  # equal columns: exactly 1, the height exactly 0
    assert abs(r["correlation"][1][3] - 1.0) <= ABS
    # the threshold 0 puts them into one group, the others stay alone
    assert r["groups"][1] == r["groups"][3] == r["groups"][4] and r["group_count"] == 3
    # without logarithms too: 2.5·AU is a straight line of AU
    plain = dendrogram_chart(names, cols, None, CorrSpec(method=method, log=False), TreeSpec(linkage=linkage))
    assert plain["linkage"][1][2] <= ABS


def test_t727_2_ward_is_not_offered() -> None:
    """Т7.27-2: Ward is not in the list of linkages and the kernel refuses it."""
    assert LINKAGES == ("average", "complete", "single", "weighted")
    for name in ("ward", "Ward", "centroid", "median"):
        e = refused("bad_param", "linkage", lambda name=name: TreeSpec(linkage=name))
        assert "евклидов" in e.message and "average" in e.hint
        refused("bad_param", "linkage", lambda name=name: TreeSpec.from_settings({"linkage": name}))
    refused("bad_param", "linkage", lambda: TreeSpec(linkage="upgma"))
    r = np.array([[1.0, 0.5], [0.5, 1.0]])
    for name in LINKAGES:
        assert cluster(r, ["a", "b"], TreeSpec(linkage=name))["linkage"] == [[0.0, 1.0, 0.5, 2.0]]


# --- the tests of BF ---------------------------------------------------------------------------------------


def test_correlations_match_numpy_and_scipy() -> None:
    rng = np.random.default_rng(1)
    a = rng.normal(size=300)
    b = a * 0.7 + rng.normal(size=300) * 0.5
    c = rng.normal(size=300)
    got = correlations([a, b, c], None, PLAIN)
    assert np.allclose(got.r, np.corrcoef(np.vstack([a, b, c])), rtol=0, atol=ABS) and (got.n == 300).all()
    assert got.counts == {"rows": 300, "empty": 0, "bad_weight": 0, "incomplete": 0, "used": 300}
    rs = correlations([a, b, c], None, CorrSpec(method="spearman", log=False, negatives="keep"))
    assert rs.r[0, 1] == pytest.approx(spearmanr(a, b).statistic, abs=ABS)
    assert rs.r[1, 2] == pytest.approx(spearmanr(b, c).statistic, abs=ABS)
    # the logarithms: r of ln of positive values
    la, lb = np.exp(a), np.exp(b)
    assert correlations([la, lb], None, CorrSpec()).r[0, 1] == pytest.approx(np.corrcoef(a, b)[0, 1], abs=ABS)


def test_integer_weights_equal_repeated_rows_and_missing_values() -> None:
    a, b, w = np.array([1.0, 2, 3, 5]), np.array([2.0, 1, 4, 4]), np.array([1.0, 2, 1, 3])
    rep = np.repeat(np.arange(4), w.astype(int))
    got = correlations([a, b], w, PLAIN)
    assert got.r[0, 1] == pytest.approx(np.corrcoef(a[rep], b[rep])[0, 1], abs=ABS)
    assert got.r[0, 1] == pytest.approx(weighted_corr(a, b, w), abs=ABS)
    v = np.array([[1, 2, NAN], [2, 3, 1], [3, 5, 2], [4, 4, 3], [5, 7, NAN]], dtype=float)
    pairwise = correlations(list(v.T), None, PLAIN)
    assert pairwise.n[0, 1] == 5 and pairwise.n[0, 2] == 3 and pairwise.n[2, 2] == 3
    assert pairwise.counts == {"rows": 5, "empty": 0, "bad_weight": 0, "incomplete": 0, "used": 5}
    complete = correlations(list(v.T), None, CorrSpec(log=False, missing="complete", negatives="keep"))
    assert complete.n[0, 1] == 3 and complete.r[0, 1] != pytest.approx(pairwise.r[0, 1])
    assert complete.counts == {"rows": 5, "empty": 0, "bad_weight": 0, "incomplete": 2, "used": 3}
    assert complete.r[0, 1] == pytest.approx(np.corrcoef(v[1:4, 0], v[1:4, 1])[0, 1], abs=ABS)


def test_correlated_fields_join_first() -> None:
    r = np.array([[1, 0.9, 0.1, -0.8], [0.9, 1, 0.2, -0.7], [0.1, 0.2, 1, 0.0], [-0.8, -0.7, 0.0, 1]])
    names = ["AU", "AG", "CU", "SIO2"]
    t = cluster(r, names)
    assert t["linkage"][0][:2] == [0.0, 1.0] and t["linkage"][0][2] == pytest.approx(0.1)
    # 1 − |r|: SIO2, which goes against AU and AG, is close to them
    t2 = cluster(r, names, TreeSpec(distance="1-abs"))
    assert t2["leaves"][-1] == "CU" or t2["leaves"][0] == "CU"
    assert len(t["icoord"]) == 3 and sorted(t["leaves"]) == ["AG", "AU", "CU", "SIO2"]
    assert TreeSpec.from_settings({"distance": "1-|r|"}).distance == "1-abs"  # the name of BF
    assert t["max_distance"] == pytest.approx(max(row[2] for row in t["linkage"]))


def test_chart_of_the_bf_table() -> None:
    """The table of BF `test_dendrogram_api`: AG follows AU; PB has «below detection» values."""
    rng = np.random.default_rng(3)
    au = rng.lognormal(0, 1, 120)
    ag = au * rng.lognormal(1, 0.2, 120)
    cu = rng.lognormal(0, 1, 120)
    pb = np.where(np.arange(120) % 10 == 0, -0.01, rng.lognormal(0, 1, 120))
    length = 1.0 + np.arange(120) % 2
    names = ["AU", "AG", "CU", "PB"]
    r = dendrogram_chart(names, [au, ag, cu, pb], length, units=["г/т", "г/т", "%", None])
    assert r["fields"] == names and r["units"] == ["г/т", "г/т", "%", None]
    assert r["rows"] == 120 and r["used"] == 120 and r["nonpositive_log"] == 0 and r["conventions"]
    assert r["spec"] == {"method": "pearson", "log": True, "missing": "pairwise", "negatives": "half_abs",
                         "exclude_zero": False, "weighted": True, "linkage": "average", "distance": "1-r"}  # fmt: skip
    assert sorted(names[int(i)] for i in r["linkage"][0][:2]) == ["AG", "AU"]
    assert r["correlation"][0][1] > 0.9 and r["pairs"][0][3] == 120
    assert r["field_counts"][3] == {"name": "PB", "missing": 0, "negative": 12, "zero_excluded": 0, "nonpositive": 0,
                                    "used": 120}  # fmt: skip
    # the keys the window reads (`DendrogramResult` of the interface)
    assert {"fields", "units", "correlation", "pairs", "rows", "used", "nonpositive_log", "linkage", "order",
            "icoord", "dcoord", "max_distance", "groups", "conventions"} <= set(r)  # fmt: skip
    assert r["correlation"][0][3] == pytest.approx(weighted_corr(np.log(au), np.log(np.where(pb < 0, 0.005, pb)),
                                                                 length), abs=ABS)  # fmt: skip
    # negatives kept: ln has none of them, the pair loses these rows
    kept = dendrogram_chart(names, [au, ag, cu, pb], length, CorrSpec(negatives="keep"))
    assert kept["field_counts"][3]["nonpositive"] == 12 and kept["pairs"][0][3] == 108 and kept["pairs"][0][1] == 120
    assert kept["nonpositive_log"] == 12 and kept["used"] == 120 and kept["units"] == [None] * 4
    some = dendrogram_chart(["AU", "CU", "length"], [au, cu, length], None,
                            CorrSpec(method="spearman", log=False), TreeSpec(linkage="complete"))  # fmt: skip
    assert some["fields"] == ["AU", "CU", "length"] and some["spec"]["weighted"] is False
    refused("bad_param", "fields", lambda: dendrogram_chart(["AU"], [au]))
    refused("bad_param", "fields", lambda: dendrogram_chart(["AU", "au"], [au, au]))  # one field named twice
    json.dumps(r, allow_nan=False)
    json.dumps(some, allow_nan=False)


# --- correlations ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["pearson", "spearman"])
@pytest.mark.parametrize("missing", ["pairwise", "complete"])
@pytest.mark.parametrize("weighted", [False, True])
def test_matrix_equals_a_pass_per_pair(method: str, missing: str, weighted: bool) -> None:
    """Every r and n of the matrix against the loop of BF over the pairs (with gaps, weights, ties)."""
    names, cols, length = table(700, seed=11)
    cols[2] = np.round(cols[2], 1)  # ties in the ranks
    w = length if weighted else np.ones(700)
    got = correlations(cols, length if weighted else None,
                       CorrSpec(method=method, log=False, missing=missing, negatives="keep"))
    r, n = bf_matrix(np.column_stack(cols), w, method, missing)
    assert np.array_equal(got.n, n)
    assert np.allclose(got.r, r, rtol=0, atol=ABS)
    assert np.array_equal(got.r, got.r.T) and (np.diag(got.r) == 1.0).all()
    # by logarithms: the same of ln
    logs = correlations(cols, length if weighted else None, CorrSpec(method=method, missing=missing))
    with np.errstate(divide="ignore"):  # the rounded zeros have no logarithm: −inf is «missing» for BF
        r, n = bf_matrix(np.log(np.column_stack(cols)), w, method, missing)
    assert np.array_equal(logs.n, n) and np.allclose(logs.r, r, rtol=0, atol=ABS)
    assert logs.fields[2]["nonpositive"] == int(np.count_nonzero(cols[2] == 0.0)) > 0


@pytest.mark.parametrize("method", ["pearson", "spearman"])
def test_no_row_with_every_field_and_many_fields(method: str) -> None:
    """Every row lacks a field (all the sums go by pairs); thirty fields (the tables of the pairs are large)."""
    rng = np.random.default_rng(31)
    v = rng.normal(size=(600, 5)) + rng.normal(size=(600, 1))
    v[np.arange(600), rng.integers(0, 5, 600)] = NAN
    spec = CorrSpec(method=method, log=False, negatives="keep")
    got = correlations(list(v.T), None, spec)
    r, n = bf_matrix(v, np.ones(600), method)
    assert np.array_equal(got.n, n) and np.allclose(got.r, r, rtol=0, atol=ABS)
    assert got.counts == {"rows": 600, "empty": 0, "bad_weight": 0, "incomplete": 0, "used": 600}
    wide = rng.normal(size=(300, 30)) + rng.normal(size=(300, 1))
    wide[rng.random(wide.shape) < 0.1] = NAN
    w = rng.uniform(0.5, 2.0, 300)
    got = correlations(list(wide.T), w, spec)
    r, n = bf_matrix(wide, w, method)
    assert np.array_equal(got.n, n) and np.allclose(got.r, r, rtol=0, atol=ABS)
    assert len(dendrogram_chart([f"F{i}" for i in range(30)], list(wide.T), w, spec)["linkage"]) == 29


def test_spearman_is_scipy_on_the_shared_rows() -> None:
    names, cols, _ = table(500, seed=2, gaps=0.3)
    cols[1] = np.round(cols[1])  # many ties
    got = correlations(cols, None, CorrSpec(method="spearman", log=False))
    for i in range(4):
        for j in range(i + 1, 4):
            both = np.isfinite(cols[i]) & np.isfinite(cols[j])
            assert got.n[i, j] == both.sum()
            assert got.r[i, j] == pytest.approx(spearmanr(cols[i][both], cols[j][both]).statistic, abs=ABS)
    v = np.array([3.0, NAN, 1.0, 3.0, 2.0, 3.0])
    assert np.array_equal(average_ranks(v), [4.0, NAN, 1.0, 4.0, 2.0, 4.0], equal_nan=True)
    assert np.array_equal(average_ranks(cols[0][np.isfinite(cols[0])]), rankdata(cols[0][np.isfinite(cols[0])]))


def test_rules_for_negatives_zeros_and_weights() -> None:
    a = np.array([1.0, -2.0, 0.0, 4.0, 5.0, NAN, 7.0, 8.0, np.inf, 3.0])
    b = np.array([2.0, 3.0, 1.0, -1.0, 6.0, 5.0, 0.0, 9.0, 2.0, 4.0])
    w = np.array([1.0, 1.0, 2.0, 1.0, NAN, 1.0, 1.0, 0.0, 1.0, -1.0])

    def direct(va, vb, ww):  # type: ignore[no-untyped-def]
        ok = np.isfinite(va) & np.isfinite(vb) & np.isfinite(ww) & (ww > 0)
        return weighted_corr(va[ok], vb[ok], ww[ok]), int(ok.sum())

    inf = np.where(np.isinf(a), NAN, a)
    keep = correlations([a, b], w, PLAIN)
    assert (keep.r[0, 1], keep.n[0, 1]) == pytest.approx(direct(inf, b, w), abs=ABS)
    assert keep.counts == {"rows": 10, "empty": 0, "bad_weight": 3, "incomplete": 2, "used": 5}
    assert keep.fields[0] == {"missing": 2, "negative": 1, "zero_excluded": 0, "nonpositive": 0, "used": 5}
    half = correlations([a, b], w, CorrSpec(log=False, negatives="half_abs"))
    ha, hb = np.where(inf < 0, -inf / 2, inf), np.where(b < 0, -b / 2, b)
    assert (half.r[0, 1], half.n[0, 1]) == pytest.approx(direct(ha, hb, w), abs=ABS)
    out = correlations([a, b], w, CorrSpec(log=False, negatives="exclude", exclude_zero=True))
    ea, eb = np.where(inf <= 0, NAN, inf), np.where(b <= 0, NAN, b)
    assert out.n[0, 1] == direct(ea, eb, w)[1] == 1 and math.isnan(out.r[0, 1])
    assert out.fields[1] == {"missing": 0, "negative": 1, "zero_excluded": 1, "nonpositive": 0, "used": 5}
    # ln: values ≤ 0 have none; they are counted and never become 0
    logs = correlations([a, b], None, CorrSpec(negatives="keep"))
    with np.errstate(invalid="ignore", divide="ignore"):
        la, lb = np.where(inf > 0, np.log(inf), NAN), np.where(b > 0, np.log(b), NAN)
    assert (logs.r[0, 1], logs.n[0, 1]) == pytest.approx(direct(la, lb, np.ones(10)), abs=ABS)
    assert logs.fields[0]["nonpositive"] == 2 and logs.fields[1]["nonpositive"] == 2
    # a row without any value is empty whatever its weight
    pair = [np.array([NAN, 1.0, 2.0, 4.0]), np.array([NAN, 2.0, 1.0, 5.0])]
    e = correlations(pair, np.array([NAN, 1, 1, 1.0]), PLAIN)
    assert e.counts == {"rows": 4, "empty": 1, "bad_weight": 0, "incomplete": 0, "used": 3}


def test_fewer_than_three_shared_rows_is_refused_with_the_names() -> None:
    a = np.array([1.0, 2.0, 3.0, 4.0, NAN, NAN])
    b = np.array([NAN, NAN, NAN, 1.0, 2.0, 3.0])
    c = np.array([1.0, 3.0, 2.0, 5.0, 4.0, 6.0])
    got = correlations([a, b, c], None, PLAIN)
    assert got.n[0, 1] == 1 and math.isnan(got.r[0, 1]) and math.isfinite(got.r[0, 2])
    e = refused("too_few_values", "fields", lambda: dendrogram_chart(["AU", "AG", "CU"], [a, b, c], None, PLAIN))
    assert "«AU» и «AG» — 1" in e.message and "CU" not in e.message
    whole = CorrSpec(log=False, missing="complete")
    e = refused("too_few_values", "fields", lambda: dendrogram_chart(["AU", "AG", "CU"], [a, b, c], None, whole))
    assert "попарно" in e.hint
    # two rows are not enough, three are
    two = [np.array([1.0, 2.0, NAN, NAN]), np.array([3.0, 5.0, NAN, 1.0])]
    refused("too_few_values", "fields", lambda: dendrogram_chart(["A", "B"], two, None, PLAIN))
    three = [np.array([1.0, 2.0, 4.0, NAN]), np.array([3.0, 5.0, 4.0, 1.0])]
    assert dendrogram_chart(["A", "B"], three, None, PLAIN)["pairs"][0][1] == 3
    # no rows at all; a field without spread
    refused("too_few_values", "fields", lambda: dendrogram_chart(["A", "B"], [np.zeros(0), np.zeros(0)]))
    flat = [np.array([1.0, 2.0, 3.0, 4.0]), np.array([5.0, 5.0, 5.0, 5.0]), np.array([2.0, 1.0, 4.0, 3.0])]
    e = refused("no_spread", "fields", lambda: dendrogram_chart(["A", "FLAT", "C"], flat, None, PLAIN))
    assert "«FLAT»" in e.message and "«A»" not in e.message and "«C»" not in e.message
    e = refused("no_spread", "fields", lambda: dendrogram_chart(["A", "FLAT"], flat[:2], None, PLAIN))
    assert "«A» и «FLAT»" in e.message
    for spec in (lambda: CorrSpec(method="kendall"), lambda: CorrSpec(missing="rows"), lambda: CorrSpec(log=1),  # type: ignore[arg-type]
                 lambda: CorrSpec(negatives="drop")):  # fmt: skip
        with pytest.raises(StatsError) as bad:
            spec()
        assert bad.value.code == "bad_param" and bad.value.param in ("method", "missing", "log", "negatives")
    assert CorrSpec.from_settings({"method": "spearman", "log": False, "field": "AU", "missing": None}) == CorrSpec(
        method="spearman", log=False
    )
    # the defaults of the window (and of BF): by logarithms, a half of the modulus for values < 0
    assert CorrSpec() == CorrSpec("pearson", True, "pairwise", "half_abs", False)


def test_hard_data() -> None:
    """Values far from zero, a pair whose rows lie far from the rest of its fields, huge and tiny numbers."""
    rng = np.random.default_rng(9)
    n = 40
    a = 1e9 + rng.normal(size=n)
    b = 1e9 + 0.5 * (a - 1e9) + rng.normal(size=n)
    w = rng.choice([0.5, 1.0, 2.0], n)
    got = correlations([a, b], w, PLAIN)
    assert got.r[0, 1] == pytest.approx(exact_r(a, b, w), abs=ABS)
    m = pair_moments([a, b], w)
    assert m.mean[0, 1] == pytest.approx(float(np.sum(w * a) / w.sum()), rel=1e-15)
    assert m.total[0, 1] == pytest.approx(w.sum(), rel=1e-15) and m.n[0, 1] == n
    assert m.sxx[0, 1] / m.total[0, 1] == pytest.approx(float(np.sum(w * (a - np.sum(w * a) / w.sum()) ** 2) / w.sum()),
                                                        rel=1e-9)  # fmt: skip
    # the rows shared with C are a tight cloud far from the mean of A and B: the sums are taken about its own mean
    big = 5000
    a2 = np.concatenate([rng.normal(0.0, 1.0, big), a])
    b2 = np.concatenate([rng.normal(0.0, 1.0, big), b])
    c2 = np.concatenate([np.full(big, NAN), rng.normal(size=n)])
    got = correlations([a2, b2, c2], None, PLAIN)
    assert got.n[0, 2] == n and got.r[0, 2] == pytest.approx(exact_r(a, c2[big:], np.ones(n)), abs=ABS)
    assert got.r[1, 2] == pytest.approx(exact_r(b, c2[big:], np.ones(n)), abs=ABS)
    assert got.r[0, 1] == pytest.approx(np.corrcoef(a2, b2)[0, 1], abs=ABS)
    # tiny and huge values, by logarithms
    t = np.array([1e-300, 1e-200, 1e-100, 1.0, 1e100, 1e200, 1e300])
    u = t**0.5 * np.array([1.0, 2.0, 0.5, 3.0, 1.0, 0.7, 2.0])
    assert correlations([t, u], None, CorrSpec()).r[0, 1] == pytest.approx(np.corrcoef(np.log(t), np.log(u))[0, 1],
                                                                          abs=ABS)  # fmt: skip
    # squares that overflow: no r, and the chart says so instead of a wrong tree
    over = correlations([t, u], None, PLAIN)
    assert math.isnan(over.r[0, 1])
    refused("no_spread", "fields", lambda: dendrogram_chart(["T", "U"], [t, u], None, PLAIN))
    # r stays within −1 … 1
    x = rng.normal(size=1000)
    opposite = correlations([x, -3.0 * x + 7.0, x], None, PLAIN)
    assert opposite.r[0, 1] == pytest.approx(-1.0, abs=ABS) and -1.0 <= opposite.r[0, 1] and opposite.r[0, 2] == 1.0


@pytest.mark.parametrize("method", ["pearson", "spearman"])
def test_blocks_and_threads_give_the_same_bits(method: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """More rows than a block: the answer is that of a direct count and does not depend on the threads."""
    names, cols, length = table(40_000, seed=21, gaps=0.05)
    assert block_rows(1) == 65536 and block_rows(10) == 65536 and block_rows(100) == 10485 and block_rows(1000) == 4096
    monkeypatch.setattr(corr_mod, "_CELLS", 4096 * 4)  # blocks of 4096 rows: ten of them
    assert block_rows(4) == 4096
    spec = CorrSpec(method=method)
    one = correlations(cols, length, spec, threads=1)
    for threads in (2, 4):
        more = correlations(cols, length, spec, threads=threads)
        assert np.array_equal(one.r, more.r) and np.array_equal(one.n, more.n) and one.counts == more.counts
    r, n = bf_matrix(np.log(np.column_stack(cols)), length, method)
    assert np.array_equal(one.n, n) and np.allclose(one.r, r, rtol=0, atol=ABS)


# --- the tree ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("linkage", LINKAGES)
def test_tree_is_that_of_scipy(linkage: str) -> None:
    names, cols, length = table(300, seed=4)
    names = [*names, "X5", "X6", "X7"]
    rng = np.random.default_rng(8)
    cols = [*cols, rng.lognormal(size=300), cols[0] * rng.lognormal(0, 0.5, 300), rng.lognormal(size=300)]
    r = dendrogram_chart(names, cols, length, CorrSpec(), TreeSpec(linkage=linkage, cut=0.7))
    z = np.array(r["linkage"])
    ref = scipy_dendrogram(z, labels=names, no_plot=True)
    assert r["leaves"] == ref["ivl"] and r["order"] == ref["leaves"]

    def brackets(icoord, dcoord):  # type: ignore[no-untyped-def]
        return np.array(sorted([*i, *d] for i, d in zip(icoord, dcoord, strict=True)), dtype=np.float64)

    assert np.array_equal(brackets(r["icoord"], r["dcoord"]), brackets(ref["icoord"], ref["dcoord"]))
    # the brackets go in the order of the rows of `linkage`
    assert [d[1] for d in r["dcoord"]] == [row[2] for row in r["linkage"]]
    # the groups of the threshold are those of scipy, numbered from left to right
    flat = fcluster(z, t=0.7, criterion="distance")
    same = [[flat[i] == flat[j] for j in range(7)] for i in range(7)]
    assert [[r["groups"][i] == r["groups"][j] for j in range(7)] for i in range(7)] == same
    along = [r["groups"][i] for i in r["order"]]
    assert along == sorted(along) and r["group_count"] == len(set(flat)) == max(r["groups"])
    for t, row in enumerate(r["linkage"]):
        under = r["groups"][int(row[0])] if row[0] < 7 else r["link_groups"][int(row[0]) - 7]
        assert r["link_groups"][t] == (under if row[2] <= 0.7 else None)
    assert r["cut"] == 0.7
    none = dendrogram_chart(names, cols, length, CorrSpec(), TreeSpec(linkage=linkage))
    assert [none[key] for key in ("cut", "groups", "link_groups", "group_count")] == [None] * 4
    whole = dendrogram_chart(names, cols, length, CorrSpec(), TreeSpec(linkage=linkage, cut=2.0))
    assert whole["group_count"] == 1 and set(whole["link_groups"]) == {1}


def test_tree_spec_is_checked() -> None:
    for cut in (-0.1, NAN, math.inf, "0.5", True):
        refused("bad_param", "cut", lambda cut=cut: TreeSpec(cut=cut))  # type: ignore[arg-type]
    refused("bad_param", "distance", lambda: TreeSpec(distance="r"))
    assert TreeSpec.from_settings({"linkage": "single", "distance": "1-abs", "cut": 1, "v": 1, "fields": ["A"]}) == (
        TreeSpec(linkage="single", distance="1-abs", cut=1.0)
    )
    assert TreeSpec.from_settings({"linkage": None, "cut": None}) == TreeSpec()
    refused("bad_param", "fields", lambda: cluster(np.eye(1), ["A"]))
    refused("too_few_values", "fields", lambda: cluster(np.array([[1.0, NAN], [NAN, 1.0]]), ["A", "B"]))


def test_tick_reports_and_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    names, cols, length = table(30_000, seed=6)
    monkeypatch.setattr(corr_mod, "_CELLS", 4096 * 4)
    for method in ("pearson", "spearman"):
        seen: list[float] = []
        dendrogram_chart(names, cols, length, CorrSpec(method=method), tick=seen.append)
        assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) > 5

        class Stop(Exception):
            pass

        def stop(f: float) -> None:
            raise Stop

        for threads in (1, 3):
            with pytest.raises(Stop):
                correlations(cols, length, CorrSpec(method=method), tick=stop, threads=threads)


def test_warm_compiles() -> None:
    tree_mod.warm()
