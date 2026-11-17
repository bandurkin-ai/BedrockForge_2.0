"""Writing drillhole sets (ТЗ 7.7, 7.10; ADR 0003 §4).

Interval tables store all number columns of a row as one float64 little-endian vector (`dh_rows.num`,
slot = `dh_columns.slot`, NaN = missing, never 0) and text columns as `{slot: text}`.

Import «Заменить» never leaves a half-written table: a new table is filled in portions (≤ 50 000 rows a
transaction, writers are not blocked) with state «building», and one last transaction drops the old table
of that name and makes the new one «ready». Cancellation drops building tables and holes left without data.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_rows, dh_surveys, dh_tables, import_log, objects

BATCH = 50_000
SET_KIND = "drillhole_set"
TABLE_KINDS = ("assay", "interval", "event")

Tick = Callable[[float | None, str | None], None]


def pack(values: Sequence[float | None]) -> bytes | None:
    """Float64 LE vector; None/NaN → NaN; trailing missing slots are cut (short vector = missing tail)."""
    import numpy as np

    arr = np.array([math.nan if v is None else float(v) for v in values], dtype="<f8")
    n = len(arr)
    while n and math.isnan(arr[n - 1]):
        n -= 1
    return arr[:n].tobytes() if n else None


def unpack(data: bytes | None, width: int) -> list[float]:
    """Values of `width` slots (missing → NaN)."""
    import numpy as np

    out = np.full(width, np.nan, dtype="<f8")
    if data:
        got = np.frombuffer(data, dtype="<f8")
        out[: min(width, len(got))] = got[:width]
    return [float(v) for v in out]


# --- sets and holes -----------------------------------------------------------------------------


class NameTaken(Exception):
    """A set of that name exists (or is being built by another job)."""


def create_set(conn: sa.Connection, name: str, *, job_id: int | None = None, crs: str | None = None) -> int:
    taken = conn.execute(
        sa.select(objects.c.id).where(
            objects.c.kind == SET_KIND,
            objects.c.name_key == dialect.name_key(name),
            objects.c.state.in_(("ready", "building")),
        )
    ).first()
    if taken is not None:
        raise NameTaken(name)
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(objects).values(
            kind=SET_KIND,
            name=name,
            name_key=dialect.name_key(name),
            sort_key=0.0,
            state="building" if job_id is not None else "ready",
            version=1,
            meta={},
            crs=crs,
            job_id=job_id,
            created_at=now,
            updated_at=now,
        )
    )
    key = res.inserted_primary_key
    assert key is not None
    return int(key[0])


def find_set(conn: sa.Connection, name: str) -> int | None:
    row = conn.execute(
        sa.select(objects.c.id).where(
            objects.c.kind == SET_KIND, objects.c.name_key == dialect.name_key(name), objects.c.state == "ready"
        )
    ).first()
    return int(row.id) if row else None


def hole_ids(conn: sa.Connection, set_id: int) -> dict[str, int]:
    return {
        r.name: int(r.id)
        for r in conn.execute(sa.select(dh_holes.c.id, dh_holes.c.name).where(dh_holes.c.set_id == set_id))
    }


def ensure_holes(conn: sa.Connection, set_id: int, names: Iterable[str], known: dict[str, int]) -> None:
    """Holes met in surveys or tables but not in collars (has_collar = False); `known` is updated.
    No IN list of names: 50 000 holes exceed the SQLite parameter limit (32 766)."""
    missing = sorted({n for n in names if n not in known})
    if not missing:
        return
    for start in range(0, len(missing), BATCH):
        conn.execute(
            sa.insert(dh_holes),
            [{"set_id": set_id, "name": n, "has_collar": False} for n in missing[start : start + BATCH]],
        )
    known.update(hole_ids(conn, set_id))


@dataclass
class CollarRow:
    name: str
    x: float | None = None
    y: float | None = None
    z: float | None = None
    td: float | None = None
    azimuth: float | None = None
    dip: float | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


def _num(v: Any) -> float | None:
    if v is None:
        return None
    f = float(v)
    return None if math.isnan(f) else f


def replace_collars(conn: sa.Connection, set_id: int, rows: Sequence[CollarRow]) -> dict[str, int]:
    """«Заменить» collars of one set: holes present get the new collar, holes absent lose theirs (their
    surveys and samples stay), new names become holes. Returns counts."""
    known = hole_ids(conn, set_id)
    names = {r.name for r in rows}
    gone = [hid for name, hid in known.items() if name not in names]
    for start in range(0, len(gone), 10_000):  # ids in portions: under the SQLite parameter limit
        conn.execute(
            sa.update(dh_holes)
            .where(dh_holes.c.id.in_(gone[start : start + 10_000]))
            .values(x=None, y=None, z=None, td=None, azimuth=None, dip=None, has_collar=False, attrs=None)
        )
    added = updated = 0
    for r in rows:
        values = {
            "x": _num(r.x),
            "y": _num(r.y),
            "z": _num(r.z),
            "td": _num(r.td),
            "azimuth": _num(r.azimuth),
            "dip": _num(r.dip),
            "has_collar": True,
            "attrs": r.attrs or None,
        }
        if r.name in known:
            conn.execute(sa.update(dh_holes).where(dh_holes.c.id == known[r.name]).values(**values))
            updated += 1
        else:
            conn.execute(sa.insert(dh_holes).values(set_id=set_id, name=r.name, **values))
            added += 1
    return {"added": added, "updated": updated, "collars": len(rows)}


#: (hole, depth, azimuth, dip[, attributes])
SurveyRow = tuple[Any, ...]


def replace_surveys(engine: sa.Engine, set_id: int, rows: Sequence[SurveyRow], tick: Tick) -> dict[str, int]:
    """«Заменить» surveys of one set: (hole, depth, azimuth, dip). One transaction: surveys are small enough
    (tens of thousands of holes × tens of readings)."""
    with dialect.write_tx(engine) as conn:
        return replace_surveys_in(conn, set_id, rows, tick)


def replace_surveys_in(conn: sa.Connection, set_id: int, rows: Sequence[SurveyRow], tick: Tick) -> dict[str, int]:
    """The same inside the caller's transaction (the final transaction of an import)."""
    known = hole_ids(conn, set_id)
    ensure_holes(conn, set_id, (r[0] for r in rows), known)
    conn.execute(sa.delete(dh_surveys).where(dh_surveys.c.set_id == set_id))
    for start in range(0, len(rows), BATCH):
        tick(None, f"Инклинометрия: {start} из {len(rows)}")
        part = rows[start : start + BATCH]
        conn.execute(
            sa.insert(dh_surveys),
            [
                {
                    "set_id": set_id,
                    "hole_id": known[r[0]],
                    "depth": float(r[1]),
                    "azimuth": _num(r[2]),
                    "dip": _num(r[3]),
                    "attrs": (r[4] if len(r) > 4 else None) or None,
                }
                for r in part
            ],
        )
    return {"surveys": len(rows)}


# --- interval tables ------------------------------------------------------------------------------


@dataclass
class ColumnSpec:
    name: str
    kind: str  # number | text
    unit: str | None = None
    role: str = "value"  # component | value | source


@dataclass
class TableData:
    """Normalized rows of one interval table: parallel sequences, numbers already parsed (NaN = missing)."""

    name: str
    kind: str  # assay | interval | event
    holes: Sequence[str]
    from_m: Any  # Sequence[float | None] or a float64 array (NaN = missing)
    to_m: Any
    columns: list[ColumnSpec]
    values: list[Sequence[Any]]  # one sequence per column, in `columns` order
    qual: list[Sequence[int]] | None = None  # per number column: 0 plain, 1 below / 2 above limit


def write_table(
    engine: sa.Engine, set_id: int, data: TableData, *, job_id: int | None, tick: Tick, swap: bool = True
) -> dict[str, int]:
    """Builds the table aside, then swaps it in (see the module docstring). Returns {"rows", "table_id"}.
    `swap=False`: the caller swaps it later with `swap_in` (several files of one import in one transaction)."""
    if data.kind not in TABLE_KINDS:
        raise ValueError(data.kind)
    now = dialect.utcnow()
    number_slots = [i for i, c in enumerate(data.columns) if c.kind == "number"]
    text_slots = [i for i, c in enumerate(data.columns) if c.kind == "text"]
    with dialect.write_tx(engine) as conn:
        res = conn.execute(
            sa.insert(dh_tables).values(
                set_id=set_id,
                name=data.name,
                name_key=dialect.name_key(data.name),
                kind=data.kind,
                row_count=0,
                rev=1,
                state="building",
                job_id=job_id,
                created_at=now,
                updated_at=now,
            )
        )
        key = res.inserted_primary_key
        assert key is not None
        table_id = int(key[0])
        slot_of: dict[int, int] = {}
        for pos, (i, col) in enumerate(enumerate(data.columns)):
            slot = number_slots.index(i) if col.kind == "number" else text_slots.index(i)
            slot_of[i] = slot
            conn.execute(
                sa.insert(dh_columns).values(
                    table_id=table_id,
                    name=col.name,
                    kind=col.kind,
                    unit=col.unit,
                    slot=slot,
                    role=col.role,
                    deleted=False,
                    position=pos,
                )
            )
        known = hole_ids(conn, set_id)
        ensure_holes(conn, set_id, data.holes, known)
    import json

    import numpy as np

    n = len(data.holes)
    # All number columns as one float64 matrix: a row's vector is a slice of it, cut after its last value.
    matrix = np.empty((n, len(number_slots)), dtype="<f8")
    for k, i in enumerate(number_slots):
        matrix[:, k] = np.asarray(data.values[i], dtype=float)
    present = ~np.isnan(matrix)
    widths = (
        np.where(present.any(axis=1), len(number_slots) - np.argmax(present[:, ::-1], axis=1), 0)
        if number_slots
        else np.zeros(n, dtype=int)
    )
    from_m = np.asarray(data.from_m, dtype=float)
    to_m = np.asarray(data.to_m, dtype=float)
    hole_of = np.array([known[h] for h in data.holes], dtype=np.int64)
    texts = [(str(slot_of[i]), data.values[i]) for i in text_slots]
    columns = ("table_id", "hole_id", "from_m", "to_m", "num", "qual", "txt")
    for start in range(0, n, BATCH):
        tick(start / max(n, 1), f"«{data.name}»: строк {start} из {n}")
        stop = min(n, start + BATCH)
        batch = []
        for r in range(start, stop):
            w = int(widths[r])
            txt = {slot: vals[r] for slot, vals in texts if vals[r] not in (None, "")} if texts else None
            qual = None
            if data.qual is not None:
                q = [int(data.qual[k][r]) for k in range(len(number_slots))]
                qual = bytes(q) if any(q) else None
            f, t = from_m[r], to_m[r]
            batch.append(
                (
                    table_id,
                    int(hole_of[r]),
                    None if f != f else float(f),
                    None if t != t else float(t),
                    matrix[r, :w].tobytes() if w else None,
                    qual,
                    json.dumps(txt, ensure_ascii=False) if txt else None,
                )
            )
        with dialect.write_tx(engine) as conn:
            dialect.bulk_insert(conn, dh_rows, columns, batch)
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == table_id).values(row_count=n))
        if swap:
            swap_in(conn, set_id, table_id)
    return {"rows": n, "table_id": table_id}


def replaced_by(kind: str, name: str) -> sa.ColumnElement[bool]:
    """Which ready table of a set a new one replaces («Заменить», ТЗ 7.7 «таблица того же типа»): the assay
    table — one per set, whatever its name; interval tables and events — the one of the same name (a set has
    lithology, alteration, RQD… side by side)."""
    if kind == "assay":
        return dh_tables.c.kind == "assay"
    return sa.and_(dh_tables.c.kind == kind, dh_tables.c.name_key == dialect.name_key(name))


def swap_in(conn: sa.Connection, set_id: int, table_id: int) -> None:
    """The table it replaces goes, the built one becomes visible — in the caller's transaction."""
    row = conn.execute(sa.select(dh_tables.c.kind, dh_tables.c.name).where(dh_tables.c.id == table_id)).one()
    conn.execute(
        sa.delete(dh_tables).where(
            dh_tables.c.set_id == set_id,
            dh_tables.c.state == "ready",
            dh_tables.c.id != table_id,
            replaced_by(row.kind, row.name),
        )
    )
    conn.execute(
        sa.update(dh_tables)
        .where(dh_tables.c.id == table_id)
        .values(state="ready", job_id=None, updated_at=dialect.utcnow())
    )


def drop_building(conn: sa.Connection, job_id: int) -> None:
    """After a cancel or a crash: building tables of the job go, and holes left with no data at all."""
    sets = {
        r.set_id
        for r in conn.execute(
            sa.select(dh_tables.c.set_id).where(dh_tables.c.job_id == job_id, dh_tables.c.state == "building")
        )
    }
    conn.execute(sa.delete(dh_tables).where(dh_tables.c.job_id == job_id, dh_tables.c.state == "building"))
    for set_id in sets:
        prune_empty_holes(conn, set_id)


def drop_import_owners(conn: sa.Connection, object_ids: Sequence[int]) -> None:
    """Before objects are deleted (their import history goes by cascade): the source files they kept in the
    storage lose that owner, so the storage cleanup can remove them. Every deletion of sets calls this."""
    from bf_core.db.schema import blob_owners

    logs = sa.select(import_log.c.id).where(import_log.c.object_id.in_(list(object_ids)))
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == "import", blob_owners.c.owner_id.in_(logs)))


def prune_empty_holes(conn: sa.Connection, set_id: int) -> int:
    has_survey = sa.exists().where(dh_surveys.c.hole_id == dh_holes.c.id)
    has_rows = sa.exists().where(dh_rows.c.hole_id == dh_holes.c.id)
    res = conn.execute(
        sa.delete(dh_holes).where(dh_holes.c.set_id == set_id, dh_holes.c.has_collar.is_(False), ~has_survey, ~has_rows)
    )
    return int(res.rowcount or 0)


# --- counters of the project composition (ТЗ 7.3) ------------------------------------------------


def _ready_sets() -> sa.Select[Any]:
    return sa.select(objects.c.id).where(objects.c.kind == SET_KIND, objects.c.state == "ready")


def count_holes(conn: sa.Connection) -> int:
    return int(
        conn.execute(
            sa.select(sa.func.count()).select_from(dh_holes).where(dh_holes.c.set_id.in_(_ready_sets()))
        ).scalar_one()
    )


def count_surveys(conn: sa.Connection) -> int:
    return int(
        conn.execute(
            sa.select(sa.func.count()).select_from(dh_surveys).where(dh_surveys.c.set_id.in_(_ready_sets()))
        ).scalar_one()
    )


def _rows_of(kind_filter: sa.ColumnElement[bool]) -> Callable[[sa.Connection], int]:
    def count(conn: sa.Connection) -> int:
        q = sa.select(sa.func.coalesce(sa.func.sum(dh_tables.c.row_count), 0)).where(
            dh_tables.c.state == "ready", dh_tables.c.set_id.in_(_ready_sets()), kind_filter
        )
        return int(conn.execute(q).scalar_one())

    return count


count_assays = _rows_of(dh_tables.c.kind == "assay")
count_interval_rows = _rows_of(dh_tables.c.kind != "assay")
