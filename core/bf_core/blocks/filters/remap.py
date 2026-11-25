"""Ids inside the rules of the filters on a `.bfggis` import (ADR 0023 §8 «.bfggis»; contract e17-api.md §13).

The filters come with the archive as objects (renumbered by `archive/spec.py`); their `rules` name wireframes and
surfaces by id. An id the archive does not have becomes null: the leaf stays and shows «недоступен»
(`wireframes[].available: false`, its share is 0 and the model is warned) — the old number would point at another
object of the new project. The rows `object_refs(role="filter_src")` come with the archive (those of the missing
wireframes are not there) and are not touched; the consumers' rows `role="filter"` too. `text`, `fields` and
`title` need nothing: they hold no ids. The ids of the filters inside `block_styles` of the view are renumbered by
`viewstate/keys.py::map_ids` with the other view keys.

Called once all rows are written: `remap_filters(conn, maps["objects"])` in `importer._restore`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import sqlalchemy as sa

from .schema import remap_ids


def filter_meta(meta: Mapping[str, Any], new_id: Callable[[int], int | None]) -> dict[str, Any]:
    """The `meta` of a filter with the ids of the new project."""
    rules = meta.get("rules")
    if not isinstance(rules, Mapping):
        return dict(meta)
    return {**meta, "rules": remap_ids(rules, new_id)}


def remap_filters(conn: sa.Connection, objects_map: Mapping[int, int]) -> int:
    """Renumbers the wireframes and surfaces in the rules of every filter; returns how many filters changed."""
    from bf_core.db.schema import objects

    from .store import KIND

    changed = 0
    for fid, meta in conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == KIND)).all():
        new = filter_meta(meta or {}, objects_map.get)
        if new != (meta or {}):
            conn.execute(sa.update(objects).where(objects.c.id == fid).values(meta=new))
            changed += 1
    return changed
