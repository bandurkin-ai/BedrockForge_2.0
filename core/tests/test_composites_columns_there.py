"""Columns written in place by statements of the database (`sink._set_in_place`, `dialect.set_slots`; подшаг 12.33
ч. 2, Т8.2-19: ORE_ID and ORE_CLS of 5 million samples) against a model of the table kept here: after every
write the vector `num` of every row is, byte for byte, the model's row cut after its last value (NULL — no value)
and `txt` is the model's {slot: text} (NULL — none) — the same for the statements and for the pass of
`write_columns` (the other databases, the rows the statements do not take).

Hard rows: without FROM (not listed by the reading: they get the columns empty), without a number at all (`num`
NULL), with the last numbers missing (short vectors), texts with quotes, a backslash and Cyrillic, empty strings
and None; values that become empty on a repeat (the vector gets shorter); a column in the middle of the vector
written over; rows deleted in between (runs of ids), a row listed twice; too many runs, too many labels and
labels that are not strings — the pass."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.composites import sink, source
from bf_core.db import dialect, migrate
from bf_core.db.schema import dh_columns, dh_rows
from bf_core.drillholes import store as dh
from bf_core.drillholes.store_rows import encode_vectors
from tests.dbutil import make_engine

pytestmark = pytest.mark.db
N = 2400
TEXTS = ["ore", "waste", 'с "кавычками"', "back\\slash", "", None, None]


def noop(_p: float | None, _m: str | None) -> None:
    pass


def table(engine: sa.Engine, seed: int = 5) -> tuple[int, int]:
    rng = np.random.default_rng(seed)
    frm = rng.uniform(0.0, 100.0, N).round(2)
    frm[::97] = np.nan  # no FROM: the reading leaves the row out, the columns come empty there
    au = rng.lognormal(0.0, 1.0, N).round(3)
    au[rng.random(N) < 0.15] = np.nan
    cu = rng.random(N).round(3)
    cu[rng.random(N) < 0.4] = np.nan  # the last number of the row: the vector is short
    lith = np.array(["гранит", 'кварц "Q"', "a\\b", None], dtype=object)[rng.integers(0, 4, N)]
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
    data = dh.TableData(
        "Пробы", "assay", [f"H-{k:03d}" for k in rng.integers(0, 40, N)], frm, frm + 1.0,
        [dh.ColumnSpec("AU", "number", "g/t"), dh.ColumnSpec("CU", "number", "%"), dh.ColumnSpec("LITH", "text")],
        [au, cu, lith],
    )  # fmt: skip
    return set_id, int(dh.write_table(engine, set_id, data, job_id=None, tick=noop)["table_id"])


def stored(engine: sa.Engine, tid: int) -> dict[int, tuple[bytes | None, dict[str, str] | None]]:
    with dialect.read_tx(engine) as conn:
        q = sa.select(dh_rows.c.id, dh_rows.c.num, dh_rows.c.txt).where(dh_rows.c.table_id == tid)
        return {int(r.id): (None if r.num is None else bytes(r.num), r.txt or None) for r in conn.execute(q)}


def slots(engine: sa.Engine, tid: int) -> dict[str, tuple[str, int]]:
    with dialect.read_tx(engine) as conn:
        q = sa.select(dh_columns.c.name, dh_columns.c.kind, dh_columns.c.slot).where(dh_columns.c.table_id == tid)
        return {r.name: (r.kind, int(r.slot)) for r in conn.execute(q)}


class Model:
    """The table as it must be: the numbers by slot and the texts by slot of every row id."""

    def __init__(self, engine: sa.Engine, tid: int) -> None:
        self.tid = tid
        self.num: dict[int, list[float]] = {}
        self.txt: dict[int, dict[str, str]] = {}
        for rid, (num, txt) in stored(engine, tid).items():
            self.num[rid] = [] if num is None else np.frombuffer(num, dtype="<f8").tolist()
            self.txt[rid] = dict(txt or {})

    def write(self, engine: sa.Engine, ids: Any, columns: list[tuple[dh.ColumnSpec, np.ndarray]]) -> None:
        """What `write_columns` must do: the listed rows take their (first) values, the others — empty."""
        where = slots(engine, self.tid)
        first: dict[int, int] = {}
        for k, rid in enumerate(np.asarray(ids).tolist()):
            first.setdefault(rid, k)
        for spec, values in columns:
            kind, slot = where[spec.name]
            assert kind == spec.kind
            for rid in self.num:
                v = values[first[rid]] if rid in first else None
                if kind == "number":
                    row = self.num[rid]
                    row += [np.nan] * (slot + 1 - len(row))
                    row[slot] = np.nan if v is None else float(v)
                elif v is None or v == "" or v != v:
                    self.txt[rid].pop(str(slot), None)
                else:
                    self.txt[rid][str(slot)] = str(v)

    def check(self, engine: sa.Engine) -> None:
        got = stored(engine, self.tid)
        assert got.keys() == self.num.keys()
        width = max(len(row) for row in self.num.values())
        ids = sorted(self.num)
        matrix = np.array([self.num[i] + [np.nan] * (width - len(self.num[i])) for i in ids], dtype="<f8")
        want = encode_vectors(matrix)  # cut after the last value of the row; None — no value at all
        bad = [i for i, w in zip(ids, want, strict=True) if got[i][0] != w]
        assert not bad, (len(bad), bad[:5], got[bad[0]][0], want[ids.index(bad[0])])
        bad = [i for i in ids if got[i][1] != (self.txt[i] or None)]
        assert not bad, (len(bad), bad[:5], got[bad[0]][1], self.txt[bad[0]])


def write(engine: sa.Engine, set_id: int, tid: int, ids: Any, columns: list[tuple[dh.ColumnSpec, Any]]) -> int:
    with dialect.write_tx(engine) as conn:
        return int(sink.write_columns(conn, set_id, sink.ColumnsOut(tid, np.asarray(ids), columns))["rows"])


def values(rng: np.random.Generator, n: int, empty: float) -> np.ndarray:
    v = rng.lognormal(0.0, 1.0, n).round(4)
    v[rng.random(n) < empty] = np.nan
    return v


@pytest.mark.parametrize("there", [True, False])
def test_statements_and_the_pass_leave_the_same_rows(  # type: ignore[no-untyped-def]
    db_url: str, tmp_path: Path, monkeypatch, there: bool
) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    set_id, tid = table(engine)
    taken: list[bool] = []
    real = dialect.set_slots

    def spy(*args: Any, **kw: Any) -> int | None:
        done = real(*args, **kw) if there else None
        taken.append(done is not None)
        return done

    monkeypatch.setattr(dialect, "set_slots", spy)
    sqlite = db_url == "sqlite" and there
    model = Model(engine, tid)
    rng = np.random.default_rng(11)
    ids = source.read(engine, set_id, tid, ["AU"]).row_ids  # in line order, without the rows that have no FROM
    n = len(ids)
    assert 0 < N - n < 40 and not np.array_equal(ids, np.sort(ids))
    text = np.array(TEXTS, dtype=object)

    def step(ids: Any, columns: list[tuple[dh.ColumnSpec, Any]], expect: bool = True) -> None:
        taken.clear()
        rows = write(engine, set_id, tid, ids, columns)
        model.write(engine, ids, columns)
        model.check(engine)
        assert (taken == [True]) is (sqlite and expect) and (rows == len(model.num) or taken != [True]), taken

    n1, n2, t1 = dh.ColumnSpec("N1", "number"), dh.ColumnSpec("N2", "number"), dh.ColumnSpec("T1", "text")
    # new columns after the last slot: values, many empty ones, every text of `TEXTS`
    step(ids, [(n1, values(rng, n, 0.5)), (n2, values(rng, n, 0.8)), (t1, text[rng.integers(0, len(text), n)])])
    assert slots(engine, tid)["N2"] == ("number", 3) and slots(engine, tid)["T1"] == ("text", 1)
    # a repeat: other values in the same slots; N2 goes empty everywhere — the vectors get shorter
    step(ids, [(n2, np.full(n, np.nan)), (t1, text[rng.integers(0, len(text), n)]), (n1, values(rng, n, 0.9))])
    # a column in the middle of the vector and the first text column written over, the rest stays
    step(ids, [(dh.ColumnSpec("CU", "number", "%"), values(rng, n, 0.3)),
               (dh.ColumnSpec("LITH", "text"), text[rng.integers(0, len(text), n)])])  # fmt: skip
    # numbers only, texts only
    step(ids, [(n2, values(rng, n, 0.2))])
    step(ids, [(t1, np.where(rng.random(n) < 0.5, "ore", None).astype(object))])
    # rows deleted in between: runs of ids; some rows listed twice — the first value counts
    gone = np.sort(ids)[100:1900:60].tolist()
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.delete(dh_rows).where(dh_rows.c.id.in_(gone)))
    for rid in gone:
        del model.num[rid], model.txt[rid]
    left = np.array([i for i in ids.tolist() if i not in set(gone)], dtype=np.int64)
    twice = np.concatenate([left, left[::7]])
    step(twice, [(n1, values(rng, len(twice), 0.3)), (t1, text[rng.integers(0, len(text), len(twice))])])
    # nothing listed: the columns go empty in every row
    step(np.zeros(0, dtype=np.int64), [(n1, np.zeros(0)), (t1, np.zeros(0, dtype=object))])
    # what the statements do not take: labels that are not strings, too many of them, too many runs
    step(left, [(t1, rng.integers(0, 3, len(left)).astype(object))], expect=False)
    many = np.array([f"L{k}" for k in rng.integers(0, dialect.SLOT_LABELS + 1, len(left))], dtype=object)
    step(left, [(t1, many)], expect=False)
    step(left, [(t1, many), (n1, values(rng, len(left), 0.1))], expect=False)
    assert len(set(many.tolist())) == dialect.SLOT_LABELS + 1
    few = np.array([f"L{k}" for k in rng.integers(0, dialect.SLOT_LABELS, len(left))], dtype=object)
    step(left, [(t1, few)])
    step(left[::3], [(n2, values(rng, len(left[::3]), 0.5))], expect=False)  # hundreds of runs
    engine.dispose()


def test_set_slots_refuses_what_it_cannot_write(tmp_path: Path) -> None:
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    _set_id, tid = table(engine)
    before = stored(engine, tid)
    scope = ("table_id", tid)
    with dialect.write_tx(engine) as conn:
        runs = [(k, 1) for k in range(1, 2 * dialect.SLOT_RUNS + 3, 2)]
        assert dialect.set_slots(conn, dh_rows, scope, runs, [(0, np.zeros(len(runs)))], []) is None
        labels = [str(k) for k in range(dialect.SLOT_LABELS + 1)]
        assert dialect.set_slots(conn, dh_rows, scope, [(1, 2)], [], [("1", np.zeros(2, np.uint8), labels)]) is None
        with pytest.raises(ValueError):  # a key that is not a slot is never put into a statement
            dialect.set_slots(conn, dh_rows, scope, [(1, 2)], [], [("1'", np.zeros(2, np.uint8), ["a"])])
    assert stored(engine, tid) == before
    engine.dispose()


def raw_rows(engine: sa.Engine, tid: int) -> dict[int, tuple[bytes | None, str | None]]:
    """`num` and `txt` of every row as the database keeps them: the bytes of the vector, the text of the JSON."""
    with dialect.read_tx(engine) as conn:
        sql = "SELECT id, num, txt FROM dh_rows WHERE table_id = ?"
        return {int(r[0]): (None if r[1] is None else bytes(r[1]), r[2])
                for part in dialect.iter_driver_rows(conn, sql, (tid,)) for r in part}  # fmt: skip


def test_lean_statements_write_the_bytes_of_the_general_ones(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """One number and one text column (ORE_ID and ORE_CLS of Т8.2-19, подшаг 12.33в) go by the statements with
    few function calls: `txt` is, byte for byte, what the writer of the table makes ({"0": "…", "1": "…"} —
    `json.dumps(…, ensure_ascii=False)`), and both columns are what the statements for many columns leave.
    Hard rows: `txt` that does not end with a string (a number, a nested object), an empty object, the key of
    the column inside a text; a vector longer than the slot, NULL and short ones; texts with quotes, «\\»,
    Cyrillic; a repeat (the key is there, values go empty)."""
    import json

    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    set_id, tid = table(engine)
    ids = np.sort(source.read(engine, set_id, tid, ["AU"]).row_ids)
    odd = {int(ids[3]): '{"0": 5}', int(ids[4]): '{"0": {"a": "}"}}', int(ids[5]): "{}",
           int(ids[6]): '{"0": "x \\"1\\": y"}', int(ids[7]): '{"0":"tight"}'}  # fmt: skip
    with dialect.write_tx(engine) as conn:
        for rid, txt in odd.items():
            conn.exec_driver_sql("UPDATE dh_rows SET txt = ? WHERE id = ?", (txt, rid))
    rng = np.random.default_rng(3)
    n = len(ids)
    text = np.array(TEXTS, dtype=object)
    n1, t1 = dh.ColumnSpec("N1", "number"), dh.ColumnSpec("T1", "text")
    first = [(n1, values(rng, n, 0.5)), (t1, text[rng.integers(0, len(text), n)])]
    again = [(n1, values(rng, n, 0.7)), (t1, text[rng.integers(0, len(text), n)])]
    before = raw_rows(engine, tid)

    def run(lean: bool) -> list[dict[int, tuple[bytes | None, str | None]]]:
        """The two writes with the lean statements, or with those for many columns (a second text column
        that is empty everywhere and a second number column make them so; they are taken away after)."""
        with dialect.write_tx(engine) as conn:
            conn.execute(sa.delete(dh_columns).where(dh_columns.c.table_id == tid, dh_columns.c.name.in_(["N1", "T1"])))
            for rid, (num, txt) in before.items():
                conn.exec_driver_sql("UPDATE dh_rows SET num = ?, txt = ? WHERE id = ?", (num, txt, rid))
        assert raw_rows(engine, tid) == before
        real = dialect.set_slots
        seen: list[tuple[int, int]] = []

        def spy(conn: Any, tab: Any, scope: Any, runs: Any, numbers: Any, texts: Any) -> int | None:
            seen.append((len(numbers), len(texts)))
            if lean:
                return real(conn, tab, scope, runs, numbers, texts)
            (slot, vals), (key, codes, labels) = numbers[0], texts[0]
            more_n = [(slot, vals), (slot + 1, np.full(len(vals), np.nan))]
            more_t = [(key, codes, labels), (str(int(key) + 1), np.full(len(codes), 255, np.uint8), [])]
            return real(conn, tab, scope, runs, more_n, more_t)

        monkeypatch.setattr(dialect, "set_slots", spy)
        out = []
        for columns in (first, again):
            assert write(engine, set_id, tid, ids, columns) == len(before)
            out.append(raw_rows(engine, tid))
        monkeypatch.setattr(dialect, "set_slots", real)
        assert seen == [(1, 1), (1, 1)]
        return out

    lean, general = run(True), run(False)
    slot = str(slots(engine, tid)["T1"][1])
    at = {int(i): k for k, i in enumerate(ids.tolist())}
    for step in (0, 1):
        diff = [i for i in before if lean[step][i][0] != general[step][i][0]]
        assert not diff, (step, diff[:5])  # the vectors: the same bytes
        for rid, (_num, txt) in lean[step].items():  # the texts: the same JSON; the bytes — see below
            other = general[step][rid][1]
            assert (txt is None) == (other is None) and (txt is None or json.loads(txt) == json.loads(other)), rid
    for rid, (_num, old) in before.items():  # the first write: the key is new — the text as the writer makes it
        v = first[1][1][at[rid]] if rid in at else None
        got = lean[0][rid][1]
        if v is None or v == "":
            assert got == old, (rid, got, old)
        elif old is None:
            assert got == json.dumps({slot: v}, ensure_ascii=False), (rid, got)
        elif rid in (int(ids[5]), int(ids[6])):  # {} and a text with the key in it: through `json_patch`
            assert json.loads(got) == {**json.loads(old), slot: v}, (rid, got)
        else:  # the pair is added at the end, whatever the row ends with
            assert got == old[:-1] + ", " + json.dumps({slot: v}, ensure_ascii=False)[1:], (rid, got, old)
    engine.dispose()


def test_fold_later_commits_without_folding_and_folds_at_its_end(tmp_path: Path) -> None:
    """`dialect.fold_later` + `write_tx(fold=…)` (подшаг 12.33в): the commit of a big transaction leaves its
    pages in the WAL (nothing is folded inside it), other connections see the rows at once, and the WAL is
    folded into the file when the block ends; a failed transaction leaves nothing; without `fold` — as ever."""
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    set_id, tid = table(engine)
    db = tmp_path / "project.sqlite"
    wal = tmp_path / "project.sqlite-wal"
    dialect.checkpoint(engine)
    size = db.stat().st_size
    blob = b"\x01" * 3000  # every row of the table gets a vector of 3000 bytes: thousands of pages

    def grow(conn: sa.Connection) -> None:
        conn.exec_driver_sql("UPDATE dh_rows SET num = ? WHERE table_id = ?", (blob, tid))

    def count(value: bytes) -> int:
        with dialect.read_tx(engine) as conn:
            return int(conn.exec_driver_sql("SELECT count(*) FROM dh_rows WHERE num = ?", (value,)).scalar_one())

    with dialect.fold_later(engine) as fold:
        assert fold is not None
        with dialect.write_tx(engine, fold=fold) as conn:
            grow(conn)
        assert count(blob) == N  # committed and seen …
        assert wal.stat().st_size > N * len(blob)  # … and in the WAL: the commit did not fold it
        with pytest.raises(RuntimeError), dialect.write_tx(engine, fold=fold) as conn:
            conn.exec_driver_sql("UPDATE dh_rows SET num = NULL WHERE table_id = ?", (tid,))
            raise RuntimeError
        assert count(blob) == N
    assert db.stat().st_size > size + N * len(blob)  # folded into the file when the block ended
    with dialect.read_tx(engine) as conn:
        assert conn.exec_driver_sql("PRAGMA wal_autocheckpoint").scalar_one() == 1000  # other connections: as ever
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    assert dialect.quick_check(engine) == "ok"
    engine.dispose()
