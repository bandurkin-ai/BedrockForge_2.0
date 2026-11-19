"""Series of sections in `objects` (kind `section_series`; ADR 0016 §1, §3): list, create, change with a version,
copy, delete, the table of sections.

A series is an object like a saved view: `meta` holds its parameters (`schema.py`), `version` grows with every
change, a stale `expected_version` is a `Conflict` (409). The number of the series (`meta.number`) is the largest
in the project + 1 and never changes; a copy gets a new one. Deleting a series leaves its contours as they are
(their attributes `SECTION_SET`, `SECTION`, `SECTION_NAME` stay). The current section is not part of the series:
it lives in the view key `section.series` (decision 13), so stepping through sections does not change the version.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects

from . import schema
from .base import SeriesError
from .schema import KIND

MAX_SERIES = 1_000  # series in a project
TABLE_FORMAT = 1  # bump when the rules of the names or origins change: the ETag of every table changes
COPY_SUFFIX = " (копия)"
_AUTO_NAME = re.compile(r"^Серия (\d{1,6})$")
_LOCK = 0x5EC7_5E21E5  # PostgreSQL advisory lock of the series numbers


class NotFound(LookupError):
    pass


class Conflict(Exception):
    def __init__(self, version: int, series: dict[str, Any]) -> None:
        super().__init__("version_conflict")
        self.version = version
        self.series = series


def _name(v: Any) -> str:
    name = v.strip() if isinstance(v, str) else ""
    if not name or len(name) > 200:
        raise SeriesError("Название серии — от 1 до 200 символов.", "Введите название серии.")
    return name


def _folder(v: Any) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str) or len(v) > 200:
        raise SeriesError("Папка серии — до 200 символов.", "Сократите путь папки.")
    parts = [p.strip() for p in v.split("/") if p.strip()]
    return "/".join(parts) or None


def _alive_layer(conn: sa.Connection) -> Any:
    from bf_core.lines.store import LINE_LAYER

    def alive(layer_id: int) -> bool:
        q = sa.select(objects.c.id).where(objects.c.id == layer_id, objects.c.kind == LINE_LAYER)
        return conn.execute(q).first() is not None

    return alive


def _check(conn: sa.Connection, params: Any) -> dict[str, Any]:
    return schema.check(params, _alive_layer(conn))


def _out(conn: sa.Connection, r: Any) -> dict[str, Any]:
    meta = r.meta if isinstance(r.meta, dict) else {}
    params = schema.params_of(meta)
    if params["contour_layer"] is not None and not _alive_layer(conn)(params["contour_layer"]):
        params["contour_layer"] = None  # the layer was deleted since
    try:
        from .geometry import series_geometry  # numpy: after BF-READY

        k = series_geometry(meta).count
    except SeriesError:
        k = 0  # a meta this version cannot read: listed, without sections
    return {
        "id": int(r.id),
        "name": r.name,
        "folder": r.folder,
        "version": int(r.version),
        "number": params.pop("number"),
        "count": k,
        "params": params,
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }


def row(conn: sa.Connection, series_id: int) -> Any:
    r = conn.execute(sa.select(objects).where(objects.c.id == series_id, objects.c.kind == KIND)).first()
    if r is None:
        raise NotFound(series_id)
    return r


def list_series(conn: sa.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        sa.select(objects)
        .where(objects.c.kind == KIND, objects.c.state == "ready")
        .order_by(objects.c.sort_key, objects.c.id)
    ).all()
    return [_out(conn, r) for r in rows]


def get(conn: sa.Connection, series_id: int) -> dict[str, Any]:
    return _out(conn, row(conn, series_id))


def _numbers(conn: sa.Connection) -> list[int]:
    out = []
    for (meta,) in conn.execute(sa.select(objects.c.meta).where(objects.c.kind == KIND)):
        n = meta.get("number") if isinstance(meta, dict) else None
        if isinstance(n, int) and not isinstance(n, bool):
            out.append(n)
    return out


def _next_name(conn: sa.Connection, number: int) -> str:
    taken = {n for (n,) in conn.execute(sa.select(objects.c.name).where(objects.c.kind == KIND))}
    name = f"Серия {number}"
    while name in taken:
        number += 1
        name = f"Серия {number}"
    return name


def _insert(conn: sa.Connection, name: Any, folder: Any, meta: dict[str, Any], author: str | None) -> int:
    if not dialect.is_sqlite(conn.engine):  # two creations at once must not get one number
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK})
    count = conn.execute(sa.select(sa.func.count()).select_from(objects).where(objects.c.kind == KIND)).scalar_one()
    if count >= MAX_SERIES:
        raise SeriesError(f"В проекте уже {MAX_SERIES} серий.", "Удалите ненужные серии разрезов.")
    number = max(_numbers(conn), default=0) + 1
    name = _next_name(conn, number) if name is None else _name(name)
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(objects).values(
            kind=KIND,
            name=name,
            name_key=dialect.name_key(name),
            folder=_folder(folder),
            sort_key=float(count),
            state="ready",
            version=1,
            meta={"v": 1, "number": number, **meta},
            author=author,
            created_at=now,
            updated_at=now,
        )
    )
    key = res.inserted_primary_key
    assert key is not None
    return int(key[0])


def create(
    conn: sa.Connection, params: Any, name: Any = None, folder: Any = None, author: str | None = None
) -> dict[str, Any]:
    meta = _check(conn, params)
    return get(conn, _insert(conn, name, folder, meta, author))


def update(
    conn: sa.Connection,
    series_id: int,
    expected_version: int,
    *,
    name: Any = None,
    folder: Any = ...,
    params: Any = None,
) -> dict[str, Any]:
    """Rename, move to a folder, change the parameters (the fields sent; the others stay)."""
    r = row(conn, series_id)
    if int(r.version) != expected_version:
        raise Conflict(int(r.version), _out(conn, r))
    values: dict[str, Any] = {"version": int(r.version) + 1, "updated_at": dialect.utcnow()}
    if name is not None:
        values["name"] = _name(name)
        values["name_key"] = dialect.name_key(values["name"])
    if folder is not ...:
        values["folder"] = _folder(folder)
    if params is not None:
        old = r.meta if isinstance(r.meta, dict) else {}
        meta = _check(conn, schema.merge(old, params))
        values["meta"] = {"v": 1, "number": old.get("number"), **meta}
    n = conn.execute(
        sa.update(objects).where(objects.c.id == series_id, objects.c.version == expected_version).values(**values)
    ).rowcount
    if n != 1:
        raise Conflict(int(row(conn, series_id).version), get(conn, series_id))
    return get(conn, series_id)


def copy(conn: sa.Connection, series_id: int, name: Any = None, author: str | None = None) -> dict[str, Any]:
    """«Копировать серию»: the same parameters under a new number (a new geometry is set by a PATCH after)."""
    r = row(conn, series_id)
    meta = {k: v for k, v in (r.meta or {}).items() if k not in ("v", "number")}
    if name is None:
        name = r.name[: 200 - len(COPY_SUFFIX)] + COPY_SUFFIX
    return get(conn, _insert(conn, name, r.folder, _check(conn, meta), author))


def delete(conn: sa.Connection, series_id: int) -> None:
    row(conn, series_id)
    conn.execute(sa.delete(objects).where(objects.c.id == series_id))


def table(r: Any) -> dict[str, Any]:
    """`GET …/table`: `{version, count, sections: [{k, name, s, origin, towards, away}][, segments]}` — the
    names and origins computed from the parameters (`names.series_table`), never stored."""
    from .names import series_table

    meta = r.meta if isinstance(r.meta, dict) else {}
    return {"version": int(r.version), **series_table(meta)}


def table_etag(uid: str, r: Any) -> str:
    """The table changes with the parameters only, and every change of them is a new version."""
    key = json.dumps(["section_series.table", TABLE_FORMAT, uid, int(r.id), int(r.version)], separators=(",", ":"))
    return '"' + hashlib.sha256(key.encode("utf-8")).hexdigest() + '"'
