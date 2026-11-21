"""The frame of the swath cuboid and the layout of the swaths (ТЗ 7.30, 7.35 Д-01; ADR 0019 §5; Э13, 13.9):
orthonormal axes (1e−15), X, Y, Z as frames of angles and an azimuth of 90° as X bit for bit (ТД01-5), axes ↔
angles and the number of a swath by the projection (1e−9, ТД01-2), the default width (Т7.30-4), the anchor and
the limit of 2000 swaths (Т7.30-1), the tests of BF (`test_frames…`, `test_layout_and_default_width`).

The shared answers `tests/data/swath/frames.json` are read here and by vitest (`ui/test/swath-frame.test.ts`).
They are computed by the core: the tests check the core against hand-made expectations and the file against
the core. Rewrite the file after a deliberate change:
`BF_REGEN_SWATH_CASES=1 uv run pytest -q tests/test_swath_frame.py`.
"""

from __future__ import annotations

import itertools
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.stats import swath_frame as sf
from bf_core.stats.weighted import StatsError

DATA = Path(__file__).parent / "data" / "swath" / "frames.json"
REGEN = os.environ.get("BF_REGEN_SWATH_CASES") == "1"
TOL = {"axes": 1e-12, "angles": 1e-9, "t": 1e-9}

AZIMUTHS = [-720.0, -90.0, -37.5, 0.0, 12.5, 30.0, 45.0, 90.0, 120.0, 135.0, 180.0, 200.0, 270.0, 300.0, 359.5, 450.0]
DIPS = [-90.0, -89.999, -60.0, -35.0, -15.25, 0.0, 15.25, 30.0, 45.0, 60.0, 89.999, 90.0]
ROLLS = [-180.0, -179.0, -110.0, -90.0, -45.0, -20.0, 0.0, 20.0, 45.0, 90.0, 110.0, 179.0, 180.0, 200.0, 540.0]
SWEEP = list(itertools.product(AZIMUTHS, DIPS, ROLLS))


def circle(a: float, b: float) -> float:
    """The difference of two angles on the circle, degrees."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def bits(a: Any, b: Any) -> bool:
    """Bit for bit, a −0.0 is not a 0.0."""
    x, y = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return x.shape == y.shape and x.tobytes() == y.tobytes()


# --- the frame -------------------------------------------------------------------------------------------------


def test_axes_are_orthonormal_for_any_angles() -> None:
    worst = 0.0
    for a, d, r in SWEEP:
        f = sf.frame(a, d, r)
        m = f.axes
        worst = max(worst, float(np.abs(m @ m.T - np.eye(3)).max()), float(np.abs(np.cross(f.w, f.v) - f.u).max()))
        assert 0.0 <= f.azimuth < 360.0 and -90.0 <= f.dip <= 90.0 and -180.0 < f.roll <= 180.0
    assert worst <= 1e-15


def test_x_y_z_are_frames_of_angles_and_azimuth_90_is_x_bit_for_bit() -> None:
    """ТД01-5: sin and cos of the multiples of 90° are exact — u of the azimuth 90° is (1, 0, 0)."""
    x = sf.frame(90.0)
    assert bits(x.u, [1.0, 0.0, 0.0]) and bits(x.v, [0.0, -1.0, 0.0]) and bits(x.w, [0.0, 0.0, 1.0])
    for other in (sf.direction_frame("x"), sf.direction_frame("angles", 90.0), sf.frame(450.0), sf.frame(-270.0)):
        assert bits(other.axes, x.axes) and (other.azimuth, other.dip, other.roll) == (90.0, 0.0, 0.0)
    y = sf.direction_frame("y")
    assert bits(y.axes, [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]) and bits(y.axes, sf.frame(360.0).axes)
    z = sf.direction_frame("z")
    assert bits(z.axes, [[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, -1.0, 0.0]]) and z.dip == 90.0
    for a in (0.0, 30.0, 123.4, 270.0):  # Z turned in plan: the axis stays exactly vertical
        f = sf.direction_frame("z", a, dip=-5.0, roll=77.0)  # the dip and the roll of the settings are not used
        assert bits(f.u, [0.0, 0.0, 1.0]) and bits(f.axes, sf.frame(a, 90.0).axes)
        sa, ca = math.sin(math.radians(a)), math.cos(math.radians(a))
        assert np.allclose(f.v, [ca, -sa, 0.0], atol=1e-15) and np.allclose(f.w, [-sa, -ca, 0.0], atol=1e-15)
    # The other multiples of 90°: every component is 0 or ±1, and no −0.0 is written.
    for a, d, r in itertools.product((0.0, 90.0, 180.0, 270.0), (-90.0, 0.0, 90.0), (-90.0, 0.0, 90.0, 180.0)):
        m = sf.frame(a, d, r).axes
        assert set(np.abs(m).ravel().tolist()) <= {0.0, 1.0} and not np.signbit(m[m == 0.0]).any()
        assert np.array_equal(m @ m.T, np.eye(3))
    # BF `test_frames_are_orthonormal_and_azimuth_matches_the_axes`.
    assert np.allclose(sf.frame(45.0).u, [2**-0.5, 2**-0.5, 0.0], atol=1e-15)
    assert np.allclose(sf.frame(37.5).w, [0.0, 0.0, 1.0]) and np.allclose(sf.frame(30.0, 90.0).w, -sf.frame(30.0).u)
    with pytest.raises(StatsError) as e:
        sf.direction_frame("down")
    assert e.value.code == "bad_param" and e.value.param == "direction" and "down" in e.value.message


def test_the_dip_goes_down_when_negative_and_the_roll_turns_about_the_axis() -> None:
    f = sf.frame(0.0, -30.0)
    assert np.allclose(f.u, [0.0, math.cos(math.radians(30)), -0.5], atol=1e-15)  # north and down
    assert np.allclose(f.v, [1.0, 0.0, 0.0]) and np.allclose(f.w, [0.0, 0.5, math.cos(math.radians(30))], atol=1e-15)
    g = sf.frame(0.0, -30.0, 90.0)  # a quarter turn about u: v takes the place of w
    assert np.allclose(g.u, f.u, atol=1e-15) and np.allclose(g.v, f.w, atol=1e-15)
    assert np.allclose(g.w, -f.v, atol=1e-15)
    assert sf.normalize_angles(-90.0, 0.0, 540.0) == (270.0, 0.0, 180.0)
    assert sf.normalize_angles(725.0, -90.0, -180.0) == (5.0, -90.0, 180.0)
    assert sf.normalize_angles("12.5", 1, 200) == (12.5, 1.0, -160.0)
    for bad, param in (((0.0, 90.5, 0.0), "dip"), ((math.nan, 0.0, 0.0), "azimuth"), ((0.0, 0.0, math.inf), "roll")):
        with pytest.raises(StatsError) as e:
            sf.frame(*bad)
        assert e.value.code == "bad_param" and e.value.param == param and e.value.hint
    with pytest.raises(StatsError):
        sf.frame(None)


def test_angles_give_the_axes_back_and_the_axes_the_angles() -> None:
    """ТД01-2: the rotation matrix ↔ the angles, 1e−9; a vertical axis keeps the turn as the azimuth."""
    for a, d, r in SWEEP:
        f = sf.frame(a, d, r)
        back = sf.angles_of(f.u, f.v)
        assert 0.0 <= back[0] < 360.0 and -180.0 < back[2] <= 180.0
        g = sf.frame(*back)
        assert float(np.abs(g.axes - f.axes).max()) <= 1e-9, (a, d, r)
        if abs(d) < 90.0:
            assert circle(back[0], f.azimuth) <= 1e-9 and abs(back[1] - f.dip) <= 1e-9, (a, d, r)
            assert circle(back[2], f.roll) <= 1e-9, (a, d, r)
        else:  # the azimuth and the roll are one turn: A + R looking up, A − R looking down
            turn = f.azimuth + f.roll if d > 0 else f.azimuth - f.roll
            assert back[1] == d and back[2] == 0.0 and circle(back[0], turn) <= 1e-9, (a, d, r)
    # Exact at the multiples of 90°, the length of u does not matter, a zero axis is refused.
    assert sf.angles_of([1.0, 0.0, 0.0], [0.0, -1.0, 0.0]) == (90.0, 0.0, 0.0)
    assert sf.angles_of([0.0, 5.0, 0.0], [-1.0, 0.0, 0.0]) == (0.0, 0.0, 180.0)
    assert sf.angles_of([0.0, 0.0, -2.0], [0.0, 1.0, 0.0]) == (270.0, -90.0, 0.0)
    assert sf.angles_of([-3.0, 0.0, 0.0], [0.0, 0.0, 1.0]) == (270.0, 0.0, 90.0)
    with pytest.raises(StatsError):
        sf.angles_of([0.0, 0.0, 0.0], [1.0, 0.0, 0.0])
    with pytest.raises(StatsError):
        sf.angles_of([1.0, 0.0], [1.0, 0.0, 0.0])
    # Just outside the vertical zone the azimuth is still read from u.
    f = sf.frame(200.0, 89.999, 10.0)
    assert sf.angles_of(f.u, f.v) == pytest.approx((200.0, 89.999, 10.0), abs=1e-9)


# --- projections, extents ------------------------------------------------------------------------------------


def test_projection_is_the_coordinate_along_an_axis_and_skips_what_the_axis_does_not_use() -> None:
    rng = np.random.default_rng(13)
    p = rng.uniform(-1e3, 1e3, (500, 3)) + [512_345.678, 7_101_234.5, 250.0]  # UTM-like
    assert bits(sf.project(p, sf.frame(90.0).u), p[:, 0]) and bits(sf.project(p, sf.frame(0.0).u), p[:, 1])
    assert bits(sf.project(p, sf.frame(77.0, 90.0).u), p[:, 2])
    q = p.copy()
    q[::7, 2] = np.nan  # an empty Z does not spoil a horizontal axis …
    assert bits(sf.project(q, sf.frame(90.0).u), p[:, 0])
    assert np.isnan(sf.project(q, sf.frame(90.0, -10.0).u)[::7]).all()  # … and spoils an inclined one
    f = sf.frame(120.0, -35.0, 20.0)
    u = f.u
    assert bits(sf.project(p, u), (0.0 + p[:, 0] * u[0]) + p[:, 1] * u[1] + p[:, 2] * u[2])
    local = sf.to_frame(f, p)
    assert np.allclose(local, p @ f.axes.T, rtol=0, atol=1e-8) and local.shape == (500, 3)
    assert float(np.abs(sf.to_world(f, local) - p).max()) <= 1e-8  # there and back at 7·10⁶ m
    assert sf.project(np.zeros((0, 3)), u).shape == (0,) and sf.project([1.0, 2.0, 3.0], [0.0, 0.0, 2.0]).tolist() == [
        6.0
    ]
    with pytest.raises(ValueError):
        sf.project(np.zeros((4, 2)), u)


def test_bounds_of_points_and_cells_and_the_corners_of_the_cuboid() -> None:
    f = sf.direction_frame("x")
    pts = np.array([[10.0, 5.0, -2.0], [30.0, 1.0, 4.0], [20.0, 9.0, 0.0], [np.nan, 0.0, 0.0], [0.0, 0.0, np.nan]])
    assert sf.bounds(f, pts).tolist() == [[10.0, 30.0], [-9.0, -1.0], [-2.0, 4.0]]  # v of X looks south
    assert sf.bounds(f, pts, [10.0, 2.0, 4.0]).tolist() == [[5.0, 35.0], [-10.0, 0.0], [-4.0, 6.0]]
    sizes = np.array([[2.0, 2.0, 2.0], [4.0, 2.0, 2.0], [np.nan, 1.0, 1.0], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]])
    assert sf.bounds(f, pts, sizes).tolist() == [[9.0, 32.0], [-6.0, 0.0], [-3.0, 5.0]]
    assert np.isnan(sf.bounds(f, pts[3:])).all() and np.isnan(sf.bounds(f, np.zeros((0, 3)))).all()
    # A cell seen from a turned frame: half of its extent is Σ |e_i|·size_i / 2.
    g = sf.frame(45.0)
    half = sf.cell_half(g, [10.0, 10.0, 4.0])
    assert half == pytest.approx([10.0 * 2**-0.5, 10.0 * 2**-0.5, 2.0], abs=1e-12)
    assert sf.bounds(g, [[0.0, 0.0, 0.0]], [10.0, 10.0, 4.0])[0] == pytest.approx([-half[0], half[0]])
    rng = np.random.default_rng(5)
    h = sf.frame(300.0, 60.0, -110.0)
    c, s = rng.uniform(-50, 50, (200, 3)), rng.uniform(1, 9, (200, 3))
    signs = np.array(list(itertools.product((-0.5, 0.5), repeat=3)))
    every = (c[:, None, :] + signs[None] * s[:, None, :]).reshape(-1, 3)  # the 8 corners of every cell
    assert np.allclose(sf.bounds(h, c, s), sf.bounds(h, every), atol=1e-12)
    lim = np.array([[100.0, 160.0], [-20.0, 20.0], [0.0, 30.0]])
    cs = sf.corners(h, lim)
    assert cs.shape == (8, 3) and np.allclose(sf.bounds(h, cs), lim, atol=1e-12)
    assert np.allclose(sf.to_frame(h, cs[5]), [[160.0, -20.0, 30.0]], atol=1e-12)  # 5 = u max, v min, w max
    assert sf.corners(f, lim)[0].tolist() == [100.0, 20.0, 0.0] and sf.corners(f, lim)[7].tolist() == [
        160.0,
        -20.0,
        30.0,
    ]


# --- the width and the swaths -----------------------------------------------------------------------------------


def test_layout_and_default_width() -> None:
    """BF `test_layout_and_default_width`, + the errors as {code, message, hint}."""
    assert sf.layout(100.0, 200.0, 10.0) == (100.0, 10)
    assert sf.layout(100.0, 201.0, 10.0) == (100.0, 11)
    # A boundary passes through the anchor: the first swath starts at or below the data.
    assert sf.layout(103.0, 200.0, 10.0, anchor=0.0) == (100.0, 10)
    assert sf.layout(103.0, 200.0, 10.0, anchor=1005.0) == (95.0, 11)
    assert sf.layout(5.0, 5.0, 2.0) == (5.0, 1) and sf.layout(7.0, 5.0, 2.0) == (7.0, 1)  # a flat extent: one swath
    assert sf.layout(0.0, 30.0 + 1e-12, 10.0) == (0.0, 3)  # the tolerance: no fourth swath for a rounding error
    assert sf.default_width(100.0, 5.0) == 5.0  # 20 swaths of one block
    assert sf.default_width(1000.0, 5.0) == 20.0  # 4 blocks: 50 swaths
    assert sf.default_width(1000.0, None) == 50.0  # no model: a round number, about 1/25 of the extent
    assert sf.default_width(0.0, 5.0) == 5.0 and sf.default_width(0.0) == 1.0 and sf.default_width(10.0, 0.0) == 0.5
    assert sf.nice(37.0) == 50.0 and sf.nice(0.21) == 0.25 and sf.nice(1.0) == 1.0 and sf.nice(2.2) == 2.5
    assert sf.nice(-3.0) == 1.0 and sf.nice(math.inf) == 1.0 and sf.nice(8e-7) == pytest.approx(1e-6)
    for width in (0.0, -1.0, math.nan, math.inf, "x", None):
        with pytest.raises(StatsError) as e:
            sf.layout(0.0, 10.0, width)  # type: ignore[arg-type]
        assert e.value.code == "bad_param" and e.value.param == "width" and "больше нуля" in e.value.message
    with pytest.raises(StatsError) as e:
        sf.layout(0.0, 10.0, 1.0, anchor=math.nan)
    assert e.value.param == "anchor"
    with pytest.raises(StatsError) as e:
        sf.layout(math.nan, 10.0, 1.0)
    assert e.value.code == "bad_param" and e.value.param == "limits"
    assert sf.centres(100.0, 10.0, 3).tolist() == [105.0, 115.0, 125.0]


def test_more_than_2000_swaths_are_refused() -> None:
    """Т7.30-1: a width that gives 2000+ swaths is refused — `too_many_swaths` with the hint to widen."""
    assert sf.MAX_SWATHS == 2000 and sf.layout(0.0, 2000.0, 1.0) == (0.0, 2000)
    assert sf.layout(0.5, 2000.0, 1.0, anchor=0.0) == (0.0, 2000)  # the anchor moves the start, not the count
    for lo, hi, width, anchor in ((0.0, 2000.5, 1.0, None), (0.5, 2000.5, 1.0, 0.0), (0.0, 1e6, 1.0, None),
                                  (0.0, 1e300, 1e-300, None), (-1e15, 1e15, 0.001, 0.5)):  # fmt: skip
        with pytest.raises(StatsError) as e:
            sf.layout(lo, hi, width, anchor)
        err = e.value
        assert err.code == "too_many_swaths" and err.param == "width" and "2000" in err.message
        assert "Увеличьте ширину" in err.hint and set(err.as_dict()) == {"code", "message", "hint"}


def test_default_width_without_a_model_is_round_and_with_one_whole_blocks() -> None:
    """Т7.30-4: no model — a round number near 1/25 of the extent; a model — whole parent blocks, ≤ 50 swaths."""
    rng = np.random.default_rng(730)
    for span in [1234.0, 25.0, 0.37, 99999.0, *rng.uniform(0.01, 1e6, 300).tolist()]:
        width = sf.default_width(span)
        mantissa = width / 10.0 ** math.floor(math.log10(width) + 1e-12)
        assert min(abs(mantissa - m) for m in (1.0, 2.0, 2.5, 5.0, 10.0)) < 1e-9, (span, width)
        assert span / 25.0 * (1 - 1e-9) <= width < 2.0 * span / 25.0 * (1 + 1e-9)
        assert sf.layout(1000.0, 1000.0 + span, width)[1] <= 25
    for span, block in [(100.0, 5.0), (1000.0, 5.0), (1000.0, 20.0), (1001.0, 20.0), (999.99, 20.0), (3.0, 12.5),
                        *zip(rng.uniform(1.0, 1e5, 300).tolist(), rng.choice([0.5, 2.5, 5, 10, 12.5, 25], 300).tolist(),
                             strict=True)]:  # fmt: skip
        width = sf.default_width(span, block)
        blocks = width / block
        assert blocks == round(blocks) >= 1, (span, block)
        assert sf.layout(-500.0, -500.0 + span, width)[1] <= sf.DEFAULT_SWATHS == 50
        if blocks > 1:  # the fewest blocks: one block less would give more than 50 swaths
            assert sf.layout(-500.0, -500.0 + span, (blocks - 1) * block)[1] > 50
    # An anchor may move the first boundary below the data: `auto_width` keeps the swaths within 50.
    assert sf.default_width(1000.0, 20.0) == 20.0 and sf.layout(7.0, 1007.0, 20.0, anchor=0.0)[1] == 51
    assert sf.auto_width(7.0, 1007.0, 20.0, anchor=0.0) == 40.0 and sf.auto_width(7.0, 1007.0, 20.0) == 20.0
    assert sf.auto_width(0.0, 1000.0, 20.0, anchor=0.0) == 20.0 and sf.auto_width(7.0, 1007.0, None, anchor=0.0) == 50.0
    los, anchors = rng.uniform(-1e4, 1e4, 200).tolist(), rng.uniform(-99, 99, 200).tolist()
    for lo, block, anchor in zip(los, rng.choice([2.5, 5, 10, 25], 200).tolist(), anchors, strict=True):
        hi = lo + float(rng.uniform(1.0, 5e4))
        width = sf.auto_width(lo, hi, block, anchor)
        assert width / block == round(width / block) and sf.layout(lo, hi, width, anchor)[1] <= 50
        assert width == block or sf.layout(lo, hi, width - block, anchor)[1] > 50  # the fewest blocks
    # … and counts as `layout` does: at 7·10⁶ m a hair over 50 blocks is 50 swaths of one block, not 25 of two.
    assert sf.default_width(250.0000025, 5.0) == 10.0 and sf.auto_width(7e6, 7e6 + 250.0000025, 5.0) == 5.0
    assert sf.layout(7e6, 7e6 + 250.0000025, 5.0) == (7e6, 50)


def test_the_block_side_is_taken_along_the_axis_of_the_model_nearest_to_the_swaths() -> None:
    sides = (10.0, 20.0, 5.0)
    assert sf.block_side(sf.direction_frame("x").u, sides) == 10.0
    assert sf.block_side(sf.direction_frame("y").u, sides) == 20.0
    assert sf.block_side(sf.direction_frame("z", 33.0).u, sides) == 5.0
    assert sf.block_side(sf.frame(100.0, -20.0, 45.0).u, sides) == 10.0  # nearer to X than to Y and Z
    assert (
        sf.block_side(sf.frame(200.0, 10.0).u, sides) == 20.0 and sf.block_side(sf.frame(10.0, -70.0).u, sides) == 5.0
    )
    # A diagonal: sin 45° and cos 45° differ in the last bit — still a tie, the larger side.
    assert (
        sf.block_side(sf.frame(45.0).u, sides) == 20.0 and sf.block_side(sf.frame(225.0).u, (20.0, 10.0, 5.0)) == 20.0
    )
    assert sf.block_side(sf.frame(90.0, 45.0).u, (10.0, 20.0, 15.0)) == 15.0
    regular = {"corner": [0, 0, 0], "counts": [4, 3, 2], "size": [5.0, 10.0, 2.5], "order": "F"}
    assert sf.grid_sides(regular) == (5.0, 10.0, 2.5)
    tensors = {"corner": [0, 0, 0], "counts": [5, 2, 1], "order": "F",
               "tensors": {"x": [40.0, 10.0, 10.0, 10.0, 40.0], "y": [5.0, 15.0], "z": [3.0]}}  # fmt: skip
    assert sf.grid_sides(tensors) == (10.0, 10.0, 3.0)  # the median of the steps of a variable axis
    # The default width of the first model along a turned axis: whole blocks of the nearest side.
    f = sf.frame(80.0, -10.0, 30.0)
    width = sf.default_width(1830.0, sf.block_side(f.u, sf.grid_sides(regular)))
    assert width == 40.0 and sf.layout(0.0, 1830.0, width)[1] == 46


def test_swath_number_by_the_projection() -> None:
    """ТД01-2: the swath of a point is ⌊(p·u − start) / width⌋; the far boundary belongs to the last swath."""
    f = sf.frame(120.0, -35.0, 20.0)
    start, width, n = 6_150_000.0, 25.0, 40
    rng = np.random.default_rng(2)
    k = rng.integers(-3, n + 3, 4000)
    local = np.column_stack([start + (k + rng.uniform(0.01, 0.99, 4000)) * width, rng.uniform(-300, 300, (4000, 2))])
    got = sf.swath_index(sf.project(sf.to_world(f, local), f.u), start, width, n)
    assert got.dtype == np.int64 and (got == np.where((k >= 0) & (k < n), k, -1)).all()
    assert (got >= 0).sum() > 3000 and (got < 0).sum() > 100
    # The ends: the far boundary is in the last swath, a rounding error below the start is in the first.
    t = np.array([0.0, 9.999999999, 10.0, 99.99, 100.0, 100.0 + 5e-9, 100.0 + 2e-8, -5e-9, -2e-8, 50.0, np.nan,
                  np.inf, -np.inf, 1e300, -1e300])  # fmt: skip
    assert sf.swath_index(t, 0.0, 10.0, 10).tolist() == [0, 0, 1, 9, 9, 9, -1, 0, -1, 5, -1, -1, -1, -1, -1]
    # The tolerance grows with the coordinate and the width: 1e−9·max(1, |start|, width).
    assert sf.swath_index([7e6 + 100.0 + 5e-3, 7e6 + 100.0 + 9e-3, 7e6 - 5e-3], 7e6, 10.0, 10).tolist() == [9, -1, 0]
    assert sf.swath_index([], 0.0, 1.0, 5).shape == (0,) and sf.swath_index(3.5, 0.0, 1.0, 5).tolist() == 3
    # The direct count of BF `test_means_match_a_direct_count`: boundaries inside go to the swath above.
    t = rng.uniform(-5, 105, 2000)
    idx = sf.swath_index(t, 0.0, 10.0, 10)
    for j in range(10):
        assert ((idx == j) == ((t >= 10 * j) & (t < 10 * (j + 1)))).all()
    assert ((idx < 0) == ((t < 0) | (t > 100))).all()


# --- the shared answers -----------------------------------------------------------------------------------------

RULES = [
    "Angles in degrees: azimuth A of the swath axis clockwise from north, dip D (negative - the axis goes down),"
    " roll R about the axis. Normalised: A = ((A % 360) + 360) % 360 in [0, 360); D in [-90, 90] (beyond - refused);"
    " R = ((R % 360) + 360) % 360, minus 360 when above 180 - in (-180, 180].",
    "sin/cos of degrees d: d reduced to [0, 360) as above; exact (0, 1), (1, 0), (0, -1), (-1, 0) at 0, 90, 180,"
    " 270; otherwise sin and cos of d*(PI/180) (sinCosDegCore of viz/section/plane.ts). -0 is written 0.",
    "Axes: u = (cD*sA, cD*cA, sD) across the swaths; n = (-sD*sA, -sD*cA, cD); v = (cR*cA + sR*n.x, -cR*sA + sR*n.y,"
    " sR*n.z); w = (-sR*cA + cR*n.x, sR*sA + cR*n.y, cR*n.z); u = w x v. Directions: x = (90, 0, 0), y = (0, 0, 0),"
    " z = (A, 90, 0) - the azimuth turns the swaths in plan, dip and roll of the settings are not used.",
    "Back (axes -> angles), u of unit length: rho = hypot(u.x, u.y); D = atan2d(u.z, rho); if |u.z| < 1 - 1e-12:"
    " A = atan2d(u.x, u.y), R = atan2d(v.z*rho*rho - u.z*(v.x*u.x + v.y*u.y), v.x*u.y - v.y*u.x); otherwise the axis"
    " is vertical: R = 0, A = atan2d(-v.y, v.x). A below 0 gets +360; R = -180 is written 180.",
    "atan2d(y, x): y == 0 -> 0 for x >= 0, else 180; x == 0 -> 90 for y > 0, else -90; otherwise atan2(y, x)*(180/PI).",
    "back: the angles of the axes of the case. They give the same axes (tolerance.axes); they equal `angles` when"
    " vertical is false (azimuth and roll compared on the circle), and hold the whole turn in the azimuth when true.",
    "Projection t = p.axis: 0.0 + the terms x, y, z in this order, a term with a zero component of the axis"
    " skipped. local = (p.u, p.v, p.w).",
    "Swath number of a point: tol = 1e-9*max(1, |start|, width); q = floor((t - start)/width), t = local[0];"
    " q == count and t <= start + count*width + tol -> count - 1; q == -1 and t >= start - tol -> 0; q outside"
    " 0..count-1 or t not finite -> -1 (k: -1).",
    "Cuboid: limits are (min, max) along u, v, w; corner c (0..7) takes the max along u when c & 4, along v when"
    " c & 2, along w when c & 1; a corner = lu*u + lv*v + lw*w per component, in this order, zero components of"
    " the axes skipped; centre - the same of the middles of the limits.",
    "Compare: cases with exact = true - bit for bit (-0 equals 0); otherwise axes and corners |a - b| <="
    " tolerance.axes*max(1, |b|), angles on the circle within tolerance.angles degrees, local within"
    " tolerance.t*max(1, |b|); k - always exactly.",
]

# id, title, direction, the angles as given (azimuth, dip, roll).
FRAMES: list[tuple[str, str, str, tuple[float, float, float]]] = [
    ("x", "Направление X = (90; 0; 0)", "x", (0.0, 0.0, 0.0)),
    ("y", "Направление Y = (0; 0; 0)", "y", (0.0, 0.0, 0.0)),
    ("z", "Направление Z = (0; +90; 0): полосы снизу вверх", "z", (0.0, 0.0, 0.0)),
    ("z_az30", "Направление Z, повёрнутое в плане: (30; +90; 0)", "z", (30.0, -5.0, 77.0)),
    ("td01_5_azimuth_90", "ТД01-5: азимут 90° — u = (1, 0, 0) точно, как X", "angles", (90.0, 0.0, 0.0)),
    ("azimuth_0", "Азимут 0° — как Y", "angles", (0.0, 0.0, 0.0)),
    ("azimuth_180", "Азимут 180°", "angles", (180.0, 0.0, 0.0)),
    ("azimuth_270", "Азимут 270°", "angles", (270.0, 0.0, 0.0)),
    ("azimuth_450", "Азимут 450° приводится к 90°", "angles", (450.0, 0.0, 0.0)),
    ("azimuth_minus_90", "Азимут −90° приводится к 270°", "angles", (-90.0, 0.0, 0.0)),
    ("roll_90", "Поворот 90° вокруг оси полос", "angles", (0.0, 0.0, 90.0)),
    ("roll_180", "Поворот 180°", "angles", (90.0, 0.0, 180.0)),
    ("roll_minus_180", "Поворот −180° приводится к 180°", "angles", (0.0, 0.0, -180.0)),
    ("roll_270", "Поворот 270° приводится к −90°", "angles", (180.0, 0.0, 270.0)),
    ("dip_90", "Наклон +90°: ось полос вверх", "angles", (0.0, 90.0, 0.0)),
    ("dip_minus_90", "Наклон −90°: ось полос вниз", "angles", (90.0, -90.0, 0.0)),
    ("dip_90_roll_90", "Ось вверх, поворот 90°: обратно — азимут 270°, поворот 0", "angles", (180.0, 90.0, 90.0)),
    ("dip_minus_90_roll_90", "Ось вниз, поворот 90°: обратно — азимут 90°, поворот 0", "angles", (180.0, -90.0, 90.0)),
    ("azimuth_45", "Азимут 45°: диагональ", "angles", (45.0, 0.0, 0.0)),
    ("azimuth_37_5", "Азимут 37,5°", "angles", (37.5, 0.0, 0.0)),
    ("dip_only", "Наклон −30° на север", "angles", (0.0, -30.0, 0.0)),
    ("roll_only", "Поворот 20° при азимуте 90°", "angles", (90.0, 0.0, 20.0)),
    ("general", "Три угла: (120; −35; 20)", "angles", (120.0, -35.0, 20.0)),
    ("general_up", "Три угла: (300; 60; −110)", "angles", (300.0, 60.0, -110.0)),
    ("general_fine", "Три угла: (359,5; 15,25; 179)", "angles", (359.5, 15.25, 179.0)),
    ("wrapped", "Углы вне диапазона: (−400; −45; 540) → (320; −45; 180)", "angles", (-400.0, -45.0, 540.0)),
    ("near_vertical", "Почти вертикально (89,999°): азимут ещё читается по оси", "angles", (200.0, 89.999, 10.0)),
    ("vertical_roll", "Ось вверх с поворотом 40°: обратно — азимут 70°, поворот 0", "angles", (30.0, 90.0, 40.0)),
    ("vertical_down_roll", "Ось вниз с поворотом 40°: обратно — азимут 350°, поворот 0", "angles", (30.0, -90.0, 40.0)),
]

# id, title, frame, start, width, count and where the points are along v and w.
SWATHS: list[tuple[str, str, str, float, float, int, tuple[float, float]]] = [
    ("x_edges", "По X: границы полос и концы — точно", "x", 512_000.0, 25.0, 8, (-7_101_000.0, 250.0)),
    (
        "azimuth_90_edges",
        "ТД01-5: азимут 90° — те же номера, что по X",
        "td01_5_azimuth_90",
        512_000.0,
        25.0,
        8,
        (-7_101_000.0, 250.0),
    ),  # fmt: skip
    ("z_benches", "По Z: уступы 5 м от отметки −100", "z", -100.0, 5.0, 12, (512_100.0, -7_101_100.0)),
    ("azimuth_270", "Азимут 270°: t = −x", "azimuth_270", -512_200.0, 10.0, 20, (7_101_050.0, 250.0)),
    (
        "general",
        "Три угла (120; −35; 20), координаты UTM",
        "general",
        -3_114_000.0,
        20.0,
        30,
        (5_050_000.0, 4_030_000.0),
    ),
    ("general_up", "Три угла (300; 60; −110), ширина 12,5", "general_up", 2_500.0, 12.5, 16, (0.0, 0.0)),
    ("vertical_roll", "Ось вверх с поворотом: t = z", "vertical_roll", 0.0, 2.5, 10, (120.0, -40.0)),
]
INSIDE = (-1.5, -0.25, 0.1, 0.5, 0.9, 1.5, 2.25, 3.75)  # in widths from the start; + count − these from the end
EDGES = (0.0, 1.0, 2.0, 3.0)  # boundaries (exact frames only): the swath above; the far end — the last swath

BOXES: list[tuple[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]] = [
    ("x", ((512_000.0, 512_200.0), (-7_101_150.0, -7_101_000.0), (200.0, 300.0))),
    ("z_az30", ((-100.0, -40.0), (1_000.0, 1_400.0), (-2_300.0, -2_000.0))),
    ("general", ((-3_114_000.0, -3_113_400.0), (5_049_800.0, 5_050_200.0), (4_029_900.0, 4_030_100.0))),
    ("vertical_down_roll", ((-50.0, 0.0), (-10.0, 10.0), (-20.0, 20.0))),
]


def _angles(t: tuple[float, float, float]) -> dict[str, float]:
    return {"azimuth": float(t[0]), "dip": float(t[1]), "roll": float(t[2])}


def _frame(direction: str, given: tuple[float, float, float]) -> sf.Frame:
    return sf.direction_frame(direction, *given)


def _exact(f: sf.Frame) -> bool:
    return all(x % 90.0 == 0.0 for x in (f.azimuth, f.dip, f.roll))


def build() -> dict[str, Any]:
    """The answers as the core computes them."""
    frames, by_id = [], {}
    for fid, title, direction, given in FRAMES:
        f = _frame(direction, given)
        by_id[fid] = f
        d = f.as_dict()
        frames.append(
            {
                "id": fid,
                "title": title,
                "exact": _exact(f),
                "direction": direction,
                "given": _angles(given),
                "angles": {k: d[k] for k in ("azimuth", "dip", "roll")},
                "axes": {k: d[k] for k in ("u", "v", "w")},
                "vertical": abs(float(f.u[2])) >= sf.VERTICAL,
                "back": _angles(sf.angles_of(f.u, f.v)),
            }
        )
    swaths = []
    for sid, title, fid, start, width, count, (cv, cw) in SWATHS:
        f = by_id[fid]
        steps = [*INSIDE, *(count - s for s in INSIDE)]
        if _exact(f):
            steps += [*EDGES, count - 1.0, float(count), count + 1.0]
        local = np.array([[start + s * width, cv + 3.0 * i, cw - 2.0 * i] for i, s in enumerate(steps)])
        if _exact(f):  # a rounding error beyond the two ends is still inside, a larger step is not
            tol = sf.TOL * max(1.0, abs(start), width)
            end = start + count * width
            local = np.vstack(
                [local, [[t, cv, cw] for t in (start - 0.5 * tol, start - 4 * tol, end + 0.5 * tol, end + 4 * tol)]]
            )
        world = sf.to_world(f, local)
        back = sf.to_frame(f, world)
        k = sf.swath_index(back[:, 0], start, width, count)
        points = [{"p": [float(x) for x in p], "local": [float(x) for x in q], "k": int(j)}
                  for p, q, j in zip(world, back, k, strict=True)]  # fmt: skip
        swaths.append({"id": sid, "title": title, "frame": fid, "exact": _exact(f), "start": start, "width": width,
                       "count": count, "points": points})  # fmt: skip
    boxes = []
    for fid, lim in BOXES:
        f = by_id[fid]
        mid = [[0.5 * (a + b) for a, b in lim]]
        limits = {"u": list(lim[0]), "v": list(lim[1]), "w": list(lim[2])}
        centre = [float(x) for x in sf.to_world(f, mid)[0]]
        corners = [[float(x) for x in c] for c in sf.corners(f, lim)]
        boxes.append({"frame": fid, "exact": _exact(f), "limits": limits, "centre": centre, "corners": corners})
    return {
        "about": "Общие эталоны системы короба swath (ADR 0019 §5; ТД01-2, ТД01-5): ядро — bf_core/stats/"
        "swath_frame.py (tests/test_swath_frame.py), окно — ui/src/viz/swath/frame.ts",
        "rules": RULES,
        "tolerance": TOL,
        "frames": frames,
        "swaths": swaths,
        "boxes": boxes,
    }


def _same(a: Any, b: Any, exact: bool, kind: str) -> bool:
    x, y = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if exact:
        return bool((x == y).all())
    if kind == "angles":
        return bool((np.abs((x - y + 180.0) % 360.0 - 180.0) <= TOL["angles"]).all())
    return bool((np.abs(x - y) <= TOL[kind] * np.maximum(1.0, np.abs(y))).all())


@pytest.fixture(scope="module")
def cases() -> dict[str, Any]:
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return json.loads(DATA.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_the_shared_answers_are_what_the_core_computes(cases: dict[str, Any]) -> None:
    """The file against the core: exact cases bit for bit, the others within the tolerances of the file."""
    fresh = build()
    assert cases["rules"] == fresh["rules"] and cases["tolerance"] == TOL == {"axes": 1e-12, "angles": 1e-9, "t": 1e-9}
    assert [c["id"] for c in cases["frames"]] == [c["id"] for c in fresh["frames"]]
    for got, want in zip(cases["frames"], fresh["frames"], strict=True):
        ex = got["exact"]
        assert {k: got[k] for k in ("id", "exact", "direction", "given", "vertical")} == {
            k: want[k] for k in ("id", "exact", "direction", "given", "vertical")
        }
        for name in ("u", "v", "w"):
            assert _same(got["axes"][name], want["axes"][name], ex, "axes"), (got["id"], name)
        for part in ("angles", "back"):
            assert _same(list(got[part].values()), list(want[part].values()), ex, "angles"), (got["id"], part)
    for got, want in zip(cases["swaths"], fresh["swaths"], strict=True):
        assert {k: got[k] for k in ("id", "frame", "exact", "start", "width", "count")} == {
            k: want[k] for k in ("id", "frame", "exact", "start", "width", "count")
        }
        assert [p["k"] for p in got["points"]] == [p["k"] for p in want["points"]], got["id"]
        for a, b in zip(got["points"], want["points"], strict=True):
            assert _same(a["p"], b["p"], got["exact"], "t") and _same(a["local"], b["local"], got["exact"], "t")
    for got, want in zip(cases["boxes"], fresh["boxes"], strict=True):
        assert got["frame"] == want["frame"] and got["limits"] == want["limits"]
        assert _same(got["corners"], want["corners"], got["exact"], "axes")
        assert _same(got["centre"], want["centre"], got["exact"], "axes")


def test_the_shared_answers_hold_the_acceptance_cases(cases: dict[str, Any]) -> None:
    """Hand-made expectations of ТД01-2 and ТД01-5 on the file itself — what vitest reads."""
    frames = {c["id"]: c for c in cases["frames"]}
    x, az90 = frames["x"], frames["td01_5_azimuth_90"]
    assert az90["axes"] == x["axes"] == {"u": [1.0, 0.0, 0.0], "v": [0.0, -1.0, 0.0], "w": [0.0, 0.0, 1.0]}
    assert az90["exact"] and x["angles"] == az90["angles"] == {"azimuth": 90.0, "dip": 0.0, "roll": 0.0}
    assert frames["y"]["axes"]["u"] == [0.0, 1.0, 0.0] and frames["z"]["axes"]["u"] == [0.0, 0.0, 1.0]
    assert frames["z_az30"]["axes"]["u"] == [0.0, 0.0, 1.0] and frames["z_az30"]["angles"]["dip"] == 90.0
    assert frames["azimuth_450"]["axes"] == x["axes"] and frames["azimuth_minus_90"]["angles"]["azimuth"] == 270.0
    assert frames["wrapped"]["angles"] == {"azimuth": 320.0, "dip": -45.0, "roll": 180.0}
    assert not re.search(r"-0\.0\s*[,\]]", DATA.read_text(encoding="utf-8"))  # a −0.0 is written 0.0
    # Axes ↔ angles: the angles read back from the axes give the axes again; a vertical axis keeps the turn.
    for c in cases["frames"]:
        m = np.array([c["axes"][k] for k in ("u", "v", "w")])
        assert float(np.abs(m @ m.T - np.eye(3)).max()) <= 1e-15, c["id"]
        again = sf.frame(**c["back"])
        assert float(np.abs(again.axes - m).max()) <= 1e-9, c["id"]
        if c["vertical"]:
            assert abs(c["angles"]["dip"]) == 90.0 and c["back"]["roll"] == 0.0
        else:
            assert _same(list(c["back"].values()), list(c["angles"].values()), False, "angles"), c["id"]
    assert sum(c["vertical"] for c in cases["frames"]) >= 6 and sum(c["exact"] for c in cases["frames"]) >= 14
    assert frames["vertical_roll"]["back"] == pytest.approx({"azimuth": 70.0, "dip": 90.0, "roll": 0.0}, abs=1e-9)
    assert frames["vertical_down_roll"]["back"] == pytest.approx(
        {"azimuth": 350.0, "dip": -90.0, "roll": 0.0}, abs=1e-9
    )
    assert frames["dip_90_roll_90"]["back"] == {"azimuth": 270.0, "dip": 90.0, "roll": 0.0}
    assert not frames["near_vertical"]["vertical"]
    # The swath of a point: by the projection of the point, 1e−9; X and the azimuth 90° — the same numbers.
    swaths = {c["id"]: c for c in cases["swaths"]}
    assert swaths["x_edges"]["points"] == swaths["azimuth_90_edges"]["points"]
    for c in cases["swaths"]:
        f = sf.frame(**frames[c["frame"]]["angles"])
        p = np.array([q["p"] for q in c["points"]])
        local = np.array([q["local"] for q in c["points"]])
        assert _same(sf.to_frame(f, p), local, c["exact"], "t"), c["id"]
        k = [q["k"] for q in c["points"]]
        assert sf.swath_index(local[:, 0], c["start"], c["width"], c["count"]).tolist() == k, c["id"]
        assert -1 in k and 0 in k and c["count"] - 1 in k
        if not c["exact"]:  # no point of a turned frame sits where a rounding error could move it to a neighbour
            frac = (local[:, 0] - c["start"]) / c["width"]
            assert float(np.abs(frac - np.round(frac)).min()) > 0.05
    on_x = swaths["x_edges"]
    start, width, n = on_x["start"], on_x["width"], on_x["count"]
    by_t = {q["local"][0]: q["k"] for q in on_x["points"]}
    assert all(q["local"][0] == q["p"][0] for q in on_x["points"])  # along X the projection is the coordinate
    assert by_t[start] == 0 and by_t[start + width] == 1 and by_t[start + 3 * width] == 3
    assert by_t[start + n * width] == n - 1 and by_t[start + (n + 1) * width] == -1 and by_t[start - 1.5 * width] == -1
    tol = 1e-9 * start
    assert by_t[start - 0.5 * tol] == 0 and by_t[start - 4 * tol] == -1
    assert by_t[start + n * width + 0.5 * tol] == n - 1 and by_t[start + n * width + 4 * tol] == -1
    assert all(q["local"][0] == q["p"][2] for q in swaths["z_benches"]["points"])
    assert all(q["local"][0] == -q["p"][0] for q in swaths["azimuth_270"]["points"])
    # The cuboid: the corners in the order of the rules lie on the limits.
    for b in cases["boxes"]:
        f = sf.frame(**frames[b["frame"]]["angles"])
        lim = np.array([b["limits"][k] for k in ("u", "v", "w")])
        local = sf.to_frame(f, b["corners"])
        want = [[lim[0, (c >> 2) & 1], lim[1, (c >> 1) & 1], lim[2, c & 1]] for c in range(8)]
        assert np.allclose(local, want, rtol=1e-12, atol=1e-8) and np.allclose(
            np.mean(b["corners"], axis=0), b["centre"]
        )
    assert cases["boxes"][0]["corners"][0] == [512_000.0, 7_101_150.0, 200.0]
