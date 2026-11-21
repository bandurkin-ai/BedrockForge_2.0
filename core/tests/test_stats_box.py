"""Box plots by domains (ТЗ 8.3; ADR 0019 §4): Т8.3-1 (the quartiles of a box are `weighted_percentiles` of BF,
1e−12), whiskers and outliers on an example counted by hand, no more than 5000 outliers with the outermost
ones kept."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from bf_core.stats import boxplot
from bf_core.stats.boxplot import MAX_OUTLIERS, box_chart, box_stats
from bf_core.stats.domain_stats import MAX_DOMAINS, group_samples
from bf_core.stats.select import SampleFilter, select_sample, select_values
from bf_core.stats.weighted import StatsError, sort_groups, weighted_percentiles

NAN = math.nan
REL = 1e-12
LABELS = ["GRANITE", "ORE", "SCHIST"]


def refused(code: str, param: str | None, call) -> StatsError:  # type: ignore[no-untyped-def]
    with pytest.raises(StatsError) as e:
        call()
    assert e.value.code == code and e.value.param == param and e.value.message and e.value.hint
    return e.value


def table(rows: int = 3000, seed: int = 83):  # type: ignore[no-untyped-def]
    """Grades of three domains (and rows without one), lengths of intervals, a few gaps and zeros."""
    rng = np.random.default_rng(seed)
    codes = rng.integers(-1, 3, rows).astype(np.int32)
    values = rng.lognormal(0.3 * codes, 0.6 + 0.2 * codes, rows)
    values[rng.random(rows) < 0.03] = NAN
    values[rng.random(rows) < 0.02] = 0.0
    return values, rng.choice([0.5, 1.0, 1.5, 2.0], rows), codes


def box_of(r: dict, domain: str | None) -> dict:
    return next(b for b in r["boxes"] if b["domain"] == domain)


# --- acceptance of ТЗ 8.3 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("weight", ["length", "none"])
def test_t83_1_quartiles_of_a_box_are_weighted_percentiles(weight: str) -> None:
    """Т8.3-1: Q1, the median and Q3 of every box equal `weighted_percentiles` of BF (1e−12)."""
    values, length, codes = table()
    sel = select_sample(values, SampleFilter(weight=weight), length=length)
    r = box_chart(sel, codes, LABELS)
    assert [b["domain"] for b in r["boxes"]] == [*LABELS, None] and [b["role"] for b in r["boxes"]][-1] == "none"
    for code, name in [(0, "GRANITE"), (1, "ORE"), (2, "SCHIST"), (-1, None)]:
        rows = (codes == code) & np.isfinite(values)
        w = length[rows] if weight == "length" else None
        q1, q2, q3 = weighted_percentiles(values[rows], w, [25, 50, 75])
        b = box_of(r, name)
        assert (b["q1"], b["median"], b["q3"]) == pytest.approx((q1, q2, q3), rel=REL)
        assert b["count"] == rows.sum() and b["min"] == values[rows].min() and b["max"] == values[rows].max()
        ww = np.ones(rows.sum()) if w is None else w
        assert b["mean"] == pytest.approx(np.sum(ww * values[rows]) / ww.sum(), rel=REL) == b["arithmetic_mean"]
        assert b["weight_sum"] == pytest.approx(ww.sum(), rel=REL) and b["nonpositive"] == 0
    assert sum(b["count"] for b in r["boxes"]) == r["counts"]["used"] == int(np.isfinite(values).sum())
    assert r["counts"]["no_domain"] == box_of(r, None)["count"] and r["weighted"] is (weight == "length")
    # the log scale: the quartiles of the logarithms
    logs = box_chart(sel, codes, LABELS, log=True)
    for code, name in [(0, "GRANITE"), (1, "ORE"), (2, "SCHIST"), (-1, None)]:
        rows = (codes == code) & (values > 0)
        w = length[rows] if weight == "length" else np.ones(rows.sum())
        q = 10.0 ** weighted_percentiles(np.log10(values[rows]), w, [25, 50, 75])
        b = box_of(logs, name)
        assert (b["q1"], b["median"], b["q3"]) == pytest.approx(tuple(q), rel=REL)
        assert b["mean"] == pytest.approx(10.0 ** (np.sum(w * np.log10(values[rows])) / w.sum()), rel=REL)
        assert b["arithmetic_mean"] == pytest.approx(np.sum(w * values[rows]) / w.sum(), rel=REL)
        assert b["count"] == rows.sum() and b["nonpositive"] == ((codes == code) & (values == 0)).sum()
    assert logs["counts"]["nonpositive_log"] == (values == 0).sum() > 0 and logs["log"] is True
    json.dumps(r, allow_nan=False)
    json.dumps(logs, allow_nan=False)


# --- whiskers and outliers ---------------------------------------------------------------------------------


def test_whiskers_and_outliers_by_hand() -> None:
    """Values 1 … 9 and 100 without weights: positions (i + 0.5) / 10, so Q1 = 3, the median 5.5, Q3 = 8; the
    fences are 3 − 7.5 and 8 + 7.5; the whiskers end at 1 and 9; 100 is an outlier."""
    v = np.array([4.0, 100.0, 1.0, 9.0, 2.0, 8.0, 3.0, 7.0, 5.0, 6.0])
    r = box_chart(select_values(v), None, None)
    assert len(r["boxes"]) == 1
    b = r["boxes"][0]
    assert b == {"domain": None, "role": "all", "count": 10, "weight_sum": 10.0, "nonpositive": 0, "mean": 14.5,
                 "arithmetic_mean": 14.5, "min": 1.0, "max": 100.0, "q1": 3.0, "median": 5.5, "q3": 8.0,
                 "whisker_low": 1.0, "whisker_high": 9.0, "outliers_low": 0, "outliers_high": 1,
                 "outliers": [100.0]}  # fmt: skip
    assert r["outliers"] == {"total": 1, "shown": 1, "limit": MAX_OUTLIERS}
    # outliers on both sides; a value exactly on a fence is inside: Q1 = 3, Q3 = 8, the fences −4.5 and 15.5
    v = np.array([-20.0, -4.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 15.5, 15.6])
    b = box_chart(select_values(v), None, None)["boxes"][0]
    assert (b["q1"], b["median"], b["q3"]) == tuple(weighted_percentiles(v, None, [25, 50, 75])) == (3.0, 5.5, 8.0)
    assert (b["whisker_low"], b["whisker_high"], b["outliers"]) == (-4.5, 15.5, [-20.0, 15.6])
    assert (b["outliers_low"], b["outliers_high"]) == (1, 1)
    # with weights (the lengths of the intervals): W = 15, the positions 2/15, 4.5/15, 7.5/15, 12.5/15 —
    # Q1 = 7 lies between 0 and 10, Q3 = 10; no value between the fence 2.5 and Q1: the whisker ends at Q1
    sel = select_values(np.array([0.0, 10.0, 10.0, 10.0]), np.array([4.0, 1.0, 5.0, 5.0]))
    b = box_chart(sel, None, None)["boxes"][0]
    assert (b["q1"], b["median"], b["q3"]) == pytest.approx((7.0, 10.0, 10.0), rel=REL)
    assert b["whisker_low"] == pytest.approx(7.0, rel=REL) and b["whisker_high"] == 10.0
    assert (b["outliers_low"], b["outliers_high"], b["outliers"]) == (1, 0, [0.0])
    assert b["mean"] == pytest.approx(110.0 / 15.0, rel=REL)
    # no spread: the box is a line, nothing is an outlier
    flat = box_chart(select_values(np.full(7, 2.5)), None, None)["boxes"][0]
    assert {flat[k] for k in ("q1", "median", "q3", "whisker_low", "whisker_high", "min", "max", "mean")} == {2.5}
    assert flat["outliers"] == [] and flat["count"] == 7
    one = box_chart(select_values(np.array([3.0])), None, None)["boxes"][0]
    assert one["q1"] == one["q3"] == one["whisker_low"] == one["whisker_high"] == 3.0 and one["count"] == 1


def test_log_scale_is_counted_by_logarithms() -> None:
    """1, 10, 100, 1000 and 10⁷ on a log scale: the fences are 1.5·IQR of the logarithms away."""
    v = np.array([1.0, 10.0, 100.0, 1000.0, 1e7, 0.0, -5.0, 100.0, 10.0, 1000.0])
    codes = np.array([0, 0, 0, 0, 0, 0, 0, 1, 1, 2])
    r = box_chart(select_values(v), codes, ["A", "B", "ONLY_ZERO"], log=True)
    a = box_of(r, "A")
    logs = np.log10(v[:5])
    q1, q2, q3 = weighted_percentiles(logs, None, [25, 50, 75])
    assert (a["q1"], a["median"], a["q3"]) == pytest.approx((10.0**q1, 10.0**q2, 10.0**q3), rel=REL)
    assert (q1, q2, q3) == pytest.approx((0.75, 2.0, 4.0))  # IQR = 3.25: the fences are −4.125 and 8.875
    assert a["whisker_low"] == 1.0 and a["whisker_high"] == 1e7 and a["outliers"] == []
    assert a["mean"] == pytest.approx(10.0 ** logs.mean(), rel=REL)
    assert a["arithmetic_mean"] == pytest.approx(v[:5].mean(), rel=REL)
    assert a["count"] == 5 and a["nonpositive"] == 2 and a["min"] == 1.0
    # on the linear scale the same 10⁷ is an outlier
    lin = box_of(box_chart(select_values(v), codes, ["A", "B", "ONLY_ZERO"]), "A")
    assert lin["outliers"] == [1e7] and lin["count"] == 7 and lin["min"] == -5.0
    assert r["counts"]["nonpositive_log"] == 2 and [b["domain"] for b in r["boxes"]] == ["A", "B", "ONLY_ZERO"]
    # a domain without a value > 0 is not shown; no value > 0 at all — the refusal
    zero = box_chart(select_values(np.array([1.0, 2.0, 0.0, -1.0])), np.array([0, 0, 1, 1]), ["A", "B"], log=True)
    assert [b["domain"] for b in zero["boxes"]] == ["A"] and zero["counts"]["nonpositive_log"] == 2
    refused("no_positive", "log", lambda: box_chart(select_values(np.array([0.0, -1.0])), None, None, log=True))
    refused("bad_param", "log", lambda: box_chart(select_values(np.array([1.0])), None, None, log=1))  # type: ignore[arg-type]
    refused("no_values", None, lambda: box_chart(select_values(np.array([NAN])), None, None))
    assert box_stats(sort_groups(np.array([0.0, -1.0]), np.ones(2))[0], log=True) is None


def test_no_more_than_5000_outliers_and_the_outermost_stay() -> None:
    rng = np.random.default_rng(5000)
    n = 120_000
    codes = rng.integers(0, 4, n)
    values = rng.standard_t(1.5, n) * (1 + codes)  # heavy tails: thousands of outliers in a domain
    values[codes == 3] = rng.normal(size=int((codes == 3).sum()))  # … and a domain with a few
    sel = select_values(values)
    r = box_chart(sel, codes, ["A", "B", "C", "D"])
    assert r["outliers"]["total"] > 9000 and r["outliers"]["limit"] == MAX_OUTLIERS == 5000
    assert 4000 < r["outliers"]["shown"] <= 5000
    assert r["outliers"]["total"] == sum(b["outliers_low"] + b["outliers_high"] for b in r["boxes"])
    for code, b in enumerate(r["boxes"]):
        mine = np.sort(values[codes == code])
        lo, hi = b["q1"] - 1.5 * (b["q3"] - b["q1"]), b["q3"] + 1.5 * (b["q3"] - b["q1"])
        outside = mine[(mine < lo) | (mine > hi)]
        assert b["outliers_low"] == (mine < lo).sum() and b["outliers_high"] == (mine > hi).sum()
        shown = np.array(b["outliers"])
        assert np.isin(shown, outside).all() and np.array_equal(shown, np.sort(shown)) and len(set(shown)) == len(shown)
        # the outermost values stay, and so do the ends of every tail
        assert shown[0] == mine[0] == b["min"] and shown[-1] == mine[-1] == b["max"]
        assert mine[mine < lo][-1] in shown and mine[mine > hi][0] in shown
        assert b["whisker_low"] == mine[mine >= lo][0] and b["whisker_high"] == mine[mine <= hi][-1]
        assert len(shown) >= 2 and (len(shown) < len(outside) or code == 3)
    # every tail keeps a share of the limit
    shares = [len(b["outliers"]) / (b["outliers_low"] + b["outliers_high"]) for b in r["boxes"][:3]]
    assert max(shares) / min(shares) < 1.2
    # under the limit all of them are shown
    few = box_chart(sel, codes, ["A", "B", "C", "D"], max_outliers=10**6)
    assert few["outliers"]["shown"] == few["outliers"]["total"] == r["outliers"]["total"]
    tiny = box_chart(sel, codes, ["A", "B", "C", "D"], max_outliers=8)  # one point per tail: the outermost
    assert [b["outliers"] for b in tiny["boxes"]] == [[b["min"], b["max"]] for b in r["boxes"]]


# --- domains -----------------------------------------------------------------------------------------------


def test_domains_and_rows_without_one() -> None:
    values, length, codes = table(500, seed=4)
    sel = select_sample(values, SampleFilter(weight="length", exclude_zero=True, top_cut=3.0), length=length)
    r = box_chart(sel, codes, LABELS)
    assert sum(b["count"] for b in r["boxes"]) == sel.n == r["counts"]["used"]
    assert r["counts"]["capped"] == sel.counts["capped"] > 0 and max(b["max"] for b in r["boxes"]) == 3.0
    # the codes are those of the rows of the table: the selection carries the numbers of its rows
    found = group_samples(sel, codes, LABELS)
    for g in found.groups:
        code = LABELS.index(g.domain) if g.domain is not None else -1
        assert g.sample.n == (codes[sel.rows] == code).sum() and g.role == ("domain" if code >= 0 else "none")
    assert np.array_equal(np.bincount(found.index), [g.sample.n for g in found.groups])
    # a domain without rows is not shown; codes outside the labels are rows without a domain
    r = box_chart(select_values(np.arange(1.0, 7.0)), np.array([2, 2, 5, -1, -7, 2]), LABELS)
    assert [(b["domain"], b["role"], b["count"]) for b in r["boxes"]] == [("SCHIST", "domain", 3), (None, "none", 3)]
    assert r["counts"]["no_domain"] == 3
    many = np.arange(MAX_DOMAINS + 1)
    e = refused("too_many_domains", "domain",
                lambda: box_chart(select_values(many + 1.0), many, [f"D{i}" for i in many]))  # fmt: skip
    assert str(MAX_DOMAINS) in e.message
    # as many domains as the limit are fine, even with more labels than rows
    ok = box_chart(select_values(many[:-1] + 1.0), many[:-1], [f"D{i}" for i in range(1000)])
    assert len(ok["boxes"]) == MAX_DOMAINS


def test_tick_reports_and_cancels() -> None:
    values, length, codes = table(4000)
    sel = select_sample(values, SampleFilter(), length=length)
    seen: list[float] = []
    box_chart(sel, codes, LABELS, tick=seen.append)
    assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) > 3

    class Stop(Exception):
        pass

    def stop(f: float) -> None:
        raise Stop

    with pytest.raises(Stop):
        box_chart(sel, codes, LABELS, tick=stop)


def test_warm_compiles() -> None:
    boxplot.warm()
