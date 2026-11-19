"""Demo part of Э9 (demo/contours.py; ТЗ 7.5, 7.23; ADR 0015 §6–§7, решение 6): contours on the sections of the
hole grid with a branching 1 : 2 and tie lines `ROLE = TIE`, the solid «Рудное тело 3» stitched from them, and the
isolines of «Рельеф». The criterion of 9.34: the layer as stored → `stitch` → the closed solid of the demo."""

from __future__ import annotations

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.demo import contours as dc
from bf_core.demo import geo
from bf_core.geo.checks import mesh_checks
from bf_core.geo.stitch import stitch_contours
from bf_core.lines import store as ls_store
from bf_core.lines.geometry_io import tie_lines
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as wf_store
from tests.test_import_job import API

pytestmark = pytest.mark.windows


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    return pclient, project, BlobStore(project.folder)


def _file(c, name: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    files = [f for f in c.get(f"{API}/lines/files").json()["files"] if f["name"] == name]
    assert len(files) == 1, name
    return files[0]


def _meta(conn: sa.Connection, object_id: int) -> dict:  # type: ignore[type-arg]
    return dict(conn.execute(sa.select(objects.c.meta).where(objects.c.id == object_id)).scalar_one() or {})


def _faces(t: np.ndarray) -> set[tuple[int, ...]]:
    """Triangles as a set, each from its smallest vertex on: the same orientation, any order and start."""
    out = set()
    for tri in np.asarray(t).tolist():
        k = tri.index(min(tri))
        out.add(tuple(tri[k:] + tri[:k]))
    return out


def test_contours_stitch_into_the_closed_body(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, store = demo
    info = _file(c, dc.CONTOURS_FILE)["layers"]
    assert [x["name"] for x in info] == [dc.CONTOURS]
    assert (info[0]["line_count"], info[0]["color"]) == (13, dc.CONTOURS_COLOR)
    listed = {w["name"]: w for w in c.get(f"{API}/wireframes").json()["wireframes"]}
    body = listed[dc.BODY_3]
    with dialect.read_tx(project.engine) as conn:
        layer = ls_store.read_layer(conn, store, info[0]["id"])
        v, t = wf_store.read_wireframe(conn, store, body["id"])
        built = _meta(conn, body["id"])["built"]

    # the layer as the operation reads it: the closed lines are the contours, the lines marked TIE the ties
    ties = tie_lines(layer)
    assert len(ties) == 4 and all(not layer.closed[i] for i in ties)
    contours = []
    for i in range(layer.line_count):
        p = np.asarray(layer.line(i), dtype=np.float64)
        if i not in ties:
            assert layer.closed[i]
            contours.append(p[:-1])  # a stored closed line repeats its first vertex
    assert len(contours) == 9
    sections = sorted({float(p[0, 1]) for p in contours})
    assert sections == list(dc.SECTIONS)
    on = [sum(float(p[0, 1]) == y for p in contours) for y in sections]
    assert on == [1, 1, 1, 2, 2, 2]  # the branching 1 : 2 between Y 8100 and Y 8150
    ends = {row.tobytes() for p in contours for row in p}
    tie_xyz = [np.asarray(layer.line(i), dtype=np.float64) for i in ties]
    assert all(e.tobytes() in ends for line in tie_xyz for e in line)  # both ends on contour vertices
    assert all(abs(line[1, 1] - line[0, 1]) == 50.0 for line in tie_xyz)  # neighbouring sections

    # the criterion: the layer → stitch → the closed solid of the demo, the same one
    result = stitch_contours(contours, tie_xyz, start_cap=dc.CAP, end_cap=dc.CAP)
    rep = result.report
    assert rep["closed"] and rep["ties_used"] == 4 and not rep["warnings"]
    assert sum(p["branch"] for p in rep["pairs"]) == 1
    assert np.array_equal(result.vertices, v)
    assert _faces(result.triangles) == _faces(t)  # the store may order the triangles its own way
    s = body["stats"]
    assert s["closed"] and s["orientation"] == "outward" and s["open_edges"] == 0 and s["nonmanifold_edges"] == 0
    assert s["degenerate"] == 0 and 5e5 < s["volume"] < 1.2e6
    found = mesh_checks(v, t)
    assert found["self_intersections"] == 0 and found["orientation_conflicts"] == 0
    assert all(row.tobytes() in {x.tobytes() for x in v} for p in contours for row in p)  # bit for bit
    # every tie is an edge of the solid
    index = {row.tobytes(): k for k, row in enumerate(v)}
    edges = {frozenset(e) for tri in t.tolist() for e in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0]))}
    assert all(frozenset((index[a.tobytes()], index[b.tobytes()])) in edges for a, b in tie_xyz)
    # under «Рудное тело 1» and the ground, within the plan of the site
    ore = {w["name"]: w for w in listed.values()}["Рудное тело 1"]
    with dialect.read_tx(project.engine) as conn:
        v1, _ = wf_store.read_wireframe(conn, store, ore["id"])
    assert v[:, 2].max() < v1[:, 2].min()
    assert geo.AREA[0] < v[:, 0].min() and v[:, 0].max() < geo.AREA[2]
    assert geo.AREA[1] < v[:, 1].min() and v[:, 1].max() < geo.AREA[3]

    # the record of the operation (ADR 0015 §6): the layer by its id, all its lines, the parameters to rebuild
    assert built["kind"] == "operation" and built["op"] == "stitch" and built["rebuild_of"] is None
    assert built["params"] == {
        "contours": [{"layer_id": info[0]["id"], "lines": None}],
        "start_cap": dc.CAP, "end_cap": dc.CAP, "cap_distance": 0.5,
    }  # fmt: skip
    assert built["sources"] == [
        {"role": "contours[0]", "kind": "line_layer", "id": info[0]["id"], "name": dc.CONTOURS, "version": 1,
         "lines": None},
    ]  # fmt: skip
    assert built["report"]["volume"] == pytest.approx(s["volume"]) and built["report"]["ties_used"] == 4
    assert set(built["tolerances"]) == {"section", "tie"}


def test_isolines_of_the_relief(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, store = demo
    file = _file(c, dc.ISOLINES_FILE)
    assert [x["name"] for x in file["layers"]] == ["Основные", "Дополнительные"]
    relief = next(w for w in c.get(f"{API}/wireframes").json()["wireframes"] if w["name"] == geo.DTM)
    with dialect.read_tx(project.engine) as conn:
        layers = [ls_store.read_layer(conn, store, x["id"]) for x in file["layers"]]
        metas = [_meta(conn, x["id"]) for x in file["layers"]]
        file_meta = _meta(conn, file["id"])
        v, _t = wf_store.read_wireframe(conn, store, relief["id"])
    lo, hi = float(v[:, 2].min()), float(v[:, 2].max())
    levels = set()
    for layer, major in zip(layers, (True, False), strict=True):
        assert layer.line_count > 0
        elev = layer.attrs.column("ELEV").to_pylist()
        for i in range(layer.line_count):
            p = np.asarray(layer.line(i), dtype=np.float64)
            assert len(p) >= 2 and np.all(p[:, 2] == elev[i])  # Z of a line is its level exactly
            assert lo <= elev[i] <= hi
            assert elev[i] % dc.INTERVAL == 0 and (elev[i] % (dc.INTERVAL * dc.MAJOR_EVERY) == 0) == major
            levels.add(elev[i])
    assert len(levels) >= 5  # the relief rises some 20 m over the site
    built = file_meta["built"]
    assert file_meta["format"] == "operation" and all(m["built"] == built for m in metas)
    assert built["op"] == "contours" and built["params"]["wireframe_id"] == relief["id"]
    assert built["sources"][0]["name"] == geo.DTM and built["report"]["levels"] == len(levels)
