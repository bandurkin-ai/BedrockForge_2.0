"""Histogram of a sample field (ТЗ 7.25; ADR 0019 §4): the tests of BF `test_histogram.py` (bins), the sum over
the bins and outside the range against the total, the limit of 1000 bins, the logarithmic scale, Т7.25-4 on
200 thousand rows.

Tolerance — counts exact; sums of weights 1e−12 relative against `math.fsum`."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from bf_core.stats import histogram as hg
from bf_core.stats.describe import describe_both
from bf_core.stats.histogram import MAX_BINS, HistogramSpec, histogram, histogram_chart
from bf_core.stats.select import SampleFilter, select_sample, select_values
from bf_core.stats.weighted import StatsError, sort_groups

NAN = math.nan
REL = 1e-12


def refused(code: str, param: str | None, call) -> StatsError:  # type: ignore[no-untyped-def]
    with pytest.raises(StatsError) as e:
        call()
    assert e.value.code == code and e.value.param == param and e.value.message and e.value.hint
    return e.value


def check_total(h: hg.Histogram, v: np.ndarray, w: np.ndarray) -> None:
    """Σ over the bins + outside the range = the total: the counts exactly, the weights to 1e−12."""
    assert int(h.counts.sum()) + h.below.count + h.above.count == len(v) == h.total_count
    assert math.fsum(h.weights) + h.below.weight + h.above.weight == pytest.approx(math.fsum(w), rel=REL)
    assert h.total_weight == pytest.approx(math.fsum(w), rel=REL)
    assert h.nonpositive.count <= h.below.count and h.nonpositive.weight <= h.below.weight


# --- the tests of BF `test_histogram.py` -------------------------------------------------------------------


def test_linear_bins_are_aligned_half_open_and_last_closed() -> None:
    """BF `test_linear_bins_are_aligned_half_open_and_last_closed`."""
    v = np.array([0.0, 0.5, 1.0, 1.0, 1.9, 2.0])
    h = histogram(v, np.ones(6), HistogramSpec(bin_width=1.0))
    assert h.edges.tolist() == [0.0, 1.0, 2.0] and h.counts.tolist() == [2, 4]  # 2.0 falls into the closed last bin
    assert h.weights.tolist() == [2.0, 4.0] and h.width == 1.0 and h.below.count == h.above.count == 0
    h = histogram(v, np.ones(6), HistogramSpec(bin_width=0.5, start=0.5, end=1.5))
    assert h.edges.tolist() == [0.5, 1.0, 1.5] and h.counts.tolist() == [1, 2]
    assert (h.below.count, h.below.weight) == (1, 1.0) and (h.above.count, h.above.weight) == (2, 2.0)
    h = histogram(np.array([3.2, 7.9]), np.ones(2), HistogramSpec(bins=4))
    assert len(h.counts) == 4 and h.edges[0] == 3.2 and h.edges[-1] == pytest.approx(7.9, rel=REL)
    assert h.counts.tolist() == [1, 0, 0, 1]


def test_log_bins_per_decade() -> None:
    """BF `test_log_bins_per_decade`: logarithmic bins are equal in log10."""
    v = np.array([0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0])
    h = histogram(v, np.ones(len(v)), HistogramSpec(log=True, bin_width=1.0))
    assert h.edges == pytest.approx([0.01, 0.1, 1.0, 10.0], rel=REL) and h.counts.tolist() == [2, 2, 3]
    assert h.width == 1.0 and h.log


def test_chart_of_the_bf_table() -> None:
    """BF `test_histogram_api_weights_filters_and_counts` (the bins of the answer) without the database."""
    au = np.array([1.0, 2.0, -0.01, 0.0, NAN, 40.0])
    length = np.array([1.0, 2.0, 1.0, 2.0, 1.0, 2.0])
    plain = histogram_chart(select_values(au, None, SampleFilter(weight="none")), HistogramSpec())
    assert plain["counts"]["rows"] == 6 and plain["counts"]["missing"] == 1 and plain["counts"]["used"] == 5
    assert plain["counts"]["nonpositive_log"] == 0 and not plain["weighted"] and plain["stats_unweighted"] is None
    assert plain["stats"]["mean"] == pytest.approx((1 + 2 - 0.01 + 0 + 40) / 5, rel=REL)
    assert sum(plain["bins"]["counts"]) == 5 and plain["bins"]["cumulative_percent"][-1] == pytest.approx(100)
    assert "GSLIB" in plain["conventions"] and "последний закрыт" in plain["conventions"]

    flt = SampleFilter(weight="length", negatives="half_abs")
    by_length = histogram_chart(select_sample(au, flt, length=length), HistogramSpec())
    v, w = np.array([1, 2, 0.005, 0, 40]), np.array([1, 2, 1, 2, 2])
    assert by_length["stats"]["mean"] == pytest.approx((v * w).sum() / w.sum(), rel=REL)
    assert by_length["stats_unweighted"]["mean"] == pytest.approx(v.mean(), rel=REL) and by_length["weighted"]
    assert sum(by_length["bins"]["weights"]) == pytest.approx(8.0, rel=REL)
    assert sum(by_length["bins"]["percent"]) == pytest.approx(100.0, rel=REL)

    # the logarithmic scale leaves the values ≤ 0 below the range and says how many they are
    sel = select_values(au, None, SampleFilter(weight="none"))
    log = histogram_chart(sel, HistogramSpec(log=True, bin_width=0.5), weighted=False)
    assert log["counts"]["nonpositive_log"] == 2 and sum(log["bins"]["counts"]) == 3 and log["log"]
    assert log["bins"]["below"] == {"count": 2, "weight": 2.0} and log["bins"]["nonpositive"]["count"] == 2
    assert log["bins"]["cumulative_percent"][0] >= 40.0 and log["bins"]["cumulative_percent"][-1] == pytest.approx(100)
    json.dumps([plain, by_length, log], allow_nan=False)

    # BF: a bin width of 1e−9 — 422
    refused("too_many_bins", "bin_width", lambda: histogram_chart(sel, HistogramSpec(bin_width=1e-9), weighted=False))
    empty = select_values(np.array([NAN]), None, SampleFilter(weight="none"))
    assert refused("no_values", None, lambda: histogram_chart(empty, HistogramSpec(), weighted=False))


# --- the sum over the bins, the limit, the logarithmic scale -----------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        HistogramSpec(),
        HistogramSpec(bins=37),
        HistogramSpec(bin_width=0.25, start=0.5, end=6.0),
        HistogramSpec(start=1.0),
        HistogramSpec(end=2.0, bins=12),
        HistogramSpec(log=True),
        HistogramSpec(log=True, bin_width=0.1, start=0.05, end=20.0),
        HistogramSpec(log=True, bins=25, end=3.0),
    ],
)
def test_bins_and_outside_add_up_to_the_total(spec: HistogramSpec) -> None:
    rng = np.random.default_rng(42)
    n = 30_000
    v = np.round(rng.lognormal(0.0, 1.2, n), 2)  # rounded assays: values sit on the edges of round bins
    v[rng.integers(0, n, 300)] = 0.0
    v[rng.integers(0, n, 200)] *= -1.0
    w = rng.uniform(0.2, 3.0, n)
    h = histogram(v, w, spec)
    check_total(h, v, w)
    assert h.nonpositive.count == (int((v <= 0).sum()) if spec.log else 0)
    # every bin against a direct count by its edges (no value sits within 1e−9 of a bin of an edge here,
    # except on it — and those belong to the bin that starts there)
    x = np.log10(v[v > 0]) if spec.log else v
    wx = w[v > 0] if spec.log else w
    edges = np.log10(h.edges) if spec.log else h.edges
    k = np.floor((x - edges[0]) / h.width + 1e-12).astype(np.int64)
    k[(k == len(h.counts)) & (np.abs(x - edges[-1]) <= h.width * 1e-9)] -= 1
    inside = (k >= 0) & (k < len(h.counts))
    assert h.counts.tolist() == np.bincount(k[inside], minlength=len(h.counts)).tolist()
    for j in (0, len(h.counts) // 2, len(h.counts) - 1):
        assert h.weights[j] == pytest.approx(math.fsum(wx[inside & (k == j)]), rel=REL, abs=1e-300)
    assert h.below.count == int((k < 0).sum()) + h.nonpositive.count
    assert h.above.weight == pytest.approx(math.fsum(wx[k >= len(h.counts)]), rel=REL, abs=1e-300)
    # the share and the running share: Σ percent = 100 − outside; the curve starts above what lies below
    total = math.fsum(w)
    assert h.percent == pytest.approx(h.weights / total * 100.0, rel=REL)
    assert h.cumulative_percent[-1] == pytest.approx(100.0 * (1.0 - h.above.weight / total), rel=1e-11)
    assert h.cumulative_percent[0] == pytest.approx(100.0 * (h.below.weight + h.weights[0]) / total, rel=1e-11)
    assert bool(np.all(np.diff(h.cumulative_percent) >= 0.0))
    # the order of the rows does not change the bins: sorted rows give the same counts
    s = sort_groups(v, w)[0]
    hs = histogram(s.v, s.w, spec)
    assert hs.counts.tolist() == h.counts.tolist() and hs.edges.tolist() == h.edges.tolist()
    assert hs.weights == pytest.approx(h.weights, rel=REL)


def test_values_on_round_edges_fall_into_the_bin_that_starts_there() -> None:
    """0.3 / 0.1 is 2.9999999999999996 in float64: the value 0.3 still belongs to the bin [0.3; 0.4)."""
    v = np.arange(31) / 10.0  # 0.0, 0.1 … 3.0
    h = histogram(v, None, HistogramSpec(bin_width=0.1))
    assert len(h.counts) == 30 and h.counts.tolist() == [1] * 29 + [2]  # 3.0 closes the last bin
    assert h.edges[0] == 0.0 and h.edges[-1] == pytest.approx(3.0, rel=REL) and h.weights.tolist() == [1.0] * 29 + [2.0]
    h = histogram(v * 1000.0 + 5.0e5, None, HistogramSpec(bin_width=100.0))
    assert h.counts.tolist() == [1] * 29 + [2] and h.edges[0] == 5.0e5
    # in log10 too: 0.001, 0.01 … 1000 with a width of one decade
    h = histogram(10.0 ** np.arange(-3, 4), None, HistogramSpec(log=True, bin_width=1.0))
    assert h.counts.tolist() == [1, 1, 1, 1, 1, 2]


def test_a_thousand_bins_is_the_limit() -> None:
    v = np.linspace(0.0, 1000.0, 5001)
    ok = histogram(v, None, HistogramSpec(bin_width=1.0))
    assert len(ok.counts) == MAX_BINS == 1000 and int(ok.counts.sum()) == 5001
    assert len(histogram(v, None, HistogramSpec(bins=1000)).counts) == 1000
    # 1001 bins: refused, the hint says what to change
    e = refused("too_many_bins", "bin_width", lambda: histogram(v, None, HistogramSpec(bin_width=1.0, end=1001.0)))
    assert "1001" in e.message and "1000" in e.message and "ширин" in e.hint
    e = refused("too_many_bins", "bins", lambda: HistogramSpec(bins=1001))
    assert "1000" in e.message
    refused("too_many_bins", "bins", lambda: HistogramSpec.from_settings({"bins": 5000}))
    # a width far too small for the range does not hang or overflow
    refused("too_many_bins", "bin_width", lambda: histogram(v, None, HistogramSpec(bin_width=1e-300)))
    refused("too_many_bins", "bin_width", lambda: histogram(np.array([0.0, 1e300]), None, HistogramSpec(bin_width=1.0)))


def test_log_scale_needs_positive_values() -> None:
    e = refused("no_positive", "log", lambda: histogram(np.array([-1.0, 0.0, 0.0]), None, HistogramSpec(log=True)))
    assert "больше нуля" in e.message
    refused("no_values", None, lambda: histogram(np.zeros(0), None, HistogramSpec()))
    refused("no_values", None, lambda: histogram(np.array([NAN, NAN]), None, HistogramSpec(log=True)))
    # one positive value is enough; a start ≤ 0 has no place on a logarithmic axis and is not used (BF)
    h = histogram(np.array([-1.0, 0.0, 2.0]), None, HistogramSpec(log=True, start=0.0))
    assert int(h.counts.sum()) == 1 and h.below.count == 2 and h.nonpositive.count == 2 and h.above.count == 0
    assert h.edges[0] <= 2.0 <= h.edges[-1]


def test_spec_is_checked() -> None:
    def bad(param: str, **kw) -> None:  # type: ignore[no-untyped-def]
        refused("bad_param", param, lambda: HistogramSpec.from_settings(kw))

    bad("bin_width", bin_width=0)
    bad("bin_width", bin_width=-1.0)
    bad("bin_width", bin_width="0.5")
    bad("bin_width", bin_width=math.inf)
    bad("bins", bins=0)
    bad("bins", bins=2.5)
    bad("bins", bins=True)
    bad("start", start=NAN)
    bad("end", end="x")
    bad("end", start=5, end=5)  # BF: «Конец диапазона должен быть больше начала»
    bad("log", log="yes")
    refused("bad_param", "bin_width", lambda: HistogramSpec(bin_width=0.0))
    spec = HistogramSpec.from_settings({"field": "AU", "weight": "length", "log": True, "bins": 20.0, "start": 1})
    assert spec == HistogramSpec(log=True, bins=20, start=1.0) and isinstance(spec.bins, int)
    assert HistogramSpec.from_settings({"bin_width": None, "bins": None, "log": None}) == HistogramSpec()


def test_automatic_width_is_round() -> None:
    """Auto: a «round» width (1, 2, 2.5, 5 × 10^k) for about √n bins within 10 … 60, edges on its multiples."""
    rng = np.random.default_rng(8)
    for n, scale in ((50, 1.0), (400, 37.0), (10_000, 0.004), (200_000, 1.0e6)):
        v = rng.uniform(0.13, 0.94, n) * scale
        h = histogram(v, None, HistogramSpec())
        target = int(np.clip(round(math.sqrt(n)), 10, 60))
        mantissa = h.width / 10.0 ** math.floor(math.log10(h.width) + 1e-9)
        assert min(abs(mantissa - m) for m in (1.0, 2.0, 2.5, 5.0, 10.0)) < 1e-9
        assert target / 2.6 <= len(h.counts) <= target + 1
        assert np.allclose(h.edges / h.width, np.round(h.edges / h.width), atol=1e-6)
        assert h.below.count == h.above.count == 0 and int(h.counts.sum()) == n
    # logarithmic: the width is round in log10 units
    h = histogram(rng.lognormal(0, 2, 5000), None, HistogramSpec(log=True))
    assert h.width in (0.1, 0.2, 0.25, 0.5) and h.below.count == h.above.count == 0
    assert np.diff(np.log10(h.edges)) == pytest.approx(h.width, rel=1e-9)
    # a number of bins: exactly that many from the minimum to the maximum; with a range — over the range
    h = histogram(v, None, HistogramSpec(bins=20))
    assert len(h.counts) == 20 and h.edges[0] == v.min() and h.edges[-1] == pytest.approx(v.max(), rel=REL)
    h = histogram(v, None, HistogramSpec(bins=8, start=2.0e5, end=6.0e5))
    assert h.edges == pytest.approx(np.linspace(2.0e5, 6.0e5, 9), rel=REL) and h.below.count > 0 < h.above.count


def test_hard_data() -> None:
    # one value and equal values: a range is made around them
    h = histogram(np.array([5.0]), np.array([2.5]), HistogramSpec())
    assert int(h.counts.sum()) == 1 and math.fsum(h.weights) == 2.5 and h.edges[0] <= 5.0 <= h.edges[-1]
    h = histogram(np.zeros(100), None, HistogramSpec())
    assert int(h.counts.sum()) == 100 and h.edges[0] <= 0.0 < h.edges[-1]
    h = histogram(np.full(10, 3.0), None, HistogramSpec(log=True))
    assert int(h.counts.sum()) == 10
    # a range that holds no value: everything is outside, nothing is lost
    v = np.array([1.0, 2.0, 3.0])
    h = histogram(v, None, HistogramSpec(start=10.0))
    assert int(h.counts.sum()) == 0 and h.below.count == 3 and h.cumulative_percent[-1] == pytest.approx(100.0)
    h = histogram(v, None, HistogramSpec(end=0.5))
    assert int(h.counts.sum()) == 0 and h.above.count == 3 and h.cumulative_percent[-1] == 0.0
    # values far from zero with a small width; negative values; tiny and huge values on a logarithmic axis
    far = 1.0e9 + np.arange(200) * 0.5
    h = histogram(far, None, HistogramSpec(bin_width=10.0))
    assert h.counts.tolist() == [20] * 10 and h.edges[0] == 1.0e9
    h = histogram(np.array([-5.0, -2.5, -0.1, 0.0, 2.4]), None, HistogramSpec(bin_width=2.5))
    assert h.edges.tolist() == [-5.0, -2.5, 0.0, 2.5] and h.counts.tolist() == [1, 2, 2]
    h = histogram(np.array([1e-300, 1.0, 1e300]), None, HistogramSpec(log=True, bins=6))
    assert h.counts.tolist() == [1, 0, 0, 1, 0, 1] and h.edges[3] == pytest.approx(1.0, rel=1e-9)
    # rows without a value or a weight take no part (the caller's selection leaves none; a guard)
    h = histogram(np.array([1.0, NAN, 2.0, 3.0, math.inf]), np.array([1.0, 1.0, 0.0, NAN, 1.0]), HistogramSpec())
    assert h.total_count == 1 and h.total_weight == 1.0
    with pytest.raises(ValueError):
        histogram(np.zeros(3), np.zeros(2), HistogramSpec())


def test_tick_cancels() -> None:
    seen: list[float] = []
    histogram(np.arange(100.0), None, HistogramSpec(), tick=seen.append)
    assert seen and seen[-1] == 1.0 and seen == sorted(seen)

    class Stop(Exception):
        pass

    def stop(_: float) -> None:
        raise Stop

    with pytest.raises(Stop):
        histogram(np.arange(100.0), None, HistogramSpec(), tick=stop)


def test_t725_4_two_hundred_thousand_samples() -> None:
    """Т7.25-4 at the size of a pytest run (5 млн — bench `chart_histogram`, 13.35): the chart is built, the
    bins equal numpy's, the statistics are those of `describe`."""
    rng = np.random.default_rng(7254)
    n = 200_000
    grade = rng.lognormal(-0.5, 1.4, n)
    grade[rng.integers(0, n, 1500)] = NAN
    length = rng.choice([0.5, 1.0, 1.5, 2.0], n)
    flt = SampleFilter(weight="length", top_cut=50.0)
    sel = select_sample(grade, flt, length=length)
    for spec in (HistogramSpec(), HistogramSpec(log=True), HistogramSpec(bins=200, end=10.0)):
        out = histogram_chart(sel, spec, weighted=flt.weighted)
        bins = out["bins"]
        assert out["counts"]["used"] == sel.n == int(np.isfinite(grade).sum()) and out["counts"]["capped"] > 0
        assert sum(bins["counts"]) + bins["below"]["count"] + bins["above"]["count"] == sel.n
        total = math.fsum(sel.weights)
        outside = bins["below"]["weight"] + bins["above"]["weight"]
        assert math.fsum(bins["weights"]) + outside == pytest.approx(total, rel=REL)
        assert len(bins["edges"]) == len(bins["counts"]) + 1 <= MAX_BINS + 1
        edges = np.array(bins["edges"])
        x, e = (np.log10(sel.values), np.log10(edges)) if spec.log else (sel.values, edges)
        want, _ = np.histogram(x, e, weights=sel.weights)
        assert np.array(bins["weights"]) == pytest.approx(want, rel=1e-9)
        assert bins["counts"] == np.histogram(x, e)[0].tolist()
        weighted, plain = describe_both(sel.sort())
        assert out["stats"] == weighted and out["stats_unweighted"] == plain and out["stats"]["max"] == 50.0
        json.dumps(out, allow_nan=False)
    hg.warm()
