"""The read tools of the assistant on a big project (ADR 0029 решение 38; ТЗ 7.32, 7.12): `data_tree`, `data_holes`,
`data_stats` through `data_tools.run` as the loop calls them (the check of the arguments, the thread with the limit of
5 s, the cleaning and the cut of the answer to 16 000 signs). The project is the one of `audit_db` (ТЗ 7.12: 50 000
holes, 20 surveys each, 5 million samples of the table «Пробы»; `prepare` of that scenario builds it).

`run` times (best of 3; keys of `info`, the budgets of `limits`):
  tree_s     `data_tree` of the project;
  holes_s    `data_holes`, a page of 50 from the offset of 80 % of the set;
  search_s   `data_holes` with `search` (report only);
  stats_s    `data_stats` of AU of «Пробы» with the columns in the cache of the charts, as on the second call of the
             window: the first one reads the 5 million samples from the database (`stats_cold_s`, report only, under
             a longer limit than the 5 s of a tool — the window has the table open before Claude asks).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bench.scenarios import assistant_common as common
from bench.scenarios import audit_db


def prepare(params: dict[str, Any], work: Path) -> None:
    audit_db.prepare(params, work)


def load() -> None:
    import bf_core.assistant.data_tools as data_tools
    import bf_core.stats.routes_more  # noqa: F401

    data_tools.load_all()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import sqlalchemy as sa

    from bf_core.assistant import data_tools as dt
    from bf_core.db import dialect
    from bf_core.db.schema import objects

    project = common.open_project(work)
    out: dict[str, Any] = {"holes": int(params["holes"]), "rows": int(params["rows"])}
    try:
        with dialect.read_tx(project.engine) as conn:
            query = sa.select(objects.c.id).where(objects.c.kind == "drillhole_set").order_by(objects.c.id)
            set_id = int(conn.execute(query).scalar_one())

        def best(name: str, args: dict[str, Any], key: str) -> dict[str, Any]:
            seconds = []
            body: dict[str, Any] = {}
            for _ in range(3):
                t0 = time.perf_counter()
                res = dt.run(name, args, project=project, set_id=set_id)
                seconds.append(time.perf_counter() - t0)
                assert not res.is_error, res.text
                assert len(res.text) <= dt.MAX_TEXT
                body = json.loads(res.text)
            out[key] = round(min(seconds), 4)
            return body

        tree = best("data_tree", {}, "tree_s")
        assert tree["nodes"], tree
        holes = int(params["holes"])
        page = best("data_holes", {"limit": 50, "offset": holes * 4 // 5}, "holes_s")
        assert page["total"] == holes and len(page["rows"]) == 50, page["total"]
        best("data_holes", {"search": f"H{holes // 2:06d}"[:5]}, "search_s")

        t0 = time.perf_counter()
        cold = dt.run("data_stats", {"table": "Пробы", "field": "AU"}, project=project, set_id=set_id, timeout=900.0)
        out["stats_cold_s"] = round(time.perf_counter() - t0, 3)
        assert not cold.is_error, cold.text
        stats = best("data_stats", {"table": "Пробы", "field": "AU"}, "stats_s")
        assert stats["n"] + stats["missing"] == int(params["rows"]), stats
    finally:
        project.engine.dispose()
    return out
