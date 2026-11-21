"""Thresholds and standards of QAQC (ТЗ 8.4.2, Т8.4-1; ADR 0020 §3, §4): the ranges of the thresholds, the
comparisons with a tolerance, the order of a series, z and the status, «две подряд», the run on one side of μ,
a swapped CRM, the bias and the moving average.

Tolerance (10.5 п. 1): the statuses, the rules, the counters, the order of a series and the swaps — exact;
z, the mean, σ, the bias and the moving average — 1e−12 relative against ADR 0020 §4 counted row by row in
plain Python (`direct`)."""

from __future__ import annotations

import math
import subprocess
import sys
import time
import warnings
from typing import Any

import numpy as np
import pytest

from bf_core.qaqc import limits as lm
from bf_core.qaqc.limits import FAIL, NORM, NOT_ASSESSED, WARN, Limits, QaqcError, gt, le
from bf_core.qaqc.standards import (
    NO_DATE,
    NO_REFERENCE,
    OK,
    RULE_PAIR,
    RULE_RUN,
    RULE_SWAP,
    UNIT_MISMATCH,
    Standards,
    date_key,
    group_index,
    standards,
)

NAN = math.nan
REL = 1e-12
MU, SD = 1.00, 0.05  # Т8.4-1


def days(values: list[int | None]) -> np.ndarray:
    """Dates of a series: a day number from 2024-01-01, None — no date."""
    base = np.datetime64("2024-01-01", "ms")
    return np.array([np.datetime64("NaT", "ms") if d is None else base + np.timedelta64(d, "D") for d in values])


def numbers(values: list[float | None]) -> np.ndarray:
    return np.array([NAN if v is None else v for v in values], dtype=np.float64)


def calc(x: Any, crm: Any = None, lab: Any = None, date: Any = None, seq: Any = None, mu: Any = (MU,),
         sd: Any = (SD,), limits: Limits | None = None, mismatch: Any = None) -> Standards:  # fmt: skip
    """The kernel on a series; what is not given: one CRM, one laboratory, no dates, no SEQ (the order of the file)."""
    n = len(x)
    return standards(
        np.asarray(x, dtype=np.float64),
        np.zeros(n, dtype=np.int64) if crm is None else np.asarray(crm),
        np.zeros(n, dtype=np.int64) if lab is None else np.asarray(lab),
        days([None] * n) if date is None else days(list(date)),
        numbers([None] * n) if seq is None else numbers(list(seq)),
        np.asarray(mu, dtype=np.float64),
        np.asarray(sd, dtype=np.float64),
        limits,
        mismatch=mismatch,
    )


def soft_le(a: float, b: float) -> bool:
    return a <= b + 1e-9 * max(1.0, abs(b))


def direct(x: Any, crm: Any, lab: Any, date: Any, seq: Any, mu: Any, sd: Any, k2: float = 2.0, k3: float = 3.0,
           run: int = 9, mismatch: tuple[int, ...] = ()) -> dict[str, Any]:  # fmt: skip
    """ADR 0020 §4 «Стандарты» row by row: the rows, the order of the series and the groups."""
    n = len(x)
    rows: dict[str, list[Any]] = {
        "z": [NAN] * n, "status": [NOT_ASSESSED] * n, "rule": [0] * n, "ma": [NAN] * n, "run": [0] * n,
        "swap": [[-1, -1, -1] for _ in range(n)],
    }  # fmt: skip
    members: dict[tuple[int, int], list[int]] = {}
    for i in range(n):
        members.setdefault((int(crm[i]), int(lab[i])), []).append(i)

    def usable(j: int) -> bool:
        return j not in mismatch and math.isfinite(mu[j]) and math.isfinite(sd[j]) and sd[j] > 0

    order: list[int] = []
    groups: list[dict[str, Any]] = []
    for c, lb in sorted(members):
        series = [i for i in members[c, lb] if math.isfinite(x[i])]
        series.sort(key=lambda i: (date[i] is None, date[i] or 0, seq[i] is None, seq[i] or 0.0, i))
        order += series
        ok = usable(c)
        length, last_side, last_band = 0, 0, False
        values: list[float] = []
        for pos, i in enumerate(series):
            values.append(float(x[i]))
            if pos >= 9:
                rows["ma"][i] = math.fsum(values[pos - 9 :]) / 10
            if not ok:
                continue
            z = (x[i] - mu[c]) / sd[c]
            status = NORM if soft_le(abs(z), k2) else WARN if soft_le(abs(z), k3) else FAIL
            band, beyond = status == WARN, status == FAIL
            side = 1 if not soft_le(z, 0.0) else -1 if not soft_le(-z, 0.0) else 0
            rule = 0
            if band and last_band and side == last_side:
                status, rule = FAIL, RULE_PAIR
            length = 0 if side == 0 else length + 1 if side == last_side else 1
            if length >= run:
                status, rule = max(status, WARN), rule | RULE_RUN
            if beyond:
                near = sorted(
                    (abs((x[i] - mu[j]) / sd[j]), j)
                    for j in range(len(mu))
                    if j != c and usable(j) and soft_le(abs((x[i] - mu[j]) / sd[j]), k2)
                )[:3]
                if near:
                    rule |= RULE_SWAP
                    rows["swap"][i] = [j for _, j in near] + [-1] * (3 - len(near))
            rows["z"][i], rows["status"][i], rows["rule"][i], rows["run"][i] = z, status, rule, length
            last_band, last_side = band, side
        m = len(values)
        mean = math.fsum(values) / m if m else NAN
        std = math.sqrt(math.fsum((v - mean) ** 2 for v in values) / m) if m else NAN
        st = [rows["status"][i] for i in series]
        rl = [rows["rule"][i] for i in series]
        groups.append({
            "crm": c, "lab": lb, "n": m, "missing": len(members[c, lb]) - m, "mean": mean, "std": std,
            "state": OK if ok else UNIT_MISMATCH if c in mismatch else NO_REFERENCE,
            "bias": 100.0 * (mean - mu[c]) / mu[c] if ok and m else NAN,
            "norm": st.count(NORM), "warn": st.count(WARN), "fail": st.count(FAIL),
            "pair_fails": sum(1 for r in rl if r & RULE_PAIR), "run_flags": sum(1 for r in rl if r & RULE_RUN),
            "swaps": sum(1 for r in rl if r & RULE_SWAP),
        })  # fmt: skip
    return {"rows": rows, "order": order, "groups": groups}


def close(got: Any, want: Any, scale: float = 0.0) -> None:
    """Equal within 1e−12 relative (of `scale` when the number itself is less: a bias is a share of 100 %); NaN
    stands where NaN is expected."""
    got, want = np.asarray(got, dtype=np.float64), np.asarray(want, dtype=np.float64)
    assert got.shape == want.shape
    assert np.array_equal(np.isnan(got), np.isnan(want))
    assert np.allclose(got, want, rtol=REL, atol=REL * scale, equal_nan=True)


def refused(param: str, call: Any) -> QaqcError:
    with pytest.raises(QaqcError) as e:
        call()
    assert e.value.code == "bad_param" and e.value.param == param and e.value.message and e.value.hint
    assert e.value.as_dict() == {"code": "bad_param", "message": e.value.message, "hint": e.value.hint}
    return e.value


# --- thresholds ---------------------------------------------------------------------------------------------


def test_defaults_and_ranges_of_the_spec() -> None:
    d = Limits()
    assert d.as_dict() == {
        "sigma_warn": 2.0, "sigma_fail": 3.0, "run": 9, "k_blank": 5.0, "k_dup": 5.0, "th_group": 11,
        "hard_p": 10.0, "hard_c": 20.0, "hard_f": 30.0, "hard_ump": 10.0, "hard_share": 90.0, "n_boot": 1000,
        "seed": 12345,
    }  # fmt: skip
    assert Limits.parse(None) == d and Limits.parse({}) == d and Limits.parse(d.as_dict()) == d
    assert (lm.TOL, lm.BIAS_OK, lm.BIAS_ATTENTION, lm.MA_WINDOW, lm.TH_MIN_PAIRS) == (1e-9, 5.0, 10.0, 10, 50)
    table = lm.ranges()
    assert list(table) == list(d.as_dict())
    assert table["run"] == {"min": 5, "max": 20, "default": 9, "integer": True, "odd": False,
                            "title": lm.RANGES["run"].title}  # fmt: skip
    assert table["th_group"]["odd"] and all(t["min"] <= t["default"] <= t["max"] for t in table.values())
    # the borders belong to the ranges
    edge = Limits(sigma_warn=1, sigma_fail=5, run=5, k_blank=1, k_dup=0, th_group=51, hard_share=50, n_boot=5000)
    assert (edge.sigma_warn, edge.sigma_fail, edge.run, edge.k_dup, edge.th_group) == (1.0, 5.0, 5, 0.0, 51)
    assert Limits(run=20, k_blank=50, k_dup=50, th_group=5, hard_p=1, hard_f=100, n_boot=100, seed=0).run == 20
    whole = Limits.parse({"run": 12.0, "sigma_warn": np.float64(2.5), "n_boot": np.int64(200)})
    assert (whole.run, whole.sigma_warn, whole.n_boot) == (12, 2.5, 200) and isinstance(whole.run, int)


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("sigma_warn", 0.99), ("sigma_warn", 5.01), ("sigma_fail", 0.5), ("sigma_fail", 6), ("run", 4), ("run", 21),
        ("run", 9.5), ("k_blank", 0.9), ("k_blank", 51), ("k_dup", -0.1), ("k_dup", 50.5), ("th_group", 4),
        ("th_group", 53), ("th_group", 12), ("hard_p", 0), ("hard_c", 101), ("hard_f", 0.5), ("hard_ump", 200),
        ("hard_share", 49), ("hard_share", 101), ("n_boot", 99), ("n_boot", 5001), ("n_boot", 100.5),
        ("seed", -1), ("seed", 2**32), ("run", True), ("sigma_warn", "2"), ("k_blank", NAN), ("k_blank", math.inf),
        ("sigma_fail", None),
    ],
)  # fmt: skip
def test_threshold_out_of_range_is_bad_param(param: str, value: Any) -> None:
    e = refused(param, lambda: Limits.parse({param: value}))
    assert "от" in e.message and "до" in e.message and "умолчанию" in e.hint
    refused(param, lambda: Limits(**{param: value}))


def test_warn_below_fail_and_unknown_keys() -> None:
    refused("sigma_warn", lambda: Limits(sigma_warn=3, sigma_fail=3))
    refused("sigma_warn", lambda: Limits.parse({"sigma_warn": 3.5, "sigma_fail": 2.5}))
    refused("sigma_x", lambda: Limits.parse({"sigma_x": 2}))
    refused("limits", lambda: Limits.parse([2, 3]))  # type: ignore[arg-type]
    assert Limits(sigma_warn=2.9, sigma_fail=3).sigma_warn == 2.9


def test_comparisons_with_tolerance() -> None:
    z = (1.10 - 1.00) / 0.05
    assert z > 2.0  # float64: 2.0000000000000018 — a plain comparison puts the border on the hard side
    assert bool(le(z, 2.0)) and not bool(gt(z, 2.0))
    assert bool(gt(2.0 + 3e-9, 2.0)) and not bool(le(2.0 + 3e-9, 2.0))
    assert bool(le(0.05, 5 * 0.01)) and not bool(gt(0.05, 5 * 0.01))  # k_б·DL of Т8.4-2
    # relative from 1 up, absolute below
    assert bool(le(1e12 + 500.0, 1e12)) and bool(gt(1e12 + 2000.0, 1e12))
    assert bool(le(1e-12 + 5e-10, 1e-12)) and bool(gt(1e-12 + 2e-9, 1e-12))
    a = np.array([1.0, 2.0, 2.0000000001, 2.1, NAN, -math.inf, math.inf])
    assert le(a, 2.0).tolist() == [True, True, True, False, False, True, False]
    assert gt(a, 2.0).tolist() == [False, False, False, True, False, False, True]
    assert not bool(le(1.0, NAN)) and not bool(gt(1.0, NAN))  # a missing value is neither «норма» nor «отказ»
    assert le(np.array([1.0, 3.0]), np.array([2.0, 2.0])).tolist() == [True, False]


# --- Т8.4-1 and the rules -----------------------------------------------------------------------------------


def test_t841_series() -> None:
    x = [1.11, 1.12, 0.84, 1.16, 0.95] + [1.01] * 9
    r = calc(x)
    assert r.status.tolist() == [WARN, FAIL, FAIL, FAIL, NORM] + [NORM] * 8 + [WARN]
    assert r.rule.tolist() == [0, RULE_PAIR, 0, 0, 0] + [0] * 8 + [RULE_RUN]  # «смещение» on the 9th 1,01
    assert r.run.tolist() == [1, 2, 1, 1, 1, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    close(r.z, [(v - MU) / SD for v in x])
    assert r.swap.tolist() == [[-1, -1, -1]] * 14 and r.order.tolist() == list(range(14))
    assert (r.crm.tolist(), r.lab.tolist(), r.state.tolist(), r.start.tolist()) == ([0], [0], [OK], [0, 14])
    assert (r.n[0], r.missing[0], r.norm[0], r.warn[0], r.fail[0]) == (14, 0, 9, 2, 3)
    assert (r.pair_fails[0], r.run_flags[0], r.swaps[0]) == (1, 1, 0)
    assert (r.mu[0], r.sd[0]) == (MU, SD)
    close(r.mean, [math.fsum(x) / 14])
    close(r.bias, [100.0 * (math.fsum(x) / 14 - MU) / MU])
    assert r.bias_class.tolist() == [NORM]
    assert r.rows(0).tolist() == list(range(14))


def test_values_on_the_borders_fall_on_the_soft_side() -> None:
    r = calc([1.10, 0.90, 1.15, 0.85, 1.1000001, 0.8499999])
    assert r.status.tolist() == [NORM, NORM, WARN, WARN, WARN, FAIL]
    assert r.rule.tolist() == [0] * 6  # 1,15 and 0,85 are on the opposite sides of μ: no pair
    r = calc([1.15, 1.15])  # two on the border 3σ in a row: the second fails by the rule of the pair
    assert r.status.tolist() == [WARN, FAIL] and r.rule.tolist() == [0, RULE_PAIR]
    r = calc([1.10, 1.10, 1.10])  # 2σ exactly is «норма»: no pair at all
    assert r.status.tolist() == [NORM] * 3 and r.rule.tolist() == [0] * 3


def test_third_in_a_row_fails_too() -> None:
    r = calc([1.11, 1.12, 1.13, 1.01, 1.12, 0.89, 0.88])
    assert r.status.tolist() == [WARN, FAIL, FAIL, NORM, WARN, WARN, FAIL]
    assert r.rule.tolist() == [0, RULE_PAIR, RULE_PAIR, 0, 0, 0, RULE_PAIR]
    r = calc([1.16, 1.12, 0.84, 0.88])  # the one before is beyond 3σ: no pair
    assert r.status.tolist() == [FAIL, WARN, FAIL, WARN] and not r.rule.any()


def test_zero_z_breaks_the_run() -> None:
    x = [1.01] * 5 + [1.00] + [1.01] * 8
    r = calc(x)
    assert r.run.tolist() == [1, 2, 3, 4, 5, 0, 1, 2, 3, 4, 5, 6, 7, 8]
    assert r.status.tolist() == [NORM] * 14 and not r.rule.any() and r.run_flags[0] == 0
    r = calc(x + [1.01])
    assert r.run.tolist()[-1] == 9 and r.rule.tolist()[-1] == RULE_RUN and r.status.tolist()[-1] == WARN
    # on μ within the tolerance of a comparison is on neither side; two of them in a row stay 0
    r = calc([1.01] * 4 + [1.00 + 1e-12, 1.00] + [0.99] * 4 + [1.00 - 1e-12] + [0.99] * 5)
    assert r.run.tolist() == [1, 2, 3, 4, 0, 0, 1, 2, 3, 4, 0, 1, 2, 3, 4, 5] and not r.rule.any()
    r = calc([1.01] * 8 + [0.99] * 8 + [1.01] * 8)  # the other side starts the count anew
    assert r.run.max() == 8 and not r.rule.any()


def test_tenth_and_eleventh_of_a_run_are_flagged() -> None:
    r = calc([0.99] * 11 + [1.01, 0.99])
    assert r.run.tolist() == list(range(1, 12)) + [1, 1]
    assert r.rule.tolist() == [0] * 8 + [RULE_RUN] * 3 + [0, 0]
    assert r.status.tolist() == [NORM] * 8 + [WARN] * 3 + [NORM, NORM]
    assert (r.run_flags[0], r.warn[0], r.fail[0], r.norm[0]) == (3, 3, 0, 10)
    # the status is never lowered: beyond 3σ and in the pair it stays «отказ», the flag is set beside
    r = calc([1.01] * 8 + [1.16, 1.11, 1.12, 1.01])
    assert r.status.tolist() == [NORM] * 8 + [FAIL, WARN, FAIL, WARN]
    assert r.rule.tolist() == [0] * 8 + [RULE_RUN, RULE_RUN, RULE_RUN | RULE_PAIR, RULE_RUN]
    r = calc([1.01] * 6, limits=Limits(run=5))
    assert r.rule.tolist() == [0, 0, 0, 0, RULE_RUN, RULE_RUN]


def test_sample_without_value_is_counted_and_left_out_of_the_series() -> None:
    r = calc([1.11, NAN, 1.12, math.inf, NAN])
    assert r.status.tolist() == [WARN, NOT_ASSESSED, FAIL, NOT_ASSESSED, NOT_ASSESSED]  # the pair goes over the gap
    assert r.rule.tolist() == [0, 0, RULE_PAIR, 0, 0] and r.order.tolist() == [0, 2]
    assert (r.n[0], r.missing[0], r.norm[0], r.warn[0], r.fail[0]) == (2, 3, 0, 1, 1)
    assert np.isnan(r.z[[1, 3, 4]]).all() and np.isnan(r.ma).all() and r.run.tolist() == [1, 0, 2, 0, 0]
    close(r.mean, [(1.11 + 1.12) / 2])  # what is missing is not a zero
    r = calc([NAN, NAN])
    assert (r.n[0], r.missing[0], r.state[0], r.bias_class[0]) == (0, 2, OK, NOT_ASSESSED)
    assert np.isnan([r.mean[0], r.std[0], r.bias[0]]).all() and r.order.size == 0 and r.start.tolist() == [0, 0]


def test_order_is_date_seq_row_and_what_is_empty_goes_last() -> None:
    date = [2, 1, 1, None, 1, None, 1, None]
    seq = [1, 2, 1, 1, None, None, 1, 1]
    r = calc([1.0] * 8, date=date, seq=seq)
    assert r.order.tolist() == [2, 6, 1, 4, 0, 3, 7, 5]
    # no dates and no SEQ — the order of the file; dates turn the series round
    assert calc([1.11, 1.12]).status.tolist() == [WARN, FAIL]
    assert calc([1.11, 1.12], date=[2, 1]).status.tolist() == [FAIL, WARN]
    assert calc([1.11, 1.12], seq=[7, 3]).status.tolist() == [FAIL, WARN]
    assert calc([1.11, 1.12], date=[1, 1], seq=[7, 3]).status.tolist() == [FAIL, WARN]
    assert calc([1.11, 1.12], date=[1, 2], seq=[7, 3]).status.tolist() == [WARN, FAIL]  # the date goes first
    assert calc([1.11, 1.12], date=[None, 5]).status.tolist() == [FAIL, WARN]  # no date — after the dated
    # the dates of `qaqc.records` are float64 milliseconds, NaN — none; int64 with the code of NaT is taken too
    zero = np.zeros(8, np.int64)
    as_int = days(date).astype(np.int64)
    as_float = np.where(as_int == NO_DATE, NAN, as_int.astype(np.float64))
    assert np.isnan(as_float).sum() == 3 and date_key(as_float).tolist() == as_int.tolist()
    for form in (as_int, as_float):
        assert standards(np.ones(8), zero, zero, form, numbers(seq), [MU], [SD]).order.tolist() == r.order.tolist()


def test_groups_are_crm_by_laboratory() -> None:
    #       A/lab0  A/lab1  A/lab0  B/lab0  A/lab1  B/lab0
    x = [1.11, 1.12, 1.12, 2.22, 0.89, 2.24]
    r = calc(x, crm=[0, 0, 0, 1, 0, 1], lab=[0, 1, 0, 0, 1, 0], mu=[1.0, 2.0], sd=[0.05, 0.10])
    assert (r.crm.tolist(), r.lab.tolist()) == ([0, 0, 1], [0, 1, 0])
    assert r.order.tolist() == [0, 2, 1, 4, 3, 5] and r.start.tolist() == [0, 2, 4, 6]
    assert r.status.tolist() == [WARN, WARN, FAIL, WARN, WARN, FAIL]  # a pair never crosses the groups
    assert r.rule.tolist() == [0, 0, RULE_PAIR, 0, 0, RULE_PAIR]
    assert (r.n.tolist(), r.fail.tolist(), r.warn.tolist()) == ([2, 2, 2], [1, 0, 1], [1, 2, 1])
    assert (r.mu.tolist(), r.sd.tolist()) == ([1.0, 1.0, 2.0], [0.05, 0.05, 0.10])
    assert [r.rows(g).tolist() for g in range(3)] == [[0, 2], [1, 4], [3, 5]]
    close(r.mean, [(1.11 + 1.12) / 2, (1.12 + 0.89) / 2, (2.22 + 2.24) / 2])


def test_group_without_reference_is_not_assessed() -> None:
    x = [1.5, 1.6, 1.7, 5.0, 5.0, 7.0, 9.0]
    r = calc(x, crm=[0, 0, 0, 1, 2, 3, 4], mu=[NAN, 5.0, 5.0, 7.0, 9.0], sd=[NAN, NAN, 0.0, -1.0, 0.1],
             mismatch=[False, False, False, False, True])  # fmt: skip
    assert r.state.tolist() == [NO_REFERENCE] * 4 + [UNIT_MISMATCH]
    assert r.status.tolist() == [NOT_ASSESSED] * 7 and not r.rule.any() and np.isnan(r.z).all()
    assert (r.norm.sum(), r.warn.sum(), r.fail.sum(), r.n.tolist()) == (0, 0, 0, [3, 1, 1, 1, 1])
    close(r.mean, [1.6, 5.0, 5.0, 7.0, 9.0])  # the values themselves are there
    close(r.std[:1], [math.sqrt(0.02 / 3)])
    assert np.isnan(r.bias).all() and r.bias_class.tolist() == [NOT_ASSESSED] * 5
    assert np.isnan(r.mu).all() and np.isnan(r.sd).all()  # nothing to draw the lines of the chart by


def test_swap_on_two_crm() -> None:
    #          A     B     C     D (no sample)  E (no reference)  F (units differ)
    mu = [1.00, 2.00, 2.10, 2.06, 2.05, 2.05]
    sd = [0.05, 0.10, 0.20, 0.01, NAN, 0.01]
    mismatch = [False] * 5 + [True]
    x = [2.05, 1.16, 1.00, 2.20, 2.07, 1.0]
    crm = [0, 0, 1, 0, 0, 4]
    r = calc(x, crm=crm, mu=mu, sd=sd, mismatch=mismatch)
    assert r.status.tolist() == [FAIL, FAIL, FAIL, FAIL, FAIL, NOT_ASSESSED]  # a swap stays «отказ»
    # 2,05 of A: |z| is 1 by D, 0,25 by C, 0,5 by B — the nearest first; E and F have no reference
    assert r.swap.tolist()[0] == [2, 1, 3]
    assert r.swap.tolist()[1] == [-1, -1, -1]  # 1,16 of A is near no other CRM
    assert r.swap.tolist()[2] == [0, -1, -1]  # 1,00 of B is A
    assert r.swap.tolist()[3] == [2, 1, -1]  # 2,20 is right on 2σ of B — the soft side
    assert r.swap.tolist()[4] == [2, 1, 3]  # D at |z| = 1: three at most, by |z|
    assert r.swap.tolist()[5] == [-1, -1, -1]
    assert r.rule.tolist() == [RULE_SWAP, 0, RULE_SWAP, RULE_SWAP, RULE_SWAP, 0]
    assert r.swaps.tolist() == [3, 1, 0] and r.fail.tolist() == [4, 1, 0]
    # more than three near: the three nearest by |z|, equal |z| — by the code
    r = calc([3.0], mu=[1.0, 3.0, 3.1, 2.9, 3.05, 2.95], sd=[0.05, 1.0, 1.0, 1.0, 1.0, 1.0])
    assert r.swap.tolist() == [[1, 4, 5]]
    # only beyond 3σ: a warning and a failure by the pair are not looked up
    r = calc([1.11, 1.12], mu=[1.0, 1.1], sd=[0.05, 0.05])
    assert r.status.tolist() == [WARN, FAIL] and not (r.rule & RULE_SWAP).any() and (r.swap == -1).all()


def test_bias_classes_and_borders() -> None:
    cases = [([1.05] * 3, NORM), ([0.95] * 3, NORM), ([1.0501] * 3, WARN), ([1.10] * 3, WARN), ([0.90] * 3, WARN),
             ([1.1001] * 3, FAIL), ([0.80] * 3, FAIL), ([1.0, 1.0, 1.0], NORM)]  # fmt: skip
    for x, want in cases:
        r = calc(x, sd=[1.0])
        assert r.bias_class.tolist() == [want], x
        close(r.bias, [100.0 * (x[0] - MU) / MU])
    assert 100.0 * (1.05 - 1.0) / 1.0 > 5.0  # float64: 5.000000000000004 — the border is the soft side all the same


def test_mean_std_bias_and_moving_average() -> None:
    rng = np.random.default_rng(7)
    n = 77
    x = np.round(1.0 + 0.05 * rng.standard_normal(n), 4) + np.array([0.0, 1e6])[rng.integers(0, 2, n)]
    crm = (x > 1000).astype(np.int64)  # two groups interleaved: a window never takes rows of the other
    r = calc(x, crm=crm, mu=[1.0, 1e6 + 1], sd=[0.05, 0.05])
    for g in (0, 1):
        v = x[crm == g]
        assert r.rows(g).tolist() == np.flatnonzero(crm == g).tolist()
        mean = math.fsum(v) / v.size
        close(r.mean[g : g + 1], [mean])
        close(r.std[g : g + 1], [math.sqrt(math.fsum((t - mean) ** 2 for t in v) / v.size)])
        close(r.bias[g : g + 1], [100.0 * (mean - r.mu[g]) / r.mu[g]], scale=100.0)
        assert r.bias[g] == 100.0 * (r.mean[g] - r.mu[g]) / r.mu[g]
        ma = r.ma[crm == g]
        assert np.isnan(ma[:9]).all() and not np.isnan(ma[9:]).any()
        close(ma[9:], [math.fsum(v[i - 9 : i + 1]) / 10 for i in range(9, v.size)])
    assert np.isnan(calc([1.0] * 9).ma).all() and calc([1.0] * 10).ma.tolist()[9] == 1.0
    assert calc([3.0]).std.tolist() == [0.0]


@pytest.mark.parametrize(("seed", "k2", "k3", "run"), [(1, 2.0, 3.0, 9), (2, 2.0, 3.0, 9), (3, 1.5, 2.5, 5)])
def test_hard_data_against_row_by_row_count(seed: int, k2: float, k3: float, run: int) -> None:
    rng = np.random.default_rng(seed)
    n = 4000
    mu = np.array([1.0, 2.0, 2.1, 0.35, 12.5, NAN, 3.0, 1.02])
    sd = np.array([0.05, 0.10, 0.20, 0.02, 0.5, 0.1, NAN, 0.04])
    mismatch = (4,) if seed == 2 else ()
    crm = rng.integers(0, mu.size, n)
    lab = rng.integers(0, 3, n)
    # multiples of σ / 2 — right on μ, 2σ, 3σ and around them; some from another CRM; some missing
    steps = rng.integers(-8, 9, n) / 2.0
    safe_mu, safe_sd = np.where(np.isfinite(mu), mu, 1.0), np.where(np.isfinite(sd), sd, 0.1)
    x = safe_mu[crm] + steps * safe_sd[crm] + np.where(rng.random(n) < 0.3, 0.003 * rng.standard_normal(n), 0.0)
    other = rng.random(n) < 0.08
    x[other] = safe_mu[rng.integers(0, mu.size, n)][other] + 0.01 * rng.standard_normal(int(other.sum()))
    x[rng.random(n) < 0.05] = NAN
    date: list[int | None] = [None if rng.random() < 0.2 else int(rng.integers(0, 40)) for _ in range(n)]
    seq: list[float | None] = [None if rng.random() < 0.2 else float(rng.integers(1, 6)) for _ in range(n)]
    lim = Limits(sigma_warn=k2, sigma_fail=k3, run=run)
    flags = [j in mismatch for j in range(mu.size)]
    r = calc(x, crm=crm, lab=lab, date=date, seq=seq, mu=mu, sd=sd, limits=lim, mismatch=flags)
    want = direct(x, crm, lab, date, seq, mu, sd, k2, k3, run, mismatch)
    assert r.order.tolist() == want["order"]
    rows = want["rows"]
    assert r.status.tolist() == rows["status"] and r.rule.tolist() == rows["rule"] and r.run.tolist() == rows["run"]
    assert r.swap.tolist() == rows["swap"]
    close(r.z, rows["z"])
    close(r.ma, rows["ma"])
    groups = want["groups"]
    assert len(groups) == r.crm.size == mu.size * 3
    for key in ("crm", "lab", "n", "missing", "state", "norm", "warn", "fail", "pair_fails", "run_flags", "swaps"):
        assert getattr(r, key).tolist() == [g[key] for g in groups], key
    for key in ("mean", "std"):
        close(getattr(r, key), [g[key] for g in groups])
    close(r.bias, [g["bias"] for g in groups], scale=100.0)
    assert r.start.tolist() == np.concatenate(([0], np.cumsum(r.n))).tolist()
    # every kind of outcome is in the data
    assert {NOT_ASSESSED, NORM, WARN, FAIL} == set(rows["status"])
    assert all(any(v & bit for v in rows["rule"]) for bit in (RULE_PAIR, RULE_RUN, RULE_SWAP))
    assert max(len([j for j in s if j >= 0]) for s in rows["swap"]) >= 2


def test_empty_and_degenerate_inputs() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no warning of numpy leaves a kernel
        r = calc([])
        assert r.z.shape == r.status.shape == r.rule.shape == r.ma.shape == r.run.shape == (0,)
        assert r.swap.shape == (0, 3) and r.order.size == 0 and r.crm.size == 0 and r.start.tolist() == [0]
        r = calc([1.0])
        assert (r.status.tolist(), r.run.tolist(), r.n.tolist()) == ([NORM], [0], [1])
        r = calc([1e300, -1e300, 1e300], sd=[1e-300])  # numbers that overflow: «отказ»; not computed is NaN, not inf
        assert r.status.tolist() == [FAIL, FAIL, FAIL] and r.run.tolist() == [1, 1, 1]
        assert np.isnan(r.z).all() and np.isnan(r.std[0]) and r.bias_class.tolist() == [FAIL]
        close(r.mean, [1e300 / 3])
        r = calc([1.0, 1.0, 1.0, 1.0], crm=[0, 1, 2, 3], mu=[1.0] * 4, sd=[0.05] * 4)  # equal CRM: no swap at μ
        assert r.status.tolist() == [NORM] * 4 and (r.swap == -1).all()
        r = calc([9.0, 1e300], crm=[0, 0], mu=[1.0, 5.0, 2.0], sd=[0.05, 1e308, 1e-320])  # σ huge and tiny
        assert r.status.tolist() == [FAIL, FAIL] and r.swap.tolist() == [[1, -1, -1], [1, -1, -1]]


def test_many_crm_near_one_value() -> None:
    """Two crowds of CRM: every sample of the first with a value of the second fails and is near all 150 CRM
    of it — the three nearest are kept, equal distances by the code."""
    k, n = 300, 1200
    mu = np.concatenate((1.0 + np.arange(150) * 1e-4, 1.5 + np.arange(150) * 1e-4))
    sd = np.full(k, 0.05)
    crm = np.arange(n) % k
    x = 1.5 + 0.0007 * (np.arange(n) % 11)
    r = calc(x, crm=crm, mu=mu, sd=sd)
    want = direct(x, crm, np.zeros(n, np.int64), [None] * n, [None] * n, mu, sd)
    assert r.swap.tolist() == want["rows"]["swap"] and r.status.tolist() == want["rows"]["status"]
    assert int((r.swap >= 0).all(axis=1).sum()) == 600 and int(r.swaps.sum()) == 600


def test_order_with_time_of_day_fractions_and_far_numbers() -> None:
    """The keys of the sort: dates with the time of day, SEQ that are not whole, numbers far apart — the same
    order as a plain sort of (date, SEQ, row)."""
    rng = np.random.default_rng(3)
    n = 500
    for date, seq in (
        (rng.integers(0, 5, n) * 86_400_000.0 + rng.integers(0, 3, n) * 3_600_000.0, rng.integers(1, 4, n) / 2.0),
        (rng.integers(-3, 3, n) * 86_400_000.0 * 40_000, rng.integers(1, 4, n) * 1e12),
        (rng.integers(0, 5, n) * 86_400_000.0, rng.integers(-4, 4, n).astype(np.float64)),
        (rng.integers(0, 3, n) * 1.0, rng.integers(0, 2, n) * 1e300),
        (1.6e12 + rng.integers(0, 5, n) * 86_400_000.0, 0.5 + rng.integers(0, 3, n)),
        (np.where(rng.random(n) < 0.5, 1e19, -math.inf), np.where(rng.random(n) < 0.5, math.inf, 2.0)),
    ):
        date[rng.random(n) < 0.1] = NAN
        seq[rng.random(n) < 0.1] = NAN
        zero = np.zeros(n, np.int64)
        got = standards(np.ones(n), zero, zero, date, seq, [MU], [SD]).order.tolist()
        no_date = [not abs(float(d)) < 9e18 for d in date]  # NaN, ±inf and what is not a date at all
        no_seq = [not math.isfinite(v) for v in seq]
        key = [(no_date[i], 0.0 if no_date[i] else date[i], no_seq[i], 0.0 if no_seq[i] else seq[i], i)
               for i in range(n)]  # fmt: skip
        assert got == sorted(range(n), key=key.__getitem__)


def test_group_index_dense_and_sparse_codes() -> None:
    a, b = np.array([3, 0, 3, 1, 0, 3]), np.array([1, 2, 1, 0, 2, 0])
    index, (ga, gb) = group_index(a, b)
    assert (ga.tolist(), gb.tolist()) == ([0, 1, 3, 3], [2, 0, 0, 1]) and index.tolist() == [3, 0, 3, 1, 0, 2]
    big = np.array([5, 2**40, 5, 0])  # codes too far apart for a table — the sort takes over
    index, (ga,) = group_index(big)
    assert ga.tolist() == [0, 5, 2**40] and index.tolist() == [1, 2, 1, 0]
    index, (ga, gb) = group_index(np.array([2**40, 1, 2**40]), np.array([2**40, 7, 2**40]))  # no single key
    assert (ga.tolist(), gb.tolist(), index.tolist()) == ([1, 2**40], [7, 2**40], [1, 0, 1])
    index, (ga,) = group_index(np.zeros(0, dtype=np.int64))
    assert index.size == 0 and ga.size == 0


def test_inputs_are_checked() -> None:
    one = np.zeros(2, dtype=np.int64)
    nat = days([None, None])
    with pytest.raises(ValueError):
        standards(np.ones(3), one, one, nat, np.ones(2), [1.0], [0.1])
    with pytest.raises(ValueError):
        standards(np.ones(2), one, one, nat, np.ones(3), [1.0], [0.1])
    with pytest.raises(ValueError):
        standards(np.ones(2), one + 1, one, nat, np.ones(2), [1.0], [0.1])  # a CRM the table does not know
    with pytest.raises(ValueError):
        standards(np.ones(2), one, one - 1, nat, np.ones(2), [1.0], [0.1])
    with pytest.raises(ValueError):
        standards(np.ones(2), one, one, nat, np.ones(2), [1.0], [0.1, 0.2])
    with pytest.raises(TypeError):
        standards(np.ones(2), one, one, np.array(["2024-01-01", ""]), np.ones(2), [1.0], [0.1])
    with pytest.raises(TypeError):
        standards(np.ones(2), one.astype(np.float64), one, nat, np.ones(2), [1.0], [0.1])


def test_read_only_columns_and_the_same_bits_twice() -> None:
    rng = np.random.default_rng(5)
    n = 5000
    cols = [1.0 + 0.06 * rng.standard_normal(n), rng.integers(0, 4, n), rng.integers(0, 2, n),
            days([int(d) for d in rng.integers(0, 50, n)]), rng.integers(1, 9, n).astype(np.float64)]  # fmt: skip
    for c in cols:
        c.setflags(write=False)  # the cached columns of the records are read-only
    mu, sd = np.array([1.0, 1.05, 0.9, 1.2]), np.array([0.05, 0.05, 0.04, 0.06])
    a, b = standards(*cols, mu, sd), standards(*cols, mu, sd)
    for name in ("z", "status", "rule", "ma", "run", "swap", "order", "start", "mean", "std", "bias", "fail"):
        assert getattr(a, name).tobytes() == getattr(b, name).tobytes(), name
    assert a.z.dtype == a.ma.dtype == a.mean.dtype == np.float64 and a.status.dtype == np.int8
    assert a.rule.dtype == np.uint8 and a.order.dtype == a.start.dtype == a.n.dtype == np.int64


def test_kernels_import_no_numba_and_no_database() -> None:
    code = (
        "import sys; import bf_core.qaqc.limits;"
        "assert 'numpy' not in sys.modules, 'limits imports numpy';"
        "import bf_core.qaqc.standards;"
        "heavy=[m for m in ('numba','scipy','pandas','pyarrow','duckdb','sqlalchemy','fastapi') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_hundred_thousand_samples_in_sixty_groups_in_time() -> None:
    """The criterion of 14.2 is 300 thousand samples in 60 groups within 0.2 s (measured by `bench`); here — a
    third of them (pytest holds at most 100 thousand records) within the same bound, the best of three."""
    rng = np.random.default_rng(11)
    n = 100_000
    crm, lab = rng.integers(0, 20, n), rng.integers(0, 3, n)
    mu = 0.5 + np.arange(20) * 0.4
    sd = mu * 0.03
    x = mu[crm] + sd[crm] * rng.standard_normal(n)
    date = (np.datetime64("2020-01-01", "ms") + rng.integers(0, 1500, n) * np.timedelta64(1, "D")).astype("M8[ms]")
    seq = rng.integers(1, 60, n).astype(np.float64)
    best = math.inf
    for _ in range(3):
        t0 = time.perf_counter()
        r = standards(x, crm, lab, date, seq, mu, sd)
        best = min(best, time.perf_counter() - t0)
    assert r.crm.size == 60 and int(r.n.sum()) == n
    assert best <= 0.2, f"{best:.3f} s"
