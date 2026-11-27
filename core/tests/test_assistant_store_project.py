"""Dialogs of the assistant across the projects (подшаг 23.6, the second half of `test_assistant_store.py`): the move of
a temporary dialog into the project opened later (№ 130) and the copy into another one after a change of the project
(решение 31), delete and the collector of the blobs, a damaged part, the dialogs unseen by the catalog and the graph,
`.bfggis` export → import (the dialogs are there, no usage), the delete of a project, PostgreSQL (`-m db`, BF_TEST_PG)
and the criteria of the speed (a part of 2 MiB ≤ 0,1 s, a list of 1000 dialogs ≤ 0,2 s)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.assistant import store_parts as sp
from bf_core.assistant.store import Dialogs
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs, lineage, objects
from bf_core.storage import gc
from bf_core.storage.blobs import BlobStore
from tests.test_assistant_store import ANSWER, API, NOTE_NONE, USAGE, ask, item, project_in, refused


@pytest.fixture()
def temp() -> sp.TempDialogs:
    return sp.TempDialogs()


@pytest.fixture()
def proj(tmp_path: Path):  # type: ignore[no-untyped-def]
    p = project_in(tmp_path)
    yield p
    p.engine.dispose()


@pytest.fixture()
def dlg(proj: Any, temp: sp.TempDialogs) -> Dialogs:
    return Dialogs(proj, temp)


# --- the project changes ---------------------------------------------------------------------------


def test_temporary_dialog_moves_into_the_project_opened_later(proj: Any, temp: sp.TempDialogs) -> None:  # № 130
    none = Dialogs(None, temp)
    h = ask(none, "Вопрос без проекта")
    pending = {"id": "p-7", "tools": [{"tool_use_id": "t1"}], "data_results": [1]}
    none.append(h["id"], ANSWER, [item("assistant", 3, text="Второй ход")], pending=pending)
    uid = temp.get(h["id"]).meta["uid"]  # type: ignore[union-attr]
    mine = Dialogs(proj, temp)
    moved = none.copy_to(h["id"], mine)
    assert (
        not moved["temporary"]
        and moved["project_uid"] == "p-1"
        and moved["uid"] == uid
        and moved["title"] == h["title"]
    )
    assert moved["questions"] == 1 and moved["state"] == "pending" and moved["cost_usd"] == 0.0421
    assert temp.get(h["id"]) is None and none.list()["total"] == 0  # moved, not copied
    loaded = mine.read(moved["id"])
    assert (
        [i["id"] for i in loaded.items] == ["i1", "i2", "i3"]
        and len(loaded.messages) == 3
        and loaded.pending["id"] == "p-7"
    )  # type: ignore[index]
    with dialect.read_tx(proj.engine) as conn:
        assert (
            conn.execute(
                sa.select(sa.func.count()).select_from(blob_owners).where(blob_owners.c.role == "data")
            ).scalar_one()
            == 2
        )
        meta = conn.execute(sa.select(objects.c.meta)).scalar_one()
    assert meta["pending"]["data_results"] == [1] and meta["bytes"] > 0
    mine.append(moved["id"], [], [item("assistant", 4, text="и дальше")])  # it goes on in the project
    assert len(mine.read(moved["id"]).items) == 4


def test_change_of_project_copies_the_dialog_and_the_old_keeps_the_version_before(
    tmp_path: Path, temp: sp.TempDialogs
) -> None:
    a, b = project_in(tmp_path, "a"), project_in(tmp_path, "b")
    try:
        da, db = Dialogs(a, temp), Dialogs(b, temp)
        h = ask(da, "Вопрос в проекте A")
        da.append(h["id"], ANSWER, [item("assistant", 3, text="Ещё")])
        copied = da.copy_to(h["id"], db)
        assert copied["project_uid"] == "b" and copied["uid"] != h["uid"] and copied["questions"] == 1
        da.append(h["id"], [], [item("assistant", 4, text="после смены")])  # the old one lives on in A
        assert [i["id"] for i in db.read(copied["id"]).items] == ["i1", "i2", "i3"]
        assert [i["id"] for i in da.read(h["id"]).items] == ["i1", "i2", "i3", "i4"]
        assert len(BlobStore(b.folder).root.glob("??/??/*").__iter__().__next__().name) == 64
        # the copy goes on in B: its parts are B's own files
        db.append(copied["id"], [], [item("user", 5, text="в B")], questions=1)
        assert db.read(copied["id"]).head["questions"] == 2 and da.read(h["id"]).head["questions"] == 1
        # a saved dialog of a project into the temporary ones (the project is closed and the user goes on)
        none = Dialogs(None, temp)
        tmp_copy = da.copy_to(h["id"], none)
        assert tmp_copy["temporary"] and none.read(tmp_copy["id"]).items[-1]["id"] == "i4" and da.read(h["id"]).items
        assert none.read(tmp_copy["id"]).note == NOTE_NONE
    finally:
        a.engine.dispose()
        b.engine.dispose()


# --- delete, the collector of the blobs, the views of the project ----------------------------------


def test_delete_drops_the_parts_and_the_collector_keeps_the_live_ones(dlg: Dialogs, proj: Any) -> None:
    keep, gone = ask(dlg, "Оставить"), ask(dlg, "Удалить")
    dlg.append(gone["id"], [], [item("assistant", 5, text="ещё часть")])
    store = BlobStore(proj.folder)
    with dialect.read_tx(proj.engine) as conn:
        shas = {int(i): m["parts"] for i, m in conn.execute(sa.select(objects.c.id, objects.c.meta))}
    far = time.time() + 30 * 24 * 3600  # the strays and the temp files of any age would go: the parts must not
    report = gc.collect(proj.engine, store, now=far)
    assert report.blobs == 0 and all(store.exists(s) for parts in shas.values() for s in parts)
    dlg.delete(gone["id"])
    refused("assistant_dialog_not_found", dlg.read, gone["id"])
    refused("assistant_dialog_not_found", dlg.delete, gone["id"])
    with dialect.read_tx(proj.engine) as conn:
        assert (
            conn.execute(
                sa.select(sa.func.count()).select_from(blob_owners).where(blob_owners.c.owner_id == int(gone["id"]))
            ).scalar_one()
            == 0
        )
    report = gc.collect(proj.engine, store, now=far)
    assert report.blobs == 2  # the two parts of the deleted dialog, nothing of the other one
    assert not any(store.exists(s) for s in shas[int(gone["id"])]) and all(
        store.exists(s) for s in shas[int(keep["id"])]
    )
    assert dlg.read(keep["id"]).items[1]["text"] == "Ответ" and dlg.list()["total"] == 1


def test_a_lost_or_damaged_part_is_a_russian_error_not_a_crash(dlg: Dialogs, proj: Any) -> None:
    h = ask(dlg, "Побитый")
    with dialect.read_tx(proj.engine) as conn:
        sha = conn.execute(sa.select(objects.c.meta)).scalar_one()["parts"][0]
    path = BlobStore(proj.folder).path_of(sha)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])  # cut
    e = refused("assistant_dialog_broken", dlg.read, h["id"])
    assert e.status == 409 and "повреждён" in e.message
    path.write_bytes(b"not zstd at all")
    refused("assistant_dialog_broken", dlg.read, h["id"])
    path.unlink()
    refused("assistant_dialog_broken", dlg.read, h["id"])
    assert dlg.list()["total"] == 1 and dlg.rename(h["id"], "Можно переименовать")  # the history is still managed
    dlg.delete(h["id"])


def test_dialogs_are_not_data_not_in_the_graph_and_counted_as_chats(
    dlg: Dialogs, proj: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bf_core.lineage import graph
    from bf_core.projects import catalog
    from bf_core.projects import store as projects_store

    with dialect.write_tx(proj.engine) as conn:
        now = dialect.utcnow()
        conn.execute(
            sa.insert(objects).values(
                kind="chart",
                name="Гр",
                name_key="гр",
                meta={},
                state="ready",
                version=1,
                sort_key=0.0,
                created_at=now,
                updated_at=now,
            )  # fmt: skip
        )
    for n in range(3):
        ask(dlg, f"Вопрос {n}")
    with dialect.write_tx(proj.engine) as conn:
        assert (
            [r["name"] for r in catalog.rows(conn)] == [] and catalog.DATA_KINDS and sp.KIND not in catalog.DATA_KINDS
        )
        assert catalog.folders(conn) == {} or "assistant" not in json.dumps(catalog.folders(conn))
        graph.reindex(conn)
        graph.sweep(conn)
        assert conn.execute(sa.select(sa.func.count()).select_from(lineage)).scalar_one() == 0
    assert dlg.list()["total"] == 3  # the sweep of the graph left the dialogs alone
    with dialect.read_tx(proj.engine) as conn:
        assert projects_store.summary(conn)["chats"] == 3  # the summary of the project: «диалоги Claude»


# --- .bfggis, delete of the project (the whole core) -----------------------------------------------


def wait_job(c, key: str, job_id: int, timeout: float = 30) -> dict:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = c.get(f"{API}/jobs/{key}/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(job)


def test_bfggis_export_import_keeps_the_dialogs_and_no_usage(pclient, tmp_path: Path, temp: sp.TempDialogs) -> None:  # type: ignore[no-untyped-def]
    from bf_core.projects.service import projects_of

    pclient.post(f"{API}/projects", json={"name": "С диалогами"})
    src = projects_of(pclient.core).require_current()
    d = Dialogs(src, temp)
    first = ask(d, "Сводка по проекту", at="2026-11-26T08:00:00Z")
    d.append(first["id"], ANSWER, [item("assistant", 3, text="Ещё ход")])
    second = ask(d, "Другой диалог", at="2026-11-26T11:00:00Z")
    d.rename(second["id"], "Переименованный")
    big = Dialogs(src, temp).create("Крупный")
    d.append(
        big["id"], [], [item("assistant", 1, text=os.urandom(1 << 19).hex())], questions=1, at="2026-11-26T12:00:00Z"
    )
    want = {h["title"]: d.read(h["id"]) for h in d.list()["items"]}
    # the profile of the assistant (the usage) lives beside the projects, never in one
    (tmp_path / "roaming").mkdir(exist_ok=True)
    usage = tmp_path / "roaming" / "assistant" / "usage.jsonl"
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text('{"model": "claude-opus-5-5", "cost": 0.04}\n', encoding="utf-8")
    out = tmp_path / "диалоги.bfggis"
    r = pclient.post(f"{API}/projects/export", json={"path": str(out), "password": None})
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done", job
    r = pclient.post(f"{API}/projects/import", json={"path": str(out), "mode": "copy"}).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "done", job
    uid = job["result"]["uid"]
    assert uid != src.uid
    pclient.post(f"{API}/projects/{uid}/open")
    dst = projects_of(pclient.core).require_current()
    got = Dialogs(dst, temp)
    page = got.list()
    assert (
        page["total"] == 3
        and {h["title"] for h in page["items"]} == set(want)
        and all(h["project_uid"] == uid for h in page["items"])
    )
    for h in page["items"]:
        a, b = want[h["title"]], got.read(h["id"])
        assert (b.messages, b.items, b.pending) == (a.messages, a.items, a.pending)
        assert (h["questions"], h["cost_usd"], h["tokens"], h["models"], h["state"]) == (
            a.head["questions"], a.head["cost_usd"], a.head["tokens"], a.head["models"], a.head["state"])  # fmt: skip
    assert got.last()["title"] == "Крупный"  # type: ignore[index]
    got.append(got.last()["id"], [], [item("user", 9, text="продолжение")], questions=1)  # they go on in the copy
    assert Dialogs(src, temp).list()["total"] == 3  # the source is as it was
    # no usage anywhere in the new project
    files = [p.name for p in dst.folder.rglob("*") if p.is_file()]
    assert not any("usage" in n for n in files) and usage.read_text(encoding="utf-8").count("\n") == 1
    # a dialog's blobs of the copy are owned by the copy
    with dialect.read_tx(dst.engine) as conn:
        owners = (
            conn.execute(sa.select(blob_owners.c.owner_id).where(blob_owners.c.role == "data").distinct())
            .scalars()
            .all()
        )
        ids = conn.execute(sa.select(objects.c.id).where(objects.c.kind == sp.KIND)).scalars().all()
    assert set(owners) == set(ids)


def test_bfggis_import_closes_the_pending_commands_of_a_dialog(pclient, tmp_path: Path, temp: sp.TempDialogs) -> None:  # type: ignore[no-untyped-def]
    """Э26 В5: the commands waiting for the window carry the numbers of the old project (`data.delete ids=12`): after
    the import they are closed with «Не выполнено», the dialog is still readable and goes on."""
    from bf_core.assistant import errors
    from bf_core.projects.service import projects_of

    pclient.post(f"{API}/projects", json={"name": "С ожиданием"})
    src = projects_of(pclient.core).require_current()
    d = Dialogs(src, temp)
    head = ask(d, "Удали набор 12")
    waiting = item("tool", 5, tool_use_id="t9", name="ui_data_delete", side="ui", title="Удалить", status="waiting",
                   args={"ids": [12]}, summary="")  # fmt: skip
    use = {"type": "tool_use", "id": "t9", "name": "ui_data_delete", "input": {"ids": [12]}}
    call = {"role": "assistant", "content": [use]}
    pending = {
        "id": "p-9",
        "tools": [
            {"tool_use_id": "t9", "name": "ui_data_delete", "command": None, "args": {"ids": [12]}, "item_id": "i5"}
        ],
        "data_results": [{"type": "tool_result", "tool_use_id": "d1", "content": "ok"}],
    }
    d.append(head["id"], [call], [waiting], pending=pending)
    before = d.read(head["id"])
    assert before.pending is not None and before.head["state"] == "pending"
    out = tmp_path / "ожидание.bfggis"
    r = pclient.post(f"{API}/projects/export", json={"path": str(out), "password": None})
    assert wait_job(pclient, r.json()["source"], r.json()["id"])["status"] == "done"
    r = pclient.post(f"{API}/projects/import", json={"path": str(out), "mode": "copy"}).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "done", job
    pclient.post(f"{API}/projects/{job['result']['uid']}/open")
    dst = projects_of(pclient.core).require_current()
    got = Dialogs(dst, temp)
    loaded = got.read(got.list()["items"][0]["id"])
    assert loaded.pending is None and loaded.meta["pending"] is None and loaded.head["state"] == "idle"
    assert loaded.messages == before.messages  # the history of the API is as it was: the next question closes the call
    cards = [i for i in loaded.items if i["id"] == "i5"]
    assert [c["status"] for c in cards] == ["waiting", "declined"]  # the last card of the id wins
    assert cards[1]["summary"] == errors.TEXT_IMPORTED and cards[1]["args"] == {"ids": [12]}
    assert cards[1]["title"] == "Удалить" and "Не выполнено" in cards[1]["summary"]
    assert got.list()["items"][0]["questions"] == before.head["questions"]  # the dialog goes on
    got.append(got.list()["items"][0]["id"], [], [item("user", 9, text="повтори")], questions=1)
    assert Dialogs(src, temp).read(head["id"]).pending == before.pending  # the source keeps what it had


def test_deleting_a_project_deletes_its_dialogs_and_not_the_temporary_ones(
    pclient, tmp_path: Path, temp: sp.TempDialogs
) -> None:  # type: ignore[no-untyped-def]
    from bf_core.projects.service import projects_of

    made = pclient.post(f"{API}/projects", json={"name": "Удаляемый"}).json()
    project = projects_of(pclient.core).require_current()
    folder = Path(made["path"])
    d = Dialogs(project, temp)
    ask(d, "В проекте")
    hold = ask(Dialogs(None, temp), "Временный")
    assert list((folder / "blobs").glob("??/??/*"))
    assert pclient.delete(f"{API}/projects/{made['uid']}").status_code == 200
    assert not folder.exists()  # the dialogs, their parts and the database went with the folder
    assert Dialogs(None, temp).read(hold["id"]).head["title"] == "Временный"


# --- PostgreSQL ------------------------------------------------------------------------------------


@pytest.mark.db
def test_the_store_on_both_databases(db_url: str, tmp_path: Path, temp: sp.TempDialogs) -> None:
    p = project_in(tmp_path, "db", db_url)
    try:
        d = Dialogs(p, temp)
        a = ask(d, "Первый", at="2026-11-26T10:00:00Z")
        b = ask(d, "Второй", at="2026-11-26T10:01:00Z", pending={"id": "p-1", "tools": []})
        d.append(a["id"], ANSWER, [item("assistant", 7, text="Ещё")], questions=1, at="2026-11-26T10:02:00Z")
        assert [h["id"] for h in d.list()["items"]] == [a["id"], b["id"]] and d.last()["id"] == a["id"]  # type: ignore[index]
        loaded = d.read(a["id"])
        assert [i["id"] for i in loaded.items] == ["i1", "i2", "i7"] and loaded.head["questions"] == 2
        assert d.read(b["id"]).pending == {
            "id": "p-1",
            "created_at": d.read(b["id"]).pending["created_at"],
            "tools": [],
        }  # type: ignore[index]
        assert d.rename(b["id"], "Переименован")["title"] == "Переименован"
        moved = d.copy_to(a["id"], Dialogs(None, temp))
        assert moved["temporary"] and len(Dialogs(None, temp).read(moved["id"]).items) == 3
        store = BlobStore(p.folder)
        assert gc.collect(p.engine, store, now=time.time() + 10**7).blobs == 0
        d.delete(b["id"])
        assert gc.collect(p.engine, store, now=time.time() + 10**7).blobs == 1
        assert d.list()["total"] == 1
        with dialect.read_tx(p.engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(blobs)).scalar_one() == 2
    finally:
        p.engine.dispose()


# --- speed -----------------------------------------------------------------------------------------


def test_a_part_of_2_mib_is_written_in_a_tenth_of_a_second(dlg: Dialogs) -> None:
    h = dlg.create("Крупные части")
    text = os.urandom(1 << 20).hex()  # 2 MiB of JSON text, hard to compress: the worst case of the write
    assert len(text) == 2 << 20
    times = []
    for n in range(5):
        t0 = time.perf_counter()
        dlg.append(
            h["id"], [{"role": "assistant", "content": [{"type": "text", "text": text}]}], [item("assistant", n)]
        )
        times.append(time.perf_counter() - t0)
    print(f"2 MiB part: {[round(t, 3) for t in times]} s")
    assert min(times) <= 0.1, times  # the criterion of 23.6; the median below guards against a luck of one run
    assert sorted(times)[2] <= 0.25, times
    assert len(dlg.read(h["id"]).messages) == 5


def test_a_list_of_1000_dialogs_in_two_tenths_of_a_second(dlg: Dialogs, proj: Any) -> None:
    metas = []
    for n in range(1000):
        meta = sp.new_meta(f"u{n}", f"2026-11-{1 + n % 28:02d}T{n % 24:02d}:{n % 60:02d}:00Z")
        for k in range(12):  # a working dialog: a dozen parts, the cost, two models
            meta = sp.grown(meta, f"{n:032x}{k:032x}", 4000, at=meta["last_at"], questions=1, usage=USAGE,
                            models=["claude-opus-5-5", "claude-haiku-5-5"], prices_version="2026-11")  # fmt: skip
        metas.append(meta)
    with dialect.write_tx(proj.engine) as conn:
        now = dialect.utcnow()
        conn.execute(
            sa.insert(objects),
            [dict(kind=sp.KIND, name=f"Диалог {n}", name_key=f"диалог {n}", state="ready", version=1, sort_key=0.0,
                  meta=m, created_at=now, updated_at=now) for n, m in enumerate(metas)],
        )  # fmt: skip
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        page = dlg.list(0, 50)
        times.append(time.perf_counter() - t0)
    print(f"list of 1000 dialogs: {[round(t, 3) for t in times]} s")
    assert page["total"] == 1000 and len(page["items"]) == 50 and min(times) <= 0.2, times
    last_ats = [h["last_at"] for h in page["items"]]
    assert last_ats == sorted(last_ats, reverse=True)
    t0 = time.perf_counter()
    assert dlg.last() is not None
    assert time.perf_counter() - t0 <= 0.3
