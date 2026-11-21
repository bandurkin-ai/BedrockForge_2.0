"""Log-probability plot, statistics by domains, ternary diagram (ТЗ 8.3; ADR 0019 §4): a lognormal sample is
a straight line (r ≥ 0.999), no more than 2000 points per domain; by domains Σ n = the total, without `DECL_W`
the declustered mean is empty; the shares of a ternary diagram add up to 1.

Tolerance — 1e−12 against a direct count (numpy, `weighted_percentiles`)."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
from scipy.special import ndtri

from bf_core.stats import domain_stats as domains_mod
from bf_core.stats import probplot, ternary
from bf_core.stats.domain_stats import NO_DECL, domain_stats, group_sums
from bf_core.stats.probplot import MAX_POINTS, probability_line, probplot_chart
from bf_core.stats.select import SampleFilter, select_sample, select_values
from bf_core.stats.ternary import HEIGHT, plane, shares, ternary_chart
from bf_core.stats.weighted import StatsError, sort_groups, weighted_percentiles

NAN = math.nan
REL = 1e-12
LABELS = ["GRANITE", "ORE", "SCHIST"]


def refused(code: str, param: str | None, call) -> StatsError:  # type: ignore[no-untyped-def]
    with pytest.raises(StatsError) as e:
        call()
    assert e.value.code == code and e.value.param == param and e.value.message and e.value.hint
    return e.value


def table(rows: int = 9000, seed: int = 83):  # type: ignore[no-untyped-def]
    """Lognormal grades of three domains (and rows without one), lengths, declustering weights, gaps."""
    rng = np.random.default_rng(seed)
    codes = rng.integers(-1, 3, rows).astype(np.int32)
    values = rng.lognormal(0.3 * codes, 0.6 + 0.2 * codes, rows)
    values[rng.random(rows) < 0.03] = NAN
    length = rng.choice([0.5, 1.0, 1.5, 2.0], rows)
    decl = rng.uniform(0.2, 3.0, rows)
    decl[rng.random(rows) < 0.05] = NAN
    return values, length, codes, decl


# --- the log-probability plot ------------------------------------------------------------------------------


@pytest.mark.parametrize("weighted", [False, True])
def test_lognormal_sample_is_a_straight_line(weighted: bool) -> None:
    """A lognormal sample of 10 000 values: r ≥ 0.999, the slope is σ of the logarithms; ≤ 2000 points."""
    rng = np.random.default_rng(8301)
    v = rng.lognormal(0.4, 1.1, 10_000)
    w = rng.choice([0.5, 1.0, 2.0], 10_000) if weighted else None
    r = probplot_chart(select_values(v, w))
    assert len(r["lines"]) == 1 and r["weighted"] is weighted
    line = r["lines"][0]
    pts = np.array(line["points"])
    assert line["r"] >= 0.999 and line["domain"] is None and line["role"] == "all"
    assert np.corrcoef(pts[:, 1], np.log(pts[:, 0]))[0, 1] == pytest.approx(line["r"], rel=REL)
    slope, intercept = np.polyfit(pts[:, 1], np.log(pts[:, 0]), 1)
    assert slope == pytest.approx(1.1, abs=0.03) and intercept == pytest.approx(0.4, abs=0.03)
    # thinned to no more than 2000 points; the least and the greatest value stay; every point is a sample
    assert 1000 < len(pts) <= MAX_POINTS == 2000 and line["thinned"] is True and line["count"] == 10_000
    assert pts[0, 0] == v.min() == line["min"] and pts[-1, 0] == v.max() == line["max"]
    assert np.isin(pts[:, 0], v).all() and (np.diff(pts[:, 1]) > 0).all() and (np.diff(pts[:, 0]) >= 0).all()
    # the tails keep their samples: the ten greatest values are all there
    assert np.isin(np.sort(v)[-10:], pts[:, 0]).all() and np.isin(np.sort(v)[:10], pts[:, 0]).all()
    # a normal sample is not a straight line on this plot
    bent = probplot_chart(select_values(np.abs(rng.normal(10.0, 3.0, 10_000)) + 1e-3))["lines"][0]
    assert bent["r"] < 0.99
    json.dumps(r, allow_nan=False)


def test_plotting_positions_by_hand() -> None:
    """1, 2, 4 with the weights 1, 2, 1: C − w / 2 = 0.5, 2, 3.5 of W = 4 — P = 12.5, 50 and 87.5 %."""
    line = probability_line(sort_groups(np.array([4.0, 1.0, 2.0]), np.array([1.0, 1.0, 2.0]))[0])
    assert line is not None
    pts = np.array(line["points"])
    assert pts[:, 0].tolist() == [1.0, 2.0, 4.0] and pts[:, 2].tolist() == [12.5, 50.0, 87.5]
    assert pts[:, 1] == pytest.approx(ndtri([0.125, 0.5, 0.875]), abs=1e-15) and pts[1, 1] == 0.0
    assert line["count"] == 3 and line["weight_sum"] == 4.0 and line["thinned"] is False and line["nonpositive"] == 0
    assert line["r"] == pytest.approx(np.corrcoef(ndtri([0.125, 0.5, 0.875]), np.log([1.0, 2.0, 4.0]))[0, 1], rel=REL)
    # the positions are those of the percentiles: the value at P is the weighted percentile
    v = np.random.default_rng(2).lognormal(size=500)
    w = np.random.default_rng(3).uniform(0.5, 2.0, 500)
    pts = np.array(probability_line(sort_groups(v, w)[0])["points"])  # type: ignore[index]
    for value, _, percent in pts[::50]:
        assert weighted_percentiles(v, w, [percent])[0] == pytest.approx(value, rel=1e-9)
    # values ≤ 0 have no place on a log axis: counted and left out, the positions are those of the rest
    low = probability_line(sort_groups(np.array([-1.0, 0.0, 1.0, 2.0, 4.0]), np.array([5.0, 5.0, 1.0, 2.0, 1.0]))[0])
    assert low is not None and low["nonpositive"] == 2 and low["count"] == 3 and low["min"] == 1.0
    assert np.array(low["points"])[:, 2].tolist() == [12.5, 50.0, 87.5]
    assert probability_line(sort_groups(np.array([-1.0, 0.0]), np.ones(2))[0]) is None
    two = probability_line(sort_groups(np.array([1.0, 2.0]), np.ones(2))[0])
    assert two is not None and two["r"] is None and len(two["points"]) == 2
    flat = probability_line(sort_groups(np.full(5, 2.0), np.ones(5))[0])
    assert flat is not None and flat["r"] is None and len(flat["points"]) == 5


def test_a_line_per_domain_with_no_more_than_2000_points() -> None:
    values, length, codes, _ = table(30_000)
    values[:40] = 0.0
    sel = select_sample(values, SampleFilter(), length=length)
    r = probplot_chart(sel, codes, LABELS)
    assert [(line["domain"], line["role"]) for line in r["lines"]] == [
        ("GRANITE", "domain"), ("ORE", "domain"), ("SCHIST", "domain"), (None, "none")]  # fmt: skip
    for code, line in zip((0, 1, 2, -1), r["lines"], strict=True):
        rows = (codes == code) & (values > 0)
        pts = np.array(line["points"])
        assert line["count"] == rows.sum() > MAX_POINTS and 1000 < len(pts) <= MAX_POINTS
        assert pts[0, 0] == values[rows].min() and pts[-1, 0] == values[rows].max()
        assert line["nonpositive"] == ((codes == code) & (values == 0)).sum()
        assert line["weight_sum"] == pytest.approx(length[rows].sum(), rel=REL) and line["r"] > 0.995
        # the positions are those of the domain alone
        alone = np.array(probability_line(sort_groups(values[rows], length[rows])[0])["points"])  # type: ignore[index]
        assert np.array_equal(pts, alone)
    assert r["counts"]["nonpositive_log"] == 40 and r["counts"]["no_domain"] == (codes[sel.rows] == -1).sum()
    assert sum(line["count"] + line["nonpositive"] for line in r["lines"]) == r["counts"]["used"]
    # fewer points than the limit are all shown
    small = probplot_chart(select_values(values[:500]), codes[:500], LABELS)
    assert all(line["thinned"] is False and len(line["points"]) == line["count"] for line in small["lines"])
    wide = probplot_chart(sel, codes, LABELS, max_points=50)
    assert all(10 < len(line["points"]) <= 50 for line in wide["lines"])
    # a domain without a value > 0 gives no line; none at all — the refusal
    part = probplot_chart(select_values(np.array([1.0, 2.0, 3.0, 0.0, -1.0])), np.array([0, 0, 0, 1, 1]), ["A", "B"])
    assert [line["domain"] for line in part["lines"]] == ["A"] and part["counts"]["nonpositive_log"] == 2
    refused("no_positive", None, lambda: probplot_chart(select_values(np.array([0.0, -2.0]))))
    refused("no_values", None, lambda: probplot_chart(select_values(np.array([NAN]))))
    json.dumps(r, allow_nan=False)


# --- statistics by domains ---------------------------------------------------------------------------------


@pytest.mark.parametrize("weight", ["length", "none"])
def test_domain_table_adds_up_and_equals_a_direct_count(weight: str) -> None:
    values, length, codes, decl = table()
    sel = select_sample(values, SampleFilter(weight=weight), length=length)
    r = domain_stats(sel, codes, LABELS, length=length, decl_weights=decl)
    assert [row["domain"] for row in r["rows"]] == [*LABELS, None] and r["rows"][-1]["role"] == "none"
    # Σ n = the total
    assert sum(row["count"] for row in r["rows"]) == r["total"]["count"] == r["counts"]["used"] == sel.n
    assert sum(row["length"] for row in r["rows"]) == pytest.approx(r["total"]["length"], rel=REL)
    assert r["declustered"] is True and r["notes"] == [] and r["weighted"] is (weight == "length")
    ok = np.isfinite(values)
    for code, row in zip((0, 1, 2, -1, None), [*r["rows"], r["total"]], strict=True):
        rows = ok if code is None else ok & (codes == code)
        v, w = values[rows], (length[rows] if weight == "length" else np.ones(rows.sum()))
        mean = np.sum(w * v) / w.sum()
        std = math.sqrt(np.sum(w * (v - mean) ** 2) / w.sum())
        p10, p50, p90 = weighted_percentiles(v, w, [10, 50, 90])
        d = decl[rows]
        have = np.isfinite(d)
        want = {
            "count": int(rows.sum()), "length": length[rows].sum(), "weight_sum": w.sum(), "mean": mean,
            "declustered_mean": np.sum(d[have] * v[have]) / d[have].sum(), "median": p50, "std": std,
            "cv": std / mean, "min": v.min(), "max": v.max(), "p10": p10, "p50": p50, "p90": p90,
        }  # fmt: skip
        got = {k: row[k] for k in want}
        assert got == pytest.approx(want, rel=REL), (code, got, want)
    assert r["total"]["domain"] is None and r["total"]["role"] == "all"
    # in two threads and in one: the same bits
    again = domain_stats(sel, codes, LABELS, length=length, decl_weights=decl, parallel_from=10)
    assert again == r
    json.dumps(r, allow_nan=False)


def test_without_decl_w_the_declustered_mean_is_empty() -> None:
    values, length, codes, _ = table(600)
    sel = select_sample(values, SampleFilter(), length=length)
    r = domain_stats(sel, codes, LABELS, length=length)
    assert r["declustered"] is False and r["notes"] == [NO_DECL]
    assert "DECL_W" in NO_DECL and "Декластеризация" in NO_DECL
    assert all(row["declustered_mean"] is None for row in [*r["rows"], r["total"]])
    assert all(row["mean"] is not None and row["length"] is not None for row in r["rows"])
    # without the lengths too: empty, not 0
    bare = domain_stats(select_values(values), codes, LABELS)
    assert all(row["length"] is None for row in [*bare["rows"], bare["total"]])
    # the weights of `DECL_W` that are missing or ≤ 0 take no part; a domain with none of them stays empty
    v = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    d = np.array([1.0, 3.0, NAN, 0.0, -1.0, NAN])
    part = domain_stats(select_values(v), np.array([0, 0, 0, 1, 1, 1]), ["A", "B"], decl_weights=d)
    assert [row["declustered_mean"] for row in part["rows"]] == [1.75, None]
    assert part["total"]["declustered_mean"] == 1.75
    assert part["declustered"] is True and part["notes"] == []
    sw, swv, n = group_sums(v, d, np.array([0, 0, 0, 1, 1, 1]), 2)
    assert sw.tolist() == [4.0, 0.0] and swv.tolist() == [7.0, 0.0] and n.tolist() == [2, 0]


def test_domain_table_without_domains_and_hard_rows() -> None:
    v = np.array([2.0, 2.0, 2.0, -1.0, 0.0, 5.0])
    one = domain_stats(select_values(v[:3]))
    assert one["rows"] == [one["total"]] and one["total"]["std"] == 0.0 and one["total"]["cv"] == 0.0
    assert one["total"]["median"] == one["total"]["p10"] == one["total"]["p90"] == 2.0
    # a mean ≤ 0 has no CV; the rows of a top cut are those after the cut
    neg = domain_stats(select_values(v[3:5]))["total"]
    assert neg["mean"] == -0.5 and neg["cv"] is None and neg["std"] == 0.5
    capped = select_values(v, None, SampleFilter(weight="none", top_cut=3.0))
    cut = domain_stats(capped, np.array([0, 0, 0, 0, 0, 1]), ["A", "B"])
    assert cut["rows"][1]["max"] == 3.0 and cut["rows"][1]["count"] == 1 and cut["counts"]["capped"] == 1
    refused("no_values", None, lambda: domain_stats(select_values(np.array([NAN, NAN]))))
    seen: list[float] = []
    domain_stats(select_values(v), np.array([0, 0, 1, 1, 0, 1]), ["A", "B"], tick=seen.append)
    assert seen == sorted(seen) and seen[-1] == 1.0


# --- the ternary diagram -----------------------------------------------------------------------------------


def test_shares_of_the_ternary_diagram_add_up_to_one() -> None:
    rng = np.random.default_rng(7)
    a, b, c = rng.lognormal(0, 1, 5000), rng.lognormal(1, 1, 5000), rng.lognormal(-1, 2, 5000)
    r = ternary_chart(a, b, c)
    pa, pb, pc = (np.array(r["points"][k]) for k in "abc")
    assert np.abs(pa + pb + pc - 1.0).max() <= REL and r["mode"] == "points" and r["density"] is None
    total = a + b + c
    assert np.array_equal(pa, a / total) and np.array_equal(pb, b / total) and np.array_equal(pc, c / total)
    assert r["points"]["row"] == list(range(5000)) and r["points"]["color"] is None
    assert r["counts"] == {"rows": 5000, "missing": 0, "negative": 0, "zero_sum": 0, "used": 5000}
    want = {"a": (a / total).mean(), "b": (b / total).mean(), "c": (c / total).mean()}
    assert r["means"] == pytest.approx(want, rel=REL)
    assert sum(r["means"].values()) == pytest.approx(1.0, abs=REL)
    json.dumps(r, allow_nan=False)


def test_rows_of_the_ternary_diagram_that_are_not_drawn() -> None:
    a = np.array([1.0, NAN, 2.0, 0.0, -1.0, 3.0, 0.0, np.inf, 5.0])
    b = np.array([1.0, 1.0, 0.0, 0.0, 5.0, 3.0, 0.0, 1.0, 0.0])
    c = np.array([2.0, 1.0, 6.0, 0.0, 5.0, NAN, 4.0, 1.0, 0.0])
    s = shares(a, b, c)
    assert s.rows.tolist() == [0, 2, 6, 8] and s.counts == {"rows": 9, "missing": 3, "negative": 1, "zero_sum": 1,
                                                            "used": 4}  # fmt: skip
    assert (s.a.tolist(), s.b.tolist(), s.c.tolist()) == ([0.25, 0.25, 0.0, 1.0], [0.25, 0.0, 0.0, 0.0],
                                                          [0.5, 0.75, 1.0, 0.0])  # fmt: skip
    # the corners: the first field at (0, 0), the second at (1, 0), the third at (1 / 2, √3 / 2)
    x, y = plane(shares(np.array([1.0, 0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0, 1.0]), np.array([0.0, 0.0, 1.0, 1.0])))
    assert x.tolist() == pytest.approx([0.0, 1.0, 0.5, 0.5])
    assert y.tolist() == pytest.approx([0.0, 0.0, HEIGHT, HEIGHT / 3])
    r = ternary_chart(a, b, c, color=np.arange(9.0) * 1.5, row_ids=np.arange(9) + 500)
    assert r["points"]["row"] == [500, 502, 506, 508] and r["points"]["color"] == [0.0, 3.0, 9.0, 12.0]
    codes = ternary_chart(a, b, c, color=np.array([0, 1, -1, 2, 0, 1, 2, 0, 1], dtype=np.int32))
    assert codes["points"]["color"] == [0, -1, 2, 1]
    missing = ternary_chart(a, b, c, color=np.where(np.arange(9) == 2, NAN, 1.0))
    assert missing["points"]["color"] == [1.0, None, 1.0, 1.0]
    e = refused("no_values", None, lambda: ternary_chart(a, b, np.full(9, NAN)))
    assert "с пропуском — 9" in e.hint
    refused("no_values", None, lambda: ternary_chart(np.zeros(3), np.zeros(3), np.zeros(3)))
    with pytest.raises(ValueError):
        shares(a, b, c[:3])


def test_large_ternary_cloud_becomes_a_density(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ternary, "_BLOCK", 4096)
    rng = np.random.default_rng(11)
    a, b, c = rng.lognormal(0, 1, 30_000), rng.lognormal(0.5, 1, 30_000), rng.lognormal(-0.5, 1, 30_000)
    a[::500] = NAN
    seen: list[float] = []
    r = ternary_chart(a, b, c, max_points=20_000, tick=seen.append)
    assert r["mode"] == "density" and r["points"] is None and seen == sorted(seen) and seen[-1] == 1.0
    counts = np.array(r["density"]["counts"])
    assert counts.shape == (100, 100) and counts.sum() == r["counts"]["used"] == 30_000 - 60
    assert r["density"]["x_edges"][0] == 0.0 and r["density"]["x_edges"][-1] == 1.0
    assert r["density"]["y_edges"][-1] == pytest.approx(HEIGHT) and r["density"]["max"] == counts.max()
    # the cells outside the triangle are empty
    xc = (np.arange(100) + 0.5) / 100
    yc = (np.arange(100) + 0.5) / 100 * HEIGHT
    outside = (yc[:, None] > 2 * HEIGHT * xc[None, :] + 0.02) | (yc[:, None] > 2 * HEIGHT * (1 - xc[None, :]) + 0.02)
    assert counts[outside].sum() == 0 and counts[~outside].sum() == counts.sum()
    assert ternary_chart(a, b, c, max_points=30_000)["mode"] == "points"

    class Stop(Exception):
        pass

    def stop(f: float) -> None:
        raise Stop

    with pytest.raises(Stop):
        ternary_chart(a, b, c, tick=stop)
    json.dumps(r, allow_nan=False)


def test_warm_compiles() -> None:
    probplot.warm()
    domains_mod.warm()
    ternary.warm()
