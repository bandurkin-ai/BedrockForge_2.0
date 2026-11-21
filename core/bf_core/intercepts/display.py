"""The display «Рудные пересечения» of an intercepts result (ТЗ 8.5.3 «подписи на стволах»; ADR 0021 §11, решение 19):
a saved striplog of the set named as the result table — a colour column by KIND in the brand colours of ADR 0006
(«в том числе» hidden by default), the label LABEL; found again by its `settings.origin` {op: "intercepts", table:
name_key}. The model is `composites/ore_display.py` (Э12); `viz` is not touched (the mechanism of Э6).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bf_core.composites.ore_display import _BRAND
from bf_core.db import dialect

OP = "intercepts"
MAIN = "основное"
INCLUDED = "в том числе"
KINDS = (MAIN, INCLUDED)
KIND_COLORS = {MAIN: _BRAND["copper"], INCLUDED: _BRAND["cobalt"]}


def origin(name: str) -> dict[str, Any]:
    return {"op": OP, "table": dialect.name_key(name)}


def display_settings(name: str) -> dict[str, Any]:
    """The striplog of the result table `name` (`ui/src/charts/striplog/model.ts`): every hole of the set, a tube
    coloured by KIND («в том числе» hidden) with the label LABEL. The track names its table by `dh_tables.name_key`
    (`viz/keys.table_by_key`): it follows a replaced table of the same name."""
    label = {"on": True, "template": "{LABEL}", "decimals": None, "size": 12, "color": "#f3f6fb", "place": "outside"}
    track = {"id": "intercept-kind", "table": dialect.name_key(name), "field": "KIND", "type": "color", "width": 110,
             "color": KIND_COLORS[MAIN], "min": "", "max": "", "log": False, "boundaries": True, "form": "tube",
             "catColors": dict(KIND_COLORS), "catOrder": list(KINDS), "hidden": [INCLUDED],
             "label": label}  # fmt: skip
    return {"holes": [], "allHoles": True, "tracks": [track], "from": "", "to": "", "scale": 0, "grid": True,
            "tube": 8, "panel": 0, "labels": True, "form": "lith", "origin": origin(name)}  # fmt: skip


def _mine(conn: sa.Connection, set_id: int, name: str) -> list[dict[str, Any]]:
    from bf_core.charts import service as charts

    want = origin(name)
    return [c for c in charts.list_charts(conn, set_id, "striplog") if (c["settings"] or {}).get("origin") == want]


def put_display(conn: sa.Connection, set_id: int, name: str) -> int:
    """The display of the result table `name`: the one made for a table of this name gets its settings again (a
    rebuild, «Заменить»), else a new one named as the table (a name taken by another display — « (2)»). Its id."""
    from bf_core.charts import service as charts
    from bf_core.drillholes.edit import author

    settings = display_settings(name)
    mine = _mine(conn, set_id, name)
    if mine:
        return int(charts.update(conn, mine[0]["id"], None, settings, mine[0]["version"])["id"])
    names = {dialect.name_key(c["name"]) for c in charts.list_charts(conn, set_id)}
    title, k = name, 2
    while dialect.name_key(title) in names:
        title, k = f"{name} ({k})", k + 1
    return int(charts.create(conn, set_id, title, "striplog", settings, author())["id"])


def chart_of(conn: sa.Connection, set_id: int, name: str) -> int | None:
    mine = _mine(conn, set_id, name)
    return int(mine[0]["id"]) if mine else None


def drop_display(conn: sa.Connection, set_id: int, name: str) -> list[int]:
    """Deletes the displays made for the result table `name` (its name or name key); their ids."""
    from bf_core.charts import service as charts

    ids = [int(c["id"]) for c in _mine(conn, set_id, name)]
    for i in ids:
        charts.delete(conn, i)
    return ids
