"""Д-26, steps 4–7 and the examples of the sources (ТЗ 7.35, Т8.2-12, Т8.2-17; ADR 0018 §8, решения 21, 22):
сближенные интервалы, CLASS and BASIS, the nested rows `waste_in` and `frame`, the mode «по контактам»; every
example of the sources in every mode it is written for."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from bf_core.composites import ore_units as ou
from bf_core.composites.ore_intervals import READY_MODES, OreError, OreIntervals, OreParams, ore_intervals
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
    ref_lines,
    ref_mode,
    ref_nested,
    sums,
    unit_line,
)
from tests.test_ore_simple import _random_holes

CASES = [(name, mode) for name, ex in EXAMPLES.items() for mode in ex.get("expect", {})]


def params(**over: object) -> OreParams:
    return OreParams(**{"cutoff": 1.0, "m_min": 3.0, "m_waste_max": 3.0, "mode": "strict_excl", **over})  # type: ignore[arg-type]


def main(res: OreIntervals) -> list[dict[str, object]]:
    return [r for r in res.rows() if not r["nested"]]


def nested(res: OreIntervals) -> list[tuple[str, int, int, int]]:
    """(class, first sample, last sample, ORE_ID) of the nested rows."""
    return [(r["cls"], r["row0"] + 1, r["row1"] + 1, r["ore_id"]) for r in res.rows() if r["nested"]]


def chains(res: OreIntervals) -> list[tuple[str, str, int, int]]:
    """(decision, compared, first sample, last sample) of the events of step 4."""
    return [(e["decision"], e["compared"], e["row0"] + 1, e["row1"] + 1) for e in events_of(res, step=4)]


@pytest.mark.parametrize(("name", "mode"), CASES)
def test_examples_of_the_sources(name: str, mode: str) -> None:
    """Every interval written for an example and a mode is among the rows: class, samples, depths, M, MC, C,
    BASIS (1e−12 отн.); Σ M of the rows is the length of the line."""
    res = ore_intervals(example_lines(name), example_params(name, mode), events=True, nested=True)
    check_rows(res, EXAMPLES[name]["expect"][mode]["intervals"])
    lines = example_lines(name)
    rows = main(res)
    assert close(sum(r["m"] for r in rows), float((lines.to - lines.frm).sum()))  # type: ignore[misc]
    assert [r["from"] for r in rows[1:]] == [r["to"] for r in rows[:-1]]
    assert mode in READY_MODES and len(READY_MODES) == 5


def test_t8_2_12_strict_excl() -> None:
    """И1, сл. 10–16: ore 3–11 м, 8 м @ 2,8375, MC 22,7; ore 17–22 м, 5 м @ 2,9, MC 14,5; ore 28–33 м, 5 м @
    1,16, MC 5,8 (сближенные); interlayer 11–17 м, 6 м @ 0,6."""
    ex = EXAMPLES["T8.2-12"]
    res = ore_intervals(example_lines("T8.2-12"), example_params("T8.2-12", "strict_excl"), events=True)
    assert brief(res) == [
        ("waste", 0.0, 3.0), ("ore", 3.0, 11.0), ("interlayer", 11.0, 17.0), ("ore", 17.0, 22.0),
        ("waste", 22.0, 28.0), ("ore", 28.0, 33.0), ("waste", 33.0, 34.0),
    ]  # fmt: skip
    ore = [r for r in res.rows() if r["cls"] == "ore"]
    assert [(r["m"], r["basis"], r["ore_id"], r["n_samples"]) for r in ore] == [
        (8.0, "thickness", 1, 8), (5.0, "thickness", 2, 5), (5.0, "thickness", 3, 5),
    ]  # fmt: skip
    for row, (c, mc, waste) in zip(ore, [(2.8375, 22.7, 2.0), (2.9, 14.5, 1.0), (1.16, 5.8, 3.0)], strict=True):
        assert close(row["c"], c) and close(row["mc"], mc) and close(row["waste_len"], waste)
    layer = res.rows()[2]
    assert close(layer["m"], 6.0) and close(layer["c"], 0.6) and close(layer["mc"], 3.6)
    names = [ou.CLASSES[c] for c in res.sample_cls]
    assert names[:11] == ["waste"] * 3 + ["ore", "waste_in", "ore", "ore", "ore", "waste_in", "ore", "ore"]
    assert names[11:17] == ["interlayer"] * 6  # sample 14 (1,9) leaves with the fragment
    assert names[28:33] == ["ore", "waste_in", "waste_in", "waste_in", "ore"] and names[33] == "waste"
    assert res.sample_ore_id.tolist() == [0] * 3 + [1] * 8 + [0] * 6 + [2] * 5 + [0] * 6 + [3] * 5 + [0]

    # the protocol: прирезка 14–17 and 29–32 rejected at stage 1, the fragment 12–17 out, the chain 29–33 joined
    assert check_protocol(res, ex["protocol"], ("rejected_c", "interlayer")) == 3
    assert [(e["row0"] + 1, e["row1"] + 1, e["decision"]) for e in events_of(res, step=3)] == [(12, 17, "interlayer")]
    assert chains(res) == [("near_joined", "k", 29, 33)]
    chain = events_of(res, step=4)[0]
    assert chain["kind"] == "chain" and close(chain["c"], 1.16) and close(chain["m"], 5.0)
    trim = events_of(res, samples=(29, 32), side="upper", stage=1)[0]
    assert trim["decision"] == "rejected_c" and close(trim["c"], 0.825)  # 0,825 < Сб, and still 29–33 is ore
    final = [(e["decision"], e["compared"], e["seq"]) for e in events_of(res, step=5)]
    assert final == [("ore", "m_min", 1), ("ore", "m_min", 2), ("ore", "m_min", 3)]


def test_t8_2_12_strict() -> None:
    """«Строгий» (derived by step 3): ore 3–14 м, 11 м @ 2,345454…; the gap 14–17 м (3 м ≤ Мпп) is waste."""
    res = ore_intervals(example_lines("T8.2-12"), example_params("T8.2-12", "strict"), events=True)
    assert brief(res) == [
        ("waste", 0.0, 3.0), ("ore", 3.0, 14.0), ("waste", 14.0, 17.0), ("ore", 17.0, 22.0),
        ("waste", 22.0, 28.0), ("ore", 28.0, 33.0), ("waste", 33.0, 34.0),
    ]  # fmt: skip
    upper = res.rows()[1]
    assert close(upper["m"], 11.0) and close(upper["mc"], 25.8) and close(upper["c"], 25.8 / 11.0)
    assert close(upper["waste_len"], 4.0) and upper["basis"] == "thickness"
    assert close(res.rows()[2]["m"], 3.0) and res.rows()[2]["m"] <= 5.0
    cut = [(e["kind"], e["row0"] + 1, e["row1"] + 1, e["decision"]) for e in events_of(res, step=3)]
    assert cut == [("layer", 12, 17, "torn"), ("gap", 15, 17, "torn")]
    assert chains(res) == [("near_joined", "k", 29, 33)]
    assert [ou.CLASSES[c] for c in res.sample_cls[11:17]] == ["waste_in", "waste_in", "ore", "waste", "waste", "waste"]


def test_t8_2_17_in_every_mode() -> None:
    """И1, сл. 18–19: the strict modes — 2 × 10,6 м @ 2,452830188…, MC 26,0, with the прослой 14,3 м @
    1,223776…; «смягчённый» — one ore 0–35,5 м @ 1,957746…, MC 69,5; no mode gives an empty result."""
    lines = example_lines("T8.2-17")
    for mode in ("strict_excl", "strict"):
        res = ore_intervals(lines, example_params("T8.2-17", mode), events=True)
        ore = [r for r in res.rows() if r["cls"] == "ore"]
        assert [(r["row0"] + 1, r["row1"] + 1, r["basis"]) for r in ore] == [(1, 3, "thickness"), (7, 9, "thickness")]
        for r in ore:
            assert close(r["m"], 10.6) and close(r["mc"], 26.0) and close(r["c"], 26.0 / 10.6)
            assert close(r["c"], 2.452830188679245) and close(r["waste_len"], 6.0)
        taken = [e for e in events_of(res, step=3, kind="layer") if e["decision"] != "not_selected"][0]
        assert close(taken["m"], 14.3) and close(taken["mc"], 17.5) and close(taken["c"], 1.2237762237762237)
        assert (taken["row0"] + 1, taken["row1"] + 1) == (4, 6) and taken["m"] > 8.0 and taken["c"] < 1.5
    excl = ore_intervals(lines, example_params("T8.2-17", "strict_excl")).rows()
    layer = [r for r in excl if r["cls"] == "interlayer"]
    assert len(layer) == 1 and close(layer[0]["from"], 10.6) and close(layer[0]["to"], 24.9)
    soft = ore_intervals(lines, example_params("T8.2-17", "soft")).rows()
    assert len(soft) == 1 and close(soft[0]["m"], 35.5) and close(soft[0]["mc"], 69.5)
    assert close(soft[0]["c"], 1.9577464788732395)
    domain = replace(lines, domain=np.zeros(9, np.int32))
    for mode in READY_MODES:
        res = ore_intervals(domain, example_params("T8.2-17", mode))
        assert EXAMPLES["T8.2-17"]["never_empty"] and np.any(res.cls == ou.CLS_ORE), mode
        assert np.all(res.c[res.cls == ou.CLS_ORE] >= 1.5)


def test_near_intervals_step_4() -> None:
    """A chain of ore units none of which meets К, through waste ≤ Мпп, with C ≥ Сб: picked when it meets К and
    lies farther than Мпп from the intervals of step 2."""
    # Т8.2-14 (И2, сл. 17): the chain 5–8 м, 3 м @ 1,0667, is 1 м from 0–4 — not picked, in every mode with step 4
    for mode in ("soft", "strict", "strict_excl"):
        res = ore_intervals(example_lines("T8.2-14"), example_params("T8.2-14", mode), events=True)
        assert brief(res, "ore") == [("ore", 0.0, 4.0)]
        assert check_protocol(res, EXAMPLES["T8.2-14"]["protocol"], ("not_selected",)) == 1
        assert chains(res) == [("not_selected", "m_waste_max", 6, 8)]
        assert [ou.CLASSES[c] for c in res.sample_cls[4:]] == ["waste", "subgrade", "waste", "subgrade"]
    # the distance: Мпп = 3 м; 3 м of waste above the chain — not farther than Мпп; 4 м — picked
    chain = [1.1, 0.8, 1.3]
    for gap, picked in ((3, False), (4, True)):
        grades = [3.0, 3.0, 3.0] + [0.1] * gap + chain + [0.1] * gap + [3.0, 3.0, 3.0]
        res = ore_intervals(unit_line(grades), params(mode="soft"), events=True)
        assert (("ore", 3.0 + gap, 6.0 + gap) in brief(res)) == picked
        assert chains(res)[0][:2] == (("near_joined", "k") if picked else ("not_selected", "m_waste_max"))
        assert res.ore_id[res.cls == ou.CLS_ORE].tolist() == ([1, 2, 3] if picked else [1, 2])
    below = [1.1, 0.8, 1.3] + [0.1] * 3 + [3.0, 3.0, 3.0]  # too close to the interval below
    assert brief(ore_intervals(unit_line(below), params()), "ore") == [("ore", 6.0, 9.0)]
    # at the ends of a line there is nothing to be close to
    assert brief(ore_intervals(unit_line(chain), params()), "ore") == [("ore", 0.0, 3.0)]
    assert brief(ore_intervals(unit_line([0.1, *chain, 0.1]), params()), "ore") == [("ore", 1.0, 4.0)]
    # the chain has to meet К: 2 м @ 1,2 through 1 м — 3 м by thickness; with Мрт = 4 and MC 3,2 < 4 — no
    res = ore_intervals(unit_line(chain), params(m_min=4.0), events=True)
    assert brief(res) == [("subgrade", 0.0, 1.0), ("waste", 1.0, 2.0), ("subgrade", 2.0, 3.0)]
    assert chains(res) == [("not_selected", "k", 1, 3)]
    # waste longer than Мпп ends a chain without a word; C of the whole chain below Сб — the pair is not taken
    assert not brief(ore_intervals(unit_line([1.4, 0.9, 0.9, 0.9, 0.9, 1.4]), params()), "ore")
    res = ore_intervals(unit_line([1.1, 0.1, 0.1, 2.4, 0.3, 0.1, 0.5, 2.5]), params(), events=True)
    assert chains(res) == [("rejected_c", "cutoff", 1, 4), ("near_joined", "k", 4, 8)]
    assert brief(res, "ore") == [("ore", 3.0, 8.0)] and res.rows()[0]["cls"] == "subgrade"


def test_a_chain_takes_no_fragment_in_the_strict_modes() -> None:
    """Р Б Р Б Р with «Б Р Б» of 5 м @ 0,32 (Мпп = 3 м): «смягчённый» joins the three, the strict modes stop
    the chain before the fragment would appear in it."""
    grades = [2.8, 0.1, 0.1, 1.2, 0.1, 0.1, 2.8]
    soft = ore_intervals(unit_line(grades), params(mode="soft", mc_min=20.0), events=True)
    assert brief(soft) == [("ore", 0.0, 7.0)] and chains(soft) == [("near_joined", "k", 1, 7)]
    assert [(e["row0"] + 1, e["row1"] + 1) for e in events_of(soft, kind="layer")] == [(2, 6)]
    for mode in ("strict", "strict_excl"):
        res = ore_intervals(unit_line(grades), params(mode=mode, mc_min=20.0), events=True)
        assert chains(res) == [("not_selected", "layer", 1, 7), ("near_joined", "k", 1, 4)]
        assert brief(res) == [("ore", 0.0, 4.0), ("waste", 4.0, 6.0), ("subgrade", 6.0, 7.0)]
        assert not events_of(res, step=3)


def test_nested_rows_waste_in_and_frame() -> None:
    """ADR 0018, решение 21: `waste_in` rows inside an `ore` row and `frame` rows of Мпп on both sides are
    nested (NESTED = 1) and follow their interval; they do not change the other rows or the samples."""
    lines = example_lines("T8.2-12")
    p = example_params("T8.2-12", "strict_excl")
    res = ore_intervals(lines, p, nested=True)
    plain = ore_intervals(lines, p)
    keep = res.nested == 0
    assert res.iv_i[keep].tobytes() == plain.iv_i.tobytes() and res.iv_f[keep].tobytes() == plain.iv_f.tobytes()
    assert res.sample_cls.tobytes() == plain.sample_cls.tobytes() and not np.any(plain.nested)
    assert res.sample_ore_id.tobytes() == plain.sample_ore_id.tobytes()
    assert nested(res) == [
        ("waste_in", 5, 5, 1), ("waste_in", 9, 9, 1), ("frame", 1, 3, 1), ("frame", 12, 16, 1),
        ("waste_in", 21, 21, 2), ("frame", 13, 17, 2), ("frame", 23, 27, 2),
        ("waste_in", 30, 32, 3), ("frame", 24, 28, 3), ("frame", 34, 34, 3),
    ]  # fmt: skip
    order = [(r["cls"], r["nested"]) for r in res.rows()][1:6]
    assert order == [("ore", 0), ("waste_in", 1), ("waste_in", 1), ("frame", 1), ("frame", 1)]
    frames = [r for r in res.rows() if r["cls"] == "frame"]
    assert close(frames[0]["m"], 3.0) and close(frames[0]["c"], 0.65 / 3.0)  # the line begins 3 м above
    assert close(frames[1]["m"], 5.0) and close(frames[1]["c"], 0.7) and close(frames[1]["mc"], 3.5)  # Мпп = 5 м
    assert all(r["basis"] == "" and r["waste_len"] == 0.0 for r in res.rows() if r["nested"])
    inside = [r for r in res.rows() if r["cls"] == "waste_in"]
    assert close(sum(r["m"] for r in inside[:2]), plain.rows()[1]["waste_len"])
    # Σ M by NESTED = 0 is the length of the line; with the nested rows it is not
    assert close(float(res.m[keep].sum()), 34.0) and float(res.m.sum()) > 34.0

    # a frame does not go into another ore interval: Т8.2-15 «строгий», the gap 4–6 м frames both intervals
    res = ore_intervals(example_lines("T8.2-15"), example_params("T8.2-15", "strict"), nested=True)
    assert nested(res) == [("waste_in", 3, 3, 1), ("frame", 5, 6, 1), ("frame", 5, 6, 2)]
    # whole samples until Мпп is reached: 6,0 + 2,3 = 8,3 м for Мпп = 8 м (Т8.2-17)
    res = ore_intervals(example_lines("T8.2-17"), example_params("T8.2-17", "strict_excl"), nested=True)
    assert nested(res) == [("waste_in", 2, 2, 1), ("frame", 4, 5, 1), ("waste_in", 8, 8, 2), ("frame", 5, 6, 2)]
    assert all(close(r["m"], 8.3) for r in res.rows() if r["cls"] == "frame")
    # Мпп = 0 — no frame; a missing stretch counted as waste is flagged in the frame as well
    assert nested(ore_intervals(unit_line([0.1, 3.0, 3.0, 3.0, 0.1]), params(m_waste_max=0.0), nested=True)) == []
    gap = make_lines([[(0.0, 1.0, 3.0), (1.0, 2.0, 3.0), (2.0, 3.0, 3.0), (3.0, 4.0, math.nan), (4.0, 5.0, 0.1)]])
    res = ore_intervals(gap, params(missing="waste", missing_max=1.0), nested=True)
    assert nested(res) == [("frame", 4, 5, 1)] and res.rows()[-1]["flag"] == 1 and close(res.rows()[-1]["m"], 2.0)
    assert res.rows()[-1]["n_samples"] == 2 and res.sample_cls.tolist() == [0, 0, 0, ou.CLS_WASTE, ou.CLS_WASTE]


def test_nested_rows_match_the_reference_and_find_room() -> None:
    """Hard data, every mode: the nested rows are those of the reference; a strip where the nested rows
    outnumber the room set aside for them is run again and gives the same rows."""
    holes = _random_holes(3141, 400)
    lines = make_lines(holes)
    for mode in ("strict_excl", "strict", "soft", "simple"):
        p = params(mode=mode, m_waste_max=2.0).checked()
        res = ore_intervals(lines, p, nested=True)
        check_reference(res, holes, p)
        inner = [r for r in res.rows() if r["nested"]]
        got = [(r["hole"], r["cls"], r["row0"], r["row1"]) for r in inner]
        want = []
        first = 0
        for h, hole in enumerate(holes):
            for line in ref_lines(hole):
                ls = [hole[i][1] - hole[i][0] for i in line]
                gs = [hole[i][2] for i in line]
                for cls, a, b in ref_nested(ls, gs, ref_mode(ls, gs, p)[0], p):
                    want.append((h, cls, first + line[a], first + line[b - 1]))
                    assert len(want) <= len(inner) and got[len(want) - 1] == want[-1]
                    row = inner[len(want) - 1]
                    m, mc = sums(ls, gs, a, b)
                    assert close(row["m"], m) and close(row["mc"], mc) and row["n_samples"] == b - a
            first += len(hole)
        assert got == want and len({cls for _, cls, _, _ in got}) == 2 and len(got) > 500
    # every ore sample is an interval with two frames: 2 nested rows per 2 samples > half the samples + 16
    n = 400
    dense = unit_line([3.0, 0.1] * n)
    res = ore_intervals(dense, params(m_min=1.0, m_waste_max=0.5), nested=True)
    assert int((res.cls == ou.CLS_ORE).sum()) == n and int((res.cls == ou.CLS_FRAME).sum()) == 2 * n - 1
    assert len(res) == 2 * n + 2 * n - 1 and res.stats["pieces"] == 2 * n
    assert res.ore_id[res.cls == ou.CLS_FRAME].tolist() == [1, *np.repeat(np.arange(2, n + 1), 2).tolist()]
    for threads in (2, 4):
        big = make_lines(_random_holes(2718, 9000))
        one = ore_intervals(big, params(), nested=True)
        res = ore_intervals(big, params(), nested=True, threads=threads)
        assert res.iv_i.tobytes() == one.iv_i.tobytes() and res.iv_f.tobytes() == one.iv_f.tobytes()


def test_contacts_mode() -> None:
    """«По контактам» (И1, сл. 6; решение 22): the interval is the sampling line of a domain of the body; it is
    кондиционный by MC ≥ МС alone; `contacts_values` — the codes of the body, all of them by default."""
    hole = [
        (0.0, 1.0, 0.2, 0), (1.0, 2.0, 0.4, 0),  # host rock, code 0
        (2.0, 3.0, 2.0, 1), (3.0, 4.0, 0.3, 1), (4.0, 5.0, 1.9, 1),  # vein 1: 3 м, MC 4,2
        (5.0, 6.0, 5.0, -1),  # outside the domains
        (6.0, 7.0, 0.6, 2), (7.0, 8.0, 0.9, 2),  # vein 2: 2 м, MC 1,5 < МС
        (8.0, 9.0, 0.5, 1), (9.0, 10.0, 0.5, 1), (10.0, 11.0, 0.5, 1), (11.0, 12.0, 0.6, 1),  # vein 1 again
    ]  # fmt: skip
    lines = make_lines([hole], domain=True)
    p = params(mode="contacts", mc_min=2.0, contacts_values=(1, 2))
    res = ore_intervals(lines, p, events=True, nested=True)
    assert [(r["cls"], r["from"], r["to"], r["basis"], r["nested"]) for r in res.rows()] == [
        ("waste", 0.0, 2.0, "", 0),
        ("ore", 2.0, 5.0, "mc", 0), ("waste_in", 3.0, 4.0, "", 1),
        ("subgrade", 6.0, 8.0, "", 0),
        ("ore", 8.0, 12.0, "mc", 0), ("waste_in", 8.0, 12.0, "", 1),
    ]  # fmt: skip
    rows = main(res)
    assert close(rows[1]["mc"], 4.2) and close(rows[1]["waste_len"], 1.0) and rows[1]["ore_id"] == 1
    # 4 м @ 0,525: every sample is below Сб, and still MC 2,1 ≥ МС — кондиционный by МС (the contacts rule)
    assert close(rows[3]["mc"], 2.1) and close(rows[3]["c"], 0.525) and rows[3]["ore_id"] == 2
    names = [ou.CLASSES[c] if c >= 0 else "" for c in res.sample_cls]
    assert names == ["waste", "waste", "ore", "waste_in", "ore", "", "subgrade", "subgrade"] + ["waste_in"] * 4
    assert res.sample_ore_id.tolist() == [0, 0, 1, 1, 1, 0, 0, 0, 2, 2, 2, 2] and res.stats["no_domain"] == 1
    final = [(e["decision"], e["compared"], e["row0"] + 1, e["row1"] + 1) for e in events_of(res, step=5)]
    assert final == [("ore", "mc_min", 3, 5), ("subgrade", "mc_min", 7, 8), ("ore", "mc_min", 9, 12)]
    assert not events_of(res, step=2) and not events_of(res, step=3) and not events_of(res, step=4)
    # every code is the body by default; Мрт is not looked at (MC < МС = Сб·Мрт = 3 for 0–2 м and 6–8 м)
    every = ore_intervals(lines, params(mode="contacts"))
    assert brief(every) == [("subgrade", 0.0, 2.0), ("ore", 2.0, 5.0), ("subgrade", 6.0, 8.0), ("subgrade", 8.0, 12.0)]
    only = ore_intervals(lines, params(mode="contacts", mc_min=2.0, contacts_values=[2]))
    assert brief(only) == [("waste", 0.0, 2.0), ("waste", 2.0, 5.0), ("subgrade", 6.0, 8.0), ("waste", 8.0, 12.0)]
    # a missing grade breaks the interval («разрыв»); MC = МС is кондиционный
    vein = [(0.0, 1.0, 2.0, 1), (1.0, 2.0, math.nan, 1), (2.0, 3.0, 1.5, 1), (3.0, 4.0, 1.5, 1)]
    broken = make_lines([vein], domain=True)
    res = ore_intervals(broken, params(mode="contacts"))
    assert brief(res) == [("subgrade", 0.0, 1.0), ("ore", 2.0, 4.0)] and close(res.rows()[1]["mc"], 3.0)
    assert res.sample_cls[1] == ou.CLS_NONE and res.stats["missing"] == 1
    # the mode needs a domain; the codes are checked
    with pytest.raises(OreError) as err:
        ore_intervals(unit_line([1.0, 2.0]), params(mode="contacts"))
    assert err.value.code == "ore_no_domain" and "домен" in err.value.message and err.value.hint
    for bad in ((-1,), ("жила",), 5):
        with pytest.raises(OreError) as err:
            ore_intervals(lines, params(mode="contacts", contacts_values=bad))
        assert err.value.code == "ore_bad_param"


def test_domains_bound_the_other_modes() -> None:
    """A domain field with the other modes: the lines end at the contacts, the steps run inside a domain."""
    grades = [3.0, 3.0, 0.5, 3.0, 3.0, 0.5, 0.5, 3.0, 3.0, 3.0]
    hole = [(float(k), k + 1.0, g, 0 if k < 5 else 1) for k, g in enumerate(grades)]
    res = ore_intervals(make_lines([hole], domain=True), params(m_waste_max=2.0), nested=True)
    assert [(r["cls"], r["from"], r["to"], r["line"]) for r in main(res)] == [
        ("ore", 0.0, 5.0, 0), ("waste", 5.0, 7.0, 1), ("ore", 7.0, 10.0, 1),
    ]  # fmt: skip
    # a frame stays inside its line: none below 0–5 м, though the waste 5–7 м lies right under it
    assert nested(res) == [("waste_in", 3, 3, 1), ("frame", 6, 7, 2)]
    assert brief(ore_intervals(unit_line(grades), params(m_waste_max=2.0))) == [("ore", 0.0, 10.0)]
