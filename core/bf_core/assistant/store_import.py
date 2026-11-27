"""Dialogs of the assistant in a project just imported from a `.bfggis` (Э26, проверка В5).

A dialog may be kept with `meta.pending`: commands of the window (`data.delete ids=12`) the user did not run yet. Their
arguments hold the numbers of the objects of the project that wrote the file; the import renumbers the objects, so
the card «Выполнить» would run the old arguments over another object (the chat confirms with the numbers only).
Therefore every pending of an imported project is closed here, in the same transaction as the import:

    n = close_imported_pending(conn, store)      # the number of dialogs closed

The waiting cards become `declined` with the text `errors.TEXT_IMPORTED` (a new part, the same `id` of the card: the
last one wins, as with every card that got its result), `meta.pending` is dropped and the state is `idle`. The
messages of the API are not touched: the next question closes the unanswered `tool_use` blocks by itself
(`loop._result_blocks`), so the dialog stays readable and can be continued; `POST /dialogs/{id}/tool_results` answers
409 `assistant_no_pending`. The results of the data tools kept in the pending go with it (they describe the old
project). A damaged dialog (`check_meta`) is left as it is; one that cannot take one more part (400 parts / 32 MiB,
unreadable parts) loses only its pending (the cards stay as they were).
"""

from __future__ import annotations

import copy
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.storage.blobs import BlobStore

from . import store_parts as sp
from .errors import TEXT_IMPORTED


def _closed_cards(entries: list[dict[str, Any]], shown: list[dict[str, Any]], at: str) -> list[dict[str, Any]]:
    """The finished card of every waiting command: the waiting card of the same `item_id` gives title and time."""
    last: dict[str, dict[str, Any]] = {str(it.get("id")): it for it in shown if it.get("kind") == "tool"}
    out: list[dict[str, Any]] = []
    for e in entries:
        tool_use_id = str(e.get("tool_use_id") or "")
        item_id = str(e.get("item_id") or f"imp-{tool_use_id}")
        name = str(e.get("name") or "")
        card = copy.deepcopy(last.get(item_id)) or {
            "kind": "tool", "id": item_id, "at": at, "tool_use_id": tool_use_id, "name": name, "side": "ui",
            "title": name or "команда", "args": e.get("args") if isinstance(e.get("args"), dict) else {},
        }  # fmt: skip
        card.update(status="declined", summary=TEXT_IMPORTED)
        out.append(card)
    return out


def close_imported_pending(conn: sa.Connection, store: BlobStore) -> int:
    """Closes the `pending` of every dialog of the project of `conn` (see the module); the number of dialogs changed."""
    rows = conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == sp.KIND)).all()
    closed = 0
    for row in rows:
        try:
            meta = sp.check_meta(row.meta)
        except ApiError:
            continue  # a damaged dialog is listed as `error` and is deleted by the user
        pending = meta.get("pending")
        if not pending:
            continue
        closed += 1
        at = sp.stamp()
        entries = [t for t in pending.get("tools") or [] if isinstance(t, dict)]
        new = copy.deepcopy(meta)
        new["pending"], new["state"] = None, "idle"
        try:
            shown: list[dict[str, Any]] = []
            for _blob, part, _raw in sp.load_parts(store, meta["parts"], meta["bytes"]):
                shown += [i for i in part.get("items") or [] if isinstance(i, dict)]
            enc = sp.encode(sp.part_of(len(meta["parts"]) + 1, at, [], _closed_cards(entries, shown, at)))
            sp.check_room(meta, enc.raw)
        except ApiError:  # unreadable parts or no room for one more: the pending goes, the cards stay as they were
            pass
        else:
            sha = store.commit_staged(
                conn, store.stage_bytes(enc.blob), media_type=sp.MEDIA, owner_kind=sp.OWNER, owner_id=int(row.id),
                role=sp.ROLE,
            )  # fmt: skip
            new = sp.grown(meta, sha, enc.raw, at=at, state="idle", pending=None)
        conn.execute(
            sa.update(objects)
            .where(objects.c.id == row.id)
            .values(meta=new, version=objects.c.version + 1, updated_at=dialect.utcnow())
        )
    return closed
