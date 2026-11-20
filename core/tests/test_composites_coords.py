"""Coordinates of samples (ТЗ 8.2.1; ADR 0018 §6): XB…ZE of the begin, the middle and the end of every row by
the trajectories; a hole without a trajectory — NaN, never a zero, and a line of the journal.

Tolerance: 1e−9 m against the formula of a straight hole; bit for bit against `Trajectories.positions`."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites import coords as cc
from bf_core.composites import intervals as iv
from bf_core.drillholes.desurvey import Collars, Surveys, Trajectories, desurvey

NAN = math.nan
TOL = 1e-9


def test_inclined_straight_hole_exact() -> None:
    """Наклонная прямая скважина: X, Y, Z = устье + глубина · d, d = (sin θ·sin A, sin θ·cos A, −cos θ)
    (ТЗ 8.0.1); the middle is the middle by depth."""
    azimuth, dip, collar = 120.0, -60.0, np.array([5000.0, 7000.0, 350.0])
    traj = desurvey(Collars([0], [collar[0]], [collar[1]], [collar[2]], [100.0], [azimuth], [dip]))
    zenith = math.radians(90.0 + dip)
    d = np.array([
        math.sin(zenith) * math.sin(math.radians(azimuth)),
        math.sin(zenith) * math.cos(math.radians(azimuth)),
        -math.cos(zenith),
    ])  # fmt: skip
    frm, to = np.array([0.0, 2.0, 37.5, 90.0]), np.array([2.0, 5.0, 40.0, 100.0])
    lines = iv.build_lines([42] * 4, frm, to)
    c = cc.line_coords(lines, traj, [0])
    assert c.begin == pytest.approx(collar + frm[:, None] * d, abs=TOL)
    assert c.end == pytest.approx(collar + to[:, None] * d, abs=TOL)
    assert c.middle == pytest.approx(collar + 0.5 * (frm + to)[:, None] * d, abs=TOL)
    assert c.begin[0].tolist() == collar.tolist()  # the collar itself, exact
    assert c.journal == [] and c.no_trajectory.shape == (0,) and c.rows_without == 0
    # the nine columns of the result, in their order
    cols = c.columns()
    assert list(cols) == ["XB", "YB", "ZB", "XM", "YM", "ZM", "XE", "YE", "ZE"] == list(cc.COLUMNS)
    assert np.array_equal(cols["ZB"], c.begin[:, 2]) and np.array_equal(cols["XM"], c.middle[:, 0])
    assert np.array_equal(cols["YE"], c.end[:, 1]) and all(
        v.shape == (4,) and v.flags.c_contiguous for v in cols.values()
    )
    assert cols["ZE"][-1] == pytest.approx(350.0 - 100.0 * math.sin(math.radians(60.0)), abs=TOL)
    # X, Y, Z of composites (ТЗ 8.2.3) — the same middle
    assert np.array_equal(cc.midpoints(lines, traj, [0]), c.middle)


def test_hole_without_a_trajectory_is_nan_and_goes_to_the_journal() -> None:
    # dense 0 — a vertical hole; 1 — no collar coordinates; the hole 30 is not in the set (−1)
    traj = desurvey(Collars([0, 1], [10.0, NAN], [20.0, 0.0], [300.0, 300.0], [50.0, 50.0]), n_holes=2)
    lines = iv.build_lines([10, 10, 20, 20, 20, 30], [0.0, 5.0, 0.0, 1.0, 2.0, 0.0], [5.0, 9.0, 1.0, 2.0, 3.0, 4.0])
    c = cc.line_coords(lines, traj, [0, 1, -1])
    assert c.begin[:2].tolist() == [[10.0, 20.0, 300.0], [10.0, 20.0, 295.0]]
    assert c.middle[:2].tolist() == [[10.0, 20.0, 297.5], [10.0, 20.0, 293.0]]
    assert c.end[:2].tolist() == [[10.0, 20.0, 295.0], [10.0, 20.0, 291.0]]
    for part in (c.begin, c.middle, c.end):
        assert np.isnan(part[2:]).all()  # NaN, not zero
    assert c.no_trajectory.tolist() == [20, 30] and c.rows_without == 4
    assert len(c.journal) == 1 and "без траектории — 2" in c.journal[0] and "4" in c.journal[0]
    with pytest.raises(ValueError):
        cc.line_coords(lines, traj, [0, 1])  # not per hole of the lines


def test_hole_index() -> None:
    set_ids = np.array([11, 14, 15, 90], dtype=np.int64)  # `SetTrajectories.hole_ids`: ascending
    assert cc.hole_index(set_ids, [14, 90, 11, 12, 100, 3]).tolist() == [1, 3, 0, -1, -1, -1]
    assert cc.hole_index(np.zeros(0, dtype=np.int64), [5]).tolist() == [-1]
    assert cc.hole_index(set_ids, []).shape == (0,)


def curved(holes: int, seed: int = 9) -> tuple[Trajectories, iv.Lines]:
    """Curved holes of 80–200 m with 2 m samples from the collar, the rows in any order."""
    rng = np.random.default_rng(seed)
    depth = np.round(rng.uniform(80.0, 200.0, holes))
    per = 5
    md = np.linspace(0.0, 1.0, per)[None, :] * depth[:, None]
    az = (rng.uniform(0.0, 360.0, holes)[:, None] + rng.normal(0.0, 5.0, (holes, per)).cumsum(axis=1)) % 360.0
    dip = np.clip(
        rng.uniform(-90.0, -40.0, holes)[:, None] + rng.normal(3.0, 6.0, (holes, per)).cumsum(axis=1), -90, 10
    )
    col = Collars(
        np.arange(holes), rng.uniform(0, 3000, holes), rng.uniform(0, 3000, holes), rng.uniform(200, 400, holes), depth
    )
    traj = desurvey(col, Surveys(np.repeat(np.arange(holes), per), md.reshape(-1), az.reshape(-1), dip.reshape(-1)))
    n = (depth // 2).astype(np.int64)
    hole = np.repeat(np.arange(holes, dtype=np.int64) + 1000, n)
    frm = 2.0 * (np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n))
    p = rng.permutation(hole.shape[0])
    return traj, iv.build_lines(hole[p], frm[p], frm[p] + 2.0)


def test_curved_holes_are_the_positions_of_the_trajectories_bit_for_bit() -> None:
    traj, lines = curved(3000)
    dense = lines.holes - 1000
    c = cc.line_coords(lines, traj, dense)
    rows = np.repeat(dense, np.diff(lines.starts))
    begin, end, middle = traj.interval_positions(rows, lines.frm, lines.to)
    assert c.begin.tobytes() == begin.tobytes() and c.end.tobytes() == end.tobytes()
    assert c.middle.tobytes() == middle.tobytes() and lines.n > 150_000
    assert not np.isnan(c.begin).any() and c.rows_without == 0
    # the path is continuous: the end of a sample is the begin of the next one
    nxt = np.flatnonzero(np.diff(lines.hole_ids()) == 0)
    assert np.array_equal(c.end[nxt], c.begin[nxt + 1])
    # the middle by depth is on the path, not on the chord: nearer to it than the bend of 2 m lets
    chord = 0.5 * (c.begin + c.end)
    off = np.linalg.norm(c.middle - chord, axis=1)
    assert off.max() < 0.05 and off.max() > 1e-6
    for threads in (2, 8):
        other = cc.line_coords(lines, traj, dense, threads=threads)
        assert all(getattr(other, k).tobytes() == getattr(c, k).tobytes() for k in ("begin", "middle", "end"))
    assert cc.midpoints(lines, traj, dense, threads=3).tobytes() == c.middle.tobytes()


def test_tick_reports_progress_and_cancels() -> None:
    traj, lines = curved(3000)
    seen: list[float] = []
    cc.line_coords(lines, traj, lines.holes - 1000, tick=seen.append)
    assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) >= 6

    class Cancelled(Exception):
        pass

    def stop(_f: float) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        cc.line_coords(lines, traj, lines.holes - 1000, tick=stop)
    with pytest.raises(Cancelled):
        cc.midpoints(lines, traj, lines.holes - 1000, tick=stop)
