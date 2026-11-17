"""Project view settings in `settings` (scope `view`): read normalized, write with a version (ADR 0007 §2)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bf_core.db.schema import objects, settings

from .keys import SCOPE, drop_missing, ids_in, normalize


class Conflict(Exception):
    def __init__(self, value: Any, version: int) -> None:
        super().__init__("version_conflict")
        self.value = value
        self.version = version


def _alive(conn: sa.Connection, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    found: set[int] = set()
    chunk = sorted(ids)
    for i in range(0, len(chunk), 500):  # SQLite parameter limit
        part = chunk[i : i + 500]
        found.update(int(r[0]) for r in conn.execute(sa.select(objects.c.id).where(objects.c.id.in_(part))))
    return found


def read_all(conn: sa.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        sa.select(settings.c.key, settings.c.value, settings.c.version).where(settings.c.scope == SCOPE)
    ).all()
    values: dict[str, Any] = {}
    versions: dict[str, int] = {}
    for key, value, version in rows:
        try:
            values[key] = normalize(key, value, strict=False)
        except ValueError:
            continue  # unknown key of another version
        versions[key] = int(version)
    ids: set[int] = set()
    for k, v in values.items():
        ids.update(ids_in(k, v))
    alive = _alive(conn, ids)
    return {k: {"value": drop_missing(k, v, alive.__contains__), "version": versions[k]} for k, v in values.items()}


def write(conn: sa.Connection, key: str, value: Any, expected_version: int) -> dict[str, Any]:
    clean = normalize(key, value, strict=True)
    row = conn.execute(
        sa.select(settings.c.value, settings.c.version).where(settings.c.scope == SCOPE, settings.c.key == key)
    ).first()
    current = int(row.version) if row is not None else 0
    if current != expected_version:
        stale = normalize(key, row.value, strict=False) if row is not None else None
        raise Conflict(stale, current)
    if row is None:
        conn.execute(sa.insert(settings).values(scope=SCOPE, key=key, value=clean, version=1))
    else:
        n = conn.execute(
            sa.update(settings)
            .where(settings.c.scope == SCOPE, settings.c.key == key, settings.c.version == current)
            .values(value=clean, version=current + 1)
        ).rowcount
        if n != 1:
            raise Conflict(None, current)
    return {"value": clean, "version": current + 1}
