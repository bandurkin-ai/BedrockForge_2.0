"""Д-26, the protocol of оконтуривание (ТЗ 7.35 «Результат» (4); ADR 0018 §9, решения 10, 19): the form И1,
сл. 17 of a hole — stages and decisions of Т8.2-12, «не проверяется» of Т8.2-13, the прослой of Т8.2-17 — the
long form of a set, and the criterion: the protocol of a stored result is its calculation run again."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pytest

from bf_core.composites import ore_units as ou
from bf_core.composites.ore_intervals import ALGO, READY_MODES, OreError, OreParams, ore_intervals
from bf_core.composites.ore_protocol import LONG_HEAD, HoleProtocol, event_text, hole_protocol, long_form, mark
from bf_core.composites.ore_set import run_set
from tests.oreutil import EXAMPLES, close, example_lines, example_params, make_lines
from tests.test_ore_simple import _random_holes


def spans(pr: HoleProtocol, col: str, **want: Any) -> list[tuple[Any, ...]]:
    """(first sample, last sample, text) of the cells of a column, from the top down."""
    cells = [c for c in pr.cells if c["col"] == col and all(c[k] == v for k, v in want.items())]
    cells.sort(key=lambda c: (c["row0"], c["lane"]))
    return [(pr.rows[c["row0"]]["sample"], pr.rows[c["row1"]]["sample"], c["text"]) for c in cells]


def cell(pr: HoleProtocol, col: str, first: int, last: int) -> dict[str, Any]:
    hit = [
        c
        for c in pr.cells
        if c["col"] == col and (pr.rows[c["row0"]]["sample"], pr.rows[c["row1"]]["sample"]) == (first, last)
    ]
    assert len(hit) == 1, (col, first, last, hit)
    return hit[0]


def check_form(pr: HoleProtocol) -> None:
    """The form is a sheet: the cells of a lane do not overlap, lie inside the form, point to their records."""
    lanes = {c["key"]: c["lanes"] for c in pr.columns}
    taken: set[tuple[str, int, int]] = set()
    for c in pr.cells:
        assert 0 <= c["lane"] < lanes[c["col"]] and 0 <= c["row0"] <= c["row1"] < len(pr.rows)
        for r in range(c["row0"], c["row1"] + 1):
            assert (c["col"], c["lane"], r) not in taken, c
            taken.add((c["col"], c["lane"], r))
        if c["event"] is not None:
            e = pr.events[c["event"]]
            assert (e["from"], e["to"], e["m"], e["mc"]) == (c["from"], c["to"], c["m"], c["mc"])
    grid = pr.grid()
    assert len(grid["head"]) == 6 + sum(lanes.values()) and len(grid["body"]) == len(pr.rows)
    assert all(len(row) == len(grid["head"]) for row in grid["body"])
    assert sum(text is not None for row in grid["body"] for text in row[6:]) == len(pr.cells)
    assert len(grid["merges"]) == sum(c["row1"] > c["row0"] for c in pr.cells)
    json.dumps(pr.as_dict(), allow_nan=False)  # what the API answers: no NaN


def test_t8_2_12_stages_and_decisions() -> None:
    """И1, сл. 10–16 in the form of сл. 17: N / IN₀, stage 1 (C₁, C₂, IN₁), stage 2 (C₃, C₄, IN₂), the прослой
    12–17, the сближенные 29–33, the result."""
    ex = EXAMPLES["T8.2-12"]
    pr = hole_protocol(example_lines("T8.2-12"), example_params("T8.2-12", "strict_excl"), unit="г/т")
    assert [c["key"] for c in pr.columns] == [
        "n", "in0", "c1", "c2", "in1", "c3", "c4", "in2", "layer", "near", "final",
    ]  # fmt: skip
    assert [c["title"] for c in pr.columns] == [
        "N",
        "IN₀",
        "C₁",
        "C₂",
        "IN₁",
        "C₃",
        "C₄",
        "IN₂",
        "Прослои",
        "Сближенные",
        "Итог",
    ]
    assert all(c["lanes"] == 1 for c in pr.columns)
    check_form(pr)

    assert [r["sample"] for r in pr.rows] == list(range(1, 35))
    assert [r["g"] for r in pr.rows] == ex["grades"] and {r["l"] for r in pr.rows} == {1.0}
    assert [r["cls"] for r in pr.rows[2:6]] == ["waste", "ore", "waste_in", "ore"]
    assert [r["ore_id"] for r in pr.rows[16:23]] == [0, 2, 2, 2, 2, 2, 0]

    # N / IN₀: 17 elementary units, numbered from the top; the unit 6–8 is 3,0 м @ 4,60, MC 13,8
    assert [text for _, _, text in spans(pr, "n")] == [str(k) for k in range(1, 18)]
    units = spans(pr, "in0")
    assert len(units) == 17 and units[3] == (6, 8, "3,0/4,60/13,8") and units[12] == (23, 28, "6,0/0,23/1,4")
    assert [c["tone"] for c in pr.cells if c["col"] == "in0"][:4] == ["waste", "ore", "waste", "ore"]

    # stage 1: the прирезки of every triple from both sides; the waste 23–28 is longer than Мпп
    assert spans(pr, "c1") == [
        (4, 5, "1,30 ≥ Сб"), (6, 9, "3,63 ≥ Сб"), (10, 13, "1,70 ≥ Сб"), (14, 17, "0,60 < Сб"),
        (18, 21, "3,15 ≥ Сб"), (23, 28, "M 6,0 > Мпп"), (29, 32, "0,83 < Сб"),
    ]  # fmt: skip
    assert spans(pr, "c2") == [
        (5, 8, "3,65 ≥ Сб"), (9, 11, "2,10 ≥ Сб"), (12, 14, "1,03 ≥ Сб"), (15, 20, "2,03 ≥ Сб"),
        (21, 22, "1,40 ≥ Сб"), (30, 33, "0,85 < Сб"),
    ]  # fmt: skip
    rejected = cell(pr, "c1", 14, 17)
    assert (rejected["decision"], rejected["tone"]) == ("rejected_c", "reject") and close(rejected["c"], 0.6)
    assert close(cell(pr, "c1", 29, 32)["c"], 0.825) and close(cell(pr, "c1", 6, 9)["c"], 3.625)
    assert cell(pr, "c1", 23, 28)["decision"] == "rejected_m"
    # IN₁: 4–14 and 18–22 joined; sample 29 is некондиционная → the waste 23–32; sample 33 waits for stage 2
    assert spans(pr, "in1") == [
        (1, 3, "3,0/0,22/0,7"), (4, 14, "11,0/2,35/25,8"), (15, 17, "3,0/0,17/0,5"), (18, 22, "5,0/2,90/14,5"),
        (23, 32, "10,0/0,47/4,7"), (33, 33, "1,0/2,50/2,5"), (34, 34, "1,0/0,10/0,1"),
    ]  # fmt: skip
    assert [c["decision"] for c in sorted((c for c in pr.cells if c["col"] == "in1"), key=lambda c: c["row0"])] == [
        "", "joined", "", "joined", "demoted", "", "",
    ]  # fmt: skip
    # stage 2 — over the units of stage 1: C₃ = 26,3 / 14, C₄ = 15 / 8; 4–22 joined; sample 33 → waste
    assert spans(pr, "c3") == [(4, 17, "1,88 ≥ Сб"), (23, 32, "M 10,0 > Мпп")]
    assert spans(pr, "c4") == [(15, 22, "1,88 ≥ Сб")]
    assert close(cell(pr, "c3", 4, 17)["c"], 26.3 / 14.0) and close(cell(pr, "c4", 15, 22)["c"], 1.875)
    assert spans(pr, "in2") == [(1, 3, "3,0/0,22/0,7"), (4, 22, "19,0/2,15/40,8"), (23, 34, "12,0/0,61/7,3")]
    joined = cell(pr, "in2", 4, 22)
    assert (joined["decision"], joined["ore"], joined["tone"]) == ("joined", True, "ore")
    assert close(joined["m"], 19.0) and close(joined["mc"], 40.8)
    # steps 3–4 and the result
    assert spans(pr, "layer") == [(12, 17, "6,0 м @ 0,60 — прослой исключён")]
    assert cell(pr, "layer", 12, 17)["tone"] == "interlayer"
    assert spans(pr, "near") == [(29, 33, "5,0 м @ 1,16 — сближенные объединены")]
    assert spans(pr, "final") == [
        (1, 3, "безрудный 3,0/0,22/0,7"), (4, 11, "8,0 м @ 2,84 г/т (МС 22,7)"),
        (12, 17, "исключённый прослой 6,0/0,60/3,6"), (18, 22, "5,0 м @ 2,90 г/т (МС 14,5)"),
        (23, 28, "безрудный 6,0/0,23/1,4"), (29, 33, "5,0 м @ 1,16 г/т (МС 5,8)"), (34, 34, "безрудный 1,0/0,10/0,1"),
    ]  # fmt: skip
    assert [c["tone"] for c in pr.cells if c["col"] == "final"] == [
        "waste", "ore", "interlayer", "ore", "waste", "ore", "waste",
    ]  # fmt: skip
    ore = cell(pr, "final", 4, 11)
    assert (
        close(ore["m"], 8.0) and close(ore["c"], 2.8375) and close(ore["mc"], 22.7) and ore["decision"] == "thickness"
    )

    # the long form: every record of the example, the numbers, the words
    for want in ex["protocol"]:
        a, b = want["samples"]
        hit = [
            e
            for e in pr.events
            if (e["row0"] + 1, e["row1"] + 1, e["step"], e["decision"]) == (a, b, want["step"], want["decision"])
            and all(e[k] == want[k] for k in ("stage", "side") if k in want)
        ]
        assert len(hit) == 1 and all(close(hit[0][k], want[k]) for k in ("m", "mc", "c")), want
    assert [e["n"] for e in pr.events] == list(range(1, len(pr.events) + 1))
    text = pr.events[cell(pr, "c1", 14, 17)["event"]]["text"]
    assert text == "Этап 1 · C₁ · висячий бок · пробы 14–17 · 13–17 м · M 4 · C 0,6 · MC 2,4 — отклонён — C < Сб"
    assert (
        pr.events[cell(pr, "in1", 23, 32)["event"]]["text"]
        == "Этап 1 · IN₁ · пробы 23–32 · 22–32 м · M 10 · C 0,47 · MC 4,7"
    )
    assert (
        pr.events[-1]["text"]
        == "Класс интервала · итог · пробы 29–33 · 28–33 м · M 5 · C 1,16 · MC 5,8 — кондиционный (Мрт)"
    )
    assert [e["mark"] for e in pr.events if e["step"] == 2 and e["stage"] == 2] == [
        "C₃",
        "C₄",
        "Б",
        "IN₂",
        "IN₂",
        "IN₂",
    ]
    assert pr.as_dict()["algo"] == ALGO and pr.as_dict()["mode"] == "strict_excl"


def test_t8_2_12_strict_and_soft() -> None:
    """«Строгий» tears the fragment 12–17 (the waste 15–17 leaves), «смягчённый» does not check it."""
    strict = hole_protocol(example_lines("T8.2-12"), example_params("T8.2-12", "strict"))
    check_form(strict)
    assert spans(strict, "layer") == [
        (12, 17, "6,0 м @ 0,60 — разорван"), (15, 17, "3,0 м @ 0,17 — разорван"),
    ]  # fmt: skip
    assert next(c["lanes"] for c in strict.columns if c["key"] == "layer") == 2  # the fragment and its waste
    assert spans(strict, "final", tone="ore") == [
        (4, 14, "11,0 м @ 2,35 (МС 25,8)"), (18, 22, "5,0 м @ 2,90 (МС 14,5)"), (29, 33, "5,0 м @ 1,16 (МС 5,8)"),
    ]  # fmt: skip
    soft = hole_protocol(example_lines("T8.2-12"), example_params("T8.2-12", "soft"))
    check_form(soft)
    assert spans(soft, "layer") == [(12, 17, "6,0 м @ 0,60 — не проверяется в этом режиме")]
    assert spans(soft, "final", tone="ore") == [(4, 22, "19,0 м @ 2,15 (МС 40,8)"), (29, 33, "5,0 м @ 1,16 (МС 5,8)")]


def test_t8_2_13_not_checked() -> None:
    """И2, сл. 16, «упрощённый»: the interval grows by pairs (C₁, C₂ — its average); the fragment 2–6 м @ 0,975
    longer than Мпп is in the protocol — «не проверяется в этом режиме»."""
    pr = hole_protocol(example_lines("T8.2-13"), example_params("T8.2-13", "simple"), unit="г/т")
    check_form(pr)
    assert [c["key"] for c in pr.columns] == ["n", "in0", "c1", "in1", "c2", "in2", "layer", "final"]
    assert spans(pr, "c1") == [(1, 4, "1,10 ≥ Сб")] and spans(pr, "c2") == [(1, 7, "1,06 ≥ Сб")]
    assert spans(pr, "in1") == [(1, 4, "4,0/1,10/4,4"), (5, 6, "2,0/0,85/1,7"), (7, 7, "1,0/1,30/1,3")]
    assert spans(pr, "in2") == [(1, 7, "7,0/1,06/7,4")]
    layer = cell(pr, "layer", 3, 6)
    assert (layer["from"], layer["to"], layer["decision"], layer["tone"]) == (2.0, 6.0, "not_checked", "none")
    assert close(layer["m"], 4.0) and close(layer["c"], 0.975) and layer["m"] > 3.0
    assert layer["text"] == "4,0 м @ 0,98 — не проверяется в этом режиме"
    record = pr.events[layer["event"]]
    assert (record["step"], record["compared"], record["mark"]) == (3, "m_waste_max", "прослой")
    assert (
        record["text"]
        == "Пустой прослой · прослой · пробы 3–6 · 2–6 м · M 4 · C 0,975 · MC 3,9 — не проверяется в этом режиме (Мпп)"
    )
    assert spans(pr, "final") == [(1, 7, "7,0 м @ 1,06 г/т (МС 7,4)")]
    assert [e["mark"] for e in pr.events if e["step"] == 2] == ["C₁", "C₂"]


def test_t8_2_17_interlayer() -> None:
    """И1, сл. 18–19: the прослой 10,6–24,9 м is 14,3 м @ 1,223776… > Мпп; the fragment 2,3–16,6 м above it is
    passed over (no кондиционный interval would stay above) — in a lane of its own."""
    pr = hole_protocol(example_lines("T8.2-17"), example_params("T8.2-17", "strict_excl"), unit="г/т")
    check_form(pr)
    assert [c["key"] for c in pr.columns] == ["n", "in0", "c1", "c2", "in1", "layer", "final"]
    assert next(c["lanes"] for c in pr.columns if c["key"] == "layer") == 2
    layer = cell(pr, "layer", 4, 6)
    assert layer["decision"] == "interlayer" and layer["text"] == "14,3 м @ 1,22 — прослой исключён"
    assert close(layer["m"], 14.3) and close(layer["mc"], 17.5) and close(layer["c"], 1.2237762237762237)
    assert close(layer["from"], 10.6) and close(layer["to"], 24.9) and layer["m"] > 8.0 and layer["c"] < 1.5
    skipped = cell(pr, "layer", 2, 4)
    assert (skipped["decision"], skipped["tone"], skipped["lane"] != layer["lane"]) == ("not_selected", "none", True)
    assert spans(pr, "in1") == [(1, 9, "35,5/1,96/69,5")]
    assert spans(pr, "final") == [
        (1, 3, "10,6 м @ 2,45 г/т (МС 26,0)"), (4, 6, "исключённый прослой 14,3/1,22/17,5"),
        (7, 9, "10,6 м @ 2,45 г/т (МС 26,0)"),
    ]  # fmt: skip
    soft = hole_protocol(example_lines("T8.2-17"), example_params("T8.2-17", "soft"), unit="г/т")
    assert spans(soft, "final") == [(1, 9, "35,5 м @ 1,96 г/т (МС 69,5)")]
    assert {c["decision"] for c in soft.cells if c["col"] == "layer"} == {"not_checked"}


def test_lines_gaps_and_missing_values() -> None:
    """Samples outside the sampling lines stay in the form with the reason; «пустая порода до L_н» adds the
    stretch without samples as a line of the form, and a unit may begin or end with it."""
    nan = math.nan
    hole = [
        (0.0, 1.0, 2.0), (1.0, 2.0, 3.0), (2.5, 3.5, 2.0), (3.5, 4.5, nan), (4.5, 5.5, 4.0), (5.5, 5.5, 9.0),
        (5.0, 6.0, 1.0), (5.5, 6.5, 0.2), (9.0, 10.0, 5.0),
    ]  # fmt: skip
    lines = make_lines([[(0.0, 1.0, 1.0)], hole])
    p = OreParams(1.0, 2.0, 3.0, mode="soft")
    pr = hole_protocol(lines, p, 1)
    check_form(pr)
    assert [r["sample"] for r in pr.rows] == list(range(1, 10)) and pr.hole == 1
    assert [r["state"] for r in pr.rows] == [
        "piece",
        "piece",
        "piece",
        "missing",
        "piece",
        "bad",
        "overlap",
        "piece",
        "piece",
    ]
    assert [r["line"] for r in pr.rows] == [1, 1, 2, None, 3, None, None, 3, 4]
    assert [r["note"] for r in pr.rows] == [
        "", "", "разрыв линии — промежуток без проб 2–2,5 м", "разрыв линии — нет значения 3,5–4,5 м", "",
        "неверный интервал (TO ≤ FROM) — не участвует", "перекрытие с предыдущей пробой — не участвует", "",
        "разрыв линии — промежуток без проб 6,5–9 м",
    ]  # fmt: skip
    assert pr.rows[3]["g"] is None and pr.rows[3]["cls"] is None and pr.rows[5]["l"] is None  # never zero
    assert [text for _, _, text in spans(pr, "n")] == ["1", "1", "1", "2", "1"]  # N — in its sampling line
    assert spans(pr, "final", tone="ore") == [  # M < Мрт = 2, MC ≥ МС = 2: by the метропроцент
        (1, 2, "2,0 м @ 2,50 (МС 5,0)"), (3, 3, "1,0 м @ 2,00 (МС 2,0)"), (5, 5, "1,0 м @ 4,00 (МС 4,0)"),
        (9, 9, "1,0 м @ 5,00 (МС 5,0)"),
    ]  # fmt: skip
    breaks = [e for e in pr.events if e["step"] == 0]
    assert [(e["compared"], e["row0"] + 1, e["c"], e["mc"]) for e in breaks] == [
        ("gap", 3, None, None), ("no_value", 4, None, None), ("gap", 9, None, None),
    ]  # fmt: skip
    assert (
        breaks[0]["text"]
        == "Разрыв линии · проба 3 · 2–2,5 м · M 0,5 — линия опробования прервана (промежуток без проб)"
    )

    waste = hole_protocol(lines, OreParams(1.0, 2.0, 3.0, mode="soft", missing="waste", missing_max=3.0), 1)
    check_form(waste)
    assert [r["sample"] for r in waste.rows] == [1, 2, None, 3, 4, 5, 6, 7, 8, None, 9]
    assert [r["state"] for r in waste.rows] == [
        "piece", "piece", "gap", "piece", "bridged", "piece", "bad", "overlap", "piece", "gap", "piece",
    ]  # fmt: skip
    gap = waste.rows[2]
    assert (gap["from"], gap["to"], gap["l"], gap["g"], gap["note"]) == (
        2.0,
        2.5,
        0.5,
        None,
        "нет проб — пустая порода (флаг)",
    )
    assert waste.rows[4]["g"] is None and waste.rows[4]["note"] == "нет значения — пустая порода (флаг)"
    assert {r["line"] for r in waste.rows if r["state"] not in ("bad", "overlap")} == {1}
    units = [(c["row0"], c["row1"], c["ore"]) for c in waste.cells if c["col"] == "in0"]
    assert units == [
        (0, 1, True),
        (2, 2, False),
        (3, 3, True),
        (4, 4, False),
        (5, 5, True),
        (8, 9, False),
        (10, 10, True),
    ]
    assert [e["decision"] for e in waste.events if e["step"] == 0] == ["missing_waste"] * 3
    top = next(c for c in waste.cells if c["col"] == "final" and c["tone"] == "ore")  # 0–5,5 м through both
    assert (top["row0"], top["row1"], close(top["m"], 5.5), close(top["mc"], 11.0)) == (0, 5, True, True)
    assert [c["text"] for c in waste.cells if c["col"] == "c1"] == ["2,00 ≥ Сб", "1,00 ≥ Сб", "M 3,5 > Мпп"]


@pytest.mark.parametrize("mode", [m for m in READY_MODES if m != "contacts"])
def test_protocol_is_the_stored_result_run_again(mode: str) -> None:
    """Критерий 12.15: the protocol of a hole, taken from its own samples with the parameters of the result,
    gives the rows of the table and the fields of the samples of the result of the whole set bit for bit; the
    long form of the set is the long forms of its holes."""
    holes = _random_holes(2024, 120)
    lines = make_lines(holes)
    for p in (
        OreParams(1.0, 2.0, 2.5, mode=mode),
        OreParams(0.6, 3.0, 1.0, c_min=1.0, mode=mode, missing="waste", missing_max=2.5),
    ):
        stored = run_set(lines, p, threads=2)
        table = stored.intervals.rows()
        long = [e for block in long_form(lines, p, chunk=700) for e in block.records()]
        at = 0
        seen = set()
        for h in range(len(holes)):
            pr = hole_protocol(lines, p, h)
            check_form(pr)
            a, b = int(lines.starts[h]), int(lines.starts[h + 1])
            mine = [  # the rows of the hole; in the protocol the samples are counted from the first of the hole
                {**r, "row0": r["row0"] - a if r["row0"] >= 0 else -1, "row1": r["row1"] - a if r["row1"] >= 0 else -1}
                for r in table
                if r["hole"] == h
            ]
            assert pr.intervals == [{**r, "manual": 0} for r in mine]  # exact: floats and all
            samples = [r for r in pr.rows if r["sample"]]
            assert [r["ore_id"] for r in samples] == stored.ore_id[a:b].tolist()
            assert [r["cls"] for r in samples] == [
                None if c < 0 else ou.CLASSES[c] for c in stored.ore_cls[a:b].tolist()
            ]
            final = sorted((c for c in pr.cells if c["col"] == "final"), key=lambda c: c["row0"])
            flat = [r for r in mine if not r["nested"]]
            assert [(c["from"], c["to"], c["m"], c["mc"], c["tone"]) for c in final] == [
                (r["from"], r["to"], r["m"], r["mc"], r["cls"]) for r in flat
            ]
            # every line of the form inside a sampling line is under exactly one cell of «Итог»
            covered = sorted(r for c in final for r in range(c["row0"], c["row1"] + 1) if pr.rows[r]["line"])
            assert covered == [n for n, r in enumerate(pr.rows) if r["line"]]
            assert long[at : at + len(pr.events)] == pr.events
            at += len(pr.events)
            seen |= {c["key"] for c in pr.columns}
        assert at == len(long) and at > 1500
        assert {"n", "in0", "final", "layer", "c1", "in1"} <= seen
    again = [e for block in long_form(lines, p) for e in block.records()]
    assert again == long  # the size of the blocks changes nothing


def test_contacts_and_thickness_field() -> None:
    """«По контактам»: units and the class by МС; a thickness field goes to `l` of the form."""
    holes = [[(0.0, 2.0, 1.0, 1.0, 0), (2.0, 4.0, 3.0, 1.0, 0), (4.0, 6.0, 0.5, 1.5, 1), (6.0, 8.0, 0.6, 1.5, 1)]]
    lines = make_lines(holes, thick=True, domain=True)
    pr = hole_protocol(lines, OreParams(1.0, 1.0, 1.0, mode="contacts", contacts_values=(0,)), unit="%")
    check_form(pr)
    assert [c["key"] for c in pr.columns] == ["n", "in0", "final"]
    assert [r["l"] for r in pr.rows] == [1.0, 1.0, 1.5, 1.5] and [r["line"] for r in pr.rows] == [1, 1, 2, 2]
    assert spans(pr, "final") == [(1, 2, "2,0 м @ 2,00 % (МС 4,0)"), (3, 4, "безрудный 3,0/0,55/1,7")]
    assert pr.rows[2]["note"] == "разрыв линии — граница домена"
    assert [e["text"] for e in pr.events if e["step"] == 5] == [
        "Класс интервала · итог · пробы 1–2 · 0–4 м · M 2 · C 2 · MC 4 — кондиционный (МС)"
    ]


def test_long_form_of_a_set() -> None:
    """The long form: blocks of whole holes, rows under `LONG_HEAD` with the words in Russian, the names of
    the holes, None where a number does not apply; chosen holes; `tick` after every block cancels."""
    one = example_lines("T8.2-12")
    grades = one.grade.tolist()
    lines = make_lines([[(float(k), k + 1.0, g) for k, g in enumerate(grades)]] * 3 + [[], [(0.0, 1.0, math.nan)]])
    p = example_params("T8.2-12", "strict_excl")
    blocks = list(long_form(lines, p, chunk=40))
    assert [sorted(set(b.hole.tolist())) for b in blocks] == [[0, 1], [2]]  # whole holes; the last two have no events
    rows = [r for b in blocks for r in b.rows(["А-1", "А-2", "А-3", "А-4", "А-5"])]
    assert len(LONG_HEAD) == 16 and all(len(r) == 16 for r in rows) and len(rows) == 3 * 45
    assert rows[0] == (
        "А-1",
        1,
        1,
        "элементарные интервалы",
        None,
        "N₁",
        1,
        3,
        0.0,
        3.0,
        3.0,
        rows[0][11],
        rows[0][12],
        "",
        "Сб",
        "",
    )
    assert close(rows[0][11], 0.65 / 3.0) and close(rows[0][12], 0.65)
    assert rows[45 + 23] == (
        "А-2",
        1,
        24,
        "прирезка",
        1,
        "C₁",
        14,
        17,
        13.0,
        17.0,
        4.0,
        rows[68][11],
        rows[68][12],
        "висячий бок",
        "Сб",
        "отклонён — C < Сб",
    )
    assert (
        rows[90 + 40][:8] == ("А-3", 1, 41, "пустой прослой", None, "прослой", 12, 17)
        and rows[130][15] == "прослой исключён"
    )
    assert [r[2] for r in rows[90:]] == list(range(1, 46))
    assert blocks[0].rows()[50][0] == 2  # without names — the number of the hole
    single = hole_protocol(lines, p, 1)
    assert [e for b in long_form(lines, p, holes=[1]) for e in b.records()] == single.events
    assert [sorted(set(b.hole.tolist())) for b in long_form(lines, p, holes=[2, 0, 2])] == [[0], [2]]
    assert list(long_form(make_lines([]), p)) == []

    ticks: list[float] = []
    list(long_form(lines, p, chunk=40, tick=ticks.append))
    assert ticks == [0.5, 1.0]

    def cancel(_f: float) -> None:
        raise RuntimeError("cancelled")

    done = []
    with pytest.raises(RuntimeError, match="cancelled"):
        for block in long_form(lines, p, chunk=40, tick=cancel):
            done.append(block)
    assert len(done) == 1  # the first block was handed over, the rest never ran

    with pytest.raises(OreError) as err:
        hole_protocol(lines, p, 5)
    assert err.value.code == "ore_bad_lines"
    with pytest.raises(OreError):
        list(long_form(lines, p, holes=[7]))


def test_marks_and_texts() -> None:
    assert [mark(1, 0, "unit", "", 12), mark(2, 3, "unit", "", 0)] == ["N₁₂", "IN₃"]
    assert [mark(2, 1, "trim", "upper", 0), mark(2, 1, "trim", "lower", 0), mark(2, 3, "trim", "lower", 0)] == [
        "C₁",
        "C₂",
        "C₆",
    ]
    assert [mark(2, 4, "trim", "", 0), mark(2, 2, "gap", "", 0), mark(0, 0, "break", "", 0)] == ["C₄", "Б", ""]
    res = ore_intervals(example_lines("T8.2-14"), example_params("T8.2-14", "soft"), events=True)
    assert res.events is not None
    pr = hole_protocol(example_lines("T8.2-14"), example_params("T8.2-14", "soft"))
    assert [e["text"] for e in pr.events] == [event_text(e) for e in pr.events] and len(pr.events) == len(res.events)
    near = next(e for e in pr.events if e["step"] == 4 and e["decision"] == "not_selected")
    assert (
        near["text"] == "Сближенные интервалы · цепочка · пробы 6–8 · 5–8 м · M 3 · C 1,067 · MC 3,2 — не выбран (Мпп)"
    )
    assert np.all([e["hole"] == 0 for e in pr.events])


def test_lanes_are_first_fit_and_fast() -> None:
    """Review 12.33 № 6: the lanes of the columns of the form are those of the plain first fit, in O(n·lanes·log n)
    — 20 000 records of one hole in well under a second (was O(n²))."""
    import random
    import time

    from bf_core.composites.ore_protocol import _lanes

    def plain(spans: list[tuple[int, int]]) -> tuple[list[int], int]:
        taken: list[list[tuple[int, int]]] = []
        out = []
        for a, b in spans:
            k = next((k for k, lane in enumerate(taken) if all(b < x or a > y for x, y in lane)), len(taken))
            if k == len(taken):
                taken.append([])
            taken[k].append((a, b))
            out.append(k)
        return out, max(1, len(taken))

    rng = random.Random(7)
    for _ in range(200):
        spans = []
        for _k in range(rng.randrange(0, 40)):
            a = rng.randrange(0, 60)
            spans.append((a, a + rng.randrange(0, 8)))
        assert _lanes(spans) == plain(spans), spans
    many = [(2 * i, 2 * i + 1) for i in range(20_000)][::-1] + [(0, 40_000)]
    t0 = time.perf_counter()
    lanes, count = _lanes(many)
    assert time.perf_counter() - t0 < 0.5 and count == 2 and lanes[-1] == 1 and set(lanes[:-1]) == {0}
