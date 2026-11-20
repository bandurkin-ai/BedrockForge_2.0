"""Д-26, step 3 — пустой прослой and the «строгий» modes (ТЗ 7.35, Т8.2-15, Т8.2-16; ADR 0018 §8): fragments
«порода … порода» with C < Сб and M > Мпп, «разорвать» and «исключить весь фрагмент», the rule that picks the
fragment; no such fragment is left in an ore interval — checked by brute force on 1 000 random lines."""

from __future__ import annotations

import time

import numpy as np

from bf_core.composites import ore_units as ou
from bf_core.composites.ore_intervals import OreParams, ore_intervals
from tests.oreutil import (
    EXAMPLES,
    brief,
    brute_layers,
    check_protocol,
    check_reference,
    check_rows,
    close,
    events_of,
    example_lines,
    example_params,
    make_lines,
    ref_k,
    unit_line,
)
from tests.test_ore_simple import _random_holes


def strict(**over: object) -> OreParams:
    return OreParams(**{"cutoff": 1.0, "m_min": 3.0, "m_waste_max": 3.0, "mode": "strict", **over})  # type: ignore[arg-type]


def cuts(res: object) -> list[tuple[str, str, int, int]]:
    """(kind, decision, first sample, last sample) of the events of step 3."""
    return [(e["kind"], e["decision"], e["row0"] + 1, e["row1"] + 1) for e in events_of(res, step=3)]  # type: ignore[arg-type]


def rounded(res: object, cls: str | None = None) -> list[tuple[str, float, float]]:
    """`brief` with the depths rounded: 2,3 + 6,0 + 2,3 is 10,600000000000001."""
    return [(c, round(f, 9), round(t, 9)) for c, f, t in brief(res, cls)]  # type: ignore[arg-type]


def test_t8_2_15_strict() -> None:
    """И2, сл. 18: before step 3 — 0–8 м @ 1,225; the fragment 2–6 м (4 м @ 0,95) is torn — ore 0–4 м @ 1,25
    (thickness) and 6–8 м @ 1,5 (MC = МС, mc); the gap 4–6 м (2 м ≤ Мпп) is waste."""
    ex = EXAMPLES["T8.2-15"]
    res = ore_intervals(example_lines("T8.2-15"), example_params("T8.2-15", "strict"), events=True)
    assert brief(res) == [("ore", 0.0, 4.0), ("waste", 4.0, 6.0), ("ore", 6.0, 8.0)]
    check_rows(res, ex["expect"]["strict"]["intervals"])
    upper, gap, lower = res.rows()
    assert close(upper["c"], 1.25) and close(upper["mc"], 5.0) and upper["basis"] == "thickness"
    assert close(lower["c"], 1.5) and close(lower["mc"], 3.0) and lower["basis"] == "mc"
    assert close(upper["waste_len"], 1.0) and close(gap["m"], 2.0) and close(gap["mc"], 1.8)
    assert [ou.CLASSES[c] for c in res.sample_cls] == ["ore", "ore", "waste_in", "ore", "waste", "waste", "ore", "ore"]
    assert res.sample_ore_id.tolist() == [1, 1, 1, 1, 0, 0, 2, 2] and res.ore_id.tolist() == [1, 0, 2]

    assert check_protocol(res, ex["protocol"], ("torn",)) == 1
    assert cuts(res) == [("layer", "torn", 3, 6), ("gap", "torn", 5, 6)]
    layer = events_of(res, kind="layer")[0]
    assert (layer["compared"], layer["seq"]) == ("m_waste_max", 1) and close(layer["c"], 0.95)
    assert layer["m"] > example_params("T8.2-15", "strict").m_waste_max
    final = [(e["decision"], e["compared"], e["row0"] + 1, e["row1"] + 1) for e in events_of(res, step=5)]
    assert final == [("ore", "m_min", 1, 4), ("ore", "mc_min", 7, 8)]
    assert [e["stage"] for e in events_of(res, step=2, kind="unit", decision="joined")] == [1]  # 0–8 м first


def test_t8_2_16_strict_excl() -> None:
    """И2, сл. 19: the whole fragment 2–6 м leaves — ore 0–2 м and 6–8 м, both @ 1,5, MC 3,0, BASIS mc;
    interlayer 2–6 м, 4 м @ 0,95."""
    ex = EXAMPLES["T8.2-16"]
    res = ore_intervals(example_lines("T8.2-16"), example_params("T8.2-16", "strict_excl"), events=True)
    assert brief(res) == [("ore", 0.0, 2.0), ("interlayer", 2.0, 6.0), ("ore", 6.0, 8.0)]
    check_rows(res, ex["expect"]["strict_excl"]["intervals"])
    layer = res.rows()[1]
    assert close(layer["m"], 4.0) and close(layer["c"], 0.95) and (layer["basis"], layer["ore_id"]) == ("", 0)
    assert layer["n_samples"] == 4 and layer["waste_len"] == 0.0
    names = [ou.CLASSES[c] for c in res.sample_cls]
    assert names == ["ore", "ore"] + ["interlayer"] * 4 + ["ore", "ore"]  # sample 4 (1,2 ≥ Сб) — with its fragment
    assert res.sample_ore_id.tolist() == [1, 1, 0, 0, 0, 0, 2, 2]
    assert cuts(res) == [("layer", "interlayer", 3, 6)]
    # between the intervals of one interval of step 2 no gap of Мпп or less is left
    gap = res.frm[2] - res.to[0]
    assert gap > example_params("T8.2-16", "strict_excl").m_waste_max


def test_a_conditioned_interval_stays_above_the_fragment() -> None:
    """The rule of step 3 on И1, сл. 18–19: the first fragment from the top, 2,3–16,6 м, leaves 2,3 м @ 5,0
    above — not кондиционный (MC 11,5 < 12); the next one, 10,6–24,9 м, leaves 10,6 м @ 2,45 and is taken.
    «Первый сверху» alone would end with five ore units of 2,3 м and nothing кондиционное."""
    res = ore_intervals(example_lines("T8.2-17"), example_params("T8.2-17", "strict_excl"), events=True)
    assert rounded(res) == [("ore", 0.0, 10.6), ("interlayer", 10.6, 24.9), ("ore", 24.9, 35.5)]
    assert cuts(res) == [("layer", "not_selected", 2, 4), ("layer", "interlayer", 4, 6)]
    passed = events_of(res, decision="not_selected")[0]
    assert passed["compared"] == "k" and close(passed["m"], 14.3) and close(passed["c"], 17.5 / 14.3)
    assert close(passed["from"], 2.3) and close(passed["to"], 16.6)
    # «разорвать»: the same fragment; its last waste unit leaves, then 2,3–16,6 м is the only one in 0–18,9 м
    res = ore_intervals(example_lines("T8.2-17"), example_params("T8.2-17", "strict"), events=True)
    assert rounded(res, "ore") == [("ore", 0.0, 10.6), ("ore", 24.9, 35.5)]
    assert rounded(res)[1:4] == [("waste", 10.6, 16.6), ("subgrade", 16.6, 18.9), ("waste", 18.9, 24.9)]
    assert cuts(res) == [
        ("layer", "not_selected", 2, 4), ("layer", "torn", 4, 6), ("gap", "torn", 6, 6),
        ("layer", "not_selected", 2, 4), ("layer", "torn", 2, 4), ("gap", "torn", 4, 4),
    ]  # fmt: skip
    assert [e["seq"] for e in events_of(res, kind="layer", decision="torn")] == [1, 2]
    # no fragment leaves a кондиционный interval above: the first one is taken (three ore units of 2,3 м)
    short = make_lines([[(0.0, 2.3, 5.0), (2.3, 8.3, 0.5), (8.3, 10.6, 5.0), (10.6, 16.6, 0.5), (16.6, 18.9, 5.0)]])
    p = example_params("T8.2-17", "strict_excl")
    assert brief(ore_intervals(short, p)) == [
        ("subgrade", 0.0, 2.3), ("interlayer", 2.3, 16.6), ("subgrade", 16.6, 18.9),
    ]  # fmt: skip
    torn = ore_intervals(short, example_params("T8.2-17", "strict"))
    assert brief(torn) == [("ore", 0.0, 10.6), ("waste", 10.6, 16.6), ("subgrade", 16.6, 18.9)]
    assert brief(ore_intervals(short, example_params("T8.2-17", "soft"))) == [("ore", 0.0, 18.9)]


def test_limits_of_a_fragment() -> None:
    """M(F) = Мпп is not a прослой, M(F) > Мпп is; C(F) = Сб is not (also an ulp short of it), C(F) < Сб is."""
    grades = [3.0, 3.0, 0.5, 1.5, 0.5, 0.5, 3.0, 3.0]  # the fragment 3–6: 4 м @ 0,75
    assert brief(ore_intervals(unit_line(grades), strict(m_waste_max=4.0))) == [("ore", 0.0, 8.0)]
    res = ore_intervals(unit_line(grades), strict(m_waste_max=3.999))
    assert brief(res) == [("ore", 0.0, 4.0), ("waste", 4.0, 6.0), ("ore", 6.0, 8.0)]
    thick = [1.0, 1.0, 0.1, 0.2, 0.3, 0.1, 1.0, 1.0]  # the fragment: 0,1 + 0,2 + 0,3 + 0,1 = 0,7000000000000001
    assert 0.1 + 0.2 + 0.3 + 0.1 > 0.7
    hole = [(float(k), k + 1.0, g, t) for k, (g, t) in enumerate(zip(grades, thick, strict=True))]
    lines = make_lines([hole], thick=True)
    assert len(ore_intervals(lines, strict(m_waste_max=0.7, m_min=2.0))) == 1
    assert len(ore_intervals(lines, strict(m_waste_max=0.7 * (1 - 1e-8), m_min=2.0))) == 3
    # C(F) = Сб: (0,7 + 1,9) + (0,7 + 0,7) over 4 м is 3,9999999999999996 — not below the cutoff
    at_cutoff = [3.0, 3.0, 0.7, 1.9, 0.7, 0.7, 3.0, 3.0]
    assert (0.7 + 1.9) + (0.7 + 0.7) < 4.0
    assert brief(ore_intervals(unit_line(at_cutoff), strict(mode="strict_excl"))) == [("ore", 0.0, 8.0)]
    below = [3.0, 3.0, 0.7, 1.8, 0.7, 0.7, 3.0, 3.0]
    res = ore_intervals(unit_line(below), strict(mode="strict_excl"))
    assert brief(res) == [("ore", 0.0, 2.0), ("interlayer", 2.0, 6.0), ("ore", 6.0, 8.0)]
    assert close(res.rows()[1]["c"], 0.975)


def test_parts_are_looked_through_again() -> None:
    """Two fragments in one interval; a part that does not meet К falls back to its samples; the parts of a
    torn interval keep the прирезка above."""
    grades = [1.5, 1.5, 0.8, 1.2, 0.9, 0.9, 1.5, 1.5, 0.8, 1.2, 0.9, 0.9, 1.2, 1.1]  # И2, сл. 18, twice
    soft = ore_intervals(unit_line(grades), strict(mode="soft"), events=True)
    assert brief(soft) == [("ore", 0.0, 14.0)]
    assert [(e["row0"] + 1, e["row1"] + 1) for e in events_of(soft, kind="layer")] == [(3, 6), (9, 12)]
    res = ore_intervals(unit_line(grades), strict(mode="strict_excl"), events=True)
    assert brief(res) == [
        ("ore", 0.0, 2.0), ("interlayer", 2.0, 6.0), ("ore", 6.0, 8.0), ("interlayer", 8.0, 12.0),
        ("subgrade", 12.0, 14.0),
    ]  # fmt: skip
    assert [e["seq"] for e in events_of(res, kind="layer")] == [1, 2]
    assert events_of(res, step=5, decision="subgrade")[0]["compared"] == "k"
    res = ore_intervals(unit_line(grades), strict(), events=True)
    assert brief(res) == [
        ("ore", 0.0, 4.0), ("waste", 4.0, 6.0), ("ore", 6.0, 10.0), ("waste", 10.0, 12.0), ("subgrade", 12.0, 14.0),
    ]  # fmt: skip
    assert close(res.rows()[2]["mc"], 5.0) and close(res.rows()[2]["waste_len"], 1.0)
    assert res.ore_id.tolist() == [1, 0, 2, 0, 0]
    # modes without step 3 name the fragments, the strict ones leave none to name
    assert not events_of(res, decision="not_checked")


def _layered_lines(seed: int, n: int) -> list[list[tuple[float, float, float]]]:
    """Lines without gaps: grades around the cutoff, lengths that do not sum exactly — long intervals of
    step 2 with waste inside."""
    rng = np.random.default_rng(seed)
    grades = [0.05, 0.3, 0.6, 0.9, 1.0, 1.1, 1.6, 2.5, 6.0]
    lengths = [0.5, 1.0, 1.0, 1.0, 1.5, 2.3]
    out = []
    for _ in range(n):
        depth = 0.0
        hole = []
        for _ in range(int(rng.integers(8, 50))):
            top = depth
            depth += float(rng.choice(lengths))
            hole.append((top, depth, float(rng.choice(grades))))
        out.append(hole)
    return out


def test_no_fragment_is_left_in_ore_on_1000_random_lines() -> None:
    """Критерий 12.12: in the strict modes no ore interval holds a fragment «порода … порода» with C < Сб and
    M > Мпп — every fragment of every ore row by brute force over the samples (1 000 lines, seed 12345); the
    rows are those of the plain-Python reference."""
    holes = _layered_lines(12345, 1000)
    lines = make_lines(holes)
    first = np.concatenate([[0], np.cumsum([len(h) for h in holes])])
    hard = {"m_waste_max": 4.5, "m_min": 5.0, "mc_min": 4.0, "c_min": 1.2}
    for over in ({}, {"m_waste_max": 2.0, "m_min": 2.0}, hard):
        found = {}
        for mode in ("soft", "strict", "strict_excl"):
            p = strict(mode=mode, **over).checked()
            res = ore_intervals(lines, p)
            with_layers = 0
            ore = 0
            for r in res.rows():
                hole = holes[r["hole"]]
                ls = [t - f for f, t, _ in hole]
                gs = [g for _, _, g in hole]
                a, b = r["row0"] - first[r["hole"]], r["row1"] + 1 - first[r["hole"]]
                if r["cls"] == "ore":
                    ore += 1
                    with_layers += bool(brute_layers(ls, gs, a, b, p))
                    assert ref_k(ls, gs, a, b, p) == r["basis"] != ""
                    assert gs[a] >= p.cutoff - 1e-9 and gs[b - 1] >= p.cutoff - 1e-9
                elif r["cls"] == "interlayer":
                    assert mode == "strict_excl" and (a, b) in brute_layers(ls, gs, a, b, p)
                    assert r["m"] > p.m_waste_max and r["c"] < p.cutoff
            found[mode] = with_layers
            classes, logged = check_reference(res, holes, p)
            assert ore == classes["ore"] > 1500
            if mode == "soft":
                assert with_layers > 100  # the lines do have such fragments before step 3
            else:
                assert with_layers == 0
                assert logged["interlayer" if mode == "strict_excl" else "torn"] > 150 and logged["skipped"] > 5
                assert classes.get("interlayer", 0) == logged.get("interlayer", 0)
        assert found["soft"] > 100 and found["strict"] == found["strict_excl"] == 0


def test_strict_matches_the_reference_on_hard_data() -> None:
    """Lines with gaps, missing grades, grades at the cutoff: both strict modes against the reference."""
    holes = _random_holes(1357, 600)
    lines = make_lines(holes)
    for over in ({}, {"m_waste_max": 2.0, "m_min": 4.0, "mc_min": 6.0}, {"cutoff": 0.6, "c_min": 1.0, "m_min": 2.0}):
        for mode in ("strict", "strict_excl"):
            p = strict(mode=mode, **over).checked()
            res = ore_intervals(lines, p, events=True)
            classes, logged = check_reference(res, holes, p)
            assert sum(classes.values()) == len(res) > 2500
            cut = [e for e in events_of(res, step=3, kind="layer") if e["decision"] in ("torn", "interlayer")]
            assert len(cut) == logged.get("torn", 0) + logged.get("interlayer", 0) > 5
            assert len(events_of(res, step=3, decision="not_selected")) == logged.get("skipped", 0)
            assert (mode == "strict_excl") == bool(np.any(res.cls == ou.CLS_INTERLAYER))
            length = lines.to - lines.frm
            in_line = res.sample_state == ou.ROW_PIECE
            assert close(float(res.m.sum()), float(length[in_line].sum()), 1e-11)
            assert close(float(res.mc.sum()), float((length * lines.grade)[in_line].sum()), 1e-11)


def test_threads_and_long_lines() -> None:
    """1, 2 and 4 threads give the same bytes; a line of 20 000 units: one interval without fragments is
    looked through once, an interval with a fragment at every step is cut in one pass."""
    holes = _random_holes(8642, 9000)
    lines = make_lines(holes)
    for mode in ("strict", "strict_excl"):
        one = ore_intervals(lines, strict(mode=mode, m_waste_max=2.0))
        assert np.any(one.cls == ou.CLS_ORE)
        for threads in (2, 4):
            res = ore_intervals(lines, strict(mode=mode, m_waste_max=2.0), threads=threads)
            assert res.iv_i.tobytes() == one.iv_i.tobytes() and res.iv_f.tobytes() == one.iv_f.tobytes()
            assert res.sample_cls.tobytes() == one.sample_cls.tobytes()
            assert res.sample_ore_id.tobytes() == one.sample_ore_id.tobytes()
    n = 20_000
    rich = unit_line([5.0, 0.9] * (n // 2))
    ore_intervals(unit_line([5.0, 0.9] * 8), strict())  # compiled
    start = time.perf_counter()
    res = ore_intervals(rich, strict())
    assert brief(res) == [("ore", 0.0, n - 1.0), ("waste", n - 1.0, float(n))]
    # Р 1 м @ 3,0 (MC = МС), Б 2 м @ 0,2: every «Б Р Б» is 5 м @ 0,76 — a fragment
    cut = unit_line([3.0, 0.2, 0.2] * (n // 2))
    whole = [("ore", 0.0, 1.5 * n - 2.0), ("waste", 1.5 * n - 2.0, 1.5 * n)]
    assert brief(ore_intervals(cut, strict(mode="soft"))) == whole
    res = ore_intervals(cut, strict(mode="strict_excl"))
    assert int((res.cls == ou.CLS_ORE).sum()) == n // 4 and int((res.cls == ou.CLS_INTERLAYER).sum()) == n // 4 - 1
    assert np.all(res.basis[res.cls == ou.CLS_ORE][:-1] == ou.BASIS_MC)  # the last one: Р Б Р, 4 м
    assert rounded(res)[-3:] == [("interlayer", 1.5 * n - 11, 1.5 * n - 6), ("ore", 1.5 * n - 6, 1.5 * n - 2),
                                 ("waste", 1.5 * n - 2, 1.5 * n)]  # fmt: skip
    res = ore_intervals(cut, strict())
    assert int((res.cls == ou.CLS_ORE).sum()) == n // 4 and close(float(res.m[0]), 4.0)
    assert time.perf_counter() - start < 5.0
