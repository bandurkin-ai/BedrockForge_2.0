"""Source and sink of sample processing (ADR 0018 §2–4, подшаг 12.19): narrow reading, codes, SHA-256, the
building table and the final transaction, columns in place, «Заменить», the Parquet snapshot."""

from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.composites import results, sink, source
from bf_core.db import dialect, migrate
from bf_core.db.schema import dh_columns, dh_results, dh_rows, dh_tables, edit_log
from bf_core.drillholes import actions, snapshot
from bf_core.drillholes import store as dh
from tests.dbutil import make_engine

pytestmark = pytest.mark.db
JOB = 4242

ROWS: list[tuple[str, float | None, float | None, float | None, float, str | None]] = [
    # hole, from, to, AU, CU, LITH
    ("C-2", 0.0, 1.0, 1.5, 0.1, "гранит"),
    ("C-2", 1.0, 2.5, None, 0.2, "диорит"),
    ("A-1", 0.0, 2.0, 0.0, 0.3, None),
    ("A-1", 2.0, 3.0, 4.25, 0.4, "гранит"),
    ("A-1", None, 4.0, 9.0, 0.5, "x"),  # no FROM: left out, counted
    ("B-7", 5.0, 6.0, 0.7, 0.6, "диорит"),
]


def noop(_p: float | None, _m: str | None) -> None:
    pass


def samples(rows: list[Any] = ROWS, name: str = "Пробы") -> dh.TableData:
    return dh.TableData(
        name=name,
        kind="assay" if name == "Пробы" else "interval",  # one assay table per set
        holes=[r[0] for r in rows],
        from_m=[r[1] for r in rows],
        to_m=[r[2] for r in rows],
        columns=[
            dh.ColumnSpec("AU", "number", "g/t", "component"),
            dh.ColumnSpec("CU", "number", "%", "component"),
            dh.ColumnSpec("LITH", "text"),
        ],
        values=[[r[3] for r in rows], [r[4] for r in rows], [r[5] for r in rows]],
    )


def setup(db_url: str, tmp_path: Path, rows: list[Any] = ROWS) -> tuple[sa.Engine, int, int]:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
    tid = dh.write_table(engine, set_id, samples(rows), job_id=None, tick=noop)["table_id"]
    return engine, set_id, tid


def out_table(lines: source.Lines, name: str = "Композиты 2 м", scale: float = 1.0) -> sink.OutTable:
    holes = [lines.holes[i] for i in lines.hole_of().tolist()]
    return sink.OutTable(
        name=name,
        holes=holes,
        from_m=lines.from_m,
        to_m=lines.to_m,
        columns=[dh.ColumnSpec("AU", "number", "g/t", "component"), dh.ColumnSpec("LITH", "text")],
        values=[lines.numbers["AU"] * scale, np.array([None, "a", "b", None, "c"], dtype=object)],
    )


def commit_of(engine: sa.Engine, lines: source.Lines, **kw: Any) -> sink.Commit:
    with dialect.read_tx(engine) as conn:
        inputs = results.make_inputs(conn, lines.set_id, [lines.input()])
    args: dict[str, Any] = dict(
        set_id=lines.set_id,
        op="length",
        name="Композиты 2 м",
        params={"length": 2.0},
        inputs=inputs,
        sha256=source.input_hash([lines], {"length": 2.0}),
        algo="1",
        job_id=JOB,
    )
    return sink.Commit(**{**args, **kw})


def names(engine: sa.Engine, set_id: int) -> dict[str, int]:
    with dialect.read_tx(engine) as conn:
        return {
            r.name: int(r.id)
            for r in conn.execute(
                sa.select(dh_tables.c.id, dh_tables.c.name).where(
                    dh_tables.c.set_id == set_id, dh_tables.c.state == "ready"
                )
            )
        }


def columns(engine: sa.Engine, table_id: int) -> dict[str, Any]:
    with dialect.read_tx(engine) as conn:
        return {c.name: c for c in conn.execute(sa.select(dh_columns).where(dh_columns.c.table_id == table_id))}


def test_lines_order_codes_and_skipped_rows(db_url: str, tmp_path: Path) -> None:
    engine, set_id, tid = setup(db_url, tmp_path)
    lines = source.read(engine, set_id, tid, ["au", "LITH"])
    assert lines.source == "rows" and lines.skipped == 1 and lines.rows == 5
    assert lines.holes == ["A-1", "B-7", "C-2"] and lines.starts.tolist() == [0, 2, 3, 5]
    assert lines.from_m.tolist() == [0.0, 2.0, 5.0, 0.0, 1.0]
    au = lines.numbers["AU"]
    assert au[0] == 0.0 and au[1] == 4.25 and np.isnan(au[4])  # a zero stays a zero, missing stays NaN
    assert lines.labels["LITH"] == ["гранит", "диорит"]
    assert lines.codes["LITH"].tolist() == [-1, 0, 1, 0, 1]
    assert set(lines.fields) == {"AU", "LITH"} and lines.fields["AU"].unit == "g/t"
    with pytest.raises(ApiError) as err:
        source.read(engine, set_id, tid, ["AG"])
    assert err.value.code == "field_not_found" and err.value.hint


def test_parquet_snapshot_gives_the_same_lines(tmp_path: Path) -> None:
    engine, set_id, tid = setup("sqlite", tmp_path)
    cache = tmp_path / "cache" / "tables"
    with dialect.read_tx(engine) as conn:
        key = snapshot.table_key(conn, "u", set_id, str(tid))
        assert key is not None
        snap = snapshot.build_rows(conn, set_id, tid, key, cache)
    snapshot._to_parquet(snap, cache / f"{tid}-{key[-2]}-{key[-1]}.parquet")
    cold = source.read(engine, set_id, tid, ["AU", "CU", "LITH"])
    warm = source.read(engine, set_id, tid, ["AU", "CU", "LITH"], cache_dir=cache)
    assert cold.source == "rows" and warm.source == "parquet"
    for a, b in ((cold.row_ids, warm.row_ids), (cold.from_m, warm.from_m), (cold.numbers["AU"], warm.numbers["AU"])):
        assert np.array_equal(a, b, equal_nan=True)
    assert cold.codes["LITH"].tolist() == warm.codes["LITH"].tolist() and cold.labels == warm.labels
    assert source.input_hash([cold], {}) == source.input_hash([warm], {})


def test_sha256_does_not_depend_on_the_order_of_rows(db_url: str, tmp_path: Path) -> None:
    engine, set_id, tid = setup(db_url, tmp_path)
    shuffled = ROWS + [("B-7", 5.0, 6.0, 0.1, 0.6, "диорит")]  # a tie (same hole, FROM, TO): sorted by values
    random.Random(3).shuffle(shuffled)
    other = dh.write_table(engine, set_id, samples(shuffled, "Пробы 2"), job_id=None, tick=noop)["table_id"]
    tie = dh.write_table(engine, set_id, samples(ROWS + [ROWS[-1][:3] + (0.1, 0.6, "диорит")], "П3"), job_id=None,
                         tick=noop)["table_id"]  # fmt: skip
    p = {"length": 2.0, "fields": ["AU"]}
    a = source.input_hash([source.read(engine, set_id, other, ["AU", "LITH"])], p)
    b = source.input_hash([source.read(engine, set_id, tie, ["AU", "LITH"])], p)
    assert a == b
    base = source.input_hash([source.read(engine, set_id, tid, ["AU", "LITH"])], p)
    assert base != a  # one row more
    assert base != source.input_hash([source.read(engine, set_id, tid, ["AU", "LITH"])], {**p, "length": 1.0})
    assert base != source.input_hash([source.read(engine, set_id, tid, ["AU", "LITH"])], p, stations=b"\0")
    assert base != source.input_hash([source.read(engine, set_id, tid, ["AU", "CU", "LITH"])], p)


def test_trajectory_stations_go_into_the_hash(tmp_path: Path) -> None:
    engine, set_id, tid = setup("sqlite", tmp_path)
    with dialect.write_tx(engine) as conn:
        dh.replace_collars(
            conn, set_id, [dh.CollarRow("A-1", 10, 20, 300, 10, 0, -90), dh.CollarRow("C-2", 0, 0, 0, 5)]
        )
    project = SimpleNamespace(engine=engine, uid="u", folder=tmp_path)
    lines = source.read(engine, set_id, tid, ["AU"])
    one = source.stations_bytes(source.trajectories(project, set_id), lines.hole_db_ids.tolist())
    with dialect.write_tx(engine) as conn:
        dh.replace_collars(
            conn, set_id, [dh.CollarRow("A-1", 10, 20, 301, 10, 0, -90), dh.CollarRow("C-2", 0, 0, 0, 5)]
        )
        conn.execute(sa.text("UPDATE objects SET version = version + 1"))
    two = source.stations_bytes(source.trajectories(project, set_id), lines.hole_db_ids.tolist())
    assert one != two and source.input_hash([lines], {}, stations=one) != source.input_hash([lines], {}, stations=two)


def test_cancel_and_failure_leave_no_table_and_no_columns(db_url: str, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine, set_id, tid = setup(db_url, tmp_path)
    lines = source.read(engine, set_id, tid, ["AU"])
    with pytest.raises(KeyboardInterrupt), sink.guarded(engine, JOB):  # a cancel while the table is built
        sink.build_table(engine, set_id, out_table(lines), job_id=JOB)
        raise KeyboardInterrupt
    cut = sink.ColumnsOut(tid, lines.row_ids, [(dh.ColumnSpec("AU_CUT", "number", "g/t"), lines.numbers["AU"])])

    def broken(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("сбой")

    monkeypatch.setattr(results, "record", broken)
    with pytest.raises(RuntimeError), sink.guarded(engine, JOB):  # a crash inside the final transaction
        built = sink.build_table(engine, set_id, out_table(lines), job_id=JOB)
        sink.commit(engine, commit_of(engine, lines, table=built, columns=[cut]))
    with dialect.read_tx(engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(dh_tables)).scalar_one() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(dh_results)).scalar_one() == 0
        assert conn.execute(sa.select(sa.func.max(dh_rows.c.table_id))).scalar_one() == tid
        assert conn.execute(sa.select(sa.func.count()).select_from(edit_log)).scalar_one() == 0
        assert conn.execute(sa.select(dh_tables.c.rev).where(dh_tables.c.id == tid)).scalar_one() == 1
    assert set(columns(engine, tid)) == {"AU", "CU", "LITH"}


def test_name_taken_rename_and_replace(db_url: str, tmp_path: Path) -> None:
    engine, set_id, tid = setup(db_url, tmp_path)
    lines = source.read(engine, set_id, tid, ["AU"])
    first = sink.commit(
        engine, commit_of(engine, lines, table=sink.build_table(engine, set_id, out_table(lines), job_id=JOB))
    )
    assert first["name"] == "Композиты 2 м" and names(engine, set_id)["Композиты 2 м"] == first["table_id"]
    built = sink.build_table(engine, set_id, out_table(lines, "композиты 2 М"), job_id=JOB)
    with pytest.raises(sink.TableExists) as err:
        sink.commit(engine, commit_of(engine, lines, name="композиты 2 М", table=built))
    assert err.value.status == 409 and err.value.code == "table_exists" and "Заменить" in err.value.hint
    second = sink.commit(engine, commit_of(engine, lines, table=built, mode="rename"))
    assert second["name"] == "Композиты 2 м (2)"
    third = sink.commit(
        engine,
        commit_of(engine, lines, table=sink.build_table(engine, set_id, out_table(lines), job_id=JOB), mode="rename"),
    )
    assert third["name"] == "Композиты 2 м (3)"
    built = sink.build_table(engine, set_id, out_table(lines, scale=2.0), job_id=JOB)
    swapped = sink.commit(engine, commit_of(engine, lines, table=built, mode="replace"))
    assert swapped["replaced"] == [first["table_id"]] and swapped["name"] == "Композиты 2 м"
    got = names(engine, set_id)
    assert got["Композиты 2 м"] == built and first["table_id"] not in got.values()
    with dialect.read_tx(engine) as conn:
        ids = set(conn.execute(sa.select(dh_results.c.id)).scalars())
        assert first["result_id"] not in ids and swapped["result_id"] in ids  # the replaced record went with its table
        assert (
            conn.execute(
                sa.select(sa.func.count()).select_from(dh_tables).where(dh_tables.c.state != "ready")
            ).scalar_one()
            == 0
        )
    with pytest.raises(sink.TableExists):  # «Заменить» never replaces a table of another kind (the samples)
        sink.commit(
            engine,
            commit_of(
                engine,
                lines,
                name="Пробы",
                table=sink.build_table(engine, set_id, out_table(lines, "Пробы"), job_id=JOB),
                mode="replace",
            ),  # fmt: skip
        )


def test_columns_in_place_undo_and_repeat(db_url: str, tmp_path: Path) -> None:
    engine, set_id, tid = setup(db_url, tmp_path)
    lines = source.read(engine, set_id, tid, ["AU", "LITH"])

    def reader(conn: sa.Connection, name: str, fields: list[str]) -> int:
        inputs = results.make_inputs(conn, set_id, [(tid, fields)])
        return results.record(
            conn, set_id=set_id, table_id=None, op="conditions", name=name, params={}, inputs=inputs, sha256="0",
            algo="1", report={},
        )  # fmt: skip

    with dialect.write_tx(engine) as conn:  # a result that reads AU only: it stays fresh
        keeps = reader(conn, "К", ["AU"])

    def run(scale: float) -> dict[str, Any]:
        au = lines.numbers["AU"]
        cut = np.where(au > 2.0, 2.0, au) * scale
        cls = np.array(["руда" if v > 1 else None for v in np.nan_to_num(au)], dtype=object)
        out = sink.ColumnsOut(
            tid,
            lines.row_ids,
            [(dh.ColumnSpec("AU_CUT", "number", "g/t"), cut), (dh.ColumnSpec("ORE_CLS", "text"), cls)],
        )
        return sink.commit(engine, commit_of(engine, lines, op="topcut", name="Урезка · AU", columns=[out]))

    first = run(1.0)
    assert first["changed"] == [{"table": str(tid), "rev": 2}] and first["table_id"] == tid
    cols = columns(engine, tid)
    with dialect.write_tx(engine) as conn:  # one that reads AU_CUT: the repeat makes it stale
        later = reader(conn, "К2", ["AU_CUT"])
    project = SimpleNamespace(engine=engine, uid="u", folder=tmp_path)
    with dialect.read_tx(engine) as conn:
        snap = snapshot.build_rows(conn, set_id, tid, ("u", set_id, str(tid), "t", 2), None)
    by_id = dict(zip(snap.ids.tolist(), snap.data[f"c{cols['AU_CUT'].id}"].tolist(), strict=True))
    assert [by_id[i] for i in lines.row_ids.tolist()][:2] == [0.0, 2.0] and np.isnan(by_id[lines.row_ids[4]])
    cls_of = dict(zip(snap.ids.tolist(), snap.data[f"c{cols['ORE_CLS'].id}"].tolist(), strict=True))
    assert cls_of[lines.row_ids[1]] == "руда" and cls_of[lines.row_ids[0]] is None
    au_rows = [np.nan if r[3] is None else r[3] for r in ROWS]  # the other slots of the vectors are untouched
    assert np.array_equal(snap.data[f"c{cols['AU'].id}"], au_rows, equal_nan=True)

    second = run(0.5)  # a repeat: the same columns and slots, the values of the second run, one record
    again = columns(engine, tid)
    assert {k: (c.id, c.slot) for k, c in again.items()} == {k: (c.id, c.slot) for k, c in cols.items()}
    with dialect.read_tx(engine) as conn:
        snap = snapshot.build_rows(conn, set_id, tid, ("u", set_id, str(tid), "t", 3), None)
        tops = conn.execute(sa.select(dh_results.c.id).where(dh_results.c.op == "topcut")).scalars().all()
        listed = {r["id"]: r for r in results.list_results(conn, set_id)}
    by_id = dict(zip(snap.ids.tolist(), snap.data[f"c{cols['AU_CUT'].id}"].tolist(), strict=True))
    assert by_id[lines.row_ids[1]] == 1.0 and tops == [second["result_id"]]
    assert listed[keeps]["stale"] is False and listed[second["result_id"]]["stale"] is False
    assert listed[later]["stale"] is True  # it read AU_CUT, which was written again
    assert listed[second["result_id"]]["written"] == [{"table": "пробы", "columns": ["AU_CUT", "ORE_CLS"]}]

    with dialect.read_tx(engine) as conn:
        group = conn.execute(
            sa.select(edit_log).where(edit_log.c.action == "column").order_by(edit_log.c.id.desc())
        ).first()
    assert group is not None
    actions.undo(project, int(group.id))  # «Отменить» in the journal hides the columns of the last run
    hidden = columns(engine, tid)
    assert hidden["AU_CUT"].deleted and hidden["ORE_CLS"].deleted and not hidden["AU"].deleted
    third = run(1.0)  # back again: the hidden columns and their slots are reused
    assert {k: (c.id, c.slot, c.deleted) for k, c in columns(engine, tid).items() if k in ("AU_CUT", "ORE_CLS")} == {
        k: (cols[k].id, cols[k].slot, False) for k in ("AU_CUT", "ORE_CLS")
    }
    assert third["changed"][0]["rev"] == 5


def test_text_column_of_another_kind_is_refused(tmp_path: Path) -> None:
    engine, set_id, tid = setup("sqlite", tmp_path)
    lines = source.read(engine, set_id, tid, ["AU"])
    out = sink.ColumnsOut(tid, lines.row_ids, [(dh.ColumnSpec("LITH", "number"), lines.numbers["AU"])])
    with pytest.raises(ApiError) as err:
        sink.commit(engine, commit_of(engine, lines, op="equivalent", columns=[out]))
    assert err.value.code == "column_exists"
    assert columns(engine, tid)["LITH"].kind == "text"


def test_result_table_snapshot_opens_without_the_database(db_url: str, tmp_path: Path) -> None:
    engine, set_id, tid = setup(db_url, tmp_path)
    lines = source.read(engine, set_id, tid, ["AU"])
    out = out_table(lines)
    with sink.guarded(engine, JOB):
        built = sink.build_table(engine, set_id, out, job_id=JOB)
        done = sink.commit(engine, commit_of(engine, lines, table=built))
    cache = tmp_path / "cache" / "tables"
    path = sink.write_snapshot(engine, cache, done["table_id"], out)
    with dialect.read_tx(engine) as conn:
        key = snapshot.table_key(conn, "u", set_id, str(done["table_id"]))
        assert key is not None and path.name == f"{done['table_id']}-{key[-2]}-{key[-1]}.parquet"
        fast = snapshot.build_rows(conn, set_id, done["table_id"], key, cache)
        slow = snapshot.build_rows(conn, set_id, done["table_id"], key, None)
    assert path.is_file()  # a damaged file would have been removed by build_rows
    assert np.array_equal(fast.ids, slow.ids) and np.array_equal(fast.hole_ids, slow.hole_ids)
    for k in slow.data:
        assert np.array_equal(fast.data[k], slow.data[k], equal_nan=slow.data[k].dtype.kind == "f"), k
    warm = source.read(engine, set_id, done["table_id"], ["AU", "LITH"], cache_dir=cache)
    assert warm.source == "parquet" and warm.labels["LITH"] == ["a", "b", "c"]
