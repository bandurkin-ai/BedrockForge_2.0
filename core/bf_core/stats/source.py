"""The data of charts (ADR 0019 §3): columns of one table of a drillhole set, kept in the memory of the API process.

    t = find_table(conn, set_id, "пробы")             # by name_key (or id); None — the first assay table
    cols = read(project, set_id, "пробы", ["AU", "length", "ROCK"])
        cols.number("AU")       float64 [n], NaN — missing (never 0); "length" — TO − FROM of every row
        cols.text("ROCK")       (int32 codes [n], −1 — empty; sorted labels)
        cols.row_ids            int64 dh_rows.id of every row (what a selection and the table filter use)
        cols.hole_of()          int32 dense hole index into cols.holes (names, sorted)
    other = paired(cols, read(project, set_id, "литология", ["CU"]), "CU")   # by the sample key
        other.values  float64 [n] aligned to `cols`, NaN — no row with the same (hole, FROM, TO); other.unmatched
    dom = domain_at(cols, read(project, set_id, "литология", ["ZONE"]), "ZONE")   # by the middle of a sample
        dom.codes     int32 [n], −1 — no interval of the other table covers the middle; dom.labels; dom.outside
    xyz = midpoints(project, cols)                   # float64 [n, 3]: the middle (by depth) on the hole path

The rows are «lines» of `composites/source.read` (Э12): ordered by (hole name, FROM, TO, row id) — the order does
not depend on the ids the database gave; rows without FROM or TO are left out and counted (`skipped`). The values
come from the Parquet snapshot of the table (`cache/tables`, Э6) when it is there, else from `dh_rows`.

Cache (`COLUMNS`): LRU of columns by (project, table, token, rev, field), ≤ 512 МБ in all (ADR 0019 §3, решение 4
gave 256 МБ; 13.35: 5 million rows are 120 МБ of lines and 40 МБ a field — the ten fields of a dendrogram did not
fit and every recalculation read them again). `length` is not kept: it is TO − FROM of the lines, counted for
every request. An edit of a row raises `rev`: the old columns are a miss and are dropped when the new ones come;
a request of another project drops the columns of the previous one; a value bigger than the limit is not kept.
A request holds the columns it found until it has them all: those read for it may push out of the cache what it
found there, nothing is read twice. Concurrent requests of one column read it once (the others wait). After a
read of a snapshot the memory pyarrow keeps for itself goes back to the system (a few hundred МБ at 5 million
rows). numpy and the readers load on first use, not with the router (tests/test_process.py); the kernel of
`domain_at` (numba) — with the first domain of another table."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_tables, objects

LENGTH = "length"  # the pseudo-field: TO − FROM (a column of that name in the table wins)
LIMIT_BYTES = 512 * 1024 * 1024
KEY_EPS = 1e-6  # m: FROM and TO of a sample key are equal within it
TABLE_KINDS = ("assay", "interval", "event")
_BASE = "\0lines"
_XYZ = "\0xyz"


# --- tables and fields --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Table:
    set_id: int
    id: int
    name: str
    name_key: str
    kind: str  # assay | interval | event
    token: str  # the identity of the table besides its name (`composites.results._token`)
    rev: int
    rows: int


@dataclass(frozen=True)
class FieldInfo:
    name: str
    kind: str  # number | text
    unit: str | None
    role: str  # component | value | sample | attribute | source | length


def _set_missing() -> ApiError:
    return ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")


def _table_missing(table: Any) -> ApiError:
    return ApiError(
        404, "table_not_found", f"В наборе нет таблицы «{str(table)[:80]}».", "Выберите таблицу из списка набора."
    )


def _table(r: Any) -> Table:
    from bf_core.composites.results import _token

    return Table(int(r.set_id), int(r.id), r.name, r.name_key, r.kind, _token(r.created_at), int(r.rev),
                 int(r.row_count or 0))  # fmt: skip


def tables(conn: sa.Connection, set_id: int) -> list[Table]:
    """The ready interval tables of the set: assay first, then by name (as the tabs of «Таблица»)."""
    found = conn.execute(
        sa.select(objects.c.id).where(objects.c.id == set_id, objects.c.kind == "drillhole_set")
    ).first()
    if found is None:
        raise _set_missing()
    rows = conn.execute(
        sa.select(dh_tables).where(
            dh_tables.c.set_id == set_id, dh_tables.c.state == "ready", dh_tables.c.kind.in_(TABLE_KINDS)
        )
    ).all()
    out = [_table(r) for r in rows]
    return sorted(out, key=lambda t: (t.kind != "assay", t.name_key, t.id))


def find_table(conn: sa.Connection, set_id: int, table: str | int | None) -> Table:
    """A table of the set by `name_key` of its name (settings of charts keep it, ADR 0019 §2) or by id; None —
    the first assay table (the first table at all when there is none)."""
    found = tables(conn, set_id)
    if table is None:
        if not found:
            raise ApiError(404, "table_not_found", "В наборе нет таблиц проб.", "Загрузите пробы в набор.")
        return found[0]
    key = dialect.name_key(str(table))
    for t in found:
        if t.name_key == key:
            return t
    if isinstance(table, int) or str(table).isdigit():
        for t in found:
            if t.id == int(table):
                return t
    raise _table_missing(table)


def fields_of(conn: sa.Connection, t: Table) -> list[FieldInfo]:
    """The columns of the table in their order, then `length` (unless the table has a column of that name)."""
    cols = conn.execute(
        sa.select(dh_columns.c.name, dh_columns.c.kind, dh_columns.c.unit, dh_columns.c.role)
        .where(dh_columns.c.table_id == t.id, dh_columns.c.deleted.is_(False))
        .order_by(dh_columns.c.position, dh_columns.c.id)
    ).all()
    out = [FieldInfo(c.name, c.kind, c.unit, c.role) for c in cols]
    if all(f.name.casefold() != LENGTH for f in out):
        out.append(FieldInfo(LENGTH, "number", "м", LENGTH))
    return out


def _resolve(infos: Sequence[FieldInfo], names: Sequence[str]) -> list[FieldInfo]:
    by_name = {f.name.casefold(): f for f in infos}
    out: list[FieldInfo] = []
    for n in names:
        f = by_name.get(str(n).casefold())
        if f is None:
            raise ApiError(
                422, "field_not_found", f"В таблице нет поля «{str(n)[:80]}».", "Выберите поле из списка колонок.",
                extra={"param": "field"},
            )  # fmt: skip
        if f not in out:
            out.append(f)
    return out


# --- the cache ------------------------------------------------------------------------------------------


def _nbytes(value: Any) -> int:
    if isinstance(value, np.ndarray):
        return int(value.nbytes)
    if isinstance(value, tuple | list):
        return sum(_nbytes(v) for v in value) + 8 * len(value)
    if isinstance(value, str):
        return len(value) + 50
    return 64


class ColumnCache:
    """LRU of columns by bytes (module docstring). Keys: (project uid, table id, token, rev, field, …)."""

    def __init__(self, limit: int = LIMIT_BYTES) -> None:
        self.limit = limit
        self._lock = threading.Lock()
        self._items: OrderedDict[tuple[Any, ...], tuple[Any, int]] = OrderedDict()
        self._gates: dict[tuple[Any, ...], threading.Lock] = {}
        self.size = 0
        self.hits = 0
        self.misses = 0

    def _drop(self, key: tuple[Any, ...]) -> None:
        _v, n = self._items.pop(key)
        self.size -= n

    def peek(self, key: tuple[Any, ...]) -> Any:
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return hit[0]

    def put(self, key: tuple[Any, ...], value: Any) -> None:
        n = _nbytes(value)
        with self._lock:
            stale = [
                k for k in self._items
                if k[0] != key[0] or (k[1] == key[1] and k[2:4] != key[2:4]) or (k[:5] == key[:5] and k != key)
            ]  # fmt: skip
            for k in stale:  # another project; older revisions (or another token) of the table; older positions
                self._drop(k)
            if key in self._items:
                self._drop(key)
            if n > self.limit:
                return
            self._items[key] = (value, n)
            self.size += n
            while self.size > self.limit:
                self._drop(next(iter(self._items)))

    def get(self, key: tuple[Any, ...], build: Callable[[], Any]) -> Any:
        """The value of `key`, built once when it is not here (concurrent callers wait for that build)."""
        hit = self.peek(key)
        if hit is not None:
            return hit
        with self._lock:
            gate = self._gates.setdefault(key, threading.Lock())
        with gate:
            hit = self.peek(key)
            if hit is None:
                with self._lock:
                    self.misses += 1
                hit = build()
                self.put(key, hit)
            with self._lock:
                self._gates.pop(key, None)
            return hit

    def locked(self, key: tuple[Any, ...]) -> threading.Lock:
        """A lock of its own for `key`: `with cache.locked(k):` — one reader of a table at a time."""
        with self._lock:
            return self._gates.setdefault(key, threading.Lock())

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.size = 0

    def keys(self) -> list[tuple[Any, ...]]:
        with self._lock:
            return list(self._items)


COLUMNS = ColumnCache()


# --- columns --------------------------------------------------------------------------------------------


@dataclass
class Columns:
    """Chosen fields of one table, rows in line order (module docstring)."""

    table: Table
    row_ids: np.ndarray  # int64 dh_rows.id
    holes: list[str]  # hole names, sorted
    hole_db_ids: np.ndarray  # int64 dh_holes.id of every hole
    starts: np.ndarray  # int64, len(holes) + 1
    from_m: np.ndarray  # float64
    to_m: np.ndarray
    skipped: int  # rows without FROM or TO, left out
    numbers: dict[str, np.ndarray] = field(default_factory=dict)  # float64, NaN — missing
    codes: dict[str, np.ndarray] = field(default_factory=dict)  # int32, −1 — empty
    labels: dict[str, list[str]] = field(default_factory=dict)
    infos: dict[str, FieldInfo] = field(default_factory=dict)  # by the stored name

    @property
    def n(self) -> int:
        return int(self.row_ids.shape[0])

    def hole_of(self) -> np.ndarray:
        """int32 dense hole index (into `holes`) of every row."""
        return np.repeat(np.arange(len(self.holes), dtype=np.int32), np.diff(self.starts))

    def _name(self, name: str) -> str:
        for n in (*self.numbers, *self.codes):
            if n.casefold() == str(name).casefold():
                return n
        raise ApiError(422, "field_not_found", f"Поле «{str(name)[:80]}» не прочитано.", "Выберите поле из списка.",
                       extra={"param": "field"})  # fmt: skip

    def number(self, name: str) -> np.ndarray:
        n = self._name(name)
        if n not in self.numbers:
            raise ApiError(422, "field_not_number", f"Поле «{n}» — текстовое.", "Выберите числовое поле.",
                           extra={"param": "field"})  # fmt: skip
        return self.numbers[n]

    def select_args(self, name: str, weight_field: str | None = None) -> dict[str, np.ndarray | None]:
        """The arrays of `stats.select.select_sample(values, flt, **args)` (13.1): `values`, `length`,
        `weight_values` (None without a weight field) — float64, NaN kept, rows in line order; the `rows` of its
        selection index these, `row_ids[selection.rows]` — dh_rows.id."""
        lengths = self.numbers.get(LENGTH)
        if lengths is None:
            lengths = np.subtract(self.to_m, self.from_m)
        return {
            "values": self.number(name),
            "length": lengths,
            "weight_values": None if weight_field is None else self.number(weight_field),
        }

    def text(self, name: str) -> tuple[np.ndarray, list[str]]:
        n = self._name(name)
        if n not in self.codes:
            raise ApiError(422, "field_not_text", f"Поле «{n}» — числовое.", "Выберите текстовое поле.",
                           extra={"param": "field"})  # fmt: skip
        return self.codes[n], self.labels[n]


def _frozen(a: np.ndarray) -> np.ndarray:
    """Cached arrays are shared by requests: read-only, so a caller cannot change them by accident."""
    a.flags.writeable = False
    return a


def read(project: Any, set_id: int, table: str | int | None, fields: Sequence[str], *,
         cache: ColumnCache = COLUMNS) -> Columns:  # fmt: skip
    """The fields (names, case aside; `length` — the interval length) of a table of the set. `project` needs
    `engine`, `uid` and `folder`. Arrays are shared with the cache: read-only."""
    with dialect.read_tx(project.engine) as conn:
        t = find_table(conn, set_id, table)
        chosen = _resolve(fields_of(conn, t), fields)
    head = (project.uid, t.id, t.token, t.rev)
    stored = [f for f in chosen if f.role != LENGTH]
    got: dict[str, Any] = {}
    with cache.locked(head):  # concurrent requests of one table read its columns once
        for f in stored:
            hit = cache.peek((*head, f.name))
            if hit is not None:
                got[f.name] = hit  # held: the columns read next may push it out of the cache
        missing = [f for f in stored if f.name not in got]
        base = cache.peek((*head, _BASE))
        if missing or base is None:
            lines = _read_lines(project, t, [f.name for f in missing])
            if base is None or len(base[0]) != lines.rows:
                base = tuple(_frozen(a) for a in (lines.row_ids, lines.hole_db_ids, lines.starts, lines.from_m,
                                                  lines.to_m)) + (lines.holes, lines.skipped)  # fmt: skip
                cache.put((*head, _BASE), base)
            for f in missing:
                value = (_frozen(lines.numbers[f.name]) if f.kind == "number"
                         else (_frozen(lines.codes[f.name]), lines.labels[f.name]))  # fmt: skip
                cache.put((*head, f.name), value)
                got[f.name] = value
    ids, hole_db_ids, starts, frm, to, holes, skipped = base
    out = Columns(t, ids, list(holes), hole_db_ids, starts, frm, to, int(skipped))
    for f in chosen:
        out.infos[f.name] = f
        if f.role == LENGTH:
            out.numbers[f.name] = _frozen(np.subtract(to, frm))  # not kept: 10 ms and 40 МБ at 5 million rows
            continue
        have: Any = got.get(f.name)
        if have is None:
            have = cache.peek((*head, f.name))
        if have is None:  # evicted between the two looks: read it alone
            have = _one(project, t, f, base, cache, head)
        if f.kind == "number":
            out.numbers[f.name] = have
        else:
            out.codes[f.name], out.labels[f.name] = have
    return out


def _read_lines(project: Any, t: Table, names: list[str]) -> Any:
    from bf_core.composites import source as dh_source

    lines = dh_source.read(project.engine, t.set_id, t.id, names, cache_dir=project.folder / "cache" / "tables")
    if lines.source == "parquet":
        import pyarrow as pa

        pa.default_memory_pool().release_unused()  # what the pool keeps after the columns were copied out of it
    return lines


def _one(project: Any, t: Table, f: FieldInfo, base: tuple[Any, ...], cache: ColumnCache,
         head: tuple[Any, ...]) -> Any:  # fmt: skip
    lines = _read_lines(project, t, [f.name])
    if len(lines.row_ids) != len(base[0]):
        raise ApiError(409, "table_changed", "Таблица изменилась во время чтения.", "Повторите действие.")
    value = (_frozen(lines.numbers[f.name]) if f.kind == "number"
             else (_frozen(lines.codes[f.name]), lines.labels[f.name]))  # fmt: skip
    cache.put((*head, f.name), value)
    return value


# --- another table: by the sample key, by the middle of a sample -----------------------------------------


@dataclass(frozen=True)
class Paired:
    values: np.ndarray  # float64 [n of a], NaN — no match (or missing in b)
    rows: np.ndarray  # int64: the row of b (line position) of every row of a, −1 — none
    unmatched: int  # rows of a without a row of b with the same key


def paired(a: Columns, b: Columns, name: str, *, eps: float = KEY_EPS) -> Paired:
    """The field `name` of the table `b` for every row of `a` by the sample key (hole, FROM, TO), FROM and TO
    equal within `eps` (two rows of b with one key: the last in line order)."""
    from . import source_kernels as sk

    col = b.number(name)
    if b.table.id == a.table.id:
        return Paired(col, np.arange(a.n, dtype=np.int64), 0)
    q = 1.0 / eps

    def key(x: np.ndarray) -> np.ndarray:
        return np.rint(x * q).astype(np.int64)

    lo, hi = sk.hole_ranges(a.holes, b.holes, b.starts)
    rows = np.empty(a.n, dtype=np.int64)
    unmatched = sk.match(np.ascontiguousarray(a.starts, dtype=np.int64), key(a.from_m), key(a.to_m), lo, hi,
                         key(b.from_m), key(b.to_m), rows)  # fmt: skip
    ok = rows >= 0
    values = np.full(a.n, np.nan)
    values[ok] = col[rows[ok]]
    return Paired(values, rows, int(unmatched))


@dataclass(frozen=True)
class Domain:
    codes: np.ndarray  # int32 [n of a], −1 — empty or no interval
    labels: list[str]
    outside: int  # rows of a whose middle no interval of the other table covers (0 for a field of `a` itself)


def domain_at(a: Columns, dom: Columns, name: str) -> Domain:
    """The text field `name` of `dom` for every row of `a`: of the same table — as it is; of another interval
    table — the code of its interval [FROM; TO) that holds the middle (FROM + TO) / 2 of the sample (the one that
    starts last when they overlap)."""
    from . import source_kernels as sk

    codes, labels = dom.text(name)
    if dom.table.id == a.table.id:
        return Domain(codes, labels, 0)
    lo, hi = sk.hole_ranges(a.holes, dom.holes, dom.starts)
    rows = np.empty(a.n, dtype=np.int64)
    outside = sk.cover(np.ascontiguousarray(a.starts, dtype=np.int64), 0.5 * (a.from_m + a.to_m), lo, hi,
                       np.ascontiguousarray(dom.from_m), np.ascontiguousarray(dom.to_m), rows)  # fmt: skip
    out = np.full(a.n, -1, dtype=np.int32)
    ok = rows >= 0
    out[ok] = codes[rows[ok]]
    return Domain(out, labels, int(outside))


# --- positions ------------------------------------------------------------------------------------------


def midpoints(project: Any, cols: Columns, *, cache: ColumnCache = COLUMNS) -> np.ndarray:
    """X, Y, Z (float64 [n, 3]) of the middle by depth of every row on its hole path (`composites.coords`): what
    3D, the sections and «Координаты проб» show; NaN — the hole has no trajectory (never a zero). Cached with the
    columns, by the version of the trajectories too."""
    from bf_core.composites import coords
    from bf_core.composites import intervals as iv
    from bf_core.composites import source as dh_source

    st = dh_source.trajectories(project, cols.table.set_id)
    t = cols.table
    key = (project.uid, t.id, t.token, t.rev, _XYZ, *st.key[2:])

    def build() -> np.ndarray:
        dense = coords.hole_index(st.hole_ids, cols.hole_db_ids)
        lines = iv.Lines(np.asarray(cols.hole_db_ids), np.asarray(cols.starts), np.asarray(cols.from_m),
                         np.asarray(cols.to_m))  # fmt: skip
        return _frozen(coords.midpoints(lines, st.traj, dense))

    return cache.get(key, build)  # type: ignore[no-any-return]


# --- the list of fields (GET /stats/fields) -------------------------------------------------------------

TEXT_VALUES = 300


def fields_list(project: Any, set_id: int, table: str | None) -> dict[str, Any]:
    """The tables of the set with their numeric and text fields; with `table` — only it, and its text fields
    carry their values (≤ TEXT_VALUES, `values` — how many there are)."""
    with dialect.read_tx(project.engine) as conn:
        chosen = tables(conn, set_id) if table is None else [find_table(conn, set_id, table)]
        infos = {t.id: fields_of(conn, t) for t in chosen}
    out = []
    for t in chosen:
        texts = [f for f in infos[t.id] if f.kind == "text"]
        values: dict[str, list[str]] = {}
        if table is not None and texts:
            cols = read(project, set_id, t.id, [f.name for f in texts])
            values = {f.name: cols.labels[f.name] for f in texts}
        out.append({
            "id": t.id, "name": t.name, "name_key": t.name_key, "kind": t.kind, "rows": t.rows, "rev": t.rev,
            "numbers": [{"name": f.name, "unit": f.unit, "role": f.role} for f in infos[t.id] if f.kind == "number"],
            "texts": [{"name": f.name, "role": f.role, **_values(values.get(f.name))} for f in texts],
        })  # fmt: skip
    return {"set_id": set_id, "tables": out}


def _values(labels: list[str] | None) -> dict[str, Any]:
    if labels is None:
        return {"values": None, "labels": None, "more": False}
    return {"values": len(labels), "labels": labels[:TEXT_VALUES], "more": len(labels) > TEXT_VALUES}
