"""Demo parts of Э7 (demo/geo.py; ТЗ 7.5, 7.18–7.20, 7.35 Д-16): the trench in «Скважины 2025», the strings of the
ore body and the road, the survey points, the plan with its world file and the DTM with the plan on it.
The holes stay as they were, everything is the same in every installation, the audit finds nothing new."""

from __future__ import annotations

import hashlib
import io
import math
import random
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, dh_holes, dh_rows, dh_surveys, dh_tables, object_refs, objects
from bf_core.demo import geo
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.rasters import store as rs
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as wf_store
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    return pclient, project, BlobStore(project.folder)


def _set(conn: sa.Connection) -> int:
    return int(conn.execute(sa.select(objects.c.id).where(objects.c.kind == "drillhole_set")).scalar_one())


def _inside(point: tuple[float, float], ring: np.ndarray) -> bool:
    """Even-odd rule for a point and a closed ring (k, 2)."""
    x, y = point
    hit = False
    for (x1, y1), (x2, y2) in zip(ring[:-1].tolist(), ring[1:].tolist(), strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def test_holes_of_the_demo_are_untouched(demo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The parts of Э7 come after the holes in the random stream: collars, surveys and samples are the same."""
    from bf_core.db import migrate
    from bf_core.demo.drillholes import add_drillholes
    from tests.dbutil import make_engine

    _c, project, _store = demo
    alone = make_engine("sqlite", tmp_path)
    migrate.upgrade(alone, "project")
    try:
        with dialect.write_tx(alone) as conn:
            add_drillholes(conn, BlobStore(tmp_path), random.Random(7))

        def content(engine: sa.Engine) -> tuple[list[Any], list[Any], list[Any]]:
            h, s, r = dh_holes.c, dh_surveys.c, dh_rows.c
            drilled = h.name.like("DH-%")
            with dialect.read_tx(engine) as conn:
                holes = conn.execute(sa.select(h.name, h.x, h.y, h.z, h.td, h.azimuth, h.dip).where(drilled)).all()
                ids = sa.select(h.id).where(drilled)
                surveys = conn.execute(
                    sa.select(s.depth, s.azimuth, s.dip).where(s.hole_id.in_(ids)).order_by(s.id)
                ).all()
                rows = conn.execute(
                    sa.select(r.from_m, r.to_m, r.num, r.txt).where(r.hole_id.in_(ids)).order_by(r.id)
                ).all()
            return [tuple(x) for x in holes], [tuple(x) for x in surveys], [tuple(x) for x in rows]

        a, b = content(alone), content(project.engine)
        assert len(a[0]) == 48 and a[0] == b[0] and a[1] == b[1] and len(a[2]) > 3000 and a[2] == b[2]
    finally:
        alone.dispose()


def test_trench_is_a_hole_with_stations_at_its_vertices_and_samples_along_it(demo) -> None:  # type: ignore[no-untyped-def]
    from bf_core.drillholes.trajectory import trajectories

    c, project, _store = demo
    sets = c.get(f"{API}/drillholes/sets").json()["sets"]
    assert [s["name"] for s in sets] == ["Скважины 2025"] and sets[0]["holes"] == 49
    with dialect.read_tx(project.engine) as conn:
        set_id = _set(conn)
        collars = geo._collars(conn)
        hole = conn.execute(sa.select(dh_holes).where(dh_holes.c.name == geo.TRENCH)).mappings().one()
        stations = conn.execute(
            sa.select(dh_surveys.c.depth, dh_surveys.c.x, dh_surveys.c.y, dh_surveys.c.z, dh_surveys.c.azimuth)
            .where(dh_surveys.c.hole_id == hole["id"])
            .order_by(dh_surveys.c.depth)
        ).all()
        table = conn.execute(sa.select(dh_tables).where(dh_tables.c.kind == "assay")).mappings().one()
        samples = conn.execute(
            sa.select(dh_rows.c.from_m, dh_rows.c.to_m).where(dh_rows.c.hole_id == hole["id"]).order_by(dh_rows.c.from_m)
        ).all()
        total = conn.execute(sa.select(sa.func.count()).select_from(dh_rows)).scalar_one()
    assert len(collars) == 48
    line = geo.trench_line(collars)
    assert len(line) == 9 and hole["attrs"] == {"TYPE": "Канава"} and hole["azimuth"] is None and hole["dip"] is None
    assert np.array([hole["x"], hole["y"], hole["z"]]).tobytes() == line[0].tobytes()
    # a station at every vertex: its coordinates bit for bit, its depth — the 3D length from the first vertex
    assert np.array([s[1:4] for s in stations]).tobytes() == line.tobytes() and all(s[4] is None for s in stations)
    length = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(line, axis=0), axis=1))])
    assert np.abs(np.array([s[0] for s in stations]) - length).max() <= 1e-9 and hole["td"] == pytest.approx(length[-1], abs=1e-9)
    assert 235.0 < length[-1] < 250.0
    # the trench is dug: 1.5 m below the ground the survey measured at its edge (centimetres)
    assert np.abs(geo.relief(line[:, 0], line[:, 1], collars) - 1.5 - line[:, 2]).max() <= 0.005 + 1e-9
    # samples of 2 m from the start to the end of the trench, without gaps
    assert samples[0][0] == 0.0 and samples[-1][1] == pytest.approx(length[-1], abs=1e-9)
    assert all(a[1] == b[0] for a, b in zip(samples[:-1], samples[1:], strict=True))
    assert len(samples) == math.ceil(length[-1] / 2.0) and table["row_count"] == total  # the counter of the table
    # «от/до» run along the polyline: the trajectory passes through the vertices
    st = trajectories(project, set_id)
    k = st.names.index(geo.TRENCH)
    at = st.traj.positions(np.full(len(line), k), length)
    assert np.abs(at - line).max() <= 1e-9
    assert not st.skipped


def test_lines_points_plan_and_dtm(demo) -> None:  # type: ignore[no-untyped-def]
    from PIL import Image

    c, project, store = demo
    files = c.get(f"{API}/lines/files").json()["files"]
    assert [(f["name"], [x["name"] for x in f["layers"]]) for f in files] == [
        ("Рудное тело и дорога", ["Рудное тело", "Дорога"]),
        ("Топосъёмка", ["Пикеты"]),
    ]
    ore_info, road_info = files[0]["layers"]
    assert (ore_info["line_count"], ore_info["closed"], ore_info["color"]) == (6, "all", "#e05d6f")
    assert (road_info["line_count"], road_info["closed"]) == (2, "none") and files[1]["layers"][0]["points_only"]
    with dialect.read_tx(project.engine) as conn:
        collars = geo._collars(conn)
        ore = ls_store.read_layer(conn, store, ore_info["id"])
        road = ls_store.read_layer(conn, store, road_info["id"])
        points = ls_store.read_layer(conn, store, files[1]["layers"][0]["id"])
        rasters = rs.list_rasters(conn)
        wires = wf_store.list_wireframes(conn)
        v, t = wf_store.read_wireframe(conn, store, wires[0]["id"])
        drape = conn.execute(sa.select(object_refs.c.src_id, object_refs.c.dst_id, object_refs.c.role)).all()
        roles = {
            str(r.role): str(r.sha256)
            for r in conn.execute(sa.select(blob_owners).where(blob_owners.c.owner_id == rasters[0]["id"]))
        }
        states = set(conn.execute(sa.select(objects.c.state)).scalars())
    assert states == {"ready"}

    # strings of the ore lens: one on every section line, closed, around the centre of the lens at every column
    assert ore.attrs.column("Разрез").to_pylist() == [f"Y {8000 + 50 * i}" for i in range(6)]
    for i in range(6):
        ring = ore.line(i)
        assert ring[0].tobytes() == ring[-1].tobytes() and set(ring[:, 1].tolist()) == {8000.0 + 50.0 * i}
        for col in range(8):
            assert _inside((5045.0 + 53.0 * col, 342.0 - 5.2 * col), ring[:, [0, 2]]), (i, col)
        assert not _inside((5045.0, 420.0), ring[:, [0, 2]])  # the collars are well above it
    # the road and the survey points lie on the ground (heights in centimetres)
    for layer in (road, points):
        ground = geo.relief(layer.xyz[:, 0], layer.xyz[:, 1], collars)
        assert np.abs(ground - layer.xyz[:, 2]).max() <= 0.005 + 1e-9
    assert points.point_count == 23 * 19 + 9 and points.attrs.column("Код").to_pylist().count("канава") == 9
    assert np.isfinite(points.xyz).all() and np.isfinite(road.xyz).all() and np.isfinite(ore.xyz).all()
    # the ground meets the collars within centimetres where the function is asked, exactly in the DTM
    assert np.abs(geo.relief(collars[:, 0], collars[:, 1], collars) - collars[:, 2]).max() < 0.01

    # the plan: the centre of the top-left pixel is where the world file puts it; the corners are the area
    assert [r["name"] for r in rasters] == ["План участка"]
    plan = rasters[0]
    assert (plan["width"], plan["height"]) == (1100, 900) and not plan["vertical"] and plan["elevation"] is None
    a, b, cc, d, e, f = plan["georef"]["affine"]
    assert abs(a * 0.5 + b * 0.5 + cc - 4900.25) <= 1e-9 and abs(d * 0.5 + e * 0.5 + f - 8349.75) <= 1e-9
    assert plan["georef"]["source"] == "world"
    assert [p[:2] for p in plan["georef"]["corners3d"]] == [[4900.0, 8350.0], [5450.0, 8350.0], [5450.0, 7900.0], [4900.0, 7900.0]]
    assert set(roles) == {"source", "sidecar:pgw", "display0", "display1"}
    assert store.read_bytes(roles["sidecar:pgw"]).decode("ascii").split() == ["0.5", "0.0", "0.0", "-0.5", "4900.25", "8349.75"]
    source = Image.open(io.BytesIO(store.read_bytes(roles["source"])))
    assert source.format == "PNG" and source.size == (1100, 900)
    # a collar is drawn where it is: the dark dot at the pixel of DH-001
    px = np.asarray(source.convert("RGB"))
    col, row = int((collars[0, 0] - 4900.0) / 0.5), int((8350.0 - collars[0, 1]) / 0.5)
    assert px[row, col].tolist() == [30, 36, 44] and px[5, 5].tolist() != [30, 36, 44]

    # the DTM: built from the survey points, the collars and the road, the plan laid on it
    assert [w["name"] for w in wires] == ["Рельеф"]
    dtm = wires[0]
    assert dtm["dtm"] == {"raster_id": plan["id"], "clip": True, "tint": "#ffffff", "opacity": 1.0, "shade": True}
    assert [tuple(r) for r in drape] == [(dtm["id"], plan["id"], "drape")]
    vertices = {row.tobytes() for row in v}
    for source_xyz in (points.xyz, collars, road.xyz):
        assert all(row.tobytes() in vertices for row in source_xyz)  # float64 bit for bit
    assert len(v) == points.point_count + 48 + road.point_count and len(t) > 900
    area = 0.5 * np.abs(
        (v[t[:, 1], 0] - v[t[:, 0], 0]) * (v[t[:, 2], 1] - v[t[:, 0], 1])
        - (v[t[:, 2], 0] - v[t[:, 0], 0]) * (v[t[:, 1], 1] - v[t[:, 0], 1])
    )
    assert area.min() > 0 and 0.9 * 550 * 450 < area.sum() <= 550 * 450  # the hull of the survey, no overlaps
    # every segment of the road is an edge of the surface (a structural line)
    index = {row.tobytes(): i for i, row in enumerate(v)}
    edges = {frozenset(e) for tri in t.tolist() for e in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0]))}
    for i in range(road.line_count):
        line = road.line(i)
        ids = [index[p.tobytes()] for p in line]
        assert all(frozenset(e) in edges for e in zip(ids[:-1], ids[1:], strict=True)), i
    info = c.get(f"{API}/wireframes/{dtm['id']}").json()
    assert info["point_count"] == len(v) and info["triangle_count"] == len(t)
    # the 3D window gets it: the manifest and its only part
    r = c.get(f"{API}/viz/wireframes/{dtm['id']}")
    assert r.status_code == 200, r.text


def test_demo_is_the_same_everywhere_and_the_audit_finds_nothing_new(demo) -> None:  # type: ignore[no-untyped-def]
    """Seed 7: the generated data do not depend on the run (collars are read back from the database)."""
    c, project, store = demo
    with dialect.read_tx(project.engine) as conn:
        set_id = _set(conn)
        collars = geo._collars(conn)
        layer_ids = [i for f in conn.execute(sa.select(objects.c.id).where(objects.c.kind == "line_file").order_by(objects.c.id)).scalars() for i in ls_store.layer_ids(conn, f)]
        got = [ls_store.read_layer(conn, store, i).xyz.tobytes() for i in layer_ids]
    rng = random.Random(7)
    from bf_core.db import migrate
    from bf_core.demo.drillholes import add_drillholes
    from tests.dbutil import make_engine

    # the stream after the holes and the samples of the trench: replay the parts on a database of their own
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine("sqlite", Path(tmp))
        migrate.upgrade(engine, "project")
        try:
            other = BlobStore(Path(tmp))
            for part in (add_drillholes, geo.add_trench, geo.add_lines, geo.add_terrain):
                with dialect.write_tx(engine) as conn:
                    part(conn, other, rng)
            with dialect.read_tx(engine) as conn:
                again_ids = [i for f in conn.execute(sa.select(objects.c.id).where(objects.c.kind == "line_file").order_by(objects.c.id)).scalars() for i in ls_store.layer_ids(conn, f)]
                again = [ls_store.read_layer(conn, other, i).xyz.tobytes() for i in again_ids]
                v2, t2 = wf_store.read_wireframe(conn, other, wf_store.list_wireframes(conn)[0]["id"])
                png2 = other.read_bytes(
                    conn.execute(sa.select(blob_owners.c.sha256).where(blob_owners.c.role == "source")).scalar_one()
                )
        finally:
            engine.dispose()
    assert got == again and len(got) == 3
    with dialect.read_tx(project.engine) as conn:
        v, t = wf_store.read_wireframe(conn, store, wf_store.list_wireframes(conn)[0]["id"])
        png = store.read_bytes(conn.execute(sa.select(blob_owners.c.sha256).where(blob_owners.c.role == "source")).scalar_one())
    assert v.tobytes() == v2.tobytes() and t.tobytes() == t2.tobytes()
    assert hashlib.sha256(png).hexdigest() == hashlib.sha256(png2).hexdigest()
    assert len(collars) == 48

    # the audit of the demo: the deliberate errors of the holes, nothing about the trench
    r = c.post(f"{API}/audit/sets/{set_id}/run")
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=120)
    assert job["status"] == "done", job
    res = c.get(f"{API}/audit/sets/{set_id}").json()["result"]
    found = {x["code"]: x["rows"] for x in res["summary"] if x["rows"]}
    assert set(found) >= {
        "SURVEY_BEYOND_TD", "ASSAY_NEGATIVE_VALUE", "ASSAY_OVERLAP", "ASSAY_GAP", "ASSAY_EMPTY_VALUE",
        "ASSAY_DUPLICATE_SAMPLE", "SURVEY_DOGLEG",
    }  # fmt: skip
    rows = c.get(f"{API}/audit/sets/{set_id}/findings", params={"limit": 500}).json()
    assert rows["total"] == sum(found.values()) and rows["total"] > 0
    assert not [x for x in rows["rows"] if x["hole"] == geo.TRENCH], "the trench must not add findings"
