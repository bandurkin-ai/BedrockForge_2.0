"""Top cut (ТЗ 8.2.2, 8.2.4; ADR 0018 §7): Т8.2-9, thresholds by domain, the report «до/после», the tools for
choosing a threshold; the percentile rule of BF (`stats/weighted.py`, tests of BF `test_histogram.py`).

Tolerance — 1e−12 relative (8.2.4) for T and the columns; 1e−10 relative for the statistics of the report and
of the analysis against a direct numpy computation (they come from running sums of the sorted sample)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites import topcut as tc
from bf_core.composites.intervals import CompositeError
from bf_core.stats.weighted import pair_sums, sort_groups, warm, weighted_percentiles

NAN = math.nan
REL = 1e-12
STAT = 1e-10


def brute(v: np.ndarray, w: np.ndarray, t: float) -> dict[str, float]:
    """The report of one domain straight from the definitions (rows with a value and a length > 0)."""
    ok = np.isfinite(v) & (w > 0)
    v, w = v[ok], w[ok]
    g = np.minimum(v, t)
    out = {"n": len(v), "n_cut": int((v > t).sum()), "length": math.fsum(w)}
    for key, x in (("before", v), ("after", g)):
        mean = math.fsum(w * x) / math.fsum(w)
        var = math.fsum(w * (x - mean) ** 2) / math.fsum(w)
        out[f"mean_{key}"], out[f"cv_{key}"], out[f"max_{key}"] = mean, math.sqrt(var) / mean, float(x.max())
    out["loss_pct"] = 100.0 * math.fsum(w * (v - g)) / math.fsum(w * v)
    return out


def check_row(row: dict, ref: dict, rel: float = STAT) -> None:
    for key, want in ref.items():
        assert row[key] == pytest.approx(want, rel=rel, abs=1e-13), key


# --- the percentile rule of BF -----------------------------------------------------------------------------


def test_weighted_percentiles_bf_rule() -> None:
    """BF `test_integer_weights_equal_repeated_samples`: every value at the middle of its cumulative weight."""
    v = np.array([1.0, 2.0, 5.0, 9.0])
    w = np.array([1.0, 3.0, 2.0, 1.0])
    pos = (np.cumsum(w) - w / 2) / w.sum()  # 0.071, 0.357, 0.714, 0.929
    assert weighted_percentiles(v, w, [50])[0] == pytest.approx(np.interp(0.5, pos, v), rel=REL)  # 3.2
    assert weighted_percentiles(v, w, [0, 100]).tolist() == [1.0, 9.0]  # outside — the first and the last value
    # the order of the rows does not matter; rows without a value or a weight take no part
    mixed = weighted_percentiles([9.0, NAN, 2.0, 5.0, 1.0, 7.0, 8.0], [1.0, 4.0, 3.0, 2.0, 1.0, 0.0, NAN], [10, 50, 90])
    assert mixed == pytest.approx(weighted_percentiles(v, w, [10, 50, 90]), rel=REL)
    assert np.isnan(weighted_percentiles([NAN], [1.0], [50])).all()


def test_weighted_percentiles_without_weights_is_hazen() -> None:
    """BF `test_unweighted_statistics_match_numpy`."""
    v = np.random.default_rng(1).lognormal(0, 1, 501)
    ps = [5, 25, 50, 75, 97.5]
    assert weighted_percentiles(v, None, ps) == pytest.approx(np.percentile(v, ps, method="hazen"), rel=REL)


def test_ties_keep_the_order_of_the_rows() -> None:
    """Equal values with different weights: the result is that of a stable sort, whatever sort numpy used."""
    rng = np.random.default_rng(3)
    v = rng.integers(0, 6, 5000).astype(np.float64)  # six values, many ties
    w = rng.uniform(0.1, 3.0, 5000)
    order = np.argsort(v, kind="stable")
    sv, sw = v[order], w[order]
    pos = (np.cumsum(sw) - sw / 2) / sw.sum()
    ps = np.linspace(1, 99, 41)
    assert weighted_percentiles(v, w, ps) == pytest.approx(np.interp(ps / 100, pos, sv), rel=REL)
    s = sort_groups(v, w)[0]
    assert np.array_equal(s.v, sv) and np.array_equal(s.w, sw)


def test_pair_sums_is_pairwise_and_exact_enough() -> None:
    rng = np.random.default_rng(5)
    a, b = rng.lognormal(0, 2, 100_003), rng.lognormal(0, 2, 100_003)
    s0, s1 = pair_sums(a, b)
    assert s0 == pytest.approx(math.fsum(a), rel=1e-14) and s1 == pytest.approx(math.fsum(a * b), rel=1e-14)
    assert pair_sums(a[:0], b[:0]) == (0.0, 0.0)
    warm()


# --- Т8.2-9 ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("length", [1.0, 0.7])
def test_t8_2_9_percentile_95_of_1_to_100(length: float) -> None:
    """Т8.2-9. Значения 1…100 равной длины, P = 95: T = 95,5, урезано 5 проб."""
    v = np.arange(1.0, 101.0)
    np.random.default_rng(0).shuffle(v)
    t = tc.topcut(v, np.full(100, length), tc.TopcutParams.of({"percentile": 95}), field="AU")
    assert t.thresholds == {"": pytest.approx(95.5, rel=REL)}
    assert t.report["total"]["n_cut"] == 5 and t.report["domains"][0]["n_cut"] == 5
    assert int(t.flag.sum()) == 5 and set(v[t.flag == 1.0]) == {96.0, 97.0, 98.0, 99.0, 100.0}
    assert t.cut[t.flag == 1.0] == pytest.approx(95.5, rel=REL)
    assert np.array_equal(t.cut[t.flag == 0.0], v[t.flag == 0.0])  # the rest is copied bit for bit
    assert t.report["columns"] == ["AU_CUT", "AU_CUT_F"] and tc.column_names("AU") == ("AU_CUT", "AU_CUT_F")
    row = t.report["domains"][0]
    assert row["cut_pct"] == pytest.approx(5.0) and row["max_before"] == 100.0
    assert row["max_after"] == pytest.approx(95.5, rel=REL)
    assert row["mean_before"] == pytest.approx(50.5, rel=REL)
    assert row["mean_after"] == pytest.approx((5050 - 12.5) / 100, rel=REL)  # 96…100 lose 0,5 + … + 4,5
    assert row["loss_pct"] == pytest.approx(100 * 12.5 / 5050, rel=STAT)


def test_missing_stays_missing() -> None:
    """NaN остаётся NaN: no value — no cut value and no flag; such a row is not counted."""
    v = np.array([1.0, NAN, 50.0, 2.0, NAN, 3.0])
    t = tc.topcut(v, np.ones(6), tc.TopcutParams.of({"value": 10}), field="AU")
    assert np.array_equal(t.cut, [1.0, NAN, 10.0, 2.0, NAN, 3.0], equal_nan=True)
    assert np.array_equal(t.flag, [0.0, NAN, 1.0, 0.0, NAN, 0.0], equal_nan=True)
    assert t.report["total"]["n"] == 4 and t.report["total"]["n_cut"] == 1
    empty = tc.topcut(np.full(3, NAN), np.ones(3), tc.TopcutParams.of({}), field="AU")
    assert np.isnan(empty.cut).all() and np.isnan(empty.flag).all()
    assert empty.thresholds == {"": None} and empty.report["total"]["n"] == 0
    assert empty.report["total"]["mean_before"] is None and empty.report["total"]["loss_pct"] is None


def test_row_without_length_is_cut_but_not_counted() -> None:
    v = np.array([1.0, 2.0, 3.0, 100.0, 200.0])
    ln = np.array([1.0, 1.0, 1.0, 0.0, NAN])
    t = tc.topcut(v, ln, tc.TopcutParams.of({"value": 2.5}), field="AU")
    assert t.cut.tolist() == [1.0, 2.0, 2.5, 2.5, 2.5] and t.flag.tolist() == [0.0, 0.0, 1.0, 1.0, 1.0]
    assert t.report["total"]["n"] == 3 and t.report["total"]["n_cut"] == 1 and t.report["total"]["max_before"] == 3.0
    assert any("без длины: 2" in note for note in t.report["notes"])


# --- domains and the report --------------------------------------------------------------------------------


def domain_data(n: int = 6000, seed: int = 11) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Skewed grades of three domains and rows without a domain; uneven lengths, gaps, zeros and negatives."""
    rng = np.random.default_rng(seed)
    codes = rng.choice([0, 1, 2, -1], n, p=[0.5, 0.3, 0.15, 0.05]).astype(np.int32)
    scale = np.where(codes == 0, 1.0, np.where(codes == 1, 5.0, 0.2))
    v = scale * rng.lognormal(0.0, 1.6, n)
    v[rng.random(n) < 0.03] = NAN
    v[rng.random(n) < 0.02] = 0.0
    v[rng.random(n) < 0.01] = -0.005  # «ниже предела обнаружения» as some laboratories write it
    ln = rng.choice([0.5, 1.0, 1.5, 2.0, 3.7], n)
    return v, ln, codes, ["ОК", "ПР", "ЗАБ"]


def test_percentile_by_domain_and_report_against_definitions() -> None:
    v, ln, codes, names = domain_data()
    t = tc.topcut(v, ln, tc.TopcutParams.of({"percentile": 97.5}), codes=codes, names=names, field="AU")
    assert list(t.thresholds) == ["ОК", "ПР", "ЗАБ", ""]
    rows = {r["domain"]: r for r in t.report["domains"]}
    for k, name in enumerate([*names, ""]):
        sel = codes == (k if name else -1)
        want = weighted_percentiles(v[sel], ln[sel], [97.5])[0]
        assert t.thresholds[name] == pytest.approx(want, rel=REL)
        check_row(rows[name], brute(v[sel], ln[sel], want))
        has = sel & ~np.isnan(v)
        assert np.array_equal(t.cut[has], np.minimum(v[has], t.thresholds[name]))
        assert np.array_equal(t.flag[has], (v[has] > t.thresholds[name]).astype(float))
    assert np.isnan(t.cut[np.isnan(v)]).all() and np.isnan(t.flag[np.isnan(v)]).all()
    # «Всего»: every domain cut at its own threshold
    ok = ~np.isnan(v)
    total = t.report["total"]
    assert total["n"] == int(ok.sum()) and total["n_cut"] == int(np.nansum(t.flag))
    for key, x in (("before", v), ("after", t.cut)):
        mean = math.fsum(ln[ok] * x[ok]) / math.fsum(ln[ok])
        var = math.fsum(ln[ok] * (x[ok] - mean) ** 2) / math.fsum(ln[ok])
        assert total[f"mean_{key}"] == pytest.approx(mean, rel=STAT)
        assert total[f"cv_{key}"] == pytest.approx(math.sqrt(var) / mean, rel=STAT)
        assert total[f"max_{key}"] == float(x[ok].max())
        assert t.report["summary"][key]["fields"]["AU"]["metal"] == pytest.approx(math.fsum(ln[ok] * x[ok]), rel=STAT)
    lost = math.fsum(ln[ok] * (v[ok] - t.cut[ok]))
    assert total["loss_pct"] == pytest.approx(100 * lost / math.fsum(ln[ok] * v[ok]), rel=1e-9)
    assert t.report["summary"]["before"]["count"] == total["n"]


def test_shuffling_the_rows_changes_nothing() -> None:
    v, ln, codes, names = domain_data(seed=12)
    a = tc.topcut(v, ln, tc.TopcutParams.of({"percentile": 99}), codes=codes, names=names, field="AU")
    perm = np.random.default_rng(1).permutation(len(v))
    b = tc.topcut(v[perm], ln[perm], tc.TopcutParams.of({"percentile": 99}), codes=codes[perm], names=names, field="AU")
    assert np.array_equal(a.cut[perm], b.cut, equal_nan=True) and np.array_equal(a.flag[perm], b.flag, equal_nan=True)
    for name in a.thresholds:  # ties of unequal length may move T by the order of the rows — in the last bits only
        assert b.thresholds[name] == pytest.approx(a.thresholds[name], rel=1e-9)


def test_thresholds_by_domain() -> None:
    v, ln, codes, names = domain_data()
    for raw in ("ОК = 12,5; ПР=40;\n НЕТ=3", {"ОК": 12.5, "ПР": "40", "НЕТ": 3}, [{"domain": "ОК", "value": 12.5},
                {"domain": "ПР", "value": 40}, {"domain": "НЕТ", "value": 3}]):  # fmt: skip
        t = tc.topcut(v, ln, tc.TopcutParams.of({"thresholds": raw}), codes=codes, names=names, field="AU")
        assert t.thresholds == {"ОК": 12.5, "ПР": 40.0, "ЗАБ": None, "": None}
        free = (codes == 2) | (codes == -1)  # no threshold — copied, flagged 0
        assert np.array_equal(t.cut[free], v[free], equal_nan=True) and np.nansum(t.flag[free]) == 0
        assert np.nanmax(t.cut[codes == 0]) == 12.5 and np.nanmax(t.cut[codes == 1]) == 40.0
        rows = {r["domain"]: r for r in t.report["domains"]}
        assert rows["ЗАБ"]["threshold"] is None and rows["ЗАБ"]["n_cut"] == 0 and rows["ЗАБ"]["loss_pct"] == 0.0
        assert rows["ЗАБ"]["mean_after"] == rows["ЗАБ"]["mean_before"]
        notes = " ".join(t.report["notes"])
        assert "«НЕТ»" in notes and "ЗАБ" in notes and "без домена" in notes
    one = tc.topcut(v, ln, tc.TopcutParams.of({"thresholds": "=7"}), codes=codes, names=names)  # rows without a domain
    assert one.thresholds[""] == 7.0 and np.nanmax(one.cut[codes == -1]) == 7.0


def test_value_wins_over_thresholds_and_percentile() -> None:
    assert tc.TopcutParams.of({"value": "30,5", "thresholds": "A=1", "percentile": 95}).mode == "value"
    assert tc.TopcutParams.of({"value": None, "thresholds": " ", "percentile": None}) == tc.TopcutParams()
    assert tc.TopcutParams.of({}).percentile == 99.0


@pytest.mark.parametrize(
    ("raw", "param"),
    [
        ({"percentile": 89.9}, "percentile"),
        ({"percentile": 100}, "percentile"),
        ({"percentile": "много"}, "percentile"),
        ({"value": 0}, "value"),
        ({"value": -1}, "value"),
        ({"value": True}, "value"),
        ({"value": NAN}, "value"),
        ({"thresholds": "ОК 30"}, "thresholds"),
        ({"thresholds": "ОК=тридцать"}, "thresholds"),
        ({"thresholds": "ОК=1; ОК=2"}, "thresholds"),
        ({"thresholds": {"ОК": -5}}, "thresholds"),
        ({"thresholds": ";"}, "thresholds"),
        ({"thresholds": 5}, "thresholds"),
    ],
)
def test_bad_parameters_are_errors_with_a_hint(raw: dict, param: str) -> None:
    with pytest.raises(CompositeError) as err:
        tc.TopcutParams.of(raw)
    assert err.value.code == "bad_param" and err.value.param == param
    assert err.value.message and err.value.hint


# --- choosing a threshold ----------------------------------------------------------------------------------


def test_analysis_of_1_to_100() -> None:
    """Equal lengths: deciles by cumulative length are deciles by count — 10 samples each."""
    v = np.arange(1.0, 101.0)
    a = tc.topcut_analysis(v, np.full(100, 2.0), field="AU")
    d = a["domains"][0]
    assert a["field"] == "AU" and a["limits"] == {"decile": 40.0, "percentile": 10.0}
    assert d["n"] == 100 and d["length"] == 200.0 and d["mean"] == pytest.approx(50.5, rel=REL)
    assert d["min"] == 1.0 and d["max"] == 100.0 and d["metal"] == pytest.approx(10100.0, rel=REL)
    assert [x["from"] for x in d["deciles"]] == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
    for k, row in enumerate(d["deciles"]):
        part = v[10 * k : 10 * k + 10]
        assert row["share_pct"] == pytest.approx(100 * part.sum() / 5050, rel=STAT)
        assert row["mean"] == pytest.approx(part.mean(), rel=STAT) and row["max"] == part.max()
    assert [x["from"] for x in d["top"]] == [90, 91, 92, 93, 94, 95, 96, 97, 98, 99]
    assert [x["share_pct"] for x in d["top"]] == pytest.approx([100 * x / 5050 for x in range(91, 101)], rel=1e-9)
    assert d["top_decile_pct"] == pytest.approx(100 * 955 / 5050, rel=STAT) and not d["flag_decile"]
    assert d["top_percentile_pct"] == pytest.approx(100 * 100 / 5050, rel=1e-9) and not d["flag_percentile"]
    k = d["curves"]["p"].index(95.0)
    assert d["curves"]["threshold"][k] == pytest.approx(95.5, rel=REL) and d["curves"]["n_cut"][k] == 5
    assert d["curves"]["p"][0] == 90.0 and d["curves"]["p"][-1] == 99.9 and len(d["curves"]["p"]) == 100


def test_analysis_curves_and_shares_against_definitions() -> None:
    v, ln, codes, names = domain_data(20_000, seed=21)
    a = tc.topcut_analysis(v, ln, codes=codes, names=names, field="AU", percentiles=[90, 95, 97.5, 99, 99.9])
    assert [d["domain"] for d in a["domains"]] == ["ОК", "ПР", "ЗАБ", ""]
    for k, d in enumerate(a["domains"]):
        sel = codes == (k if d["domain"] else -1)
        x, w = v[sel], ln[sel]
        ok = np.isfinite(x)
        x, w = x[ok], w[ok]
        assert d["n"] == len(x) and d["length"] == pytest.approx(w.sum(), rel=REL)
        for j, p in enumerate(d["curves"]["p"]):
            t = weighted_percentiles(x, w, [p])[0]
            ref = brute(x, w, t)
            assert d["curves"]["threshold"][j] == pytest.approx(t, rel=REL)
            assert d["curves"]["mean"][j] == pytest.approx(ref["mean_after"], rel=STAT)
            assert d["curves"]["cv"][j] == pytest.approx(ref["cv_after"], rel=STAT)
            assert d["curves"]["loss_pct"][j] == pytest.approx(ref["loss_pct"], rel=1e-9)
            assert d["curves"]["n_cut"][j] == ref["n_cut"]
        # shares: the metal of a slice of cumulative length, a sample at a boundary split by length
        order = np.argsort(x, kind="stable")
        sx, sw = x[order], w[order]
        edge = np.r_[0.0, np.cumsum(sw)]
        for rows, whole in ((d["deciles"], 100.0), (d["top"], d["top_decile_pct"])):
            assert sum(r["share_pct"] for r in rows) == pytest.approx(whole, rel=1e-9)
            for r in rows:
                a0, a1 = r["from"] / 100 * edge[-1], r["to"] / 100 * edge[-1]
                part = np.clip(np.minimum(edge[1:], a1) - np.maximum(edge[:-1], a0), 0.0, None)
                assert r["metal"] == pytest.approx(math.fsum(part * sx), rel=1e-9)
                assert r["min"] <= r["mean"] <= r["max"]
        q = d["quantiles"]
        assert len(q["p"]) == len(q["z"]) == len(q["value"]) == 129 and q["z"][64] == 0.0 and q["p"][64] == 50.0
        assert np.all(np.diff(q["value"]) >= 0) and q["value"][0] >= d["min"] and q["value"][-1] <= d["max"]
        assert q["value"][64] == pytest.approx(weighted_percentiles(x, w, [50])[0], rel=REL)


def test_analysis_flags_a_nugget() -> None:
    """One sample holds most of the metal: both marks are set; with looser limits — none."""
    v = np.r_[np.full(999, 1.0), 5000.0]
    d = tc.topcut_analysis(v, np.ones(1000))["domains"][0]
    assert d["flag_decile"] and d["flag_percentile"] and d["top_percentile_pct"] > 80
    loose = tc.topcut_analysis(v, np.ones(1000), decile_limit=95, percentile_limit=90)["domains"][0]
    assert not loose["flag_decile"] and not loose["flag_percentile"]
    with pytest.raises(CompositeError):
        tc.topcut_analysis(v, np.ones(1000), decile_limit=140)
    with pytest.raises(CompositeError):
        tc.topcut_analysis(v, np.ones(1000), percentiles=[101])


def test_analysis_hard_samples() -> None:
    """One sample, a constant, an empty domain, all zeros, values far from zero: nothing breaks, no NaN leaks."""
    one = tc.topcut_analysis([3.0], [1.5])["domains"][0]
    assert one["n"] == 1 and one["cv"] == 0.0 and set(one["curves"]["threshold"]) == {3.0}
    assert one["deciles"][0]["share_pct"] == pytest.approx(10.0) and one["curves"]["loss_pct"][0] == 0.0
    const = tc.topcut_analysis(np.full(50, 7.0), np.ones(50))["domains"][0]
    assert const["cv"] == 0.0 and set(const["curves"]["n_cut"]) == {0} and not const["flag_decile"]
    two = tc.topcut_analysis([1.0, NAN, 2.0], [1.0, 1.0, 1.0], codes=[0, 1, 0], names=["A", "B"])["domains"]
    assert two[1] == {"domain": "B", "n": 0, "length": 0.0} and two[0]["n"] == 2
    zeros = tc.topcut_analysis(np.zeros(20), np.ones(20))["domains"][0]
    assert zeros["cv"] is None and zeros["top_decile_pct"] is None and not zeros["flag_decile"]
    assert set(zeros["curves"]["loss_pct"]) == {None}
    # values far from zero: the variance does not drown in the square of the mean
    rng = np.random.default_rng(8)
    far = 1.0e6 + rng.normal(0.0, 1.0e-3, 4000)
    w = rng.uniform(0.5, 2.0, 4000)
    d = tc.topcut_analysis(far, w)["domains"][0]
    mean = math.fsum(w * far) / math.fsum(w)
    cv = math.sqrt(math.fsum(w * (far - mean) ** 2) / math.fsum(w)) / mean
    assert d["cv"] == pytest.approx(cv, rel=1e-6)
    t = tc.topcut(far, w, tc.TopcutParams.of({"percentile": 99}))
    check_row(t.report["domains"][0], brute(far, w, t.thresholds[""]), rel=1e-6)


def test_tick_reports_progress_and_cancels() -> None:
    v, ln, codes, names = domain_data(3000)
    seen: list[float] = []
    tc.topcut(v, ln, tc.TopcutParams.of({}), codes=codes, names=names, tick=seen.append)
    assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) >= 4
    seen.clear()
    tc.topcut_analysis(v, ln, codes=codes, names=names, tick=seen.append)
    assert seen == sorted(seen) and seen[-1] == 1.0

    class Stop(Exception):
        pass

    def stop(fraction: float) -> None:
        raise Stop

    with pytest.raises(Stop):
        tc.topcut(v, ln, tc.TopcutParams.of({}), tick=stop)
    with pytest.raises(Stop):
        tc.topcut_analysis(v, ln, tick=stop)
