"""Edit actions of the «Таблица» panel: a cell, a new row, a deletion, undo (ТЗ 7.10).

Each action runs in one write transaction, returns the new revision and the changes, and patches the cached
snapshot of the table (no rebuild of a 5 million-row table after one cell).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_rows, dh_surveys, dh_tables, edit_log

from . import edit as e
from .snapshot import Snapshot, snapshots, table_key


def _patch(changes: list[e.Change], names: dict[int, str]) -> Any:
    """Applies row changes to a snapshot (values by column key; inserts appended; deletions hidden)."""

    def apply(snap: Snapshot) -> None:
        snap.hole_names.update(names)
        snap.data.pop("hole", None)  # the hole text column is derived again on use
        for c in changes:
            if c.action == "insert":
                snap.ids = np.append(snap.ids, c.row_id)
                snap.hole_ids = np.append(snap.hole_ids, c.hole_id or 0)
                snap.alive = np.append(snap.alive, True)
                for k, arr in list(snap.data.items()):
                    v = c.changes.get(k, [None, None])[1]
                    snap.data[k] = np.append(arr, np.nan if v is None and arr.dtype.kind == "f" else v)
                continue
            i = snap.position(c.row_id)
            if i is None:
                continue
            if c.action == "delete":
                snap.alive[i] = False
                continue
            if c.hole_id is not None:
                snap.hole_ids[i] = c.hole_id
            for k, (_before, after) in c.changes.items():
                if k in snap.data:
                    arr = snap.data[k]
                    arr[i] = np.nan if after is None and arr.dtype.kind == "f" else after

    return apply


def _finish(
    project: Any, t: e.Target, old_rev: int, new_rev: int, changes: list[e.Change], names: dict[int, str]
) -> None:
    old = (project.uid, t.set_id, t.table, old_rev)
    new = (project.uid, t.set_id, t.table, new_rev)
    if any(k.startswith("a:") for c in changes for k in c.changes) and t.table in ("collar", "survey"):
        snapshots.forget(t.set_id)  # a new free column: its kind and order come from a fresh build
    else:
        snapshots.patched(old, new, _patch(changes, names))


def _rename_hole(conn: sa.Connection, t: e.Target, hole_id: int, name: str) -> None:
    taken = conn.execute(
        sa.select(dh_holes.c.id).where(dh_holes.c.set_id == t.set_id, dh_holes.c.name == name, dh_holes.c.id != hole_id)
    ).first()
    if taken is not None:
        raise ApiError(409, "hole_exists", f"Скважина «{name}» уже есть в наборе.", "Введите другой номер скважины.")
    conn.execute(sa.update(dh_holes).where(dh_holes.c.id == hole_id).values(name=name))


def update_cell(
    project: Any, set_id: int, table: str, row_id: int, key: str, value: Any, expected: int | None
) -> dict[str, Any]:
    with dialect.write_tx(project.engine) as conn:
        t = e.target(conn, set_id, table)
        old_rev = e.revision(conn, t)
        found = e.read_row(conn, t, row_id)
        if found is None:
            raise ApiError(404, "row_not_found", "Строка не найдена: её удалили.", "Обновите таблицу.")
        vals, hole_id, hole = found
        names: dict[int, str] = {}
        if key == "hole":
            name = e._hole_text(value)
            if name == hole:
                return {"rev": old_rev, "changed": False}
            if t.table == "collar":
                _rename_hole(conn, t, hole_id, name)
                names[hole_id] = name
                new_rev = e.bump(conn, t, expected)
                change = e.Change("update", row_id, name, hole_id, {"hole": [hole, name]})
                e.journal(conn, t, e.new_group(conn), [change], e.author())
                snapshots.forget(set_id)  # every table of the set shows the hole name
                return {"rev": new_rev, "changed": True}
            hole_id = e.hole_by_name(conn, set_id, name, create=True) or hole_id
            names[hole_id] = name
            vals = {**vals, "hole": name}
        else:
            new = e.clean(t, key, value)
            if vals.get(key) == new:
                return {"rev": old_rev, "changed": False}
            vals = {**vals, key: new}
        e.check_row(t, vals)
        before = found[0]
        e.write_row(conn, t, row_id, hole_id, vals)
        if t.table not in ("collar", "survey") and key == "hole":
            _prune(conn, set_id)
        new_rev = e.bump(conn, t, expected)
        change = e.Change("update", row_id, vals["hole"], hole_id, {key: [before.get(key), vals.get(key)]})
        e.journal(conn, t, e.new_group(conn), [change], e.author())
    _finish(project, t, old_rev, new_rev, [change], names)
    return {"rev": new_rev, "changed": True, "value": vals.get(key)}


def insert_row(project: Any, set_id: int, table: str, values: dict[str, Any], expected: int | None) -> dict[str, Any]:
    with dialect.write_tx(project.engine) as conn:
        t = e.target(conn, set_id, table)
        old_rev = e.revision(conn, t)
        name = e._hole_text(values.get("hole"))
        vals: dict[str, Any] = {"hole": name}
        for k, v in values.items():
            if k != "hole":
                vals[k] = e.clean(t, k, v)
        e.check_row(t, vals)
        if t.table == "collar":
            hole_id = e.unique_collar(conn, t, name, None) or e.hole_by_name(conn, set_id, name, create=True)
        else:
            hole_id = e.hole_by_name(conn, set_id, name, create=True)
        assert hole_id is not None
        row_id = e.write_row(conn, t, None, hole_id, vals)
        new_rev = e.bump(conn, t, expected, rows_delta=1)
        change = e.Change("insert", row_id, name, hole_id, {k: [None, v] for k, v in vals.items() if k != "hole"})
        e.journal(conn, t, e.new_group(conn), [change], e.author())
    if t.table == "collar":
        snapshots.forget(set_id)
    else:
        _finish(project, t, old_rev, new_rev, [change], {hole_id: name})
    return {"rev": new_rev, "id": row_id}


def _prune(conn: sa.Connection, set_id: int) -> None:
    from .store import prune_empty_holes

    prune_empty_holes(conn, set_id)


def delete_row(project: Any, set_id: int, table: str, row_id: int, expected: int | None) -> dict[str, Any]:
    with dialect.write_tx(project.engine) as conn:
        t = e.target(conn, set_id, table)
        old_rev = e.revision(conn, t)
        found = e.read_row(conn, t, row_id)
        if found is None:
            raise ApiError(404, "row_not_found", "Строка не найдена: её уже удалили.", "Обновите таблицу.")
        vals, hole_id, hole = found
        if t.table == "collar":  # the hole stays with its other data; it loses the collar
            conn.execute(
                sa.update(dh_holes)
                .where(dh_holes.c.id == hole_id)
                .values(x=None, y=None, z=None, td=None, azimuth=None, dip=None, has_collar=False, attrs=None)
            )
        elif t.table == "survey":
            conn.execute(sa.delete(dh_surveys).where(dh_surveys.c.id == row_id))
        else:
            conn.execute(sa.delete(dh_rows).where(dh_rows.c.id == row_id))
        _prune(conn, set_id)
        new_rev = e.bump(conn, t, expected, rows_delta=-1)
        change = e.Change(
            "delete", row_id, hole, hole_id, {k: [v, None] for k, v in vals.items() if k != "hole" and v is not None}
        )
        e.journal(conn, t, e.new_group(conn), [change], e.author())
    _finish(project, t, old_rev, new_rev, [change], {})
    return {"rev": new_rev}


def history(project: Any, set_id: int, *, table: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """The journal of a set, newest first (ТЗ 7.10 «Журнал правок»)."""
    with dialect.read_tx(project.engine) as conn:
        q = sa.select(edit_log).where(edit_log.c.scope_kind == "object", edit_log.c.scope_id == set_id)
        rows = conn.execute(q.order_by(edit_log.c.id.desc()).limit(max(1, min(limit, 1000)))).mappings().all()
    out = []
    for r in rows:
        ref = r["row_ref"] or {}
        if table is not None and str(ref.get("table")) != table:
            continue
        out.append(
            {
                "id": r["id"],
                "group": r["group_id"],
                "action": r["action"],
                "table": ref.get("table"),
                "row": ref.get("row"),
                "hole": ref.get("hole"),
                "changes": r["changes"],
                "undone": r["undone"],
                "author": r["author"],
                "at": r["created_at"].isoformat(),
            }
        )
    return out


def undo(project: Any, edit_id: int) -> dict[str, Any]:
    """Undo one journal record (or its whole group: a find-and-replace is undone at once)."""
    with dialect.read_tx(project.engine) as conn:
        rec = conn.execute(sa.select(edit_log).where(edit_log.c.id == edit_id)).mappings().first()
    if rec is None or rec["scope_kind"] != "object":
        raise ApiError(404, "edit_not_found", "Правка не найдена.", "Обновите журнал правок.")
    if rec["undone"]:
        raise ApiError(409, "already_undone", "Эта правка уже отменена.", "Обновите журнал правок.")
    set_id = int(rec["scope_id"])
    table = str((rec["row_ref"] or {}).get("table"))
    with dialect.write_tx(project.engine) as conn:
        t = e.target(conn, set_id, table)
        old_rev = e.revision(conn, t)
        group = (
            conn.execute(
                sa.select(edit_log)
                .where(edit_log.c.group_id == rec["group_id"], edit_log.c.undone.is_(False))
                .order_by(edit_log.c.id.desc())
            )
            .mappings()
            .all()
        )
        done: list[e.Change] = []
        delta = 0
        for g in group:
            ref = g["row_ref"] or {}
            row_id = int(ref.get("row") or 0)
            changes = g["changes"] or {}
            if g["action"] in ("column", "append"):
                _undo_structure(conn, t, g["action"], ref, changes)
                continue
            if g["action"] == "insert":
                found = e.read_row(conn, t, row_id)
                if found is not None:
                    _delete_raw(conn, t, row_id, found[1])
                    done.append(e.Change("delete", row_id, ref.get("hole"), found[1]))
                    delta -= 1
            elif g["action"] == "delete":
                name = str(ref.get("hole"))
                vals = {"hole": name, **{k: v[0] for k, v in changes.items()}}
                if t.table == "collar":
                    hole_id = e.unique_collar(conn, t, name, None) or e.hole_by_name(conn, set_id, name, create=True)
                else:
                    hole_id = e.hole_by_name(conn, set_id, name, create=True)
                assert hole_id is not None
                new_id = e.write_row(conn, t, None, hole_id, vals)
                done.append(e.Change("insert", new_id, name, hole_id, {k: [None, v[0]] for k, v in changes.items()}))
                delta += 1
            else:
                found = e.read_row(conn, t, row_id)
                if found is None:
                    raise ApiError(
                        409, "row_gone", "Строку этой правки удалили позже.", "Сначала отмените удаление строки."
                    )
                vals, hole_id, hole = found
                if "hole" in changes:
                    before = changes["hole"][0]
                    if t.table == "collar":
                        _rename_hole(conn, t, hole_id, before)
                    else:
                        hole_id = e.hole_by_name(conn, set_id, before, create=True) or hole_id
                    vals = {**vals, "hole": before}
                for k, (b, _a) in changes.items():
                    if k != "hole":
                        vals[k] = b
                e.check_row(t, vals)
                e.write_row(conn, t, row_id, hole_id, vals)
                done.append(
                    e.Change("update", row_id, vals["hole"], hole_id, {k: [v[1], v[0]] for k, v in changes.items()})
                )
        _prune(conn, set_id)
        conn.execute(sa.update(edit_log).where(edit_log.c.id.in_([g["id"] for g in group])).values(undone=True))
        new_rev = e.bump(conn, t, None, rows_delta=delta)
    if t.table == "collar" or any(g["action"] in ("column", "append") for g in group):
        snapshots.forget(set_id)
    else:
        _finish(project, t, old_rev, new_rev, done, {})
    return {"rev": new_rev, "undone": [g["id"] for g in group], "set_id": set_id, "table": table}


def _delete_raw(conn: sa.Connection, t: e.Target, row_id: int, hole_id: int) -> None:
    if t.table == "collar":
        conn.execute(
            sa.update(dh_holes)
            .where(dh_holes.c.id == hole_id)
            .values(x=None, y=None, z=None, td=None, azimuth=None, dip=None, has_collar=False, attrs=None)
        )
    elif t.table == "survey":
        conn.execute(sa.delete(dh_surveys).where(dh_surveys.c.id == row_id))
    else:
        conn.execute(sa.delete(dh_rows).where(dh_rows.c.id == row_id))


def current(project: Any, set_id: int, table: str) -> tuple[Any, ...]:
    with dialect.read_tx(project.engine) as conn:
        key = table_key(conn, project.uid, set_id, table)
    if key is None:
        raise ApiError(404, "table_not_found", "Таблица не найдена.", "Обновите дерево проекта.")
    return key


def _undo_structure(
    conn: sa.Connection, t: e.Target, action: str, ref: dict[str, Any], changes: dict[str, Any]
) -> None:
    """Undo of a tool: a column added → hidden, deleted → back, renamed → old name; appended rows → deleted."""
    from bf_core.db.schema import dh_columns

    if action == "append":
        first, last = ref.get("rows") or [0, -1]
        conn.execute(sa.delete(dh_rows).where(dh_rows.c.table_id == int(t.table), dh_rows.c.id.between(first, last)))
        conn.execute(
            sa.update(dh_tables)
            .where(dh_tables.c.id == int(t.table))
            .values(
                row_count=sa.select(sa.func.count())
                .select_from(dh_rows)
                .where(dh_rows.c.table_id == int(t.table))
                .scalar_subquery()
            )
        )
        return
    key = str(ref.get("column"))
    if key.startswith("a:"):  # a free column of collars or surveys: the attribute leaves every row
        from .snapshot import build_collars, build_surveys
        from .tools import set_column

        snap = (build_collars if t.table == "collar" else build_surveys)(conn, t.set_id, ())
        idx = np.arange(len(snap.ids))
        if changes.get("column", [None, None])[0] is None:
            set_column(conn, t, snap, key, idx, [None] * len(idx))
        return
    col_id = int(key[1:])
    if "name" in changes:
        conn.execute(sa.update(dh_columns).where(dh_columns.c.id == col_id).values(name=changes["name"][0]))
    elif changes.get("column", [None, None])[0] is None:
        conn.execute(sa.update(dh_columns).where(dh_columns.c.id == col_id).values(deleted=True))
    else:
        conn.execute(sa.update(dh_columns).where(dh_columns.c.id == col_id).values(deleted=False))
