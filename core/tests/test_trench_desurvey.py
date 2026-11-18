"""Survey stations with coordinates — trenches along a polyline (ТЗ 7.35 Д-16, ADR 0011 §1): the trajectory goes
through the vertices exactly (≤ 1e-9 m on UTM coordinates, in fact bit for bit), is straight between them, depth
is the 3D length; samples across a vertex; the display polylines; the audit exceptions; the project database."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from bf_core.drillholes.desurvey import METHODS, SOURCE_SURVEY, Collars, Surveys, desurvey, sample_paths

NAN = math.nan
TOL = 1e-9  # metres, ТЗ Д-16
# Five vertices on UTM coordinates (X ~ 5e5, Y ~ 6e6: one float64 step is 9.3e-10 m): turns in plan and in Z.
UTM5 = np.array(
    [
        [512_345.678, 5_912_345.123, 1234.56],
        [512_371.913, 5_912_352.871, 1231.07],
        [512_380.004, 5_912_390.335, 1236.92],
        [512_355.217, 5_912_401.668, 1229.40],
        [512_349.951, 5_912_433.019, 1230.11],
    ]
)


def chainage(v: np.ndarray) -> np.ndarray:
    """Depth of every vertex: the 3D length of the polyline from its first vertex."""
    return np.concatenate([[0.0], np.cumsum(np.sqrt((np.diff(v, axis=0) ** 2).sum(axis=1)))])


def trenches(lines: list[np.ndarray], method: str = "minimum_curvature", depths: list[np.ndarray] | None = None):  # type: ignore[no-untyped-def]
    """Trenches as a set: the collar is the first vertex, TD the length, a station with coordinates per vertex."""
    md = depths or [chainage(v) for v in lines]
    first = np.array([v[0] for v in lines])
    collars = Collars(np.arange(len(lines)), first[:, 0], first[:, 1], first[:, 2], np.array([d[-1] for d in md]))
    hole = np.concatenate([np.full(len(v), i, dtype=np.int64) for i, v in enumerate(lines)])
    xyz = np.concatenate(lines)
    none = np.full(len(hole), NAN)
    surveys = Surveys(hole, np.concatenate(md), none, none, xyz[:, 0], xyz[:, 1], xyz[:, 2])
    return desurvey(collars, surveys, method=method, n_holes=len(lines)), md


def on_polyline(v: np.ndarray, md: np.ndarray, depth: np.ndarray) -> np.ndarray:
    """Reference: the point of the polyline at each depth, computed from a local origin (errors ~1e-13 m);
    returned relative to that origin (the subtraction of the origin from a float64 result is exact)."""
    origin = np.floor(v[0])
    local = v - origin
    k = np.clip(np.searchsorted(md, depth, side="right") - 1, 0, len(md) - 2)
    t = (depth - md[k]) / (md[k + 1] - md[k])
    return local[k] + t[:, None] * (local[k + 1] - local[k])


@pytest.mark.parametrize("method", METHODS)
def test_five_vertices_on_utm_match_exactly(method: str) -> None:
    t, md = trenches([UTM5], method)
    at = t.positions(np.zeros(5, dtype=np.int64), md[0])
    assert np.abs(at - UTM5).max() <= TOL
    assert (at == UTM5).all()  # the stored float64 themselves, whatever the method
    assert t.lengths()[0] == md[0][-1] == pytest.approx(126.4200, abs=1e-3)  # 27.577 + 38.772 + 28.274 + 31.798
    assert t.source[0] == SOURCE_SURVEY and t.invalid_stations == 0 and t.duplicate_stations == 0
    # a vertex between two segments is listed twice: once with the direction of each segment
    d, xyz, dirs = t.stations(0)
    assert d.tolist() == [md[0][0], *np.repeat(md[0][1:-1], 2).tolist(), md[0][-1]]
    assert (xyz[1] == xyz[2]).all() and (xyz[1] == UTM5[1]).all()
    assert np.allclose(np.linalg.norm(dirs, axis=1), 1.0, atol=1e-12)
    assert np.allclose(dirs[0], (UTM5[1] - UTM5[0]) / md[0][1]) and (dirs[0] == dirs[1]).all()


@pytest.mark.parametrize("method", METHODS)
def test_straight_between_the_vertices_by_3d_length(method: str) -> None:
    t, md = trenches([UTM5], method)
    depth = np.concatenate([np.linspace(0.0, md[0][-1], 997), 0.5 * (md[0][1:] + md[0][:-1])])
    got = t.positions(np.zeros(len(depth), dtype=np.int64), depth) - np.floor(UTM5[0])
    assert np.abs(got - on_polyline(UTM5, md[0], depth)).max() <= TOL
    # depth is the 3D length, not the plan one: a steep segment of 130 m, 50 m in plan
    a, b, c = [400_000.0, 6_000_000.0, 500.0], [400_030.0, 6_000_040.0, 380.0], [400_030.0, 6_000_140.0, 380.0]
    steep = np.array([a, b, c])
    t, md = trenches([steep], method)
    assert md[0].tolist() == [0.0, 130.0, 230.0]
    at = t.positions(np.zeros(3, dtype=np.int64), np.array([65.0, 130.0, 180.0]))
    assert np.abs(at - [[400_015.0, 6_000_020.0, 440.0], b, [400_030.0, 6_000_090.0, 380.0]]).max() <= TOL


def test_sample_across_a_vertex_lies_on_the_polyline() -> None:
    t, md = trenches([UTM5])
    v1 = md[0][1]
    hole = np.zeros(1, dtype=np.int64)
    start, end, mid = t.interval_positions(hole, np.array([v1 - 3.0]), np.array([v1 + 1.0]))
    origin = np.floor(UTM5[0])
    for got, depth in ((start, v1 - 3.0), (end, v1 + 1.0), (mid, v1 - 1.0)):
        assert np.abs(got - origin - on_polyline(UTM5, md[0], np.array([depth]))).max() <= TOL
    # the middle (by depth) is on the first segment, 1 m before the vertex — not on the chord start–end
    assert np.linalg.norm(mid[0] - UTM5[1]) == pytest.approx(1.0, abs=1e-9)
    assert np.linalg.norm(mid[0] - 0.5 * (start[0] + end[0])) > 0.3
    # a sample that ends exactly at a vertex, and one beyond the end (clipped to the last vertex)
    _s, end, _m = t.interval_positions(hole, np.array([0.0]), np.array([md[0][2]]))
    assert (end[0] == UTM5[2]).all()
    assert (t.positions(hole, np.array([1e6]))[0] == UTM5[4]).all()


@pytest.mark.parametrize("method", METHODS)
def test_display_polylines_have_no_bulges(method: str) -> None:
    """`sample_paths` and the 3D paths (`viz.arcs`) of a trench are its segments: every point is on the polyline."""
    from bf_core.viz.arcs import densify

    t, md = trenches([UTM5], method)
    origin = np.floor(UTM5[0])
    p = sample_paths(t, 2.5).points
    assert np.abs(p[:, :3] - origin - on_polyline(UTM5, md[0], p[:, 3])).max() <= TOL
    assert set(md[0].tolist()) <= set(p[:, 3].tolist()) and (np.diff(p[:, 3]) >= 0).all()
    _offsets, xyz, depth = densify(t, np.array([0]), 0.01, origin)
    assert np.abs(xyz - on_polyline(UTM5, md[0], depth)).max() < 1e-4  # float32 from the origin
    assert sorted(set(depth.tolist())) == md[0].tolist()  # the vertices only: no arc points in between


def test_holes_of_the_same_set_are_not_changed() -> None:
    """A set of drillholes and trenches: the holes keep their stations and positions bit for bit."""
    rng = np.random.default_rng(3)
    n, k = 40, 12
    cx, cy, cz = 5e5 + rng.uniform(0, 500, n), 6e6 + rng.uniform(0, 500, n), rng.uniform(900, 1000, n)
    hole = np.repeat(np.arange(n), k)
    depth = np.tile(np.arange(k) * 25.0, n) + 5.0  # the first station is below the collar
    az, dip = rng.uniform(0, 360, n * k), rng.uniform(-90, -40, n * k)
    td = np.full(n, 320.0)
    plain = desurvey(Collars(np.arange(n), cx, cy, cz, td), Surveys(hole, depth, az, dip))
    md = chainage(UTM5)
    none = np.full(5, NAN)
    collars = Collars(np.arange(n + 1), np.append(cx, UTM5[0, 0]), np.append(cy, UTM5[0, 1]), np.append(cz, UTM5[0, 2]),
                      np.append(td, md[-1]))  # fmt: skip
    surveys = Surveys(np.append(hole, np.full(5, n)), np.append(depth, md), np.append(az, none), np.append(dip, none),
                      np.append(np.full(n * k, NAN), UTM5[:, 0]), np.append(np.full(n * k, NAN), UTM5[:, 1]),
                      np.append(np.full(n * k, NAN), UTM5[:, 2]))  # fmt: skip
    mixed = desurvey(collars, surveys)
    stations = int(plain.offsets[-1])
    assert (mixed.offsets[: n + 1] == plain.offsets).all() and mixed.offsets[-1] == stations + 8
    for name in ("md", "xyz", "dirs"):
        assert (getattr(mixed, name)[:stations] == getattr(plain, name)).all(), name
    assert (mixed.positions(np.full(5, n), md) == UTM5).all()


@pytest.mark.parametrize("method", METHODS)
def test_depths_that_are_not_lengths_still_follow_the_polyline(method: str) -> None:
    """Depths edited by hand (here twice the lengths): positions are linear in depth between the vertices."""
    md = 2.0 * chainage(UTM5)
    t, _ = trenches([UTM5], method, depths=[md])
    assert (t.positions(np.zeros(5, dtype=np.int64), md) == UTM5).all()
    depth = np.linspace(0.0, md[-1], 501)
    got = t.positions(np.zeros(len(depth), dtype=np.int64), depth) - np.floor(UTM5[0])
    assert np.abs(got - on_polyline(UTM5, md, depth)).max() <= TOL


def test_first_vertex_below_the_collar_and_extension_to_td() -> None:
    collar = np.array([512_340.0, 5_912_340.0, 1236.0])
    md = chainage(UTM5) + 7.0  # the first station with coordinates is 7 m down the hole
    none = np.full(5, NAN)
    t = desurvey(
        Collars(np.array([0]), collar[:1], collar[1:2], collar[2:], np.array([md[-1] + 10.0])),
        Surveys(np.zeros(5, dtype=np.int64), md, none, none, UTM5[:, 0], UTM5[:, 1], UTM5[:, 2]),
    )
    hole = np.zeros(1, dtype=np.int64)
    assert (t.positions(hole, np.array([0.0]))[0] == collar).all()
    assert np.abs(t.positions(hole, np.array([3.5]))[0] - 0.5 * (collar + UTM5[0])).max() <= TOL  # straight to it
    assert (t.positions(hole, md[:1])[0] == UTM5[0]).all()
    # below the last vertex the path goes on along the last segment up to TD
    step = (UTM5[4] - UTM5[3]) / np.linalg.norm(UTM5[4] - UTM5[3])
    assert np.abs(t.positions(hole, np.array([md[-1] + 4.0]))[0] - (UTM5[4] + 4.0 * step)).max() <= 2 * TOL
    assert t.lengths()[0] == md[-1] + 10.0
    # a station with coordinates at depth 0 wins over the collar; a single one leaves the hole its collar direction
    md = chainage(UTM5)
    for k, azimuth, expect in ((5, NAN, UTM5[1] - UTM5[0]), (1, 90.0, [1.0, 0.0, 0.0]), (1, NAN, [0.0, 0.0, -1.0])):
        cx, cy, cz = collar[:1], collar[1:2], collar[2:]
        t = desurvey(
            Collars(np.array([0]), cx, cy, cz, np.array([NAN]), np.array([azimuth]), np.zeros(1)),
            Surveys(np.zeros(k, dtype=np.int64), md[:k], none[:k], none[:k], *np.ascontiguousarray(UTM5[:k].T)),
        )
        depth, xyz, dirs = t.stations(0)
        assert (xyz[0] == UTM5[0]).all() and depth[0] == 0.0 and len(depth) == max(1, 2 * k - 2)
        assert np.allclose(dirs[0], np.asarray(expect) / np.linalg.norm(expect), atol=1e-12)


def test_stations_with_and_without_coordinates_in_one_hole() -> None:
    """Coordinates win over the method: the course into such a station is straight and ends exactly there; the
    courses between stations with angles only are computed by the method from where the hole is."""
    fixed = np.array([1000.0, 2000.0, 440.0])  # 60 m down a hole that starts vertical at (1000, 2000, 500)
    depth = np.array([0.0, 60.0, 100.0, 140.0])
    az = np.array([0.0, NAN, 90.0, 90.0])
    dip = np.array([-90.0, NAN, -45.0, -45.0])
    x, y, z = (np.array([NAN, v, NAN, NAN]) for v in fixed + [3.0, 0.0, 0.0])  # 3 m east of the vertical
    one = np.ones(1)
    t = desurvey(Collars(np.array([0]), 1000 * one, 2000 * one, 500 * one, 140 * one),
                 Surveys(np.zeros(4, dtype=np.int64), depth, az, dip, x, y, z), method="tangent")  # fmt: skip
    hole = np.zeros(1, dtype=np.int64)
    assert (t.positions(hole, np.array([60.0]))[0] == fixed + [3.0, 0.0, 0.0]).all()
    assert np.abs(t.positions(hole, np.array([30.0]))[0] - [1001.5, 2000.0, 470.0]).max() <= TOL
    # 60 → 100 m: the tangent method from a station without angles keeps the direction it arrived with
    arrive = np.array([3.0, 0.0, -60.0]) / math.hypot(3.0, 60.0)
    assert np.abs(t.positions(hole, np.array([100.0]))[0] - (fixed + [3.0, 0.0, 0.0] + 40.0 * arrive)).max() <= 1e-9
    r = 40.0 * math.cos(math.radians(45.0))
    end = fixed + [3.0, 0.0, 0.0] + 40.0 * arrive + [r, 0.0, -r]
    assert np.abs(t.positions(hole, np.array([140.0]))[0] - end).max() <= 1e-9
    assert t.invalid_stations == 0


def test_invalid_and_repeated_stations_are_counted() -> None:
    md = chainage(UTM5)
    hole = np.zeros(8, dtype=np.int64)
    depth = np.concatenate([md, [md[2], 50.0, -1.0]])  # a repeated depth, a row without anything, a bad depth
    none = np.full(8, NAN)
    x, y, z = (np.concatenate([UTM5[:, i], [UTM5[2, i] + 9.0, NAN, UTM5[0, i]]]) for i in range(3))
    y[1] = NAN  # a station with two coordinates of three has no coordinates, and no angles either
    t = desurvey(Collars(np.array([0]), UTM5[:1, 0], UTM5[:1, 1], UTM5[:1, 2], md[-1:]),
                 Surveys(hole, depth, none, none, x, y, z))  # fmt: skip
    assert t.invalid_stations == 3 and t.duplicate_stations == 1
    kept = UTM5[[0, 2, 3, 4]]
    assert (t.positions(np.zeros(4, dtype=np.int64), md[[0, 2, 3, 4]]) == kept).all()  # the first row of a depth wins
    mid = t.positions(np.zeros(1, dtype=np.int64), np.array([0.5 * md[2]]))[0]
    assert np.abs(mid - 0.5 * (UTM5[0] + UTM5[2])).max() <= TOL  # the dropped vertex is not on the path


def test_hard_data_thousands_of_trenches_in_any_row_order() -> None:
    """2 500 trenches of 2…60 vertices (short and long segments, vertical ones, a point repeated at another
    depth) among 300 drillholes, rows shuffled: every vertex exact, 50 000 random depths within 1e-9 m."""
    rng = np.random.default_rng(11)
    lines = []
    for _ in range(2500):
        m = int(rng.integers(2, 61))
        step = rng.normal(0.0, 1.0, (m - 1, 3)) * rng.choice([1e-4, 0.5, 30.0, 400.0], (m - 1, 1))
        step[rng.random(m - 1) < 0.05, :2] = 0.0  # vertical segments
        start = [rng.uniform(3e5, 7e5), rng.uniform(5.5e6, 6.5e6), rng.uniform(-200, 3000)]
        lines.append(np.vstack([start, start + np.cumsum(step, axis=0)]))
    md = [chainage(v) for v in lines]
    for d in md:  # no zero-length segment (the importer removes them)
        assert (np.diff(d) > 0).all()
    n_t, n_h, k = len(lines), 300, 15
    hole = np.concatenate([np.repeat(np.arange(n_h), k), *[np.full(len(v), n_h + i) for i, v in enumerate(lines)]])
    depth = np.concatenate([np.tile(np.arange(k) * 20.0, n_h), *md])
    az = np.concatenate([rng.uniform(0, 360, n_h * k), np.full(len(hole) - n_h * k, NAN)])
    dip = np.concatenate([rng.uniform(-90, -30, n_h * k), np.full(len(hole) - n_h * k, NAN)])
    xyz = np.vstack([np.full((n_h * k, 3), NAN), *lines])
    first = np.array([v[0] for v in lines])
    cx = np.concatenate([rng.uniform(3e5, 7e5, n_h), first[:, 0]])
    cy = np.concatenate([rng.uniform(5.5e6, 6.5e6, n_h), first[:, 1]])
    cz = np.concatenate([rng.uniform(0, 2000, n_h), first[:, 2]])
    td = np.concatenate([np.full(n_h, 300.0), [d[-1] for d in md]])
    order = rng.permutation(len(hole))
    ticks: list[float | None] = []
    t = desurvey(
        Collars(np.arange(n_h + n_t), cx, cy, cz, td),
        Surveys(hole[order], depth[order], az[order], dip[order], *np.ascontiguousarray(xyz[order].T)),
        tick=lambda f, _m: ticks.append(f),
    )
    assert ticks and t.invalid_stations == 0 and t.duplicate_stations == 0 and len(t.skipped) == 0
    which = np.concatenate([np.full(len(v), n_h + i) for i, v in enumerate(lines)])
    assert (t.positions(which, np.concatenate(md)) == np.vstack(lines)).all()
    assert (t.lengths()[n_h:] == [d[-1] for d in md]).all()
    worst = 0.0
    for i in rng.integers(0, n_t, 500):
        d = rng.uniform(0.0, md[i][-1], 100)
        got = t.positions(np.full(100, n_h + i), d) - np.floor(lines[i][0])
        worst = max(worst, float(np.abs(got - on_polyline(lines[i], md[i], d)).max()))
    assert worst <= TOL, worst


# --- audit ----------------------------------------------------------------------------------------


def _audit_data(fixed: bool):  # type: ignore[no-untyped-def]
    """A drillhole with a sharp dogleg (H1) and a trench of five vertices with right-angle turns (T1)."""
    from bf_core.audit.checks import AuditData

    md = chainage(UTM5)
    return AuditData(
        holes=np.array(["H1", "T1"], dtype=object),
        has_collar=np.array([True, True]),
        x=np.array([512_000.0, UTM5[0, 0]]),
        y=np.array([5_912_000.0, UTM5[0, 1]]),
        z=np.array([1200.0, UTM5[0, 2]]),
        td=np.array([100.0, md[-1]]),
        azimuth=np.array([90.0, NAN]),
        dip=np.array([-60.0, NAN]),
        status=np.array(["Пробурена", "Пройдена"], dtype=object),
        survey_row_id=np.arange(1, 9),
        survey_hole=np.array([0, 0, 0, 1, 1, 1, 1, 1]),
        survey_depth=np.concatenate([[0.0, 30.0, 60.0], md]),
        survey_azimuth=np.array([90.0, 90.0, 180.0, NAN, NAN, NAN, NAN, NAN]),
        survey_dip=np.array([-60.0, -60.0, -60.0, NAN, NAN, NAN, NAN, NAN]),
        assay_row_id=np.arange(1, 4),
        assay_hole=np.array([1, 1, 1]),
        assay_from=np.array([0.0, md[1] - 2.0, md[1] + 2.0]),
        assay_to=np.array([md[1] - 2.0, md[1] + 2.0, md[-1]]),
        components={"AU": np.array([0.4, 1.2, 0.7])},
        survey_fixed=np.array([False] * 3 + [True] * 5) if fixed else None,
    )


def test_audit_does_not_check_stations_with_coordinates() -> None:
    from bf_core.audit.checks import run_checks

    found = {(f["code"], f["hole"]): f["count"] for f in run_checks(_audit_data(fixed=True)).findings}
    assert found == {("SURVEY_DOGLEG", "H1"): 1, ("COLLAR_WITHOUT_ASSAY", "H1"): 1}, found
    # the same rows taken as plain surveys: the trench vertices would be reported as unusable readings
    plain = {(f["code"], f["hole"]): f["count"] for f in run_checks(_audit_data(fixed=False)).findings}
    assert plain[("SURVEY_EMPTY_VALUES", "T1")] == 5 and ("SURVEY_DOGLEG", "H1") in plain
    # rows given in another order: the mark of a station follows its row
    data = _audit_data(fixed=True)
    order = np.array([7, 2, 5, 0, 3, 6, 1, 4])
    for name in ("survey_row_id", "survey_hole", "survey_depth", "survey_azimuth", "survey_dip", "survey_fixed"):
        setattr(data, name, getattr(data, name)[order])
    assert {(f["code"], f["hole"]): f["count"] for f in run_checks(data).findings} == found


# --- the project database ---------------------------------------------------------------------------


def test_trench_in_a_project_database(tmp_path: Path) -> None:
    """Stations with x, y, z in `dh_surveys`: trajectories of the set, coordinates of sample intervals, the audit."""
    import sqlalchemy as sa

    from bf_core.audit import service as audit
    from bf_core.db import dialect
    from bf_core.db.schema import dh_holes, dh_surveys
    from bf_core.drillholes import store as dh
    from bf_core.drillholes.trajectory import interval_xyz, trajectories
    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path, "Канавы", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    md = chainage(UTM5)
    try:
        with dialect.write_tx(engine) as conn:
            set_id = dh.create_set(conn, "Набор")
            dh.replace_collars(
                conn,
                set_id,
                [
                    dh.CollarRow("H1", 512_000.0, 5_912_000.0, 1200.0, 100.0, 90.0, -60.0),
                    dh.CollarRow("T1", *UTM5[0].tolist(), td=float(md[-1]), attrs={"TYPE": "Канава"}),
                ],
            )
            ids = dh.hole_ids(conn, set_id)
            none = dict.fromkeys(("azimuth", "dip", "x", "y", "z"))
            conn.execute(
                sa.insert(dh_surveys),
                [{**none, "set_id": set_id, "hole_id": ids["H1"], "depth": d, "azimuth": 90.0, "dip": -60.0}
                 for d in (0.0, 50.0)]
                + [{**none, "set_id": set_id, "hole_id": ids["T1"], "depth": float(d), "x": v[0], "y": v[1], "z": v[2]}
                   for d, v in zip(md, UTM5.tolist(), strict=True)],
            )  # fmt: skip
        dh.write_table(
            engine,
            set_id,
            dh.TableData(
                "Пробы", "assay", ["T1", "T1"], [0.0, md[1] - 2.0], [md[1] - 2.0, md[1] + 2.0],
                [dh.ColumnSpec("AU", "number", "g/t", "component")], [[0.4, 1.2]],
            ),  # fmt: skip
            job_id=None,
            tick=lambda _f, _m: None,
        )
        st = trajectories(SimpleNamespace(engine=engine, uid=f"trench-{tmp_path.name}"), set_id)
        i = st.index[ids["T1"]]
        assert st.skipped == [] and (st.traj.positions(np.full(5, i), md) == UTM5).all()
        assert st.traj.lengths()[i] == md[-1]
        hole = np.full(2, ids["T1"])
        a, b = interval_xyz(st, hole, np.array([0.0, md[1] - 2.0]), np.array([md[1] - 2.0, md[1] + 2.0]))
        origin = np.floor(UTM5[0])
        assert (a[0] == UTM5[0]).all() and (a[1] == b[0]).all()
        assert np.abs(b - origin - on_polyline(UTM5, md, np.array([md[1] - 2.0, md[1] + 2.0]))).max() <= TOL

        with dialect.read_tx(engine) as conn:
            table = audit.default_table(conn, set_id)
            data = audit.load(conn, set_id, table)
            assert conn.execute(sa.select(sa.func.count()).select_from(dh_holes)).scalar_one() == 2
        assert data.survey_fixed.tolist() == [False, False, True, True, True, True, True]
        audit.run(engine, set_id, table=table, config={}, job_id=None, tick=lambda _f, _m=None: None)
        with dialect.read_tx(engine) as conn:
            run = audit.latest(conn, set_id)
            assert run is not None
            found = audit.findings(conn, run["run_id"])["findings"]
        assert {(f["code"], f["hole"]) for f in found} == {
            ("COLLAR_EMPTY_STATUS", "H1"),
            ("COLLAR_EMPTY_STATUS", "T1"),
            ("COLLAR_WITHOUT_ASSAY", "H1"),
        }
    finally:
        engine.dispose()
