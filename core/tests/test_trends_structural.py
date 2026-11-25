"""The structural trend (подшаг 19.8; ТЗ 7.35 Д-15; ADR 0026, решения 11, 27, вопрос № 97).

ТД15-5 a trend by the planes of the measurements gives the normal of the plane (1e−12); ТД15-6 a trend by a wireframe
gives the normal of its face (1e−12). Also: the weights 1 / max(d, 1e−6 m) and the radius R_тр, the nearest
measurement beyond R_тр, the same bits with 1, 2, 4 and 8 threads, the axes by strike / dip / normal, the stored trend
(create, read, change, the refusals), the discs, `trend_structural` of the estimation, the graph of dependencies,
`.bfggis` (ids of the set, table and wireframe), 409 `in_use`. ТД15-1…4 of Э16 stay in `test_trends.py`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core import lineage
from bf_core.db import dialect
from bf_core.drillholes import store as dh
from bf_core.geo.intersect import MeshTarget
from bf_core.geostat import remap, rotation
from bf_core.geostat import trend as tr
from bf_core.geostat import trend_store as ts
from bf_core.geostat import trend_struct as st
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as ws
from tests import test_vario_store as vs
from tests.meshutil import grid
from tests.test_import_job import wait_job
from tests.test_lineage import insert_object
from tests.test_trends import API, TR, consumer

vc = vs.vc  # the core with an open project and the set of `test_vario_store` (holes A-1, B-2, C-3 — vertical, z 100)
_roles = vs._roles  # autouse

TOL = 1e-12
ZERO = np.zeros(3)
HOLE_XY = {"A-1": (0.0, 0.0), "B-2": (100.0, 0.0), "C-3": (0.0, 100.0)}


def noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def ref_frame(dip: float, dipdir: float) -> np.ndarray:
    """R_j as the rest of the system makes it: `rotation_matrix` of `angles_of_plane(α, Aп, pitch 0)`."""
    return rotation.rotation_matrix(*rotation.angles_of_plane(dip, dipdir, 0.0))


def meta_of(radius: float = 200.0, **extra: Any) -> dict[str, Any]:
    return {"v": 1, "kind": "structural", "radius": radius, "ratios": [1.0, 0.5, 0.1], **extra}


def measured(alpha: Any, dipdir: Any, xyz: Any) -> st.Measurements:
    return st.Measurements(np.asarray(xyz, dtype=np.float64), st.turn_up(st.plane_normals(alpha, dipdir)))


def random_points(n: int, seed: int = 7, around: Any = (512_345.678, 6_234_567.123, 412.3), half: float = 400.0) -> Any:
    return np.asarray(around) + np.random.default_rng(seed).uniform(-half, half, (n, 3))


# --- ТД15-5: the measurements ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("dip, dipdir", [(35.0, 120.0), (60.0, 300.0), (10.0, 0.0), (89.0, 359.5), (90.0, 45.0),
                                         (0.0, 0.0), (72.25, 181.125)])  # fmt: skip
def test_td15_5_one_plane_gives_its_normal(dip: float, dipdir: float) -> None:
    pts = random_points(40, 1)
    src = measured(np.full(40, dip), np.full(40, dipdir), pts)
    q = random_points(300, 2, half=300.0)
    rep: dict[str, Any] = {}
    frames = st.local_axes(meta_of(), q, source=src, report=rep)
    want = ref_frame(dip, dipdir)
    assert np.abs(frames - want).max() <= TOL  # strike, dip, normal: the rows of the matrix of the angles of the plane
    s, d = np.sin(np.radians(dip)), np.cos(np.radians(dip))
    n = np.array([s * np.sin(np.radians(dipdir)), s * np.cos(np.radians(dipdir)), d])
    assert np.abs(frames[:, 2, :] - n).max() <= TOL  # the normal is the normal of the plane
    assert np.abs(np.einsum("mij,mkj->mik", frames, frames) - np.eye(3)).max() <= TOL  # a rotation
    assert np.abs(np.linalg.det(frames) - 1.0).max() <= TOL
    assert rep["points"] == 300 and rep["measurements"] == 40
    assert np.abs(frames[:, 0, 2]).max() <= TOL  # the strike is horizontal
    assert (frames[:, 1, 2] >= -TOL).all()  # the line of dip, its sign turned so that the frame is right-handed (n × s)


def test_td15_5_the_matrix_of_the_trend_at_a_point_is_a_of_the_global_trend() -> None:
    """A_j = diag(1, 1 / r2, 1 / r3)·R_j is the matrix of the global trend of the same plane (`trend.matrix`)."""
    src = measured([40.0] * 5, [75.0] * 5, random_points(5, 3))
    r = st.local_axes(meta_of(), random_points(4, 4), source=src)
    ang = rotation.angles_of_plane(40.0, 75.0, 0.0)
    glob = tr.matrix(tr.shape(*ang, [1.0, 0.5, 0.1]))
    for rj in r:
        assert np.abs(np.diag([1.0, 1 / 0.5, 1 / 0.1]) @ rj - glob).max() <= TOL


def principal(normals: np.ndarray, w: np.ndarray) -> np.ndarray:
    m = np.einsum("k,ki,kj->ij", w, normals, normals)
    vals, vecs = np.linalg.eigh(m)
    return vecs[:, int(np.argmax(vals))]  # type: ignore[no-any-return]


def test_the_normal_is_the_principal_eigenvector_of_the_weighted_sum() -> None:
    rng = np.random.default_rng(11)
    pts = random_points(400, 5, half=500.0)
    alpha, dipdir = rng.uniform(5, 85, 400), rng.uniform(0, 360, 400)
    src = measured(alpha, dipdir, pts)
    q = random_points(60, 6, half=450.0)
    radius = 180.0
    rep: dict[str, Any] = {}
    got = st.local_axes(meta_of(radius), q, source=src, report=rep)[:, 2, :]
    for i, p in enumerate(q):
        d = np.linalg.norm(src.xyz - p, axis=1)
        near = d <= radius
        if near.any():
            n = principal(src.normal[near], 1.0 / np.maximum(d[near], 1e-6))
        else:  # beyond R_тр: the nearest measurement
            n = src.normal[int(np.argmin(d))]
        n = st.turn_up(n[None, :])[0]
        assert abs(float(n @ got[i])) >= 1.0 - 1e-12 and np.abs(n - got[i]).max() <= 1e-9, i
    assert rep["nearest"] >= 0 and (rep["nearest"] == 0 or rep["journal"])


def test_the_weights_are_one_over_the_distance_and_the_radius_cuts() -> None:
    # two planes: a near one dominates (w = 1 / d), a far one beyond R_тр is not in the sum at all
    src = measured([30.0, 70.0, 20.0], [90.0, 270.0, 10.0], [[1.0, 0.0, 0.0], [100.0, 0.0, 0.0], [500.0, 0.0, 0.0]])
    q = np.array([[0.0, 0.0, 0.0]])
    got = st.local_axes(meta_of(150.0), q, source=src)[0, 2]
    n1, n2 = src.normal[0], src.normal[1]
    want = principal(np.array([n1, n2]), np.array([1.0 / 1.0, 1.0 / 100.0]))
    assert abs(float(want @ got)) >= 1.0 - 1e-12
    only_far = st.local_axes(meta_of(150.0), np.array([[500.0, 0.0, 400.0]]), source=src)[0, 2]  # nothing within R_тр
    assert np.abs(only_far - src.normal[2]).max() <= TOL  # the nearest measurement (the one at 400 m)
    on_top = st.local_axes(meta_of(150.0), np.array([[100.0, 0.0, 0.0]]), source=src)[0, 2]  # d = 0 → w = 1e6
    assert np.abs(on_top - src.normal[1]).max() <= 1e-5  # the measurement right there wins by the weight 1 / 1e−6


def test_a_point_without_coordinates_gets_nan_rows() -> None:
    src = measured([30.0], [90.0], [[0.0, 0.0, 0.0]])
    q = np.array([[0.0, 0.0, 1.0], [np.nan, 0.0, 0.0], [1.0, 2.0, 3.0]])
    got = st.local_axes(meta_of(), q, source=src)
    assert np.isnan(got[1]).all() and np.isfinite(got[0]).all() and np.isfinite(got[2]).all()  # never a zero frame


def test_threads_give_the_same_bits() -> None:
    rng = np.random.default_rng(3)
    src = measured(rng.uniform(5, 85, 3000), rng.uniform(0, 360, 3000), random_points(3000, 8, half=700.0))
    q = random_points(70_000, 9, half=700.0)  # more than one band
    base = st.local_axes(meta_of(120.0), q, source=src, threads=1)
    for threads in (2, 4, 8):
        assert st.local_axes(meta_of(120.0), q, source=src, threads=threads).tobytes() == base.tobytes()


def test_tick_may_cancel() -> None:
    src = measured([30.0], [90.0], [[0.0, 0.0, 0.0]])
    seen: list[float] = []

    def tick(f: float) -> None:
        seen.append(f)
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError):
        st.local_axes(meta_of(), np.zeros((70_000, 3)), source=src, tick=tick)
    assert seen and seen[0] < 1.0


def test_the_measurement_arrays_of_a_plane() -> None:
    n = st.plane_normals([0.0, 90.0, 45.0, 91.0, np.nan, 30.0], [10.0, 90.0, 360.0, 10.0, 10.0, np.nan])
    assert n[0].tolist() == [0.0, 0.0, 1.0] and n[1].tolist() == [1.0, 0.0, 0.0]  # exact on the multiples of 90°
    assert abs(n[2][0]) <= 1e-12 and abs(n[2][1] - np.sqrt(0.5)) <= 1e-15  # Aп = 360 is 0
    assert np.isnan(n[3:]).all()  # outside the ranges or missing: NaN, never a zero
    up = st.turn_up(np.array([[0.0, 0.0, -1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.6, 0.0, -0.8]]))
    assert up.tolist() == [[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [-0.6, 0.0, 0.8]]


def test_local_axes_refuses_what_it_cannot_read() -> None:
    src = measured([30.0], [90.0], [[0.0, 0.0, 0.0]])
    with pytest.raises(ValueError):
        st.local_axes({"kind": "global"}, np.zeros((1, 3)), source=src)
    with pytest.raises(ValueError):
        st.local_axes(meta_of(), np.zeros((3,)), source=src)
    empty = st.Measurements(np.zeros((0, 3)), np.zeros((0, 3)))
    with pytest.raises(ValueError, match="пригодных"):
        st.local_axes(meta_of(), np.zeros((2, 3)), source=empty)
    for bad in (5.0, 5001.0, "x"):
        with pytest.raises(ValueError):
            st.radius_of(bad)
    assert st.radius_of(None) == 200.0 and st.radius_of("350,5") == 350.5


# --- ТД15-6: the wireframe -------------------------------------------------------------------------------------------


def plane_mesh(ax: float, ay: float, offset: Any = ZERO, seed: int | None = 0) -> tuple[np.ndarray, np.ndarray]:
    return grid(24, 24, lambda x, y: ax * x + ay * y, 10.0, offset, seed)  # type: ignore[no-any-return]


def face_normal(ax: float, ay: float) -> np.ndarray:
    n = np.array([-ax, -ay, 1.0])
    return n / np.linalg.norm(n)  # type: ignore[no-any-return]


@pytest.mark.parametrize("r_n", [0.0, 15.0])
def test_td15_6_a_plane_wireframe_gives_the_normal_of_its_face(r_n: float) -> None:
    ax, ay = 0.3, -0.2
    v, t = plane_mesh(ax, ay)
    src = st.MeshNormals(MeshTarget(v, t), r_n)
    q = np.random.default_rng(5).uniform([10.0, 10.0, -30.0], [230.0, 230.0, 90.0], (500, 3))  # above and below it
    frames = st.local_axes(meta_of(), q, source=src)
    n = face_normal(ax, ay)
    assert np.abs(frames[:, 2, :] - n).max() <= TOL
    dip = float(np.degrees(np.arccos(n[2])))
    dipdir = float(np.degrees(np.arctan2(n[0], n[1])) % 360.0)
    assert np.abs(frames - ref_frame(dip, dipdir)).max() <= 1e-10  # the frame of the angles of the plane
    assert (frames[:, 2, 2] > 0).all()  # the upper normal, whichever side of the wireframe the point is on


def test_td15_6_a_dome_takes_the_face_nearest_to_the_point_and_r_n_averages() -> None:
    from tests.meshutil import icosphere

    v, t = icosphere(3, 100.0, ZERO)
    mesh = MeshTarget(v, t)
    q = np.array([[0.0, 0.0, 150.0], [100.0, 100.0, 100.0], [-130.0, 10.0, 20.0]])
    near = st.local_axes(meta_of(), q, source=st.MeshNormals(mesh, 0.0))[:, 2, :]
    for p, got in zip(q, near, strict=True):  # outward normal of the sphere (upper side) at the nearest point
        n = p / np.linalg.norm(p)
        assert float(got @ n) >= 0.99 or float(got @ -n) >= 0.99
    avg = st.local_axes(meta_of(), q, source=st.MeshNormals(mesh, 30.0))[:, 2, :]
    assert np.abs(np.linalg.norm(avg, axis=1) - 1.0).max() <= TOL and (avg[:, 2] >= 0).all()


# --- the stored trend ------------------------------------------------------------------------------------------------

DEPTHS = [3.0, 6.0, 9.0, 12.0]
ROWS: list[tuple[str, float | None, float | None, float | None]] = [  # hole, depth, α, Aп: one plane, three bad rows
    *[(h, d, 35.0, 120.0) for h in HOLE_XY for d in DEPTHS],
    ("A-1", 15.0, None, 120.0), ("B-2", 15.0, 95.0, 10.0), ("C-3", None, 35.0, 120.0)]  # fmt: skip


def write_measurements(project: Any, set_id: int, name: str = "Замеры", kind: str = "event") -> int:
    holes = [r[0] for r in ROWS]
    depth = [r[1] for r in ROWS]
    data = dh.TableData(
        name=name, kind=kind, holes=holes, from_m=depth, to_m=depth,
        columns=[dh.ColumnSpec("ALPHA", "number", "°"), dh.ColumnSpec("AZ_DIP", "number", "°"),
                 dh.ColumnSpec("DEPTH", "number", "м"), dh.ColumnSpec("NOTE", "text")],
        values=[[r[2] for r in ROWS], [r[3] for r in ROWS], depth, ["x"] * len(ROWS)])  # fmt: skip
    return int(dh.write_table(project.engine, set_id, data, job_id=None, tick=noop)["table_id"])


@pytest.fixture()
def sc(vc):  # type: ignore[no-untyped-def]
    vc.table = write_measurements(vc.project, vc.set_id)
    vc.source = {"type": "measurements", "set_id": vc.set_id, "table": "Замеры", "alpha": "alpha", "dipdir": "az_dip",
                 "depth": "depth"}  # fmt: skip — names in any case, the table by name
    return vc


def meta_stored(c: Any, tid: int) -> dict[str, Any]:
    with dialect.read_tx(c.project.engine) as conn:
        return ts.meta_of(conn, tid)[1]


def put_mesh(c: Any, ax: float = 0.3, ay: float = -0.2, name: str = "Кровля", offset: Any = ZERO) -> int:
    v, t = plane_mesh(ax, ay, offset)
    with dialect.write_tx(c.project.engine) as conn:
        return int(ws.put_wireframe(conn, BlobStore(c.project.folder), v, t, name=name))


def test_create_and_read_a_structural_trend_of_measurements(sc) -> None:  # type: ignore[no-untyped-def]
    r = sc.post(TR, json={"kind": "structural", "source": sc.source, "radius": "250,5", "ratios": [1, 0.4, 0.1],
                          "size": 80, "color": "#1F6BFF"})  # fmt: skip
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    assert r.json()["name"] == "Структурный тренд"
    got = sc.get(f"{TR}/{tid}").json()
    assert got["kind"] == "structural" and got["radius"] == 250.5 and got["ratios"] == [1.0, 0.4, 0.1]
    assert got["size"] == 80.0 and got["color"] == "#1f6bff" and got["matrix"] is None and "ang1" not in got
    assert got["source"] == {"type": "measurements", "set_id": sc.set_id, "table": str(sc.table), "alpha": "ALPHA",
                             "dipdir": "AZ_DIP", "depth": "DEPTH"}  # the names as stored, the table by id
    assert got["source_info"]["table_name"] == "Замеры" and got["source_lost"] is False
    assert got["n_measurements"] == 12  # rows left out: no α, α > 90° (a row without a depth is not a line at all)
    assert sc.events[-1] == ("geostat.changed", {"kind": "trend", "id": tid})
    listed = {t["id"]: t for t in sc.get(TR).json()["trends"]}
    assert listed[tid]["kind"] == "structural" and "n_measurements" not in listed[tid]
    # ТД15-5 through the stored trend: the project's measurements → the normal of their plane
    meta = meta_stored(sc, tid)
    src = st.load_source(sc.project, meta)
    assert isinstance(src, st.Measurements) and src.n == 12 and src.counts["no_plane"] == 2
    assert src.counts["no_position"] == 0 and src.counts["rows"] == 14  # the row without a depth is not a line
    want_xyz = np.array([[*HOLE_XY[h], 100.0 - d] for h, d, _a, _p in ROWS[:12]])
    rounded = {tuple(p) for p in np.round(src.xyz, 6).tolist()}  # the collars are vertical: on the paths, by `depth`
    assert rounded == {tuple(p) for p in want_xyz.tolist()}
    q = random_points(50, 12, around=(50.0, 50.0, 90.0), half=60.0)
    frames = st.local_axes(meta, q, source=src)
    assert np.abs(frames - ref_frame(35.0, 120.0)).max() <= TOL
    rep: dict[str, Any] = {}
    st.local_axes(meta, np.array([[5000.0, 5000.0, 0.0]]), source=src, report=rep)
    assert rep["nearest"] == 1 and "R_тр = 250.5 м" in rep["journal"][0] + "".join(rep["journal"]).replace(",", ".")


def test_the_middle_of_from_to_when_there_is_no_depth_field(sc) -> None:  # type: ignore[no-untyped-def]
    holes, frm, to = ["A-1", "A-1", "B-2"], [0.0, 4.0, 2.0], [2.0, 8.0, 4.0]
    data = dh.TableData(name="Структура", kind="interval", holes=holes, from_m=frm, to_m=to,
                        columns=[dh.ColumnSpec("A", "number"), dh.ColumnSpec("P", "number")],
                        values=[[10.0, 20.0, 30.0], [45.0, 90.0, 135.0]])  # fmt: skip
    table = int(dh.write_table(sc.project.engine, sc.set_id, data, job_id=None, tick=noop)["table_id"])
    r = sc.post(TR, json={"kind": "structural", "name": "По интервалам",
                          "source": {"type": "measurements", "set_id": sc.set_id, "table": str(table), "alpha": "A",
                                     "dipdir": "P"}})  # fmt: skip
    assert r.status_code == 200, r.text
    meta = meta_stored(sc, r.json()["id"])
    assert "depth" not in meta["source"]
    src = st.load_source(sc.project, meta)
    got = {tuple(p) for p in np.round(src.xyz, 6).tolist()}
    assert got == {(0.0, 0.0, 99.0), (0.0, 0.0, 94.0), (100.0, 0.0, 97.0)}  # (FROM + TO) / 2 along the vertical holes


def test_the_refusals_of_a_structural_trend(sc) -> None:  # type: ignore[no-untyped-def]
    s = sc.source
    wid = put_mesh(sc)
    cases: list[tuple[dict[str, Any], int, str, str | None]] = [
        ({"kind": "structural"}, 422, "bad_param", "source"),
        ({"kind": "structural", "source": {"type": "other"}}, 422, "bad_param", "source"),
        ({"kind": "structural", "source": {**s, "set_id": "x"}}, 422, "bad_param", "source.set_id"),
        ({"kind": "structural", "source": {**s, "set_id": sc.set_id + 99}}, 404, "set_not_found", None),
        ({"kind": "structural", "source": {**s, "table": ""}}, 422, "bad_param", "source.table"),
        ({"kind": "structural", "source": {**s, "table": "нет такой"}}, 404, "table_not_found", None),
        ({"kind": "structural", "source": {**s, "alpha": None}}, 422, "bad_param", "source.alpha"),
        ({"kind": "structural", "source": {**s, "alpha": "NOPE"}}, 422, "bad_param", "source.alpha"),
        ({"kind": "structural", "source": {**s, "dipdir": "NOTE"}}, 422, "bad_param", "source.dipdir"),
        ({"kind": "structural", "source": {**s, "depth": "NOPE"}}, 422, "bad_param", "source.depth"),
        ({"kind": "structural", "source": {"type": "mesh"}}, 422, "bad_param", "source.wireframe_id"),
        ({"kind": "structural", "source": {"type": "mesh", "wireframe_id": wid + 99}}, 404, "wireframe_not_found",
         "source.wireframe_id"),
        ({"kind": "structural", "source": {"type": "mesh", "wireframe_id": wid, "r_n": 500}}, 422, "bad_param",
         "source.r_n"),
        ({"kind": "structural", "source": s, "radius": 5}, 422, "bad_param", "radius"),
        ({"kind": "structural", "source": s, "radius": 6000}, 422, "bad_param", "radius"),
        ({"kind": "structural", "source": s, "ratios": [1, 0]}, 422, "bad_param", "ratios"),
        ({"kind": "structural", "source": s, "model_id": 1}, 422, "bad_param", "model_id"),
        ({"kind": "structural", "source": s, "color": "red"}, 422, "bad_param", "color"),
    ]
    for body, status, code, param in cases:
        r = sc.post(TR, json=body)
        assert (r.status_code, r.json()["code"], r.json().get("param")) == (status, code, param), (body, r.text)
        assert r.json()["message"] and r.json()["hint"]
    assert sc.post(TR, json={"kind": "plane"}).json()["code"] == "bad_param"


def test_change_a_structural_trend_and_change_its_kind(sc) -> None:  # type: ignore[no-untyped-def]
    wid = put_mesh(sc)
    tid = sc.post(TR, json={"kind": "structural", "source": sc.source, "name": "Замеры СВ"}).json()["id"]
    r = sc.put(f"{TR}/{tid}", json={"expected_version": 1, "source": {"type": "mesh", "wireframe_id": wid, "r_n": 5},
                                    "radius": 300})  # fmt: skip
    assert r.status_code == 200 and r.json() == {"id": tid, "name": "Замеры СВ", "version": 2}, r.text
    got = sc.get(f"{TR}/{tid}").json()
    assert got["source"] == {"type": "mesh", "wireframe_id": wid, "r_n": 5.0} and got["radius"] == 300.0
    assert got["source_info"]["wireframe_name"] == "Кровля" and got["n_measurements"] is None
    r = sc.put(f"{TR}/{tid}", json={"expected_version": 2, "color": "#00ff00"})  # the rest is kept
    assert r.status_code == 200 and sc.get(f"{TR}/{tid}").json()["radius"] == 300.0
    r = sc.put(f"{TR}/{tid}", json={"expected_version": 3, "kind": "global", "ang1": 20, "ratios": [1, 0.5, 0.2]})
    assert r.status_code == 200, r.text
    glob = sc.get(f"{TR}/{tid}").json()
    assert glob["kind"] == "global" and glob["ang1"] == 20.0 and "source" not in glob  # the structural fields are gone
    bad = sc.put(f"{TR}/{tid}", json={"expected_version": 4, "kind": "structural"})  # no source of its own to keep
    assert (bad.status_code, bad.json()["code"], bad.json()["param"]) == (422, "bad_param", "source")


def test_discs_of_the_measurements_and_of_a_wireframe(sc) -> None:  # type: ignore[no-untyped-def]
    tid = sc.post(TR, json={"kind": "structural", "source": sc.source, "radius": 120}).json()["id"]
    d = sc.get(f"{TR}/{tid}/discs").json()
    assert d["radius"] == 120.0 and (d["n"], d["total"], d["thinned"]) == (12, 12, False)
    cols = d["discs"]
    assert set(cols) == {"x", "y", "z", "nx", "ny", "nz"} and all(len(v) == 12 for v in cols.values())
    n = np.column_stack([cols["nx"], cols["ny"], cols["nz"]])
    assert np.abs(n - st.plane_normals([35.0], [120.0])[0]).max() <= TOL and (n[:, 2] > 0).all()
    assert {(round(x, 6), round(y, 6)) for x, y in zip(cols["x"], cols["y"], strict=True)} == set(HOLE_XY.values())
    wid = put_mesh(sc)
    mid = sc.post(TR, json={"kind": "structural", "source": {"type": "mesh", "wireframe_id": wid}}).json()["id"]
    m = sc.get(f"{TR}/{mid}/discs").json()
    assert (m["total"], m["n"], m["thinned"]) == (2 * 24 * 24, 2 * 24 * 24, False) and m["radius"] == 200.0
    mn = np.column_stack([m["discs"]["nx"], m["discs"]["ny"], m["discs"]["nz"]])
    assert np.abs(mn - face_normal(0.3, -0.2)).max() <= 1e-10  # the centres of the faces carry their normals
    glob = sc.post(TR, json={"ang1": 10}).json()["id"]
    r = sc.get(f"{TR}/{glob}/discs")
    assert (r.status_code, r.json()["code"]) == (422, "trend_not_structural") and r.json()["hint"]
    assert sc.get(f"{TR}/9999/discs").json()["code"] == "trend_not_found"


def test_discs_of_a_large_wireframe_are_thinned(sc, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(st, "DISCS_MESH_MAX", 300)
    wid = put_mesh(sc)
    mid = sc.post(TR, json={"kind": "structural", "source": {"type": "mesh", "wireframe_id": wid}}).json()["id"]
    m = sc.get(f"{TR}/{mid}/discs").json()
    assert m["total"] == 1152 and 250 <= m["n"] <= 300 and m["thinned"] is True
    assert len(m["discs"]["x"]) == m["n"]


def test_the_trend_td15_6_through_the_project(sc) -> None:  # type: ignore[no-untyped-def]
    wid = put_mesh(sc, 0.3, -0.2)
    tid = sc.post(TR, json={"kind": "structural", "source": {"type": "mesh", "wireframe_id": wid}}).json()["id"]
    meta = meta_stored(sc, tid)
    q = np.random.default_rng(1).uniform([10.0, 10.0, -20.0], [230.0, 230.0, 80.0], (200, 3))
    frames = st.local_axes(meta, q, source=st.load_source(sc.project, meta))
    assert np.abs(frames[:, 2, :] - face_normal(0.3, -0.2)).max() <= TOL  # the normal of the face (ТД15-6)


def test_a_source_that_is_gone(sc) -> None:  # type: ignore[no-untyped-def]
    wid = put_mesh(sc)
    tid = sc.post(TR, json={"kind": "structural", "source": {"type": "mesh", "wireframe_id": wid}}).json()["id"]
    tid2 = sc.post(TR, json={"kind": "structural", "source": sc.source}).json()["id"]
    assert sc.delete(f"{API}/drillholes/sets/{sc.set_id}").status_code == 200
    r = sc.get(f"{TR}/{tid2}")
    assert r.status_code == 200 and r.json()["source_lost"] is True and r.json()["n_measurements"] is None
    r = sc.get(f"{TR}/{tid2}/discs")
    assert (r.status_code, r.json()["code"]) == (409, "source_gone") and r.json()["hint"]
    with dialect.write_tx(sc.project.engine) as conn:
        from sqlalchemy import delete

        from bf_core.db.schema import objects

        conn.execute(delete(objects).where(objects.c.id == wid))
    r = sc.get(f"{TR}/{tid}/discs")
    assert (r.status_code, r.json()["code"]) == (409, "source_gone")
    assert sc.get(f"{TR}/{tid}").json()["source_lost"] is True


def test_the_estimation_refuses_a_structural_trend(sc) -> None:  # type: ignore[no-untyped-def]
    """Вопрос № 99: the estimation of 8.8 takes the global trends only — `trend_structural` with a hint."""
    from bf_core.api.errors import ApiError
    from bf_core.geostat.estimate import store_check as sc_check

    tid = sc.post(TR, json={"kind": "structural", "source": sc.source}).json()["id"]
    glob = sc.post(TR, json={"ang1": 30, "ratios": [1, 0.5, 0.5]}).json()["id"]
    with dialect.read_tx(sc.project.engine) as conn:
        for fn in (lambda: ts.get(conn, tid), lambda: ts.matrix_of(conn, tid), lambda: sc_check.trend_form(conn, tid)):
            with pytest.raises(ApiError) as e:
                fn()
            assert (e.value.status, e.value.code) == (422, "trend_structural") and e.value.hint
            assert e.value.extra["param"] == "trend_id"
        assert ts.get(conn, glob)["ang1"] == 30.0 and ts.matrix_of(conn, glob).shape == (3, 3)  # Э16/Э18 as before
        assert ts.form(conn, tid)["kind"] == "structural"


def test_409_in_use_and_the_graph_of_a_structural_trend(sc) -> None:  # type: ignore[no-untyped-def]
    wid = put_mesh(sc)
    g = sc.post(TR, json={"ang1": 5}).json()["id"]
    tid = sc.post(TR, json={"kind": "structural", "source": sc.source}).json()["id"]
    mid = sc.post(TR, json={"kind": "structural", "name": "Каркас", "source": {"type": "mesh", "wireframe_id": wid}}
                  ).json()["id"]  # fmt: skip
    node = lineage.Node("object:trend", tid)
    with dialect.read_tx(sc.project.engine) as conn:
        kinds = {(r["kind"], r["id"]) for r in lineage.origin(conn, node)}
        mesh_kinds = {(r["kind"], r["id"]) for r in lineage.origin(conn, lineage.Node("object:trend", mid))}
        assert kinds == {("dh_table", sc.table), ("dh_set", sc.set_id)}  # the measurements and the paths
        assert mesh_kinds == {("wireframe", wid)}
        assert lineage.status(conn, [node])[node].state == lineage.CURRENT
    with dialect.write_tx(sc.project.engine) as conn:  # something is built from the trend (an RBF model, Э19)
        built = insert_object(conn, "resources_report", "Построено по тренду", {"algo": "x"})
        lineage.record(conn, lineage.Node("object:resources_report", built), [lineage.Node("object:trend", tid)])
        fp = lineage.fingerprint(conn, node)
        assert fp is not None and fp.startswith("1|")  # the version, then the table and the set
        assert lineage.fingerprint(conn, lineage.Node("object:trend", g)) == "1"  # a global trend — its version only
        assert node.id in {n["id"] for n in lineage.impact(conn, lineage.Node("dh_table", sc.table))["nodes"]
                           if n["kind"] == "object:trend"}  # «Влияние изменения»: the edit of the table reaches it
        from bf_core.composites import results as res

        res.bump_table(conn, sc.set_id, sc.table)  # the table of the measurements is edited
        assert lineage.fingerprint(conn, node) != fp  # the trend reads live: its fingerprint holds that of its data
        report = lineage.Node("object:resources_report", built)
        assert lineage.status(conn, [report])[report].state == lineage.STALE  # «изменено: тренд «…»»
        assert lineage.status(conn, [node])[node].state == lineage.CURRENT
    user = consumer(sc.project.engine, tid, "RBF зона 1")
    r = sc.delete(f"{TR}/{tid}")
    assert (r.status_code, r.json()["code"]) == (409, "in_use") and "«RBF зона 1»" in r.json()["message"]
    assert sc.get(f"{TR}/{tid}").json()["used"] is True
    with dialect.write_tx(sc.project.engine) as conn:
        from sqlalchemy import delete

        from bf_core.db.schema import objects

        conn.execute(delete(objects).where(objects.c.id == user))
    assert sc.delete(f"{TR}/{tid}").json() == {"deleted": tid}
    with dialect.read_tx(sc.project.engine) as conn:
        assert lineage.origin(conn, node) == []  # its rows go with it


def test_read_only_project_is_423_for_a_structural_trend(sc) -> None:  # type: ignore[no-untyped-def]
    sc.project.read_only = True
    try:
        r = sc.post(TR, json={"kind": "structural", "source": sc.source})
        assert (r.status_code, r.json()["code"]) == (423, "project_read_only")
    finally:
        sc.project.read_only = False


# --- .bfggis ---------------------------------------------------------------------------------------------------------


def test_remap_of_a_structural_trend_meta() -> None:
    meta = {"v": 1, "kind": "structural", "radius": 100.0,
            "source": {"type": "measurements", "set_id": 4, "table": "9", "alpha": "A", "dipdir": "P",
                       "depth": "D"}}  # fmt: skip
    new = remap.trend_meta(meta, {4: 40}.get, {9: 90}.get)
    assert new["source"] == {"type": "measurements", "set_id": 40, "table": "90", "alpha": "A", "dipdir": "P",
                             "depth": "D"}  # fmt: skip
    assert meta["source"]["set_id"] == 4  # the stored meta is not changed
    gone = remap.trend_meta(meta, {}.get, {}.get)["source"]
    assert gone["set_id"] is None and gone["table"] is None and gone["alpha"] == "A"  # not in the archive: null
    mesh = {"kind": "structural", "source": {"type": "mesh", "wireframe_id": 7, "r_n": 5.0}}
    assert remap.trend_meta(mesh, {7: 70}.get, {}.get)["source"] == {"type": "mesh", "wireframe_id": 70, "r_n": 5.0}
    assert remap.trend_meta({"origin": {"type": "model", "model_id": 4}}, {4: 40}.get)["origin"]["model_id"] == 40


def test_bfggis_roundtrip_of_structural_trends(sc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    wid = put_mesh(sc)
    a = sc.post(TR, json={"kind": "structural", "name": "По замерам", "source": sc.source, "radius": 321,
                          "ratios": [1, 0.25, 0.05], "size": 90}).json()["id"]  # fmt: skip
    b = sc.post(TR, json={"kind": "structural", "name": "По каркасу",
                          "source": {"type": "mesh", "wireframe_id": wid, "r_n": 12}}).json()["id"]  # fmt: skip
    g = sc.post(TR, json={"name": "Глобальный", "ang1": 17, "ratios": [1, 0.3, 0.07]}).json()["id"]
    user = consumer(sc.project.engine, a)
    before = {t["name"]: t for t in sc.get(TR).json()["trends"]}
    r = sc.post(API + "/projects/export", json={"path": str(tmp_path / "Структурные")}).json()
    job = wait_job(sc, r["source"], r["id"])
    assert job["status"] == "done", job
    r = sc.post(API + "/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(sc, r["source"], r["id"])
    assert job["status"] == "done", job
    maps = {int(k): int(v) for k, v in job["result"]["id_maps"]["objects"].items()}
    tables = {int(k): int(v) for k, v in job["result"]["id_maps"]["dh_tables"].items()}
    assert sc.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    project = projects_of(sc.core).require_current()
    after = {t["name"]: t for t in sc.get(TR).json()["trends"]}
    assert set(after) == set(before) == {"По замерам", "По каркасу", "Глобальный"}
    one, two = after["По замерам"], after["По каркасу"]
    assert one["id"] == maps[a] and two["id"] == maps[b] and after["Глобальный"]["id"] == maps[g]
    assert one["source"] == {**before["По замерам"]["source"], "set_id": maps[sc.set_id],
                             "table": str(tables[sc.table])}  # fmt: skip
    assert two["source"] == {"type": "mesh", "wireframe_id": maps[wid], "r_n": 12.0}
    for k in ("radius", "ratios", "size", "color", "kind", "used"):
        assert one[k] == before["По замерам"][k] and two[k] == before["По каркасу"][k] or k in ("used", "size")
    assert one["source_lost"] is False and two["source_lost"] is False
    assert sc.get(f"{TR}/{one['id']}").json()["n_measurements"] == 12
    d = sc.get(f"{TR}/{one['id']}/discs").json()
    assert d["n"] == 12 and np.abs(np.array(d["discs"]["nz"]) - np.cos(np.radians(35.0))).max() <= TOL
    assert sc.get(f"{TR}/{two['id']}/discs").json()["total"] == 2 * 24 * 24
    assert sc.get(f"{TR}/{maps[a]}/usage").json()["usage"][0]["id"] == maps[user]
    assert sc.delete(f"{TR}/{maps[a]}").json()["code"] == "in_use"
    with dialect.read_tx(project.engine) as conn:  # the graph is the archive's: the inputs name the new ids
        got = {(r["kind"], r["id"]) for r in lineage.origin(conn, lineage.Node("object:trend", maps[a]))}
    assert got == {("dh_table", tables[sc.table]), ("dh_set", maps[sc.set_id])}


# --- imports -------------------------------------------------------------------------------------------------------


def test_the_modules_do_not_load_numba_with_their_import() -> None:
    """The API process imports the router; numba and scipy come with the first `local_axes` (BF-READY)."""
    code = ("import sys, bf_core.geostat.trend_struct, bf_core.geostat.trend_store, bf_core.geostat.routes_trend, "
            "bf_core.geostat.remap, bf_core.lineage.adapt_objects\n"
            "bad = [m for m in ('numba', 'scipy') if m in sys.modules]\nraise SystemExit(3 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-500:]
