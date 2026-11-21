"""The rotation of the anisotropy ellipsoid: the angles of GSLIB ↔ the axes (ТЗ 8.6.3 `setrot`; ADR 0022 §6, §9;
Э16, 16.6). Т8.6-3 (1e−12), the matrix of 8.6.3 written out literally (1e−15), exact axes at the multiples of 90°,
angles → axes → angles (1e−9; a vertical major axis keeps ang1), the direction of a variogram, the matrix A of a
trend and of the ellipsoid, the second notation «dip / dip direction / pitch» against ADR 0021 §2.

The shared answers `tests/data/variography/frames.json` are read here and by vitest (`ui/src/viz/ellipsoid/frame.ts`,
ТД14-1). They are computed by the core: the tests check the core against hand-made expectations and the file against
the core. Rewrite the file after a deliberate change:
`BF_REGEN_VARIO_CASES=1 uv run pytest -q tests/test_geostat_rotation.py`.
"""

from __future__ import annotations

import itertools
import json
import math
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.geostat import rotation as rot
from bf_core.stats.weighted import StatsError

DATA = Path(__file__).parent / "data" / "variography" / "frames.json"
REGEN = os.environ.get("BF_REGEN_VARIO_CASES") == "1"
TOL = {"axes": 1e-12, "angles": 1e-9, "plane": 1e-9, "matrix": 1e-12}

ANG1 = [0.0, 12.5, 30.0, 45.0, 90.0, 120.0, 135.0, 180.0, 200.0, 269.999, 270.0, 300.0, 359.5]
ANG2 = [-90.0, -89.999, -60.0, -30.0, -15.25, 0.0, 15.25, 30.0, 45.0, 89.999, 90.0]
ANG3 = [-179.0, -110.0, -90.0, -45.0, -20.0, 0.0, 20.0, 45.0, 90.0, 110.0, 179.0, 180.0]
SWEEP = list(itertools.product(ANG1, ANG2, ANG3))


def circle(a: float, b: float, period: float = 360.0) -> float:
    """The difference of two angles on the circle, degrees."""
    return abs((a - b + period / 2) % period - period / 2)


def bits(a: Any, b: Any) -> bool:
    """Bit for bit, a −0.0 is not a 0.0."""
    x, y = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return x.shape == y.shape and x.tobytes() == y.tobytes()


def literal(ang1: float, ang2: float, ang3: float) -> np.ndarray:
    """ТЗ 8.6.3 word for word (GSLIB `setrot`), sin and cos of radians."""
    a = math.radians(90.0 - ang1 if 0.0 <= ang1 < 270.0 else 450.0 - ang1)
    b, t = math.radians(-ang2), math.radians(ang3)
    sa, ca, sb, cb, st, ct = math.sin(a), math.cos(a), math.sin(b), math.cos(b), math.sin(t), math.cos(t)
    return np.array(
        [
            [cb * ca, cb * sa, -sb],
            [-ct * sa + st * sb * ca, ct * ca + st * sb * sa, st * cb],
            [st * sa + ct * sb * ca, -st * ca + ct * sb * sa, ct * cb],
        ]
    )


# --- the matrix --------------------------------------------------------------------------------------------------


def test_t8_6_3_major_axis() -> None:
    """Т8.6-3: zero angles — the major axis (0; 1; 0); ang1 = 90°, a plunge of 30° — (0.8660254; 0; −0.5), 1e−12."""
    r = rot.rotation_matrix(0.0, 0.0, 0.0)
    assert bits(r, [[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])  # exact, a right-handed triple
    assert np.array_equal(np.cross(r[0], r[1]), r[2])
    u1 = rot.rotation_matrix(90.0, -30.0, 0.0)[0]  # the plunge p = −ang2
    assert float(np.abs(u1 - [math.sqrt(3.0) / 2.0, 0.0, -0.5]).max()) <= 1e-12
    assert float(np.abs(u1 - [0.8660254, 0.0, -0.5]).max()) <= 5e-8  # the digits of the ТЗ
    assert bits(rot.direction(90.0, 30.0), u1) and u1[1] == 0.0


def test_the_matrix_is_8_6_3_and_orthonormal() -> None:
    worst = orth = 0.0
    for a, b, c in SWEEP:
        r = rot.rotation_matrix(a, b, c)
        worst = max(worst, float(np.abs(r - literal(a, b, c)).max()))
        orth = max(orth, float(np.abs(r @ r.T - np.eye(3)).max()), float(np.abs(np.cross(r[0], r[1]) - r[2]).max()))
    assert worst <= 4e-15 and orth <= 1e-15  # the literal form rounds 90° − ang1 once more
    rng = np.random.default_rng(863)
    for a, b, c in zip(rng.uniform(0, 360, 2000), rng.uniform(-90, 90, 2000), rng.uniform(-180, 180, 2000),
                       strict=True):  # fmt: skip
        r = rot.rotation_matrix(a, b, c)
        assert float(np.abs(r - literal(a, b, c)).max()) <= 4e-15
        assert abs(float(np.linalg.det(r)) - 1.0) <= 4e-15


def test_multiples_of_90_are_exact_and_no_negative_zero() -> None:
    for a, b, c in itertools.product((0.0, 90.0, 180.0, 270.0), (-90.0, 0.0, 90.0), (-90.0, 0.0, 90.0, 180.0)):
        r = rot.rotation_matrix(a, b, c)
        assert set(np.abs(r).ravel().tolist()) <= {0.0, 1.0} and not np.signbit(r[r == 0.0]).any()
        assert np.array_equal(r @ r.T, np.eye(3)) and np.array_equal(np.cross(r[0], r[1]), r[2])
    assert bits(rot.rotation_matrix(90.0), [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])  # the identity
    assert bits(rot.rotation_matrix(0.0, -90.0), [[0.0, 0.0, -1.0], [-1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])  # straight down
    assert bits(rot.rotation_matrix(0.0, 0.0, 90.0), [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]])
    for other in (rot.rotation_matrix(450.0), rot.rotation_matrix(-270.0), rot.rotation_matrix(90.0, 0.0, 360.0)):
        assert bits(other, rot.rotation_matrix(90.0))


def test_the_meaning_of_the_three_angles() -> None:
    """ang1 — the azimuth of the major axis, clockwise from north; ang2 — its dip, negative down (plunge = −ang2);
    ang3 — the turn about the major axis: a positive one lifts the a2 axis."""
    for az in (0.0, 37.0, 123.4, 300.0):
        r = rot.rotation_matrix(az)
        s, c = math.sin(math.radians(az)), math.cos(math.radians(az))
        assert np.allclose(r, [[s, c, 0.0], [-c, s, 0.0], [0.0, 0.0, 1.0]], atol=1e-15)
    r = rot.rotation_matrix(0.0, -30.0)  # north and down by 30°
    assert np.allclose(r[0], [0.0, math.cos(math.radians(30)), -0.5], atol=1e-15) and bits(r[1], [-1.0, 0.0, 0.0])
    f, g = rot.rotation_matrix(40.0, -20.0, 0.0), rot.rotation_matrix(40.0, -20.0, 25.0)
    assert np.allclose(g[0], f[0], atol=1e-15)  # the major axis does not move
    assert g[1][2] > 0.0 and math.isclose(float(g[1] @ f[2]), math.sin(math.radians(25)), abs_tol=1e-15)
    quarter = rot.rotation_matrix(40.0, -20.0, 90.0)
    assert np.allclose(quarter[1], f[2], atol=1e-15) and np.allclose(quarter[2], -f[1], atol=1e-15)


def test_angles_are_normalised_and_refused_out_of_range() -> None:
    assert rot.normalize_angles(-90.0, 0.0, 540.0) == (270.0, 0.0, 180.0)
    assert rot.normalize_angles(725.0, -90.0, -180.0) == (5.0, -90.0, 180.0)
    assert rot.normalize_angles("12.5", 1, 200) == (12.5, 1.0, -160.0)
    assert rot.normalize_angles(360.0) == (0.0, 0.0, 0.0) and rot.normalize_angles(-0.0, -0.0, -0.0) == (0.0, 0.0, 0.0)
    assert not any(math.copysign(1.0, x) < 0 for x in rot.normalize_angles(-0.0, -0.0, -360.0))
    for bad, param in (((0.0, 90.5, 0.0), "ang2"), ((math.nan, 0.0, 0.0), "ang1"), ((0.0, 0.0, math.inf), "ang3"),
                       ((None, 0.0, 0.0), "ang1"), ((0.0, "x", 0.0), "ang2")):  # fmt: skip
        with pytest.raises(StatsError) as e:
            rot.rotation_matrix(*bad)
        assert e.value.code == "bad_param" and e.value.param == param and e.value.hint
        assert set(e.value.as_dict()) == {"code", "message", "hint"}


# --- the way back ------------------------------------------------------------------------------------------------


def test_angles_give_the_axes_and_the_axes_the_angles() -> None:
    """ТД14-1: the angles → the matrix → the angles, 1e−9; a vertical major axis keeps the ang1 it is told."""
    for a, b, c in SWEEP:
        r = rot.rotation_matrix(a, b, c)
        back = rot.angles_of(r, a)
        assert 0.0 <= back[0] < 360.0 and -90.0 <= back[1] <= 90.0 and -180.0 < back[2] <= 180.0
        assert circle(back[0], a) <= 1e-9 and abs(back[1] - b) <= 1e-9 and circle(back[2], c) <= 1e-9, (a, b, c)
        assert float(np.abs(rot.rotation_matrix(*back) - r).max()) <= 1e-12, (a, b, c)
    rng = np.random.default_rng(22)
    worst = 0.0
    for a, b, c in zip(rng.uniform(0, 360, 20000), rng.uniform(-89.9, 89.9, 20000), rng.uniform(-180, 180, 20000),
                       strict=True):  # fmt: skip
        back = rot.angles_of(rot.rotation_matrix(a, b, c))  # no hint is needed away from the vertical
        worst = max(worst, circle(back[0], a), abs(back[1] - b), circle(back[2], c))
    assert worst <= 1e-10
    # Exact at the multiples of 90°; the rows need not be of unit length.
    assert rot.angles_of([[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]) == (0.0, 0.0, 0.0)
    assert rot.angles_of([[2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 5.0]]) == (90.0, 0.0, 0.0)
    assert rot.angles_of(rot.rotation_matrix(270.0, 0.0, 180.0)) == (270.0, 0.0, 180.0)
    assert rot.angles_of(rot.rotation_matrix(180.0, 0.0, -90.0)) == (180.0, 0.0, -90.0)


def test_a_vertical_major_axis_keeps_ang1_and_turns_ang3() -> None:
    """The azimuth and the turn about a vertical axis are one rotation: the window keeps its ang1 (ADR 0022 §6)."""
    for b in (-90.0, 90.0):
        for a, c in itertools.product((0.0, 30.0, 123.0, 270.0, 359.0), (-170.0, -45.0, 0.0, 60.0, 180.0)):
            r = rot.rotation_matrix(a, b, c)
            assert bits(r[0], [0.0, 0.0, 1.0 if b > 0 else -1.0])
            back = rot.angles_of(r, a)
            assert back[0] == a and back[1] == b and circle(back[2], c) <= 1e-9, (a, b, c)
            for hint in (0.0, 77.0, 200.5, -30.0, 725.0):  # another ang1 — another ang3, the same axes
                other = rot.angles_of(r, hint)
                assert other[0] == rot.normalize_angles(hint)[0] and other[1] == b
                assert float(np.abs(rot.rotation_matrix(*other) - r).max()) <= 1e-12
    # Within 1e−12 of the vertical the axis is vertical; just outside the azimuth is read from the axis.
    near = rot.rotation_matrix(200.0, 89.9999999, 10.0)
    assert rot.angles_of(near, 55.0)[0] == 55.0
    for b in (89.999, -89.999):
        assert rot.angles_of(rot.rotation_matrix(200.0, b, 10.0), 55.0) == pytest.approx((200.0, b, 10.0), abs=1e-9)


def test_angles_of_refuses_what_is_not_a_frame() -> None:
    for bad in ([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]], [[1.0, 0.0], [0.0, 1.0]], np.full((3, 3), np.nan),
                "axes", [[1.0, 0.0, 0.0], [0.0, math.inf, 0.0], [0.0, 0.0, 1.0]]):  # fmt: skip
        with pytest.raises(StatsError) as e:
            rot.angles_of(bad)
        assert e.value.code == "bad_param" and e.value.param == "axes" and e.value.hint


# --- directions, the matrices of the ellipsoid ----------------------------------------------------------------------


def test_the_direction_of_a_variogram_is_the_major_axis() -> None:
    """u = (cos p·sin Az, cos p·cos Az, −sin p) (ADR 0022 §3) — the first row of the matrix, bit for bit."""
    rng = np.random.default_rng(3)
    for az, p in [(0.0, 0.0), (90.0, 0.0), (0.0, 90.0), (0.0, -90.0), (33.0, 12.0),
                  *zip(rng.uniform(0, 360, 300), rng.uniform(-90, 90, 300), strict=True)]:  # fmt: skip
        u = rot.direction(az, p)
        assert bits(u, rot.rotation_matrix(az, -p, 0.0)[0])
        sa, ca, sp, cp = (f(math.radians(x)) for x in (az, p) for f in (math.sin, math.cos))
        assert float(np.abs(u - [cp * sa, cp * ca, -sp]).max()) <= 4e-15
    assert bits(rot.direction(0.0), [0.0, 1.0, 0.0]) and bits(rot.direction(90.0), [1.0, 0.0, 0.0])
    assert bits(rot.direction(123.0, 90.0), [0.0, 0.0, -1.0]) and bits(rot.direction(450.0, 0.0), [1.0, 0.0, 0.0])
    for bad, param in (((0.0, 91.0), "plunge"), ((math.nan, 0.0), "azimuth"), ((0.0, None), "plunge")):
        with pytest.raises(StatsError) as e:
            rot.direction(*bad)
        assert e.value.code == "bad_param" and e.value.param == param


def test_the_anisotropy_matrix_and_the_matrix_of_the_ellipsoid() -> None:
    """ТЗ 8.6.3: h_r = ‖diag(1, a1/a2, a1/a3)·R·Δ‖ / a1 — the same as √Σ (u_i·Δ / a_i)² (ADR 0022 §6, §10)."""
    rng = np.random.default_rng(15)
    for _ in range(200):
        ang = (float(rng.uniform(0, 360)), float(rng.uniform(-90, 90)), float(rng.uniform(-180, 180)))
        a = [float(x) for x in rng.uniform(5.0, 500.0, 3)]
        r, m = rot.rotation_matrix(*ang), rot.anisotropy_matrix(*ang, *a)
        d = rng.normal(0.0, 100.0, (50, 3))
        hr = np.sqrt((((d @ r.T) / a) ** 2).sum(axis=1))
        assert np.allclose(np.linalg.norm(d @ m.T, axis=1) / a[0], hr, rtol=1e-12, atol=0.0)
        e = rot.ellipsoid_matrix(*ang, *a)  # the unit sphere → the ellipsoid: the columns are a_i·u_i
        assert float(np.abs(e - r.T * a).max()) <= 1e-12 * max(a)
        assert float(np.abs(m @ e - a[0] * np.eye(3)).max()) <= 1e-12 * a[0]
        ends = e.T  # the ends of the axes from the centre: h_r = 1 on each
        assert np.allclose(np.sqrt((((ends @ r.T) / a) ** 2).sum(axis=1)), 1.0, rtol=1e-12)
    assert bits(rot.anisotropy_matrix(0.0, 0.0, 0.0, 100.0, 50.0, 25.0), [[0, 1, 0], [-2, 0, 0], [0, 0, 4]])
    assert bits(rot.ellipsoid_matrix(90.0, 0.0, 0.0, 100.0, 50.0, 25.0), np.diag([100.0, 50.0, 25.0]))
    assert bits(rot.anisotropy_matrix(0.0, 0.0, 0.0, 1.0, 0.5, 0.25), rot.anisotropy_matrix(0, 0, 0, 100, 50, 25))
    for bad, param in (((0.0, 1.0, 1.0), "a1"), ((1.0, -2.0, 1.0), "a2"), ((1.0, 1.0, math.nan), "a3"),
                       ((1.0, None, 1.0), "a2"), ((math.inf, 1.0, 1.0), "a1")):  # fmt: skip
        for fn in (rot.anisotropy_matrix, rot.ellipsoid_matrix):
            with pytest.raises(StatsError) as e:
                fn(0.0, 0.0, 0.0, *bad)
            assert e.value.code == "bad_param" and e.value.param == param and e.value.hint
            assert ("больше нуля" in e.value.message) == (bad[int(param[1]) - 1] in (0.0, -2.0))


# --- the second notation: dip / dip direction / pitch ----------------------------------------------------------------


def plane_by_hand(r: np.ndarray) -> tuple[float, float, float]:
    """The plane of the axes a1–a2 by ADR 0021 §2 (`intercepts.formulas`) and the pitch by its definition."""
    from bf_core.intercepts.formulas import plane_angles, plane_normal

    alpha, dipdir = (float(x) for x in plane_angles(r[2]))
    n = plane_normal(alpha, dipdir)
    strike = np.array([math.sin(math.radians(dipdir - 90.0)), math.cos(math.radians(dipdir - 90.0)), 0.0])
    down = np.cross(strike, n)  # the line of dip
    pitch = math.degrees(math.atan2(float(r[0] @ down), float(r[0] @ strike))) % 180.0
    return alpha, dipdir, pitch


def test_the_plane_of_the_axes_is_dip_dip_direction_and_pitch() -> None:
    rng = np.random.default_rng(21)
    cases = [(30.0, 0.0, 0.0), (30.0, -20.0, 40.0), (200.0, 35.0, -70.0), (0.0, 0.0, 30.0), (120.0, -45.0, 135.0),
             *zip(rng.uniform(0, 360, 400), rng.uniform(-89, 89, 400), rng.uniform(-180, 180, 400),
                  strict=True)]  # fmt: skip
    for ang in cases:
        r = rot.rotation_matrix(*ang)
        dip, dipdir, pitch = rot.plane_of(*ang)
        want = plane_by_hand(r)
        assert 0.0 <= dip <= 90.0 and 0.0 <= dipdir < 360.0 and 0.0 <= pitch < 180.0
        assert abs(dip - want[0]) <= 1e-9 and circle(dipdir, want[1]) <= 1e-9, ang
        assert circle(pitch, want[2], 180.0) <= 1e-9, ang
        # Back: the same ellipsoid — every axis is the same line; the major axis is given looking down.
        back = rot.angles_of_plane(dip, dipdir, pitch)
        g = rot.rotation_matrix(*back)
        assert float(np.abs(np.abs(g @ r.T) - np.eye(3)).max()) <= 1e-9, ang
        assert g[0][2] <= 1e-15 and g[2][2] >= -1e-15 and back[1] <= 0.0
        again = rot.plane_of(*back)  # … and the three numbers of the plane are kept
        assert abs(again[0] - dip) <= 1e-9 and circle(again[1], dipdir) <= 1e-9
        assert circle(again[2], pitch, 180.0) <= 1e-9, ang


def test_plane_notation_special_cases() -> None:
    # A level plane has no dip direction: Aп = 0, the strike looks west, the pitch is counted from it towards north.
    assert rot.plane_of(0.0, 0.0, 0.0) == (0.0, 0.0, 90.0) and rot.plane_of(90.0, 0.0, 0.0) == (0.0, 0.0, 0.0)
    assert rot.plane_of(270.0, 0.0, 0.0) == (0.0, 0.0, 0.0) and rot.plane_of(45.0)[2] == pytest.approx(135.0, abs=1e-12)
    assert rot.angles_of_plane(0.0, 0.0, 90.0) == (0.0, 0.0, 0.0)
    assert rot.angles_of_plane(0.0, 0.0, 0.0) == (270.0, 0.0, 0.0)
    # The plane dips to where a2 was lifted from: ang1 = 0, ang3 = 30 — a dip of 30° to the east, a1 along the strike.
    assert rot.plane_of(0.0, 0.0, 30.0) == pytest.approx((30.0, 90.0, 0.0), abs=1e-12)
    assert rot.plane_of(0.0, 0.0, -30.0) == pytest.approx((30.0, 270.0, 0.0), abs=1e-12)
    assert rot.plane_of(0.0, -40.0, 0.0) == pytest.approx((40.0, 0.0, 90.0), abs=1e-12)  # a1 straight down the dip
    # A vertical plane: of its two dip directions the one in [0°; 180°); the pitch is counted from its strike.
    assert rot.plane_of(0.0, 0.0, 90.0) == (90.0, 90.0, 0.0) and rot.plane_of(0.0, 0.0, -90.0) == (90.0, 90.0, 0.0)
    assert rot.plane_of(90.0, -30.0, 90.0) == pytest.approx((90.0, 0.0, 150.0), abs=1e-12)
    assert rot.plane_of(270.0, -30.0, 90.0) == pytest.approx((90.0, 0.0, 30.0), abs=1e-12)
    assert rot.plane_of(123.0, -90.0, 0.0)[0] == 90.0 and rot.plane_of(123.0, -90.0, 0.0)[2] == pytest.approx(90.0)
    # A vertical major axis from a plane: ang1 is the dip direction.
    assert rot.angles_of_plane(90.0, 40.0, 90.0) == pytest.approx((40.0, -90.0, 0.0), abs=1e-12)
    # A pitch a rounding error below 180° is 0°.
    assert rot.plane_of(77.0, 0.0, 33.0)[2] == pytest.approx(0.0, abs=1e-9)
    for az in np.random.default_rng(4).uniform(0, 360, 200):
        assert circle(rot.plane_of(float(az), 0.0, 20.0)[2], 0.0, 180.0) <= 1e-9
        assert rot.plane_of(float(az), 0.0, 20.0)[2] < 90.0
    # The numbers of a request: the dip direction and the pitch are turned by whole circles, the dip is refused.
    assert rot.angles_of_plane(30.0, 450.0, 200.0) == pytest.approx(rot.angles_of_plane(30.0, 90.0, 20.0), abs=1e-12)
    for bad, param in (((91.0, 0.0, 0.0), "dip"), ((-1.0, 0.0, 0.0), "dip"), ((10.0, math.nan, 0.0), "dip_direction"),
                       ((10.0, 0.0, None), "pitch")):  # fmt: skip
        with pytest.raises(StatsError) as e:
            rot.angles_of_plane(*bad)
        assert e.value.code == "bad_param" and e.value.param == param and e.value.hint


# --- the shared answers -------------------------------------------------------------------------------------------

RULES = [
    "Angles of GSLIB in degrees (TZ 8.6.3): ang1 - the azimuth of the major axis a1, clockwise from north (+Y);"
    " ang2 - its dip, negative down (the plunge p = -ang2); ang3 - the turn about a1. Normalised: ang1 ="
    " ((ang1 % 360) + 360) % 360 in [0, 360); ang2 in [-90, 90] (beyond - refused); ang3 = ((ang3 % 360) + 360) % 360,"
    " minus 360 when above 180 - in (-180, 180]. The cases hold normalised angles; `normalize` - the others.",
    "sin/cos of degrees d: d reduced to [0, 360) as above; exact (0, 1), (1, 0), (0, -1), (-1, 0) at 0, 90, 180,"
    " 270; otherwise sin and cos of d*(PI/180) (sinCosDegCore of viz/section/plane.ts). (s1, c1), (s2, c2), (s3, c3)"
    " - of ang1, ang2, ang3. -0 is written 0.",
    "axes = R of TZ 8.6.3 (a = 90 - ang1: cos a = s1, sin a = c1; b = -ang2: cos b = c2, sin b = -s2; t = ang3),"
    " its rows are the axes a1, a2, a3 in the world (X east, Y north, Z up): u1 = (c2*s1, c2*c1, s2);"
    " u2 = (-c3*c1 - s3*s2*s1, c3*s1 - s3*s2*c1, s3*c2); u3 = (s3*c1 - c3*s2*s1, -s3*s1 - c3*s2*c1, c3*c2);"
    " u3 = u1 x u2. Zero angles: (0, 1, 0), (-1, 0, 0), (0, 0, 1).",
    "atan2d(y, x): y == 0 -> 0 for x >= 0, else 180; x == 0 -> 90 for y > 0, else -90; otherwise atan2(y, x)*(180/PI).",
    "back (axes -> angles; `hint` - the ang1 the window holds, in the cases the ang1 of the case): u1 of unit length;"
    " rho = hypot(u1.x, u1.y); ang2 = atan2d(u1.z, rho) (not asin: no loss near +-90). If |u1.z| <= 1 - 1e-12:"
    " ang1 = atan2d(u1.x, u1.y), +360 below 0; ang3 = atan2d(u2.z, u3.z). Otherwise the major axis is vertical, the"
    " azimuth and the turn are one rotation: ang1 = hint (normalised); ang3 = ang1 + atan2d(-u2.y, -u2.x) when"
    " u1.z > 0, atan2d(u2.y, -u2.x) - ang1 when u1.z < 0; ang3 normalised as above.",
    "plane (the second notation: the plane of the axes a1-a2, its normal u3): n = u3; a vertical plane"
    " (|n.z| <= 1e-12): n = -n when n.x < 0 or (n.x == 0 and n.y < 0); otherwise n = -n when n.z < 0."
    " dip = atan2d(hypot(n.x, n.y), |n.z|); dip_direction = atan2d(n.x, n.y), +360 below 0, 0 when >= 360; a vertical"
    " plane with dip_direction >= 180 -> 0; dip < 1e-6 -> dip_direction = 0 (ADR 0021, 2). With (sd, cd) of dip and"
    " (sA, cA) of dip_direction: the strike s = (-cA, sA, 0) (the azimuth dip_direction - 90), the line of dip"
    " d = s x n = (cd*sA, cd*cA, -sd), the upper normal n = (sd*sA, sd*cA, cd). pitch = atan2d(u1.d, u1.s), +180"
    " below 0, -180 when >= 180, and 0 when >= 180 - 1e-9: in [0, 180), from the strike towards the line of dip.",
    "from_plane (dip, dip_direction, pitch -> angles): dip in [0, 90] (beyond - refused), dip_direction mod 360,"
    " pitch mod 180; (sp, cp) of pitch; u1 = cp*s + sp*d (it looks down: u1.z <= 0), u3 = n, u2 = u3 x u1; the angles"
    " - `back` of these axes with hint = dip_direction. The same ellipsoid as the angles of the case: every axis is"
    " the same line, maybe the other way. Not given where the major axis is almost vertical (its azimuth is"
    " ill-conditioned at 1e-9).",
    "ellipsoids: anisotropy = A = diag(1, a1/a2, a1/a3)*R (rows u1, (a1/a2)*u2, (a1/a3)*u3): h_r = |A*delta| / a1"
    " (TZ 8.6.3; the matrix of a trend). matrix = the unit sphere -> the ellipsoid, world = centre + matrix*s: its"
    " columns are a1*u1, a2*u2, a3*u3 (matrix[i][j] = a_j * u_j[i]). ends = the ends of the three axes from the"
    " centre: a1*u1, a2*u2, a3*u3.",
    "directions: the direction of a variogram with the azimuth Az and the plunge p (positive down), u ="
    " (cos p*sin Az, cos p*cos Az, -sin p) - the row u1 of the angles (Az, -p, 0).",
    "Tolerances: axes, anisotropy, matrix (relative to the largest range), ends, directions - 1e-12; an `exact` case"
    " - bit for bit; back, plane, from_plane - 1e-9 degrees.",
]

CASES: list[tuple[str, str, tuple[float, float, float], bool]] = [
    ("zero", "Т8.6-3: нулевые углы — большая ось (0; 1; 0)", (0.0, 0.0, 0.0), True),
    ("t863", "Т8.6-3: ang1 = 90°, погружение 30° — (0,8660254; 0; −0,5)", (90.0, -30.0, 0.0), False),
    ("east", "ang1 = 90°: единичная матрица", (90.0, 0.0, 0.0), True),
    ("south", "ang1 = 180°", (180.0, 0.0, 0.0), True),
    ("west", "ang1 = 270°: граница правила a = 450° − ang1", (270.0, 0.0, 0.0), True),
    ("roll90", "ang3 = 90°: плоскость осей вертикальна", (0.0, 0.0, 90.0), True),
    ("roll180", "ang3 = 180°", (90.0, 0.0, 180.0), True),
    ("roll-90", "ang3 = −90°", (180.0, 0.0, -90.0), True),
    ("down", "Большая ось вертикальна, вниз: ang1 сохраняется", (40.0, -90.0, 25.0), False),
    ("down0", "Большая ось вниз, нулевые ang1 и ang3", (0.0, -90.0, 0.0), True),
    ("up", "Большая ось вертикальна, вверх", (310.0, 90.0, -60.0), False),
    ("up90", "Большая ось вверх, кратные 90°", (90.0, 90.0, 90.0), True),
    ("near-down", "Почти вертикально: азимут ещё читается с оси", (200.0, -89.999, 10.0), False),
    ("near-up", "Почти вертикально вверх", (33.0, 89.999, -170.0), False),
    ("ne", "Северо-восток, полого", (45.0, -10.0, 0.0), False),
    ("dip-east", "ang3 = 30°: плоскость падает на восток, a1 по простиранию", (0.0, 0.0, 30.0), False),
    ("dip-west", "ang3 = −30°: падение на запад", (0.0, 0.0, -30.0), False),
    ("down-dip", "a1 по линии падения", (0.0, -40.0, 0.0), False),
    ("general1", "Общий случай", (30.0, -20.0, 40.0), False),
    ("general2", "Общий случай, ось вверх", (200.0, 35.0, -70.0), False),
    ("general3", "Общий случай, ang1 ≥ 270°", (300.0, -55.5, 110.0), False),
    ("general4", "Общий случай, плоскость круче вертикали", (120.0, -45.0, 135.0), False),
    ("general5", "Общий случай", (269.999, 15.25, -179.0), False),
    ("general6", "Общий случай", (359.5, -60.0, 20.0), False),
    ("general7", "Общий случай", (12.5, 45.0, -110.0), False),
    ("general8", "Общий случай", (135.0, -15.25, 179.0), False),
    ("vertical-plane", "Вертикальная плоскость осей, ось наклонна", (90.0, -30.0, 90.0), False),
    ("vertical-plane2", "Вертикальная плоскость осей, другая сторона", (270.0, -30.0, 90.0), False),
]
ILL_FROM_PLANE = {"near-down", "near-up"}  # the azimuth of an almost vertical axis is ill-conditioned (1e−9°)
NORMALIZE = [(-720.0, 0.0, 0.0), (-90.0, 0.0, 540.0), (725.0, -90.0, -180.0), (450.0, 30.0, 200.0),
             (360.0, 90.0, -200.0), (-37.5, -15.25, 180.0), (12.5, 1.0, -160.0)]  # fmt: skip
ELLIPSOIDS = [((0.0, 0.0, 0.0), (100.0, 50.0, 25.0)), ((90.0, -30.0, 0.0), (300.0, 150.0, 40.0)),
              ((30.0, -20.0, 40.0), (250.0, 120.0, 35.5)), ((200.0, 35.0, -70.0), (80.0, 80.0, 20.0)),
              ((40.0, -90.0, 25.0), (60.0, 45.0, 30.0)), ((300.0, -55.5, 110.0), (1.0, 0.5, 0.2)),
              ((120.0, -45.0, 135.0), (40.0, 90.0, 10.0))]  # fmt: skip
DIRECTIONS = [(0.0, 0.0), (90.0, 0.0), (90.0, 30.0), (0.0, 90.0), (0.0, -90.0), (33.0, 12.0), (215.5, -40.0),
              (300.0, 75.25), (135.0, 5.0)]  # fmt: skip


def _rows(m: np.ndarray) -> list[list[float]]:
    return [[float(x) for x in row] for row in m]


def _angles(a: tuple[float, float, float]) -> dict[str, float]:
    return {"ang1": a[0], "ang2": a[1], "ang3": a[2]}


def build() -> dict[str, Any]:
    """The shared answers, by the core."""
    cases = []
    for ident, title, ang, exact in CASES:
        assert rot.normalize_angles(*ang) == ang, ident
        r = rot.rotation_matrix(*ang)
        dip, dipdir, pitch = rot.plane_of(*ang)
        case = {
            "id": ident, "title": title, "exact": exact, **_angles(ang), "axes": _rows(r),
            "vertical": bool(abs(r[0][2]) > rot.VERTICAL), "back": _angles(rot.angles_of(r, ang[0])),
            "plane": {"dip": dip, "dip_direction": dipdir, "pitch": pitch},
        }  # fmt: skip
        if ident not in ILL_FROM_PLANE:
            case["from_plane"] = _angles(rot.angles_of_plane(dip, dipdir, pitch))
        cases.append(case)
    ellipsoids = []
    for ang, a in ELLIPSOIDS:
        e = rot.ellipsoid_matrix(*ang, *a)
        ellipsoids.append({**_angles(ang), "ranges": list(a), "anisotropy": _rows(rot.anisotropy_matrix(*ang, *a)),
                           "matrix": _rows(e), "ends": _rows(e.T)})  # fmt: skip
    return {
        "about": "Общие эталоны поворота эллипсоида анизотропии (ТЗ 8.6.3; ADR 0022 §6, §9; ТД14-1): ядро —"
        " bf_core/geostat/rotation.py (tests/test_geostat_rotation.py), окно — ui/src/viz/ellipsoid/frame.ts",
        "rules": "\n".join(RULES),
        "tolerance": TOL,
        "cases": cases,
        "normalize": [{"given": list(g), "angles": list(rot.normalize_angles(*g))} for g in NORMALIZE],
        "ellipsoids": ellipsoids,
        "directions": [
            {"azimuth": az, "plunge": p, "u": [float(x) for x in rot.direction(az, p)]} for az, p in DIRECTIONS
        ],  # fmt: skip
    }


def _dump(obj: Any) -> str:
    """JSON with every list of numbers on one line."""
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    return re.sub(r"\[\s+((?:-?[\d.e+-]+,?\s*)+)\]", lambda m: "[" + " ".join(m.group(1).split()) + "]", text) + "\n"


def _close(a: Any, b: Any, tol: float) -> bool:
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(_close(a[k], b[k], tol) for k in a)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b, strict=True))
    if isinstance(a, bool) or isinstance(a, str) or a is None:
        return bool(a == b)
    return isinstance(b, int | float) and not isinstance(b, bool) and abs(float(a) - float(b)) <= tol


def _numbers(x: Any) -> Iterator[float]:
    if isinstance(x, dict | list):
        for v in x.values() if isinstance(x, dict) else x:
            yield from _numbers(v)
    elif isinstance(x, int | float) and not isinstance(x, bool):
        yield float(x)


def test_shared_answers_are_what_the_core_computes() -> None:
    built = build()
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(_dump(built), encoding="utf-8")
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    assert stored["rules"] == built["rules"] and stored["tolerance"] == built["tolerance"]
    assert [c["id"] for c in stored["cases"]] == [c[0] for c in CASES]
    # The file is the core to 1e−12 (sin and cos of libm may differ in the last bit between platforms) …
    assert _close(stored, built, 1e-12)
    for case in stored["cases"]:
        ang = (case["ang1"], case["ang2"], case["ang3"])
        r = np.array(case["axes"])
        if case["exact"]:  # … and bit for bit where the angles are multiples of 90°
            assert bits(r, rot.rotation_matrix(*ang)), case["id"]
            assert set(np.abs(r).ravel().tolist()) <= {0.0, 1.0}
        # What the window checks: axes 1e−12, back 1e−9 (the ang1 of a vertical axis is the given one), plane 1e−9.
        back = rot.angles_of(r, ang[0])
        assert all(abs(x - y) <= TOL["angles"] for x, y in zip(back, ang, strict=True)), case["id"]
        assert [case["back"][k] for k in ("ang1", "ang2", "ang3")] == pytest.approx(list(ang), abs=TOL["angles"])
        assert case["vertical"] == (abs(ang[1]) == 90.0)
        plane = case["plane"]
        assert ("from_plane" in case) == (case["id"] not in ILL_FROM_PLANE)
        g = rot.rotation_matrix(*rot.angles_of_plane(plane["dip"], plane["dip_direction"], plane["pitch"]))
        assert float(np.abs(np.abs(g @ r.T) - np.eye(3)).max()) <= 1e-9, case["id"]  # the same lines of the axes
        assert 0.0 <= case["plane"]["pitch"] < 180.0 and 0.0 <= case["plane"]["dip_direction"] < 360.0
    numbers = list(_numbers(stored))  # no gaps and no −0 in the file
    assert len(numbers) > 500 and all(math.isfinite(x) and (x != 0.0 or math.copysign(1.0, x) > 0) for x in numbers)


def test_shared_answers_hold_the_acceptance_numbers() -> None:
    """Т8.6-3 as the window reads it."""
    stored = {c["id"]: c for c in json.loads(DATA.read_text(encoding="utf-8"))["cases"]}
    assert stored["zero"]["axes"] == [[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    assert stored["t863"]["axes"][0] == pytest.approx([0.8660254037844386, 0.0, -0.5], abs=1e-12)
    assert stored["down"]["back"] == {"ang1": 40.0, "ang2": -90.0, "ang3": pytest.approx(25.0, abs=1e-9)}
    assert stored["dip-east"]["plane"] == pytest.approx({"dip": 30.0, "dip_direction": 90.0, "pitch": 0.0}, abs=1e-9)
