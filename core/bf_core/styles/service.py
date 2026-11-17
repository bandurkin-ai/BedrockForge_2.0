"""The style library of a project (ТЗ 7.16, ADR 0007 §3): list, create, change with a version, «Где
используется», delete with the copies kept in the displays."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import charts, objects, styles

from . import merge
from .rules import DATA_KINDS, LEVELS, body_error, default_for_error, name_error


class StyleError(Exception):
    """422 with a Russian text."""


class NotFound(Exception):
    pass


class NameTaken(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class Conflict(Exception):
    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__("version_conflict")
        self.current = current


def out(row: Any) -> dict[str, Any]:
    r = row._mapping if hasattr(row, "_mapping") else row
    return {
        "id": int(r["id"]),
        "uid": r["uid"],
        "name": r["name"],
        "level": r["level"],
        "data_kind": r["data_kind"],
        "body": r["body"],
        "default_for": r["default_for"] or [],
        "version": int(r["version"]),
        "author": r["author"],
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    }


def check(
    name: Any = None, level: Any = None, data_kind: Any = None, body: Any = None, default_for: Any = None
) -> None:
    """Raises StyleError for the first wrong field among those given (None — not checked)."""
    if name is not None and (e := name_error(name)):
        raise StyleError(e)
    if level is not None and level not in LEVELS:
        raise StyleError(f"неизвестный уровень стиля «{level}»")
    if data_kind is not None and data_kind not in DATA_KINDS:
        raise StyleError(f"неизвестный вид данных «{data_kind}»")
    if body is not None and (e := body_error(body)):
        raise StyleError(e)
    if default_for is not None and (e := default_for_error(default_for)):
        raise StyleError(e)


def list_styles(conn: sa.Connection, level: str | None = None) -> list[dict[str, Any]]:
    q = sa.select(styles)
    if level is not None:
        q = q.where(styles.c.level == level)
    return [out(r) for r in conn.execute(q.order_by(styles.c.level, styles.c.name_key, styles.c.id))]


def get(conn: sa.Connection, style_id: int) -> dict[str, Any]:
    row = conn.execute(sa.select(styles).where(styles.c.id == style_id)).first()
    if row is None:
        raise NotFound(style_id)
    return out(row)


def _taken(conn: sa.Connection, level: str, name: str, but: int | None = None) -> bool:
    q = sa.select(styles.c.id).where(styles.c.level == level, styles.c.name_key == dialect.name_key(name))
    if but is not None:
        q = q.where(styles.c.id != but)
    return conn.execute(q).first() is not None


def create(
    conn: sa.Connection,
    *,
    name: str,
    level: str,
    data_kind: str,
    body: dict[str, Any],
    default_for: list[str],
    extra: dict[str, Any] | None = None,
    uid: str | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    check(name, level, data_kind, body, default_for)
    name = name.strip()
    if _taken(conn, level, name):
        raise NameTaken(name)
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(styles).values(
            uid=uid or uuid.uuid4().hex,
            name=name,
            name_key=dialect.name_key(name),
            level=level,
            data_kind=data_kind,
            body=body,
            default_for=[p.strip() for p in default_for],
            extra=extra or {},
            version=1,
            author=author,
            created_at=now,
            updated_at=now,
        )
    )
    key = res.inserted_primary_key
    assert key is not None
    return get(conn, int(key[0]))


def patch(conn: sa.Connection, style_id: int, changes: dict[str, Any], expected_version: int) -> dict[str, Any]:
    current = get(conn, style_id)
    if current["version"] != expected_version:
        raise Conflict(current)
    check(changes.get("name"), None, changes.get("data_kind"), changes.get("body"), changes.get("default_for"))
    values: dict[str, Any] = {}
    if changes.get("name") is not None:
        name = str(changes["name"]).strip()
        if _taken(conn, current["level"], name, but=style_id):
            raise NameTaken(name)
        values.update(name=name, name_key=dialect.name_key(name))
    for k in ("body", "data_kind", "default_for", "extra"):
        if changes.get(k) is not None:
            values[k] = changes[k]
    if not values:
        return current
    n = conn.execute(
        sa.update(styles)
        .where(styles.c.id == style_id, styles.c.version == expected_version)
        .values(**values, version=expected_version + 1, updated_at=dialect.utcnow())
    ).rowcount
    if n != 1:
        raise Conflict(get(conn, style_id))
    return get(conn, style_id)


def _striplogs(conn: sa.Connection) -> list[Any]:
    return list(
        conn.execute(
            sa.select(charts.c.object_id, charts.c.settings, objects.c.name, objects.c.parent_id, objects.c.version)
            .join(objects, objects.c.id == charts.c.object_id)
            .where(charts.c.kind == "striplog")
            .order_by(charts.c.object_id)
        )
    )


def usage(conn: sa.Connection, style_id: int) -> list[dict[str, Any]]:
    """Displays that use the style, linked or as the source of their copy («Где используется»)."""
    uid = get(conn, style_id)["uid"]
    used = []
    for r in _striplogs(conn):
        found = merge.refs_of(r.settings if isinstance(r.settings, dict) else {}, uid)
        if found is not None:
            used.append(
                {
                    "chart_id": int(r.object_id),
                    "name": r.name,
                    "set_id": r.parent_id,
                    "linked": found[0],
                    "tracks": found[1],
                }
            )
    return used


def delete(conn: sa.Connection, style_id: int) -> list[int]:
    """Delete a style; the displays using it keep its last settings as their own copy. Returns their ids."""
    style = get(conn, style_id)
    changed = []
    now = dialect.utcnow()
    for r in _striplogs(conn):
        settings = merge.unlink(
            r.settings if isinstance(r.settings, dict) else {}, style["uid"], style["level"], style["body"]
        )
        if settings is None:
            continue
        conn.execute(sa.update(charts).where(charts.c.object_id == r.object_id).values(settings=settings))
        conn.execute(
            sa.update(objects).where(objects.c.id == r.object_id).values(version=objects.c.version + 1, updated_at=now)
        )
        changed.append(int(r.object_id))
    conn.execute(sa.delete(styles).where(styles.c.id == style_id))
    return changed
