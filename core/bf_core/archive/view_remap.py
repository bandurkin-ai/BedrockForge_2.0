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
