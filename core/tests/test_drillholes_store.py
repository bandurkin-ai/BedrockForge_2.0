"""Drillhole set storage (ADR 0003 §4): vectors, collar replace, table swap, cancel cleanup."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import sqlalchemy as sa

from bf_core.db import dialect, migrate
from bf_core.db.schema import dh_columns, dh_holes, dh_rows, dh_surveys, dh_tables
from bf_core.drillholes import store as dh
from tests.dbutil import make_engine

pytestmark = pytest.mark.db


def noop(_p: float | None, _m: str | None) -> None:
    pass


def test_pack_keeps_missing_as_nan_and_cuts_the_tail() -> None:
    data = dh.pack([1.5, None, 0.0, math.nan])
    assert data is not None and len(data) == 24  # the trailing NaN is cut
    vals = dh.unpack(data, 5)
    assert vals[0] == 1.5 and math.isnan(vals[1]) and vals[2] == 0.0 and math.isnan(vals[3]) and math.isnan(vals[4])
    assert dh.pack([None, math.nan]) is None


def _setup(db_url: str, tmp_path: Path) -> tuple[sa.Engine, int, int]:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        s1 = dh.create_set(conn, "Набор 1")
        s2 = dh.create_set(conn, "Набор 2")
    return engine, s1, s2


def table(name: str, holes: list[str], au: list[float | None], lith: list[str]) -> dh.TableData:
    n = len(holes)
    return dh.TableData(
        name=name,
        kind="assay",
        holes=holes,
        from_m=[float(i) for i in range(n)],
        to_m=[float(i + 1) for i in range(n)],
        columns=[dh.ColumnSpec("AU", "number", "g/t", "component"), dh.ColumnSpec("LITH", "text")],
        values=[au, lith],
    )


def test_collar_replace_touches_only_its_set(db_url: str, tmp_path: Path) -> None:
    engine, s1, s2 = _setup(db_url, tmp_path)
    with dialect.write_tx(engine) as conn:
        dh.replace_collars(conn, s1, [dh.CollarRow("C1", 1, 2, 3, 100), dh.CollarRow("C2", 4, 5, 6, 50)])
        dh.replace_collars(conn, s2, [dh.CollarRow("C1", 9, 9, 9, 10)])
    dh.replace_surveys(engine, s1, [("C1", 0.0, 90.0, -60.0), ("C3", 0.0, 0.0, -90.0)], noop)
    with dialect.write_tx(engine) as conn:
        r = dh.replace_collars(conn, s1, [dh.CollarRow("C1", 10, 20, 30, 120, attrs={"Участок": "Север"})])
    assert r == {"added": 0, "updated": 1, "collars": 1}
    with dialect.read_tx(engine) as conn:
        rows = {(h.set_id, h.name): h for h in conn.execute(sa.select(dh_holes))}
        assert rows[(s1, "C1")].x == 10 and rows[(s1, "C1")].attrs == {"Участок": "Север"}
        assert rows[(s1, "C2")].has_collar is False and rows[(s1, "C2")].x is None
        assert rows[(s1, "C3")].has_collar is False  # met in surveys only
        assert rows[(s2, "C1")].x == 9  # the other set is untouched
        assert conn.execute(sa.select(sa.func.count()).select_from(dh_surveys)).scalar_one() == 2


def test_table_swap_replaces_same_name_and_keeps_others(db_url: str, tmp_path: Path) -> None:
    engine, s1, s2 = _setup(db_url, tmp_path)
    dh.write_table(
        engine,
        s1,
        table("Пробы", ["A", "A", "B"], [1.0, None, 0.005], ["гранит", "", "диорит"]),
        job_id=None,
        tick=noop,
    )
    dh.write_table(engine, s2, table("Пробы", ["A"], [7.0], ["x"]), job_id=None, tick=noop)
    res = dh.write_table(engine, s1, table("ПРОБЫ", ["A", "B"], [2.0, 3.0], ["a", "b"]), job_id=5, tick=noop)
    with dialect.read_tx(engine) as conn:
        tables = conn.execute(sa.select(dh_tables).order_by(dh_tables.c.id)).mappings().all()
        assert [(t["set_id"], t["row_count"], t["state"]) for t in tables] == [(s2, 1, "ready"), (s1, 2, "ready")]
        assert tables[1]["id"] == res["table_id"]
        cols = conn.execute(
            sa.select(dh_columns.c.name, dh_columns.c.slot, dh_columns.c.kind).where(
                dh_columns.c.table_id == res["table_id"]
            )
        ).all()
        assert sorted(cols) == [("AU", 0, "number"), ("LITH", 0, "text")]
        row = (
            conn.execute(sa.select(dh_rows).where(dh_rows.c.table_id == res["table_id"]).order_by(dh_rows.c.id))
            .mappings()
            .first()
        )
        assert dh.unpack(row["num"], 1) == [2.0] and row["txt"] == {"0": "a"}
        summary_rows = dh.count_assays(conn)
    assert summary_rows == 3


def test_cancelled_import_leaves_old_table_and_no_new_holes(db_url: str, tmp_path: Path) -> None:
    engine, s1, _ = _setup(db_url, tmp_path)
    dh.write_table(engine, s1, table("Пробы", ["A"], [1.0], ["x"]), job_id=None, tick=noop)

    calls = {"n": 0}

    def cancel_after_first(_p: float | None, _m: str | None) -> None:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("cancel")

    big = ["H" + str(i % 7) for i in range(dh.BATCH + 10)]
    with pytest.raises(RuntimeError):
        dh.write_table(
            engine, s1, table("Пробы", big, [1.0] * len(big), ["y"] * len(big)), job_id=9, tick=cancel_after_first
        )
    with dialect.write_tx(engine) as conn:
        dh.drop_building(conn, 9)
    with dialect.read_tx(engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(dh_tables)).scalar_one() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(dh_rows)).scalar_one() == 1
        assert [h.name for h in conn.execute(sa.select(dh_holes.c.name))] == ["A"]


def test_more_holes_than_sqlite_parameters(db_url: str, tmp_path: Path) -> None:
    """40 000 holes: no IN list of names (SQLite allows 32 766 parameters)."""
    engine, s1, _ = _setup(db_url, tmp_path)
    names = [f"H{i:05d}" for i in range(40_000)]
    dh.write_table(engine, s1, table("Пробы", names, [1.0] * len(names), ["x"] * len(names)), job_id=None, tick=noop)
    with dialect.write_tx(engine) as conn:
        r = dh.replace_collars(conn, s1, [dh.CollarRow(n, 1, 2, 3, 10) for n in names[:35_000]])
        assert r["updated"] == 35_000
        r = dh.replace_collars(conn, s1, [dh.CollarRow("H00000", 5, 5, 5, 10)])
    with dialect.read_tx(engine) as conn:
        with_collar = conn.execute(
            sa.select(sa.func.count()).select_from(dh_holes).where(dh_holes.c.has_collar.is_(True))
        ).scalar_one()
    assert with_collar == 1
