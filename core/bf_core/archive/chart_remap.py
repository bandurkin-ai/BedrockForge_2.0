"""Ids of other objects inside the settings of saved charts on a `.bfggis` import (ADR 0019 §2).

The `charts` rows come with their objects (`archive/spec.py`: `charts.object_id` is renumbered as a column);
their settings refer to other objects by id: the set of the side Y of a Q-Q plot and of a scatter
(`y_set_id`), the wireframe of a contact analysis (`wireframe_id`), the sets and models of the series of a swath
plot (`series[].set_id`, `series[].model_id`) — `charts/kinds.py::map_ids`. An object the archive does not have
(deleted before the export) becomes null with the rest of the series kept — the window shows «источник удалён»;
the old number would point at another object of the new project. Tables, fields and holes are names: they need
nothing. Displays along holes (`striplog`) hold no ids.

Called once all rows are written: `remap_charts(conn, maps["objects"])` in `importer._restore`."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa


def remap_charts(conn: sa.Connection, objects_map: dict[int, int]) -> int:
    """Renumbers the ids inside the settings of every chart; returns how many charts changed."""
    from bf_core.charts.kinds import STRIPLOG, map_ids
    from bf_core.db.schema import charts

    rows = conn.execute(sa.select(charts.c.object_id, charts.c.settings).where(charts.c.kind != STRIPLOG)).all()
    changed = 0
    for chart_id, settings in rows:
        renumbered: Any = map_ids(settings, objects_map.get)
        if renumbered is not settings:
            conn.execute(sa.update(charts).where(charts.c.object_id == chart_id).values(settings=renumbered))
            changed += 1
    return changed
