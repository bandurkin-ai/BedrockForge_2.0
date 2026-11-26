"""The ids inside the `meta` of a planned hole on a `.bfggis` import (ADR 0026 решение 26; e19-api.md §10): the only
one is `meta.set_id`, the set the hole is planned for and compared with. The rows come renumbered with `objects`
(`archive/spec.py`); the set the archive did not have becomes `DEAD_ID` (the old number would name another row of the
new project): the hole stays readable, a request that names that set is refused with 404 `set_not_found`.

Called once all rows are written: `remap_planned(conn, maps["objects"])` in `importer._restore`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

DEAD_ID = 2_000_000_000  # `model/store_base.DEAD_ID`
KIND = "planned_hole"


def remap_meta(meta: Mapping[str, Any], objects_map: Mapping[int, int]) -> dict[str, Any]:
    """The `meta` with the new number of the set; null (no set) stays null."""
    out = dict(meta)
    old = out.get("set_id")
    if old is not None and not isinstance(old, bool):
        try:
            out["set_id"] = int(objects_map.get(int(old), DEAD_ID))
        except (TypeError, ValueError):
            out["set_id"] = DEAD_ID
    return out


def remap_planned(conn: sa.Connection, objects_map: Mapping[int, int]) -> int:
    """Renumbers `meta.set_id` of every planned hole; returns how many rows changed."""
    from bf_core.db.schema import objects

    changed = 0
    for ident, meta in conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == KIND)).all():
        new = remap_meta(meta or {}, objects_map)
        if new != (meta or {}):
            conn.execute(sa.update(objects).where(objects.c.id == ident).values(meta=new))
            changed += 1
    return changed
