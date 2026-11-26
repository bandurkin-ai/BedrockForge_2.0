"""The rows of the planned holes: `objects(kind="planned_hole", parent_id=NULL)`, names unique among them (`name_key`);
no files — the path is computed from `meta` when it is asked for (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решение 29).

The object is not a row of a set of holes: it takes no part in the statistics or the estimation. A planned hole is a
source in the graph (ADR 0025: `object:planned_hole`, the fingerprint is its `version`, `lineage/adapt_planned.py`);
it has no inputs, so nothing is recorded at a write, and the rows of its node go with it at the deletion.

Errors: 404 `planned_hole_not_found`, `set_not_found`; 409 `name_taken`, `version_conflict {version}`; 422 `bad_param
{param}` (`planned.parse`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.geostat.estimate import store_base as eb

from . import planned as pl

KIND = pl.KIND
DEFAULT_NAME = "Проектная скважина"
SET_KIND = "drillhole_set"


def not_found() -> ApiError:
    return ApiError(404, "planned_hole_not_found", "Проектная скважина не найдена.", "Обновите дерево проекта.")


def conflict(version: int) -> ApiError:
    return ApiError(409, "version_conflict", "Проектная скважина изменена в другом окне.",
                    "Перечитайте скважину или сохраните свою под новым именем.", {"version": version})  # fmt: skip


def clean_version(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise pl.bad("expected_version", "Не задана версия, которую правит окно.",
                     "Перечитайте скважину и повторите сохранение.")  # fmt: skip
    return raw


def check_set(conn: sa.Connection, set_id: int | None) -> None:
    """404 `set_not_found`: the set the hole is planned for (and compared with) is in the project."""
    if set_id is None:
        return
    found = conn.execute(sa.select(objects.c.id).where(
        objects.c.id == set_id, objects.c.kind == SET_KIND, objects.c.state == "ready")).scalar()  # fmt: skip
    if found is None:
        raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")


def _item(row: Any, *, with_path: bool) -> dict[str, Any]:
    """`{id, name, version, meta[, trajectory, target_depth]}` of a row; a `meta` that does not parse (a file of another
    program, a hand edit) has no path: the row comes without `trajectory` and with `path_error` {code, message, hint}
    — a list or a read does not fail for one broken hole."""
    out: dict[str, Any] = {"id": int(row.id), "name": str(row.name), "version": int(row.version),
                           "meta": dict(row.meta or {})}  # fmt: skip
    if with_path:
        try:
            p = pl.parse(out["meta"])
        except ApiError as e:
            out["path_error"] = {"code": e.code, "message": e.message, "hint": e.hint}
            return out
        out["trajectory"] = pl.path(pl.trajectory(p), p.length)
        out["target_depth"] = p.target_depth
    return out


def stored(conn: sa.Connection, ident: int) -> Any:
    r = conn.execute(sa.select(objects).where(objects.c.id == ident, objects.c.kind == KIND)).first()
    if r is None:
        raise not_found()
    return r


def get(conn: sa.Connection, ident: int, *, with_path: bool = True) -> dict[str, Any]:
    return _item(stored(conn, ident), with_path=with_path)


def list_rows(conn: sa.Connection, *, with_path: bool = False) -> list[dict[str, Any]]:
    rows = conn.execute(sa.select(objects).where(objects.c.kind == KIND).order_by(objects.c.name_key, objects.c.id))
    return [_item(r, with_path=with_path) for r in rows]


def create(conn: sa.Connection, name: Any, p: pl.Planned, author: str | None = None) -> dict[str, Any]:
    """A planned hole (in the caller's write transaction): `{id, name, version: 1, meta, trajectory}`."""
    check_set(conn, p.set_id)
    final = eb.final_name(conn, KIND, name, DEFAULT_NAME)
    now = dialect.utcnow()
    meta = p.meta()
    extent = pl.extent_of(pl.path(pl.trajectory(p), p.length))
    res = conn.execute(sa.insert(objects).values(
        kind=KIND, name=final, name_key=dialect.name_key(final), parent_id=None, sort_key=0.0, state="ready",
        version=1, meta=meta, extent=extent, author=author, created_at=now, updated_at=now))  # fmt: skip
    return get(conn, int(res.inserted_primary_key[0]))  # type: ignore[index]


def update(conn: sa.Connection, ident: int, raw: Mapping[str, Any]) -> dict[str, Any]:
    """The keys of `raw` (`collar`, `azimuth`, `dip`, `length`, `change`, `target`, `method`, `set_id`, `name`) over
    the stored ones, checked again; `expected_version` is required: another one — 409 `version_conflict`."""
    row = stored(conn, ident)
    version = clean_version(raw.get("expected_version"))
    if int(row.version) != version:
        raise conflict(int(row.version))
    p = pl.parse(raw, row.meta or {})
    if "set_id" in raw and p.set_id != (row.meta or {}).get("set_id"):
        check_set(conn, p.set_id)  # only a set that is new: the stored one may be gone (deleted, or not in a `.bfggis`)
    final = str(row.name) if raw.get("name") is None else eb.final_name(conn, KIND, raw["name"], "", ident)
    extent = pl.extent_of(pl.path(pl.trajectory(p), p.length))
    n = conn.execute(sa.update(objects).where(objects.c.id == ident, objects.c.version == version).values(
        name=final, name_key=dialect.name_key(final), meta=p.meta(), extent=extent, version=version + 1,
        updated_at=dialect.utcnow())).rowcount  # fmt: skip
    if n != 1:
        raise conflict(int(stored(conn, ident).version))
    return get(conn, ident)


def delete(conn: sa.Connection, ident: int) -> dict[str, Any]:
    """The row and its rows of the graph go; nothing else refers to a planned hole."""
    from bf_core import lineage
    from bf_core.lineage import store as lstore

    stored(conn, ident)
    conn.execute(sa.delete(objects).where(objects.c.id == ident, objects.c.kind == KIND))
    lstore.forget(conn, lineage.Node("object:" + KIND, ident))
    return {"deleted": ident}
