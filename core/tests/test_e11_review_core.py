"""Findings of the review of the core of Э11 (11.21) at the level of jobs and the API: the OMF export of a model is
judged by the cells of its grid; «только просмотр» refuses `POST /omf/export` (423) and `GET /omf/exportable`
says so; a volume the store refuses is skipped and the import goes on; a model with broken cells is skipped by
«Экспорт объектов»; long tables of a set whose names match after cleaning go to different CSV files; an object a
job is still building does not move to another folder."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import store as bm
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.exporting import registry as reg
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.formats.omf_builder import RawOmf
from tests.test_export_objects import _handler_run, _params
from tests.test_import_job import API
from tests.test_import_objects import run_job

FIELDS = [{"name": "Au", "kind": "number", "unit": "g/t"}]


@pytest.fixture()
def demo(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from bf_core.exporting import omf_router
    from bf_core.exporting.router import router
    from bf_core.jobs.registry import HANDLER_MODULES

    if "bf_core.exporting.job" not in HANDLER_MODULES:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.exporting.job")
    paths = {getattr(r, "path", "") for r in pclient.app.routes}
    if f"{API}/export/formats" not in paths:
        pclient.app.include_router(router, prefix=API)
    if f"{API}/omf/exportable" not in paths:
        pclient.app.include_router(omf_router.router, prefix=API)
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    return pclient, projects_of(pclient.core).require_current()


def _omf_writer() -> reg.Writer:
    return next(w for w in reg.formats_of("block_model") if w.format == "omf")


def test_a_model_is_judged_by_the_cells_of_its_grid(demo) -> None:  # type: ignore[no-untyped-def]
    _c, project = demo
    store = BlobStore(project.folder)
    big = {"corner": [0, 0, 0], "counts": [1000, 1000, 101], "size": [1, 1, 1]}  # 101 million cells, 3 occupied
    with dialect.write_tx(project.engine) as conn:
        big_id = bm.put_block_model(conn, store, big, FIELDS, np.array([0, 5, 9]), {"Au": [1.0, 2.0, 3.0]},
                                    name="Большая")  # fmt: skip
    with dialect.read_tx(project.engine) as conn:
        items = {i.id: i for i in reg.items(conn, ["block_model"])}
    assert items[big_id].info["cells"] == 3 and items[big_id].info["grid_cells"] == 101_000_000
    why = _omf_writer().accepts(items[big_id])
    assert why is not None and "100 млн" in why.message and why.hint
    assert all(_omf_writer().accepts(i) is None for k, i in items.items() if k != big_id)  # the model of the demo


def test_read_only_project_refuses_the_omf_export(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, project = demo
    ids = [w["id"] for w in c.get(f"{API}/omf/exportable").json()["wireframes"]]
    assert ids and c.get(f"{API}/omf/exportable").json()["read_only"] is False
    project.read_only = True  # opened in another window (ТЗ 5.7)
    try:
        lists = c.get(f"{API}/omf/exportable").json()
        assert lists["read_only"] is True and "только для чтения" in lists["reason"] and lists["wireframes"]
        r = c.post(f"{API}/omf/export", json={"path": str(tmp_path / "x.omf"), "wireframe_ids": ids[:1]})
        assert r.status_code == 423 and r.json()["code"] == "project_read_only" and r.json()["hint"]
    finally:
        project.read_only = False
    assert not (tmp_path / "x.omf").exists()


def test_a_volume_the_store_refuses_is_skipped(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path / "projects", "Объёмы", {})
    raw = RawOmf()
    for name, tensors, origin in (
        ("Хорошая", ([1.0, 1.0], [1.0], [1.0]), (0.0, 0.0, 0.0)),
        ("Огромная", ([1e308], [1.0], [1.0]), (1.7e308, 0.0, 0.0)),  # its far side is beyond the floats
        ("Отказ", ([1.0, 1.0, 1.0], [1.0], [1.0]), (0.0, 0.0, 0.0)),
    ):
        n = len(tensors[0])
        raw.volume(name, tensors, origin=origin, data=[raw.scalar("Au", np.arange(1.0, n + 1), "cells")])
    path = raw.write(tmp_path / "Модели.omf")
    real = bm.CellsWriter

    def picky(store: Any, grid: Any, fields: Any, *a: Any, **k: Any) -> Any:
        if list(grid["counts"]) == [3, 1, 1]:
            raise ApiError(422, "block_model_invalid", "Сетка модели отклонена.", "Проверьте исходный файл модели.")
        return real(store, grid, fields, *a, **k)

    monkeypatch.setattr(bm, "CellsWriter", picky)
    with np.errstate(over="ignore"):
        ctx = run_job(folder, {"files": [str(path)]})
    (entry,) = [f for f in ctx.result["files"] if f.get("kind") == "block_models"]
    assert entry["block_models"] == 1 and entry["skipped"] == 2 and entry["cells"] == 2
    text = "\n".join(ctx.lines)
    assert "объём «Огромная» не загружен" in text and "диапазона" in text
    assert "объём «Отказ» не загружен: Сетка модели отклонена." in text
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.read_tx(engine) as conn:
            assert [m["name"] for m in bm.list_block_models(conn)] == ["Хорошая"]
    finally:
        engine.dispose()


def test_a_model_with_broken_cells_is_skipped_by_the_export(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _c, project = demo
    store = BlobStore(project.folder)
    grid = {"corner": [0, 0, 0], "counts": [4, 3, 2], "size": [1, 1, 1]}
    with dialect.write_tx(project.engine) as conn:
        bad_id = bm.put_block_model(conn, store, grid, FIELDS, np.array([1, 2, 3]), {"Au": [1.0, 2.0, 3.0]},
                                    name="Испорченная")  # fmt: skip
        sha = bm.cells_sha(conn, bad_id)
    cells = store.path_of(sha)
    os.chmod(cells, 0o644)
    table = pa.table({"idx": pa.array([9, 5, 0], pa.uint64()), "Au": pa.array([1.0, 2.0, 3.0])})  # out of order
    pq.write_table(table, cells)
    out = tmp_path / "Выгрузка"
    out.mkdir()
    res = _handler_run(project, _params(project, out, {"block_model": "omf"}))
    (skipped,) = res["skipped"]
    assert skipped["object_id"] == bad_id and "повреждён" in skipped["reason"] and skipped["hint"]
    written = sorted(p.name for p in out.rglob("*.omf"))
    assert written == ["Блочная модель Au.omf"], (written, res)  # the model of the demo is written
    assert not list(out.rglob("Испорченная*")) and not list(out.rglob("*.part"))


def test_long_tables_with_names_alike_go_to_different_csv(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from bf_core.drillholes import export as dx
    from bf_core.drillholes import export_book as eb

    monkeypatch.setattr(eb, "tables_of", lambda _conn, _set: [("1", "А/Б"), ("2", "А_Б"), ("3", "а/б")])
    monkeypatch.setattr(eb, "_read", lambda _e, _s, table: ([], 5, {"table": table}))
    monkeypatch.setattr(dx, "XLSX_ROWS", 1)  # every table is longer than a sheet

    def write_table(path: Path, _fmt: str, _columns: Any, _n: int, *, source: Any, **_k: Any) -> list[str]:
        path.write_text(source["table"], encoding="utf-8")
        return []

    monkeypatch.setattr(dx, "write_table", write_table)
    engine = sa.create_engine("sqlite://")
    files, _rows, warnings = eb.write_book(tmp_path / "Набор.xlsx", 1, engine=engine, project_folder=None,
                                           tick=lambda *_a: None)  # fmt: skip
    csv = files[1:]
    assert len(csv) == 3 and len({p.name.casefold() for p in csv}) == 3, [p.name for p in csv]
    assert sorted(p.read_text(encoding="utf-8") for p in csv) == ["1", "2", "3"]  # nothing written over
    assert all(p.name in w for p, w in zip(csv, warnings, strict=True))


def test_an_object_still_building_does_not_move(demo) -> None:  # type: ignore[no-untyped-def]
    c, project = demo
    rows = c.get(f"{API}/catalog", params={"limit": 5000}).json()["rows"]
    ready = [r for r in rows if r["state"] == "ready"]
    first, other = ready[0], ready[1]
    with dialect.write_tx(project.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == first["id"]).values(state="building"))
    r = c.post(f"{API}/catalog/move", json={"ids": [other["id"], first["id"]], "folder": "Перенос"})
    assert r.status_code == 409 and r.json()["code"] == "object_not_ready", r.text
    assert f"«{first['name']}»" in r.json()["message"] and r.json()["hint"]
    with dialect.read_tx(project.engine) as conn:
        folders = dict(conn.execute(sa.select(objects.c.id, objects.c.folder).where(
            objects.c.id.in_([first["id"], other["id"]]))).all())  # fmt: skip
    assert folders == {first["id"]: first["folder"], other["id"]: other["folder"]}  # one transaction: none moved
