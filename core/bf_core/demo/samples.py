"""Demo sample processing (ТЗ 7.5, 8.2; подшаг 12.32): «Композиты 2 м» of «Пробы» (AU, CU by length) and the
ore intervals by the ГКЗ method (Д-26) of AU — the table «Рудные интервалы», ORE_ID / ORE_CLS of the samples and
the display «Рудные интервалы».

Both are made by the bodies of their jobs (`composites.jobs.run`, `composites.ore_job.run_ore`) in this process,
after the parts of the demo and before its audit: the results are ordinary results of «Обработка проб» (their
parameters, «устарел», «Перестроить»). No random numbers: the stream of the demo is not touched.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects
from bf_core.projects.project import Project

from .drillholes import SET_NAME

COMPOSITES_NAME = "Композиты 2 м"
ORE_NAME = "Рудные интервалы"
#: job ids of the results (no job row: the demo makes them in this process)
_JOB_COMPOSITES = 0
_JOB_ORE = 0


def composites_body(set_id: int, table: str) -> dict[str, Any]:
    return {"set_id": set_id, "table": table, "fields": ["AU", "CU"], "method": "length", "length": 2,
            "name": COMPOSITES_NAME}  # fmt: skip


def ore_body(set_id: int, table: str) -> dict[str, Any]:
    """Сб 1 г/т, Мрт 4 м, Мпп 4 м, МС auto (Сб·Мрт), strict mode with the waste excluded (ТЗ 7.35.4)."""
    return {"set_id": set_id, "table": table, "field": "AU", "cutoff": 1.0, "m_min": 4.0, "m_waste_max": 4.0,
            "mode": "strict_excl", "missing": "break", "thickness": {"source": "length"}, "name": ORE_NAME}  # fmt: skip


def _samples_table(project: Project) -> tuple[int, str] | None:
    with dialect.read_tx(project.engine) as conn:
        set_id = conn.execute(
            sa.select(objects.c.id).where(objects.c.kind == "drillhole_set", objects.c.name == SET_NAME)
        ).scalar()
        if set_id is None:
            return None
        table = conn.execute(
            sa.select(dh_tables.c.id).where(dh_tables.c.set_id == set_id, dh_tables.c.name_key == dialect.name_key("Пробы"))
        ).scalar()
    return None if table is None else (int(set_id), str(table))


def add_samples(project: Project) -> None:
    """«Композиты 2 м» and «Рудные интервалы» of the set «Скважины 2025»."""
    from bf_core.composites import jobs as cj
    from bf_core.composites import ore_job as oj

    found = _samples_table(project)
    if found is None:
        return
    set_id, table = found

    def tick(_f: float | None = None, _m: str | None = None) -> None:
        return None

    cj.run(project.engine, project.folder, {"body": composites_body(set_id, table)}, job_id=_JOB_COMPOSITES, tick=tick)
    oj.run_ore(project.engine, project.folder, {"body": ore_body(set_id, table)}, job_id=_JOB_ORE, tick=tick)
