"""Ids inside the `meta` of the geostatistics objects on a `.bfggis` import (ADR 0022 §12 «.bfggis, схема»).

The objects come with their rows (`archive/spec.py`: `objects` renumbered, the files with `blob_owners`); their
`meta` names other rows by id: a variogram model — its set (`source.set_id`), its table and the table of the domain
(`source.table`, `source.domain.table`: the id as text) and the tables it was computed from (`inputs.tables[].id`).
An id the archive does not have becomes null — the model shows «источник удалён» (`store.freshness`); the old number
would point at another row of the new project. Names, tokens and revisions need nothing. The trends (16.16) add
`origin.model_id` here.

Called once all rows are written: `remap_geostat(conn, maps["objects"], maps["dh_tables"])` in `importer._restore`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import sqlalchemy as sa

Lookup = Callable[[int], int | None]


def _new(old: Any, lookup: Lookup) -> int | None:
    if old is None or isinstance(old, bool):
        return None
    try:
        return lookup(int(old))
    except (TypeError, ValueError):
        return None


def _table(old: Any, tables: Lookup) -> str | None:
    new = _new(old, tables)
    return None if new is None else str(new)


def model_meta(meta: Mapping[str, Any], objects_of: Lookup, tables_of: Lookup) -> dict[str, Any]:
    """The `meta` of a variogram model with the ids of the new project."""
    out = dict(meta)
    src = dict(out.get("source") or {})
    if "set_id" in src:
        src["set_id"] = _new(src["set_id"], objects_of)
    if "table" in src:
        src["table"] = _table(src["table"], tables_of)
    if isinstance(src.get("domain"), Mapping) and src["domain"].get("table") is not None:
        src["domain"] = {**src["domain"], "table": _table(src["domain"]["table"], tables_of)}
    out["source"] = src
    inputs = out.get("inputs")
    if isinstance(inputs, Mapping):
        listed = [t for t in inputs.get("tables") or [] if isinstance(t, Mapping)]
        tables = [{**t, "id": _new(t.get("id"), tables_of)} for t in listed]
        out["inputs"] = {**inputs, "tables": tables}
    return out


def remap_geostat(conn: sa.Connection, objects_map: Mapping[int, int], tables_map: Mapping[int, int]) -> int:
    """Renumbers the ids inside the `meta` of every variogram model; returns how many changed."""
    from bf_core.db.schema import objects

    from .variogram.store import KIND

    changed = 0
    rows = conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == KIND)).all()
    for model_id, meta in rows:
        new = model_meta(meta or {}, objects_map.get, tables_map.get)
        if new != (meta or {}):
            conn.execute(sa.update(objects).where(objects.c.id == model_id).values(meta=new))
            changed += 1
    return changed
