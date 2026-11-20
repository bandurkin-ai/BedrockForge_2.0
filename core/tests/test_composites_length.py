"""Composites by length (ТЗ 8.2.2, 8.2.4; ADR 0018 §6): Т8.2-1, Т8.2-2 — the value of a composite.

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
