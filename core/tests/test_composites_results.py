"""Results of sample processing (ADR 0018 §2, подшаг 12.18): migration 0007, «устарел», cascades, deletion,
revision marks, `.bfggis`, the API."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.composites import results as res
from bf_core.db import dialect, migrate
from bf_core.db.schema import dh_columns, dh_results, dh_rows, dh_tables, edit_log, objects
from bf_core.drillholes import actions
from bf_core.drillholes import store as dh
from bf_core.projects.service import projects_of
from tests.dbutil import make_engine

pytestmark = pytest.mark.db
API = "/api/v1"


def noop(_p: float | None, _m: str | None) -> None:
    pass


def samples(name: str = "Пробы", au: list[float | None] | None = None) -> dh.TableData:
    holes = ["A", "A", "B", "B"]
    return dh.TableData(
        name=name,
        kind="assay",
        holes=holes,
        from_m=[0.0, 1.0, 0.0, 2.0],
        to_m=[1.0, 2.0, 2.0, 3.0],
        columns=[dh.ColumnSpec("AU", "number", "g/t", "component"), dh.ColumnSpec("CU", "number", "%", "component")],
        values=[au or [1.0, None, 0.5, 2.0], [0.1, 0.2, 0.3, 0.4]],
    )


def composites(name: str = "Композиты 2 м") -> dh.TableData:
    return dh.TableData(
        name=name,
        kind="interval",
        holes=["A", "B"],
        from_m=[0.0, 0.0],
        to_m=[2.0, 2.0],
        columns=[dh.ColumnSpec("AU", "number", "g/t", "component")],
        values=[[1.0, 0.5]],
    )


def setup(db_url: str, tmp_path: Path) -> tuple[sa.Engine, int, int]:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
    src = dh.write_table(engine, set_id, samples(), job_id=None, tick=noop)["table_id"]
    return engine, set_id, src


def add_result(
    engine: sa.Engine,
    set_id: int,
    table_id: int | None,
    op: str,
    inputs: list[tuple[int, list[str]]],
    *,
    written: list[dict[str, Any]] | None = None,
    trajectories: bool = False,
) -> int:
    with dialect.write_tx(engine) as conn:
        return res.record(
            conn,
            set_id=set_id,
            table_id=table_id,
            op=op,
            name=res.OP_TITLES[op],
            params={"length": 2.0},
            inputs=res.make_inputs(conn, set_id, inputs, trajectories=trajectories),
            sha256="0" * 64,
            algo="1",
            report={"written": written or []},
        )


def listed(engine: sa.Engine, set_id: int) -> dict[int, dict[str, Any]]:
    with dialect.read_tx(engine) as conn:
        return {r["id"]: r for r in res.list_results(conn, set_id)}


def project_of(engine: sa.Engine, tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(engine=engine, uid="t", folder=tmp_path)


def test_migration_0007_cycle_keeps_e4_tables(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project", "0006")
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
    tid = dh.write_table(engine, set_id, samples(), job_id=None, tick=noop)["table_id"]
    migrate.upgrade(engine, "project")
    rid = add_result(engine, set_id, tid, "topcut", [(tid, ["AU"])])
    assert listed(engine, set_id)[rid]["stale"] is False
    migrate.downgrade(engine, "project", "0006")
    with engine.connect() as conn:
        assert "dh_results" not in sa.inspect(conn).get_table_names()
        assert conn.execute(sa.select(sa.func.count()).select_from(dh_rows)).scalar_one() == 4
    migrate.upgrade(engine, "project")
    assert migrate.state(engine, "project") == "current"
    assert listed(engine, set_id) == {}


def test_edit_replace_and_set_version_make_results_stale(db_url: str, tmp_path: Path) -> None:
    engine, set_id, src = setup(db_url, tmp_path)
    comp = dh.write_table(engine, set_id, composites(), job_id=None, tick=noop)["table_id"]
    rid = add_result(engine, set_id, comp, "length", [(src, ["AU"])])
    xyz = add_result(engine, set_id, None, "conditions", [(src, ["AU"])], trajectories=True)
    got = listed(engine, set_id)
    assert got[rid]["stale"] is False and got[rid]["reason"] is None and got[rid]["table"] == "Композиты 2 м"
    assert got[rid]["makes_table"] and got[rid]["op_title"] == "Композиты по длине"
    assert got[xyz]["table_id"] is None and got[xyz]["inputs"]["set"]["version"] == 1

    with dialect.read_tx(engine) as conn:
        row = conn.execute(sa.select(dh_rows.c.id).where(dh_rows.c.table_id == src).order_by(dh_rows.c.id)).scalar()
        au = conn.execute(sa.select(dh_columns.c.id).where(dh_columns.c.table_id == src, dh_columns.c.name == "AU"))
        au_key = f"c{au.scalar_one()}"
    actions.update_cell(project_of(engine, tmp_path), set_id, str(src), int(row), au_key, 3.5, None)
    got = listed(engine, set_id)
    assert got[rid]["stale"] is True and got[rid]["reason"] == "изменена таблица «Пробы»"

    with dialect.write_tx(engine) as conn:  # an edit of collars or surveys moves the set version
        conn.execute(sa.update(objects).where(objects.c.id == set_id).values(version=objects.c.version + 1))
    with dialect.write_tx(engine) as conn:  # the table marks follow; only the set version differs now
        res.mark_fresh(
            conn, xyz, {**got[xyz]["inputs"], "tables": res.make_inputs(conn, set_id, [(src, ["AU"])])["tables"]}
        )
    assert listed(engine, set_id)[xyz]["reason"] == "изменены устья или инклинометрия набора"

    new_src = dh.write_table(engine, set_id, samples(), job_id=None, tick=noop)["table_id"]  # «Заменить»
    with dialect.write_tx(engine) as conn:
        res.mark_fresh(conn, rid, res.make_inputs(conn, set_id, [(new_src, ["AU"])]))
    assert listed(engine, set_id)[rid]["stale"] is False
    time.sleep(0.002)
    dh.write_table(engine, set_id, samples(au=[9.0, 9.0, 9.0, 9.0]), job_id=None, tick=noop)  # same rev 1, new token
    assert listed(engine, set_id)[rid]["reason"] == "заменена таблица «Пробы»"


def test_deleting_a_table_or_the_set_drops_its_results(db_url: str, tmp_path: Path) -> None:
    engine, set_id, src = setup(db_url, tmp_path)
    comp = dh.write_table(engine, set_id, composites(), job_id=None, tick=noop)["table_id"]
    made = add_result(engine, set_id, comp, "length", [(src, ["AU"])])
    cut = add_result(
        engine, set_id, src, "topcut", [(src, ["AU"])], written=[{"table": "пробы", "columns": ["AU_CUT"]}]
    )
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.delete(dh_tables).where(dh_tables.c.id == comp))
    got = listed(engine, set_id)
    assert made not in got and cut in got
    with dialect.write_tx(engine) as conn:  # the source table: the record of its columns goes too
        conn.execute(sa.delete(dh_tables).where(dh_tables.c.id == src))
    assert listed(engine, set_id) == {}
    src = dh.write_table(engine, set_id, samples(), job_id=None, tick=noop)["table_id"]
    add_result(engine, set_id, None, "conditions", [(src, ["AU"])])
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == set_id))
        assert conn.execute(sa.select(sa.func.count()).select_from(dh_results)).scalar_one() == 0


def _add_column(engine: sa.Engine, table_id: int, name: str) -> int:
    with dialect.write_tx(engine) as conn:
        slot = conn.execute(sa.select(sa.func.max(dh_columns.c.slot)).where(dh_columns.c.table_id == table_id)).scalar()
        r = conn.execute(
            sa.insert(dh_columns).values(
                table_id=table_id, name=name, kind="number", slot=int(slot) + 1, role="value", deleted=False, position=9
            )
        )
        key = r.inserted_primary_key
        assert key is not None
        return int(key[0])


def test_delete_removes_table_or_hides_columns_and_undo_brings_them_back(db_url: str, tmp_path: Path) -> None:
    engine, set_id, src = setup(db_url, tmp_path)
    ore = dh.write_table(engine, set_id, composites("Рудные интервалы"), job_id=None, tick=noop)["table_id"]
    for name in ("ORE_ID", "ORE_CLS", "AU_CUT"):
        _add_column(engine, src, name)
    rid = add_result(
        engine, set_id, ore, "ore", [(src, ["AU"])], written=[{"table": "пробы", "columns": ["ORE_ID", "ORE_CLS"]}]
    )
    other = add_result(engine, set_id, src, "topcut", [(src, ["AU"])])
    reader = add_result(engine, set_id, None, "conditions", [(src, ["AU", "ORE_CLS"])])
    with dialect.write_tx(engine) as conn:
        out = res.delete_result(conn, rid)
    assert out["deleted_tables"] == [ore] and out["changed"] == [{"table": str(src), "rev": 2}]
    with dialect.read_tx(engine) as conn:
        assert conn.execute(sa.select(dh_tables.c.id).where(dh_tables.c.id == ore)).first() is None
        hidden = {c.name: c.deleted for c in conn.execute(sa.select(dh_columns).where(dh_columns.c.table_id == src))}
        assert hidden["ORE_ID"] and hidden["ORE_CLS"] and not hidden["AU_CUT"] and not hidden["AU"]
        log = conn.execute(sa.select(edit_log).where(edit_log.c.action == "column")).mappings().all()
        assert len(log) == 2 and len({r["group_id"] for r in log}) == 1
    got = listed(engine, set_id)
    assert rid not in got
    assert got[other]["stale"] is False  # it read AU only: its mark followed the write
    assert got[reader]["reason"] == "изменена таблица «Пробы»"  # it read ORE_CLS, which is gone
    actions.undo(project_of(engine, tmp_path), int(log[0]["id"]))
    with dialect.read_tx(engine) as conn:
        back = {
            c.name for c in conn.execute(sa.select(dh_columns).where(dh_columns.c.table_id == src)) if not c.deleted
        }
    assert {"ORE_ID", "ORE_CLS"} <= back
    with pytest.raises(Exception) as err, dialect.write_tx(engine) as conn:
        res.delete_result(conn, rid)
    assert getattr(err.value, "code", None) == "result_not_found"


def test_shift_marks_skips_results_whose_fields_were_overwritten(db_url: str, tmp_path: Path) -> None:
    engine, set_id, src = setup(db_url, tmp_path)
    keeps = add_result(engine, set_id, src, "equivalent", [(src, ["AU", "CU"])])
    reads_cut = add_result(engine, set_id, None, "conditions", [(src, ["au_cut"])])
    with dialect.write_tx(engine) as conn:
        before, after, key = res.bump_table(conn, set_id, src)
        assert res.shift_marks(conn, set_id, key, before, after, ["AU_CUT"]) == 1
    got = listed(engine, set_id)
    assert got[keeps]["stale"] is False and got[reads_cut]["stale"] is True


def test_list_of_100_results_is_fast(tmp_path: Path) -> None:
    engine, set_id, src = setup("sqlite", tmp_path)
    for _ in range(100):
        add_result(engine, set_id, src, "topcut", [(src, ["AU"])], trajectories=True)
    with dialect.read_tx(engine) as conn:
        res.list_results(conn, set_id)  # warm the statement cache
        t0 = time.perf_counter()
        got = res.list_results(conn, set_id)
        took = time.perf_counter() - t0
    assert len(got) == 100 and not any(r["stale"] for r in got)
    assert took <= 0.020, took


# --- API and .bfggis ---------------------------------------------------------------------------------------


@pytest.fixture()
def cc(pclient):  # type: ignore[no-untyped-def]
    """A core with an open project; the composites router is mounted here until api/modules.py lists it."""
    if not any(str(getattr(r, "path", "")).startswith(API + "/composites") for r in pclient.app.routes):
        from bf_core.composites.router import router

        pclient.app.include_router(router, prefix=API)
    assert pclient.post(API + "/projects", json={"name": "Обработка"}).status_code == 200
    return pclient


def fill(c) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    p = projects_of(c.core).require_current()
    set_id = c.post(API + "/drillholes/sets", json={"name": "Скважины"}).json()["id"]
    src = dh.write_table(p.engine, set_id, samples(), job_id=None, tick=noop)["table_id"]
    comp = dh.write_table(p.engine, set_id, composites(), job_id=None, tick=noop)["table_id"]
    made = add_result(p.engine, set_id, comp, "length", [(src, ["AU"])], trajectories=True)
    cut = add_result(p.engine, set_id, src, "topcut", [(src, ["AU"])])
    return {"set_id": set_id, "src": src, "comp": comp, "made": made, "cut": cut, "engine": p.engine}


def test_api_list_origin_and_delete(cc) -> None:  # type: ignore[no-untyped-def]
    f = fill(cc)
    r = cc.get(API + "/composites/results", params={"set_id": f["set_id"]})
    assert r.status_code == 200, r.text
    rows = {x["id"]: x for x in r.json()["results"]}
    assert set(rows) == {f["made"], f["cut"]} and rows[f["made"]]["params"] == {"length": 2.0}
    tabs = {t["table"]: t for t in cc.get(f"{API}/drillholes/sets/{f['set_id']}/tables").json()["tables"]}
    assert tabs[str(f["comp"])]["origin"] == {"result_id": f["made"], "op": "length", "stale": False, "reason": None}
    assert tabs[str(f["src"])]["origin"] is None and tabs["collar"].get("origin") is None
    cc.core.events._recent.clear()
    r = cc.delete(f"{API}/composites/results/{f['made']}")
    assert r.status_code == 200 and r.json()["deleted_tables"] == [f["comp"]]
    events = [(n, json.loads(d)) for _i, n, d in cc.core.events._recent]
    assert ("composites.changed", {"set_id": f["set_id"]}) in events
    assert ("drillholes.changed", {"set_id": f["set_id"], "table": str(f["comp"]), "rev": 0}) in events
    tabs = {t["table"] for t in cc.get(f"{API}/drillholes/sets/{f['set_id']}/tables").json()["tables"]}
    assert str(f["comp"]) not in tabs
    r = cc.delete(f"{API}/composites/results/{f['made']}")
    assert r.status_code == 404 and r.json()["code"] == "result_not_found" and r.json()["hint"]


def _wait(c, key: str, job_id: int) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = c.get(f"{API}/jobs/{key}/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return dict(job)
        time.sleep(0.05)
    raise AssertionError(job)


def test_bfggis_roundtrip_keeps_results_fresh(cc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    f = fill(cc)
    r = cc.post(API + "/projects/export", json={"path": str(tmp_path / "Обработка")}).json()
    job = _wait(cc, r["source"], r["id"])
    assert job["status"] == "done", job
    r = cc.post(API + "/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = _wait(cc, r["source"], r["id"])
    assert job["status"] == "done", job
    maps = job["result"]["id_maps"]
    assert cc.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    set_id = maps["objects"][str(f["set_id"])]
    got = cc.get(API + "/composites/results", params={"set_id": set_id}).json()["results"]
    by_op = {x["op"]: x for x in got}
    assert set(by_op) == {"length", "topcut"} and not any(x["stale"] for x in got)
    assert by_op["length"]["table_id"] == maps["dh_tables"][str(f["comp"])]
    assert by_op["topcut"]["table_id"] == maps["dh_tables"][str(f["src"])]
    assert by_op["length"]["inputs"]["tables"][0]["key"] == "пробы" and by_op["length"]["job_id"] is None
