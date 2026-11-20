"""Д-26, the analysis of conditions (ТЗ 7.35 «Анализ кондиций», Т8.2-20; ADR 0018 §12, решения 23, 27): every
cell of the table Сб × Мрт × Мпп is a separate run of the kernel; the means of И1, сл. 24; the grade of the
прирезка between neighbouring cutoffs and the mark «не 10–15 %»; samples left out; threads and cancellation."""

from __future__ import annotations

import json
import math
from dataclasses import replace

import numpy as np
import pytest

from bf_core.composites import ore_units as ou
from bf_core.composites.ore_conditions import (
    MAX_VARIANTS,
    conditions_analysis,
    exclusion,
    neighbours,
    variant_row,
    warm,
)
from bf_core.composites.ore_intervals import READY_MODES, OreError, OreLines, OreParams, ore_intervals
from bf_core.composites.ore_set import run_set
from tests.oreutil import EXAMPLES, close, example_lines, make_lines
from tests.test_ore_simple import _random_holes

EX = EXAMPLES["T8.2-20"]
KEYS = ("count", "m_sum", "m_mean", "c_mean", "mc_sum")


def separate(lines: OreLines, p: OreParams, threads: int = 1) -> tuple[object, ...]:
    """The row of the class `ore` of the summary of a separate run of `composite.ore_intervals`."""
    ore = [e for e in run_set(lines, p, threads=threads).summary["classes"] if e["cls"] == "ore"]
    if not ore:
        return (0, 0.0, None, None, 0.0)
    return (ore[0]["count"], ore[0]["m"], ore[0]["m_mean"], ore[0]["c"], ore[0]["mc"])


@pytest.mark.parametrize("mode", ["strict_excl", "strict", "soft", "simple"])
def test_t8_2_20_every_cell_is_a_separate_run(mode: str) -> None:
    """Т8.2-20: the data of Т8.2-12, Сб {0,5; 1; 1,5} × Мрт {1; 3} × Мпп {3; 5} — 12 variants, each one equal
    (exactly) to its own run; МС = Сб·Мрт, Смин = Сб."""
    lines = example_lines(EX["data"])
    res = conditions_analysis(lines, EX["cutoffs"], EX["m_min"], EX["m_waste_max"], mode=mode)
    assert len(res.variants) == 12 and res.mode == mode
    assert (res.cutoffs, res.m_min, res.m_waste_max) == ((0.5, 1.0, 1.5), (1.0, 3.0), (3.0, 5.0))
    order = [(v["m_min"], v["m_waste_max"], v["cutoff"]) for v in res.variants]
    assert order == sorted(order) and len(set(order)) == 12  # the groups (Мрт, Мпп) of the form, Сб ascending
    for v in res.variants:
        p = OreParams(v["cutoff"], v["m_min"], v["m_waste_max"], mode=mode)
        assert tuple(v[k] for k in KEYS) == separate(lines, p), v
        assert (v["mc_min"], v["c_min"]) == (v["cutoff"] * v["m_min"], v["cutoff"])
        rows = [r for r in ore_intervals(lines, p).rows() if r["cls"] == "ore"]  # and from the rows themselves
        assert v["count"] == len(rows) and close(v["m_sum"], sum(r["m"] for r in rows))
        assert close(v["mc_sum"], sum(r["mc"] for r in rows))
        if rows:
            assert close(v["c_mean"], v["mc_sum"] / v["m_sum"]) and close(v["m_mean"], v["m_sum"] / v["count"])
    json.dumps(res.as_dict(), allow_nan=False)
    assert res.as_dict()["samples"] == 34 and res.stats == {"samples": 34, "holes": 1, "excluded": 0}


def test_t8_2_20_the_example_itself() -> None:
    """The variant of Т8.2-12 (Сб 1, Мрт 3, Мпп 5): 3 intervals, Σ M 18, Σ MC 43,0, C 2,3888…, mean M 6; a lower
    cutoff adds ore: Σ M does not fall, the grade of the прирезка lies below the upper cutoff."""
    res = conditions_analysis(example_lines("T8.2-12"), EX["cutoffs"], EX["m_min"], EX["m_waste_max"])
    cell = next(v for v in res.variants if (v["cutoff"], v["m_min"], v["m_waste_max"]) == (1.0, 3.0, 5.0))
    assert (cell["count"], cell["m_sum"], cell["m_mean"]) == (3, 18.0, 6.0)
    assert close(cell["mc_sum"], 43.0) and close(cell["c_mean"], 43.0 / 18.0)
    for a, b in zip(res.variants, res.variants[1:], strict=False):
        if (a["m_min"], a["m_waste_max"]) != (b["m_min"], b["m_waste_max"]):
            assert (b["step_pct"], b["c_trim"], b["step_flag"], b["mark"]) == (None, None, False, None)
            continue
        assert a["cutoff"] < b["cutoff"] and a["m_sum"] >= b["m_sum"]
        assert close(b["step_pct"], (a["m_sum"] - b["m_sum"]) / a["m_sum"] * 100.0)
        if a["m_sum"] > b["m_sum"]:
            assert close(b["c_trim"], (a["mc_sum"] - b["mc_sum"]) / (a["m_sum"] - b["m_sum"]))
            assert b["c_trim"] < b["cutoff"]
        else:
            assert b["c_trim"] is None
        assert b["step_flag"] == (not 10.0 <= b["step_pct"] <= 15.0) and (b["mark"] is not None) == b["step_flag"]
    assert res.variants[0]["step_pct"] is None and res.variants[0]["mark"] is None


def test_means_and_neighbours_of_the_source() -> None:
    """И1, сл. 24 (Мрт 1, Мпп 3): 17424 / 3432 = 5,08 and 3432 / 610 = 5,63; сл. 25–26 on its row of cutoffs:
    the steps of Σ M outside 10–15 % are marked, the grade of every прирезка lies between its two cutoffs."""
    src = EX["source_check"]
    row = variant_row(0.5, 1.0, 3.0, src["count"], src["sum_m"], src["sum_mc"])
    assert round(row["c_mean"], 2) == src["mean_c"] == 5.08 and round(row["m_mean"], 2) == src["mean_m"] == 5.63
    assert row["c_mean"] == 17424.0 / 3432.0 and row["m_mean"] == 3432.0 / 610
    table = [  # Сб, число, Σ M, Σ MC — the first block of the slide
        (0.5, 610, 3432.0, 17424.0), (0.75, 521, 2866.0, 17101.0), (1.0, 487, 2544.0, 16832.0),
        (1.25, 445, 2274.0, 16543.0), (1.5, 417, 2075.0, 16286.0), (2.0, 365, 1785.0, 15806.0),
    ]  # fmt: skip
    rows = neighbours(variant_row(cb, 1.0, 3.0, n, m, mc) for cb, n, m, mc in reversed(table))
    assert [r["cutoff"] for r in rows] == [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    # the slide rounds its sums: its means are met within a unit of the last digit (7,28 against 7,2748…)
    slide = [(5.08, 5.63), (5.97, 5.5), (6.62, 5.22), (7.28, 5.11), (7.85, 4.98), (8.86, 4.89)]
    for r, (c, m) in zip(rows, slide, strict=True):
        assert abs(r["c_mean"] - c) < 0.011 and abs(r["m_mean"] - m) < 0.011
        assert r["c_mean"] == r["mc_sum"] / r["m_sum"] and r["m_mean"] == r["m_sum"] / r["count"]
    assert [None if r["step_pct"] is None else round(r["step_pct"], 1) for r in rows] == [
        None,
        16.5,
        11.2,
        10.6,
        8.8,
        14.0,
    ]
    assert [r["step_flag"] for r in rows] == [False, True, False, False, True, False]
    assert rows[1]["mark"] == "Σ M отличается от варианта Сб 0,5 на 16,5 % — рекомендуется 10–15 % (И1, сл. 25)"
    assert rows[4]["mark"] == "Σ M отличается от варианта Сб 1,25 на 8,8 % — рекомендуется 10–15 % (И1, сл. 25)"
    assert rows[0]["c_trim"] is None and rows[2]["mark"] is None
    for low, high in zip(rows, rows[1:], strict=False):
        assert close(high["c_trim"], (low["mc_sum"] - high["mc_sum"]) / (low["m_sum"] - high["m_sum"]))
        assert low["cutoff"] < high["c_trim"] < high["cutoff"]  # сл. 26: the control holds on the slide
    # the limits are inside: exactly 10 % and 15 % are not marked; Σ M that grows with the cutoff is
    edge = neighbours([variant_row(1.0, 1.0, 1.0, 5, 100.0, 300.0), variant_row(2.0, 1.0, 1.0, 4, 90.0, 290.0),
                       variant_row(3.0, 1.0, 1.0, 3, 76.5, 270.0), variant_row(4.0, 1.0, 1.0, 3, 80.0, 250.0),
                       variant_row(5.0, 1.0, 1.0, 0, 0.0, 0.0), variant_row(6.0, 1.0, 1.0, 0, 0.0, 0.0)])  # fmt: skip
    assert [r["step_flag"] for r in edge] == [False, False, False, True, True, False]
    assert edge[3]["c_trim"] is None and edge[3]["mark"].startswith("Σ M больше, чем при Сб 3:")
    assert (edge[4]["m_mean"], edge[4]["c_mean"], edge[4]["step_pct"]) == (None, None, 100.0)  # no intervals: None
    assert (edge[5]["step_pct"], edge[5]["c_trim"], edge[5]["mark"]) == (None, None, None)


def test_hard_data_threads_and_separate_runs() -> None:
    """Missing grades, gaps, zero-length rows, ties at the cutoff, a thickness field and domains over several
    strips: every variant equals its separate run exactly, with 1 and 4 threads alike; МС and Смин as given."""
    rng = np.random.default_rng(5)
    holes = _random_holes(99, 9000)
    lines = make_lines(holes)
    n = len(lines.frm)
    lines = replace(lines, thick=(lines.to - lines.frm) * rng.choice([0.5, 1.0], n),
                    domain=np.repeat(rng.integers(-1, 3, n // 7 + 1), 7)[:n].astype(np.int32))  # fmt: skip
    assert len(lines.starts) - 1 > 2 * 4096  # three strips
    cuts, mrt, mpp = [0.6, 1.0, 1.4], [1.5, 3.0], [1.0, 2.5]
    for kw in (
        {"mode": "strict_excl"},
        {"mode": "soft", "missing": "waste", "missing_max": 2.5, "mc_min": 2.0, "c_min": 0.9},
        {"mode": "contacts", "contacts_values": (0, 2)},
    ):
        one = conditions_analysis(lines, cuts, mrt, mpp, threads=1, **kw)  # type: ignore[arg-type]
        four = conditions_analysis(lines, cuts, mrt, mpp, threads=4, **kw)  # type: ignore[arg-type]
        assert one.variants == four.variants and len(one.variants) == 12
        for v in one.variants:
            p = OreParams(v["cutoff"], v["m_min"], v["m_waste_max"], **kw)  # type: ignore[arg-type]
            assert tuple(v[k] for k in KEYS) == separate(lines, p, threads=2), (kw, v)
            assert (v["mc_min"], v["c_min"]) == (p.checked().mc_min, p.checked().c_min)
        assert sum(v["count"] for v in one.variants) > 20000
    assert {v["mc_min"] for v in one.variants} == {cb * m for cb in cuts for m in mrt}  # contacts: МС = Сб·Мрт


def test_exclude_ends_the_line_and_is_never_zero() -> None:
    """Решение 23: a sample left out ends its sampling line — the same as a sample without a grade in the mode
    «разрыв» and as a sample outside the domains; with «пустая порода до L_н» it still is not waste."""
    holes = _random_holes(31, 600)
    lines = make_lines(holes)
    rng = np.random.default_rng(8)
    code = rng.choice(np.array(["Карьер", "Подземка", None, " Карьер "], object), len(lines.frm))
    mask = exclusion(code, ["Карьер"])
    assert mask.tolist() == [c is not None and c.strip() == "Карьер" for c in code.tolist()] and 100 < mask.sum() < len(
        mask
    )
    cuts, mrt, mpp = [0.6, 1.0], [2.0], [2.5]
    got = conditions_analysis(lines, cuts, mrt, mpp, exclude=mask, threads=2)
    assert got.stats["excluded"] == int(mask.sum()) == got.as_dict()["excluded"]
    no_grade = replace(lines, grade=np.where(mask, math.nan, lines.grade))
    outside = replace(lines, domain=np.where(mask, -1, 0).astype(np.int32))
    for other in (no_grade, outside):
        want = conditions_analysis(other, cuts, mrt, mpp)
        assert [[v[k] for k in KEYS] for v in want.variants] == [[v[k] for k in KEYS] for v in got.variants]
    full = conditions_analysis(lines, cuts, mrt, mpp)
    assert all(a["mc_sum"] > b["mc_sum"] for a, b in zip(full.variants, got.variants, strict=True))
    # «пустая порода»: a missing grade is bridged as waste, a sample left out is not
    kw = {"missing": "waste", "missing_max": 3.0}
    bridged = conditions_analysis(lines, cuts, mrt, mpp, exclude=mask, **kw)  # type: ignore[arg-type]
    as_waste = conditions_analysis(no_grade, cuts, mrt, mpp, **kw)  # type: ignore[arg-type]
    as_domain = conditions_analysis(outside, cuts, mrt, mpp, **kw)  # type: ignore[arg-type]
    assert [v["m_sum"] for v in bridged.variants] == [v["m_sum"] for v in as_domain.variants]
    assert [v["m_sum"] for v in bridged.variants] != [v["m_sum"] for v in as_waste.variants]
    # a domain of the samples stays: the excluded ones leave it
    domains = replace(lines, domain=rng.integers(0, 2, len(lines.frm)).astype(np.int32))
    with_dom = conditions_analysis(domains, cuts, mrt, mpp, exclude=mask)
    by_hand = conditions_analysis(
        replace(domains, domain=np.where(mask, -1, domains.domain).astype(np.int32)), cuts, mrt, mpp
    )
    assert with_dom.variants == by_hand.variants and domains.domain.min() == 0  # the lines given are not touched
    assert conditions_analysis(lines, cuts, mrt, mpp, exclude=np.zeros(len(mask), bool)).variants == full.variants


def test_exclusion_by_value_and_text() -> None:
    """`exclude {field, values}`: the values come as typed; a numeric field is compared by value."""
    nan = math.nan
    assert exclusion(np.array([1.0, 1.5, nan, 2.0, 10.0]), ["1,5", " 2 ", "x", "1e1"]).tolist() == [
        False,
        True,
        False,
        True,
        True,
    ]
    assert exclusion(np.array([3, 4, 5], np.int32), ["4.0", "5"]).tolist() == [False, True, True]
    assert exclusion(np.array([1.0, nan]), ["nan", "", "inf"]).tolist() == [False, False]  # no value — never left out
    texts = np.array(["Карьер", None, "карьер", "1", 1, 1.0, "  Карьер"], object)
    assert exclusion(texts, ["Карьер"]).tolist() == [True, False, False, False, False, False, True]
    assert exclusion(texts, ["1"]).tolist() == [False, False, False, True, True, True, False]
    assert exclusion(np.array(["a", "b"]), []).tolist() == [False, False]
    assert exclusion(np.array([], object), ["a"]).tolist() == []


def test_parameters_limits_and_cancel() -> None:
    lines = example_lines("T8.2-12")
    res = conditions_analysis(lines, [1.5, 0.5, 1.5, 1], 3, (5.0, 3.0))  # repeats and order do not matter
    assert (res.cutoffs, res.m_min, res.m_waste_max, len(res.variants)) == ((0.5, 1.0, 1.5), (3.0,), (3.0, 5.0), 6)
    ok = conditions_analysis(lines, np.linspace(0.1, 2.0, 20), [1.0, 2.0], [1.0, 2.0, 3.0, 4.0, 5.0])
    assert len(ok.variants) == MAX_VARIANTS == 200
    for bad, text in (
        ((np.linspace(0.1, 2.1, 21), [1.0, 2.0], [1.0, 2.0, 3.0, 4.0, 5.0]), "Вариантов 210: не больше 200."),
        (([], [1.0], [1.0]), "Бортовые содержания Сб: задайте хотя бы одно значение."),
        (([1.0], ["x"], [1.0]), "Минимальные мощности Мрт: ожидается список чисел."),
        (([1.0, 0.0], [1.0], [1.0]), "Бортовое содержание Сб: значение должно быть больше нуля."),
        (([1.0], [1.0], [math.nan]), "Максимальная мощность пустого прослоя Мпп: значение должно быть не меньше нуля."),
    ):
        with pytest.raises(OreError) as err:
            conditions_analysis(lines, *bad)
        assert (err.value.code, err.value.message) == ("ore_bad_param", text) and err.value.hint
    with pytest.raises(OreError) as err:
        conditions_analysis(lines, [1.0], [1.0], [1.0], mode="best")
    assert err.value.code == "ore_bad_param"
    with pytest.raises(OreError) as err:
        conditions_analysis(lines, [1.0], [1.0], [1.0], mode="contacts", exclude=np.zeros(34, bool))
    assert err.value.code == "ore_no_domain"
    with pytest.raises(OreError) as err:
        conditions_analysis(lines, [1.0], [1.0], [1.0], exclude=[True])
    assert err.value.code == "ore_bad_lines"
    empty = conditions_analysis(make_lines([]), [1.0], [1.0], [1.0])
    assert [(v["count"], v["m_sum"], v["m_mean"], v["c_mean"], v["mc_sum"]) for v in empty.variants] == [
        (0, 0.0, None, None, 0.0)
    ]
    nothing = conditions_analysis(lines, [100.0], [1.0], [1.0]).variants[0]  # no ore: sums 0, means None — not 0
    assert (nothing["count"], nothing["m_sum"], nothing["m_mean"], nothing["c_mean"]) == (0, 0.0, None, None)

    many = make_lines(_random_holes(3, 5000))  # two strips × 4 variants = 8 tasks
    ticks: list[float] = []
    conditions_analysis(many, [0.6, 1.0], [1.0, 2.0], [2.0], tick=ticks.append)
    assert ticks == [k / 8 for k in range(1, 9)]

    def cancel(f: float) -> None:
        ticks.append(f)
        if f >= 0.25:
            raise RuntimeError("cancelled")

    ticks.clear()
    with pytest.raises(RuntimeError, match="cancelled"):
        conditions_analysis(many, [0.6, 1.0], [1.0, 2.0], [2.0], tick=cancel, threads=2)
    assert ticks == [0.125, 0.25]
    assert set(READY_MODES) >= {"strict_excl", "contacts"} and ou.CLASSES[0] == "ore"


def test_warm_runs() -> None:
    warm()
