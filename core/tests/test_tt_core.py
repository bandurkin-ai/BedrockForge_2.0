"""True thickness, oriented core (ТЗ 8.5.2; ADR 0021 §6): Т8.5-7, Т8.5-8; (α_к, β_к) → the world normal for
the lines BOH and TOH, |n·d| = sin α_к on 10⁴ random measurements, a nearly vertical hole, measurements bound
to intervals (inside, the nearest within d_к, the principal vector of several), one hash with any threads.

Tolerances: angles — 1e−4° (the ТЗ gives four decimals); |n·d| = sin α_к — 1e−12."""

from __future__ import annotations

import hashlib
import math

import numpy as np
import pytest

from bf_core.composites.intervals import CompositeError
from bf_core.drillholes.desurvey import Collars, Surveys, Trajectories, desurvey
from bf_core.intercepts import core_orient as co
from bf_core.intercepts import directions as dr
from bf_core.intercepts import formulas as fm

NAN = math.nan
TOL = 1e-12
DEG = 1e-4


def angles(d: np.ndarray, alpha_c: float, beta_c: float, line: str = "boh") -> tuple[float, float]:
    """α and Aп of the plane of a measurement; |n·d| = sin α_к is checked on the way."""
    n = co.core_normal(d, alpha_c, beta_c, line)
    assert abs(float(n @ d)) == pytest.approx(math.sin(math.radians(alpha_c)), abs=TOL)
    assert float(np.linalg.norm(n)) == pytest.approx(1.0, abs=TOL)
    alpha, dipdir = fm.plane_angles(n)
    return float(alpha), float(dipdir)


# --- Т8.5-7, Т8.5-8 ------------------------------------------------------------------------------------------


def test_t8_5_7_core_measurements_of_an_inclined_hole() -> None:
    """Т8.5-7. Керн, θ = 60°, Aс = 0°, BOH. (α_к; β_к) = (30°; 180°) → α = 0°; (90°; любое) → α = 60°,
    Aп = 180°; (60°; 90°) → α = 64,3411°, Aп = 213,6901°; (60°; 270°) → α = 64,3411°, Aп = 146,3099° (1e−4°);
    везде abs(n·d) = sin α_к."""
    d = fm.hole_direction(60.0, 0.0)
    alpha, dipdir = angles(d, 30.0, 180.0)
    assert alpha == pytest.approx(0.0, abs=DEG) and dipdir == 0.0  # a level plane has no dip direction
    for beta in (0.0, 77.0, 180.0, 270.0, 359.0):
        assert angles(d, 90.0, beta) == pytest.approx((60.0, 180.0), abs=DEG)
    assert angles(d, 60.0, 90.0) == pytest.approx((64.3411, 213.6901), abs=DEG)
    assert angles(d, 60.0, 270.0) == pytest.approx((64.3411, 146.3099), abs=DEG)
    # the same numbers in closed form: n = (±1/2, 3/4, −√3/4) turned to the upper side
    assert angles(d, 60.0, 90.0) == pytest.approx(
        (math.degrees(math.acos(math.sqrt(3) / 4)), 180.0 + math.degrees(math.atan2(0.5, 0.75))), abs=1e-10
    )


def test_t8_5_8_core_measurement_gives_a_vertical_plane() -> None:
    """Т8.5-8. Керн, θ = 45°, Aс = 90°, α_к = 45°, β_к = 0°: α = 90°, Aп = 90°."""
    alpha, dipdir = angles(fm.hole_direction(45.0, 90.0), 45.0, 0.0)
    assert alpha == pytest.approx(90.0, abs=DEG) and dipdir == pytest.approx(90.0, abs=DEG)
    # the opposite side of the core: the plane lies level
    alpha, _ = angles(fm.hole_direction(45.0, 90.0), 45.0, 180.0)
    assert alpha == pytest.approx(0.0, abs=DEG)


# --- the formula ---------------------------------------------------------------------------------------------


def test_ten_thousand_random_measurements() -> None:
    """abs(n·d) = sin α_к на 10⁴ случайных замерах; the normal is a unit upper one; TOH is BOH turned by
    180°; β_к is taken modulo 360."""
    rng = np.random.Generator(np.random.PCG64(1))
    theta, azimuth = rng.uniform(1.5, 178.5, 10_000), rng.uniform(0.0, 360.0, 10_000)
    alpha_c, beta_c = rng.uniform(0.0, 90.0, 10_000), rng.uniform(0.0, 360.0, 10_000)
    d = fm.hole_direction(theta, azimuth)
    n = co.core_normal(d, alpha_c, beta_c)
    assert n.shape == (10_000, 3) and not np.isnan(n).any()
    assert float(np.abs(np.abs((n * d).sum(axis=1)) - np.sin(np.radians(alpha_c))).max()) <= TOL
    assert float(np.abs(np.linalg.norm(n, axis=1) - 1.0).max()) <= TOL
    assert float(n[:, 2].min()) >= -fm.VERTICAL
    toh = co.core_normal(d, alpha_c, (beta_c + 180.0) % 360.0, "toh")
    assert float(np.abs(toh - n).max()) <= 1e-10
    for turn in (360.0, -360.0, 720.0):
        assert float(np.abs(co.core_normal(d, alpha_c, beta_c + turn) - n).max()) <= 1e-10
    # the formula of the ТЗ term by term: e1 — the bottom line of the core, e2 = d × e1,
    # n = sin α_к·d − cos α_к·(cos β_к·e1 + sin β_к·e2), turned to the upper side
    g = np.array([0.0, 0.0, -1.0])
    e1 = g - (d @ g)[:, None] * d
    e1 /= np.linalg.norm(e1, axis=1)[:, None]
    e2 = np.cross(d, e1)
    a, b = np.radians(alpha_c)[:, None], np.radians(beta_c)[:, None]
    want = np.sin(a) * d - np.cos(a) * (np.cos(b) * e1 + np.sin(b) * e2)
    assert float(np.abs(fm.upper_normal(want) - n).max()) <= TOL
    assert float(e1[:, 2].max()) < 0.0  # the bottom line looks down, for rising holes too


def test_beta_is_counted_clockwise_looking_down_the_hole_to_the_far_point_of_the_ellipse() -> None:
    """e2 = d × e1 — 90° по часовой стрелке, если смотреть вниз по стволу; β_к отсчитан до дальней по стволу
    точки эллипса: of the points of the plane on the core, the one at β_к is the farthest down the hole."""
    d = fm.hole_direction(60.0, 0.0)  # to the north, 30° below the level: looking down it, the west is on the left
    e1 = np.array([0.0, -0.5, -math.sqrt(3) / 2])  # the bottom line, 6 o'clock
    e2 = np.cross(d, e1)
    assert e2 == pytest.approx([-1.0, 0.0, 0.0], abs=TOL)  # 9 o'clock: clockwise from the bottom line
    rng = np.random.Generator(np.random.PCG64(2))
    for alpha_c, beta_c in zip(rng.uniform(5.0, 85.0, 50), rng.uniform(0.0, 360.0, 50), strict=True):
        n = co.core_normal(d, alpha_c, beta_c)
        n = n if n @ d > 0.0 else -n  # the side of the formula: n·d = +sin α_к
        phi = np.radians(np.arange(0.0, 360.0, 1.0))
        rim = np.cos(phi)[:, None] * e1 + np.sin(phi)[:, None] * e2  # points of the core surface, radius 1
        along = -(rim @ n) / (n @ d)  # where the plane n·x = 0 meets the line rim + s·d
        far = math.radians(beta_c)
        s_far = -((math.cos(far) * e1 + math.sin(far) * e2) @ n) / (n @ d)
        assert s_far >= float(along.max()) - 1e-12
        assert s_far == pytest.approx(1.0 / math.tan(math.radians(alpha_c)), rel=1e-10)


def test_nearly_vertical_hole_is_not_turned_into_a_world_normal() -> None:
    """При θ < 1° — замер не пересчитывается (the bottom line of a vertical core is not defined), up or down."""
    for theta, turned in ((0.0, False), (0.5, False), (0.999, False), (1.001, True), (30.0, True), (179.5, False),
                          (178.9, True), (180.0, False)):  # fmt: skip
        n = co.core_normal(fm.hole_direction(theta, 40.0), 50.0, 10.0)
        assert bool(np.isfinite(n).all()) is turned, theta
        assert bool(np.isnan(n).all()) is not turned
    assert co.STEEP_DEG == 1.0


def test_missing_measurements_give_no_normal() -> None:
    d = fm.hole_direction(60.0, 0.0)
    for alpha_c, beta_c in ((NAN, 10.0), (-0.1, 10.0), (90.1, 10.0), (50.0, NAN), (50.0, math.inf)):
        assert np.isnan(co.core_normal(d, alpha_c, beta_c)).all()
    assert np.isnan(co.core_normal([NAN, 0.0, -1.0], 50.0, 10.0)).all()
    assert np.isnan(co.core_normal([0.0, 0.0, 0.0], 50.0, 10.0)).all()
    # α_к = 90°: the plane is square to the core, β_к means nothing and may be empty
    assert co.core_normal(d, 90.0, NAN) == pytest.approx(fm.upper_normal(d), abs=TOL)
    with pytest.raises(CompositeError) as err:
        co.core_normal(d, 50.0, 10.0, "top")
    assert err.value.code == "bad_param" and err.value.param == "line" and err.value.hint


# --- measurements of a set -----------------------------------------------------------------------------------


def three_holes() -> Trajectories:
    """Dense 0 — curved, from vertical to 45° to the east at 100 m; 1 — vertical; 2 — no collar coordinates."""
    return desurvey(
        Collars([0, 1, 2], [0.0, 50.0, NAN], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [120.0, 60.0, 60.0]),
        Surveys([0, 0], [0.0, 100.0], [90.0, 90.0], [-90.0, -45.0]),
        n_holes=3,
    )


def test_measurements_take_the_direction_at_their_depth() -> None:
    """d — направление ствола на глубине замера; states: OK, LOCAL (θ < 1°, no trajectory), UNUSED."""
    traj = three_holes()
    dense = np.array([0, 0, 0, 0, 0, 0, 0, 1, 2, -1, 0, 0])
    depth = np.array([50.0, 100.0, 110.0, 1.0, 50.0, 50.0, 50.0, 30.0, 30.0, 30.0, NAN, 50.0])
    alpha_c = np.array([60.0, 60.0, 60.0, 60.0, NAN, 95.0, 60.0, 60.0, 60.0, 60.0, 60.0, 90.0])
    beta_c = np.array([90.0, 90.0, 90.0, 90.0, 10.0, 10.0, NAN, 10.0, 10.0, 10.0, 10.0, NAN])
    m = co.measurements(traj, dense, depth, alpha_c, beta_c)
    ok, local, unused = co.STATE_OK, co.STATE_LOCAL, co.STATE_UNUSED
    assert m.state.tolist() == [ok, ok, ok, local, unused, unused, unused, local, local, local, unused, ok]
    assert (m.n_ok, m.n_local, m.n_unused) == (4, 4, 4)
    d = dr.directions(traj, dense[:3], depth[:3])
    assert m.normal[:3] == pytest.approx(co.core_normal(d, 60.0, 90.0), abs=TOL)
    assert np.abs((m.normal[:3] * d).sum(axis=1)) == pytest.approx(np.full(3, math.sin(math.radians(60.0))), abs=TOL)
    assert float(np.abs(m.normal[0] - m.normal[1]).max()) > 0.1  # the hole turns: another plane in the world
    assert m.normal[1] == pytest.approx(m.normal[2], abs=TOL)  # below the last station — its direction
    assert np.isnan(m.normal[3:11]).all()
    usable = m.state != unused
    assert m.sin_alpha[usable] == pytest.approx(np.sin(np.radians(alpha_c[usable])), abs=TOL)
    assert np.isnan(m.sin_alpha[~usable]).all()  # a measurement left out gives nothing, not a zero
    assert m.normal.dtype == np.float64 and m.state.dtype == np.uint8
    # the line of the top of the core: the same planes with β_к + 180°
    toh = co.measurements(traj, dense, depth, alpha_c, (beta_c + 180.0) % 360.0, line="toh")
    assert toh.normal[:3] == pytest.approx(m.normal[:3], abs=1e-10) and toh.state.tolist() == m.state.tolist()


# --- measurements → intervals --------------------------------------------------------------------------------


def straight_hole() -> Trajectories:
    """Dense 0 — θ = 30°, Aс = 120°, 200 m; 1 — vertical."""
    return desurvey(Collars([0, 1], [0.0, 9.0], [0.0, 9.0], [0.0, 0.0], [200.0, 200.0], [120.0, 0.0], [-60.0, -90.0]))


M_DEPTH = np.array([1.0, 2.0, 5.0, 7.0, 7.5, 26.0, 60.0, 61.0])
M_ALPHA = np.array([40.0, 55.0, 70.0, 30.0, 50.0, 65.0, 20.0, NAN])
M_BETA = np.array([10.0, 200.0, 45.0, 300.0, 120.0, 80.0, 15.0, 15.0])
M_STATE = [0, 0, 1, 0, 0, 0, 0, 2]  # the measurement at 5 m is LOCAL (given by hand below), the last one is unused


def hole_7() -> tuple[np.ndarray, co.Measurements]:
    """Measurements of the hole with the key 7 (dense 0 of `straight_hole`); the one at 5 m — as if the hole
    were vertical there."""
    m = co.measurements(straight_hole(), np.zeros(8, dtype=np.int64), M_DEPTH, M_ALPHA, M_BETA)
    normal, state = m.normal.copy(), m.state.copy()
    normal[2] = NAN
    state[2] = co.STATE_LOCAL
    assert state.tolist() == M_STATE
    return np.full(8, 7, dtype=np.int64), co.Measurements(normal, m.sin_alpha, state)


def sin_of(depth: float) -> float:
    return math.sin(math.radians(float(M_ALPHA[M_DEPTH.tolist().index(depth)])))


def principal(normals: np.ndarray) -> np.ndarray:
    """The principal vector of Σ n·nᵀ, on the upper side."""
    _, vectors = np.linalg.eigh(normals.T @ normals)
    return fm.upper_normal(vectors[:, -1])


def test_interval_with_measurements() -> None:
    """Интервал с замером: один — TT = l·sin α_к, ALPHA и DIPDIR — из мировой нормали; несколько — главный
    собственный вектор Σ n·nᵀ, TT — суммой; глубина замера в [FROM; TO] с допуском 1e−6 м."""
    m_hole, m = hole_7()
    frm = np.array([0.0, 2.0, 4.0, 6.0, 25.9999995, 26.0000005, 59.5])
    to = np.array([2.0, 4.0, 6.0, 8.0, 25.9999995 + 0.5, 28.0, 62.0])
    o = co.assign(np.full(7, 7), frm, to, m_hole, M_DEPTH, m)
    assert o.kind.tolist() == [co.KIND_MEASURED] * 7 and co.KIND_MEASURED == 1
    assert o.count.tolist() == [2, 1, 1, 2, 1, 1, 1]  # the unused one at 61 m is not counted
    # 0–2: the measurements at 1 and 2 m (a measurement on the boundary belongs to both intervals)
    assert o.normal[0] == pytest.approx(principal(m.normal[[0, 1]]), abs=1e-10) and math.isnan(o.sin_alpha[0])
    # 2–4: the one at 2 m
    assert o.normal[1].tolist() == m.normal[1].tolist() and o.sin_alpha[1] == pytest.approx(sin_of(2.0), abs=TOL)
    # 4–6: the one at 5 m was not turned into a world normal: TT = l·sin α_к, no ALPHA and DIPDIR, bit 4
    assert np.isnan(o.normal[2]).all() and o.sin_alpha[2] == pytest.approx(sin_of(5.0), abs=TOL)
    assert o.flag.tolist() == [0, 0, fm.FLAG_CORE_LOCAL, 0, 0, 0, 0] and fm.FLAG_CORE_LOCAL == 4
    # 6–8: two measurements
    assert o.normal[3] == pytest.approx(principal(m.normal[[3, 4]]), abs=1e-10) and math.isnan(o.sin_alpha[3])
    # the measurement at 26 m is within 1e−6 m of the ends of both intervals
    assert o.normal[4].tolist() == m.normal[5].tolist() == o.normal[5].tolist()
    assert o.sin_alpha[4] == o.sin_alpha[5] == pytest.approx(sin_of(26.0), abs=TOL)
    assert o.sin_alpha[6] == pytest.approx(sin_of(60.0), abs=TOL)
    assert o.kind.dtype == np.uint8 and o.flag.dtype == np.uint8 and o.count.dtype == np.int32
    assert o.normal.dtype == np.float64 and o.normal.shape == (7, 3)
    # TT of the single measurement on a straight hole is the sum along the hole with its world normal
    t = dr.thickness(straight_hole(), np.zeros(7, dtype=np.int64), frm, to, o.normal)
    for r in (1, 4, 5, 6):
        assert float(t.tt[r]) == pytest.approx((to[r] - frm[r]) * float(o.sin_alpha[r]), rel=1e-12)


def test_principal_vector_of_several_measurements() -> None:
    """Несколько замеров — главный собственный вектор Σ n·nᵀ (знак — n_z ≥ 0): two planes dipping 30° to the
    east and to the west give the level plane; the sides of the normals do not matter."""
    east, west = fm.plane_normal(30.0, 90.0), fm.plane_normal(30.0, 270.0)
    for normals in ([east, west], [east, -west], [-east, -west], [east, west, east, west]):
        m = co.Measurements(np.array(normals), np.full(len(normals), 0.5), np.zeros(len(normals), dtype=np.uint8))
        o = co.assign([3], [10.0], [20.0], np.full(len(normals), 3), np.linspace(11.0, 19.0, len(normals)), m)
        assert o.normal[0] == pytest.approx([0.0, 0.0, 1.0], abs=TOL) and o.kind[0] == 1
        assert o.count[0] == len(normals) and math.isnan(o.sin_alpha[0])
    rng = np.random.Generator(np.random.PCG64(6))
    normals = fm.plane_normal(rng.uniform(20.0, 60.0, 9), rng.uniform(100.0, 160.0, 9))
    m = co.Measurements(normals, np.full(9, 0.5), np.zeros(9, dtype=np.uint8))
    o = co.assign([3, 3], [0.0, 4.5], [20.0, 20.0], np.full(9, 3), np.arange(9.0) + 1.0, m)
    assert o.normal[0] == pytest.approx(principal(normals), abs=1e-10)
    assert o.normal[1] == pytest.approx(principal(normals[4:]), abs=1e-10)  # the measurements at 5 … 9 m
    assert float(o.normal[0] @ fm.plane_normal(40.0, 130.0)) > 0.9  # near the mean plane of the cloud


def test_interval_without_measurements_takes_the_nearest_one() -> None:
    """Без замера — ближайший пересчитанный замер той же скважины не дальше d_к по стволу от ближайшего
    конца интервала (равные расстояния — верхний); a measurement that was not turned is never «the nearest»."""
    m_hole, m = hole_7()
    rows = [
        (8.0001, 10.0, 4),  # 7.5 above (0.5 m)
        (20.0, 22.0, 5),  # 7.5 is 12.5 m above — too far; 26 is 4 m below
        (15.0, 16.5, 4),  # 7.5 m above, 9.5 m below
        (16.5, 17.0, 4),  # 9 m both ways: the upper one
        (16.6, 17.0, 5),  # 9.1 m above, 9 m below
        (36.0, 38.0, 5),  # exactly d_к = 10 m below 26
        (36.0000005, 38.0, 5),  # float noise of the depths
        (36.01, 38.0, -1),  # farther than d_к above, 22 m to the one below
        (4.2, 4.8, 1),  # 2.2 m above (2 m) and 2.2 m below (7 m): the one at 5 m is not turned and is skipped
        (40.0, 49.0, -1),  # 14 m above, 11 m below
        (40.0, 51.0, 6),  # 9 m to the one at 60 m
        (70.0, 80.0, 6),  # 10 m below the last usable one (the unused one at 61 m does not exist)
        (70.1, 80.0, -1),
    ]
    frm, to = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])
    o = co.assign(np.full(len(rows), 7), frm, to, m_hole, M_DEPTH, m, d_core=10.0)
    for r, (_, _, pick) in enumerate(rows):
        if pick < 0:
            assert o.kind[r] == co.KIND_NONE == 0 and np.isnan(o.normal[r]).all(), rows[r]
        else:
            assert o.kind[r] == co.KIND_NEAREST == 2 and o.normal[r].tolist() == m.normal[pick].tolist(), rows[r]
    assert np.isnan(o.sin_alpha).all() and not o.flag.any() and not o.count.any()  # TT — by the sum, always
    # d_к = 0: only measurements inside the interval; a wider d_к reaches further
    assert not co.assign(np.full(len(rows), 7), frm, to, m_hole, M_DEPTH, m, d_core=0.0).kind.any()
    wide = co.assign(np.full(len(rows), 7), frm, to, m_hole, M_DEPTH, m, d_core=25.0)
    assert wide.kind.tolist() == [2] * len(rows) and wide.normal[7].tolist() == m.normal[5].tolist()


def test_other_holes_bad_rows_and_any_order_of_the_measurements() -> None:
    m_hole, m = hole_7()
    hole = np.array([7, 8, 9, 7, 7, 7, 9, 11])
    frm = np.array([0.5, 0.5, 0.5, NAN, 3.0, 3.0, 29.0, 0.0])
    to = np.array([1.5, 1.5, 1.5, 1.5, 3.0, 2.0, 31.0, 50.0])
    # the hole 9 has one measurement of its own, at 30 m, which was not turned (a vertical hole); 11 — none
    v = co.measurements(straight_hole(), [1], [30.0], [35.0], [0.0])
    assert v.state.tolist() == [co.STATE_LOCAL]
    all_hole = np.concatenate([m_hole, [9]])
    all_depth = np.concatenate([M_DEPTH, [30.0]])
    both = co.Measurements(np.vstack([m.normal, v.normal]), np.concatenate([m.sin_alpha, v.sin_alpha]),
                           np.concatenate([m.state, v.state]))  # fmt: skip
    o = co.assign(hole, frm, to, all_hole, all_depth, both)
    assert o.kind.tolist() == [1, 0, 0, 0, 0, 0, 1, 0]  # rows without depths or with TO ≤ FROM get nothing
    assert o.sin_alpha[0] == pytest.approx(sin_of(1.0), abs=TOL)
    assert o.sin_alpha[6] == pytest.approx(math.sin(math.radians(35.0)), abs=TOL)
    assert o.flag.tolist() == [0, 0, 0, 0, 0, 0, 4, 0] and np.isnan(o.normal[6]).all()
    # the hole 9 at 0.5–1.5 m: its only measurement is 27.5 m away and is not turned anyway
    assert co.assign([9], [0.5], [1.5], all_hole, all_depth, both, d_core=100.0).kind.tolist() == [0]
    # the measurements in another order — the same result
    order = np.random.Generator(np.random.PCG64(8)).permutation(9)
    shuffled = co.Measurements(both.normal[order], both.sin_alpha[order], both.state[order])
    s = co.assign(hole, frm, to, all_hole[order], all_depth[order], shuffled)
    for name in ("kind", "normal", "sin_alpha", "flag", "count"):
        assert getattr(s, name).tobytes() == getattr(o, name).tobytes(), name
    # no measurements at all, no rows at all
    none = co.Measurements(np.zeros((0, 3)), np.zeros(0), np.zeros(0, dtype=np.uint8))
    assert not co.assign(hole, frm, to, [], [], none).kind.any()
    empty = co.assign([], [], [], all_hole, all_depth, both)
    assert empty.kind.shape == (0,) and empty.normal.shape == (0, 3)


def test_several_unturned_measurements_give_the_mean_sine() -> None:
    """An interval whose measurements were all left in the core frame: TT = l·(mean sin α_к), bit 4; one that
    has a turned measurement as well uses the turned ones only."""
    normal = np.full((3, 3), NAN)
    normal[2] = fm.plane_normal(25.0, 300.0)
    m = co.Measurements(normal, np.array([0.5, 0.7, 0.9]), np.array([1, 1, 0], dtype=np.uint8))
    o = co.assign([1, 1], [0.0, 0.0], [3.0, 10.0], [1, 1, 1], [1.0, 2.0, 5.0], m)
    assert o.kind.tolist() == [1, 1] and o.count.tolist() == [2, 3] and o.flag.tolist() == [4, 0]
    assert o.sin_alpha[0] == pytest.approx(0.6, abs=TOL) and np.isnan(o.normal[0]).all()
    assert o.sin_alpha[1] == 0.9 and o.normal[1].tolist() == normal[2].tolist()


def test_bad_input() -> None:
    m_hole, m = hole_7()
    for d_core in (-0.1, 1000.5, NAN):
        with pytest.raises(CompositeError) as err:
            co.assign([7], [0.0], [1.0], m_hole, M_DEPTH, m, d_core=d_core)
        assert err.value.code == "bad_param" and err.value.param == "d_core" and err.value.hint
    with pytest.raises(CompositeError) as err:
        co.measurements(straight_hole(), [0], [1.0], [50.0], [10.0], line="x")
    assert err.value.code == "bad_param" and err.value.param == "line"
    with pytest.raises(ValueError, match="one length"):
        co.assign([7, 7], [0.0], [1.0], m_hole, M_DEPTH, m)
    with pytest.raises(ValueError, match="one length"):
        co.assign([7], [0.0], [1.0], m_hole[:3], M_DEPTH, m)
    with pytest.raises(ValueError, match="one length"):
        co.measurements(straight_hole(), [0, 0], [1.0], [50.0], [10.0])
    with pytest.raises(TypeError, match="integer"):
        co.assign([7.5], [0.0], [1.0], m_hole, M_DEPTH, m)
    assert (co.D_CORE_DEFAULT, co.D_CORE_RANGE, co.LINES) == (10.0, (0.0, 1000.0), ("boh", "toh"))


# --- strips and threads --------------------------------------------------------------------------------------


def digest(o: co.CoreOrientation) -> str:
    h = hashlib.sha256()
    for a in (o.kind, o.normal, o.sin_alpha, o.flag, o.count):
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def test_one_hash_with_1_2_4_8_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Результат побитно одинаков при 1, 2, 4, 8 потоках (ТЗ 8.0.2) and does not depend on the strips."""
    rng = np.random.Generator(np.random.PCG64(10))
    n_holes, per_hole, per_core = 3000, 100, 12
    h = np.arange(n_holes)
    traj = desurvey(
        Collars(h, np.where(h % 97 == 5, NAN, rng.uniform(0.0, 3000.0, n_holes)), rng.uniform(0.0, 3000.0, n_holes),
                np.zeros(n_holes), np.full(n_holes, 120.0), rng.uniform(0.0, 360.0, n_holes),
                np.where(h % 13 == 0, -90.0, rng.uniform(-88.0, -40.0, n_holes))),
        n_holes=n_holes,
    )  # fmt: skip
    keys = 1000 + 3 * h  # the ids of the holes: not their dense indices
    cored = h[h % 4 != 1]  # every fourth hole has no oriented core
    m_dense = np.repeat(cored, per_core)
    m_depth = np.round(rng.uniform(0.0, 110.0, len(m_dense)), 1)
    m_alpha = rng.uniform(0.0, 90.0, len(m_dense))
    m_alpha[rng.random(len(m_dense)) < 0.03] = NAN
    meas = co.measurements(traj, m_dense, m_depth, m_alpha, rng.uniform(0.0, 360.0, len(m_dense)), threads=2)
    assert meas.n_ok > 0 and meas.n_local > 0 and meas.n_unused > 0
    hole = np.repeat(keys, per_hole)
    frm = np.tile(np.arange(per_hole, dtype=np.float64), n_holes)
    to = frm + rng.choice([1.0, 1.0, 1.0, 4.0], len(hole))
    frm[rng.random(len(hole)) < 0.002] = NAN
    assert len(hole) > 4 * dr.STRIP_ROWS
    runs = {k: co.assign(hole, frm, to, keys[m_dense], m_depth, meas, d_core=3.0, threads=k) for k in (1, 2, 4, 8)}
    hashes = {k: digest(o) for k, o in runs.items()}
    assert len(set(hashes.values())) == 1, hashes
    o = runs[1]
    assert {int(v) for v in np.unique(o.kind)} == {0, 1, 2} and {int(v) for v in np.unique(o.flag)} == {0, 4}
    assert int((o.count > 1).sum()) > 100  # intervals with several measurements: the principal vector
    monkeypatch.setattr(dr, "STRIP_ROWS", 1000)
    assert digest(co.assign(hole, frm, to, keys[m_dense], m_depth, meas, d_core=3.0, threads=3)) == hashes[1]
    order = np.random.Generator(np.random.PCG64(1)).permutation(len(hole))
    shuffled = co.assign(hole[order], frm[order], to[order], keys[m_dense], m_depth, meas, d_core=3.0, threads=2)
    assert shuffled.normal.tobytes() == o.normal[order].tobytes() and shuffled.kind.tobytes() == o.kind[order].tobytes()


def test_tick_reports_progress_and_cancels() -> None:
    m_hole, m = hole_7()
    n = 3 * dr.STRIP_ROWS + 5
    frm = np.arange(n, dtype=np.float64) % 90.0
    seen: list[float] = []
    co.assign(np.full(n, 7), frm, frm + 1.0, m_hole, M_DEPTH, m, threads=2, tick=seen.append)
    assert len(seen) >= 4 and seen == sorted(seen) and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    def cancel(fraction: float) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        co.assign(np.full(n, 7), frm, frm + 1.0, m_hole, M_DEPTH, m, threads=2, tick=cancel)


def test_warm_compiles_the_kernels() -> None:
    co.warm()
