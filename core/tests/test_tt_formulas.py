"""True thickness, the formulas (ТЗ 8.5.1, 8.0.1; ADR 0021 §2): Т8.5-1 … Т8.5-6, the three ways to write m, the
normal of a plane and the direction of a hole from their angles and back.

Tolerance — 1e−12 at l = 1 (Т8.5-5 — 1e−6, as the ТЗ writes the number); angles — 1e−12°."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites.intervals import CompositeError
from bf_core.intercepts import formulas as fm

NAN = math.nan
TOL = 1e-12


def m_of(alpha: float, dipdir: float, theta: float, azimuth: float, length: float = 1.0) -> float:
    """m by the vector form, checked against the two other ways to write it."""
    vector = float(fm.tt_vector(length, fm.plane_normal(alpha, dipdir), fm.hole_direction(theta, azimuth)))
    assert float(fm.tt_expanded(length, alpha, dipdir, theta, azimuth)) == pytest.approx(vector, abs=TOL * length)
    assert float(fm.tt_leontovsky(length, alpha, dipdir, theta, azimuth)) == pytest.approx(vector, abs=TOL * length)
    return vector


def circular(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The difference of two azimuths, degrees, in [0; 180]."""
    return np.abs((np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0)


# --- conventions of ТЗ 8.0.1 ---------------------------------------------------------------------------------


def test_conventions_of_the_normal_and_the_direction() -> None:
    """n = (sin α·sin Aп, sin α·cos Aп, cos α) — the upper normal; d = (sin θ·sin Aс, sin θ·cos Aс, −cos θ) —
    down the hole; X east, Y north, Z up."""
    assert fm.plane_normal(0.0, 0.0).tolist() == [0.0, 0.0, 1.0]  # a level plane
    assert fm.plane_normal(30.0, 90.0) == pytest.approx([0.5, 0.0, math.sqrt(3) / 2], abs=TOL)  # dips to the east
    assert fm.plane_normal(90.0, 0.0) == pytest.approx([0.0, 1.0, 0.0], abs=TOL)  # vertical, faces north
    assert fm.hole_direction(0.0, 0.0).tolist() == [0.0, 0.0, -1.0]  # vertical, down
    assert fm.hole_direction(90.0, 90.0) == pytest.approx([1.0, 0.0, 0.0], abs=TOL)  # level, to the east
    assert fm.hole_direction(180.0, 0.0) == pytest.approx([0.0, 0.0, 1.0], abs=TOL)  # a rising hole
    # the inclination of the survey δ (−90° — down): θ = 90° + δ; the direction is that of the trajectories
    from bf_core.drillholes.desurvey import Collars, desurvey

    traj = desurvey(Collars([0], [0.0], [0.0], [0.0], [10.0], [120.0], [-60.0]))
    assert fm.hole_direction(90.0 - 60.0, 120.0) == pytest.approx(traj.dirs[0], abs=TOL)
    # arrays broadcast: one plane, many holes
    n = fm.plane_normal(np.array([0.0, 30.0]), 90.0)
    assert n.shape == (2, 3) and n.dtype == np.float64


# --- Т8.5-1 … Т8.5-6 -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("dipdir", [0.0, 77.0, 245.5])
@pytest.mark.parametrize("length", [1.0, 12.5])
def test_t8_5_1_vertical_hole(dipdir: float, length: float) -> None:
    """Т8.5-1. Вертикальная скважина, α = 30°: m = 0,8660254·l."""
    m = m_of(30.0, dipdir, 0.0, 0.0, length)
    assert m == pytest.approx(math.cos(math.radians(30.0)) * length, abs=TOL * length)
    assert m == pytest.approx(0.8660254 * length, abs=5e-8 * length)  # the number of the ТЗ, 7 digits


@pytest.mark.parametrize("dipdir", [0.0, 33.0, 180.0, 300.0])
def test_t8_5_2_hole_against_the_dip(dipdir: float) -> None:
    """Т8.5-2. α = 60°, Aс = Aп + 180°, θ = 60°: m = l (the hole is normal to the plane)."""
    assert m_of(60.0, dipdir, 60.0, (dipdir + 180.0) % 360.0) == pytest.approx(1.0, abs=TOL)
    assert m_of(60.0, dipdir, 60.0, (dipdir + 180.0) % 360.0, 7.3) == pytest.approx(7.3, abs=TOL * 7.3)


@pytest.mark.parametrize("dipdir", [0.0, 120.0, 333.0])
def test_t8_5_3_hole_along_the_plane(dipdir: float) -> None:
    """Т8.5-3. α = 30°, θ = 60°, Aс = Aп: m = 0, TT_FLAG = 1 (the hole lies in the plane)."""
    m = m_of(30.0, dipdir, 60.0, dipdir, 5.0)
    assert m == pytest.approx(0.0, abs=TOL * 5.0)
    assert bool(fm.is_parallel(m, 5.0)) and fm.FLAG_PARALLEL == 1
    assert int(np.where(fm.is_parallel(m, 5.0), fm.FLAG_PARALLEL, 0)) == 1


@pytest.mark.parametrize("dipdir", [0.0, 45.0, 200.0])
def test_t8_5_4_vertical_plane_level_hole(dipdir: float) -> None:
    """Т8.5-4. α = 90°, θ = 90°: Aс = Aп ± 90° — m = 0; Aс = Aп или Aп + 180° — m = l."""
    for turn in (90.0, -90.0):
        assert m_of(90.0, dipdir, 90.0, (dipdir + turn) % 360.0) == pytest.approx(0.0, abs=TOL)
    for turn in (0.0, 180.0):
        assert m_of(90.0, dipdir, 90.0, (dipdir + turn) % 360.0) == pytest.approx(1.0, abs=TOL)


def test_t8_5_5_general_case() -> None:
    """Т8.5-5. α = 45°, Aп = 120°, θ = 30°, Aс = 250°: m = 0,839632·l (1e−6)."""
    assert m_of(45.0, 120.0, 30.0, 250.0) == pytest.approx(0.839632, abs=1e-6)
    assert m_of(45.0, 120.0, 30.0, 250.0) == pytest.approx(0.8396321745, abs=1e-10)  # ADR 0021, находка 12
    assert m_of(45.0, 120.0, 30.0, 250.0, 20.0) == pytest.approx(0.839632 * 20.0, abs=1e-6 * 20.0)


def test_t8_5_6_three_forms_agree_on_ten_thousand_random() -> None:
    """Т8.5-6. 10⁴ случайных (α, Aп, θ, Aс), зерно 1: векторная, развёрнутая и запись Леонтовского равны
    (1e−12 абс.)."""
    rng = np.random.Generator(np.random.PCG64(1))
    alpha, dipdir = rng.uniform(0.0, 90.0, 10_000), rng.uniform(0.0, 360.0, 10_000)
    theta, azimuth = rng.uniform(0.0, 180.0, 10_000), rng.uniform(0.0, 360.0, 10_000)
    vector = fm.tt_vector(1.0, fm.plane_normal(alpha, dipdir), fm.hole_direction(theta, azimuth))
    expanded = fm.tt_expanded(1.0, alpha, dipdir, theta, azimuth)
    leontovsky = fm.tt_leontovsky(1.0, alpha, dipdir, theta, azimuth)
    assert vector.shape == (10_000,) and vector.dtype == np.float64
    assert float(np.abs(vector - expanded).max()) <= TOL
    assert float(np.abs(vector - leontovsky).max()) <= TOL
    assert float(np.abs(expanded - leontovsky).max()) <= TOL
    assert 0.0 <= float(vector.min()) and float(vector.max()) <= 1.0 + TOL
    assert 0.5 < float(vector.mean()) < 0.7  # not all zeros: the mean of |cos| of a random angle on a sphere


def test_leontovsky_special_cases() -> None:
    """8.5.1: навстречу падению (γ = 180°) — m = l·sin(α + β); по падению (γ = 0°) — m = l·|sin(α − β)|;
    вертикальная скважина — m = l·cos α; β = 90° − θ."""
    rng = np.random.Generator(np.random.PCG64(7))
    for alpha, dipdir, theta in zip(
        rng.uniform(0, 90, 50), rng.uniform(0, 360, 50), rng.uniform(0, 90, 50), strict=True
    ):
        a, b = math.radians(alpha), math.radians(90.0 - theta)
        against = m_of(alpha, dipdir, theta, (dipdir + 180.0) % 360.0)
        along = m_of(alpha, dipdir, theta, dipdir)
        assert against == pytest.approx(abs(math.sin(a + b)), abs=TOL)  # «+» of Leontovsky's formula
        assert along == pytest.approx(abs(math.sin(a - b)), abs=TOL)  # «−»
        assert m_of(alpha, dipdir, 0.0, 123.0) == pytest.approx(math.cos(a), abs=TOL)


# --- angles ↔ vectors ----------------------------------------------------------------------------------------


def test_round_trip_on_ten_thousand_random() -> None:
    """Обратное преобразование на 10⁴ случайных: углы → вектор → углы и вектор → углы → вектор (1e−12)."""
    rng = np.random.Generator(np.random.PCG64(1))
    alpha, dipdir = rng.uniform(0.0, 90.0, 10_000), rng.uniform(0.0, 360.0, 10_000)
    theta, azimuth = rng.uniform(0.0, 180.0, 10_000), rng.uniform(0.0, 360.0, 10_000)
    n, d = fm.plane_normal(alpha, dipdir), fm.hole_direction(theta, azimuth)
    assert float(np.abs(np.linalg.norm(n, axis=1) - 1.0).max()) <= TOL
    assert float(np.abs(np.linalg.norm(d, axis=1) - 1.0).max()) <= TOL
    a2, dd2 = fm.plane_angles(n)
    t2, az2 = fm.hole_angles(d)
    assert float(np.abs(a2 - alpha).max()) <= TOL and float(circular(dd2, dipdir).max()) <= TOL
    assert float(np.abs(t2 - theta).max()) <= TOL and float(circular(az2, azimuth).max()) <= TOL
    assert 0.0 <= float(dd2.min()) and float(dd2.max()) < 360.0
    assert 0.0 <= float(az2.min()) and float(az2.max()) < 360.0
    # any vectors, of any length and side: the angles give the unit upper normal and the unit direction back
    v = rng.normal(size=(10_000, 3)) * rng.uniform(0.01, 1000.0, (10_000, 1))
    up = fm.upper_normal(v)
    assert float(up[:, 2].min()) >= 0.0
    assert float(np.abs(np.cross(up, v / np.linalg.norm(v, axis=1)[:, None])).max()) <= TOL  # the same line
    assert float(np.abs(fm.plane_normal(*fm.plane_angles(v)) - up).max()) <= TOL
    assert float(np.abs(fm.hole_direction(*fm.hole_angles(v)) - v / np.linalg.norm(v, axis=1)[:, None]).max()) <= TOL


def test_small_and_steep_angles_keep_the_accuracy() -> None:
    """α near 0° and near 90° (arccos would lose digits there): still 1e−12°."""
    alpha = np.array([1e-5, 1e-4, 0.001, 0.01, 89.99, 89.9999, 89.999999, 90.0])
    dipdir = np.array([10.0, 100.0, 200.0, 359.0, 45.0, 135.0, 179.0, 60.0])
    a2, dd2 = fm.plane_angles(fm.plane_normal(alpha, dipdir))
    assert float(np.abs(a2 - alpha).max()) <= TOL and float(circular(dd2, dipdir).max()) <= 1e-9
    theta = np.array([1e-5, 0.001, 89.999999, 90.0, 90.000001, 179.999, 180.0])
    t2, _ = fm.hole_angles(fm.hole_direction(theta, 33.0))
    assert float(np.abs(t2 - theta).max()) <= TOL


def test_the_normal_is_brought_to_the_upper_side() -> None:
    """n_z < 0 → −n; α < 1e−6° → Aп = 0; the angles do not depend on the side of the normal."""
    n = fm.plane_normal(40.0, 250.0)
    for v in (n, -n, 3.0 * n, -0.2 * n):
        alpha, dipdir = fm.plane_angles(v)
        assert float(alpha) == pytest.approx(40.0, abs=TOL) and float(dipdir) == pytest.approx(250.0, abs=TOL)
        assert fm.upper_normal(v) == pytest.approx(n, abs=TOL)
    for v in ([0.0, 0.0, 1.0], [0.0, 0.0, -1.0], [0.0, 0.0, -5.0], [1e-9, -1e-9, 1.0]):
        alpha, dipdir = fm.plane_angles(v)
        assert float(alpha) < 1e-6 and float(dipdir) == 0.0  # a level plane has no dip direction
    up = fm.upper_normal([0.0, 0.0, -1.0])
    assert up.tolist() == [0.0, 0.0, 1.0] and not np.signbit(up).any()  # no −0.0


def test_vertical_plane_takes_the_dip_direction_below_180() -> None:
    """Вертикальная плоскость (|n_z| ≤ 1e−12): из двух азимутов берётся Aп ∈ [0°; 180°) — the result does not
    depend on the side of the normal, i.e. on the order of the corners of a triangle."""
    for azimuth in (0.0, 30.0, 90.0, 179.0, 180.0, 210.0, 270.0, 359.0):
        a = math.radians(azimuth)
        for nz in (0.0, 1e-13, -1e-13):
            v = np.array([math.sin(a), math.cos(a), nz])
            (a1, d1), (a2, d2) = fm.plane_angles(v), fm.plane_angles(-v)
            assert float(a1) == float(a2) and float(d1) == float(d2)  # exactly
            assert float(a1) == pytest.approx(90.0, abs=1e-10) and float(a1) <= 90.0
            assert 0.0 <= float(d1) < 180.0
            assert float(d1) == pytest.approx(azimuth % 180.0, abs=1e-9)
            assert fm.upper_normal(v).tolist() == fm.upper_normal(-v).tolist()


def test_hole_without_an_azimuth_and_rising_holes() -> None:
    """θ = arccos(−d_z) ∈ [0°; 180°]; горизонтальная часть < 1e−12 → Aс = 0."""
    for d, theta in (([0.0, 0.0, -1.0], 0.0), ([0.0, 0.0, 2.0], 180.0), ([1e-13, -1e-13, -1.0], 0.0)):
        t, az = fm.hole_angles(d)
        assert float(t) == pytest.approx(theta, abs=1e-10) and float(az) == 0.0
    t, az = fm.hole_angles([0.0, -3.0, 3.0])  # rising to the south at 45°
    assert float(t) == pytest.approx(135.0, abs=TOL) and float(az) == pytest.approx(180.0, abs=TOL)
    t, az = fm.hole_angles([-1.0, 0.0, 0.0])  # level, to the west
    assert float(t) == pytest.approx(90.0, abs=TOL) and float(az) == pytest.approx(270.0, abs=TOL)
    # an azimuth a hair below north never comes out as 360
    _, az = fm.hole_angles([-1e-18, 1.0, -1.0])
    _, dd = fm.plane_angles([-1e-18, 1.0, 1.0])
    assert float(az) == 0.0 and float(dd) == 0.0


def test_dip_direction_360_is_0() -> None:
    assert fm.plane_normal(35.0, 360.0).tolist() == fm.plane_normal(35.0, 0.0).tolist()
    assert fm.hole_direction(35.0, 360.0).tolist() == fm.hole_direction(35.0, 0.0).tolist()
    assert fm.attitude(35.0, 360.0) == (35.0, 0.0)


def test_agrees_with_the_attitude_of_a_triangle() -> None:
    """The dip and the dip direction of a triangle (`geo.pick.triangle_attitude`, Э8) — the same numbers."""
    from bf_core.geo.pick import triangle_attitude

    rng = np.random.Generator(np.random.PCG64(3))
    for a, b, c in rng.uniform(-50.0, 50.0, (200, 3, 3)):
        att = triangle_attitude(a, b, c)
        alpha, dipdir = fm.plane_angles(att.normal)
        assert float(alpha) == pytest.approx(att.dip, abs=1e-9)
        assert float(circular(dipdir, att.dip_direction)) <= 1e-9


# --- missing values, the range of the input, the flag --------------------------------------------------------


def test_missing_angles_give_no_normal_never_a_zero() -> None:
    """Пусто — у строки нет ориентировки (не ноль); вне диапазона — тоже нет."""
    alpha = np.array([30.0, NAN, 30.0, -1.0, 90.5, 30.0, 30.0, 0.0])
    dipdir = np.array([100.0, 100.0, NAN, 100.0, 100.0, -0.1, 360.5, 0.0])
    n = fm.plane_normal(alpha, dipdir)
    assert np.isnan(n).all(axis=1).tolist() == [False, True, True, True, True, True, True, False]
    assert np.isnan(fm.tt_vector(2.0, n, fm.hole_direction(0.0, 0.0))).tolist() == np.isnan(n[:, 0]).tolist()
    theta = np.array([30.0, NAN, 30.0, -1.0, 181.0])
    azimuth = np.array([100.0, 100.0, NAN, 100.0, 100.0])
    assert np.isnan(fm.hole_direction(theta, azimuth)).all(axis=1).tolist() == [False, True, True, True, True]
    for v in ([NAN, 0.0, 1.0], [0.0, 0.0, 0.0], [math.inf, 0.0, 1.0]):
        assert np.isnan(fm.upper_normal(v)).all()
        assert all(math.isnan(float(x)) for x in fm.plane_angles(v))
        assert all(math.isnan(float(x)) for x in fm.hole_angles(v))
    assert math.isnan(float(fm.tt_vector(NAN, [0.0, 0.0, 1.0], [0.0, 0.0, -1.0])))
    assert math.isnan(float(fm.tt_expanded(1.0, NAN, 0.0, 0.0, 0.0)))
    assert math.isnan(float(fm.tt_leontovsky(1.0, 30.0, 0.0, NAN, 0.0)))


@pytest.mark.parametrize(
    ("alpha", "dipdir", "param"),
    [(-0.1, 10.0, "alpha"), (90.1, 10.0, "alpha"), (NAN, 10.0, "alpha"), ("x", 10.0, "alpha"),
     (30.0, -1.0, "dipdir"), (30.0, 360.1, "dipdir"), (30.0, None, "dipdir"), (True, 10.0, "alpha")],
)  # fmt: skip
def test_attitude_out_of_range_is_bad_param(alpha: object, dipdir: object, param: str) -> None:
    """α ∈ [0; 90], Aп ∈ [0; 360]; вне диапазона — `bad_param` (422) with the parameter at fault."""
    with pytest.raises(CompositeError) as err:
        fm.attitude(alpha, dipdir)
    assert err.value.code == "bad_param" and err.value.param == param
    assert err.value.message and err.value.hint and "диапазон" in (err.value.message + err.value.hint)
    with pytest.raises(CompositeError) as err:
        fm.attitude(alpha, dipdir, alpha_param="fallback_alpha", dipdir_param="fallback_dipdir")
    assert err.value.param == "fallback_" + param


def test_attitude_in_range() -> None:
    assert fm.attitude(0, 0) == (0.0, 0.0) and fm.attitude("45", 359.5) == (45.0, 359.5)
    assert fm.attitude(90.0, 180.0) == (90.0, 180.0)


def test_parallel_flag_threshold() -> None:
    """Вырождение: TT / l < ε_в (0,1 by default) — strictly below; a missing TT has no flag."""
    length = np.array([1.0, 1.0, 1.0, 10.0, 10.0, 2.0, 2.0])
    tt = np.array([0.0, 0.0999, 0.1, 0.99, 1.0, NAN, 1.5])
    assert fm.is_parallel(tt, length).tolist() == [True, True, False, True, False, False, False]
    assert fm.is_parallel(tt, length, 0.5).tolist() == [True, True, True, True, True, False, False]
    assert (fm.EPS_DEFAULT, fm.EPS_RANGE, fm.STEP_DEFAULT, fm.STEP_RANGE) == (0.1, (0.01, 0.5), 1.0, (0.1, 10.0))
    assert (fm.FLAG_PARALLEL, fm.FLAG_NO_ORIENTATION, fm.FLAG_CORE_LOCAL, fm.FLAG_NO_PATH) == (1, 2, 4, 8)
