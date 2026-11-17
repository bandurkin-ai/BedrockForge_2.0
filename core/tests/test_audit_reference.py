"""All 27 audit rules against a slow per-hole reference on random hard data (ТЗ 7.12), and the paths that only
big sets take: more than 65 536 holes, a hole longer than a sort block, rows in any order."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict

import numpy as np
import pytest

from bf_core.audit import checks
from bf_core.audit.checks import MAX_EXAMPLES, AuditConfig, AuditData, run_checks
from bf_core.audit.rules import RULES, SEVERITIES
from tests.test_audit_checks import NAN, Rows, _assay, _collar, _data

CFG = AuditConfig(dogleg_per_30m=6.0, isolated_factor=4.0, z_jump=30.0, assay_outlier_z=5.0)


def _oracle(collar: Rows, survey: Rows, assay: Rows, cfg: AuditConfig) -> dict[tuple[str, str], int]:
    """Offending rows per rule and hole, hole by hole in plain Python (BF semantics)."""
    out: Counter[tuple[str, str]] = Counter()
    tol = cfg.depth_tolerance
    by = {c["hole"]: c for c in collar}
    nan = math.isnan
    s_by: dict[str, Rows] = defaultdict(list)
    a_by: dict[str, Rows] = defaultdict(list)
    for r in sorted(survey, key=lambda r: r["id"]):  # file order
        s_by[r["hole"]].append(r)
    for r in sorted(assay, key=lambda r: r["id"]):
        a_by[r["hole"]].append(r)

    def bad_angles(az: float, dip: float) -> bool:
        return az < 0 or az > 360 or dip < -90 or dip > 90

    located = [c for c in collar if not nan(c["x"]) and not nan(c["y"])]
    near: dict[str, tuple[float, list[float]]] = {}
    for c in located:
        others = sorted((math.hypot(c["x"] - o["x"], c["y"] - o["y"]), o["z"]) for o in located if o is not c)
        near[c["hole"]] = (others[0][0], [z for _, z in others[:5] if not nan(z)])
    typical = statistics.median(d for d, _ in near.values())
    for c in collar:
        h = c["hole"]
        out["COLLAR_MISSING_COORDS", h] += nan(c["x"]) or nan(c["y"]) or nan(c["z"])
        out["COLLAR_INVALID_TD", h] += nan(c["td"]) or c["td"] <= 0
        out["COLLAR_ANGLE_RANGE", h] += bad_angles(c["azimuth"], c["dip"])
        if h in near:
            out["COLLAR_ISOLATED", h] += near[h][0] > cfg.isolated_factor * typical
            zs = near[h][1]
            out["COLLAR_Z_JUMP", h] += bool(zs) and abs(c["z"] - statistics.median(zs)) > cfg.z_jump
        status = (c["status"] or "").strip()
        out["COLLAR_EMPTY_STATUS", h] += status == ""
        out["COLLAR_PLANNED_WITH_DATA", h] += status.lower() in cfg.planned_markers and (h in s_by or h in a_by)
        out["COLLAR_WITHOUT_SURVEY", h] += h not in s_by
        out["COLLAR_WITHOUT_ASSAY", h] += h not in a_by

    for h, rows in s_by.items():
        td = by[h]["td"] if h in by else NAN
        depths = Counter(r["depth"] for r in rows if not nan(r["depth"]))
        for i, r in enumerate(rows):
            out["SURVEY_ORPHAN_HOLE", h] += h not in by
            out["SURVEY_EMPTY_VALUES", h] += nan(r["depth"]) or nan(r["azimuth"]) or nan(r["dip"])
            out["SURVEY_ANGLE_RANGE", h] += bad_angles(r["azimuth"], r["dip"])
            out["SURVEY_BEYOND_TD", h] += r["depth"] > td + tol
            out["SURVEY_DUPLICATE_DEPTH", h] += depths[r["depth"]] > 1
            out["SURVEY_NOT_ASCENDING", h] += i > 0 and r["depth"] < rows[i - 1]["depth"]
        known = sorted((r for r in rows if not nan(r["depth"])), key=lambda r: r["depth"])
        if known:
            first = known[0]
            out["SURVEY_FIRST_NOT_ZERO", h] += first["depth"] > tol
            if h in by and not (nan(first["azimuth"]) or nan(first["dip"]) or nan(by[h]["dip"])):
                turn = abs((first["azimuth"] - by[h]["azimuth"] + 180) % 360 - 180)
                tilt = abs(first["dip"] - by[h]["dip"])
                out["SURVEY_COLLAR_MISMATCH", h] += tilt > 10 or (abs(first["dip"]) < 85 and turn > 30)
        good = [r for r in known if not (nan(r["azimuth"]) or nan(r["dip"]) or bad_angles(r["azimuth"], r["dip"]))]
        for p, r in zip(good, good[1:], strict=False):
            (a1, d1), (a2, d2) = ((math.radians(q["azimuth"]), math.radians(q["dip"])) for q in (p, r))
            dot = math.cos(d1) * math.cos(d2) * math.cos(a1 - a2) + math.sin(d1) * math.sin(d2)
            dz = r["depth"] - p["depth"]
            if dz > tol:
                bend = math.degrees(math.acos(max(-1.0, min(1.0, dot)))) / dz * 30
                out["SURVEY_DOGLEG", h] += bend > cfg.dogleg_per_30m

    numbers = Counter((r["sample"] or "").strip() for r in assay)
    for h, rows in a_by.items():
        td = by[h]["td"] if h in by else NAN
        valid = []
        for r in rows:
            out["ASSAY_ORPHAN_HOLE", h] += h not in by
            wrong = nan(r["from"]) or nan(r["to"]) or r["from"] < 0 or r["from"] >= r["to"]
            out["ASSAY_INVALID_INTERVAL", h] += wrong
            out["ASSAY_BEYOND_TD", h] += r["to"] > td + tol
            number = (r["sample"] or "").strip()
            out["ASSAY_DUPLICATE_SAMPLE", h] += number != "" and numbers[number] > 1
            if not wrong:
                valid.append(r)
        reach = None
        for r in sorted(valid, key=lambda r: (r["from"], r["to"])):
            if reach is not None:
                out["ASSAY_OVERLAP", h] += r["from"] < reach - tol
                out["ASSAY_GAP", h] += r["from"] > reach + tol
            reach = r["to"] if reach is None else max(reach, r["to"])
    for comp in ("AU", "CU"):
        logs = [math.log10(r[comp]) for r in assay if r[comp] > 0]
        med = statistics.median(logs)
        sigma = 1.4826 * statistics.median(abs(v - med) for v in logs)
        for r in assay:
            out["ASSAY_NEGATIVE_VALUE", r["hole"]] += r[comp] < 0
            out["ASSAY_EMPTY_VALUE", r["hole"]] += nan(r[comp])
            out["ASSAY_OUTLIER", r["hole"]] += r[comp] > 0 and (math.log10(r[comp]) - med) / sigma > cfg.assay_outlier_z
    return {k: int(v) for k, v in out.items() if v}


def _random_set(seed: int, holes: int = 150) -> tuple[Rows, Rows, Rows]:
    """Holes with every kind of error: a few rows in a hundred are spoiled, the last six holes have no collar."""
    rng = np.random.default_rng(seed)

    def some(p: float) -> bool:
        return bool(rng.random() < p)

    def pick(values: list) -> object:  # type: ignore[type-arg]
        return values[int(rng.integers(0, len(values)))]

    collar: Rows = []
    survey: Rows = []
    assay: Rows = []
    for i in range(holes + 6):
        name = f"R{i:03d}" if i % 7 else f"r{i}"  # names do not sort like the dense ids
        td = float(rng.uniform(40.0, 160.0))
        az, dip = float(rng.uniform(0.0, 360.0)), float(rng.uniform(-90.0, -50.0))
        if i < holes:
            collar.append({
                "hole": name,
                "x": NAN if some(0.03) else float(rng.uniform(0, 3000)) * (40 if i % 50 == 7 else 1),
                "y": float(rng.uniform(0, 3000)),
                "z": NAN if some(0.05) else float(rng.normal(500, 12)) + 200 * (i % 40 == 3),
                "td": [NAN, 0.0, -3.0][i % 3] if some(0.04) else td,
                "azimuth": NAN if some(0.05) else az + 400 * some(0.02),
                "dip": NAN if some(0.05) else dip - 60 * some(0.02),
                "status": pick([None, "", " ", "План", "PROJECT ", "done"]) if some(0.3) else "ok",
            })
        if not some(0.05):
            depth = 0.0 if some(0.8) else 7.5
            while depth < td + (30 if some(0.05) else 0):
                az, dip = az + float(rng.normal(0, 3)) + 90 * some(0.03), max(-95.0, dip + float(rng.normal(0, 1.5)))
                station = {"depth": depth, "azimuth": az % 370, "dip": dip}
                survey.append({"hole": name, **{k: NAN if some(0.02) else v for k, v in station.items()}})
                depth += 0.0 if some(0.03) else float(rng.choice([10.0, 25.0, 0.005]))
        if not some(0.05):
            start = 0.0
            while start < td + (5 if some(0.05) else 0):
                end = start + float(rng.choice([1.0, 2.0, 12.0]))
                grades = [float(rng.lognormal(-1, 1)) * (-1 if some(0.01) else 1) * (1e9 if some(0.004) else 1)
                          for _ in range(2)]
                assay.append({
                    "hole": name, "sample": pick([None, "", f"S{len(assay) // 2}", f" S{len(assay)} "]),
                    "from": NAN if some(0.01) else start - 3 * some(0.01), "to": NAN if some(0.01) else end,
                    "AU": NAN if some(0.03) else grades[0], "CU": NAN if some(0.03) else grades[1],
                })
                start = end + float(rng.choice([0.0, 0.0, 0.0, 0.005, 0.02, -0.005, -0.5, 4.0]))
    for rows in (survey, assay):
        for i, j in enumerate(rng.permutation(len(rows))):  # file order is not the order of the holes
            rows[int(j)]["id"] = i + 1
    return collar, survey, assay


def _counts(result: checks.AuditResult) -> dict[tuple[str, str], int]:
    return {(f["code"], f["hole"]): f["count"] for f in result.findings}


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_all_rules_match_the_reference_on_random_data(seed: int) -> None:
    collar, survey, assay = _random_set(seed)
    expected = _oracle(collar, survey, assay, CFG)
    assert {code for code, _ in expected} == set(RULES)  # the data are hard enough for every rule

    result = run_checks(_data(collar, sorted(survey, key=lambda r: r["id"]), sorted(assay, key=lambda r: r["id"])), CFG)

    assert _counts(result) == expected
    assert all(len(f["examples"]) in (0, min(f["count"], MAX_EXAMPLES)) for f in result.findings)
    order = [(SEVERITIES.index(f["severity"]), f["table"], f["code"], f["hole"]) for f in result.findings]
    assert order == sorted(order)
    again = run_checks(_data(collar, survey[::-1], assay[::-1]), CFG)  # arrays in any order: the same result
    assert again.findings == result.findings and again.summary == result.summary


def _shifted(d: AuditData, extra: int) -> AuditData:
    """The same set after `extra` holes that have neither a collar nor rows: every hole number grows."""

    def pad(values: np.ndarray, fill: object) -> np.ndarray:
        return np.concatenate([np.full(extra, fill, dtype=values.dtype), values])

    def move(hole: np.ndarray) -> np.ndarray:
        return np.where(hole < 0, hole, hole + extra)

    names = np.array([f"Z{i}" for i in range(extra)], dtype=object)
    return AuditData(
        holes=np.concatenate([names, d.holes]), has_collar=pad(d.has_collar, False),
        x=pad(d.x, NAN), y=pad(d.y, NAN), z=pad(d.z, NAN), td=pad(d.td, NAN), azimuth=pad(d.azimuth, NAN),
        dip=pad(d.dip, NAN), status=pad(d.status, None),
        survey_row_id=d.survey_row_id, survey_hole=move(d.survey_hole), survey_depth=d.survey_depth,
        survey_azimuth=d.survey_azimuth, survey_dip=d.survey_dip,
        assay_row_id=d.assay_row_id, assay_hole=move(d.assay_hole), assay_from=d.assay_from, assay_to=d.assay_to,
        assay_sample=d.assay_sample, components=d.components,
    )


def test_more_than_65536_holes_give_the_same_result() -> None:
    collar, survey, assay = _random_set(4, holes=60)
    survey.append({"id": 10**6, "hole": None, "depth": 0.0, "azimuth": 0.0, "dip": -90.0})  # a row without a hole
    data = _data(collar, survey, assay)

    small, big = run_checks(data, CFG), run_checks(_shifted(data, 70_000), CFG)

    assert big.findings == small.findings and big.summary == small.summary
    assert _counts(small) == {**_oracle(collar, survey[:-1], assay, CFG), ("SURVEY_ORPHAN_HOLE", None): 1}


def test_hole_longer_than_a_sort_block() -> None:
    n = 3 * checks._BLOCK + 17
    rng = np.random.default_rng(8)
    long = [{"hole": "H1", "sample": f"L{i}", "from": float(i), "to": i + 1.0, "AU": 0.02, "CU": 0.1} for i in range(n)]
    long[5000]["from"], long[7000]["from"], long[9000]["to"] = 4999.5, 7000.5, 9003.0
    a = [r for r in _assay() if r["hole"] != "H1"] + long
    for i, j in enumerate(rng.permutation(len(a))):
        a[int(j)]["id"] = i + 1
    a.sort(key=lambda r: r["id"])
    c = [{**row, "td": float(n)} for row in _collar()]

    found = {(f["code"], f["hole"]): f for f in run_checks(_data(collar=c, assay=a), AuditConfig()).findings}

    assert {k: f["count"] for k, f in found.items()} == {("ASSAY_OVERLAP", "H1"): 3, ("ASSAY_GAP", "H1"): 1}
    assert [(e["from"], e["prev_to"]) for e in found["ASSAY_OVERLAP", "H1"]["examples"]] == [
        (4999.5, 5000.0), (9001.0, 9003.0), (9002.0, 9003.0),
    ]
    assert found["ASSAY_GAP", "H1"]["examples"][0]["sample"] == "L7000"
