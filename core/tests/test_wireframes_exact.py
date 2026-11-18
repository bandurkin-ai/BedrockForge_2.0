"""Exact queries on the wireframes of a project over the API (ADR 0013 §5, ADR 0007 §5; ТЗ 7.14, 7.15, 7.21):

* `POST /wireframes/pick` — the nearest triangle under a ray in project coordinates: the point on the
  surface (1e-9 m against the analytic answer on UTM-sized coordinates), the dip and the dip direction
  (1e-9°), the nearest of several wireframes, the planes of the section window, a miss, bad requests;
* `GET /viz/wireframes/{id}/section` — BFA1 `section`: a closed solid gives a closed line (its length to
  1e-8 m, its points on the plane to 2e-9 m), an open surface an open one; by a plane and by a section line
  (the same lines as the kernel gives, bit for bit; a line with a length is cut at its ends); ETag and 304;
* `GET /viz/sets/{id}/intersections` — where the holes of a set cross wireframes: depths against a scan
  with bisection along `Trajectories.positions` (1e-6 m; the acceptance of 7.15 is 1 mm), the point of a
  mark is `positions(hole, depth)` bit for bit, ENTER and LEAVE only for a closed solid; ETag and 304;
* the cache of geometry and trees by the hash of the geometry: one load for many requests, a byte limit.
"""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_surveys, objects
from bf_core.geo import intersect as gi
from bf_core.geo import section as gs
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from bf_core.wireframes import exact
from bf_core.wireframes import store as ws
from tests.meshutil import UTM, cube, grid
from tests.test_import_job import API

pytestmark = pytest.mark.windows

SIDE = 100.0
SLOPE = 0.5  # the fault rises to the east: it dips west at atan(0.5)
FAULT_AT = np.array([-40.0, -40.0, 20.0])  # its low corner from the low corner of the cube
DIP = math.degrees(math.atan(SLOPE))


def fault_z(x: Any, y: Any) -> Any:
    return SLOPE * x + 0.0 * y


@pytest.fixture()
def api(pclient):  # type: ignore[no-untyped-def]
    """A cube 100 m (19 200 triangles, closed, normals outward), an inclined open surface «Разлом» that cuts
    through it, a DTM far away, and a set of holes: 0 vertical through the cube, 1 from inside the cube, 2 a
    curved one through the fault and the cube, 3 without a collar."""
    r = pclient.post(f"{API}/projects", json={"name": "Точные запросы"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    lo = UTM.copy()
    cv, ct = cube(40, SIDE, offset=lo)
    fv, ft = grid(18, 18, fault_z, step=10.0, offset=lo + FAULT_AT)
    dv, dt = grid(4, 4, lambda x, y: 0.0 * x, step=10.0, offset=lo + [5000.0, 0.0, 0.0])
    now = dialect.utcnow()
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        ids = {
            "cube": ws.put_wireframe(conn, store, cv, ct, name="Куб", color="#ff0000"),
            "fault": ws.put_wireframe(conn, store, fv, ft, name="Разлом"),
            "dtm": ws.put_wireframe(conn, store, dv, dt, name="Рельеф", dtm=True),
        }
        set_id = conn.execute(
            sa.insert(objects).values(
                kind="drillhole_set",
                name="Разведка",
                name_key="разведка",
                sort_key=0.0,
                state="ready",
                version=1,
                meta={},
                created_at=now,
                updated_at=now,
            )  # fmt: skip
        ).inserted_primary_key[0]
        collars = [
            ("С-1", lo + [50.3, 49.9, 130.0], 200.0),
            ("С-2", lo + [13.1, 77.2, 60.0], 90.0),
            ("С-3", lo + [-20.0, 20.0, 160.0], 260.0),
        ]
        holes = {}
        for name, (x, y, z), td in collars:
            holes[name] = conn.execute(
                sa.insert(dh_holes).values(set_id=set_id, name=name, x=x, y=y, z=z, td=td, has_collar=True)
            ).inserted_primary_key[0]
        holes["С-4"] = conn.execute(
            sa.insert(dh_holes).values(set_id=set_id, name="С-4", has_collar=False)
        ).inserted_primary_key[0]
        for depth, az, dip in ((0.0, 90.0, -70.0), (80.0, 95.0, -55.0), (170.0, 110.0, -35.0), (260.0, 120.0, -20.0)):
            conn.execute(
                sa.insert(dh_surveys).values(set_id=set_id, hole_id=holes["С-3"], depth=depth, azimuth=az, dip=dip)
            )
    exact.forget()
    return pclient, project, {**ids, "set": int(set_id), "holes": holes, "lo": lo}


def ok(r: Any) -> Any:
    assert r.status_code == 200, r.text
    return r.json()


def refused(r: Any, status: int, code: str) -> dict[str, Any]:
    body: dict[str, Any] = r.json()
    assert r.status_code == status and body["code"] == code, r.text
    assert body["message"] and body["hint"]
    return body


def pick(c: Any, origin: Any, direction: Any, **rest: Any) -> Any:
    body = {"origin": [float(x) for x in origin], "direction": [float(x) for x in direction], **rest}
    return ok(c.post(f"{API}/wireframes/pick", json=body))["hit"]


def section(c: Any, wf: int, **params: Any) -> tuple[Any, dict[str, Any], dict[str, np.ndarray]]:
    r = c.get(f"{API}/viz/wireframes/{wf}/section", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["cache-control"] == "no-cache"
    return r, *binary.decode(r.content)


def triple(v: Any) -> str:
    return ",".join(repr(float(x)) for x in v)


def lines_of(a: dict[str, np.ndarray]) -> list[np.ndarray]:
    xyz = a["xyz"].reshape(-1, 3)
    return [xyz[int(a["offsets"][i]) : int(a["offsets"][i + 1])] for i in range(len(a["closed"]))]


def length(points: np.ndarray, closed: bool) -> float:
    p = np.vstack([points, points[:1]]) if closed else points
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum())


# --- pick --------------------------------------------------------------------------------------------------


def test_pick_gives_the_point_the_triangle_and_the_attitude(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = api
    lo = ids["lo"]
    # straight down on the fault, west of the cube
    x, y = lo[0] - 23.7, lo[1] + 31.9
    surface = lo[2] + FAULT_AT[2] + SLOPE * (x - (lo[0] + FAULT_AT[0]))
    hit = pick(c, [x, y, lo[2] + 500.0], [0.0, 0.0, -1.0])
    assert hit["wireframe_id"] == ids["fault"] and hit["name"] == "Разлом"
    assert hit["point"][:2] == pytest.approx([x, y], abs=1e-9) and hit["point"][2] == pytest.approx(surface, abs=1e-9)
    assert hit["distance"] == pytest.approx(lo[2] + 500.0 - surface, abs=1e-9)
    assert hit["dip"] == pytest.approx(DIP, abs=1e-9) and hit["dip_direction"] == pytest.approx(270.0, abs=1e-9)
    assert hit["area"] == pytest.approx(50.0 * math.hypot(1.0, SLOPE), rel=1e-12)
    with dialect.read_tx(project.engine) as conn:
        xyz, tri = ws.read_wireframe(conn, BlobStore(project.folder), ids["fault"])
    assert hit["indices"] == tri[hit["triangle"]].tolist()  # the number of the triangle as the store keeps it
    assert hit["vertices"] == xyz[tri[hit["triangle"]]].tolist()  # its corners bit for bit

    # a side of the cube: a vertical face looks west
    origin = lo + [-40.0, 33.3, 61.7]
    hit = pick(c, origin, [1.0, 0.0, 0.0], wireframe_ids=[ids["cube"]])
    assert hit["wireframe_id"] == ids["cube"] and hit["distance"] == pytest.approx(40.0, abs=1e-9)
    assert hit["point"] == pytest.approx([lo[0], origin[1], origin[2]], abs=1e-9)
    assert hit["dip"] == pytest.approx(90.0, abs=1e-9) and hit["dip_direction"] == pytest.approx(270.0, abs=1e-9)
    assert hit["normal"] == pytest.approx([-1.0, 0.0, 0.0], abs=1e-12)
    # the top of the cube is level: a dip of 0 and no direction (null, not 0)
    top = pick(c, lo + [20.5, 20.5, 400.0], [0.0, 0.0, -3.0], wireframe_ids=[ids["cube"]])
    assert top["dip"] == pytest.approx(0.0, abs=1e-9) and top["dip_direction"] is None
    assert top["point"][2] == pytest.approx(lo[2] + SIDE, abs=1e-9)


def test_pick_takes_the_nearest_wireframe_and_the_section_window(api) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = api
    lo = ids["lo"]
    origin = lo + [-60.0, 50.0, 50.0]  # going east: the west face of the cube at 60 m, the fault inside it at 80 m
    fault_x = FAULT_AT[0] + (50.0 - FAULT_AT[2]) / SLOPE  # where the fault is at this height: 20 m east of lo
    both = [ids["cube"], ids["fault"]]
    first = pick(c, origin, [1.0, 0.0, 0.0])
    assert first["wireframe_id"] == ids["cube"] and first["distance"] == pytest.approx(60.0, abs=1e-9)
    hit = pick(c, origin, [1.0, 0.0, 0.0], wireframe_ids=[ids["fault"]])
    assert hit["wireframe_id"] == ids["fault"] and hit["distance"] == pytest.approx(60.0 + fault_x, abs=1e-9)
    # the window of a section keeps x ≥ lo + 10: the west face of the cube is cut away, the fault is next
    clip = [[1.0, 0.0, 0.0, -(lo[0] + 10.0)]]
    hit = pick(c, origin, [1.0, 0.0, 0.0], wireframe_ids=both, clip=clip)
    assert hit["wireframe_id"] == ids["fault"] and hit["point"][0] == pytest.approx(lo[0] + fault_x, abs=1e-9)
    # x ≥ lo + 30: the east face of the cube from inside
    hit = pick(c, origin, [1.0, 0.0, 0.0], wireframe_ids=both, clip=[[1.0, 0.0, 0.0, -(lo[0] + 30.0)]])
    assert hit["wireframe_id"] == ids["cube"] and hit["point"][0] == pytest.approx(lo[0] + SIDE, abs=1e-9)
    # a window the ray never enters, a ray that misses, a wireframe that is gone, an empty list
    assert pick(c, origin, [1.0, 0.0, 0.0], clip=[[0.0, 1.0, 0.0, -(lo[1] + 1000.0)]]) is None
    assert pick(c, origin, [0.0, 0.0, 1.0]) is None
    assert ok(c.post(f"{API}/wireframes/pick", json={"origin": origin.tolist(), "direction": [0, 0, 1]}))["tested"] == 0
    assert pick(c, origin, [1.0, 0.0, 0.0], wireframe_ids=[987_654]) is None
    assert pick(c, origin, [1.0, 0.0, 0.0], wireframe_ids=[]) is None
    # aimed exactly at a corner of the cube along its diagonal: nothing slips between the triangles
    corner = pick(c, lo - 30.0, [1.0, 1.0, 1.0], wireframe_ids=[ids["cube"]])
    assert corner is not None and corner["point"] == pytest.approx(lo.tolist(), abs=1e-9)


def test_pick_refuses_bad_requests(api) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = api
    url = f"{API}/wireframes/pick"
    refused(c.post(url, json={"origin": [0, 0, 0], "direction": [0, 0, 0]}), 422, "bad_ray")
    refused(c.post(url, json={"origin": [0, 0], "direction": [0, 0, 1]}), 422, "bad_request")
    refused(c.post(url, json={"origin": [0, 0, 0], "direction": [0, 0, 1], "clip": [[0, 0, 0, 1]]}), 422, "bad_clip")
    refused(c.post(url, json={"origin": [0, 0, 0], "direction": [0, 0, 1], "clip": [[1, 0, 0]]}), 422, "bad_request")
    assert (
        ok(c.post(url, json={"origin": [0, 0, 0], "direction": [0, 0, 1], "wireframe_ids": [ids["dtm"]]}))["hit"]
        is None
    )


def test_pick_is_fast_once_the_geometry_is_cached(api) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = api
    lo = ids["lo"]
    pick(c, lo + [-40.0, 33.3, 61.7], [1.0, 0.0, 0.0])  # loads the geometry, compiles the kernels
    rng = np.random.default_rng(5)
    t0 = time.perf_counter()
    for _ in range(20):
        target = lo + rng.uniform(5.0, 95.0, 3)
        assert pick(c, target + [-300.0, 40.0, 220.0], [300.0, -40.0, -220.0]) is not None
    assert (time.perf_counter() - t0) / 20 < 0.05  # the budget of a click (5.3), the HTTP round trip included
    info = exact.cache_info()
    assert info["entries"] == 2 and info["loads"] == 2  # the DTM is not under the rays: never loaded


# --- section -----------------------------------------------------------------------------------------------


def test_section_of_a_closed_solid_is_a_closed_line(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = api
    lo = ids["lo"]
    point, normal = lo + [37.25, 0.0, 0.0], [1.0, 0.0, 0.0]
    r, head, a = section(c, ids["cube"], point=triple(point), normal=triple(normal))
    assert head["kind"] == "section" and head["wireframe"] == ids["cube"] and head["lines"] == 1
    assert a["xyz"].dtype == np.dtype("<f8") and a["offsets"].tolist() == [0, head["points"]]
    assert a["closed"].tolist() == [1]
    (line,) = lines_of(a)
    assert np.abs(line[:, 0] - point[0]).max() <= 2e-9  # on the plane, in project coordinates
    assert length(line, True) == pytest.approx(4 * SIDE, abs=1e-8)
    assert line[:, 1].min() == pytest.approx(lo[1], abs=1e-9) and line[:, 2].max() == pytest.approx(
        lo[2] + SIDE, abs=1e-9
    )
    # the same lines as the kernel gives from the stored arrays, bit for bit
    with dialect.read_tx(project.engine) as conn:
        xyz, tri = ws.read_wireframe(conn, BlobStore(project.folder), ids["cube"])
    ref = gs.plane_section(xyz, tri, point, normal)
    assert np.array_equal(a["xyz"].reshape(-1, 3), ref.xyz) and a["offsets"].tolist() == ref.offsets.tolist()

    # ETag: the geometry and the plane; 304 before any calculation
    tag = r.headers["etag"]
    again = c.get(r.request.url, headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == tag
    moved, _h, _a = section(c, ids["cube"], point=triple(point + [1.0, 0.0, 0.0]), normal=triple(normal))
    assert moved.headers["etag"] != tag
    other, _h, _a = section(c, ids["fault"], point=triple(point), normal=triple(normal))
    assert other.headers["etag"] != tag
    ok(c.patch(f"{API}/wireframes/{ids['cube']}", json={"expected_version": 1, "name": "Куб 2", "color": "#00ff00"}))
    same, _h, _a = section(c, ids["cube"], point=triple(point), normal=triple(normal))
    assert same.headers["etag"] == tag  # a name and a colour are not the geometry

    # an inclined plane through the middle: a closed hexagon; a plane that misses: no lines
    _r, head, a = section(c, ids["cube"], point=triple(lo + SIDE / 2), normal="1,1,1")
    (hexagon,) = lines_of(a)
    assert a["closed"].tolist() == [1] and length(hexagon, True) == pytest.approx(6 * SIDE / math.sqrt(2.0), abs=1e-8)
    assert np.abs((hexagon - (lo + SIDE / 2)) @ np.ones(3) / math.sqrt(3.0)).max() <= 2e-9
    _r, head, a = section(c, ids["cube"], point=triple(lo - 5.0), normal="0,0,1")
    assert head["lines"] == 0 and head["points"] == 0 and a["offsets"].tolist() == [0] and len(a["xyz"]) == 0


def test_section_by_a_section_line_and_its_ends(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = api
    lo = ids["lo"]
    with dialect.read_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        cube_arrays = ws.read_wireframe(conn, store, ids["cube"])
        fault_arrays = ws.read_wireframe(conn, store, ids["fault"])
    x0, y0 = float(lo[0] - 30.0), float(lo[1] + 42.5)
    # a line to the east without ends: the whole outline of the cube in the plane y = y0
    _r, head, a = section(c, ids["cube"], x0=repr(x0), y0=repr(y0), az="90")
    assert head["normal"] == [0.0, -1.0, 0.0] and a["closed"].tolist() == [1]
    (line,) = lines_of(a)
    assert np.abs(line[:, 1] - y0).max() <= 2e-9 and length(line, True) == pytest.approx(4 * SIDE, abs=1e-8)
    ref = gs.window_section(*cube_arrays, gs.section_window(x0, y0, 90.0))
    assert np.array_equal(a["xyz"].reshape(-1, 3), ref.xyz)
    # with a length of 80 m the line ends inside the cube: an open piece, cut at x0 + 80 = lo + 50
    r, head, a = section(c, ids["cube"], x0=repr(x0), y0=repr(y0), az="90", length="80")
    assert a["closed"].tolist() == [0]
    (piece,) = lines_of(a)
    assert piece[:, 0].max() == pytest.approx(lo[0] + 50.0, abs=1e-9) and piece[:, 0].min() == pytest.approx(
        lo[0], abs=1e-9
    )
    assert length(piece, False) == pytest.approx(50.0 + SIDE + 50.0, abs=1e-8)
    ref = gs.window_section(*cube_arrays, gs.section_window(x0, y0, 90.0, length=80.0))
    assert np.array_equal(a["xyz"].reshape(-1, 3), ref.xyz) and a["closed"].tolist() == ref.closed.astype(int).tolist()
    assert c.get(r.request.url, headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    # the plane moved to a face of the window (offset along the normal, towards the viewer)
    _r, head, a = section(c, ids["cube"], x0=repr(x0), y0=repr(y0), az="90", offset="12.5")
    assert np.abs(a["xyz"].reshape(-1, 3)[:, 1] - (y0 - 12.5)).max() <= 2e-9
    # an open surface: one open line from border to border, on the fault and on the plane
    _r, head, a = section(c, ids["fault"], x0=repr(x0), y0=repr(y0), az="90")
    assert a["closed"].tolist() == [0]
    (trace,) = lines_of(a)
    assert length(trace, False) == pytest.approx(180.0 * math.hypot(1.0, SLOPE), abs=1e-8)
    rise = trace[:, 2] - (lo[2] + FAULT_AT[2] + SLOPE * (trace[:, 0] - (lo[0] + FAULT_AT[0])))
    assert np.abs(rise).max() <= 2e-9
    ref = gs.window_section(*fault_arrays, gs.section_window(x0, y0, 90.0))
    assert np.array_equal(a["xyz"].reshape(-1, 3), ref.xyz)


def test_section_refuses_bad_requests(api) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = api
    url = f"{API}/viz/wireframes/{ids['cube']}/section"
    refused(c.get(url), 422, "bad_plane")  # neither a plane nor a line
    refused(c.get(url, params={"point": "0,0,0", "normal": "0,0,0"}), 422, "bad_plane")
    refused(c.get(url, params={"point": "0,0", "normal": "0,0,1"}), 422, "bad_plane")
    refused(c.get(url, params={"point": "0,0,x", "normal": "0,0,1"}), 422, "bad_plane")
    refused(c.get(url, params={"point": "0,0,nan", "normal": "0,0,1"}), 422, "bad_plane")
    refused(c.get(url, params={"x0": "0", "y0": "0", "az": "45", "length": "-1"}), 422, "bad_plane")
    refused(c.get(url, params={"x0": "0", "az": "45"}), 422, "bad_plane")
    refused(
        c.get(url, params={"x0": "0", "y0": "0", "az": "45", "point": "0,0,0", "normal": "0,0,1"}), 422, "bad_plane"
    )
    refused(
        c.get(f"{API}/viz/wireframes/987654/section", params={"point": "0,0,0", "normal": "0,0,1"}),
        404,
        "wireframe_not_found",
    )


# --- intersections -----------------------------------------------------------------------------------------


def marks(c: Any, set_id: int, **params: Any) -> tuple[Any, dict[str, Any], dict[str, np.ndarray]]:
    r = c.get(f"{API}/viz/sets/{set_id}/intersections", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["cache-control"] == "no-cache"
    return r, *binary.decode(r.content)


def crossings_by_scan(traj: Any, hole: int, height: Any, step: float = 0.01) -> list[float]:
    """Depths at which `height(points)` changes its sign along a hole: a scan and a bisection to 1e-9 m."""
    total = float(traj.lengths()[hole])
    d = np.unique(np.append(np.arange(0.0, total, step), total))
    h = height(traj.positions(np.full(len(d), hole), d))
    out = []
    for i in np.flatnonzero((h[:-1] >= 0.0) != (h[1:] >= 0.0)).tolist():
        a, b = float(d[i]), float(d[i + 1])
        up = h[i] >= 0.0
        for _ in range(60):
            m = 0.5 * (a + b)
            if (height(traj.positions(np.array([hole]), np.array([m])))[0] >= 0.0) == up:
                a = m
            else:
                b = m
        out.append(0.5 * (a + b))
    return out


def test_intersections_of_holes_with_wireframes(api) -> None:  # type: ignore[no-untyped-def]
    from bf_core.drillholes.trajectory import trajectories

    c, project, ids = api
    lo = ids["lo"]
    wanted = f"{ids['cube']},{ids['fault']}"
    r, head, a = marks(c, ids["set"], wireframes=wanted)
    assert head["kind"] == "intersections" and head["set"] == ids["set"] and head["method"] == "minimum_curvature"
    assert head["objects"] == [
        {"kind": "wireframe", "id": ids["cube"], "name": "Куб", "color": "#ff0000"},
        {"kind": "wireframe", "id": ids["fault"], "name": "Разлом", "color": None},
    ]
    n = head["n"]
    assert len(a["hole"]) == len(a["depth"]) == len(a["obj"]) == len(a["event"]) == n and len(a["xyz"]) == 3 * n
    assert a["depth"].dtype == np.dtype("<f8") and a["xyz"].dtype == np.dtype("<f8")
    st = trajectories(project, ids["set"])
    traj = st.traj
    dense = np.array([st.index[int(h)] for h in a["hole"].tolist()])
    # the point of a mark is the point of the trace, bit for bit
    assert np.array_equal(a["xyz"].reshape(-1, 3), traj.positions(dense, a["depth"]))
    found: dict[tuple[str, int], list[tuple[float, int]]] = {}
    for h, d, o, e in zip(a["hole"].tolist(), a["depth"].tolist(), a["obj"].tolist(), a["event"].tolist(), strict=True):
        name = next(k for k, v in ids["holes"].items() if v == h)
        found.setdefault((name, o), []).append((d, e))

    def fault_height(p: np.ndarray) -> np.ndarray:
        return p[:, 2] - (lo[2] + FAULT_AT[2] + SLOPE * (p[:, 0] - (lo[0] + FAULT_AT[0])))

    def cube_height(p: np.ndarray) -> np.ndarray:  # > 0 inside
        q = p - lo
        return np.minimum(q, SIDE - q).min(axis=1)

    # the vertical hole: into the cube at 30 m, out at 130 m; the fault once on the way
    assert [e for _d, e in found[("С-1", 0)]] == [gi.ENTER, gi.LEAVE]
    assert [d for d, _e in found[("С-1", 0)]] == pytest.approx([30.0, 130.0], abs=1e-9)
    (through,) = found[("С-1", 1)]
    assert through[1] == gi.CROSS and through[0] == pytest.approx(130.0 - (20.0 + SLOPE * 90.3), abs=1e-9)
    # the hole that starts inside the cube leaves it at its bottom
    assert found[("С-2", 0)] == [(pytest.approx(60.0, abs=1e-9), gi.LEAVE)]
    # the curved hole: every depth against the scan of its trajectory (the acceptance is 1 mm)
    for obj, height in ((0, cube_height), (1, fault_height)):
        ref = crossings_by_scan(traj, st.index[ids["holes"]["С-3"]], height)
        got = [d for d, _e in found[("С-3", obj)]]
        assert len(ref) >= 1 and got == pytest.approx(ref, abs=1e-6), (obj, got, ref)
    assert [e for _d, e in found[("С-3", 0)]] == [gi.ENTER, gi.LEAVE]
    assert all(e == gi.CROSS for _d, e in found[("С-3", 1)])  # an open surface is never entered
    assert not any(name == "С-4" for name, _o in found)  # no collar: no trace

    # ETag and 304; the same marks for the other order of the list; a subset; a wireframe that is gone
    tag = r.headers["etag"]
    again = c.get(r.request.url, headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.content == b""
    r2, head2, a2 = marks(c, ids["set"], wireframes=f"{ids['fault']},{ids['cube']},424242")
    assert [o["id"] for o in head2["objects"]] == [ids["cube"], ids["fault"]] and r2.headers["etag"] == tag
    assert a2["depth"].tolist() == a["depth"].tolist()
    _r, head3, a3 = marks(c, ids["set"], wireframes=str(ids["fault"]))
    assert [o["id"] for o in head3["objects"]] == [ids["fault"]] and set(a3["event"].tolist()) == {gi.CROSS}
    assert a3["depth"].tolist() == [d for d, o in zip(a["depth"].tolist(), a["obj"].tolist(), strict=True) if o == 1]
    # without the list — every wireframe of the project (the DTM far away is crossed by no hole)
    _r, head4, a4 = marks(c, ids["set"])
    assert [o["name"] for o in head4["objects"]] == ["Куб", "Разлом", "Рельеф"] and head4["n"] == n
    _r, head5, _a = marks(c, ids["set"], wireframes="")
    assert head5["objects"] == [] and head5["n"] == 0
    # a new colour is a new answer with the same marks; a moved collar — new marks
    ok(c.patch(f"{API}/wireframes/{ids['cube']}", json={"expected_version": 1, "color": "#00ff00"}))
    r6, head6, a6 = marks(c, ids["set"], wireframes=wanted)
    assert r6.headers["etag"] != tag and head6["objects"][0]["color"] == "#00ff00"
    assert a6["depth"].tolist() == a["depth"].tolist()
    with dialect.write_tx(project.engine) as conn:
        conn.execute(sa.update(dh_holes).where(dh_holes.c.id == ids["holes"]["С-1"]).values(z=float(lo[2] + 140.0)))
        conn.execute(sa.update(objects).where(objects.c.id == ids["set"]).values(version=2))
    r7, _h, a7 = marks(c, ids["set"], wireframes=wanted)
    assert r7.headers["etag"] not in (tag, r6.headers["etag"])
    first = a7["depth"][(a7["hole"] == ids["holes"]["С-1"]) & (a7["obj"] == 0)]
    assert first.tolist() == pytest.approx([40.0, 140.0], abs=1e-9)


def test_intersections_refuse_bad_requests(api) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = api
    url = f"{API}/viz/sets/{ids['set']}/intersections"
    refused(c.get(f"{API}/viz/sets/987654/intersections"), 404, "set_not_found")
    refused(c.get(url, params={"method": "spline"}), 422, "bad_method")
    refused(c.get(url, params={"wireframes": "1,два"}), 422, "bad_wireframes")
    refused(c.get(url, params={"wireframes": ",".join(str(i) for i in range(1, 2500))}), 422, "bad_wireframes")


def test_intersections_hold_one_wireframe_at_a_time(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """The marks of every wireframe of the project (no `wireframes=`) are those the kernel gives for all of
    them at once, bit for bit, and are found a wireframe at a time: with a cache too small for two wireframes
    never more than one of them is in memory."""
    import gc
    import weakref

    from bf_core.drillholes.trajectory import trajectories

    c, project, ids = api
    _r, head, a = marks(c, ids["set"])
    assert [o["name"] for o in head["objects"]] == ["Куб", "Разлом", "Рельеф"] and head["n"] > 4
    st = trajectories(project, ids["set"])
    with dialect.read_tx(project.engine) as conn:
        targets = exact.set_targets(conn, None)
    at_once = gi.hole_crossings(st.traj, [exact.geometry(project, t).target(stats) for t, stats in targets])
    assert a["hole"].tolist() == st.hole_ids[at_once.hole].tolist() and a["obj"].tolist() == at_once.mesh.tolist()
    assert a["depth"].tobytes() == at_once.depth.tobytes() and a["xyz"].tobytes() == at_once.xyz.tobytes()
    assert a["event"].tolist() == at_once.event.tolist() and set(a["obj"].tolist()) == {0, 1}
    parts = [gi.hole_crossings(st.traj, [exact.geometry(project, t).target(stats)]) for t, stats in targets]
    joined = gi.join(parts)
    for name in ("hole", "mesh", "depth", "xyz", "triangle", "side", "event"):
        one, other = getattr(joined, name), getattr(at_once, name)
        assert one.dtype == other.dtype and one.shape == other.shape and one.tobytes() == other.tobytes(), name
    assert len(gi.join([])) == 0 and gi.join([]).xyz.shape == (0, 3)
    del at_once, parts, joined, targets

    exact.forget()
    monkeypatch.setattr(exact, "CACHE_BYTES", 1024)
    alive: list[weakref.ReferenceType[np.ndarray]] = []
    seen: list[tuple[int, int]] = []
    load, cross = exact._load, gi.hole_crossings

    def loading(*args: Any) -> exact.Geometry:
        g = load(*args)
        alive.append(weakref.ref(g.xyz if g.xyz.base is None else g.xyz.base))  # the memory of the vertices
        return g

    def crossing(traj: Any, meshes: Any, **rest: Any) -> gi.Crossings:
        gc.collect()
        seen.append((len(meshes), sum(ref() is not None for ref in alive)))
        return cross(traj, meshes, **rest)

    monkeypatch.setattr(exact, "_load", loading)
    monkeypatch.setattr(gi, "hole_crossings", crossing)
    _r, head, again = marks(c, ids["set"])
    assert seen == [(1, 1)] * 3 and exact.cache_info()["entries"] == 1  # three wireframes, one at a time
    assert head["n"] == len(a["depth"]) and all(again[k].tobytes() == a[k].tobytes() for k in a)


# --- the cache ---------------------------------------------------------------------------------------------


def test_geometry_is_loaded_once_and_kept_within_the_limit(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = api
    lo = ids["lo"]
    wanted = f"{ids['cube']},{ids['fault']}"
    section(c, ids["cube"], point=triple(lo + 10.0), normal="1,0,0")
    pick(c, lo + [-40.0, 33.3, 61.7], [1.0, 0.0, 0.0], wireframe_ids=[ids["cube"]])
    marks(c, ids["set"], wireframes=wanted)
    marks(c, ids["set"], wireframes=wanted, method="balanced_tangent")
    info = exact.cache_info()
    assert info["entries"] == 2 and info["loads"] == 2 and info["trees"] == 2  # one tree a wireframe for both methods
    assert 0 < info["bytes"] <= exact.CACHE_BYTES
    # a limit below one geometry: the last one asked for stays alone
    monkeypatch.setattr(exact, "CACHE_BYTES", 1024)
    section(c, ids["fault"], point=triple(lo + 10.0), normal="1,0,0")
    assert exact.cache_info()["entries"] == 1
    section(c, ids["cube"], point=triple(lo + 10.0), normal="1,0,0")
    info = exact.cache_info()
    assert info["entries"] == 1 and info["loads"] == 3  # the cube was read again
    exact.forget()
    assert exact.cache_info() == {"entries": 0, "bytes": 0, "loads": 0, "trees": 0}


def test_deleted_wireframe_and_another_project_leave_the_cache(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = api
    lo = ids["lo"]
    wanted = f"{ids['cube']},{ids['fault']}"
    _r, head, _a = marks(c, ids["set"], wireframes=wanted)
    assert head["n"] > 0 and exact.cache_info()["entries"] == 2 and len(exact._marks) == 1
    # a deleted wireframe: its arrays and the marks found on it go at once, the other wireframe stays
    assert ok(c.delete(f"{API}/wireframes/{ids['fault']}"))["deleted"] == ids["fault"]
    assert exact.cache_info()["entries"] == 1 and len(exact._marks) == 0
    _r, head, a = marks(c, ids["set"], wireframes=wanted)
    assert [o["id"] for o in head["objects"]] == [ids["cube"]] and set(a["obj"].tolist()) == {0}
    assert pick(c, lo + [-23.7, 31.9, 500.0], [0.0, 0.0, -1.0]) is None  # where the fault was
    assert exact.cache_info()["loads"] == 2  # nothing was read again
    # another project: nothing of the first one is kept
    assert c.post(f"{API}/projects", json={"name": "Другой проект"}).status_code == 200
    other = projects_of(c.core).require_current()
    v, t = grid(6, 6, fault_z, step=5.0, offset=lo)
    with dialect.write_tx(other.engine) as conn:
        wf = ws.put_wireframe(conn, BlobStore(other.folder), v, t, name="Склон")
    hit = pick(c, lo + [12.5, 7.5, 300.0], [0.0, 0.0, -1.0])
    assert hit["wireframe_id"] == wf and hit["point"][2] == pytest.approx(lo[2] + SLOPE * 12.5, abs=1e-9)
    info = exact.cache_info()
    assert info["entries"] == 1 and info["loads"] == 3
