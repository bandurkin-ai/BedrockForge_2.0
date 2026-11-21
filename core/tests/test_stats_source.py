"""The data of charts (ADR 0019 §3, подшаг 13.14): fields of a table, a missing value stays NaN, `length`, the field
of another table by the sample key (unmatched counted), the domain of another table by the middle of a sample,
the middles on the hole paths, the cache of columns (an edit raises `rev` — a miss; the limit evicts the oldest;
13.35: a request reads no column twice, the rows of another table are searched hole by hole; 13.36: no lock
stays after its request, lines of other rows at the same revision drop the columns kept),
`GET /stats/fields`, nothing heavy imported with the router."""

from __future__ import annotations

import math
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect, migrate
from bf_core.db.schema import dh_rows, dh_tables
from bf_core.drillholes import store as dh
from bf_core.stats import source
from tests.dbutil import make_engine
from tests.test_import_job import API

SAMPLES = [  # hole, from, to, AU, ROCK
    ("C-2", 0.0, 1.0, 1.5, "гранит"),
    ("C-2", 1.0, 2.5, None, "диорит"),
    ("A-1", 0.0, 2.0, 0.0, None),
    ("A-1", 2.0, 3.0, 4.25, "гранит"),
    ("A-1", None, 4.0, 9.0, "x"),  # no FROM: left out, counted
    ("B-7", 5.0, 6.0, 0.7, "диорит"),
]
LITHO = [  # hole, from, to, CU, ZONE — the key of A-1 0–2 and C-2 0–1, 1–2.5; B-7 is not here
    ("A-1", 0.0, 2.0000000001, 0.3, "окисл"),
    ("A-1", 2.0, 2.5, 0.4, "перех"),
    ("C-2", 0.0, 1.0, None, "перв"),
    ("C-2", 1.0, 2.5, 0.2, "окисл"),
    ("Z-9", 0.0, 1.0, 9.9, "перв"),
]


def noop(_p: float | None, _m: str | None) -> None:
    pass


def table(name: str, kind: str, rows: list[Any], num: str, txt: str) -> dh.TableData:
    return dh.TableData(
        name=name,
        kind=kind,
        holes=[r[0] for r in rows],
        from_m=[r[1] for r in rows],
        to_m=[r[2] for r in rows],
        columns=[dh.ColumnSpec(num, "number", "g/t", "component"), dh.ColumnSpec(txt, "text")],
        values=[[r[3] for r in rows], [r[4] for r in rows]],
    )


def make(db_url: str, tmp_path: Path) -> tuple[Any, int, int, int]:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        dh.replace_collars(
            conn, set_id, [dh.CollarRow(h, x=100.0 * k, y=50.0, z=500.0, td=10.0, azimuth=0.0, dip=-90.0)
                           for k, h in enumerate(("A-1", "B-7"))],
        )  # fmt: skip
    a = dh.write_table(engine, set_id, table("Пробы", "assay", SAMPLES, "AU", "ROCK"), job_id=None, tick=noop)
    b = dh.write_table(engine, set_id, table("Литология", "interval", LITHO, "CU", "ZONE"), job_id=None, tick=noop)
    project = SimpleNamespace(engine=engine, uid=f"test:{tmp_path}", folder=tmp_path)
    return project, set_id, int(a["table_id"]), int(b["table_id"])


@pytest.fixture()
def data(tmp_path: Path):  # type: ignore[no-untyped-def]
    project, set_id, a, b = make("sqlite", tmp_path)
    yield project, set_id, a, b
    project.engine.dispose()


def test_fields_missing_stays_nan_and_length(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id, a, _ = data
    cache = source.ColumnCache()
    cols = source.read(project, set_id, "пробы", ["au", "Length", "ROCK"], cache=cache)
    assert cols.table.id == a and cols.table.kind == "assay"
    assert cols.holes == ["A-1", "B-7", "C-2"] and cols.skipped == 1 and cols.n == 5
    au = cols.number("AU")
    assert au.dtype == np.float64 and au[:4].tolist() == [0.0, 4.25, 0.7, 1.5] and math.isnan(au[4])
    assert cols.number("length").tolist() == [2.0, 1.0, 1.0, 1.0, 1.5]
    codes, labels = cols.text("rock")
    assert labels == ["гранит", "диорит"] and codes.tolist() == [-1, 0, 1, 0, 1]
    assert cols.hole_of().tolist() == [0, 0, 1, 2, 2] and len(set(cols.row_ids.tolist())) == 5
    assert not au.flags.writeable  # shared with the cache
    with pytest.raises(ApiError) as e:
        source.read(project, set_id, "пробы", ["PB"], cache=cache)
    assert e.value.status == 422 and e.value.code == "field_not_found"
    with pytest.raises(ApiError) as e:
        source.read(project, set_id, "керн", ["AU"], cache=cache)
    assert e.value.status == 404 and e.value.code == "table_not_found"
    with pytest.raises(ApiError) as e:
        cols.number("ROCK")
    assert e.value.code == "field_not_number"
    args = cols.select_args("AU", "length")  # what `stats.select.select_sample` takes (13.1)
    assert args["values"] is au and args["length"].tolist() == args["weight_values"].tolist()  # type: ignore[union-attr]
    assert source.read(project, set_id, "пробы", ["AU"], cache=cache).select_args("AU")["weight_values"] is None
    # no table named: the assay table; by id too
    assert source.read(project, set_id, None, ["AU"], cache=cache).table.id == a
    assert source.read(project, set_id, a, ["AU"], cache=cache).table.id == a


def test_second_field_by_the_sample_key(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = data
    cols = source.read(project, set_id, "Пробы", ["AU"])
    other = source.read(project, set_id, "Литология", ["CU"])
    p = source.paired(cols, other, "CU")
    # A-1 0–2 (TO equal within 1e−6 m), A-1 2–3 (no such key), B-7 (no hole), C-2 0–1 (CU missing), C-2 1–2.5
    assert np.isnan(p.values[[1, 2, 3]]).all() and p.values[0] == 0.3 and p.values[4] == 0.2
    assert p.unmatched == 2 and p.rows[1] == -1 and p.rows[2] == -1 and p.rows[3] >= 0
    same = source.paired(cols, cols, "AU")
    assert same.unmatched == 0 and same.values is cols.number("AU")


def test_domain_of_another_table_by_the_middle(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = data
    cols = source.read(project, set_id, "Пробы", ["AU", "ROCK"])
    dom = source.domain_at(cols, source.read(project, set_id, "Литология", ["ZONE"]), "ZONE")
    # middles: A-1 1.0 → окисл, A-1 2.5 → the interval 2–2.5 ends there: none; B-7 — no hole; C-2 0.5, 1.75
    assert [dom.labels[c] if c >= 0 else None for c in dom.codes.tolist()] == ["окисл", None, None, "перв", "окисл"]
    assert dom.outside == 2 and dom.codes.dtype == np.int32
    own = source.domain_at(cols, cols, "ROCK")
    assert own.outside == 0 and own.labels == ["гранит", "диорит"]


def _last_before(ka: list[np.ndarray], kb: list[np.ndarray]) -> np.ndarray:
    """The rule of 13.14, kept as the reference of the kernels of 13.35: for every query a — the position in b
    of the last b that sorts not after it by the keys (hole first), b before a on ties; −1 — none."""
    na, nb = ka[0].shape[0], kb[0].shape[0]
    keys = [np.concatenate((x, y)) for x, y in zip(kb, ka, strict=True)]
    is_a = np.concatenate((np.zeros(nb, dtype=np.int8), np.ones(na, dtype=np.int8)))
    order = np.lexsort((is_a, *reversed(keys)))
    last = np.maximum.accumulate(np.where(order < nb, order, -1))
    out = np.empty(na, dtype=np.int64)
    at_a = order >= nb
    out[order[at_a] - nb] = last[at_a]
    return out


def _lines_of(rng: np.random.Generator, names: list[str], table_id: int, rows: int) -> source.Columns:
    """A table as `source.read` gives it: holes sorted by name, rows of a hole by (FROM, TO), depths on a grid
    of 0.5 m with repeats and overlaps, a number and a text column."""
    names = sorted(names)
    per = rng.integers(0, rows, len(names))
    hole = np.repeat(np.arange(len(names)), per)
    frm = rng.integers(0, 12, len(hole)) * 0.5
    to = frm + rng.integers(1, 5, len(hole)) * 0.5 + rng.choice([0.0, 1e-9, 4e-7], len(hole))
    order = np.lexsort((to, frm, hole))
    frm, to = frm[order], to[order]
    t = source.Table(1, table_id, f"t{table_id}", f"t{table_id}", "interval", "tok", 1, len(hole))
    starts = np.concatenate(([0], np.cumsum(per))).astype(np.int64)
    return source.Columns(
        t, np.arange(len(hole), dtype=np.int64) + 100 * table_id, names, np.arange(len(names), dtype=np.int64),
        starts, frm, to, 0, {"V": rng.random(len(hole))}, {"Z": rng.integers(-1, 3, len(hole)).astype(np.int32)},
        {"Z": ["a", "b", "c"]},
    )  # fmt: skip


def test_another_table_as_the_sort_of_both_gave(tmp_path: Path) -> None:
    """13.35: `paired` and `domain_at` search the rows of a hole instead of sorting the two tables together —
    the answers are those of the sort (ties, overlaps, repeated keys, holes of one table only, empty tables)."""
    rng = np.random.default_rng(1335)
    holes = [f"H{k}" for k in range(9)]
    for case in range(60):
        a = _lines_of(rng, list(rng.choice(holes, int(rng.integers(0, 8)), replace=False)), 1, 9)
        b = _lines_of(rng, list(rng.choice(holes, int(rng.integers(0, 8)), replace=False)), 2, 7)
        rank = {name: i for i, name in enumerate(a.holes)}
        hb = np.array([rank.get(name, -1) for name in b.holes], dtype=np.int64)[b.hole_of().astype(np.int64)]
        keep = np.flatnonzero(hb >= 0)
        ha = a.hole_of().astype(np.int64)
        # the middle of a sample in an interval of the other table
        mid = 0.5 * (a.from_m + a.to_m)
        want = np.full(a.n, -1, dtype=np.int64)
        if a.n and len(keep):
            at = _last_before([ha, mid], [hb[keep], b.from_m[keep]])
            ok = at >= 0
            rows = keep[at[ok]]
            ok[ok] = (hb[rows] == ha[ok]) & (mid[ok] < b.to_m[rows])
            want[ok] = keep[at[ok]]
        dom = source.domain_at(a, b, "Z")
        codes = [int(b.codes["Z"][r]) if r >= 0 else -1 for r in want.tolist()]
        assert dom.codes.tolist() == codes and dom.codes.dtype == np.int32, case
        assert dom.outside == int((want < 0).sum()) and dom.labels == ["a", "b", "c"], case
        # the sample key
        want = np.full(a.n, -1, dtype=np.int64)
        if a.n and len(keep):
            fa, ta, fb, tb = (np.rint(x * 1e6).astype(np.int64) for x in (a.from_m, a.to_m, b.from_m[keep],
                                                                         b.to_m[keep]))  # fmt: skip
            at = _last_before([ha, fa, ta], [hb[keep], fb, tb])
            ok = at >= 0
            ok[ok] = (hb[keep][at[ok]] == ha[ok]) & (fb[at[ok]] == fa[ok]) & (tb[at[ok]] == ta[ok])
            want[ok] = keep[at[ok]]
        got = source.paired(a, b, "V")
        assert got.rows.tolist() == want.tolist() and got.unmatched == int((want < 0).sum()), case
        values = np.array([b.numbers["V"][r] if r >= 0 else np.nan for r in want.tolist()], dtype=np.float64)
        assert np.array_equal(got.values, values, equal_nan=True), case


def test_midpoints_on_the_hole_paths(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = data
    cache = source.ColumnCache()
    cols = source.read(project, set_id, "Пробы", ["AU"], cache=cache)
    xyz = source.midpoints(project, cols, cache=cache)
    assert xyz.shape == (5, 3) and xyz.dtype == np.float64
    np.testing.assert_allclose(xyz[0], [0.0, 50.0, 499.0], atol=1e-9)  # A-1 vertical, 0–2: the middle 1 m down
    np.testing.assert_allclose(xyz[2], [100.0, 50.0, 494.5], atol=1e-9)  # B-7 5–6
    assert np.isnan(xyz[3:]).all()  # C-2 has no collar: NaN, never 0
    assert source.midpoints(project, cols, cache=cache) is xyz  # from the cache


def _edit(project: Any, table_id: int) -> None:
    """A row changed as the «Таблица» panel does it: the value, then `rev` + 1."""
    with dialect.write_tx(project.engine) as conn:
        rid = conn.execute(sa.select(sa.func.min(dh_rows.c.id)).where(dh_rows.c.table_id == table_id)).scalar_one()
        conn.execute(sa.update(dh_rows).where(dh_rows.c.id == rid).values(num=dh.pack([7.5])))
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == table_id).values(rev=dh_tables.c.rev + 1))


def test_cache_hit_edit_misses_and_the_limit_evicts(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id, a, _ = data
    cache = source.ColumnCache()
    first = source.read(project, set_id, "Пробы", ["AU"], cache=cache)
    misses = cache.misses
    again = source.read(project, set_id, "Пробы", ["AU"], cache=cache)
    assert again.number("AU") is first.number("AU") and cache.misses == misses and cache.hits > 0
    keys = cache.keys()
    assert all(k[1] == a for k in keys) and {k[4] for k in keys} == {"\0lines", "AU"}
    _edit(project, a)
    after = source.read(project, set_id, "Пробы", ["AU"], cache=cache)
    assert after.table.rev == first.table.rev + 1 and 7.5 in after.number("AU").tolist()
    assert all(k[3] == after.table.rev for k in cache.keys())  # the old revision is gone
    # another project: the columns of this one go
    other = SimpleNamespace(engine=project.engine, uid="другой", folder=project.folder)
    source.read(other, set_id, "Пробы", ["AU"], cache=cache)
    assert {k[0] for k in cache.keys()} == {"другой"}
    # the limit: the lines and one column of 5 rows fit, the second pushes the oldest out; `length` is not kept
    small = source.ColumnCache(limit=5 * 8 * 5 + 5 * 8 + 300)
    source.read(project, set_id, "Пробы", ["AU"], cache=small)
    assert source.read(project, set_id, "Пробы", ["length"], cache=small).number("length")[1] == 1.0
    assert {k[4] for k in small.keys()} == {"\0lines", "AU"}
    source.read(project, set_id, "Пробы", ["ROCK"], cache=small)
    names = [k[4] for k in small.keys()]
    assert "ROCK" in names and len(names) < 3 and small.size <= small.limit
    got = source.read(project, set_id, "Пробы", ["AU", "ROCK", "length"], cache=small)  # evicted — read again
    assert got.number("AU")[1] == 4.25
    tiny = source.ColumnCache(limit=10)  # nothing fits: read every time, kept nowhere
    assert source.read(project, set_id, "Пробы", ["AU"], cache=tiny).number("AU")[1] == 4.25 and tiny.size == 0


def test_a_request_reads_no_column_twice(data, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """13.35: the columns of a request that do not fit the cache together — those found in it are held while
    the others are read (they push the found ones out), so a column is read once a request, in one pass."""
    project, set_id, _, _ = data
    calls: list[list[str]] = []
    real = source._read_lines

    def counted(p: Any, t: Any, names: list[str]) -> Any:
        calls.append(list(names))
        return real(p, t, names)

    monkeypatch.setattr(source, "_read_lines", counted)
    small = source.ColumnCache(limit=5 * 8 * 5 + 5 * 8 + 300)  # the lines and one column
    first = source.read(project, set_id, "Пробы", ["AU"], cache=small)
    for _ in range(3):
        got = source.read(project, set_id, "Пробы", ["AU", "ROCK", "length"], cache=small)
        assert got.number("AU")[1] == 4.25 and got.text("ROCK")[0].tolist() == [-1, 0, 1, 0, 1]
    assert got.number("AU").tolist()[:4] == first.number("AU").tolist()[:4]
    assert all(len(names) == 1 for names in calls) and len(calls) == 4, calls  # AU; then one of the two a request
    assert source.LIMIT_BYTES == 512 * 1024 * 1024 and source.COLUMNS.limit == source.LIMIT_BYTES


def test_the_locks_of_the_cache_do_not_stay(data) -> None:  # type: ignore[no-untyped-def]
    """13.36: the lock of a table (project, table, revision) and of a value lives while it is held or waited
    for — after the requests, the edits and another project none is left; `clear()` leaves the cache empty."""
    project, set_id, a, _ = data
    cache = source.ColumnCache()
    for _ in range(3):
        cols = source.read(project, set_id, "Пробы", ["AU", "ROCK"], cache=cache)
        source.midpoints(project, cols, cache=cache)
        source.read(project, set_id, "Литология", ["CU"], cache=cache)
        _edit(project, a)  # another revision: another lock of the table
    other = SimpleNamespace(engine=project.engine, uid="другой", folder=project.folder)
    source.read(other, set_id, "Пробы", ["AU"], cache=cache)
    assert cache._gates == {}
    with cache.locked(("p", 1)):
        assert set(cache._gates) == {("p", 1)}
        waiting = threading.Thread(target=lambda: cache.get(("p", 1), lambda: 7))  # the same key: waits
        waiting.start()
        while cache._gates[("p", 1)].users < 2:
            pass
        assert cache.peek(("p", 1)) is None
    waiting.join()
    assert cache._gates == {} and cache.peek(("p", 1)) == 7
    with pytest.raises(RuntimeError):
        cache.get(("p", 2), lambda: (_ for _ in ()).throw(RuntimeError("no value")))
    assert cache._gates == {}  # a build that failed leaves no lock either
    cache.clear()
    assert cache.keys() == [] and cache.size == 0 and cache._gates == {}
    assert cache.forget(("p",)) == 0


def test_lines_of_other_rows_at_the_same_revision_drop_the_columns(data, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """13.36: a table whose rows changed while its revision stayed (it was written between the look at the
    revision and the read) — the lines read for a new field have another number of rows than the columns kept:
    everything kept for that revision goes and the fields of the request are read anew, all of one state."""
    project, set_id, a, _ = data
    cache = source.ColumnCache()
    first = source.read(project, set_id, "Пробы", ["AU"], cache=cache)
    xyz = source.midpoints(project, first, cache=cache)
    assert first.n == 5 and xyz.shape == (5, 3) and len(cache.keys()) == 3
    with dialect.write_tx(project.engine) as conn:  # a row goes, `rev` stays
        rid = conn.execute(sa.select(sa.func.max(dh_rows.c.id)).where(dh_rows.c.table_id == a)).scalar_one()
        conn.execute(sa.delete(dh_rows).where(dh_rows.c.id == rid))
    calls: list[list[str]] = []
    real = source._read_lines

    def counted(p: Any, t: Any, names: list[str]) -> Any:
        calls.append(list(names))
        return real(p, t, names)

    monkeypatch.setattr(source, "_read_lines", counted)
    assert source.read(project, set_id, "Пробы", ["AU"], cache=cache).n == 5 and calls == []  # nothing new asked
    got = source.read(project, set_id, "Пробы", ["AU", "ROCK", "length"], cache=cache)
    assert calls == [["ROCK"], ["AU", "ROCK"]]  # the lines of ROCK are of other rows: both fields anew
    assert got.n == 4 and got.table.rev == first.table.rev
    assert {len(got.number("AU")), len(got.text("ROCK")[0]), len(got.number("length")), len(got.row_ids)} == {4}
    assert got.number("AU") is not first.number("AU") and rid not in got.row_ids.tolist()
    kept = {k[4]: v for k, v in ((k, cache.peek(k)) for k in cache.keys())}
    assert set(kept) == {"\0lines", "AU", "ROCK"}  # the middles of the five rows went with their columns
    assert len(kept["AU"]) == 4 and len(kept["\0lines"][0]) == 4
    assert source.midpoints(project, got, cache=cache).shape == (4, 3)
    again = source.read(project, set_id, "Пробы", ["AU", "ROCK"], cache=cache)
    assert again.number("AU") is got.number("AU") and len(calls) == 2  # from the cache now


def test_concurrent_requests_read_once(data) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = data
    cache = source.ColumnCache()
    calls = []
    real = source._read_lines

    def counted(*args: Any) -> Any:
        calls.append(1)
        return real(*args)

    source._read_lines = counted  # type: ignore[assignment]
    try:
        got: list[Any] = []
        threads = [
            threading.Thread(target=lambda: got.append(source.read(project, set_id, "Пробы", ["AU"], cache=cache)))
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        source._read_lines = real  # type: ignore[assignment]
    assert len(calls) == 1 and len(got) == 8 and all(g.number("AU") is got[0].number("AU") for g in got)


@pytest.mark.db
def test_on_both_databases(db_url: str, tmp_path: Path) -> None:
    project, set_id, _, _ = make(db_url, tmp_path)
    try:
        cache = source.ColumnCache()
        cols = source.read(project, set_id, "Пробы", ["AU", "ROCK", "length"], cache=cache)
        assert cols.n == 5 and math.isnan(cols.number("AU")[4]) and cols.number("length")[4] == 1.5
        assert source.paired(cols, source.read(project, set_id, "Литология", ["CU"], cache=cache), "CU").unmatched == 2
    finally:
        project.engine.dispose()


# --- GET /stats/fields, imports -----------------------------------------------------------------------------


@pytest.fixture()
def stats_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the core starts: its app has the router of `stats` (until `api/modules.py` lists it)."""
    import bf_core.api.app as app_module

    if "stats" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "stats"))


def test_fields_route(stats_router, pclient) -> None:  # type: ignore[no-untyped-def]
    c = pclient
    assert c.post(f"{API}/projects/demo").status_code == 200
    set_id = c.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    r = c.get(f"{API}/stats/fields", params={"set_id": set_id})
    assert r.status_code == 200, r.text
    tabs = r.json()["tables"]
    assert tabs and tabs[0]["kind"] == "assay"
    first = tabs[0]
    assert any(f["name"] == "length" and f["role"] == "length" for f in first["numbers"])
    assert all(t["values"] is None for t in first["texts"])
    r = c.get(f"{API}/stats/fields", params={"set_id": set_id, "table": first["name"]})
    one = r.json()["tables"]
    assert len(one) == 1 and one[0]["id"] == first["id"]
    for t in one[0]["texts"]:
        assert t["values"] == len(t["labels"]) or t["more"]
    r = c.get(f"{API}/stats/fields", params={"set_id": set_id, "table": "нет такой"})
    assert r.status_code == 404 and r.json()["code"] == "table_not_found" and r.json()["hint"]
    assert c.get(f"{API}/stats/fields", params={"set_id": 10**9}).json()["code"] == "set_not_found"


def test_router_imports_nothing_heavy() -> None:
    code = (
        "import sys; import bf_core.stats.router;"
        "heavy=[m for m in ('numba','numpy','pandas','pyarrow','duckdb','scipy') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported with the router: {r.stdout.strip()}"
