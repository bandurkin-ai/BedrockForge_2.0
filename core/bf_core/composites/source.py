"""Reading the input of a sample-processing operation (ADR 0018 §2, §4): only the chosen columns of one table.

The rows come as «lines»: ordered by (hole name, FROM, TO, row id) — the order does not depend on the ids the
database gave (`.bfggis` renumbers them, an import may write holes in any order); `starts` cut the lines into
holes (`rows[starts[i]:starts[i + 1]]` belong to `holes[i]`). Rows without FROM or TO cannot be placed on a
hole: they are left out and counted (`skipped`), never silently.

Numbers are float64 (NaN — missing, never 0); text columns become int32 codes into sorted labels (−1 — empty),
so the codes do not depend on the order of rows either. The columns are read from the Parquet snapshot of
the table (`cache/tables/<id>-<token>-<rev>.parquet`, Э6) when it is there, else by a narrow read of
`dh_rows` (the vectors decoded only up to the last wanted slot, JSON only for the wanted text slots).

`input_hash` — SHA-256 of what the result depends on (8.0.2): the bytes of the chosen columns, FROM, TO, the
hole names, the trajectory stations, the geometry keys of wireframes and the parameters; rows in a canonical
order, so the same data written in another order gives the same hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_tables

from .results import _token


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
    hole_ids: np.ndarray  # int64 dh_holes.id of every row
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


def read(
    engine: sa.Engine, set_id: int, table_id: int, fields: Sequence[str], *, cache_dir: Path | None = None
) -> Lines:
    """The chosen columns (names or keys `c<id>`) of a ready table of the set, as lines."""
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
        names = {
            int(r.id): r.name
            for r in conn.execute(sa.select(dh_holes.c.id, dh_holes.c.name).where(dh_holes.c.set_id == set_id))
        }
        path = None if cache_dir is None else cache_dir / f"{table_id}-{token}-{t.rev}.parquet"
        got = None
        if path is not None and path.is_file():
            try:
                got = _from_parquet(path, chosen)
            except Exception:  # noqa: BLE001 - a damaged or older snapshot: read the rows instead
                got = None
        source = "parquet" if got is not None else "rows"
        if got is None:
            got = _from_rows(conn, table_id, chosen)
    ids, hole_ids, f, to, cols = got
    return _lines(set_id, t, token, chosen, names, ids, hole_ids, f, to, cols, source)


def _from_parquet(path: Path, chosen: dict[str, Field]) -> tuple[Any, ...]:
    import pyarrow.parquet as pq

    with pq.ParquetFile(path) as pf:
        wanted = ["__id", "__hole", "from", "to", *(c.key for c in chosen.values())]
        tbl = pf.read(columns=wanted)
    cols: dict[str, np.ndarray] = {}
    for c in chosen.values():
        col = tbl.column(c.key)
        cols[c.name] = (
            col.to_numpy().astype(float, copy=False)
            if c.kind == "number"
            else col.to_numpy(zero_copy_only=False).astype(object, copy=False)
        )
    arr = [tbl.column(k).to_numpy() for k in ("__id", "__hole", "from", "to")]
    return arr[0].astype(np.int64), arr[1].astype(np.int64), arr[2].astype(float), arr[3].astype(float), cols


def _decode(blobs: Sequence[bytes | None], width: int) -> np.ndarray:
    """Vectors → (n, width) float64, NaN for missing and the cut tail; longer vectors are cut at `width`."""
    from bf_core.drillholes.snapshot import _decode_vectors

    return _decode_vectors(list(blobs), width)


def _from_rows(conn: sa.Connection, table_id: int, chosen: dict[str, Field]) -> tuple[Any, ...]:
    nums = [c for c in chosen.values() if c.kind == "number"]
    texts = [c for c in chosen.values() if c.kind == "text"]
    width = max((c.slot for c in nums), default=-1) + 1
    sel = ["id", "hole_id", "from_m", "to_m"] + (["num"] if nums else [])
    sel += [dialect.json_field_sql(conn, "txt", str(c.slot)) for c in texts]
    sql = f"SELECT {', '.join(sel)} FROM dh_rows WHERE table_id = ? ORDER BY id"
    parts: list[list[np.ndarray]] = [[], [], [], []]
    mats: list[np.ndarray] = []
    text_parts: dict[str, list[np.ndarray]] = {c.name: [] for c in texts}
    for chunk in dialect.iter_driver_rows(conn, sql, (table_id,)):
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

    def cat(arrs: list[np.ndarray], dtype: Any) -> np.ndarray:
        return np.concatenate(arrs) if arrs else np.zeros(0, dtype=dtype)

    cols: dict[str, np.ndarray] = {}
    if nums:
        matrix = np.concatenate(mats) if mats else np.zeros((0, width))
        for c in nums:
            cols[c.name] = np.ascontiguousarray(matrix[:, c.slot])
        del matrix
    for c in texts:
        cols[c.name] = cat(text_parts[c.name], object)
    return cat(parts[0], np.int64), cat(parts[1], np.int64), cat(parts[2], float), cat(parts[3], float), cols


def _lines(
    set_id: int,
    t: Any,
    token: str,
    chosen: dict[str, Field],
    names: dict[int, str],
    ids: np.ndarray,
    hole_ids: np.ndarray,
    f: np.ndarray,
    to: np.ndarray,
    cols: dict[str, np.ndarray],
    source: str,
) -> Lines:
    if t.kind == "event":  # one depth: «до» follows «от»
        to = np.where(np.isnan(to), f, to)
    keep = ~np.isnan(f) & ~np.isnan(to)
    skipped = int(len(keep) - keep.sum())
    uniq = np.unique(hole_ids[keep]) if len(ids) else np.zeros(0, dtype=np.int64)
    hole_names = [names.get(int(h), "") for h in uniq.tolist()]
    by_name = np.argsort(np.array(hole_names, dtype=object), kind="stable") if hole_names else np.zeros(0, int)
    rank = np.empty(len(uniq), dtype=np.int64)
    rank[by_name] = np.arange(len(uniq))
    rows = np.flatnonzero(keep)
    h_rank = rank[np.searchsorted(uniq, hole_ids[rows])] if len(rows) else np.zeros(0, dtype=np.int64)
    order = rows[np.lexsort((ids[rows], to[rows], f[rows], h_rank))] if len(rows) else rows
    h_sorted = rank[np.searchsorted(uniq, hole_ids[order])] if len(order) else np.zeros(0, dtype=np.int64)
    counts = np.bincount(h_sorted, minlength=len(uniq)) if len(uniq) else np.zeros(0, dtype=np.int64)
    out = Lines(
        set_id=set_id,
        table_id=int(t.id),
        name=t.name,
        name_key=t.name_key,
        token=token,
        rev=int(t.rev),
        kind=t.kind,
        fields=chosen,
        row_ids=ids[order],
        hole_ids=hole_ids[order],
        holes=[hole_names[i] for i in by_name.tolist()],
        hole_db_ids=uniq[by_name],
        starts=np.concatenate(([0], np.cumsum(counts))).astype(np.int64),
        from_m=np.ascontiguousarray(f[order]),
        to_m=np.ascontiguousarray(to[order]),
        skipped=skipped,
        source=source,
    )
    for c in chosen.values():
        v = cols[c.name][order]
        if c.kind == "number":
            out.numbers[c.name] = np.ascontiguousarray(v, dtype=float)
        else:
            out.codes[c.name], out.labels[c.name] = text_codes(v)
    return out


def text_codes(values: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """int32 codes into sorted labels; None and "" → −1."""
    import pandas as pd

    vals = np.array([None if v is None or v == "" else str(v) for v in values.tolist()], dtype=object)
    codes, uniques = pd.factorize(vals, sort=True, use_na_sentinel=True)
    return codes.astype(np.int32), [str(u) for u in uniques.tolist()]


# --- trajectories --------------------------------------------------------------------------------------


def trajectories(project: Any, set_id: int, method: str = "minimum_curvature") -> Any:
    """Trajectories of the set (`drillholes.trajectory`, cached by the set version); `project` needs
    `engine` and `uid`."""
    from bf_core.drillholes.trajectory import trajectories as build

    return build(project, set_id, method)


def stations_bytes(st: Any, hole_db_ids: Sequence[int]) -> bytes:
    """The stations (depth, X, Y, Z) of the holes in the given order — for `input_hash`."""
    out = bytearray()
    skipped = set(np.asarray(st.traj.skipped).tolist())
    for h in hole_db_ids:
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
) -> str:
    """SHA-256 (hex) of the inputs of a result (ADR 0018 §2)."""
    h = hashlib.sha256()

    def put(tag: str, data: bytes) -> None:
        h.update(tag.encode() + len(data).to_bytes(8, "little") + data)

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
