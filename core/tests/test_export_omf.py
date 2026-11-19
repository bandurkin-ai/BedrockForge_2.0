"""Export to OMF v1 (ТЗ 7.24 «выгруженный OMF загружается обратно»; ADR 0017 §4; 10.5 п. 3): the wireframes, DTMs,
line files and a block model of the demo → `export.omf` in a worker process → «Импорт файлов» into a new project:
the same objects, coordinates bit for bit; real TRIDB and STR files → OMF → back bit for bit; a cancel leaves no
file; what is not offered says why."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.blocks import store as bm
from bf_core.db import dialect
from bf_core.db.schema import export_log, objects
from bf_core.exporting import omf_job
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API, wait_job
from tests.test_import_objects import run_job

REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real"
GRID = {"corner": [512_000.125, 5_912_000.5, 100.0], "counts": [20, 10, 5], "size": [5.0, 5.0, 2.5]}
FIELDS = [{"name": "Au", "kind": "number", "unit": "g/t"}, {"name": "Порода", "kind": "category",
                                                           "labels": ["руда", "порода"]}]  # fmt: skip


@pytest.fixture()
def demo(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from bf_core.exporting import omf_router
    from bf_core.jobs.registry import HANDLER_MODULES

    if "bf_core.exporting.omf_job" not in HANDLER_MODULES:  # until the line of the orchestrator (11.7)
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.exporting.omf_job")
    if not any(getattr(r, "path", "") == f"{API}/omf/exportable" for r in pclient.app.routes):
        pclient.app.include_router(omf_router.router, prefix=API)
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    return pclient, projects_of(pclient.core).require_current()


@pytest.fixture()
def target(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A second project to load the file back into."""
    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path / "projects", "Обратно", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    yield SimpleNamespace(folder=folder, engine=engine)
    engine.dispose()


def model_cells(n: int = 600) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    rng = np.random.default_rng(3)
    idx = np.sort(rng.choice(1000, size=n, replace=False)).astype(np.uint64)
    au = rng.lognormal(0.0, 1.0, n)
    au[::7] = np.nan
    rock = rng.integers(-1, 2, n).astype(np.int32)
    rock[::7] = 0  # a cell with no Au still has a rock: it stays occupied
    return idx, {"Au": au, "Порода": rock}


def wireframes_of(engine: sa.Engine, store: BlobStore) -> dict[str, tuple[bytes, list[Any], Any, Any]]:
    """name → (vertices, triangles as a sorted list, colour, is a DTM)."""
    from bf_core.wireframes import store as ws

    out = {}
    with dialect.read_tx(engine) as conn:
        for w in ws.list_wireframes(conn):
            xyz, tri = ws.read_wireframe(conn, store, w["id"])
            out[w["name"]] = (xyz.tobytes(), sorted(map(tuple, tri.tolist())), w["color"], w["dtm"] is not None)
    return out


def layers_of(engine: sa.Engine, store: BlobStore) -> dict[str, Any]:
    from bf_core.lines import store as ls

    with dialect.read_tx(engine) as conn:
        ids = conn.execute(sa.select(objects.c.id).where(objects.c.kind == "line_layer")).scalars().all()
        return {layer.name: layer for layer in (ls.read_layer(conn, store, i) for i in ids)}


def test_demo_and_a_model_go_to_omf_and_back(demo, target, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, project = demo
    store = BlobStore(project.folder)
    idx, values = model_cells()
    with dialect.write_tx(project.engine) as conn:
        bm.put_block_model(conn, store, GRID, FIELDS, idx, values, name="Модель 2026")
    offered = c.get(f"{API}/omf/exportable").json()
    assert offered["wireframes"] and offered["line_files"] and offered["block_models"]
    pick = {
        "wireframe_ids": [w["id"] for w in offered["wireframes"]],
        "line_file_ids": [f["id"] for f in offered["line_files"] if f["available"]],
        # «Модель 2026» only: the demo has a model of its own (demo/blocks.py, 11.19)
        "block_model_ids": [b["id"] for b in offered["block_models"] if b["name"] == "Модель 2026"],
    }
    path = tmp_path / "Демо"  # the extension comes with the format
    r = c.post(f"{API}/omf/export", json={"path": str(path), **pick})
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=120)
    assert job["status"] == "done", job
    res = job["result"]
    file = Path(res["path"])
    assert file.name == "Демо.omf" and file.is_file() and not file.with_name("Демо.omf.part").exists()
    assert res["wireframes"] == len(pick["wireframe_ids"]) and res["block_models"] == 1 and res["cells"] == 600
    with dialect.read_tx(project.engine) as conn:
        log = conn.execute(sa.select(export_log).where(export_log.c.format == "omf")).mappings().one()
    assert log["file_name"] == "Демо.omf" and log["counts"]["size"] == file.stat().st_size

    back = run_job(target.folder, {"files": [str(file)]}).result
    assert back["wireframes"] == res["wireframes"] and back["layers"] == res["line_layers"]
    assert back["block_models"] == 1 and back["cells"] == 600
    tstore = BlobStore(target.folder)
    before, after = wireframes_of(project.engine, store), wireframes_of(target.engine, tstore)
    grey = {k: (v[0], v[1], v[2] or "#808080", v[3]) for k, v in before.items()}  # OMF wants a colour: grey
    assert after == grey  # vertices bit for bit, triangles, colours, DTMs (ТЗ 7.24)
    old, new = layers_of(project.engine, store), layers_of(target.engine, tstore)
    assert set(new) <= set(old) and len(new) == res["line_layers"]
    for name, layer in new.items():
        was = old[name]
        assert layer.xyz.tobytes() == was.xyz.tobytes(), name
        assert np.array_equal(layer.offsets, was.offsets) and np.array_equal(layer.closed, was.closed), name
    with dialect.read_tx(target.engine) as conn:
        (model,) = bm.list_block_models(conn)
        info = bm.block_model_info(conn, model["id"])
        got_idx, got = bm.read_cells(conn, tstore, model["id"])
    assert info["grid"] == {**GRID, "order": "F", "variable": False} and info["name"] == "Модель 2026"
    assert [(f["name"], f["kind"], f["unit"]) for f in info["fields"]] == [("Au", "number", "g/t"),
                                                                         ("Порода", "category", None)]  # fmt: skip
    assert info["fields"][1]["labels"] == ["руда", "порода"]
    assert np.array_equal(got_idx, idx) and np.array_equal(got["Au"], values["Au"], equal_nan=True)
    assert np.array_equal(got["Порода"], values["Порода"])


@pytest.mark.parametrize("name", ["Rock Model.tridb", "ORE SEP 2025.tridb", "Восточный Цители-Сопели.STR"])
def test_real_files_to_omf_and_back_bit_for_bit(target, tmp_path: Path, name: str) -> None:  # type: ignore[no-untyped-def]
    """10.5 п. 3: a real file → the project → OMF → another project — the same geometry bit for bit."""
    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path / "src", "Исходный", {})
    src = SimpleNamespace(
        folder=folder, engine=dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    )
    try:
        first = run_job(src.folder, {"files": [str(REAL / name)]}).result
        store = BlobStore(src.folder)
        out = tmp_path / f"{Path(name).stem}.omf"
        counts, _warnings = omf_job.write_objects(
            out, wireframe_ids=first["wireframe_ids"],
            line_file_ids=[f["object_id"] for f in first["files"] if f.get("kind") == "lines"],
            engine=src.engine, store=store,
        )  # fmt: skip
        assert counts["size"] == out.stat().st_size
        run_job(target.folder, {"files": [str(out)]})
        tstore = BlobStore(target.folder)
        before = {k: (v[0], v[1], v[2] or "#808080", v[3]) for k, v in wireframes_of(src.engine, store).items()}
        assert wireframes_of(target.engine, tstore) == before
        old, new = layers_of(src.engine, store), layers_of(target.engine, tstore)
        assert set(new) == set(old)
        for layer_name, layer in new.items():
            assert layer.xyz.tobytes() == old[layer_name].xyz.tobytes(), layer_name
            assert np.array_equal(layer.offsets, old[layer_name].offsets)
    finally:
        src.engine.dispose()


class Stop(Exception):
    pass


def test_cancel_leaves_no_file_and_refusals_say_why(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, project = demo
    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        ids = [w["id"] for w in omf_job.exportable(conn)["wireframes"]]
    out = tmp_path / "Отмена.omf"
    for stop_at in (1, 2, 5):

        def tick(fraction: float, message: str | None = None, stop_at: int = stop_at, seen: list[int] = [0]) -> None:  # noqa: B006
            seen[0] += 1
            if seen[0] >= stop_at:
                raise Stop

        with pytest.raises(Stop):
            omf_job.write_objects(out, wireframe_ids=ids, engine=project.engine, store=store, progress=tick)
        assert not out.exists() and not out.with_name(out.name + ".part").exists()
    # a file the save window did not ask about: 409 with its name; with overwrite — the job goes
    taken = tmp_path / "Есть.omf"
    taken.write_bytes(b"old")
    r = c.post(f"{API}/omf/export", json={"path": str(tmp_path / "Есть"), "wireframe_ids": ids[:1]})
    assert r.status_code == 409 and r.json()["code"] == "files_exist" and r.json()["files"] == ["Есть.omf"]
    r = c.post(f"{API}/omf/export", json={"path": str(taken), "wireframe_ids": ids[:1]})
    assert r.status_code == 200, r.text  # the very file chosen in the save window: it asked already
    r = c.post(f"{API}/omf/export", json={"path": str(tmp_path / "x.omf")})
    assert r.status_code == 422 and r.json()["code"] == "nothing_to_export" and r.json()["hint"]
    r = c.post(f"{API}/omf/export", json={"path": str(tmp_path / "x.omf"), "block_model_ids": [999_999]})
    assert r.status_code == 404 and r.json()["code"] == "object_not_found"
    big = {"corner": [0, 0, 0], "counts": [1000, 1000, 101], "size": [1, 1, 1]}  # 101 million cells, 3 occupied
    with dialect.write_tx(project.engine) as conn:
        big_id = bm.put_block_model(conn, store, big, FIELDS[:1], np.array([0, 5, 9]), {"Au": [1.0, 2.0, 3.0]},
                                    name="Большая")  # fmt: skip
    row = next(b for b in c.get(f"{API}/omf/exportable").json()["block_models"] if b["id"] == big_id)
    assert not row["available"] and "100" in row["reason"]["message"] and row["reason"]["hint"]
    r = c.post(f"{API}/omf/export", json={"path": str(tmp_path / "x.omf"), "block_model_ids": [big_id]})
    assert r.status_code == 422 and r.json()["code"] == "omf_not_exportable"


def test_the_export_of_objects_finds_the_omf_writer() -> None:
    """«Экспорт объектов» (Д-17, `exporting/writers.py`) takes OMF from here: the format is available."""
    from bf_core.exporting import writers

    assert writers._omf_writer() is omf_job.write_objects
    assert writers._omf_available(None) is None  # type: ignore[arg-type]
