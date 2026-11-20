"""Д-26, step 2 — прирезка по этапам and the «смягчённый» mode (ТЗ 7.35, Т8.2-14; ADR 0018 §5, §8): triples
Р_в, Б, Р_н, некондиционная руда → безрудная, stages until the series stops changing; the stages and the
decisions are in the events. The mode also runs step 4 (сближенные интервалы, `test_ore_examples`)."""

from __future__ import annotations

import math

import numpy as np

from bf_core.composites import ore_units as ou
from bf_core.composites.ore_intervals import OreIntervals, OreParams, ore_intervals
from tests.oreutil import (
    EXAMPLES,
    brief,
    check_protocol,
    check_reference,
    check_rows,
    close,
    events_of,
    example_lines,
    example_params,
    make_lines,
    ref_k,
    ref_lines,
    ref_soft,
    unit_line,
)
from tests.test_ore_simple import _random_holes


def soft(**over: object) -> OreParams:
    return OreParams(**{"cutoff": 1.0, "m_min": 3.0, "m_waste_max": 3.0, "mode": "soft", **over})  # type: ignore[arg-type]


def stage_log(res: OreIntervals) -> list[tuple[int, str, int, int]]:
    """(stage, what, first sample, last sample) of the joins and of the ore units that became waste."""
    return [
        (e["stage"], e["decision"], e["row0"] + 1, e["row1"] + 1)
        for e in events_of(res, step=2, kind="unit")
        if e["decision"]
    ]


def test_t8_2_14_soft() -> None:
    """И2, сл. 17: ore 0–4 м @ 1,05, MC 4,2; the прирезка 0–7 м @ 0,985714… is rejected; samples 6 and 8 are
    subgrade; the chain 5–8 м is not picked (1 м from 0–4)."""
    ex = EXAMPLES["T8.2-14"]
    res = ore_intervals(example_lines("T8.2-14"), example_params("T8.2-14", "soft"), events=True)
    assert brief(res) == [
        ("ore", 0.0, 4.0), ("waste", 4.0, 5.0), ("subgrade", 5.0, 6.0), ("waste", 6.0, 7.0), ("subgrade", 7.0, 8.0),
    ]  # fmt: skip
    check_rows(res, ex["expect"]["soft"]["intervals"])
    ore = res.rows()[0]
    assert close(ore["c"], 1.05) and close(ore["mc"], 4.2) and ore["basis"] == "thickness"
    assert close(ore["waste_len"], 1.0) and ore["n_samples"] == 4
    names = [ou.CLASSES[c] for c in res.sample_cls]
    assert names == ["ore", "ore", "waste_in", "ore", "waste", "subgrade", "waste", "subgrade"]
    assert res.sample_ore_id.tolist() == [1, 1, 1, 1, 0, 0, 0, 0]

    # the decisions are in the events: stage 2 tries sample 8 through the waste 5–7
    assert check_protocol(res, ex["protocol"], ("pass", "rejected_c")) == 2
    rejected = events_of(res, samples=(1, 7), decision="rejected_c")[0]
    assert (rejected["stage"], rejected["side"], rejected["compared"]) == (2, "upper", "cutoff")
    assert close(rejected["c"], 6.9 / 7.0) and (rejected["from"], rejected["to"]) == (0.0, 7.0)
    assert stage_log(res) == [(1, "joined", 1, 4), (1, "demoted", 6, 6), (2, "demoted", 8, 8)]
    merged = [(e["stage"], e["row0"] + 1, e["row1"] + 1) for e in events_of(res, step=2, kind="unit", ore=False)]
    assert merged == [(1, 5, 7), (2, 5, 8)]
    assert not events_of(res, kind="layer")  # no fragment «порода … порода» longer than Мпп inside 0–4


def test_c_equal_to_cutoff_is_ore() -> None:
    """Samples 5–8 of И2, сл. 17: 0,8 + 1,1 + 0,8 + 1,3 over 4 м is exactly Сб — the прирезка passes. As a
    difference of running totals the same sum is below 4 (ADR 0018, находка 9)."""
    grades = EXAMPLES["T8.2-14"]["grades"]
    totals = np.cumsum(grades)
    assert totals[7] - totals[3] < 4.0
    res = ore_intervals(example_lines("T8.2-14"), example_params("T8.2-14", "soft"), events=True)
    lower = events_of(res, samples=(5, 8), side="lower")
    assert len(lower) == 1 and lower[0]["decision"] == "pass" and lower[0]["stage"] == 2
    assert close(lower[0]["c"], 1.0) and close(lower[0]["mc"], 4.0) and close(lower[0]["m"], 4.0)
    # the same holds when the sum itself is an ulp short: 1,9 + (0,7 + 0,7 + 0,7) = 3,9999999999999996
    assert 1.9 + ((0.7 + 0.7) + 0.7) < 4.0
    res = ore_intervals(unit_line([1.9, 0.7, 0.7, 0.7, 3.0, 3.0]), soft(), events=True)
    assert brief(res) == [("ore", 0.0, 6.0)]
    upper = events_of(res, samples=(1, 4), side="upper")[0]
    assert upper["decision"] == "pass" and upper["c"] < 1.0


def test_stages_use_the_units_of_the_previous_stage() -> None:
    """Т8.2-12 without step 3 (derived): stage 1 rejects the прирезка 14–17 (0,6) taken over the unit 14 and
    builds 4–14 and 18–22; stage 2 joins them over the units of stage 1. Step 4 adds the chain 29–33."""
    ex = EXAMPLES["T8.2-12"]
    res = ore_intervals(example_lines("T8.2-12"), example_params("T8.2-12", "soft"), events=True)
    check_rows(res, ex["expect"]["soft"]["intervals"])
    assert brief(res, "ore") == [("ore", 3.0, 22.0), ("ore", 28.0, 33.0)]
    assert check_protocol(res, ex["protocol"], ("rejected_c",)) == 2
    log = stage_log(res)
    assert log == [
        (1, "joined", 4, 14), (1, "joined", 18, 22), (1, "demoted", 29, 29),
        (2, "joined", 4, 22), (2, "demoted", 33, 33),
    ]  # fmt: skip
    gaps = events_of(res, kind="gap")  # 23–28: 6 м > Мпп = 5 м; after sample 29 went to waste — 23–32
    assert [(e["stage"], e["row0"] + 1, e["row1"] + 1, e["decision"]) for e in gaps] == [
        (1, 23, 28, "rejected_m"), (2, 23, 32, "rejected_m"),
    ]  # fmt: skip
    up = events_of(res, samples=(4, 17), side="upper", stage=2)[0]
    low = events_of(res, samples=(15, 22), side="lower", stage=2)[0]
    assert up["decision"] == low["decision"] == "pass" and close(up["c"], 26.3 / 14) and close(low["c"], 15.0 / 8)
    layer = events_of(res, kind="layer")  # what step 3 would cut: 12–17, 6 м @ 0,6 — not checked here
    assert [(e["row0"] + 1, e["row1"] + 1, e["decision"]) for e in layer] == [(12, 17, "not_checked")]
    assert close(layer[0]["c"], 0.6) and close(layer[0]["m"], 6.0)


def test_examples_before_step_3() -> None:
    """Т8.2-15 before step 3 — 0–8 м @ 1,225; Т8.2-17 «смягчённый» — one ore 0–35,5 м @ 1,957746…, MC 69,5."""
    res = ore_intervals(example_lines("T8.2-15"), example_params("T8.2-15", "soft"), events=True)
    assert brief(res) == [("ore", 0.0, 8.0)]
    check_rows(res, EXAMPLES["T8.2-15"]["expect"]["soft"]["intervals"])
    assert stage_log(res) == [(1, "joined", 1, 8)]  # 1–4 and 4–8 in one stage: the triples share unit 4
    layer = events_of(res, kind="layer")
    assert [(e["from"], e["to"], e["decision"]) for e in layer] == [(2.0, 6.0, "not_checked")]
    assert close(layer[0]["c"], 0.95)

    res = ore_intervals(example_lines("T8.2-17"), example_params("T8.2-17", "soft"), events=True)
    check_rows(res, EXAMPLES["T8.2-17"]["expect"]["soft"]["intervals"])
    assert len(res) == 1 and close(res.rows()[0]["c"], 69.5 / 35.5) and res.rows()[0]["n_samples"] == 9
    assert close(res.rows()[0]["waste_len"], 24.0)
    layer = events_of(res, kind="layer")
    assert layer and all(e["decision"] == "not_checked" for e in layer)
    assert close(layer[0]["m"], 14.3) and close(layer[0]["c"], 17.5 / 14.3)


def test_waste_at_the_limit_of_m_waste_max() -> None:
    """M(Б) = Мпп joins (also an ulp short of it); M(Б) > Мпп is rejected and named in the events."""
    grades = [3.0, 3.0, 0.5, 0.5, 0.5, 3.0, 3.0]
    assert brief(ore_intervals(unit_line(grades), soft())) == [("ore", 0.0, 7.0)]
    res = ore_intervals(unit_line(grades), soft(m_waste_max=2.0), events=True)
    assert brief(res) == [("ore", 0.0, 2.0), ("waste", 2.0, 5.0), ("ore", 5.0, 7.0)]
    assert [r["basis"] for r in res.rows()] == ["mc", "", "mc"] and res.ore_id.tolist() == [1, 0, 2]
    gap = events_of(res, kind="gap")
    assert len(gap) == 1 and (gap[0]["decision"], gap[0]["compared"], gap[0]["m"]) == ("rejected_m", "m_waste_max", 3.0)
    assert not events_of(res, kind="trim")
    hole = [(float(k), k + 1.0, g, 0.7) for k, g in enumerate(grades)]  # 0,7 × 3 = 2,0999999999999996
    lines = make_lines([hole], thick=True)
    assert brief(ore_intervals(lines, soft(m_waste_max=2.1))) == [("ore", 0.0, 7.0)]
    assert len(ore_intervals(lines, soft(m_waste_max=2.1 * (1 - 1e-8)))) == 3


def test_subgrade_ore_becomes_waste_and_is_bridged() -> None:
    """Некондиционная руда → безрудная: sample 5 (1,1; MC 1,1 < МС) cannot be joined from either side, merges
    with the waste 4 and 6, and stage 2 joins the ore above and below through the waste 4–6."""
    grades = [4.0, 4.0, 4.0, 0.1, 1.1, 0.1, 4.0, 4.0, 4.0]
    res = ore_intervals(unit_line(grades), soft(m_waste_max=5.0), events=True)
    assert brief(res) == [("ore", 0.0, 9.0)]
    row = res.rows()[0]
    assert close(row["mc"], 25.3) and close(row["waste_len"], 2.0)
    assert stage_log(res) == [(1, "demoted", 5, 5), (2, "joined", 1, 9)]
    assert ou.CLASSES[res.sample_cls[4]] == "ore"  # inside an ore interval a sample is named by its own grade
    demoted = events_of(res, decision="demoted")[0]
    assert demoted["compared"] == "k" and close(demoted["mc"], 1.1)
    waste = events_of(res, step=2, kind="unit", ore=False)[0]
    assert (waste["row0"], waste["row1"]) == (3, 5) and close(waste["m"], 3.0) and close(waste["mc"], 1.3)
    # with Мпп = 2 м the waste 4–6 (3 м) is too long: two intervals, sample 5 is subgrade
    res = ore_intervals(unit_line(grades), soft(m_waste_max=2.0))
    assert brief(res) == [
        ("ore", 0.0, 3.0), ("waste", 3.0, 4.0), ("subgrade", 4.0, 5.0), ("waste", 5.0, 6.0), ("ore", 6.0, 9.0),
    ]  # fmt: skip


def test_unit_below_a_merge_waits_for_the_next_stage() -> None:
    """From the top down: sample 5 goes to waste at stage 1; sample 7 (2,9; MC < МС) is not dropped with it —
    stage 2 tries it through the merged waste 4–6 and joins. The same sequence as И2, сл. 17, where the check
    of stage 2 fails."""
    grades = [3.0, 3.0, 3.0, 0.9, 1.05, 0.2, 2.9]
    res = ore_intervals(unit_line(grades), soft(), events=True)
    assert stage_log(res) == [(1, "demoted", 5, 5), (2, "joined", 1, 7)]
    assert brief(res) == [("ore", 0.0, 7.0)] and close(res.rows()[0]["mc"], 14.05)
    tried = [(e["stage"], e["side"], e["row0"] + 1, e["row1"] + 1, e["decision"]) for e in events_of(res, kind="trim")]
    assert tried == [
        (1, "upper", 1, 4, "pass"), (1, "lower", 4, 5, "rejected_c"),
        (1, "upper", 5, 6, "rejected_c"), (1, "lower", 6, 7, "pass"),
        (2, "upper", 1, 6, "pass"), (2, "lower", 4, 7, "pass"),
    ]  # fmt: skip
    # three in a row: 5 goes to waste, 7 waits, 9 goes to waste at stage 1; 7 — at stage 2
    res = ore_intervals(unit_line([3.0, 3.0, 3.0, 0.1, 1.1, 0.1, 1.1, 0.1, 1.1, 0.1]), soft(), events=True)
    assert stage_log(res) == [(1, "demoted", 5, 5), (1, "demoted", 9, 9), (2, "demoted", 7, 7)]
    assert brief(res, "ore") == [("ore", 0.0, 3.0)]
    assert [ou.CLASSES[c] for c in res.sample_cls[3:]] == ["waste", "subgrade"] * 3 + ["waste"]


def test_soft_matches_the_reference_on_hard_data() -> None:
    holes = _random_holes(2468, 500)
    for params in (soft(), soft(m_waste_max=5.0, m_min=4.0, mc_min=6.0), soft(cutoff=0.6, c_min=1.0, m_min=2.0)):
        p = params.checked()
        res = ore_intervals(make_lines(holes), p, events=True)
        seen = check_reference(res, holes, p)
        assert sum(seen[cls] for cls in ("ore", "subgrade", "waste")) == len(res) > 2000
        decisions = 0
        for hole in holes:
            for line in ref_lines(hole):
                ls = [hole[i][1] - hole[i][0] for i in line]
                gs = [hole[i][2] for i in line]
                found, log = ref_soft(ls, gs, p)
                decisions += len(log)
                for a, b in found:  # at the end every ore unit meets К, begins and ends with an ore sample
                    assert ref_k(ls, gs, a, b, p) and gs[a] >= p.cutoff - 1e-9 and gs[b - 1] >= p.cutoff - 1e-9
        stages = events_of(res, step=2)
        assert len([e for e in stages if e["kind"] != "unit" or e["decision"]]) == decisions
        assert max(e["stage"] for e in stages) >= 3


def test_invariants_and_threads() -> None:
    """Σ M of the rows = the length of the lines; MC of an interval = Σ l·g; ore rows meet К; 1, 2 and 4
    threads give the same bytes."""
    holes = _random_holes(97531, 9000)
    lines = make_lines(holes)
    p = soft(m_waste_max=4.0)
    res = ore_intervals(lines, p)
    length = lines.to - lines.frm
    in_line = res.sample_state == ou.ROW_PIECE
    assert close(float(res.m.sum()), float(length[in_line].sum()), 1e-10)
    assert close(float(res.mc.sum()), float((length * lines.grade)[in_line].sum()), 1e-10)
    ore = res.cls == ou.CLS_ORE
    assert ore.sum() > 3000 and np.all(res.c[ore] >= 1.0 - 1e-9)
    assert np.all((res.m[ore] >= 3.0 - 1e-9) == (res.basis[ore] == ou.BASIS_THICKNESS))
    assert np.all((res.mc[ore] >= 3.0 - 1e-9) | (res.basis[ore] == ou.BASIS_THICKNESS))
    assert np.all(lines.grade[res.row0[ore]] >= 1.0) and np.all(lines.grade[res.row1[ore]] >= 1.0)
    assert np.all(res.waste_len[~ore] == 0.0)
    for threads in (2, 4):
        other = ore_intervals(lines, p, threads=threads)
        assert other.iv_i.tobytes() == res.iv_i.tobytes() and other.iv_f.tobytes() == res.iv_f.tobytes()
        assert other.sample_cls.tobytes() == res.sample_cls.tobytes()
        assert other.sample_ore_id.tobytes() == res.sample_ore_id.tobytes()


def test_long_line_of_poor_ore() -> None:
    """20 000 alternating units, every ore sample is некондиционная руда: the stages end, nothing is ore."""
    n = 20_000
    res = ore_intervals(unit_line([1.05, 0.2] * (n // 2)), soft())
    assert len(res) == n and not np.any(res.cls == ou.CLS_ORE)
    assert int((res.cls == ou.CLS_SUBGRADE).sum()) == n // 2
    rich = ore_intervals(unit_line([5.0, 0.9] * (n // 2)), soft())  # every triple passes: one interval
    assert brief(rich) == [("ore", 0.0, n - 1.0), ("waste", n - 1.0, float(n))]
    assert math.isclose(rich.rows()[0]["mc"], 5.0 * (n // 2) + 0.9 * (n // 2 - 1), rel_tol=1e-12)
