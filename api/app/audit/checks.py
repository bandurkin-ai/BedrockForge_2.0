"""Audit checks: pure functions over pandas frames, no database access.

Each check returns findings grouped per hole and rule: `count` is the number of
offending rows, `details["examples"]` holds up to MAX_EXAMPLES of them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.audit.rules import RULES

MAX_EXAMPLES = 20


@dataclass(frozen=True)
class AuditConfig:
    depth_tolerance: float = 0.01  # m, for gaps/overlaps/TD comparisons
    dogleg_per_30m: float = 10.0  # degrees per 30 m
    isolated_factor: float = 10.0  # nearest-neighbour distance vs. the median one
    z_jump: float = 50.0  # m, vs. the median Z of the nearest collars
    assay_outlier_z: float = 8.0  # robust sigmas of log10(value)
    planned_markers: tuple[str, ...] = ("project", "planned", "plan", "проект", "план")


@dataclass
class AuditData:
    collar: pd.DataFrame  # hole_id, x, y, z, td, azimuth, dip, status
    survey: pd.DataFrame  # id, hole_id, depth, azimuth, dip
    assay: pd.DataFrame  # id, hole_id, from_m, to_m, sample_id
    values: pd.DataFrame  # long form: id, hole_id, sample_id, from_m, to_m, component, value


@dataclass
class Finding:
    code: str
    severity: str
    table: str
    hole_id: str | None
    count: int
    message: str
    details: dict = field(default_factory=dict)


def _clean(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return None if math.isnan(f) else f
    if isinstance(value, np.integer):
        return int(value)
    return value


def _emit(out: list[Finding], code: str, df: pd.DataFrame, mask: pd.Series, cols: tuple[str, ...] = ()) -> None:
    sub = df[mask]
    if sub.empty:
        return
    rule = RULES[code]
    for hole, group in sub.groupby("hole_id", sort=True):
        examples = [
            {c: _clean(row[c]) for c in cols}
            for row in group.head(MAX_EXAMPLES)[list(cols)].to_dict("records")
        ] if cols else []
        out.append(Finding(code, rule.severity, rule.table, str(hole), len(group), rule.title, {"examples": examples}))


def _neighbours(xy: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-neighbour distance and indices of the k nearest points for every point.

    Works in blocks so memory stays bounded for thousands of collars.
    """
    n = len(xy)
    nearest = np.empty(n)
    idx = np.empty((n, k), dtype=int)
    for start in range(0, n, 500):
        block = xy[start:start + 500]
        d = np.hypot(block[:, None, 0] - xy[None, :, 0], block[:, None, 1] - xy[None, :, 1])
        d[np.arange(len(block)), np.arange(start, start + len(block))] = np.inf
        part = np.argpartition(d, k - 1, axis=1)[:, :k]
        idx[start:start + len(block)] = part
        nearest[start:start + len(block)] = np.take_along_axis(d, part, axis=1).min(axis=1)
    return nearest, idx


def check_collar(d: AuditData, cfg: AuditConfig) -> list[Finding]:
    out: list[Finding] = []
    c = d.collar
    survey_holes = set(d.survey["hole_id"])
    assay_holes = set(d.assay["hole_id"])

    _emit(out, "COLLAR_MISSING_COORDS", c, c[["x", "y", "z"]].isna().any(axis=1), ("x", "y", "z"))
    _emit(out, "COLLAR_INVALID_TD", c, c["td"].isna() | (c["td"] <= 0), ("td",))
    angle_bad = (c["azimuth"] < 0) | (c["azimuth"] > 360) | (c["dip"] < -90) | (c["dip"] > 90)
    _emit(out, "COLLAR_ANGLE_RANGE", c, angle_bad, ("azimuth", "dip"))

    located = c.dropna(subset=["x", "y"]).reset_index(drop=True)
    if len(located) >= 3:
        k = min(5, len(located) - 1)
        nearest, idx = _neighbours(located[["x", "y"]].to_numpy(), k)
        typical = float(np.median(nearest))
        isolated = located.assign(nn_distance=nearest)
        _emit(out, "COLLAR_ISOLATED", isolated, isolated["nn_distance"] > cfg.isolated_factor * typical, ("x", "y", "nn_distance"))

        z = located["z"].to_numpy()
        neighbour_z = np.nanmedian(z[idx], axis=1)
        jump = located.assign(neighbour_z=neighbour_z)
        _emit(out, "COLLAR_Z_JUMP", jump, (located["z"] - neighbour_z).abs() > cfg.z_jump, ("z", "neighbour_z"))

    status = c["status"].fillna("").astype("string").str.strip()
    _emit(out, "COLLAR_EMPTY_STATUS", c, status == "")
    planned = status.str.lower().isin(cfg.planned_markers) & c["hole_id"].isin(survey_holes | assay_holes)
    _emit(out, "COLLAR_PLANNED_WITH_DATA", c, planned, ("status",))

    _emit(out, "COLLAR_WITHOUT_SURVEY", c, ~c["hole_id"].isin(survey_holes))
    _emit(out, "COLLAR_WITHOUT_ASSAY", c, ~c["hole_id"].isin(assay_holes))
    return out


def _dogleg(s: pd.DataFrame, tol: float) -> pd.DataFrame:
    """Add 'dls' (degrees per 30 m between consecutive valid stations)."""
    # Out-of-range angles are reported by SURVEY_ANGLE_RANGE; do not add dogleg noise on top.
    g = s.dropna(subset=["depth", "azimuth", "dip"])
    g = g[g["azimuth"].between(0, 360) & g["dip"].between(-90, 90)]
    g = g.sort_values(["hole_id", "depth"]).copy()
    az, dip = np.radians(g["azimuth"]), np.radians(g["dip"])
    g["vx"], g["vy"], g["vz"] = np.cos(dip) * np.sin(az), np.cos(dip) * np.cos(az), np.sin(dip)
    prev = g.groupby("hole_id")[["vx", "vy", "vz", "depth"]].shift(1)
    dot = g["vx"] * prev["vx"] + g["vy"] * prev["vy"] + g["vz"] * prev["vz"]
    angle = np.degrees(np.arccos(dot.clip(-1, 1)))
    dz = g["depth"] - prev["depth"]
    g["dls"] = (angle / dz.where(dz > tol)) * 30
    return g


def check_survey(d: AuditData, cfg: AuditConfig) -> list[Finding]:
    out: list[Finding] = []
    tol = cfg.depth_tolerance
    s = d.survey.merge(
        d.collar[["hole_id", "td", "azimuth", "dip"]].rename(
            columns={"azimuth": "collar_az", "dip": "collar_dip"}
        ),
        on="hole_id", how="left",
    )

    _emit(out, "SURVEY_ORPHAN_HOLE", s, ~s["hole_id"].isin(d.collar["hole_id"]), ("depth",))
    _emit(out, "SURVEY_EMPTY_VALUES", s, s[["depth", "azimuth", "dip"]].isna().any(axis=1), ("depth", "azimuth", "dip"))
    angle_bad = (s["azimuth"] < 0) | (s["azimuth"] > 360) | (s["dip"] < -90) | (s["dip"] > 90)
    _emit(out, "SURVEY_ANGLE_RANGE", s, angle_bad, ("depth", "azimuth", "dip"))
    _emit(out, "SURVEY_BEYOND_TD", s, s["depth"] > s["td"] + tol, ("depth", "td"))
    _emit(out, "SURVEY_DUPLICATE_DEPTH", s, s.duplicated(["hole_id", "depth"], keep=False) & s["depth"].notna(), ("depth",))

    by_file = s.sort_values("id")
    prev_depth = by_file.groupby("hole_id")["depth"].shift(1)
    _emit(out, "SURVEY_NOT_ASCENDING", by_file, by_file["depth"] < prev_depth, ("depth",))

    ordered = s.dropna(subset=["depth"]).sort_values(["hole_id", "depth"])
    first = ordered.groupby("hole_id").head(1)
    _emit(out, "SURVEY_FIRST_NOT_ZERO", first, first["depth"] > tol, ("depth",))

    g = _dogleg(s, tol)
    _emit(out, "SURVEY_DOGLEG", g, g["dls"] > cfg.dogleg_per_30m, ("depth", "dls"))

    first_valid = first.dropna(subset=["azimuth", "dip", "collar_dip"])
    dip_diff = (first_valid["dip"] - first_valid["collar_dip"]).abs()
    az_diff = ((first_valid["azimuth"] - first_valid["collar_az"] + 180) % 360 - 180).abs()
    inclined = first_valid["dip"].abs() < 85
    mismatch = (dip_diff > 10) | (inclined & (az_diff > 30))
    shown = first_valid.assign(dip_diff=dip_diff, az_diff=az_diff.where(inclined))
    _emit(out, "SURVEY_COLLAR_MISMATCH", shown, mismatch, ("depth", "dip", "collar_dip", "azimuth", "collar_az"))
    return out


def check_assay(d: AuditData, cfg: AuditConfig) -> list[Finding]:
    out: list[Finding] = []
    tol = cfg.depth_tolerance
    a = d.assay.merge(d.collar[["hole_id", "td"]], on="hole_id", how="left")

    _emit(out, "ASSAY_ORPHAN_HOLE", a, ~a["hole_id"].isin(d.collar["hole_id"]), ("from_m", "to_m"))

    invalid = a["from_m"].isna() | a["to_m"].isna() | (a["from_m"] < 0) | (a["from_m"] >= a["to_m"])
    _emit(out, "ASSAY_INVALID_INTERVAL", a, invalid, ("sample_id", "from_m", "to_m"))
    _emit(out, "ASSAY_BEYOND_TD", a, a["to_m"] > a["td"] + tol, ("sample_id", "from_m", "to_m", "td"))

    ok = a[~invalid].sort_values(["hole_id", "from_m", "to_m"]).copy()
    reached = ok.groupby("hole_id")["to_m"].cummax()
    ok["prev_to"] = reached.groupby(ok["hole_id"]).shift(1)
    cols = ("sample_id", "from_m", "to_m", "prev_to")
    _emit(out, "ASSAY_OVERLAP", ok, ok["from_m"] < ok["prev_to"] - tol, cols)
    _emit(out, "ASSAY_GAP", ok, ok["from_m"] > ok["prev_to"] + tol, cols)

    sample = a["sample_id"].fillna("").astype("string").str.strip()
    dup = (sample != "") & sample.duplicated(keep=False)
    _emit(out, "ASSAY_DUPLICATE_SAMPLE", a, dup, ("sample_id", "from_m", "to_m"))

    v = d.values
    if not v.empty:
        vcols = ("component", "value", "sample_id", "from_m")
        _emit(out, "ASSAY_NEGATIVE_VALUE", v, v["value"] < 0, vcols)
        _emit(out, "ASSAY_EMPTY_VALUE", v, v["value"].isna(), ("component", "sample_id", "from_m"))

        positive = v[v["value"] > 0].copy()
        log = np.log10(positive["value"])
        med = log.groupby(positive["component"]).transform("median")
        mad = (log - med).abs().groupby(positive["component"]).transform("median")
        z = (log - med) / (1.4826 * mad.where(mad > 0))
        _emit(out, "ASSAY_OUTLIER", positive, z > cfg.assay_outlier_z, vcols)
    return out


def run_checks(data: AuditData, cfg: AuditConfig) -> list[Finding]:
    findings = check_collar(data, cfg) + check_survey(data, cfg) + check_assay(data, cfg)
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order[f.severity], f.table, f.code, f.hole_id or ""))
    return findings
