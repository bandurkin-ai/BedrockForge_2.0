"""Trajectories of a drillhole set and coordinates of intervals (ТЗ 7.11 «50 000 скважин без нехватки памяти»).

Collars and unordered survey stations → minimum curvature → coordinates of the start, the end and the
middle of every interval. A few holes have no coordinates or no TD, a few stations are unusable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

NAMES = "cx cy cz td s_hole s_depth s_az s_dip i_hole i_from i_to".split()


def prepare(params: dict[str, Any], work: Path) -> None:
    n, k, rows = int(params["holes"]), int(params["surveys"]), int(params["intervals"])
    rng = np.random.default_rng(7)
    length = rng.uniform(150.0, 400.0, n)
    cx = 400_000.0 + rng.uniform(0.0, 20_000.0, n)
    cy = 4_600_000.0 + rng.uniform(0.0, 20_000.0, n)
    cz = rng.uniform(800.0, 1500.0, n)
    cx[rng.random(n) < 0.01] = np.nan  # holes without coordinates are skipped
    td = np.where(rng.random(n) < 0.05, np.nan, length)

    s_hole = np.repeat(np.arange(n, dtype=np.int64), k)
    s_depth = (np.tile(np.arange(k), n) * np.repeat(length / k, k)).round(1)
    drift = rng.normal(0.0, 2.0, (n, k)).cumsum(axis=1).ravel()
    s_az = (np.repeat(rng.uniform(0.0, 360.0, n), k) + drift) % 360.0
    drift = rng.normal(0.0, 1.0, (n, k)).cumsum(axis=1).ravel()
    s_dip = np.clip(np.repeat(rng.uniform(-90.0, -45.0, n), k) + drift, -90.0, 0.0)
    s_az[rng.random(n * k) < 0.002] = np.nan  # unusable stations
    order = rng.permutation(n * k)  # stations come in any order

    per = max(1, rows // n)
    i_hole = np.repeat(np.arange(n, dtype=np.int64), per)
    step = np.repeat(length / per, per)
    i_from = np.tile(np.arange(per), n) * step
    data = (cx, cy, cz, td, s_hole[order], s_depth[order], s_az[order], s_dip[order])
    for name, values in zip(NAMES, (*data, i_hole, i_from, i_from + step), strict=True):
        np.save(work / f"{name}.npy", values)
    load()  # compile into the cache outside the timing


def load() -> None:
    from bf_core.drillholes.desurvey import Collars, Surveys, desurvey

    zero = np.zeros(2)
    hole = np.array([0, 0], dtype=np.int64)
    surveys = Surveys(hole, np.array([5.0, 0.0]), zero, zero - 80.0)
    one = zero[:1]
    traj = desurvey(Collars(hole[:1], one, one, one, one + 9.0), surveys)
    traj.interval_positions(hole, zero, zero + 1.0)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.drillholes.desurvey import Collars, Surveys, desurvey

    d = {name: np.load(work / f"{name}.npy") for name in NAMES}
    n = len(d["cx"])
    collars = Collars(np.arange(n, dtype=np.int64), d["cx"], d["cy"], d["cz"], d["td"])
    surveys = Surveys(d["s_hole"], d["s_depth"], d["s_az"], d["s_dip"])
    traj = desurvey(collars, surveys)
    start, end, mid = traj.interval_positions(d["i_hole"], d["i_from"], d["i_to"])
    located = np.isfinite(mid[:, 2])
    assert located.sum() + np.isin(d["i_hole"], traj.skipped).sum() == len(mid)
    return {
        "holes": n,
        "stations": int(len(traj.md)),
        "skipped": int(len(traj.skipped)),
        "invalid_stations": traj.invalid_stations,
        "intervals": int(len(mid)),
        "located": int(located.sum()),
        "mean_z": float(mid[located, 2].mean()),
    }
