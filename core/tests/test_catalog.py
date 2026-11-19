"""The catalog of «Менеджер данных» (ТЗ 7.35 Д-20; ADR 0017 §6, decisions 10–11; `projects/catalog.py`): the
types of the rows, the folders, the search, the sort, the sizes by the share of shared blobs, the move of many
objects in one transaction with `objects.changed`; 1 000 objects — a page ≤ 50 ms, a move ≤ 0,5 s."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs, objects
from bf_core.projects import catalog
from bf_core.projects.service import projects_of
from tests.test_import_job import API

REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real"


def _mount(pclient: Any) -> None:
    from bf_core.api.modules import ROUTER_MODULES

    if "exporting" not in ROUTER_MODULES:  # until the line of the orchestrator (11.9–11.10)
        from bf_core.exporting.router import router

        pclient.app.include_router(router, prefix=API)


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    from tests.test_import_objects import run_job

    _mount(pclient)
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    run_job(project.folder, {"files": [str(REAL / "ORE SEP 2025.tridb"), str(REAL / "Восточный Цители-Сопели.STR")]})
    return pclient, project


def _all(c: Any, **query: Any) -> dict[str, Any]:
    r = c.get(f"{API}/catalog", params={"limit": 5000, **query})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_rows_types_formats_sizes(demo) -> None:  # type: ignore[no-untyped-def]
    c, project = demo
    page = _all(c)
    rows = page["rows"]
    assert page["total"] == len(rows) > 0
    assert {r["kind"] for r in rows} >= {"drillhole_set", "wireframe", "dtm", "line_file"}
    assert set(rows[0]) == {"id", "kind", "name", "folder", "format", "size", "rows", "modified", "crs", "state"}
    with dialect.read_tx(project.engine) as conn:
        kinds = dict(conn.execute(sa.select(objects.c.id, objects.c.kind)).all())
        total_blobs = int(conn.execute(sa.select(sa.func.coalesce(sa.func.sum(blobs.c.size), 0))).scalar() or 0)
    assert all(kinds[r["id"]] in catalog.DATA_KINDS for r in rows)  # no views, series, charts, layers
    assert sum(1 for k in kinds.values() if k in catalog.DATA_KINDS) == len(rows)
    sets = [r for r in rows if r["kind"] == "drillhole_set"]
    assert all(r["rows"] > 0 for r in sets) and all(r["rows"] is None for r in rows if r["kind"] != "drillhole_set")
    tridb = [r for r in rows if r["format"] == "TRIDB"]
    strings = [r for r in rows if r["format"] == "STR"]
    assert tridb and all(r["kind"] == "wireframe" for r in tridb) and len(strings) == 1
    assert strings[0]["name"] == "Восточный Цители-Сопели.STR" and strings[0]["kind"] == "line_file"
    # the TRIDB is one blob of all its wireframes: their shares make the file, the catalog never counts it twice
    source = (REAL / "ORE SEP 2025.tridb").stat().st_size
    assert sum(r["size"] for r in tridb) >= source
    assert sum(r["size"] for r in rows) <= total_blobs + len(rows)  # shares are rounded to bytes
    assert strings[0]["size"] >= (REAL / "Восточный Цители-Сопели.STR").stat().st_size
    assert all(r["state"] == "ready" and r["modified"] for r in rows)
    made = [r for r in rows if r["kind"] == "wireframe" and r["format"] is None]
    assert made  # the demo wireframes are made in the program


def test_filter_search_sort_pages(demo) -> None:  # type: ignore[no-untyped-def]
    c, _project = demo
    rows = _all(c)["rows"]
    names = [r["name"].casefold() for r in rows]
    assert names == sorted(names) or [(r["name"].casefold(), r["id"]) for r in rows] == sorted(
        (r["name"].casefold(), r["id"]) for r in rows
    )
    by_size = _all(c, sort="size", desc=True)["rows"]
    sizes = [r["size"] for r in by_size]
    assert sizes == sorted(sizes, reverse=True)
    by_rows = _all(c, sort="rows")["rows"]
    have = [r for r in by_rows if r["rows"] is not None]
    assert by_rows[: len(have)] == have  # missing values last
    assert [r["rows"] for r in have] == sorted(r["rows"] for r in have)
    wf = _all(c, kind="wireframe")["rows"]
    assert wf and all(r["kind"] == "wireframe" for r in wf)
    assert all(r["kind"] == "dtm" for r in _all(c, kind="dtm")["rows"])
    found = _all(c, q="цители")["rows"]
    assert [r["name"] for r in found] == ["Восточный Цители-Сопели.STR"]
    first = c.get(f"{API}/catalog", params={"offset": 2, "limit": 3}).json()
    assert first["total"] == len(rows) and [r["id"] for r in first["rows"]] == [r["id"] for r in rows[2:5]]
    assert c.get(f"{API}/catalog", params={"sort": "размер"}).status_code == 422
    assert c.get(f"{API}/catalog", params={"kind": "chart"}).status_code == 422


def test_move_many_into_a_folder(demo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    c, project = demo
    rows = _all(c)["rows"]
    tridb = [r["id"] for r in rows if r["format"] == "TRIDB"]
    lines = [r["id"] for r in rows if r["format"] == "STR"]
    sent: list[tuple[str, dict[str, Any]]] = []
    bus = c.core.events
    real = bus.publish
    monkeypatch.setattr(bus, "publish", lambda topic, data: (sent.append((topic, data)), real(topic, data))[1])
    r = c.post(f"{API}/catalog/move", json={"ids": tridb + lines, "folder": " Участок 1 // Борт/ "})
    assert r.status_code == 200, r.text
    assert r.json() == {"moved": len(tridb) + len(lines), "folder": "Участок 1/Борт"}
    changed = {d["kind"]: d for t, d in sent if t == "objects.changed"}
    assert sorted(changed["wireframe"]["ids"]) == sorted(tridb) and changed["line_file"]["ids"] == lines
    assert changed["wireframe"]["moved"] is True and changed["wireframe"]["folder"] == "Участок 1/Борт"
    folders = c.get(f"{API}/catalog/folders").json()["folders"]
    assert {"folder": "Участок 1/Борт", "count": len(tridb) + len(lines)} in folders
    inside = _all(c, folder="Участок 1")["rows"]  # a folder holds the folders in it
    assert sorted(r["id"] for r in inside) == sorted(tridb + lines)
    assert all(r["folder"] is not None for r in _all(c, folder="Участок 1/Борт")["rows"])
    root = _all(c, folder="")["rows"]
    assert root and all(r["folder"] is None or r["id"] not in tridb + lines for r in root)
    # back to the root; an unknown object moves nothing (one transaction)
    assert c.post(f"{API}/catalog/move", json={"ids": lines, "folder": ""}).json()["folder"] is None
    r = c.post(f"{API}/catalog/move", json={"ids": [*tridb, 10**9], "folder": "Чужая"})
    assert r.status_code == 404 and r.json()["code"] == "object_not_found"
    assert all(r["folder"] == "Участок 1/Борт" for r in _all(c)["rows"] if r["id"] in tridb)
    r = c.post(f"{API}/catalog/move", json={"ids": tridb, "folder": "п" * 201})
    assert r.status_code == 422 and r.json()["code"] == "folder_too_long"
    project.read_only = True
    try:
        assert c.post(f"{API}/catalog/move", json={"ids": tridb, "folder": "Х"}).status_code == 423
    finally:
        project.read_only = False


def test_thousand_objects_page_and_move_in_time(pclient) -> None:  # type: ignore[no-untyped-def]
    _mount(pclient)
    r = pclient.post(f"{API}/projects", json={"name": "Тысяча объектов"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    now = dialect.utcnow()
    with dialect.write_tx(project.engine) as conn:
        values = [
            {"kind": "block_model", "name": f"Модель {i:04d}", "name_key": f"модель {i:04d}",
             "folder": f"Участок {i % 7}" if i % 3 else None, "meta": {"v": 1, "cells": i}, "state": "ready",
             "created_at": now, "updated_at": now, "sort_key": float(i), "version": 1}
            for i in range(1000)
        ]  # fmt: skip
        conn.execute(sa.insert(objects), values)
        ids = list(conn.execute(sa.select(objects.c.id).where(objects.c.kind == "block_model")).scalars())
        shas = [f"{i:064x}" for i in range(500)]
        conn.execute(
            sa.insert(blobs), [{"sha256": s, "size": 1000, "media_type": "x", "created_at": now} for s in shas]
        )
        conn.execute(  # every blob shared by two models: each counts half
            sa.insert(blob_owners),
            [
                {"sha256": shas[k // 2], "owner_kind": "object", "owner_id": i, "role": "cells"}
                for k, i in enumerate(ids)
            ],
        )
    times = []
    for _ in range(5):
        start = time.perf_counter()
        with dialect.read_tx(project.engine) as conn:
            page = catalog.page(conn, sort="size", desc=True, limit=2000)
        times.append(time.perf_counter() - start)
    assert page["total"] == 1000 and all(r["size"] == 500 for r in page["rows"])
    assert sorted(times)[2] <= 0.05, times
    start = time.perf_counter()
    r = pclient.post(f"{API}/catalog/move", json={"ids": ids, "folder": "Все модели"})
    took = time.perf_counter() - start
    assert r.status_code == 200 and r.json()["moved"] == 1000, r.text
    assert took <= 0.5, took
    assert pclient.get(f"{API}/catalog/folders").json()["folders"] == [{"folder": "Все модели", "count": 1000}]
