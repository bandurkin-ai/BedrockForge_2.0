"""Saved charts (ADR 0007 §3): an object `chart` under a drillhole set + a `charts` row. The striplog kind holds
the displays along holes of ТЗ 7.16 (BF StripLogSettings); the interface owns its shape, the core checks the
size, the layers count and the version. Until «Сохранить» a display lives only in the interface."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import charts, objects

from ..styles.rules import json_size

KINDS = ("striplog",)
MAX_TRACKS = 32
MAX_SETTINGS_BYTES = 256 * 1024
SET_KIND = "drillhole_set"


class ChartError(Exception):
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


def check(kind: str, settings: Any) -> None:
    if kind not in KINDS:
        raise ChartError(f"Неизвестный вид графика «{kind}».")
    if not isinstance(settings, dict):
        raise ChartError("Настройки отображения должны быть объектом.")
    if json_size(settings) > MAX_SETTINGS_BYTES:
        raise ChartError("Настройки отображения больше 256 КБ.")
    tracks = settings.get("tracks", [])
    if not isinstance(tracks, list) or any(not isinstance(t, dict) for t in tracks):
        raise ChartError("Слои отображения должны быть списком.")
    if len(tracks) > MAX_TRACKS:
        raise ChartError(f"В отображении не больше {MAX_TRACKS} слоёв.")


def _query() -> sa.Select[Any]:
    return sa.select(
        objects.c.id,
        objects.c.parent_id,
        objects.c.name,
        objects.c.version,
        objects.c.author,
        objects.c.updated_at,
        charts.c.kind,
        charts.c.settings,
        charts.c.auto_name,
    ).join(charts, charts.c.object_id == objects.c.id)


def _out(r: Any) -> dict[str, Any]:
    return {
        "id": int(r.id),
        "set_id": r.parent_id,
        "name": r.name,
        "kind": r.kind,
        "settings": r.settings,
        "auto_name": bool(r.auto_name),
        "version": int(r.version),
        "author": r.author,
        "updated_at": r.updated_at.isoformat(),
    }


def get(conn: sa.Connection, chart_id: int) -> dict[str, Any]:
    r = conn.execute(_query().where(objects.c.id == chart_id, objects.c.kind == "chart")).first()
    if r is None:
        raise NotFound(chart_id)
    return _out(r)


def list_charts(conn: sa.Connection, set_id: int | None = None, kind: str | None = None) -> list[dict[str, Any]]:
    q = _query().where(objects.c.kind == "chart", objects.c.state == "ready")
    if set_id is not None:
        q = q.where(objects.c.parent_id == set_id)
    if kind is not None:
        q = q.where(charts.c.kind == kind)
    return [_out(r) for r in conn.execute(q.order_by(objects.c.sort_key, objects.c.name_key, objects.c.id))]


def _taken(conn: sa.Connection, set_id: int, name: str, but: int | None = None) -> bool:
    q = sa.select(objects.c.id).where(
        objects.c.kind == "chart", objects.c.parent_id == set_id, objects.c.name_key == dialect.name_key(name)
    )
    if but is not None:
        q = q.where(objects.c.id != but)
    return conn.execute(q).first() is not None


def _clean(name: str) -> str:
    name = name.strip()
    if not name:
        raise ChartError("Нужно название отображения.")
    return name


def create(
    conn: sa.Connection, set_id: int, name: str, kind: str, settings: dict[str, Any], author: str | None
) -> dict[str, Any]:
    check(kind, settings)
    name = _clean(name)
    found = conn.execute(
        sa.select(objects.c.id).where(objects.c.id == set_id, objects.c.kind == SET_KIND, objects.c.state == "ready")
    ).first()
    if found is None:
        raise NotFound(set_id)
    if _taken(conn, set_id, name):
        raise NameTaken(name)
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(objects).values(
            parent_id=set_id,
            kind="chart",
            name=name,
            name_key=dialect.name_key(name),
            sort_key=0.0,
            state="ready",
            version=1,
            meta={"chart": kind},
            author=author,
            created_at=now,
            updated_at=now,
        )
    )
    key = res.inserted_primary_key
    assert key is not None
    conn.execute(sa.insert(charts).values(object_id=int(key[0]), kind=kind, settings=settings, auto_name=False))
    return get(conn, int(key[0]))


def update(
    conn: sa.Connection, chart_id: int, name: str | None, settings: dict[str, Any] | None, expected_version: int
) -> dict[str, Any]:
    current = get(conn, chart_id)
    if current["version"] != expected_version:
        raise Conflict(current)
    values: dict[str, Any] = {}
    if name is not None:
        name = _clean(name)
        if _taken(conn, current["set_id"], name, but=chart_id):
            raise NameTaken(name)
        values.update(name=name, name_key=dialect.name_key(name))
    if settings is not None:
        check(current["kind"], settings)
        conn.execute(sa.update(charts).where(charts.c.object_id == chart_id).values(settings=settings))
    n = conn.execute(
        sa.update(objects)
        .where(objects.c.id == chart_id, objects.c.version == expected_version)
        .values(**values, version=expected_version + 1, updated_at=dialect.utcnow())
    ).rowcount
    if n != 1:
        raise Conflict(get(conn, chart_id))
    return get(conn, chart_id)


def delete(conn: sa.Connection, chart_id: int) -> None:
    get(conn, chart_id)
    conn.execute(sa.delete(objects).where(objects.c.id == chart_id))
