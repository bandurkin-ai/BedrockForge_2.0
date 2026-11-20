"""Metal equivalent (ТЗ 8.2.2, 8.2.4, 8.0.1; ADR 0018 §7): Т8.2-10, k by the units of the grade and of the price,
missing grades, the units of the fields when the request names none.

Tolerance — 1e−12 relative against the formula; Т8.2-10 itself — 1e−6 г/т, as the ТЗ writes it."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites import equivalent as eq
from bf_core.composites.intervals import CompositeError

NAN = math.nan
REL = 1e-12
OZ = 31.1034768

AU_CU = {
    "base": "AU",
    "base_price": 2000,
    "base_recovery": 0.9,
    "base_unit": "г/т",
    "base_price_unit": "$/унц",
    "others": [{"field": "CU", "price": 9000, "recovery": 0.9, "unit": "%", "price_unit": "$/т"}],
}


def test_t8_2_10_au_eq() -> None:
    """Т8.2-10. Au 1 г/т, Cu 1 %, 2000 $/унц, 9000 $/т, R = 0,9: AuEq = 2,399656 г/т (1e−6)."""
    p = eq.EquivalentParams.of(AU_CU)
    out = eq.equivalent([np.array([1.0]), np.array([1.0])], p)
    assert out.values[0] == pytest.approx(2.399656, abs=1e-6)
    assert out.values[0] == pytest.approx(1.0 + (9000 * 0.9 * 0.01) / (2000 * 0.9 / OZ), rel=REL)
    assert p.name == "AUEQ" and p.fields == ["AU", "CU"]
    assert p.base.k == pytest.approx(1 / OZ, rel=REL) and p.others[0].k == 0.01
    rep = out.report
    assert rep["name"] == "AUEQ" and rep["unit"] == "г/т" and rep["n"] == 1 and rep["n_missing"] == 0
    assert rep["factors"][0]["factor"] == pytest.approx(1.39965646, rel=1e-8)
    assert rep["factors"][0]["text"] == "1 % CU = 1,39966 г/т AU-экв"


@pytest.mark.parametrize(
    ("grade", "price", "k"),
    [
        ("г/т", "$/унц", 1 / OZ),
        ("%", "$/т", 0.01),
        ("ppm", "$/унц", 1 / OZ),  # ppm = г/т
        ("ppb", "$/унц", 0.001 / OZ),  # ×0,001
        ("кг/т", "$/т", 0.001),
        ("кг/т", "$/кг", 1.0),
        ("г/т", "$/г", 1.0),
        ("г/т", "$/кг", 0.001),
        ("%", "$/кг", 10.0),
        ("%", "$/унц", 10_000 / OZ),
        ("ppb", "$/т", 1e-9),
    ],
)
def test_k_by_units(grade: str, price: str, k: float) -> None:
    assert eq.k_factor(grade, price) == pytest.approx(k, rel=REL)


def test_units_are_read_as_tables_write_them() -> None:
    for text, unit in (("g/t", "г/т"), (" Г/т ", "г/т"), ("гр/т", "г/т"), ("PPM", "ppm"), ("мг/кг", "ppm"),
                       ("ppb", "ppb"), ("%", "%"), ("pct", "%"), ("kg/t", "кг/т")):  # fmt: skip
        assert eq.grade_unit(text) == unit
    assert eq.grade_unit("oz/t") is None and eq.grade_unit("") is None and eq.grade_unit(None) is None
    assert eq.price_unit("$/oz") == "$/унц" and eq.price_unit("USD/t") == "$/т" and eq.price_unit("руб/т") is None
    by_grade = [eq.default_price_unit(u) for u in ("г/т", "ppm", "ppb", "%", "кг/т")]
    assert by_grade == ["$/унц", "$/унц", "$/унц", "$/т", "$/т"]


def test_units_of_the_fields_when_the_request_names_none() -> None:
    """The assistant sends no units: they come from the fields; the unit of the price — by the grade."""
    body = {"base": "AU", "base_price": "2 000", "others": "CU 9000 0,9; AG 25 0,8"}
    p = eq.EquivalentParams.of(body, units={"au": "g/t", "CU": "pct", "AG": "ppm", "PB": "%"})
    assert (p.base.unit, p.base.price_unit, p.base.recovery, p.base.price) == ("г/т", "$/унц", 0.9, 2000.0)
    assert [(m.field, m.unit, m.price_unit, m.price, m.recovery) for m in p.others] == [
        ("CU", "%", "$/т", 9000.0, 0.9),
        ("AG", "ppm", "$/унц", 25.0, 0.8),
    ]
    g = [np.array([1.0, 0.5]), np.array([1.0, 0.2]), np.array([10.0, 40.0])]
    want = g[0] + g[1] * (9000 * 0.9 * 0.01) / (2000 * 0.9 / OZ) + g[2] * (25 * 0.8 / OZ) / (2000 * 0.9 / OZ)
    assert eq.equivalent(g, p).values == pytest.approx(want, rel=REL)
    # the unit of the request wins over the unit of the field
    p2 = eq.EquivalentParams.of({**body, "base_unit": "ppb", "others": [{"field": "CU", "price": 9, "recovery": 1,
                                 "unit": "кг/т", "price_unit": "$/kg"}]}, units={"AU": "г/т", "CU": "%"})  # fmt: skip
    assert p2.base.unit == "ppb" and p2.others[0].unit == "кг/т" and p2.others[0].price_unit == "$/кг"
    assert p2.factors[0] == pytest.approx((9 * 1 * 1.0) / (2000 * 0.9 * 0.001 / OZ), rel=REL)


@pytest.mark.parametrize("units", [None, {}, {"AU": "г/т"}, {"AU": "г/т", "CU": ""}, {"AU": "г/т", "CU": "oz/t"}])
def test_unknown_unit_is_an_error_not_a_guess(units: dict | None) -> None:
    with pytest.raises(CompositeError) as err:
        eq.EquivalentParams.of({"base": "AU", "base_price": 2000, "others": "CU 9000 0,9"}, units=units)
    assert err.value.code == "unit_unknown" and "г/т, ppm, ppb, %, кг/т" in err.value.hint
    assert err.value.as_dict().keys() == {"code", "message", "hint"}


def test_missing_grades() -> None:
    """Компонент NaN → Eq = NaN; «считать нулём» — only by the explicit flag, and never for a sample with no
    grade at all."""
    au = np.array([1.0, NAN, 2.0, NAN, 0.0])
    cu = np.array([1.0, 1.0, NAN, NAN, 0.0])
    f = (9000 * 0.9 * 0.01) / (2000 * 0.9 / OZ)
    strict = eq.equivalent([au, cu], eq.EquivalentParams.of(AU_CU))
    assert strict.values == pytest.approx([1.0 + f, NAN, NAN, NAN, 0.0], rel=REL, nan_ok=True)
    assert strict.report["n"] == 2 and strict.report["n_missing"] == 3 and strict.report["n_zeroed"] == 0
    zero = eq.equivalent([au, cu], eq.EquivalentParams.of({**AU_CU, "nan_zero": True}))
    assert zero.values == pytest.approx([1.0 + f, f, 2.0, NAN, 0.0], rel=REL, nan_ok=True)
    assert zero.report["n"] == 4 and zero.report["n_missing"] == 1 and zero.report["n_zeroed"] == 2
    assert zero.report["nan_zero"] is True
    assert eq.EquivalentParams.of({**AU_CU, "nan_zero": "false"}).nan_zero is False


def test_three_metals_blocks_and_tick() -> None:
    rng = np.random.default_rng(4)
    n = 2_500_000  # more than one block of rows
    g = [rng.lognormal(0, 1, n), rng.lognormal(-1, 1, n), rng.lognormal(2, 1, n)]
    g[1][::7] = NAN
    body = {**AU_CU, "name": "AuEq", "others": [*AU_CU["others"], {"field": "AG", "price": 25, "recovery": 0.8,
            "unit": "г/т", "price_unit": "$/унц"}]}  # fmt: skip
    p = eq.EquivalentParams.of(body)
    seen: list[float] = []
    out = eq.equivalent(g, p, tick=seen.append)
    want = g[0] + g[1] * p.factors[0] + g[2] * p.factors[1]
    assert np.array_equal(out.values, want, equal_nan=True) and p.name == "AuEq"
    assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) == 3
    assert out.report["n_missing"] == int(np.isnan(g[1]).sum())
    zero = eq.equivalent(g, eq.EquivalentParams.of({**body, "nan_zero": True}))
    full = ~np.isnan(g[1])
    assert np.array_equal(zero.values[full], want[full])  # the same bits where nothing is missing

    class Stop(Exception):
        pass

    def stop(fraction: float) -> None:
        raise Stop

    with pytest.raises(Stop):
        eq.equivalent(g, p, tick=stop)
    with pytest.raises(ValueError):
        eq.equivalent(g[:2], p)


@pytest.mark.parametrize(
    ("patch", "param"),
    [
        ({"base": ""}, "base"),
        ({"base_price": None}, "base"),
        ({"base_price": 0}, "base"),
        ({"base_price": "дорого"}, "base"),
        ({"base_recovery": 1.2}, "base"),
        ({"base_recovery": 0}, "base"),
        ({"base_unit": "oz/t"}, "base"),
        ({"base_price_unit": "руб/т"}, "base"),
        ({"others": []}, "others"),
        ({"others": None}, "others"),
        ({"others": "CU 9000"}, "others"),
        ({"others": "CU -1 0,9"}, "others"),
        ({"others": "CU 9000 90"}, "others"),
        ({"others": 5}, "others"),
        ({"others": [{"field": "AU", "price": 1, "recovery": 1, "unit": "%"}]}, "others"),
        ({"others": [{"field": "CU", "price": 1, "recovery": 1, "unit": "%"}] * 2}, "others"),
        ({"others": [{"field": "CU", "price": 1, "recovery": 1, "unit": "%", "price_unit": "€/т"}]}, "others"),
        ({"name": "cu"}, "name"),
    ],
)
def test_bad_parameters_are_errors_with_a_hint(patch: dict, param: str) -> None:
    with pytest.raises(CompositeError) as err:
        eq.EquivalentParams.of({**AU_CU, **patch})
    assert err.value.code == "bad_param" and err.value.param == param
    assert err.value.message and err.value.hint
