"""`.bfggis` export → import (ТЗ 7.4): same composition, renumbered ids, password, versions, failures."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.archive.container import ArchiveWriter
from bf_core.archive.spec import SKIPPED, TABLES
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, edit_log, object_refs, objects, project_md, settings
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore

API = "/api/v1"


def test_every_project_table_is_archived_or_skipped() -> None:
    listed = {t.name for t in TABLES} | set(SKIPPED)
    assert set(project_md.tables) - {"alembic_version"} <= listed, set(project_md.tables) - listed


def wait_job(c, key: str, job_id: int, timeout: float = 30) -> dict:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = c.get(f"{API}/jobs/{key}/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(job)


def fill(c) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    p = projects_of(c.core).require_current()
    now = dialect.utcnow()
    base = dict(sort_key=0.0, state="ready", version=1, created_at=now, updated_at=now)
    store = BlobStore(p.folder)
    with dialect.write_tx(p.engine) as conn:
        # gaps in ids: renumbering must not rely on them being 1..n
        conn.execute(sa.insert(objects).values(id=10, kind="folder", name="Папка", name_key="папка", meta={}, **base))
        conn.execute(
            sa.insert(objects).values(
                id=20, parent_id=10, kind="wireframe", name="Тело", name_key="тело", meta={"triangles": 12}, **base
            )
        )
        conn.execute(sa.insert(objects).values(id=30, kind="chart", name="Гр", name_key="гр", meta={}, **base))
        conn.execute(sa.insert(object_refs).values(src_id=30, dst_id=20, role="source"))
        sha = store.put_bytes(
            conn, b"\x00\x01" * 5000, media_type="application/x-npy", owner_kind="object", owner_id=20
        )
        conn.execute(
            sa.insert(edit_log).values(
                scope_kind="object",
                scope_id=20,
                group_id=1,
                action="meta",
                changes={"name": ["Старое", "Тело"]},
                undone=False,
                created_at=now,
            )
        )
        conn.execute(sa.insert(settings).values(scope="view", key="hidden", value=["wf:20"], version=1))
    return {"sha": sha, "uid": p.uid}


def export(c, path: Path, password: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    r = c.post(f"{API}/projects/export", json={"path": str(path), "password": password})
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"])
    assert job["status"] == "done", job
    return job


@pytest.fixture()
def ac(pclient):  # type: ignore[no-untyped-def]
    pclient.post(API + "/projects", json={"name": "Исходный", "description": {"deposit": "Северное"}})
    return pclient


def test_roundtrip_same_composition_and_new_ids(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    src = fill(ac)
    out = tmp_path / "Проект_2026-11-16"
    job = export(ac, out)
    path = Path(job["result"]["path"])
    assert path.suffix == ".bfggis" and not path.with_name(path.name + ".part").exists()
    info = ac.post(f"{API}/projects/import/inspect", json={"path": str(path)}).json()
    assert info["name"] == "Исходный" and info["name_taken"] and info["copy_name"] == "Исходный (2)"
    assert info["summary"]["wireframes"] == 1 and info["encrypted"] is False and info["from_bf"] is False
    r = ac.post(f"{API}/projects/import", json={"path": str(path), "mode": "copy"}).json()
    job = wait_job(ac, r["source"], r["id"])
    assert job["status"] == "done", job
    res = job["result"]
    assert res["name"] == "Исходный (2)" and res["uid"] != src["uid"]
    new_ids = res["id_maps"]["objects"]
    assert set(new_ids) == {"10", "20", "30"}
    opened = ac.post(f"{API}/projects/{res['uid']}/open").json()
    assert opened["summary"]["wireframes"] == 1 and opened["summary"]["charts"] == 1
    assert opened["description"]["deposit"] == "Северное"
    p = projects_of(ac.core).require_current()
    with dialect.read_tx(p.engine) as conn:
        wf = conn.execute(sa.select(objects).where(objects.c.kind == "wireframe")).mappings().one()
        assert wf["id"] == new_ids["20"] and wf["parent_id"] == new_ids["10"] and wf["meta"] == {"triangles": 12}
        ref = conn.execute(sa.select(object_refs)).mappings().one()
        assert (ref["src_id"], ref["dst_id"]) == (new_ids["30"], new_ids["20"])
        own = conn.execute(sa.select(blob_owners)).mappings().one()
        assert own["owner_id"] == new_ids["20"] and own["sha256"] == src["sha"]
        log = conn.execute(sa.select(edit_log)).mappings().one()
        assert log["scope_id"] == new_ids["20"] and log["changes"] == {"name": ["Старое", "Тело"]}
        assert conn.execute(sa.select(settings.c.value)).scalar_one() == ["wf:20"]
    assert BlobStore(p.folder).read_bytes(src["sha"]) == b"\x00\x01" * 5000
    r = ac.get(f"{API}/blobs/{src['sha']}", headers={"Range": "bytes=0-1"})
    assert r.status_code == 206 and r.content == b"\x00\x01"


def test_password_wrong_missing_and_never_stored(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    fill(ac)
    path = Path(export(ac, tmp_path / "secret.bfggis", password="Пароль-42")["result"]["path"])
    info = ac.post(f"{API}/projects/import/inspect", json={"path": str(path)}).json()
    assert info["encrypted"] and info["needs_password"] and "name" not in info
    r = ac.post(f"{API}/projects/import/inspect", json={"path": str(path), "password": "нет"})
    assert r.status_code == 422 and r.json()["code"] == "bad_password" and r.json()["message"] == "Неверный пароль"
    r = ac.post(f"{API}/projects/import", json={"path": str(path), "password": "Пароль-42", "mode": "copy"}).json()
    job = wait_job(ac, r["source"], r["id"])
    assert job["status"] == "done"
    everything = ac.get(f"{API}/jobs").text + json.dumps(
        [ac.get(f"{API}/jobs/{j['source']}/{j['id']}/log").json() for j in ac.get(f"{API}/jobs").json()["jobs"]]
    )
    assert "Пароль-42" not in everything


def test_newer_file_rejected(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "new.bfggis"
    manifest = {"format": "bfggis", "app": "bf2", "schema_revision": "9999", "project": {"name": "Будущее"}}
    with ArchiveWriter(path, {"app": "bf2"}) as w:
        w.add_bytes("manifest.json", json.dumps(manifest).encode())
    r = ac.post(f"{API}/projects/import/inspect", json={"path": str(path)})
    assert r.status_code == 422 and "обновите программу" in r.json()["message"]


def test_broken_file_leaves_nothing_and_replace_keeps_old(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    fill(ac)
    p = projects_of(ac.core).require_current()
    with dialect.write_tx(p.engine) as conn:  # several zstd blocks: a cut end leaves the beginning readable
        BlobStore(p.folder).put_bytes(conn, os.urandom(3 << 20), media_type="x", owner_kind="object", owner_id=20)
    good = Path(export(ac, tmp_path / "good.bfggis")["result"]["path"])
    data = good.read_bytes()
    broken = tmp_path / "broken.bfggis"
    broken.write_bytes(data[: len(data) - 1000])  # cut: the end of the zstd stream is gone
    root = projects_of(ac.core).root
    before = sorted(p.name for p in root.iterdir())
    r = ac.post(f"{API}/projects/import", json={"path": str(broken), "mode": "replace"}).json()
    assert "source" in r, r
    job = wait_job(ac, r["source"], r["id"])
    assert job["status"] == "failed" and job["attempts"] <= 2
    assert sorted(p.name for p in root.iterdir()) == before  # no partial project, the replaced one is intact
    r = ac.post(f"{API}/projects/import", json={"path": str(good), "mode": "replace"}).json()
    job = wait_job(ac, r["source"], r["id"])
    assert job["status"] == "done", job
    names = [e["name"] for e in ac.get(f"{API}/projects").json()["projects"]]
    assert names == ["Исходный"]


def test_file_of_bf1_is_recognised(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "bf1.bfggis"
    manifest = {
        "format": "bfggis",
        "app_version": "0.9.0",
        "schema_revision": "0016",
        "project": {"name": "Демо: участок Северный", "description": ""},
        "tables": {"collar": 48},
    }
    with ArchiveWriter(path, {"app_version": "0.9.0", "schema_revision": "0016"}) as w:
        w.add_bytes("manifest.json", json.dumps(manifest, ensure_ascii=False).encode())
    info = ac.post(f"{API}/projects/import/inspect", json={"path": str(path)}).json()
    assert info["from_bf"] is True and info["name"] == "Демо: участок Северный" and info["tables"]["collar"] == 48


def test_lost_password_never_writes_a_plain_file(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.jobs.service import jobs_of

    sched = jobs_of(ac.core)
    out = tmp_path / "x.bfggis"
    # what a retry after a worker crash sees: the job says «encrypted», the secret was taken by the first attempt
    key, jid = sched.submit("export.project", {"path": str(out), "encrypted": True}, secret=None)
    job = wait_job(ac, key, jid)
    assert job["status"] == "failed" and job["error"]["code"] == "password_lost" and not out.exists()
    r = ac.post(f"{API}/jobs/{key}/{jid}/retry")
    assert r.status_code == 409 and r.json()["code"] == "password_not_kept"


def test_internal_kinds_are_not_started_by_the_generic_endpoint(ac) -> None:  # type: ignore[no-untyped-def]
    r = ac.post(f"{API}/jobs", json={"kind": "import.project", "params": {"replace_folder": "/"}})
    assert r.status_code == 422 and r.json()["code"] == "unknown_job"


def test_import_retry_finds_the_project_of_the_first_attempt(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.archive.importer import find_imported, import_project

    fill(ac)
    path = Path(export(ac, tmp_path / "t.bfggis")["result"]["path"])
    root = projects_of(ac.core).root
    first = import_project(path, root, name="Повтор", token="tok1")
    again = find_imported(root, "tok1")
    assert again is not None and again["uid"] == first["uid"] and again["id_maps"] == first["id_maps"]
    assert find_imported(root, "other") is None
