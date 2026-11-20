"""The job `export.objects` and `/export/…` (ТЗ 7.35 Д-17; ADR 0010 §1, ADR 0017 §5): the demo into a new folder
«‹Проект› — экспорт ‹дата›» with a folder per type, a file per object, every file read back with the same
coordinates bit for bit (STR, DXF, TRIDB); a cancel and a killed worker leave neither the final folder nor —
after the next export — the temporary one; «только просмотр» — 423. The jobs run in `spawn` workers of the core
of the test."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import export_log
from bf_core.exporting import job as ej
from bf_core.exporting import registry as reg
from bf_core.jobs.context import JobCancelled, JobError
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API, wait_job
from tests.test_lines_export import EveryTick

TODAY = time.strftime("%Y-%m-%d")


@pytest.fixture()
def demo(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from bf_core.api.modules import ROUTER_MODULES
    from bf_core.jobs.registry import HANDLER_MODULES

    if "bf_core.exporting.job" not in HANDLER_MODULES:  # until the line of the orchestrator (11.9)
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.exporting.job")
    if "exporting" not in ROUTER_MODULES:
        from bf_core.exporting.router import router

        pclient.app.include_router(router, prefix=API)
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    return pclient, projects_of(pclient.core).require_current()


@pytest.fixture()
def out(tmp_path: Path) -> Path:
    folder = tmp_path / "Выгрузка проектов"
    folder.mkdir()
    return folder


def _defaults(c: Any) -> dict[str, str]:
    table = c.get(f"{API}/export/formats").json()
    return {t["kind"]: t["default"] for t in table["types"] if t["count"] and t["default"]}


def _run(c: Any, body: dict[str, Any]) -> dict[str, Any]:
    r = c.post(f"{API}/export/objects", json=body)
    assert r.status_code == 200, r.text
    done = wait_job(c, r.json()["source"], r.json()["id"], timeout=120)
    assert done["status"] == "done", done
    return done["result"]  # type: ignore[no-any-return]


def _rows(a: np.ndarray) -> bytes:
    """The vertices as a set, bit for bit: unique rows of float64, sorted."""
    return np.unique(np.ascontiguousarray(a, dtype=np.float64).reshape(-1, 3), axis=0).tobytes()


def _project_geometry(project: Any, item: reg.Item, points: bool = False) -> bytes:
    from bf_core.lines import store as ls
    from bf_core.wireframes import source as src

    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        if item.kind in ("wireframe", "dtm"):
            return _rows(src.read_mesh(conn, store, item.id).xyz)
        layers = [ls.read_layer(conn, store, i) for i in ls.layer_ids(conn, item.id)]
    return _rows(np.concatenate([layer.xyz for layer in layers if points or not layer.points_only]))


def _file_geometry(path: Path) -> bytes:
    from bf_core.formats.registry import read_objects

    read = read_objects(path)
    meshes = list(read.meshes or [])
    if meshes:
        return _rows(np.concatenate([m.xyz for m in meshes]))
    return _rows(np.concatenate([layer.xyz for layer in read.layers]))


def test_demo_into_a_new_folder_with_a_file_per_object(demo, out: Path) -> None:  # type: ignore[no-untyped-def]
    c, project = demo
    with dialect.read_tx(project.engine) as conn:
        items = {i.id: i for i in reg.items(conn)}
    formats = _defaults(c)
    assert formats["wireframe"] == "dxf" and formats["line_file"] == "str" and formats["drillhole_set"] == "csv"
    result = _run(c, {"formats": formats, "folder": str(out)})
    final = out / f"Демо_ участок Северный — экспорт {TODAY}"
    assert result["folder"] == str(final) and final.is_dir()
    assert sorted(p.name for p in out.iterdir()) == [final.name]  # no temporary folder left
    assert {p.name for p in final.iterdir()} >= {"Скважины", "Каркасы", "ЦТМ", "Линии"}
    by_object: dict[int, list[dict[str, Any]]] = {}
    for f in result["files"]:
        assert Path(f["path"]).is_file() and Path(f["path"]).stat().st_size == f["size"]
        by_object.setdefault(f["object_id"], []).append(f)
    assert set(by_object) == set(items) - {s["object_id"] for s in result["skipped"]}
    assert all(s["reason"] for s in result["skipped"])
    for obj_id, files in by_object.items():
        item = items[obj_id]
        if item.kind in ("wireframe", "dtm", "line_file"):
            assert len(files) == 1, files
            assert _file_geometry(Path(files[0]["path"])) == _project_geometry(project, item), item.name
    with dialect.read_tx(project.engine) as conn:
        logged = conn.execute(sa.select(sa.func.count()).select_from(export_log)).scalar()
    assert logged == len(result["files"])
    # the second export of the day: another folder; TRIDB and DXF of lines also read back bit for bit
    again = _run(c, {"formats": {"wireframe": "tridb", "dtm": "tridb", "line_file": "dxf"}, "folder": str(out),
                     "types": ["wireframe", "dtm", "line_file"]})  # fmt: skip
    assert again["name"] == f"{final.name} (2)"
    for f in again["files"]:
        item = items[f["object_id"]]
        geometry = _project_geometry(project, item, points=f["format"] == "dxf")
        assert _file_geometry(Path(f["path"])) == geometry, (item.name, f["format"])


def test_requests_that_cannot_be_exported(demo, out: Path) -> None:  # type: ignore[no-untyped-def]
    c, project = demo
    r = c.post(f"{API}/export/objects", json={"formats": {"block_model": "dat"}, "folder": str(out)})
    assert r.status_code == 422 and r.json()["code"] == "export_nothing" and "Э17" in r.json()["message"]
    r = c.post(f"{API}/export/objects", json={"formats": {"wireframe": "pdf"}, "folder": str(out)})
    assert r.status_code == 422 and r.json()["code"] == "bad_format"
    r = c.post(f"{API}/export/objects", json={"formats": {"wireframe": "dxf"}, "folder": "относительная"})
    assert r.status_code == 422 and r.json()["code"] == "path_not_absolute"
    r = c.post(f"{API}/export/objects", json={"formats": {"wireframe": "dxf"}, "folder": str(out / "нет")})
    assert r.status_code == 404
    project.read_only = True  # opened in another window (ТЗ 5.7)
    try:
        table = c.get(f"{API}/export/formats").json()
        assert table["read_only"] is True and "только для чтения" in table["reason"]
        r = c.post(f"{API}/export/objects", json={"formats": {"wireframe": "dxf"}, "folder": str(out)})
        assert r.status_code == 423 and r.json()["code"] == "project_read_only"
    finally:
        project.read_only = False
    assert list(out.iterdir()) == []


def _handler_run(project: Any, params: dict[str, Any], cancel_at: float | None = None) -> dict[str, Any]:
    from bf_core.jobs.registry import load_handlers

    stop = threading.Event()

    def send(message: tuple[Any, ...]) -> None:
        if cancel_at is not None and message[0] == "progress" and (message[2] or 0.0) >= cancel_at:
            stop.set()

    ctx = EveryTick({"id": 9000, "params": params, "project_folder": str(project.folder)}, send, stop)
    try:
        return load_handlers()[ej.KIND].run(ctx)  # type: ignore[no-any-return]
    finally:
        ctx.close()


def _params(project: Any, out: Path, formats: dict[str, str]) -> dict[str, Any]:
    with dialect.read_tx(project.engine) as conn:
        return ej.request(conn, folder=str(out), formats=formats, project="Демо", store=BlobStore(project.folder))


def test_cancel_and_a_killed_worker_leave_nothing(demo, out: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.exporting.job")
    _c, project = demo
    params = _params(project, out, {"wireframe": "dxf", "dtm": "obj", "line_file": "str", "drillhole_set": "xlsx"})
    with pytest.raises(JobCancelled):
        _handler_run(project, params, cancel_at=0.3)
    assert list(out.iterdir()) == []
    # a worker killed while writing: its temporary folder stays, its lock is free — the next export removes it;
    # one of a job still running (its lock held) stays; one without a lock file — when older than an hour
    killed = out / f".Демо — экспорт {TODAY}.bf-tmp"
    code = (
        "import sys, time; from pathlib import Path; from bf_core.projects.lock import ProjectLock;"
        "p = Path(sys.argv[1]); p.mkdir(); (p / 'Каркасы').mkdir(); (p / 'Каркасы' / 'тело.dxf').write_bytes(b'x');"
        "lock = ProjectLock(p / '.bf-export.lock'); assert lock.acquire(); print('ready', flush=True); time.sleep(60)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code, str(killed)], stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None and proc.stdout.readline().strip() == "ready"
    proc.kill()
    proc.wait(timeout=20)
    from bf_core.projects.lock import ProjectLock

    live = out / ".Другой — экспорт.bf-tmp"
    live.mkdir()
    held = ProjectLock(live / ej.LOCK)
    assert held.acquire()
    old = out / ".Старый — экспорт.bf-tmp"
    old.mkdir()
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    try:
        result = _handler_run(project, _params(project, out, {"wireframe": "dxf"}))
    finally:
        held.release()
    left = sorted(p.name for p in out.iterdir())
    assert left == sorted([live.name, Path(result["folder"]).name]), left
    assert result["name"] == f"Демо — экспорт {TODAY}"


@pytest.mark.windows
def test_cyrillic_target_and_a_rename_refused_for_a_moment(demo, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.exporting.job")
    _c, project = demo
    target = tmp_path / "Мои документы" / "Выгрузка — Северный участок"
    target.mkdir(parents=True)
    real = os.rename
    calls: list[str] = []

    def busy(src: Any, dst: Any) -> None:  # an antivirus holds a file of the folder twice
        calls.append(str(dst))
        if len(calls) <= 2:
            raise PermissionError(13, "Процесс не может получить доступ к файлу")
        real(src, dst)

    monkeypatch.setattr(ej.os, "rename", busy)
    monkeypatch.setattr(ej, "RENAME_PAUSE_S", 0.01)
    result = _handler_run(project, _params(project, target, {"wireframe": "dxf", "line_file": "str"}))
    assert len(calls) == 3 and Path(result["folder"]).is_dir()
    assert all(Path(f["path"]).is_file() and len(f["path"]) <= 259 for f in result["files"])
    assert not [p for p in target.iterdir() if p.name.endswith(ej.TMP_SUFFIX)]
    calls.clear()

    def refused(src: Any, dst: Any) -> None:
        calls.append(str(dst))
        raise PermissionError(13, "Процесс не может получить доступ к файлу")

    monkeypatch.setattr(ej.os, "rename", refused)
    with pytest.raises(JobError) as err:
        _handler_run(project, _params(project, target, {"wireframe": "dxf"}))
    assert err.value.code == "file_unwritable" and len(calls) == ej.RENAME_ATTEMPTS
    assert sorted(p.name for p in target.iterdir()) == [Path(result["folder"]).name]
