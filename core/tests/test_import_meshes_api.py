"""Wireframes in «Импорт файлов» through the API and the scheduler (ADR 0013 §5; 8.15, 8.18): the job in a
worker process, the tree it gives, and a `.bfggis` cycle of what it wrote — the wireframes of a file, the order
of the file, `meta.source`, the journal rows and the source file they own together. Reference: the geometry BLOBs
of the TRIDB the wireframes came from, byte for byte."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, import_log
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API, wait_job
from tests.test_import_meshes import make_files, paths, sha_of

pytestmark = pytest.mark.windows


@pytest.fixture()
def files(tmp_path: Path) -> dict[str, Path]:
    return make_files(tmp_path / "исходные")


def test_wizard_through_the_api(pclient, files: dict[str, Path]) -> None:  # type: ignore[no-untyped-def]
    """inspect → start → the job in a worker process → the tree of wireframes and of lines."""
    pclient.post(f"{API}/projects", json={"name": "Каркасы"})
    body = {"files": paths(files), "options": {str(files["ore.dm"]): {"split_by": ""}}, "folder": "Импорт"}
    seen = pclient.post(f"{API}/import/objects/inspect", json=body)
    assert seen.status_code == 200, seen.text
    assert seen.json()["totals"]["wireframes"] == 7 and seen.json()["totals"]["files"] == 5
    r = pclient.post(f"{API}/import/objects", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and job["result"]["wireframes"] == 7, job
    assert len(job["result"]["wireframe_ids"]) == 7 and job["result"]["failed"] == []
    tree = pclient.get(f"{API}/wireframes").json()["wireframes"]
    assert sorted((w["name"], w["folder"]) for w in tree) == sorted([
        ("Куб", "Импорт/Тела.tridb"), ("Рельеф", "Импорт/Тела.tridb"), ("ORE", "Импорт/mixed.dxf"),
        ("TOPO", "Импорт/mixed.dxf"), ("pit", "Импорт"), ("Куб (2)", "Импорт"), ("ore (2)", "Импорт"),
    ])  # fmt: skip  (names differ without case: «ore» of Datamine comes after the layer «ORE» of the drawing)
    assert [f["name"] for f in pclient.get(f"{API}/lines/files").json()["files"]] == ["mixed.dxf"]
    first = tree[0]
    assert pclient.get(f"{API}/viz/wireframes/{first['id']}").status_code == 200  # the 3D manifest of an imported one


def test_imported_wireframes_survive_bfggis(pclient, files: dict[str, Path], tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The wireframes of a file, the order of the file, `meta.source`, the journal rows and the source file
    they own together come back from a `.bfggis` with new ids — and the TRIDB is still written byte for byte."""
    pclient.post(f"{API}/projects", json={"name": "Каркасы"})
    r = pclient.post(f"{API}/import/objects", json={"files": paths(files, "Тела.tridb", "one.obj")})
    assert wait_job(pclient, r.json()["source"], r.json()["id"])["status"] == "done"

    def tree() -> list[tuple[Any, ...]]:
        shown = pclient.get(f"{API}/wireframes").json()["wireframes"]
        return [(w["name"], w["folder"], w["color"], w["triangle_count"], w["source"], w["attrs"]) for w in shown]

    before = tree()
    assert [row[:2] for row in before] == [("Куб", "Тела.tridb"), ("Рельеф", "Тела.tridb"), ("Куб (2)", None)]
    r = pclient.post(f"{API}/projects/export", json={"path": str(tmp_path / "w.bfggis"), "password": None})
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done", job
    r = pclient.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "done", job
    pclient.post(f"{API}/projects/{job['result']['uid']}/open")
    assert tree() == before
    project = projects_of(pclient.core).require_current()
    sha = sha_of(files["Тела.tridb"])
    with dialect.read_tx(project.engine) as conn:
        log = conn.execute(sa.select(import_log).order_by(import_log.c.id)).mappings().all()
        assert [(row["file_name"], row["counts"]["kind"]) for row in log] == [
            ("Тела.tridb", "wireframe"), ("Тела.tridb", "wireframe"), ("one.obj", "wireframe"),
        ]  # fmt: skip
        owners = conn.execute(sa.select(blob_owners).where(blob_owners.c.owner_kind == "import")).mappings().all()
        assert sorted((o["owner_id"], o["sha256"] == sha) for o in owners) == [
            (log[0]["id"], True), (log[1]["id"], True), (log[2]["id"], False),
        ]  # fmt: skip
        ids = [row["object_id"] for row in log[:2]]
    assert BlobStore(project.folder).exists(sha)
    body = {"path": str(tmp_path / "снова.tridb"), "format": "tridb", "wireframe_ids": ids}
    r = pclient.post(f"{API}/wireframes/export", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done", job

    def geometry(path: Path) -> list[tuple[int, bytes]]:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
            return [(int(i), bytes(b)) for i, b in conn.execute("SELECT ID, Geometry FROM Geometry ORDER BY ID")]

    assert geometry(Path(job["result"]["path"])) == geometry(files["Тела.tridb"])
