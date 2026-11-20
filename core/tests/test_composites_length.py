"""Composites by length (ТЗ 8.2.2, 8.2.4; ADR 0018 §6): Т8.2-1, Т8.2-2 — the value of a composite;
Т8.2-3, Т8.2-4 — the partition and the batch over a set of holes.

Tolerance — 1e−12 relative (8.2.4) unless a check says «exact»."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites import intervals as iv
from bf_core.composites import length as cl
from bf_core.composites.intervals import CompositeError

NAN = math.nan
REL = 1e-12


def table(frm: list[float], to: list[float], *cols: list[float]) -> tuple[iv.Lines, np.ndarray]:
    """One hole: its lines and the numeric fields [n, n_fields]."""
    lines = iv.build_lines([1] * len(frm), frm, to)
    return lines, iv.stack_fields(lines, [np.array(c, dtype=np.float64) for c in cols])


def by_two(end: float = 6.0) -> iv.Lines:
    """Composites of 2 m from the collar to `end`."""
    return iv.targets(iv.build_lines([1], [0.0], [end]), 2.0).lines


# --- Т8.2-1 ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("row_3_4", ["absent", "empty"])
def test_t8_2_1_gap_inside_a_composite(row_3_4: str) -> None:
    """Т8.2-1. Пробы 0–1: 1,0; 1–3: 2,0; 3–4: нет; 4–6: 4,0; L = 2 от устья, p_min = 0,5 →
    композиты 1,5; 2,0 (опробовано 1 м); 4,0. «Нет» — the row is absent or has no value: the same."""
    if row_3_4 == "absent":
        lines, vals = table([0.0, 1.0, 4.0], [1.0, 3.0, 6.0], [1.0, 2.0, 4.0])
    else:
        lines, vals = table([0.0, 1.0, 3.0, 4.0], [1.0, 3.0, 4.0, 6.0], [1.0, 2.0, NAN, 4.0])
    tg = by_two()
    assert (tg.frm.tolist(), tg.to.tolist()) == ([0.0, 2.0, 4.0], [2.0, 4.0, 6.0])  # exact
    v = cl.composite_values(lines, vals, tg, p_min=0.5)
    assert v.values[:, 0] == pytest.approx([1.5, 2.0, 4.0], rel=REL)
    assert v.sampled == pytest.approx([2.0, 1.0, 2.0], rel=REL)  # SAMPLED_LEN: 1 m in 2–4
    assert v.count.tolist() == [2, 1, 1] and not v.low.any()  # 1 m = p_min·2 m is enough
    assert np.isnan(v.density).all() and v.texts == () and v.notes() == []


def test_missing_value_inside_a_composite_does_not_become_zero() -> None:
    lines, vals = table([0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0], [3.0, NAN, NAN, 5.0], [NAN, NAN, NAN, NAN])
    whole = iv.build_lines([1], [0.0], [4.0])
    v = cl.composite_values(lines, vals, whole, p_min=0.5)
    assert v.values[0, 0] == pytest.approx(4.0, rel=REL)  # (3 + 5) / 2, not (3 + 0 + 0 + 5) / 4
    assert math.isnan(v.values[0, 1])  # a field without a single value: NaN, not 0
    assert v.sampled[0] == pytest.approx(2.0, rel=REL) and v.count[0] == 2
    assert v.low.tolist() == [True]  # the second field is short of p_min
    # p_min: 2 m of 4 pass at 0.5 and fail at 0.6 — NaN and the flag
    strict = cl.composite_values(lines, vals[:, :1], whole, p_min=0.6)
    assert math.isnan(strict.values[0, 0]) and strict.low.tolist() == [True]
    # unsampled = 0 is an explicit choice: the 2 m without a value count as 0; the flag stays
    zero = cl.composite_values(lines, vals[:, :1], whole, p_min=0.6, unsampled=0.0)
    assert zero.values[0, 0] == pytest.approx(2.0, rel=REL) and zero.low.tolist() == [True]
    half = cl.composite_values(lines, vals, whole, p_min=0.0, unsampled=0.5)
    assert half.values[0].tolist() == pytest.approx([2.25, 0.5], rel=REL) and not half.low.any()
    # a composite outside the samples: NaN by default, the given value otherwise
    far = iv.build_lines([1], [10.0], [12.0])
    assert math.isnan(cl.composite_values(lines, vals[:, :1], far).values[0, 0])
    assert cl.composite_values(lines, vals[:, :1], far, unsampled=0.0).values[0, 0] == 0.0
    assert cl.composite_values(lines, vals[:, :1], far).count[0] == 0


# --- Т8.2-2 ------------------------------------------------------------------------------------------------


def test_t8_2_2_weighting_by_density() -> None:
    """Т8.2-2. Т8.2-1 с ρ = 2,5 (0–1) и 3,0 (1–3), взвешивание по плотности: 0–2 — 8,5/5,5 = 1,545454…,
    ρ = 2,75."""
    lines, vals = table([0.0, 1.0, 4.0], [1.0, 3.0, 6.0], [1.0, 2.0, 4.0])
    rho = np.array([2.5, 3.0, NAN])
    v = cl.composite_values(lines, vals, by_two(), density=rho, p_min=0.5)
    assert v.values[0, 0] == pytest.approx(8.5 / 5.5, rel=REL)
    assert v.values[0, 0] == pytest.approx(1.5454545454545454, rel=REL)
    assert v.density[0] == pytest.approx(2.75, rel=REL)
    assert v.values[1, 0] == pytest.approx(2.0, rel=REL) and v.density[1] == pytest.approx(3.0, rel=REL)
    # 4–6 has a grade and no density at all: the grade is kept (w = 1), the density stays empty
    assert v.values[2, 0] == pytest.approx(4.0, rel=REL) and math.isnan(v.density[2])
    assert v.n_no_density == 1 and "без плотности — 1" in v.notes()[0]
    plain = cl.composite_values(lines, vals, by_two(), density=rho, by_density=False, p_min=0.5)
    assert plain.values[0, 0] == pytest.approx(1.5, rel=REL) and plain.density[0] == pytest.approx(2.75, rel=REL)
    assert plain.n_no_density == 0


def test_density_measured_on_a_part_of_the_samples() -> None:
    lines, vals = table([0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 6.0, NAN])
    rho = np.array([2.0, 4.0, NAN, 3.5])
    v = cl.composite_values(lines, vals, iv.build_lines([1], [0.0], [4.0]), density=rho)
    mean_rho = (2.0 + 4.0 + 3.5) / 3.0  # ρ_c: the sample 3–4 has a density though no grade
    assert v.density[0] == pytest.approx(mean_rho, rel=REL)
    # the sample 2–3 (grade 6, no density) takes ρ_c; it is neither dropped nor weighted by 0
    assert v.values[0, 0] == pytest.approx((2.0 * 1 + 4.0 * 2 + mean_rho * 6) / (2.0 + 4.0 + mean_rho), rel=REL)
    assert v.n_no_density == 1
    bad = cl.composite_values(lines, vals, iv.build_lines([1], [0.0], [4.0]), density=[2.0, 4.0, 0.0, -1.0])
    assert bad.values[0, 0] == pytest.approx((2.0 * 1 + 4.0 * 2 + 3.0 * 6) / 9.0, rel=REL)  # ρ ≤ 0 is not a weight


# --- text, flags, parameters, summary ----------------------------------------------------------------------


def test_text_fields_take_the_mode_by_length() -> None:
    lines, vals = table([0.0, 1.0, 2.0, 3.5, 5.0], [1.0, 2.0, 3.5, 5.0, 6.0], [1.0, 1.0, 1.0, 1.0, 1.0])
    lith = np.array([1, 0, 0, -1, 2], dtype=np.int32)  # codes of a text field, −1 — empty
    zone = np.array([-1, -1, -1, -1, -1], dtype=np.int32)
    v = cl.composite_values(lines, vals, by_two(), texts=[lith, zone])
    # 0–2: 1 m of each — the first; 2–4: 1.5 m of code 0 against an empty 0.5 m; 4–6: empty 1 m, code 2 — 1 m
    assert v.texts[0].tolist() == [1, 0, 2] and v.texts[0].dtype == np.int32
    assert v.texts[1].tolist() == [-1, -1, -1]
    ticks: list[float] = []
    cl.composite_values(lines, vals, by_two(), texts=[lith, zone], tick=ticks.append)
    assert ticks == sorted(ticks) and ticks[-1] == pytest.approx(1.0)


def test_flag_column() -> None:
    low = np.array([False, True, True, False])
    flat = np.array([False, False, True, True])
    flags = cl.flag_column([(cl.FLAG_LOW, low), ("пологая", flat)], 4)
    assert flags.tolist() == [None, "мало проб", "мало проб; пологая", "пологая"]
    assert cl.flag_column([], 2).tolist() == [None, None]


def test_parameters_are_checked_against_the_ranges() -> None:
    p = cl.LengthParams.of({"length": 5, "remainder": "equal", "p_min": None, "unsampled": 0, "table": "Пробы"})
    assert p == cl.LengthParams(5.0, "equal", 0.5, 0.5, "domain", 0.0) and isinstance(p.length, float)
    assert cl.LengthParams.of({}) == cl.LengthParams(2.0, "attach", 0.5, 0.5, "domain", None)
    for raw, param in (
        ({"length": 0.05}, "length"),
        ({"length": 100.5}, "length"),
        ({"length": "два"}, "length"),
        ({"length": NAN}, "length"),
        ({"length": True}, "length"),
        ({"f_min": 1.2}, "f_min"),
        ({"p_min": -0.1}, "p_min"),
        ({"remainder": "split"}, "remainder"),
        ({"origin": "bottom"}, "origin"),
        ({"unsampled": math.inf}, "unsampled"),
    ):
        with pytest.raises(CompositeError) as e:
            cl.LengthParams.of(raw)
        err = e.value
        assert err.code == "bad_param" and err.param == param and err.message and err.hint
        assert set(err.as_dict()) == {"code", "message", "hint"}
    with pytest.raises(CompositeError, match="от 0,1 до 100 м"):
        cl.LengthParams(length=0.0).checked()
    assert cl.LengthParams.of({"length": 0.1, "f_min": 0, "p_min": 1}).length == 0.1  # the ends of a range pass


def test_summary_before_and_after() -> None:
    lines, vals = table([0.0, 1.0, 3.0, 4.0], [1.0, 3.0, 4.0, 6.0], [1.0, 2.0, NAN, 4.0], [NAN, NAN, NAN, NAN])
    before = cl.summary(lines.lengths(), vals, ["AU", "AG"])
    assert before["count"] == 4 and before["length"] == 6.0
    assert before["fields"]["AU"] == {"mean": pytest.approx(13.0 / 5.0, rel=REL), "metal": pytest.approx(13.0, rel=REL)}
    assert before["fields"]["AG"] == {"mean": None, "metal": None}  # no value: not a zero
    tg = by_two()
    v = cl.composite_values(lines, vals, tg, p_min=0.5)
    after = cl.summary(tg.lengths(), v.values, ["AU", "AG"])
    assert after["count"] == 3 and after["fields"]["AU"]["metal"] == pytest.approx(15.0, rel=REL)  # 2–4: 2 m @ 2
    rng = np.random.default_rng(12345)
    ln, g = rng.random(1000) + 0.1, 10.0 ** rng.uniform(-3, 3, size=(1000, 2))
    big = cl.summary(ln, g, ["A", "B"])
    assert big["length"] == pytest.approx(math.fsum(ln), rel=REL)
    assert big["fields"]["B"]["metal"] == pytest.approx(math.fsum(ln * g[:, 1]), rel=REL)
    assert cl.summary([], np.empty((0, 1)), ["A"]) == {
        "count": 0,
        "length": 0.0,
        "fields": {"A": {"mean": None, "metal": None}},
    }


# --- Т8.2-3: the partition ---------------------------------------------------------------------------------


def metres(depth: float, step: float = 1.0) -> tuple[iv.Lines, np.ndarray]:
    """A hole sampled by `step` from the collar to `depth` (the last sample is shorter), grade = sample number."""
    frm = np.arange(0.0, depth, step)
    to = np.minimum(frm + step, depth)
    return table(list(frm), list(to), list(np.arange(len(frm)) + 1.0))


def bounds(c: cl.Composites) -> tuple[list[float], list[float]]:
    return c.lines.frm.tolist(), c.lines.to.tolist()


def test_t8_2_3_remainder_and_equal_parts() -> None:
    """Т8.2-3. Скважина 7 м, L = 2: остаток 1 м — отдельный композит; 6,8 м — последний композит 4–6,8;
    «равные» для 7 м — 4 × 1,75 м. Boundaries — exact."""
    seven = cl.composite_by_length(*metres(7.0), cl.LengthParams(length=2.0))
    assert bounds(seven) == ([0.0, 2.0, 4.0, 6.0], [2.0, 4.0, 6.0, 7.0])  # r = 1 = f_min·L: its own composite
    assert seven.values.values[:, 0] == pytest.approx([1.5, 3.5, 5.5, 7.0], rel=REL)
    assert seven.journal == [] and seven.report["composites"] == 4 and seven.stretch.tolist() == [0, 0, 0, 0]
    short = cl.composite_by_length(*metres(6.8), cl.LengthParams(length=2.0))
    assert bounds(short) == ([0.0, 2.0, 4.0], [2.0, 4.0, 6.8])  # r = 0.8 < 1: joined to the last one
    assert short.values.values[2, 0] == pytest.approx((5 + 6 + 0.8 * 7) / 2.8, rel=REL)
    equal = cl.composite_by_length(*metres(7.0), cl.LengthParams(length=2.0, remainder="equal"))
    assert bounds(equal) == ([0.0, 1.75, 3.5, 5.25], [1.75, 3.5, 5.25, 7.0])
    assert equal.lines.lengths().tolist() == [1.75] * 4
    # «отбросить»: the 0.8 m go, with a line in the journal
    drop = cl.composite_by_length(*metres(6.8), cl.LengthParams(length=2.0, remainder="drop"))
    assert bounds(drop) == ([0.0, 2.0, 4.0], [2.0, 4.0, 6.0])
    assert drop.report["cut"] == 1 and drop.report["cut_len"] == pytest.approx(0.8, abs=1e-12)
    assert "отброшено — 1" in drop.journal[0] and "0,80 м" in drop.journal[0]
    # f_min = 0.3: 0.8 ≥ 0.6 — a composite of its own
    own = cl.composite_by_length(*metres(6.8), cl.LengthParams(length=2.0, f_min=0.3))
    assert bounds(own) == ([0.0, 2.0, 4.0, 6.0], [2.0, 4.0, 6.0, 6.8])


def test_hole_shorter_than_the_remainder_goes_to_the_journal() -> None:
    """n0 = 0 and r < f_min·L: no composite, and it is said in the journal (not silently)."""
    hole = [1, 1, 2, 3, 3, 3]
    lines = iv.build_lines(hole, [0.0, 0.5, 0.0, 0.0, 1.0, 2.0], [0.5, 0.9, 1.0, 1.0, 2.0, 3.0])
    vals = iv.stack_fields(lines, [np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])])
    c = cl.composite_by_length(lines, vals, cl.LengthParams(length=2.0))
    # hole 1: 0.9 m < 1 m — nothing; hole 2: 1 m = f_min·L — a composite; hole 3: 0–3 (r = 1 → its own)
    assert c.lines.holes.tolist() == [2, 3] and bounds(c) == ([0.0, 0.0, 2.0], [1.0, 2.0, 3.0])
    assert c.stretch.tolist() == [1, 2, 2]
    assert c.report["short"] == 1 and c.report["short_len"] == pytest.approx(0.9, abs=1e-12)
    assert "Скважин короче f_min·L — 1" in c.journal[0] and "0,90 м" in c.journal[0]


def test_origin_and_the_depth_of_the_hole() -> None:
    lines, vals = table([12.4, 13.4, 14.4, 15.4], [13.4, 14.4, 15.4, 16.4], [1.0, 2.0, 3.0, 4.0])
    first = cl.composite_by_length(lines, vals, cl.LengthParams(length=2.0, origin="first"))
    assert bounds(first) == ([12.4, 14.4], [14.4, 16.4])  # from the top of the first sample
    assert first.values.values[:, 0] == pytest.approx([1.5, 3.5], rel=REL)
    for origin in ("domain", "collar"):  # the stretch of a whole hole starts at the collar
        c = cl.composite_by_length(lines, vals, cl.LengthParams(length=2.0, origin=origin))
        assert bounds(c)[0] == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0] and bounds(c)[1][-1] == 16.4
        assert c.values.count.tolist() == [0, 0, 0, 0, 0, 0, 2, 3]
        assert np.isnan(c.values.values[:6, 0]).all()  # unsampled: no value, not 0
        assert c.values.values[6, 0] == pytest.approx((1.0 * 1.0 + 0.6 * 2.0) / 1.6, rel=REL)  # 12–14: 1.6 m
        assert c.values.low.tolist() == [True] * 6 + [False, False]
        assert c.report["empty"] == 6 and "непробованных участках — 6: без значений (не ноль)" in c.journal[0]
    zero = cl.composite_by_length(lines, vals, cl.LengthParams(length=2.0, unsampled=0.0, p_min=0.0))
    assert zero.values.values[:6, 0].tolist() == [0.0] * 6 and "значение 0" in zero.journal[0]
    assert zero.values.values[6, 0] == pytest.approx((1.0 * 1.0 + 0.6 * 2.0) / 2.0, rel=REL)
    # the depth of the hole (collar table): 20 m — the composites go on to the bottom; unknown — the last TO
    deep = cl.composite_by_length(lines, vals, cl.LengthParams(length=2.0, origin="first"), ends=[20.0])
    assert deep.lines.frm == pytest.approx([12.4, 14.4, 16.4, 18.4], abs=1e-12)  # the remainder 1.6 ≥ 1: its own
    assert deep.lines.to == pytest.approx([14.4, 16.4, 18.4, 20.0], abs=1e-12) and deep.lines.to[-1] == 20.0
    assert deep.values.count.tolist() == [2, 2, 0, 0]
    for unknown in ([NAN], [5.0]):
        c = cl.composite_by_length(lines, vals, cl.LengthParams(length=2.0, origin="first"), ends=unknown)
        assert bounds(c) == bounds(first)


def test_t8_2_1_through_the_batch() -> None:
    """Т8.2-1 as the job runs it: «L = 2 от устья, p_min = 0,5»."""
    lines, vals = table([0.0, 1.0, 4.0], [1.0, 3.0, 6.0], [1.0, 2.0, 4.0])
    c = cl.composite_by_length(lines, vals, cl.LengthParams(length=2.0, origin="collar", p_min=0.5))
    assert bounds(c) == ([0.0, 2.0, 4.0], [2.0, 4.0, 6.0])
    assert c.values.values[:, 0] == pytest.approx([1.5, 2.0, 4.0], rel=REL)
    assert c.values.sampled == pytest.approx([2.0, 1.0, 2.0], rel=REL) and not c.values.low.any()


def test_given_stretches_are_cut_from_their_start_or_by_a_grid() -> None:
    """Stretches of domains (12.4 gives them): composites do not leave a stretch; "collar" and "first" count
    the grid of L from the collar or the top of the first sample of the hole."""
    lines, vals = metres(20.0)
    lines = iv.build_lines([1] * 19, lines.frm[1:], lines.to[1:])  # the first sample starts at 1 m
    vals = vals[1:]
    st = iv.build_lines([1, 1], [3.0, 12.0], [12.0, 19.0])
    own = cl.composite_by_length(lines, vals, cl.LengthParams(length=4.0), stretches=st)
    # 3–12: n0 = 2, r = 1 < 2 joins the last one; 12–19: n0 = 1, r = 3 ≥ 2 — its own
    assert bounds(own) == ([3.0, 7.0, 12.0, 16.0], [7.0, 12.0, 16.0, 19.0]) and own.stretch.tolist() == [0, 0, 1, 1]
    collar = cl.composite_by_length(lines, vals, cl.LengthParams(length=4.0, origin="collar"), stretches=st)
    assert bounds(collar) == ([3.0, 8.0, 12.0, 16.0], [8.0, 12.0, 16.0, 19.0])  # 3–4 joins 4–8; 16–19 its own
    first = cl.composite_by_length(lines, vals, cl.LengthParams(length=4.0, origin="first"), stretches=st)
    # the grid 1, 5, 9, 13, 17: 3–5 (2 m) is its own; 12–13 (1 m) joins 13–17; 17–19 is its own
    assert bounds(first) == ([3.0, 5.0, 9.0, 12.0, 17.0], [5.0, 9.0, 12.0, 17.0, 19.0])
    assert first.stretch.tolist() == [0, 0, 0, 1, 1]
    assert "Участков" in cl.composite_by_length(lines, vals, cl.LengthParams(length=100.0), stretches=st).journal[0]
    other = iv.build_lines([7], [0.0], [5.0])  # a stretch in a hole without samples: from its own start, no values
    c = cl.composite_by_length(lines, vals, cl.LengthParams(length=4.0, origin="first"), stretches=other)
    assert bounds(c) == ([0.0], [5.0]) and math.isnan(c.values.values[0, 0]) and c.values.count[0] == 0


# --- Т8.2-4: balance ---------------------------------------------------------------------------------------


def continuous(rng: np.random.Generator, holes: int, per_hole: int) -> tuple[np.ndarray, ...]:
    """Continuous sampling from the collar: (hole, frm, to, values [n, 3], density)."""
    n = holes * per_hole
    hole = np.repeat(np.arange(holes, dtype=np.int64) + 100, per_hole)
    step = rng.choice([0.3, 0.5, 1.0, 1.2, 2.5], size=(holes, per_hole))
    to = np.cumsum(step, axis=1)
    frm = np.concatenate((np.zeros((holes, 1)), to[:, :-1]), axis=1)
    return hole, frm.reshape(-1), to.reshape(-1), 10.0 ** rng.uniform(-2, 3, size=(n, 3)), rng.uniform(2.4, 3.4, size=n)


@pytest.mark.parametrize(
    "params",
    [
        cl.LengthParams(length=2.0),
        cl.LengthParams(length=2.0, remainder="equal"),
        cl.LengthParams(length=5.0, f_min=0.0, origin="first"),
        cl.LengthParams(length=0.7, f_min=1.0, p_min=1.0),
    ],
)
def test_t8_2_4_metal_and_length_are_kept(params: cl.LengthParams) -> None:
    """Т8.2-4. Сплошное опробование без отбрасывания: Σ L_c·g_c = Σ l_i·g_i, суммарные длины равны (1e−12
    отн.) — in total and in every hole."""
    rng = np.random.default_rng(12345)
    hole, frm, to, g, rho = continuous(rng, holes=400, per_hole=60)
    lines = iv.build_lines(hole, frm, to)
    c = cl.composite_by_length(lines, g, params, threads=2)
    lc, li = c.lines.lengths(), to - frm
    assert c.report["short"] == 0 and c.report["cut"] == 0 and not c.values.low.any()
    assert math.fsum(lc) == pytest.approx(math.fsum(li), rel=REL)
    for f in range(3):
        assert math.fsum(lc * c.values.values[:, f]) == pytest.approx(math.fsum(li * g[:, f]), rel=REL)
    ch, per = c.lines.hole_ids(), 60
    for k in (0, 17, 399):
        mine = ch == 100 + k
        rows = slice(k * per, (k + 1) * per)
        assert math.fsum(lc[mine]) == pytest.approx(math.fsum(li[rows]), rel=REL)
        assert math.fsum(lc[mine] * c.values.values[mine, 0]) == pytest.approx(
            math.fsum(li[rows] * g[rows, 0]), rel=REL
        )
    # the same with density weights: the mass of metal Σ L_c·ρ_c·g_c = Σ l_i·ρ_i·g_i
    w = cl.composite_by_length(lines, g, params, density=rho)
    assert math.fsum(lc * w.values.density) == pytest.approx(math.fsum(li * rho), rel=REL)
    for f in range(3):
        mass = math.fsum(lc * w.values.density * w.values.values[:, f])
        assert mass == pytest.approx(math.fsum(li * rho * g[:, f]), rel=REL)
    # «до/после» of the window agree as well
    before, after = cl.summary(li, g, ["A", "B", "C"]), cl.summary(lc, c.values.values, ["A", "B", "C"])
    assert after["length"] == pytest.approx(before["length"], rel=REL)
    assert after["fields"]["B"]["metal"] == pytest.approx(before["fields"]["B"]["metal"], rel=REL)


# --- the batch: order of the holes, threads, cancellation --------------------------------------------------


def _batch(hole: np.ndarray, frm: np.ndarray, to: np.ndarray, g: np.ndarray, rho: np.ndarray, threads: int) -> bytes:
    lines = iv.build_lines(hole, frm, to)
    vals = iv.stack_fields(lines, list(g.T))
    codes = (g[:, 0] > 5.0).astype(np.int32) - (g[:, 1] > 500.0)
    c = cl.composite_by_length(
        lines, vals, cl.LengthParams(length=2.0, f_min=0.4), density=lines.take(rho), texts=[lines.take(codes)],
        ends=np.full(lines.n_holes, 70.0), threads=threads,
    )  # fmt: skip
    v = c.values
    parts = (c.lines.holes, c.lines.starts, c.lines.frm, c.lines.to, c.stretch, v.values, v.density, v.sampled)
    return b"".join(a.tobytes() for a in (*parts, v.count, v.low, v.texts[0]))


def test_batch_does_not_depend_on_threads_or_the_order_of_holes() -> None:
    rng = np.random.default_rng(12345)
    hole, frm, to, g, rho = continuous(rng, holes=9000, per_hole=8)  # 3 strips of holes, 72 thousand samples
    g[rng.random(g.shape) < 0.05] = NAN
    rho[rng.random(len(rho)) < 0.5] = NAN
    base = _batch(hole, frm, to, g, rho, 1)
    for threads in (2, 4, 8):
        assert _batch(hole, frm, to, g, rho, threads) == base
    p = rng.permutation(len(hole))
    assert _batch(hole[p], frm[p], to[p], g[p], rho[p], 4) == base


def test_batch_reports_progress_and_can_be_cancelled() -> None:
    rng = np.random.default_rng(1)
    hole, frm, to, g, _rho = continuous(rng, holes=9000, per_hole=4)
    lines = iv.build_lines(hole, frm, to)
    seen: list[float] = []
    cl.composite_by_length(lines, g, cl.LengthParams(), texts=[np.zeros(lines.n, dtype=np.int32)], tick=seen.append)
    assert seen == sorted(seen) and seen[0] > 0.0 and seen[-1] == pytest.approx(1.0) and len(seen) >= 12

    class Cancelled(Exception):
        pass

    def stop(fraction: float) -> None:
        if fraction > 0.5:
            raise Cancelled()

    with pytest.raises(Cancelled):
        cl.composite_by_length(lines, g, cl.LengthParams(), threads=4, tick=stop)
    with pytest.raises(CompositeError):
        cl.composite_by_length(lines, g, cl.LengthParams(length=0.01))
