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


# --- the bulk writer against a row-by-row reference (Э12 12.33б) ------------------------------------------


def _reference_rows(data: dh.TableData) -> list[tuple[object, ...]]:
    """What Э4 stores for every row, the slow way: (hole, from, to, vector, qual, text map)."""
    import struct

    numbers = [i for i, c in enumerate(data.columns) if c.kind == "number"]
    texts = [i for i, c in enumerate(data.columns) if c.kind == "text"]
    out: list[tuple[object, ...]] = []
    for r, hole in enumerate(data.holes):
        vals = [float(data.values[i][r]) for i in numbers]
        w = len(vals)
        while w and math.isnan(vals[w - 1]):
            w -= 1
        num = struct.pack(f"<{w}d", *vals[:w]) if w else None
        qual = None
        if data.qual is not None:
            q = [int(data.qual[k][r]) for k in range(len(numbers))]
            qual = bytes(q) if any(q) else None
        txt = {}
        for slot, i in enumerate(texts):
            v = data.values[i][r]
            if v is None or v == "" or (isinstance(v, float) and math.isnan(v)):
                continue
            txt[str(slot)] = v
        f, t = float(data.from_m[r]), float(data.to_m[r])
        out.append((hole, None if math.isnan(f) else f, None if math.isnan(t) else t, num, qual, txt or None))
    return out


def _stored_rows(engine: sa.Engine, table_id: int) -> list[tuple[object, ...]]:
    with dialect.read_tx(engine) as conn:
        names = {h.id: h.name for h in conn.execute(sa.select(dh_holes.c.id, dh_holes.c.name))}
        rows = conn.execute(sa.select(dh_rows).where(dh_rows.c.table_id == table_id).order_by(dh_rows.c.id)).all()
    return [
        (names[r.hole_id], r.from_m, r.to_m, None if r.num is None else bytes(r.num),
         None if r.qual is None else bytes(r.qual), r.txt)
        for r in rows
    ]  # fmt: skip


def _raw_text(engine: sa.Engine, table_id: int) -> list[str | None]:
    """SQLite: the text maps as stored."""
    with dialect.read_tx(engine) as conn:
        raw = conn.exec_driver_sql("SELECT txt FROM dh_rows WHERE table_id = ? ORDER BY id", (table_id,)).all()
    return [r[0] for r in raw]


def _reference_text(want: list[tuple[object, ...]]) -> list[str | None]:
    """The text maps as Э4 wrote them: json.dumps(…, ensure_ascii=False)."""
    import json

    return [None if w[5] is None else json.dumps(w[5], ensure_ascii=False) for w in want]


def _hard_table(n: int, seed: int = 7) -> dh.TableData:
    """Rows a real base has: gaps and cut tails in the vectors, −0.0, ±inf and denormals, rows without «от»,
    holes in any order (some not among the collars), text with quotes, slashes, line breaks, Cyrillic, empty
    and missing values, a column of unique texts, detection-limit flags."""
    import numpy as np

    rng = np.random.default_rng(seed)
    names = [f"С-{i:03d}" for i in range(40)] + ['Скв "7"\\бис', "НОВАЯ\n1", "ZZ"]
    holes = [names[i] for i in rng.integers(0, len(names), n).tolist()]
    frm = np.round(rng.random(n) * 500.0, 2)
    to = frm + np.round(rng.random(n) * 3.0, 2)
    frm[rng.random(n) < 0.02] = np.nan
    to[rng.random(n) < 0.02] = np.nan
    a = rng.lognormal(0.0, 2.0, n)
    b = rng.random(n)
    c = rng.random(n)
    d = rng.random(n)
    a[rng.random(n) < 0.3] = np.nan
    b[rng.random(n) < 0.5] = np.nan
    c[rng.random(n) < 0.7] = np.nan  # the tail of the vector is often cut
    d[rng.random(n) < 0.9] = np.nan
    a[:4], b[:4], c[:4], d[:4] = np.nan, np.nan, np.nan, np.nan  # no value at all: NULL
    a[10:14] = [-0.0, np.inf, -np.inf, 5e-324]
    rocks = np.array(["гранит", 'сланец "чёрный"', "a\\b", "две\nстроки", "", None, "x" * 300], dtype=object)
    rock = rocks[rng.integers(0, len(rocks), n)]
    flag = np.array([None, "мало проб", "пологая"], dtype=object)[rng.integers(0, 3, n)]
    uniq = np.array([f"проба-{i}" if i % 11 else None for i in range(n)], dtype=object)
    qual = [rng.choice([0, 0, 0, 0, 1, 2], n).tolist() for _ in range(4)]
    qual[3] = np.zeros(n, dtype=np.int64)
    return dh.TableData(
        name="Трудная",
        kind="interval",
        holes=holes,
        from_m=frm,
        to_m=to,
        columns=[
            dh.ColumnSpec("AU", "number", "г/т", "component"),
            dh.ColumnSpec("ROCK", "text"),
            dh.ColumnSpec("CU", "number", "%"),
            dh.ColumnSpec("FLAG", "text"),
            dh.ColumnSpec("AG", "number"),
            dh.ColumnSpec("ID", "text"),
            dh.ColumnSpec("S", "number"),
        ],
        values=[a, rock, b.tolist(), flag.tolist(), c, uniq, d],
        qual=qual,
    )


@pytest.mark.parametrize("groups", [65_536, 8])
def test_bulk_rows_are_what_the_row_by_row_writer_stored(
    db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, groups: int
) -> None:
    """The vectorized writer in several portions (statements of many rows and their remainder) stores, bit
    for bit, the vectors, flags and text maps of a row-by-row encoder; missing stays missing, never 0. The
    text maps are written once per group of equal texts — for the whole table, or (more groups than `groups`:
    a column of sample numbers) portion by portion."""
    engine, s1, _ = _setup(db_url, tmp_path)
    monkeypatch.setattr(dh, "BATCH", 257)
    monkeypatch.setattr(dh._Texts, "GROUPS", groups)
    with dialect.write_tx(engine) as conn:
        dh.replace_collars(conn, s1, [dh.CollarRow(f"С-{i:03d}", 1.0, 2.0, 3.0, 100.0) for i in range(30, -1, -1)])
    data = _hard_table(1500)
    ticks: list[float | None] = []
    res = dh.write_table(engine, s1, data, job_id=None, tick=lambda p, _m: ticks.append(p))
    assert res["rows"] == 1500 and len(ticks) == 6 and ticks[0] == 0.0
    want = _reference_rows(data)
    got = _stored_rows(engine, res["table_id"])
    assert len(got) == len(want)
    for k, (g, w) in enumerate(zip(got, want, strict=True)):
        assert g == w, (k, g, w)
    assert sum(1 for g in got if g[3] is None) >= 4 and any(g[4] is not None for g in got)
    if db_url == "sqlite":
        assert _raw_text(engine, res["table_id"]) == _reference_text(want)
    with dialect.read_tx(engine) as conn:
        t = conn.execute(sa.select(dh_tables).where(dh_tables.c.id == res["table_id"])).one()
        assert (t.row_count, t.state) == (1500, "ready")
        added = {h.name for h in conn.execute(sa.select(dh_holes.c.name).where(dh_holes.c.has_collar.is_(False)))}
    assert added == set(data.holes) - {f"С-{i:03d}" for i in range(31)}


def test_bulk_rows_of_odd_tables(db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Text that is not text (numbers, flags — stored as given), no number column, no text column, no rows."""
    import numpy as np

    engine, s1, _ = _setup(db_url, tmp_path)
    monkeypatch.setattr(dh, "BATCH", 64)
    n = 300
    holes = [f"H{i % 9}" for i in range(n)]
    frm = np.arange(n, dtype=float)
    mixed = [None, 7, 2.5, True, "семь", "", "7", 1, 1.0, "1"]  # 1, 1.0 and True are equal, and stay apart
    odd = dh.TableData("Смесь", "interval", holes, frm, frm + 1.0,
                       [dh.ColumnSpec("V", "text"), dh.ColumnSpec("W", "text")],
                       [[mixed[i % 10] for i in range(n)], [("б" if i % 3 else None) for i in range(n)]])  # fmt: skip
    plain = dh.TableData("Числа", "interval", np.array(holes, dtype=object), frm.tolist(), (frm + 1.0).tolist(),
                         [dh.ColumnSpec("A", "number"), dh.ColumnSpec("B", "number")],
                         [np.where(np.arange(n) % 5 == 0, np.nan, 1.5), np.full(n, np.nan)])  # fmt: skip
    empty = dh.TableData("Пусто", "event", [], [], [], [dh.ColumnSpec("A", "number"), dh.ColumnSpec("T", "text")],
                         [[], []])  # fmt: skip
    for data in (odd, plain, empty):
        res = dh.write_table(engine, s1, data, job_id=None, tick=noop)
        want = _reference_rows(data)
        assert res["rows"] == len(want)
        assert _stored_rows(engine, res["table_id"]) == want, data.name
        if db_url == "sqlite":
            assert _raw_text(engine, res["table_id"]) == _reference_text(want), data.name
    with dialect.read_tx(engine) as conn:
        widths = {len(r.num or b"") for r in conn.execute(sa.select(dh_rows.c.num))}
    assert widths == {0, 8}  # «Числа»: the second column is empty — the vector is cut after the first


def test_row_keys_come_in_the_order_of_the_arrays(db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`row_ids` — the keys of the rows as given (SQLite: without reading them back); None on PostgreSQL."""
    engine, s1, _ = _setup(db_url, tmp_path)
    monkeypatch.setattr(dh, "BATCH", 70)
    dh.write_table(engine, s1, table("Другая", ["Z"] * 33, [1.0] * 33, ["x"] * 33), job_id=None, tick=noop)
    n = 500
    holes = [f"H{i % 13}" for i in range(n)]
    data = table("Пробы", holes, [float(i) for i in range(n)], ["x"] * n)
    res = dh.write_table(engine, s1, data, job_id=None, tick=noop)
    with dialect.read_tx(engine) as conn:
        mine = dh_rows.c.table_id == res["table_id"]
        rows = conn.execute(sa.select(dh_rows.c.id, dh_rows.c.from_m).where(mine).order_by(dh_rows.c.id)).all()
    assert [r.from_m for r in rows] == [float(i) for i in range(n)]  # the rows are stored in the order given
    if db_url == "sqlite":
        assert res["row_ids"].tolist() == [r.id for r in rows]
    else:
        assert res["row_ids"] is None
    empty = dh.write_table(engine, s1, table("Пусто", [], [], []), job_id=None, tick=noop)
    assert empty["rows"] == 0 and (empty["row_ids"] is None or len(empty["row_ids"]) == 0)


def test_holes_given_as_names_and_an_index(db_url: str, tmp_path: Path) -> None:
    """`hole_index` — the holes of the rows once more, as (names, index): the writer looks nothing up and
    stores the same rows; an index of another length is not used."""
    import numpy as np

    engine, s1, _ = _setup(db_url, tmp_path)
    names = ["Б-2", "А-1", "В-3"]
    index = np.array([1, 1, 0, 2, 0, 1], dtype=np.int32)
    holes = [names[i] for i in index.tolist()]
    au = [float(i) for i in range(6)]
    plain = table("Интервалы", holes, au, ["x"] * 6)
    plain.kind = "interval"
    fast = table("Быстрая", np.array(holes, dtype=object), au, ["x"] * 6)  # type: ignore[arg-type]
    fast.kind, fast.hole_index = "interval", (names, index)
    wrong = table("Не та", holes, au, ["x"] * 6)
    wrong.kind, wrong.hole_index = "interval", (["Г-4"], np.array([0, 0]))
    got = [
        _stored_rows(engine, dh.write_table(engine, s1, t, job_id=None, tick=noop)["table_id"])
        for t in (plain, fast, wrong)
    ]
    assert got[0] == got[1] == got[2] and [g[0] for g in got[0]] == holes
    with dialect.read_tx(engine) as conn:
        assert sorted(dh.hole_ids(conn, s1)) == sorted(names)  # «Г-4» of the unused index is not a hole


@pytest.mark.parametrize("gone", ["hole", "table"])
def test_a_parent_deleted_between_portions_fails_the_write(
    db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gone: str
) -> None:
    """The portions are separate transactions on one connection, statements of many rows on the driver cursor:
    a hole (or the table) deleted between two of them fails the write with the error of the foreign key, as a
    row-by-row insert did, and leaves no row without its hole."""
    engine, s1, _ = _setup(db_url, tmp_path)
    monkeypatch.setattr(dh, "BATCH", 100)
    holes = ["A"] * 150 + ["B"] * 150
    calls: list[float | None] = []

    def tick(p: float | None, _m: str | None) -> None:
        calls.append(p)
        if len(calls) == 2:  # the first portion is committed; the second has rows of A and of B
            with dialect.write_tx(engine) as conn:
                if gone == "hole":
                    conn.execute(sa.delete(dh_holes).where(dh_holes.c.name == "A"))
                else:
                    conn.execute(sa.delete(dh_tables).where(dh_tables.c.job_id == 3))

    with pytest.raises(sa.exc.DBAPIError) as err:
        dh.write_table(engine, s1, table("Пробы", holes, [1.0] * 300, ["x"] * 300), job_id=3, tick=tick)
    assert dialect.is_integrity_error(err.value) and len(calls) == 2
    with dialect.read_tx(engine) as conn:
        orphans = conn.execute(
            sa.select(sa.func.count())
            .select_from(dh_rows)
            .where(sa.or_(dh_rows.c.hole_id.not_in(sa.select(dh_holes.c.id)),
                          dh_rows.c.table_id.not_in(sa.select(dh_tables.c.id))))
        ).scalar_one()  # fmt: skip
        left = conn.execute(sa.select(sa.func.count()).select_from(dh_rows)).scalar_one()
    assert orphans == 0 and left == 0  # the rows of the first portion went with their hole (table)
