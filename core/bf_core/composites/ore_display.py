"""The display «Рудные интервалы» of an ore result (ТЗ 7.35 Д-26 «Результат» (3); ADR 0018 решение 20; split off
`ore_job` in 12.34): a saved striplog of the set named as the result table — a colour column by CLASS in the brand
colours of ADR 0006 (those of `charts/column/classes.ts`), `frame` hidden, the label LABEL; found again by its
`settings.origin` {op: "ore", table: name_key}.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db import dialect

OP = "ore"

# The colours of the classes: those of `ui/src/charts/column/classes.ts` (brand palette, ADR 0006; решение 20).
_BRAND = {"anthracite": "#1b222b", "cobalt": "#1f6bff", "copper": "#f46a1a", "stone": "#e6e9ee", "slate": "#8a99a8"}


def _mix(a: str, b: str, t: float) -> str:
    x = [int(_BRAND[a][i : i + 2], 16) for i in (1, 3, 5)]
    y = [int(_BRAND[b][i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(np.floor(v + (w - v) * t + 0.5)):02x}" for v, w in zip(x, y, strict=True))


CLASS_COLORS = {
    "ore": _BRAND["copper"],
    "subgrade": _mix("copper", "stone", 0.55),
    "waste_in": _mix("slate", "stone", 0.3),
    "interlayer": _BRAND["cobalt"],
    "waste": _mix("anthracite", "slate", 0.45),
    "frame": _mix("slate", "stone", 0.65),
}


def display_settings(name: str) -> dict[str, Any]:
    """The striplog of the result table `name` (`ui/src/charts/striplog/model.ts`): every hole of the set, a tube
    coloured by CLASS (`frame` hidden by default, решение 20) with the label LABEL. The track names its table by
    `dh_tables.name_key`, as every display does (`viz/keys.table_by_key`): it follows a replaced table."""
    from .ore_set import CLASS_NAMES

    label = {"on": True, "template": "{LABEL}", "decimals": None, "size": 12, "color": "#f3f6fb", "place": "outside"}
    track = {"id": "ore-class", "table": dialect.name_key(name), "field": "CLASS", "type": "color", "width": 110,
             "color": CLASS_COLORS["ore"], "min": "", "max": "", "log": False, "boundaries": True, "form": "tube",
             "catColors": dict(CLASS_COLORS), "catOrder": list(CLASS_NAMES), "hidden": ["frame"],
             "label": label}  # fmt: skip
    return {"holes": [], "allHoles": True, "tracks": [track], "from": "", "to": "", "scale": 0, "grid": True,
            "tube": 8, "panel": 0, "labels": True, "form": "lith",
            "origin": {"op": OP, "table": dialect.name_key(name)}}  # fmt: skip


def put_display(conn: sa.Connection, set_id: int, name: str) -> int:
    """The display of the result table `name`: the one made for a table of this name gets its settings again,
    else a new one (named as the table; a name taken by another display — « (2)»). Returns its id."""
    from bf_core.charts import service as charts
    from bf_core.drillholes.edit import author

    settings = display_settings(name)
    shown = charts.list_charts(conn, set_id, "striplog")
    mine = next((c for c in shown if (c["settings"] or {}).get("origin") == settings["origin"]), None)
    if mine is not None:
        return int(charts.update(conn, mine["id"], None, settings, mine["version"])["id"])
    names = {dialect.name_key(c["name"]) for c in charts.list_charts(conn, set_id)}
    title, k = name, 2
    while dialect.name_key(title) in names:
        title, k = f"{name} ({k})", k + 1
    return int(charts.create(conn, set_id, title, "striplog", settings, author())["id"])


def chart_of(conn: sa.Connection, set_id: int, name: str) -> int | None:
    from bf_core.charts import service as charts

    origin = {"op": OP, "table": dialect.name_key(name)}
    return next((int(c["id"]) for c in charts.list_charts(conn, set_id, "striplog")
                 if (c["settings"] or {}).get("origin") == origin), None)  # fmt: skip


