"""The calendar and the NPV of the pit-by-pit report (подшаг 21.8; ТЗ 8.11.4; ADR 0028 decisions 18, 19).

**Т8.11-8**: CF of the two shells 10 and 15 million $, ore 1 and 2 million t, Q_р = 1 million t/year, Q_гм not set,
r = 10 %: NPV = 21,953707 million $ to 1e−6 million $ (written out: 10·(1 − 1,1⁻¹)/ln 1,1 +
15·(1,1⁻¹ − 1,1⁻³)/(2·ln 1,1)).
Also: r = 0 (the limit, Σ CF), τ = 0 (CF·(1 + r)^(−t)), Q_гм (the limit by the rock mass), the NPV of the best case is a
prefix, the worst case on the benches, the calendar without Q_р — 422 `bad_param`; all against the formula of the ТЗ
word for word in plain Python."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.mine import npv as npv_mod
from bf_core.mine import report as rep_mod
from bf_core.mine.spec_parts import Calendar

MLN = 1e6
Part = tuple[float, float, float]


def cal(q_ore: float | None = 1e6, q_mass: float | None = None, rate: float = 0.10) -> Calendar:
    return Calendar(q_ore, q_mass, rate)


def ref_npv(parts: list[Part], q_ore: float, q_mass: float | None, r: float) -> float:
    """The ТЗ word for word: (O_i, W_i, CF_i) in order; τ, t, the sum; the special cases r = 0 and τ = 0."""
    total, t = 0.0, 0.0
    for o, w, cf in parts:
        tau = o / q_ore if q_mass is None else max(o / q_ore, (o + w) / q_mass)
        if tau == 0.0:
            total += cf * (1.0 + r) ** (-t)
        elif r == 0.0:
            total += cf
        else:
            total += cf * ((1.0 + r) ** (-t) - (1.0 + r) ** (-(t + tau))) / (tau * math.log(1.0 + r))
        t += tau
    return total


def arrays(parts: list[Part]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (np.array([p[0] for p in parts]), np.array([p[1] for p in parts]), np.array([p[2] for p in parts]))


def synthetic(parts: list[Part], benches: dict[int, list[Part]]) -> rep_mod.Report:
    """A report with only the columns the NPV reads: the increments `parts` and the benches of every n (the parts of an
    n are listed from the top level down)."""
    n = len(parts)
    data: dict[str, Any] = {c: np.zeros(n) for c in rep_mod.column_names([])}
    data["k"] = np.arange(1, n + 1, dtype=np.int32)
    data["blocks"] = np.ones(n, dtype=np.int64)
    data["cum_blocks"] = np.arange(1, n + 1, dtype=np.int64)
    data["ore_t"], data["waste_t"], data["cf"] = arrays(parts)
    rows = [(k, len(benches[k]) - 1 - i, *b) for k in sorted(benches) for i, b in enumerate(benches[k])]
    bench = {"n": np.array([r[0] for r in rows], dtype=np.int32),
             "level": np.array([r[1] for r in rows], dtype=np.int32), "ore_t": np.array([r[2] for r in rows]),
             "waste_t": np.array([r[3] for r in rows]), "cf": np.array([r[4] for r in rows])}  # fmt: skip
    return rep_mod.Report.from_columns([], data, bench)


# --- Т8.11-8 --------------------------------------------------------------------------------------


def test_t8_11_8_two_shells_give_21_953707_million() -> None:
    parts = [(1.0 * MLN, 0.0, 10.0 * MLN), (2.0 * MLN, 0.0, 15.0 * MLN)]
    o, w, cf = arrays(parts)
    c = cal(1e6, None, 0.10)
    got = npv_mod.npv_sequence(o, w, cf, c)
    assert abs(got - 21.953707 * MLN) <= 1e-6 * MLN  # 1e−6 million $ = 1 $
    assert abs(got - 21_953_706.569_504_84) < 1e-4  # the closed form
    assert abs(got - ref_npv(parts, 1e6, None, 0.10)) < 1e-6
    assert npv_mod.durations(o, w, c).tolist() == [1.0, 2.0]  # τ_1 = 1, τ_2 = 2 years (t_2 = 1)
    # through the stored tables: the best case of n = 1, 2 and the worst case on the benches that are the same parts
    rep = synthetic(parts, {1: parts[:1], 2: parts})
    res = npv_mod.npv(rep, c)
    assert abs(res.best[1] - 21.953707 * MLN) <= 1.0 and abs(res.worst[1] - 21.953707 * MLN) <= 1.0
    assert abs(res.best[0] - 10 * MLN * (1 - 1 / 1.1) / math.log(1.1)) < 1e-6  # n = 1: the first part alone
    assert res.best[0] == res.worst[0]


def test_the_best_case_of_n_is_the_prefix_of_the_sequence() -> None:
    rng = np.random.default_rng(3)
    o, w, cf = rng.uniform(0, 5e5, 12), rng.uniform(0, 2e6, 12), rng.normal(2e6, 3e6, 12)
    c = cal(4e5, 1.2e6, 0.08)
    best = npv_mod.npv_best(o, w, cf, c)
    for n in range(1, 13):
        assert best[n - 1] == npv_mod.npv_sequence(o[:n], w[:n], cf[:n], c)
        parts = list(zip(o[:n], w[:n], cf[:n], strict=True))
        assert abs(best[n - 1] - ref_npv(parts, 4e5, 1.2e6, 0.08)) <= 1e-9 * max(1.0, abs(best[n - 1])) + 1e-6


# --- the special cases of the ТЗ ------------------------------------------------------------------


def test_zero_rate_is_the_sum_of_cf() -> None:
    o, w, cf = arrays([(1e6, 2e6, 4e6), (3e6, 1e6, -1e6), (0.5e6, 0.0, 2.5e6)])
    assert npv_mod.npv_sequence(o, w, cf, cal(1e6, None, 0.0)) == math.fsum(cf)
    assert npv_mod.npv_sequence(o, w, cf, cal(1e6, 5e5, 0.0)) == math.fsum(cf)  # whatever the limit by the mass
    # the limit: a tiny r is the same number up to the rounding (the term is written without the cancellation)
    assert abs(npv_mod.npv_sequence(o, w, cf, cal(1e6, None, 1e-12)) - math.fsum(cf)) < 1e-3


def test_a_part_of_zero_duration_is_discounted_at_its_start() -> None:
    # the second part has no ore and no limit by the mass: τ = 0, t = 1 year: CF·(1 + r)^(−1)
    parts = [(1e6, 0.0, 10e6), (0.0, 5e6, -3e6)]
    o, w, cf = arrays(parts)
    got = npv_mod.npv_sequence(o, w, cf, cal(1e6, None, 0.10))
    assert got == pytest.approx(10e6 * (1 - 1 / 1.1) / math.log(1.1) - 3e6 / 1.1, rel=1e-12)
    assert got == pytest.approx(ref_npv(parts, 1e6, None, 0.10), rel=1e-12)
    assert npv_mod.npv_sequence(o, w, cf, cal(1e6, None, 0.0)) == 7e6  # at r = 0 a part is just its CF
    # a tiny duration is continuous with zero
    o2 = np.array([1e6, 1e-9])
    assert abs(npv_mod.npv_sequence(o2, w, cf, cal(1e6, None, 0.10)) - got) < 1e-3


def test_the_limit_by_the_rock_mass_makes_a_part_longer() -> None:
    # O = 1e6, W = 3e6: the processing takes 1 year, the rock mass (4e6) / 2e6 = 2 years: τ = 2
    parts = [(1e6, 3e6, 8e6), (1e6, 0.0, 4e6)]
    o, w, cf = arrays(parts)
    c = cal(1e6, 2e6, 0.08)
    assert npv_mod.durations(o, w, c).tolist() == [2.0, 1.0]
    assert npv_mod.npv_sequence(o, w, cf, c) == pytest.approx(ref_npv(parts, 1e6, 2e6, 0.08), rel=1e-12)
    assert npv_mod.durations(o, w, cal(1e6, 1e12, 0.08)).tolist() == [1.0, 1.0]  # a big Q_гм is no limit
    assert npv_mod.durations(o, w, cal(1e6, None, 0.08)).tolist() == [1.0, 1.0]  # none is no limit


def test_random_sequences_against_the_formula_of_the_tz() -> None:
    rng = np.random.default_rng(11)
    for seed in range(20):
        n = int(rng.integers(1, 30))
        o, w, cf = rng.uniform(0, 3e6, n), rng.uniform(0, 6e6, n), rng.normal(0.0, 5e6, n)
        o[rng.random(n) < 0.2] = 0.0
        r = float(rng.choice([0.0, 0.03, 0.08, 0.25, 0.5]))
        qm = None if seed % 2 else float(rng.uniform(1e6, 5e6))
        got = npv_mod.npv_sequence(o, w, cf, cal(1.5e6, qm, r))
        want = ref_npv(list(zip(o, w, cf, strict=True)), 1.5e6, qm, r)
        assert got == pytest.approx(want, rel=1e-9, abs=1e-3), seed


def test_a_long_life_and_a_high_rate_stay_finite() -> None:
    # the first part lasts 10¹² years: its PV is CF/(τ·ln(1 + r)); the second starts at t = 10¹² and is worth 0
    o, w, cf = arrays([(1e12, 0.0, 1e9), (1.0, 0.0, 1e9)])
    got = npv_mod.npv_sequence(o, w, cf, cal(1.0, None, 0.5))
    assert math.isfinite(got) and got == pytest.approx(1e9 / (1e12 * math.log(1.5)), rel=1e-9)


# --- the worst case on the benches ----------------------------------------------------------------


def test_the_worst_case_runs_the_benches_from_the_top() -> None:
    # n = 2: a waste bench on top (a cost, τ = 0), then two ore benches; Q_гм not set
    benches = {1: [(1e6, 0.0, 3e6)], 2: [(0.0, 5e6, -4e6), (1e6, 1e6, 6e6), (1e6, 0.0, 9e6)]}
    parts = [(1e6, 0.0, 3e6), (1e6, 1e6, 8e6)]
    rep = synthetic(parts, benches)
    got = npv_mod.npv(rep, cal(1e6, None, 0.10))
    assert got.worst[1] == pytest.approx(ref_npv(benches[2], 1e6, None, 0.10), rel=1e-12)
    assert got.worst[0] == pytest.approx(ref_npv(benches[1], 1e6, None, 0.10), rel=1e-12)
    assert got.best[1] == pytest.approx(ref_npv(parts, 1e6, None, 0.10), rel=1e-12)
    assert got.best[1] > got.worst[1] > 0.0  # the stripping first costs: the same total CF, later
    zero = npv_mod.npv(rep, cal(1e6, None, 0.0))  # at r = 0 both are the sum of the CF of C_2
    assert zero.worst[1] == math.fsum([p[2] for p in benches[2]]) and zero.best[1] == 11e6


def test_an_n_without_benches_has_no_cash_flow() -> None:
    rep = synthetic([(1e6, 0.0, 3e6), (0.0, 0.0, 0.0)], {1: [(1e6, 0.0, 3e6)]})  # C_2 = C_1: no bench of n = 2
    got = npv_mod.npv(rep, cal())
    assert got.worst[1] == 0.0 and got.best[1] == got.best[0]


# --- the calendar without what the NPV needs ------------------------------------------------------


@pytest.mark.parametrize(
    ("c", "param"),
    [(Calendar(None, None, 0.08), "calendar.q_ore"), (Calendar(0.0, None, 0.08), "calendar.q_ore"),
     (Calendar(float("nan"), None, 0.08), "calendar.q_ore"), (Calendar(1e6, 0.0, 0.08), "calendar.q_mass"),
     (Calendar(1e6, -5.0, 0.08), "calendar.q_mass"), (Calendar(1e6, None, -0.01), "calendar.rate"),
     (Calendar(1e6, None, float("inf")), "calendar.rate")],
)  # fmt: skip
def test_a_calendar_that_gives_no_npv_is_422_bad_param(c: Calendar, param: str) -> None:
    rep = synthetic([(1e6, 0.0, 3e6)], {1: [(1e6, 0.0, 3e6)]})
    calls: list[Callable[[], Any]] = [lambda: npv_mod.npv(rep, c), lambda: npv_mod.check_calendar(c),
                                      lambda: npv_mod.durations(np.ones(1), np.ones(1), c)]  # fmt: skip
    for call in calls:
        with pytest.raises(ApiError) as e:
            call()
        err = e.value
        assert err.status == 422 and err.code == "bad_param" and err.extra == {"param": param}
        assert err.message and err.hint and any("а" <= ch <= "я" for ch in err.message.lower())  # Russian + a hint


def test_the_calendar_without_q_ore_says_how_to_fix_it() -> None:
    with pytest.raises(ApiError) as e:
        npv_mod.check_calendar(Calendar(None, 1e6, 0.08))
    assert "Q_р" in e.value.message and "Укажите" in e.value.hint
