"""Blob storage (ТЗ 7.6, ADR 0002 §5): write, owners, collector, ETag and Range."""

from __future__ import annotations

import os
import time

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore, add_owner, drop_owner
from bf_core.storage.gc import collect

API = "/api/v1"


@pytest.fixture()
def project(pclient):  # type: ignore[no-untyped-def]
    pclient.post(API + "/projects", json={"name": "Хранилище"})
    return projects_of(pclient.core).require_current()


def test_put_dedup_and_layout(project) -> None:  # type: ignore[no-untyped-def]
    store = BlobStore(project.folder)
    with dialect.write_tx(project.engine) as conn:
        a = store.put_bytes(conn, b"abc" * 1000, media_type="application/octet-stream", owner_kind="object", owner_id=1)
        b = store.put_bytes(conn, b"abc" * 1000, media_type="application/octet-stream", owner_kind="object", owner_id=2)
    assert a == b and store.path_of(a) == project.folder / "blobs" / a[:2] / a[2:4] / a
    assert store.read_bytes(a) == b"abc" * 1000
    assert not list(store.tmp_dir.iterdir())
    with dialect.read_tx(project.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(blobs)).scalar_one() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(blob_owners)).scalar_one() == 2


def test_unregistered_owner_kind_fails(project) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="BLOB_OWNER_KINDS"), dialect.write_tx(project.engine) as conn:
        add_owner(conn, "a" * 64, "wireframe_v2", 1)


def test_collector_removes_unreferenced_strays_and_old_tmp(project) -> None:  # type: ignore[no-untyped-def]
    store = BlobStore(project.folder)
    with dialect.write_tx(project.engine) as conn:
        kept = store.put_bytes(conn, b"kept", media_type="x", owner_kind="object", owner_id=1)
        gone = store.put_bytes(conn, b"gone", media_type="x", owner_kind="object", owner_id=2)
        drop_owner(conn, "object", 2)
    stray_tmp, stray_sha, _ = store.stage_bytes(b"stray")
    stray = store.path_of(stray_sha)
    stray.parent.mkdir(parents=True, exist_ok=True)
    os.replace(stray_tmp, stray)
    fresh_tmp, _, _ = store.stage_bytes(b"in flight")
    old_tmp, _, _ = store.stage_bytes(b"old")
    now = time.time()
    os.utime(old_tmp, (now - 25 * 3600, now - 25 * 3600))
    report = collect(project.engine, store, now=now)
    assert report.blobs == 1 and not store.exists(gone) and store.exists(kept)
    assert report.stray_files == 0 and stray.exists()  # younger than 15 min: a writer may be committing it
    assert report.tmp_files == 1 and fresh_tmp.exists() and not old_tmp.exists()
    report = collect(project.engine, store, now=now + 16 * 60)
    assert report.stray_files == 1 and not stray.exists()


def test_blob_etag_range_and_304(pclient, project) -> None:  # type: ignore[no-untyped-def]
    data = bytes(range(256)) * 40
    store = BlobStore(project.folder)
    with dialect.write_tx(project.engine) as conn:
        sha = store.put_bytes(conn, data, media_type="application/octet-stream", owner_kind="object", owner_id=1)
    r = pclient.get(f"{API}/blobs/{sha}")
    assert r.status_code == 200 and r.content == data and r.headers["etag"] == f'"{sha}"'
    assert r.headers["accept-ranges"] == "bytes"
    r = pclient.get(f"{API}/blobs/{sha}", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == data[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(data)}"
    r = pclient.get(f"{API}/blobs/{sha}", headers={"Range": "bytes=-10"})
    assert r.status_code == 206 and r.content == data[-10:]
    r = pclient.get(f"{API}/blobs/{sha}", headers={"Range": f"bytes={len(data)}-"})
    assert r.status_code == 416
    r = pclient.get(f"{API}/blobs/{sha}", headers={"If-None-Match": f'"{sha}"'})
    assert r.status_code == 304
    assert pclient.get(f"{API}/blobs/{'0' * 64}").status_code == 404
    assert pclient.post(f"{API}/storage/collect").json()["blobs"] == 0


def test_guard_exposes_etag_and_range_headers(pclient, project) -> None:  # type: ignore[no-untyped-def]
    r = pclient.options(
        f"{API}/blobs/{'0' * 64}",
        headers={
            "Origin": "app://bedrockforge",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-bf-token,range",
        },
    )
    assert r.status_code in (200, 204)
    assert "range" in r.headers.get("access-control-allow-headers", "").lower()


def test_collect_needs_project(pclient) -> None:  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/storage/collect")
    assert r.status_code == 409 and r.json()["code"] == "no_project"
