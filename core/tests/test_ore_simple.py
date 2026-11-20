"""Д-26, steps 0–1 and the «упрощённый» mode (ТЗ 7.35, Т8.2-13; ADR 0018 §5, §8): sampling lines, missing
values, elementary units, the sums and the comparisons at the limits, условие К, the growth, the events."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites import ore_units as ou
from bf_core.composites.ore_intervals import OreError, OreLines, OreParams, ore_intervals
from tests.oreutil import (
    EXAMPLES,
    brief,
    check_protocol,
    check_rows,
    close,
    events_of,
    example_lines,
    example_params,
    make_lines,
    ref_lines,
    ref_rows,
    ref_simple,
    sums,
    unit_line,
)


def simple(**over: object) -> OreParams:
    return OreParams(**{"cutoff": 1.0, "m_min": 3.0, "m_waste_max": 3.0, "mode": "simple", **over})  # type: ignore[arg-type]


def test_t8_2_13_simple() -> None:
    """И2, сл. 16: one ore 0–7 м @ 1,057142857…, MC 7,4; the fragment 2–6 м @ 0,975 > Мпп is not checked."""
    ex = EXAMPLES["T8.2-13"]
    res = ore_intervals(example_lines("T8.2-13"), example_params("T8.2-13", "simple"), events=True)
    assert brief(res) == [("ore", 0.0, 7.0)]
    check_rows(res, ex["expect"]["simple"]["intervals"])
    row = res.rows()[0]
    assert close(row["c"], 7.4 / 7.0) and close(row["mc"], 7.4) and close(row["m"], 7.0)
    assert (row["n_samples"], row["ore_id"], row["flag"]) == (7, 1, 0) and close(row["waste_len"], 3.0)
    names = [ou.CLASSES[c] for c in res.sample_cls]
    assert names == ["ore", "ore", "waste_in", "ore", "waste_in", "waste_in", "ore"]
    assert res.sample_ore_id.tolist() == [1] * 7

    assert check_protocol(res, ex["protocol"], ("joined", "not_checked")) == 3
    layers = events_of(res, kind="layer")
    assert len(layers) == 1 and layers[0]["step"] == 3 and layers[0]["decision"] == "not_checked"
    assert (layers[0]["from"], layers[0]["to"], layers[0]["compared"]) == (2.0, 6.0, "m_waste_max")
    assert close(layers[0]["c"], 0.975) and layers[0]["m"] > example_params("T8.2-13", "simple").m_waste_max
    units = events_of(res, step=1)
    assert [(u["seq"], u["ore"], u["row0"], u["row1"]) for u in units] == [
        (1, True, 0, 1), (2, False, 2, 2), (3, True, 3, 3), (4, False, 4, 5), (5, True, 6, 6),
    ]  # fmt: skip
    final = events_of(res, step=5)
    assert len(final) == 1 and final[0]["decision"] == "ore" and final[0]["compared"] == "m_min"


def test_events_do_not_change_the_result() -> None:
    lines = example_lines("T8.2-12")
    a = ore_intervals(lines, example_params("T8.2-12", "simple"))
    b = ore_intervals(lines, example_params("T8.2-12", "simple"), events=True)
    assert a.events is None and b.events is not None and len(b.events) > 0
    assert a.iv_i.tobytes() == b.iv_i.tobytes() and a.iv_f.tobytes() == b.iv_f.tobytes()
    assert a.sample_cls.tobytes() == b.sample_cls.tobytes()


def test_grade_equal_to_cutoff_is_ore() -> None:
    """g = Сб — ore; the comparison holds within 1e−9 towards equality and not further."""
    res = ore_intervals(unit_line([1.0, 1.0, 1.0]), simple())
    assert brief(res) == [("ore", 0.0, 3.0)] and res.rows()[0]["basis"] == "thickness"
    res = ore_intervals(unit_line([1.0 - 5e-10, 1.0, 1.0 - 5e-10]), simple())
    assert brief(res) == [("ore", 0.0, 3.0)]
    res = ore_intervals(unit_line([1.0 - 1e-8, 1.0 - 1e-8, 1.0 - 1e-8]), simple())
    assert brief(res) == [("waste", 0.0, 3.0)]
    # 1.2 + 0.7 + 0.7 + 1.4 is 3.9999999999999996 in float64: C of the grown interval equals Сб — joined
    grades = [1.2, 0.7, 0.7, 1.4]
    assert ((1.2 + 0.7) + 0.7) + 1.4 < 4.0
    res = ore_intervals(unit_line(grades), simple(), events=True)
    assert brief(res) == [("ore", 0.0, 4.0)] and len(events_of(res, decision="joined")) == 1
    assert res.rows()[0]["c"] < 1.0


def test_thickness_and_mc_at_the_limits() -> None:
    """M = Мрт with float noise is «по мощности»; MC = МС is «по метропроценту»; below both — subgrade."""
    hole = [(float(k), k + 1.0, 2.0, 0.7) for k in range(3)]  # thickness 0,7 м × 3 = 2,0999999999999996
    assert 0.7 + 0.7 + 0.7 < 2.1
    res = ore_intervals(make_lines([hole], thick=True), simple(m_min=2.1, mc_min=1e9))
    assert [(r["cls"], r["basis"]) for r in res.rows()] == [("ore", "thickness")] and res.rows()[0]["m"] < 2.1
    res = ore_intervals(make_lines([hole], thick=True), simple(m_min=2.1 * (1 + 1e-8), mc_min=1e9))
    assert [r["cls"] for r in res.rows()] == ["subgrade"]
    res = ore_intervals(unit_line([1.5, 1.5]), simple())  # MC = 3,0 = МС = Сб·Мрт
    assert [(r["cls"], r["basis"]) for r in res.rows()] == [("ore", "mc")]
    res = ore_intervals(unit_line([1.4, 1.5]), simple())  # MC = 2,9 < МС, M = 2 < Мрт
    assert [(r["cls"], r["basis"]) for r in res.rows()] == [("subgrade", "")]
    assert [ou.CLASSES[c] for c in res.sample_cls] == ["subgrade", "subgrade"]
    assert res.sample_ore_id.tolist() == [0, 0]


def test_c_min_is_part_of_k() -> None:
    """Смин: an interval with C < Смин is not conditional; by default Смин = Сб."""
    lines = unit_line([1.2, 1.2, 1.2])
    assert brief(ore_intervals(lines, simple())) == [("ore", 0.0, 3.0)]
    assert brief(ore_intervals(lines, simple(c_min=1.2))) == [("ore", 0.0, 3.0)]
    assert brief(ore_intervals(lines, simple(c_min=1.3))) == [("subgrade", 0.0, 3.0)]
    p = simple().checked()
    assert p.c_min == 1.0 and p.mc_min == 3.0


def test_nan_breaks_the_line_and_is_not_zero() -> None:
    """A sample without a grade ends the sampling line: as zero it would give one interval 0–6 м @ 1,0."""
    grades = [1.2, 1.2, math.nan, 1.2, 1.2, 1.2]
    res = ore_intervals(unit_line(grades), simple(), events=True)
    assert brief(res) == [("subgrade", 0.0, 2.0), ("ore", 3.0, 6.0)]
    assert res.sample_cls[2] == ou.CLS_NONE and res.sample_state[2] == ou.ROW_MISSING
    assert res.stats["missing"] == 1 and res.stats["lines"] == 2 and res.stats["breaks"] == 1
    assert [r["line"] for r in res.rows()] == [0, 1]
    breaks = events_of(res, step=0)
    assert len(breaks) == 1 and breaks[0]["decision"] == "break" and breaks[0]["compared"] == "no_value"
    assert (breaks[0]["from"], breaks[0]["to"]) == (2.0, 3.0)
    assert brief(ore_intervals(unit_line([1.2, 1.2, 0.0, 1.2, 1.2, 1.2]), simple())) == [("ore", 0.0, 6.0)]


def test_missing_as_waste_only_by_choice() -> None:
    """«Пустая порода до L_н»: a missing sample not longer than L_н stays in the line as waste, flagged."""
    lines = unit_line([1.2, 1.2, math.nan, 1.2, 1.2, 1.2])
    res = ore_intervals(lines, simple(missing="waste", missing_max=1.0), events=True)
    assert brief(res) == [("ore", 0.0, 6.0)]
    row = res.rows()[0]
    assert row["flag"] == 1 and close(row["mc"], 6.0) and close(row["m"], 6.0) and close(row["waste_len"], 1.0)
    assert res.sample_cls[2] == ou.CLS_WASTE_IN and res.sample_state[2] == ou.ROW_BRIDGED
    assert res.stats["bridged"] == 1 and len(events_of(res, decision="missing_waste")) == 1
    res = ore_intervals(lines, simple(missing="waste", missing_max=0.5), events=True)
    assert brief(res) == [("subgrade", 0.0, 2.0), ("ore", 3.0, 6.0)]
    assert events_of(res, step=0)[0]["compared"] == "missing_max" and res.sample_state[2] == ou.ROW_MISSING
    # missing samples at the ends of a hole never join a line
    res = ore_intervals(unit_line([math.nan, 1.2, 1.2, 1.2, math.nan]), simple(missing="waste", missing_max=5.0))
    assert brief(res) == [("ore", 1.0, 4.0)] and res.rows()[0]["flag"] == 0


def test_gap_breaks_the_line() -> None:
    hole = [(0.0, 1.0, 1.2), (1.0, 2.0, 1.2), (3.0, 4.0, 1.2), (4.0, 5.0, 1.2), (5.0, 6.0, 1.2)]
    res = ore_intervals(make_lines([hole]), simple(), events=True)
    assert brief(res) == [("subgrade", 0.0, 2.0), ("ore", 3.0, 6.0)]
    assert events_of(res, step=0)[0]["compared"] == "gap"
    res = ore_intervals(make_lines([hole]), simple(missing="waste", missing_max=1.0))
    row = res.rows()[0]
    assert brief(res) == [("ore", 0.0, 6.0)] and (row["flag"], row["n_samples"]) == (1, 5)
    assert close(row["m"], 6.0) and close(row["mc"], 6.0) and close(row["waste_len"], 1.0)
    # two adjacent missing stretches are one run: 1 м without samples + 1 м without a grade > L_н = 1,5 м
    hole2 = [(0.0, 1.0, 1.2), (1.0, 2.0, 1.2), (3.0, 4.0, math.nan), (4.0, 5.0, 1.2), (5.0, 6.0, 1.2), (6.0, 7.0, 1.2)]
    res = ore_intervals(make_lines([hole2]), simple(missing="waste", missing_max=1.5))
    assert brief(res) == [("subgrade", 0.0, 2.0), ("ore", 4.0, 7.0)]
    res = ore_intervals(make_lines([hole2]), simple(missing="waste", missing_max=2.0))
    assert brief(res) == [("subgrade", 0.0, 2.0), ("waste", 2.0, 4.0), ("ore", 4.0, 7.0)]  # 6,0 / 7 < Сб
    waste = res.rows()[1]
    assert (waste["flag"], waste["n_samples"], waste["m"], waste["mc"]) == (1, 1, 2.0, 0.0)
    assert res.stats["lines"] == 1 and res.stats["bridged"] == 2


def test_domains_split_the_line() -> None:
    hole = [(float(k), k + 1.0, 1.5, d) for k, d in enumerate([0, 0, 0, 1, 1, 1])]
    assert brief(ore_intervals(make_lines([hole]), simple())) == [("ore", 0.0, 6.0)]
    res = ore_intervals(make_lines([hole], domain=True), simple(), events=True)
    assert brief(res) == [("ore", 0.0, 3.0), ("ore", 3.0, 6.0)] and res.ore_id.tolist() == [1, 2]
    assert events_of(res, step=0)[0]["compared"] == "domain"
    outside = [(float(k), k + 1.0, 1.5, d) for k, d in enumerate([0, 0, 0, -1, 0, 0, 0])]
    res = ore_intervals(make_lines([outside], domain=True), simple())
    assert brief(res) == [("ore", 0.0, 3.0), ("ore", 4.0, 7.0)]
    assert res.sample_cls[3] == ou.CLS_NONE and res.sample_state[3] == ou.ROW_NO_DOMAIN
    assert res.stats["no_domain"] == 1


def test_thickness_from_a_field() -> None:
    """Мощность — a field of the samples: M and MC use it, FROM and TO stay depths along the hole."""
    hole = [(0.0, 2.0, 2.0, 1.0), (2.0, 4.0, 2.0, 1.0), (4.0, 6.0, 2.0, 1.0)]
    by_length = ore_intervals(make_lines([hole]), simple(m_min=3.5)).rows()[0]
    assert (by_length["basis"], by_length["m"], by_length["mc"]) == ("thickness", 6.0, 12.0)
    by_field = ore_intervals(make_lines([hole], thick=True), simple(m_min=3.5)).rows()[0]
    assert (by_field["basis"], by_field["m"], by_field["mc"]) == ("mc", 3.0, 6.0)
    assert (by_field["from"], by_field["to"]) == (0.0, 6.0)
    hole[1] = (2.0, 4.0, 2.0, math.nan)  # no thickness — the sample is missing, the line ends
    res = ore_intervals(make_lines([hole], thick=True), simple(m_min=3.5))
    assert res.stats["missing"] == 1 and len(res) == 2 and res.sample_cls[1] == ou.CLS_NONE


def test_bad_and_overlapping_rows_are_counted() -> None:
    hole = [
        (0.0, 1.0, 1.2),
        (1.0, 1.0, 9.0),  # TO = FROM
        (1.0, 2.0, 1.2),
        (1.5, 2.5, 9.0),  # overlaps the previous sample
        (math.nan, 3.0, 9.0),  # no depth
        (2.0, 3.0, 1.2),
    ]
    res = ore_intervals(make_lines([hole]), simple())
    assert brief(res) == [("ore", 0.0, 3.0)] and close(res.rows()[0]["mc"], 3.6)
    assert (res.stats["bad"], res.stats["overlap"]) == (2, 1)
    assert res.sample_state.tolist() == [0, ou.ROW_BAD, 0, ou.ROW_OVERLAP, ou.ROW_BAD, 0]
    assert res.sample_cls.tolist() == [0, -1, 0, -1, -1, 0]


def _random_holes(seed: int, holes: int) -> list[list[tuple[float, float, float]]]:
    """Hard data: grades at the cutoff, missing grades, gaps, zero-length rows, lengths that do not sum exactly."""
    rng = np.random.default_rng(seed)
    grades = [0.2, 0.6, 1.0, 1.0, 1.4, 3.0, math.nan]
    lengths = [0.5, 1.0, 1.5, 2.3, 0.1]
    out = []
    for _ in range(holes):
        depth = float(rng.choice([0.0, 12.5]))
        hole = []
        for _ in range(int(rng.integers(0, 40))):
            if rng.random() < 0.05:
                depth += float(rng.choice(lengths))
            if rng.random() < 0.03:
                hole.append((depth, depth, 5.0))
                continue
            top = depth
            depth += float(rng.choice(lengths))
            hole.append((top, depth, float(rng.choice(grades))))
        out.append(hole)
    return out


def test_simple_matches_the_reference_on_hard_data() -> None:
    holes = _random_holes(12345, 400)
    p = simple().checked()
    res = ore_intervals(make_lines(holes), p)
    got = res.rows()
    first = 0
    n = 0
    for h, hole in enumerate(holes):
        for line in ref_lines(hole):
            ls = [hole[i][1] - hole[i][0] for i in line]
            gs = [hole[i][2] for i in line]
            for cls, a, b, basis in ref_rows(ls, gs, ref_simple(ls, gs, p), p):
                row = got[n]
                n += 1
                m, mc = sums(ls, gs, a, b)
                assert (row["hole"], row["cls"], row["basis"]) == (h, cls, basis)
                assert (row["row0"], row["row1"]) == (first + line[a], first + line[b - 1])
                assert (row["from"], row["to"]) == (hole[line[a]][0], hole[line[b - 1]][1])
                assert close(row["m"], m) and close(row["mc"], mc)
        first += len(hole)
    assert n == len(got) and n > 1500
    assert {r["cls"] for r in got} == {"ore", "subgrade", "waste"}


def test_invariants() -> None:
    """Т8.2-19 for one strip: MC of an interval = Σ l·g of its samples, Σ M of the rows = the length of the
    lines, ore intervals are numbered 1…k in a hole, begin and end with ore samples."""
    holes = _random_holes(777, 300)
    lines = make_lines(holes)
    res = ore_intervals(lines, simple())
    length = lines.to - lines.frm
    in_line = res.sample_state == ou.ROW_PIECE
    assert close(float(res.m.sum()), float(length[in_line].sum()), 1e-11)
    assert close(float(res.mc.sum()), float((length * lines.grade)[in_line].sum()), 1e-11)
    assert np.all((res.sample_cls >= 0) == in_line)
    for r in res.rows():
        rows = slice(r["row0"], r["row1"] + 1)
        mine = in_line[rows]  # a row that does not take part (TO = FROM) may lie between the samples
        assert close(r["mc"], float((length[rows] * lines.grade[rows])[mine].sum()))
        if r["cls"] == "ore":
            assert lines.grade[r["row0"]] >= 1.0 and lines.grade[r["row1"]] >= 1.0
            assert np.all(res.sample_ore_id[rows][mine] == r["ore_id"])
    for h in range(len(holes)):
        ids = res.ore_id[(res.hole == h) & (res.cls == ou.CLS_ORE)].tolist()
        assert ids == list(range(1, len(ids) + 1))
    assert np.all(res.sample_ore_id[res.sample_cls > ou.CLS_WASTE_IN] == 0)


def test_params_are_checked() -> None:
    for bad in (
        {"cutoff": 0.0},
        {"cutoff": math.nan},
        {"m_min": -1.0},
        {"m_waste_max": math.inf},
        {"mc_min": -3.0},
        {"c_min": "много"},
        {"mode": "any"},
        {"missing": "zero"},
        {"missing_max": -1.0},
    ):
        with pytest.raises(OreError) as err:
            ore_intervals(unit_line([1.0]), simple(**bad))
        assert err.value.code == "ore_bad_param" and err.value.message and err.value.hint
        assert any("а" <= ch <= "я" for ch in err.value.message.lower())
    with pytest.raises(OreError) as err:
        OreLines.of([0, 3], [0.0, 1.0], [1.0, 2.0], [1.0, 1.0])
    assert err.value.code == "ore_bad_lines"


def test_strips_threads_and_cancel() -> None:
    """Strips of 4096 holes: the same bytes with 1, 2 and 4 threads; tick after every strip; a raise cancels."""
    holes = _random_holes(4242, 9000)
    lines = make_lines(holes)
    one = ore_intervals(lines, simple())
    seen: list[float] = []
    for threads in (2, 4):
        seen.clear()
        res = ore_intervals(lines, simple(), threads=threads, tick=seen.append)
        assert res.iv_i.tobytes() == one.iv_i.tobytes() and res.iv_f.tobytes() == one.iv_f.tobytes()
        assert res.sample_cls.tobytes() == one.sample_cls.tobytes() and res.stats == one.stats
        assert res.sample_ore_id.tobytes() == one.sample_ore_id.tobytes()
        assert seen == sorted(seen) and len(seen) == 3 and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    def stop(_: float) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        ore_intervals(lines, simple(), threads=2, tick=stop)
    empty = ore_intervals(OreLines.of([0], [], [], []), simple(), events=True)
    assert len(empty) == 0 and empty.events is not None and len(empty.events) == 0 and empty.stats["lines"] == 0
