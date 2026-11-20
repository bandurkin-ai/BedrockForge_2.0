"""Д-26, the manual edit of the ore intervals of a hole (ТЗ 7.35 «Место»; ADR 0018 §12, решение 24): split,
merge, move by the boundaries of the samples; M, C, MC and the classes counted anew; К, Мпп and the прирезка
checked again and the violations named; a boundary at a waste sample refused; MANUAL; the replay of the edits."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pytest

from bf_core.composites import ore_units as ou
from bf_core.composites.ore_edit import ACTIONS, HoleEdit, edit_hole, warm
from bf_core.composites.ore_intervals import READY_MODES, OreError, OreLines, OreParams, ore_intervals
from bf_core.composites.ore_protocol import hole_protocol, long_form
from tests.oreutil import close, example_lines, example_params, make_lines
from tests.test_ore_simple import _random_holes

LINES = example_lines("T8.2-12")


def edit(*edits: tuple[str, float, float], mode: str = "strict_excl", lines: OreLines = LINES, **over: Any) -> HoleEdit:
    p = example_params("T8.2-12", mode, **over)
    return edit_hole(lines, p, [{"action": a, "from": f, "to": t} for a, f, t in edits])


def top(he: HoleEdit) -> list[tuple[Any, ...]]:
    """(class, from, to, MANUAL) of the rows with NESTED = 0."""
    return [(r["cls"], r["from"], r["to"], r["manual"]) for r in he.rows() if not r["nested"]]


def refused(*edits: tuple[str, float, float], **kw: Any) -> OreError:
    with pytest.raises(OreError) as err:
        edit(*edits, **kw)
    assert err.value.code == "ore_edit_refused" and err.value.message and err.value.hint
    return err.value


AUTO = [
    ("waste", 0.0, 3.0, 0), ("ore", 3.0, 11.0, 0), ("interlayer", 11.0, 17.0, 0), ("ore", 17.0, 22.0, 0),
    ("waste", 22.0, 28.0, 0), ("ore", 28.0, 33.0, 0), ("waste", 33.0, 34.0, 0),
]  # fmt: skip


def test_no_edits_is_the_kernel() -> None:
    """Without edits the rows of a hole are those of the kernel bit for bit — every mode, missing values,
    «пустая порода до L_н», a thickness field, domains."""
    holes = _random_holes(404, 150)
    plain = make_lines(holes)
    rng = np.random.default_rng(1)
    n = len(plain.frm)
    thick = (plain.to - plain.frm) * rng.choice([0.5, 1.0], n)
    rich = OreLines.of(plain.starts, plain.frm, plain.to, plain.grade, thick,
                       np.repeat(rng.integers(-1, 3, n // 5 + 1), 5)[:n])  # fmt: skip
    rows = 0
    for mode in READY_MODES:
        for lines, p in (
            (plain, OreParams(1.0, 2.0, 2.5, mode=mode))
            if mode != "contacts"
            else (rich, OreParams(1.0, 2.0, 2.5, mode=mode)),
            (rich, OreParams(0.6, 3.0, 1.0, c_min=0.9, mode=mode, missing="waste", missing_max=2.5)),
        ):
            for h in range(len(holes)):
                he = edit_hole(lines, p, [], h)
                want = ore_intervals(lines.holes(h), p, nested=True)
                want.iv_i[:, 0] = h
                assert np.array_equal(he.intervals.iv_i, want.iv_i) and np.array_equal(he.intervals.iv_f, want.iv_f)
                assert np.array_equal(he.intervals.sample_cls, want.sample_cls)
                assert np.array_equal(he.intervals.sample_ore_id, want.sample_ore_id)
                assert not he.manual.any() and not he.made.any() and he.violations == [] and he.edits == []
                assert (he.hole, len(he.events())) == (h, 0)
                rows += len(want)
    assert rows > 10000
    assert top(edit()) == AUTO


def test_split_names_the_violation() -> None:
    """Разделить ore 3–11 м Т8.2-12 at 10 м: 3–10 м stays кондиционный, 10–11 м (1 м @ 1,3) does not meet К —
    the violation is named; the two parts touch — by the прирезка they are one interval, named too."""
    he = edit(("split", 3.0, 10.0))
    assert top(he) == [
        ("waste", 0.0, 3.0, 0), ("ore", 3.0, 10.0, 1), ("subgrade", 10.0, 11.0, 1), ("interlayer", 11.0, 17.0, 0),
        ("ore", 17.0, 22.0, 0), ("waste", 22.0, 28.0, 0), ("ore", 28.0, 33.0, 0), ("waste", 33.0, 34.0, 0),
    ]  # fmt: skip
    rows = {(r["cls"], r["from"]): r for r in he.rows() if not r["nested"]}
    upper, lower = rows["ore", 3.0], rows["subgrade", 10.0]
    assert close(upper["m"], 7.0) and close(upper["mc"], 21.4) and close(upper["c"], 21.4 / 7.0)
    assert (upper["basis"], upper["ore_id"], upper["n_samples"], upper["row0"], upper["row1"]) == (
        "thickness",
        1,
        7,
        3,
        9,
    )
    assert close(upper["waste_len"], 2.0) and (lower["m"], lower["mc"], lower["basis"], lower["ore_id"]) == (
        1.0,
        1.3,
        "",
        0,
    )
    assert [r["ore_id"] for r in he.rows() if r["cls"] == "ore" and not r["nested"]] == [1, 2, 3]
    cls = [ou.CLASSES[c] for c in he.intervals.sample_cls]
    assert cls[3:11] == ["ore", "waste_in", "ore", "ore", "ore", "waste_in", "ore", "subgrade"]
    assert he.intervals.sample_ore_id[3:12].tolist() == [1] * 7 + [0, 0]
    assert [(v["code"], v["from"], v["to"]) for v in he.violations] == [("join", 3.0, 11.0), ("not_k", 10.0, 11.0)]
    assert he.violations[1]["message"] == (
        "Интервал 10–11 м не отвечает условию К: M 1 м < Мрт 3 м и MC 1,3 < МС 3 — некондиционный."
    )
    assert he.violations[0]["message"] == (
        "Интервалы 3–10 м и 10–11 м по правилу прирезки объединяются: примыкают друг к другу, "
        "C прирезок 3,057 и 1,3 ≥ Сб 1."
    )
    assert all(v["hint"] for v in he.violations) and he.edits == [{"action": "split", "from": 3.0, "to": 10.0}]
    # the nested rows follow the new interval: its waste, its frame (above 0–3 м; below — none: 10–11 is no waste…)
    nested = [
        (r["cls"], r["from"], r["to"], r["ore_id"], r["manual"]) for r in he.rows() if r["nested"] and r["ore_id"] == 1
    ]
    assert nested == [
        ("waste_in", 4.0, 5.0, 1, 1),
        ("waste_in", 8.0, 9.0, 1, 1),
        ("frame", 0.0, 3.0, 1, 1),
        ("frame", 10.0, 15.0, 1, 1),
    ]

    both = edit(("split", 3.0, 7.0))  # two кондиционных parts: only the прирезка is broken
    assert top(both)[1:3] == [("ore", 3.0, 7.0, 1), ("ore", 7.0, 11.0, 1)]
    assert [(v["code"], v["from"], v["to"]) for v in both.violations] == [("join", 3.0, 11.0)]
    assert [r["ore_id"] for r in both.rows() if r["cls"] == "ore" and not r["nested"]] == [1, 2, 3, 4]
    json.dumps(both.columns("г/т"), allow_nan=False)
    cols = both.columns("г/т")
    assert cols["values"]["LABEL"][1] == "4,0 м @ 3,18 г/т (МС 12,7)" and cols["values"]["CLASS"][0] == "waste"
    assert (
        cols["values"]["MANUAL"] == both.manual.tolist()
        and cols["from"][1] == 3.0
        and len(cols["to"]) == len(both.manual)
    )


def test_merge_through_an_interlayer_is_a_violation() -> None:
    """Объединить 3–11 и 17–22 м через прослой 11–17 м (6 м @ 0,6 > Мпп = 5): the interval is made — 19 м @
    2,147…, as «смягчённый» gives it — and the прослой inside is named."""
    he = edit(("merge", 3.0, 22.0))
    assert top(he) == [
        ("waste", 0.0, 3.0, 0), ("ore", 3.0, 22.0, 1), ("waste", 22.0, 28.0, 0), ("ore", 28.0, 33.0, 0),
        ("waste", 33.0, 34.0, 0),
    ]  # fmt: skip
    soft = next(r for r in ore_intervals(LINES, example_params("T8.2-12", "soft")).rows() if r["cls"] == "ore")
    made = next(r for r in he.rows() if r["cls"] == "ore")
    assert (made["m"], made["mc"], made["waste_len"], made["n_samples"]) == (
        soft["m"],
        soft["mc"],
        soft["waste_len"],
        19,
    )
    assert close(made["m"], 19.0) and close(made["mc"], 40.8) and made["basis"] == "thickness"
    assert [(v["code"], v["from"], v["to"]) for v in he.violations] == [("layer", 3.0, 22.0)]
    assert he.violations[0]["message"] == (
        "В интервале 3–22 м пустой прослой 11–17 м: 6 м @ 0,6 (M > Мпп 5 м, C < Сб 1)."
    )
    cls = [ou.CLASSES[c] for c in he.intervals.sample_cls]
    assert cls[11:17] == ["waste_in", "waste_in", "ore", "waste_in", "waste_in", "waste_in"]  # no `interlayer` now
    assert "interlayer" not in {r["cls"] for r in he.rows()} and he.intervals.sample_ore_id[3:22].tolist() == [1] * 19
    # «смягчённый» does not check the прослой: the same edit of 4–22 м and the сближенные below is clean of it
    far = edit(("merge", 3.0, 33.0), mode="soft")
    assert [v["code"] for v in far.violations] == ["trim"] and top(far)[1] == ("ore", 3.0, 33.0, 1)
    assert far.violations[0]["message"] == "Интервал 3–33 м не получается прирезкой по этапам: 3–22 м."
    # more than two intervals at once; the прирезка of 17–33 м alone leaves only 17–22 м
    wide = edit(("merge", 17.0, 33.0))
    assert [(v["code"], v["from"], v["to"]) for v in wide.violations] == [("layer", 17.0, 33.0), ("trim", 17.0, 33.0)]
    assert wide.violations[0]["message"].startswith("В интервале 17–33 м пустой прослой 20–28 м: 8 м @ 0,525")


def test_a_boundary_at_a_waste_sample_is_refused() -> None:
    """Граница на безрудной пробе — отказ: an interval begins and ends with an ore sample (step 7)."""
    err = refused(("split", 3.0, 5.0))  # the upper part would end with sample 5 (0,8 < Сб)
    assert err.message == (
        "Граница на безрудной пробе 4–5 м: интервал 3–5 м должен начинаться и кончаться рудной пробой "
        "(содержание ≥ Сб)."
    )
    assert refused(("split", 3.0, 8.0)).message.startswith("Граница на безрудной пробе 8–9 м: интервал 8–11 м")
    assert refused(("move", 11.0, 13.0)).message.startswith("Граница на безрудной пробе 12–13 м: интервал 3–13 м")
    assert refused(("move", 3.0, 2.0)).message.startswith("Граница на безрудной пробе 2–3 м: интервал 2–11 м")
    assert refused(("move", 3.0, 4.0)).message.startswith("Граница на безрудной пробе 4–5 м: интервал 4–11 м")
    assert top(edit()) == AUTO  # nothing is kept of a refused edit
    # the tie g = Сб is ore: a boundary there is taken
    tie = make_lines([[(0.0, 1.0, 2.0), (1.0, 2.0, 1.0), (2.0, 3.0, 1.0), (3.0, 4.0, 2.0)]])
    assert top(edit(("split", 0.0, 2.0), lines=tie, m_min=1.0, mc_min=1.0))[:2] == [
        ("ore", 0.0, 2.0, 1),
        ("ore", 2.0, 4.0, 1),
    ]


def test_move_a_boundary() -> None:
    """Сдвинуть: the interval grows over waste or shrinks; what it lets go falls back to the samples; an
    excluded прослой it cuts into falls back too. 3–11 → 3–14 м is the first interval of «строгий»."""
    grown = edit(("move", 11.0, 14.0))
    assert top(grown) == [
        ("waste", 0.0, 3.0, 0), ("ore", 3.0, 14.0, 1), ("waste", 14.0, 17.0, 1), ("ore", 17.0, 22.0, 0),
        ("waste", 22.0, 28.0, 0), ("ore", 28.0, 33.0, 0), ("waste", 33.0, 34.0, 0),
    ]  # fmt: skip
    strict = [r for r in ore_intervals(LINES, example_params("T8.2-12", "strict"), nested=True).rows()]
    assert [(r["cls"], r["from"], r["to"], r["m"], r["mc"], r["nested"], r["ore_id"]) for r in grown.rows()] == [
        (r["cls"], r["from"], r["to"], r["m"], r["mc"], r["nested"], r["ore_id"]) for r in strict
    ]
    assert grown.violations == []  # 14–17 м ≤ Мпп, and joining 17–22 м would bring the прослой back
    # the same pair in «смягчённый»: nothing forbids the join — it is named
    soft = edit(("move", 22.0, 20.0), mode="soft")
    assert top(soft)[1:4] == [("ore", 3.0, 20.0, 1), ("waste", 20.0, 21.0, 1), ("subgrade", 21.0, 22.0, 1)]
    assert [(v["code"], v["from"], v["to"]) for v in soft.violations] == [("join", 3.0, 22.0)]

    shrunk = edit(("move", 11.0, 8.0))
    assert top(shrunk)[:5] == [
        ("waste", 0.0, 3.0, 0), ("ore", 3.0, 8.0, 1), ("waste", 8.0, 9.0, 1), ("subgrade", 9.0, 11.0, 1),
        ("interlayer", 11.0, 17.0, 0),
    ]  # fmt: skip
    assert [(v["code"], v["message"]) for v in shrunk.violations] == [
        ("join", "Интервалы 3–8 м и 9–11 м по правилу прирезки объединяются: промежуток 1 м ≤ Мпп 5 м, "
                 "C прирезок 2,85 и 2,1 ≥ Сб 1."),
    ]  # fmt: skip
    up = edit(("move", 17.0, 13.0))  # the top of 17–22 м goes up into the прослой: 13–14 is ore, the rest falls back
    assert top(up)[2:5] == [("waste", 11.0, 13.0, 1), ("ore", 13.0, 22.0, 1), ("waste", 22.0, 28.0, 0)]
    # the прирезка 13–17 м is 0,6 < Сб: by the stages only 17–22 м stays; joining 3–11 м would bring the прослой back
    assert [(v["code"], v["message"]) for v in up.violations] == [
        ("trim", "Интервал 13–22 м не получается прирезкой по этапам: 17–22 м.")
    ]
    lost = edit(("move", 28.0, 32.0))  # 32–33 м alone is 1 м @ 2,5: некондиционный
    assert top(lost)[5:8] == [("subgrade", 28.0, 29.0, 1), ("waste", 29.0, 32.0, 1), ("subgrade", 32.0, 33.0, 1)]
    assert [(v["code"], v["from"], v["to"]) for v in lost.violations] == [("not_k", 32.0, 33.0)]
    assert [r["ore_id"] for r in lost.rows() if r["cls"] == "ore" and not r["nested"]] == [1, 2]

    assert refused(("move", 11.0, 18.0)).message == "Граница заходит в соседний интервал 17–22 м."
    assert refused(("move", 11.0, 3.0)).message == "Интервал 3–11 м исчезает: граница уходит за его другой конец."
    assert refused(("move", 11.0, 11.0)).message == "Граница уже на этой глубине."
    assert refused(("move", 11.0, 13.5)).message == "Новая граница 13,5 м не совпадает с границей проб."
    assert refused(("move", 12.0, 14.0)).message.startswith("Граница 12 м не найдена")
    assert refused(("split", 4.0, 7.0)).message.startswith("Интервал с началом 4 м не найден")
    assert refused(("split", 3.0, 11.0)).message.startswith("Глубина разреза 11 м не совпадает с границей проб внутри")
    assert refused(("split", 3.0, 6.5)).message.startswith("Глубина разреза 6,5 м")
    assert refused(("merge", 3.0, 11.0)).message.startswith("Объединить 3–11 м нельзя")
    assert refused(("merge", 17.0, 11.0)).message.startswith("Объединить 17–11 м нельзя")
    assert top(edit(("move", 11.0 + 4e-7, 14.0 - 4e-7))) == top(grown)  # depths within 1e−6 м of the boundaries


def test_edits_are_replayed_in_order() -> None:
    """The edits of a hole — the stored ones and the new one — are applied in order to its calculation; two
    intervals that touch: the one that shrinks is moved; an edit that undoes another leaves the calculation."""
    he = edit(("split", 3.0, 7.0), ("move", 7.0, 6.0))  # the upper one shrinks: sample 7 is let go
    assert top(he)[1:4] == [("ore", 3.0, 6.0, 1), ("subgrade", 6.0, 7.0, 1), ("ore", 7.0, 11.0, 1)]
    down = edit(("split", 3.0, 7.0), ("move", 7.0, 9.0))  # the lower one shrinks
    assert top(down)[1:5] == [
        ("ore", 3.0, 7.0, 1),
        ("subgrade", 7.0, 8.0, 1),
        ("waste", 8.0, 9.0, 1),
        ("ore", 9.0, 11.0, 1),
    ]
    assert next(r for r in down.rows() if r["from"] == 9.0)["basis"] == "mc"  # 2 м < Мрт, MC 5,6 ≥ МС
    assert refused(("split", 3.0, 7.0), ("move", 7.0, 8.0)).message.startswith("Граница на безрудной пробе 8–9 м")
    assert len(he.edits) == 2 and he.edits[1] == {"action": "move", "from": 7.0, "to": 6.0}

    back = edit(("split", 3.0, 7.0), ("merge", 3.0, 11.0))
    auto = edit()
    assert np.array_equal(back.intervals.iv_i, auto.intervals.iv_i) and np.array_equal(
        back.intervals.iv_f, auto.intervals.iv_f
    )
    assert top(back) == [(c, f, t, int((f, t) == (3.0, 11.0))) for c, f, t, _ in AUTO]  # made by an edit: MANUAL
    assert back.violations == [] and back.made.sum() == 1
    events = back.events().records()
    assert [(e["step"], e["kind"], e["decision"], e["compared"], e["row0"], e["row1"], e["seq"]) for e in events] == [
        (6, "interval", "ore", "m_min", 3, 10, 1)
    ]
    assert (events[0]["from"], events[0]["to"], events[0]["m"], close(events[0]["c"], 2.8375)) == (3.0, 11.0, 8.0, True)
    chain = edit(("merge", 3.0, 22.0), ("merge", 3.0, 33.0), ("split", 3.0, 19.0), ("move", 19.0, 14.0))
    assert top(chain)[1:4] == [("ore", 3.0, 14.0, 1), ("waste", 14.0, 17.0, 1), ("subgrade", 17.0, 19.0, 1)]
    with pytest.raises(OreError) as err:
        edit_hole(LINES, example_params("T8.2-12", "strict_excl"), [{"action": "cut", "from": 3.0, "to": 7.0}])
    assert err.value.code == "ore_bad_param"
    for bad in (
        {"action": "split", "from": "x", "to": 7.0},
        {"action": "split", "from": 3.0},
        {"action": "move", "from": 3.0, "to": math.inf},
    ):
        with pytest.raises(OreError) as err:
            edit_hole(LINES, example_params("T8.2-12", "strict_excl"), [bad])
        assert err.value.code == "ore_bad_param"
    assert ACTIONS == ("split", "merge", "move")


def test_lines_modes_and_other_holes() -> None:
    """An interval lies in one sampling line; «упрощённый» checks its own rule; «по контактам» has no edit;
    the hole is taken by its index, the samples of its rows are counted from its first one."""
    nan = math.nan
    hole = [
        (0.0, 1.0, 3.0),
        (1.0, 2.0, 2.0),
        (2.0, 3.0, nan),
        (3.0, 4.0, 4.0),
        (4.0, 5.0, 3.0),
        (6.0, 7.0, 5.0),
        (7.0, 8.0, 2.0),
    ]
    lines = make_lines([[(0.0, 1.0, 0.1)], hole])
    p = OreParams(1.0, 2.0, 3.0)
    assert top(edit_hole(lines, p, [], 1)) == [("ore", 0.0, 2.0, 0), ("ore", 3.0, 5.0, 0), ("ore", 6.0, 8.0, 0)]
    for a, b in ((0.0, 5.0), (3.0, 8.0)):
        with pytest.raises(OreError) as err:
            edit_hole(lines, p, [{"action": "merge", "from": a, "to": b}], 1)
        assert err.value.code == "ore_edit_refused" and "пересекает разрыв линии опробования" in err.value.message
    with pytest.raises(OreError) as err:
        edit_hole(lines, p, [{"action": "move", "from": 3.0, "to": 2.0}], 1)  # sample 3 has no grade: no boundary there
    assert err.value.message == "Новая граница 2 м не совпадает с границей проб."
    he = edit_hole(lines, p, [{"action": "split", "from": 3.0, "to": 4.0}], 1)
    assert top(he) == [("ore", 0.0, 2.0, 0), ("ore", 3.0, 4.0, 1), ("ore", 4.0, 5.0, 1), ("ore", 6.0, 8.0, 0)]
    assert [(r["hole"], r["row0"], r["row1"], r["line"]) for r in he.rows() if not r["nested"]] == [
        (1, 0, 1, 0), (1, 3, 3, 1), (1, 4, 4, 1), (1, 5, 6, 2),
    ]  # fmt: skip
    assert len(he.intervals.sample_cls) == 7 and he.intervals.sample_cls[2] == -1  # the sample without a grade
    assert [v["code"] for v in he.violations] == ["join"]  # МС = 2: both parts meet К by MC
    # «пустая порода до L_н»: the stretches counted as waste are pieces — an interval may hold them (FLAG)
    bridged = edit_hole(
        lines,
        OreParams(1.0, 2.0, 3.0, missing="waste", missing_max=1.0),
        [{"action": "split", "from": 0.0, "to": 4.0}],
        1,
    )
    assert top(bridged) == [("ore", 0.0, 4.0, 1), ("ore", 4.0, 8.0, 1)]
    assert [(r["flag"], r["n_samples"], r["m"]) for r in bridged.rows() if not r["nested"]] == [
        (1, 4, 4.0),
        (1, 3, 4.0),
    ]

    simple = edit(("split", 3.0, 7.0), mode="simple")  # «упрощённый» grows 3–33 м @ 1,6
    assert [(v["code"], v["message"]) for v in simple.violations] == [
        ("join", "Интервалы 3–7 м и 7–33 м по правилу наращивания объединяются: среднее содержание 1,6 ≥ Сб 1.")
    ]
    weak = make_lines([[(0.0, 1.0, 1.0), (1.0, 2.0, 0.1), (2.0, 3.0, 1.0), (3.0, 4.0, 0.1), (4.0, 5.0, 9.0)]])
    ps = OreParams(1.0, 2.0, 3.0, mode="simple")
    assert top(edit_hole(weak, ps, [])) == [("subgrade", 0.0, 1.0, 0), ("waste", 1.0, 2.0, 0), ("ore", 2.0, 5.0, 0)]
    grown = edit_hole(weak, ps, [{"action": "merge", "from": 0.0, "to": 5.0}])  # 0–3 м @ 0,7: the growth stops
    assert top(grown) == [("ore", 0.0, 5.0, 1)] and [(v["code"], v["message"]) for v in grown.violations] == [
        ("trim", "Интервал 0–5 м не получается наращиванием: 0–1 м, 2–5 м.")
    ]
    contacts = make_lines([[(0.0, 1.0, 2.0, 0), (1.0, 2.0, 3.0, 0), (2.0, 3.0, 0.5, 1)]], domain=True)
    pc = OreParams(1.0, 1.0, 1.0, mode="contacts", contacts_values=(0,))
    assert top(edit_hole(contacts, pc, [])) == [("ore", 0.0, 2.0, 0), ("waste", 2.0, 3.0, 0)]
    with pytest.raises(OreError) as err:
        edit_hole(contacts, pc, [{"action": "split", "from": 0.0, "to": 1.0}])
    assert err.value.code == "ore_edit_refused" and "По контактам" in err.value.message
    with pytest.raises(OreError) as err:
        edit_hole(lines, p, [], 2)
    assert err.value.code == "ore_bad_lines"
    assert top(edit_hole(make_lines([[]]), p, [])) == []
    with pytest.raises(OreError):
        edit_hole(make_lines([[]]), p, [{"action": "split", "from": 0.0, "to": 1.0}])


def test_random_edits_keep_the_invariants() -> None:
    """Random edits of hard holes (thousands tried, hundreds applied): the rows with NESTED = 0 cover the
    sampling lines once, M and MC are the sums over the samples, every interval made begins and ends with an
    ore sample, `ore` meets К and `subgrade` made by an edit does not, ORE_ID is 1…k, the classes of the samples
    are those of the rows, MANUAL = 0 only on rows of the calculation; a replay gives the same rows."""
    rng = np.random.default_rng(77)
    holes = _random_holes(606, 80)
    lines = make_lines(holes)
    applied = refused_n = 0
    codes: set[str] = set()
    for mode in ("strict_excl", "soft", "simple"):
        p = OreParams(1.0, 2.0, 2.5, mode=mode).checked()
        for h, hole in enumerate(holes):
            auto = edit_hole(lines, p, [], h)
            calc = {(r["cls"], r["nested"], r["from"], r["to"]) for r in auto.rows()}
            bounds = sorted({x for f, t, _ in hole for x in (f, t)})
            edits: list[dict[str, Any]] = []
            he = auto
            for _ in range(12):
                ivs = [r for r in he.rows() if not r["nested"] and r["cls"] in ("ore", "subgrade")]
                if not ivs or len(bounds) < 2:
                    break
                action = ACTIONS[int(rng.integers(3))]
                one = ivs[int(rng.integers(len(ivs)))]
                if action == "merge":
                    two = ivs[min(len(ivs) - 1, ivs.index(one) + int(rng.integers(1, 3)))]
                    new = {"action": action, "from": one["from"], "to": two["to"]}
                else:
                    start = one["from"] if action == "split" or rng.random() < 0.5 else one["to"]
                    new = {"action": action, "from": start, "to": float(rng.choice(bounds))}
                try:
                    he = edit_hole(lines, p, [*edits, new], h)
                except OreError as err:
                    assert err.code == "ore_edit_refused"
                    refused_n += 1
                    continue
                edits.append(new)
                applied += 1
                codes |= {v["code"] for v in he.violations}
                rows = he.rows()
                flat = [r for r in rows if not r["nested"]]
                in_line = he.intervals.sample_state == ou.ROW_PIECE
                length = [t - f for f, t, _ in hole]
                assert close(
                    sum(r["m"] for r in flat), sum(x for x, ok in zip(length, in_line, strict=True) if ok), 1e-11
                )
                assert all(a["to"] <= b["from"] for a, b in zip(flat, flat[1:], strict=False))
                number = 0
                for r in flat:
                    mine = [k for k in range(r["row0"], r["row1"] + 1) if in_line[k]]
                    m = sum(length[k] for k in mine)
                    mc = sum(length[k] * hole[k][2] for k in mine)
                    assert close(r["m"], m, 1e-11) and close(r["mc"], mc, 1e-11) and r["n_samples"] == len(mine)
                    want = {ou.CLASSES[c] for c in he.intervals.sample_cls[mine].tolist()}
                    assert want <= ({"ore", "waste_in"} if r["cls"] == "ore" else {r["cls"]})
                    ids = set(he.intervals.sample_ore_id[mine].tolist())
                    if r["cls"] == "ore":
                        number += 1
                        k_met = r["c"] >= p.c_min - 1e-9 and (r["m"] >= p.m_min - 1e-9 or r["mc"] >= p.mc_min - 1e-9)  # type: ignore[operator]
                        assert (r["ore_id"], ids) == (number, {number}) and (k_met or not r["manual"])
                        assert hole[mine[0]][2] >= p.cutoff - 1e-9 and hole[mine[-1]][2] >= p.cutoff - 1e-9
                        assert (r["basis"] != "") == k_met or not r["manual"]
                    else:
                        assert (r["ore_id"], ids, r["basis"]) == (0, {0}, "")
                assert all(r["manual"] or (r["cls"], r["nested"], r["from"], r["to"]) in calc for r in rows)
                assert all(r["manual"] for r, made in zip(rows, he.made.tolist(), strict=True) if made)
                again = edit_hole(lines, p, edits, h)
                assert np.array_equal(again.intervals.iv_f, he.intervals.iv_f) and again.violations == he.violations
    assert applied > 250 and refused_n > 400 and codes == {"not_k", "layer", "trim", "join"}


def test_protocol_of_an_edited_hole() -> None:
    """The protocol of a result with manual edits is its calculation and its edits run again: «Итог», the
    classes of the samples and `intervals` are the rows after the edits (MANUAL), the intervals they made are
    the records of step 6 of the long form, the violations come along."""
    p = example_params("T8.2-12", "strict_excl")
    edits = [{"action": "merge", "from": 3.0, "to": 22.0}, {"action": "split", "from": 3.0, "to": 10.0}]
    he = edit_hole(LINES, p, edits)
    pr = hole_protocol(LINES, p, unit="г/т", edits=edits)
    plain = hole_protocol(LINES, p, unit="г/т")
    assert pr.intervals == he.rows() and pr.edits == he.edits and pr.violations == he.violations
    assert [c["key"] for c in pr.columns] == [c["key"] for c in plain.columns]
    final = sorted((c for c in pr.cells if c["col"] == "final"), key=lambda c: c["row0"])
    assert [(c["tone"], c["from"], c["to"], c["manual"], c["text"]) for c in final][:4] == [
        ("waste", 0.0, 3.0, 0, "безрудный 3,0/0,22/0,7"),
        ("ore", 3.0, 10.0, 1, "7,0 м @ 3,06 г/т (МС 21,4) — правка"),
        ("ore", 10.0, 22.0, 1, "12,0 м @ 1,62 г/т (МС 19,4) — правка"),
        ("waste", 22.0, 28.0, 0, "безрудный 6,0/0,23/1,4"),
    ]
    assert [r["cls"] for r in pr.rows[9:14]] == ["ore", "ore", "waste_in", "waste_in", "ore"]
    assert [r["ore_id"] for r in pr.rows[9:12]] == [1, 2, 2] and plain.rows[11]["cls"] == "interlayer"
    assert pr.events[: len(plain.events)] == plain.events  # the decisions of the kernel stay as they were
    extra = pr.events[len(plain.events) :]
    assert [(e["step"], e["mark"], e["decision"], e["row0"], e["row1"], e["seq"], e["n"]) for e in extra] == [
        (6, "правка", "ore", 3, 9, 1, len(plain.events) + 1), (6, "правка", "ore", 10, 21, 2, len(plain.events) + 2),
    ]  # fmt: skip
    assert (
        extra[0]["text"]
        == "Ручная правка · правка · пробы 4–10 · 3–10 м · M 7 · C 3,057 · MC 21,4 — кондиционный (Мрт)"
    )
    # 10–22 м holds the прослой 11–17 м and is not what the прирезка gives; joining 3–10 м would not mend it
    assert [(v["code"], v["from"], v["to"]) for v in pr.violations] == [("layer", 10.0, 22.0), ("trim", 10.0, 22.0)]
    json.dumps(pr.as_dict(), allow_nan=False)
    assert pr.as_dict()["edits"] == he.edits and plain.as_dict()["edits"] == [] and plain.violations == []
    assert all(r["manual"] == 0 for r in plain.intervals) and all(c["manual"] == 0 for c in plain.cells)

    # the long form of a set: the records of step 6 follow those of their hole; the other holes are untouched
    lines = make_lines([[(float(k), k + 1.0, g) for k, g in enumerate(LINES.grade.tolist())]] * 3)
    blocks = list(long_form(lines, p, edits={1: edits, 2: []}, chunk=40))
    records = [e for b in blocks for e in b.records()]
    mine = [e for e in records if e["hole"] == 1]
    assert mine == [{**e, "hole": 1} for e in pr.events]
    assert [e for e in records if e["hole"] == 0] == plain.events
    assert [e for e in records if e["hole"] == 2] == [{**e, "hole": 2} for e in plain.events]
    rows = [r for b in blocks for r in b.rows(["А", "Б", "В"])]
    assert rows[45 + 45][:8] == ("Б", 1, 46, "ручная правка", None, "правка", 4, 10) and rows[90][15] == "кондиционный"
    assert [e for b in long_form(lines, p, holes=[1], edits={1: edits}) for e in b.records()] == mine
    with pytest.raises(OreError) as err:  # stored edits that no longer apply: nothing is answered silently
        hole_protocol(LINES, p, edits=[{"action": "split", "from": 4.0, "to": 7.0}])
    assert err.value.code == "ore_edit_refused"


def test_warm_runs() -> None:
    """The warm-up of `composites/warm.py` applies its edits (a refused one would stop the warm-up of the app)."""
    warm()
