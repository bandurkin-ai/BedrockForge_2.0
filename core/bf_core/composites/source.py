"""Reading the input of a sample-processing operation (ADR 0018 §2, §4): only the chosen columns of one table.

The rows come as «lines»: ordered by (hole name, FROM, TO, row id) — the order does not depend on the ids the
database gave (`.bfggis` renumbers them, an import may write holes in any order); `starts` cut the lines into
holes (`rows[starts[i]:starts[i + 1]]` belong to `holes[i]`). Rows without FROM or TO cannot be placed on a
hole: they are left out and counted (`skipped`), never silently.

Numbers are float64 (NaN — missing, never 0); text columns become int32 codes into sorted labels (−1 — empty),
so the codes do not depend on the order of rows either. The columns are read from the Parquet snapshot of
the table (`cache/tables/<id>-<token>-<rev>.parquet`, Э6) when it is there, else by a narrow read of
`dh_rows` (the vectors decoded only up to the last wanted slot, JSON only for the wanted text slots).

5 million rows of 10 fields are read within the memory of the columns themselves (ТЗ 8.2.3: the job — ≤ 2 ГБ):
the snapshot is read column by column, each put into line order (when the rows are not in it already — an
import writes them hole by hole, by depth — and then nothing is sorted or copied) and released before the
next; a text column comes as the dictionary of the file and the indices into it, no string per row. `tick`
(the job's) is called between the columns, the stations and the parts of the hash: a cancel is answered there.

`input_hash` — SHA-256 of what the result depends on (8.0.2): the bytes of the chosen columns, FROM, TO, the
hole names, the trajectory stations, the geometry keys of wireframes and the parameters; rows in a canonical
order, so the same data written in another order gives the same hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_tables

from .results import _token

Tick = Callable[[float | None, str | None], None]


def _beat(tick: Tick | None) -> None:
    """A sign of life between two steps of a reading (no progress, no text): a cancel is raised here."""
    if tick is not None:
        tick(None, None)


@dataclass
class Field:
    name: str
    kind: str  # number | text
    key: str  # c<dh_columns.id>
    slot: int
    unit: str | None = None
    role: str = "value"


@dataclass
class Lines:
    """The chosen columns of one table, rows in line order."""

    set_id: int
    table_id: int
    name: str
    name_key: str
    token: str
    rev: int
    kind: str  # assay | interval | event
    fields: dict[str, Field]  # by the stored column name
    row_ids: np.ndarray  # int64 dh_rows.id
    holes: list[str]  # hole names in line order (sorted)
    hole_db_ids: np.ndarray  # int64 dh_holes.id of every hole
    starts: np.ndarray  # int64, len(holes) + 1
    from_m: np.ndarray  # float64
    to_m: np.ndarray
    numbers: dict[str, np.ndarray] = field(default_factory=dict)
    codes: dict[str, np.ndarray] = field(default_factory=dict)  # int32, −1 empty
    labels: dict[str, list[str]] = field(default_factory=dict)
    skipped: int = 0  # rows without FROM or TO
    source: str = "rows"  # parquet | rows — where the columns came from

    @property
    def rows(self) -> int:
        return len(self.row_ids)

    @property
    def hole_ids(self) -> np.ndarray:
        """int64 dh_holes.id of every row (made on demand: the holes and `starts` hold it)."""
        return np.repeat(self.hole_db_ids, np.diff(self.starts))  # type: ignore[no-any-return]

    def hole_of(self) -> np.ndarray:
        """Dense hole index (0…len(holes)-1) of every row."""
        return np.repeat(np.arange(len(self.holes), dtype=np.int32), np.diff(self.starts))

    def input(self) -> tuple[int, list[str]]:
        """(table id, fields) for `results.make_inputs`."""
        return self.table_id, list(self.fields)


def _not_found() -> ApiError:
    return ApiError(404, "table_not_found", "Таблица не найдена.", "Обновите дерево проекта.")


def _fields(conn: sa.Connection, table_id: int, names: Iterable[str]) -> dict[str, Field]:
    cols = conn.execute(
        sa.select(dh_columns)
        .where(dh_columns.c.table_id == table_id, dh_columns.c.deleted.is_(False))
        .order_by(dh_columns.c.position, dh_columns.c.id)
    ).all()
    by_name = {c.name.casefold(): c for c in cols}
    by_key = {f"c{c.id}": c for c in cols}
    out: dict[str, Field] = {}
    for n in names:
        c = by_key.get(n) or by_name.get(str(n).casefold())
        if c is None:
            raise ApiError(
                422, "field_not_found", f"В таблице нет поля «{n}».", "Выберите поле из списка колонок таблицы."
            )
        out[c.name] = Field(c.name, c.kind, f"c{c.id}", int(c.slot), c.unit, c.role)
    return out


def _hole_names(conn: sa.Connection, set_id: int, holes: Sequence[int] | None) -> dict[int, str]:
    """Names of the holes of the set by id; `holes` — only of these (the preview of one hole)."""
    q = sa.select(dh_holes.c.id, dh_holes.c.name).where(dh_holes.c.set_id == set_id)
    if holes is not None:
        q = q.where(dh_holes.c.id.in_(_few(holes)))
    return {int(r.id): r.name for r in conn.execute(q)}


def _few(holes: Sequence[int]) -> list[int]:
    return sorted({int(h) for h in holes})[:500]  # a preview reads one hole; parameters stay few


def read(
    engine: sa.Engine,
    set_id: int,
    table_id: int,
    fields: Sequence[str],
    *,
    cache_dir: Path | None = None,
    holes: Sequence[int] | None = None,
    tick: Tick | None = None,
) -> Lines:
    """The chosen columns (names or keys `c<id>`) of a ready table of the set, as lines. `holes` — only the rows
    of these holes (dh_holes.id; the preview of one hole: an indexed read of `dh_rows`, no snapshot)."""
    with dialect.read_tx(engine) as conn:
        t = conn.execute(
            sa.select(dh_tables).where(
                dh_tables.c.id == table_id, dh_tables.c.set_id == set_id, dh_tables.c.state == "ready"
            )
        ).first()
        if t is None:
            raise _not_found()
        chosen = _fields(conn, table_id, fields)
        token = _token(t.created_at)
        names = _hole_names(conn, set_id, holes)
        path = None if cache_dir is None else cache_dir / f"{table_id}-{token}-{t.rev}.parquet"
        snapshot = path is not None and holes is None and path.is_file()
        got = None if snapshot else _from_rows(conn, table_id, chosen, holes, tick)
    if got is None:
        assert path is not None
        try:
            return _from_parquet(path, set_id, t, token, chosen, names, tick)
        except Exception as exc:  # noqa: BLE001 - a damaged or older snapshot: read the rows instead
            from bf_core.jobs.context import JobCancelled

            if isinstance(exc, JobCancelled):
                raise
        with dialect.read_tx(engine) as conn:
            got = _from_rows(conn, table_id, chosen, holes, tick)
    ids, hole_ids, f, to, cols = got
    if t.kind == "event":  # one depth: «до» follows «от»
        to = np.where(np.isnan(to), f, to)
    lay = _layout(names, ids, hole_ids, f, to)
    del hole_ids
    out = _lines(set_id, t, token, chosen, lay, lay.take(ids), lay.take(f), lay.take(to), "rows")
    del ids, f, to
    for c in chosen.values():
        col = lay.take(cols.pop(c.name))
        if c.kind == "number":
            out.numbers[c.name] = col
        else:
            out.codes[c.name], out.labels[c.name] = text_codes(col)
        _beat(tick)
    return out


def _from_parquet(
    path: Path, set_id: int, t: Any, token: str, chosen: dict[str, Field], names: dict[int, str], tick: Tick | None
) -> Lines:
    """The lines from the snapshot: the fixed columns give the order of the rows, then every chosen column is
    read, put into it and released — the peak is the columns kept and one more."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    texts = [c.key for c in chosen.values() if c.kind == "text"]
    with pq.ParquetFile(path, read_dictionary=texts) as pf:

        def column(key: str) -> Any:
            col = pf.read(columns=[key]).column(0)
            _beat(tick)
            return col

        def number(key: str, dtype: Any = np.float64) -> np.ndarray:
            return np.asarray(column(key).to_numpy(), dtype=dtype)  # nulls of a float column come as NaN

        ids, hole_ids, f, to = number("__id", np.int64), number("__hole", np.int64), number("from"), number("to")
        if t.kind == "event":
            to = np.where(np.isnan(to), f, to)
        lay = _layout(names, ids, hole_ids, f, to)
        del hole_ids
        out = _lines(set_id, t, token, chosen, lay, lay.take(ids), lay.take(f), lay.take(to), "parquet")
        del ids, f, to
        for c in chosen.values():
            if c.kind == "number":
                out.numbers[c.name] = lay.take(number(c.key))
                continue
            col = column(c.key)
            if not pa.types.is_dictionary(col.type):  # not a dictionary in the file: strings, the slow way
                values = col.to_numpy(zero_copy_only=False).astype(object, copy=False)
                out.codes[c.name], out.labels[c.name] = text_codes(lay.take(values))
                continue
            whole = col.unify_dictionaries().combine_chunks()  # every row group has a dictionary of its own
            whole = whole.chunk(0) if isinstance(whole, pa.ChunkedArray) else whole
            index = lay.take(np.asarray(whole.indices.fill_null(-1).to_numpy(zero_copy_only=False), dtype=np.int32))
            out.codes[c.name], out.labels[c.name] = _dictionary_codes(index, whole.dictionary.to_pylist())
    return out


def _dictionary_codes(index: np.ndarray, values: list[Any]) -> tuple[np.ndarray, list[str]]:
    """Indices into the dictionary of a text column (−1 — null) → int32 codes into the sorted labels the rows
    have (−1 — empty): what `text_codes` gives for the strings themselves. "" is empty; a value no row of the
    lines has (it stood only in rows left out) is not a label."""
    labels_of = [None if v is None or v == "" else str(v) for v in values]
    used = np.bincount(index[index >= 0], minlength=len(values)).astype(bool) if len(values) else np.zeros(0, bool)
    labels = sorted({v for v, u in zip(labels_of, used.tolist(), strict=True) if u and v is not None})
    at = {v: i for i, v in enumerate(labels)}
    remap = np.array([*(at.get(v, -1) if v is not None else -1 for v in labels_of), -1], dtype=np.int32)
    return remap[index], labels  # −1 → the last one: −1


def _decode(blobs: Sequence[bytes | None], width: int) -> np.ndarray:
    """Vectors → (n, width) float64, NaN for missing and the cut tail; longer vectors are cut at `width`."""
    from bf_core.drillholes.snapshot import _decode_vectors

    return _decode_vectors(list(blobs), width)


def _from_rows(
    conn: sa.Connection,
    table_id: int,
    chosen: dict[str, Field],
    holes: Sequence[int] | None = None,
    tick: Tick | None = None,
) -> tuple[Any, ...]:
    nums = [c for c in chosen.values() if c.kind == "number"]
    texts = [c for c in chosen.values() if c.kind == "text"]
    width = max((c.slot for c in nums), default=-1) + 1
    sel = ["id", "hole_id", "from_m", "to_m"] + (["num"] if nums else [])
    sel += [dialect.json_field_sql(conn, "txt", str(c.slot)) for c in texts]
    sql = f"SELECT {', '.join(sel)} FROM dh_rows WHERE table_id = ?"
    args: tuple[Any, ...] = (table_id,)
    if holes is not None:
        wanted = _few(holes)
        sql += f" AND hole_id IN ({', '.join('?' * len(wanted))})" if wanted else " AND 0 = 1"
        args += tuple(wanted)
    sql += " ORDER BY id"
    parts: list[list[np.ndarray]] = [[], [], [], []]
    mats: list[np.ndarray] = []
    text_parts: dict[str, list[np.ndarray]] = {c.name: [] for c in texts}
    for chunk in dialect.iter_driver_rows(conn, sql, args):
        got = list(zip(*chunk, strict=True))
        parts[0].append(np.array(got[0], dtype=np.int64))
        parts[1].append(np.array(got[1], dtype=np.int64))
        parts[2].append(np.array(got[2], dtype=float))  # None → NaN
        parts[3].append(np.array(got[3], dtype=float))
        j = 4
        if nums:
            mats.append(_decode(got[4], width))
            j = 5
        for k, c in enumerate(texts):
            text_parts[c.name].append(np.array(got[j + k], dtype=object))
        del chunk, got
        _beat(tick)

    def cat(arrs: list[np.ndarray], dtype: Any) -> np.ndarray:
        return np.concatenate(arrs) if arrs else np.zeros(0, dtype=dtype)

    cols: dict[str, np.ndarray] = {}
    if nums:
        matrix = np.concatenate(mats) if mats else np.zeros((0, width))
        del mats
        for c in nums:
            cols[c.name] = np.ascontiguousarray(matrix[:, c.slot])
        del matrix
    for c in texts:
        cols[c.name] = cat(text_parts[c.name], object)
    return cat(parts[0], np.int64), cat(parts[1], np.int64), cat(parts[2], float), cat(parts[3], float), cols


@dataclass
class _Layout:
    """Where the rows of a table go in its lines."""

    order: np.ndarray | None  # the rows kept, in line order; None — all of them, as they are
    skipped: int
    holes: list[str]
    hole_db_ids: np.ndarray
    starts: np.ndarray

    def take(self, column: np.ndarray) -> np.ndarray:
        """A column of the table in line order: C-contiguous and writable (one numba specialization of every
        kernel; an Arrow buffer is read-only) — the column itself when nothing moves and it is such."""
        if self.order is not None:
            return column[self.order]  # type: ignore[no-any-return]
        if column.flags.c_contiguous and column.flags.writeable:
            return column
        return np.array(column, order="C")


def _layout(names: dict[int, str], ids: np.ndarray, hole_ids: np.ndarray, f: np.ndarray, to: np.ndarray) -> _Layout:
    """The line order of rows: by (hole name, FROM, TO, row id), rows without FROM or TO left out. Rows that
    already come so (an import writes a table hole by hole, by depth; a result is written in line order) are
    found by one pass — nothing is sorted and no column is copied."""
    n = len(ids)
    keep = ~(np.isnan(f) | np.isnan(to))
    kept = int(keep.sum())
    if kept == n and n:
        cut = np.flatnonzero(hole_ids[1:] != hole_ids[:-1]) + 1
        runs = hole_ids[np.concatenate(([0], cut))]  # the hole of every run of rows
        run_names = [names.get(int(h), "") for h in runs.tolist()]
        by_hole = all(a < b for a, b in zip(run_names, run_names[1:], strict=False)) or (
            len(np.unique(runs)) == len(runs)
            and all(a <= b for a, b in zip(zip(run_names, runs.tolist(), strict=True),
                                           zip(run_names[1:], runs[1:].tolist(), strict=True), strict=False))
        )  # fmt: skip
        if by_hole:
            f0, f1, t0, t1 = f[:-1], f[1:], to[:-1], to[1:]
            rising = (f1 > f0) | ((f1 == f0) & ((t1 > t0) | ((t1 == t0) & (ids[1:] > ids[:-1]))))
            rising[cut - 1] = True  # the first row of a hole follows another hole
            if bool(rising.all()):
                starts = np.concatenate(([0], cut, [n])).astype(np.int64)
                return _Layout(None, 0, run_names, np.ascontiguousarray(runs, dtype=np.int64), starts)
    uniq = np.unique(hole_ids[keep]) if n else np.zeros(0, dtype=np.int64)
    hole_names = [names.get(int(h), "") for h in uniq.tolist()]
    by_name = np.argsort(np.array(hole_names, dtype=object), kind="stable") if hole_names else np.zeros(0, int)
    rank = np.empty(len(uniq), dtype=np.int64)
    rank[by_name] = np.arange(len(uniq))
    rows = np.flatnonzero(keep)
    h_rank = rank[np.searchsorted(uniq, hole_ids[rows])] if len(rows) else np.zeros(0, dtype=np.int64)
    order = rows[np.lexsort((ids[rows], to[rows], f[rows], h_rank))] if len(rows) else rows
    counts = np.bincount(h_rank, minlength=len(uniq)) if len(uniq) else np.zeros(0, dtype=np.int64)
    return _Layout(
        order,
        n - kept,
        [hole_names[i] for i in by_name.tolist()],
        uniq[by_name],
        np.concatenate(([0], np.cumsum(counts))).astype(np.int64),
    )


def _lines(
    set_id: int,
    t: Any,
    token: str,
    chosen: dict[str, Field],
    lay: _Layout,
    ids: np.ndarray,
    f: np.ndarray,
    to: np.ndarray,
    source: str,
) -> Lines:
    """The lines without their columns (the callers add them one by one); `ids`, `f`, `to` — in line order."""
    return Lines(
        set_id=set_id,
        table_id=int(t.id),
        name=t.name,
        name_key=t.name_key,
        token=token,
        rev=int(t.rev),
        kind=t.kind,
        fields=chosen,
        row_ids=ids,
        holes=lay.holes,
        hole_db_ids=lay.hole_db_ids,
        starts=lay.starts,
        from_m=f,
        to_m=to,
        skipped=lay.skipped,
        source=source,
    )


def text_codes(values: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """int32 codes into sorted labels; None and "" → −1."""
    import pandas as pd

    vals = np.array([None if v is None or v == "" else str(v) for v in values.tolist()], dtype=object)
    codes, uniques = pd.factorize(vals, sort=True, use_na_sentinel=True)
    return codes.astype(np.int32), [str(u) for u in uniques.tolist()]


# --- trajectories --------------------------------------------------------------------------------------


def trajectories(project: Any, set_id: int, method: str = "minimum_curvature", *, tick: Tick | None = None) -> Any:
    """Trajectories of the set (`drillholes.trajectory`, cached by the set version); `project` needs
    `engine` and `uid`. With `tick` (a job) they are built on a thread of their own while this one ticks:
    50 thousand holes take over a second, and the builder has no place to ask for a cancel. A cancel waits
    for the builder to end (it writes nothing) and is raised then."""
    from bf_core.drillholes.trajectory import trajectories as build

    if tick is None:
        return build(project, set_id, method)
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as Waiting

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="bf-trajectories") as pool:
        job = pool.submit(build, project, set_id, method)
        while True:
            try:
                return job.result(timeout=0.2)
            except Waiting:
                tick(None, None)


def stations_bytes(st: Any, hole_db_ids: Sequence[int], *, tick: Tick | None = None) -> bytes:
    """The stations (depth, X, Y, Z) of the holes in the given order — for `input_hash`."""
    out = bytearray()
    skipped = set(np.asarray(st.traj.skipped).tolist())
    for k, h in enumerate(hole_db_ids):
        if not k % 5000:
            _beat(tick)
        i = st.index.get(int(h))
        if i is None or i in skipped:
            out += b"\xff" * 8
            continue
        md, xyz, _d = st.traj.stations(i)
        out += np.ascontiguousarray(md, dtype="<f8").tobytes() + np.ascontiguousarray(xyz, dtype="<f8").tobytes()
        out += b"\xfe" * 8
    return bytes(out)


# --- SHA-256 of the inputs -------------------------------------------------------------------------------


def _canon(a: np.ndarray) -> np.ndarray:
    """float64 LE with one NaN pattern and +0.0 for −0.0: equal values give equal bytes."""
    a = np.asarray(a, dtype="<f8")
    return np.where(np.isnan(a), np.nan, a + 0.0).astype("<f8")


def _canonical_order(lines: Lines) -> np.ndarray | None:
    """Line order already sorts by (hole, FROM, TO); rows equal in these three are sorted by their values, so
    the order of ids does not show. None — no such ties (the usual case: no sorting at all)."""
    h = lines.hole_of()
    f, t = lines.from_m, lines.to_m
    if len(h) < 2 or not np.any((h[1:] == h[:-1]) & (f[1:] == f[:-1]) & (t[1:] == t[:-1])):
        return None
    keys: list[np.ndarray] = [lines.codes[n] for n in sorted(lines.codes, reverse=True)]
    keys += [_canon(lines.numbers[n]) for n in sorted(lines.numbers, reverse=True)]
    return np.lexsort((*keys, t, f, h))


def input_hash(
    tables: Sequence[Lines],
    params: dict[str, Any],
    *,
    stations: bytes | None = None,
    meshes: Sequence[str] = (),
    algo: str = "",
    tick: Tick | None = None,
) -> str:
    """SHA-256 (hex) of the inputs of a result (ADR 0018 §2). `tick` — after every column."""
    h = hashlib.sha256()

    def put(tag: str, data: bytes) -> None:
        h.update(tag.encode() + len(data).to_bytes(8, "little"))
        h.update(data)  # not joined with the tag: a column of 5 million rows is not copied for it
        _beat(tick)

    put("params", json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
    put("algo", algo.encode())
    for lines in tables:
        order = _canonical_order(lines)

        def arr(a: np.ndarray, o: np.ndarray | None = order) -> np.ndarray:
            return a if o is None else a[o]

        put("holes", "\0".join(lines.holes).encode())
        put("starts", lines.starts.astype("<i8").tobytes())
        put("from", _canon(arr(lines.from_m)).tobytes())
        put("to", _canon(arr(lines.to_m)).tobytes())
        for name in sorted(lines.numbers):
            put("num:" + name, _canon(arr(lines.numbers[name])).tobytes())
        for name in sorted(lines.codes):
            put("txt:" + name, "\0".join(lines.labels[name]).encode())
            put("codes:" + name, arr(lines.codes[name]).astype("<i4").tobytes())
    if stations is not None:
        put("stations", stations)
    for key in meshes:
        put("mesh", key.encode())
    return h.hexdigest()
