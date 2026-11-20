"""Review of Э12 part 2, the core (подшаг 12.34; `docs/progress/e12-review.md`, «Часть 2» № 3, 4, 5, 8–11): the user's
columns of a result table and ORE_* of a replaced table of composites are listed in `lost`; «Перестроить» after
`.bfggis` finds the wireframes by `inputs.meshes`; the thresholds of the top cut by P come from one sort per table
and domain; a table gone during a job — 409 `source_gone`; the snapshot keys by casefold; `X` and `X_SRC` of a
merge never make two columns of one name; no dead branches in `routes_run`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_results
from tests.test_composites_prep import demo  # noqa: F401 - the fixture
from tests.test_import_job import API

C = f"{API}/composites"


# --- № 11: no dead branches ----------------------------------------------------------------------------------


def test_routes_run_has_no_dead_branches(demo) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.composites import routes_run

    src = Path(routes_run.__file__).read_text(encoding="utf-8")
    assert "TODO(12.2" not in src and "PREVIEW_OPS" not in src
    with pytest.raises(ApiError) as e:  # the previews of the other ops have their own routes, registered first
        routes_run.preview(None, "ore", {})  # type: ignore[arg-type]
    assert e.value.status == 404 and e.value.code == "unknown_operation"
    c, project, set_id, _table = demo
    with dialect.write_tx(project.engine) as conn:  # an op of a newer build: the fallback refusal
        made = conn.execute(sa.insert(dh_results).values(set_id=set_id, table_id=None, op="newop", name="Новая",
                                                         params={}, inputs={}, sha256="0", algo="x", report={},
                                                         created_at=dialect.utcnow()))  # fmt: skip
    rid = made.inserted_primary_key[0]
    r = c.post(f"{C}/results/{rid}/rebuild", json={})
    assert r.status_code == 422 and r.json()["code"] == "not_ready", r.text
    assert "в этой версии" in r.json()["message"] and r.json()["hint"]


# --- № 8: the table is gone while the job runs ---------------------------------------------------------------


def test_a_table_deleted_during_the_job_is_409_source_gone(demo) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.composites import jobs_prep as jp
    from bf_core.db.schema import dh_tables

    c, project, set_id, table = demo

    def tick(_p: float | None = None, m: str | None = None) -> None:
        if m == "Запись полей":  # read and computed: the table goes before the final transaction
            with dialect.write_tx(project.engine) as conn:
                conn.execute(sa.delete(dh_tables).where(dh_tables.c.id == int(table)))

    req = {"set_id": set_id, "table": table, "field": "AU", "percentile": 99}
    with pytest.raises(ApiError) as e:
        jp.run_prep(project.engine, project.folder, {"body": req}, "topcut", job_id=96_001, tick=tick)
    assert e.value.status == 409 and e.value.code == "source_gone", e.value.code
    assert "«Пробы»" in e.value.message and e.value.hint
    assert c.get(f"{C}/results?set_id={set_id}").json()["results"] == []


# --- № 9: the snapshot finds its columns by casefold, live ones only ---------------------------------------


def test_snapshot_keys_columns_by_casefold_and_live_only(db_url: str, tmp_path: Path) -> None:
    import numpy as np
    import pyarrow.parquet as pq

    from bf_core.composites import sink
    from bf_core.db.schema import dh_columns
    from bf_core.drillholes.store import ColumnSpec
    from tests.test_composites_results import setup

    engine, _set_id, src = setup(db_url, tmp_path)  # «Пробы»: AU, CU
    with dialect.write_tx(engine) as conn:  # a hidden text column «au» after the live AU
        conn.execute(sa.insert(dh_columns).values(table_id=src, name="au", kind="text", slot=0, role="value",
                                                  deleted=True, position=9))  # fmt: skip
        live = {r.name: f"c{r.id}" for r in conn.execute(
            sa.select(dh_columns.c.id, dh_columns.c.name).where(dh_columns.c.table_id == src,
                                                                dh_columns.c.deleted.is_(False)))}  # fmt: skip
    au, cu = np.array([1.0, np.nan, 0.5, 2.0]), np.array([0.1, 0.2, 0.3, 0.4])
    out = sink.OutTable("Пробы", ["A", "A", "B", "B"], np.array([0.0, 1.0, 0.0, 2.0]), np.array([1.0, 2.0, 2.0, 3.0]),
                        [ColumnSpec("au", "number"), ColumnSpec("Cu", "number")], [au, cu])  # fmt: skip
    path = sink.write_snapshot(engine, tmp_path / "cache", src, out)
    got = pq.read_table(path)
    assert {live["AU"], live["CU"]} <= set(got.column_names)
    np.testing.assert_array_equal(got.column(live["AU"]).to_numpy(zero_copy_only=False), au)
    np.testing.assert_array_equal(got.column(live["CU"]).to_numpy(zero_copy_only=False), cu)
    engine.dispose()


# --- № 10: a field named as an own column gets a free name -------------------------------------------------


def test_fields_x_and_x_src_never_make_two_columns_of_one_name(demo) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.composites.compose import own_names
    from tests.test_composites_jobs import body, rows_of, run_here

    assert own_names(["X", "X_SRC", "AU"], ["X", "Y"]) == {"X": "X_SRC_2", "X_SRC": "X_SRC", "AU": "AU"}
    assert own_names(["x", "X_src", "x_SRC_2"], ["X"]) == {"x": "x_SRC_3", "X_src": "X_src", "x_SRC_2": "x_SRC_2"}
    c, project, set_id, table = demo
    base = f"{API}/drillholes/sets/{set_id}/tables/{table}"
    for name, expr in (("X", "AU * 2"), ("X_SRC", "AU * 3")):
        r = c.post(f"{base}/columns", json={"name": name, "expression": expr})
        assert r.status_code == 200, r.text
    done = run_here(project, {"body": body(set_id, table, fields=["AU", "X", "X_SRC"], name="Композиты X")})
    got = rows_of(c, set_id, str(done["table_id"]))
    names = [x["name"] for x in got["columns"]]
    assert len({n.casefold() for n in names}) == len(names), names
    key = {x["name"]: x["key"] for x in got["columns"]}
    for r in got["rows"]:
        au = r[key["AU"]]
        if au is not None:
            assert r[key["X_SRC_2"]] == pytest.approx(au * 2) and r[key["X_SRC"]] == pytest.approx(au * 3)


# --- № 3: the columns of the user and ORE_* are listed in `lost` -------------------------------------------


def test_lost_lists_the_columns_of_the_user_and_ore(demo) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from tests.test_composites_jobs import body, run_here, tables
    from tests.test_composites_prep import prep_here
    from tests.test_ore_job import body as ore_body
    from tests.test_ore_job import ore_here

    c, project, set_id, table = demo
    first = run_here(project, {"body": body(set_id, table)})
    tid, rid = str(first["table_id"]), first["result_id"]
    prep_here(project, {"body": {"set_id": set_id, "table": tid, "field": "AU", "value": 1.0}}, "topcut")  # repeated
    r = c.post(f"{API}/drillholes/sets/{set_id}/tables/{tid}/columns", json={"name": "МОЯ", "expression": "AU * 2"})
    assert r.status_code == 200, r.text
    r = c.post(f"{C}/results/{rid}/rebuild", json={})
    assert r.status_code == 409 and r.json()["code"] == "dependents_lost", r.text
    assert r.json()["lost"] == [{"kind": "column", "name": "МОЯ"}]  # not the own columns, not AU_CUT of the top cut
    assert "колонки «МОЯ»" in r.json()["message"] and r.json()["hint"]
    ore_here(project, {"body": ore_body(set_id, tid, name="Рудные")})  # ORE_ID, ORE_CLS into the composites
    name = tables(c, set_id)  # «Заменить?» of a new run under the name of the composites
    comp = next(n for n, t in name.items() if t["table"] == tid)
    r = c.post(f"{C}/run", json={**body(set_id, table), "name": comp})
    assert r.status_code == 409 and r.json()["code"] == "table_exists", r.text
    lost = r.json()["lost"]
    assert {"kind": "column", "name": "ORE_ID"} in lost and {"kind": "column", "name": "ORE_CLS"} in lost, lost
    assert {"kind": "column", "name": "МОЯ"} in lost and not any(x["name"] == "AU_CUT" for x in lost), lost
    assert c.post(f"{C}/results/{rid}/rebuild", json={"drop_lost": True}).status_code == 200


# --- № 4: «Перестроить» after `.bfggis` finds the wireframes by name and geometry ---------------------------


def test_rebuild_after_bfggis_takes_the_wireframes_by_inputs_meshes(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """Coding and composites by domains by wireframes → `.bfggis` (the ids renumbered: «Рельеф» before the bodies is
    deleted, the bodies move down one id — an old id names another body) → «Перестроить» takes the same bodies."""
    from tests.test_composites_jobs import body, rows_of, run_here
    from tests.test_composites_prep import run_coding_here, wireframe_ids
    from tests.test_composites_results import _wait
    from tests.test_import_job import wait_job

    c, project, set_id, table = demo
    wf = wireframe_ids(c)
    one, two = wf["Рудное тело 1"], wf["Рудное тело 2"]
    coded = run_coding_here(project, {"body": {"set_id": set_id, "table": table, "wireframe_ids": [one]}})
    dom = {"wireframe_ids": [two, one]}
    by_wf = run_here(project, {"body": body(set_id, table, method="domain", name="Композиты по телу", domain=dom)})
    before = {k: rows_of(c, set_id, str(d["table_id"]))["rows"] for k, d in (("coding", coded), ("domain", by_wf))}
    assert wf["Рельеф"] < one and c.delete(f"{API}/wireframes/{wf['Рельеф']}").status_code == 200
    r = c.post(f"{API}/projects/export", json={"path": str(tmp_path / "Каркасы")}).json()
    job = _wait(c, r["source"], r["id"])
    assert job["status"] == "done", job
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = _wait(c, r["source"], r["id"])
    assert job["status"] == "done", job
    maps = job["result"]["id_maps"]
    assert c.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    now = wireframe_ids(c)
    assert now["Рудное тело 2"] == one  # the old id of «Рудное тело 1» names another body now
    new_set = maps["objects"][str(set_id)]
    listed = {x["op"]: x for x in c.get(f"{C}/results?set_id={new_set}").json()["results"]}
    for op in ("coding", "domain"):
        r = c.post(f"{C}/results/{listed[op]['id']}/rebuild", json={})
        assert r.status_code == 200, (op, r.text)
        done = wait_job(c, r.json()["source"], r.json()["id"], timeout=180)
        assert done["status"] == "done" and done["result"]["fresh"] is True, (op, done)  # the same bodies
        got = rows_of(c, new_set, str(listed[op]["table_id"]))["rows"]
        same = [{k: v for k, v in x.items() if k != "id"} for x in got]
        assert same == [{k: v for k, v in x.items() if k != "id"} for x in before[op]], op
    with dialect.read_tx(projects_of_c(c).engine) as conn:
        from bf_core.composites.results import current_meshes

        assert current_meshes(conn, listed["domain"]["inputs"]) == [now["Рудное тело 2"], now["Рудное тело 1"]]
        with pytest.raises(ApiError) as e:
            current_meshes(conn, {"meshes": [{"name": "Нет такого", "key": "x"}]})
    assert e.value.status == 409 and e.value.code == "source_gone" and "«Нет такого»" in e.value.message


def projects_of_c(c: Any) -> Any:
    from bf_core.projects.service import projects_of

    return projects_of(c.core).require_current()


# --- № 5: the top cut by P — one sort per table and domain -------------------------------------------------


def test_settle_is_the_cut_of_topcut_bit_for_bit() -> None:
    import numpy as np

    from bf_core.composites import topcut as tc

    rng = np.random.default_rng(7)
    n = 20_000
    v = rng.lognormal(0.0, 1.2, n)
    v[rng.random(n) < 0.05] = np.nan
    ln = rng.uniform(0.5, 2.0, n)
    ln[:30] = np.nan  # a value without a length
    codes = rng.integers(-1, 3, n)
    names = ["ОК", "ПР", "ЗК"]
    prep = tc.prepare(v, ln, codes=codes, names=names)
    for raw in ({"percentile": 90}, {"percentile": 97.35}, {"percentile": 99.99}, {"value": 3.5},
                {"thresholds": "ОК=2; ЗК=5; НЕТ=1"}):  # fmt: skip
        params = tc.TopcutParams.of(raw)
        t = tc.topcut(v, ln, params, codes=codes, names=names, field="AU")
        thr, rep = tc.settle(prep, params, "AU")
        assert dict(zip(prep.labels, thr, strict=True)) == t.thresholds and rep == t.report, raw
    for g, s in enumerate(prep.parts):
        assert prep.percentile(g, 95.5) == tc._f(s.percentiles(95.5)[0])


def _holes(project: Any, set_id: int) -> list[int]:
    from bf_core.db.schema import dh_holes

    with dialect.read_tx(project.engine) as conn:
        q = sa.select(dh_holes.c.id).where(dh_holes.c.set_id == set_id).order_by(dh_holes.c.id)
        return [int(i) for i in conn.execute(q).scalars()]


def test_a_new_p_does_not_sort_the_table_again(demo, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.composites import routes_prep
    from bf_core.composites import topcut as tc
    from tests.test_composites_prep import prep_here

    c, project, set_id, table = demo
    routes_prep._sorted_cache.clear()
    from bf_core.db.schema import dh_tables

    with dialect.read_tx(project.engine) as conn:
        total = int(conn.execute(sa.select(dh_tables.c.row_count).where(dh_tables.c.id == int(table))).scalar_one())
    whole = [0]
    real = tc.sort_groups

    def counted(values: Any, *a: Any, **kw: Any) -> Any:
        whole[0] += len(values) >= total - 5  # the whole table (but rows without FROM or TO), not one hole
        return real(values, *a, **kw)

    monkeypatch.setattr(tc, "sort_groups", counted)
    hole = _holes(project, set_id)[3]
    seen = {}
    for p in (95, 97.5, 99, 95):
        req = {"set_id": set_id, "table": table, "field": "AU", "percentile": p}
        r = c.post(f"{C}/topcut/preview", json={"params": req, "hole_id": hole})
        assert r.status_code == 200, r.text
        seen[p] = r.json()
    assert whole[0] == 1, whole  # one sort of the whole table for every P
    monkeypatch.setattr(tc, "sort_groups", real)
    for p, got in seen.items():  # the preview is the job: its thresholds, summary and notes
        done = prep_here(project, {"body": {"set_id": set_id, "table": table, "field": "AU", "percentile": p}},
                         "topcut")  # fmt: skip
        rec = next(x for x in c.get(f"{C}/results?set_id={set_id}").json()["results"] if x["id"] == done["result_id"])
        assert got["thresholds"] == rec["report"]["thresholds"] and got["set_summary"] == rec["report"]["summary"], p
        assert got["notes"] == rec["report"]["journal"], p


def test_one_sort_at_a_time_per_key(demo, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    import threading
    import time

    from bf_core.composites import jobs_prep as jp
    from bf_core.composites import routes_prep
    from bf_core.composites import topcut as tc

    c, project, set_id, table = demo
    routes_prep._sorted_cache.clear()
    calls = [0]
    real = tc.prepare

    def slow(*a: Any, **kw: Any) -> Any:
        calls[0] += 1
        time.sleep(0.3)
        return real(*a, **kw)

    monkeypatch.setattr(tc, "prepare", slow)
    with dialect.read_tx(project.engine) as conn:
        specs = [jp.parse_prep(conn, {"set_id": set_id, "table": table, "field": "AU", "percentile": p}, "topcut")
                 for p in (91, 93, 95, 97, 99)]  # fmt: skip
    got: list[Any] = []
    threads = [threading.Thread(target=lambda s=s: got.append(routes_prep._whole(project, s))) for s in specs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls[0] == 1 and len(got) == 5, calls
    assert len({json_key(g["thresholds"]) for g in got}) == 5  # each P its own thresholds
    assert routes_prep._gates == {}


def json_key(x: Any) -> str:
    import json

    return json.dumps(x, sort_keys=True)
