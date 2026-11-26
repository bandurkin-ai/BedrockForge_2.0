"""Проектные скважины (подшаг 19.37; ТЗ 7.35 Д-34 ч. 2; ADR 0026 решения 28, 29; e19-api.md §10): the path is
`desurvey` of 7.11 on the stations of the parameters; **ТД34-3** a hole through a target point has the point on its
path (1e−6 m); the forecast by the bodies of a model or of wireframes (as «Модель на скважины» cuts the holes of a
set); the nearest holes at the target against all the holes; «План / факт» (equal paths — 0); the files (`collar.csv`
+ `survey.csv`, DXF); the rows (404 / 409 / 422 / 423), the graph, `.bfggis`; the object is not a hole of a set."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.drillholes import planned as pl
from bf_core.drillholes import planned_eval as pe
from bf_core.drillholes import planned_export as px
from bf_core.drillholes import store as dh
from bf_core.drillholes.desurvey import Collars, Surveys, desurvey
from bf_core.drillholes.trajectory import trajectories
from bf_core.wireframes import store as ws
from tests import test_vario_store as vs
from tests.estutil import W, world
from tests.meshutil import cube, icosphere
from tests.test_drillholes_from_model import (  # the bodies of Д-34 ч. 1
    G,
    layers,
    model_with,
    new_set,
    refused_with,
    spans,
    table,
    wire,
)
from tests.test_drillholes_from_model import go as from_model_go
from tests.test_model_store import _roles  # noqa: F401 - the autouse fixture: the roles of the files

vc = vs.vc  # the core with an open project and the set of `test_vario_store`
API = "/api/v1"
URL = API + "/planned_holes"
TOL = 1e-9
BASE: dict[str, Any] = {"collar": [100.0, 200.0, 50.0], "azimuth": 37.5, "dip": -63.0, "length": 250.0}


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def zeros(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.int64)


def reference(collar: Any, td: float, azimuth: float, dip: float, surveys: Any = None) -> Any:
    """A path as 7.11 makes it for a hole of a set: `desurvey` of one collar (and the stations given)."""
    c = Collars(zeros(1), [collar[0]], [collar[1]], [collar[2]], [td], [azimuth], [dip])
    return desurvey(c, surveys, n_holes=1)


def at(traj: Any, depth: Any) -> np.ndarray:
    d = np.asarray(depth, dtype=np.float64).reshape(-1)
    return traj.positions(zeros(d.shape[0]), d)


# --- the path -----------------------------------------------------------------------------------------------------


def test_a_straight_hole_is_desurvey_of_the_collar_orientation() -> None:
    p = pl.parse(BASE)
    depth = pl.depth_grid(250.0)
    ref = at(reference(BASE["collar"], 250.0, 37.5, -63.0), depth)
    got = at(pl.trajectory(p), depth)
    assert np.abs(got - ref).max() <= TOL
    # the direction is the one of 7.11: azimuth from the north clockwise, the dip down is negative
    u = np.array([math.sin(math.radians(37.5)) * math.cos(math.radians(63)),
                  math.cos(math.radians(37.5)) * math.cos(math.radians(63)), -math.sin(math.radians(63))])  # fmt: skip
    assert np.abs(got[-1] - (np.array(BASE["collar"]) + 250.0 * u)).max() <= TOL
    xyz = pl.path(pl.trajectory(p), 250.0)
    assert xyz["depth"][:3] == [0.0, 1.0, 2.0] and xyz["depth"][-1] == 250.0 and len(xyz["depth"]) == 251


def test_a_hole_that_turns_is_desurvey_on_the_stations_every_10_m() -> None:
    raw = {**BASE, "length": 255.0, "dip": -80.0, "azimuth": 350.0, "change": {"azimuth": 4.0, "dip": 7.0}}
    p = pl.parse(raw)
    depth = np.append(np.arange(0.0, 251.0, 10.0), 255.0)
    az = np.mod(350.0 + 4.0 * depth / 100.0, 360.0)  # wraps through the north
    dip = np.clip(-80.0 + 7.0 * depth / 100.0, -90.0, 90.0)
    surveys = Surveys(zeros(depth.shape[0]), depth, az, dip)
    ref = reference(BASE["collar"], 255.0, 350.0, -80.0, surveys)
    grid = pl.depth_grid(255.0)
    assert np.abs(at(pl.trajectory(p), grid) - at(ref, grid)).max() <= TOL
    assert grid[-1] == 255.0 and grid[-2] == 254.0
    d, a, s = pl.stations(p)
    assert d.tolist() == depth.tolist() and np.abs(a - az).max() == 0.0 and np.abs(s - dip).max() == 0.0
    assert a.min() >= 0.0 and a.max() < 360.0
    # the clamp at the vertical: the dip never goes past −90 / +90
    steep = pl.parse({**BASE, "dip": -85.0, "length": 100.0, "change": {"azimuth": 0.0, "dip": -30.0}})
    assert pl.stations(steep)[2].min() == -90.0
    # the path really turns: not the straight one
    straight = at(pl.trajectory(pl.parse({**raw, "change": None})), [255.0])
    assert np.abs(at(pl.trajectory(p), [255.0]) - straight).max() > 1.0


def test_td34_3_a_hole_through_the_target_has_the_point_on_its_path() -> None:
    rng = np.random.default_rng(34)
    worst = 0.0
    for k in range(200):
        collar = rng.uniform(-5000.0, 5000.0, 3)
        reach = 10.0 ** rng.uniform(0.0, 3.7)
        u = rng.normal(size=3)
        target = collar + reach * u / np.linalg.norm(u)
        p = pl.parse({"collar": collar.tolist(), "target": target.tolist(), "set_id": None})
        assert p.target_depth == pytest.approx(reach, abs=1e-9) and p.length == p.target_depth
        got = at(pl.trajectory(p), [p.target_depth])[0]
        worst = max(worst, float(np.abs(got - target).max()))
        if k % 5 == 0:  # a longer hole goes on past the point
            longer = pl.parse({"collar": collar.tolist(), "target": target.tolist(), "length": p.length + 37.0})
            assert np.abs(at(pl.trajectory(longer), [p.target_depth])[0] - target).max() <= 1e-6
    assert worst <= 1e-6, worst
    for target in ([0.0, 0.0, -300.0], [0.0, 0.0, 300.0], [250.0, 0.0, 0.0], [0.0, -250.0, 0.0], [-3.0, -4.0, 0.0]):
        p = pl.parse({"collar": [0.0, 0.0, 0.0], "target": target})  # straight down, straight up, due east, …
        assert 0.0 <= p.azimuth < 360.0 and -90.0 <= p.dip <= 90.0
        assert np.abs(at(pl.trajectory(p), [p.target_depth])[0] - target).max() <= 1e-6
    down = pl.parse({"collar": [0.0, 0.0, 0.0], "target": [0.0, 0.0, -300.0], "azimuth": 135.0})
    assert (down.dip, down.azimuth, down.length) == (-90.0, 135.0, 300.0)  # a vertical line keeps the azimuth given
    assert pl.parse({"collar": [0.0, 0.0, 0.0], "target": [-3.0, -4.0, 0.0]}).azimuth == pytest.approx(
        math.degrees(math.atan2(-3.0, -4.0)) % 360.0)  # fmt: skip


def test_the_files_give_a_set_the_same_path(tmp_path: Path) -> None:
    for raw in (BASE, {**BASE, "change": {"azimuth": -9.0, "dip": 5.5}, "length": 333.3}):
        p = pl.parse(raw)
        folder = tmp_path / ("a" if "change" in raw else "b")
        folder.mkdir()
        done = px.export("ПС-1", p, "csv", str(folder), delimiter=",", decimal=".")
        assert [Path(f).name for f in done["files"]] == ["collar.csv", "survey.csv"]
        collar = list(csv.reader((folder / "collar.csv").open(encoding="utf-8-sig"), delimiter=","))
        survey = list(csv.reader((folder / "survey.csv").open(encoding="utf-8-sig"), delimiter=","))
        assert collar[0] == list(px.COLLAR_HEAD) and survey[0] == list(px.SURVEY_HEAD)
        assert len(collar) == 2 and collar[1][0] == "ПС-1" and len(survey) - 1 == done["points"]
        x, y, z, td, az, dip = (float(v) for v in collar[1][1:])
        rows = np.array([[float(v) for v in r[1:]] for r in survey[1:]])
        back = reference((x, y, z), td, az, dip, Surveys(zeros(len(rows)), rows[:, 0], rows[:, 1], rows[:, 2]))
        grid = pl.depth_grid(p.length)
        assert np.abs(at(back, grid) - at(pl.trajectory(p), grid)).max() == 0.0  # the numbers are the same float64


# --- the checks of the parameters ---------------------------------------------------------------------------------


def test_the_parameters_are_checked_with_code_message_and_hint() -> None:
    def refused(raw: dict[str, Any], code: str, param: str) -> ApiError:
        return refused_with(lambda: pl.parse(raw), 422, code, param)

    for key, bad_value in (("azimuth", 360.0), ("azimuth", -1.0), ("azimuth", "90"), ("dip", 91.0), ("dip", math.nan),
                           ("length", 0.0), ("length", 10_000.1), ("length", True), ("collar", [1.0, 2.0]),
                           ("collar", [1.0, 2.0, math.inf])):  # fmt: skip
        refused({**BASE, key: bad_value}, "bad_param", key)
    refused({**BASE, "change": {"azimuth": 31.0, "dip": 0.0}}, "bad_param", "change.azimuth")
    refused({**BASE, "change": {"azimuth": 0.0, "dip": -30.5}}, "bad_param", "change.dip")
    refused({**BASE, "change": [1, 2]}, "bad_param", "change")
    refused({**BASE, "change": {"azimuth": 1.0, "dip": 1.0}, "target": [1.0, 2.0, 3.0]}, "bad_param", "change")
    refused({**BASE, "method": "spline"}, "bad_param", "method")
    refused({**BASE, "set_id": 0}, "bad_param", "set_id")
    refused({"collar": [0.0, 0.0, 0.0], "dip": -60.0, "length": 100.0}, "bad_param", "azimuth")
    refused({"collar": [0.0, 0.0, 0.0], "azimuth": 1.0, "dip": -60.0}, "bad_param", "length")
    refused({"collar": [0.0, 0.0, 0.0], "target": [0.0, 0.0, 0.0]}, "target_at_collar", "target")
    refused({"collar": [0.0, 0.0, 0.0], "target": [0.0, 0.0, -20_000.0]}, "target_beyond_length", "target")
    err = refused({"collar": [0.0, 0.0, 0.0], "target": [30.0, 40.0, 0.0], "length": 49.0}, "target_beyond_length",
                  "length")  # fmt: skip
    assert err.extra["need"] == pytest.approx(50.0) and "50" in err.hint
    pl.parse({"collar": [0.0, 0.0, 0.0], "target": [30.0, 40.0, 0.0], "length": 50.0})  # exactly: allowed
    for method in pl.METHODS:  # the methods of 7.11
        assert pl.parse({**BASE, "method": method}).method == method
    merged = pl.parse({"length": 300.0}, pl.parse(BASE).meta())  # a PUT changes the keys given only
    assert (merged.length, merged.azimuth, merged.collar) == (300.0, 37.5, (100.0, 200.0, 50.0))
    swapped = pl.parse({"target": [0.0, 0.0, -50.0]}, pl.parse({**BASE, "change": {"azimuth": 1.0, "dip": 1.0}}).meta())
    assert swapped.change is None and swapped.target == (0.0, 0.0, -50.0)  # a mode named takes the other one away
    assert pl.parse(pl.parse(BASE).meta()) == pl.parse(BASE)  # the meta reads back as it was


# --- the forecast -------------------------------------------------------------------------------------------------


def hole_of(set_holes: list[dh.CollarRow], p: pl.Planned) -> dh.CollarRow:
    return dh.CollarRow("P", x=p.collar[0], y=p.collar[1], z=p.collar[2], td=p.length, azimuth=p.azimuth, dip=p.dip)


def test_the_forecast_of_two_layers_has_the_contacts_for_the_boundaries_and_the_gaps_are_empty(w: W) -> None:
    upper, lower = layers()
    ident = model_with(w, [("Верх", 1, upper, {}), ("Низ", 2, lower, {})])
    p = pl.parse({"collar": [25.0, 25.0, 10.0], "azimuth": 0.0, "dip": -90.0, "length": 120.0})
    out = pe.forecast(w.project, pl.trajectory(p), p.length, {"model_id": ident})
    got = [(i["from"], i["to"], i["code"]) for i in out["intervals"]]
    want = [(0.0, 10.0, None), (10.0, 60.0, "Группа 1"), (60.0, 110.0, "Группа 2"), (110.0, 120.0, None)]
    assert len(got) == 4 and all(
        g[2] == x[2] and abs(g[0] - x[0]) <= TOL and abs(g[1] - x[1]) <= TOL for g, x in zip(got, want, strict=True)
    ), got
    assert got[0][0] == 0.0 and got[-1][1] == 120.0  # the collar and the end of the hole, bit for bit
    assert [(c["code"], round(c["length"], 6)) for c in out["codes"]] == [("Группа 1", 50.0), ("Группа 2", 50.0)]
    assert out["inside"] == pytest.approx(100.0) and out["length"] == 120.0
    # the same bodies cut a hole of a set the same way: the table of «Модель на скважины»
    set_id = new_set(w, [dh.CollarRow("V-1", x=25.0, y=25.0, z=10.0, td=120.0, azimuth=0.0, dip=-90.0)])
    done = from_model_go(w, {"set_id": set_id, "model_id": ident, "name": "Те же тела"})
    again = spans(table(w, set_id, done["table_id"]), "V-1")
    assert [(a, b, c) for a, b, c in again] == [(i["from"], i["to"], i["code"]) for i in out["intervals"]]
    # a hole beside the bodies meets none: one empty interval, never a code
    far = pl.parse({"collar": [900.0, 900.0, 10.0], "azimuth": 0.0, "dip": -90.0, "length": 120.0})
    miss = pe.forecast(w.project, pl.trajectory(far), far.length, {"model_id": ident})
    assert miss["intervals"] == [{"from": 0.0, "to": 120.0, "code": None}] and miss["inside"] == 0.0


def test_the_forecast_of_a_wireframe_through_a_sphere_is_100_m_and_the_codes_are_named(w: W) -> None:
    centre = np.array([200.0, 300.0, -40.0])
    u = np.array([G, 0.0, -1.0]) / math.sqrt(G * G + 1.0)
    collar = centre - 80.0 * u
    ball = wire(w, "Шар", icosphere(2, 50.0, centre))
    p = pl.parse({"collar": collar.tolist(), "azimuth": 90.0, "dip": -math.degrees(math.asin(-u[2])), "length": 200.0})
    out = pe.forecast(w.project, pl.trajectory(p), p.length, {"mesh_ids": [ball], "codes": {str(ball): "Руда"}})
    rows = out["intervals"]
    assert [r["code"] for r in rows] == [None, "Руда", None]
    assert rows[1]["to"] - rows[1]["from"] == pytest.approx(100.0, abs=1e-6)
    assert rows[1]["from"] == pytest.approx(30.0, abs=1e-6) and rows[2]["to"] == 200.0
    a = pe.forecast(w.project, pl.trajectory(p), p.length, {"mesh_ids": [ball]})
    assert [r["code"] for r in a["intervals"]] == [None, "Шар", None] and a["intervals"][1]["from"] == rows[1]["from"]
    # the refusals of the request, as the table of the set has them
    traj = pl.trajectory(p)
    refused_with(lambda: pe.forecast(w.project, traj, 200.0, {}), 422, "bad_param", "model_id")
    refused_with(lambda: pe.forecast(w.project, traj, 200.0, {"model_id": 1, "mesh_ids": [ball]}), 422, "bad_param")
    refused_with(lambda: pe.forecast(w.project, traj, 200.0, {"model_id": 424242}), 404, "model_not_found")
    refused_with(lambda: pe.forecast(w.project, traj, 200.0, {"mesh_ids": [424242]}), 422, "wireframe_not_found")
    refused_with(lambda: pe.forecast(w.project, traj, 200.0, {"mesh_ids": []}), 422, "bad_param")
    flat = wire(w, "Плоскость", cube(2, 20.0, (0.0, 0.0, 0.0), seed=None))
    with dialect.write_tx(w.project.engine) as conn:  # an open surface is no body
        sheet = ws.put_wireframe(conn, w.store, np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]]), np.array([[0, 1, 2]]),
                                 name="Лист")  # fmt: skip
    refused_with(lambda: pe.forecast(w.project, traj, 200.0, {"mesh_ids": [sheet]}), 422, "wireframe_open")
    assert flat != sheet


# --- the nearest holes --------------------------------------------------------------------------------------------


def random_set(rng: np.random.Generator, n: int, area: float = 1000.0) -> Any:
    """`SetTrajectories`-like: `n` holes with surveys (a few stations, curved); some have no coordinates."""
    x, y = rng.uniform(0, area, n), rng.uniform(0, area, n)
    z = rng.uniform(0, 100.0, n)
    td = rng.uniform(20.0, 400.0, n)
    az, dip = rng.uniform(0, 360, n), rng.uniform(-90, -30, n)
    x[::37] = np.nan  # no collar coordinates: no path
    hole, depth, saz, sdip = [], [], [], []
    for h in range(n):
        for d in sorted(rng.uniform(0.0, td[h], int(rng.integers(0, 5)))):
            hole.append(h), depth.append(d), saz.append(rng.uniform(0, 360)), sdip.append(rng.uniform(-90, -20))
    traj = desurvey(Collars(np.arange(n), x, y, z, td, az, dip),
                    Surveys(np.array(hole, dtype=np.int64), depth, saz, sdip), n_holes=n)  # fmt: skip
    ids = np.arange(1000, 1000 + n, dtype=np.int64)
    return SimpleNamespace(
        key=("random", n, int(rng.integers(1 << 30))),
        traj=traj,
        hole_ids=ids,
        names=[f"H-{i}" for i in range(n)],
        index={int(v): i for i, v in enumerate(ids)},
    )


def brute(traj: Any, point: np.ndarray) -> list[tuple[int, float, float]]:
    """Every hole against the point, one segment at a time in plain Python."""
    out = []
    for h in range(traj.n_holes):
        md, xyz, _dirs = traj.stations(h)
        if len(md) == 0:
            continue
        best = (math.dist(point, xyz[0]), float(md[0]))
        for i in range(len(md) - 1):
            a, b = xyz[i], xyz[i + 1]
            ab = b - a
            t = 0.0 if not ab @ ab else float(np.clip((point - a) @ ab / (ab @ ab), 0.0, 1.0))
            d = math.dist(point, a + t * ab)
            if d < best[0]:
                best = (d, float(md[i] + t * (md[i + 1] - md[i])))
        out.append((h, best[0], best[1]))
    return sorted(out, key=lambda r: (r[1], r[0]))


def test_the_nearest_holes_are_those_of_a_search_through_all_the_holes() -> None:
    rng = np.random.default_rng(103)
    st = random_set(rng, 400)
    index = pe.index_of(st)
    points = [rng.uniform(-200, 1200, 3) * np.array([1, 1, 0.3]) for _ in range(40)]
    points += [np.array([1e5, -1e5, 0.0]), st.traj.xyz[5].copy(), st.traj.xyz[-1].copy()]  # far away, on a station
    for point in points:
        want = brute(st.traj, point)[:5]
        got = pe.nearest(index, st.traj, point)
        assert [g[0] for g in got] == [w_[0] for w_ in want]
        assert max(abs(g[1] - w_[1]) for g, w_ in zip(got, want, strict=True)) <= TOL
        assert max(abs(g[2] - w_[2]) for g, w_ in zip(got, want, strict=True)) <= 1e-6
    rows = pe.nearest_rows(st, points[0])
    assert len(rows) == 5 and set(rows[0]) == {"hole", "hole_id", "distance", "depth"}
    assert rows[0]["hole_id"] == 1000 + int(rows[0]["hole"][2:]) and rows == sorted(rows, key=lambda r: r["distance"])
    assert len(pe.nearest(index, st.traj, points[0], k=3)) == 3


def test_a_long_straight_hole_with_stations_far_away_is_found_and_few_holes_are_few_rows() -> None:
    # one 3 km hole whose two stations are 1.5 km from the point, 3 m from the point at the middle; the rest are short
    n = 60
    x = np.concatenate(([0.0], np.linspace(10.0, 590.0, n - 1)))
    y = np.concatenate(([-1500.0], np.linspace(-40.0, 40.0, n - 1)))
    td = np.concatenate(([3000.0], np.full(n - 1, 50.0)))
    dip = np.concatenate(([0.0], np.full(n - 1, -90.0)))
    traj = desurvey(Collars(np.arange(n), x, y, np.zeros(n), td, np.zeros(n), dip), n_holes=n)
    st = SimpleNamespace(key=("long", 1), traj=traj, hole_ids=np.arange(n), names=[f"L-{i}" for i in range(n)])
    point = np.array([3.0, 0.0, 0.0])  # the long hole runs north along x = 0, z = 0
    got = pe.nearest(pe.index_of(st), traj, point)
    want = brute(traj, point)[:5]
    assert got[0][0] == 0 and got[0][1] == pytest.approx(3.0) and got[0][2] == pytest.approx(1500.0)
    assert [g[0] for g in got] == [w_[0] for w_ in want]
    two = desurvey(Collars(np.arange(2), [0.0, np.nan], [0.0, 0.0], [0.0, 0.0], [10.0, 10.0], [0.0, 0.0],
                           [-90.0, -90.0]), n_holes=3)  # fmt: skip
    assert len(pe.nearest(pe.build_index(two), two, point)) == 1  # a hole without a path is not a hole
    empty = desurvey(Collars(zeros(0), [], [], [], [], [], []), n_holes=0)
    assert pe.nearest(pe.build_index(empty), empty, point) == []
    pe.forget()


# --- «План / факт» ------------------------------------------------------------------------------------------------


def survey_rows_of(name: str, p: pl.Planned) -> list[tuple[Any, ...]]:
    d, a, s = pl.stations(p)
    return [(name, float(i), float(j), float(k)) for i, j, k in zip(d, a, s, strict=True)]


def test_plan_and_fact_of_equal_paths_is_zero_and_a_shifted_hole_is_by_the_shift(w: W) -> None:
    p = pl.parse({**BASE, "length": 180.5})
    q = pl.parse({**BASE, "length": 180.5, "change": {"azimuth": 3.0, "dip": -2.0}})
    shifted = pl.parse({**BASE, "collar": [103.0, 204.0, 50.0], "length": 180.0})
    holes = [
        dh.CollarRow(n, x=m.collar[0], y=m.collar[1], z=m.collar[2], td=m.length, azimuth=m.azimuth, dip=m.dip)
        for n, m in (("EQ", p), ("TURN", q), ("SHIFT", shifted), ("LONG", pl.parse({**BASE, "length": 400.0})))
    ]
    holes.append(dh.CollarRow("NOXYZ"))
    set_id = new_set(w, holes)
    dh.replace_surveys(w.project.engine, set_id, survey_rows_of("TURN", q), lambda _f, _m: None)
    st = trajectories(w.project, set_id)
    traj = pl.trajectory(p)
    same = pe.compare(traj, p.length, st, pe.find_hole(st, {"hole": "EQ"}))
    assert same["max"] == 0.0 and all(r["d"] == 0.0 and r["dx"] == 0.0 for r in same["rows"])
    assert [r["depth"] for r in same["rows"]][:3] == [0.0, 1.0, 2.0] and same["rows"][-1]["depth"] == 180.5
    assert same["length"] == {"planned": 180.5, "actual": 180.5, "compared": 180.5} and same["hole"] == "EQ"
    turned = pe.compare(pl.trajectory(q), q.length, st, pe.find_hole(st, {"hole": "TURN"}))
    assert turned["max"] <= TOL  # the same stations: the same path
    far = pe.compare(traj, p.length, st, pe.find_hole(st, {"hole_id": int(st.hole_ids[2])}))  # SHIFT, by id
    assert far["hole"] == "SHIFT" and far["length"]["compared"] == 180.0 and len(far["rows"]) == 181
    first = far["rows"][0]
    assert (first["dx"], first["dy"], first["dz"]) == (3.0, 4.0, 0.0) and first["d"] == 5.0
    assert far["max"] == pytest.approx(5.0) and all(r["d"] == pytest.approx(5.0, abs=1e-9) for r in far["rows"])
    long_ = pe.compare(traj, p.length, st, pe.find_hole(st, {"hole": "long"}))  # a name by case-insensitive match
    assert long_["length"]["actual"] == 400.0 and long_["length"]["compared"] == 180.5 and long_["max"] <= TOL
    refused_with(lambda: pe.find_hole(st, {"hole": "нет такой"}), 404, "hole_not_found")
    refused_with(lambda: pe.find_hole(st, {"hole_id": 1}), 404, "hole_not_found")
    refused_with(lambda: pe.find_hole(st, {"hole": "NOXYZ"}), 422, "hole_no_trajectory")
    refused_with(lambda: pe.find_hole(st, {}), 422, "bad_param", "hole")


# --- the files ----------------------------------------------------------------------------------------------------


def test_csv_and_dxf_files_the_formats_and_the_refusals(tmp_path: Path) -> None:
    p = pl.parse({**BASE, "change": {"azimuth": 1.5, "dip": 0.5}})
    done = px.export("ПС-1", p, "csv", str(tmp_path))
    text = (tmp_path / "survey.csv").read_bytes()
    assert text.startswith(b"\xef\xbb\xbf") and b"HOLEID;DEPTH;AZIMUTH;DIP" in text  # «;», utf-8-sig
    assert done["points"] == 26 and done["size"] == sum(f.stat().st_size for f in tmp_path.iterdir())
    line = text.decode("utf-8-sig").splitlines()[2]
    assert line.startswith("ПС-1;10,0;") and "." not in line  # the decimal is a comma, as in the export of tables
    err = refused_with(lambda: px.export("ПС-1", p, "csv", str(tmp_path)), 409, "files_exist")
    assert err.extra["files"] == ["collar.csv", "survey.csv"]
    px.export("ПС-1", p, "csv", str(tmp_path), overwrite=True, delimiter="\t", decimal=".", encoding="cp1251")
    assert (tmp_path / "collar.csv").read_bytes().split(b"\r\n")[0] == b"HOLEID\tEAST\tNORTH\tRL\tDEPTH\tAZIMUTH\tDIP"
    assert not list(tmp_path.glob("*.part"))
    refused_with(lambda: px.export("a", p, "csv", str(tmp_path / "нет")), 404, "folder_not_found")
    refused_with(lambda: px.export("a", p, "csv", "relative"), 422, "path_not_absolute")
    refused_with(lambda: px.export("a", p, "shp", str(tmp_path)), 422, "bad_format")
    refused_with(lambda: px.export("a", p, "csv", str(tmp_path), overwrite=True, delimiter=";", decimal=";"), 422,
                 "bad_param", "delimiter")  # fmt: skip
    refused_with(lambda: px.export("ПС-1", p, "csv", str(tmp_path), overwrite=True, encoding="ascii"), 422, "bad_param",
                 "encoding")  # fmt: skip
    dxf = px.export("ПС-1", p, "dxf", str(tmp_path / "Путь"))
    out = Path(dxf["files"][0])
    assert out.name == "Путь.dxf" and dxf["points"] == 251 and dxf["size"] == out.stat().st_size
    body = out.read_text(encoding="utf-8", errors="replace")
    assert "POLYLINE" in body and "ENTITIES" in body and "EOF" in body
    refused_with(lambda: px.export("ПС-1", p, "dxf", str(tmp_path / "Путь.dxf")), 409, "files_exist")
    assert px.export("ПС-1", p, "dxf", str(tmp_path / "Путь.dxf"), overwrite=True)["points"] == 251
    assert not list(tmp_path.glob("*.part"))
