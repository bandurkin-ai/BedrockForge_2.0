"""Audit rules on small hand-made datasets (ТЗ 7.12; ported from BF `tests/test_audit_checks.py`), no database.

Baseline: six vertical holes on a 50 m grid with clean surveys and samples. Each test breaks one thing and
expects exactly the matching finding. All 27 rules against a slow reference: `test_audit_reference.py`.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from bf_core.audit.checks import MAX_EXAMPLES, AuditConfig, AuditData, run_checks
from bf_core.audit.rules import EXAMPLE_FIELDS, RULES, SEVERITIES

HOLES = ["H1", "H2", "H3", "H4", "H5", "H6"]
CFG = AuditConfig()
NAN = float("nan")
Rows = list[dict[str, Any]]


def _collar() -> Rows:
    xs, ys = [0, 50, 100, 0, 50, 100], [0, 0, 0, 50, 50, 50]
    zs = [100.0, 101.0, 102.0, 100.5, 101.5, 102.5]
    return [
        {"hole": h, "x": 500000.0 + xs[i], "y": 4500000.0 + ys[i], "z": zs[i], "td": 100.0, "azimuth": 0.0,
         "dip": -90.0, "status": "measured"}
        for i, h in enumerate(HOLES)
    ]


def _survey() -> Rows:
    rows = [{"hole": h, "depth": d, "azimuth": 0.0, "dip": -90.0} for h in HOLES for d in (0.0, 50.0, 100.0)]
    return [{"id": i, **r} for i, r in enumerate(rows, 1)]


def _assay() -> Rows:
    rows = []
    for h_i, h in enumerate(HOLES):
        for i in range(5):
            au, cu = 0.01 * (1 + (h_i + i) % 5), 0.1 + 0.01 * ((h_i * 2 + i) % 4)
            rows.append({"hole": h, "sample": f"{h}-{i}", "from": float(i), "to": float(i + 1), "AU": au, "CU": cu})
    return [{"id": i, **r} for i, r in enumerate(rows, 1)]


def _at(rows: Rows, hole: str, **match: Any) -> dict[str, Any]:
    (row,) = [r for r in rows if r["hole"] == hole and all(r[k] == v for k, v in match.items())]
    return row


def _data(collar: Rows | None = None, survey: Rows | None = None, assay: Rows | None = None, sample: bool = True):
    collar = _collar() if collar is None else collar
    survey = _survey() if survey is None else survey
    assay = _assay() if assay is None else assay
    by = {c["hole"]: c for c in collar}
    names = list(by)
    for r in [*survey, *assay]:  # holes known only from the rows: no collar
        if r["hole"] is not None and r["hole"] not in names:
            names.append(r["hole"])
    index = {n: i for i, n in enumerate(names)}

    def per_hole(key: str) -> np.ndarray:
        return np.array([by[n][key] if n in by else NAN for n in names], dtype=float)

    def col(rows: Rows, key: str) -> np.ndarray:
        return np.array([r[key] for r in rows], dtype=float)

    def ids(rows: Rows, key: str) -> np.ndarray:
        return np.array([index.get(r[key], -1) if key == "hole" else r[key] for r in rows], dtype=np.int64)

    return AuditData(
        holes=np.array(names, dtype=object),
        has_collar=np.array([n in by for n in names], dtype=bool),
        x=per_hole("x"), y=per_hole("y"), z=per_hole("z"), td=per_hole("td"),
        azimuth=per_hole("azimuth"), dip=per_hole("dip"),
        status=np.array([by[n]["status"] if n in by else None for n in names], dtype=object),
        survey_row_id=ids(survey, "id"), survey_hole=ids(survey, "hole"),
        survey_depth=col(survey, "depth"), survey_azimuth=col(survey, "azimuth"), survey_dip=col(survey, "dip"),
        assay_row_id=ids(assay, "id"), assay_hole=ids(assay, "hole"),
        assay_from=col(assay, "from"), assay_to=col(assay, "to"),
        assay_sample=np.array([r["sample"] for r in assay], dtype=object) if sample else None,
        components={k: col(assay, k) for k in ("AU", "CU")},
    )


def _found(data: AuditData, cfg: AuditConfig = CFG) -> set[tuple[str, str | None]]:
    return {(f["code"], f["hole"]) for f in run_checks(data, cfg).findings}


def _rows(data: AuditData, code: str, cfg: AuditConfig = CFG) -> int:
    return next(r["rows"] for r in run_checks(data, cfg).summary if r["code"] == code)


def test_clean_baseline_passes_every_rule() -> None:
    result = run_checks(_data(), CFG)

    assert result.findings == []
    assert [r["code"] for r in result.summary] == list(RULES) and len(result.summary) == 27
    assert all(r["rows"] == 0 and r["holes"] == 0 for r in result.summary)
    rule = RULES["ASSAY_GAP"]
    assert result.summary[22] == {
        "code": "ASSAY_GAP", "severity": "warning", "table": "assay", "title": rule.title,
        "description": rule.description, "rows": 0, "holes": 0,
    }


# ---- collar -----------------------------------------------------------------


def test_collar_missing_coords_and_invalid_td_and_angles() -> None:
    c = _collar()
    c[0]["x"] = NAN
    c[1]["td"] = 0.0
    c[2]["dip"] = -120.0
    c[3]["azimuth"] = 400.0

    assert _found(_data(collar=c)) >= {
        ("COLLAR_MISSING_COORDS", "H1"), ("COLLAR_INVALID_TD", "H2"),
        ("COLLAR_ANGLE_RANGE", "H3"), ("COLLAR_ANGLE_RANGE", "H4"),
    }


def test_collar_isolated_only_flags_the_far_one() -> None:
    c = [*_collar(), {**_collar()[0], "hole": "H7", "x": 505000.0, "y": 4500000.0, "z": 101.0}]
    s = [*_survey(), {"id": 100, "hole": "H7", "depth": 0.0, "azimuth": 0.0, "dip": -90.0}]
    a = [*_assay(), {"id": 100, "hole": "H7", "sample": "H7-0", "from": 0.0, "to": 1.0, "AU": 0.02, "CU": 0.1}]

    result = run_checks(_data(collar=c, survey=s, assay=a), CFG)

    assert [(f["code"], f["hole"]) for f in result.findings] == [("COLLAR_ISOLATED", "H7")]
    assert result.findings[0]["examples"] == [{"x": 505000.0, "y": 4500000.0, "nn_distance": 4900.0}]


def test_collar_z_jump() -> None:
    c = _collar()
    c[1]["z"] = 300.0

    result = run_checks(_data(collar=c), CFG)

    assert [(f["code"], f["hole"]) for f in result.findings] == [("COLLAR_Z_JUMP", "H2")]
    assert result.findings[0]["examples"] == [{"z": 300.0, "neighbour_z": 101.5}]


def test_collars_at_the_same_point_are_neighbours_of_each_other() -> None:
    c = _collar()
    for row in c:  # more twins than neighbours looked at: the hole itself is not among its nearest ones
        row["x"], row["y"] = 500000.0, 4500000.0
    c[2]["z"] = 400.0

    assert _found(_data(collar=c)) == {("COLLAR_Z_JUMP", "H3")}


def test_collar_status_planned_and_empty() -> None:
    c = _collar()
    c[0]["status"] = "Project"
    c[1]["status"] = ""
    c[2]["status"] = None
    c[3]["status"] = "  ПЛАН "

    assert _found(_data(collar=c)) == {
        ("COLLAR_PLANNED_WITH_DATA", "H1"), ("COLLAR_EMPTY_STATUS", "H2"), ("COLLAR_EMPTY_STATUS", "H3"),
        ("COLLAR_PLANNED_WITH_DATA", "H4"),
    }
    assert _found(_data(collar=c), AuditConfig(planned_markers=("Measured",))) == {
        ("COLLAR_PLANNED_WITH_DATA", h) for h in ("H5", "H6")
    } | {("COLLAR_EMPTY_STATUS", "H2"), ("COLLAR_EMPTY_STATUS", "H3")}


def test_collar_without_survey_and_assay() -> None:
    s = [r for r in _survey() if r["hole"] != "H1"]
    a = [r for r in _assay() if r["hole"] != "H2"]

    assert _found(_data(survey=s, assay=a)) == {("COLLAR_WITHOUT_SURVEY", "H1"), ("COLLAR_WITHOUT_ASSAY", "H2")}


# ---- survey -----------------------------------------------------------------


def test_survey_orphan_empty_and_ranges() -> None:
    s = [*_survey(), {"id": 100, "hole": "GHOST", "depth": 0.0, "azimuth": 0.0, "dip": -90.0}]
    _at(s, "H1", depth=50.0)["azimuth"] = NAN
    _at(s, "H2", depth=50.0)["dip"] = 95.0

    result = run_checks(_data(survey=s), CFG)

    assert {(f["code"], f["hole"]) for f in result.findings} == {
        ("SURVEY_ORPHAN_HOLE", "GHOST"), ("SURVEY_EMPTY_VALUES", "H1"), ("SURVEY_ANGLE_RANGE", "H2"),
    }
    empty = next(f for f in result.findings if f["code"] == "SURVEY_EMPTY_VALUES")
    assert empty["examples"] == [{"row_id": 2, "depth": 50.0, "azimuth": None, "dip": -90.0}]


def test_survey_beyond_td_duplicate_and_first_not_zero() -> None:
    s = _survey()
    _at(s, "H1", depth=100.0)["depth"] = 150.0
    _at(s, "H2", depth=100.0)["depth"] = 50.0
    s = [r for r in s if not (r["hole"] == "H3" and r["depth"] == 0.0)]

    result = run_checks(_data(survey=s), CFG)

    assert {(f["code"], f["hole"]): f["count"] for f in result.findings} == {
        ("SURVEY_BEYOND_TD", "H1"): 1, ("SURVEY_DUPLICATE_DEPTH", "H2"): 2, ("SURVEY_FIRST_NOT_ZERO", "H3"): 1,
    }
    assert result.findings[0]["examples"] == [{"row_id": 3, "depth": 150.0, "td": 100.0}]


def test_survey_not_ascending_in_file_order() -> None:
    s = _survey()
    h1 = [r for r in s if r["hole"] == "H1"]
    for r, i in zip(h1, [r["id"] for r in h1][::-1], strict=True):  # depths now descend in file order
        r["id"] = i

    result = run_checks(_data(survey=s), CFG)

    assert [(f["code"], f["hole"], f["count"]) for f in result.findings] == [("SURVEY_NOT_ASCENDING", "H1", 2)]
    assert [e["row_id"] for e in result.findings[0]["examples"]] == [2, 3]  # in file order


def test_survey_dogleg_flags_sharp_turn_but_not_near_vertical_azimuth_noise() -> None:
    c = _collar()
    c[0]["azimuth"], c[0]["dip"] = 0.0, -60.0  # keep the collar consistent with the first station
    s = _survey()
    for r in s:
        if r["hole"] == "H1":
            r["azimuth"], r["dip"] = 0.0, -60.0
    _at(s, "H1", depth=50.0)["azimuth"] = 180.0
    _at(s, "H2", depth=50.0).update(azimuth=180.0, dip=-89.9)  # almost vertical

    result = run_checks(_data(collar=c, survey=s), CFG)

    assert [(f["code"], f["hole"], f["count"]) for f in result.findings] == [("SURVEY_DOGLEG", "H1", 2)]
    first = result.findings[0]["examples"][0]
    assert first["row_id"] == 2 and first["depth"] == 50.0 and first["dls"] == pytest.approx(36.0, abs=1e-9)


def test_survey_collar_mismatch() -> None:
    s = _survey()
    _at(s, "H1", depth=0.0)["dip"] = -60.0

    assert _found(_data(survey=s)) == {("SURVEY_COLLAR_MISMATCH", "H1"), ("SURVEY_DOGLEG", "H1")}


@pytest.mark.parametrize("threshold", [5.0, 40.0])
def test_dogleg_threshold_is_configurable(threshold: float) -> None:
    s = _survey()
    for r in s:
        if r["hole"] == "H1":
            r["azimuth"], r["dip"] = 0.0, -60.0
    _at(s, "H1", depth=50.0)["dip"] = -50.0  # 10 degrees over 50 m = 6 deg/30 m
    c = _collar()
    c[0]["azimuth"], c[0]["dip"] = 0.0, -60.0

    codes = {code for code, _ in _found(_data(collar=c, survey=s), AuditConfig(dogleg_per_30m=threshold))}

    assert ("SURVEY_DOGLEG" in codes) == (threshold == 5.0)


def test_lower_dogleg_threshold_finds_more_bends() -> None:
    """ТЗ 7.12 acceptance: «Порог изгиба 5° увеличивает число SURVEY_DOGLEG, если такие изгибы есть»."""
    s = _survey()
    _at(s, "H1", depth=50.0)["dip"] = -78.0  # 12° over 50 m = 7,2 °/30 m twice
    _at(s, "H2", depth=50.0)["dip"] = -60.0  # 30° over 50 m = 18 °/30 m twice

    default, lowered = (_rows(_data(survey=s), "SURVEY_DOGLEG", AuditConfig(dogleg_per_30m=t)) for t in (10.0, 5.0))

    assert (default, lowered) == (2, 4)


# ---- assay ------------------------------------------------------------------


def test_assay_orphan_invalid_and_beyond_td() -> None:
    a = [*_assay(), {"id": 100, "hole": "GHOST", "sample": "G-0", "from": 0.0, "to": 1.0, "AU": 0.02, "CU": 0.1}]
    _at(a, "H1", **{"from": 0.0}).update({"from": 2.0, "to": 1.0})
    _at(a, "H2", **{"from": 4.0})["to"] = 120.0

    found = _found(_data(assay=a))

    assert ("ASSAY_ORPHAN_HOLE", "GHOST") in found
    assert ("ASSAY_INVALID_INTERVAL", "H1") in found
    assert ("ASSAY_BEYOND_TD", "H2") in found


def test_assay_overlap_and_gap() -> None:
    a = _assay()
    _at(a, "H1", **{"from": 2.0})["from"] = 1.5  # overlaps the previous 1-2
    _at(a, "H2", **{"from": 2.0}).update({"from": 3.0, "to": 3.5})  # gap 2-3, then 3-3.5
    a.remove(_at(a, "H2", **{"from": 3.0, "to": 4.0}))  # … and gap 3.5-4

    result = run_checks(_data(assay=a), CFG)
    found = {(f["code"], f["hole"]): f for f in result.findings}

    assert set(found) == {("ASSAY_OVERLAP", "H1"), ("ASSAY_GAP", "H2")}
    assert found["ASSAY_OVERLAP", "H1"]["examples"] == [
        {"row_id": 3, "sample": "H1-2", "from": 1.5, "to": 3.0, "prev_to": 2.0}
    ]
    assert found["ASSAY_GAP", "H2"]["count"] == 2


def _two_intervals(second_from: float) -> AuditData:
    a = [r for r in _assay() if r["hole"] != "H1"]
    a += [
        {"id": 201, "hole": "H1", "sample": "A", "from": 0.0, "to": 2.0, "AU": 0.02, "CU": 0.1},
        {"id": 202, "hole": "H1", "sample": "B", "from": second_from, "to": 3.0, "AU": 0.02, "CU": 0.1},
    ]
    return _data(assay=a)


def test_spec_acceptance_overlap_and_tolerated_gap() -> None:
    """ТЗ 7.12: «0–2 и 1,5–3 одной скважины дают ASSAY_OVERLAP; 0–2 и 2,005–3 разрыва не дают»."""
    assert _found(_two_intervals(1.5)) == {("ASSAY_OVERLAP", "H1")}
    assert _found(_two_intervals(2.005)) == set()
    assert _found(_two_intervals(1.995)) == set()
    assert _found(_two_intervals(2.02)) == {("ASSAY_GAP", "H1")}
    assert _found(_two_intervals(2.005), AuditConfig(depth_tolerance=0.001)) == {("ASSAY_GAP", "H1")}


def test_overlap_is_measured_from_the_deepest_end_reached() -> None:
    a = [r for r in _assay() if r["hole"] != "H1"]
    spans = [(0.0, 10.0), (1.0, 2.0), (2.0, 3.0), (10.0, 11.0), (12.0, 13.0)]  # a long interval covers two
    a += [{"id": 300 + i, "hole": "H1", "sample": f"N{i}", "from": f, "to": t, "AU": 0.02, "CU": 0.1}
          for i, (f, t) in enumerate(spans)]

    found = {(f["code"], f["hole"]): f for f in run_checks(_data(assay=a[::-1]), CFG).findings}  # any row order

    assert {k: f["count"] for k, f in found.items()} == {("ASSAY_OVERLAP", "H1"): 2, ("ASSAY_GAP", "H1"): 1}
    assert [e["prev_to"] for e in found["ASSAY_OVERLAP", "H1"]["examples"]] == [10.0, 10.0]
    assert found["ASSAY_GAP", "H1"]["examples"][0]["prev_to"] == 11.0


def test_assay_duplicate_sample_negative_and_empty_values() -> None:
    a = _assay()
    _at(a, "H2", sample="H2-1")["sample"] = " H1-0 "  # spaces around the number do not make it another one
    _at(a, "H3", sample="H3-0")["AU"] = -1.0
    _at(a, "H4", sample="H4-0")["CU"] = NAN
    _at(a, "H5", sample="H5-0")["sample"] = None
    _at(a, "H5", sample="H5-1")["sample"] = ""
    _at(a, "H6", sample="H6-0")["sample"] = "  "

    result = run_checks(_data(assay=a), CFG)
    found = {(f["code"], f["hole"]): f for f in result.findings}

    assert set(found) == {
        ("ASSAY_DUPLICATE_SAMPLE", "H1"), ("ASSAY_DUPLICATE_SAMPLE", "H2"),
        ("ASSAY_NEGATIVE_VALUE", "H3"), ("ASSAY_EMPTY_VALUE", "H4"),
    }
    assert found["ASSAY_NEGATIVE_VALUE", "H3"]["examples"] == [
        {"row_id": 11, "component": "AU", "value": -1.0, "sample": "H3-0", "from": 0.0}
    ]
    assert found["ASSAY_EMPTY_VALUE", "H4"]["examples"] == [
        {"row_id": 16, "component": "CU", "sample": "H4-0", "from": 0.0}
    ]


def test_table_without_sample_column() -> None:
    a = _assay()
    _at(a, "H3", sample="H3-0")["AU"] = -1.0

    result = run_checks(_data(assay=a, sample=False), CFG)

    assert [(f["code"], f["hole"]) for f in result.findings] == [("ASSAY_NEGATIVE_VALUE", "H3")]
    assert result.findings[0]["examples"][0]["sample"] is None


def test_assay_outlier_is_reported_per_hole_with_examples() -> None:
    a = _assay()
    _at(a, "H5", sample="H5-2")["AU"] = 5000.0

    findings = [f for f in run_checks(_data(assay=a), CFG).findings if f["code"] == "ASSAY_OUTLIER"]

    assert [(f["hole"], f["count"]) for f in findings] == [("H5", 1)]
    assert findings[0] == {
        "code": "ASSAY_OUTLIER", "severity": "info", "table": "assay", "hole": "H5", "count": 1,
        "examples": [{"row_id": 23, "component": "AU", "value": 5000.0, "sample": "H5-2", "from": 2.0}],
    }
    assert _found(_data(assay=a), AuditConfig(assay_outlier_z=100.0)) == set()


# ---- result, missing values, hard data -----------------------------------------


def test_findings_are_sorted_errors_first_and_examples_capped() -> None:
    c = _collar()
    c[0]["x"] = NAN  # error
    c[1]["status"] = ""  # info
    a = _assay() + [
        {"id": 1000 + i, "hole": "H1", "sample": f"X{i}", "from": 10.0, "to": 5.0, "AU": 0.02, "CU": 0.1}
        for i in range(50)
    ]

    result = run_checks(_data(collar=c, assay=a), CFG)

    severities = [f["severity"] for f in result.findings]
    assert severities == sorted(severities, key=SEVERITIES.index)
    invalid = next(f for f in result.findings if f["code"] == "ASSAY_INVALID_INTERVAL")
    assert invalid["count"] == 50 and len(invalid["examples"]) == MAX_EXAMPLES == 20
    assert [e["row_id"] for e in invalid["examples"]] == list(range(1000, 1020))
    summary = {r["code"]: (r["rows"], r["holes"]) for r in result.summary}
    assert summary["ASSAY_INVALID_INTERVAL"] == (50, 1) and summary["COLLAR_EMPTY_STATUS"] == (1, 1)


def test_empty_project_does_not_crash() -> None:
    result = run_checks(_data(collar=[], survey=[], assay=[]), CFG)

    assert result.findings == [] and len(result.summary) == 27


def test_rows_without_a_hole_are_orphans() -> None:
    s = [*_survey(), {"id": 100, "hole": None, "depth": 0.0, "azimuth": 0.0, "dip": -90.0}]
    a = [*_assay(), {"id": 100, "hole": None, "sample": "Z", "from": 0.0, "to": 1.0, "AU": 0.02, "CU": 0.1}]

    assert _found(_data(survey=s, assay=a)) == {("SURVEY_ORPHAN_HOLE", None), ("ASSAY_ORPHAN_HOLE", None)}


def test_missing_values_are_not_zero() -> None:
    c = _collar()
    c[0]["td"] = NAN  # as 0 it would put every survey and sample of H1 below TD
    c[1]["azimuth"], c[1]["dip"] = NAN, NAN  # as 0° dip it would disagree with the first station (-90°)
    for i in (3, 4, 5):
        c[i]["z"] = NAN  # as 0 the median Z of the neighbours of H1…H3 would be 0: a jump of 100 m
    s = _survey()
    _at(s, "H3", depth=50.0)["depth"] = NAN  # as 0 it would repeat the depth 0 and break the order
    a = _assay()
    _at(a, "H4", sample="H4-1")["AU"] = NAN  # as 0 it would not be an empty value
    _at(a, "H5", sample="H5-1")["from"] = NAN  # as 0 it would overlap 0–1

    result = run_checks(_data(collar=c, survey=s, assay=a), CFG)
    found = {(f["code"], f["hole"]): f for f in result.findings}

    assert set(found) == {
        ("COLLAR_INVALID_TD", "H1"), ("COLLAR_MISSING_COORDS", "H4"), ("COLLAR_MISSING_COORDS", "H5"),
        ("COLLAR_MISSING_COORDS", "H6"), ("SURVEY_EMPTY_VALUES", "H3"), ("ASSAY_EMPTY_VALUE", "H4"),
        ("ASSAY_INVALID_INTERVAL", "H5"), ("ASSAY_GAP", "H5"),
    }
    assert found["COLLAR_INVALID_TD", "H1"]["examples"] == [{"td": None}]
    assert found["COLLAR_MISSING_COORDS", "H4"]["examples"] == [{"x": 500000.0, "y": 4500050.0, "z": None}]
    assert found["SURVEY_EMPTY_VALUES", "H3"]["examples"][0]["depth"] is None
    assert found["ASSAY_INVALID_INTERVAL", "H5"]["examples"][0]["from"] is None


def test_infinite_values_do_not_break_the_result() -> None:
    c = _collar()
    c[0]["x"] = math.inf
    a = _assay()
    _at(a, "H2", sample="H2-0")["AU"] = math.inf
    _at(a, "H3", sample="H3-0")["to"] = math.inf

    result = run_checks(_data(collar=c, assay=a), CFG)

    assert {(f["code"], f["hole"]) for f in result.findings} >= {("ASSAY_OUTLIER", "H2"), ("ASSAY_BEYOND_TD", "H3")}
    values = [v for f in result.findings for e in f["examples"] for v in e.values()]
    assert all(v is None or not isinstance(v, float) or math.isfinite(v) for v in values)  # JSON-safe


def test_every_rule_has_a_check() -> None:
    """A dataset with one error of each kind triggers all 27 codes; example keys are documented."""
    c = _collar() + [
        {**_collar()[0], "hole": "FAR", "x": 600000.0},
        {**_collar()[1], "hole": "PLAN", "x": 500025.0, "status": "проект", "z": 300.0},
        {**_collar()[2], "hole": "BARE", "x": 500075.0, "status": None},
    ]
    c[0]["z"] = NAN
    c[1]["td"] = -5.0
    c[2]["azimuth"] = 361.0
    s = [r for r in _survey() if r["hole"] != "H6"]
    s += [{"id": 100 + i, "hole": h, "depth": d, "azimuth": az, "dip": dip} for i, (h, d, az, dip) in enumerate([
        ("GHOST", 0.0, 0.0, -90.0), ("H1", 25.0, NAN, -90.0), ("H2", 75.0, 0.0, 91.0), ("H3", 250.0, 0.0, -90.0),
        ("H4", 50.0, 0.0, -90.0), ("H5", 10.0, 0.0, -90.0), ("H6", 20.0, 90.0, -60.0), ("H6", 40.0, 270.0, -60.0),
        ("PLAN", 0.0, 0.0, -90.0), ("FAR", 0.0, 0.0, -90.0),
    ])]
    a = [r for r in _assay() if r["hole"] != "H6"]
    a += [{"id": 100 + i, "hole": h, "sample": n, "from": f, "to": t, "AU": au, "CU": 0.1}
          for i, (h, n, f, t, au) in enumerate([
              ("GHOST", "G", 0.0, 1.0, 0.02), ("H1", "I", 7.0, 6.0, 0.02), ("H2", "T", 99.0, 101.0, 0.02),
              ("H3", "O", 4.5, 6.0, 0.02), ("H4", "H1-0", 8.0, 9.0, 0.02), ("H5", "N", 5.0, 6.0, -0.5),
              ("H5", "E", 6.0, 7.0, NAN), ("H5", "X", 7.0, 8.0, 9000.0), ("FAR", "F", 0.0, 1.0, 0.02),
          ])]

    result = run_checks(_data(collar=c, survey=s, assay=a), CFG)

    assert {r["code"] for r in result.summary if r["rows"] > 0} == set(RULES)
    assert {f["code"] for f in result.findings} == set(RULES)
    for f in result.findings:
        rule = RULES[f["code"]]
        assert (f["severity"], f["table"]) == (rule.severity, rule.table) and 1 <= f["count"]
        assert all(set(e) <= set(EXAMPLE_FIELDS) for e in f["examples"])
        assert all(("row_id" in e) == (rule.table != "collar") for e in f["examples"])
    for r in result.summary:
        mine = [f for f in result.findings if f["code"] == r["code"]]
        assert (r["rows"], r["holes"]) == (sum(f["count"] for f in mine), len(mine))


# ---- configuration and progress -------------------------------------------------


def test_config_defaults_round_trip_and_validation() -> None:
    assert AuditConfig().as_dict() == {
        "depth_tolerance": 0.01, "dogleg_per_30m": 10.0, "isolated_factor": 10.0, "z_jump": 50.0,
        "assay_outlier_z": 8.0, "planned_markers": ["project", "planned", "plan", "проект", "план"],
    }
    assert AuditConfig.from_dict({}) == AuditConfig.from_dict(None) == AuditConfig()
    cfg = AuditConfig.from_dict({"dogleg_per_30m": 5, "depth_tolerance": "0,005", "planned_markers": " План; Резерв ",
                                 "table": "17"})  # keys of other settings are ignored
    assert (cfg.dogleg_per_30m, cfg.depth_tolerance, cfg.planned_markers) == (5.0, 0.005, ("план", "резерв"))
    assert AuditConfig.from_dict(cfg.as_dict()) == cfg
    assert AuditConfig.from_dict({"depth_tolerance": 0}).depth_tolerance == 0.0
    for bad in ({"dogleg_per_30m": -1}, {"dogleg_per_30m": 0}, {"z_jump": "много"}, {"assay_outlier_z": None},
                {"isolated_factor": NAN}, {"depth_tolerance": -0.01}, {"z_jump": True}, {"planned_markers": 5},
                {"depth_tolerance": math.inf}):
        with pytest.raises(ValueError, match="[а-яА-Я]"):
            AuditConfig.from_dict(bad)
    with pytest.raises(ValueError, match="Изгиб"):
        AuditConfig(dogleg_per_30m=0.0)


def test_progress_is_reported_and_cancellation_propagates() -> None:
    seen: list[tuple[float | None, str | None]] = []
    run_checks(_data(), CFG, tick=lambda fraction, message: seen.append((fraction, message)))
    fractions = [f for f, _ in seen if f is not None]
    assert fractions == sorted(fractions) and 0.0 <= fractions[0] and fractions[-1] == 1.0
    assert all(m is None or isinstance(m, str) for _, m in seen) and any(m for _, m in seen)

    class Cancelled(Exception):
        pass

    def cancel(_fraction: float | None, _message: str | None) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        run_checks(_data(), CFG, tick=cancel)


def test_wrong_array_length_is_an_error() -> None:
    d = _data()
    d.assay_to = d.assay_to[:-1]
    with pytest.raises(ValueError, match="assay_to"):
        run_checks(d, CFG)
    d = _data()
    d.survey_hole = d.survey_hole + 100
    with pytest.raises(ValueError, match="survey_hole"):
        run_checks(d, CFG)
