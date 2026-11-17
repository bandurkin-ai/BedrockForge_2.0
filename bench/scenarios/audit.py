"""Audit of a drillhole set (ТЗ 7.12 «Аудит 50 000 скважин / 5 млн проб завершается без нехватки памяти»).

All 27 rules over collars, surveys and samples with two components and sample numbers. The data are hard:
sample rows come in any order (the sort is not skipped), a part of the holes has no collar, values are
missing, and every rule has something to find (a few rows in a thousand), so tens of thousands of findings
with examples are built. The time includes loading the arrays and making the sample numbers Python strings.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

NAMES = (
    "has_collar x y z td azimuth dip status s_id s_hole s_depth s_az s_dip a_id a_hole a_from a_to a_sample au cu"
).split()
STATUSES = ("пробурена", "проект", "")


def _spoil(rng: np.random.Generator, values: np.ndarray, share: float, change: Any) -> None:
    rows = np.flatnonzero(rng.random(values.size) < share)
    values[rows] = change(values[rows]) if callable(change) else change


def prepare(params: dict[str, Any], work: Path) -> None:
    n, k, rows = int(params["holes"]), int(params["surveys"]), int(params["samples"])
    rng = np.random.default_rng(12)
    nan = np.nan
    length = rng.uniform(150.0, 400.0, n).round(1)
    azimuth, dip = rng.uniform(0.0, 360.0, n), rng.uniform(-90.0, -45.0, n)
    d: dict[str, np.ndarray] = {
        "has_collar": rng.random(n) >= 0.005,
        "x": 400_000.0 + rng.uniform(0.0, 20_000.0, n),
        "y": 4_600_000.0 + rng.uniform(0.0, 20_000.0, n),
        "z": rng.normal(1000.0, 15.0, n),
        "td": length.copy(),
        "azimuth": azimuth.copy(),
        "dip": dip.copy(),
        "status": rng.choice(3, n, p=[0.96, 0.02, 0.02]).astype(np.int8),
    }
    _spoil(rng, d["x"], 0.01, nan)
    _spoil(rng, d["x"], 0.002, lambda v: v + 1.0e6)  # isolated
    _spoil(rng, d["z"], 0.005, lambda v: v + 300.0)  # a jump of Z
    _spoil(rng, d["td"], 0.01, nan)
    _spoil(rng, d["td"], 0.005, 0.0)
    _spoil(rng, d["dip"], 0.005, 95.0)

    # Surveys: holes in any order, the stations of a hole together; 2 % of the holes are written bottom-up.
    surveyed = rng.permutation(np.flatnonzero(rng.random(n) >= 0.005))
    hole = np.repeat(surveyed, k)
    station = np.tile(np.arange(k), surveyed.size)
    station = np.where(np.repeat(rng.random(surveyed.size) < 0.02, k), k - 1 - station, station)
    line = np.repeat(np.arange(surveyed.size), k)
    d["s_id"] = np.arange(1, hole.size + 1, dtype=np.int64)
    d["s_hole"] = hole.copy()
    d["s_depth"] = (station * (length[hole] / (k - 1))).round(1)
    d["s_az"] = (azimuth[hole] + rng.normal(0.0, 1.0, (surveyed.size, k)).cumsum(axis=1)[line, station]) % 360.0
    d["s_dip"] = np.clip(dip[hole] + rng.normal(0.0, 0.5, (surveyed.size, k)).cumsum(axis=1)[line, station], -90.0, 0.0)
    again = np.flatnonzero((rng.random(hole.size) < 0.001) & (station > 0) & (np.roll(hole, 1) == hole))
    d["s_depth"][again] = d["s_depth"][again - 1]  # repeated depths
    _spoil(rng, d["s_az"], 0.002, nan)
    _spoil(rng, d["s_az"], 0.005, lambda v: (v + 120.0) % 360.0)  # sharp bends
    _spoil(rng, d["s_dip"], 0.001, -95.0)
    _spoil(rng, d["s_depth"], 0.001, nan)
    _spoil(rng, d["s_depth"], 0.002, lambda v: v + 500.0)  # below TD
    _spoil(rng, d["s_hole"], 0.0005, -1)

    sampled = np.flatnonzero(rng.random(n) >= 0.005)
    per = max(1, rows // sampled.size)
    hole = np.repeat(sampled, per)
    size = np.repeat(length[sampled] / per, per)
    start = np.tile(np.arange(per), sampled.size) * size
    end = start + size
    _spoil(rng, start, 0.005, lambda v: v - 0.5)  # overlaps (and a negative start at the collar)
    _spoil(rng, start, 0.005, lambda v: v + 0.3)  # gaps
    _spoil(rng, start, 0.001, nan)
    _spoil(rng, end, 0.001, lambda v: v + 600.0)  # below TD, covers the rest of the hole
    number = np.arange(hole.size)
    _spoil(rng, number, 0.0005, lambda v: v // 2)  # repeated sample numbers
    sample = np.char.add("S", np.char.zfill(number.astype("U8"), 8)).astype("S9")
    _spoil(rng, sample, 0.01, b"")
    au = rng.lognormal(-1.0, 1.0, hole.size)
    _spoil(rng, au, 0.03, nan)
    _spoil(rng, au, 0.002, lambda v: -v)
    _spoil(rng, au, 0.0005, lambda v: v * 1.0e6)  # outliers
    cu = rng.lognormal(-2.0, 0.7, hole.size)
    _spoil(rng, cu, 0.10, nan)
    order = rng.permutation(hole.size)  # sample rows come in any order
    d["a_id"] = np.arange(1, hole.size + 1, dtype=np.int64)
    columns = {"a_hole": hole, "a_from": start, "a_to": end, "a_sample": sample, "au": au, "cu": cu}
    d.update({name: values[order] for name, values in columns.items()})
    for name in NAMES:
        np.save(work / f"{name}.npy", d[name])
    load()  # compile into the cache outside the timing


def load() -> None:
    from bf_core.audit.checks import AuditData, run_checks

    one, two = np.zeros(1), np.array([0.0, 1.0])
    ids = np.array([1, 2], dtype=np.int64)
    hole = np.zeros(2, dtype=np.int64)
    run_checks(
        AuditData(
            holes=np.array(["A"], dtype=object), has_collar=np.ones(1, dtype=bool), x=one, y=one, z=one, td=one + 9.0,
            azimuth=one, dip=one - 90.0, status=np.array([None], dtype=object),
            survey_row_id=ids, survey_hole=hole, survey_depth=two, survey_azimuth=two * 0.0, survey_dip=two - 90.0,
            assay_row_id=ids, assay_hole=hole, assay_from=two, assay_to=two + 1.0, components={"AU": two},
        )
    )


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.audit.checks import AuditConfig, AuditData, run_checks

    d = {name: np.load(work / f"{name}.npy") for name in NAMES}
    n = len(d["x"])
    sample = d["a_sample"].astype("U9").astype(object)
    sample[sample == ""] = None
    data = AuditData(
        holes=np.array([f"DH-{i:05d}" for i in range(n)], dtype=object),
        has_collar=d["has_collar"], x=d["x"], y=d["y"], z=d["z"], td=d["td"], azimuth=d["azimuth"], dip=d["dip"],
        status=np.array([STATUSES[i] or None for i in d["status"].tolist()], dtype=object),
        survey_row_id=d["s_id"], survey_hole=d["s_hole"], survey_depth=d["s_depth"], survey_azimuth=d["s_az"],
        survey_dip=d["s_dip"],
        assay_row_id=d["a_id"], assay_hole=d["a_hole"], assay_from=d["a_from"], assay_to=d["a_to"],
        assay_sample=sample, components={"AU": d["au"], "CU": d["cu"]},
    )
    del d, sample
    gaps = [0.0]
    last = [time.perf_counter()]

    def tick(_fraction: float | None, _message: str | None) -> None:
        now = time.perf_counter()
        gaps[0] = max(gaps[0], now - last[0])
        last[0] = now

    t0 = time.perf_counter()
    result = run_checks(data, AuditConfig(), tick=tick)
    seconds = time.perf_counter() - t0
    passed = [r["code"] for r in result.summary if r["rows"] == 0]
    assert not passed, passed  # every rule has something to find
    rows = {s: sum(r["rows"] for r in result.summary if r["severity"] == s) for s in ("error", "warning", "info")}
    return {
        "holes": n,
        "surveys": int(len(data.survey_row_id)),
        "samples": int(len(data.assay_row_id)),
        "findings": len(result.findings),
        "examples": sum(len(f["examples"]) for f in result.findings),
        "rows": rows,
        "checks_s": round(seconds, 3),
        "max_tick_gap_s": round(gaps[0], 3),
    }
