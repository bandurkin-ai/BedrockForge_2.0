"""Composites by benches (ТЗ 8.2.2, 8.2.4; ADR 0018 §6): Т8.2-5 — the length of a composite of a straight hole;
a curved hole — the boundaries are on the levels Z_j; a trench along stations with coordinates; «пологая».

Tolerance: depths and Z of the boundaries — 1e−6 m (8.2.2), checked here to 1e−8; values — 1e−12 relative."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites import benches as cb
from bf_core.composites import intervals as iv
from bf_core.composites.intervals import CompositeError
from bf_core.drillholes.desurvey import METHODS, Collars, Surveys, Trajectories, desurvey

NAN = math.nan
REL = 1e-12
TOL = 1e-8  # m: 100 times tighter than the acceptance


def straight(dip: float = -45.0, z: float = 100.0, depth: float = 60.0, azimuth: float = 30.0) -> Trajectories:
    return desurvey(Collars([0], [1000.0], [2000.0], [z], [depth], [azimuth], [dip]))


def curved(method: str = "minimum_curvature") -> Trajectories:
    """One hole of 400 m: steep, flattening, horizontal at 250 m, rising to 320 m, then down again."""
    depth = [0.0, 50.0, 120.0, 200.0, 250.0, 320.0, 400.0]
    az = [10.0, 14.0, 25.0, 40.0, 55.0, 60.0, 70.0]
    dip = [-80.0, -60.0, -35.0, -10.0, 0.0, 25.0, -40.0]
    sv = Surveys([0] * 7, depth, az, dip)
    return desurvey(Collars([0], [500.0], [700.0], [312.3], [400.0]), sv, method=method)


def samples(depth: float, hole: int = 7, step: float = 1.0) -> tuple[iv.Lines, np.ndarray]:
    """Samples of `step` m from the collar to the depth of a hole; the grade is the number of the sample."""
    n = int(round(depth / step))
    frm = np.arange(n) * step
    lines = iv.build_lines([hole] * n, frm, frm + step)
    return lines, iv.stack_fields(lines, [np.arange(1.0, n + 1.0)])


def z_at(traj: Trajectories, depth: np.ndarray, hole: int = 0) -> np.ndarray:
    return traj.positions(np.full(np.shape(depth), hole, dtype=np.int64), depth)[:, 2]


def brute_crossings(traj: Trajectories, levels: np.ndarray, end: float, hole: int = 0) -> np.ndarray:
    """Depths where the path crosses a level, without the kernel: a scan by 5 mm, then bisection on
    `Trajectories.positions`. A point on a level counts as below it (the rule of `benches`)."""
    d = np.linspace(0.0, end, int(round(end / 0.005)) + 1)
    z = z_at(traj, d, hole)
    out = []
    for level in levels:
        above = z > level
        for i in np.flatnonzero(above[1:] != above[:-1]):
            lo, hi, up = d[i], d[i + 1], above[i]
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                if (z_at(traj, np.array([mid]), hole)[0] > level) == up:
                    lo = mid
                else:
                    hi = mid
            out.append(0.5 * (lo + hi))
    return np.sort(np.array(out))


# --- Т8.2-5 ------------------------------------------------------------------------------------------------


def test_t8_2_5_straight_hole_at_45_degrees() -> None:
    """Т8.2-5. Прямая скважина θ = 45°, H = 10: длина композита 14,1421356 м (1e−6 м)."""
    traj = straight(dip=-45.0, z=100.0, depth=60.0)
    lines, vals = samples(60.0)
    c = cb.composite_by_benches(lines, vals, cb.BenchParams(bench=10.0), traj, [0])
    # levels 100, 90, 80, …: boundaries every 10·√2 m of depth; the tail 56.57–60 is the fifth composite
    assert c.lines.n == 5 and c.report["top"] == 100.0
    assert (np.abs(c.lines.lengths()[:4] - 14.1421356) < 1e-6).all()
    assert c.lines.lengths()[:4] == pytest.approx([10.0 * math.sqrt(2.0)] * 4, abs=TOL)
    assert c.lines.frm[0] == 0.0 and c.lines.to[-1] == 60.0  # exact
    assert z_at(traj, c.lines.to[:4]) == pytest.approx([90.0, 80.0, 70.0, 60.0], abs=TOL)
    assert c.bench.tolist() == [0, 1, 2, 3, 4]
    assert c.crest.tolist() == [100.0, 90.0, 80.0, 70.0, 60.0] and c.floor.tolist() == [90.0, 80.0, 70.0, 60.0, 50.0]
    # X, Y, Z — the middle by length; the hole is steeper than «пологая»
    mid = traj.positions(np.zeros(5, dtype=np.int64), 0.5 * (c.lines.frm + c.lines.to))
    assert np.array_equal(c.xyz, mid) and c.xyz[:4, 2] == pytest.approx([95.0, 85.0, 75.0, 65.0], abs=TOL)
    assert not c.flat.any() and c.stretch.tolist() == [0] * 5
    # the value of a composite: the samples by the length inside it (12.2)
    w = np.minimum(lines.to, c.lines.to[0]) - lines.frm
    w = np.where(w > 0.0, w, 0.0)
    assert c.values.values[0, 0] == pytest.approx(float((w * vals[:, 0]).sum() / w.sum()), rel=REL)
    ln = c.lines.lengths()
    assert (ln * c.values.values[:, 0]).sum() == pytest.approx(vals[:, 0].sum(), rel=REL)  # metal kept


@pytest.mark.parametrize("dip", [-90.0, -60.0, -30.0, -12.0, -11.0])
def test_straight_holes_length_is_height_over_sine(dip: float) -> None:
    traj = straight(dip=dip, z=250.0, depth=200.0)
    lines, vals = samples(200.0)
    c = cb.composite_by_benches(lines, vals, cb.BenchParams(bench=5.0), traj, [0])
    step = 5.0 / math.sin(math.radians(-dip))
    assert c.lines.lengths()[:-1] == pytest.approx([step] * (c.lines.n - 1), abs=TOL)
    # «пологая»: abs(Δz) < 0,2·length, i.e. the sine of the dip below 0.2 (11.5°)
    assert c.flat.tolist() == [math.sin(math.radians(-dip)) < 0.2] * c.lines.n


# --- levels --------------------------------------------------------------------------------------------------


def test_default_top_and_levels() -> None:
    assert cb.default_top(np.array([312.3, 298.0, NAN]), 10.0) == 320.0
    assert cb.default_top(np.array([300.0, 250.0]), 10.0) == 300.0  # a collar on a level: not a bench higher
    assert cb.default_top(np.array([300.0 + 1e-10]), 10.0) == 300.0  # float noise is not a bench either
    assert cb.default_top(np.array([-12.0]), 5.0) == -10.0 and cb.default_top(np.array([NAN]), 5.0) == 0.0
    grid = cb.Levels(300.0, 10.0)
    assert grid.crest(np.array([-1, 0, 3])).tolist() == [310.0, 300.0, 270.0]
    assert grid.floor(np.array([-1, 0, 3])).tolist() == [300.0, 290.0, 260.0]
    listed = cb.Levels(0.0, 0.0, np.array([300.0, 280.0, 275.0]))
    crest, floor = listed.crest(np.array([-1, 0, 2])), listed.floor(np.array([-1, 0, 2]))
    assert math.isnan(crest[0]) and crest[1:].tolist() == [300.0, 275.0]
    assert floor[:2].tolist() == [300.0, 280.0] and math.isnan(floor[2])


def test_params_are_checked() -> None:
    p = cb.BenchParams.of({"bench": "5", "bench_top": None, "p_min": 0.25, "length": 2.0, "unsampled": 0})
    assert (p.bench, p.bench_top, p.bench_levels, p.p_min, p.unsampled) == (5.0, None, None, 0.25, 0.0)
    assert cb.BenchParams.of({}).bench == 10.0 and cb.BenchParams.of({"bench": None}).bench == 10.0
    assert cb.BenchParams.of({"bench_levels": [280, 300.0, 280.0, 275]}).bench_levels == (300.0, 280.0, 275.0)
    for raw, param in (
        ({"bench": 0.5}, "bench"),
        ({"bench": 51}, "bench"),
        ({"bench": "десять"}, "bench"),
        ({"bench_top": "x"}, "bench_top"),
        ({"bench_levels": []}, "bench_levels"),
        ({"bench_levels": [300.0, NAN]}, "bench_levels"),
        ({"bench_levels": 300.0}, "bench_levels"),
        ({"p_min": 1.5}, "p_min"),
    ):
        with pytest.raises(CompositeError) as err:
            cb.BenchParams.of(raw)
        assert err.value.param == param and err.value.code == "bad_param"
        assert err.value.as_dict()["message"] and err.value.as_dict()["hint"]


# --- a curved hole ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_curved_hole_boundaries_are_on_the_levels(method: str) -> None:
    """Искривлённая скважина: Z границ = Z_j; восходящие и горизонтальные участки — уровень пересекается
    не один раз, и каждое пересечение найдено."""
    traj = curved(method)
    lines, vals = samples(400.0)
    c = cb.composite_by_benches(lines, vals, cb.BenchParams(bench=5.0, bench_top=320.0), traj, [0])
    inner = c.lines.to[:-1]
    assert np.array_equal(inner, c.lines.frm[1:]) and c.lines.frm[0] == 0.0 and c.lines.to[-1] == 400.0
    assert (c.lines.lengths() > 1e-6).all()
    z = z_at(traj, inner)
    # every boundary is on a level of the grid 320 − 5·j …
    assert np.abs(z - np.round(z / 5.0) * 5.0).max() < TOL
    # … and they are all the crossings there are, at the same depths
    zs = z_at(traj, np.linspace(0.0, 400.0, 4001))
    levels = np.arange(math.floor(zs.min() / 5.0) * 5.0, zs.max() + 5.0, 5.0)
    brute = brute_crossings(traj, levels, 400.0)
    assert inner.shape == brute.shape and inner.shape[0] > 25
    assert np.abs(inner - brute).max() < TOL
    # the hole comes back up: a level is crossed more than once
    assert np.unique(np.round(z)).shape[0] < z.shape[0]
    # a composite lies in its bench: floor ≤ Z ≤ crest all along (the ends are on the levels)
    for share in (0.0, 0.25, 0.5, 0.75, 1.0):
        zc = z_at(traj, c.lines.frm + share * c.lines.lengths())
        assert (zc <= c.crest + TOL).all() and (zc >= c.floor - TOL).all()
    assert np.array_equal(c.crest - c.floor, np.full(c.lines.n, 5.0))
    # «пологая» by the ends of the composite
    dz = z_at(traj, c.lines.to) - z_at(traj, c.lines.frm)
    assert np.array_equal(c.flat, np.abs(dz) < 0.2 * c.lines.lengths()) and c.flat.any() and not c.flat.all()
    assert c.report["flat"] == int(c.flat.sum()) and any("пологая" in line for line in c.journal)
    # sampled all along: the metal and the length are kept
    assert c.lines.lengths().sum() == pytest.approx(400.0, rel=REL)
    assert (c.lines.lengths() * c.values.values[:, 0]).sum() == pytest.approx(vals[:, 0].sum(), rel=1e-11)


def test_a_list_of_levels() -> None:
    traj = curved()
    lines, vals = samples(400.0)
    listed = [300.0, 262.5, 250.0, 195.0]
    c = cb.composite_by_benches(lines, vals, cb.BenchParams(bench_levels=listed), traj, [0])
    inner = c.lines.to[:-1]
    brute = brute_crossings(traj, np.array(listed), 400.0)
    assert inner.shape == brute.shape and np.abs(inner - brute).max() < TOL
    # the collar (312.3) is above the highest level: bench −1 without a crest; nothing is cut above and below
    assert c.bench[0] == -1 and math.isnan(c.crest[0]) and c.floor[0] == 300.0
    assert c.report["levels"] == listed and "top" not in c.report
    zm = c.xyz[:, 2]
    assert ((zm <= c.crest) | np.isnan(c.crest)).all() and ((zm > c.floor) | np.isnan(c.floor)).all()


# --- on a level --------------------------------------------------------------------------------------------------


def test_collar_and_end_on_a_level_make_no_empty_composite() -> None:
    """A collar exactly on a level, a hole that ends exactly on one, a collar a tenth of a micron above one:
    no composite of zero length, the boundaries are those of the levels in between."""
    lines, vals = samples(30.0)
    for z0 in (300.0, 300.0 + 1e-7, 300.0 - 1e-7):
        traj = desurvey(Collars([0], [0.0], [0.0], [z0], [30.0]))  # vertical, 30 m: ends on the level 270
        c = cb.composite_by_benches(lines, vals, cb.BenchParams(bench=10.0, bench_top=300.0), traj, [0])
        assert c.lines.frm.tolist() == [0.0, pytest.approx(10.0, abs=1e-6), pytest.approx(20.0, abs=1e-6)]
        assert c.lines.to.tolist() == [pytest.approx(10.0, abs=1e-6), pytest.approx(20.0, abs=1e-6), 30.0]
        assert c.bench.tolist() == [0, 1, 2]  # a point on a level belongs to the bench below it


def test_trench_along_stations_with_coordinates() -> None:
    """Канава со станциями: straight between the vertices, linear in depth. Levels 100, 95, 90 (H = 5)."""
    #          0: 103 ↘ 97   (crosses 100 at half-way)
    #         10: 97 ↘ 95    (comes down exactly onto the level 95 at the vertex)
    #         20: 95 → 95    (runs along the level: the bench below it)
    #         30: 95 ↗ 99    (leaves the level upwards: a boundary at the vertex)
    #         40: 99 ↘ 93    (crosses 95 at 46.67 m)
    #         50: 93 ↗ 95    (touches the level 95 from below at the vertex …)
    #         60: 95 ↘ 91    (… and goes back down: no boundary)   70: end
    depth = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0]
    z = [103.0, 97.0, 95.0, 95.0, 99.0, 93.0, 95.0, 91.0]
    x = [10.0 * k for k in range(8)]
    sv = Surveys([0] * 8, depth, [NAN] * 8, [NAN] * 8, x, [0.0] * 8, z)
    traj = desurvey(Collars([0], [0.0], [0.0], [103.0], [70.0]), sv)
    lines, vals = samples(70.0)
    c = cb.composite_by_benches(lines, vals, cb.BenchParams(bench=5.0, bench_top=100.0), traj, [0])
    assert c.lines.frm.tolist() == pytest.approx([0.0, 5.0, 20.0, 30.0, 40.0 + 40.0 / 6.0], abs=TOL)
    assert c.lines.to.tolist() == pytest.approx([5.0, 20.0, 30.0, 40.0 + 40.0 / 6.0, 70.0], abs=TOL)
    assert c.bench.tolist() == [-1, 0, 1, 0, 1]
    assert c.crest.tolist() == [105.0, 100.0, 95.0, 100.0, 95.0]
    # 20–30 runs along the level; 30–46.67 ends at the height it started from; 46.67–70 loses 4 m of 23
    assert c.flat.tolist() == [False, False, True, True, True]
    assert c.xyz[2].tolist() == pytest.approx([25.0, 0.0, 95.0], abs=TOL)  # the middle of 20–30
    assert c.values.values[2, 0] == pytest.approx(25.5, rel=REL) and c.values.count.tolist() == [5, 15, 10, 17, 24]


def test_a_touch_of_a_level_is_not_a_boundary() -> None:
    """A path that comes down to a level exactly at a station and goes back up stays in its bench: one
    composite, not two."""
    sv = Surveys([0] * 3, [0.0, 10.0, 20.0], [NAN] * 3, [NAN] * 3, [0.0, 8.0, 16.0], [0.0] * 3, [106.0, 100.0, 106.0])
    traj = desurvey(Collars([0], [0.0], [0.0], [106.0], [20.0]), sv)
    lines, vals = samples(20.0)
    c = cb.composite_by_benches(lines, vals, cb.BenchParams(bench=10.0, bench_top=100.0), traj, [0])
    assert (c.lines.frm.tolist(), c.lines.to.tolist(), c.bench.tolist()) == ([0.0], [20.0], [-1])
    assert c.flat.tolist() == [True]  # it ends at the height it started from


# --- a set of holes ----------------------------------------------------------------------------------------------


def test_holes_without_a_trajectory_are_left_out_and_counted() -> None:
    # hole 5 — vertical; hole 6 has no collar coordinates; hole 9 is not in the set at all
    traj = desurvey(Collars([0, 1], [0.0, NAN], [0.0, 5.0], [100.0, 100.0], [20.0, 20.0]), n_holes=2)
    frm = np.arange(20.0)
    hole = np.repeat([5, 6, 9], 20)
    lines = iv.build_lines(hole, np.tile(frm, 3), np.tile(frm + 1.0, 3))
    vals = iv.stack_fields(lines, [np.ones(60)])
    c = cb.composite_by_benches(lines, vals, cb.BenchParams(), traj, [0, 1, -1])
    assert c.lines.holes.tolist() == [5] and c.lines.starts.tolist() == [0, 2]
    assert c.no_trajectory.tolist() == [6, 9] and c.report["no_trajectory"] == 2 and c.report["holes"] == 1
    assert any("без траектории — 2" in line for line in c.journal)
    with pytest.raises(ValueError):
        cb.composite_by_benches(lines, vals, cb.BenchParams(), traj, [0, 1])  # not per hole of the lines
    # no hole has a trajectory: an empty result, not an error
    none = cb.composite_by_benches(lines, vals, cb.BenchParams(), traj, [-1, -1, -1])
    assert none.lines.n == 0 and none.xyz.shape == (0, 3) and none.values.values.shape == (0, 1)


def test_values_flags_and_the_end_of_the_hole() -> None:
    traj = straight(dip=-90.0, z=100.0, depth=40.0)
    # samples 0–4 and 12–16 only; the hole is 40 m deep (the trajectory), the density — on the first samples
    lines = iv.build_lines([3] * 8, [0, 1, 2, 3, 12, 13, 14, 15], [1, 2, 3, 4, 13, 14, 15, 16])
    vals = iv.stack_fields(lines, [np.array([1.0, 2.0, 3.0, 4.0, 5.0, NAN, 7.0, 8.0])])
    rho = np.array([2.0, 2.0, 4.0, 4.0, NAN, NAN, NAN, NAN])
    codes = np.array([0, 0, 1, 1, 1, -1, 0, 0], dtype=np.int32)
    p = cb.BenchParams(bench=10.0, p_min=0.3)
    c = cb.composite_by_benches(lines, vals, p, traj, [0], density=rho, texts=[codes])
    assert (c.lines.frm.tolist(), c.lines.to.tolist()) == ([0.0, 10.0, 20.0, 30.0], [10.0, 20.0, 30.0, 40.0])
    assert c.values.values[0, 0] == pytest.approx((2 + 4 + 12 + 16) / 12.0, rel=REL)  # weighted by density
    assert c.values.density[0] == pytest.approx(3.0, rel=REL) and math.isnan(c.values.density[1])
    assert c.values.values[1, 0] == pytest.approx(20.0 / 3.0, rel=REL)  # no density there: w = 1
    assert np.isnan(c.values.values[2:, 0]).all() and c.values.low.tolist() == [False, False, True, True]
    assert c.values.sampled.tolist() == [4.0, 3.0, 0.0, 0.0] and c.values.count.tolist() == [4, 3, 0, 0]
    assert c.values.texts[0].tolist() == [0, 0, -1, -1] and c.report["empty"] == 2 and c.report["low"] == 2
    assert any("непробованных участках — 2" in line for line in c.journal)
    # p_min = 0.5: 4 m of 10 and 3 m of 10 are not enough — no value and the flag, never a zero
    strict = cb.composite_by_benches(lines, vals, cb.BenchParams(p_min=0.5), traj, [0])
    assert np.isnan(strict.values.values).all() and strict.values.low.all()
    loose = cb.composite_by_benches(lines, vals, cb.BenchParams(p_min=0.0, unsampled=0.0), traj, [0], ends=[25.0])
    assert loose.lines.to.tolist() == [10.0, 20.0, 25.0]  # `ends` — the depth of the hole instead of the path's
    assert loose.values.values[:, 0].tolist() == pytest.approx([1.0, 2.0, 0.0], rel=REL)


# --- order, threads, cancel ----------------------------------------------------------------------------------------


def field(holes: int, seed: int = 5) -> tuple[Trajectories, np.ndarray, np.ndarray, np.ndarray]:
    """`holes` curved holes of 60–160 m with 1 m samples: the trajectories, (hole id, FROM, TO) of the samples."""
    rng = np.random.default_rng(seed)
    depth = np.round(rng.uniform(60.0, 160.0, holes))
    per = 4
    md = np.linspace(0.0, 1.0, per)[None, :] * depth[:, None]
    az = (rng.uniform(0.0, 360.0, holes)[:, None] + rng.normal(0.0, 6.0, (holes, per)).cumsum(axis=1)) % 360.0
    dip = np.clip(
        rng.uniform(-90.0, -30.0, holes)[:, None] + rng.normal(8.0, 10.0, (holes, per)).cumsum(axis=1), -90, 20
    )
    idx = np.repeat(np.arange(holes), per)
    z = 300.0 + rng.normal(0.0, 8.0, holes)
    z[::7] = 300.0  # collars exactly on a level
    col = Collars(np.arange(holes), rng.uniform(0, 5000, holes), rng.uniform(0, 5000, holes), z, depth)
    traj = desurvey(col, Surveys(idx, md.reshape(-1), az.reshape(-1), dip.reshape(-1)))
    n = depth.astype(np.int64)
    hole = np.repeat(np.arange(holes, dtype=np.int64) + 100, n)
    frm = (np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)).astype(np.float64)
    return traj, hole, frm, frm + 1.0


def _everything(threads: int = 1, shuffle: int | None = None) -> dict[str, np.ndarray]:
    traj, hole, frm, to = field(9000)
    rng = np.random.default_rng(11)
    au, rho = rng.lognormal(0.0, 1.0, hole.shape[0]), 2.5 + rng.random(hole.shape[0])
    au[rng.random(hole.shape[0]) < 0.1] = NAN
    if shuffle is not None:
        p = np.random.default_rng(shuffle).permutation(hole.shape[0])
        hole, frm, to, au, rho = hole[p], frm[p], to[p], au[p], rho[p]
    lines = iv.build_lines(hole, frm, to)
    vals = iv.stack_fields(lines, [au])
    dense = lines.holes - 100
    c = cb.composite_by_benches(
        lines, vals, cb.BenchParams(bench=5.0), traj, dense, density=lines.take(rho), threads=threads
    )
    return {
        "starts": c.lines.starts, "frm": c.lines.frm, "to": c.lines.to, "bench": c.bench, "xyz": c.xyz,
        "flat": c.flat, "values": c.values.values, "density": c.values.density, "count": c.values.count,
    }  # fmt: skip


def test_set_of_holes_threads_and_the_order_of_rows_do_not_change_a_bit() -> None:
    base = _everything()
    assert base["frm"].shape[0] > 50_000
    for other in (_everything(threads=2), _everything(threads=8), _everything(shuffle=4)):
        for k, v in base.items():
            assert v.tobytes() == other[k].tobytes(), k
    # every inner boundary of the set is on a level of the default grid (300 or above — by the collars)
    traj, hole, frm, to = field(9000)
    lines = iv.build_lines(hole, frm, to)
    c = cb.composite_by_benches(lines, np.zeros((lines.n, 0)), cb.BenchParams(bench=5.0), traj, lines.holes - 100)
    inner = np.ones(c.lines.n, dtype=np.bool_)
    inner[c.lines.starts[1:] - 1] = False
    z = traj.positions(c.lines.hole_ids()[inner] - 100, c.lines.to[inner])[:, 2]
    assert np.abs(z - np.round(z / 5.0) * 5.0).max() < TOL and c.report["top"] % 5.0 == 0.0
    assert (c.lines.lengths() > 1e-6).all()
    assert c.lines.lengths().sum() == pytest.approx(float(traj.lengths().sum()), rel=1e-12)
    zm = c.xyz[:, 2]
    assert (zm <= c.crest + TOL).all() and (zm >= c.floor - TOL).all()


def test_tick_reports_progress_and_cancels() -> None:
    traj, hole, frm, to = field(9000)
    lines = iv.build_lines(hole, frm, to)
    vals = iv.stack_fields(lines, [np.ones(lines.n)])
    seen: list[float] = []
    cb.composite_by_benches(lines, vals, cb.BenchParams(), traj, lines.holes - 100, tick=seen.append)
    assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) >= 6

    class Cancelled(Exception):
        pass

    def stop(_f: float) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        cb.composite_by_benches(lines, vals, cb.BenchParams(), traj, lines.holes - 100, tick=stop)
