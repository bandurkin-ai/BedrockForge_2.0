"""Preconditions of the operations (ТЗ 7.23; ADR 0015 §6, `ops/registry.py`): an unknown operation, the kinds
of the objects, closedness by `wireframes.stats` (a boolean with an open wireframe → 422 naming it, its open
edges and «Исправить каркас»), lines by their stable numbers, the ranges of the parameters, the queue; and a
`.bfggis` cycle renumbering what a result was built from (`ops/provenance.py`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.ops import provenance, registry
from bf_core.ops.schema import OPERATIONS, listing
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.opsutil import API, make_project
from tests.test_archive import export
from tests.test_import_job import wait_job


@pytest.fixture()
def proj(pclient):  # type: ignore[no-untyped-def]
    project, ids = make_project(pclient)
    return pclient, project, ids


def check(project: Any, op: Any, params: Any, **kw: Any) -> registry.Checked:
    with dialect.read_tx(project.engine) as conn:
        return registry.precheck(conn, BlobStore(project.folder), op, params, **kw)


def refusal(project: Any, op: Any, params: Any, code: str, **kw: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        check(project, op, params, **kw)
    assert e.value.status == 422 and e.value.code == code, (e.value.code, e.value.message)
    assert e.value.message and e.value.hint
    return e.value


def test_unknown_operation_and_bad_params(proj) -> None:  # type: ignore[no-untyped-def]
    _c, project, ids = proj
    e = refusal(project, "shell", {}, "unknown_operation")
    assert "shell" in e.message and "stitch" in e.extra["operations"]
    refusal(project, None, {}, "unknown_operation")
    refusal(project, "repair", [1, 2], "bad_params")
    refusal(project, "smooth", {"wireframe_id": ids["a"], "strength": "сильно"}, "not_a_number")
    refusal(project, "smooth", {"wireframe_id": ids["a"], "strength": 1.5}, "bad_param")
    refusal(project, "smooth", {"wireframe_id": ids["a"], "iterations": 0}, "bad_param")
    refusal(project, "simplify", {"wireframe_id": ids["a"]}, "param_missing")
    refusal(project, "simplify", {"wireframe_id": ids["a"], "target_triangles": 40}, "bad_param")  # 12 triangles
    refusal(project, "clip_plane", {"wireframe_id": ids["a"], "point": [0, 0, 5], "normal": [0, 0, 0]}, "bad_vector")
    refusal(project, "clip_plane", {"wireframe_id": ids["a"], "point": [0, 0, 5], "normal": "0;0;1", "keep": "up"},
            "bad_choice")  # fmt: skip
    refusal(project, "contours", {"wireframe_id": ids["relief"], "interval": 1e-6}, "too_many_levels")
    refusal(project, "merge", {"wireframe_ids": [ids["a"]]}, "too_few_sources")
    refusal(project, "merge", {"wireframe_ids": [ids["a"], ids["a"]]}, "same_source")
    refusal(project, "edit_triangles", {"wireframe_id": ids["a"]}, "empty_edit")
    refusal(project, "edit_triangles", {"wireframe_id": ids["a"], "remove": [12]}, "stale_triangles")
    # numbers come as text with a comma, as the windows send them
    c = check(project, "clip_plane", {"wireframe_id": ids["a"], "point": ["1,5", "0", "5"], "normal": "0; 0; 1"})
    assert c.params == {"wireframe_id": ids["a"], "point": [1.5, 0.0, 5.0], "normal": [0.0, 0.0, 1.0], "keep": "both"}
    c = check(project, "repair", {"wireframe_id": ids["open"], "weld": "0,001"})
    assert c.params["weld"] == 0.001 and c.params["fill_holes"] is True


def test_kinds_of_the_objects(proj) -> None:  # type: ignore[no-untyped-def]
    _c, project, ids = proj
    e = refusal(project, "repair", {"wireframe_id": ids["contours"]}, "wrong_kind")
    assert "«Контуры» — слой линий" in e.message
    refusal(project, "stitch", {"contours": [{"layer_id": ids["a"], "lines": None}]}, "wrong_kind")
    refusal(project, "stitch", {"contours": [{"layer_id": ids["file"], "lines": None}]}, "wrong_kind")
    e = refusal(project, "repair", {"wireframe_id": 99_999}, "source_missing")
    assert "№ 99999" in e.message
    refusal(project, "repair", {"wireframe_id": None}, "source_missing")
    refusal(project, "repair", {"wireframe_id": True}, "bad_ref")
    # a rebuild names the source by the name in the record of the result
    e = refusal(project, "repair", {"wireframe_id": None}, "source_missing", known={"wireframe": "Старое тело"})
    assert "«Старое тело»" in e.message and e.extra["role"] == "wireframe"
    e = refusal(project, "clip_surface", {"wireframe_id": ids["a"], "surface_id": ids["b"]}, "wireframe_closed")
    assert "«Куб B»" in e.message
    refusal(project, "clip_surface", {"wireframe_id": ids["a"], "surface_id": ids["a"]}, "same_source")
    refusal(project, "volume_between", {"top": ids["relief"], "bottom": ids["relief"]}, "same_source")
    refusal(project, "volume_between", {"top": ids["relief"], "mode": "level"}, "param_missing")
    refusal(project, "intersection_lines", {"a": ids["a"], "b": ids["a"]}, "same_source")
    with dialect.write_tx(project.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == ids["b"]).values(state="building"))
    refusal(project, "boolean", {"op": "union", "a": ids["a"], "b": ids["b"]}, "source_not_ready")


def test_boolean_with_an_open_wireframe_is_refused(proj) -> None:  # type: ignore[no-untyped-def]
    """The acceptance of 7.23: the message names the wireframe and its open edges; the window gets the button."""
    _c, project, ids = proj
    e = refusal(project, "boolean", {"op": "difference", "a": ids["a"], "b": ids["open"]}, "wireframe_open")
    assert e.message == "Каркас «Тело 2» не замкнут: открытых рёбер — 4."
    assert e.hint == "Запустите «Исправить каркас» и повторите."
    assert e.extra["open_edges"] == 4 and e.extra["fix"] == {"op": "repair", "wireframe_id": ids["open"]}
    refusal(project, "boolean", {"op": "union", "a": ids["open"], "b": [ids["a"], ids["b"]]}, "wireframe_open")
    c = check(project, "boolean", {"op": "difference", "a": ids["a"], "b": ids["b"]})
    assert c.params == {"op": "difference", "a": ids["a"], "b": ids["b"]}
    assert [(u["role"], u["id"], u["name"], u["version"]) for u in c.sources] == [
        ("a", ids["a"], "Куб A", 1), ("b", ids["b"], "Куб B", 1),
    ]  # fmt: skip
    assert c.triangles == 24 and c.queue_job == registry.JOB and c.folder is None
    many = check(project, "boolean", {"op": "union", "a": ids["a"], "b": [ids["b"]]})
    assert many.params["b"] == [ids["b"]] and [u["role"] for u in many.sources] == ["a", "b[0]"]
    # the size of the input picks the queue (decision 1)
    assert registry.memory_mb({"op": "boolean", "size": 2_000_000}) < 2048
    c.triangles = registry.QUICK_TRIANGLES + 1
    assert c.queue_job == registry.HEAVY_JOB


def test_lines_by_their_stable_numbers(proj) -> None:  # type: ignore[no-untyped-def]
    _c, project, ids = proj
    layer = ids["contours"]
    c = check(project, "stitch", {"contours": [{"layer_id": layer, "lines": None}], "end_cap": "point"})
    assert c.params == {"contours": [{"layer_id": layer, "lines": None}], "start_cap": "flat", "end_cap": "point",
                        "cap_distance": 0.5}  # fmt: skip
    assert c.sources == [{"role": "contours[0]", "kind": "line_layer", "id": layer, "name": "Контуры", "version": 1,
                          "lines": None}]  # fmt: skip
    assert c.vertices == 4 + 1 + 4 + 1 + 2  # closed lines are stored with their first vertex repeated
    e = refusal(project, "stitch", {"contours": [{"layer_id": layer, "lines": [0, 7]}]}, "line_missing")
    assert "линии 8" in e.message and e.extra["lines"] == [7]
    e = refusal(project, "stitch", {"contours": [{"layer_id": layer, "lines": [0, 2]}]}, "too_few_contours")
    assert "выбрано 1" in e.message
    refusal(project, "stitch", {"contours": []}, "no_contours")
    refusal(project, "stitch", {"contours": [{"layer_id": layer, "lines": []}]}, "no_lines")
    refusal(project, "stitch", {"contours": [{"layer_id": layer, "lines": [-1]}]}, "bad_lines")
    refusal(project, "stitch", {"contours": [{"layer_id": layer}], "start_cap": "dome"}, "bad_choice")
    refusal(project, "extrude", {"contours": [{"layer_id": layer, "lines": [2]}], "distance": 5}, "no_polygon")
    refusal(project, "extrude", {"contours": [{"layer_id": layer, "lines": [0]}], "distance": 0}, "bad_param")
    refusal(project, "extrude", {"contours": [{"layer_id": layer, "lines": [0]}], "distance": 5,
                                 "direction": "vector"}, "param_missing")  # fmt: skip
    # a polygon: one closed line — the only one of the layer, or picked by its number
    c = check(project, "clip_polygon", {"wireframe_id": ids["a"], "polygon": {"layer_id": ids["polygons"]}})
    assert c.params["polygon"] == {"layer_id": ids["polygons"], "line": 0} and c.params["keep"] == "inside"
    e = refusal(project, "clip_polygon", {"wireframe_id": ids["a"], "polygon": {"layer_id": layer}}, "bad_polygon")
    assert "замкнутых линий 2" in e.message
    refusal(project, "clip_polygon", {"wireframe_id": ids["a"], "polygon": {"layer_id": layer, "line": 2}},
            "bad_polygon")  # fmt: skip
    c = check(project, "clip_polygon", {"wireframe_id": ids["a"], "polygon": {"layer_id": layer, "line": 1}})
    assert c.params["polygon"] == {"layer_id": layer, "line": 1}
    assert c.folder is None  # the folder of the first source: the cube is at the root of the tree


def test_dtm_is_the_job_model_dtm(proj) -> None:  # type: ignore[no-untyped-def]
    _c, project, ids = proj
    c = check(project, "dtm", {"layers": [ids["contours"]], "boundary": {"layer_id": ids["polygons"]},
                               "max_edge": "50"}, name="Поверхность")  # fmt: skip
    assert c.queue_job == "model.dtm" and c.job_params["layer_ids"] == [ids["contours"]]
    assert c.job_params["boundary"] == {"layer_id": ids["polygons"], "line": 0, "use_z": False}
    assert c.job_params["max_edge"] == 50.0 and c.job_params["name"] == "Поверхность"
    refusal(project, "dtm", {}, "no_sources")


def test_the_listing_for_the_windows() -> None:
    ops = listing()
    assert [o["op"] for o in ops] == list(OPERATIONS) and len(ops) == 17
    # 13 windows `model.*` (ADR 0015 §6) and «Каркас по серии» (ADR 0016 §8)
    assert len({o["dialog"] for o in ops if o["dialog"]}) == 14
    for o in ops:
        assert o["title"] and o["result"] in ("mesh", "lines", "report") and o["fields"], o
        for f in o["fields"]:
            assert {"key", "label", "type"} <= set(f) and f["label"], (o["op"], f)
        if o["op"] != "dtm":
            assert o["job"] == "geometry.operation" and set(registry.PREPARE) >= {o["op"]}


def test_remap_built_renumbers_every_id_of_a_record() -> None:
    built = provenance.record(
        op="boolean", title="Булева операция", params={"op": "union", "a": 3, "b": [4, 5]},
        sources=[{"role": "a", "kind": "wireframe", "id": 3, "name": "A"},
                 {"role": "b[0]", "kind": "wireframe", "id": 4, "name": "B"},
                 {"role": "b[1]", "kind": "wireframe", "id": 5, "name": "C"}],
        report={"volume": 1.5}, rebuild_of=9,
    )  # fmt: skip
    new = {3: 30, 4: 40, 9: 90}.get
    out = provenance.remap_built(built, new)
    assert out["params"] == {"op": "union", "a": 30, "b": [40, None]}
    assert [s["id"] for s in out["sources"]] == [30, 40, None] and out["rebuild_of"] == 90
    assert {k: v for k, v in out.items() if k not in ("params", "sources", "rebuild_of")} == {
        k: v for k, v in built.items() if k not in ("params", "sources", "rebuild_of")
    }
    lines = {"params": {"contours": [{"layer_id": 3, "lines": [0, 5]}], "polygon": {"layer_id": 4, "line": 2}}}
    assert provenance.remap_built(lines, new)["params"] == {
        "contours": [{"layer_id": 30, "lines": [0, 5]}], "polygon": {"layer_id": 40, "line": 2},
    }  # fmt: skip


def test_bfggis_renumbers_the_sources_of_results(proj, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """`meta.built` of a wireframe and of a line layer made by operations: in the copy every id names the
    object the source project meant; a source deleted before the export keeps its name, loses its id."""
    c, project, ids = proj
    solid = provenance.record(
        op="stitch", title="Сшить контуры", params={"contours": [{"layer_id": ids["contours"], "lines": [0, 1]}]},
        sources=[{"role": "contours[0]", "kind": "line_layer", "id": ids["contours"], "name": "Контуры",
                  "version": 1, "lines": [0, 1]}], report={}, rebuild_of=ids["a"],
    )  # fmt: skip
    lines = provenance.record(
        op="intersection_lines", title="Линии пересечения", params={"a": ids["relief"], "b": ids["b"]},
        sources=[{"role": "a", "kind": "wireframe", "id": ids["relief"], "name": "Рельеф"},
                 {"role": "b", "kind": "wireframe", "id": ids["b"], "name": "Куб B"}], report={},
    )  # fmt: skip
    with dialect.write_tx(project.engine) as conn:
        for oid, built in ((ids["open"], solid), (ids["polygons"], lines)):
            meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == oid)).scalar() or {}
            conn.execute(sa.update(objects).where(objects.c.id == oid).values(meta={**meta, "built": built}))
    assert c.delete(f"{API}/wireframes/{ids['b']}").status_code == 200  # a source gone before the export
    job = export(c, tmp_path / "операции.bfggis")
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(c, r["source"], r["id"])
    assert job["status"] == "done", job
    new = {int(k): v for k, v in job["result"]["id_maps"]["objects"].items()}
    assert c.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    copy = projects_of(c.core).require_current()
    with dialect.read_tx(copy.engine) as conn:
        meta = {int(r.id): r.meta for r in conn.execute(sa.select(objects.c.id, objects.c.meta))}
    got = meta[new[ids["open"]]]["built"]
    assert got["params"]["contours"] == [{"layer_id": new[ids["contours"]], "lines": [0, 1]}]
    assert got["sources"][0]["id"] == new[ids["contours"]] and got["rebuild_of"] == new[ids["a"]]
    got = meta[new[ids["polygons"]]]["built"]
    assert got["params"] == {"a": new[ids["relief"]], "b": None}
    assert [(s["id"], s["name"]) for s in got["sources"]] == [(new[ids["relief"]], "Рельеф"), (None, "Куб B")]
    assert new[ids["relief"]] != ids["relief"]  # the ids moved: a number kept would name another object
