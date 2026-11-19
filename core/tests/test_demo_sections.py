"""Demo part of Э10 (demo/sections.py; ТЗ 7.5, 8.1; ADR 0016 §1, §7–§8, решение 2; 10.25): the series of the
contours of «Рудное тело 3» with the attributes `SECTION_*` of its contours, the series of plans of «Рельеф»; the
criterion — «Каркас по серии» on the demo series is a closed solid."""

from __future__ import annotations

import math

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.demo import contours as dc
from bf_core.demo import sections as ds
from bf_core.geo.checks import mesh_checks
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.sections import store as ss
from bf_core.sections.geometry import series_geometry
from bf_core.sections.names import section_names
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as wf_store
from tests.test_import_job import API
from tests.test_ops_job import numba_cache, ops_env, run_here  # noqa: F401 - fixtures
from tests.test_ops_series import series_ops  # noqa: F401 - fixture

pytestmark = pytest.mark.windows


@pytest.fixture()
def demo(series_ops, pclient):  # type: ignore[no-untyped-def]  # noqa: F811
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    return pclient, project, BlobStore(project.folder)


def _layer(c, file_name: str, layer_name: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    files = [f for f in c.get(f"{API}/lines/files").json()["files"] if f["name"] == file_name]
    assert len(files) == 1, file_name
    return next(x for x in files[0]["layers"] if x["name"] == layer_name)


def _lines(layer) -> tuple[np.ndarray, np.ndarray]:  # type: ignore[no-untyped-def]
    xyz = np.vstack([np.asarray(layer.line(i), dtype=np.float64) for i in range(layer.line_count)])
    offsets = np.concatenate([[0], np.cumsum([len(layer.line(i)) for i in range(layer.line_count)])])
    return xyz, offsets


def test_series_of_the_ore_body_and_its_contours(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, store = demo
    info = _layer(c, dc.CONTOURS_FILE, dc.CONTOURS)
    with dialect.read_tx(project.engine) as conn:
        series = {s["name"]: s for s in ss.list_series(conn)}
        layer = ls_store.read_layer(conn, store, info["id"])
        meta = dict(conn.execute(sa.select(objects.c.meta).where(objects.c.id == series[ds.ORE_SERIES]["id"]))
                    .scalar_one())  # fmt: skip
    assert list(series) == [ds.ORE_SERIES, ds.PLANS]
    ore = series[ds.ORE_SERIES]
    assert (ore["number"], ore["count"], ore["version"]) == (1, 6, 1)
    assert ore["params"]["type"] == "vertical" and ore["params"]["contour_layer"] == info["id"]
    names = section_names(meta)
    assert names == [f"{y:.0f}N" for y in dc.SECTIONS]
    listed = c.get(f"{API}/section-series").json()
    assert [s["name"] for s in listed["series"]] == [ds.ORE_SERIES, ds.PLANS]

    # every contour lies in the window of its section, by the rule of the core; the attributes say the same
    geom = series_geometry(meta)
    xyz, offsets = _lines(layer)
    on = geom.line_sections(xyz, offsets).tolist()
    sets = layer.attrs.column(ds.SET).to_pylist()
    ks = layer.attrs.column(ds.SECTION).to_pylist()
    labels = layer.attrs.column(ds.SECTION_NAME).to_pylist()
    roles = layer.attrs.column(dc.ROLE).to_pylist()
    per_section = [0] * geom.count
    for i in range(layer.line_count):
        if layer.closed[i]:
            k = on[i]
            assert k >= 0 and (sets[i], ks[i], labels[i]) == (1.0, k + 1.0, names[k])
            assert np.all(geom.local(xyz[offsets[i] : offsets[i + 1]], k)[:, 2] == 0.0)  # on the plane exactly
            per_section[k] += 1
        else:  # a tie: on no section, the attributes empty
            assert roles[i] == dc.TIE and math.isnan(sets[i]) and math.isnan(ks[i]) and labels[i] is None
    assert per_section == [1, 1, 1, 2, 2, 2]  # no section empty; the branching 1 : 2


def test_plans_of_the_relief(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, store = demo
    file = next(f for f in c.get(f"{API}/lines/files").json()["files"] if f["name"] == dc.ISOLINES_FILE)
    with dialect.read_tx(project.engine) as conn:
        plans = next(s for s in ss.list_series(conn) if s["name"] == ds.PLANS)
        meta = dict(conn.execute(sa.select(objects.c.meta).where(objects.c.id == plans["id"])).scalar_one())
        layers = [ls_store.read_layer(conn, store, x["id"]) for x in file["layers"]]
    assert plans["number"] == 2 and plans["params"]["type"] == "plan" and plans["params"]["contour_layer"] is None
    geom = series_geometry(meta)
    levels = sorted({float(e) for layer in layers for e in layer.attrs.column("ELEV").to_pylist()}, reverse=True)
    assert geom.count == len(levels) >= 5  # the relief rises some 20 m: a plan on every level of the isolines
    assert section_names(meta) == [f"{z:.0f}" for z in levels]
    np.testing.assert_array_equal(geom.origins()[:, 2], levels)  # from the top down, every 2 m
    seen: set[int] = set()
    for layer in layers:
        xyz, offsets = _lines(layer)
        on = geom.line_sections(xyz, offsets).tolist()
        assert all(k >= 0 for k in on)  # every isoline lies in the window of its level, on its plane
        assert [levels[k] for k in on] == layer.attrs.column("ELEV").to_pylist()
        seen.update(on)
    assert seen == set(range(geom.count))  # no plan empty


def test_stitch_series_of_the_demo_is_a_closed_body(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, store = demo
    with dialect.read_tx(project.engine) as conn:
        ore = next(s for s in ss.list_series(conn) if s["name"] == ds.ORE_SERIES)
    out, _ctx = run_here(project, "stitch_series", {"series_id": ore["id"]})
    rep = out["report"]
    assert rep["contours"] == 9 and rep["skipped"] == {"open": 4, "short": 0, "outside": 0}  # the ties are open
    assert rep["auto_links"] and rep["links"] == 8 and rep["closed"]
    (wf_id,) = out["wireframe_ids"]  # trunk and both branches: one connected body
    w = c.get(f"{API}/wireframes/{wf_id}").json()
    assert w["name"] == f"{ds.ORE_SERIES} — тело 1"
    assert w["built"]["op"] == "stitch_series"
    s = w["stats"]
    assert s["closed"] and s["orientation"] == "outward" and s["open_edges"] == 0 and s["nonmanifold_edges"] == 0
    with dialect.read_tx(project.engine) as conn:
        v, t = wf_store.read_wireframe(conn, store, wf_id)
    found = mesh_checks(v, t)
    assert found["self_intersections"] == 0 and found["orientation_conflicts"] == 0
    assert 5e5 < s["volume"] < 2e6  # caps extend by Δ/2: more than the 0.78 million m³ of the body of Э9
