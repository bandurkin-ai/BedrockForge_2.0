"""OMF v1 in «Импорт файлов» (ТЗ 7.24, 9.8; ADR 0017 §2; Д-21): the sample of omf-python → two wireframes in the
folder of the file, one line file with one layer, the journal of what is not carried over, a cancel at any step,
OMF 2 refused, the CRS and the DTM mark BF2 writes. Reference: the arrays of the file, read by `omf_read`."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, import_log, objects
from bf_core.jobs.context import JobCancelled, JobError
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API, wait_job
from tests.test_import_objects import run_job, state_of

DATA = Path(__file__).resolve().parents[2] / "tests" / "data" / "ext"
V1 = DATA / "omf" / "v1" / "test_file.omf"
V2 = DATA / "omf" / "v2" / "test_file.omf"
MCLAUGHLIN = DATA / "omf" / "minelib" / "McLaughlin_tutorial.omf"
EXPECTED = json.loads((DATA / "expected" / "test_file.v1.json").read_text(encoding="utf-8"))


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path / "projects", "OMF", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    yield SimpleNamespace(folder=folder, engine=engine)
    engine.dispose()


def sha_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference() -> dict[str, Any]:
    """The surfaces and the line set of the sample as the reader gives them."""
    from bf_core.formats.omf_read import open_reader

    with open_reader(V1) as r:
        return {"surfaces": {m.name: m for m in r.surfaces()}, "lines": list(r.line_layers())}


def with_descriptions(dst: Path, about: str, element: dict[str, str]) -> Path:
    """The sample with other descriptions of the project and of elements (by name): the JSON is written again in
    its place at the end of the file, the offset in the header stays."""
    raw = V1.read_bytes()
    (start,) = struct.unpack("<Q", raw[52:60])
    doc = json.loads(raw[start:].decode("utf-8"))
    for obj in doc.values():
        if isinstance(obj, dict) and obj.get("__class__") == "Project":
            obj["description"] = about
        elif isinstance(obj, dict) and obj.get("name") in element:
            obj["description"] = element[obj["name"]]
    dst.write_bytes(raw[:start] + json.dumps(doc).encode("utf-8"))
    return dst


def test_sample_loads_as_wireframes_and_a_line_file(project) -> None:  # type: ignore[no-untyped-def]
    ctx = run_job(project.folder, {"files": [str(V1)], "folder": "Импорт"})
    res = ctx.result
    assert res["wireframes"] == 2 and res["layers"] == 1 and res["failed"] == [], res
    assert res["block_models"] == 1 and res["cells"] == 3000 and len(res["block_model_ids"]) == 1
    assert not set(res["block_model_ids"]) & set(res["wireframe_ids"])
    ref = reference()
    store = BlobStore(project.folder)
    from bf_core.lines import store as ls
    from bf_core.wireframes import store as ws

    with dialect.read_tx(project.engine) as conn:
        wfs = {w["name"]: w for w in ws.list_wireframes(conn)}
        assert set(wfs) == {"trisurf", "gridsurf"}
        assert {w["folder"] for w in wfs.values()} == {"Импорт/test_file.omf"}
        for name, w in wfs.items():
            xyz, tri = ws.read_wireframe(conn, store, w["id"])
            mesh = ref["surfaces"][name]
            assert xyz.tobytes() == np.ascontiguousarray(mesh.xyz, dtype="<f8").tobytes()  # bit for bit
            assert sorted(map(tuple, tri.tolist())) == sorted(map(tuple, mesh.tri.tolist()))
            assert w["source"]["format"] == "omf" and w["dtm"] is None and w["crs"] is None
            meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == w["id"])).scalar_one()
            assert any("данные на вершинах" in note for note in meta["warnings"]), meta
        assert wfs["gridsurf"]["triangle_count"] == 300  # a grid 10 × 15 nodes: two triangles a cell
        files = [r for r in conn.execute(sa.select(objects).where(objects.c.kind == ls.LINE_FILE)).mappings()]
        assert [(f["name"], f["folder"], f["state"]) for f in files] == [("test_file.omf", "Импорт", "ready")]
        layers = (
            conn.execute(
                sa.select(objects.c.name).where(objects.c.kind == "line_layer", objects.c.parent_id == files[0]["id"])
            )
            .scalars()
            .all()
        )
        assert layers == ["Random Line"]
        line = ref["lines"][0]
        assert line.name == "Random Line" and line.point_count > 0
        log = conn.execute(sa.select(import_log).order_by(import_log.c.id)).mappings().all()
        assert [r["counts"]["kind"] for r in log] == ["lines", "block_model", "wireframe", "wireframe"]
        assert {r["sha256"] for r in log} == {sha_of(V1)} and {r["format"] for r in log} == {"omf"}
        owners = conn.execute(sa.select(blob_owners.c.owner_kind).where(blob_owners.c.sha256 == sha_of(V1))).all()
        assert len(owners) == 4  # the line file and the journal rows of the wireframes and the model own one blob
        from bf_core.blocks import store as bm

        (model,) = bm.list_block_models(conn)
        info = bm.block_model_info(conn, model["id"])
        idx, values = bm.read_cells(conn, store, model["id"])
    assert model["name"] == "vol" and model["folder"] == "Импорт" and model["cells"] == 3000
    assert info["grid"]["counts"] == [10, 15, 20] and info["source"] == {
        "format": "omf", "file": "test_file.omf", "element": "vol"
    }  # fmt: skip
    assert [f["name"] for f in info["fields"]] == ["Random Data"] and info["fields"][0]["stats"]["count"] == 3000
    assert np.array_equal(idx, np.arange(3000, dtype=np.uint64)) and np.isfinite(values["Random Data"]).all()
    text = "\n".join(ctx.lines)
    assert "Точки «Random Points»: элемент — точки не переносятся" in text  # Д-21: what is not carried over
    assert "текстуры (2)" in text and "текстуры (1)" in text and "данные на вершинах линий" in text
    assert "Система координат в файле OMF не указана" in text
    expected = {e["name"]: e for e in EXPECTED["elements"]}
    assert expected["gridsurf"]["class"] == "SurfaceElement" and expected["Random Line"]["segments"] == 50


def test_cancel_at_any_step_leaves_nothing(project) -> None:  # type: ignore[no-untyped-def]
    was = state_of(project.engine, project.folder)
    ticks = 0
    for at in range(1, 200):
        try:
            run_job(project.folder, {"files": [str(V1)]}, cancel_at=at)
        except JobCancelled:
            ticks = at
            assert state_of(project.engine, project.folder) == was, f"cancelled at tick {at}"
            continue
        break
    else:
        raise AssertionError("the job never finished")
    assert ticks >= 6, ticks  # reading, the layer, every wireframe, the block model, the source file


@pytest.mark.parametrize("path", [V2, MCLAUGHLIN], ids=["v2", "mclaughlin"])
def test_omf2_is_refused_in_russian(project, path: Path) -> None:  # type: ignore[no-untyped-def]
    was = state_of(project.engine, project.folder)
    with pytest.raises(JobError) as err:
        run_job(project.folder, {"files": [str(path)]})
    assert err.value.code == "omf2_not_supported" and "OMF 0.9" in err.value.hint and path.name in err.value.message
    assert state_of(project.engine, project.folder) == was


def test_crs_and_dtm_of_bf2_are_read_back(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = with_descriptions(
        tmp_path / "Карьер.omf",
        "Проект BF2\nСистема координат: WGS 84 / UTM zone 38N (EPSG:32638)",
        {"gridsurf": "Рельеф 2026\nBedrockForge: ЦТМ"},
    )
    ctx = run_job(project.folder, {"files": [str(path)]})
    assert ctx.result["wireframes"] == 2
    from bf_core.wireframes import store as ws

    with dialect.read_tx(project.engine) as conn:
        wfs = {w["name"]: w for w in ws.list_wireframes(conn)}
        line_crs = conn.execute(sa.select(objects.c.crs).where(objects.c.kind == "line_file")).scalar_one()
    assert wfs["gridsurf"]["dtm"] is not None and wfs["trisurf"]["dtm"] is None
    assert {"name": "Описание", "value": "Рельеф 2026"} in wfs["gridsurf"]["attrs"]
    assert "32638" in (wfs["trisurf"]["crs"] or "") and "32638" in (line_crs or "")
    assert not any("Система координат" in line for line in ctx.lines)


def test_wizard_and_job_through_the_api(pclient) -> None:  # type: ignore[no-untyped-def]
    """The composition of the wizard (JSON only) and the job in a worker process (`spawn`)."""
    pclient.post(f"{API}/projects", json={"name": "OMF"})
    seen = pclient.post(f"{API}/import/objects/inspect", json={"files": [str(V1), str(V2)]})
    assert seen.status_code == 200, seen.text
    ok, bad = seen.json()["files"]
    assert ok["kind"] == "omf" and ok["totals"]["wireframes"] == 2 and ok["totals"]["layers"] == 1
    assert ok["totals"]["block_models"] == 1 and ok["totals"]["cells"] == 3000
    assert ok["block_models"] == [{"name": "vol", "counts": [10, 15, 20], "cells": 3000, "fields": ["Random Data"]}]
    assert [e["name"] for e in ok["omf"]["elements"]] == [e["name"] for e in EXPECTED["elements"]]
    assert any(x["what"] == "текстуры" and x["count"] == 2 for x in ok["left_out"])
    assert bad["error"]["code"] == "omf2_not_supported" and bad["error"]["hint"]
    r = pclient.post(f"{API}/import/objects", json={"files": [str(V1)]})
    assert r.status_code == 200, r.text
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and job["result"]["wireframes"] == 2 and job["result"]["block_models"] == 1, job
    tree = pclient.get(f"{API}/wireframes").json()["wireframes"]
    assert sorted((w["name"], w["folder"]) for w in tree) == [
        ("gridsurf", "test_file.omf"), ("trisurf", "test_file.omf"),
    ]  # fmt: skip
    files = pclient.get(f"{API}/lines/files").json()["files"]
    assert [(f["name"], [layer["name"] for layer in f["layers"]]) for f in files] == [
        ("test_file.omf", ["Random Line"])
    ]
    models = pclient.get(f"{API}/blocks").json()["block_models"]
    assert [(b["name"], b["cells"], b["grid"]["counts"]) for b in models] == [("vol", 3000, [10, 15, 20])]
