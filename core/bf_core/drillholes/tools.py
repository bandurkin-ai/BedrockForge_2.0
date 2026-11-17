"""Tools of the «Таблица» panel (ТЗ 7.34 п. 22, 31–36): find and replace, computed fields, columns (add,
rename, delete), appending a table to another, merging columns by a key, code lists and the check by them.

All of them write through `set_column` (whole columns, in portions through the driver) and record one
journal group, so one «Отменить» undoes the whole action. Column changes are journal records with
action `column` (undo: the column is hidden again or comes back); appended rows — action `append` with
their id range (undo deletes them).
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_rows, dh_surveys, edit_log, settings

from . import edit as e
from .expr import ExprError, evaluate_filter, evaluate_value
from .query import resolver
from .snapshot import Snapshot

PORTION = 50_000


# --- writing whole columns ---------------------------------------------------------------------


def _slots(conn: sa.Connection, table_id: int, kind: str) -> int:
    """Next free slot of a kind (deleted columns keep theirs: their values stay in the vectors)."""
    used = conn.execute(
        sa.select(sa.func.max(dh_columns.c.slot)).where(dh_columns.c.table_id == table_id, dh_columns.c.kind == kind)
    ).scalar()
    return 0 if used is None else int(used) + 1


def set_column(conn: sa.Connection, t: e.Target, snap: Snapshot, key: str, idx: np.ndarray, values: list[Any]) -> None:
    """Writes `values` into column `key` of the rows at snapshot positions `idx`."""
    if not len(idx):
        return
    ids = snap.ids[idx]
    col = t.col(key)
    if t.table in ("collar", "survey"):
        tbl = dh_holes if t.table == "collar" else dh_surveys
        for row_id, v in zip(ids.tolist(), values, strict=True):
            if key.startswith("a:"):
                attrs = conn.execute(sa.select(tbl.c.attrs).where(tbl.c.id == row_id)).scalar() or {}
                attrs = {**attrs}
                if v in (None, ""):
                    attrs.pop(key[2:], None)
                else:
                    attrs[key[2:]] = v
                conn.execute(sa.update(tbl).where(tbl.c.id == row_id).values(attrs=attrs or None))
            else:
                conn.execute(sa.update(tbl).where(tbl.c.id == row_id).values({key: v}))
        return
    if key in ("from", "to"):
        dialect.bulk_update(
            conn, dh_rows, [f"{key}_m"], "id", [(v, i) for v, i in zip(values, ids.tolist(), strict=True)]
        )
        return
    assert col.slot is not None
    for start in range(0, len(ids), PORTION):
        part = ids[start : start + PORTION].tolist()
        vals = values[start : start + PORTION]
        q = sa.select(dh_rows.c.id, dh_rows.c.num if col.kind == "number" else dh_rows.c.txt).where(
            dh_rows.c.id.in_(part)
        )
        current = {r[0]: r[1] for r in conn.execute(q)}
        rows: list[tuple[Any, int]] = []
        for row_id, v in zip(part, vals, strict=True):
            old = current.get(row_id)
            if col.kind == "number":
                vec = np.full(col.slot + 1, np.nan) if old is None else np.frombuffer(old, dtype="<f8").copy()
                if len(vec) <= col.slot:
                    vec = np.concatenate([vec, np.full(col.slot + 1 - len(vec), np.nan)])
                vec[col.slot] = np.nan if v is None else float(v)
                n = len(vec)
                while n and math.isnan(vec[n - 1]):
                    n -= 1
                rows.append((vec[:n].tobytes() if n else None, row_id))
            else:
                txt = json.loads(old) if isinstance(old, str) else dict(old or {})
                if v in (None, ""):
                    txt.pop(str(col.slot), None)
                else:
                    txt[str(col.slot)] = str(v)
                rows.append((json.dumps(txt, ensure_ascii=False) if txt else None, row_id))
        dialect.bulk_update(conn, dh_rows, ["num" if col.kind == "number" else "txt"], "id", rows)


def _record(
    conn: sa.Connection, t: e.Target, group: int, action: str, ref: dict[str, Any], changes: dict[str, Any]
) -> None:
    conn.execute(
        sa.insert(edit_log).values(
            scope_kind="object",
            scope_id=t.set_id,
            group_id=group,
            action=action,
            row_ref={"table": t.table, **ref},
            changes=changes,
            undone=False,
            author=e.author(),
            created_at=dialect.utcnow(),
        )
    )


def _cells(
    conn: sa.Connection, t: e.Target, group: int, snap: Snapshot, key: str, idx: np.ndarray, new: list[Any]
) -> int:
    """Writes changed cells and journals each (before → after); returns the count."""
    old = snap.values(key)[idx]
    changed = [
        (int(i), _plain(o), n)
        for i, o, n in zip(idx.tolist(), old.tolist(), new, strict=True)
        if not _same(_plain(o), n)
    ]
    if not changed:
        return 0
    pos = np.array([c[0] for c in changed], dtype=np.int64)
    set_column(conn, t, snap, key, pos, [c[2] for c in changed])
    now = dialect.utcnow()
    who = e.author()
    names = snap.hole_names
    records = [
        {
            "scope_kind": "object",
            "scope_id": t.set_id,
            "group_id": group,
            "action": "update",
            "row_ref": {"table": t.table, "row": int(snap.ids[i]), "hole": names.get(int(snap.hole_ids[i]))},
            "changes": {key: [o, n]},
            "undone": False,
            "author": who,
            "created_at": now,
        }
        for i, o, n in changed
    ]
    for start in range(0, len(records), PORTION):
        conn.execute(sa.insert(edit_log), records[start : start + PORTION])
    return len(changed)


def _plain(v: Any) -> Any:
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _same(a: Any, b: Any) -> bool:
    return a == b or (a is None and b in (None, ""))


def _rows(snap: Snapshot, hole: int | None, where: str | None) -> np.ndarray:
    m = snap.alive.copy()
    if hole is not None:
        m &= snap.hole_ids == hole
    if where:
        try:
            m &= evaluate_filter(where, resolver(snap), len(snap.ids))
        except ExprError as exc:
            raise ApiError(422, "bad_expression", str(exc), "Проверьте выражение фильтра.") from exc
    return np.flatnonzero(m)


# --- find and replace (п. 22) ------------------------------------------------------------------


def replace(
    conn: sa.Connection,
    t: e.Target,
    snap: Snapshot,
    column: str,
    find: str,
    to: str,
    *,
    whole: bool = False,
    case: bool = False,
    hole: int | None = None,
    where: str | None = None,
) -> int:
    col = t.col(column)
    if col.key == "hole":
        raise ApiError(
            422, "not_allowed", "Номер скважины меняется в устьях.", "Переименуйте скважину в таблице устьев."
        )
    idx = _rows(snap, hole, where)
    values = snap.values(col.key)[idx]
    new: list[Any] = []
    if col.kind == "number":
        target = e.parse_number(find, col.name)
        value = e.parse_number(to, col.name)
        hit = np.isnan(values) if target is None else values == target
        idx, new = idx[hit], [value] * int(hit.sum())
    else:
        keep, out = [], []
        for i, v in zip(idx.tolist(), values.tolist(), strict=True):
            text = "" if v is None else str(v)
            if whole:
                ok = text.casefold() == find.casefold() if not case else text == find
                r = to if ok else None
            else:
                if not find:
                    raise ApiError(422, "find_empty", "Не задано, что искать.", "Введите текст для поиска.")
                if case:
                    ok = find in text
                    r = text.replace(find, to) if ok else None
                else:
                    import re

                    pattern = re.compile(re.escape(find), re.IGNORECASE)
                    ok = bool(pattern.search(text))
                    r = pattern.sub(lambda _m: to, text) if ok else None
            if ok:
                keep.append(i)
                out.append(e.parse_text(r))
        idx, new = np.array(keep, dtype=np.int64), out
    for c in t.columns:
        if c.key == col.key and c.required and any(v is None for v in new):
            raise ApiError(422, "required_field", f"«{c.name}» — обязательное поле.", "Замените на непустое значение.")
    return _cells(conn, t, e.new_group(conn), snap, col.key, idx, new)


# --- columns (п. 31, 32) -----------------------------------------------------------------------


def add_column(
    conn: sa.Connection,
    t: e.Target,
    snap: Snapshot,
    name: str,
    kind: str,
    *,
    unit: str | None = None,
    expression: str | None = None,
    group: int | None = None,
) -> dict[str, Any]:
    name = " ".join(name.split())
    if not name:
        raise ApiError(422, "name_empty", "Название колонки не может быть пустым.", "Введите название.")
    if any(c.name.casefold() == name.casefold() for c in t.columns):
        raise ApiError(409, "column_exists", f"Колонка «{name}» уже есть.", "Введите другое название.")
    values: np.ndarray | None = None
    if expression:
        try:
            kind, values = evaluate_value(expression, resolver(snap), len(snap.ids))
        except ExprError as exc:
            raise ApiError(422, "bad_expression", str(exc), "Пример: (TO - FROM) * AU.") from exc
    if kind not in ("number", "text"):
        raise ApiError(422, "bad_kind", "Тип колонки — число или текст.", "Выберите тип.")
    group = group or e.new_group(conn)
    if t.table in ("collar", "survey"):
        key = f"a:{name}"
    else:
        pos = len(t.columns)
        res = conn.execute(
            sa.insert(dh_columns).values(
                table_id=int(t.table),
                name=name,
                kind=kind,
                unit=unit,
                slot=_slots(conn, int(t.table), kind),
                role="value",
                deleted=False,
                position=pos,
            )
        )
        pk = res.inserted_primary_key
        assert pk is not None
        key = f"c{int(pk[0])}"
    t.columns.append(
        e.ColumnInfo(key, name, kind, unit=unit, slot=None if key.startswith("a:") else _slot_of(conn, key))
    )
    _record(conn, t, group, "column", {"column": key}, {"column": [None, name], "kind": kind, "expression": expression})
    if values is not None:
        idx = np.flatnonzero(snap.alive)
        vals = [None if (kind == "number" and math.isnan(v)) else v for v in values[idx].tolist()]
        set_column(conn, t, snap, key, idx, vals)
    return {"key": key, "name": name, "kind": kind}


def _slot_of(conn: sa.Connection, key: str) -> int:
    return int(conn.execute(sa.select(dh_columns.c.slot).where(dh_columns.c.id == int(key[1:]))).scalar_one())


def rename_column(conn: sa.Connection, t: e.Target, column: str, name: str) -> None:
    col = t.col(column)
    name = " ".join(name.split())
    if col.role == "fixed" or col.key.startswith("a:"):
        raise ApiError(
            422, "not_allowed", "Эту колонку нельзя переименовать.", "Переименовываются колонки интервальных таблиц."
        )
    if not name or any(c.name.casefold() == name.casefold() and c.key != col.key for c in t.columns):
        raise ApiError(
            409, "column_exists", f"Колонка «{name}» уже есть или название пустое.", "Введите другое название."
        )
    conn.execute(sa.update(dh_columns).where(dh_columns.c.id == int(col.key[1:])).values(name=name))
    _record(conn, t, e.new_group(conn), "column", {"column": col.key}, {"name": [col.name, name]})


def delete_column(conn: sa.Connection, t: e.Target, column: str) -> None:
    """The slot is marked deleted (values stay in the vectors: undo brings the column back, ADR 0003)."""
    col = t.col(column)
    if col.role == "fixed" or col.key.startswith("a:"):
        raise ApiError(422, "not_allowed", "Эту колонку нельзя удалить.", "Удаляются колонки интервальных таблиц.")
    conn.execute(sa.update(dh_columns).where(dh_columns.c.id == int(col.key[1:])).values(deleted=True))
    _record(conn, t, e.new_group(conn), "column", {"column": col.key}, {"column": [col.name, None]})


# --- appending and merging (п. 33, 34) ---------------------------------------------------------


def append_table(conn: sa.Connection, t: e.Target, src_t: e.Target, src: Snapshot) -> dict[str, Any]:
    """Rows of another table of the set go to the end of this one; columns are matched by name, missing ones
    are added (same kind)."""
    if t.table in ("collar", "survey") or src_t.table in ("collar", "survey") or t.table == src_t.table:
        raise ApiError(422, "not_allowed", "Объединяются интервальные таблицы набора.", "Выберите другую таблицу.")
    group = e.new_group(conn)
    mapping: dict[str, str] = {}
    for c in src_t.columns:
        if c.role == "fixed":
            continue
        own = next((x for x in t.columns if x.name.casefold() == c.name.casefold()), None)
        if own is None:
            own_key = add_column(conn, t, src, c.name, c.kind, unit=c.unit, group=group)["key"]
            own = t.col(own_key)
        elif own.kind != c.kind:
            raise ApiError(
                422, "kind_mismatch", f"Колонка «{c.name}» — разного типа в таблицах.", "Приведите типы колонок."
            )
        mapping[c.key] = own.key
    idx = np.flatnonzero(src.alive)
    first = int(conn.execute(sa.select(sa.func.coalesce(sa.func.max(dh_rows.c.id), 0))).scalar_one()) + 1
    width = max([c.slot for c in t.columns if c.kind == "number" and c.slot is not None], default=-1) + 1
    rows = []
    for i in idx.tolist():
        nums: list[float | None] = [None] * width
        txt: dict[str, str] = {}
        for sk, ok in mapping.items():
            c = t.col(ok)
            v = _plain(src.data[sk][i])
            if v is None or c.slot is None:
                continue
            if c.kind == "number":
                nums[c.slot] = float(v)
            else:
                txt[str(c.slot)] = str(v)
        f, to = _plain(src.data["from"][i]), _plain(src.data.get("to", src.data["from"])[i])
        rows.append(
            (
                int(t.table),
                int(src.hole_ids[i]),
                f,
                to,
                e.pack(nums),
                json.dumps(txt, ensure_ascii=False) if txt else None,
            )
        )
    for start in range(0, len(rows), PORTION):
        dialect.bulk_insert(
            conn, dh_rows, ("table_id", "hole_id", "from_m", "to_m", "num", "txt"), rows[start : start + PORTION]
        )
    last = int(conn.execute(sa.select(sa.func.coalesce(sa.func.max(dh_rows.c.id), 0))).scalar_one())
    _record(conn, t, group, "append", {"rows": [first, last], "from_table": src_t.table}, {"rows": len(rows)})
    return {"rows": len(rows), "group": group}


def merge_by_key(
    conn: sa.Connection, t: e.Target, snap: Snapshot, src_t: e.Target, src: Snapshot, key: list[str], columns: list[str]
) -> dict[str, Any]:
    """Columns of another table go into this one where the key matches (hole + chosen fields, e.g. from/to
    or the sample number); new columns are added; rows without a match stay empty."""
    if not columns:
        raise ApiError(422, "columns_empty", "Не выбраны колонки для переноса.", "Выберите колонки.")
    for k in key:
        t.col(k), src_t.col(k)

    def keys_of(s: Snapshot) -> list[tuple[Any, ...]]:
        parts = [s.hole_texts()] + [s.values(k) for k in key if k != "hole"]
        return [tuple(_plain(p[i]) for p in parts) for i in range(len(s.ids))]

    lookup: dict[tuple[Any, ...], int] = {}
    dup = 0
    for i, kv in enumerate(keys_of(src)):
        if src.alive[i]:
            if kv in lookup:
                dup += 1
            lookup.setdefault(kv, i)
    if dup:
        raise ApiError(
            422, "key_not_unique", f"Ключ повторяется в исходной таблице: {dup} строк.", "Добавьте поля в ключ."
        )
    own = keys_of(snap)
    idx = np.array([i for i in range(len(snap.ids)) if snap.alive[i] and own[i] in lookup], dtype=np.int64)
    group = e.new_group(conn)
    added = []
    for ck in columns:
        c = src_t.col(ck)
        new = add_column(conn, t, snap, _free_name(t, c.name), c.kind, unit=c.unit, group=group)
        vals = [_plain(src.data[c.key][lookup[own[i]]]) for i in idx.tolist()]
        set_column(conn, t, snap, new["key"], idx, vals)
        added.append(new)
    return {"matched": int(len(idx)), "rows": int(snap.alive.sum()), "columns": added}


def _free_name(t: e.Target, name: str) -> str:
    taken = {c.name.casefold() for c in t.columns}
    if name.casefold() not in taken:
        return name
    k = 2
    while f"{name} ({k})".casefold() in taken:
        k += 1
    return f"{name} ({k})"


# --- code lists (п. 35, 36) ----------------------------------------------------------------------

CODES = "codes"  # settings scope: key = list name, value = {"items": [{"code", "title"}]}
CODE_LINKS = "code_links"  # settings scope: key = "<set>:<table>:<column>", value = list name


def code_lists(conn: sa.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        sa.select(settings.c.key, settings.c.value).where(settings.c.scope == CODES).order_by(settings.c.key)
    )
    return [{"name": r.key, "items": (r.value or {}).get("items", [])} for r in rows]


def save_code_list(conn: sa.Connection, name: str, items: list[dict[str, str]]) -> None:
    name = " ".join(name.split())
    if not name:
        raise ApiError(422, "name_empty", "Название справочника не может быть пустым.", "Введите название.")
    clean = []
    seen: set[str] = set()
    for it in items:
        code = str(it.get("code", "")).strip()
        if not code or code.casefold() in seen:
            continue
        seen.add(code.casefold())
        clean.append({"code": code, "title": str(it.get("title", "")).strip()})
    exists = conn.execute(
        sa.select(settings.c.version).where(settings.c.scope == CODES, settings.c.key == name)
    ).scalar()
    if exists is None:
        conn.execute(sa.insert(settings).values(scope=CODES, key=name, value={"items": clean}, version=1))
    else:
        conn.execute(
            sa.update(settings)
            .where(settings.c.scope == CODES, settings.c.key == name)
            .values(value={"items": clean}, version=settings.c.version + 1)
        )


def link_codes(conn: sa.Connection, t: e.Target, column: str, name: str | None) -> None:
    col = t.col(column)
    key = f"{t.set_id}:{t.table}:{col.key}"
    conn.execute(sa.delete(settings).where(settings.c.scope == CODE_LINKS, settings.c.key == key))
    if name:
        if (
            conn.execute(sa.select(settings.c.key).where(settings.c.scope == CODES, settings.c.key == name)).first()
            is None
        ):
            raise ApiError(404, "codes_not_found", f"Справочник «{name}» не найден.", "Создайте справочник.")
        conn.execute(sa.insert(settings).values(scope=CODE_LINKS, key=key, value=name, version=1))


def check_codes(conn: sa.Connection, t: e.Target, snap: Snapshot, column: str) -> dict[str, Any]:
    """Values not in the linked code list (ТЗ 7.34 п. 36): which, how many, and a filter that shows them."""
    col = t.col(column)
    name = conn.execute(
        sa.select(settings.c.value).where(
            settings.c.scope == CODE_LINKS, settings.c.key == f"{t.set_id}:{t.table}:{col.key}"
        )
    ).scalar()
    if not name:
        raise ApiError(409, "codes_not_linked", "К колонке не привязан справочник.", "Привяжите справочник кодов.")
    items: list[dict[str, Any]] = next((c["items"] for c in code_lists(conn) if c["name"] == name), [])
    allowed = {str(it["code"]).casefold() for it in items}
    counts: dict[str, int] = {}
    for v in snap.values(col.key)[snap.alive].tolist():
        text = None if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)
        if text is not None and text.casefold() not in allowed:
            counts[text] = counts.get(text, 0) + 1
    bad = sorted(counts.items(), key=lambda kv: -kv[1])
    codes = "; ".join(json.dumps(it["code"], ensure_ascii=False) for it in items)
    where = f"notnull([{col.name}]) и не inlist([{col.name}]; {codes})" if items else None
    return {
        "list": name,
        "bad": [{"value": v, "count": c} for v, c in bad],
        "rows": sum(counts.values()),
        "where": where,
    }
