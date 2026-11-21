"""Thompson–Howarth precision of duplicates (ТЗ 8.4.2, Т8.4-5, Т8.4-6; ADR 0020 §4): the groups of the pairs by
their mean, the line of the medians, K, σ(c) and P(c); the block `th` of the section `duplicates`.

Tolerance (10.5 п. 1): a and b — 1e−12 (Т8.4-5 and against the method counted pair by pair), P(c) — 1e−4 of
ТЗ; the groups and their sizes — exact; Т8.4-6 — K·b within [0,045; 0,055] for the seeds 0 … 9."""

from __future__ import annotations

import math
import statistics
import time
from typing import Any

import numpy as np
import pytest
from scipy.stats import norm

from bf_core.qaqc.calc import calc, canonical, context, run
from bf_core.qaqc.filters import map_types, parse_filter, row_mask
from bf_core.qaqc.limits import QaqcError
from bf_core.qaqc.records import TYPE_CODE
from bf_core.qaqc.thompson import PERCENTILES, k_factor, precision_at, thompson_howarth
from tests.qaqcutil import CONFIG, FIVE, build, hard_records, json_ready, refused

K = 1.0 / (math.sqrt(2.0) * norm.ppf(0.75))


def test_k_is_an_expression() -> None:
    assert k_factor() == pytest.approx(K, rel=1e-15) and round(k_factor(), 6) == 1.048358
    assert k_factor() == pytest.approx(1.0483580825, abs=1e-10)


def t845_pairs() -> tuple[np.ndarray, np.ndarray]:
    """Ten groups of eleven pairs: m is the same within a group, |x1 − x2| = 0,1·m."""
    m = np.repeat(np.arange(1.0, 11.0), 11)
    return m * 1.05, m * 0.95


def test_t845_line_and_precision() -> None:
    x1, x2 = t845_pairs()
    shuffle = np.random.default_rng(0).permutation(110)
    th = thompson_howarth(x1[shuffle], x2[shuffle])
    assert (th.state, th.group, th.need, th.size.tolist()) == ("ok", 11, 50, [11] * 10)
    assert th.a == pytest.approx(0.0, abs=1e-12) and th.b == pytest.approx(0.1, rel=1e-12)
    assert np.allclose(th.m, np.arange(1.0, 11.0), rtol=1e-12) and np.allclose(th.med, 0.1 * th.m, rtol=1e-12)
    assert [q for q, _, _ in th.precision] == list(PERCENTILES) == [10, 50, 90]
    for _, c, p in th.precision:
        assert p == pytest.approx(20.9672, abs=1e-4)  # 200·K·0,1 at any c
        assert p == pytest.approx(200 * K * (th.a + th.b * c) / c, rel=1e-12)
    assert [c for _, c, _ in th.precision] == np.percentile(th.m.repeat(11), [10, 50, 90], method="hazen").tolist()
    more = thompson_howarth(x1, x2, 11, [0.5, 100])
    assert [(q, c) for q, c, _ in more.precision[3:]] == [(None, 0.5), (None, 100.0)]
    assert all(p == pytest.approx(20.9672, abs=1e-4) for _, _, p in more.precision)


@pytest.mark.parametrize("seed", range(10))
def test_t846_five_percent_error(seed: int) -> None:
    rng = np.random.Generator(np.random.PCG64(seed))  # the data of ADR 0020 §4
    t = rng.uniform(1, 10, 11000)
    x1 = t * (1 + 0.05 * rng.standard_normal(11000))
    x2 = t * (1 + 0.05 * rng.standard_normal(11000))
    th = thompson_howarth(x1, x2)
    assert th.state == "ok" and th.size.tolist() == [11] * 1000
    assert 0.045 <= 1.048358 * th.b <= 0.055, 1.048358 * th.b
    assert th.k * th.b == pytest.approx(1.048358 * th.b, rel=1e-6)
    p50 = th.precision[1]
    assert p50[0] == 50 and 9.0 < p50[2] < 11.0  # an error of 5 % of each sample — a precision of about 10 %


def direct(x1: Any, x2: Any, group: int) -> dict[str, Any]:
    """ADR 0020 §4 «Томпсон–Ховарт» pair by pair."""
    n = len(x1)
    m = [(float(a) + float(b)) / 2 for a, b in zip(x1, x2, strict=True)]
    d = [abs(float(a) - float(b)) for a, b in zip(x1, x2, strict=True)]
    order = sorted(range(n), key=m.__getitem__)  # stable
    count = n // group
    parts = [order[i * group : (i + 1) * group] for i in range(count - 1)] + [order[(count - 1) * group :]]
    mean = [math.fsum(m[i] for i in part) / len(part) for part in parts]
    med = [statistics.median(d[i] for i in part) for part in parts]
    mx, my = math.fsum(mean) / count, math.fsum(med) / count
    sxx = math.fsum((x - mx) ** 2 for x in mean)
    b = math.fsum((x - mx) * (y - my) for x, y in zip(mean, med, strict=True)) / sxx
    return {"size": [len(part) for part in parts], "mean": mean, "med": med, "a": my - b * mx, "b": b}


@pytest.mark.parametrize(("n", "group", "sizes"), [
    (50, 11, [11, 11, 11, 17]), (54, 11, [11, 11, 11, 21]), (55, 11, [11] * 5), (50, 5, [5] * 10),
    (53, 5, [5] * 9 + [8]), (102, 51, [51, 51]), (152, 51, [51, 101]), (300, 25, [25] * 12), (64, 7, [7] * 8 + [8]),
])  # fmt: skip
def test_groups_and_the_line_against_pair_by_pair_count(n: int, group: int, sizes: list[int]) -> None:
    rng = np.random.default_rng(n + group)
    x1 = np.round(rng.lognormal(0, 1, n), 2)  # rounded: many pairs share their mean — the sort is stable
    x2 = np.round(x1 * (1 + 0.1 * rng.standard_normal(n)), 2).clip(0.0)
    x1[::7] = x2[::7] = 1.0
    th = thompson_howarth(x1, x2, group, [0.3])
    want = direct(x1, x2, group)
    assert th.size.tolist() == sizes == want["size"] and (th.state, th.group) == ("ok", group)
    assert np.allclose(th.m, want["mean"], rtol=1e-12) and np.allclose(th.med, want["med"], rtol=1e-12, atol=1e-15)
    assert th.b == pytest.approx(want["b"], rel=1e-10) and th.a == pytest.approx(want["a"], rel=1e-10, abs=1e-13)
    for _, c, p in th.precision:
        spread = want["a"] + want["b"] * c
        assert (math.isnan(p) and spread <= 0) or p == pytest.approx(200 * K * spread / c, rel=1e-9)
    assert th.precision[3][:2] == (None, 0.3)


def test_few_pairs_and_no_fit() -> None:
    rng = np.random.default_rng(1)
    x1 = rng.lognormal(0, 1, 120)
    x2 = x1 * 1.1
    th = thompson_howarth(x1[:49], x2[:49])
    assert (th.state, th.need, th.group, th.k) == ("few_pairs", 50, 11, k_factor())
    assert math.isnan(th.a) and math.isnan(th.b) and th.m.size == th.med.size == th.size.size == 0
    assert th.precision == () and thompson_howarth(x1[:50], x2[:50]).state == "ok"
    assert thompson_howarth([], []).state == "few_pairs"
    # a group of 51 needs two groups: 102 pairs, not 50
    wide = thompson_howarth(x1[:101], x2[:101], 51)
    assert (wide.state, wide.need) == ("few_pairs", 102) and thompson_howarth(x1[:102], x2[:102], 51).state == "ok"
    assert thompson_howarth(x1[:50], x2[:50], 5).size.tolist() == [5] * 10
    # every pair has the same mean: no line through the groups — not a zero slope
    flat = thompson_howarth(np.full(60, 1.1), np.full(60, 0.9))
    assert flat.state == "no_fit" and math.isnan(flat.a) and math.isnan(flat.b) and flat.precision == ()
    assert flat.m.tolist() == [1.0] * 5 and flat.med.tolist() == pytest.approx([0.2] * 5)


def test_group_and_given_contents_are_checked() -> None:
    x = np.arange(1.0, 121.0)
    for group in (12, 4, 53, 11.5, True, "11"):  # even, out of 5 … 51, not a whole number
        with pytest.raises(QaqcError) as e:
            thompson_howarth(x, x, group)  # type: ignore[arg-type]
        assert (e.value.code, e.value.param) == ("bad_param", "th_group")
    assert thompson_howarth(x, x * 1.1, 5).group == 5 and thompson_howarth(x, x * 1.1, 51).group == 51
    for c in ([0.0], [-1.0], ["1"], [math.nan], [True], 5, [1.0] * 21):
        with pytest.raises(QaqcError) as e:
            thompson_howarth(x, x * 1.1, 11, c)
        assert (e.value.code, e.value.param) == ("bad_param", "c") and e.value.hint
    assert len(thompson_howarth(x, x * 1.1, 11, [1.0] * 20).precision) == 23
    with pytest.raises(ValueError):
        thompson_howarth(x, x[:5])


def test_precision_is_empty_where_the_line_is_not_positive() -> None:
    assert precision_at(1.0, 0.0, 0.1, K) == pytest.approx(20.9672, abs=1e-4)
    assert precision_at(2.0, 0.05, 0.1, 1.0) == pytest.approx(200 * 0.25 / 2.0)
    for c, a, b in ((1.0, -0.2, 0.1), (2.0, -0.2, 0.1), (0.0, 0.1, 0.1), (-1.0, 0.1, 0.1), (1.0, math.nan, 0.1)):
        assert math.isnan(precision_at(c, a, b, K))  # a + b·c ≤ 0, c ≤ 0, no line
    assert math.isnan(precision_at(1e-320, 1e300, 1.0, K))  # overflows: not computed, not inf
    # a line with a negative intercept: the differences vanish at low contents
    m = np.repeat(np.arange(2.0, 12.0), 11)
    d = 0.1 * m - 0.15
    th = thompson_howarth(m + d / 2, m - d / 2, 11, [0.5, 1.5, 1.6, 3.0])
    assert th.a == pytest.approx(-0.15, abs=1e-12) and th.b == pytest.approx(0.1, rel=1e-12)
    given = {c: p for q, c, p in th.precision if q is None}
    assert math.isnan(given[0.5])  # a + b·c < 0
    assert math.isnan(given[1.5]) or given[1.5] < 1e-8  # a + b·c is 0 within the rounding
    assert given[1.6] == pytest.approx(200 * K * 0.01 / 1.6, rel=1e-9) and given[3.0] > 0


def test_block_th_of_the_section() -> None:
    x1, x2 = t845_pairs()
    pairs = zip(x1.tolist(), x2.tolist(), strict=True)
    rows = [{"type": "DUP_C", "lab": "L1", "parent": a, "au": b} for a, b in pairs]
    recs = build(rows, has_dl=False)
    a = calc("duplicates", recs, CONFIG, [], "AU", kind="DUP_C", c=[2.5])
    th = a["th"]
    assert (th["state"], th["group"], th["need"], th["k"]) == ("ok", 11, 50, k_factor())
    assert th["a"] == pytest.approx(0.0, abs=1e-12) and th["b"] == pytest.approx(0.1, rel=1e-12)
    assert th["groups"]["m"] == pytest.approx(list(range(1, 11))) and th["groups"]["n"] == [11] * 10
    assert th["groups"]["med"] == pytest.approx([0.1 * m for m in range(1, 11)])
    assert [(row["q"], row["c"]) for row in th["precision"]][3] == (None, 2.5)
    assert all(row["p"] == pytest.approx(20.9672, abs=1e-4) for row in th["precision"])
    json_ready(a)
    # the group of the settings; fewer pairs than it needs
    cfg = {**CONFIG, "limits": {**CONFIG["limits"], "th_group": 5}}
    assert calc("duplicates", recs, cfg, [], "AU", kind="DUP_C")["th"]["groups"]["n"] == [5] * 22
    few = calc("duplicates", build(rows[:49], has_dl=False), CONFIG, [], "AU", kind="DUP_C")["th"]
    assert few == {"state": "few_pairs", "group": 11, "need": 50, "a": None, "b": None, "k": k_factor(),
                   "groups": {"m": [], "med": [], "n": []}, "precision": []}  # fmt: skip
    none = calc("duplicates", recs, CONFIG, [], "AU", kind="DUP_P")["th"]
    assert none["state"] == "few_pairs" and none["precision"] == []
    refused("bad_param", "c", lambda: calc("duplicates", recs, CONFIG, [], "AU", kind="DUP_C", c=[-1]))
    even = {**CONFIG, "limits": {"th_group": 12}}
    refused("bad_param", "th_group", lambda: calc("duplicates", recs, even, [], "AU", kind="DUP_C"))


@pytest.mark.parametrize("number", (0, 3, 4))
def test_t848_block_th_by_a_filter_and_over_the_records_taken(number: int) -> None:
    recs, cfg, ref = hard_records()
    flt = FIVE[number]
    part = recs.take(row_mask(recs, parse_filter(flt), map_types(recs.text(TYPE_CODE), cfg)))
    by_filter, taken = context(recs, cfg, ref, "AU", flt), context(part, cfg, ref, "AU")
    states = set()
    for kind in ("DUP_F", "DUP_P"):
        params = {"kind": kind, "c": [0.5, 2]}
        a, b = run(by_filter, "duplicates", **params), run(taken, "duplicates", **params)
        assert canonical(a) == canonical(b) and canonical(a["th"]) == canonical(b["th"])
        states.add(a["th"]["state"])
    assert "ok" in states


def test_hundred_thousand_pairs_in_time() -> None:
    """The criterion of 14.6 is 150 thousand pairs within 0.1 s (measured by `bench`); here — 100 thousand
    within the same bound, the best of three, for the least and the greatest group."""
    rng = np.random.default_rng(23)
    n = 100_000
    x1 = rng.lognormal(0, 1, n)
    x2 = np.abs(x1 * (1 + 0.1 * rng.standard_normal(n)))
    k_factor()
    for group in (5, 51):
        best = math.inf
        for _ in range(3):
            t0 = time.perf_counter()
            th = thompson_howarth(x1, x2, group)
            best = min(best, time.perf_counter() - t0)
        assert th.state == "ok" and th.size.sum() == n and 0.05 < th.k * th.b < 0.12
        assert best <= 0.1, f"{best:.3f} s"
