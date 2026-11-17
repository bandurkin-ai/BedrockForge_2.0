"""The «Таблица» panel on a big assay table (ТЗ 7.10 «Таблица на 5 млн проб листается, сортируется и
фильтруется по скважине»; ADR 0003 §4).

Measured: the first snapshot from the database (vectors decoded, Parquet written), then pages — sorted by a
grade, the far end of the table, one hole, a filter by expression, Σ of a field, and one cell edit that
patches the snapshot instead of rebuilding it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

HOLE_ROWS = 100


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    from bf_core.db import dialect
    from bf_core.drillholes.store import ColumnSpec, TableData, create_set, write_table
    from bf_core.projects.project import create_folder

    n = int(params["rows"])
    rng = np.random.default_rng(11)
    folder = create_folder(work, "Таблица", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.write_tx(engine) as conn:
            set_id = create_set(conn, "Набор")
        holes = [f"DH{i // HOLE_ROWS:06d}" for i in range(n)]
        depth = (np.arange(n) % HOLE_ROWS).astype(float)
        au = rng.lognormal(0.0, 1.0, n)
        au[rng.random(n) < 0.05] = np.nan  # missing values sort last
        data = TableData(
            "Пробы",
            "assay",
            holes,
            depth,
            depth + 1,
            [ColumnSpec("AU", "number", "g/t", "component"), ColumnSpec("CU", "number", "%", "component"),
             ColumnSpec("Номер пробы", "text", None, "sample")],
            [au, rng.lognormal(-1.0, 0.5, n), [f"S{i:08d}" for i in range(n)]],
        )
        write_table(engine, set_id, data, job_id=None, tick=lambda _f, _m: None)
    finally:
        engine.dispose()
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")


def load() -> None:
    import bf_core.drillholes.query  # noqa: F401
    import bf_core.drillholes.snapshot  # noqa: F401


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import dh_tables
    from bf_core.drillholes.query import page, stats
    from bf_core.drillholes.snapshot import build_rows, table_key

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    out: dict[str, Any] = {}
    try:
        with dialect.read_tx(engine) as conn:
            t = conn.execute(sa.select(dh_tables.c.id, dh_tables.c.set_id)).one()
            key = table_key(conn, "bench", t.set_id, str(t.id))
            assert key is not None
            t0 = time.perf_counter()
            snap = build_rows(conn, t.set_id, t.id, key, folder / "cache" / "tables")
            out["build_s"] = round(time.perf_counter() - t0, 3)
            t0 = time.perf_counter()
            build_rows(conn, t.set_id, t.id, key, folder / "cache" / "tables")  # from the Parquet file
            out["reopen_s"] = round(time.perf_counter() - t0, 3)
        au = next(c.key for c in snap.columns if c.name == "AU")
        t0 = time.perf_counter()
        first = page(snap, sort=au, desc=True, limit=100)
        out["sort_s"] = round(time.perf_counter() - t0, 3)
        assert first["rows"][0][au] is not None
        t0 = time.perf_counter()
        last = page(snap, sort=au, desc=True, offset=first["total"] - 100, limit=100)  # the cached order
        out["far_page_s"] = round(time.perf_counter() - t0, 3)
        assert last["rows"][-1][au] is None  # missing last
        hole = int(snap.hole_ids[len(snap.ids) // 2])
        t0 = time.perf_counter()
        one = page(snap, hole=hole, sort="from")
        out["hole_s"] = round(time.perf_counter() - t0, 3)
        assert one["total"] == HOLE_ROWS
        t0 = time.perf_counter()
        hit = page(snap, where="AU > 5 и CU < 0,3")
        out["filter_s"] = round(time.perf_counter() - t0, 3)
        assert hit["total"] > 0
        t0 = time.perf_counter()
        s = stats(snap, au)
        out["stats_s"] = round(time.perf_counter() - t0, 3)
        out["rows"] = len(snap.ids)
        assert s["filled"] + s["empty"] == len(snap.ids)
    finally:
        engine.dispose()
    return out
