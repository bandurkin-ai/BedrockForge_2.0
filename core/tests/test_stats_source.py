"""The data of charts (ADR 0019 §3, подшаг 13.14): fields of a table, a missing value stays NaN, `length`, the field
of another table by the sample key (unmatched counted), the domain of another table by the middle of a sample,
the middles on the hole paths, the cache of columns (an edit raises `rev` — a miss; the limit evicts the oldest),
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
    # the limit: the lines and two columns of 5 rows fit, the third pushes the oldest out
    small = source.ColumnCache(limit=5 * 8 * 5 + 2 * 5 * 8 + 300)
    source.read(project, set_id, "Пробы", ["AU"], cache=small)
    source.read(project, set_id, "Пробы", ["length"], cache=small)
    source.read(project, set_id, "Пробы", ["ROCK"], cache=small)
    names = [k[4] for k in small.keys()]
    assert "ROCK" in names and len(names) < 4 and small.size <= small.limit
    got = source.read(project, set_id, "Пробы", ["AU", "ROCK", "length"], cache=small)  # evicted — read again
    assert got.number("AU")[1] == 4.25
    tiny = source.ColumnCache(limit=10)  # nothing fits: read every time, kept nowhere
    assert source.read(project, set_id, "Пробы", ["AU"], cache=tiny).number("AU")[1] == 4.25 and tiny.size == 0


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
