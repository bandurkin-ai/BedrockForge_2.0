"""Rows of `settings` on a `.bfggis` import (ADR 0007 §2): the view scope is optional and carries object ids
inside keys and values; code lists links carry the set id in the key. Other scopes are copied as they are."""

from __future__ import annotations

from typing import Any

from bf_core.viewstate.keys import SCOPE as VIEW
from bf_core.viewstate.keys import map_ids, normalize

CODE_LINKS = "code_links"  # key "<set>:<table>:<column>" (drillholes/tools.py)


def remap_setting(row: dict[str, Any], objects: dict[int, int], apply_view: bool) -> dict[str, Any] | None:
    scope = row.get("scope")
    if scope == VIEW:
        if not apply_view:
            return None
        try:
            value = normalize(str(row["key"]), row["value"], strict=False)
        except ValueError:
            return None  # a key this version does not know
        return {**row, "value": map_ids(str(row["key"]), value, objects.get)}
    if scope == CODE_LINKS:
        set_id, _, rest = str(row["key"]).partition(":")
        if not set_id.isdigit() or int(set_id) not in objects:
            return None
        return {**row, "key": f"{objects[int(set_id)]}:{rest}"}
    return row


def remap_views(conn: Any, objects_map: dict[int, int]) -> int:
    """Saved views (ТЗ 7.35 Д-09, ADR 0008): ids inside their meta on a `.bfggis` import. Views are objects and
    always come along; their keys refer to sets, wireframes, displays… of the same archive."""
    import sqlalchemy as sa

    from bf_core.db.schema import objects
    from bf_core.viewstate.views import KIND, remap_meta

    rows = conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == KIND)).all()
    for view_id, meta in rows:
        conn.execute(sa.update(objects).where(objects.c.id == view_id).values(meta=remap_meta(meta, objects_map.get)))
    return len(rows)
