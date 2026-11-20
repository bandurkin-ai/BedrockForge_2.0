"""Writing the result of a sample-processing operation (ADR 0018 §2–3): atomically, or not at all.

1. A new table is built aside (`build_table`: `drillholes.store.write_table(swap=False)`, state `building`
   under the id of the job, in portions — readers and writers are not blocked).
2. One final transaction (`commit`): the name (taken — 409 `table_exists`, « (2)», or «Заменить»: the table
   of that name goes, its results with it), the columns in place (`write_columns`: one pass over `dh_rows`,
   rev + 1, one journal group `column`), the `dh_results` record, the revision marks of the other results.
3. After it, the Parquet snapshot of the new table from the arrays of the job (`write_snapshot`): the table
   opens without decoding the vectors.

A cancel or a crash (`abort`, or the `guarded` context) drops the building table: no table, no columns.
A repeat of an operation overwrites its own columns (the same columns, the same slots) and replaces its
earlier record.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_results, dh_rows, dh_tables
from bf_core.drillholes import store

from . import results

Mode = Literal["new", "rename", "replace"]
PORTION = 50_000


class TableExists(ApiError):
    """409 `table_exists`: the window asks «Заменить?» (ADR 0018 §11)."""

    def __init__(self, name: str) -> None:
        super().__init__(
            409,
            "table_exists",
            f"В наборе уже есть таблица «{name}».",
            "Выберите «Заменить», чтобы заменить её, или задайте другое название.",
        )
        self.name = name


def _noop(_p: float | None, _m: str | None) -> None:
    pass


# --- names ---------------------------------------------------------------------------------------


def _ready(conn: sa.Connection, set_id: int, name: str, *, but: int | None = None) -> Any:
    q = sa.select(dh_tables.c.id, dh_tables.c.kind).where(
        dh_tables.c.set_id == set_id, dh_tables.c.name_key == dialect.name_key(name), dh_tables.c.state == "ready"
    )
    if but is not None:
        q = q.where(dh_tables.c.id != but)
    return conn.execute(q).first()


def taken(conn: sa.Connection, set_id: int, name: str) -> bool:
    return _ready(conn, set_id, name) is not None


def free_name(conn: sa.Connection, set_id: int, name: str) -> str:
    """`name`, or «name (2)», «name (3)»… — the first one no ready table of the set has."""
    if not taken(conn, set_id, name):
        return name
    n = 2
    while taken(conn, set_id, f"{name} ({n})"):
        n += 1
    return f"{name} ({n})"


# --- the new table -------------------------------------------------------------------------------


@dataclass
class OutTable:
    """Rows of a result table: parallel arrays; numbers float64 (NaN — missing), text — None for empty."""

    name: str
    holes: Sequence[str]  # hole name of every row (a list or an object array)
    from_m: np.ndarray
    to_m: np.ndarray
    columns: list[store.ColumnSpec]
    values: list[np.ndarray]
    row_ids: np.ndarray | None = None  # dh_rows.id of every row once the table is built, when the database told
    #: optional (hole names, the index into them of every row) — `holes` once more, as the kernel had them
    hole_index: tuple[Sequence[str], np.ndarray] | None = None


def build_table(engine: sa.Engine, set_id: int, out: OutTable, *, job_id: int, tick: store.Tick = _noop) -> int:
    """The table in state `building` (invisible); returns its id. Its name is settled by `commit`. The keys of
    its rows are kept in `out.row_ids` (SQLite) for the snapshot."""
    values = [cast(Sequence[Any], v) for v in out.values]
    data = store.TableData(out.name, "interval", out.holes, out.from_m, out.to_m, out.columns, values,
                           hole_index=out.hole_index)  # fmt: skip
    res = store.write_table(engine, set_id, data, job_id=job_id, tick=tick, swap=False)
    out.row_ids = res["row_ids"]
    return int(res["table_id"])


def abort(engine: sa.Engine, job_id: int) -> None:
    """After a cancel or a crash: the building tables of the job go (columns are written only by `commit`)."""
    with dialect.write_tx(engine) as conn:
        store.drop_building(conn, job_id)


@contextmanager
def guarded(engine: sa.Engine, job_id: int) -> Iterator[None]:
    """`with guarded(engine, job_id): build…; commit…` — any exception (a cancel too) leaves nothing."""
    try:
        yield
    except BaseException:
        abort(engine, job_id)
        raise


# --- columns in place --------------------------------------------------------------------------------


@dataclass
class ColumnsOut:
    """Columns an operation writes into an existing table: values by row id (rows not listed get empty)."""

    table_id: int
    row_ids: np.ndarray  # int64
    columns: list[tuple[store.ColumnSpec, np.ndarray]]  # number: float64; text: object (None — empty)


def _column_slots(conn: sa.Connection, table_id: int, specs: Sequence[store.ColumnSpec]) -> list[tuple[int, int]]:
    """(dh_columns.id, slot) of every spec: the visible column of that name (same kind), else a hidden one of
    that name and kind (back, its slot reused), else a new column in the next free slot of its kind (hidden
    columns keep theirs: their values stay in the vectors)."""
    cols = conn.execute(sa.select(dh_columns).where(dh_columns.c.table_id == table_id)).all()
    used: dict[str, int] = {}
    for c in cols:
        used[c.kind] = max(used.get(c.kind, -1), int(c.slot))
    out = []
    for spec in specs:
        same = [c for c in cols if c.name.casefold() == spec.name.casefold()]
        live = [c for c in same if not c.deleted]
        if live and live[0].kind != spec.kind:
            what = "текст" if live[0].kind == "text" else "числа"
            raise ApiError(
                409,
                "column_exists",
                f"Колонка «{spec.name}» уже есть, и в ней {what}.",
                "Задайте другое название поля результата.",
            )
        reuse = live or [c for c in same if c.deleted and c.kind == spec.kind]
        if reuse:
            c = reuse[0]
            conn.execute(sa.update(dh_columns).where(dh_columns.c.id == c.id).values(deleted=False, unit=spec.unit))
            out.append((int(c.id), int(c.slot)))
            continue
        slot = used.get(spec.kind, -1) + 1
        used[spec.kind] = slot
        res = conn.execute(
            sa.insert(dh_columns).values(
                table_id=table_id,
                name=spec.name,
                kind=spec.kind,
                unit=spec.unit,
                slot=slot,
                role=spec.role,
                deleted=False,
                position=len(cols) + len(out),
            )
        )
        key = res.inserted_primary_key
        assert key is not None
        out.append((int(key[0]), slot))
    return out


def _set_in_place(
    conn: sa.Connection, table_id: int, ids: np.ndarray, nums: Sequence[tuple[int, np.ndarray]],
    texts: Sequence[tuple[str, np.ndarray]],
) -> int | None:  # fmt: skip
    """The columns of `write_columns` (`ids` ascending, the values in their order) set by statements of the
    database — `dialect.set_slots`: no row comes to Python and goes back (5 million rows: 45 s → 6 s). The same
    values as the pass of `write_columns` leaves, the vectors cut the same way; the JSON of the texts is as the
    database writes it. Returns the rows written, or None — nothing is done, the pass writes them: not SQLite,
    rows that are not a few runs of consecutive ids, a text column of many values or not of strings."""
    import pandas as pd

    keep = np.ones(len(ids), dtype=bool)
    keep[1:] = ids[1:] != ids[:-1]  # a row listed twice takes its first value, as in the pass
    ids = ids[keep]
    first = np.flatnonzero(np.diff(ids, prepend=ids[:1] - 2) != 1)
    if len(first) > dialect.SLOT_RUNS:
        return None
    runs = [(int(ids[a]), int(b - a)) for a, b in zip(first.tolist(), [*first[1:].tolist(), len(ids)], strict=False)]
    coded = []
    for slot, values in texts:
        codes, uniques = pd.factorize(values[keep], use_na_sentinel=True)  # None, NaN — empty
        labels = uniques.tolist()
        if len(labels) > dialect.SLOT_LABELS + 1 or any(type(x) is not str for x in labels):
            return None
        if "" in labels:  # empty too
            gone = labels.index("")
            codes = np.where(codes == gone, -1, codes - (codes > gone))
            labels.remove("")
        coded.append((slot, np.where(codes < 0, 255, codes).astype(np.uint8), labels))
    return dialect.set_slots(conn, dh_rows, ("table_id", table_id), runs, [(s, v[keep]) for s, v in nums], coded)


def write_columns(conn: sa.Connection, set_id: int, out: ColumnsOut) -> dict[str, Any]:
    """All columns of an operation in one pass over the table, in the caller's transaction; rev + 1, one
    journal group (`column` per column: «Отменить» hides them). Returns {"rev", "keys": {name: c<id>},
    "group", "rows": rows changed (all the rows of the table when the database set them itself:
    `_set_in_place`), "before", "after", "table_key"}."""
    import json

    specs = [s for s, _v in out.columns]
    if len({s.name.casefold() for s in specs}) != len(specs):
        raise ValueError("duplicate column names")
    slots = _column_slots(conn, out.table_id, specs)
    order = np.argsort(out.row_ids, kind="stable")
    ids_sorted = np.asarray(out.row_ids, dtype=np.int64)[order]
    nums = [
        (slot, np.asarray(v, dtype=float)[order])
        for (s, v), (_c, slot) in zip(out.columns, slots, strict=True)
        if s.kind == "number"
    ]
    texts = [
        (str(slot), np.asarray(v, dtype=object)[order])
        for (s, v), (_c, slot) in zip(out.columns, slots, strict=True)
        if s.kind == "text"
    ]
    width = (
        int(
            conn.execute(
                sa.select(sa.func.max(dh_columns.c.slot)).where(
                    dh_columns.c.table_id == out.table_id, dh_columns.c.kind == "number"
                )
            ).scalar()
            or 0
        )
        + 1
    )
    fields = ["id"] + (["num"] if nums else []) + (["txt"] if texts else [])
    sql = f"SELECT {', '.join(fields)} FROM dh_rows WHERE table_id = ? ORDER BY id"
    set_there = _set_in_place(conn, out.table_id, ids_sorted, nums, texts)
    changed = set_there or 0
    pending: list[tuple[Any, ...]] = []
    for chunk in dialect.iter_driver_rows(conn, sql, (out.table_id,)) if set_there is None else ():
        got = list(zip(*chunk, strict=True))
        ids = np.array(got[0], dtype=np.int64)
        pos = np.searchsorted(ids_sorted, ids).clip(0, max(len(ids_sorted) - 1, 0))
        hit = (ids_sorted[pos] == ids) if len(ids_sorted) else np.zeros(len(ids), dtype=bool)
        dirty = np.zeros(len(ids), dtype=bool)
        if nums:
            from .source import _decode

            matrix = _decode(got[1], width)  # width covers every number slot of the table, hidden ones too
            old = matrix.copy()
            for slot, vals in nums:
                matrix[:, slot] = np.where(hit, vals[pos] if len(vals) else np.nan, np.nan)
            dirty |= np.any(matrix.view(np.int64) != old.view(np.int64), axis=1)
            del old
        new_txt: list[str | None] = []
        if texts:
            raw = got[-1]
            for r in range(len(ids)):
                cur = raw[r]
                txt = json.loads(cur) if isinstance(cur, str) else dict(cur or {})
                was = dict(txt)
                for tslot, tvals in texts:
                    v = tvals[pos[r]] if hit[r] else None
                    if v is None or v == "" or v != v:  # None, "", NaN — empty (as `store_rows.Texts`)
                        txt.pop(tslot, None)
                    else:
                        txt[tslot] = str(v)
                if txt != was:
                    dirty[r] = True
                new_txt.append(json.dumps(txt, ensure_ascii=False) if txt else None)
        rows = np.flatnonzero(dirty)
        parts: list[Any] = []
        if nums:
            parts.append(store.encode_vectors(matrix[rows]))
        if texts:
            parts.append([new_txt[r] for r in rows.tolist()])
        pending.extend(zip(*parts, ids[rows].tolist(), strict=True))
        changed += int(dirty.sum())
        if len(pending) >= PORTION:
            dialect.bulk_update(conn, dh_rows, fields[1:], "id", pending)
            pending = []
        del chunk, got
    if pending:
        dialect.bulk_update(conn, dh_rows, fields[1:], "id", pending)
    keys = {s.name: f"c{cid}" for s, (cid, _slot) in zip(specs, slots, strict=True)}
    group = results.journal_columns(conn, set_id, out.table_id, {keys[s.name]: (None, s.name) for s in specs})
    before, after, table_key = results.bump_table(conn, set_id, out.table_id)
    return {"rev": after[1], "keys": keys, "group": group, "rows": changed, "before": before, "after": after,
            "table_key": table_key}  # fmt: skip


# --- the final transaction ---------------------------------------------------------------------------


@dataclass
class Dependent:
    """A column operation repeated on the new table of a rebuild or «Заменить» (ADR 0018 решение 18): its
    columns go into the new table in the final transaction and its record moves to that table."""

    op: str
    name: str
    params: dict[str, Any]
    fields: list[str]  # fields of the new table it read
    sha256: str
    algo: str
    report: dict[str, Any]
    columns: list[tuple[store.ColumnSpec, np.ndarray]]  # values in the row order of the new table


def with_dependents(out: OutTable, deps: Sequence[Dependent]) -> OutTable:
    """The arrays of the new table with the columns of the repeated operations (for its snapshot)."""
    if not deps:
        return out
    cols, vals = list(out.columns), list(out.values)
    for d in deps:
        for spec, v in d.columns:
            at = next((k for k, c in enumerate(cols) if c.name.casefold() == spec.name.casefold()), None)
            if at is None:
                cols.append(spec)
                vals.append(np.asarray(v))
            else:
                cols[at], vals[at] = spec, np.asarray(v)
    return OutTable(out.name, out.holes, out.from_m, out.to_m, cols, vals, out.row_ids, out.hole_index)


def _row_ids(conn: sa.Connection, table_id: int) -> np.ndarray:
    parts = [
        np.array([r[0] for r in chunk], dtype=np.int64)
        for chunk in dialect.iter_driver_rows(
            conn, "SELECT id FROM dh_rows WHERE table_id = ? ORDER BY id", (table_id,)
        )
    ]
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int64)


@dataclass
class Commit:
    set_id: int
    op: str
    name: str  # of the result; of the table too, when `table` is given
    params: dict[str, Any]
    inputs: dict[str, Any]
    sha256: str
    algo: str
    report: dict[str, Any] = field(default_factory=dict)
    job_id: int | None = None
    table: int | None = None  # the building table (TABLE_OPS)
    mode: Mode = "new"
    columns: list[ColumnsOut] = field(default_factory=list)
    dependents: list[Dependent] = field(default_factory=list)  # repeated on `table` (rows in its build order)
    #: called last inside the final transaction with its result (may add keys to it): what must be committed
    #: with the result or not at all — the display «Рудные интервалы» of the ore intervals (`ore_job`)
    after: Callable[[sa.Connection, dict[str, Any]], None] | None = None


def commit(engine: sa.Engine, c: Commit, *, fold: Any = None) -> dict[str, Any]:
    """The final transaction (see the module docstring). Returns {"result_id", "table_id", "name", "rev",
    "replaced": [ids of tables replaced], "changed": [{"table", "rev"}] — tables that got columns} and what
    `c.after` added. A table of `c.columns` deleted or replaced while the job ran — 409 `source_gone`.
    `fold` — of `dialect.fold_later`: the WAL of the transaction (columns of millions of rows) is folded into
    the file after it, on a thread, while the job writes the snapshot — not inside its commit."""
    with dialect.write_tx(engine, fold=fold) as conn:
        for co in c.columns:
            _still_there(conn, c, co.table_id)
        name, rev = c.name, None
        replaced: list[int] = []
        own_cols: dict[str, Any] = {}
        if c.table is not None:
            name, replaced = _settle_name(conn, c)
            store.swap_in(conn, c.set_id, c.table)
            rev = int(conn.execute(sa.select(dh_tables.c.rev).where(dh_tables.c.id == c.table)).scalar_one())
            live = (dh_columns.c.table_id == c.table) & dh_columns.c.deleted.is_(False)
            made = conn.execute(sa.select(dh_columns.c.name).where(live)).scalars()
            own_cols = {"table_columns": list(made)}  # what the operation made: the rest is the user's (`lost`)
        changed, written, shifts = [], [], []
        for co in c.columns:
            w = write_columns(conn, c.set_id, co)
            changed.append({"table": str(co.table_id), "rev": w["rev"]})
            names = [s.name for s, _v in co.columns]
            written.append({"table": w["table_key"], "columns": names})
            shifts.append((w["table_key"], w["before"], w["after"], names))
        own = c.table if c.table is not None else (c.columns[0].table_id if c.columns else None)
        if c.table is None and c.columns:
            _supersede(conn, c, own, written)
        rid = results.record(
            conn,
            set_id=c.set_id,
            table_id=own,
            op=c.op,
            name=name,
            params=c.params,
            inputs=c.inputs,
            sha256=c.sha256,
            algo=c.algo,
            report={**c.report, "written": written, **own_cols},
            job_id=c.job_id,
        )
        for key, before, after, names in shifts:  # the new record follows too, unless it read what it wrote
            results.shift_marks(conn, c.set_id, key, before, after, names)
        if c.table is not None and c.dependents:
            _repeat(conn, c, c.table, changed)
            rev = int(conn.execute(sa.select(dh_tables.c.rev).where(dh_tables.c.id == c.table)).scalar_one())
        out = {"result_id": rid, "table_id": own, "name": name, "rev": rev, "replaced": replaced, "changed": changed}
        if c.after is not None:
            c.after(conn, out)
    return out


def _still_there(conn: sa.Connection, c: Commit, table_id: int) -> None:
    """409 `source_gone` when the table the columns go into was deleted or replaced while the job ran."""
    state = conn.execute(sa.select(dh_tables.c.state).where(dh_tables.c.id == table_id)).scalar()
    if state == "ready":
        return
    names = [str(t["name"]) for t in (c.inputs or {}).get("tables") or [] if t.get("name")]
    what = f"Таблица «{names[0]}»" if names else "Таблица"
    raise ApiError(409, "source_gone", f"{what} удалена или заменена, пока шло задание: поля не записаны.",
                   "Запустите операцию заново на текущей таблице.")  # fmt: skip


def _repeat(conn: sa.Connection, c: Commit, table_id: int, changed: list[dict[str, Any]]) -> None:
    """The dependents of a replaced table, in their order: columns into the new table, records on it."""
    ids = _row_ids(conn, table_id)  # rows were written in the order of the arrays: ids ascend with it
    for d in c.dependents:
        inputs = {"tables": [results.table_input(conn, table_id, d.fields)], "set": None, "meshes": []}
        w = write_columns(conn, c.set_id, ColumnsOut(table_id, ids, list(d.columns)))
        names = [s.name for s, _v in d.columns]
        results.record(conn, set_id=c.set_id, table_id=table_id, op=d.op, name=d.name, params=d.params,
                       inputs=inputs, sha256=d.sha256, algo=d.algo,
                       report={**d.report, "written": [{"table": w["table_key"], "columns": names}]},
                       job_id=c.job_id)  # fmt: skip
        results.shift_marks(conn, c.set_id, w["table_key"], w["before"], w["after"], names)
        changed.append({"table": str(table_id), "rev": w["rev"]})


def _settle_name(conn: sa.Connection, c: Commit) -> tuple[str, list[int]]:
    assert c.table is not None
    other = _ready(conn, c.set_id, c.name, but=c.table)
    name = c.name
    replaced: list[int] = []
    if other is not None:
        if c.mode == "new" or (c.mode == "replace" and other.kind != "interval"):
            raise TableExists(c.name)
        if c.mode == "rename":
            name = free_name(conn, c.set_id, c.name)
        else:
            replaced.append(int(other.id))  # swap_in deletes it; its results go by cascade
    conn.execute(
        sa.update(dh_tables).where(dh_tables.c.id == c.table).values(name=name, name_key=dialect.name_key(name))
    )
    return name, replaced


def _supersede(conn: sa.Connection, c: Commit, table_id: int | None, written: list[dict[str, Any]]) -> None:
    """A repeat of a column operation replaces the earlier record that wrote (some of) the same columns."""
    mine = {(w["table"], str(n).casefold()) for w in written for n in w["columns"]}
    for r in conn.execute(
        sa.select(dh_results.c.id, dh_results.c.report).where(
            dh_results.c.set_id == c.set_id, dh_results.c.op == c.op, dh_results.c.table_id == table_id
        )
    ).all():
        theirs = {
            (w.get("table"), str(n).casefold())
            for w in (r.report or {}).get("written") or []
            for n in w.get("columns") or []
        }
        if theirs and theirs <= mine:
            conn.execute(sa.delete(dh_results).where(dh_results.c.id == r.id))


# --- the snapshot of the new table ---------------------------------------------------------------


def _text_column(values: Any) -> Any:
    """A text column of a result as an Arrow string array (None and "" — null). The rows are grouped by value
    first: millions of rows repeat some dozen labels, and no string is made per row."""
    import pandas as pd
    import pyarrow as pa

    codes, uniques = pd.factorize(np.asarray(values, dtype=object), use_na_sentinel=True)
    labels = [str(u) for u in uniques.tolist()]
    if "" in labels:
        codes = np.where(codes == labels.index(""), -1, codes)
    index = pa.array(codes.astype(np.int32), mask=codes < 0)
    return pa.DictionaryArray.from_arrays(index, pa.array(labels, type=pa.string())).cast(pa.string())


def write_snapshot(engine: sa.Engine, cache_dir: Path, table_id: int, out: OutTable) -> Path:
    """`cache/tables/<id>-<token>-<rev>.parquet` from the arrays of the job, in the format of the snapshots of
    the «Таблица» panel (`drillholes.snapshot`): `__id`, `__hole`, `from`, `to`, `c<id>`. The keys of the rows
    are those `build_table` kept (`out.row_ids`), else they are read from the table."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    n = len(out.holes)
    ids = out.row_ids if out.row_ids is not None and len(out.row_ids) == n else None
    with dialect.read_tx(engine) as conn:
        t = conn.execute(sa.select(dh_tables).where(dh_tables.c.id == table_id)).one()
        cols = conn.execute(
            sa.select(dh_columns.c.id, dh_columns.c.name).where(
                dh_columns.c.table_id == table_id, dh_columns.c.deleted.is_(False)
            )
        ).all()
        holes = store.hole_ids(conn, int(t.set_id))
        if ids is None or int(t.row_count or 0) != n:
            ids = _row_ids(conn, table_id)
    if len(ids) != n:
        raise ValueError("rows of the table differ from the arrays")
    key_of = {c.name.casefold(): f"c{c.id}" for c in cols}  # by casefold, as `_column_slots` finds them
    codes, names = store.hole_codes(out.holes, out.hole_index)
    hole_of = np.array([holes[h] for h in names], dtype=np.int64)[codes] if n else np.zeros(0, dtype=np.int64)
    arrays: dict[str, Any] = {
        "__id": pa.array(np.asarray(ids, dtype=np.int64)),
        "__hole": pa.array(hole_of),
        "from": pa.array(np.asarray(out.from_m, dtype=float), type=pa.float64()),
        "to": pa.array(np.asarray(out.to_m, dtype=float), type=pa.float64()),
    }
    for spec, vals in zip(out.columns, out.values, strict=True):
        key = key_of[spec.name.casefold()]
        if spec.kind == "number":
            arrays[key] = pa.array(np.asarray(vals, dtype=float), type=pa.float64())
        else:
            arrays[key] = _text_column(vals)
    path = cache_dir / f"{table_id}-{results._token(t.created_at)}-{t.rev}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    pq.write_table(pa.table(arrays), tmp, compression="zstd")
    tmp.replace(path)
    return path
