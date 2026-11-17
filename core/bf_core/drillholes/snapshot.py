"""Column snapshots of drillhole tables for the «Таблица» panel (ТЗ 7.10; ADR 0003 §4).

A snapshot holds every row of one table as columns in memory: numbers as float64 arrays (NaN = missing),
text as object arrays (None = missing), plus row ids and hole ids. Sorting, filtering and statistics run
on it (numpy), pages are cut from it — the window never loads a table whole.

Keys: (project uid, set id, table, revision) — interval tables by `dh_tables.rev`, collars and surveys by
`objects.version` of the set (both grow on every edit). Interval tables of 100 000 rows and more are also
kept as `cache/tables/<id>-<rev>.parquet`, so reopening the project does not decode the vectors again.
Edits patch the snapshot in memory and move it to the new key (`patched`); a build from the database
happens only when the key is unknown.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_surveys, dh_tables, objects

PARQUET_FROM = 100_000
CACHE_SIZE = 3

#: Fixed columns of collars and surveys: key, title, kind, required.
COLLAR_FIELDS = (
    ("hole", "Скважина", "text", True),
    ("x", "X", "number", True),
    ("y", "Y", "number", True),
    ("z", "Z", "number", True),
    ("td", "Глубина", "number", True),
    ("azimuth", "Азимут", "number", False),
    ("dip", "Наклон", "number", False),
)
SURVEY_FIELDS = (
    ("hole", "Скважина", "text", True),
    ("depth", "Глубина", "number", True),
    ("azimuth", "Азимут", "number", True),
    ("dip", "Наклон", "number", True),
)
INTERVAL_FIELDS = (("hole", "Скважина", "text", True), ("from", "От", "number", True), ("to", "До", "number", True))
EVENT_FIELDS = (("hole", "Скважина", "text", True), ("from", "Глубина", "number", True))


@dataclass
class ColumnInfo:
    key: str  # hole, x… | a:<attribute> | c<dh_columns.id>
    name: str
    kind: str  # number | text
    required: bool = False
    unit: str | None = None
    role: str = "value"  # fixed | component | sample | value | source | attribute
    slot: int | None = None  # interval tables: position in the vector / text map

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "kind": self.kind,
            "required": self.required,
            "unit": self.unit,
            "role": self.role,
        }


@dataclass
class Snapshot:
    """All rows of one table, column-wise. `alive` marks rows not deleted since the build."""

    table: str  # collar | survey | <dh_tables.id>
    set_id: int
    kind: str  # collar | survey | assay | interval | event
    key: tuple[Any, ...]
    columns: list[ColumnInfo]
    ids: np.ndarray  # int64 row ids (dh_holes.id, dh_surveys.id or dh_rows.id)
    hole_ids: np.ndarray  # int64
    data: dict[str, np.ndarray]  # column key → float64 or object array
    hole_names: dict[int, str]
    alive: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    orders: dict[tuple[str, bool], np.ndarray] = field(default_factory=dict)  # cached sort orders
    masks: dict[tuple[Any, str], np.ndarray] = field(default_factory=dict)  # cached filters (hole, expression)

    def __post_init__(self) -> None:
        if len(self.alive) != len(self.ids):
            self.alive = np.ones(len(self.ids), dtype=bool)

    @property
    def rows(self) -> int:
        return int(self.alive.sum())

    def column(self, key: str) -> ColumnInfo:
        for c in self.columns:
            if c.key == key:
                return c
        raise KeyError(key)

    def values(self, key: str) -> np.ndarray:
        if key == "hole":
            return self.hole_texts()
        return self.data[key]

    def hole_texts(self) -> np.ndarray:
        if "hole" not in self.data:
            names = self.hole_names
            self.data["hole"] = np.array([names.get(int(h)) for h in self.hole_ids], dtype=object)
        return self.data["hole"]

    def position(self, row_id: int) -> int | None:
        """Index of a row by its id (ids are ascending as built; appended rows come last)."""
        i = int(np.searchsorted(self.ids, row_id))
        if i < len(self.ids) and self.ids[i] == row_id and self.alive[i]:
            return i
        hit = np.flatnonzero((self.ids == row_id) & self.alive)
        return int(hit[0]) if len(hit) else None


# --- building ------------------------------------------------------------------------------------


def _token(created: Any) -> str:
    """Identity of a table besides its id: SQLite gives a freed id again (no AUTOINCREMENT), so a table made
    after a deletion may carry the old id and rev 1 — its creation time tells them apart (cache, Parquet)."""
    return "0" if created is None else f"{int(created.timestamp() * 1_000_000):x}"


def table_key(conn: sa.Connection, uid: str, set_id: int, table: str) -> tuple[Any, ...] | None:
    """The current key of a table: (uid, set id, table, identity token, revision); None — no such table."""
    if table in ("collar", "survey"):
        r = conn.execute(
            sa.select(objects.c.version, objects.c.created_at).where(
                objects.c.id == set_id, objects.c.kind == "drillhole_set"
            )
        ).first()
        return None if r is None else (uid, set_id, table, _token(r.created_at), int(r.version))
    if not table.isdigit():
        return None
    row = conn.execute(
        sa.select(dh_tables.c.rev, dh_tables.c.created_at).where(
            dh_tables.c.id == int(table), dh_tables.c.set_id == set_id, dh_tables.c.state == "ready"
        )
    ).first()
    return None if row is None else (uid, set_id, table, _token(row.created_at), int(row.rev))


def drop_cache_files(cache_dir: Path, table_ids: list[int]) -> None:
    """Parquet snapshots of deleted tables (a deleted set, a replaced table)."""
    for tid in table_ids:
        for f in cache_dir.glob(f"{tid}-*.parquet"):
            f.unlink(missing_ok=True)


def _hole_names(conn: sa.Connection, set_id: int) -> dict[int, str]:
    return {
        int(r.id): r.name
        for r in conn.execute(sa.select(dh_holes.c.id, dh_holes.c.name).where(dh_holes.c.set_id == set_id))
    }


def _attr_columns(attrs: list[dict[str, Any] | None]) -> tuple[list[ColumnInfo], dict[str, np.ndarray]]:
    """Free columns (row attributes, JSON) in first-seen order; numbers when every value is a number."""
    order: dict[str, None] = {}
    for a in attrs:
        for k in a or {}:
            order.setdefault(k, None)
    cols: list[ColumnInfo] = []
    data: dict[str, np.ndarray] = {}
    for name in order:
        vals = [None if a is None else a.get(name) for a in attrs]
        numeric = all(v is None or (isinstance(v, int | float) and not isinstance(v, bool)) for v in vals)
        key = f"a:{name}"
        cols.append(ColumnInfo(key, name, "number" if numeric else "text", role="attribute"))
        if numeric:
            data[key] = np.array([np.nan if v is None else float(v) for v in vals], dtype=float)
        else:
            data[key] = np.array([None if v is None or v == "" else str(v) for v in vals], dtype=object)
    return cols, data


def _floats(values: list[Any]) -> np.ndarray:
    return np.array([np.nan if v is None else float(v) for v in values], dtype=float)


def build_collars(conn: sa.Connection, set_id: int, key: tuple[Any, ...]) -> Snapshot:
    rows = conn.execute(
        sa.select(dh_holes).where(dh_holes.c.set_id == set_id, dh_holes.c.has_collar.is_(True)).order_by(dh_holes.c.id)
    ).all()
    cols = [ColumnInfo(k, t, kind, req, role="fixed") for k, t, kind, req in COLLAR_FIELDS]
    data = {k: _floats([getattr(r, k) for r in rows]) for k, _t, kind, _q in COLLAR_FIELDS if kind == "number"}
    extra, extra_data = _attr_columns([r.attrs for r in rows])
    ids = np.array([r.id for r in rows], dtype=np.int64)
    names = {int(r.id): r.name for r in rows}
    return Snapshot("collar", set_id, "collar", key, cols + extra, ids, ids.copy(), {**data, **extra_data}, names)


def build_surveys(conn: sa.Connection, set_id: int, key: tuple[Any, ...]) -> Snapshot:
    rows = conn.execute(sa.select(dh_surveys).where(dh_surveys.c.set_id == set_id).order_by(dh_surveys.c.id)).all()
    cols = [ColumnInfo(k, t, kind, req, role="fixed") for k, t, kind, req in SURVEY_FIELDS]
    data = {k: _floats([getattr(r, k) for r in rows]) for k, _t, kind, _q in SURVEY_FIELDS if kind == "number"}
    extra, extra_data = _attr_columns([r.attrs for r in rows])
    return Snapshot(
        "survey",
        set_id,
        "survey",
        key,
        cols + extra,
        np.array([r.id for r in rows], dtype=np.int64),
        np.array([r.hole_id for r in rows], dtype=np.int64),
        {**data, **extra_data},
        _hole_names(conn, set_id),
    )


def table_columns(conn: sa.Connection, table_id: int, kind: str) -> list[ColumnInfo]:
    fixed = EVENT_FIELDS if kind == "event" else INTERVAL_FIELDS
    cols = [ColumnInfo(k, t, knd, req, role="fixed") for k, t, knd, req in fixed]
    for c in conn.execute(
        sa.select(dh_columns)
        .where(dh_columns.c.table_id == table_id, dh_columns.c.deleted.is_(False))
        .order_by(dh_columns.c.position, dh_columns.c.id)
    ):
        cols.append(ColumnInfo(f"c{c.id}", c.name, c.kind, False, c.unit, c.role, c.slot))
    return cols


def _decode_vectors(blobs: list[bytes | None], width: int) -> np.ndarray:
    """Rows' float64 vectors → (n, width) matrix, NaN for missing and the cut tail; no per-row numpy work."""
    n = len(blobs)
    out = np.full((n, max(width, 1)), np.nan, dtype="<f8")
    lengths = np.fromiter((0 if b is None else len(b) // 8 for b in blobs), dtype=np.int64, count=n)
    lengths = np.minimum(lengths, width)
    total = int(lengths.sum())
    if total:
        parts = [b[: k * 8] for b, k in zip(blobs, lengths.tolist(), strict=True) if k and b is not None]
        flat = np.frombuffer(b"".join(parts), dtype="<f8")
        rows = np.repeat(np.arange(n), lengths)
        starts = np.cumsum(lengths) - lengths
        slots = np.arange(total) - np.repeat(starts, lengths)
        out[rows, slots] = flat
    return out[:, :width]


def build_rows(
    conn: sa.Connection, set_id: int, table_id: int, key: tuple[Any, ...], cache_dir: Path | None
) -> Snapshot:
    kind = conn.execute(sa.select(dh_tables.c.kind).where(dh_tables.c.id == table_id)).scalar_one()
    cols = table_columns(conn, table_id, kind)
    names = _hole_names(conn, set_id)
    path = cache_dir / f"{table_id}-{key[-2]}-{key[-1]}.parquet" if cache_dir and len(key) >= 2 else None
    if path is not None and path.is_file():
        try:
            return _from_parquet(path, str(table_id), set_id, kind, key, cols, names)
        except Exception:  # noqa: BLE001 - a damaged cache file is rebuilt from the database
            path.unlink(missing_ok=True)
    number_slots = {c.slot: c.key for c in cols if c.kind == "number" and c.slot is not None}
    text_slots = {str(c.slot): c.key for c in cols if c.kind == "text" and c.slot is not None}
    width = max(number_slots, default=-1) + 1

    texts = list(text_slots.items())
    fields = ["id", "hole_id", "from_m", "to_m", "num"] + [
        dialect.json_field_sql(conn, "txt", slot) for slot, _k in texts
    ]
    sql = f"SELECT {', '.join(fields)} FROM dh_rows WHERE table_id = ? ORDER BY id"
    parts: dict[str, list[np.ndarray]] = {k: [] for k in ("id", "hole", "from", "to")}
    text_parts: dict[str, list[np.ndarray]] = {k: [] for _s, k in texts}
    matrices: list[np.ndarray] = []
    for chunk in dialect.iter_driver_rows(conn, sql, (table_id,)):
        got = list(zip(*chunk, strict=True))
        parts["id"].append(np.array(got[0], dtype=np.int64))
        parts["hole"].append(np.array(got[1], dtype=np.int64))
        parts["from"].append(np.array(got[2], dtype=float))  # None → NaN
        parts["to"].append(np.array(got[3], dtype=float))
        matrices.append(_decode_vectors(list(got[4]), width))
        for j, (_s, k) in enumerate(texts):
            text_parts[k].append(np.array(got[5 + j], dtype=object))
        del chunk, got

    def cat(arrs: list[np.ndarray], dtype: Any) -> np.ndarray:
        return np.concatenate(arrs) if arrs else np.zeros(0, dtype=dtype)

    ids = cat(parts["id"], np.int64)
    data: dict[str, np.ndarray] = {"from": cat(parts["from"], float), "to": cat(parts["to"], float)}
    matrix = np.concatenate(matrices) if matrices else np.zeros((0, max(width, 1)))
    del matrices
    for nslot, nk in number_slots.items():
        data[nk] = np.ascontiguousarray(matrix[:, nslot])
    del matrix
    for _s, k in texts:
        data[k] = cat(text_parts[k], object)
    snap = Snapshot(str(table_id), set_id, kind, key, cols, ids, cat(parts["hole"], np.int64), data, names)
    if path is not None and len(ids) >= PARQUET_FROM:
        _to_parquet(snap, path)
    return snap


def _to_parquet(snap: Snapshot, path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    arrays = {"__id": pa.array(snap.ids), "__hole": pa.array(snap.hole_ids)}
    for k, v in snap.data.items():
        if k == "hole":
            continue
        arrays[k] = pa.array(v, type=pa.float64()) if v.dtype.kind == "f" else pa.array(v.tolist(), type=pa.string())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    pq.write_table(pa.table(arrays), tmp, compression="zstd")
    tmp.replace(path)
    for old in path.parent.glob(f"{path.name.split('-')[0]}-*.parquet"):  # earlier revisions of this table
        if old != path:
            old.unlink(missing_ok=True)


def _from_parquet(
    path: Path, table: str, set_id: int, kind: str, key: tuple[Any, ...], cols: list[ColumnInfo], names: dict[int, str]
) -> Snapshot:
    import pyarrow.parquet as pq

    t = pq.read_table(path)
    data: dict[str, np.ndarray] = {}
    for c in [*cols, ColumnInfo("from", "", "number"), ColumnInfo("to", "", "number")]:
        if c.key == "hole" or c.key in data:
            continue
        if c.key not in t.column_names:
            raise KeyError(c.key)  # the columns changed: build again
        col = t.column(c.key)
        # copies: arrays from Arrow are read-only, and edits patch the snapshot in place
        data[c.key] = col.to_numpy().copy() if c.kind == "number" else np.array(col.to_pylist(), dtype=object)
    return Snapshot(
        table,
        set_id,
        kind,
        key,
        cols,
        t.column("__id").to_numpy().copy(),
        t.column("__hole").to_numpy().copy(),
        data,
        names,
    )


# --- cache -----------------------------------------------------------------------------------------


class SnapshotCache:
    """The last few snapshots of one process (the API). Builds run outside the lock of other tables."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: OrderedDict[tuple[Any, ...], Snapshot] = OrderedDict()
        self._building: dict[tuple[Any, ...], threading.Lock] = {}

    def get(self, key: tuple[Any, ...], build: Callable[[], Snapshot]) -> Snapshot:
        with self._lock:
            snap = self._items.get(key)
            if snap is not None:
                self._items.move_to_end(key)
                return snap
            gate = self._building.setdefault(key, threading.Lock())
        with gate:  # one build per key; the others wait for it
            with self._lock:
                snap = self._items.get(key)
            if snap is None:
                snap = build()
                self._put(snap)
            with self._lock:
                self._building.pop(key, None)
            return snap

    def _put(self, snap: Snapshot) -> None:
        with self._lock:
            for k in [k for k in self._items if k[:-1] == snap.key[:-1]]:  # older revisions of the same table
                del self._items[k]
            self._items[snap.key] = snap
            while len(self._items) > CACHE_SIZE:
                self._items.popitem(last=False)

    def patched(self, old: tuple[Any, ...], new: tuple[Any, ...], patch: Callable[[Snapshot], None]) -> None:
        """An edit moved the table to key `new`: patch the cached snapshot instead of dropping it."""
        with self._lock:
            snap = self._items.pop(old, None)
        if snap is None:
            return
        patch(snap)
        snap.key = new
        snap.orders.clear()
        snap.masks.clear()
        self._put(snap)

    def forget(self, set_id: int | None = None) -> None:
        with self._lock:
            for k in [k for k in self._items if set_id is None or k[1] == set_id]:
                del self._items[k]


snapshots = SnapshotCache()
