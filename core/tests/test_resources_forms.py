"""The form of a resource report (подшаг 20.4; ТЗ 8.10 «Формы»; ADR 0027 decisions 17–19): the rows of a result by a
template — the order, the subtotals as sums (ḡ = Σ t·g / Σ wt), the units and the decimals (`{value, text}`),
«Прочие», the sections, the balance columns. The input rows are built here as dictionaries (the format of ADR 0027
decision 15), the result of 20.3 is not needed."""

from __future__ import annotations

import math
from typing import Any

import pytest

from bf_core.resources import forms as fm
from bf_core.resources import templates as tp

AU = {"name": "Au", "unit": "g/t"}
CU = {"name": "Cu", "unit": "%"}
JORC = {"id": "jorc", **tp.defaults()["jorc"]}
GKZ = {"id": "gkz", **tp.defaults()["gkz"]}


def grp(
    g: str | tuple[str, ...] | None,
    tonnes: float,
    grade: float | None,
    *,
    kind: str = "cutoff",
    cutoff: float | None = 0.0,
    el: str = "Au",
    k: float = 1.0,
    volume: float | None = None,
    blocks: int = 1,
) -> dict[str, Any]:
    """A row of a result: the tonnes, the grade of one element (None — no block with a grade), the metal in the base
    unit (k — the factor of the unit: 1 for g/t, ppm; 1e-3 for ppb; 0.01 for %)."""
    keys = (g,) if not isinstance(g, tuple) else g
    row: dict[str, Any] = {"g1": None, "g2": None, "g3": None, "kind": kind, "cutoff": cutoff, "blocks": blocks}
    for i, v in enumerate(keys):
        row[f"g{i + 1}"] = v
    row["volume"] = tonnes / 2.5 if volume is None else volume
    row["tonnes"] = tonnes
    if grade is None:
        row.update({f"{el}:wt": 0.0, f"{el}:sum": 0.0, f"{el}:metal": None, f"{el}:grade": None})
    else:
        row.update({f"{el}:wt": tonnes, f"{el}:sum": tonnes * grade, f"{el}:metal": tonnes * grade * k,
                    f"{el}:grade": grade})  # fmt: skip
    row.update({f"{el}:missing_t": 0.0, f"{el}:missing_n": 0})
    return row


def cell(form: dict[str, Any], label: str, key: str, section: int = 0) -> dict[str, Any]:
    for r in form["sections"][section]["rows"]:
        if r["label"] == label:
            return r["cells"][key]  # type: ignore[no-any-return]
    raise AssertionError(f"no row {label}")


def labels(form: dict[str, Any], section: int = 0) -> list[str]:
    return [r["label"] for r in form["sections"][section]["rows"]]


JORC_ROWS = [
    grp("Измеренные", 1_000_000.0, 2.0),
    grp("Выявленные", 3_000_000.0, 1.0),
    grp("Предполагаемые", 500_000.0, 0.5),
]


# --- the numbers --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "decimals", "text"),
    [
        (None, 2, ""),
        (0.0, 2, "0,00"),
        (2.5, 0, "3"),  # half away from zero, not to even
        (3.5, 0, "4"),
        (-2.5, 0, "-3"),
        (2.675, 2, "2,68"),  # the decimal text of the number, not its binary approximation
        (1234567.891, 1, "1 234 567,9"),
        (12.5, 1, "12,5"),
        (-0.0004, 2, "0,00"),  # no «-0,00»
        (1.004711, 6, "1,004711"),
        (1e-7, 3, "0,000"),
        (1e22, 0, "\u00a0".join(["10", *["000"] * 7])),
    ],
)
def test_format_number(value: float | None, decimals: int, text: str) -> None:
    assert fm.format_number(value, decimals) == text


def test_excel_format_shows_the_same_decimals() -> None:
    assert fm.excel_format(0) == "#,##0" and fm.excel_format(2) == "#,##0.00" and fm.excel_format(6) == "#,##0.000000"


def test_t8101_the_metal_in_thousand_ounces() -> None:
    """Т8.10-1: 12 500 t at 2.5 g/t = 31.25 kg = 1.004711 koz (1e-6)."""
    rows = [grp("Измеренные", 12_500.0, 2.5, volume=5000.0)]
    form = fm.build(rows, JORC, [AU])
    metal = cell(form, "Измеренные", "metal:Au")
    assert metal["value"] == pytest.approx(1.004711, abs=1e-6)
    assert metal["text"] == "1,0"  # the JORC decimals: 1
    assert cell(form, "Измеренные", "tonnes") == {"value": 12.5, "text": "13"}  # kt, 0 decimals
    assert cell(form, "Измеренные", "grade:Au") == {"value": 2.5, "text": "2,50"}
    custom = {
        **JORC,
        "decimals": {**JORC["decimals"], "metal": 6, "tonnes": 1},
        "columns": ["tonnes", "volume", "metal"],
    }
    form = fm.build(rows, custom, [AU])
    assert cell(form, "Измеренные", "metal:Au")["text"] == "1,004711"
    assert cell(form, "Измеренные", "tonnes")["text"] == "12,5"
    assert cell(form, "Измеренные", "volume") == {"value": 5.0, "text": "5"}  # 5000 m³ = 5 thousand m³


@pytest.mark.parametrize(
    ("unit", "base", "k", "grade"),
    [
        ("g/t", "g", 1.0, 2.5),
        ("г/т", "g", 1.0, 2.5),
        ("ppm", "g", 1.0, 2.5),
        ("ppb", "g", 1e-3, 2500.0),
        ("%", "t", 0.01, 2.5),
    ],
)
def test_the_base_unit_of_the_metal_by_the_unit_of_the_grade(unit: str, base: str, k: float, grade: float) -> None:
    assert fm.grade_unit(unit)[1] == base
    tonnes = 1000.0
    row = grp("Измеренные", tonnes, grade, k=k)  # `e:metal` is in the base unit: g (g/t, ppm, ppb) or t (%)
    if base == "t":
        row["Au:metal"] = tonnes * grade * 0.01  # tonnes of metal for %
    kg = {
        **JORC,
        "units": {**JORC["units"], "metal": {"g": "kg", "t": "kg"}},
        "decimals": {**JORC["decimals"], "metal": 6},
    }
    form = fm.build([row], kg, [{"name": "Au", "unit": unit}])
    want = {"g/t": 2.5, "г/т": 2.5, "ppm": 2.5, "ppb": 2.5, "%": 25000.0}[unit] * tonnes / 1000.0  # kg of metal
    assert cell(form, "Измеренные", "metal:Au")["value"] == pytest.approx(want, rel=1e-12)


def test_a_percent_grade_gives_tonnes_of_metal_by_the_box() -> None:
    row = grp("Измеренные", 10_000.0, 1.5, el="Cu")
    row["Cu:metal"] = 10_000.0 * 1.5 * 0.01  # 150 t of copper, the base unit of % is t
    form = fm.build([row], JORC, [CU])
    assert cell(form, "Измеренные", "metal:Cu") == {"value": pytest.approx(150.0), "text": "150,0"}
    balance = fm.build([{**row, "g1": "A", "kind": "balance"}], GKZ, [CU])
    assert cell(balance, "A", "balance:metal:Cu") == {"value": pytest.approx(150.0), "text": "150"}  # t in ГКЗ too


def test_an_element_without_a_known_unit_has_no_metal_and_a_warning() -> None:
    form = fm.build(JORC_ROWS, JORC, [AU, {"name": "Ag", "unit": "oz/t"}, {"name": "Zn", "unit": None}])
    keys = [c["key"] for c in form["columns"]]
    assert keys == ["tonnes", "grade:Au", "metal:Au", "grade:Ag", "grade:Zn"]
    assert len(form["warnings"]) == 2 and "«Ag»" in form["warnings"][0] and "«Zn»" in form["warnings"][1]
    assert [c["title"] for c in form["columns"]][:3] == ["Руда, тыс. т", "Au, г/т", "Металл Au, тыс. унц"]


# --- the rows -----------------------------------------------------------------------------------------------------


def test_jorc_rows_in_the_order_of_the_template_and_the_subtotal_is_a_sum() -> None:
    form = fm.build(JORC_ROWS, JORC, [AU])
    assert labels(form) == ["Измеренные", "Выявленные", "Изм. + Выяв.", "Предполагаемые", "Итого"]
    assert [r["type"] for r in form["sections"][0]["rows"]] == ["group", "group", "subtotal", "group", "total"]
    sub = next(r for r in form["sections"][0]["rows"] if r["type"] == "subtotal")
    assert sub["members"] == ["Измеренные", "Выявленные"]
    # tonnes: 1 000 000 + 3 000 000 = 4 000 kt; ḡ = (2·1 + 1·3) / 4 = 1.25 g/t — not the mean of the means (1.5)
    assert cell(form, "Изм. + Выяв.", "tonnes")["value"] == 4000.0
    assert cell(form, "Изм. + Выяв.", "grade:Au")["value"] == pytest.approx(1.25, rel=1e-15)
    assert cell(form, "Изм. + Выяв.", "grade:Au")["text"] == "1,25"
    assert cell(form, "Изм. + Выяв.", "metal:Au")["value"] == pytest.approx((2e6 + 3e6) / 31103.4768, rel=1e-12)
    # the total is the sum of every row, ḡ = Σ t·g / Σ wt
    assert cell(form, "Итого", "tonnes")["value"] == 4500.0
    assert cell(form, "Итого", "grade:Au")["value"] == pytest.approx((2e6 + 3e6 + 250_000) / 4.5e6, rel=1e-15)
    assert form["template"] == {"id": "jorc", "name": "JORC", "version": 1}
    assert form["warnings"] == []


def test_the_subtotal_equals_the_sum_of_the_cells_of_its_members_to_1e12() -> None:
    rows = [
        grp("Измеренные", 123_456.789, 3.21),
        grp("Выявленные", 98_765.4321, 0.987),
        grp("Предполагаемые", 1.0, 100.0),
    ]
    form = fm.build(rows, JORC, [AU])
    for col in ("tonnes", "metal:Au"):
        parts = [cell(form, x, col)["value"] for x in ("Измеренные", "Выявленные")]
        assert cell(form, "Изм. + Выяв.", col)["value"] == pytest.approx(math.fsum(parts), rel=1e-12)
        every = [cell(form, x, col)["value"] for x in ("Измеренные", "Выявленные", "Предполагаемые")]
        assert cell(form, "Итого", col)["value"] == pytest.approx(math.fsum(every), rel=1e-12)


def test_a_row_of_the_template_without_blocks_has_zero_tonnes_and_an_empty_grade() -> None:
    form = fm.build([grp("Измеренные", 1000.0, 2.0)], JORC, [AU])
    assert cell(form, "Предполагаемые", "tonnes") == {"value": 0.0, "text": "0"}
    assert cell(form, "Предполагаемые", "grade:Au") == {"value": None, "text": ""}  # not a 0
    assert cell(form, "Предполагаемые", "metal:Au") == {"value": None, "text": ""}


def test_a_group_with_no_grade_gives_an_empty_grade_not_a_zero() -> None:
    form = fm.build([grp("Измеренные", 1000.0, None), grp("Выявленные", 500.0, 2.0)], JORC, [AU])
    assert cell(form, "Измеренные", "tonnes")["value"] == 1.0
    assert cell(form, "Измеренные", "grade:Au")["value"] is None
    assert cell(form, "Изм. + Выяв.", "grade:Au")["value"] == 2.0  # the weights of the blocks with a grade only
    assert cell(form, "Изм. + Выяв.", "tonnes")["value"] == 1.5


def test_nan_is_a_missing_value() -> None:
    row = grp("Измеренные", 1000.0, 2.0)
    row["Au:metal"] = float("nan")
    form = fm.build([row], JORC, [AU])
    assert cell(form, "Измеренные", "metal:Au") == {"value": None, "text": ""}
    assert cell(form, "Измеренные", "grade:Au")["value"] == 2.0


def test_groups_outside_the_template_are_the_row_others_and_a_warning() -> None:
    rows = [*JORC_ROWS, grp("Прогнозные", 200_000.0, 0.4), grp("(пусто)", 50_000.0, 0.1)]
    form = fm.build(rows, JORC, [AU])
    assert labels(form) == ["Измеренные", "Выявленные", "Изм. + Выяв.", "Предполагаемые", "Прочие", "Итого"]
    other = next(r for r in form["sections"][0]["rows"] if r["type"] == "other")
    assert other["cells"]["tonnes"]["value"] == 250.0
    assert cell(form, "Прочие", "grade:Au")["value"] == pytest.approx(
        (200_000 * 0.4 + 50_000 * 0.1) / 250_000, rel=1e-15
    )
    assert cell(form, "Итого", "tonnes")["value"] == 4750.0  # the total keeps them
    assert len(form["warnings"]) == 1 and "Прогнозные" in form["warnings"][0] and "(пусто)" in form["warnings"][0]
    assert "Прочие" in form["warnings"][0]
    assert "Прочие" not in labels(fm.build(JORC_ROWS, JORC, [AU]))  # no such row when everything is in the template


def test_labels_are_matched_without_regard_to_case_and_spaces() -> None:
    form = fm.build([grp("  измеренные ", 1000.0, 2.0), grp("Выявленные  ", 1000.0, 1.0)], JORC, [AU])
    assert cell(form, "Измеренные", "tonnes")["value"] == 1.0 and "Прочие" not in labels(form)


def test_a_template_without_the_total_and_the_subtotal() -> None:
    tpl = {**JORC, "total": None, "subtotals": [], "columns": ["tonnes"]}
    form = fm.build(JORC_ROWS, tpl, [AU])
    assert labels(form) == ["Измеренные", "Выявленные", "Предполагаемые"]
    assert [c["key"] for c in form["columns"]] == ["tonnes"]


def test_two_subtotals_after_the_same_member_and_the_columns_order() -> None:
    tpl = {
        **JORC,
        "subtotals": [
            {"label": "С1", "members": ["Измеренные", "Выявленные"]},
            {"label": "С2", "members": ["Выявленные", "Предполагаемые"]},
            {"label": "С3", "members": ["Измеренные", "Предполагаемые"]},
        ],
        "columns": ["metal", "tonnes", "grade"],
    }
    form = fm.build(JORC_ROWS, tpl, [AU, CU])
    assert labels(form) == ["Измеренные", "Выявленные", "С1", "Предполагаемые", "С2", "С3", "Итого"]
    # the metrics of the elements come together at the first of them in the template: metal, grade of each element
    assert [c["key"] for c in form["columns"]] == ["metal:Au", "grade:Au", "metal:Cu", "grade:Cu", "tonnes"]


def test_other_keys_are_summed_into_the_row_of_the_class() -> None:
    rows = [
        grp(("Измеренные", "Т1"), 1000.0, 2.0),
        grp(("Измеренные", "Т2"), 3000.0, 1.0),
        grp(("Выявленные", "Т1"), 500.0, 4.0),
    ]
    form = fm.build(rows, JORC, [AU], roles=["class", "domain"])
    assert len(form["sections"]) == 1 and form["sections"][0]["title"] is None
    assert cell(form, "Измеренные", "tonnes")["value"] == 4.0
    assert cell(form, "Измеренные", "grade:Au")["value"] == pytest.approx(5000.0 / 4000.0, rel=1e-15)
    assert cell(form, "Итого", "tonnes")["value"] == 4.5


def test_the_class_is_found_by_its_role_and_a_missing_role_is_a_warning() -> None:
    rows = [grp(("Т1", "Измеренные"), 1000.0, 2.0), grp(("Т2", "Выявленные"), 500.0, 1.0)]
    form = fm.build(rows, JORC, [AU], roles=["domain", "class"])
    assert cell(form, "Измеренные", "tonnes")["value"] == 1.0 and form["warnings"] == []
    bad = fm.build(rows, JORC, [AU], roles=["domain", "bench"])  # no «class»: the first key, everything is «Прочие»
    assert "class" in bad["warnings"][0] and "Прочие" in labels(bad)
    assert cell(bad, "Прочие", "tonnes")["value"] == 1.5


def test_sections_by_the_domain_and_the_total_of_all() -> None:
    rows = [
        grp(("Измеренные", "Т1"), 1000.0, 2.0),
        grp(("Выявленные", "Т1"), 1000.0, 1.0),
        grp(("Измеренные", "Т2"), 3000.0, 1.0),
        grp(("Предполагаемые", "(пусто)"), 500.0, 0.2),
    ]
    tpl = {**JORC, "section": "domain"}
    form = fm.build(rows, tpl, [AU], roles=["class", "domain"])
    assert [s["title"] for s in form["sections"]] == ["Т1", "Т2", "(пусто)", "Все разделы"]
    assert (
        cell(form, "Измеренные", "tonnes", 0)["value"] == 1.0 and cell(form, "Измеренные", "tonnes", 1)["value"] == 3.0
    )
    assert cell(form, "Итого", "tonnes", 0)["value"] == 2.0 and cell(form, "Изм. + Выяв.", "tonnes", 0)["value"] == 2.0
    assert cell(form, "Итого", "grade:Au", 0)["value"] == pytest.approx(1.5, rel=1e-15)
    assert cell(form, "Предполагаемые", "tonnes", 1)["value"] == 0.0  # a row of the template of a section with none
    assert labels(form, 3) == ["Итого"] and cell(form, "Итого", "tonnes", 3)["value"] == 5.5
    one = fm.build(rows[:2], tpl, [AU], roles=["class", "domain"])  # one section: no «all sections»
    assert [s["title"] for s in one["sections"]] == ["Т1"]
    none = fm.build(rows, tpl, [AU], roles=["class"])  # no role «domain» — no sections, a warning
    assert none["sections"][0]["title"] is None and "domain" in none["warnings"][0]


# --- cut-offs and the balance ---------------------------------------------------------------------------------------


def test_the_cutoff_of_the_table() -> None:
    rows = [
        grp("Измеренные", 4000.0, 2.0, cutoff=0.5),
        grp("Измеренные", 3000.0, 2.5, cutoff=1.0),
        grp("Измеренные", 1000.0, 3.0, cutoff=2.0),
        grp("Измеренные", 4100.0, 1.9, kind="all", cutoff=None),
    ]
    assert fm.build(rows, JORC, [AU])["cutoff"] == {"kind": "all", "value": None}  # «all» first
    form = fm.build(rows, JORC, [AU], cutoff=1.0)
    assert form["cutoff"] == {"kind": "cutoff", "value": 1.0} and form["cutoffs"] == [0.5, 1.0, 2.0]
    assert cell(form, "Измеренные", "tonnes")["value"] == 3.0
    low = fm.build(rows[:3], JORC, [AU])  # no «all»: the lowest cut-off
    assert low["cutoff"] == {"kind": "cutoff", "value": 0.5} and cell(low, "Измеренные", "tonnes")["value"] == 4.0
    miss = fm.build(rows, JORC, [AU], cutoff=7.0)
    assert cell(miss, "Измеренные", "tonnes")["value"] == 0.0 and "Борта" in miss["warnings"][0]


def test_the_total_rows_alone_make_the_total() -> None:
    """No grouping in the report: the rows of the total are all there is (the rows of the template are empty)."""
    form = fm.build([grp(None, 9000.0, 2.0)], JORC, [AU])
    assert cell(form, "Итого", "tonnes")["value"] == 9.0 and cell(form, "Итого", "grade:Au")["value"] == 2.0
    assert cell(form, "Измеренные", "tonnes")["value"] == 0.0
    both = fm.build([grp(None, 9999.0, 9.0), *JORC_ROWS], JORC, [AU])  # groups present: the total is their sum
    assert cell(both, "Итого", "tonnes")["value"] == 4500.0


def test_gkz_balance_and_offbalance_columns() -> None:
    rows = [
        grp("A", 1000.0, 3.0, kind="balance", cutoff=1.0),
        grp("B", 2000.0, 2.0, kind="balance", cutoff=1.0),
        grp("C1", 3000.0, 1.5, kind="balance", cutoff=1.0),
        grp("C2", 500.0, 1.2, kind="balance", cutoff=1.0),
        grp("A", 100.0, 0.8, kind="offbalance", cutoff=0.5),
        grp("C1", 400.0, 0.6, kind="offbalance", cutoff=0.5),
    ]
    form = fm.build(rows, GKZ, [AU])
    assert labels(form) == ["A", "B", "C1", "A + B + C1", "C2", "Итого"]
    cols = form["columns"]
    assert [c["key"] for c in cols] == [
        f"{p}:{k}" for p in ("balance", "offbalance") for k in ("tonnes", "grade:Au", "metal:Au")
    ]
    assert {c["group"] for c in cols if c["key"].startswith("balance")} == {"Балансовые"}
    assert {c["group"] for c in cols if c["key"].startswith("offbalance")} == {"Забалансовые"}
    assert cols[2]["title"] == "Металл Au, кг" and cols[2]["unit"] == "кг"
    assert (form["cutoff"]["balance"], form["cutoff"]["offbalance"]) == (1.0, 0.5)
    assert cell(form, "A + B + C1", "balance:tonnes")["value"] == 6.0
    assert cell(form, "A + B + C1", "balance:metal:Au")["value"] == pytest.approx(
        (3000 + 4000 + 4500) / 1000.0, rel=1e-12
    )  # kg
    assert cell(form, "A + B + C1", "balance:metal:Au")["text"] == "12"  # 11.5 kg, 0 decimals, half away from zero
    assert cell(form, "A + B + C1", "offbalance:tonnes")["value"] == 0.5
    assert (
        cell(form, "B", "offbalance:tonnes")["value"] == 0.0 and cell(form, "B", "offbalance:grade:Au")["value"] is None
    )
    assert cell(form, "Итого", "offbalance:grade:Au")["value"] == pytest.approx((80 + 240) / 500, rel=1e-15)


def test_rows_flat_follows_the_columns() -> None:
    form = fm.build(JORC_ROWS, JORC, [AU])
    flat = fm.rows_flat(form)
    assert [r["label"] for r in flat] == labels(form) and flat[0]["section"] is None
    keys = [c["key"] for c in form["columns"]]
    assert flat[2]["cells"] == [form["sections"][0]["rows"][2]["cells"][k] for k in keys]


def test_the_input_rows_are_not_changed() -> None:
    import copy

    rows = [*JORC_ROWS, grp("Прогнозные", 1.0, 1.0)]
    before = copy.deepcopy(rows)
    fm.build(rows, JORC, [AU], roles=["class"])
    assert rows == before


def test_the_form_is_deterministic_whatever_the_order_of_the_rows() -> None:
    rows = [grp(("Измеренные", f"Т{i}"), 1000.0 / (i + 1) ** 3, 0.1 * (i + 1) ** 2) for i in range(40)]
    a = fm.build(rows, JORC, [AU], roles=["class", "domain"])
    b = fm.build(rows[::-1], JORC, [AU], roles=["class", "domain"])
    assert a["sections"] == b["sections"]  # fsum: the order of the rows does not change a bit
