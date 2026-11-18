"""Demo part of Э8 (demo/wireframes.py; ТЗ 7.5, 7.21; ADR 0013 «Демо»): two closed ore bodies and an open fault in
«Каркасы». The solids are closed and look outward, with a volume; none of them crosses itself; the first goes
through the strings of the ore lens bit for bit; the holes of the demo cross them — the marks of 7.15 —
entering and leaving the solids, and the fault, which is never a DTM."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.demo import wireframes as dw
from bf_core.geo.checks import mesh_checks
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from bf_core.wireframes import store as wf_store
from tests.test_import_job import API

pytestmark = pytest.mark.windows

NAMES = [dw.BODY_1, dw.BODY_2, dw.FAULT]


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    return pclient, project, BlobStore(project.folder)


def test_ore_bodies_and_the_fault(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, store = demo
    listed = c.get(f"{API}/wireframes").json()["wireframes"]
    solids = [w for w in listed if w["dtm"] is None]
    assert sorted(w["name"] for w in solids) == sorted(NAMES)
    by_name = {w["name"]: w for w in solids}
    body1, body2, fault = (by_name[n] for n in NAMES)
    assert (body1["color"], body2["color"], fault["color"]) == ("#e05d6f", dw.BODY_2_COLOR, dw.FAULT_COLOR)
    assert body1["attrs"] == [{"name": "Тип", "value": "Руда"}, {"name": "Плотность", "value": 2.7}]
    assert fault["attrs"] == [{"name": "Тип", "value": "Разлом"}]
    for body in (body1, body2):
        s = body["stats"]
        assert s["closed"] and s["orientation"] == "outward" and s["open_edges"] == 0 and s["degenerate"] == 0
        assert s["nonmanifold_edges"] == 0 and s["volume"] > 0
    assert fault["stats"]["closed"] is False and fault["stats"]["volume"] is None and fault["stats"]["open_edges"] > 0
    # the lens of the strings: 300 m along Y, some 435 m across, up to 40 m thick → a few million m³
    assert 1.5e6 < body1["stats"]["volume"] < 6e6
    # the ellipsoid: 4/3·π·a·b·c, a little less for its facets
    a, b, cc = dw.LENS_AXES
    assert 0.9 * 4 / 3 * np.pi * a * b * cc < body2["stats"]["volume"] < 4 / 3 * np.pi * a * b * cc

    with dialect.read_tx(project.engine) as conn:
        meshes = {n: wf_store.read_wireframe(conn, store, by_name[n]["id"]) for n in NAMES}
        files = [f for f in c.get(f"{API}/lines/files").json()["files"] if f["name"] == "Рудное тело и дорога"]
        ore = ls_store.read_layer(conn, store, files[0]["layers"][0]["id"])
    # the solid goes through every vertex of every string, float64 bit for bit
    vertices = {row.tobytes() for row in meshes[dw.BODY_1][0]}
    assert all(row.tobytes() in vertices for row in ore.xyz)
    for name, (v, t) in meshes.items():
        found = mesh_checks(v, t)
        assert found["self_intersections"] == 0 and found["orientation_conflicts"] == 0, name
        assert np.isfinite(v).all()
    # «Рудное тело 2» lies above the lens and under the ground; the fault is under the ground too
    assert meshes[dw.BODY_2][0][:, 2].min() > meshes[dw.BODY_1][0][:, 2].max()
    assert meshes[dw.BODY_2][0][:, 2].max() < 400 and meshes[dw.FAULT][0][:, 2].max() < 410


def test_holes_of_the_demo_cross_them(demo) -> None:  # type: ignore[no-untyped-def]
    c, _project, _store = demo
    set_id = c.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    listed = {w["name"]: w["id"] for w in c.get(f"{API}/wireframes").json()["wireframes"] if w["dtm"] is None}
    ids = [listed[n] for n in NAMES]
    r = c.get(f"{API}/viz/sets/{set_id}/intersections", params={"wireframes": ",".join(map(str, ids))})
    assert r.status_code == 200, r.text
    head, a = binary.decode(r.content)
    assert [o["name"] for o in head["objects"]] == NAMES
    obj = a["obj"].astype(int)
    holes = {k: set(a["hole"][obj == k].tolist()) for k in range(3)}
    # nearly every drilled hole enters the lens (a short one in the east ends above it); the flat lens and the
    # fault cut some of them
    counts = {k: len(v) for k, v in holes.items()}
    assert counts[0] >= 40 and 4 <= counts[1] <= 20 and 6 <= counts[2] <= 24, counts
    assert 100 < head["n"] < 300, head["n"]  # a few marks per hole, not a label for every triangle
