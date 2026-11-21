"""True thickness, the direction of a hole and the sum along an interval (ТЗ 8.5.1; ADR 0021 §3): Т8.5-9,
Т8.5-10; the direction is the derivative of `Trajectories.positions` for the four desurvey methods and for
stations with coordinates; the sum against |n·ΔP| on an arc; flags; one hash with 1, 2, 4 and 8 threads.

Tolerances: formulas — 1e−12·l; arcs — 1e−4·l (Т8.5-10; measured 7e−7·l with parts of 1 m)."""

from __future__ import annotations

import hashlib
import math

import numpy as np
import pytest

from bf_core.composites.intervals import CompositeError
from bf_core.drillholes.desurvey import METHODS, Collars, Surveys, Trajectories, desurvey
from bf_core.intercepts import directions as dr
from bf_core.intercepts import formulas as fm

NAN = math.nan
TOL = 1e-12
UP = np.array([0.0, 0.0, 1.0])


def one_hole(
    depth: list[float], azimuth: list[float], dip: list[float], *, td: float = NAN, method: str = "minimum_curvature"
) -> Trajectories:
    """One hole with its collar at the origin and the survey stations given."""
    return desurvey(Collars([0], [0.0], [0.0], [0.0], [td]), Surveys([0] * len(depth), depth, azimuth, dip),
                    method=method)  # fmt: skip


def quarter_circle() -> Trajectories:
    """300 m: vertical at the collar, level to the north at the bottom — an arc of radius 600 / π."""
    return one_hole([0.0, 300.0], [0.0, 0.0], [-90.0, 0.0])


def cascade(terms: list[float]) -> float:
    """The pairwise sum the kernel takes: terms are combined as a binary counter, the rest is folded from the
    last block to the first."""
    stack: list[float] = []
    for j, v in enumerate(terms):
        m = j + 1
        while (m & 1) == 0:
            v = stack.pop() + v
            m >>= 1
        stack.append(v)
    total = stack.pop()
    while stack:
        total = stack.pop() + total
    return total


def digest(t: dr.Thickness) -> str:
    h = hashlib.sha256()
    for a in (t.tt, t.flag, t.theta, t.azimuth):
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


# --- the direction at a depth --------------------------------------------------------------------------------


def test_t8_5_9_direction_between_two_stations() -> None:
    """Т8.5-9. t1 вниз, t2 горизонтально на север, f = 0,5: θ = 45°, Aс = 0°."""
    t = dr.tangent([0.0, 0.0, -1.0], [0.0, 1.0, 0.0], 0.5)
    theta, azimuth = fm.hole_angles(t)
    assert float(theta) == pytest.approx(45.0, abs=TOL) and float(azimuth) == pytest.approx(0.0, abs=TOL)
    assert float(np.linalg.norm(t)) == pytest.approx(1.0, abs=1e-15)
    # the same on a trajectory: stations at 0 and 300 m, the depth of 150 m
    d = dr.directions(quarter_circle(), [0], [150.0])
    theta, azimuth = fm.hole_angles(d[0])
    assert float(theta) == pytest.approx(45.0, abs=TOL) and float(azimuth) == pytest.approx(0.0, abs=1e-9)
    # the ends of the course: f = 0 — t1, f = 1 — t2; any share — a unit vector in the plane of t1 and t2
    assert dr.tangent([0.0, 0.0, -1.0], [0.0, 1.0, 0.0], 0.0) == pytest.approx([0.0, 0.0, -1.0], abs=TOL)
    assert dr.tangent([0.0, 0.0, -1.0], [0.0, 1.0, 0.0], 1.0) == pytest.approx([0.0, 1.0, 0.0], abs=TOL)
    for f in (0.1, 1.0 / 3.0, 0.9):
        t = dr.tangent([0.0, 0.0, -1.0], [0.0, 1.0, 0.0], f)
        assert t == pytest.approx([0.0, math.sin(f * math.pi / 2), -math.cos(f * math.pi / 2)], abs=TOL)
    # ψ < 1e−9: t(f) = t1
    near = fm.hole_direction(30.0 + 1e-9, 40.0)
    assert dr.tangent(fm.hole_direction(30.0, 40.0), near, 0.7).tolist() == fm.hole_direction(30.0, 40.0).tolist()


def random_hole(rng: np.random.Generator, method: str, *, first_vertical: bool = False) -> Trajectories:
    n = 6
    depth = np.arange(n) * 30.0
    azimuth = (rng.uniform(0.0, 360.0) + np.cumsum(rng.uniform(-25.0, 25.0, n))) % 360.0
    dip = np.clip(rng.uniform(-85.0, -30.0) + np.cumsum(rng.uniform(-8.0, 12.0, n)), -90.0, 20.0)
    if first_vertical:
        dip[0] = -90.0
    return one_hole(depth.tolist(), azimuth.tolist(), dip.tolist(), td=170.0, method=method)


def derivative(traj: Trajectories, depth: np.ndarray, h: float = 1e-3) -> np.ndarray:
    """dP/dD by the central difference of `positions`."""
    hole = np.zeros(len(depth), dtype=np.int64)
    return (traj.positions(hole, depth + h) - traj.positions(hole, depth - h)) / (2.0 * h)


@pytest.mark.parametrize("method", METHODS)
def test_direction_is_the_derivative_of_positions(method: str) -> None:
    """Касательная к той кривой, которую строит метод траектории: d = dP/dD, a unit vector."""
    rng = np.random.Generator(np.random.PCG64(5))
    for k in range(8):
        traj = random_hole(rng, method, first_vertical=k % 4 == 3)
        depth = rng.uniform(0.5, 169.5, 400)
        # away from the corners of the path: stations and, for the balanced tangent, the middles of the courses
        depth = depth[np.abs((depth + 7.5) % 15.0 - 7.5) > 0.01]
        d = dr.directions(traj, np.zeros(len(depth), dtype=np.int64), depth)
        fd = derivative(traj, depth)
        assert float(np.abs(d - fd).max()) < 1e-8, method
        assert float(np.abs(np.linalg.norm(d, axis=1) - 1.0).max()) < 1e-15


@pytest.mark.parametrize("method", METHODS)
def test_stations_with_coordinates_give_the_chord(method: str) -> None:
    """Между станциями с координатами (канава) — единичный вектор хорды, whatever the method and whatever
    the depths say about the length of the segment."""
    xyz = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, -1.0], [10.0, 20.0, -1.5], [4.0, 28.0, 2.5]])
    true_len = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(xyz, axis=0), axis=1))])
    for md in (true_len, np.array([0.0, 9.0, 31.0, 40.0])):  # the second: depths that are not the 3D length
        traj = desurvey(
            Collars([0], [0.0], [0.0], [0.0], [float(md[-1])]),
            Surveys([0] * 4, md, [NAN] * 4, [NAN] * 4, xyz[:, 0], xyz[:, 1], xyz[:, 2]),
            method=method,
        )
        for s in range(3):
            chord = (xyz[s + 1] - xyz[s]) / np.linalg.norm(xyz[s + 1] - xyz[s])
            depth = np.linspace(md[s], md[s + 1], 7)[1:-1]
            d = dr.directions(traj, np.zeros(5, dtype=np.int64), depth)
            assert float(np.abs(d - chord).max()) < 1e-15
            fd = derivative(traj, depth, 1e-4)
            assert float(np.abs(fd / np.linalg.norm(fd, axis=1)[:, None] - chord).max()) < 1e-9
        # a vertex belongs to the segment below it (as `positions` has it); the last one — to the one above
        d = dr.directions(traj, [0, 0], [md[1], md[3]])
        assert d[0] == pytest.approx((xyz[2] - xyz[1]) / np.linalg.norm(xyz[2] - xyz[1]), abs=1e-15)
        assert d[1] == pytest.approx((xyz[3] - xyz[2]) / np.linalg.norm(xyz[3] - xyz[2]), abs=1e-15)


def test_direction_below_the_last_station_above_the_collar_and_without_a_trajectory() -> None:
    """Ниже последней станции — её направление; depths are clipped to the hole as `positions` clips them;
    a hole without a trajectory, an unknown hole and a missing depth — NaN, never a zero."""
    traj = desurvey(
        Collars([0, 1, 2], [0.0, NAN, 5.0], [0.0, 0.0, 5.0], [0.0, 0.0, 5.0], [NAN, 50.0, NAN], [NAN, 0.0, 77.0],
                [NAN, -60.0, -50.0]),
        Surveys([0, 0], [0.0, 100.0], [0.0, 90.0], [-90.0, -45.0]),
        n_holes=3,
    )  # fmt: skip
    last = fm.hole_direction(45.0, 90.0)
    d = dr.directions(traj, [0, 0, 0, 0], [100.0, 250.0, -5.0, 0.0])
    assert d[0] == pytest.approx(last, abs=TOL) and d[1] == pytest.approx(last, abs=TOL)
    assert d[2] == pytest.approx([0.0, 0.0, -1.0], abs=TOL) and d[3] == pytest.approx([0.0, 0.0, -1.0], abs=TOL)
    # the hole 2 has one station (the collar angles) and no TD: its direction at any depth
    d = dr.directions(traj, [2, 2], [0.0, 30.0])
    assert d[0] == pytest.approx(fm.hole_direction(40.0, 77.0), abs=TOL) and d[0].tolist() == d[1].tolist()
    # no collar coordinates (1), not in the set (−1, 3), a missing depth
    d = dr.directions(traj, [1, -1, 3, 0], [10.0, 10.0, 10.0, NAN])
    assert np.isnan(d).all()


def test_segment_between_two_stations_at_one_point_has_no_direction() -> None:
    """Two stations with the same coordinates and different depths: the path stands still there — no
    direction, TT is missing (bit 8), never a zero; the segments around it are as usual."""
    traj = desurvey(
        Collars([0], [0.0], [0.0], [0.0], [30.0]),
        Surveys([0] * 4, [0.0, 10.0, 20.0, 30.0], [NAN] * 4, [NAN] * 4, [0.0, 10.0, 10.0, 10.0], [0.0] * 4,
                [0.0, 0.0, 0.0, -10.0]),
    )  # fmt: skip
    d = dr.directions(traj, [0, 0, 0], [5.0, 15.0, 25.0])
    assert d[0].tolist() == [1.0, 0.0, 0.0] and np.isnan(d[1]).all() and d[2].tolist() == [0.0, 0.0, -1.0]
    t = dr.thickness(traj, [0, 0, 0, 0], [2.0, 12.0, 22.0, 8.0], [6.0, 16.0, 26.0, 12.0], fm.plane_normal(90.0, 90.0))
    assert t.tt[0] == 4.0 and t.tt[2] == pytest.approx(0.0, abs=TOL) and np.isnan(t.tt[[1, 3]]).all()
    assert t.flag.tolist() == [0, 8, 1, 8]
    assert np.isnan(t.theta[1]) and np.isnan(t.azimuth[1]) and float(t.theta[0]) == pytest.approx(90.0, abs=TOL)


def test_reversed_course_is_two_straight_halves() -> None:
    """Разворот направления (t2 = −t1): t1 в верхней половине, t2 в нижней, как `desurvey._delta`."""
    traj = one_hole([0.0, 40.0], [0.0, 0.0], [-90.0, 90.0])
    d = dr.directions(traj, [0, 0, 0, 0], [5.0, 19.9, 20.1, 39.0])
    assert d[:2] == pytest.approx(np.array([[0.0, 0.0, -1.0]] * 2), abs=TOL)
    assert d[2:] == pytest.approx(np.array([[0.0, 0.0, 1.0]] * 2), abs=TOL)
    assert float(np.abs(d - derivative(traj, np.array([5.0, 19.9, 20.1, 39.0]))).max()) < 1e-9


# --- the sum along an interval -------------------------------------------------------------------------------


@pytest.mark.parametrize("step", [0.1, 1.0, 10.0])
def test_straight_hole_is_the_plain_formula(step: float) -> None:
    """Для прямого участка сумма по частям равна l·|n·d| (1e−12·l), whatever the part of a hole."""
    traj = desurvey(Collars([0], [500_000.0], [7_000_000.0], [300.0], [200.0], [250.0], [-60.0]))
    rng = np.random.Generator(np.random.PCG64(2))
    frm = rng.uniform(0.0, 150.0, 300)
    to = frm + rng.uniform(0.01, 45.0, 300)
    n = fm.plane_normal(rng.uniform(0.0, 90.0, 300), rng.uniform(0.0, 360.0, 300))
    t = dr.thickness(traj, np.zeros(300, dtype=np.int64), frm, to, n, step=step)
    want = fm.tt_vector(to - frm, n, fm.hole_direction(30.0, 250.0))
    assert float((np.abs(t.tt - want) / (to - frm)).max()) <= TOL
    assert t.theta == pytest.approx(np.full(300, 30.0), abs=TOL)
    assert t.azimuth == pytest.approx(np.full(300, 250.0), abs=TOL)
    assert t.tt.dtype == np.float64 and t.flag.dtype == np.uint8 and (t.n_bad, t.n_capped) == (0, 0)
    assert np.array_equal(t.flag, np.where(fm.is_parallel(t.tt, to - frm), 1, 0))
    # Т8.5-5 through the kernel: α = 45°, Aп = 120°, θ = 30°, Aс = 250°
    one = dr.thickness(traj, [0], [10.0], [30.0], fm.plane_normal(45.0, 120.0), step=step)
    assert float(one.tt[0]) == pytest.approx(0.839632 * 20.0, abs=1e-6 * 20.0)


def test_t8_5_10_level_layer_curved_hole() -> None:
    """Т8.5-10. Горизонтальный пласт, искривлённый ствол без подъёмов: Σ l_j·|n·d_j| = вертикальному размаху
    (1e−4·l)."""
    traj = quarter_circle()
    radius = 600.0 / math.pi
    whole = dr.thickness(traj, [0], [0.0], [300.0], UP)  # parts of 1 m
    assert float(whole.tt[0]) == pytest.approx(radius, abs=1e-4 * 300.0)
    assert abs(float(whole.tt[0]) - radius) < 1e-6 * 300.0  # ADR 0021, находка 12: 7e−7·l
    z = traj.positions([0, 0], [0.0, 300.0])[:, 2]
    assert float(z[0] - z[1]) == pytest.approx(radius, abs=1e-9)
    # every interval of 10 m — its own vertical range
    frm = np.arange(0.0, 300.0, 10.0)
    hole = np.zeros(30, dtype=np.int64)
    t = dr.thickness(traj, hole, frm, frm + 10.0, np.tile(UP, (30, 1)))
    drop = traj.positions(hole, frm)[:, 2] - traj.positions(hole, frm + 10.0)[:, 2]
    assert float(np.abs(t.tt - drop).max()) <= 1e-4 * 10.0
    assert float(t.tt.sum()) == pytest.approx(radius, abs=1e-4 * 300.0)
    # a hole of several courses that never rises: a random walk of the dip between −85° and −5°
    rng = np.random.Generator(np.random.PCG64(11))
    for method in METHODS:
        dip = np.clip(-80.0 + np.cumsum(rng.uniform(-3.0, 14.0, 9)), -85.0, -5.0)
        azimuth = (200.0 + np.cumsum(rng.uniform(-10.0, 10.0, 9))) % 360.0
        traj = one_hole((np.arange(9) * 40.0).tolist(), azimuth.tolist(), dip.tolist(), td=350.0, method=method)
        z = traj.positions([0, 0], [3.0, 347.0])[:, 2]
        t = dr.thickness(traj, [0], [3.0], [347.0], UP)
        assert float(t.tt[0]) == pytest.approx(float(z[0] - z[1]), abs=1e-4 * 344.0), method


@pytest.mark.parametrize(("step", "bound"), [(1.0, 2e-6), (0.1, 2e-8)])
def test_sum_against_the_chord_on_an_arc(step: float, bound: float) -> None:
    """Сверка с |n·ΔP|: where n·d keeps its sign along the interval, the sum is the projection of the chord;
    the error of a part is ψ²/24 (ψ — the turn of the hole on the part), 100 times less with parts of 0.1 m."""
    traj = quarter_circle()
    n = fm.plane_normal(40.0, 110.0)
    rng = np.random.Generator(np.random.PCG64(4))
    frm = rng.uniform(0.0, 280.0, 200)
    to = frm + rng.uniform(1.0, 20.0, 200)
    hole = np.zeros(200, dtype=np.int64)
    ends = (dr.directions(traj, hole, frm) @ n) * (dr.directions(traj, hole, to) @ n)
    keep = ends > 0.0  # on an arc n·d is a cosine: the same sign at both ends — the same sign between them
    assert 150 < int(keep.sum())
    chord = np.abs((traj.positions(hole, to) - traj.positions(hole, frm)) @ n)[keep]
    t = dr.thickness(traj, hole[keep], frm[keep], to[keep], np.tile(n, (int(keep.sum()), 1)), step=step)
    error = np.abs(t.tt - chord) / (to - frm)[keep]
    assert float(error.max()) <= bound
    assert float(error.max()) > bound / 100.0  # not the chord itself: the sum of the ТЗ, part by part


def test_parts_are_equal_not_longer_than_the_step_and_summed_pairwise() -> None:
    """k = ⌈(TO − FROM) / шаг⌉ равных частей, d_j — в середине части, попарная сумма в порядке частей."""
    traj = quarter_circle()
    n = fm.plane_normal(55.0, 20.0)
    for frm, to, step, k in ((10.0, 20.0, 10.0, 1), (10.0, 20.0, 1.0, 10), (7.25, 9.75, 1.0, 3), (0.0, 37.0, 0.5, 74)):
        part = (to - frm) / k
        mids = frm + (np.arange(k) + 0.5) * part
        d = dr.directions(traj, np.zeros(k, dtype=np.int64), mids)
        terms = [abs(n[0] * v[0] + n[1] * v[1] + n[2] * v[2]) for v in d.tolist()]
        t = dr.thickness(traj, [0], [frm], [to], n, step=step)
        assert float(t.tt[0]) == part * cascade(terms), (frm, to, step)  # bit for bit
        middle = dr.directions(traj, [0], [0.5 * (frm + to)])
        theta, azimuth = fm.hole_angles(middle[0])
        assert float(t.theta[0]) == pytest.approx(float(theta), abs=TOL)  # HOLE_THETA, HOLE_AZ — at the middle
        assert float(t.azimuth[0]) == pytest.approx(float(azimuth), abs=TOL)


def test_very_long_interval_takes_a_larger_step() -> None:
    """Частей не больше 100 000 на интервал: шаг для него увеличивается, the interval is counted."""
    traj = desurvey(Collars([0], [0.0], [0.0], [0.0], [30_000.0], [10.0], [-70.0]))
    n = fm.plane_normal(20.0, 300.0)
    t = dr.thickness(traj, [0, 0], [0.0, 0.0], [25_000.0, 9_000.0], np.tile(n, (2, 1)), step=0.1)
    want = fm.tt_vector(np.array([25_000.0, 9_000.0]), n, fm.hole_direction(20.0, 10.0))
    assert t.n_capped == 1 and dr.MAX_PARTS == 100_000
    assert t.tt == pytest.approx(want, rel=TOL)


# --- flags and missing values --------------------------------------------------------------------------------


def test_t8_5_3_flag_of_a_hole_along_the_plane() -> None:
    """Т8.5-3 through the kernel: α = 30°, θ = 60°, Aс = Aп: m = 0, TT_FLAG = 1; TT is written as it came out."""
    traj = desurvey(Collars([0], [0.0], [0.0], [0.0], [100.0], [120.0], [-30.0]))
    t = dr.thickness(traj, [0], [10.0], [15.0], fm.plane_normal(30.0, 120.0))
    assert float(t.tt[0]) == pytest.approx(0.0, abs=TOL * 5.0) and int(t.flag[0]) == fm.FLAG_PARALLEL == 1
    assert float(t.theta[0]) == pytest.approx(60.0, abs=TOL) and float(t.azimuth[0]) == pytest.approx(120.0, abs=TOL)


def test_parallel_flag_follows_eps() -> None:
    """TT / l < ε_в → бит 1; the threshold is a parameter (0.5 — the 30° of the ГКЗ practice)."""
    traj = desurvey(Collars([0], [0.0], [0.0], [0.0], [100.0]))  # vertical: |n·d| = cos α
    alpha = np.array([0.0, 59.0, 72.0, 84.0, 85.0, 90.0])  # cos: 1; 0.515; 0.309; 0.1045; 0.0872; 0
    n = fm.plane_normal(alpha, 45.0)
    args = (traj, np.zeros(6, dtype=np.int64), np.full(6, 2.0), np.full(6, 6.0), n)
    assert dr.thickness(*args).flag.tolist() == [0, 0, 0, 0, 1, 1]
    assert dr.thickness(*args, eps=0.5).flag.tolist() == [0, 0, 1, 1, 1, 1]
    assert dr.thickness(*args, eps=0.01).flag.tolist() == [0, 0, 0, 0, 0, 1]
    assert dr.thickness(*args).tt == pytest.approx(4.0 * np.cos(np.radians(alpha)), abs=TOL * 4.0)


def test_missing_values_stay_missing() -> None:
    """Нет ориентировки — TT пусто и бит 2; нет траектории — TT и углы ствола пусты, бит 8; a row without
    depths or with TO ≤ FROM is left out and counted. Never a zero."""
    traj = desurvey(Collars([0, 1], [0.0, NAN], [0.0, 0.0], [0.0, 0.0], [50.0, 50.0]), n_holes=2)
    n = np.tile(UP, (9, 1))
    n[1] = NAN  # no orientation
    n[5, 1] = NAN  # no orientation and no trajectory
    hole = [0, 0, 1, -1, 2, 1, 0, 0, 0]
    frm = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, NAN, 5.0, 4.0]
    to = [1.0, 2.0, 1.0, 1.0, 1.0, 1.0, 3.0, 5.0, 3.0]
    t = dr.thickness(traj, hole, frm, to, n)
    assert t.tt[0] == 1.0 and np.isnan(t.tt[1:]).all()
    assert t.flag.tolist() == [0, 2, 8, 8, 8, 10, 0, 0, 0]
    assert (fm.FLAG_NO_ORIENTATION, fm.FLAG_NO_PATH) == (2, 8)
    assert t.theta[:2] == pytest.approx([0.0, 0.0], abs=TOL) and t.azimuth[:2].tolist() == [0.0, 0.0]
    assert np.isnan(t.theta[2:7]).all() and np.isnan(t.azimuth[2:7]).all()
    assert t.theta[7:] == pytest.approx([0.0, 0.0], abs=TOL)  # depths are there: the direction at their middle
    assert t.n_bad == 3 and t.n_capped == 0
    # one normal for all the rows
    same = dr.thickness(traj, hole, frm, to, UP)
    assert same.flag.tolist() == [0, 0, 8, 8, 8, 8, 0, 0, 0] and same.tt[:2].tolist() == [1.0, 1.0]
    # no rows
    empty = dr.thickness(traj, [], [], [], np.zeros((0, 3)))
    assert empty.tt.shape == (0,) and empty.flag.shape == (0,) and empty.n_bad == 0


def test_bad_input() -> None:
    traj = desurvey(Collars([0], [0.0], [0.0], [0.0], [50.0]))
    for step in (0.05, 10.5, 0.0, -1.0, NAN):
        with pytest.raises(CompositeError) as err:
            dr.thickness(traj, [0], [0.0], [1.0], UP, step=step)
        assert err.value.code == "bad_param" and err.value.param == "step" and err.value.hint
    for eps in (0.005, 0.51, NAN):
        with pytest.raises(CompositeError) as err:
            dr.thickness(traj, [0], [0.0], [1.0], UP, eps=eps)
        assert err.value.code == "bad_param" and err.value.param == "eps" and err.value.hint
    with pytest.raises(ValueError, match="one length"):
        dr.thickness(traj, [0, 0], [0.0], [1.0], UP)
    with pytest.raises(ValueError, match="normal"):
        dr.thickness(traj, [0], [0.0], [1.0], np.zeros((2, 3)))
    with pytest.raises(TypeError, match="integer"):
        dr.thickness(traj, [0.5], [0.0], [1.0], UP)
    with pytest.raises(ValueError, match="one length"):
        dr.directions(traj, [0, 0], [1.0])


# --- strips and threads --------------------------------------------------------------------------------------


def many_holes(seed: int = 9, n_holes: int = 3000, per_hole: int = 100):  # type: ignore[no-untyped-def]
    """A set of curved holes with what a real one has: holes without a collar, without surveys, rows of an
    unknown hole, rows without depths, intervals without an orientation, holes along the plane."""
    rng = np.random.Generator(np.random.PCG64(seed))
    h = np.arange(n_holes)
    x = np.where(h % 211 == 5, NAN, 500_000.0 + rng.uniform(0.0, 5000.0, n_holes))
    collars = Collars(h, x, 7_000_000.0 + rng.uniform(0.0, 5000.0, n_holes), rng.uniform(200.0, 400.0, n_holes),
                      np.where(h % 101 == 3, NAN, 110.0), rng.uniform(0.0, 360.0, n_holes),
                      rng.uniform(-90.0, -40.0, n_holes))  # fmt: skip
    s_hole = np.repeat(h[h % 50 != 9], 4)
    s_depth = np.tile(np.arange(4) * 30.0, len(s_hole) // 4)
    s_az = (rng.uniform(0.0, 360.0, n_holes)[s_hole] + s_depth * 0.3) % 360.0
    s_dip = np.minimum(-5.0, rng.uniform(-90.0, -45.0, n_holes)[s_hole] + s_depth * 0.2)
    surveys = Surveys(s_hole, s_depth, s_az, s_dip)
    hole = np.repeat(h, per_hole)
    frm = np.tile(np.arange(per_hole, dtype=np.float64), n_holes) + rng.integers(0, 5, len(hole)) * 0.05
    to = frm + rng.uniform(0.3, 3.5, len(hole))
    normal = fm.plane_normal(rng.uniform(0.0, 90.0, len(hole)), rng.uniform(0.0, 360.0, len(hole)))
    normal[rng.random(len(hole)) < 0.01] = NAN
    frm[rng.random(len(hole)) < 0.002] = NAN
    hole[rng.random(len(hole)) < 0.002] = -1
    return collars, surveys, hole, frm, to, normal


@pytest.mark.parametrize("method", ["minimum_curvature", "balanced_tangent"])
def test_one_hash_with_1_2_4_8_threads(method: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Результат побитно одинаков при 1, 2, 4, 8 потоках (ТЗ 8.0.2) and does not depend on the strips."""
    collars, surveys, hole, frm, to, normal = many_holes()
    traj = desurvey(collars, surveys, method=method)
    assert len(hole) > 4 * dr.STRIP_ROWS  # several strips: threads have something to share
    runs = {k: dr.thickness(traj, hole, frm, to, normal, threads=k) for k in (1, 2, 4, 8)}
    hashes = {k: digest(t) for k, t in runs.items()}
    assert len(set(hashes.values())) == 1, hashes
    t = runs[1]
    assert {int(v) for v in np.unique(t.flag)} >= {0, 1, 2, 8}
    assert t.n_bad > 0 and all(r.n_bad == t.n_bad and r.n_capped == t.n_capped for r in runs.values())
    assert int(np.isfinite(t.tt).sum()) > 0.9 * len(hole)
    d = {k: dr.directions(traj, hole, 0.5 * (frm + to), threads=k) for k in (1, 8)}
    assert d[1].tobytes() == d[8].tobytes()
    # other strips — the same bits: a row depends on nothing but itself
    monkeypatch.setattr(dr, "STRIP_ROWS", 1000)
    assert digest(dr.thickness(traj, hole, frm, to, normal, threads=3)) == hashes[1]
    # the rows in another order — the same value for every row
    order = np.random.Generator(np.random.PCG64(1)).permutation(len(hole))
    shuffled = dr.thickness(traj, hole[order], frm[order], to[order], normal[order], threads=2)
    assert shuffled.tt.tobytes() == t.tt[order].tobytes() and shuffled.flag.tobytes() == t.flag[order].tobytes()


def test_tick_reports_progress_and_cancels() -> None:
    collars, surveys, hole, frm, to, normal = many_holes(n_holes=2000)
    traj = desurvey(collars, surveys)
    seen: list[float] = []
    dr.thickness(traj, hole, frm, to, normal, threads=2, tick=seen.append)
    assert len(seen) == math.ceil(len(hole) / dr.STRIP_ROWS) and seen == sorted(seen) and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    def cancel(fraction: float) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        dr.thickness(traj, hole, frm, to, normal, threads=2, tick=cancel)
    with pytest.raises(Cancelled):
        dr.directions(traj, hole, frm, threads=2, tick=cancel)


def test_warm_compiles_the_kernels() -> None:
    dr.warm()
