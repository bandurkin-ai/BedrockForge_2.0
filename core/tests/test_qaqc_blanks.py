"""Blanks and batches of QAQC (ТЗ 8.4.2, Т8.4-2; ADR 0020 §4): a blank against k_б·DL, «ниже DL», no DL, the DL
of a record against the table, the pairs of the carry-over, a batch (LAB, BATCH) failed by a CRM or a blank.

Tolerance (10.5 п. 1): the statuses, the counters, the order of a series and of the batches — exact; r of the
carry-over — 1e−12 against the formula counted directly."""

from __future__ import annotations

import math
import subprocess
import sys
import time
import warnings
from typing import Any

import numpy as np
import pytest

from bf_core.qaqc.batches import Batches, batches
from bf_core.qaqc.blanks import Blanks, blanks, carryover, effective_dl
from bf_core.qaqc.limits import FAIL, NORM, NOT_ASSESSED, WARN, Limits, QaqcError
from bf_core.qaqc.standards import RULE_PAIR, RULE_RUN, standards

NAN = math.nan
DAY = 86_400_000.0


def numbers(values: Any) -> np.ndarray:
    return np.array([NAN if v is None else v for v in values], dtype=np.float64)


def days(values: Any) -> np.ndarray:
    """Dates as `qaqc.records` gives them: float64 milliseconds, NaN — no date."""
    return numbers([None if d is None else d * DAY for d in values])


def calc(x: Any, dl: Any = 0.01, lab: Any = None, date: Any = None, seq: Any = None, limits: Limits | None = None,
         below: Any = None) -> Blanks:  # fmt: skip
    """The kernel on a series of blanks; what is not given: one laboratory, one DL, no dates, no SEQ."""
    n = len(x)
    return blanks(
        numbers(x),
        np.zeros(n, dtype=np.int64) if lab is None else np.asarray(lab),
        days([None] * n) if date is None else days(date),
        numbers([None] * n) if seq is None else numbers(seq),
        np.full(n, dl, dtype=np.float64) if np.ndim(dl) == 0 else numbers(dl),
        limits,
        below=below,
    )


# --- blanks -------------------------------------------------------------------------------------------------


def test_t842_blanks() -> None:
    r = calc([0.04, 0.05, 0.06])  # DL = 0,01, k_б = 5
    assert r.status.tolist() == [NORM, NORM, FAIL]
    assert np.allclose(r.limit, 0.05, rtol=1e-15) and r.order.tolist() == [0, 1, 2] and r.start.tolist() == [0, 3]
    assert (r.lab.tolist(), r.n.tolist(), r.missing.tolist()) == ([0], [3], [0])
    assert (r.norm.tolist(), r.fail.tolist(), r.not_assessed.tolist()) == ([2], [1], [0])
    assert (r.dl.tolist(), r.mixed.tolist()) == ([0.01], [False]) and np.allclose(r.group_limit, 0.05, rtol=1e-15)
    assert r.rows(0).tolist() == [0, 1, 2]


def test_border_is_the_soft_side_in_any_units() -> None:
    assert calc([0.05 + 1e-12, 0.0500001]).status.tolist() == [NORM, FAIL]
    for scale in (1e-9, 1e-4, 1.0, 1e4, 1e9):  # ppb or %: the same blanks, the same statuses
        r = calc([0.04 * scale, 0.05 * scale, 0.0500001 * scale, 0.06 * scale], dl=0.01 * scale)
        assert r.status.tolist() == [NORM, NORM, FAIL, FAIL], scale
    assert calc([0.01, 0.0101, -0.5], limits=Limits(k_blank=1)).status.tolist() == [NORM, FAIL, NORM]
    assert calc([0.5, 0.51], limits=Limits(k_blank=50)).status.tolist() == [NORM, FAIL]
    assert calc([0.03, 0.031, 0.02], limits=Limits(k_blank=3)).status.tolist() == [NORM, FAIL, NORM]


def test_k_blank_out_of_range_is_bad_param() -> None:
    for value in (0.99, 50.5, 0, -5, NAN, "5", True):
        with pytest.raises(QaqcError) as e:
            Limits(k_blank=value)
        assert (e.value.code, e.value.param) == ("bad_param", "k_blank") and e.value.hint
    assert Limits.parse({"k_blank": 1}).k_blank == 1.0 and Limits.parse({"k_blank": 50}).k_blank == 50.0


def test_below_dl_is_norm_and_no_dl_is_not_assessed() -> None:
    #        half of DL  «<DL», no DL  no DL   «<DL» far above the table DL   DL ≤ 0   no value
    x = [0.005, 0.25, 0.04, 0.25, 0.04, None]
    dl = [0.01, None, None, 0.01, 0.0, 0.01]
    below = [True, True, False, True, False, False]
    r = calc(x, dl=dl, below=below)
    assert r.status.tolist() == [NORM, NORM, NOT_ASSESSED, NORM, NOT_ASSESSED, NOT_ASSESSED]
    assert (r.n.tolist(), r.missing.tolist()) == ([5], [1])
    assert (r.norm.tolist(), r.fail.tolist(), r.not_assessed.tolist()) == ([3], [0], [2])  # «не оценён» is not «норма»
    assert np.isnan(r.limit[[1, 2, 4]]).all() and np.allclose(r.limit[[0, 3, 5]], 0.05, rtol=1e-15)
    assert calc([0.25], below=None).status.tolist() == [FAIL]  # the same value as a number is a failure
    r = calc([0.04, 0.9], dl=[None, None])
    assert r.status.tolist() == [NOT_ASSESSED] * 2 and np.isnan(r.dl).all() and np.isnan(r.group_limit).all()
    assert (r.not_assessed.tolist(), r.mixed.tolist()) == ([2], [False])


def test_dl_of_a_record_beats_the_table() -> None:
    lab = np.array([0, 0, 1, 1, 2, 2, 0, 0, 0])
    table = np.array([0.01, 0.02, NAN])  # the table by laboratory: (element, laboratory), else (element, any)
    field = numbers([None, 0.02, None, 0.005, None, 0.03, 0.0, -1.0, math.inf])
    dl = effective_dl(lab, table, field)
    assert dl.tolist()[:2] == [0.01, 0.02] and dl.tolist()[2:4] == [0.02, 0.005]
    assert math.isnan(dl[4]) and dl[5] == 0.03 and dl.tolist()[6:] == [0.01, 0.01, 0.01]  # a DL ≤ 0 or inf is none
    assert np.array_equal(effective_dl(lab, table), table[lab], equal_nan=True)
    x = [0.08] * 9
    r = blanks(numbers(x), lab, days([None] * 9), numbers([None] * 9), dl)
    #                          0,05   0,10  0,10  0,025  —             0,15  0,05  0,05  0,05
    assert r.status.tolist() == [FAIL, NORM, NORM, FAIL, NOT_ASSESSED, NORM, FAIL, FAIL, FAIL]
    assert r.lab.tolist() == [0, 1, 2] and r.mixed.tolist() == [True, True, False]
    assert np.isnan(r.dl[:2]).all() and r.dl[2] == 0.03 and np.isnan(r.group_limit[:2]).all()
    assert (r.norm.tolist(), r.fail.tolist(), r.not_assessed.tolist()) == ([1, 1, 1], [4, 1, 0], [0, 0, 1])
    with pytest.raises(ValueError):
        effective_dl(lab, table[:2])
    with pytest.raises(ValueError):
        effective_dl(lab, table, field[:3])


def test_series_by_laboratory_in_date_seq_row_order() -> None:
    x = [0.01, 0.02, 0.03, 0.04, None, 0.06, 0.07]
    r = calc(x, lab=[1, 0, 1, 0, 0, 1, 0], date=[3, 2, 1, 2, 1, None, 2], seq=[1, 2, 1, 1, 1, 1, None])
    assert r.lab.tolist() == [0, 1] and r.start.tolist() == [0, 3, 6]
    assert r.order.tolist() == [3, 1, 6, 2, 0, 5]  # the sample without a value is in no series
    assert [r.rows(g).tolist() for g in (0, 1)] == [[3, 1, 6], [2, 0, 5]]
    assert (r.n.tolist(), r.missing.tolist(), r.fail.tolist()) == ([3, 3], [1, 0], [1, 1])
    assert r.status.tolist() == [NORM, NORM, NORM, NORM, NOT_ASSESSED, FAIL, FAIL]


def pearson(u: np.ndarray, v: np.ndarray) -> float:
    du, dv = u - math.fsum(u) / u.size, v - math.fsum(v) / v.size
    return math.fsum(du * dv) / math.sqrt(math.fsum(du * du) * math.fsum(dv * dv))


def test_carryover_pairs_and_first_sample_of_a_batch() -> None:
    rng = np.random.default_rng(4)
    n = 400
    prev = np.round(rng.lognormal(0.0, 1.0, n), 3)
    x = np.round(0.004 + 0.002 * prev + 0.001 * rng.standard_normal(n), 5)
    first = rng.random(n) < 0.2  # the first sample of a batch has no previous one
    prev[first] = NAN
    x[rng.random(n) < 0.05] = NAN
    pairs = np.isfinite(prev) & np.isfinite(x)
    count, r = carryover(x, prev)
    assert count == int(pairs.sum()) < n - int(first.sum()) + 1
    assert r == pytest.approx(pearson(prev[pairs], x[pairs]), rel=1e-12) and 0.5 < r < 1.0
    # by the series of a laboratory, as the tab asks
    res = blanks(x, rng.integers(0, 2, n), days([None] * n), numbers([None] * n), np.full(n, 0.01))
    for g in (0, 1):
        rows = res.rows(g)
        m = np.isfinite(prev[rows])
        got = carryover(x[rows], prev[rows])
        assert got[0] == int(m.sum()) and got[1] == pytest.approx(pearson(prev[rows][m], x[rows][m]), rel=1e-12)
    # degenerate: nothing, one or two pairs, no spread — no r, and never a zero for it
    assert carryover(numbers([]), numbers([])) == (0, pytest.approx(NAN, nan_ok=True))
    assert carryover(numbers([0.1, 0.2]), numbers([None, None]))[0] == 0
    count, r = carryover(numbers([0.1, 0.2, None]), numbers([1.0, 2.0, 3.0]))
    assert count == 2 and math.isnan(r)
    count, r = carryover(numbers([0.1, 0.2, 0.3]), numbers([5.0, 5.0, 5.0]))
    assert count == 3 and math.isnan(r)
    assert carryover(numbers([1.0, 2.0, 3.0, 4.0]), numbers([2.0, 4.0, 6.0, 8.0])) == (4, 1.0)
    assert carryover(numbers([4.0, 3.0, 2.0, 1.0]), numbers([2.0, 4.0, 6.0, 8.0])) == (4, -1.0)
    with pytest.raises(ValueError):
        carryover(numbers([1.0, 2.0]), numbers([1.0]))


def direct_blanks(x: Any, lab: Any, date: Any, seq: Any, dl: Any, below: Any, k: float) -> dict[str, Any]:
    """ADR 0020 §4 «Бланки» row by row."""
    n = len(x)
    status = [NOT_ASSESSED] * n
    members: dict[int, list[int]] = {}
    for i in range(n):
        members.setdefault(int(lab[i]), []).append(i)
    order: list[int] = []
    groups = []
    for lb in sorted(members):
        series = [i for i in members[lb] if math.isfinite(x[i])]
        series.sort(key=lambda i: (math.isnan(date[i]), 0.0 if math.isnan(date[i]) else date[i], math.isnan(seq[i]),
                                   0.0 if math.isnan(seq[i]) else seq[i], i))  # fmt: skip
        order += series
        known = set()
        for i in series:
            has_dl = math.isfinite(dl[i]) and dl[i] > 0
            if has_dl:
                known.add(float(dl[i]))
            if below[i]:
                status[i] = NORM
            elif has_dl:
                ratio = x[i] / dl[i]
                status[i] = NORM if ratio <= k + 1e-9 * max(1.0, abs(k)) else FAIL
        st = [status[i] for i in series]
        groups.append({"lab": lb, "n": len(series), "missing": len(members[lb]) - len(series),
                       "norm": st.count(NORM), "fail": st.count(FAIL), "not_assessed": st.count(NOT_ASSESSED),
                       "dl": known.pop() if len(known) == 1 else NAN, "mixed": len(known) > 1})  # fmt: skip
    return {"status": status, "order": order, "groups": groups}


@pytest.mark.parametrize(("seed", "k"), [(1, 5.0), (2, 1.0), (3, 12.5)])
def test_hard_blanks_against_row_by_row_count(seed: int, k: float) -> None:
    rng = np.random.default_rng(seed)
    n = 3000
    lab = rng.integers(0, 5, n)
    dl = np.array([0.01, 0.02, 0.5, NAN, 0.001])[lab]
    other = (rng.random(n) < 0.15) & (lab != 4)  # a DL of the record itself, some of them unusable
    dl[other] = rng.choice([0.005, 0.01, 0.05, 0.0, -1.0, NAN, math.inf], int(other.sum()))
    steps = rng.integers(0, 2 * int(k) + 6, n) / 2.0  # multiples of DL / 2: right on k_б·DL and around
    x = steps * np.where(np.isfinite(dl) & (dl > 0), dl, 0.01) + np.where(rng.random(n) < 0.3, 1e-7, 0.0)
    x[rng.random(n) < 0.05] = NAN
    x[rng.random(n) < 0.02] = -0.01
    below = rng.random(n) < 0.2
    date = np.where(rng.random(n) < 0.2, NAN, rng.integers(0, 30, n) * DAY)
    seq = np.where(rng.random(n) < 0.2, NAN, rng.integers(1, 5, n).astype(np.float64))
    r = blanks(x, lab, date, seq, dl, Limits(k_blank=k), below=below)
    want = direct_blanks(x, lab, date, seq, dl, below, k)
    assert r.status.tolist() == want["status"] and r.order.tolist() == want["order"]
    for key in ("lab", "n", "missing", "norm", "fail", "not_assessed", "mixed"):
        assert getattr(r, key).tolist() == [g[key] for g in want["groups"]], key
    assert np.array_equal(r.dl, [g["dl"] for g in want["groups"]], equal_nan=True)
    assert np.array_equal(r.group_limit, k * r.dl, equal_nan=True)
    usable = np.isfinite(dl) & (dl > 0)
    assert np.array_equal(r.limit, np.where(usable, k * np.where(usable, dl, 1.0), NAN), equal_nan=True)
    assert {NORM, FAIL, NOT_ASSESSED} == set(want["status"]) and r.mixed.any() and not r.mixed.all()
    assert r.start.tolist() == np.concatenate(([0], np.cumsum(r.n))).tolist()


def test_blanks_empty_degenerate_and_checked_inputs() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        r = calc([])
        assert r.status.shape == r.limit.shape == (0,) and r.lab.size == 0 and r.start.tolist() == [0]
        r = calc([None, None])
        assert (r.n.tolist(), r.missing.tolist(), r.status.tolist()) == ([0], [2], [NOT_ASSESSED] * 2)
        assert np.isnan(r.dl).all() and not r.mixed.any()
        r = calc([1e300, 1e-300, math.inf], dl=[1e-300, 1e300, 0.01])  # the ratio overflows: a failure all the same
        assert r.status.tolist() == [FAIL, NORM, NOT_ASSESSED] and r.missing.tolist() == [1]
        assert np.isnan(calc([1.0], dl=1e308).limit).all()  # a limit that is not a number is not shown as inf
    zero, nat = np.zeros(2, dtype=np.int64), days([None, None])
    with pytest.raises(ValueError):
        blanks(np.ones(3), zero, nat, np.ones(2), np.ones(2))
    with pytest.raises(ValueError):
        blanks(np.ones(2), zero - 1, nat, np.ones(2), np.ones(2))
    with pytest.raises(ValueError):
        blanks(np.ones(2), zero, nat, np.ones(2), np.ones(3))
    with pytest.raises(ValueError):
        blanks(np.ones(2), zero, nat, np.ones(2), np.ones(2), below=[True])
    with pytest.raises(TypeError):
        blanks(np.ones(2), zero.astype(np.float64), nat, np.ones(2), np.ones(2))


# --- batches ------------------------------------------------------------------------------------------------


def test_batches_failed_by_crm_by_blank_and_not_by_a_run() -> None:
    """Five batches of one laboratory and the records without a batch: A — a CRM beyond 3σ, B — a blank above
    k_б·DL, C — nine CRM on one side of μ (a run: a warning, the batch stands), D — a CRM failed by the pair,
    E — no control samples at all."""
    #          type  batch  value
    records = ([("crm", 0, 1.16), ("orig", 0, 3.0), ("blank", 0, 0.01)]
               + [("crm", 1, 1.01), ("blank", 1, 0.06), ("dup", 1, 2.0)]
               + [("crm", 2, 1.01)] * 9 + [("blank", 2, 0.02)]
               + [("crm", 3, 1.11), ("crm", 3, 1.12)]
               + [("orig", 4, 5.0), ("dup", 4, 5.1)]
               + [("crm", -1, 1.30), ("blank", -1, 0.9), ("orig", -1, 1.0)])  # fmt: skip
    n = len(records)
    kind = np.array([t for t, _, _ in records])
    batch = np.array([b for _, b, _ in records])
    value = np.array([v for _, _, v in records])
    lab = np.zeros(n, dtype=np.int64)
    date = days(list(range(n)))
    seq = numbers([None] * n)
    crm_rows, blank_rows = np.flatnonzero(kind == "crm"), np.flatnonzero(kind == "blank")
    # every batch has its own CRM, so that a series never runs over two batches
    code = np.maximum(batch[crm_rows], 0)
    st = standards(value[crm_rows], code, lab[crm_rows], date[crm_rows], seq[crm_rows], [1.0] * 5, [0.05] * 5)
    bl = blanks(value[blank_rows], lab[blank_rows], date[blank_rows], seq[blank_rows], np.full(blank_rows.size, 0.01))
    assert st.status.tolist() == [FAIL, NORM] + [NORM] * 8 + [WARN] + [WARN, FAIL] + [FAIL]
    assert st.rule.tolist()[10] == RULE_RUN and st.rule.tolist()[12] == RULE_PAIR
    assert bl.status.tolist() == [NORM, FAIL, NORM, FAIL]
    r = batches(lab, batch, date, crm_rows, st.status, blank_rows, bl.status)
    assert (r.total, r.failed, r.not_assessed, r.no_batch) == (5, 3, 1, 3)
    assert (r.lab.tolist(), r.batch.tolist()) == ([0] * 5, [0, 1, 2, 3, 4])
    assert r.status.tolist() == [FAIL, FAIL, NORM, FAIL, NOT_ASSESSED]  # no control is not «норма»
    assert (r.n.tolist(), r.crm.tolist(), r.blanks.tolist()) == ([3, 3, 10, 2, 2], [1, 1, 9, 2, 0], [1, 1, 1, 0, 0])
    assert (r.crm_fail.tolist(), r.blank_fail.tolist()) == ([1, 0, 0, 1, 0], [0, 1, 0, 0, 0])
    assert r.assessed.tolist() == [2, 2, 10, 2, 0]
    assert (r.date_from.tolist(), r.date_to.tolist()) == ([0.0, 3 * DAY, 6 * DAY, 16 * DAY, 18 * DAY],
                                                          [2 * DAY, 5 * DAY, 15 * DAY, 17 * DAY, 19 * DAY])  # fmt: skip
    assert r.index.tolist() == [0] * 3 + [1] * 3 + [2] * 10 + [3] * 2 + [4] * 2 + [-1] * 3


def test_batch_key_is_laboratory_and_batch_ordered_by_date() -> None:
    lab = np.array([0, 1, 0, 1, 2, 2, 0])
    batch = np.array([7, 7, 7, 3, 3, 3, 9])
    date = days([5, 2, 4, None, None, 9, None])
    crm_rows, crm_status = np.array([0, 3, 4]), np.array([NORM, NOT_ASSESSED, FAIL], dtype=np.int8)
    blank_rows, blank_status = np.array([1, 5]), np.array([FAIL, NORM], dtype=np.int8)
    r = batches(lab, batch, date, crm_rows, crm_status, blank_rows, blank_status)
    # by the first date; a batch without dates goes last, equal — by the laboratory, then the batch
    assert (r.lab.tolist(), r.batch.tolist()) == ([1, 0, 2, 0, 1], [7, 7, 3, 9, 3])
    assert np.array_equal(r.date_from, days([2, 4, 9, None, None]), equal_nan=True)
    assert np.array_equal(r.date_to, days([2, 5, 9, None, None]), equal_nan=True)
    assert r.n.tolist() == [1, 2, 2, 1, 1] and r.index.tolist() == [1, 0, 1, 4, 2, 2, 3]
    # a CRM without a reference assesses nothing: its batch is not assessed, not passed
    assert r.status.tolist() == [FAIL, NORM, FAIL, NOT_ASSESSED, NOT_ASSESSED]
    assert (r.crm.tolist(), r.assessed.tolist()) == ([0, 1, 1, 0, 1], [1, 1, 2, 0, 0])
    assert (r.total, r.failed, r.not_assessed, r.no_batch) == (5, 2, 2, 0)
    # datetime64 and int64 dates are taken as well
    stamp = np.array([np.datetime64("NaT" if math.isnan(v) else int(v), "ms") for v in date])
    again = batches(lab, batch, stamp, crm_rows, crm_status, blank_rows, blank_status)
    assert again.batch.tolist() == r.batch.tolist() and np.array_equal(again.date_to, r.date_to, equal_nan=True)


def direct_batches(lab: Any, batch: Any, date: Any, crm: dict[int, int], blank: dict[int, int]) -> list[dict[str, Any]]:
    table: dict[tuple[int, int], dict[str, Any]] = {}
    for i in range(len(lab)):
        if batch[i] < 0:
            continue
        b = table.setdefault((int(lab[i]), int(batch[i])), {
            "lab": int(lab[i]), "batch": int(batch[i]), "n": 0, "crm": 0, "blanks": 0, "crm_fail": 0, "blank_fail": 0,
            "assessed": 0, "dates": []})  # fmt: skip
        b["n"] += 1
        if not math.isnan(date[i]):
            b["dates"].append(date[i])
        for name, status in (("crm", crm.get(i)), ("blank", blank.get(i))):
            if status is not None:
                b["crm" if name == "crm" else "blanks"] += 1
                b[f"{name}_fail"] += status == FAIL
                b["assessed"] += status != NOT_ASSESSED
    out = list(table.values())
    for b in out:
        b["date_from"], b["date_to"] = (min(b["dates"]), max(b["dates"])) if b["dates"] else (NAN, NAN)
        fails = b["crm_fail"] + b["blank_fail"]
        b["status"] = FAIL if fails else NORM if b["assessed"] else NOT_ASSESSED
    out.sort(key=lambda b: (math.isnan(b["date_from"]), 0.0 if math.isnan(b["date_from"]) else b["date_from"],
                            b["lab"], b["batch"]))  # fmt: skip
    return out


@pytest.mark.parametrize("seed", [1, 2])
def test_hard_batches_against_row_by_row_count(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = 5000
    lab = rng.integers(0, 4, n)
    batch = rng.integers(-1, 300 if seed == 1 else 10**7, n)  # the second — codes far apart
    date = np.where(rng.random(n) < 0.3, NAN, rng.integers(0, 60, n) * DAY)
    kind = rng.integers(0, 4, n)  # 0, 1 — other records, 2 — CRM, 3 — blank
    crm_rows, blank_rows = np.flatnonzero(kind == 2), np.flatnonzero(kind == 3)
    crm_status = rng.choice([NOT_ASSESSED, NORM, WARN, FAIL], crm_rows.size, p=[0.2, 0.6, 0.1, 0.1]).astype(np.int8)
    blank_status = rng.choice([NOT_ASSESSED, NORM, FAIL], blank_rows.size, p=[0.3, 0.6, 0.1]).astype(np.int8)
    r = batches(lab, batch, date, crm_rows, crm_status, blank_rows, blank_status)
    want = direct_batches(lab, batch, date, dict(zip(crm_rows.tolist(), crm_status.tolist(), strict=True)),
                          dict(zip(blank_rows.tolist(), blank_status.tolist(), strict=True)))  # fmt: skip
    assert r.total == len(want) and r.no_batch == int((batch < 0).sum())
    for key in ("lab", "batch", "n", "crm", "blanks", "crm_fail", "blank_fail", "assessed", "status"):
        assert getattr(r, key).tolist() == [b[key] for b in want], key
    for key in ("date_from", "date_to"):
        assert np.array_equal(getattr(r, key), [b[key] for b in want], equal_nan=True), key
    assert r.failed == sum(b["status"] == FAIL for b in want) > 0
    assert r.not_assessed == sum(b["status"] == NOT_ASSESSED for b in want) > 0
    place = {(b["lab"], b["batch"]): t for t, b in enumerate(want)}
    assert r.index.tolist() == [place.get((int(lab[i]), int(batch[i])), -1) for i in range(n)]


def test_batches_empty_and_checked_inputs() -> None:
    none = np.zeros(0, dtype=np.int64)
    no_status = np.zeros(0, dtype=np.int8)
    r = batches(none, none, numbers([]), none, no_status, none, no_status)
    assert (r.total, r.failed, r.not_assessed, r.no_batch) == (0, 0, 0, 0) and r.lab.size == 0 and r.index.size == 0
    lab, batch, date = np.zeros(3, dtype=np.int64), np.array([-1, -1, -1]), days([1, 2, 3])
    r = batches(lab, batch, date, np.array([0]), np.array([FAIL], dtype=np.int8), none, no_status)
    assert (r.total, r.no_batch, r.index.tolist()) == (0, 3, [-1, -1, -1])  # a failure without a batch fails none
    batch = np.zeros(3, dtype=np.int64)
    with pytest.raises(ValueError):
        batches(lab, batch[:2], date, none, no_status, none, no_status)
    with pytest.raises(ValueError):
        batches(lab - 1, batch, date, none, no_status, none, no_status)
    with pytest.raises(ValueError):
        batches(lab, batch, date, np.array([3]), np.array([FAIL], dtype=np.int8), none, no_status)
    with pytest.raises(ValueError):
        batches(lab, batch, date, np.array([0, 1]), np.array([FAIL], dtype=np.int8), none, no_status)
    with pytest.raises(ValueError):
        batches(lab, batch, date, none, no_status, np.array([-1]), np.array([FAIL], dtype=np.int8))


def test_read_only_columns_and_the_same_bits_twice() -> None:
    rng = np.random.default_rng(9)
    n = 4000
    cols = [rng.random(n) * 0.08, rng.integers(0, 3, n), rng.integers(0, 40, n) * DAY,
            rng.integers(1, 30, n).astype(np.float64), np.full(n, 0.01)]  # fmt: skip
    batch = rng.integers(-1, 200, n)
    for c in (*cols, batch):
        c.setflags(write=False)  # the cached columns of the records are read-only
    a, b = blanks(*cols), blanks(*cols)
    for name in ("status", "limit", "order", "start", "dl", "norm", "fail"):
        assert getattr(a, name).tobytes() == getattr(b, name).tobytes(), name
    rows = np.arange(0, n, 3)
    rows.setflags(write=False)
    one, two = (batches(cols[1], batch, cols[2], rows, a.status[rows], rows[:0], a.status[:0]) for _ in range(2))
    for name in ("lab", "batch", "date_from", "date_to", "n", "crm_fail", "status", "index"):
        assert getattr(one, name).tobytes() == getattr(two, name).tobytes(), name
    assert isinstance(one, Batches) and one.status.dtype == a.status.dtype == np.int8
    assert a.limit.dtype == one.date_from.dtype == np.float64 and one.index.dtype == one.n.dtype == np.int64
    assert carryover(cols[0], cols[4] * rng.random(n))[0] == n


def test_blanks_and_batches_import_no_numba_and_no_database() -> None:
    code = (
        "import sys, numpy as np; from bf_core.qaqc import blanks, batches;"
        "z = np.zeros(2, dtype=np.int64); s = np.zeros(2, dtype=np.int8);"
        "b = blanks.blanks(np.ones(2), z, np.full(2, np.nan), np.ones(2), np.ones(2));"
        "batches.batches(z, z, np.full(2, np.nan), z[:1], s[:1], z[:1] + 1, b.status[:1]);"
        "heavy=[m for m in ('numba','scipy','pandas','pyarrow','duckdb','sqlalchemy','fastapi') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""  # numba is loaded by `carryover` only — the kernel of Э13, already compiled


def test_blanks_and_batches_of_hundred_thousand_records_in_time() -> None:
    """The criterion of 14.3 is 150 thousand blanks and 20 thousand batches within 0.2 s (measured by `bench`);
    here — 50 thousand blanks of 100 thousand records in 7 thousand batches (pytest holds at most 100 thousand
    records) within the same bound, the best of three."""
    rng = np.random.default_rng(13)
    n = 100_000
    lab = rng.integers(0, 3, n)
    batch = rng.integers(0, 2400, n)
    date = rng.integers(0, 1500, n) * DAY
    seq = rng.integers(1, 60, n).astype(np.float64)
    kind = rng.integers(0, 10, n)
    blank_rows, crm_rows = np.flatnonzero(kind < 5), np.flatnonzero(kind >= 8)
    x = np.abs(rng.standard_normal(blank_rows.size)) * 0.02
    crm_status = rng.choice([NORM, WARN, FAIL], crm_rows.size, p=[0.9, 0.07, 0.03]).astype(np.int8)
    dl = np.full(blank_rows.size, 0.01)
    best = math.inf
    for _ in range(3):
        t0 = time.perf_counter()
        b = blanks(x, lab[blank_rows], date[blank_rows], seq[blank_rows], dl)
        r = batches(lab, batch, date, crm_rows, crm_status, blank_rows, b.status)
        best = min(best, time.perf_counter() - t0)
    assert int(b.n.sum()) == blank_rows.size >= 49_000 and 7000 <= r.total <= 7200 and r.failed > 0
    assert best <= 0.2, f"{best:.3f} s"
