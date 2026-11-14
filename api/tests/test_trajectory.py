import math

import numpy as np
import pytest

from app.geo.trajectory import build_trajectory, sample_path


def _hole(stations, td=100.0, az=None, dip=None, x=1000.0, y=2000.0, z=500.0):
    return build_trajectory("H", x, y, z, td, az, dip, stations)


def test_vertical_hole_goes_straight_down():
    t = _hole([(0.0, 0.0, -90.0)], td=100.0)

    x, y, z = t.positions([0, 50, 100]).T

    assert np.allclose(x, 1000) and np.allclose(y, 2000)
    assert np.allclose(z, [500, 450, 400])


def test_inclined_straight_hole():
    t = _hole([(0.0, 90.0, -45.0)], td=100.0)  # east, 45 degrees down

    x, y, z = t.positions([100]).T

    r = 100 * math.cos(math.radians(45))
    assert x[0] == pytest.approx(1000 + r) and y[0] == pytest.approx(2000, abs=1e-9)
    assert z[0] == pytest.approx(500 - r)


def test_minimum_curvature_is_an_exact_circle_arc():
    radius = 100.0
    t = _hole([(0.0, 90.0, -90.0), (math.pi * radius / 2, 90.0, 0.0)], td=0.0)  # down, turning to east

    end = t.positions([math.pi * radius / 2])[0]
    mid_depth = math.pi * radius / 4  # theta = 45 degrees
    mid = t.positions([mid_depth])[0]

    assert end[0] == pytest.approx(1000 + radius, abs=1e-6) and end[2] == pytest.approx(500 - radius, abs=1e-6)
    assert mid[0] == pytest.approx(1000 + radius * (1 - math.cos(math.pi / 4)), abs=1e-6)
    assert mid[2] == pytest.approx(500 - radius * math.sin(math.pi / 4), abs=1e-6)


def test_positions_are_continuous_at_stations_and_length_is_preserved():
    t = _hole([(0.0, 10.0, -80.0), (30.0, 40.0, -70.0), (60.0, 80.0, -62.0)], td=90.0)

    depths = np.linspace(0, t.length, 400)
    pts = t.positions(depths)
    steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)

    assert np.allclose(t.positions(t.md), t.xyz)
    # the polyline of a smooth path is a bit shorter than the arc, never longer
    assert steps.sum() <= t.length + 1e-6
    assert steps.sum() == pytest.approx(t.length, rel=1e-3)


def test_extends_to_td_with_last_orientation_and_up_to_collar():
    t = _hole([(20.0, 0.0, -90.0), (50.0, 0.0, -90.0)], td=80.0)

    assert t.md[0] == 0.0 and t.md[-1] == 80.0
    assert t.positions([80])[0][2] == pytest.approx(420.0)


def test_falls_back_to_collar_angles_then_to_vertical():
    inclined = _hole([], az=0.0, dip=-60.0, td=100.0)
    vertical = _hole([], td=100.0)

    assert inclined.source == "collar" and inclined.positions([100])[0][1] > 2000
    assert vertical.source == "vertical" and vertical.positions([100])[0][2] == pytest.approx(400.0)


def test_skips_invalid_stations_and_duplicate_depths():
    t = _hole([(0.0, 0.0, -90.0), (10.0, math.nan, -80.0), (20.0, 400.0, -80.0), (30.0, 0.0, -95.0),
               (40.0, 0.0, -90.0), (40.0, 90.0, -10.0), (None, 0.0, -90.0)], td=50.0)

    assert t.source == "survey"
    assert list(t.md) == [0.0, 40.0, 50.0]  # only valid stations; first of duplicate depths wins


def test_missing_coordinates_give_no_trajectory():
    assert build_trajectory("H", None, 1.0, 1.0, 10.0, 0.0, -90.0, []) is None
    assert build_trajectory("H", 1.0, math.nan, 1.0, 10.0, 0.0, -90.0, []) is None


def test_sample_path_contains_stations_and_respects_step():
    t = _hole([(0.0, 0.0, -90.0), (35.0, 0.0, -90.0)], td=100.0)

    path = sample_path(t, 10.0)

    assert path.shape[1] == 4
    assert {35.0, 100.0, 0.0} <= set(path[:, 3])
    assert np.diff(path[:, 3]).max() <= 10.0 + 1e-9


def test_zero_length_hole_does_not_crash():
    t = _hole([], td=0.0)

    assert sample_path(t, 10.0).shape == (1, 4)
    assert t.positions([0.0, 5.0]).shape == (2, 3)


@pytest.mark.skipif(
    not __import__("os").environ.get("MICROMINE_SAMPLE_DIR"),
    reason="set MICROMINE_SAMPLE_DIR to compare with Micromine's own coordinates",
)
def test_trajectories_match_micromine_sample_coordinates():
    """The sample Assay file carries X/Y/Z computed by Micromine for the middle of each interval."""
    import os
    from pathlib import Path

    from app.formats.micromine import read_micromine

    folder = Path(os.environ["MICROMINE_SAMPLE_DIR"])
    find = lambda token: next(p for p in folder.iterdir() if token in p.name and p.suffix.upper() == ".DAT")  # noqa: E731
    collar = read_micromine(find("Collar")).frame.set_index("BHID")
    survey = read_micromine(find("Survey")).frame
    assay = read_micromine(find("Assay")).frame.dropna(subset=["X", "Y", "Z"])

    stations = {h: list(zip(g["DEPTH"], g["BRG_TRUE"], g["DIP"])) for h, g in survey.groupby("BHID")}
    worst = 0.0
    for hole, g in assay.groupby("BHID"):
        c = collar.loc[hole]
        traj = build_trajectory(hole, c["X"], c["Y"], c["Z"], c["TDEPTH"], c["AZ"], c["DIP"], stations.get(hole, []))
        mid = (g["FROM"] + g["TO"]).to_numpy() / 2
        error = np.linalg.norm(traj.positions(mid) - g[["X", "Y", "Z"]].to_numpy(), axis=1)
        worst = max(worst, float(error.max()))

    assert worst < 0.1  # metres; observed 0.05 over 21 145 intervals
