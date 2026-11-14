"""Audit rules on small hand-made datasets (no database).

Baseline: six holes on a 50 m grid, vertical, with clean survey and assay.
Each test breaks one thing and expects exactly the matching finding.
"""

import numpy as np
import pandas as pd
import pytest

from app.audit.checks import AuditConfig, AuditData, run_checks

HOLES = ["H1", "H2", "H3", "H4", "H5", "H6"]
CFG = AuditConfig()


def _collar() -> pd.DataFrame:
    xs = [0, 50, 100, 0, 50, 100]
    ys = [0, 0, 0, 50, 50, 50]
    return pd.DataFrame({
        "hole_id": HOLES,
        "x": [500000.0 + v for v in xs],
        "y": [4500000.0 + v for v in ys],
        "z": [100.0, 101.0, 102.0, 100.5, 101.5, 102.5],
        "td": 100.0, "azimuth": 0.0, "dip": -90.0, "status": "measured",
    })


def _survey() -> pd.DataFrame:
    rows = [(h, d) for h in HOLES for d in (0.0, 50.0, 100.0)]
    df = pd.DataFrame(rows, columns=["hole_id", "depth"])
    df.insert(0, "id", range(1, len(df) + 1))
    df["azimuth"], df["dip"] = 0.0, -90.0
    return df


def _assay() -> pd.DataFrame:
    rows = []
    for h_i, h in enumerate(HOLES):
        for i in range(5):
            rows.append({
                "hole_id": h, "sample_id": f"{h}-{i}", "from_m": float(i), "to_m": float(i + 1),
                "AU": 0.01 * (1 + (h_i + i) % 5), "CU": 0.1 + 0.01 * ((h_i * 2 + i) % 4),
            })
    df = pd.DataFrame(rows)
    df.insert(0, "id", range(1, len(df) + 1))
    return df


def _data(collar=None, survey=None, assay=None) -> AuditData:
    collar = _collar() if collar is None else collar
    survey = _survey() if survey is None else survey
    assay = _assay() if assay is None else assay
    keys = ["id", "hole_id", "sample_id", "from_m", "to_m"]
    values = assay.melt(id_vars=keys, value_vars=["AU", "CU"], var_name="component", value_name="value")
    return AuditData(collar=collar, survey=survey, assay=assay[keys], values=values)


def _found(data: AuditData) -> set[tuple[str, str | None]]:
    return {(f.code, f.hole_id) for f in run_checks(data, CFG)}


def test_clean_baseline_has_no_findings():
    assert run_checks(_data(), CFG) == []


# ---- collar -----------------------------------------------------------------

def test_collar_missing_coords_and_invalid_td_and_angles():
    c = _collar()
    c.loc[0, "x"] = np.nan
    c.loc[1, "td"] = 0.0
    c.loc[2, "dip"] = -120.0
    c.loc[3, "azimuth"] = 400.0

    assert _found(_data(collar=c)) >= {
        ("COLLAR_MISSING_COORDS", "H1"), ("COLLAR_INVALID_TD", "H2"),
        ("COLLAR_ANGLE_RANGE", "H3"), ("COLLAR_ANGLE_RANGE", "H4"),
    }


def test_collar_isolated_only_flags_the_far_one():
    c = pd.concat([_collar(), pd.DataFrame([{
        "hole_id": "H7", "x": 505000.0, "y": 4500000.0, "z": 101.0,
        "td": 100.0, "azimuth": 0.0, "dip": -90.0, "status": "measured",
    }])], ignore_index=True)
    survey = pd.concat([_survey(), pd.DataFrame({"id": [100], "hole_id": ["H7"], "depth": [0.0], "azimuth": [0.0], "dip": [-90.0]})])
    assay = pd.concat([_assay(), pd.DataFrame([{"id": 100, "hole_id": "H7", "sample_id": "H7-0", "from_m": 0.0, "to_m": 1.0, "AU": 0.02, "CU": 0.1}])])

    found = _found(_data(collar=c, survey=survey, assay=assay))

    assert [x for x in found if x[0] == "COLLAR_ISOLATED"] == [("COLLAR_ISOLATED", "H7")]


def test_collar_z_jump():
    c = _collar()
    c.loc[1, "z"] = 300.0

    assert ("COLLAR_Z_JUMP", "H2") in _found(_data(collar=c))


def test_collar_status_planned_and_empty():
    c = _collar()
    c.loc[0, "status"] = "Project"
    c.loc[1, "status"] = ""
    c.loc[2, "status"] = None

    assert _found(_data(collar=c)) == {
        ("COLLAR_PLANNED_WITH_DATA", "H1"), ("COLLAR_EMPTY_STATUS", "H2"), ("COLLAR_EMPTY_STATUS", "H3"),
    }


def test_collar_without_survey_and_assay():
    s = _survey()
    a = _assay()

    found = _found(_data(survey=s[s.hole_id != "H1"], assay=a[a.hole_id != "H2"]))

    assert found == {("COLLAR_WITHOUT_SURVEY", "H1"), ("COLLAR_WITHOUT_ASSAY", "H2")}


# ---- survey -----------------------------------------------------------------

def _survey_with(**edits) -> pd.DataFrame:
    s = _survey()
    for (hole, depth), values in edits.items():
        s.loc[(s.hole_id == hole) & (s.depth == depth), list(values)] = list(values.values())
    return s


def test_survey_orphan_empty_and_ranges():
    s = pd.concat([_survey(), pd.DataFrame({"id": [100], "hole_id": ["GHOST"], "depth": [0.0], "azimuth": [0.0], "dip": [-90.0]})])
    s.loc[(s.hole_id == "H1") & (s.depth == 50.0), "azimuth"] = np.nan
    s.loc[(s.hole_id == "H2") & (s.depth == 50.0), "dip"] = 95.0

    found = _found(_data(survey=s))

    assert found == {("SURVEY_ORPHAN_HOLE", "GHOST"), ("SURVEY_EMPTY_VALUES", "H1"), ("SURVEY_ANGLE_RANGE", "H2")}


def test_survey_beyond_td_duplicate_and_first_not_zero():
    s = _survey()
    s.loc[(s.hole_id == "H1") & (s.depth == 100.0), "depth"] = 150.0
    s.loc[(s.hole_id == "H2") & (s.depth == 100.0), "depth"] = 50.0
    s = s[~((s.hole_id == "H3") & (s.depth == 0.0))]

    found = _found(_data(survey=s))

    assert found == {("SURVEY_BEYOND_TD", "H1"), ("SURVEY_DUPLICATE_DEPTH", "H2"), ("SURVEY_FIRST_NOT_ZERO", "H3")}


def test_survey_not_ascending_in_file_order():
    s = _survey()
    h1 = s.hole_id == "H1"
    s.loc[h1, "id"] = s.loc[h1, "id"].to_numpy()[::-1]  # depths now descend in file order

    assert _found(_data(survey=s)) == {("SURVEY_NOT_ASCENDING", "H1")}


def test_survey_dogleg_flags_sharp_turn_but_not_near_vertical_azimuth_noise():
    c = _collar()
    c.loc[0, ["azimuth", "dip"]] = [0.0, -60.0]  # keep collar consistent with the first station
    s = _survey()
    s.loc[s.hole_id == "H1", ["azimuth", "dip"]] = [0.0, -60.0]
    s.loc[(s.hole_id == "H1") & (s.depth == 50.0), "azimuth"] = 180.0
    s.loc[(s.hole_id == "H2") & (s.depth == 50.0), ["azimuth", "dip"]] = [180.0, -89.9]  # almost vertical

    assert _found(_data(collar=c, survey=s)) == {("SURVEY_DOGLEG", "H1")}


def test_survey_collar_mismatch():
    s = _survey()
    s.loc[(s.hole_id == "H1") & (s.depth == 0.0), "dip"] = -60.0

    assert _found(_data(survey=s)) == {("SURVEY_COLLAR_MISMATCH", "H1"), ("SURVEY_DOGLEG", "H1")}


# ---- assay ------------------------------------------------------------------

def test_assay_orphan_invalid_and_beyond_td():
    a = _assay()
    a = pd.concat([a, pd.DataFrame([{"id": 100, "hole_id": "GHOST", "sample_id": "G-0", "from_m": 0.0, "to_m": 1.0, "AU": 0.02, "CU": 0.1}])])
    a.loc[(a.hole_id == "H1") & (a.from_m == 0.0), ["from_m", "to_m"]] = [2.0, 1.0]
    a.loc[(a.hole_id == "H2") & (a.from_m == 4.0), "to_m"] = 120.0

    found = _found(_data(assay=a))

    assert ("ASSAY_ORPHAN_HOLE", "GHOST") in found
    assert ("ASSAY_INVALID_INTERVAL", "H1") in found
    assert ("ASSAY_BEYOND_TD", "H2") in found


def test_assay_overlap_and_gap():
    a = _assay()
    a.loc[(a.hole_id == "H1") & (a.from_m == 2.0), "from_m"] = 1.5  # overlaps previous 1-2
    a.loc[(a.hole_id == "H2") & (a.from_m == 2.0), ["from_m", "to_m"]] = [3.0, 3.5]  # gap 2-3 then 3.5-4 gap
    a = a[~((a.hole_id == "H2") & (a.from_m == 3.0) & (a.to_m == 4.0))]

    found = _found(_data(assay=a))

    assert ("ASSAY_OVERLAP", "H1") in found
    assert ("ASSAY_GAP", "H2") in found
    assert ("ASSAY_GAP", "H1") not in found


def test_assay_duplicate_sample_negative_and_empty_values():
    a = _assay()
    a.loc[a.sample_id == "H2-1", "sample_id"] = "H1-0"
    a.loc[a.sample_id == "H3-0", "AU"] = -1.0
    a.loc[a.sample_id == "H4-0", "CU"] = np.nan

    found = _found(_data(assay=a))

    assert ("ASSAY_DUPLICATE_SAMPLE", "H1") in found and ("ASSAY_DUPLICATE_SAMPLE", "H2") in found
    assert ("ASSAY_NEGATIVE_VALUE", "H3") in found
    assert ("ASSAY_EMPTY_VALUE", "H4") in found


def test_assay_outlier_is_reported_per_hole_with_examples():
    a = _assay()
    a.loc[a.sample_id == "H5-2", "AU"] = 5000.0

    findings = [f for f in run_checks(_data(assay=a), CFG) if f.code == "ASSAY_OUTLIER"]

    assert [(f.hole_id, f.count) for f in findings] == [("H5", 1)]
    assert findings[0].details["examples"][0] == {"component": "AU", "value": 5000.0, "sample_id": "H5-2", "from_m": 2.0}


def test_findings_are_sorted_errors_first_and_examples_capped():
    c = _collar()
    c.loc[0, "x"] = np.nan  # error
    c.loc[1, "status"] = ""  # info
    a = _assay()
    a = pd.concat([a, pd.DataFrame({
        "id": range(1000, 1050), "hole_id": "H1", "sample_id": [f"X{i}" for i in range(50)],
        "from_m": 10.0, "to_m": 5.0, "AU": 0.02, "CU": 0.1,
    })])

    findings = run_checks(_data(collar=c, assay=a), CFG)

    severities = [f.severity for f in findings]
    assert severities == sorted(severities, key=["error", "warning", "info"].index)
    invalid = next(f for f in findings if f.code == "ASSAY_INVALID_INTERVAL")
    assert invalid.count == 50 and len(invalid.details["examples"]) == 20


def test_empty_project_does_not_crash():
    empty = AuditData(
        collar=_collar().iloc[0:0], survey=_survey().iloc[0:0],
        assay=_assay()[["id", "hole_id", "sample_id", "from_m", "to_m"]].iloc[0:0],
        values=pd.DataFrame(columns=["id", "hole_id", "sample_id", "from_m", "to_m", "component", "value"]),
    )

    assert run_checks(empty, CFG) == []


@pytest.mark.parametrize("threshold", [5.0, 40.0])
def test_dogleg_threshold_is_configurable(threshold):
    s = _survey()
    s.loc[s.hole_id == "H1", ["azimuth", "dip"]] = [0.0, -60.0]
    s.loc[(s.hole_id == "H1") & (s.depth == 50.0), "dip"] = -50.0  # 10 degrees over 50 m = 6 deg/30 m
    c = _collar()
    c.loc[0, ["azimuth", "dip"]] = [0.0, -60.0]

    codes = {f.code for f in run_checks(_data(collar=c, survey=s), AuditConfig(dogleg_per_30m=threshold))}

    assert ("SURVEY_DOGLEG" in codes) == (threshold == 5.0)
