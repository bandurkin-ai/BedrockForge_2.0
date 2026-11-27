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
import hashlib
from collections.abc import Iterable, Mapping
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


def without_pending(meta: dict[str, Any]) -> dict[str, Any]:
    """The meta with the pending dropped: the state `idle`."""
    out = copy.deepcopy(meta)
    out["pending"], out["state"] = None, "idle"
    return out


def closing_part(meta: dict[str, Any], parts: Iterable[Mapping[str, Any]], at: str) -> sp.Encoded | None:
    """The part that finishes the waiting cards of `meta.pending` (`parts` — the decoded parts of the dialog), or None
    when there is no room for one more part. The caller stores it and takes the meta from `sp.grown` (pending=None)."""
    shown = [i for part in parts for i in part.get("items") or [] if isinstance(i, dict)]
    entries = [t for t in (meta.get("pending") or {}).get("tools") or [] if isinstance(t, dict)]
    enc = sp.encode(sp.part_of(len(meta["parts"]) + 1, at, [], _closed_cards(entries, shown, at)))
    try:
        sp.check_room(meta, enc.raw)
    except ApiError:
        return None
    return enc


def close_copied_pending(
    meta: dict[str, Any], parts: list[sp.Encoded], decoded: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """`Dialogs.copy_to` of a saved dialog into another project: the pending of the copy is closed the same way. The new
    part is appended to `parts`; the new meta is returned (the caller recomputes `parts` and `bytes`)."""
    if not meta.get("pending"):
        return meta
    at = sp.stamp()
    enc = closing_part(meta, decoded, at)
    if enc is None:
        return without_pending(meta)
    parts.append(enc)
    return sp.grown(meta, hashlib.sha256(enc.blob).hexdigest(), enc.raw, at=at, state="idle", pending=None)


def close_imported_pending(conn: sa.Connection, store: BlobStore) -> int:
    """Closes the `pending` of every dialog of the project of `conn` (see the module); the number of dialogs changed."""
    rows = conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == sp.KIND)).all()
    closed = 0
    for row in rows:
        try:
            meta = sp.check_meta(row.meta)
        except ApiError:
            continue  # a damaged dialog is listed as `error` and is deleted by the user
        if not meta.get("pending"):
            continue
        closed += 1
        at = sp.stamp()
        new = without_pending(meta)
        try:
            decoded = [part for _blob, part, _raw in sp.load_parts(store, meta["parts"], meta["bytes"])]
        except ApiError:  # unreadable parts: the pending goes, the cards stay as they were
            enc = None
        else:
            enc = closing_part(meta, decoded, at)  # None: no room for one more part, the same
        if enc is not None:
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
