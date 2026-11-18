"""Paths of parts for 3D (ADR 0007 §1): the polyline is within `tol` of `Trajectories.positions()` (float32
offsets from the part origin add ≤ 0.43 mm), a straight course is 2 points, ETag and 304."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.drillholes.desurvey import METHODS, Collars, Surveys, desurvey
from bf_core.viz import binary
from bf_core.viz.arcs import densify, hole_bounds
from tests.test_import_job import API
from tests.test_viz_holes import manifest, open_demo

pytestmark = pytest.mark.windows

F32 = 5e-4  # float32 rounding of offsets ≤ 8000.5 m: ≤ 0.25 mm per axis, ≤ 0.43 mm in space


def _hard() -> tuple[Collars, Surveys]:
    """Hard holes at UTM coordinates: 0 demo-like, 1 a 25° bend, 2 a near reversal over 10 m, 3 an exact
    reversal, 4 horizontal turning upwards, 5 a 90° bend over 1000 m, 6 vertical, 7 no coordinates, 8 a collar
    without TD or surveys."""
    cx = np.array([600000.0, 600050, 600100, 600150, 600200, 600250, 600300, np.nan, 600400])
    cy = np.full(9, 7_000_000.0)
    cz = np.full(9, 400.0)
    td = np.array([200, 120, 40, 40, 300, 1000, 500, 100, np.nan], dtype=float)
    s: list[tuple[int, float, float, float]] = [(0, d, 90 + d * 0.02, -60 + d * 0.03) for d in range(0, 201, 30)]
    s += [(1, 0, 90, -60), (1, 30, 90, -60), (1, 60, 115, -60), (1, 90, 115, -55)]
    s += [(2, 0, 0, -10), (2, 10, 179.9, 10), (3, 0, 0, 0), (3, 10, 180, 0)]
    s += [(4, 0, 45, 0), (4, 100, 45, 0), (4, 200, 60, 30), (5, 0, 0, -90), (5, 1000, 90, 0)]
    h, d, a, p = (np.array(v) for v in zip(*s, strict=True))
    return Collars(np.arange(9), cx, cy, cz, td), Surveys(
        h.astype(np.int64), d.astype(float), a.astype(float), p.astype(float)
    )


def _deviation(traj, hole: int, offsets, xyz, md, origin, j: int, rng) -> float:  # type: ignore[no-untyped-def]
    """Largest distance between the polyline (linear by depth) and positions() at random depths and vertices."""
    rows = slice(int(offsets[j]), int(offsets[j + 1]))
    p = xyz[rows].astype(np.float64) + np.asarray(origin)
    m = md[rows]
    d = np.concatenate([rng.uniform(0.0, m[-1], 3000), m])
    ref = traj.positions(np.full(len(d), hole), d)
    got = np.column_stack([np.interp(d, m, p[:, i]) for i in range(3)])
    return float(np.linalg.norm(got - ref, axis=1).max())


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("tol", [0.001, 0.01, 0.1])
def test_polyline_within_tolerance_of_positions(method: str, tol: float) -> None:
    rng = np.random.default_rng(11)
    collars, surveys = _hard()
    traj = desurvey(collars, surveys, method=method)
    holes = np.array([0, 1, 2, 3, 4, 5, 6, 8])
    box = hole_bounds(traj, holes, tol)
    origin = np.round((box[:, :3].min(axis=0) + box[:, 3:].max(axis=0)) / 2)
    offsets, xyz, md = densify(traj, holes, tol, origin)
    assert len(offsets) == len(holes) + 1 and offsets[0] == 0 and xyz.dtype == np.float32
    worst = 0.0
    for j, h in enumerate(holes.tolist()):
        if offsets[j + 1] - offsets[j] < 2:
            continue
        dev = _deviation(traj, h, offsets, xyz, md, origin, j, rng)
        worst = max(worst, dev)
        assert dev <= tol + F32, (h, dev)
        mdj = md[offsets[j] : offsets[j + 1]]
        assert np.all(np.diff(mdj) > 0) and mdj[0] == 0.0
    if method == "minimum_curvature":
        assert worst > 0.5 * tol  # the tolerance is used, the arcs are not over-sampled
    else:
        assert worst <= F32  # straight between the points
    counts = np.diff(offsets)
    assert counts[holes.tolist().index(6)] == 2  # a straight hole: the collar and the end
    assert counts[holes.tolist().index(8)] == 1  # a collar only
    with pytest.raises(ValueError):
        densify(traj, holes, tol, origin, max_points=10)


def test_arc_step_and_bounds() -> None:
    collars, surveys = _hard()
    traj = desurvey(collars, surveys)
    # 90° over 1000 m: R = 636.6 m, step √(8·R·0.01) = 7.14 m → 141 pieces
    offsets, _xyz, md = densify(traj, [5], 0.01, [600250, 7_000_000, 0])
    assert int(offsets[1]) == 142 and md[-1] == 1000.0
    box = hole_bounds(traj, [5, 7])
    assert np.isnan(box[1]).all()  # no coordinates
    pts = traj.positions(np.full(2001, 5), np.linspace(0, 1000, 2001))
    assert np.all(box[0, :3] <= pts.min(axis=0) + 0.01) and np.all(box[0, 3:] >= pts.max(axis=0) - 0.01)
    with pytest.raises(ValueError):
        hole_bounds(traj, [0], 0.0)


# --- API ---------------------------------------------------------------------------------------------


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    return open_demo(pclient)


def _paths(c, set_id: int, k: int, **params):  # type: ignore[no-untyped-def]
    r = c.get(f"{API}/viz/sets/{set_id}/paths/{k}", params=params)
    assert r.status_code == 200, r.text
    return r, *binary.decode(r.content)


def test_demo_paths_match_positions(demo) -> None:  # type: ignore[no-untyped-def]
    from bf_core.drillholes.trajectory import trajectories

    c, set_id = demo
    head, holes = manifest(c, set_id)
    project = c.core.services["projects"].require_current()
    rng = np.random.default_rng(5)
    for method in ("minimum_curvature", "balanced_tangent"):
        st = trajectories(project, set_id, method)
        r, ph, a = _paths(c, set_id, 0, method=method)
        assert ph["kind"] == "paths" and ph["k"] == 0 and ph["n"] == 49 and ph["tol"] == 0.01  # with the trench
        assert ph["origin"] == head["parts"][0]["origin"] and all(float(v).is_integer() for v in ph["origin"])
        offsets = a["offsets"]
        assert offsets.dtype == np.dtype("<u4") and len(offsets) == 50 and offsets[-1] == ph["points"]
        xyz = a["xyz"].reshape(-1, 3)
        assert len(xyz) == len(a["md"]) == ph["points"] and np.abs(xyz).max() <= 8000.5
        if method == "minimum_curvature":
            assert r.headers["etag"] == head["parts"][0]["etag"]
        for j, hole_id in enumerate(holes["id"].tolist()):
            dense = st.index[hole_id]
            dev = _deviation(st.traj, dense, offsets, xyz, a["md"], ph["origin"], j, rng)
            assert dev <= 0.01 + F32, (hole_id, dev)
            first = xyz[offsets[j]].astype(np.float64) + ph["origin"]
            assert np.abs(first - holes["collar"].reshape(-1, 3)[j]).max() < F32  # the collar


def test_paths_etag_304_and_errors(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    url = f"{API}/viz/sets/{set_id}/paths/0"
    first = c.get(url)
    tag = first.headers["etag"]
    hit = c.get(url, headers={"If-None-Match": tag})
    assert hit.status_code == 304 and hit.content == b"" and hit.headers["cache-control"] == "no-cache"
    fine = c.get(url, params={"tol": 0.001}, headers={"If-None-Match": tag})
    assert fine.status_code == 200 and fine.headers["etag"] != tag
    assert binary.decode(fine.content)[0]["points"] > binary.decode(first.content)[0]["points"]
    assert c.get(url, params={"method": "tangent"}, headers={"If-None-Match": tag}).status_code == 200

    # an edit of a survey moves the path: a new ETag, the new data
    surveys = c.get(f"{API}/drillholes/sets/{set_id}/tables/survey", params={"limit": 1}).json()["rows"]
    r = c.patch(
        f"{API}/drillholes/sets/{set_id}/tables/survey/rows/{surveys[0]['id']}", json={"column": "dip", "value": "-89"}
    )
    assert r.status_code == 200, r.text
    moved = c.get(url, headers={"If-None-Match": tag})
    assert moved.status_code == 200 and moved.headers["etag"] != tag and moved.content != first.content

    r = c.get(f"{API}/viz/sets/{set_id}/paths/1")
    assert r.status_code == 404 and r.json()["code"] == "part_not_found"
    for bad in (0, 0.0001, 11, "nan"):
        r = c.get(url, params={"tol": bad})
        assert r.status_code == 422, bad
    r = c.get(url, params={"tol": 0})
    assert r.json()["code"] == "bad_tolerance" and r.json()["hint"] == "Укажите допуск от 0,001 до 10 м."
    assert c.get(url, params={"method": "x"}).json()["code"] == "bad_method"
