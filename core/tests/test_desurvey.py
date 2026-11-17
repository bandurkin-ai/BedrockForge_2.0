"""Drillhole trajectories (ТЗ 7.11, 7.34 п. 98): BF tests ported to the array kernel, the other survey
methods against hand-computed values, hard data, and the Micromine sample (MICROMINE_SAMPLE_DIR)."""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

from bf_core.drillholes.desurvey import (
    METHODS,
    SOURCE_COLLAR,
    SOURCE_NONE,
    SOURCE_SURVEY,
    SOURCE_VERTICAL,
    Collars,
    Surveys,
    Trajectories,
    desurvey,
    sample_paths,
)

NAN = math.nan


def _hole(stations, td=100.0, az=None, dip=None, x=1000.0, y=2000.0, z=500.0, method="minimum_curvature"):  # type: ignore[no-untyped-def]
    """One hole, as BF `build_trajectory` (None = missing)."""

    def col(values):  # type: ignore[no-untyped-def]
        return np.array([NAN if v is None else v for v in values], dtype=float)

    collars = Collars(hole=np.array([0]), x=col([x]), y=col([y]), z=col([z]), td=col([td]), azimuth=col([az]),
                      dip=col([dip]))  # fmt: skip
    surveys = Surveys(np.zeros(len(stations), dtype=np.int64), *(col([s[k] for s in stations]) for k in range(3)))
    return desurvey(collars, surveys, method=method, n_holes=1)


def _pos(t: Trajectories, depths, hole: int = 0) -> np.ndarray:  # type: ignore[no-untyped-def]
    return t.positions(np.full(len(depths), hole), np.asarray(depths, dtype=float))


# --- BF tests (api/tests/test_trajectory.py) ----------------------------------------------------


def test_vertical_hole_goes_straight_down() -> None:
    t = _hole([(0.0, 0.0, -90.0)], td=100.0)
    x, y, z = _pos(t, [0, 50, 100]).T
    assert np.allclose(x, 1000) and np.allclose(y, 2000)
    assert np.allclose(z, [500, 450, 400])


def test_inclined_straight_hole() -> None:
    t = _hole([(0.0, 90.0, -45.0)], td=100.0)  # east, 45 degrees down
    x, y, z = _pos(t, [100]).T
    r = 100 * math.cos(math.radians(45))
    assert x[0] == pytest.approx(1000 + r) and y[0] == pytest.approx(2000, abs=1e-9)
    assert z[0] == pytest.approx(500 - r)


def test_minimum_curvature_is_an_exact_circle_arc() -> None:
    radius = 100.0
    t = _hole([(0.0, 90.0, -90.0), (math.pi * radius / 2, 90.0, 0.0)], td=0.0)  # down, turning to east
    end = _pos(t, [math.pi * radius / 2])[0]
    assert end == pytest.approx([1000 + radius, 2000, 500 - radius], abs=1e-9)
    for theta in np.radians([0.001, 1, 30, 45, 60, 89.999]):  # any point of the quarter circle
        p = _pos(t, [radius * theta])[0]
        want = [1000 + radius * (1 - math.cos(theta)), 2000, 500 - radius * math.sin(theta)]
        assert p == pytest.approx(want, abs=1e-9)


@pytest.mark.parametrize("origin", [(0.0, 0.0, 0.0), (466570.0, 4600000.0, 1200.0)])
def test_minimum_curvature_follows_a_circle_in_any_plane(origin: tuple[float, float, float]) -> None:
    """Stations on one circle (uneven spacing, any plane): every course is an arc of that circle."""
    rng = np.random.default_rng(4)
    for radius in (40.0, 250.0, 3000.0):
        u = rng.normal(size=3)
        u /= np.linalg.norm(u)  # direction at the collar
        v = np.cross(u, rng.normal(size=3))
        v /= np.linalg.norm(v)  # towards the centre of the circle
        theta = np.concatenate([[0.0], np.sort(rng.uniform(0.0, 3.0, 9))])  # up to 172° of turn
        d = np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v
        az, dip = np.degrees(np.arctan2(d[:, 0], d[:, 1])) % 360, np.degrees(np.arcsin(d[:, 2]))
        stations = list(zip(radius * theta, az, dip, strict=True))
        t = _hole(stations, td=0.0, x=origin[0], y=origin[1], z=origin[2])
        at = rng.uniform(0.0, theta[-1], 500)
        want = np.array(origin) + radius * (np.sin(at)[:, None] * u + (1.0 - np.cos(at))[:, None] * v)
        assert np.abs(_pos(t, radius * at) - want).max() <= (1e-9 if origin[0] == 0 else 2e-8) * max(1.0, radius / 250)


def test_positions_are_continuous_at_stations_and_length_is_preserved() -> None:
    t = _hole([(0.0, 10.0, -80.0), (30.0, 40.0, -70.0), (60.0, 80.0, -62.0)], td=90.0)
    md, xyz, _ = t.stations(0)
    length = t.lengths()[0]
    pts = _pos(t, np.linspace(0, length, 400))
    steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    assert np.allclose(_pos(t, md), xyz, rtol=0, atol=1e-9)
    # the polyline of a smooth path is a bit shorter than the arc, never longer
    assert steps.sum() <= length + 1e-6
    assert steps.sum() == pytest.approx(length, rel=1e-3)


def test_extends_to_td_with_last_orientation_and_up_to_collar() -> None:
    t = _hole([(20.0, 0.0, -90.0), (50.0, 0.0, -90.0)], td=80.0)
    md, _, _ = t.stations(0)
    assert md[0] == 0.0 and md[-1] == 80.0
    assert _pos(t, [80])[0][2] == pytest.approx(420.0)
    # the first station gives the orientation up to the collar (BF), not the collar angles
    u = _hole([(20.0, 90.0, -45.0)], td=20.0, az=0.0, dip=-90.0)
    assert _pos(u, [20])[0] == pytest.approx([1000 + 20 * math.sqrt(0.5), 2000, 500 - 20 * math.sqrt(0.5)])


def test_falls_back_to_collar_angles_then_to_vertical() -> None:
    inclined = _hole([], az=0.0, dip=-60.0, td=100.0)
    vertical = _hole([], td=100.0)
    half = _hole([], az=45.0, td=100.0)  # an azimuth without a dip is not an orientation
    out_of_range = _hole([], az=45.0, dip=-120.0, td=100.0)
    assert inclined.source[0] == SOURCE_COLLAR and _pos(inclined, [100])[0][1] > 2000
    assert vertical.source[0] == SOURCE_VERTICAL and _pos(vertical, [100])[0][2] == pytest.approx(400.0)
    assert half.source[0] == SOURCE_VERTICAL and out_of_range.source[0] == SOURCE_VERTICAL


def test_skips_invalid_stations_and_duplicate_depths() -> None:
    t = _hole([(0.0, 0.0, -90.0), (10.0, NAN, -80.0), (20.0, 400.0, -80.0), (30.0, 0.0, -95.0),
               (40.0, 0.0, -90.0), (40.0, 90.0, -10.0), (None, 0.0, -90.0), (-5.0, 0.0, -90.0),
               (math.inf, 0.0, -90.0)], td=50.0)  # fmt: skip
    md, _, dirs = t.stations(0)
    assert t.source[0] == SOURCE_SURVEY
    assert list(md) == [0.0, 40.0, 50.0]  # only valid stations; first of duplicate depths wins
    assert dirs[1] == pytest.approx([0, 0, -1], abs=1e-15)
    assert (t.invalid_stations, t.duplicate_stations) == (6, 1)


def test_missing_coordinates_give_no_trajectory() -> None:
    collars = Collars(hole=np.array([0, 1, 2]), x=np.array([NAN, 1.0, 5.0]), y=np.array([1.0, NAN, 5.0]),
                      z=np.array([1.0, 1.0, 5.0]), td=np.full(3, 10.0))  # fmt: skip
    surveys = Surveys(np.array([4, 1]), np.array([0.0, 0.0]), np.array([0.0, 0.0]), np.array([-90.0, -90.0]))
    t = desurvey(collars, surveys)
    assert t.n_holes == 5 and list(t.skipped) == [0, 1, 4]  # 4: surveys without a collar; 3: nothing known
    assert list(t.source) == [SOURCE_NONE, SOURCE_NONE, SOURCE_VERTICAL, SOURCE_NONE, SOURCE_NONE]
    assert list(np.diff(t.offsets)) == [0, 0, 2, 0, 0]
    got = t.positions(np.array([0, 1, 2, 3, 4]), np.full(5, 4.0))
    assert np.isnan(got[[0, 1, 3, 4]]).all() and got[2] == pytest.approx([5, 5, 1])  # missing is never zero
    assert np.isnan(t.lengths()[[0, 1, 3, 4]]).all() and t.lengths()[2] == 10.0


def test_sample_path_contains_stations_and_respects_step() -> None:
    t = _hole([(0.0, 0.0, -90.0), (35.0, 0.0, -90.0)], td=100.0)
    path = sample_paths(t, 10.0)
    assert path.points.shape[1] == 4 and list(path.offsets) == [0, len(path.points)]
    assert {35.0, 100.0, 0.0} <= set(path.points[:, 3])
    assert np.diff(path.points[:, 3]).min() > 0 and np.diff(path.points[:, 3]).max() <= 10.0 + 1e-9
    assert np.allclose(path.points[:, :3], _pos(t, path.points[:, 3]), rtol=0, atol=1e-9)


def test_zero_length_hole_does_not_crash() -> None:
    t = _hole([], td=0.0)
    assert sample_paths(t, 10).points.shape == (1, 4)
    assert _pos(t, [0.0, 5.0]) == pytest.approx(np.array([[1000, 2000, 500]] * 2))
    for td in (None, -3.0, math.inf):
        assert t.lengths()[0] == 0.0 and _hole([(0.0, 10.0, -60.0)], td=td).lengths()[0] == 0.0


# --- the BF algorithm, hole by hole: the reference the array kernel must reproduce --------------


def _ref_dirs(az, dip):  # type: ignore[no-untyped-def]
    az, dip = np.radians(az), np.radians(dip)
    return np.column_stack([np.sin(az) * np.cos(dip), np.cos(az) * np.cos(dip), np.sin(dip)])


def _ref_angle(a, b):  # type: ignore[no-untyped-def]
    return np.arccos(np.clip((a * b).sum(axis=1), -1.0, 1.0))


def _ref_rf(beta):  # type: ignore[no-untyped-def]
    out = np.ones_like(beta)
    bent = beta > 1e-9
    out[bent] = 2.0 / beta[bent] * np.tan(beta[bent] / 2.0)
    return out


def _ref_build(x, y, z, td, caz, cdip, stations):  # type: ignore[no-untyped-def]
    if not all(math.isfinite(v) for v in (x, y, z)):
        return None
    valid: dict[float, tuple[float, float]] = {}
    for depth, az, dip in stations:
        if not all(math.isfinite(v) for v in (depth, az, dip)):
            continue
        if depth < 0 or not 0 <= az <= 360 or not -90 <= dip <= 90:
            continue
        valid.setdefault(float(depth), (float(az), float(dip)))
    if valid:
        source, rows = SOURCE_SURVEY, sorted((d, *angles) for d, angles in valid.items())
    else:
        has = math.isfinite(caz) and math.isfinite(cdip) and 0 <= caz <= 360 and -90 <= cdip <= 90
        source, rows = (SOURCE_COLLAR, [(0.0, caz, cdip)]) if has else (SOURCE_VERTICAL, [(0.0, 0.0, -90.0)])
    if rows[0][0] > 0:
        rows.insert(0, (0.0, rows[0][1], rows[0][2]))
    end = max(rows[-1][0], td if math.isfinite(td) else 0.0)
    if end > rows[-1][0]:
        rows.append((end, rows[-1][1], rows[-1][2]))
    md = np.array([r[0] for r in rows])
    dirs = _ref_dirs(np.array([r[1] for r in rows]), np.array([r[2] for r in rows]))
    xyz = np.empty((len(rows), 3))
    xyz[0] = (x, y, z)
    for k in range(1, len(rows)):
        rf = _ref_rf(_ref_angle(dirs[k - 1 : k], dirs[k : k + 1]))[0]
        xyz[k] = xyz[k - 1] + (md[k] - md[k - 1]) / 2.0 * rf * (dirs[k - 1] + dirs[k])
    return md, xyz, dirs, source


def _ref_positions(md, xyz, dirs, depths):  # type: ignore[no-untyped-def]
    d = np.clip(np.asarray(depths, dtype=float), 0.0, md[-1])
    if len(md) == 1:
        return np.repeat(xyz[:1], len(d), axis=0)
    i = np.clip(np.searchsorted(md, d, side="right") - 1, 0, len(md) - 2)
    t1, t2 = dirs[i], dirs[i + 1]
    f = (d - md[i]) / (md[i + 1] - md[i])
    beta = _ref_angle(t1, t2)
    sin_beta = np.sin(beta)
    straight = sin_beta < 1e-9
    safe = np.where(straight, 1.0, sin_beta)
    w1 = np.where(straight, 1.0 - f, np.sin((1.0 - f) * beta) / safe)
    w2 = np.where(straight, f, np.sin(f * beta) / safe)
    td = w1[:, None] * t1 + w2[:, None] * t2
    td /= np.linalg.norm(td, axis=1, keepdims=True)
    return xyz[i] + ((d - md[i]) / 2.0 * _ref_rf(_ref_angle(t1, td)))[:, None] * (t1 + td)


def _random_set(rng: np.random.Generator, n: int, origin=(0.0, 0.0, 0.0)):  # type: ignore[no-untyped-def]
    """Hard data: shuffled rows, duplicates, gaps and out-of-range values, holes without collar or surveys."""
    cx = origin[0] + rng.uniform(-5000, 5000, n)
    cy = origin[1] + rng.uniform(-5000, 5000, n)
    cz = origin[2] + rng.uniform(-200, 200, n)
    cx[rng.random(n) < 0.03] = NAN
    cz[rng.random(n) < 0.02] = NAN
    td = np.where(rng.random(n) < 0.15, NAN, rng.uniform(0, 600, n))
    caz = np.where(rng.random(n) < 0.3, NAN, rng.uniform(0, 360, n))
    cdip = np.where(rng.random(n) < 0.3, NAN, rng.uniform(-95, 30, n))
    holes, depth, az, dip = [], [], [], []
    for h in range(n):
        k = int(rng.integers(0, 14)) if rng.random() > 0.2 else 0
        d = np.round(rng.uniform(0, 500, k), 0)  # whole metres: duplicate depths do happen
        if k and rng.random() < 0.5:
            d[0] = 0.0
        a = (rng.uniform(0, 360) + np.cumsum(rng.normal(0, 8, k))) % 360
        p = np.clip(rng.uniform(-90, -30) + np.cumsum(rng.normal(0, 4, k)), -90, 90)
        bad = rng.random(k) < 0.08
        a[bad] = rng.choice([NAN, 400.0, -1.0], bad.sum())
        p[rng.random(k) < 0.04] = rng.choice([NAN, -95.0, 100.0])
        d[rng.random(k) < 0.03] = rng.choice([NAN, -2.0, math.inf])
        holes += [h] * k
        depth += list(d)
        az += list(a)
        dip += list(p)
    order = rng.permutation(len(holes))
    collars = Collars(np.arange(n), cx, cy, cz, td, caz, cdip)
    surveys = Surveys(np.array(holes, dtype=np.int64)[order], *(np.array(v)[order] for v in (depth, az, dip)))
    return collars, surveys


@pytest.mark.parametrize("origin", [(0.0, 0.0, 0.0), (466570.0, 4600000.0, 1200.0)])
def test_minimum_curvature_matches_the_bf_reference_on_hard_data(origin: tuple[float, float, float]) -> None:
    rng = np.random.default_rng(11)
    n = 400
    collars, surveys = _random_set(rng, n, origin)
    t = desurvey(collars, surveys)
    tol = 1e-9 if origin[0] == 0 else 2e-8  # one float64 step at 4.6e6 m is 9e-10 m
    skipped, worst = [], 0.0
    for h in range(n):
        rows = surveys.hole == h
        stations = list(zip(surveys.depth[rows], surveys.azimuth[rows], surveys.dip[rows], strict=True))
        ref = _ref_build(*(float(c[h]) for c in (collars.x, collars.y, collars.z, collars.td, collars.azimuth,
                                                 collars.dip)), stations)  # fmt: skip
        md, xyz, dirs = t.stations(h)
        if ref is None:
            skipped.append(h)
            assert len(md) == 0 and t.source[h] == SOURCE_NONE
            continue
        assert list(md) == list(ref[0]) and t.source[h] == ref[3]
        assert np.allclose(dirs, ref[2], rtol=0, atol=1e-15) and np.allclose(xyz, ref[1], rtol=0, atol=tol)
        depths = np.concatenate([rng.uniform(-10, md[-1] + 10, 40), md, [NAN]])
        got, want = _pos(t, depths, h), _ref_positions(*ref[:3], depths[:-1])
        assert np.isnan(got[-1]).all()
        worst = max(worst, float(np.abs(got[:-1] - want).max()))
    assert list(t.skipped) == skipped and len(skipped) > 5
    assert worst <= tol, worst


# --- other methods (7.34 п. 98) against hand-computed values --------------------------------------

# One course of 95 m: inclination 45° → 52° (dip -45° → -38°), azimuth 130° → 139°.
COURSE = [(0.0, 130.0, -45.0), (95.0, 139.0, -38.0)]


def _end(method: str, stations=COURSE, depth: float | None = None):  # type: ignore[no-untyped-def]
    t = _hole(stations, td=0.0, x=0.0, y=0.0, z=0.0, method=method)
    return _pos(t, [t.lengths()[0] if depth is None else depth])[0]


def test_minimum_curvature_textbook_course() -> None:
    # dogleg 9.705939°, ratio factor 1.002398262; ΔE, ΔN, ΔTVD by the inclination/azimuth formulas
    assert _end("minimum_curvature") == pytest.approx([50.406797, -49.958367, -62.982179], abs=1e-6)


def test_balanced_tangent_course() -> None:
    # L/2 · (t1 + t2): E 47.5·(sin45·sin130 + sin52·sin139), N 47.5·(sin45·cos130 + sin52·cos139), …
    assert _end("balanced_tangent") == pytest.approx([50.286198, -49.838840, -62.831492], abs=1e-6)
    # two straight halves: the upper one along the upper station, the lower one along the lower station
    upper = 30 * np.array([math.sin(math.radians(130)) * math.sqrt(0.5), math.cos(math.radians(130)) * math.sqrt(0.5),
                           -math.sqrt(0.5)])  # fmt: skip
    assert _end("balanced_tangent", depth=30.0) == pytest.approx(upper, abs=1e-9)
    assert _end("balanced_tangent", depth=47.5) == pytest.approx(upper * 47.5 / 30, abs=1e-9)


def test_average_angle_course() -> None:
    # L · direction(azimuth 134.5°, dip -41.5°)
    assert _end("average_angle") == pytest.approx([50.748335, -49.870250, -62.948905], abs=1e-6)
    fifth = np.array([50.748335, -49.870250, -62.948905]) / 5
    assert _end("average_angle", depth=19.0) == pytest.approx(fifth, abs=1e-6)
    # 100 m, azimuth 30° → 50°, dip -60° → -40°: azimuth 40°, dip -50°
    got = _end("average_angle", [(0.0, 30.0, -60.0), (100.0, 50.0, -40.0)])
    assert got == pytest.approx([41.317591, 49.240388, -76.604444], abs=1e-6)
    # azimuths are averaged along the shorter arc: 350° and 10° give north, not south
    north = _end("average_angle", [(0.0, 350.0, -60.0), (100.0, 10.0, -60.0)])
    assert north == pytest.approx([0.0, 50.0, -86.602540], abs=1e-6)
    # a vertical station has no azimuth: the azimuth of the other station is used (dip -80°, azimuth 90°)
    bend = _end("average_angle", [(0.0, 0.0, -90.0), (100.0, 90.0, -70.0)])
    assert bend == pytest.approx([17.364818, 0.0, -98.480775], abs=1e-6)


def test_tangent_holds_each_station_until_the_next_one() -> None:
    stations = [(0.0, 0.0, -90.0), (100.0, 90.0, -45.0)]
    t = _hole(stations, td=200.0, x=0.0, y=0.0, z=0.0, method="tangent")
    r = math.sqrt(0.5)
    want = [[0, 0, -40], [0, 0, -100], [50 * r, 0, -100 - 50 * r], [100 * r, 0, -100 - 100 * r]]
    assert _pos(t, [40, 100, 150, 200]) == pytest.approx(np.array(want), abs=1e-9)
    course = [95 * math.sin(math.radians(130)) * r, 95 * math.cos(math.radians(130)) * r, -95 * r]
    assert _end("tangent", COURSE) == pytest.approx(course, abs=1e-9)


@pytest.mark.parametrize("method", METHODS)
def test_every_method_is_continuous_keeps_length_and_agrees_on_straight_holes(method: str) -> None:
    t = _hole([(0.0, 10.0, -80.0), (30.0, 40.0, -70.0), (60.0, 80.0, -62.0)], td=90.0, method=method)
    md, xyz, dirs = t.stations(0)
    assert t.method == method and list(md) == [0.0, 30.0, 60.0, 90.0]
    assert np.allclose(np.linalg.norm(dirs, axis=1), 1.0, rtol=0, atol=1e-15)
    assert np.allclose(_pos(t, md), xyz, rtol=0, atol=1e-9)
    path = sample_paths(t, 0.25).points
    steps = np.linalg.norm(np.diff(path[:, :3], axis=0), axis=1)
    assert np.all(steps <= np.diff(path[:, 3]) + 1e-9)  # never longer than the measured depth
    assert steps.sum() == pytest.approx(90.0, rel=1e-3 if method == "minimum_curvature" else 1e-12)
    straight = _hole([(0.0, 75.0, -55.0), (40.0, 75.0, -55.0)], td=100.0, method=method)
    reference = _hole([(0.0, 75.0, -55.0)], td=100.0)
    depths = np.linspace(0, 100, 33)
    assert np.allclose(_pos(straight, depths), _pos(reference, depths), rtol=0, atol=1e-9)


def test_unknown_method_and_bad_input_are_rejected() -> None:
    with pytest.raises(ValueError, match="method"):
        _hole([], method="spline")
    one = np.array([1.0])
    with pytest.raises(ValueError, match="twice"):
        desurvey(Collars(np.array([2, 2]), np.ones(2), np.ones(2), np.ones(2)))
    with pytest.raises(ValueError, match="hole index"):
        desurvey(Collars(np.array([-1]), one, one, one))
    with pytest.raises(ValueError, match="hole index"):
        desurvey(Collars(np.array([0]), one, one, one), Surveys(np.array([3]), one, one, one), n_holes=2)
    with pytest.raises(ValueError, match="length"):
        desurvey(Collars(np.array([0]), one, np.ones(2), one))
    with pytest.raises(TypeError, match="integer"):
        desurvey(Collars(np.array([0.5]), one, one, one))


# --- hard data ------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_reversal_tiny_bends_and_near_vertical_stay_finite(method: str) -> None:
    cases = [
        [(0.0, 0.0, 90.0), (50.0, 0.0, -90.0)],  # straight up, then straight down: no circle fits
        [(0.0, 10.0, -45.0), (50.0, 190.0, 45.0)],  # exactly the opposite direction
        [(0.0, 10.0, -60.0), (50.0, 10.0 + 1e-10, -60.0), (90.0, 10.0, -60.0 + 1e-13)],  # bends of 1e-12 rad
        [(0.0, 0.0, -90.0), (50.0, 123.0, -89.9999999), (80.0, 301.0, -90.0)],
        [(0.0, 359.9999, -1e-9), (50.0, 0.0001, 1e-9)],
    ]
    for stations in cases:
        t = _hole(stations, td=120.0, method=method)
        md, xyz, _ = t.stations(0)
        pts = _pos(t, np.linspace(0, 120, 481))
        assert np.isfinite(pts).all() and np.isfinite(xyz).all()
        assert np.allclose(_pos(t, md), xyz, rtol=0, atol=1e-9)
        assert np.linalg.norm(np.diff(pts, axis=0), axis=1).max() <= 0.25 + 1e-9  # no jumps: 0.25 m of depth a step


def test_survey_row_order_does_not_matter_and_one_hole_may_hold_most_rows() -> None:
    rng = np.random.default_rng(5)
    n = 200_000  # one hole with all the rows, deepest first: the stable merge sort path
    depth = np.arange(n, dtype=float)[::-1] * 0.5
    az = (np.arange(n)[::-1] * 0.001) % 360
    dip = np.full(n, -70.0)
    hole = np.full(n, 1)
    collars = Collars(np.array([0, 1, 2]), np.zeros(3), np.zeros(3), np.zeros(3), np.full(3, 50.0))
    a = desurvey(collars, Surveys(hole, depth, az, dip))
    order = rng.permutation(n)
    b = desurvey(collars, Surveys(hole[order], depth[order], az[order], dip[order]))
    md = a.stations(1)[0]
    assert len(md) == n and np.all(np.diff(md) > 0) and md[-1] == (n - 1) * 0.5
    assert list(np.diff(a.offsets)) == [2, n, 2]
    for name in ("offsets", "md", "xyz", "dirs", "source"):
        assert np.array_equal(getattr(a, name), getattr(b, name)), name
    d = rng.uniform(0, md[-1], 1000)
    assert np.array_equal(_pos(a, d, 1), _pos(b, d, 1))
    assert np.allclose(np.linalg.norm(np.diff(a.stations(1)[1], axis=0), axis=1), 0.5, rtol=0, atol=1e-9)
    # unordered rows with many repeated depths: of equal depths the first row wins, as in BF
    m = 20_000
    depth, az, dip = rng.integers(0, 3000, m).astype(float), rng.uniform(0, 360, m), rng.uniform(-90, 90, m)
    one = Collars(np.array([0]), np.zeros(1), np.zeros(1), np.zeros(1))
    t = desurvey(one, Surveys(np.zeros(m, dtype=np.int64), depth, az, dip))
    ref = _ref_build(0.0, 0.0, 0.0, NAN, NAN, NAN, list(zip(depth, az, dip, strict=True)))
    assert t.duplicate_stations == m - len(np.unique(depth)) and list(t.stations(0)[0]) == list(ref[0])
    assert np.allclose(t.stations(0)[2], ref[2], rtol=0, atol=1e-15)


def test_positions_of_many_rows_clip_depths_and_keep_missing_as_nan() -> None:
    rng = np.random.default_rng(3)
    collars, surveys = _random_set(rng, 3000, (466570.0, 4600000.0, 1200.0))
    t = desurvey(collars, surveys, n_holes=3005)
    assert t.md.dtype == np.float64 and t.xyz.dtype == np.float64 and t.xyz.flags.c_contiguous
    m = 700_000
    hole = rng.integers(-2, 3010, m)
    depth = rng.uniform(-20, 700, m)
    depth[rng.random(m) < 0.01] = NAN
    depth[:4] = [math.inf, -math.inf, 0.0, 1e300]
    got = t.positions(hole, depth)
    assert got.shape == (m, 3) and got.dtype == np.float64
    known = (hole >= 0) & (hole < t.n_holes)
    built = np.zeros(m, dtype=bool)
    built[known] = t.source[hole[known]] != SOURCE_NONE
    missing = ~built | np.isnan(depth)
    assert np.isnan(got[missing]).all() and np.isfinite(got[~missing]).all()
    # every row equals the same hole asked alone, at the depth clipped to the hole
    lengths = t.lengths()
    for r in rng.choice(np.flatnonzero(~missing), 300, replace=False):
        md, xyz, dirs = t.stations(int(hole[r]))
        want = _ref_positions(md, xyz, dirs, [min(max(depth[r], 0.0), lengths[hole[r]])])[0]
        assert np.abs(got[r] - want).max() <= 2e-8
    start, end, mid = t.interval_positions(hole, depth, depth + 2.0)
    assert np.array_equal(start, got, equal_nan=True)
    assert np.array_equal(end, t.positions(hole, depth + 2.0), equal_nan=True)
    assert np.array_equal(mid, t.positions(hole, depth + 1.0), equal_nan=True)
    # other integer types and read-only arrays (Arrow, memory maps) are accepted as they are
    small, frozen = hole.astype(np.int32), depth.copy()
    small.setflags(write=False)
    frozen.setflags(write=False)
    assert np.array_equal(t.positions(small, frozen), got, equal_nan=True)
    for column in (collars.x, collars.td, surveys.hole, surveys.depth):
        column.setflags(write=False)
    again = desurvey(Collars(collars.hole.astype(np.int32), collars.x, collars.y, collars.z, collars.td,
                             collars.azimuth, collars.dip), surveys, n_holes=3005)  # fmt: skip
    assert np.array_equal(again.xyz, t.xyz) and np.array_equal(again.offsets, t.offsets)
    assert t.positions(np.array([], dtype=np.int64), np.array([])).shape == (0, 3)
    with pytest.raises(TypeError, match="integer"):
        t.positions(np.array([0.0, 1.0]), np.array([0.0, 1.0]))
    assert t.positions(7, [0.0, 1.0]).shape == (2, 3)  # one hole, many depths


def test_empty_input() -> None:
    none = np.array([])
    t = desurvey(Collars(np.array([], dtype=np.int64), none, none, none))
    assert t.n_holes == 0 and len(t.md) == 0 and len(t.skipped) == 0 and list(t.offsets) == [0]
    assert np.isnan(t.positions(np.array([0]), np.array([1.0]))).all()
    assert sample_paths(t, 5.0).points.shape == (0, 4)


def test_tick_reports_progress_and_cancels() -> None:
    class Cancelled(Exception):
        pass

    calls: list[tuple[float | None, str | None]] = []

    def tick(fraction: float | None, message: str | None) -> None:
        calls.append((fraction, message))

    def cancel(fraction: float | None, message: str | None) -> None:
        raise Cancelled

    collars, surveys = _random_set(np.random.default_rng(1), 300)
    t = desurvey(collars, surveys, tick=tick)
    assert calls and calls[-1][0] == 1.0 and all(m is None or "Траектории" in m for _, m in calls)
    fractions = [f for f, _ in calls if f is not None]
    assert fractions == sorted(fractions) and 0.0 <= fractions[0] <= fractions[-1] <= 1.0
    calls.clear()
    hole = np.zeros(600_000, dtype=np.int64)
    t.positions(hole, np.zeros(600_000), tick=tick)
    assert len(calls) >= 3  # ticks between portions of rows
    calls.clear()
    sample_paths(t, 5.0, tick=tick)
    assert calls
    with pytest.raises(Cancelled):
        desurvey(collars, surveys, tick=cancel)
    with pytest.raises(Cancelled):
        t.positions(hole, np.zeros(600_000), tick=cancel)
    with pytest.raises(Cancelled):
        t.interval_positions(hole, np.zeros(600_000), np.ones(600_000), tick=cancel)
    with pytest.raises(Cancelled):
        sample_paths(t, 5.0, tick=cancel)


def test_sample_paths_for_many_holes() -> None:
    collars, surveys = _random_set(np.random.default_rng(2), 500)
    for method in METHODS:
        t = desurvey(collars, surveys, method=method)
        paths = sample_paths(t, 7.5)
        assert list(paths.holes) == list(range(500)) and paths.offsets[-1] == len(paths.points)
        lengths = t.lengths()
        for h in range(500):
            p = paths.points[paths.offsets[h] : paths.offsets[h + 1]]
            md = t.stations(h)[0]
            if len(md) == 0:
                assert len(p) == 0
                continue
            assert p[0, 3] == 0.0 and p[-1, 3] == lengths[h] and set(md) <= set(p[:, 3])
            assert len(p) == 1 or (np.diff(p[:, 3]).min() > 0 and np.diff(p[:, 3]).max() <= 7.5 + 1e-9)
            assert np.allclose(p[:, :3], _pos(t, p[:, 3], h), rtol=0, atol=1e-9)
        some = sample_paths(t, 7.5, holes=np.array([17, 3, 17]))
        assert list(some.holes) == [17, 3, 17]
        assert np.array_equal(some.points[: some.offsets[1]], paths.points[paths.offsets[17] : paths.offsets[18]])
        assert np.array_equal(some.points[some.offsets[1] : some.offsets[2]],
                              paths.points[paths.offsets[3] : paths.offsets[4]])  # fmt: skip
    # the corner in the middle of a balanced-tangent course is a point of the path
    corner = sample_paths(_hole(COURSE, td=0.0, method="balanced_tangent"), 40.0).points[:, 3]
    assert list(corner) == [0.0, 40.0, 47.5, 80.0, 95.0]
    with pytest.raises(ValueError, match="step"):
        sample_paths(t, 0.0)
    with pytest.raises(ValueError, match="points"):
        sample_paths(t, 1e-9)
    with pytest.raises(ValueError, match="points"):
        sample_paths(t, 7.5, max_points=100)


# --- Micromine sample -------------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("MICROMINE_SAMPLE_DIR"),
    reason="set MICROMINE_SAMPLE_DIR to compare with Micromine's own coordinates",
)
def test_trajectories_match_micromine_sample_coordinates() -> None:
    """The sample Assay file carries X/Y/Z computed by Micromine for the middle of each interval (ТЗ 7.11)."""
    from bf_core.formats.micromine import read_dat

    folder = Path(os.environ["MICROMINE_SAMPLE_DIR"])

    def read(token: str):  # type: ignore[no-untyped-def]
        matches = [p for p in folder.iterdir() if token in p.name and p.suffix.upper() == ".DAT"]
        assert len(matches) == 1, f"expected one *{token}*.DAT, found {matches}"
        return read_dat(matches[0])

    collar, survey, assay = read("Collar"), read("Survey"), read("Assay")
    index = {name: i for i, name in enumerate(collar.column("BHID").text())}
    assert len(index) == collar.rows

    def num(table, name: str) -> np.ndarray:  # type: ignore[no-untyped-def]
        return np.asarray(table.column(name).to_numpy(), dtype=np.float64)

    def holes(table) -> np.ndarray:  # type: ignore[no-untyped-def]
        return np.array([index.get(name, -1) for name in table.column("BHID").text()], dtype=np.int64)

    collars = Collars(np.arange(collar.rows), num(collar, "X"), num(collar, "Y"), num(collar, "Z"),
                      num(collar, "TDEPTH"), num(collar, "AZ"), num(collar, "DIP"))  # fmt: skip
    s_hole = holes(survey)
    known = s_hole >= 0
    surveys = Surveys(s_hole[known], num(survey, "DEPTH")[known], num(survey, "BRG_TRUE")[known],
                      num(survey, "DIP")[known])  # fmt: skip
    t = desurvey(collars, surveys)

    want = np.column_stack([num(assay, "X"), num(assay, "Y"), num(assay, "Z")])
    rows = np.isfinite(want).all(axis=1)
    mid = t.interval_positions(holes(assay)[rows], num(assay, "FROM")[rows], num(assay, "TO")[rows])[2]
    error = np.linalg.norm(mid - want[rows], axis=1)
    worst, median = float(np.nanmax(error)), float(np.nanmedian(error))
    print(f"Micromine sample: {int(rows.sum())} intervals, worst {worst:.4f} m, median {median:.4f} m")
    assert not np.isnan(error).any(), "an interval of the sample has no trajectory"
    assert rows.sum() > 20000
    assert worst <= 0.05, f"worst {worst:.4f} m, median {median:.4f} m"  # ТЗ 7.11: не хуже 5 см
