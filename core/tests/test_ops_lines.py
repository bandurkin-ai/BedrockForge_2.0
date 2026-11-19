"""Operations with lines and surfaces (ТЗ 7.23; ADR 0015 §6–7, `ops/run_lines.py`): contours and tie lines
(`ROLE = TIE`, or two vertices on contour vertices) from a layer → a closed solid of volume 1000 (in a real
worker); extrusion; isolines — two layers with `ELEV`, a line file the editor opens; lines where two
wireframes cross; volumes between surfaces and to a level — the report and the bodies «Выемка», «Насыпь»;
`dtm` → the job `model.dtm`."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

import bf_core.ops.job as ops_job
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.formats.lineset import from_lines
from bf_core.jobs.context import JobError
from bf_core.jobs.service import jobs_of
from bf_core.lines import store as ls_store
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from bf_core.wireframes import store as ws
from tests.opsutil import API, make_project, ok, square
from tests.solidutil import OFFSET, grid
from tests.test_import_job import wait_job
from tests.test_ops_job import checked, numba_cache, ops_env, run_here  # noqa: F401 - fixtures

REL = 1e-9


@pytest.fixture()
def oc(ops_env, pclient):  # type: ignore[no-untyped-def]  # noqa: F811 - the fixture of test_ops_job
    project, ids = make_project(pclient)
    return pclient, project, ids


def add_layer(project: Any, file_id: int, name: str, lines: list[Any], closed: list[bool], **kw: Any) -> int:
    with dialect.write_tx(project.engine) as conn:
        layer = from_lines(name, lines, closed=closed, **kw)
        return ls_store.put_layer(conn, BlobStore(project.folder), layer, file_id=file_id)


def add_surface(project: Any, name: str, z: Any) -> int:
    with dialect.write_tx(project.engine) as conn:
        return ws.put_wireframe(conn, BlobStore(project.folder), *grid(6, 6, (-5, 25), (-5, 25), z), name=name)


def mesh(project: Any, wf_id: int) -> tuple[np.ndarray, np.ndarray]:
    with dialect.read_tx(project.engine) as conn:
        return ws.read_wireframe(conn, BlobStore(project.folder), wf_id)


def test_stitch_from_a_layer_in_a_worker(oc) -> None:  # type: ignore[no-untyped-def]
    """The acceptance of 7.23: two contours 10 × 10 ten metres apart, flat caps → a closed solid of 1000; the
    tie line marked ROLE = TIE is an edge of the result; the vertices of the contours are theirs, bit for bit."""
    c, project, ids = oc
    ch = checked(project, "stitch", {"contours": [{"layer_id": ids["contours"], "lines": None}]})
    key, jid = ops_job.submit(jobs_of(c.core), ch, name="Рудное тело")
    job = wait_job(c, key, jid, timeout=180)
    assert job["status"] == "done", job
    (wf_id,) = job["result"]["wireframe_ids"]
    w = ok(c.get(f"{API}/wireframes/{wf_id}"))
    assert w["name"] == "Рудное тело" and w["stats"]["closed"] and w["stats"]["orientation"] == "outward"
    assert abs(w["stats"]["volume"] - 1000.0) <= REL * 1000.0
    assert w["built"]["op"] == "stitch" and w["built"]["report"]["ties"] == 1
    assert w["built"]["sources"][0]["lines"] is None and w["built"]["params"]["start_cap"] == "flat"
    v, t = mesh(project, wf_id)
    rows = [r.tobytes() for r in v]
    for p in (*square(0.0), *square(10.0)):
        assert p.tobytes() in rows
    a, b = rows.index(square(0.0)[0].tobytes()), rows.index(square(10.0)[0].tobytes())
    edges = {tuple(sorted(e)) for tri in t.tolist() for e in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0]))}
    assert tuple(sorted((a, b))) in edges


def test_tie_lines_and_refusals(oc) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = oc
    unmarked = np.array([square(0.0)[2], square(10.0)[2]])
    stray = np.array([square(0.0)[0] + [50.0, 0.0, 0.0], square(0.0)[1] + [50.0, 0.0, 0.0]])
    layer = add_layer(project, ids["file"], "Разрезы", [square(0.0), square(10.0), unmarked, stray],
                      [True, True, False, False])  # fmt: skip
    out, ctx = run_here(project, "stitch", {"contours": [{"layer_id": layer, "lines": None}], "end_cap": "point"})
    assert out["report"]["ties"] == 1 and out["report"]["contours"] == 2
    assert any("пропущено: 1" in text for _level, text in ctx.logs)
    w = ok(c.get(f"{API}/wireframes/{out['wireframe_ids'][0]}"))
    assert w["stats"]["closed"] and w["stats"]["volume"] > 1000.0  # the point cap adds a cone
    # lines picked by their numbers: the contours only, no tie
    out, _ = run_here(project, "stitch", {"contours": [{"layer_id": layer, "lines": [0, 1]}]})
    assert out["report"]["ties"] == 0
    hole = square(10.0)
    hole[2, 2] = np.nan
    gap = add_layer(project, ids["file"], "Без отметки", [square(0.0), hole], [True, True])
    with pytest.raises(JobError) as e:
        run_here(project, "stitch", {"contours": [{"layer_id": gap, "lines": None}]})
    assert e.value.code == "empty_z" and "линия 2 слоя «Без отметки»" in e.value.message


def test_extrude_a_polygon(oc) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = oc
    out, _ = run_here(project, "extrude", {"contours": [{"layer_id": ids["polygons"], "lines": [0]}],
                                           "distance": "5", "direction": "vertical"})  # fmt: skip
    w = ok(c.get(f"{API}/wireframes/{out['wireframe_ids'][0]}"))
    assert w["name"] == "Выдавливание: Полигоны" and w["stats"]["closed"]
    assert w["stats"]["volume"] == pytest.approx(16.0 * 5.0, rel=REL)
    out, _ = run_here(project, "extrude", {"contours": [{"layer_id": ids["polygons"]}], "distance": 2,
                                           "vector": [1, 0, 1], "both_sides": True})  # fmt: skip
    w = ok(c.get(f"{API}/wireframes/{out['wireframe_ids'][0]}"))
    assert w["stats"]["volume"] == pytest.approx(16.0 * 2.0 / math.sqrt(2.0), rel=REL)  # half each way
    assert w["built"]["params"]["direction"] == "vector"


def test_isolines_make_two_layers_the_editor_opens(oc) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = oc
    out, _ = run_here(project, "contours", {"wireframe_id": ids["relief"], "interval": 1, "major_every": 5})
    assert out["report"]["levels"] == 4 and out["file_id"] and len(out["layer_ids"]) == 2
    with dialect.read_tx(project.engine) as conn:
        rows = {int(r.id): r for r in conn.execute(sa.select(objects).where(objects.c.id.in_(
            [out["file_id"], *out["layer_ids"]])))}  # fmt: skip
        layers = [ls_store.read_layer(conn, BlobStore(project.folder), i) for i in out["layer_ids"]]
    assert rows[out["file_id"]].name == "Изолинии: Рельеф" and rows[out["file_id"]].meta["format"] == "operation"
    assert [ls.name for ls in layers] == ["Основные", "Дополнительные"]
    elev = [sorted(set(ls.attrs.column("ELEV").to_pylist())) for ls in layers]
    assert elev == [[OFFSET[2] + 5.0], [OFFSET[2] + 6.0, OFFSET[2] + 7.0, OFFSET[2] + 8.0]]
    for i in out["layer_ids"]:
        assert rows[i].meta["built"]["op"] == "contours" and rows[i].meta["built"]["sources"][0]["id"] == ids["relief"]
        r = c.get(f"{API}/lines/layers/{i}/geometry")  # the editor reads it
        assert r.status_code == 200, r.text
        header, arrays = binary.decode(r.content)
        assert header["lines"] == len(arrays["closed"]) > 0
        (row,) = ok(c.get(f"{API}/lines/layers/{i}/versions"))["versions"]
        assert row["action"] == "operation"


def test_lines_where_two_wireframes_cross(oc) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = oc
    out, _ = run_here(project, "intersection_lines", {"a": ids["a"], "b": ids["relief"]})
    (layer_id,) = out["layer_ids"]
    with dialect.read_tx(project.engine) as conn:
        ls = ls_store.read_layer(conn, BlobStore(project.folder), layer_id)
    assert ls.name == "Пересечение: Куб A × Рельеф" and ls.line_count == 1 and ls.closed_count == 1
    # the plane z = 5 + 0.1 x + 0.05 y across the cube 10 × 10: two edges along X, two along Y
    assert float(ls.lengths()[0]) == pytest.approx(2 * math.sqrt(101.0) + 2 * math.sqrt(100.25), rel=REL)
    with pytest.raises(JobError) as e:
        run_here(
            project, "intersection_lines", {"a": ids["a"], "b": add_surface(project, "Выше", lambda x, y: 0 * x + 50)}
        )
    assert e.value.code == "no_crossing"


def test_volumes_report_and_bodies(oc) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = oc
    level = OFFSET[2] + 4.0  # the relief 5 + 0.1 x + 0.05 y over 30 × 30, its mean height above the level 2.5
    out, _ = run_here(project, "volume_between", {"top": ids["relief"], "level": level})
    assert out["report"]["cut"] == pytest.approx(900.0 * 2.5, rel=REL) and out["report"]["fill"] == pytest.approx(0.0)
    (body,) = out["wireframe_ids"]
    w = ok(c.get(f"{API}/wireframes/{body}"))
    assert w["name"].startswith("Выемка: «Рельеф» – уровень") and w["stats"]["closed"]
    assert (
        w["stats"]["volume"] == pytest.approx(2250.0, rel=REL) and w["built"]["report"]["cut"] == out["report"]["cut"]
    )
    flat = add_surface(project, "Подошва", lambda x, y: 0 * x + 6.0)
    out, _ = run_here(project, "volume_between", {"top": ids["relief"], "bottom": flat})
    r = out["report"]
    assert r["net"] == pytest.approx(r["cut"] - r["fill"], rel=REL) and r["net"] == pytest.approx(450.0, rel=REL)
    vols = sorted(ok(c.get(f"{API}/wireframes/{i}"))["stats"]["volume"] for i in out["wireframe_ids"])
    assert vols == pytest.approx(sorted([r["cut"], r["fill"]]), rel=1e-9)
    before = len(ok(c.get(f"{API}/wireframes"))["wireframes"])
    out, _ = run_here(project, "volume_between", {"top": ids["relief"], "bottom": flat, "report_only": True,
                                                  "polygon": {"layer_id": ids["polygons"]}})  # fmt: skip
    assert out["wireframe_ids"] == [] and out["report"]["area"] == pytest.approx(16.0, rel=REL)
    assert len(ok(c.get(f"{API}/wireframes"))["wireframes"]) == before


def test_dtm_runs_as_the_job_model_dtm(oc) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = oc
    ch = checked(project, "dtm", {"layers": [ids["polygons"]]})
    key, jid = ops_job.submit(jobs_of(c.core), ch)
    job = wait_job(c, key, jid, timeout=180)
    assert job["status"] == "done" and job["kind"] == "model.dtm", job
    w = ok(c.get(f"{API}/wireframes/{job['result']['wireframe_id']}"))
    assert w["built"]["kind"] == "dtm" and w["triangle_count"] == 2
