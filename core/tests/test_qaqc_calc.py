"""Filters and sections of QAQC (ТЗ 8.4.2, Т8.4-8 at the level of the kernel; ADR 0020 §5, §10): the filter
`v: 1` → a mask of the rows, the types by the dictionary of codes, the sections `summary`, `standards`, `blanks`,
`batches` as JSON, the groups by names, the units of the CRM and of the DL.

Tolerance: a calculation by a filter and one over `recs.take(mask)` give the same canonical JSON byte for byte
(Т8.4-8); the statuses and the counters — exact; the numbers of Т8.4-1 and Т8.4-2 — as the kernels give them."""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest

from bf_core.formats.table import Column
from bf_core.qaqc import config as qc
from bf_core.qaqc import normalize as nz
from bf_core.qaqc import records as r
from bf_core.qaqc.calc import SECTIONS, calc, canonical, columns_of, context, run, unit_ratio
from bf_core.qaqc.filters import MAX_WHERE, TYPES, UNMAPPED, Filter, map_types, parse_filter, resolve_field, row_mask
from bf_core.qaqc.limits import FAIL, NORM, NOT_ASSESSED, WARN
from bf_core.qaqc.standards import RULE_PAIR, RULE_RUN, RULE_SWAP
from tests.qaqcutil import BASE, CONFIG, DAY, FIVE, NAN, build, crm_row, hard_records, json_ready, refused

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gen.qaqc import generate  # noqa: E402

# --- Т8.4-1, Т8.4-2 through the sections ---------------------------------------------------------------------------


def t84_rows() -> list[dict[str, Any]]:
    series = [1.11, 1.12, 0.84, 1.16, 0.95] + [1.01] * 9
    rows = [{"type": "STD", "crm": "G-1", "lab": "L1", "batch": "B1", "seq": i + 1, "au": x, "day": 1}
            for i, x in enumerate(series)]  # fmt: skip
    rows += [{"type": "BLK", "lab": "L1", "batch": "B2", "seq": i + 1, "au": x, "prev": 1.0 + i, "day": 2}
             for i, x in enumerate([0.04, 0.05, 0.06])]  # fmt: skip
    return rows + [{"type": "ORIG", "lab": "L1", "batch": "B2", "seq": 9, "au": 2.5, "day": 2, "hole": "DH-1"}]


def test_t841_t842_through_the_sections() -> None:
    recs = build(t84_rows(), has_dl=False)
    cfg = {**CONFIG, "dl": [{"element": "AU", "lab": None, "value": 0.01, "unit": "ppm"}]}
    ref = [crm_row("G-1", 1.00, 0.05)]
    st = calc("standards", recs, cfg, ref, "AU", group={"crm": "G-1", "lab": "L1"})
    assert st["series"]["status"] == [WARN, FAIL, FAIL, FAIL, NORM] + [NORM] * 8 + [WARN]  # Т8.4-1
    assert st["series"]["rule"] == [0, RULE_PAIR, 0, 0, 0] + [0] * 8 + [RULE_RUN]
    assert st["series"]["row"] == list(range(14)) and st["series"]["batch"] == ["B1"] * 14
    assert st["series"]["date"] == [int(BASE + DAY)] * 14 and st["series"]["seq"] == [float(i) for i in range(1, 15)]
    assert st["series"]["swap"] == [None] * 14 and st["series"]["ma"][:9] == [None] * 9 and st["series"]["ma"][9] > 0
    (g,) = st["groups"]
    assert (g["crm"], g["lab"], g["state"], g["n"], g["mu"], g["sd"]) == ("G-1", "L1", "ok", 14, 1.0, 0.05)
    assert (g["norm"], g["warn"], g["fail"], g["pair_fails"], g["run_flags"], g["swaps"]) == (9, 2, 3, 1, 1, 0)
    assert (g["unit"], g["missing"]) == ("ppm", 0)
    assert g["bias_class"] == NORM and g["bias"] == pytest.approx(100 * (g["mean"] - 1.0), rel=1e-12)
    assert st["counts"] == {"rows": 18, "filtered": 18, "control": 17, "unmapped": 0, "missing": 0}
    assert (st["element"], st["unit"], st["notes"]) == ("AU", "ppm", []) and st["limits"] == qc.default_limits()
    bl = calc("blanks", recs, cfg, ref, "au", group={"lab": "L1"})  # the element — without case
    assert bl["series"]["status"] == [NORM, NORM, FAIL] and bl["series"]["x"] == [0.04, 0.05, 0.06]  # Т8.4-2
    assert bl["series"]["prev"] == [1.0, 2.0, 3.0] and bl["series"]["limit"] == pytest.approx([0.05] * 3)
    assert bl["carryover"] == {"n": 3, "pearson": pytest.approx(1.0)}
    assert bl["groups"] == [{"lab": "L1", "n": 3, "missing": 0, "dl": 0.01, "limit": pytest.approx(0.05),
                             "mixed": False, "norm": 2, "fail": 1, "not_assessed": 0}]  # fmt: skip
    bt = calc("batches", recs, cfg, ref, "AU")
    assert (bt["total"], bt["failed"], bt["not_assessed"], bt["no_batch"], bt["offset"]) == (2, 2, 0, 0, 0)
    assert bt["batches"] == [
        {"lab": "L1", "batch": "B1", "date_from": "2024-01-02", "date_to": "2024-01-02", "n": 14, "crm": 14,
         "blanks": 0, "crm_fail": 3, "blank_fail": 0, "assessed": 14, "status": FAIL},
        {"lab": "L1", "batch": "B2", "date_from": "2024-01-03", "date_to": "2024-01-03", "n": 4, "crm": 0,
         "blanks": 3, "crm_fail": 0, "blank_fail": 1, "assessed": 3, "status": FAIL},
    ]  # fmt: skip
    sm = calc("summary", recs, cfg, ref, "AU")
    assert sm["types"] == {"ORIG": 1, "CRM": 14, "BLANK": 3, "DUP_F": 0, "DUP_C": 0, "DUP_P": 0, "UMP": 0}
    assert sm["unmapped_codes"] == [] and sm["batches"] == {"total": 2, "failed": 2, "not_assessed": 0, "no_batch": 0}
    assert sm["standards"] == {
        "groups": 1, "n": 14, "missing": 0, "norm": 9, "warn": 2, "fail": 3, "not_assessed": 0, "pair_fails": 1,
        "run_flags": 1, "swaps": 0, "bias_attention": 0, "bias_bad": 0,
    }  # fmt: skip
    assert sm["blanks"] == {"groups": 1, "n": 3, "missing": 0, "norm": 2, "fail": 1, "not_assessed": 0}
    for answer in (st, bl, bt, sm):
        json_ready(answer)
    base = "type_code lab batch crm_id date seq parent_row parent_lab v:AU q:AU b:AU p:AU".split()
    assert columns_of(recs.schema, "au") == base and columns_of(recs.schema, "CU") == []
    assert columns_of(build([]).schema, "AU") == [*base, "dl:AU"]
    assert set(SECTIONS) >= {"summary", "standards", "blanks", "batches"}
    refused("unknown_section", "section", lambda: calc("nope", recs, cfg, ref, "AU"))
    refused("field_not_found", "element", lambda: calc("standards", recs, cfg, ref, "CU"))
    refused("field_not_found", "element", lambda: calc("standards", recs, cfg, ref, None))
    refused("bad_param", "group", lambda: calc("standards", recs, cfg, ref, "AU", group="G-1"))
    assert calc("standards", recs, cfg, ref, "AU", group={"crm": "G-2", "lab": "L1"})["series"] is None
    refused("bad_param", "run", lambda: calc("summary", recs, {**cfg, "limits": {"run": 99}}, ref, "AU"))


# --- the types by the dictionary of codes --------------------------------------------------------------------------


def test_std_is_crm_and_an_edit_of_the_mapping_changes_the_result() -> None:
    codes = ("STD", "std ", "Standard", "CRM", "QC", None)
    rows = [{"type": t, "crm": "G-1", "lab": "L1", "au": 1.16} for t in codes]
    recs = build(rows)
    ref = [crm_row("G-1", 1.0, 0.05)]
    st = calc("standards", recs, CONFIG, ref, "AU")
    assert st["groups"][0]["n"] == 4 and st["groups"][0]["fail"] == 4  # STD, «std », Standard, CRM
    assert st["counts"] == {"rows": 6, "filtered": 6, "control": 4, "unmapped": 1, "missing": 0}
    assert len(st["notes"]) == 1 and "«QC»" in st["notes"][0] and "Типы" in st["notes"][0]
    sm = calc("summary", recs, CONFIG, ref, "AU")
    assert sm["types"]["CRM"] == 4 and sm["types"]["ORIG"] == 1 and sm["unmapped_codes"] == [{"code": "QC", "n": 1}]
    # «QC» → CRM in the settings: no new import, another result
    edited = {**CONFIG, "types": {**CONFIG["types"], "QC": "CRM"}}
    again = calc("standards", recs, edited, ref, "AU")
    assert again["groups"][0]["n"] == 5 and again["counts"]["unmapped"] == 0 and again["notes"] == []
    assert canonical(again) != canonical(st)
    # «STD» taken out of the dictionary: those records are not recognized and take part in nothing
    fewer = {**CONFIG, "types": {k: v for k, v in CONFIG["types"].items() if k not in ("STD", "STANDARD")}}
    less = calc("standards", recs, fewer, ref, "AU")
    assert less["groups"][0]["n"] == 1 and less["counts"]["unmapped"] == 4
    # an empty code is ORIG unless the settings say otherwise; never silently
    strict = calc("summary", recs, {**CONFIG, "empty_is_orig": False}, ref, "AU")
    assert strict["types"]["ORIG"] == 0 and strict["unmapped_codes"] == [{"code": None, "n": 1}, {"code": "QC", "n": 1}]
    # the same rule as the settings (`config.type_of`)
    code = recs.text(r.TYPE_CODE)
    for cfg in (CONFIG, edited, fewer, {**CONFIG, "empty_is_orig": False}):
        want = [qc.type_of(dict(cfg), c) for c in code.values()]
        got = [None if t == UNMAPPED else TYPES[t] for t in map_types(code, cfg).tolist()]
        assert got == want
    assert TYPES == qc.TYPES


# --- groups by names, units -----------------------------------------------------------------------------------------


def test_groups_go_by_names_and_the_reference_is_matched_without_case() -> None:
    rows = [
        {"type": "CRM", "crm": "oreas-45", "lab": "L2", "au": 1.0},
        {"type": "CRM", "crm": "OREAS-45", "lab": "L2", "au": 1.1},
        {"type": "CRM", "crm": "G-9", "lab": None, "au": 5.0},
        {"type": "CRM", "crm": None, "lab": "L1", "au": 7.0},
        {"type": "CRM", "crm": "G-9", "lab": "L1", "au": 2.05},
    ]
    ref = [crm_row(" Oreas-45 ", 1.0, 0.05), crm_row("ZZ-1", 2.0, 0.1), crm_row("G-9", 9.0, 0.1, element="CU")]
    st = calc("standards", build(rows), CONFIG, ref, "AU", samples=True)
    # by the name of the CRM without case (an empty one first), then the laboratory (an empty one first)
    assert [(g["crm"], g["lab"], g["state"], g["n"]) for g in st["groups"]] == [
        (None, "L1", "no_reference", 1), ("G-9", None, "no_reference", 1), ("G-9", "L1", "no_reference", 1),
        ("Oreas-45", "L2", "ok", 2),
    ]  # fmt: skip
    assert st["groups"][3]["mu"] == 1.0 and st["groups"][0]["mu"] is None and st["groups"][0]["bias_class"] is None
    assert st["groups"][0]["mean"] == 7.0  # the values of a group without a reference are still there
    assert len(st["notes"]) == 1 and "«—», «G-9»" in st["notes"][0] and "CRM" in st["notes"][0]
    # the series of every group in a row; a CRM of the reference without samples can still be the swap
    assert st["samples"]["group"] == [0, 1, 2, 3, 3] and st["samples"]["row"] == [3, 2, 4, 0, 1]
    assert st["samples"]["status"] == [NOT_ASSESSED, NOT_ASSESSED, NOT_ASSESSED, NORM, NORM]
    rows[1]["au"] = 2.05
    st = calc("standards", build(rows), CONFIG, ref, "AU", group={"crm": "OREAS-45", "lab": "l2"})
    assert st["series"]["status"] == [NORM, FAIL] and st["series"]["swap"] == [None, ["ZZ-1"]]
    assert st["series"]["rule"] == [0, RULE_SWAP] and st["groups"][3]["swaps"] == 1
    json_ready(st)


def test_units_are_converted_and_another_unit_is_unit_mismatch() -> None:
    assert unit_ratio("ppm", "г/т") == (1, 1) == unit_ratio("G/T", " PPM ") == unit_ratio(None, "%")
    assert unit_ratio("ppb", "ppm") == (1, 1000) and unit_ratio("%", "ppm") == (10_000, 1)
    assert unit_ratio("ppm", "%") == (1, 10_000) and unit_ratio("ppb", "%") == (1, 10_000_000)
    assert unit_ratio("мг/т", "г/т") == (1, 1000) and unit_ratio("г/л", "г/л") == (1, 1) == unit_ratio("ppm", None)
    assert unit_ratio("г/л", "ppm") is None and unit_ratio("ppm", "oz/t") is None and unit_ratio("ppt", "ppm") is None
    assert unit_ratio("pct", "gpt") == (10_000, 1) and unit_ratio("Percent", "%") == (1, 1)
    rows = [{"type": "CRM", "crm": c, "lab": "L1", "au": x} for c, x in
            (("A", 1.16), ("B", 2.5), ("C", 3000.0), ("D", 1.0), ("E", 1.0))]  # fmt: skip
    rows += [{"type": "BLANK", "lab": lab, "au": 0.06} for lab in ("L1", "L2", "L3")]
    ref = [crm_row("A", 1000.0, 50.0, "ppb"), crm_row("B", 0.00025, 0.00001, "%"), crm_row("C", 3000.0, 10.0, "г/т"),
           crm_row("D", 1.0, 0.1, "oz/t"), crm_row("E", 1.0, 0.1, None)]  # fmt: skip
    cfg = {**CONFIG, "dl": [{"element": "au", "lab": "l1", "value": 10.0, "unit": "ppb"},
                            {"element": "AU", "lab": None, "value": 0.000002, "unit": "%"},
                            {"element": "AU", "lab": "L3", "value": 0.01, "unit": "карат"},
                            {"element": "CU", "lab": None, "value": 99.0, "unit": "ppm"}]}  # fmt: skip
    st = calc("standards", build(rows, has_dl=False), cfg, ref, "AU")
    by = {g["crm"]: g for g in st["groups"]}
    assert (by["A"]["mu"], by["A"]["sd"], by["A"]["fail"], by["A"]["unit"]) == (1.0, 0.05, 1, "ppm")  # 1000 ppb
    assert by["B"]["mu"] == pytest.approx(2.5, rel=1e-15) and by["B"]["norm"] == 1  # 0,00025 %
    assert (by["C"]["mu"], by["C"]["norm"]) == (3000.0, 1) and (by["E"]["state"], by["E"]["norm"]) == ("ok", 1)
    assert (by["D"]["state"], by["D"]["mu"], by["D"]["norm"], by["D"]["bias"]) == ("unit_mismatch", None, 0, None)
    assert len(st["notes"]) == 1 and "«D»" in st["notes"][0] and "единиц" in st["notes"][0].lower()
    bl = calc("blanks", build(rows, has_dl=False), cfg, ref, "AU")
    #   L1: its own row, 10 ppb = 0,01 ppm;  L2: «any», 0,000002 % = 0,02 ppm;  L3: carats — no DL, not assessed
    assert [(g["lab"], g["dl"], g["fail"], g["norm"], g["not_assessed"]) for g in bl["groups"]] == [
        ("L1", 0.01, 1, 0, 0), ("L2", pytest.approx(0.02, rel=1e-15), 0, 1, 0), ("L3", None, 0, 0, 1)]  # fmt: skip
    assert len(bl["notes"]) == 2 and all("«L3»" in note for note in bl["notes"])
    # a DL of the record itself is in the unit of the element and beats the table
    rows[5]["dl"] = 0.02
    assert calc("blanks", build(rows), cfg, ref, "AU")["groups"][0]["norm"] == 1


# --- the filter -----------------------------------------------------------------------------------------------------


def filter_rows() -> list[dict[str, Any]]:
    return [
        {"id": "A-001", "type": "CRM", "crm": "G-1", "lab": "L1", "batch": "B1", "day": 0, "seq": 1, "au": 1.0,
         "hole": None, "zone": "Ox", "depth": 10.0},
        {"id": "A-002", "type": "ORIG", "lab": "L1", "batch": "B1", "day": 0.5, "seq": 2, "au": 2.0, "hole": "DH-10",
         "zone": "ox", "depth": 20.0},
        {"id": "a-003", "type": "BLK", "lab": "l2", "batch": "B2", "day": 1, "seq": 1, "au": 0.01, "hole": "dh-11",
         "zone": "Sulf", "depth": None},
        {"id": "B-004", "type": "DUP_F", "lab": "L2", "batch": "B2", "day": 2, "seq": 2, "au": 2.1, "hole": "RC-1",
         "zone": None, "depth": 30.0},
        {"id": None, "type": "???", "lab": None, "batch": None, "day": None, "seq": None, "au": None, "hole": "DH-2",
         "zone": "Trans", "depth": 40.0},
        {"id": "B-006", "type": None, "crm": "g-1", "lab": "L3", "batch": "B1", "day": 3, "seq": 3, "au": 3.0,
         "hole": "DH-10A", "zone": "Sulfide", "depth": 20.0},
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("flt", "rows"),
    [
        ({}, None),
        ({"v": 1, "labs": [], "where": []}, None),
        ({"labs": ["L1"]}, [0, 1]),
        ({"labs": ["l2", "L3"]}, [2, 3, 5]),  # without case
        ({"batches": ["B1"]}, [0, 1, 5]),
        ({"batches": ["B2", "nope"]}, [2, 3]),
        ({"crm": ["g-1"]}, [0, 1, 2, 3, 4, 5]),  # the only CRM sample is G-1; the other types are not cut by it
        ({"crm": ["G-2"]}, [1, 2, 3, 4, 5]),  # the CRM sample goes; the ORIG with «g-1» in CRM_ID is not a CRM
        ({"crm": ["G-2", "G-1"], "types": ["CRM", "ORIG"]}, [0, 1, 5]),
        ({"types": ["CRM", "blank"]}, [0, 2]),
        ({"types": ["ORIG"]}, [1, 5]),  # an empty code is ORIG; «???» is of no type
        ({"hole_prefix": "dh-1"}, [1, 2, 5]),
        ({"date_from": "2024-01-02"}, [2, 3, 5]),
        ({"date_to": "2024-01-01"}, [0, 1]),  # a date without the time of day takes its whole day
        ({"date_to": "2024-01-01 00:00"}, [0]),
        ({"date_from": "2024-01-01T12:00:00", "date_to": "2024-01-03"}, [1, 2, 3]),
        ({"labs": ["L1", "L2"], "types": ["ORIG", "DUP_F"], "date_from": "2024-01-01"}, [1, 3]),
        ({"where": [{"field": "x:Zone", "op": "eq", "value": "OX"}]}, [0, 1]),
        ({"where": [{"field": "zone", "op": "ne", "value": "ox"}]}, [2, 3, 4, 5]),  # an empty one is not «ox»
        ({"where": [{"field": "Zone", "op": "in", "value": ["trans", "Sulf"]}]}, [2, 4]),
        ({"where": [{"field": "x:Zone", "op": "prefix", "value": "sul"}]}, [2, 5]),
        ({"where": [{"field": "x:Zone", "op": "contains", "value": "LF"}]}, [2, 5]),
        ({"where": [{"field": "x:Zone", "op": "empty"}]}, [3]),
        ({"where": [{"field": "lab", "op": "empty"}]}, [4]),
        ({"where": [{"field": "x:Depth", "op": "eq", "value": 20}]}, [1, 5]),
        ({"where": [{"field": "depth", "op": "ge", "value": "30"}]}, [3, 4]),
        ({"where": [{"field": "x:Depth", "op": "le", "value": 20.0}]}, [0, 1, 5]),
        ({"where": [{"field": "x:Depth", "op": "between", "value": [20, "30,0"]}]}, [1, 3, 5]),
        ({"where": [{"field": "x:Depth", "op": "empty"}]}, [2]),
        ({"where": [{"field": "AU", "op": "ge", "value": 2}]}, [1, 3, 5]),  # an element by its name: its value
        ({"where": [{"field": "v:AU", "op": "empty"}]}, [4]),
        ({"where": [{"field": "seq", "op": "between", "value": [2, 3]}]}, [1, 3, 5]),
        ({"where": [{"field": "date", "op": "eq", "value": "2024-01-01"}]}, [0, 1]),
        ({"where": [{"field": "date", "op": "ge", "value": "2024-01-03"}]}, [3, 5]),
        ({"where": [{"field": "date", "op": "between", "value": ["2024-01-01 12:00", "2024-01-02"]}]}, [1, 2]),
        ({"where": [{"field": "date", "op": "empty"}]}, [4]),
        ({"where": [{"field": "sample_id", "op": "prefix", "value": "a-"}]}, [0, 1, 2]),
        ({"where": [{"field": "sample_id", "op": "eq", "value": "b-004"}]}, [3]),
        ({"where": [{"field": "SAMPLE_ID", "op": "in", "value": ["A-001", "B-006", "x"]}]}, [0, 5]),
        ({"where": [{"field": "sample_id", "op": "contains", "value": "00"}]}, [0, 1, 2, 3, 5]),
        ({"where": [{"field": "sample_id", "op": "empty"}]}, [4]),
        ({"where": [{"field": "sample_id", "op": "ne", "value": "A-001"}]}, [1, 2, 3, 4, 5]),
        ({"where": [{"field": "type_code", "op": "eq", "value": "???"}]}, [4]),
        ({"where": [{"field": "hole", "op": "prefix", "value": "DH"}, {"field": "x:Depth", "op": "ge", "value": 20},
                    {"field": "x:Zone", "op": "ne", "value": "Trans"}]}, [1, 5]),  # joined by «И»
        ({"labs": ["nowhere"]}, []),
    ],
)  # fmt: skip
def test_filter_conditions(flt: dict[str, Any], rows: list[int] | None) -> None:
    recs = build(filter_rows())
    types = map_types(recs.text(r.TYPE_CODE), CONFIG)
    parsed = parse_filter(flt)
    mask = row_mask(recs, parsed, types)
    assert (None if mask is None else np.flatnonzero(mask).tolist()) == rows
    assert parsed.empty == (rows is None) and parse_filter(parsed.to_json()) == parsed  # the stored form comes back
    assert parse_filter(parsed) is parsed and json.loads(json.dumps(parsed.to_json()))["v"] == 1
    assert calc("summary", recs, CONFIG, [], "AU", flt)["counts"]["filtered"] == (6 if rows is None else len(rows))


def test_filter_refusals() -> None:
    recs = build(filter_rows())
    types = map_types(recs.text(r.TYPE_CODE), CONFIG)
    cond = {"field": "x:Zone", "op": "eq", "value": "Ox"}
    assert len(parse_filter({"where": [cond] * MAX_WHERE}).where) == 20
    refused("bad_param", "filter.where", lambda: parse_filter({"where": [cond] * 21}))  # the 21st condition
    unknown = parse_filter({"where": [cond, {**cond, "field": "x:Nope"}]})
    e = refused("bad_param", "filter.where[1].field", lambda: row_mask(recs, unknown, types))  # an unknown field
    assert "x:Nope" in e.message and "x:Zone" in e.hint
    refused("bad_param", "filter.where[0].field",
            lambda: calc("standards", recs, CONFIG, [], "AU", {"where": [{"field": "CU", "op": "empty"}]}))  # fmt: skip
    for bad_filter, param in (
        ([1, 2], "filter"), ({"lab": ["L1"]}, "filter.lab"), ({"v": 2}, "filter.v"), ({"labs": "L1"}, "filter.labs"),
        ({"labs": ["L1", ""]}, "filter.labs"), ({"batches": [None]}, "filter.batches"),
        ({"crm": [True]}, "filter.crm"), ({"types": ["STD"]}, "filter.types"),
        ({"labs": ["x"] * 5001}, "filter.labs"), ({"hole_prefix": 5}, "filter.hole_prefix"),
        ({"date_from": "01.02.2024"}, "filter.date_from"), ({"date_to": 20240101}, "filter.date_to"),
        ({"date_from": "2024-02-30"}, "filter.date_from"),
        ({"date_to": "2024-01-01T00:00:00+03:00"}, "filter.date_to"),
        ({"date_from": "2024-02-01", "date_to": "2024-01-01"}, "filter.date_from"),
        ({"where": {"field": "lab"}}, "filter.where"), ({"where": ["lab"]}, "filter.where[0]"),
        ({"where": [{"op": "eq", "value": 1}]}, "filter.where[0].field"),
        ({"where": [{"field": "lab", "op": "like", "value": 1}]}, "filter.where[0].op"),
        ({"where": [{"field": "lab", "op": "eq"}]}, "filter.where[0].value"),
        ({"where": [{"field": "lab", "op": "eq", "value": " "}]}, "filter.where[0].value"),
        ({"where": [{"field": "lab", "op": "in", "value": []}]}, "filter.where[0].value"),
        ({"where": [{"field": "seq", "op": "between", "value": [1]}]}, "filter.where[0].value"),
        ({"where": [{"field": "seq", "op": "between", "value": [1, 2, 3]}]}, "filter.where[0].value"),
    ):  # fmt: skip
        refused("bad_param", param, lambda f=bad_filter: parse_filter(f))
    for cond2, param in (
        ({"field": "lab", "op": "ge", "value": "L1"}, "filter.where[0].op"),  # a range of a text
        ({"field": "seq", "op": "contains", "value": "1"}, "filter.where[0].op"),
        ({"field": "date", "op": "prefix", "value": "2024"}, "filter.where[0].op"),
        ({"field": "seq", "op": "ge", "value": "много"}, "filter.where[0].value"),
        ({"field": "date", "op": "ge", "value": "вчера"}, "filter.where[0].value"),
        ({"field": "date", "op": "between", "value": ["2024-01-01", 5]}, "filter.where[0].value"),
    ):
        refused("bad_param", param, lambda c=cond2: row_mask(recs, parse_filter({"where": [c]}), types))
    assert resolve_field(recs.schema, "LAB") == "lab" and resolve_field(recs.schema, "zone") == "x:Zone"
    assert resolve_field(recs.schema, "au") == "v:AU" and resolve_field(recs.schema, "q:AU") == "q:AU"
    assert resolve_field(recs.schema, "nope") is None and Filter().empty and parse_filter(None).empty


# --- Т8.4-8: by a filter and over the records taken beforehand ----------------------------------------------------


@pytest.mark.parametrize("number", range(5))
def test_t848_by_a_filter_and_over_the_records_taken_beforehand(number: int) -> None:
    recs, cfg, ref = hard_records()
    flt = FIVE[number]
    mask = row_mask(recs, parse_filter(flt), map_types(recs.text(r.TYPE_CODE), cfg))
    assert mask is not None and 100 < int(mask.sum()) < recs.n
    part = recs.take(mask)
    by_filter, taken = context(recs, cfg, ref, "AU", flt), context(part, cfg, ref, "AU")
    seen = 0
    for section, params in (("summary", {}), ("standards", {"samples": True}), ("blanks", {"samples": True}),
                            ("batches", {}), ("batches", {"offset": 3, "limit": 7})):  # fmt: skip
        a, b = run(by_filter, section, **params), run(taken, section, **params)
        assert canonical(a) == canonical(b), section  # byte for byte
        assert a["counts"]["rows"] == recs.n and b["counts"]["rows"] == part.n == a["counts"]["filtered"]
        assert {**a, "counts": None} == {**b, "counts": None}
        json_ready(a)
        seen += len(canonical(a))
    groups = run(by_filter, "standards")["groups"]
    for g in groups[:: max(len(groups) // 4, 1)]:  # the series of several groups, whole and thinned
        for params in ({}, {"max_points": 20}):
            key = {"crm": g["crm"], "lab": g["lab"]}
            a, b = run(by_filter, "standards", group=key, **params), run(taken, "standards", group=key, **params)
            assert canonical(a) == canonical(b)
            assert len(a["series"]["row"]) == (a["thinned"]["shown"] if params else g["n"])
    for g in run(by_filter, "blanks")["groups"]:
        key = {"lab": g["lab"]}
        a, b = run(by_filter, "blanks", group=key, max_points=50), run(taken, "blanks", group=key, max_points=50)
        assert canonical(a) == canonical(b) and a["carryover"]["n"] <= g["n"]
    # the same through `calc`, and the sections are not empty: the filters leave control samples of every kind
    assert canonical(calc("summary", recs, cfg, ref, "AU", flt)) == canonical(calc("summary", part, cfg, ref, "AU"))
    line = run(by_filter, "summary")
    assert line["standards"]["n"] > 30 and line["batches"]["total"] > 10 and seen > 10_000
    assert line["blanks"]["n"] > 30  # also under the filter by CRM: it cuts the CRM samples only
    # another filter — another answer
    assert canonical(line) != canonical(calc("summary", recs, cfg, ref, "AU", FIVE[(number + 1) % 5]))


def test_filter_by_crm_cuts_only_the_crm_samples() -> None:
    """`crm[]` chooses among the standards; the blanks, the duplicates, the external control, the ORIG samples
    and the batches stay as they were (решение ревью 14.25, № 15). By the filter and over the records taken
    beforehand — still the same bytes: the type of a record is a field of its own (the code of the file)."""
    recs, cfg, ref = hard_records()
    flt = {"crm": ["G-1"]}
    whole, cut = context(recs, cfg, ref, "AU"), context(recs, cfg, ref, "AU", flt)
    all_groups, groups = run(whole, "standards")["groups"], run(cut, "standards")["groups"]
    assert {g["crm"] for g in all_groups} > {g["crm"] for g in groups} == {"G-1"}  # both spellings of G-1
    assert [g for g in all_groups if g["crm"] == "G-1"] == groups  # the groups left are as they were
    for section, params in (("blanks", {"samples": True}), ("duplicates", {"kind": "DUP_F", "samples": True}),
                            ("duplicates", {"kind": "DUP_P"}), ("external", {"intervals": False})):  # fmt: skip
        a, b = run(whole, section, **params), run(cut, section, **params)
        body = {k: v for k, v in a.items() if k not in ("counts", "notes")}
        assert body == {k: v for k, v in b.items() if k not in ("counts", "notes")}, section
        assert (a.get("groups") or a["points"]["row"]) and a["counts"]["filtered"] > b["counts"]["filtered"]
    one, two = run(whole, "summary"), run(cut, "summary")
    assert one["types"]["CRM"] > two["types"]["CRM"] > 0
    others = [k for k in one["types"] if k != "CRM"]
    assert [one["types"][k] for k in others] == [two["types"][k] for k in others]
    assert (one["blanks"], one["duplicates"], one["external"]) == (two["blanks"], two["duplicates"], two["external"])
    first, second = one["batches"], two["batches"]
    assert first["total"] >= second["total"] > 100 and first["no_batch"] > second["no_batch"]
    assert first["failed"] >= second["failed"] > 0  # a batch is not failed by a CRM that is cut
    assert one["unmapped_codes"] == two["unmapped_codes"]
    # Т8.4-8 with this rule: the mask is of the rows, the records taken by it give the same bytes
    mask = row_mask(recs, parse_filter(flt), map_types(recs.text(r.TYPE_CODE), cfg))
    assert mask is not None and int(mask.sum()) == two["counts"]["filtered"]
    taken = context(recs.take(mask), cfg, ref, "AU")
    for section, params in (("summary", {}), ("standards", {"samples": True}), ("blanks", {"samples": True}),
                            ("batches", {}), ("duplicates", {"kind": "DUP_F"}), ("external", {})):  # fmt: skip
        assert canonical(run(cut, section, **params)) == canonical(run(taken, section, **params)), section
    # a CRM that is not there: no standards at all, everything else in place
    none = calc("summary", recs, cfg, ref, "AU", {"crm": ["nowhere"]})
    assert none["types"]["CRM"] == 0 and none["standards"]["n"] == 0 and none["blanks"] == one["blanks"]


def test_hard_sections_hold_every_outcome() -> None:
    recs, cfg, ref = hard_records()
    ctx = context(recs, cfg, ref, "AU")
    st, bl, bt, sm = (run(ctx, s, samples=True) for s in ("standards", "blanks", "batches", "summary"))
    assert {g["state"] for g in st["groups"]} == {"ok", "no_reference", "unit_mismatch"}
    assert [g["crm"] for g in st["groups"]][:4] == [None] * 4  # the empty CRM first, by the laboratory (None first)
    assert [g["lab"] for g in st["groups"]][:4] == [None, "L1", "L2", "l2"]
    names = list(dict.fromkeys(g["crm"] for g in st["groups"]))
    assert names == [None, "G-1", "G-2", "oreas 7", "X-0", "Z-9"]  # «K-5» of the reference has no samples
    assert {NOT_ASSESSED, NORM, WARN, FAIL} == set(st["samples"]["status"]) == set(bl["samples"]["status"]) | {WARN}
    rules = set(st["samples"]["rule"])
    assert all(any(v & bit for v in rules) for bit in (RULE_PAIR, RULE_RUN, RULE_SWAP))
    swaps = [s for s in st["samples"]["swap"] if s]
    assert any("K-5" in s for s in swaps) and all(isinstance(name, str) for s in swaps for name in s)
    assert sum(g["n"] for g in st["groups"]) == len(st["samples"]["row"]) == sm["standards"]["n"]
    assert sm["types"]["DUP_P"] > 0 and sm["unmapped_codes"] == [{"code": "???", "n": st["counts"]["unmapped"]}]
    assert sm["types"]["ORIG"] == int(sum(t in ("ORIG", None) for t in recs.text(r.TYPE_CODE).values()))
    by_lab = {g["lab"]: g for g in bl["groups"]}
    assert bl["groups"][0]["lab"] is None and by_lab[None]["not_assessed"] > 0  # no row «any» in the table of DL
    assert by_lab["L2"]["mixed"] and by_lab["L2"]["dl"] is None  # the table and the DL of some records differ
    assert by_lab["l2"]["not_assessed"] == 0  # the laboratory of the table is matched without case
    assert {round(v, 12) for v in bl["samples"]["limit"] if v is not None} == {0.05, 0.1}  # 20 ppb = 0,02 ppm
    assert all("carryover" in g for g in bl["groups"]) and by_lab["L1"]["carryover"]["n"] > 100
    assert bt["total"] == len(bt["batches"]) == sm["batches"]["total"] and bt["no_batch"] > 0 < bt["failed"]
    assert {b["status"] for b in bt["batches"]} == {NORM, FAIL, NOT_ASSESSED}
    dates = [b["date_from"] for b in bt["batches"]]
    assert dates == sorted(dates, key=lambda d: (d is None, d)) and any("T12:00:00" in (d or "") for d in dates)
    assert len(sm["notes"]) == len(set(sm["notes"])) >= 4  # codes, no reference, other units, no DL
    # pages of the batches: together — the whole list; the page is checked
    pages = [run(ctx, "batches", offset=o, limit=40)["batches"] for o in range(0, bt["total"], 40)]
    assert [b for page in pages for b in page] == bt["batches"] and run(ctx, "batches", offset=10**6)["batches"] == []
    for params, param in (({"limit": 0}, "limit"), ({"limit": 5001}, "limit"), ({"offset": -1}, "offset"),
                          ({"limit": True}, "limit"), ({"offset": 1.5}, "offset")):  # fmt: skip
        refused("bad_param", param, lambda p=params: run(ctx, "batches", **p))
    refused("bad_param", "max_points", lambda: run(ctx, "standards", group={"crm": "G-1", "lab": "L1"}, max_points=0))


def test_thinning_keeps_every_warning_and_failure() -> None:
    rng = np.random.default_rng(8)
    n = 3000
    x = 1.0 + 0.05 * rng.standard_normal(n)
    rows = [{"type": "CRM", "crm": "G-1", "lab": "L1", "seq": i, "au": float(v)} for i, v in enumerate(x)]
    recs, ref = build(rows), [crm_row("G-1", 1.0, 0.05)]
    key = {"crm": "G-1", "lab": "L1"}
    full = calc("standards", recs, CONFIG, ref, "AU", group=key)
    marked = [i for i, s in enumerate(full["series"]["status"]) if s > NORM]
    assert 100 < len(marked) < 400 and "thinned" not in full
    thin = calc("standards", recs, CONFIG, ref, "AU", group=key, max_points=500)
    assert thin["thinned"] == {"shown": 500, "total": n} and len(thin["series"]["row"]) == 500
    assert thin["series"]["row"] == sorted(thin["series"]["row"]) and set(marked) <= set(thin["series"]["row"])
    rest = [i for i in thin["series"]["row"] if i not in set(marked)]
    assert max(np.diff(rest)) < 3 * n / len(rest)  # the rest is spread evenly
    at = {row: i for i, row in enumerate(full["series"]["row"])}
    for name in ("x", "z", "status", "rule", "ma"):  # the points shown are those of the whole series
        assert thin["series"][name] == [full["series"][name][at[row]] for row in thin["series"]["row"]]
    assert thin["groups"] == full["groups"]  # the numbers are counted over everything
    few = calc("standards", recs, CONFIG, ref, "AU", group=key, max_points=50)  # fewer than the marked ones
    assert few["series"]["row"] == marked and few["thinned"] == {"shown": len(marked), "total": n}
    assert calc("standards", recs, CONFIG, ref, "AU", group=key, max_points=n)["thinned"] == {"shown": n, "total": n}


def test_empty_data_and_a_filter_that_leaves_nothing() -> None:
    recs, cfg, ref = hard_records(400)
    for data, flt in ((build([]), None), (recs, {"labs": ["nowhere"]})):
        for section in SECTIONS:
            answer = calc(section, data, cfg, ref, "AU", flt, samples=True, group={"crm": "G-1", "lab": "L1"})
            json_ready(answer)
            assert answer["counts"]["filtered"] == 0 and answer["notes"] == []
        assert calc("standards", data, cfg, ref, "AU", flt)["groups"] == []
        assert calc("batches", data, cfg, ref, "AU", flt)["batches"] == []
        assert calc("summary", data, cfg, ref, "AU", flt)["standards"]["n"] == 0


def test_generated_file_through_the_normalization() -> None:
    """The records as the import makes them (`tools/gen/qaqc.py` → `normalize`): 20 CRM × 3 laboratories, the
    DL and the reference of the generator; by a filter and over the taken records — the same bytes."""
    gen = generate(4000, seed=11)
    names = [f.name for f in gen.table.schema]
    numeric = {c for *_, c in gen.elements} | {"SEQ"}
    mapping = nz.parse_mapping(nz.auto_mapping([(n, "number" if n in numeric else "text") for n in names]), names)
    seq = np.asarray(pc.cast(gen.table.column("SEQ"), pa.float64()).to_numpy(zero_copy_only=False))
    table = {n: Column(n, "number", seq) if n == "SEQ" else Column(n, "text", gen.table.column(n)) for n in names}
    recs = nz.normalize(table, mapping).records
    cfg = {**CONFIG, "dl": gen.dl}
    for element, unit in (("AU", "ppm"), ("CU", "%")):
        sm = calc("summary", recs, cfg, gen.crm, element)
        assert sm["unit"] == unit and sm["counts"] == {**sm["counts"], "rows": 6200, "control": 4000, "unmapped": 0}
        assert sm["types"] == {"ORIG": 2200, "CRM": 1200, "BLANK": 600, "DUP_F": 600, "DUP_C": 600, "DUP_P": 600,
                               "UMP": 400}  # fmt: skip
        assert sm["standards"]["groups"] == 60 and sm["standards"]["not_assessed"] == 0 and sm["notes"] == []
        assert 0 < sm["standards"]["fail"] < 40 and 0 < sm["blanks"]["fail"] < 20 and sm["blanks"]["not_assessed"] == 0
        assert sm["batches"]["total"] == 124 and 0 < sm["batches"]["failed"] < 40 and sm["batches"]["no_batch"] == 0
    flt = {"labs": ["LAB1", "LAB3"], "date_from": "2024-03-01", "types": ["CRM", "BLANK", "ORIG"]}
    part = recs.take(row_mask(recs, parse_filter(flt), map_types(recs.text(r.TYPE_CODE), cfg)))
    for section in SECTIONS:
        a = calc(section, recs, cfg, gen.crm, "AU", flt, samples=True)
        assert canonical(a) == canonical(calc(section, part, cfg, gen.crm, "AU", samples=True)), section
    assert 500 < a["counts"]["filtered"] < 3000


def test_mask_and_rows_of_hundred_thousand_records_in_time() -> None:
    """The criterion of 14.4 is the mask and the compression of 1 million records within 0.1 s (measured by
    `bench`); here — 100 thousand (pytest holds no more) within the same bound, the best of three."""
    rng = np.random.default_rng(21)
    n = 100_000
    labs, kinds = ("L1", "L2", "L3"), ("CRM", "BLANK", "ORIG", "DUP_F", "STD")
    schema = r.Schema((r.Element("AU", "ppm", False),), ())
    code = lambda names, k: r.Text(rng.integers(0, k, n).astype(np.int32), tuple(names))  # noqa: E731
    nothing = r.Text(np.full(n, -1, np.int32), ())
    recs = r.Records.of(schema, {
        r.SAMPLE_ID: [None] * n, r.PARENT_ID: [None] * n, r.HOLE: nothing, r.TYPE_CODE: code(sorted(kinds), 5),
        r.CRM_ID: code([f"G-{i:02d}" for i in range(20)], 20), r.LAB: code(labs, 3),
        r.BATCH: code([f"B{i:05d}" for i in range(2000)], 2000), r.PARENT_LAB: nothing,
        r.DATE: BASE + rng.integers(0, 365, n) * DAY, r.SEQ: rng.integers(1, 50, n).astype(np.float64),
        r.PARENT_ROW: np.full(n, -1, np.int32), r.PREV_ROW: np.full(n, -1, np.int32),
        r.v("AU"): rng.lognormal(0, 1, n), r.q("AU"): np.zeros(n, np.uint8), r.p("AU"): np.full(n, NAN),
        r.b("AU"): rng.lognormal(0, 1, n),
    })  # fmt: skip
    flt = {"labs": ["L1", "L2"], "date_from": "2024-02-01", "date_to": "2024-11-30",
           "types": ["CRM", "BLANK", "ORIG"]}  # fmt: skip
    ref = [crm_row(f"G-{i:02d}", 1.0, 0.5) for i in range(20)]
    best = math.inf
    for _ in range(3):
        t0 = time.perf_counter()
        ctx = context(recs, CONFIG, ref, "AU", flt)
        service = (recs.text(r.LAB).codes, recs.text(r.BATCH).codes, recs.text(r.CRM_ID).codes, recs.date(),
                   recs.number(r.SEQ), recs.number(r.b("AU")), recs.row)  # fmt: skip
        columns = [ctx.take(c) for c in service]
        best = min(best, time.perf_counter() - t0)
    assert 30_000 < ctx.counts["filtered"] < 50_000 and all(len(c) == ctx.counts["filtered"] for c in columns)
    assert best <= 0.1, f"{best:.3f} s"
    assert run(ctx, "summary")["batches"]["total"] > 3000
