"""The audit job end to end through the project database (ТЗ 7.12 «Аудит 50 000 скважин / 5 млн проб
завершается без нехватки памяти»; ADR 0003 «аудит ≤ 30 с»): read collars, surveys and the samples with the
needed columns only, run 27 rules, store the findings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

SURVEYS = 20


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    from bf_core.db import dialect
    from bf_core.drillholes.store import (
        CollarRow,
        ColumnSpec,
        TableData,
        create_set,
        replace_collars,
        replace_surveys,
        write_table,
    )
    from bf_core.projects.project import create_folder

    holes, rows = int(params["holes"]), int(params["rows"])
    rng = np.random.default_rng(5)
    folder = create_folder(work, "Аудит", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        names = [f"H{i:06d}" for i in range(holes)]
        per = rows // holes
        with dialect.write_tx(engine) as conn:
            set_id = create_set(conn, "Набор")
            replace_collars(
                conn,
                set_id,
                [
                    CollarRow(n, 1000 + (i % 224) * 25.0, 2000 + (i // 224) * 25.0, 300.0, float(per * 2), 90.0, -60.0)
                    for i, n in enumerate(names)
                ],
            )
        depth = np.linspace(0, per * 2, SURVEYS)
        surveys = [(n, float(d), 90.0 + rng.normal(0, 0.5), -60.0 + rng.normal(0, 0.5)) for n in names for d in depth]
        replace_surveys(engine, set_id, surveys, lambda _f, _m: None)
        hole_of = np.repeat(np.arange(holes), per)
        k = np.tile(np.arange(per), holes).astype(float)
        f = k * 2.0
        t = f + 2.0
        au = rng.lognormal(-1, 1, len(f))
        au[rng.random(len(f)) < 0.01] = np.nan
        data = TableData(
            "Пробы",
            "assay",
            [names[i] for i in hole_of.tolist()],
            f,
            t,
            [
                ColumnSpec("AU", "number", "g/t", "component"),
                ColumnSpec("CU", "number", "%", "component"),
                ColumnSpec("Номер пробы", "text", None, "sample"),
            ],
            [au, rng.lognormal(-2, 0.5, len(f)), [f"S{i:08d}" for i in range(len(f))]],
        )
        write_table(engine, set_id, data, job_id=None, tick=lambda _f, _m: None)
    finally:
        engine.dispose()
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")


def load() -> None:
    import bf_core.audit.checks  # noqa: F401
    import bf_core.audit.service  # noqa: F401


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import sqlalchemy as sa

    from bf_core.audit.service import default_table
    from bf_core.audit.service import run as run_audit
    from bf_core.db import dialect
    from bf_core.db.schema import objects

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.read_tx(engine) as conn:
            set_id = conn.execute(sa.select(objects.c.id)).scalar_one()
            table = default_table(conn, set_id)
        out = run_audit(engine, set_id, table=table, config={}, job_id=None, tick=lambda _f, _m=None: None)
    finally:
        engine.dispose()
    return {"findings": out["findings"], "totals": out["totals"]}
