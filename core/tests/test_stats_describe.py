"""Selection of the values of a chart and descriptive statistics (ТЗ 7.25; ADR 0019 §3, §4): Т7.25-1, Т7.25-2,
the tests of BF `test_histogram.py` (describe, filters and counts), the rule of the percentiles without weights
against numpy `hazen`, missing values and bad weights.

Tolerance — 1e−12 relative against a direct computation (`math.fsum`, numpy); the counts are exact."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from bf_core.stats import describe as ds
from bf_core.stats import select as sl
from bf_core.stats.select import SampleFilter, sample_weights, select_sample, select_values
from bf_core.stats.weighted import StatsError, sort_groups, weighted_percentiles

NAN = math.nan
REL = 1e-12
NONE = SampleFilter(weight="none")


def srt(v, w=None):  # type: ignore[no-untyped-def]
    """A sample sorted once (`w` None — equal weights)."""
    v = np.asarray(v, dtype=np.float64)
    return sort_groups(v, np.ones(v.shape) if w is None else w)[0]


def brute(v: np.ndarray, w: np.ndarray) -> dict[str, float]:
    """The statistics straight from the definitions of ADR 0019 §4 (every row has a value and a weight > 0)."""
    total = math.fsum(w)
    mean = math.fsum(w * v) / total
    var = math.fsum(w * (v - mean) ** 2) / total
    std = math.sqrt(var)
    order = np.argsort(v, kind="stable")
    sv, sw = v[order], w[order]
    pos = (np.cumsum(sw) - sw / 2) / total
    out = {
        "count": len(v), "weight_sum": total, "min": float(v.min()), "max": float(v.max()), "mean": mean,
        "variance": var, "std": std, "cv": std / mean, "skewness": math.fsum(w * (v - mean) ** 3) / total / std**3,
        "median": float(np.interp(0.5, pos, sv)),
    }  # fmt: skip
    for p in ds.PERCENTILES:
        out[f"p{p}"] = float(np.interp(p / 100.0, pos, sv))
    lv, lw = np.log(v[v > 0]), w[v > 0]
    lmean = math.fsum(lw * lv) / math.fsum(lw)
    out["mean_ln"], out["variance_ln"] = lmean, math.fsum(lw * (lv - lmean) ** 2) / math.fsum(lw)
    out["geometric_mean"] = math.exp(lmean)
    return out


def check(stats: dict, ref: dict, rel: float = REL) -> None:
    for key, want in ref.items():
        if key in ("mean_ln", "variance_ln", "geometric_mean"):
            got = stats["log"][key]
        elif key.startswith("p") and key[1:2].isdigit():
            got = stats["percentiles"][key[1:]]
        else:
            got = stats[key]
        assert got == pytest.approx(want, rel=rel, abs=1e-300), key


# --- acceptance of ТЗ 7.25 ---------------------------------------------------------------------------------


def test_t725_1_length_weighted_mean() -> None:
    """Т7.25-1: the mean weighted by length = Σ(cᵢ·lᵢ) / Σ lᵢ."""
    rng = np.random.default_rng(7251)
    grade = rng.lognormal(0.3, 1.1, 4000)
    frm = np.cumsum(rng.uniform(0.0, 0.4, 4000))
    length = rng.choice([0.5, 1.0, 1.5, 2.0, 3.3], 4000)
    flt = SampleFilter(weight="length")
    sel = select_sample(grade, flt, length=(frm + length) - frm)
    w = (frm + length) - frm
    stats = ds.describe(sel.sort(), weighted=flt.weighted)
    assert stats["mean"] == pytest.approx(math.fsum(grade * w) / math.fsum(w), rel=REL)
    assert stats["weight_sum"] == pytest.approx(math.fsum(w), rel=REL) and stats["count"] == 4000
    # the plain statistics stand beside: the same rows, every weight 1
    plain = ds.describe(sel.sort(), weighted=False)
    assert plain["mean"] == pytest.approx(math.fsum(grade) / 4000, rel=REL) and plain["weight_sum"] == 4000.0
    # a tiny hand case: 1 g/t over 1 m and 4 g/t over 3 m
    hand = ds.describe(select_sample([1.0, 4.0], flt, length=[1.0, 3.0]).sort())
    assert hand["mean"] == 3.25


def test_t725_2_top_cut_replace_keeps_the_number_of_samples() -> None:
    """Т7.25-2: the top cut «заменить» makes the maximum equal to the cut, the number of samples is the same."""
    rng = np.random.default_rng(7252)
    v = rng.lognormal(0.0, 1.5, 3000)
    cut = float(np.percentile(v, 97.5))
    before = select_values(v, None, NONE)
    after = select_values(v, None, SampleFilter(weight="none", top_cut=cut))  # the default top mode is «заменить»
    assert after.n == before.n == 3000 and after.counts["used"] == 3000
    assert after.counts["capped"] == int((v > cut).sum()) == 75 and after.counts["top_excluded"] == 0
    stats = ds.describe(after.sort(), weighted=False)
    assert stats["max"] == cut and stats["count"] == 3000
    assert np.array_equal(after.values, np.minimum(v, cut))
    assert stats["mean"] == pytest.approx(math.fsum(np.minimum(v, cut)) / 3000, rel=REL)
    # «исключить» drops them instead
    out = select_values(v, None, SampleFilter(weight="none", top_cut=cut, top_mode="exclude"))
    assert out.n == 2925 and out.counts["top_excluded"] == 75 and out.counts["capped"] == 0


# --- the tests of BF `test_histogram.py` -------------------------------------------------------------------


def test_unweighted_statistics_match_numpy() -> None:
    """BF `test_unweighted_statistics_match_numpy`; the percentiles without weights are numpy `hazen`."""
    v = np.random.default_rng(1).lognormal(0, 1, 501)
    s = ds.describe(srt(v), weighted=False)
    assert s["mean"] == pytest.approx(v.mean(), rel=REL)
    assert s["variance"] == pytest.approx(v.var(), rel=REL)  # the population variance
    assert s["std"] == pytest.approx(v.std(), rel=REL)
    assert s["median"] == pytest.approx(np.percentile(v, 50, method="hazen"), rel=REL)
    for p in ds.PERCENTILES:
        assert s["percentiles"][f"{p:g}"] == pytest.approx(np.percentile(v, p, method="hazen"), rel=REL)
    assert list(s["percentiles"]) == ["5", "10", "25", "50", "75", "90", "95", "97.5", "99"]
    assert s["log"]["geometric_mean"] == pytest.approx(np.exp(np.log(v).mean()), rel=REL)
    assert s["log"]["variance_ln"] == pytest.approx(np.log(v).var(), rel=REL) and s["log"]["count"] == 501
    assert s["cv"] == pytest.approx(v.std() / v.mean(), rel=REL)
    d = v - v.mean()
    assert s["skewness"] == pytest.approx(np.mean(d**3) / v.std() ** 3, rel=1e-11)
    # with every weight 1 the weighted statistics are the same numbers
    check(ds.describe(srt(v)), {k: x for k, x in s.items() if isinstance(x, float)})
    assert ds.hazen(srt(v), [0, 100]).tolist() == [v.min(), v.max()]


def test_integer_weights_equal_repeated_samples() -> None:
    """BF `test_integer_weights_equal_repeated_samples`."""
    v = np.array([1.0, 2.0, 5.0, 9.0])
    w = np.array([1.0, 3.0, 2.0, 1.0])
    rep = np.repeat(v, w.astype(int))
    s = ds.describe(srt(v, w))
    assert s["mean"] == pytest.approx(rep.mean(), rel=REL) and s["variance"] == pytest.approx(rep.var(), rel=REL)
    assert s["skewness"] == pytest.approx(np.mean((rep - rep.mean()) ** 3) / rep.std() ** 3, rel=REL)
    assert s["weight_sum"] == 7 and s["count"] == 4
    # the percentiles follow GSLIB: every value at the middle of its cumulative weight — not the repeated sample
    pos = (np.cumsum(w) - w / 2) / w.sum()  # 0.071, 0.357, 0.714, 0.929
    assert s["median"] == pytest.approx(np.interp(0.5, pos, v), rel=REL)  # 3.2
    assert s["median"] == weighted_percentiles(v, w, [50])[0]


ROWS = [  # from, to, AU, density — the table of BF `test_histogram_api_weights_filters_and_counts`
    (0, 1, 1.0, 2.0), (1, 3, 2.0, 2.5), (3, 4, -0.01, 2.7), (4, 6, 0.0, NAN), (6, 7, NAN, 2.6), (7, 9, 40.0, 3.0),
]  # fmt: skip
AU = np.array([r[2] for r in ROWS])
LENGTH = np.array([r[1] - r[0] for r in ROWS], dtype=np.float64)
DENSITY = np.array([r[3] for r in ROWS])


def stats_of(flt: SampleFilter) -> tuple[dict, dict]:
    sel = select_sample(AU, flt, length=LENGTH, weight_values=DENSITY)
    return sel.counts, ds.describe(sel.sort(), weighted=flt.weighted)


def test_filters_and_counts_of_bf() -> None:
    """BF `test_histogram_api_weights_filters_and_counts` without the database."""
    counts, plain = stats_of(NONE)
    assert counts == {
        "rows": 6, "missing": 1, "bad_weight": 0, "negative": 1, "zero_excluded": 0, "bottom_capped": 0,
        "bottom_excluded": 0, "capped": 0, "top_excluded": 0, "used": 5,
    }  # fmt: skip
    assert list(counts) == list(sl.COUNT_KEYS)
    assert plain["mean"] == pytest.approx((1 + 2 - 0.01 + 0 + 40) / 5, rel=REL)

    v, w = np.array([1, 2, 0.005, 0, 40]), np.array([1, 2, 1, 2, 2])
    sel = select_sample(AU, SampleFilter(weight="length", negatives="half_abs"), length=LENGTH)
    weighted, unweighted = ds.describe_both(sel.sort())
    assert weighted["mean"] == pytest.approx((v * w).sum() / w.sum(), rel=REL)
    assert unweighted["mean"] == pytest.approx(v.mean(), rel=REL)
    assert sel.values.tolist() == [1.0, 2.0, 0.005, 0.0, 40.0] and sel.rows.tolist() == [0, 1, 2, 3, 5]

    # length × density: the row without density has no weight and is left out
    counts, tonnage = stats_of(SampleFilter(weight="length_field", weight_field="DENSITY"))
    assert counts["bad_weight"] == 1 and counts["used"] == 4 and counts["missing"] == 1
    vt, wt = np.array([1, 2, -0.01, 40]), np.array([1 * 2.0, 2 * 2.5, 1 * 2.7, 2 * 3.0])
    assert tonnage["mean"] == pytest.approx((vt * wt).sum() / wt.sum(), rel=REL)
    assert tonnage["weight_sum"] == pytest.approx(wt.sum(), rel=REL)

    counts, cut = stats_of(SampleFilter(weight="none", top_cut=10, negatives="exclude", exclude_zero=True))
    assert counts["capped"] == 1 and counts["used"] == 3 and cut["max"] == 10
    assert counts["negative"] == 1 and counts["zero_excluded"] == 1

    # cuts at both ends (values 1, 2, −0.01, 0, 40): the lower one excludes, the upper one caps by default
    counts, both = stats_of(SampleFilter(weight="none", bottom_cut=0.5, top_cut=10))
    assert counts["bottom_excluded"] == 2 and counts["capped"] == 1 and counts["used"] == 3
    assert both["min"] == 1 and both["max"] == 10
    counts, floor = stats_of(
        SampleFilter(weight="none", bottom_cut=0.5, bottom_mode="cap", top_cut=10, top_mode="exclude")
    )
    assert counts["bottom_capped"] == 2 and counts["top_excluded"] == 1 and counts["used"] == 4
    assert floor["mean"] == pytest.approx((1 + 2 + 0.5 + 0.5) / 4, rel=REL)


def test_bad_filters_are_refused_with_the_parameter() -> None:
    def refused(param: str, **kw) -> StatsError:  # type: ignore[no-untyped-def]
        with pytest.raises(StatsError) as e:
            SampleFilter.from_settings(kw)
        assert e.value.code == "bad_param" and e.value.param == param
        assert e.value.message and e.value.hint and set(e.value.as_dict()) == {"code", "message", "hint"}
        return e.value

    refused("top_cut", bottom_cut=5, top_cut=5)  # BF: «Верхняя отсечка должна быть больше нижней»
    assert "поле веса" in refused("weight_field", weight="field").message
    refused("weight_field", weight="length_field", weight_field="")
    refused("weight", weight="tonnes")
    refused("negatives", negatives="abs")
    refused("bottom_mode", bottom_cut=1, bottom_mode="replace")
    refused("top_mode", top_mode="replace")
    refused("top_cut", top_cut="10")
    refused("bottom_cut", bottom_cut=NAN)
    refused("top_cut", top_cut=math.inf)
    refused("top_cut", top_cut=True)
    refused("exclude_zero", exclude_zero="yes")
    with pytest.raises(StatsError) as e:
        SampleFilter(weight="field")  # the dataclass checks itself too
    assert e.value.param == "weight_field"

    # the settings of a chart hold other keys as well; the defaults are those of ТЗ 7.25
    flt = SampleFilter.from_settings({"field": "AU", "log": True, "bins": 20, "top_cut": 12, "weight_field": None})
    assert flt == SampleFilter(top_cut=12.0) and flt.weight == "length" and flt.weighted
    assert (flt.negatives, flt.exclude_zero, flt.bottom_mode, flt.top_mode) == ("keep", False, "exclude", "cap")
    assert SampleFilter.from_settings({"weight": "none", "bottom_cut": None}) == NONE and not NONE.weighted

    # a weight needs its column
    with pytest.raises(StatsError) as e:
        select_sample(AU, SampleFilter(weight="length"))
    assert e.value.code == "bad_param" and e.value.param == "weight"
    with pytest.raises(StatsError) as e:
        select_sample(AU, SampleFilter(weight="length_field", weight_field="DENSITY"), length=LENGTH)
    assert e.value.param == "weight_field"
    with pytest.raises(ValueError):
        select_values(AU, LENGTH[:3], NONE)
    with pytest.raises(ValueError):
        select_values(np.zeros((2, 2)), None, NONE)


# --- missing values, weights, the order of the rules -------------------------------------------------------


def test_missing_value_never_becomes_zero() -> None:
    v = np.array([NAN, 2.0, NAN, 4.0, math.inf, -math.inf, 6.0])
    sel = select_values(v, None, NONE)
    assert sel.counts["missing"] == 4 and sel.counts["used"] == 3 and sel.rows.tolist() == [1, 3, 6]
    s = ds.describe(sel.sort(), weighted=False)
    assert s["min"] == 2.0 and s["mean"] == 4.0 and s["count"] == 3  # a zero would give min 0 and mean 12 / 7
    # «исключить нули» counts real zeros only — a missing value is not one of them
    sel = select_values(np.array([NAN, 0.0, -0.0, 3.0]), None, SampleFilter(weight="none", exclude_zero=True))
    assert sel.counts["zero_excluded"] == 2 and sel.counts["missing"] == 1 and sel.values.tolist() == [3.0]
    # nothing left: the statistics are empty, not zero
    empty = select_values(np.array([NAN, NAN]), None, NONE)
    assert empty.n == 0 and empty.counts["used"] == 0 and empty.counts["rows"] == 2
    s = ds.describe(empty.sort())
    assert s["count"] == 0 and s["weight_sum"] == 0.0 and s["log"] is None
    assert all(s[k] is None for k in ("min", "max", "mean", "median", "std", "variance", "cv", "skewness"))
    assert set(s["percentiles"]) == {f"{p:g}" for p in ds.PERCENTILES} and set(s["percentiles"].values()) == {None}
    none = select_values(np.zeros(0), None, NONE)
    assert none.n == 0 and none.counts == dict.fromkeys(sl.COUNT_KEYS, 0)
    # a chart refuses an empty selection and says what the rows were left out for
    with pytest.raises(StatsError) as e:
        empty.require()
    assert e.value.code == "no_values" and "строк — 2" in e.value.hint and "без значения — 2" in e.value.hint
    assert sel.require() is sel and sl.no_values().hint
    json.dumps(s, allow_nan=False)


def test_bad_weight_is_counted_not_used() -> None:
    v = np.array([1.0, 2.0, 3.0, 4.0, 5.0, NAN, 7.0])
    w = np.array([1.0, 0.0, -2.0, NAN, 2.0, 1.0, math.inf])
    sel = select_values(v, w, SampleFilter(weight="field", weight_field="W"))
    assert sel.counts["bad_weight"] == 4 and sel.counts["missing"] == 1 and sel.counts["used"] == 2
    assert sel.rows.tolist() == [0, 4] and sel.weights.tolist() == [1.0, 2.0]
    assert sel.rows.dtype == np.int64 and sel.values.dtype == np.float64 and sel.weights.dtype == np.float64
    assert ds.describe(sel.sort())["mean"] == pytest.approx((1 + 2 * 5) / 3, rel=REL)
    # an interval of no length has no weight by length (BF: to ≤ from)
    sel = select_sample([1.0, 2.0, 3.0], SampleFilter(weight="length"), length=[1.0, 0.0, -1.0])
    assert sel.counts["bad_weight"] == 2 and sel.values.tolist() == [1.0]
    # without weights every row weighs 1 and none is bad
    sel = select_values(v, None, NONE)
    assert sel.counts["bad_weight"] == 0 and sel.weights.tolist() == [1.0] * 6
    assert sample_weights("none", length=LENGTH) is None
    assert sample_weights("length_field", length=[2.0, 1.0], weight_values=[3.0, NAN]).tolist()[0] == 6.0


def test_order_of_the_rules() -> None:
    """Missing and weight → negative → zero → bottom → top (BF `filter_values`)."""
    v = np.array([-0.2, -0.02, 0.0, 0.05, 0.5, 5.0, 50.0])
    # −0.2 → 0.1, −0.02 → 0.01 and then goes below the bottom cut
    half = select_values(v, None, SampleFilter(weight="none", negatives="half_abs", bottom_cut=0.03, top_cut=10))
    assert half.values.tolist() == [0.1, 0.05, 0.5, 5.0, 10.0] and half.rows.tolist() == [0, 3, 4, 5, 6]
    assert half.counts["negative"] == 2 and half.counts["bottom_excluded"] == 2 and half.counts["capped"] == 1
    # kept negatives are raised to the bottom cut with «заменить»
    cap = select_values(v, None, SampleFilter(weight="none", bottom_cut=0.03, bottom_mode="cap", exclude_zero=True))
    assert cap.values.tolist() == [0.03, 0.03, 0.05, 0.5, 5.0, 50.0] and cap.counts["bottom_capped"] == 2
    assert cap.counts["zero_excluded"] == 1 and cap.counts["negative"] == 2
    # the counts add up: every row is used or sits in exactly one «excluded» counter
    c = select_values(
        np.array([NAN, -1.0, 0.0, 0.2, 3.0, 99.0, 4.0]),
        np.array([1.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0]),
        SampleFilter(weight="field", weight_field="W", negatives="exclude", exclude_zero=True, bottom_cut=0.5,
                     top_cut=10, top_mode="exclude"),
    ).counts  # fmt: skip
    assert c["used"] == 1 and c["rows"] == 7
    left_out = c["missing"] + c["bad_weight"] + c["negative"] + c["zero_excluded"] + c["bottom_excluded"]
    assert left_out + c["top_excluded"] + c["used"] == c["rows"]


def test_selection_is_cancelled_by_tick() -> None:
    seen: list[float] = []
    sel = select_values(np.arange(10.0), None, NONE, tick=seen.append)
    assert sel.n == 10 and seen and seen[-1] == 1.0 and seen == sorted(seen)

    class Stop(Exception):
        pass

    def stop(_: float) -> None:
        raise Stop

    with pytest.raises(Stop):
        select_values(np.arange(10.0), None, NONE, tick=stop)
    with pytest.raises(Stop):
        select_values(np.arange(10.0), None, NONE).sort(tick=stop)


# --- statistics against a direct count ---------------------------------------------------------------------


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_weighted_statistics_match_a_direct_count(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = 20_000
    v = np.round(rng.lognormal(0.2, 1.3, n), 2)  # assays are rounded: many equal values
    v[rng.integers(0, n, 40)] = 0.0  # and some are «not detected»
    w = rng.choice([0.25, 1.0, 1.0, 1.5, 2.0, 7.25], n)  # binary fractions: the running sums are exact
    weighted, plain = ds.describe_both(srt(v, w))
    check(weighted, brute(v, w))
    check(plain, brute(v, np.ones(n)))
    assert weighted["log"]["count"] == int((v > 0).sum()) < n
    assert weighted == ds.describe(srt(v, w)) and plain == ds.describe(srt(v, w), weighted=False)
    json.dumps([weighted, plain], allow_nan=False)


def test_hard_data() -> None:
    rng = np.random.default_rng(5)
    # values far from zero: the variance does not drown in the mean
    far = 1.0e9 + rng.normal(0.0, 1.0, 5000)
    w = rng.uniform(0.5, 2.0, 5000)
    s = ds.describe(srt(far, w))
    ref = brute(far, w)
    assert s["variance"] == pytest.approx(ref["variance"], rel=1e-9) and 0.9 < s["variance"] < 1.1
    assert s["mean"] == pytest.approx(ref["mean"], rel=1e-15)
    # weights of very different size: the moments keep their digits (a percentile between two rows of a
    # weight 1e−8 of the total is as exact as the running sums are)
    v = rng.lognormal(0, 1, 3000)
    w = 10.0 ** rng.uniform(-4, 4, 3000)
    ref = brute(v, w)
    check(ds.describe(srt(v, w)), {k: x for k, x in ref.items() if not k.startswith("p") and k != "median"}, rel=1e-11)
    check(ds.describe(srt(v, w)), {k: x for k, x in ref.items() if k.startswith("p") or k == "median"}, rel=1e-6)
    # a heavy weight far from the median: the statistics of the logarithms keep their digits
    v = np.append(rng.lognormal(0, 1, 1000), math.exp(10.0))
    w = np.append(np.ones(1000), 1.0e12)
    ref = brute(v, w)
    assert ds.describe(srt(v, w))["log"]["variance_ln"] == pytest.approx(ref["variance_ln"], rel=1e-10)
    # one value; equal values: no spread — the skewness is empty, the variance is exactly 0
    one = ds.describe(srt([3.5], [2.0]))
    assert (one["count"], one["mean"], one["median"], one["variance"], one["cv"]) == (1, 3.5, 3.5, 0.0, 0.0)
    assert one["skewness"] is None and one["percentiles"]["5"] == 3.5 and one["weight_sum"] == 2.0
    same = ds.describe(srt(np.full(1000, 0.1), rng.uniform(1, 2, 1000)))
    assert same["variance"] == 0.0 and same["std"] == 0.0 and same["skewness"] is None and same["mean"] == 0.1
    assert same["log"]["variance_ln"] == 0.0 and same["log"]["geometric_mean"] == pytest.approx(0.1, rel=REL)
    # nothing positive: no logarithms; a mean ≤ 0 has no CV (as `Sorted.cv` of Э12)
    neg = ds.describe(srt([-3.0, -1.0, 0.0]), weighted=False)
    assert neg["log"] is None and neg["cv"] is None and neg["mean"] == pytest.approx(-4 / 3, rel=REL)
    zero = ds.describe(srt([-1.0, 1.0]), weighted=False)
    assert zero["mean"] == 0.0 and zero["cv"] is None and zero["log"]["count"] == 1
    # numbers too large for a cube: empty, not an error
    huge = ds.describe(srt([1.0e200, 2.0e200, 9.0e200]), weighted=False)
    assert huge["skewness"] is None and huge["mean"] == pytest.approx(4.0e200, rel=REL)
    json.dumps([one, same, neg, zero, huge], allow_nan=False)


def test_two_hundred_thousand_rows_one_sort() -> None:
    """The size of a pytest run (ADR 0019: ≤ 200 тыс. проб); 5 млн — the measurement of the report."""
    rng = np.random.default_rng(99)
    n = 200_000
    v = rng.lognormal(0, 1.2, n)
    v[rng.integers(0, n, 2000)] = NAN
    length = rng.choice([1.0, 1.5, 2.0], n)
    length[rng.integers(0, n, 500)] = 0.0
    flt = SampleFilter(weight="length", top_cut=30.0, negatives="half_abs")
    sel = select_sample(v, flt, length=length)
    ok = np.isfinite(v) & (length > 0)
    assert sel.counts["used"] == int(ok.sum()) and np.array_equal(sel.rows, np.flatnonzero(ok))
    weighted, plain = ds.describe_both(sel.sort())
    check(weighted, brute(np.minimum(v[ok], 30.0), length[ok]), rel=1e-11)
    check(plain, brute(np.minimum(v[ok], 30.0), np.ones(int(ok.sum()))), rel=1e-11)
    sl.warm()
    ds.warm()
