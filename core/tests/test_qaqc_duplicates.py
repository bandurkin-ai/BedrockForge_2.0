"""Duplicates of QAQC (ТЗ 8.4.2, Т8.4-3, Т8.4-4; ADR 0020 §4): HARD, HRD, AMPD, which pairs are taken and why the
others are not, the ranked HARD with the shares and the criterion of a type, the scatter (RMA, r), the section
`duplicates`.

Tolerance (10.5 п. 1): the formulas — 1e−12 against exact fractions; the counters, the shares, the points of
the ranked chart and the criterion — exact; the section by a filter and over the records taken — the same
bytes (Т8.4-8)."""

from __future__ import annotations

import math
import subprocess
import sys
import time
from fractions import Fraction
from typing import Any

import numpy as np
import pytest

from bf_core.qaqc.calc import SECTIONS, calc, canonical, context, run
from bf_core.qaqc.duplicates import EXCLUDED, RANKED_POINTS, hard_limit, measure, scatter_stats, select
from bf_core.qaqc.filters import map_types, parse_filter, row_mask
from bf_core.qaqc.limits import Limits
from bf_core.qaqc.records import TYPE_CODE
from tests.qaqcutil import CONFIG, FIVE, NAN, build, hard_records, json_ready, refused

REL = 1e-12


def numbers(values: Any) -> np.ndarray:
    return np.array([NAN if v is None else v for v in values], dtype=np.float64)


def test_t843_hard_hrd_ampd() -> None:
    m = measure([1.0], [1.2], 10.0)
    assert m.hard[0] == pytest.approx(float(Fraction(100, 11)), rel=REL)  # 9,090909 %
    assert m.hrd[0] == pytest.approx(-float(Fraction(100, 11)), rel=REL)  # −9,090909 %
    assert 2.0 * m.hard[0] == pytest.approx(float(Fraction(200, 11)), rel=REL)  # AMPD = 18,181818 %
    assert round(m.hard[0], 6) == 9.090909 and round(m.hrd[0], 6) == -9.090909 and round(2 * m.hard[0], 6) == 18.181818
    assert (m.share10, m.share20, m.passed) == (100.0, 100.0, True)
    # the formulas against exact fractions on random pairs
    rng = np.random.default_rng(3)
    x1, x2 = np.round(rng.lognormal(0, 2, 500), 4) + 1e-4, np.round(rng.lognormal(0, 2, 500), 4) + 1e-4
    m = measure(x1, x2, 10.0)
    pairs = zip(x1.tolist(), x2.tolist(), strict=True)
    exact = [100 * (Fraction(a) - Fraction(b)) / (Fraction(a) + Fraction(b)) for a, b in pairs]
    assert np.allclose(m.hrd, [float(e) for e in exact], rtol=REL, atol=0.0)
    assert np.allclose(m.hard, [float(abs(e)) for e in exact], rtol=REL, atol=0.0)
    assert measure([2.0, 0.0, 5.0], [2.0, 3.0, 0.0], 10.0).hard.tolist() == [0.0, 100.0, 100.0]


def test_t844_shares_and_ranked_points_exactly() -> None:
    h = np.arange(1, 21, dtype=np.float64)
    order = np.random.default_rng(1).permutation(20)  # the pairs come in any order
    x1, x2 = (100.0 + h)[order], (100.0 - h)[order]
    m = measure(x1, x2, 10.0)
    assert m.hard.tolist() == h[order].tolist() and m.hrd.tolist() == h[order].tolist()  # HARD = h exactly
    assert (m.share10, m.share20, m.share30, m.share_at_limit, m.passed) == (50.0, 100.0, 100.0, 50.0, False)
    assert m.ranked_hard.tolist() == h.tolist() and m.ranked_p.tolist() == [5.0 * i for i in range(1, 21)]
    wide = measure(x1, x2, 20.0)
    assert (wide.share_at_limit, wide.passed) == (100.0, True) and measure(x1, x2, 30.0).passed is True
    assert measure(x2, x1, 10.0).hrd.tolist() == (-h[order]).tolist()
    # the criterion: the share right on `hard_share` passes; a hair less does not
    nine = measure([100.0] * 9 + [150.0], [100.0] * 10, 10.0)
    assert (nine.share_at_limit, nine.passed) == (90.0, True)
    assert measure([100.0] * 9 + [150.0], [100.0] * 10, 10.0, 90.5).passed is False
    assert measure([100.0] * 8 + [150.0] * 2, [100.0] * 10, 10.0).passed is False
    # HARD right on the limit is within it: (105; 95) and (110; 90) are 5 and 10 % exactly, (111; 90) is past
    edge = measure([105.0, 110.0, 111.0], [95.0, 90.0, 90.0], 10.0)
    assert edge.hard.tolist()[:2] == [5.0, 10.0] and edge.share10 == pytest.approx(200 / 3, rel=REL)
    empty = measure([], [], 10.0)
    assert empty.passed is None and math.isnan(empty.share10) and empty.ranked_p.size == 0 and empty.hard.size == 0


def test_criteria_by_the_type() -> None:
    lim = Limits()
    assert [hard_limit(k, lim) for k in ("DUP_P", "DUP_C", "DUP_F", "UMP")] == [10.0, 20.0, 30.0, 10.0]
    own = Limits(hard_p=5, hard_c=15, hard_f=40, hard_ump=7.5)
    assert [hard_limit(k, own) for k in ("DUP_P", "DUP_C", "DUP_F", "UMP")] == [5.0, 15.0, 40.0, 7.5]
    rows = []
    for code in ("DUP_F", "DUP_C", "DUP_P"):  # HARD of 5, 15 and 25 % in every type
        rows += [{"type": code, "lab": "L1", "au": 100.0 - h, "parent": 100.0 + h} for h in (5.0, 15.0, 25.0)] * 4
    recs = build(rows, has_dl=False)
    got = {k: calc("duplicates", recs, CONFIG, [], "AU", kind=k) for k in ("DUP_F", "DUP_C", "DUP_P")}
    assert [got[k]["hard_limit"] for k in got] == [30.0, 20.0, 10.0]
    assert [got[k]["share_at_limit"] for k in got] == pytest.approx([100.0, 200 / 3, 100 / 3], rel=REL)
    assert [got[k]["passed"] for k in got] == [True, False, False] and got["DUP_F"]["share_limit"] == 90.0
    cfg = {**CONFIG, "limits": {**CONFIG["limits"], "hard_p": 25, "hard_share": 100}}
    easy = calc("duplicates", recs, cfg, [], "AU", kind="DUP_P")
    assert (easy["hard_limit"], easy["share_limit"]) == (25.0, 100.0)
    assert (easy["share_at_limit"], easy["passed"]) == (100.0, True)
    assert calc("duplicates", recs, CONFIG, [], "AU")["kind"] == "DUP_F"  # the default
    refused("bad_param", "kind", lambda: calc("duplicates", recs, CONFIG, [], "AU", kind="UMP"))


def test_which_pairs_are_taken_and_the_counters() -> None:
    #      taken   no parent  no x1  no x2  x1 < 0  x2 < 0  both 0  below  on k_д·DL  no DL: taken   inf
    x1 = [1.0, None, None, 2.0, -0.1, 2.0, 0.0, 0.04, 0.05, 0.001, math.inf]
    x2 = [1.2, 3.0, 1.0, None, 2.0, -5.0, 0.0, 0.05, 0.05, 0.001, 1.0]
    dl = [0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, None, 0.01]
    orphan = [False, True] + [False] * 9
    s = select(numbers(x1), numbers(x2), numbers(dl), 5.0, no_parent=orphan)
    assert s.used.tolist() == [True, False, False, False, False, False, False, False, True, True, False]
    assert s.excluded == {"no_parent": 1, "missing": 3, "negative": 2, "zero": 1, "below": 1} and s.no_dl == 1
    assert tuple(s.excluded) == EXCLUDED and sum(s.excluded.values()) + int(s.used.sum()) == 11
    # every pair is counted once, under the first reason: no parent before a missing value, negative before zero
    s = select(numbers([None, -1.0, -0.0]), numbers([None, 1.0, 0.0]), None, 5.0, no_parent=[True, False, False])
    assert s.excluded == {"no_parent": 1, "missing": 0, "negative": 1, "zero": 1, "below": 0}
    # without a DL the screening is not applied; neither with k_д = 0; a DL that is not > 0 is none
    low = numbers([0.001, 0.002, 0.003])
    assert select(low, low, None, 5.0).used.all() and select(low, low, None, 5.0).no_dl == 3
    assert select(low, low, numbers([0.01] * 3), 0.0).used.all()
    assert select(low, low, numbers([0.0, -1.0, math.inf]), 5.0).no_dl == 3
    assert select(low, low, numbers([0.01] * 3), 5.0).excluded["below"] == 3
    # the mean against k_д·DL in any units: the border is taken
    for scale in (1e-9, 1.0, 1e6):
        first, second = numbers([0.05, 0.04, 0.0499]) * scale, numbers([0.05, 0.06, 0.05]) * scale
        assert select(first, second, numbers([0.01] * 3) * scale).used.tolist() == [True, True, False], scale
    with pytest.raises(ValueError):
        select(low, low[:2])
    with pytest.raises(ValueError):
        select(low, low, None, 5.0, no_parent=[True])


def test_scatter_rma_on_the_line_of_equality() -> None:
    x = np.round(np.random.default_rng(2).lognormal(0, 1, 300), 3)
    s = scatter_stats(x, x)
    assert s["rma"] == {"a": 0.0, "b": 1.0} and s["pearson"] == 1.0 and s["mean1"] == s["mean2"]  # exactly
    assert s["mean1"] == pytest.approx(math.fsum(x) / 300, rel=REL)
    y = 2.0 * x + 1.0
    s = scatter_stats(x, y)
    assert s["rma"]["b"] == pytest.approx(2.0, rel=REL) and s["rma"]["a"] == pytest.approx(1.0, abs=1e-11)
    noisy = x * (1 + 0.1 * np.random.default_rng(4).standard_normal(300))
    s = scatter_stats(x, noisy)
    sx, sy = np.std(x), np.std(noisy)
    assert s["rma"]["b"] == pytest.approx(sy / sx, rel=1e-10) and 0.9 < s["pearson"] < 1.0
    assert s["rma"]["a"] == pytest.approx(noisy.mean() - sy / sx * x.mean(), abs=1e-10)
    none = scatter_stats([], [])
    assert none == {"mean1": None, "mean2": None, "pearson": None, "rma": {"a": None, "b": None}}
    one = scatter_stats([1.0], [2.0])
    assert (one["mean1"], one["mean2"], one["pearson"], one["rma"]) == (1.0, 2.0, None, {"a": None, "b": None})
    flat = scatter_stats([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])  # no spread of x: no line, not a zero
    assert flat["rma"] == {"a": None, "b": None} and flat["pearson"] is None


def test_ranked_chart_of_many_pairs() -> None:
    rng = np.random.default_rng(6)
    n = 5001
    x1 = rng.lognormal(0, 1, n)
    x2 = x1 * (1 + 0.1 * rng.standard_normal(n))
    m = measure(x1, np.abs(x2), 10.0)
    assert m.ranked_p.size == m.ranked_hard.size == RANKED_POINTS == 2000
    assert m.ranked_p[0] == 100.0 / n and m.ranked_p[-1] == 100.0  # the first and the last pair are there
    assert (np.diff(m.ranked_p) > 0).all() and (np.diff(m.ranked_hard) >= 0).all()
    order = np.sort(m.hard)
    rank = np.round(m.ranked_p * n / 100.0).astype(int) - 1
    assert m.ranked_hard.tolist() == order[rank].tolist() and np.diff(rank).max() <= 3
    assert m.share10 == 100.0 * int((m.hard <= 10.0).sum()) / n  # the shares are of all the pairs
    assert measure(x1[:2000], np.abs(x2[:2000]), 10.0).ranked_p.size == 2000  # up to 2000 — every pair


def direct(x1: Any, x2: Any, dl: Any, orphan: Any, k: float, limit: float) -> dict[str, Any]:
    """ADR 0020 §4 «Дубликаты» pair by pair."""
    counts = dict.fromkeys(EXCLUDED, 0)
    used, hard = [], []
    for i in range(len(x1)):
        a, b = float(x1[i]), float(x2[i])
        if orphan[i]:
            counts["no_parent"] += 1
        elif not (math.isfinite(a) and math.isfinite(b)):
            counts["missing"] += 1
        elif a < 0 or b < 0:
            counts["negative"] += 1
        elif a + b == 0:
            counts["zero"] += 1
        elif math.isfinite(dl[i]) and dl[i] > 0 and k > (a + b) / 2 / dl[i] + 1e-9 * max(1.0, (a + b) / 2 / dl[i]):
            counts["below"] += 1
        else:
            used.append(i)
            hard.append(100.0 * abs(a - b) / (a + b))
    within = [sum(1 for h in hard if h <= t + 1e-9 * max(1.0, t)) for t in (10.0, 20.0, 30.0, limit)]
    return {"used": used, "hard": hard, "excluded": counts, "share": [100.0 * w / len(hard) for w in within]}


@pytest.mark.parametrize(("seed", "k", "limit"), [(1, 5.0, 10.0), (2, 0.0, 20.0), (3, 12.0, 30.0)])
def test_hard_pairs_against_pair_by_pair_count(seed: int, k: float, limit: float) -> None:
    rng = np.random.default_rng(seed)
    n = 4000
    dl = rng.choice([0.01, 0.02, NAN, 0.0], n, p=[0.5, 0.3, 0.15, 0.05])
    base = np.round(rng.lognormal(-2, 2, n), 4)
    x1 = base.copy()
    x2 = np.round(base * (1 + rng.choice([0.0, 0.05, 0.1, 0.2, 0.3], n) * rng.choice([-1, 1], n)), 4)
    edge = rng.random(n) < 0.1  # pairs right on HARD of 10, 20, 30 % and on the mean of k_д·DL
    h = rng.choice([10.0, 20.0, 30.0], n)
    x1[edge], x2[edge] = (100.0 + h)[edge], (100.0 - h)[edge]
    on_dl = rng.random(n) < 0.1
    x1[on_dl] = x2[on_dl] = (k * np.where(np.isfinite(dl) & (dl > 0), dl, 0.01))[on_dl]
    for values in (x1, x2):
        values[rng.random(n) < 0.04] = NAN
        values[rng.random(n) < 0.03] = -0.5
        values[rng.random(n) < 0.03] = 0.0
    orphan = rng.random(n) < 0.05
    s = select(x1, x2, dl, k, no_parent=orphan)
    want = direct(x1, x2, dl, orphan, k, limit)
    assert np.flatnonzero(s.used).tolist() == want["used"] and s.excluded == want["excluded"]
    assert all(v > 0 for name, v in s.excluded.items() if name != "below" or k > 0)
    m = measure(x1[s.used], x2[s.used], limit)
    assert np.allclose(m.hard, want["hard"], rtol=REL, atol=0.0)
    assert [m.share10, m.share20, m.share30, m.share_at_limit] == want["share"]
    assert m.hard.size > 2000 and np.array_equal(m.ranked_hard[[0, -1]], [m.hard.min(), m.hard.max()])
    few = measure(x1[s.used][:1500], x2[s.used][:1500], limit)
    assert few.ranked_hard.tolist() == sorted(few.hard.tolist())  # up to 2000 pairs — every one is a point


def test_section_duplicates() -> None:
    rows = [{"type": "DUP_P", "lab": "L1", "au": 100.0 - h, "parent": 100.0 + h, "seq": h} for h in range(1, 21)]
    rows += [
        {"type": "DUP_P", "lab": "L1", "au": 1.0},  # no parent
        {"type": "DUP_P", "lab": "L1", "au": None, "parent": 2.0},  # no value
        {"type": "DUP_P", "lab": "L1", "au": 0.02, "parent": 0.03},  # the mean is below 5·DL
        {"type": "DUP_P", "lab": "L2", "au": 0.02, "parent": 0.03},  # no DL for the laboratory: taken
        {"type": "DUP_P", "lab": "L1", "au": 2.0, "parent": 2.0, "parent_row": -1},  # a parent value without a row
        {"type": "ORIG", "lab": "L1", "au": 5.0},
        {"type": "DUP_F", "lab": "L1", "au": 1.0, "parent": 1.5},
    ]
    recs = build(rows, has_dl=False)
    cfg = {**CONFIG, "dl": [{"element": "AU", "lab": "L1", "value": 0.01, "unit": "ppm"}]}
    a = calc("duplicates", recs, cfg, [], "AU", kind="DUP_P", samples=True)
    assert (a["kind"], a["pairs"], a["used"]) == ("DUP_P", 25, 22)
    assert a["excluded"] == {"no_parent": 1, "missing": 1, "negative": 0, "zero": 0, "below": 1}
    assert (a["hard_limit"], a["share_limit"], a["passed"]) == (10.0, 90.0, False)
    assert (a["share10"], a["share20"], a["share30"], a["share_at_limit"]) == (50.0, 100.0, 100.0, 50.0)
    assert a["points"]["row"] == [*range(20), 23, 24] and a["points"]["hard"][:20] == [float(h) for h in range(1, 21)]
    assert a["points"]["x1"][:3] == [101.0, 102.0, 103.0] and a["points"]["x2"][:3] == [99.0, 98.0, 97.0]
    assert a["points"]["hard"][20:] == [pytest.approx(20.0), 0.0] and "thinned" not in a  # 20 % — on the border
    assert a["ranked"]["hard"] == sorted(a["points"]["hard"]) and a["ranked"]["p"][-1] == 100.0
    assert a["stats"]["mean1"] == pytest.approx(math.fsum(a["points"]["x1"]) / 22, rel=REL)
    u, v = np.array(a["points"]["x1"]), np.array(a["points"]["x2"])
    assert a["stats"]["pearson"] == pytest.approx(np.corrcoef(u, v)[0, 1], rel=1e-10)
    assert a["stats"]["rma"]["b"] == pytest.approx(np.std(v) / np.std(u), rel=1e-10)
    assert a["stats"]["mean2"] == pytest.approx(v.mean(), rel=REL)
    assert a["samples"]["row"] == a["points"]["row"] and a["samples"]["hard"] == a["points"]["hard"]
    assert a["samples"]["hrd"][:2] == [1.0, 2.0] and a["samples"]["ampd"][:2] == [2.0, 4.0]
    assert a["samples"]["mean"][:2] == [100.0, 100.0]
    assert len(a["notes"]) == 1 and "DL" in a["notes"][0] and "пульповые" in a["notes"][0]
    assert a["counts"] == {"rows": 27, "filtered": 27, "control": 26, "unmapped": 0, "missing": 1}
    json_ready(a)
    # thinned for a chart: every pair past the limit of HARD stays
    thin = calc("duplicates", recs, cfg, [], "AU", kind="DUP_P", max_points=15)
    past = [row for row, hard in zip(a["points"]["row"], a["points"]["hard"], strict=True) if hard > 10]
    assert thin["thinned"] == {"shown": 15, "total": 22} and set(past) <= set(thin["points"]["row"])
    assert thin["ranked"] == a["ranked"] and thin["share10"] == a["share10"]  # the numbers — over every pair
    # the line of the summary
    line = calc("summary", recs, cfg, [], "AU")["duplicates"]
    assert line["DUP_P"] == {"pairs": 25, "used": 22, "hard_limit": 10.0, "share_at_limit": a["share10"],
                             "passed": False}  # fmt: skip
    assert line["DUP_F"] == {"pairs": 1, "used": 1, "hard_limit": 30.0, "share_at_limit": 100.0, "passed": True}
    assert line["DUP_C"] == {"pairs": 0, "used": 0, "hard_limit": 20.0, "share_at_limit": None, "passed": None}
    assert "duplicates" in SECTIONS and len(calc("summary", recs, cfg, [], "AU")["notes"]) == 1


@pytest.mark.parametrize("number", range(5))
def test_t848_duplicates_by_a_filter_and_over_the_records_taken(number: int) -> None:
    recs, cfg, ref = hard_records()
    flt = FIVE[number]
    part = recs.take(row_mask(recs, parse_filter(flt), map_types(recs.text(TYPE_CODE), cfg)))
    by_filter, taken = context(recs, cfg, ref, "AU", flt), context(part, cfg, ref, "AU")
    used = 0
    for kind in ("DUP_F", "DUP_C", "DUP_P"):
        for params in ({}, {"samples": True}, {"max_points": 40}):
            a, b = run(by_filter, "duplicates", kind=kind, **params), run(taken, "duplicates", kind=kind, **params)
            assert canonical(a) == canonical(b), (kind, params)  # byte for byte
            json_ready(a)
        used += a["used"]
    assert canonical(run(by_filter, "summary")) == canonical(run(taken, "summary"))
    assert used > 50 or number == 1  # the filter by the types leaves no duplicates; the one by CRM does not cut them


def test_hard_section_holds_every_reason() -> None:
    recs, cfg, ref = hard_records()
    a = calc("duplicates", recs, cfg, ref, "AU", kind="DUP_F")
    assert all(v > 0 for v in a["excluded"].values()) and a["pairs"] == a["used"] + sum(a["excluded"].values())
    assert any("отсев" in note for note in a["notes"]) and 0.9 < a["stats"]["pearson"] < 1.0
    none = calc("duplicates", recs, cfg, ref, "AU", kind="DUP_C")  # no such records at all
    assert (none["pairs"], none["used"], none["passed"], none["share10"]) == (0, 0, None, None)
    assert none["points"] == {"row": [], "x1": [], "x2": [], "hard": []} and none["ranked"] == {"p": [], "hard": []}
    assert none["stats"] == {"mean1": None, "mean2": None, "pearson": None, "rma": {"a": None, "b": None}}


def test_kernel_imports_no_numba_until_the_scatter() -> None:
    code = (
        "import sys; from bf_core.qaqc import duplicates as d;"
        "s = d.select([1.0, 2.0], [1.1, 2.1]); d.measure([1.0, 2.0], [1.1, 2.1], 10.0);"
        "heavy=[m for m in ('numba','scipy','pandas','pyarrow','duckdb','sqlalchemy','fastapi') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_hundred_thousand_pairs_in_time() -> None:
    """The criterion of 14.5 is 150 thousand pairs within 0.1 s (measured by `bench`); here — 100 thousand
    within the same bound, the best of three: the selection, HARD, the shares, the ranked chart, the scatter."""
    rng = np.random.default_rng(17)
    n = 100_000
    x1 = rng.lognormal(0, 1, n)
    x2 = np.abs(x1 * (1 + 0.1 * rng.standard_normal(n)))
    dl = np.full(n, 0.01)
    scatter_stats(x1[:10], x2[:10])  # the kernels of Э13 are loaded
    best = math.inf
    for _ in range(3):
        t0 = time.perf_counter()
        s = select(x1, x2, dl, 5.0)
        m = measure(x1[s.used], x2[s.used], 10.0)
        stats = scatter_stats(x1[s.used], x2[s.used])
        best = min(best, time.perf_counter() - t0)
    assert m.hard.size > 99_000 and stats["pearson"] > 0.9
    assert best <= 0.1, f"{best:.3f} s"
