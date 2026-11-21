"""External control of QAQC (ТЗ 8.4.2, Т8.4-7; ADR 0020 §4): the bias, the paired t-test, the RMA line with its
95 % intervals by a bootstrap of the pairs, the groups by the pairs of laboratories, the section `external`.

Tolerance (10.5 п. 1): Т8.4-7 — exact (bias 0, slope 1, intercept 0, intervals [1; 1] and [0; 0]); t and p —
1e−12 against `scipy.stats.ttest_rel` and the formula; the intervals — 1e−9 against the bootstrap counted
realization by realization on the same draws; one seed — the same bits; the section by a filter and over the
records taken — the same bytes (Т8.4-8)."""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
import pytest
from scipy import stats as sps

from bf_core.qaqc import calc_pairs
from bf_core.qaqc import external as ext
from bf_core.qaqc.calc import SECTIONS, calc, canonical, context, run
from bf_core.qaqc.external import bias, paired_t, realizations, rma_intervals
from bf_core.qaqc.filters import map_types, parse_filter, row_mask
from bf_core.qaqc.records import TYPE_CODE
from bf_core.stats.bootstrap import draws
from tests.qaqcutil import CONFIG, FIVE, build, hard_records, json_ready

REL = 1e-12


@pytest.fixture(autouse=True)
def _no_intervals_kept() -> None:
    """Every test bootstraps for itself: the intervals the process keeps are forgotten first."""
    calc_pairs.clear_intervals()


def pairs(n: int, seed: int, error: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """Two laboratories measuring the same contents, each with its own relative error."""
    rng = np.random.default_rng(seed)
    t = rng.lognormal(0, 1, n)
    return t * (1 + error * rng.standard_normal(n)), t * (1 + error * rng.standard_normal(n))


def ump(main: Any, check: Any, lab: str | None = "B", lab_main: str | None = "A") -> list[dict[str, Any]]:
    """Records of the external control: the values of the main laboratory and of the control one."""
    return [{"type": "UMP", "lab": lab, "parent_lab": lab_main, "au": float(v), "parent": float(u)}
            for u, v in zip(main, check, strict=True)]  # fmt: skip


def test_t847_equal_values() -> None:
    x = np.round(np.random.default_rng(1).lognormal(0, 1, 200), 3)
    ci = rma_intervals(x, x, 300, 12345)
    assert (ci.a, ci.b, ci.n_boot) == ((0.0, 0.0), (1.0, 1.0), 300)  # exactly, in every realization
    assert all(math.isnan(v) for v in paired_t(x, x))  # every difference is 0: nothing to test
    assert bias(float(x.mean()), float(x.mean())) == 0.0
    a = calc("external", build(ump(x, x, "SGS", "ALS"), has_dl=False), CONFIG, [], "AU")
    (g,) = a["groups"]
    assert (g["lab_main"], g["lab_check"], g["pairs"], g["used"]) == ("ALS", "SGS", 200, 200)
    assert (g["bias"], g["rma"]["b"], g["rma"]["a"], g["pearson"]) == (0.0, 1.0, 0.0, 1.0)
    assert (g["rma"]["b_ci"], g["rma"]["a_ci"]) == ([1.0, 1.0], [0.0, 0.0])
    assert (g["rma"]["n_boot"], g["rma"]["seed"]) == (1000, 12345)
    assert (g["t"], g["p"], g["df"]) == (None, None, None)
    assert g["notes"] == ["Все разности пар равны: t-критерий не определён."]
    assert (g["share10"], g["share_at_limit"], g["passed"], g["mean_main"]) == (100.0, 100.0, True, g["mean_check"])
    assert (a["hard_limit"], a["share_limit"]) == (10.0, 90.0)
    json_ready(a)


def test_t_and_p_against_scipy_and_the_formula() -> None:
    x1, x2 = pairs(400, 2)
    x2 = x2 * 1.01
    t, p, df = paired_t(x1, x2)
    want = sps.ttest_rel(x1, x2)
    assert t == pytest.approx(want.statistic, rel=REL) and p == pytest.approx(want.pvalue, rel=REL) and df == 399
    d = x1 - x2
    by_hand = d.mean() / (d.std(ddof=1) / math.sqrt(400))
    assert t == pytest.approx(by_hand, rel=1e-11) and p == pytest.approx(2 * sps.t.sf(abs(by_hand), 399), rel=1e-10)
    assert paired_t(x2, x1)[0] == pytest.approx(-t, rel=REL)
    # a constant shift: the differences are all equal — not defined, not NaN in the answer and not a zero
    assert all(math.isnan(v) for v in paired_t([1.0, 2.0, 3.0], [2.0, 3.0, 4.0]))
    assert all(math.isnan(v) for v in paired_t([1.0], [2.0])) and all(math.isnan(v) for v in paired_t([], []))
    t2, p2, df2 = paired_t([1.0, 2.0], [1.5, 2.1])
    assert df2 == 1 and t2 == pytest.approx(sps.ttest_rel([1.0, 2.0], [1.5, 2.1]).statistic, rel=REL) and 0 < p2 < 1
    with pytest.raises(ValueError):
        paired_t([1.0, 2.0], [1.0])


def test_bias() -> None:
    assert bias(1.04, 0.98) == pytest.approx(100 * 0.06 / 0.98, rel=REL) and bias(0.98, 1.04) < 0
    for a, b in ((None, 1.0), (1.0, None), (1.0, 0.0), (1.0, -1.0), (math.inf, 1.0)):
        assert math.isnan(bias(a, b))
    check = np.array([1.0, 2.0, 4.0, 5.0])
    (g,) = calc("external", build(ump(1.1 * check, check), has_dl=False), CONFIG, [], "AU")["groups"]
    assert g["bias"] == pytest.approx(10.0, rel=1e-10)
    assert (g["mean_main"], g["mean_check"]) == (pytest.approx(3.3), 3.0)
    assert g["rma"]["b"] == pytest.approx(1 / 1.1, rel=1e-10) and g["rma"]["a"] == pytest.approx(0.0, abs=1e-12)


def direct_intervals(x: np.ndarray, y: np.ndarray, n_boot: int, seed: int) -> tuple[list[float], list[float]]:
    """The bootstrap realization by realization: the RMA line of the pairs drawn, then the percentiles."""
    slopes, intercepts = [], []
    for rows in draws(len(x), None, n_boot, seed):
        u, v = x[rows], y[rows]
        su, sv = u.std(), v.std()
        if su == 0:
            continue
        b = math.copysign(sv / su, float(np.mean((u - u.mean()) * (v - v.mean()))))
        slopes.append(b)
        intercepts.append(v.mean() - b * u.mean())
    return np.percentile(intercepts, [2.5, 97.5]).tolist(), np.percentile(slopes, [2.5, 97.5]).tolist()


@pytest.mark.parametrize(("n", "seed"), [(7, 1), (60, 2), (1500, 3)])
def test_intervals_against_the_bootstrap_counted_directly(n: int, seed: int) -> None:
    x, y = pairs(n, seed)
    if n == 60:
        x, y = x + 1e6, y + 1e6  # far from zero: the sums are taken about the means
    ci = rma_intervals(x, y, 400, seed)
    a, b = direct_intervals(x, y, 400, seed)
    assert ci.n_boot == 400 and ci.a is not None and ci.b is not None
    assert list(ci.b) == pytest.approx(b, rel=1e-9)
    assert list(ci.a) == pytest.approx(a, rel=1e-9, abs=1e-6 if n == 60 else 1e-10)
    assert ci.b[0] < ci.b[1] and ci.a[0] < ci.a[1]


def test_intervals_cover_the_slope_of_equal_laboratories() -> None:
    """Two laboratories without a bias (the truth: y = x): the 95 % interval of the slope covers 1 in about 95
    of 100 sets of pairs, that of the intercept covers 0; a laboratory 10 % higher is seen."""
    covered_b = covered_a = 0
    sets = 60
    for seed in range(sets):
        rng = np.random.default_rng(100 + seed)
        t = rng.uniform(1, 10, 400)
        x, y = t * (1 + 0.05 * rng.standard_normal(400)), t * (1 + 0.05 * rng.standard_normal(400))
        ci = rma_intervals(x, y, 300, 12345)
        assert ci.b is not None and ci.a is not None
        assert ci.b[0] < y.std() / x.std() < ci.b[1] and 0.01 < ci.b[1] - ci.b[0] < 0.08  # around its estimate
        covered_b += ci.b[0] < 1.0 < ci.b[1]
        covered_a += ci.a[0] < 0.0 < ci.a[1]
        high = rma_intervals(x, 1.1 * y, 300, 12345)
        assert high.b is not None and high.b[0] > 1.03  # does not cover 1
    assert 0.85 * sets <= covered_b <= sets and 0.85 * sets <= covered_a <= sets, (covered_b, covered_a)


def test_one_seed_one_result() -> None:
    x, y = pairs(800, 5)
    first, again, other = rma_intervals(x, y, 300, 7), rma_intervals(x, y, 300, 7), rma_intervals(x, y, 300, 8)
    assert first == again and first != other  # the same bits; another seed — other draws
    assert rma_intervals(x, y, 300, 7).b != rma_intervals(x, y, 200, 7).b
    # a group does not depend on the groups beside it: its generator starts from the seed anew
    cfg = {**CONFIG, "limits": {**CONFIG["limits"], "n_boot": 300, "seed": 7}}
    alone = calc("external", build(ump(x, y), has_dl=False), cfg, [], "AU")["groups"][0]
    calc_pairs.clear_intervals()  # bootstrapped anew, not found in the memory of the process
    beside = calc("external", build(ump(x, y) + ump(y, x, "C"), has_dl=False), cfg, [], "AU")["groups"][0]
    assert alone == beside and alone["rma"]["b_ci"] == list(first.b) and alone["rma"]["n_boot"] == 300
    assert alone["rma"]["seed"] == 7 and alone["notes"] == []


def test_realizations_are_bounded_and_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    assert realizations(100_000, 1000) == 1000 and realizations(100_001, 1000) == 999  # n·B ≤ 10⁸
    assert realizations(300_000, 1000) == 333 and realizations(1_000_000, 1000) == 100
    assert realizations(5_000_000, 5000) == 100 and realizations(20_000, 5000) == 5000 and realizations(3, 100) == 100
    monkeypatch.setattr(ext, "MAX_DRAWS", 100_000)  # the same rule on a small scale
    x, y = pairs(500, 9)
    ci = rma_intervals(x, y, 1000, 3)
    assert ci.n_boot == 200 and ci.b is not None and ci.b == rma_intervals(x, y, 200, 3).b
    cfg = {**CONFIG, "limits": {**CONFIG["limits"], "seed": 3}}
    (g,) = calc("external", build(ump(x, y), has_dl=False), cfg, [], "AU")["groups"]
    assert g["rma"]["n_boot"] == 200 and g["rma"]["b_ci"] == list(ci.b)  # the realizations made are shown
    assert g["notes"] == ["Реализаций бутстрапа — 200 вместо 1000: пар 500, предел n·B ≤ 10⁸."]


def test_degenerate_pairs_have_no_intervals_and_tick_is_called() -> None:
    assert rma_intervals([], [], 100, 1) == ext.RmaIntervals(None, None, 0)
    assert rma_intervals([1.0, 2.0], [1.0, 2.5], 100, 1) == ext.RmaIntervals(None, None, 0)  # fewer than three
    flat = rma_intervals([2.0, 2.0, 2.0, 2.0], [1.0, 2.0, 3.0, 4.0], 100, 1)  # no spread of x: no line
    assert (flat.a, flat.b, flat.n_boot) == (None, None, 100)
    few = rma_intervals([1.0, 2.0, 3.0], [1.1, 1.9, 3.2], 200, 1)  # a third of the draws has one value of x
    assert few.b is not None and 0.5 < few.b[0] <= few.b[1] < 2.0 and math.isfinite(few.a[0])
    seen: list[float] = []
    x, y = pairs(50, 1)
    rma_intervals(x, y, 100, 1, tick=seen.append)
    assert seen == [16 / 100, 32 / 100, 48 / 100, 64 / 100, 80 / 100, 96 / 100, 1.0]

    def cancel(share: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        rma_intervals(x, y, 100, 1, tick=cancel)  # a job cancels between the realizations
    with pytest.raises(ValueError):
        rma_intervals(x, y[:10], 100, 1)


def lab_rows() -> list[dict[str, Any]]:
    x, y = pairs(60, 4)
    rows = ump(x[:40], y[:40], "SGS", "ALS") + ump(x[40:], 1.3 * y[40:], "SGS", "Bureau")
    rows += [
        {"type": "UMP", "lab": "SGS", "parent_lab": "ALS", "au": None, "parent": 1.0},  # no value
        {"type": "UMP", "lab": "SGS", "parent_lab": "ALS", "au": 1.0, "parent": -0.2},  # negative
        {"type": "UMP", "lab": "SGS", "au": 1.0},  # no parent: no main laboratory either
        {"type": "UMP", "lab": None, "parent_lab": "ALS", "au": 2.0, "parent": 2.0},
        {"type": "DUP_P", "lab": "SGS", "parent_lab": "ALS", "au": 2.0, "parent": 2.0},  # not external control
    ]
    return rows


def test_section_external_groups_points_and_the_line_of_the_summary() -> None:
    recs = build(lab_rows(), has_dl=False)
    a = calc("external", recs, CONFIG, [], "AU")
    names = [(g["lab_main"], g["lab_check"]) for g in a["groups"]]
    assert names == [(None, "SGS"), ("ALS", None), ("ALS", "SGS"), ("Bureau", "SGS")]  # by names, an empty one first
    by = dict(zip(names, a["groups"], strict=True))
    lost, one, main, far = (by[k] for k in names)
    assert (lost["pairs"], lost["used"], lost["excluded"]["no_parent"]) == (1, 0, 1)
    assert lost["rma"] == {"a": None, "b": None, "a_ci": None, "b_ci": None, "n_boot": 0, "seed": 12345}
    assert (lost["bias"], lost["t"], lost["passed"], lost["notes"]) == (None, None, None, [])
    assert (one["used"], one["t"], one["df"], one["rma"]["b_ci"], len(one["notes"])) == (1, None, None, None, 2)
    assert (main["pairs"], main["used"]) == (42, 40)
    assert main["excluded"] == {"no_parent": 0, "missing": 1, "negative": 1, "zero": 0, "below": 0}
    assert main["df"] == 39 and 0 < main["p"] <= 1
    assert main["rma"]["b_ci"][0] < main["rma"]["b"] < main["rma"]["b_ci"][1]
    assert main["rma"]["n_boot"] == 1000 and main["notes"] == [] and main["passed"] is True  # errors of 5 % each
    assert far["bias"] < -15 and far["p"] < 0.05 and far["share_at_limit"] < 50 and far["passed"] is False
    assert "points" not in a and "ranked" not in a
    # the points of a group
    d = calc("external", recs, CONFIG, [], "AU", group={"lab_main": "ALS", "lab_check": "SGS"}, intervals=False)
    assert d["points"]["row"] == list(range(40)) and len(d["ranked"]["p"]) == 40 and d["ranked"]["p"][-1] == 100.0
    assert d["ranked"]["hard"] == sorted(d["points"]["hard"]) and "thinned" not in d
    assert d["groups"][2]["rma"] == {**main["rma"], "a_ci": None, "b_ci": None, "n_boot": 0}  # no bootstrap asked
    assert {k: v for k, v in d["groups"][2].items() if k != "rma"} == {k: v for k, v in main.items() if k != "rma"}
    gone = calc("external", recs, CONFIG, [], "AU", group={"lab_main": "ALS", "lab_check": "X"}, intervals=False)
    assert gone["points"] is None and gone["ranked"] is None
    thin = calc("external", recs, CONFIG, [], "AU", group={"lab_main": "ALS", "lab_check": "SGS"}, max_points=25)
    past = [row for row, hard in zip(d["points"]["row"], d["points"]["hard"], strict=True) if hard > 10]
    assert thin["thinned"] == {"shown": max(25, len(past)), "total": 40} and set(past) <= set(thin["points"]["row"])
    # every pair taken, group by group — the rows of the sheet of a report
    s = calc("external", recs, CONFIG, [], "AU", samples=True, intervals=False)["samples"]
    assert s["group"] == [1] + [2] * 40 + [3] * 20 and s["row"] == [63, *range(40), *range(40, 60)]
    assert s["hard"][1:41] == d["points"]["hard"] and s["ampd"][5] == 2 * s["hard"][5]
    # the line of the summary: no bootstrap there
    t0 = time.perf_counter()
    line = calc("summary", recs, CONFIG, [], "AU")["external"]
    assert line == {"groups": 4, "pairs": 64, "used": 61, "passed": 2, "failed": 1}
    assert time.perf_counter() - t0 < 0.5 and "external" in SECTIONS
    for answer in (a, d, thin):
        json_ready(answer)


@pytest.mark.parametrize("number", range(5))
def test_t848_external_by_a_filter_and_over_the_records_taken(number: int) -> None:
    recs, cfg, ref = hard_records()
    cfg = {**cfg, "limits": {**cfg["limits"], "n_boot": 200}}
    flt = FIVE[number]
    part = recs.take(row_mask(recs, parse_filter(flt), map_types(recs.text(TYPE_CODE), cfg)))
    by_filter, taken = context(recs, cfg, ref, "AU", flt), context(part, cfg, ref, "AU")
    a = run(by_filter, "external", samples=True)
    calc_pairs.clear_intervals()  # the second calculation bootstraps for itself
    b = run(taken, "external", samples=True)
    assert canonical(a) == canonical(b)  # byte for byte, the intervals of the bootstrap too
    json_ready(a)
    for g in a["groups"][:3]:
        key = {"lab_main": g["lab_main"], "lab_check": g["lab_check"]}
        params = {"group": key, "max_points": 10, "intervals": False}
        assert canonical(run(by_filter, "external", **params)) == canonical(run(taken, "external", **params))
    assert canonical(run(by_filter, "summary")) == canonical(run(taken, "summary"))
    assert sum(g["used"] for g in a["groups"]) > 20 or number == 1  # no UMP under the filter by the types
    if number in (3, 4):  # a filter that cuts across the groups changes them
        whole = {(g["lab_main"], g["lab_check"]): g for g in calc("external", recs, cfg, ref, "AU")["groups"]}
        assert any(whole[g["lab_main"], g["lab_check"]] != g for g in a["groups"])


def test_intervals_are_kept_by_the_pairs_and_the_bootstrap_beats(monkeypatch: pytest.MonkeyPatch) -> None:
    """A change of the filter that leaves a pair of laboratories as it was does not bootstrap it again; other
    pairs, another seed or number of realizations do. A job is cancelled inside the bootstrap (ревью 14.25, № 11)."""
    made: list[int] = []
    real = calc_pairs.rma_intervals

    def counted(x: Any, y: Any, n_boot: int, seed: int, **kw: Any) -> Any:
        made.append(len(x))
        return real(x, y, n_boot, seed, **kw)

    monkeypatch.setattr(calc_pairs, "rma_intervals", counted)
    recs = build(lab_rows(), has_dl=False)
    cfg = {**CONFIG, "limits": {**CONFIG["limits"], "n_boot": 200}}
    first = calc("external", recs, cfg, [], "AU")
    assert made == [40, 20]  # the two groups with a line; one pair and no pairs have none
    again = calc("external", recs, cfg, [], "AU")
    assert made == [40, 20] and canonical(again) == canonical(first)  # found, the same bytes
    # filters that do not touch the pairs of external control: nothing is bootstrapped
    whole = {(g["lab_main"], g["lab_check"]): g["rma"] for g in first["groups"]}
    for flt in ({"types": ["UMP", "ORIG"]}, {"crm": ["G-1"]}, {"labs": ["SGS"]}):
        groups = calc("external", recs, cfg, [], "AU", flt)["groups"]
        assert len(groups) >= 3 and all(whole[g["lab_main"], g["lab_check"]] == g["rma"] for g in groups), flt
    assert made == [40, 20]
    cut = calc("external", recs, cfg, [], "AU", {"where": [{"field": "v:AU", "op": "ge", "value": 1.0}]})
    assert len(made) == 4 and made[2] < 40 and made[3] < 20  # other pairs — bootstrapped
    assert cut["groups"][2]["rma"]["b_ci"] != first["groups"][2]["rma"]["b_ci"]
    for limits in ({"seed": 1}, {"n_boot": 300}):  # another seed, another number of realizations
        other = calc("external", recs, {**cfg, "limits": {**cfg["limits"], **limits}}, [], "AU")
        assert other["groups"][2]["rma"]["b_ci"] != first["groups"][2]["rma"]["b_ci"]
    assert made[4:] == [40, 20, 40, 20]
    monkeypatch.setattr(ext, "MAX_DRAWS", 4000)  # the bound of the realizations is a part of the key
    assert calc("external", recs, cfg, [], "AU")["groups"][2]["rma"]["n_boot"] == 100 and len(made) == 10
    monkeypatch.undo()
    # the least recently asked go first; what was asked again stays
    monkeypatch.setattr(calc_pairs, "INTERVALS", 2)
    calc_pairs.clear_intervals()
    x, y = pairs(30, 3)
    for scale in (1.0, 2.0, 1.0, 3.0, 1.0):
        calc_pairs._intervals(x * scale, y * scale, 50, 1, None)
    assert len(calc_pairs._kept) == 2
    monkeypatch.setattr(calc_pairs, "rma_intervals", counted)
    calc_pairs._intervals(x, y, 50, 1, None)  # still there
    calc_pairs._intervals(x * 2.0, y * 2.0, 50, 1, None)  # went out when the third came
    assert made[-1:] == [30] and len(made) == 11
    calc_pairs.clear_intervals()
    # the beat of a job: called between the realizations, a cancel raised there stops the calculation
    beats: list[int] = []
    run(context(recs, cfg, [], "AU", tick=lambda: beats.append(1)), "external")
    assert len(beats) == 2 * (200 // 16 + 1)  # every 16 realizations and at the end, a group each
    calc_pairs.clear_intervals()

    def cancel() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(context(recs, cfg, [], "AU", tick=cancel), "external")
    assert len(calc_pairs._kept) == 0  # nothing half-made is kept
    run(context(recs, cfg, [], "AU", tick=cancel), "summary")  # the line of the summary does not bootstrap


def test_bootstrap_of_twenty_thousand_pairs_in_time() -> None:
    """The criterion of 14.7 is 100 thousand pairs × 1000 realizations within 1.2 s (measured by `bench`);
    here — 20 thousand pairs × 1000 within 0.6 s, the best of two."""
    x, y = pairs(20_000, 31)
    rma_intervals(x[:100], y[:100], 10, 1)
    best = math.inf
    for _ in range(2):
        t0 = time.perf_counter()
        ci = rma_intervals(x, y, 1000, 12345)
        best = min(best, time.perf_counter() - t0)
    assert ci.n_boot == 1000 and ci.b is not None and ci.b[0] < 1.0 < ci.b[1]
    assert best <= 0.6, f"{best:.3f} s"
