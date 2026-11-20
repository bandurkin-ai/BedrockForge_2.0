"""Results of sample processing (ADR 0018 §2–3): the `dh_results` records, «устарел», deletion, revision marks.

A record keeps what the kernel ran with: `params`, `inputs`, `sha256`, `algo`, `report`. `inputs` holds no ids
(`.bfggis` renumbers ids and must not recompute anything):

    {"tables": [{"key": name_key, "name": "Пробы", "token": "<created>", "rev": 3, "fields": ["AU", "CU"]}],
     "set": {"version": 7} | null,          # collars and surveys: X, Y, Z, benches, coding by wireframes
     "meshes": [{"name": "Тело 1", "key": "<MeshState.cache_key>"}]}

«Устарел» is computed when a list is read: a table of the inputs is gone, replaced (another `token`) or
edited (another `rev`); the set version moved; a wireframe is gone or its geometry changed. Edit handlers
are not touched. A write of columns by an operation moves the marks (`shift_marks`) of the other results of
that table in the same transaction, unless the columns they read were overwritten.

Operations that make a table (TABLE_OPS) have `table_id` = that table (deleted with it, ON DELETE CASCADE);
operations that write columns in place have `table_id` = the table with the columns; `report["written"]`
lists the columns by table name key: `[{"table": name_key, "columns": ["AU_CUT", "AU_CUT_F"]}]` (the ore
intervals write ORE_ID, ORE_CLS into the samples next to their own table). Deleting a result removes its
table and hides its columns (a journal record `column`: «Отменить» in the journal brings them back).

No numpy here: the list of tables of a set (`drillholes/router.py`) reads the origins at every open.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_results, dh_tables, edit_log, objects

OP_TITLES = {
    "length": "Композиты по длине",
    "domain": "Композиты по доменам",
    "bench": "Композиты по уступам",
    "coding": "Кодирование каркасами",
    "merge": "Объединение таблиц",
    "coords": "Координаты проб",
    "topcut": "Урезка",
    "equivalent": "Эквивалент металла",
    "declus": "Декластеризация",
    "ore": "Рудные интервалы",
    "conditions": "Анализ кондиций",
}
#: Operations whose result is a new table of the set (ADR 0018 §3); the others write columns or nothing.
TABLE_OPS = frozenset({"length", "domain", "bench", "coding", "merge", "coords", "ore"})


def _token(created: Any) -> str:
    """The identity of a table besides its name — the same token as the snapshot keys (`snapshot._token`)."""
    return "0" if created is None else f"{int(created.timestamp() * 1_000_000):x}"


# --- inputs ------------------------------------------------------------------------------------


def table_input(conn: sa.Connection, table_id: int, fields: Iterable[str]) -> dict[str, Any]:
    row = conn.execute(
        sa.select(dh_tables.c.name, dh_tables.c.name_key, dh_tables.c.rev, dh_tables.c.created_at).where(
            dh_tables.c.id == table_id
        )
    ).one()
    return {"key": row.name_key, "name": row.name, "token": _token(row.created_at), "rev": int(row.rev),
            "fields": list(fields)}  # fmt: skip


def set_input(conn: sa.Connection, set_id: int) -> dict[str, Any]:
    version = conn.execute(sa.select(objects.c.version).where(objects.c.id == set_id)).scalar_one()
    return {"version": int(version)}


def mesh_input(conn: sa.Connection, wireframe_id: int) -> dict[str, Any]:
    from bf_core.viz.meshes import mesh_state

    st = mesh_state(conn, wireframe_id)
    return {"name": st.name, "key": st.cache_key()}


def make_inputs(
    conn: sa.Connection,
    set_id: int,
    tables: Iterable[tuple[int, Iterable[str]]],
    *,
    trajectories: bool = False,
    meshes: Iterable[int] = (),
) -> dict[str, Any]:
    """`inputs` of a result: (table id, fields read) pairs; the set version when collars or surveys matter."""
    return {
        "tables": [table_input(conn, tid, f) for tid, f in tables],
        "set": set_input(conn, set_id) if trajectories else None,
        "meshes": [mesh_input(conn, m) for m in meshes],
    }


# --- «устарел» ------------------------------------------------------------------------------------


@dataclass
class _State:
    """The current versions of what results of one set read: loaded once per list."""

    tables: dict[str, tuple[str, int, str]]  # name_key → (token, rev, name)
    version: int | None
    meshes: dict[str, set[str] | None] = field(default_factory=dict)  # name_key → current geometry keys


def _state(conn: sa.Connection, set_id: int) -> _State:
    tables = {
        r.name_key: (_token(r.created_at), int(r.rev), r.name)
        for r in conn.execute(
            sa.select(dh_tables.c.name, dh_tables.c.name_key, dh_tables.c.rev, dh_tables.c.created_at).where(
                dh_tables.c.set_id == set_id, dh_tables.c.state == "ready"
            )
        )
    }
    version = conn.execute(sa.select(objects.c.version).where(objects.c.id == set_id)).scalar()
    return _State(tables, None if version is None else int(version))


def _mesh_keys(conn: sa.Connection, st: _State, name: str) -> set[str] | None:
    key = dialect.name_key(name)
    if key not in st.meshes:
        from bf_core.viz.meshes import mesh_state

        ids = conn.execute(
            sa.select(objects.c.id).where(
                objects.c.kind == "wireframe", objects.c.name_key == key, objects.c.state == "ready"
            )
        ).scalars()
        keys = {mesh_state(conn, int(i)).cache_key() for i in ids.all()}
        st.meshes[key] = keys or None
    return st.meshes[key]


def _reason(conn: sa.Connection, st: _State, inputs: dict[str, Any]) -> str | None:
    """Why a result is out of date (Russian, for the tree and «Свойства»); None — it is current."""
    for t in inputs.get("tables") or []:
        now = st.tables.get(str(t.get("key")))
        if now is None:
            return f"удалена таблица «{t.get('name')}»"
        if now[0] != t.get("token"):
            return f"заменена таблица «{t.get('name')}»"
        if now[1] != t.get("rev"):
            return f"изменена таблица «{t.get('name')}»"
    s = inputs.get("set")
    if s and st.version != s.get("version"):
        return "изменены устья или инклинометрия набора"
    for m in inputs.get("meshes") or []:
        keys = _mesh_keys(conn, st, str(m.get("name")))
        if keys is None:
            return f"удалён каркас «{m.get('name')}»"
        if m.get("key") not in keys:
            return f"изменён каркас «{m.get('name')}»"
    return None


def _iso(v: Any) -> str | None:
    return None if v is None else v.isoformat()


def list_results(conn: sa.Connection, set_id: int) -> list[dict[str, Any]]:
    """Results of a set, newest first, with `stale` and its `reason`."""
    st = _state(conn, set_id)
    names = {
        int(r.id): r.name
        for r in conn.execute(sa.select(dh_tables.c.id, dh_tables.c.name).where(dh_tables.c.set_id == set_id))
    }
    out = []
    for r in conn.execute(
        sa.select(dh_results).where(dh_results.c.set_id == set_id).order_by(dh_results.c.id.desc())
    ).mappings():
        reason = _reason(conn, st, r["inputs"] or {})
        report = r["report"] or {}
        out.append(
            {
                "id": int(r["id"]),
                "set_id": int(r["set_id"]),
                "table_id": None if r["table_id"] is None else int(r["table_id"]),
                "table": None if r["table_id"] is None else names.get(int(r["table_id"])),
                "op": r["op"],
                "op_title": OP_TITLES.get(r["op"], r["op"]),
                "makes_table": r["op"] in TABLE_OPS,
                "name": r["name"],
                "params": r["params"],
                "inputs": r["inputs"],
                "sha256": r["sha256"],
                "algo": r["algo"],
                "report": report,
                "written": report.get("written") or [],
                "job_id": r["job_id"],
                "created_at": _iso(r["created_at"]),
                "stale": reason is not None,
                "reason": reason,
            }
        )
    return out


def origins(conn: sa.Connection, set_id: int) -> dict[int, dict[str, Any]]:
    """`origin` of the result tables of a set for `GET /drillholes/sets/{id}/tables`: table id → {result_id,
    op, stale, reason}. Tables with columns written in place are not results themselves and get none."""
    rows = conn.execute(
        sa.select(dh_results.c.id, dh_results.c.table_id, dh_results.c.op, dh_results.c.inputs).where(
            dh_results.c.set_id == set_id, dh_results.c.table_id.is_not(None), dh_results.c.op.in_(TABLE_OPS)
        )
    ).all()
    if not rows:
        return {}
    st = _state(conn, set_id)
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        reason = _reason(conn, st, r.inputs or {})
        out[int(r.table_id)] = {"result_id": int(r.id), "op": r.op, "stale": reason is not None, "reason": reason}
    return out


# --- writing -----------------------------------------------------------------------------------------


def record(
    conn: sa.Connection,
    *,
    set_id: int,
    table_id: int | None,
    op: str,
    name: str,
    params: dict[str, Any],
    inputs: dict[str, Any],
    sha256: str,
    algo: str,
    report: dict[str, Any],
    job_id: int | None = None,
) -> int:
    if op not in OP_TITLES:
        raise ValueError(op)
    res = conn.execute(
        sa.insert(dh_results).values(
            set_id=set_id,
            table_id=table_id,
            op=op,
            name=name,
            params=params,
            inputs=inputs,
            sha256=sha256,
            algo=algo,
            report=report,
            job_id=job_id,
            created_at=dialect.utcnow(),
        )
    )
    key = res.inserted_primary_key
    assert key is not None
    return int(key[0])


def mark_fresh(conn: sa.Connection, result_id: int, inputs: dict[str, Any]) -> None:
    """«Перестроить» with the same hash and algorithm: no computation, the marks move to the current inputs."""
    conn.execute(sa.update(dh_results).where(dh_results.c.id == result_id).values(inputs=inputs))


def shift_marks(
    conn: sa.Connection,
    set_id: int,
    key: str,
    before: tuple[str, int],
    after: tuple[str, int],
    written: Iterable[str],
) -> int:
    """An operation wrote columns `written` into table `key` (token, rev `before` → `after`): the results that
    read that table at `before` and none of the written columns follow it to `after` (ADR 0018 §2). Returns
    how many records moved."""
    hit = {w.casefold() for w in written}
    moved = 0
    for r in conn.execute(sa.select(dh_results.c.id, dh_results.c.inputs).where(dh_results.c.set_id == set_id)).all():
        inputs = r.inputs or {}
        changed = False
        for t in inputs.get("tables") or []:
            if t.get("key") != key or (t.get("token"), t.get("rev")) != tuple(before):
                continue
            if any(str(f).casefold() in hit for f in t.get("fields") or []):
                continue
            t["token"], t["rev"] = after
            changed = True
        if changed:
            conn.execute(sa.update(dh_results).where(dh_results.c.id == r.id).values(inputs=inputs))
            moved += 1
    return moved


def journal_columns(
    conn: sa.Connection, set_id: int, table_id: int, names_by_key: dict[str, tuple[str | None, str | None]]
) -> int:
    """One journal group of `column` records (key → (before, after) name): `None → name` — added, undo hides
    it; `name → None` — hidden, undo brings it back (`drillholes.actions._undo_structure`). Returns the group."""
    from bf_core.drillholes.edit import author, new_group

    group = new_group(conn)
    now = dialect.utcnow()
    who = author()
    for key, (before, after) in names_by_key.items():
        conn.execute(
            sa.insert(edit_log).values(
                scope_kind="object",
                scope_id=set_id,
                group_id=group,
                action="column",
                row_ref={"table": str(table_id), "column": key},
                changes={"column": [before, after]},
                undone=False,
                author=who,
                created_at=now,
            )
        )
    return group


def bump_table(conn: sa.Connection, set_id: int, table_id: int) -> tuple[tuple[str, int], tuple[str, int], str]:
    """rev + 1 of a table (and `updated_at` of the set): ((token, old rev), (token, new rev), name_key)."""
    row = conn.execute(
        sa.select(dh_tables.c.rev, dh_tables.c.created_at, dh_tables.c.name_key).where(dh_tables.c.id == table_id)
    ).one()
    now = dialect.utcnow()
    conn.execute(sa.update(dh_tables).where(dh_tables.c.id == table_id).values(rev=dh_tables.c.rev + 1, updated_at=now))
    conn.execute(sa.update(objects).where(objects.c.id == set_id).values(updated_at=now))
    token = _token(row.created_at)
    return (token, int(row.rev)), (token, int(row.rev) + 1), row.name_key


# --- deletion ---------------------------------------------------------------------------------------


def _not_found() -> ApiError:
    return ApiError(404, "result_not_found", "Результат обработки не найден.", "Обновите дерево проекта.")


def delete_result(conn: sa.Connection, result_id: int) -> dict[str, Any]:
    """Deletes a result: its table (op of TABLE_OPS), its columns in place (hidden, journal `column`), the
    record; of «Рудные интервалы» — also its display. Returns {"set_id", "deleted_tables": [ids], "changed":
    [{"table", "rev"}], "deleted_charts": [ids]} for events and caches."""
    r = conn.execute(sa.select(dh_results).where(dh_results.c.id == result_id)).mappings().first()
    if r is None:
        raise _not_found()
    set_id = int(r["set_id"])
    deleted: list[int] = []
    changed: list[dict[str, Any]] = []
    for w in (r["report"] or {}).get("written") or []:
        tid = conn.execute(
            sa.select(dh_tables.c.id).where(
                dh_tables.c.set_id == set_id, dh_tables.c.name_key == w.get("table"), dh_tables.c.state == "ready"
            )
        ).scalar()
        if tid is None or (r["op"] in TABLE_OPS and tid == r["table_id"]):
            continue
        wanted = {str(n).casefold() for n in w.get("columns") or []}
        cols = [
            c
            for c in conn.execute(
                sa.select(dh_columns.c.id, dh_columns.c.name).where(
                    dh_columns.c.table_id == tid, dh_columns.c.deleted.is_(False)
                )
            )
            if c.name.casefold() in wanted
        ]
        if not cols:
            continue
        conn.execute(sa.update(dh_columns).where(dh_columns.c.id.in_([c.id for c in cols])).values(deleted=True))
        journal_columns(conn, set_id, int(tid), {f"c{c.id}": (c.name, None) for c in cols})
        before, after, key = bump_table(conn, set_id, int(tid))
        shift_marks(conn, set_id, key, before, after, [c.name for c in cols])
        changed.append({"table": str(tid), "rev": after[1]})
    charts: list[int] = []
    if r["op"] in TABLE_OPS and r["table_id"] is not None:
        tid = int(r["table_id"])
        t = conn.execute(sa.select(dh_tables.c.state, dh_tables.c.name_key).where(dh_tables.c.id == tid)).first()
        if t is not None and t.state == "building":
            raise ApiError(409, "result_busy", "Результат сейчас пересчитывается.", "Дождитесь окончания задания.")
        if r["op"] == "ore" and t is not None:  # its display «Рудные интервалы» (`ore_job.display_settings`)
            charts = _drop_displays(conn, set_id, {"op": "ore", "table": t.name_key})
        conn.execute(sa.delete(dh_tables).where(dh_tables.c.id == tid))  # the record goes with it (CASCADE)
        deleted.append(tid)
    conn.execute(sa.delete(dh_results).where(dh_results.c.id == result_id))
    return {"set_id": set_id, "deleted_tables": deleted, "changed": changed, "deleted_charts": charts}


def _drop_displays(conn: sa.Connection, set_id: int, origin: dict[str, Any]) -> list[int]:
    """The saved striplogs of the set made for this result table (`settings.origin`); their ids."""
    from bf_core.charts import service as charts

    shown = charts.list_charts(conn, set_id, "striplog")
    ids = [int(c["id"]) for c in shown if (c["settings"] or {}).get("origin") == origin]
    for i in ids:
        charts.delete(conn, i)
    return ids
