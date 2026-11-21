"""Saved charts (ADR 0007 §3, ADR 0019 §2): an object `chart` + a `charts` row, no migration. The striplog kind
holds the displays along holes of ТЗ 7.16 (BF StripLogSettings; until «Сохранить» a display lives only in the
interface); the charts of Э13 (`kinds.SET_KINDS`) lie under their set, a swath plot is an object of the project
(`parent_id = NULL`, ≤ 200). The interface owns the shape of the settings; `kinds.check` — the size, the lists,
the version and the ids.

Names are unique among the charts of one parent (the set, or the project for swath plots), by `name_key`. A chart
made with `auto_name` follows its settings (ТЗ 7.28): the interface sends the name it builds, a taken one becomes
« (2)», « (3)»…; a name given by hand (`PUT {name}`) drops the flag for good (Т7.28-2) and a taken one is 409."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import charts, objects

from . import kinds as k
from .kinds import KindError as ChartError

KINDS = k.KINDS
SET_KIND = "drillhole_set"


class NotFound(Exception):
    pass


class NameTaken(Exception):
    def __init__(self, name: str, kind: str = k.STRIPLOG) -> None:
        super().__init__(name)
        self.name, self.kind = name, kind


class Conflict(Exception):
    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__("version_conflict")
        self.current = current


def check(kind: str, settings: Any) -> None:
    k.check(kind, settings)


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


def _parent(q: sa.Select[Any], set_id: int | None) -> sa.Select[Any]:
    return q.where(objects.c.parent_id.is_(None) if set_id is None else objects.c.parent_id == set_id)


def _taken(conn: sa.Connection, set_id: int | None, name: str, but: int | None = None) -> bool:
    q = _parent(sa.select(objects.c.id), set_id).where(
        objects.c.kind == "chart", objects.c.name_key == dialect.name_key(name)
    )
    if but is not None:
        q = q.where(objects.c.id != but)
    return conn.execute(q).first() is not None


def _clean(name: str) -> str:
    name = name.strip()
    if not name:
        raise ChartError("Нужно название графика.", param="name")
    if len(name) > k.MAX_NAME:
        raise ChartError(f"Название графика — до {k.MAX_NAME} символов.", param="name")
    return name


def free_name(conn: sa.Connection, set_id: int | None, name: str, but: int | None = None) -> str:
    """`name`, or the first of «name (2)», «name (3)»… not taken among the charts of the parent."""
    name = _clean(name)
    if not _taken(conn, set_id, name, but):
        return name
    n = 2
    while True:
        tail = f" ({n})"
        candidate = name[: k.MAX_NAME - len(tail)].rstrip() + tail
        if not _taken(conn, set_id, candidate, but):
            return candidate
        n += 1


def _check_parent(conn: sa.Connection, set_id: int | None, kind: str) -> None:
    if kind in k.PROJECT_KINDS:
        if set_id is not None:
            raise ChartError(f"{k.TITLES[kind]} — объект проекта, без набора скважин.", param="set_id")
        count = conn.execute(
            sa.select(sa.func.count())
            .select_from(objects.join(charts, charts.c.object_id == objects.c.id))
            .where(objects.c.kind == "chart", objects.c.parent_id.is_(None), charts.c.kind == kind)
        ).scalar_one()
        if int(count) >= k.MAX_SWATHS:
            raise ChartError(
                f"В проекте не больше {k.MAX_SWATHS} swath plot: удалите ненужные.", code="too_many_charts"
            )
        return
    if set_id is None:
        raise ChartError("Нужен набор скважин графика.", param="set_id")
    found = conn.execute(
        sa.select(objects.c.id).where(objects.c.id == set_id, objects.c.kind == SET_KIND, objects.c.state == "ready")
    ).first()
    if found is None:
        raise NotFound(set_id)


def create(
    conn: sa.Connection,
    set_id: int | None,
    name: str,
    kind: str,
    settings: dict[str, Any],
    author: str | None,
    *,
    auto_name: bool = False,
) -> dict[str, Any]:
    """A new chart; `set_id` None — a chart of the project (swath). With `auto_name` a taken name becomes
    «name (2)»…, without — NameTaken (409)."""
    check(kind, settings)
    _check_parent(conn, set_id, kind)
    if auto_name:
        name = free_name(conn, set_id, name)
    else:
        name = _clean(name)
        if _taken(conn, set_id, name):
            raise NameTaken(name, kind)
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
    conn.execute(
        sa.insert(charts).values(object_id=int(key[0]), kind=kind, settings=settings, auto_name=bool(auto_name))
    )
    return get(conn, int(key[0]))


def update(
    conn: sa.Connection,
    chart_id: int,
    name: str | None,
    settings: dict[str, Any] | None,
    expected_version: int,
    *,
    auto_name: str | None = None,
) -> dict[str, Any]:
    """`name` — renamed by hand: the auto name is off for good (Т7.28-2), a taken name is 409. `auto_name` — the
    name the interface built from the new settings: applied (made free with « (2)») only while the flag is on,
    ignored after a manual rename or when `name` is given too."""
    current = get(conn, chart_id)
    if current["version"] != expected_version:
        raise Conflict(current)
    parent = current["set_id"]
    values: dict[str, Any] = {}
    flag: bool | None = None
    if name is not None:
        name = _clean(name)
        if _taken(conn, parent, name, but=chart_id):
            raise NameTaken(name, current["kind"])
        values.update(name=name, name_key=dialect.name_key(name))
        flag = False
    elif auto_name is not None and current["auto_name"]:
        new = free_name(conn, parent, auto_name, but=chart_id)
        if new != current["name"]:
            values.update(name=new, name_key=dialect.name_key(new))
    if settings is not None:
        check(current["kind"], settings)
    row: dict[str, Any] = {}
    if settings is not None:
        row["settings"] = settings
    if flag is not None and current["auto_name"]:
        row["auto_name"] = flag
    if row:
        conn.execute(sa.update(charts).where(charts.c.object_id == chart_id).values(**row))
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
