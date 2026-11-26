"""Dialogs of the assistant in the project (подшаг 23.6; ADR 0029 решения 28–32, № 130, № 134; docs/progress/e23-api.md
§7): a dialog made at the first question, the parts written only by appending (zstd JSON, owner `object`, role `data`),
the meta (questions, cost, tokens, `pending`), list / last / rename / delete, the limits (400 parts, 32 MiB), the
temporary dialogs (≤ 20, no project, «только просмотр»), the move of a temporary dialog into a project and the copy
into another one, `.bfggis` export → import (the dialogs are there, no usage), the delete of a project, the dialogs
unseen by the catalog, the graph and the collector of the blobs, the same on PostgreSQL (`-m db`, BF_TEST_PG), the
criteria of the speed. No network, no SDK: the loop (23.10) is another subshag — the parts are made by hand here."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.assistant import store_parts as sp
from bf_core.assistant.store import Dialogs
from bf_core.db import dialect, migrate
from bf_core.db.schema import blob_owners, objects, valid_blob_role
from bf_core.storage.blobs import BlobStore
from tests.dbutil import make_engine

API = "/api/v1"
NOTE_NONE = "Диалог не сохранится: проект не открыт"
USAGE = {"input": 1200, "output": 300, "cache_read": 5000, "cache_write": 800, "cost_usd": 0.0421}
TOOL = {
    "tool_use_id": "t1",
    "name": "ui_hole_select",
    "command": "hole.select",
    "args": {"hole_id": "DH-012"},
    "item_id": "i3",
}
ANSWER = [{"role": "assistant", "content": [{"type": "text", "text": "В проекте 48 скважин."}]}]


def project_in(tmp_path: Path, uid: str = "p-1", url: str = "sqlite", read_only: bool = False) -> Any:
    folder = tmp_path / uid
    folder.mkdir(parents=True, exist_ok=True)
    engine = make_engine(url, folder)
    migrate.upgrade(engine, "project")
    return SimpleNamespace(engine=engine, uid=uid, folder=folder, read_only=read_only)


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


def user(text: str) -> list[Any]:
    return [{"role": "user", "content": [{"type": "text", "text": text}]}]


def item(kind: str, n: int, **more: Any) -> dict[str, Any]:
    return {"kind": kind, "id": f"i{n}", "at": "2026-11-26T10:00:00Z", **more}


def ask(d: Dialogs, text: str, answer: str = "Ответ", **over: Any) -> dict[str, Any]:
    """The first question and its segment: the dialog is made, one part is written."""
    head = d.create(sp.make_title(text))
    kwargs: dict[str, Any] = {"questions": 1, "usage": USAGE, "models": ["claude-opus-5-5"]}
    kwargs.update(over)
    return d.append(
        head["id"],
        user(text) + [{"role": "assistant", "content": [{"type": "text", "text": answer}]}],
        [item("user", 1, text=text), item("assistant", 2, text=answer, model="claude-opus-5-5")],
        **kwargs,
    )


def refused(code: str, fn: Any, *args: Any, **kw: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        fn(*args, **kw)
    assert e.value.code == code and e.value.message and e.value.hint, (e.value.code, e.value.message)
    return e.value


def kinds(proj: Any) -> list[str]:
    with dialect.read_tx(proj.engine) as conn:
        return [str(k) for k in conn.execute(sa.select(objects.c.kind)).scalars()]


# --- the round trip -------------------------------------------------------------------------------------------------


def test_round_trip_parts_meta_and_blobs(dlg: Dialogs, proj: Any) -> None:
    head = dlg.create("Сводка по проекту", at="2026-11-26T10:00:00Z")
    assert head["questions"] == 0 and head["cost_usd"] is None and head["state"] == "idle" and not head["temporary"]
    assert head["id"].isdecimal() and head["project_uid"] == "p-1" and head["created_at"] == "2026-11-26T10:00:00Z"
    h1 = dlg.append(
        head["id"], user("Сводка") + ANSWER, [item("user", 1, text="Сводка"), item("assistant", 2, text="48")],
        questions=1, usage=USAGE, models=["claude-opus-5-5"], prices_version="2026-11", at="2026-11-26T10:00:05Z",
    )  # fmt: skip
    assert h1["id"] == head["id"] and h1["questions"] == 1 and h1["last_at"] == "2026-11-26T10:00:05Z"
    assert h1["cost_usd"] == 0.0421 and h1["models"] == ["claude-opus-5-5"] and h1["created_at"] == head["created_at"]
    assert h1["tokens"] == {"input": 1200, "output": 300, "cache_read": 5000, "cache_write": 800}
    h2 = dlg.append(
        head["id"], user("Ещё") + ANSWER, [item("user", 3, text="Ещё"), item("tool", 4, name="data_tree")],
        questions=1, usage={**USAGE, "cost_usd": 0.01}, models=["claude-opus-5-5", "claude-haiku-5-5"],
        at="2026-11-26T10:05:00Z",
    )  # fmt: skip
    assert h2["questions"] == 2 and h2["cost_usd"] == pytest.approx(0.0521) and h2["last_at"] == "2026-11-26T10:05:00Z"
    assert h2["models"] == ["claude-opus-5-5", "claude-haiku-5-5"] and h2["tokens"]["input"] == 2400
    loaded = dlg.read(head["id"])
    assert [m["content"][0]["text"] for m in loaded.messages] == [
        "Сводка",
        "В проекте 48 скважин.",
        "Ещё",
        "В проекте 48 скважин.",
    ]
    assert (
        [i["id"] for i in loaded.items] == ["i1", "i2", "i3", "i4"] and loaded.pending is None and loaded.note is None
    )
    assert loaded.as_dialog().keys() == {"head", "items", "pending", "note"} and loaded.head == h2
    # the storage: one object, one blob per part (owner `object`, role `data`: existing, no migration), zstd JSON
    with dialect.read_tx(proj.engine) as conn:
        row = conn.execute(sa.select(objects).where(objects.c.kind == sp.KIND)).mappings().one()
        owned = conn.execute(sa.select(blob_owners)).mappings().all()
    assert (
        row["parent_id"] is None
        and row["folder"] is None
        and row["state"] == "ready"
        and row["name"] == "Сводка по проекту"
    )
    assert row["version"] == 3 and row["meta"]["questions"] == 2 and len(row["meta"]["parts"]) == 2
    assert [(o["owner_kind"], o["owner_id"], o["role"]) for o in owned] == [("object", int(head["id"]), "data")] * 2
    assert valid_blob_role("data") and [o["sha256"] for o in owned] == row["meta"]["parts"]
    part = sp.decode(BlobStore(proj.folder).read_bytes(row["meta"]["parts"][0]))
    assert (
        part["seq"] == 1
        and part["at"] == "2026-11-26T10:00:05Z"
        and len(part["messages"]) == 2
        and len(part["items"]) == 2
    )
    assert row["meta"]["bytes"] == sum(sp.raw_size(BlobStore(proj.folder).read_bytes(s)) for s in row["meta"]["parts"])
    assert (
        row["meta"]["prices_version"] == "2026-11"
        and row["meta"]["v"] == 1
        and set(row["meta"]) >= {"uid", "state", "pending"}
    )


def test_a_dialog_without_a_part_is_not_listed_and_a_stale_one_goes(dlg: Dialogs, proj: Any) -> None:
    head = dlg.create("Не дошёл до записи")
    assert dlg.list() == {"total": 0, "items": []} and dlg.last() is None
    assert dlg.read(head["id"]).items == []  # but it can be read and written: its segment may be running
    with dialect.write_tx(proj.engine) as conn:  # the process died an hour ago
        old = dialect.utcnow() - timedelta(hours=2)
        conn.execute(sa.update(objects).values(created_at=old))
    fresh = dlg.create("Следующий")
    assert kinds(proj).count(sp.KIND) == 1 and fresh["uid"] != head["uid"]  # the stale one went (its id may be reused)
    assert dlg.read(fresh["id"]).head["uid"] == fresh["uid"]


def test_titles_and_rename(dlg: Dialogs) -> None:
    assert sp.make_title("  Покажи\n скважину   DH-012  ") == "Покажи скважину DH-012"
    long = "я" * 100
    assert len(sp.make_title(long)) == 60 and sp.make_title(long).endswith("…") and sp.make_title("") == "Новый диалог"
    h = ask(dlg, "Вопрос")
    again = dlg.rename(h["id"], "  Мой   диалог ")
    assert again["title"] == "Мой диалог" and dlg.read(h["id"]).head["title"] == "Мой диалог"
    assert (
        dlg.list()["items"][0]["title"] == "Мой диалог" and dlg.read(h["id"]).head["questions"] == 1
    )  # meta untouched
    for bad in ("", "   ", None, "я" * 201):
        e = refused("bad_param", dlg.rename, h["id"], bad)
        assert e.status == 422 and e.extra == {"param": "title"}
    assert dlg.rename(h["id"], "я" * 200)["title"] == "я" * 200
    refused("assistant_dialog_not_found", dlg.rename, "999999", "x")


def test_cost_is_unknown_not_zero(dlg: Dialogs) -> None:
    h = dlg.create("Дорого?")
    assert dlg.append(h["id"], [], [], questions=1, usage=None)["cost_usd"] is None  # no call, nothing known
    h1 = dlg.append(h["id"], [], [], usage={"input": 10, "output": 5, "cost_usd": 0.5})
    assert h1["cost_usd"] == 0.5
    h2 = dlg.append(h["id"], [], [], usage={"input": 7, "output": 3, "cost_usd": None})  # a model without a price
    assert h2["cost_usd"] is None and h2["tokens"]["input"] == 17 and h2["tokens"]["cache_read"] == 0
    h3 = dlg.append(h["id"], [], [], usage={"input": 1, "output": 1, "cost_usd": 0.25})
    assert h3["cost_usd"] is None  # one unknown call: the total stays unknown, the known ones are not shown as all
    assert dlg.append(h["id"], [], [], usage={"cost_usd": float("nan")})["cost_usd"] is None


def test_nan_is_a_gap_not_zero_and_json_stays_valid(dlg: Dialogs) -> None:
    h = dlg.create("NaN")
    items = [item("tool", 1, args={"x": float("nan"), "y": [1.5, float("inf")], "z": 0})]
    dlg.append(h["id"], [], items, questions=1)
    out = dlg.read(h["id"]).items[0]["args"]
    assert out == {"x": None, "y": [1.5, None], "z": 0}


def test_pending_is_kept_with_the_dialog_and_cleared_by_the_next_part(dlg: Dialogs, proj: Any) -> None:
    h = dlg.create("Покажи скважину DH-012 на разрезе")
    pending = {
        "id": "p-1",
        "created_at": "2026-11-26T10:00:00Z",
        "data_results": [{"tool_use_id": "t0", "text": "48"}],
        "tools": [TOOL],
    }
    h1 = dlg.append(
        h["id"], user("Покажи"), [item("user", 1, text="Покажи")], questions=1, state="idle", pending=pending
    )
    assert h1["state"] == "pending"  # `pending` makes the state, whatever was asked
    loaded = dlg.read(h["id"])
    assert loaded.pending == {
        "id": "p-1",
        "created_at": "2026-11-26T10:00:00Z",
        "tools": pending["tools"],
    }  # no data_results
    with dialect.read_tx(proj.engine) as conn:  # the results of the data tools stay in the meta for the continuation
        assert (
            conn.execute(sa.select(objects.c.meta)).scalar_one()["pending"]["data_results"] == pending["data_results"]
        )
    assert dlg.list()["items"][0]["state"] == "pending"
    h2 = dlg.append(h["id"], ANSWER, [item("assistant", 2, text="Готово")])
    assert h2["state"] == "idle" and dlg.read(h["id"]).pending is None
    e = refused("bad_param", dlg.append, h["id"], [], [], pending={"tools": []})
    assert e.extra == {"param": "pending"}
    refused("bad_param", dlg.append, h["id"], [], [], state="flying")
    assert dlg.append(h["id"], [], [], state="error")["state"] == "error"


def test_list_last_paging_and_running(dlg: Dialogs) -> None:
    ids = []
    for n in range(5):
        ids.append(ask(dlg, f"Вопрос {n}", at=f"2026-11-26T10:0{n}:00Z")["id"])
    page = dlg.list(0, 2)
    assert page["total"] == 5 and [h["id"] for h in page["items"]] == [ids[4], ids[3]]
    assert [h["id"] for h in dlg.list(2, 50)["items"]] == [ids[2], ids[1], ids[0]] and dlg.list(9, 5)["items"] == []
    assert dlg.last()["id"] == ids[4]  # type: ignore[index]
    # a new question into an old dialog makes it the last one (the date of the last question)
    dlg.append(ids[0], user("Ещё"), [item("user", 9, text="Ещё")], questions=1, at="2026-11-26T11:00:00Z")
    assert dlg.last()["id"] == ids[0]  # type: ignore[index]
    # an answer without a question does not move it
    dlg.append(ids[3], ANSWER, [item("assistant", 9, text="Продолжение")], at="2026-11-26T12:00:00Z")
    assert dlg.last()["id"] == ids[0]  # type: ignore[index]
    running = dlg.list(busy={ids[2]})["items"]
    assert {h["id"]: h["state"] for h in running}[ids[2]] == "running" and dlg.get(ids[2], busy={ids[2]})["head"][
        "state"
    ] == "running"
    assert dlg.get(ids[2])["head"]["state"] == "idle"


def test_ids_that_are_not_dialogs(dlg: Dialogs, proj: Any) -> None:
    with dialect.write_tx(proj.engine) as conn:
        now = dialect.utcnow()
        other = conn.execute(
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
        ).inserted_primary_key[0]
    for bad in ("abc", "-1", "", "99999999", "t-nope", str(other), "1" * 40, "١٢"):
        refused("assistant_dialog_not_found", dlg.read, bad)
        refused("assistant_dialog_not_found", dlg.delete, bad)
        refused("assistant_dialog_not_found", dlg.append, bad, [], [])
    with dialect.read_tx(proj.engine) as conn:  # the chart was not touched
        assert conn.execute(sa.select(objects.c.name).where(objects.c.id == other)).scalar_one() == "Гр"


# --- the limits ------------------------------------------------------------------------------------------------------


def test_limit_of_400_parts(dlg: Dialogs) -> None:
    h = dlg.create("Длинный")
    for n in range(sp.MAX_PARTS):
        dlg.append(h["id"], [], [item("notice", n, code="x", message="m")], questions=1 if n == 0 else 0)
    e = refused("assistant_dialog_full", dlg.append, h["id"], [], [item("user", 1, text="ещё")], questions=1)
    assert e.status == 422 and e.message == "Диалог слишком длинный." and e.hint == "Начните новый диалог."
    loaded = dlg.read(h["id"])
    assert len(loaded.items) == sp.MAX_PARTS and loaded.head["questions"] == 1  # the refused part left no trace


def test_limit_of_32_mib_uncompressed(dlg: Dialogs, proj: Any) -> None:
    h = dlg.create("Огромный")
    big = "я" * (9 << 20)  # compresses to nothing, but the JSON is counted as it is (UTF-8: 2 bytes a sign → 18 MiB)
    small = "x" * (14 << 20)
    dlg.append(h["id"], [], [item("assistant", 1, text=small)], questions=1)
    with dialect.read_tx(proj.engine) as conn:
        meta = conn.execute(sa.select(objects.c.meta)).scalar_one()
    assert 14 << 20 < meta["bytes"] < 15 << 20
    refused("assistant_dialog_full", dlg.append, h["id"], [], [item("assistant", 2, text=big)])
    dlg.append(h["id"], [], [item("assistant", 3, text="y" * (14 << 20))])  # 28 MiB: still room
    refused("assistant_dialog_full", dlg.append, h["id"], [], [item("assistant", 4, text="z" * (5 << 20))])
    assert len(dlg.read(h["id"]).items) == 2
    with dialect.read_tx(proj.engine) as conn:
        assert (
            conn.execute(sa.select(sa.func.count()).select_from(blob_owners)).scalar_one() == 2
        )  # nothing of the refused


def test_a_write_that_lost_the_race_leaves_no_file(dlg: Dialogs, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    h = dlg.create("Гонка")
    real, fired = sp.check_room, []

    def other_writer(meta: Any, raw: int) -> None:
        real(meta, raw)
        if not fired:
            fired.append(1)
            dlg.append(h["id"], [], [item("notice", 0, code="c", message="m")], questions=1)  # got in first

    monkeypatch.setattr(sp, "check_room", other_writer)
    refused("assistant_busy", dlg.append, h["id"], [], [item("user", 1, text="я")], questions=1)
    monkeypatch.undo()
    assert [i["id"] for i in dlg.read(h["id"]).items] == ["i0"]
    tmp = BlobStore(proj.folder).tmp_dir
    assert not tmp.exists() or not list(tmp.iterdir())


# --- temporary dialogs ------------------------------------------------------------------------------------------------


def test_without_a_project_the_dialog_is_temporary(temp: sp.TempDialogs) -> None:
    d = Dialogs(None, temp)
    assert not d.persistent and d.note == NOTE_NONE
    h = ask(d, "Сводка по проекту")
    assert h["id"].startswith("t-") and h["temporary"] and h["project_uid"] is None and h["questions"] == 1
    loaded = d.read(h["id"])
    assert loaded.note == NOTE_NONE and loaded.items[1]["text"] == "Ответ" and len(loaded.messages) == 2
    assert d.get(h["id"])["note"] == NOTE_NONE
    assert d.list()["total"] == 1 and d.last()["id"] == h["id"]  # type: ignore[index]
    assert d.rename(h["id"], "Временный")["title"] == "Временный"
    assert [x["title"] for x in d.list()["items"]] == ["Временный"]
    d.delete(h["id"])
    assert d.list() == {"total": 0, "items": []} and d.last() is None
    refused("assistant_dialog_not_found", d.read, h["id"])
    refused("assistant_dialog_not_found", d.delete, h["id"])
    # a saved id means nothing without its project: not found to read, «проект не открыт» to change
    refused("assistant_dialog_not_found", d.read, "41")
    refused("no_project", d.rename, "41", "x")
    refused("no_project", d.delete, "41")


def test_temporary_dialogs_are_at_most_twenty(temp: sp.TempDialogs) -> None:
    d = Dialogs(None, temp)
    ids = [ask(d, f"Вопрос {n}", at=f"2026-11-26T10:{n:02d}:00Z")["id"] for n in range(sp.MAX_TEMP + 3)]
    listed = [h["id"] for h in d.list()["items"]]
    assert len(listed) == sp.MAX_TEMP == d.list()["total"] and listed == ids[3:][::-1]  # the oldest went
    refused("assistant_dialog_not_found", d.read, ids[0])


def test_temporary_dialog_has_the_same_limits_and_nan_rule(
    temp: sp.TempDialogs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sp, "MAX_PARTS", 3)
    d = Dialogs(None, temp)
    h = d.create("Короткий")
    for n in range(3):
        d.append(h["id"], [], [item("tool", n, args={"v": float("nan")})], questions=1)
    refused("assistant_dialog_full", d.append, h["id"], [], [item("user", 9, text="x")])
    assert d.read(h["id"]).items[0]["args"] == {"v": None} and d.read(h["id"]).head["questions"] == 3
    refused("assistant_dialog_full", d.append, h["id"], [], [])


def test_read_only_project_gives_temporary_dialogs_and_shows_what_it_holds(
    tmp_path: Path, temp: sp.TempDialogs
) -> None:
    p = project_in(tmp_path)
    try:
        saved = ask(Dialogs(p, temp), "Старый вопрос", at="2026-11-26T09:00:00Z")
        ro = SimpleNamespace(engine=p.engine, uid=p.uid, folder=p.folder, read_only=True)  # opened «только просмотр»
        d = Dialogs(ro, temp)
        assert not d.persistent and d.note == "Диалог не сохранится: проект открыт только для просмотра"
        fresh = ask(d, "Новый вопрос", at="2026-11-26T10:00:00Z")
        assert fresh["temporary"] and fresh["id"].startswith("t-") and d.read(fresh["id"]).note == d.note
        assert [h["id"] for h in d.list()["items"]] == [fresh["id"], saved["id"]]  # the saved one is shown, too
        assert d.read(saved["id"]).note is None and d.read(saved["id"]).head["project_uid"] == "p-1"
        refused("project_read_only", d.append, saved["id"], [], [])
        refused("project_read_only", d.rename, saved["id"], "x")
        refused("project_read_only", d.delete, saved["id"])
        assert Dialogs(p, temp).list()["total"] == 1  # a project that keeps the new dialogs does not list the temporary
        assert kinds(p).count(sp.KIND) == 1  # nothing was written into the read-only project
    finally:
        p.engine.dispose()
