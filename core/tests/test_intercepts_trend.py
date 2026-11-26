"""The source «тренд» of the orientation (подшаг 19.20; ТЗ 8.5.2, Д-15; ADR 0026, решение 27, вопрос № 57).

ТД15-9: the source `trend` = the source `global` with the same angles (1e−12): a global trend gives the plane of its
minor axis (`rotation.plane_of`), a structural one — its local normal at the middle of the interval; TT_SRC is
«тренд». Also: the minor axis by the ratios, the middle of the interval (not its ends), an interval without a
position (no trajectory, no depth) — no plane, never 0, the chain goes on, the parameters of the source
(`trend_id`, 422 `trend_not_found` / `no_orientation` / `bad_param`), the canonical body and the hash without ids,
`rebind` by the name of the trend (`.bfggis` renumbers the objects), a job on the demo project with a global trend
and a structural trend of a wireframe, no numba before the first call."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.geostat import rotation
from bf_core.geostat import trend_store as ts
from bf_core.geostat import trend_struct as st
from bf_core.intercepts import orient, tt
from bf_core.intercepts import spec as sp
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as ws
from tests.meshutil import UTM
from tests.test_intercepts_spec import demo, parse, refused  # noqa: F401  (the fixture `demo`)
from tests.test_intercepts_tt_job import body, col, tj, tt_here  # noqa: F401  (the fixture `tj`)
from tests.test_trends_structural import face_normal, plane_mesh
from tests.test_tt_table import scene, table
from tests.test_tt_table import spec as tt_spec

TOL = 1e-12
GLOBAL = [{"source": "global", "alpha": 30, "dipdir": 120}]
TREND = orient.SRC_TREND
ANGLES = [(30.0, 0.0, 0.0), (112.5, -35.0, -20.0), (0.0, 0.0, 0.0), (250.0, 80.0, 170.0), (45.0, -90.0, 0.0)]
RATIOS = [1.0, 0.5, 0.1]


def gap(a: Any, b: Any) -> float:
    """The largest difference; a gap (NaN) must be in the same places — it is never a 0."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    assert np.array_equal(np.isnan(a), np.isnan(b))
    return float(np.nanmax(np.abs(a - b), initial=0.0))


def trend_source(meta: dict[str, Any], name: str = "Т") -> sp.Source:
    return sp.Source("trend", trend_id=1, trend_name=name, trend_meta=meta)


def global_meta(ang: tuple[float, float, float], ratios: Any = RATIOS) -> dict[str, Any]:
    return {"v": 1, "kind": "global", "ang1": ang[0], "ang2": ang[1], "ang3": ang[2], "ratios": list(ratios)}


def resolve(chain: list[sp.Source], lines: Any, loader: Any = None) -> orient.Orientation:
    s = scene()
    return orient.resolve(chain, orient.Intervals.of(lines, s), orient.Inputs(s.traj, lines, trend=loader))


# --- ТД15-9: a global trend ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ang", ANGLES)
def test_td15_9_a_global_trend_is_the_global_source_with_the_angles_of_its_plane(ang) -> None:  # type: ignore[no-untyped-def]
    alpha, dipdir, _pitch = rotation.plane_of(*ang)
    lines = table(10, [("A", 0.0, 4.0), ("B", 5.0, 30.0), ("C", 0.0, 100.0), ("E", 10.0, 12.5), ("D", 0.0, 5.0)])
    got = resolve([trend_source(global_meta(ang))], lines)
    ref = resolve([sp.Source("global", alpha=alpha, dipdir=dipdir)], lines)
    assert gap(got.normal, ref.normal) <= TOL
    assert gap(got.alpha, ref.alpha) <= TOL and gap(got.dipdir, ref.dipdir) <= TOL
    assert (got.src == TREND).all() and (ref.src == orient.SRC_GLOBAL).all()
    assert orient.SRC_LABELS[TREND] == "тренд" and got.absent == ()
    s = scene()
    t1 = tt.of_table(tt_spec(sp.Source("global", alpha=alpha, dipdir=dipdir)), lines, s)
    rows = orient.Intervals.of(lines, s)
    t2 = tt.compute(tt_spec(trend_source(global_meta(ang))), rows, orient.Inputs(s.traj, lines))
    assert gap(t1.tt, t2.tt) <= TOL * 100.0 and (t1.flag == t2.flag).all()
    assert set(t2.src_text()) == {"тренд"} and t2.summary["src"] == {"тренд": 5}


def test_the_minor_axis_is_the_one_of_the_least_ratio() -> None:
    ang = (112.5, -35.0, -20.0)
    r = rotation.rotation_matrix(*ang)
    lines = table(10, [("A", 0.0, 4.0), ("E", 0.0, 10.0)])
    ordered = resolve([trend_source(global_meta(ang, [1.0, 0.5, 0.1]))], lines)  # a3 is the minor one: the plane a1–a2
    assert np.abs(np.abs(ordered.normal) - np.abs(r[2])).max() <= TOL
    swapped = resolve([trend_source(global_meta(ang, [1.0, 0.1, 0.5]))], lines)  # a2 is the minor one: the plane a1–a3
    assert np.abs(np.abs(swapped.normal) - np.abs(r[1])).max() <= 1e-9 and (swapped.normal[:, 2] >= 0).all()
    assert np.abs(swapped.normal - swapped.normal[0]).max() == 0.0  # one plane for every interval
    equal = resolve([trend_source(global_meta(ang, [1.0, 0.3, 0.3]))], lines)  # equal: the third axis
    assert np.abs(equal.normal - ordered.normal).max() <= TOL
    a, d, _p = rotation.plane_of(*ang)
    assert swapped.alpha[0] != pytest.approx(a) or swapped.dipdir[0] != pytest.approx(d)


# --- the structural trend --------------------------------------------------------------------------------------------


def at_hole_a(*depth: float) -> np.ndarray:
    """Points on the vertical hole A of `scene()` (the collar z 300) at the depths."""
    d = np.array(depth, dtype=float)
    return np.column_stack([np.full(d.size, UTM[0]), np.full(d.size, UTM[1]), 300.0 - d])


def measured(planes: list[tuple[float, float]], xyz: np.ndarray) -> st.Measurements:
    alpha, dipdir = zip(*planes, strict=True)
    return st.Measurements(np.ascontiguousarray(xyz), st.turn_up(st.plane_normals(alpha, dipdir)))


def structural_meta(radius: float = 10.0) -> dict[str, Any]:
    return {"v": 1, "kind": "structural", "radius": radius, "ratios": [1.0, 0.5, 0.1]}


def loader_of(source: Any) -> Any:
    seen: list[Any] = []

    def load(meta: Any) -> Any:
        seen.append(meta)
        return source

    load.seen = seen  # type: ignore[attr-defined]
    return load


def test_td15_9_a_structural_trend_of_one_plane_is_the_global_source() -> None:
    xyz = np.random.default_rng(5).uniform(-60.0, 60.0, (40, 3)) + [UTM[0], UTM[1], 250.0]
    source = measured([(35.0, 120.0)] * 40, xyz)
    lines = table(10, [("A", 0.0, 4.0), ("A", 40.0, 90.0), ("B", 5.0, 30.0), ("C", 0.0, 100.0), ("E", 10.0, 12.5)])
    got = resolve([trend_source(structural_meta(500.0))], lines, loader_of(source))
    ref = resolve([sp.Source("global", alpha=35.0, dipdir=120.0)], lines)
    assert gap(got.normal, ref.normal) <= TOL
    assert gap(got.alpha, np.full(5, 35.0)) <= 1e-9 and gap(got.dipdir, np.full(5, 120.0)) <= 1e-9
    assert (got.src == TREND).all()
    s = scene()
    rows = orient.Intervals.of(lines, s)
    t1 = tt.compute(tt_spec(sp.Source("global", alpha=35.0, dipdir=120.0)), rows, orient.Inputs(s.traj, lines))
    t2 = tt.compute(tt_spec(trend_source(structural_meta(500.0))), rows,
                    orient.Inputs(s.traj, lines, trend=loader_of(source)))  # fmt: skip
    assert gap(t1.tt, t2.tt) <= TOL * 100.0 and set(t2.src_text()) == {"тренд"}


def test_the_normal_is_taken_at_the_middle_of_the_interval() -> None:
    """Plane A at the depth 10, plane B at 85, R_тр = 10: the interval 40–100 has the middle 70 — B is the nearest
    (15 m against 60); at its start 40 the nearest would be A (30 m against 45)."""
    a, b = (20.0, 90.0), (70.0, 200.0)
    source = measured([a, b], at_hole_a(10.0, 85.0))
    lines = table(10, [("A", 0.0, 30.0), ("A", 40.0, 100.0), ("A", 80.0, 90.0)])
    got = resolve([trend_source(structural_meta(10.0))], lines, loader_of(source))
    assert got.alpha.tolist() == pytest.approx([20.0, 70.0, 70.0], abs=1e-9)
    assert got.dipdir.tolist() == pytest.approx([90.0, 200.0, 200.0], abs=1e-9)
    assert got.report[0]["source"] == "trend" and got.report[0]["rows"] == 3 and got.report[0]["no_position"] == 0
    assert got.report[0]["nearest"] == 1  # the middle 70 has no measurement within 10 m
    assert any("взят ближайший замер" in line for line in got.journal)


def test_an_interval_without_a_position_has_no_plane_and_the_chain_goes_on() -> None:
    source = measured([(35.0, 120.0)] * 4, at_hole_a(10.0, 20.0, 30.0, 40.0))
    lines = table(10, [("A", 0.0, 20.0), ("D", 0.0, 20.0), ("A", 20.0, 40.0), ("Z", 0.0, 5.0)])  # D, Z: no trajectory
    only = resolve([trend_source(structural_meta(100.0))], lines, loader_of(source))
    lost = np.isnan(only.normal).all(axis=1)
    assert int(lost.sum()) == 2 and np.isnan(only.alpha[lost]).all() and np.isnan(only.dipdir[lost]).all()
    assert (only.src[lost] == orient.SRC_NONE).all() and (only.src[~lost] == TREND).all()
    assert only.report[0]["no_position"] == 2 and any("без положения" in line for line in only.journal)
    chained = resolve([trend_source(structural_meta(100.0)), sp.Source("global", alpha=10.0, dipdir=20.0)], lines,
                      loader_of(source))  # fmt: skip
    assert (chained.src[lost] == orient.SRC_GLOBAL).all() and (chained.alpha[lost] == 10.0).all()
    assert (chained.src[~lost] == TREND).all()
    t = tt.of_table(tt_spec(trend_source(structural_meta(100.0))), lines, scene(), trend=loader_of(source))
    assert np.isnan(t.tt[lost]).all() and np.isfinite(t.tt[~lost]).all()  # TT is empty there, never 0
    assert (t.flag[lost] & 2).all() and t.src_text()[lost].tolist() == ["нет", "нет"]


def test_an_interval_without_depth_is_out_of_the_calculation() -> None:
    source = measured([(35.0, 120.0)] * 2, at_hole_a(10.0, 20.0))
    lines = table(10, [("A", 0.0, 20.0), ("A", float("nan"), 30.0), ("A", 30.0, 30.0)])
    got = resolve([trend_source(structural_meta(100.0))], lines, loader_of(source))
    ok = got.valid
    assert ok.tolist().count(True) == 1 and (got.src[ok] == TREND).all()
    assert (got.src[~ok] == orient.SRC_NONE).all() and np.isnan(got.normal[~ok]).all()


def test_a_trend_is_not_asked_when_nobody_is_left_and_not_read_twice() -> None:
    source = measured([(35.0, 120.0)] * 2, at_hole_a(10.0, 20.0))
    lines = table(10, [("A", 0.0, 20.0)])
    load = loader_of(source)
    got = resolve([sp.Source("global", alpha=10.0, dipdir=20.0), trend_source(structural_meta(100.0))], lines, load)
    assert (got.src == orient.SRC_GLOBAL).all() and load.seen == []  # type: ignore[attr-defined]
    resolve([trend_source(structural_meta(100.0))], lines, load)
    assert len(load.seen) == 1  # type: ignore[attr-defined]


def test_a_structural_trend_needs_the_project_or_a_loader() -> None:
    lines = table(10, [("A", 0.0, 20.0)])
    with pytest.raises(RuntimeError, match="jobs_coding.reading"):
        resolve([trend_source(structural_meta(100.0))], lines)
    resolve([trend_source(global_meta((0.0, 0.0, 0.0)))], lines)  # a global one needs neither


def test_the_result_does_not_depend_on_the_threads() -> None:
    xyz = np.random.default_rng(3).uniform(-80.0, 80.0, (60, 3)) + [UTM[0], UTM[1], 250.0]
    planes = [(float(a), float(d)) for a, d in np.random.default_rng(4).uniform([5, 0], [85, 359], (60, 2))]
    source = measured(planes, xyz)
    lines = table(10, [("A", float(i), float(i) + 5.0) for i in range(0, 90, 5)] + [("E", 0.0, 20.0)])
    s = scene()
    rows = orient.Intervals.of(lines, s)
    chain = [trend_source(structural_meta(60.0))]
    one = orient.resolve(chain, rows, orient.Inputs(s.traj, lines, trend=loader_of(source)))
    four = orient.resolve(chain, rows, orient.Inputs(s.traj, lines, trend=loader_of(source)), threads=4)
    assert np.array_equal(one.normal, four.normal, equal_nan=True)


# --- the parameters of the source --------------------------------------------------------------------------------


@pytest.fixture()
def trends(demo):  # type: ignore[no-untyped-def]  # noqa: F811
    """A global trend and a structural one by the first wireframe of the demo project."""
    with dialect.write_tx(demo["engine"]) as conn:
        g = ts.create(conn, {"name": "Глобальный", "ang1": 30, "ang2": -20, "ratios": [1, 0.4, 0.1]})
        s = ts.create(conn, {"kind": "structural", "name": "Структурный",
                             "source": {"type": "mesh", "wireframe_id": demo["wf"][0]}, "radius": 150})  # fmt: skip
    return {**demo, "g": int(g["id"]), "s": int(s["id"]), "version": 1}


def test_the_parameters_of_the_source_trend(trends) -> None:  # type: ignore[no-untyped-def]
    g, s = trends["g"], trends["s"]
    got = parse(trends, sp.parse_tt, orientation=[{"source": "trend", "trend_id": g}])
    one = got.orientation[0]
    assert one.kind == "trend" and (one.trend_id, one.trend_name) == (g, "Глобальный")
    assert one.trend_meta["ang2"] == -20 and one.table() is None and one.mesh_ids() == []
    assert got.body["orientation"] == [{"source": "trend", "trend_id": g, "trend": "Глобальный"}]
    assert got.tables() == [(int(trends["table"]), [])] and got.mesh_ids() == []
    again = parse(trends, sp.parse_tt, orientation=[{"source": "trend", "trend_id": str(s)}, *GLOBAL])
    assert again.orientation[0].trend_meta["kind"] == "structural" and again.orientation[1].kind == "global"
    simple = parse(trends, sp.parse_tt, source="trend", trend_id=g, fallback_alpha=5, fallback_dipdir=6)
    assert [x["source"] for x in simple.body["orientation"]] == ["trend", "global"]
    assert "trend" in sp.SOURCES and parse(trends, sp.parse_run, field="AU", cutoff=1, source="trend", trend_id=g)


def test_the_refusals_of_the_source_trend(trends) -> None:  # type: ignore[no-untyped-def]
    def err(**item: Any) -> tuple[int, str, str]:
        e = refused(trends, sp.parse_tt, orientation=[{"source": "trend", **item}])
        return e.status, e.code, str(e.extra.get("param"))

    assert err() == (422, "no_orientation", "orientation.0.trend_id")
    assert err(trend_id="") == (422, "no_orientation", "orientation.0.trend_id")
    assert err(trend_id=999999) == (422, "trend_not_found", "orientation.0.trend_id")
    assert err(trend_id=trends["wf"][0]) == (422, "trend_not_found", "orientation.0.trend_id")  # not a trend
    for bad in ("x", 1.5, True, "-1", [1]):
        assert err(trend_id=bad) == (422, "bad_param", "orientation.0.trend_id")
    e = refused(trends, sp.parse_tt, orientation=[{"source": "trend", "trend_id": trends["g"]},
                                                  {"source": "trend", "trend_id": trends["s"]}])  # fmt: skip
    assert (e.code, e.extra["param"]) == ("bad_param", "orientation.1.source")  # a source is chosen once
    e = refused(trends, sp.parse_tt, source="trend")
    assert (e.code, e.extra["param"]) == ("no_orientation", "orientation.0.trend_id")


def test_the_hash_has_no_ids_and_follows_the_trend(trends) -> None:  # type: ignore[no-untyped-def]
    g = trends["g"]
    one = parse(trends, sp.parse_tt, orientation=[{"source": "trend", "trend_id": g}])
    h = one.hash_params()["orientation"][0]
    assert h["source"] == "trend" and h["trend_key"] == "глобальный" and "trend_id" not in repr(h)
    assert h["trend"]["ang1"] == 30 and h["trend"]["ratios"] == [1.0, 0.4, 0.1] and h["trend"]["kind"] == "global"
    other = parse(trends, sp.parse_tt, orientation=[{"source": "trend", "trend_id": trends["s"]}])
    hs = other.hash_params()["orientation"][0]["trend"]
    assert hs["kind"] == "structural" and hs["radius"] == 150 and hs["source"] == {"type": "mesh"}
    with dialect.write_tx(trends["engine"]) as conn:
        ts.update(conn, g, {"ang1": 31, "expected_version": trends["version"]})
    changed = parse(trends, sp.parse_tt, orientation=[{"source": "trend", "trend_id": g}])
    assert changed.hash_params() != one.hash_params()


def test_rebind_finds_the_trend_by_its_name(trends) -> None:  # type: ignore[no-untyped-def]
    g, s = trends["g"], trends["s"]
    one = parse(trends, sp.parse_tt, orientation=[{"source": "trend", "trend_id": g}, *GLOBAL])
    with dialect.read_tx(trends["engine"]) as conn:
        find = sp.trend_lookup(conn)
    assert find(g, "Глобальный") == g and find(s, "Глобальный") == g  # the old id names another trend: by the name
    assert find(777, " ГЛОБАЛЬНЫЙ ") == g and find(777, "Нет такого") == 0
    assert sp.rebind(one.body, [1], [], find)["orientation"][0]["trend_id"] == g
    moved = {**one.body, "orientation": [{"source": "trend", "trend_id": 4242, "trend": "Структурный"}]}
    assert sp.rebind(moved, [1], [], find)["orientation"][0]["trend_id"] == s
    assert sp.rebind(moved, [1], [])["orientation"][0]["trend_id"] == 4242  # no lookup — as they are
    renamed = {**one.body, "orientation": [{"source": "trend", "trend_id": g, "trend": "Прежнее имя"}]}
    assert sp.rebind(renamed, [1], [], find)["orientation"][0]["trend_id"] == g  # the id is alive: it is the trend
    lost = {**one.body, "orientation": [{"source": "trend", "trend_id": 424242, "trend": "Удалён"}]}
    again = sp.rebind(lost, [int(trends["table"])], [], find)
    e = refused({**trends}, sp.parse_tt, orientation=again["orientation"])
    assert (e.status, e.code) == (422, "trend_not_found")
    assert one.body["orientation"][0]["trend_id"] == g  # not changed in place


# --- the job on the project ----------------------------------------------------------------------------------------


def test_the_job_with_a_global_and_a_structural_trend(tj) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, project, set_id, table_id, _holes = tj
    from bf_core.geostat import rotation as rot
    from tests.test_composites_jobs import rows_of

    ang = (30.0, -20.0, 10.0)
    alpha, dipdir, _p = rot.plane_of(*ang)
    ax, ay = 0.3, -0.2
    n = face_normal(ax, ay)
    v, t = plane_mesh(ax, ay, np.array([UTM[0], UTM[1], 0.0]) * 0.0)
    with dialect.write_tx(project.engine) as conn:
        wid = int(ws.put_wireframe(conn, BlobStore(project.folder), v, t, name="Кровля тренда"))
        g = int(ts.create(conn, {"name": "Т глобальный", "ang1": ang[0], "ang2": ang[1], "ang3": ang[2],
                                 "ratios": [1, 0.5, 0.1]})["id"])  # fmt: skip
        s = int(ts.create(conn, {"kind": "structural", "name": "Т структурный",
                                 "source": {"type": "mesh", "wireframe_id": wid}})["id"])  # fmt: skip
    n_alpha = float(np.degrees(np.arccos(n[2])))
    n_dipdir = float(np.degrees(np.arctan2(n[0], n[1])) % 360.0)

    def run(orientation: list[dict[str, Any]]) -> list[Any]:
        tt_here(project, {"body": body(set_id, table_id, orientation=orientation)})
        got = rows_of(c, set_id, table_id)
        order = sorted(range(len(got["rows"])), key=lambda i: got["rows"][i]["id"])
        return [[col(got, name)[i] for i in order] for name in ("TT", "TT_SRC", "ALPHA", "DIPDIR")]

    ref = run([{"source": "global", "alpha": alpha, "dipdir": dipdir}])
    mine = run([{"source": "trend", "trend_id": g}])
    assert set(ref[1]) == {"глобальная"} and set(mine[1]) == {"тренд"}
    assert np.abs(np.array(ref[0], dtype=float) - np.array(mine[0], dtype=float)).max() <= TOL * 100.0
    assert np.abs(np.array(mine[2], dtype=float) - alpha).max() <= 1e-9
    ref = run([{"source": "global", "alpha": n_alpha, "dipdir": n_dipdir}])
    mine = run([{"source": "trend", "trend_id": s}])  # the normal of the face of the wireframe (ТД15-6) at every middle
    assert set(mine[1]) == {"тренд"}
    assert np.abs(np.array(ref[0], dtype=float) - np.array(mine[0], dtype=float)).max() <= 1e-9
    assert np.abs(np.array(mine[2], dtype=float) - n_alpha).max() <= 1e-9


def test_the_import_of_the_orientation_loads_no_numba() -> None:
    code = (
        "import sys\n"
        "import bf_core.intercepts.jobs, bf_core.intercepts.spec, bf_core.intercepts.spec_sources\n"
        "assert 'numba' not in sys.modules\n"
        "assert 'bf_core.geostat.trend' not in sys.modules and 'bf_core.geostat.trend_struct' not in sys.modules\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


# --- the data a structural trend reads (review of 19.29б: a changed measurement is a changed result) ---------------


def test_the_source_trend_of_measurements_follows_its_data(tj) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """The hash and `inputs` of a result hold what a structural trend reads: after an edit of its table of
    measurements the result is «устарел» and «Перестроить» computes again (it took the same hash before)."""
    from bf_core.composites import results
    from bf_core.db.schema import dh_results, dh_tables
    from bf_core.drillholes import store as dh

    _c, project, set_id, table_id, holes = tj
    rows = [(h, d) for h in holes for d in (2.0, 5.0, 8.0)]
    depth = [d for _h, d in rows]
    data = dh.TableData("Замеры тренда", "event", [h for h, _d in rows], depth, depth,
                        [dh.ColumnSpec("ALPHA", "number", "°"), dh.ColumnSpec("AZ_DIP", "number", "°"),
                         dh.ColumnSpec("DEPTH", "number", "м")],
                        [[35.0] * len(rows), [120.0] * len(rows), depth])  # fmt: skip
    meas = int(dh.write_table(project.engine, set_id, data, job_id=None, tick=lambda *_: None)["table_id"])
    with dialect.write_tx(project.engine) as conn:
        tid = int(ts.create(conn, {"kind": "structural", "name": "Т замеры", "radius": 500, "source": {
            "type": "measurements", "set_id": set_id, "table": "Замеры тренда", "alpha": "alpha", "dipdir": "az_dip",
            "depth": "depth"}})["id"])  # fmt: skip
    req = body(set_id, table_id, orientation=[{"source": "trend", "trend_id": tid}])
    first = tt_here(project, {"body": req})
    rid = int(first["result_id"])
    with dialect.read_tx(project.engine) as conn:
        inputs = conn.execute(sa.select(dh_results.c.inputs).where(dh_results.c.id == rid)).scalar_one()
        assert [t["name"] for t in inputs["tables"]] == ["Пробы ГКЗ", "Замеры тренда"]  # the trend's table, last
        assert inputs["tables"][1]["fields"] == ["ALPHA", "AZ_DIP", "DEPTH"]
        assert [r for r in results.list_results(conn, set_id) if r["id"] == rid][0]["stale"] is False
    assert tt_here(project, {"body": req, "rebuild": rid})["fresh"] is True  # nothing changed: the same hash
    with dialect.write_tx(project.engine) as conn:  # a measurement of the trend is edited
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == meas).values(rev=dh_tables.c.rev + 1))
    with dialect.read_tx(project.engine) as conn:
        rec = [r for r in results.list_results(conn, set_id) if r["id"] == rid][0]
        assert rec["stale"] is True and "Замеры тренда" in str(rec["reason"])
    again = tt_here(project, {"body": req, "rebuild": rid})
    assert again["fresh"] is False and int(again["result_id"]) == rid  # computed again, the record follows


def test_the_hash_of_a_trend_of_a_mesh_follows_its_geometry(trends) -> None:  # type: ignore[no-untyped-def]
    one = parse(trends, sp.parse_tt, orientation=[{"source": "trend", "trend_id": trends["s"]}])
    keys = one.hash_params()["orientation"][0]["data"]
    assert isinstance(keys["mesh"], str) and keys["mesh"]
    assert one.orientation[0].trend_meshes() == [trends["wf"][0]]
    with dialect.write_tx(trends["engine"]) as conn:  # the trend is moved to another wireframe: another geometry key
        ts.update(conn, trends["s"], {"source": {"type": "mesh", "wireframe_id": trends["wf"][1]},
                                      "expected_version": 1})  # fmt: skip
    other = parse(trends, sp.parse_tt, orientation=[{"source": "trend", "trend_id": trends["s"]}])
    assert other.hash_params() != one.hash_params()


def test_a_renamed_trend_is_found_by_its_id(trends) -> None:  # type: ignore[no-untyped-def]
    with dialect.write_tx(trends["engine"]) as conn:
        ts.update(conn, trends["g"], {"name": "Новое имя", "expected_version": 1})
    with dialect.read_tx(trends["engine"]) as conn:
        find = sp.trend_lookup(conn)
    assert find(trends["g"], "Глобальный") == trends["g"]  # the id is alive, the name is not the one remembered
    assert find(999_999, "Глобальный") == 0 and find(trends["s"], "Новое имя") == trends["g"]  # by the name first
