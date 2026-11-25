"""The source «каркас» at work (ТЗ 8.5.2, 10.5 п. 3; ADR 0021 §4–5, решения 4, 7, 8, 20;
`intercepts/orient_mesh.py`).

The owner's wireframes `tests/data/real/ORE SEP 2025.tridb`: 66 closed ore bodies, 3 590 triangles of tens of
metres on UTM coordinates; 208 curved holes over them, 47 840 intervals of 2 m. Nothing is compared with a
stored answer: the crossings are taken from `geo.intersect` and put into the intervals in plain Python, the
nearest triangles — by the full search over all the bodies as one, r_n — by the full sum; the bodies one at a
time and on threads give the same bytes.

Then how the source is read and run on synthetic wireframes: one wireframe at a time (the one before is let
go), the same bytes with 1, 2, 4 and 8 threads and in any order of the rows, read-only arrays, progress and
cancel; the source in the chain of `orient` — Т8.5-13 on a table through `tt.of_table`; the wireframes of a
project through `wireframes.exact`."""

from __future__ import annotations

import weakref
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.composites import source
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects
from bf_core.drillholes.desurvey import Collars, Surveys, Trajectories, desurvey
from bf_core.geo import meshes as gm
from bf_core.geo.intersect import MeshTarget, hole_crossings
from bf_core.geo.nearest import mean_normals, nearest_triangles
from bf_core.intercepts import directions as dr
from bf_core.intercepts import formulas as fm
from bf_core.intercepts import orient, tt
from bf_core.intercepts import orient_mesh as om
from bf_core.intercepts import spec as sp
from bf_core.projects.service import projects_of
from tests import test_tt_table as tables
from tests.meshutil import UTM, grid, icosphere, stored
from tests.test_geo_nearest import ref_area_normals, ref_distances
from tests.test_tt_mesh import Mesh, digest, merged, nothing, plate, samples, straight, targets, unit_normals

REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real" / "ORE SEP 2025.tridb"
STEP, ROWS = 2.0, 230  # the intervals of a hole of the real scene
NONE, GLOB, _DOM, CROSS, NEAR, _MEAS, _NEXT, _TREND = orient.SRC_LABELS


class OneAtATime:
    """Wireframes built when asked for, as a job reads them; remembers how many were alive at once."""

    def __init__(self, meshes: list[Mesh]) -> None:
        self.meshes, self.most, self.passes = meshes, 0, 0
        self.alive: list[weakref.ref[MeshTarget]] = []

    def __len__(self) -> int:
        return len(self.meshes)

    def __iter__(self) -> Iterator[MeshTarget]:
        self.passes += 1
        for v, t in self.meshes:
            self.most = max(self.most, sum(ref() is not None for ref in self.alive))
            target = MeshTarget(v, t)
            self.alive.append(weakref.ref(target))
            yield target
            del target


# --- the owner's ore bodies --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ore() -> SimpleNamespace:
    """The bodies of the file and holes over them: a 16 × 13 grid of collars 15 m above the highest body, 460 m
    long, 50–90° down to any azimuth, turning 7° and flattening 4° on the way; intervals of 2 m."""
    if not REAL.exists():
        pytest.skip("tests/data/real/ORE SEP 2025.tridb is not here")
    from bf_core.formats.tridb import read_tridb

    meshes = [(np.asarray(m.xyz, dtype=np.float64), np.asarray(m.tri)) for m in read_tridb(REAL)]
    lo = np.min([v.min(axis=0) for v, _t in meshes], axis=0)
    hi = np.max([v.max(axis=0) for v, _t in meshes], axis=0)
    x, y = np.meshgrid(lo[0] + (np.arange(16) + 0.37) / 16 * (hi[0] - lo[0]),
                       lo[1] + (np.arange(13) + 0.61) / 13 * (hi[1] - lo[1]))  # fmt: skip
    k = x.size
    rng = np.random.default_rng(3)
    azimuth = np.repeat(rng.uniform(0.0, 360.0, k), 4) + np.tile([0.0, 2.0, 4.5, 7.0], k)
    dip = np.repeat(-rng.uniform(50.0, 90.0, k), 4) + np.tile([0.0, 1.5, 3.0, 4.0], k)
    traj = desurvey(Collars(np.arange(k), x.ravel(), y.ravel(), np.full(k, hi[2] + 15.0), np.full(k, STEP * ROWS)),
                    Surveys(np.repeat(np.arange(k), 4), np.tile([0.0, 150.0, 300.0, 450.0], k), azimuth % 360.0,
                            np.minimum(dip, -5.0)))  # fmt: skip
    frm = np.tile(np.arange(ROWS) * STEP, k)
    stats = [gm.mesh_stats(v, t) for v, t in meshes]
    return SimpleNamespace(meshes=meshes, stats=stats, traj=traj, hole=np.repeat(np.arange(k, dtype=np.int64), ROWS),
                           frm=frm, to=frm + STEP)  # fmt: skip


def ore_targets(ore: SimpleNamespace) -> list[MeshTarget]:
    return [MeshTarget.from_stats(v, t, s) for (v, t), s in zip(ore.meshes, ore.stats, strict=True)]


def test_ore_bodies_against_the_crossings_and_the_full_search(ore: SimpleNamespace) -> None:
    tg = ore_targets(ore)
    assert len(tg) == 66 and all(s["closed"] and s["orientation"] == "outward" for s in ore.stats)
    assert any(m.order is not None for m in tg)  # the triangles of the file are not in the order of the store
    o = om.mesh_orientation(ore.traj, ore.hole, ore.frm, ore.to, tg, d_max=50.0)
    n = len(ore.hole)
    # the crossings of `geo.intersect`, put into the intervals one by one
    c = hole_crossings(ore.traj, tg)
    assert o.report["crossings"] == len(c) == 558 and o.report["triangles"] == 3590
    normals = [unit_normals(v, t) for v, t in ore.meshes]
    sums, count = np.zeros((n, 3)), np.zeros(n, dtype=np.int32)
    for h, m, depth, tri, side in zip(c.hole, c.mesh, c.depth, c.triangle, c.side, strict=True):
        for r in {int((depth - om.DEPTH_TOL_M) // STEP), int((depth + om.DEPTH_TOL_M) // STEP)} & set(range(ROWS)):
            sums[h * ROWS + r] -= side * normals[m][tri]  # turned against the hole
            count[h * ROWS + r] += 1
    crossed = count > 0
    assert np.array_equal(o.count, count) and (o.way[crossed] == om.WAY_CROSSING).all()
    assert np.allclose(o.normal[crossed], fm.upper_normal(sums[crossed]), rtol=0.0, atol=1e-12)
    assert (count == 2).sum() >= 20 and np.isnan(o.distance[crossed]).all() and o.report["cancelled"] == 0
    # the nearest triangle of all the bodies as one wireframe: every 30th interval without a crossing
    v, t = merged(ore.meshes)
    upper = fm.upper_normal(unit_normals(v, t))
    pick = np.flatnonzero(~crossed)[::30]
    ref = ref_distances(ore.traj.positions(ore.hole[pick], 0.5 * (ore.frm[pick] + ore.to[pick])), v, t)
    least = ref.min(axis=1)
    near = least <= 50.0
    assert np.array_equal(o.way[pick] == om.WAY_NEAREST, near) and nothing(o, pick[~near])
    assert near.sum() >= 500 and (~near).sum() >= 500
    assert np.allclose(o.distance[pick[near]], least[near], rtol=0.0, atol=1e-9)
    for i in np.flatnonzero(near):
        tied = np.flatnonzero(ref[i] <= least[i] + 1e-7)  # an edge or a vertex: one of its triangles
        assert np.abs(upper[tied] - o.normal[pick[i]]).max(axis=1).min() <= 1e-9, pick[i]
    # every row: a unit upper normal or nothing; the true thickness is there exactly where the plane is
    found = o.way != om.WAY_NONE
    assert np.allclose(np.linalg.norm(o.normal[found], axis=1), 1.0, rtol=0.0, atol=1e-14)
    assert (o.normal[found][:, 2] >= 0.0).all() and nothing(o, ~found)
    assert o.report["crossing"] == int(crossed.sum()) == 525 and 19_000 < o.report["nearest"] < 21_000
    assert o.report["none"] == int((~found).sum()) > 25_000 and len(o.journal) == 1 and "d_max = 50 м" in o.journal[0]
    thick = dr.thickness(ore.traj, ore.hole, ore.frm, ore.to, o.normal).tt
    assert np.array_equal(np.isnan(thick), ~found) and (thick[found] <= STEP * (1.0 + 1e-12)).all()


def test_ore_bodies_one_at_a_time_on_threads_and_r_n(ore: SimpleNamespace) -> None:
    tg = ore_targets(ore)
    base = om.mesh_orientation(ore.traj, ore.hole, ore.frm, ore.to, tg, d_max=50.0, r_n=25.0)
    given = [(v, t) for v, t in ore.meshes]
    lazy = OneAtATime(given)
    again = om.mesh_orientation(ore.traj, ore.hole, ore.frm, ore.to, lazy, d_max=50.0, r_n=25.0, threads=4)
    assert digest(again) == digest(base) and lazy.most == 0 and lazy.passes == 2
    plain = om.mesh_orientation(ore.traj, ore.hole, ore.frm, ore.to, tg, d_max=50.0, threads=2)
    assert np.array_equal(plain.way, base.way) and np.array_equal(plain.distance, base.distance, equal_nan=True)
    crossing = base.way == om.WAY_CROSSING  # r_n is of the nearest point only
    assert np.array_equal(plain.normal[crossing], base.normal[crossing])
    # the mean by area within 25 m of the point found, over the triangles of all the bodies
    rows = np.flatnonzero(base.way == om.WAY_NEAREST)[::25]
    middle = ore.traj.positions(ore.hole[rows], 0.5 * (ore.frm[rows] + ore.to[rows]))
    near = None
    for k, target in enumerate(tg):  # the nearest triangle as the kernel takes it: the bodies one into another
        near = nearest_triangles(target, middle, 50.0, into=near, mesh=k)
    assert near is not None and near.found.all()
    assert np.array_equal(plain.normal[rows], fm.upper_normal(near.normal))
    v, t = merged(given)
    mean = fm.upper_normal(mean_normals(ref_area_normals(near.point, near.normal, v, t, 25.0), near.normal))
    assert np.allclose(base.normal[rows], mean, rtol=0.0, atol=1e-9)
    assert (np.abs(base.normal[rows] - plain.normal[rows]).max(axis=1) > 1e-3).sum() > len(rows) // 2


# --- one wireframe at a time, threads, progress ------------------------------------------------------------


def big() -> tuple[list[Mesh], Trajectories, np.ndarray, np.ndarray, np.ndarray]:
    """Three wireframes and 170 000 intervals of 0,5 m down 850 holes: more than one band of points and more
    than one strip of rows."""
    meshes = [grid(60, 60, lambda x, y: 5.0 * np.sin(0.11 * x) * np.cos(0.13 * y) + 0.2 * x, step=3.0, seed=11),
              icosphere(3, 30.0, UTM + np.array([90.0, 90.0, -50.0])),
              plate(40.0, 20.0, 4.0, UTM + np.array([80.0, 100.0, -25.0]), size=140.0, cells=8)]  # fmt: skip
    rng = np.random.default_rng(31)
    k = 850
    collar = UTM + np.column_stack([rng.uniform(5.0, 175.0, (k, 2)), np.full(k, 30.0)])
    traj, _d = straight(collar, 0.0, rng.uniform(0.0, 60.0, k), rng.uniform(0.0, 360.0, k), 100.0)
    return meshes, traj, *samples(k, 100.0, 0.5)


def test_wireframes_one_at_a_time_and_same_bytes_with_any_threads() -> None:
    meshes, traj, hole, frm, to = big()
    assert len(hole) > 2 * om.STRIP_ROWS
    base = om.mesh_orientation(traj, hole, frm, to, targets(meshes), d_max=20.0, r_n=4.0)
    ways = dict(zip(*np.unique(base.way, return_counts=True), strict=True))
    assert min(ways[om.WAY_NONE], ways[om.WAY_CROSSING], ways[om.WAY_NEAREST]) > 1000, ways
    for threads in (2, 4, 8):
        assert digest(om.mesh_orientation(traj, hole, frm, to, targets(meshes), d_max=20.0, r_n=4.0,
                                          threads=threads)) == digest(base)  # fmt: skip
    lazy = OneAtATime(meshes)
    assert digest(om.mesh_orientation(traj, hole, frm, to, lazy, d_max=20.0, r_n=4.0, threads=4)) == digest(base)
    assert lazy.most == 0 and lazy.passes == 2  # the second pass is that of r_n
    lazy = OneAtATime(meshes)
    plain = om.mesh_orientation(traj, hole, frm, to, lazy, d_max=20.0)
    assert lazy.most == 0 and lazy.passes == 1
    assert np.array_equal(plain.way, base.way) and np.array_equal(plain.distance, base.distance, equal_nan=True)
    order = np.random.default_rng(1).permutation(len(hole))  # the rows in any order
    mixed = om.mesh_orientation(traj, hole[order], frm[order], to[order], targets(meshes), d_max=20.0, r_n=4.0,
                                threads=4)  # fmt: skip
    assert np.array_equal(mixed.normal, base.normal[order], equal_nan=True)
    assert np.array_equal(mixed.way, base.way[order])
    half = np.zeros(len(hole), dtype=bool)  # a part of the rows: the same answer for them
    half[::3] = True
    part = om.mesh_orientation(traj, hole, frm, to, targets(meshes), d_max=20.0, r_n=4.0, todo=half)
    assert np.array_equal(part.normal[half], base.normal[half], equal_nan=True) and nothing(part, ~half)
    assert np.array_equal(part.count[half], base.count[half]) and (part.count[~half] == 0).all()


def test_read_only_arrays_as_the_blobs_of_a_wireframe_are_mapped() -> None:
    meshes, traj, hole, frm, to = big()
    kept = [stored(v, t)[:2] for v, t in meshes]
    columns = [np.array(a[:4000]) for a in (hole, frm, to)]
    base = om.mesh_orientation(traj, *columns, [MeshTarget(v, t, ordered=True) for v, t in kept], d_max=20.0, r_n=4.0)
    for a in (*columns, *(a for pair in kept for a in pair)):
        a.setflags(write=False)
    fixed = [MeshTarget(v, t, ordered=True) for v, t in kept]
    assert not any(m.xyz.flags.writeable or m.tri.flags.writeable for m in fixed)
    assert digest(om.mesh_orientation(traj, *columns, fixed, d_max=20.0, r_n=4.0)) == digest(base)
    assert set(base.way.tolist()) == {om.WAY_NONE, om.WAY_CROSSING, om.WAY_NEAREST}


def test_tick_and_stage_report_and_cancel() -> None:
    meshes, traj, hole, frm, to = big()
    seen: list[float] = []
    names: list[str] = []
    om.mesh_orientation(traj, hole, frm, to, targets(meshes[:2]), d_max=20.0, r_n=4.0, tick=seen.append,
                        stage=names.append)  # fmt: skip
    assert seen == sorted(seen) and 0.0 <= seen[0] and seen[-1] == 1.0 and len(seen) > 20
    assert names == [f"{step}: каркас {k} из 2" for step, k in (
        ("Пересечения стволов", 1), ("Ближайшие треугольники", 1), ("Пересечения стволов", 2),
        ("Ближайшие треугольники", 2), ("Осреднение нормалей", 1), ("Осреднение нормалей", 2))]  # fmt: skip
    names.clear()
    om.mesh_orientation(traj, hole, frm, to, targets(meshes[:1]), d_max=0.0, r_n=4.0, stage=names.append)
    assert names == ["Пересечения стволов: каркас 1 из 1"]  # no nearest point: nothing to average either

    class Cancelled(Exception):
        pass

    def run(stop: int) -> int:
        calls: list[float] = []

        def tick(fraction: float) -> None:
            calls.append(fraction)
            if len(calls) > stop:
                raise Cancelled

        with pytest.raises(Cancelled):
            om.mesh_orientation(traj, hole, frm, to, OneAtATime(meshes[:2]), d_max=20.0, r_n=4.0, tick=tick)
        return len(calls)

    assert [run(stop) for stop in (1, 7, len(seen) - 2)] == [2, 8, len(seen) - 1]


# --- the source in the chain of `orient` -------------------------------------------------------------------

DIP, DIP_TO, THICK = 25.0, 200.0, 6.0


def test_t8_5_13_on_a_table_through_the_chain() -> None:
    """A plate under the holes of `test_tt_table.scene` (vertical, inclined, an arc, one without a trajectory);
    the chain «каркас → глобальная» through `tt.of_table`: the stretch inside the plate has TT = its thickness,
    the rows near it take the nearest face, the rows far from it and the hole without a trajectory — the next
    source."""
    st = tables.scene()
    mesh = plate(DIP, DIP_TO, THICK, np.array([UTM[0] + 150.0, UTM[1] + 20.0, 250.0]), size=700.0)
    c = hole_crossings(st.traj, [MeshTarget(*mesh)])
    assert c.hole.tolist() == [0, 0, 1, 1, 2, 2, 4, 4]  # every hole with a trajectory goes through the plate
    rows: list[tuple[Any, ...]] = [("D", 10.0, 20.0)]
    for name, (s_in, s_out) in zip("ABCE", c.depth.reshape(4, 2).tolist(), strict=True):
        rows += [(name, s_in, s_out), (name, s_in - 5.0, s_in - 1.0), (name, 0.0, 4.0)]
    lines = tables.table(10, rows)
    asked: list[Any] = []

    def read(s: Any) -> list[MeshTarget]:
        asked.append(s)
        return [MeshTarget(*mesh)]

    by_mesh = sp.Source("mesh", meshes=[], d_max=25.0, r_n=0.0)
    t = tt.of_table(tables.spec(by_mesh, tables.GLOBAL, step=0.1), lines, st, mesh=om.mesh_source(read), threads=2)
    assert asked == [by_mesh]
    hole = np.repeat(np.array(lines.holes), np.diff(lines.starts))
    stretch = (hole != "D") & (lines.from_m > 4.0) & (lines.to_m - lines.from_m > 4.5)
    above = (hole != "D") & (lines.from_m > 4.0) & ~stretch
    assert tables.labels(t) == [CROSS if a else NEAR if b else GLOB for a, b in zip(stretch, above, strict=True)]
    assert stretch.sum() == above.sum() == 4 and np.abs(t.tt[stretch] - THICK).max() <= 1e-6  # Т8.5-13
    by_plate = stretch | above
    assert np.allclose(t.alpha[by_plate], DIP, rtol=0.0, atol=1e-8)
    assert np.allclose(t.dipdir[by_plate], DIP_TO, rtol=0.0, atol=1e-8)
    assert t.flag[by_plate].tolist() == [0] * 8 and t.flag[hole == "D"].tolist() == [8]
    assert np.isnan(t.tt[hole == "D"]).all() and not np.isnan(t.tt[hole != "D"]).any()
    first = t.summary["sources"][0]
    assert first["source"] == "mesh" and first["rows"] == 8 and first["crossing"] == first["nearest"] == 4
    assert first["none"] == 4 and first["no_path"] == 1 and first["triangles"] == 432 and first["crossings"] == 8
    assert t.summary["src"][CROSS] == 4 and any(line.startswith("Каркас: интервалов без ориентировки") for line in
                                                t.journal)  # fmt: skip
    # the source that cannot answer (the preview of a large wireframe): the chain goes on without it
    absent = tt.of_table(tables.spec(by_mesh, tables.GLOBAL), lines, st, mesh=om.mesh_source(lambda s: None))
    assert absent.summary["absent"] == ["mesh"] and set(tables.labels(absent)) == {GLOB}
    # nobody is left to ask: the wireframes are not read
    asked.clear()
    tt.of_table(tables.spec(tables.GLOBAL, by_mesh), lines, st, mesh=om.mesh_source(read))
    assert asked == []
    # any intervals of the holes (the candidates of the rule 8.5.3), in any order, through `orient.resolve`
    ids = np.array([tables.IDS[h] for h in "EA"], dtype=np.int64)
    some = orient.Intervals.grouped(ids, [4, 0], [0, 2, 3], [c.depth[6] - 3.0, 0.0, c.depth[1] - 2.0],
                                    [c.depth[7] + 3.0, 10.0, c.depth[1] + 9.0])  # fmt: skip
    got = orient.resolve([by_mesh], some, orient.Inputs(st.traj, mesh=om.mesh_source(read)))
    assert got.src.tolist() == [orient.SRC_MESH_CROSSING, orient.SRC_NONE, orient.SRC_MESH_CROSSING]
    assert np.allclose(got.normal[[0, 2]], fm.plane_normal(DIP, DIP_TO), rtol=0.0, atol=1e-10)
    assert np.isnan(got.normal[1]).all() and np.isnan(got.alpha[1])


def test_project_source_reads_the_wireframes_of_a_project(pclient) -> None:  # type: ignore[no-untyped-def]
    """`spec.parse_tt` with the source «каркас», `source.read`, the trajectories and the wireframes of the demo
    project: `project_source` reads the bodies through `wireframes.exact`, one at a time."""
    from bf_core.wireframes import exact

    assert pclient.post("/api/v1/projects/demo").status_code == 200
    project = projects_of(pclient.core).require_current()
    with dialect.read_tx(project.engine) as conn:
        row = conn.execute(sa.select(dh_tables.c.id, dh_tables.c.set_id).where(dh_tables.c.name == "Пробы")).one()
        found = conn.execute(sa.select(objects.c.name, objects.c.id).where(objects.c.kind == "wireframe"))
        wf = {r.name: int(r.id) for r in found}
        chain = [{"source": "mesh", "wireframe_ids": [wf["Рудное тело 1"], wf["Рудное тело 2"]], "d_max": 30,
                  "r_n": 5}, {"source": "global", "alpha": 30, "dipdir": 120}]  # fmt: skip
        parsed = sp.parse_tt(conn, {"set_id": int(row.set_id), "table": str(row.id), "orientation": chain})
    lines = source.read(project.engine, parsed.set_id, parsed.table_id, parsed.tables()[0][1])
    st = source.trajectories(project, parsed.set_id)
    names: list[str] = []
    exact.forget()
    res = tt.of_table(parsed, lines, st, mesh=om.project_source(project, stage=names.append), threads=2)
    got = res.summary["src"]
    assert got[CROSS] > 50 and got[NEAR] > 500 and got[GLOB] > 500 and got.get(NONE, 0) == 0, got
    assert names[0] == "Пересечения стволов: каркас 1 из 2" and names[-1] == "Осреднение нормалей: каркас 2 из 2"
    assert exact.cache_info() == {**exact.cache_info(), "entries": 2, "loads": 2, "trees": 2}  # once, kept for r_n
    # the same as the kernel on the arrays of the wireframes
    by_mesh = parsed.orientation[0]
    bodies = [exact.geometry(project, state) for state, _stats in by_mesh.meshes]
    rows = orient.Intervals.of(lines, st)
    o = om.mesh_orientation(st.traj, rows.dense_rows(), rows.frm, rows.to,
                            [MeshTarget(g.xyz, g.tri, ordered=True) for g in bodies], d_max=30.0, r_n=5.0)  # fmt: skip
    assert np.array_equal(res.src == orient.SRC_MESH_CROSSING, o.way == om.WAY_CROSSING)
    assert np.array_equal(res.src == orient.SRC_MESH_NEAREST, o.way == om.WAY_NEAREST)
    assert res.summary["sources"][0]["rows"] == int((o.way > 0).sum()) == got[CROSS] + got[NEAR]
    alpha, dipdir = fm.plane_angles(o.normal[o.way > 0])
    assert np.allclose(res.alpha[o.way > 0], alpha, rtol=0.0, atol=1e-9)
    assert np.allclose(res.dipdir[o.way > 0], dipdir, rtol=0.0, atol=1e-9)
    # a job on wireframes larger than the cache: nothing is kept
    exact.forget()
    gone = tt.of_table(parsed, lines, st, mesh=om.project_source(project, keep=False))
    assert tables.digest(gone) == tables.digest(res) and exact.cache_info()["entries"] == 0
    assert exact.cache_info()["loads"] == 4  # each body once more for r_n
    # the preview: a list of more triangles than allowed is not computed at all
    small = tt.of_table(parsed, lines, st, mesh=om.project_source(project, max_triangles=10))
    assert small.summary["absent"] == ["mesh"] and set(tables.labels(small)) == {GLOB}
    assert exact.cache_info()["loads"] == 4
    whole = sum(int(state.triangles) for state, _stats in by_mesh.meshes)
    exactly = tt.of_table(parsed, lines, st, mesh=om.project_source(project, max_triangles=whole), threads=2)
    assert tables.digest(exactly) == tables.digest(res)
