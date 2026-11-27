"""Dialogs of the assistant in the project (подшаг 23.6; ADR 0029 решения 28–32, № 130, № 134;
docs/progress/e23-api.md §7). The parts, the meta and the temporary dialogs — `store_parts.py`.

A saved dialog is `objects(kind="assistant_dialog", parent_id=NULL, folder=NULL, state="ready")`: `name` — the title,
`meta` — see `store_parts`, the parts — blobs of the owner `object`, role `data` (existing: no migration, no new
role) written only by appending, one per segment of a turn. The dialogs are not data: `catalog.DATA_KINDS`, the data
tree, the graph of the dependencies and the manager of the data do not list them; they go into `.bfggis` with the
project (a copy of the database and the blobs) and go with it when the project is deleted. The collector of the
blobs keeps the parts (they have an owner) and takes them after a deletion (`drop_owner`). The count of a project
(«chats» of the summary) is `OBJECT_KIND_KEYS["assistant_dialog"] = "chats"` of `projects/store.py` — a line of the
orchestrator.

A dialog is made at the first question (`create`; not listed until its first part), each segment adds a part (`append`:
one write transaction — the file, the owner, the meta; a failure leaves the dialog as it was). Without a project that
can keep it (none open, or opened «только просмотр») a dialog is temporary: ≤ 20 in the memory of the core, the same
limits. `copy_to` moves a temporary dialog into the project that was opened meanwhile (№ 130) and copies a saved one
into the current project after a change of the project (решение 31; the old project keeps the version before).

    dialogs = Dialogs(project)                      # project: engine, folder, uid[, read_only]; None — no project
    head = dialogs.create(make_title(text))        # DialogHead; `id` "41" (saved) or "t-<uuid>" (temporary)
    head = dialogs.append(id, messages, items, questions=1, usage={…}, models=[…], state="idle", pending=None)
    loaded = dialogs.read(id)                       # Loaded: head, messages (the API request), items, pending, note
    dialogs.list(offset, limit, busy=…), dialogs.last(), dialogs.rename(id, title), dialogs.delete(id)
    dialogs.copy_to(id, other_dialogs)              # temporary → project (moved), saved → another project (copied)

`busy` — the ids with a running segment (`segments.py`): their state is `running`. Errors: 404
`assistant_dialog_not_found`; 409 `no_project` (rename / delete of a saved dialog without a project), `assistant_busy`
(two writers of one dialog), `assistant_dialog_broken`; 422 `assistant_dialog_full`, `bad_param {param}`; 423
`project_read_only` (a write into a saved dialog of a project opened «только просмотр»).
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.storage.blobs import BlobStore, drop_owner

from . import store_parts as sp
from .store_parts import (
    DEFAULT_TITLE,
    KIND,
    MEDIA,
    OWNER,
    ROLE,
    TEMP,
    Encoded,
    TempDialog,
    TempDialogs,
    make_title,  # noqa: F401 - the public helper of the loop
    stamp,
)

EMPTY_KEEP = timedelta(hours=1)  # a dialog made at a question and never written (a crash) is cleaned after this


class ProjectLike(Protocol):
    engine: sa.Engine
    folder: Path
    uid: str


@dataclass
class Loaded:
    """A dialog read whole: the head, the messages of the API request, the items of the display, the commands
    waiting for the window (`pending`, without the results of the data tools) and the note of a temporary dialog."""

    head: dict[str, Any]
    messages: list[Any]
    items: list[Any]
    pending: dict[str, Any] | None
    note: str | None
    meta: dict[str, Any]

    def as_dialog(self) -> dict[str, Any]:
        """`Dialog` of the contract."""
        return {"head": self.head, "items": self.items, "pending": self.pending, "note": self.note}


def _parse(dialog_id: Any) -> tuple[bool, Any]:
    """(temporary, the key): `t-<uuid>` of a temporary dialog or the id of an object."""
    text = str(dialog_id)
    if text.startswith(sp.TEMP_PREFIX):
        return True, text
    if text.isascii() and text.isdecimal() and len(text) <= 18:
        return False, int(text)
    raise sp.not_found()


class Dialogs:
    def __init__(self, project: ProjectLike | None = None, temp: TempDialogs = TEMP) -> None:
        self.project = project
        self.temp = temp

    # --- where a dialog lives -------------------------------------------------------------------

    @property
    def persistent(self) -> bool:
        """A dialog made now is kept in the project (one is open and it is not «только просмотр»)."""
        return self.project is not None and not getattr(self.project, "read_only", False)

    @property
    def note(self) -> str | None:
        """The note of a temporary dialog (№ 130, № 134); None when the project keeps the new dialogs."""
        if self.project is None:
            return sp.NOTE_NO_PROJECT
        return sp.NOTE_READ_ONLY if getattr(self.project, "read_only", False) else None

    def _store(self) -> BlobStore:
        assert self.project is not None
        return BlobStore(self.project.folder)

    def _need(self, write: bool) -> ProjectLike:
        """The project of a saved dialog: reading one without a project is «not found» (the id belongs to another
        project), changing one is 409 `no_project`; a project «только просмотр» is read only."""
        if self.project is None:
            raise sp.no_project() if write else sp.not_found()
        if write and getattr(self.project, "read_only", False):
            raise sp.read_only()
        return self.project

    def _row(self, conn: sa.Connection, ident: int) -> Any:
        row = conn.execute(
            sa.select(objects.c.id, objects.c.name, objects.c.meta).where(objects.c.id == ident, objects.c.kind == KIND)
        ).first()
        if row is None:
            raise sp.not_found()
        return row

    def _head(self, row: Any, busy: Collection[str] = (), meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
        assert self.project is not None
        uid = self.project.uid
        return sp.head_of(str(row.id), str(row.name), meta or row.meta, project_uid=uid, temporary=False, busy=busy)

    @staticmethod
    def _temp_head(d: TempDialog, busy: Collection[str] = ()) -> dict[str, Any]:
        return sp.head_of(d.id, d.title, d.meta, project_uid=None, temporary=True, busy=busy)

    def _temp(self, dialog_id: str) -> TempDialog:
        d = self.temp.get(dialog_id)
        if d is None:
            raise sp.not_found()
        return d

    # --- create ---------------------------------------------------------------------------------

    def create(self, title: str | None = None, *, at: str | None = None, uid: str | None = None) -> dict[str, Any]:
        """A dialog at its first question; empty (not listed) until `append` writes the first part."""
        title = sp.clean_title(title) if title else DEFAULT_TITLE
        meta = sp.new_meta(uid, at)
        if not self.persistent:
            d, _gone = self.temp.add(title, meta)
            return self._temp_head(d)
        project = self._need(write=True)
        now = dialect.utcnow()
        with dialect.write_tx(project.engine) as conn:
            self._purge_empty(conn, now)
            row = self._row(conn, self._insert(conn, title, meta, now))
        return self._head(row)

    @staticmethod
    def _insert(conn: sa.Connection, title: str, meta: dict[str, Any], now: Any) -> int:
        res = conn.execute(
            sa.insert(objects).values(
                kind=KIND,
                name=title,
                name_key=dialect.name_key(title),
                parent_id=None,
                folder=None,
                sort_key=0.0,
                state="ready",
                version=1,
                meta=meta,
                author=None,
                created_at=now,
                updated_at=now,
            )
        )
        assert res.inserted_primary_key is not None
        return int(res.inserted_primary_key[0])

    def _purge_empty(self, conn: sa.Connection, now: Any) -> None:
        """Dialogs made at a question whose first part was never written (the process died): an hour later they go."""
        stale = [
            int(r.id)
            for r in conn.execute(
                sa.select(objects.c.id, objects.c.meta, objects.c.created_at).where(objects.c.kind == KIND)
            )
            if sp.is_empty(r.meta) and now - r.created_at > EMPTY_KEEP
        ]
        if stale:
            conn.execute(sa.delete(objects).where(objects.c.id.in_(stale)))

    # --- append ---------------------------------------------------------------------------------

    def append(
        self,
        dialog_id: str,
        messages: Sequence[Any],
        items: Sequence[Any],
        *,
        questions: int = 0,
        usage: Mapping[str, Any] | None = None,
        models: Sequence[str] = (),
        prices_version: str | None = None,
        state: str = "idle",
        pending: Mapping[str, Any] | None = None,
        at: str | None = None,
    ) -> dict[str, Any]:
        """One more part (the end of a segment): the new messages of the API and the items of the display. `questions`
        — the questions of the user in it (the date of the last one is `last_at`); `usage` — the sum over the calls
        (`sp.grown`); `pending` — the commands of the window, kept with the dialog. Returns the new head. 422
        `assistant_dialog_full` over 400 parts or 32 MiB; 409 `assistant_busy` when another write got in first."""
        temporary, key = _parse(dialog_id)
        at = at or stamp()
        kwargs: dict[str, Any] = {
            "at": at, "questions": questions, "usage": usage, "models": models,
            "prices_version": prices_version, "state": state, "pending": pending,
        }  # fmt: skip
        if temporary:
            return self._append_temp(key, messages, items, kwargs)
        project = self._need(write=True)
        store = self._store()
        with dialect.read_tx(project.engine) as conn:
            meta = sp.check_meta(self._row(conn, key).meta)  # a dialog of another person's project may be forged
        seq = len(meta["parts"]) + 1
        enc = sp.encode(sp.part_of(seq, at, messages, items))
        sp.check_room(meta, enc.raw)
        staged = store.stage_bytes(enc.blob)  # the fsync is outside the transaction
        try:
            with dialect.write_tx(project.engine) as conn:
                row = self._row(conn, key)
                current = sp.check_meta(row.meta)
                if len(current["parts"]) != seq - 1:
                    raise sp.busy()
                sha = store.commit_staged(conn, staged, media_type=MEDIA, owner_kind=OWNER, owner_id=key, role=ROLE)
                grown = sp.grown(current, sha, enc.raw, **kwargs)
                conn.execute(
                    sa.update(objects)
                    .where(objects.c.id == key)
                    .values(meta=grown, version=objects.c.version + 1, updated_at=dialect.utcnow())
                )
                row = self._row(conn, key)
        except BaseException:
            staged[0].unlink(missing_ok=True)
            raise
        return self._head(row)

    def _append_temp(
        self, key: str, messages: Sequence[Any], items: Sequence[Any], kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        with self.temp.lock:
            d = self._temp(key)
            enc = sp.encode(sp.part_of(len(d.parts) + 1, kwargs["at"], messages, items))
            sp.check_room(d.meta, enc.raw)
            d.meta = sp.grown(d.meta, hashlib.sha256(enc.blob).hexdigest(), enc.raw, **kwargs)
            d.parts.append(enc)
            return self._temp_head(d)

    # --- read -----------------------------------------------------------------------------------

    def read(self, dialog_id: str, busy: Collection[str] = ()) -> Loaded:
        """The whole dialog: the parts in the order of `meta.parts`."""
        temporary, key = _parse(dialog_id)
        if temporary:
            with self.temp.lock:
                d = self._temp(key)
                head, meta, blobs = self._temp_head(d, busy), copy.deepcopy(d.meta), [e.blob for e in d.parts]
            note = self.note
            decoded = [sp.decode_sized(blob) for blob in blobs]
        else:
            project = self._need(write=False)
            with dialect.read_tx(project.engine) as conn:
                row = self._row(conn, key)
            meta = sp.check_meta(row.meta)
            head, note = self._head(row, busy, meta), None
            decoded = [(part, raw) for _blob, part, raw in sp.load_parts(self._store(), meta["parts"], meta["bytes"])]
        messages: list[Any] = []
        items: list[Any] = []
        for part, _raw in decoded:
            messages += part.get("messages") or []
            items += part.get("items") or []
        return Loaded(head, messages, items, sp.public_pending(meta), note, meta)

    def get(self, dialog_id: str, busy: Collection[str] = ()) -> dict[str, Any]:
        """`GET /dialogs/{id}`: `Dialog` of the contract."""
        return self.read(dialog_id, busy).as_dialog()

    # --- list -----------------------------------------------------------------------------------

    def _entries(self) -> list[tuple[str, int, Any]]:
        """(last_at, order, the saved row or the temporary dialog) of the dialogs that have a part."""
        out: list[tuple[str, int, Any]] = []
        if self.project is not None:  # «только просмотр» lists what the project holds, too
            with dialect.read_tx(self.project.engine) as conn:
                rows = conn.execute(
                    sa.select(objects.c.id, objects.c.name, objects.c.meta).where(
                        objects.c.kind == KIND, objects.c.state == "ready"
                    )
                ).all()
            for r in rows:
                meta, broken = self._listed_meta(r)
                if not sp.is_empty(meta):
                    out.append((meta["last_at"], int(r.id), (r, meta, broken)))
        if not self.persistent:
            out += [(d.meta["last_at"], d.seq, d) for d in self.temp.all() if not sp.is_empty(d.meta)]
        return out

    @staticmethod
    def _listed_meta(row: Any) -> tuple[dict[str, Any], bool]:
        """The meta of a listed dialog; a forged one (a `.bfggis` of another person) is shown as a dialog in the state
        `error` that can be deleted, it does not break the list."""
        try:
            return sp.check_meta(row.meta), False
        except ApiError:
            meta = sp.new_meta(f"broken-{row.id}", "1970-01-01T00:00:00Z")
            meta.update(state="error", questions=1)
            return meta, True

    def list(self, offset: int = 0, limit: int = 50, busy: Collection[str] = ()) -> dict[str, Any]:
        """`GET /dialogs`: {total, items [DialogHead]}, the newest question first; with a project that keeps the new
        dialogs — those of the project, otherwise the temporary ones (and those the read-only project holds)."""
        entries = sorted(self._entries(), key=lambda e: (e[0], e[1]), reverse=True)
        page = entries[max(offset, 0) : max(offset, 0) + max(limit, 0)]
        heads = [
            self._temp_head(e[2], busy) if isinstance(e[2], TempDialog) else self._head(e[2][0], busy, e[2][1])
            for e in page
        ]
        return {"total": len(entries), "items": heads}

    def last(self, busy: Collection[str] = ()) -> dict[str, Any] | None:
        """`GET /dialogs/last`: the dialog of the latest question that has a part, or None."""
        items = self.list(0, 1, busy)["items"]
        return items[0] if items else None  # type: ignore[no-any-return]

    # --- rename, delete -------------------------------------------------------------------------

    def rename(self, dialog_id: str, title: Any) -> dict[str, Any]:
        """`PATCH`: 1…200 signs (422 `bad_param {param: "title"}`)."""
        title = sp.clean_title(title)
        temporary, key = _parse(dialog_id)
        if temporary:
            with self.temp.lock:
                d = self._temp(key)
                d.title = title
                return self._temp_head(d)
        project = self._need(write=True)
        with dialect.write_tx(project.engine) as conn:
            self._row(conn, key)
            conn.execute(
                sa.update(objects)
                .where(objects.c.id == key)
                .values(
                    name=title,
                    name_key=dialect.name_key(title),
                    version=objects.c.version + 1,
                    updated_at=dialect.utcnow(),
                )  # fmt: skip
            )
            row = self._row(conn, key)
        return self._head(row)

    def delete(self, dialog_id: str) -> None:
        """`DELETE`: the object and its references to the parts go in one transaction (the files — with the next
        collection of the blobs)."""
        temporary, key = _parse(dialog_id)
        if temporary:
            if not self.temp.remove(key):
                raise sp.not_found()
            return
        project = self._need(write=True)
        with dialect.write_tx(project.engine) as conn:
            self._row(conn, key)
            drop_owner(conn, OWNER, key)
            conn.execute(sa.delete(objects).where(objects.c.id == key))

    # --- change of the project ------------------------------------------------------------------

    def copy_to(self, dialog_id: str, dst: Dialogs, *, remove_source: bool | None = None) -> dict[str, Any]:
        """The dialog into `dst`: a project that keeps it, or the temporary ones. A temporary dialog is moved
        (№ 130: the project was opened meanwhile; the same `uid`), a saved one is copied and the source stays by
        default (решение 31: a change of the project — the old one keeps the version before; a new `uid`). The parts
        are copied as they are (the same blobs). Returns the head in `dst`."""
        temporary, key = _parse(dialog_id)
        if temporary:
            with self.temp.lock:
                d = self._temp(key)
                title, meta, parts = d.title, copy.deepcopy(d.meta), list(d.parts)
        else:
            self._need(write=False)
            store = self._store()
            with dialect.read_tx(self.project.engine) as conn:  # type: ignore[union-attr]
                row = self._row(conn, key)
            meta = sp.check_meta(row.meta)
            title = str(row.name)
            parts = [Encoded(blob, raw) for blob, _part, raw in sp.load_parts(store, meta["parts"], meta["bytes"])]
            meta["uid"] = sp.new_meta()["uid"]
        meta["parts"] = [hashlib.sha256(e.blob).hexdigest() for e in parts]
        meta["bytes"] = sum(e.raw for e in parts)
        head = dst._adopt(title, meta, parts)
        if remove_source if remove_source is not None else temporary:
            self.delete(dialog_id)
        return head

    def _adopt(self, title: str, meta: dict[str, Any], parts: Sequence[Encoded]) -> dict[str, Any]:
        if not self.persistent:
            d, _gone = self.temp.add(title, meta, parts)
            return self._temp_head(d)
        project = self._need(write=True)
        store = self._store()
        staged = [store.stage_bytes(e.blob) for e in parts]
        now = dialect.utcnow()
        try:
            with dialect.write_tx(project.engine) as conn:
                ident = self._insert(conn, title, meta, now)
                for st in staged:
                    store.commit_staged(conn, st, media_type=MEDIA, owner_kind=OWNER, owner_id=ident, role=ROLE)
                row = self._row(conn, ident)
        except BaseException:
            for st in staged:
                st[0].unlink(missing_ok=True)
            raise
        return self._head(row)
