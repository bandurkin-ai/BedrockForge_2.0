"""The pit scenarios in the project (подшаг 21.10; ADR 0028 решения 20–21; docs/progress/e21-api.md §5, §6, §8): the
object `pit_scenario` (a new one without a result → the parameters edited → the result written in place), its files
(`data`, `sidecar:benches`, `sidecar:zgrid`, `sidecar:template`, `sidecar:control`), the state «идёт расчёт» in
`meta` only, the atomic write (a failure leaves the old result), the same bytes on a repeated write, the limit of
200, rename, delete, the same on PostgreSQL (`-m db`, BF_TEST_PG). The cores of the run are other subshags: the
tables are made here by hand; the graph — `test_pit_lineage.py`, `.bfggis` — `test_pit_remap.py`."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, lineage, objects, valid_blob_role
from bf_core.mine import spec
from bf_core.mine import store as st
from bf_core.mine import store_data as sd
from bf_core.mine import store_graph as sg
from tests.estutil import W, world
from tests.pitutil import (
    BASE,
    BENCHES,
    CONTROL,
    ELEMENTS,
    GRID,
    TEMPLATE,
    ZGRID,
    ZI,
    ZJ,
    built,
    counts,
    hash_of,
    make,
    meta_shells,
    new,
    owned_sha,
    params_of,
    rd,
    refused,
    roles,
    sha_of,
    shas,
    shells_of,
)


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


# --- the files ------------------------------------------------------------------------------------------------------


def test_the_columns_are_those_of_the_adr() -> None:
    names = [n for n, _ in sd.data_columns(["AU", "CU"])]
    assert names[:2] == ["k", "rf"] and names[2:11] == ["blocks", "ore_t", "waste_t", "revenue", "cost_mine",
                                                         "cost_process", "cost_sales", "royalty", "cf"]  # fmt: skip
    assert names[11:17] == ["AU:grade", "AU:metal", "AU:rec_metal", "CU:grade", "CU:metal", "CU:rec_metal"]
    assert names[17:26] == [f"cum_{p}" for p in sd.SHELL_PARTS] and names[26:] == [
        f"cum_{e}:{p}" for e in ("AU", "CU") for p in sd.ELEMENT_PARTS]  # fmt: skip
    assert len(names) == 2 + 9 + 6 + 9 + 6 and [n for n, _ in sd.data_columns([])][-1] == "cum_cf"


def test_the_shells_come_back_as_they_went_and_a_gap_is_null() -> None:
    rows = shells_of()
    table = sd.shells_table(rows, ELEMENTS)
    assert table.num_rows == 3 and sd.elements_of(table) == ELEMENTS
    back = sd.table_shells(table)
    assert back == rows
    first = back[0]  # no ore: the strip and the grade are empty, not 0
    assert first["strip"] is None and first["elements"]["AU"]["grade"] is None and first["cum"]["strip"] is None
    assert back[1]["strip"] == back[1]["waste_t"] / back[1]["ore_t"] and isinstance(back[1]["blocks"], int)
    nan = [{**rows[1], "cf": math.nan, "elements": {"AU": {"grade": math.inf, "metal": 1.0, "rec_metal": 0.9}}}]
    got = sd.table_shells(sd.shells_table(nan, ELEMENTS))[0]
    assert got["cf"] is None and got["elements"]["AU"]["grade"] is None and got["elements"]["AU"]["metal"] == 1.0
    extra = [{**rows[1], "best": {"npv": 1.0}, "worst": {"npv": 0.0}}]  # the calendar is a view: it is not stored
    assert "best" not in sd.table_shells(sd.shells_table(extra, ELEMENTS))[0]


def test_the_tables_of_arrays_and_their_refusals() -> None:
    import numpy as np

    cols = {n: np.zeros(2) for n, _ in sd.data_columns(["AU"])}
    cols["k"], cols["blocks"], cols["cf"] = np.array([1, 2]), np.array([3, 4]), np.array([np.nan, 1.0])
    rows = sd.table_shells(sd.data_table(cols, ["AU"]))
    assert [r["k"] for r in rows] == [1, 2] and rows[0]["cf"] is None and rows[1]["blocks"] == 4
    with pytest.raises(ValueError, match="no columns"):
        sd.data_table({k: v for k, v in cols.items() if k != "rf"}, ["AU"])
    with pytest.raises(ValueError, match="different length"):
        sd.data_table({**cols, "rf": np.zeros(3)}, ["AU"])
    bench = sd.table_benches(sd.benches_table(BENCHES))
    assert bench == {k: list(map(type(v[0]), v)) for k, v in BENCHES.items()}
    z = sd.table_zgrid(sd.zgrid_table(ZI, ZJ, ZGRID))
    assert list(z["i"]) == ZI and list(z["j"]) == ZJ and z["z"].shape == (3, 4)
    assert np.isnan(z["z"][0, 2]) and np.isnan(z["z"][1, 2]) and z["z"][2, 2] == 5.0  # a null is NaN, never 0
    with pytest.raises(ValueError, match="different length"):
        sd.zgrid_table([0, 1], [0], [[1.0, 2.0]])


def test_the_json_is_plain_and_the_same_document_is_the_same_bytes() -> None:
    import numpy as np

    doc = {"b": np.float64(1.5), "a": [np.int64(2), math.nan, math.inf], "k": {"z": np.array([1.0, math.nan])}}
    assert sd.clean_json(doc) == {"a": [2, None, None], "b": 1.5, "k": {"z": [1.0, None]}}
    assert sd.json_bytes(doc) == sd.json_bytes({"k": {"z": [1.0, None]}, "a": [2, None, None], "b": 1.5})
    assert sd.json_bytes({"x": "Руда"}) == '{"x":"Руда"}'.encode()


def test_the_same_tables_are_the_same_bytes(w: W) -> None:
    a = sd.stage_parquet(w.store, sd.shells_table(shells_of(), ELEMENTS))
    b = sd.stage_parquet(w.store, sd.shells_table(shells_of(), ELEMENTS))
    c = sd.stage_parquet(w.store, sd.shells_table(shells_of(2), ELEMENTS))
    assert a[1] == b[1] != c[1]  # «Перестроить» gives the same `data`: the same SHA-256
    for f in (a, b, c):
        f[0].unlink()


# --- create, edit ---------------------------------------------------------------------------------------------------


def test_a_new_scenario_has_the_defaults_and_no_result(w: W) -> None:
    with dialect.write_tx(w.project.engine) as conn:
        out = st.create(conn, None, None)  # no name, no parameters: a form to fill in
    ident = out["id"]
    assert out == {"id": ident, "version": 1, "name": "Сценарий карьера"}
    row = rd(w, lambda c: c.execute(sa.select(objects).where(objects.c.id == ident)).one())
    assert (row.kind, row.parent_id, row.state, row.version) == ("pit_scenario", None, "ready", 1)
    assert roles(w, ident) == set() and row.meta["params"] == spec.parse_params({}).canonical()
    got = rd(w, st.info, ident)
    assert (got["state"], got["has_result"], got["params_changed"], got["error"]) == ("ready", False, False, None)
    assert got["model"] == {"id": None, "name": None} and got["shells"] == 0 and got["value"] is None
    assert got["params"] == row.meta["params"] and got["params_hash"] == spec.parse_params({}).params_hash()
    assert got["graph"] == {"stale": False, "stale_reason": None, "source_lost": False, "pinned": False}
    assert "draft" not in got["meta"] and "run_job" not in got["meta"] and "uid" not in got["meta"]
    refused(lambda: rd(w, st.read_data, w.store, ident), 409, "pit_no_result")
    assert rd(w, st.read_benches, w.store, ident) is None and rd(w, st.read_template, w.store, ident) is None
    refused(lambda: rd(w, st.rebuild_params, ident), 409, "pit_no_result")
    assert rd(w, st.data_sha, ident) is None
    assert st.fingerprint(None) == "нет результата"


def test_a_scenario_is_made_on_a_model_that_is_there(w: W) -> None:
    ident = new(w)
    got = rd(w, st.info, ident)
    assert got["model"] == {"id": w.model, "name": "Модель"} and got["params"]["model_id"] == w.model
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.create(conn, "Нет модели", {"model_id": 99_999}), 404, "block_model_not_found")
        err = refused(lambda: st.create(conn, "Плохой", {"rf": [0.0]}), 422, "bad_param")
        assert err.extra["param"] == "rf.0"
    assert [r["name"] for r in rd(w, st.list_scenarios)] == ["Сценарий карьера"]


def test_names_are_made_free_and_the_number_is_limited(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    with dialect.write_tx(w.project.engine) as conn:
        assert [st.create(conn, "Вариант")["name"] for _ in range(3)] == ["Вариант", "Вариант (2)", "Вариант (3)"]
        assert st.create(conn, "  вариант  ")["name"] == "вариант (4)"  # the key is NFC + casefold
        assert st.create(conn, None)["name"] == "Сценарий карьера"
    monkeypatch.setattr(st, "MAX_SCENARIOS", 5)
    with dialect.write_tx(w.project.engine) as conn:
        err = refused(lambda: st.create(conn, "Лишний"), 422, "too_many_scenarios")
    assert "5" in err.message


def test_the_limit_of_the_project_is_200() -> None:
    assert st.MAX_SCENARIOS == 200


def test_the_parameters_of_a_scenario_without_a_result_are_replaced(w: W) -> None:
    ident = new(w, "Первый")
    other = {**BASE, "model_id": w.model, "rf": [0.9, 1.1], "mining": {"c_m0": 3.0}}
    with dialect.write_tx(w.project.engine) as conn:
        out = st.update_params(conn, ident, 1, params=other, name="Новое имя")
    assert out == {"id": ident, "version": 2, "name": "Новое имя", "params_changed": False}  # nothing to run yet
    got = rd(w, st.info, ident)
    assert got["params"]["rf"] == [0.9, 1.1] and got["params"]["mining"]["c_m0"] == 3.0
    assert got["params_hash"] == spec.parse_params(other).params_hash() and got["has_result"] is False
    assert rd(w, st.current_params, ident) == got["params"]
    with dialect.write_tx(w.project.engine) as conn:  # the model is changed with the parameters
        st.update_params(conn, ident, 2, params={**other, "model_id": None})
    assert rd(w, st.info, ident)["model"] == {"id": None, "name": None}


def test_a_version_conflict_a_name_taken_and_the_other_refusals_of_the_edit(w: W) -> None:
    a, b = new(w, "Первый"), new(w, "Второй")
    with dialect.write_tx(w.project.engine) as conn:
        err = refused(lambda: st.update_params(conn, a, 7, name="Ещё"), 409, "version_conflict")
        assert err.extra == {"version": 1}
        err = refused(lambda: st.update_params(conn, a, 1, name="второй"), 409, "name_taken")
        assert err.extra == {"param": "name"}
        refused(lambda: st.update_params(conn, a, 1, name="  "), 422, "bad_param")
        refused(lambda: st.update_params(conn, a, 1, params={"rf": [9.0]}), 422, "bad_param")
        err = refused(lambda: st.update_params(conn, a, 1, calendar={"rate": 9}), 422, "bad_param")
        assert err.extra["param"] == "calendar.rate"
        refused(lambda: st.update_params(conn, a, 1, params={"model_id": 99_999}), 404, "block_model_not_found")
        refused(lambda: st.update_params(conn, 99_999, 1, name="x"), 404, "pit_scenario_not_found")
        assert st.update_params(conn, a, 1, name="Первый")["version"] == 2  # its own name is not «taken»
    assert rd(w, st.info, b)["version"] == 1  # the other is not touched


def test_rename_takes_the_version_and_a_taken_name_is_refused(w: W) -> None:
    a, _b = new(w, "Первый"), new(w, "Второй")
    with dialect.write_tx(w.project.engine) as conn:
        assert st.rename(conn, a, "  Новое   имя ", 1) == {"id": a, "version": 2, "name": "Новое имя"}
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.rename(conn, a, "Ещё", 1), 409, "version_conflict")
        refused(lambda: st.rename(conn, a, "второй", 2), 409, "name_taken")
        refused(lambda: st.rename(conn, a, "   ", 2), 422, "bad_param")
        refused(lambda: st.rename(conn, 99_999, "x", 1), 404, "pit_scenario_not_found")


# --- the result -----------------------------------------------------------------------------------------------------


def test_write_result_writes_the_object_the_files_and_the_graph_in_one_go(w: W) -> None:
    ident = new(w, "Карьер ЮЗ")
    b = built(w)
    with dialect.write_tx(w.project.engine) as conn:
        out = st.write_result(conn, w.store, ident, b, expected_version=1)
    assert out == {"id": ident, "version": 2, "name": "Карьер ЮЗ"}  # the first run is version + 1
    assert roles(w, ident) == {"data", "sidecar:benches", "sidecar:zgrid", "sidecar:template", "sidecar:control"}
    assert all(valid_blob_role(r) for r in roles(w, ident))  # the existing roles: no migration
    meta = rd(w, lambda c: c.execute(sa.select(objects.c.meta).where(objects.c.id == ident)).scalar_one())
    assert (meta["v"], meta["algo"], meta["state"], meta["rows"], meta["solver"], meta["job_id"]) == (
        1, "pit-1", "ready", 3, "hpf", 3)  # fmt: skip
    assert meta["params"] == b.params and meta["params_hash"] == spec.parse_params(b.params).params_hash()
    assert meta["model"]["id"] == w.model and meta["model"]["cells_sha"] and meta["model"]["grid"] == GRID
    assert sorted(meta["fields"]) == ["AU"] and all(meta["fields"].values())  # FRAC is not in this model
    kinds = [(i["kind"], i["id"]) for i in meta["inputs"]]
    assert kinds == [("object:block_model", w.model), ("bm_field", f"{w.model}:AU")]
    assert (meta["q"], meta["objective_error"], meta["totals"]["strip"]) == (0.01, 12.5, 2.0)
    assert [s["k"] for s in meta["shells"]] == [1, 2, 3] and meta["shells"][-1]["value"] == 3.0e7
    assert meta["outputs"] == {"fields": ["PIT", "ORE"], "wireframes": []} and meta["control"] == CONTROL
    assert meta["template"] == {"k": 8, "k_auto": True, "domains": [{"label": None, "n_offsets": 2, "error": {
        "max": 1.44, "mean": 0.58}}], "warnings": []}  # fmt: skip
    assert meta["seconds"] == {"total": 1.5} and meta["versions"] == {"numpy": "2"} and "draft" not in meta
    with dialect.read_tx(w.project.engine) as conn:  # the same in the table of the graph
        edges = conn.execute(sa.select(lineage.c.src_kind, lineage.c.src_sub).where(
            lineage.c.dst_kind == "object:pit_scenario", lineage.c.dst_id == ident).order_by(lineage.c.pos)).all()
    assert [(k, s) for k, s in edges] == [("object:block_model", ""), ("bm_field", "au")]


def test_read_the_files_back(w: W) -> None:
    ident = make(w, "Карьер")
    assert rd(w, st.read_shells, w.store, ident) == shells_of()
    assert sd.table_shells(rd(w, st.read_data, w.store, ident)) == shells_of()
    assert sd.table_benches(rd(w, st.read_benches, w.store, ident))["cf"] == BENCHES["cf"]
    z = sd.table_zgrid(rd(w, st.read_zgrid, w.store, ident))
    assert z["z"].shape == (3, 4) and math.isnan(z["z"][0, 2])
    assert rd(w, st.read_template, w.store, ident) == TEMPLATE and rd(w, st.read_control, w.store, ident) == CONTROL
    got = rd(w, st.info, ident, store=w.store)
    assert (got["name"], got["version"], got["state"], got["has_result"], got["shells"], got["value"]) == (
        "Карьер", 2, "ready", True, 3, 3.0e7)  # fmt: skip
    assert got["job_id"] == 3 and got["model"] == {"id": w.model, "name": "Модель"} and got["size"] > 0
    assert got["params_changed"] is False and got["graph"]["stale"] is False
    assert got["params"] == got["meta"]["params"] and got["params_hash"] == got["meta"]["params_hash"]


def test_the_list_has_the_value_and_the_state_in_the_graph(w: W) -> None:
    a, b = make(w, "Б сценарий"), make(w, "А сценарий")
    plain = new(w, "В пустой")
    got = rd(w, st.list_scenarios)
    assert [r["name"] for r in got] == ["А сценарий", "Б сценарий", "В пустой"]
    assert [r["id"] for r in got] == [b, a, plain]
    first, empty = got[0], got[2]
    assert first["model"] == {"id": w.model, "name": "Модель"} and (first["shells"], first["value"]) == (3, 3.0e7)
    assert first["graph"] == {"stale": False, "stale_reason": None, "source_lost": False, "pinned": False}
    assert first["state"] == "ready" and first["has_result"] and first["created_at"] and first["updated_at"]
    assert (empty["has_result"], empty["shells"], empty["value"]) == (False, 0, None)


def test_a_run_again_replaces_the_files_in_place_and_keeps_the_name(w: W) -> None:
    ident = make(w, "Карьер")
    sha, created = sha_of(w, ident), rd(w, st.info, ident)["created_at"]
    b = built(w, shells_of(2), rf=[0.9, 1.1])  # another result, the same scenario
    b.staged.discard()
    b = built(w, shells_of(2), params=params_of(w, rf=[0.9, 1.1]))
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.write_result(conn, w.store, ident, b, expected_version=9), 409, "version_conflict")
        assert st.write_result(conn, w.store, ident, b, expected_version=2) == {"id": ident, "version": 3,
                                                                                "name": "Карьер"}  # fmt: skip
    after = rd(w, st.info, ident)
    assert (after["version"], after["name"], after["meta"]["rows"], after["shells"]) == (3, "Карьер", 2, 2)
    assert after["created_at"] == created and sha_of(w, ident) != sha
    assert [r["id"] for r in rd(w, st.list_scenarios)] == [ident]
    # a run with fewer files: a role the new result has not is dropped with the old file
    lean = st.Built(params_of(w), {"id": w.model, "name": "Модель", "grid": GRID}, ["AU"],
                    st.stage(w.store, sd.shells_table(shells_of(), ELEMENTS)), meta_shells(shells_of()))  # fmt: skip
    with dialect.write_tx(w.project.engine) as conn:
        st.write_result(conn, w.store, ident, lean)
    assert roles(w, ident) == {"data"} and rd(w, st.read_zgrid, w.store, ident) is None
    assert rd(w, st.read_control, w.store, ident) is None and rd(w, st.info, ident)["meta"]["template"] is None


def test_a_run_again_gives_the_same_bytes(w: W) -> None:
    """«Перестроить» is a run from scratch with the same parameters: the same `data` bit for bit (Т8.11-9)."""
    ident = make(w, "Карьер")
    first = shas(w, ident)
    again = built(w)
    assert again.staged.data[1] == first["data"] and again.staged.zgrid[1] == first["sidecar:zgrid"]  # type: ignore[index]
    with dialect.write_tx(w.project.engine) as conn:
        st.write_result(conn, w.store, ident, again)
    assert rd(w, st.info, ident)["version"] == 3
    assert shas(w, ident) == first
    fp = st.fingerprint(first["data"])
    assert fp.startswith("p:") and fp == st.fingerprint(first["data"]) != st.fingerprint(first["data"], "pit-2")


def test_a_failure_in_the_write_leaves_the_old_result(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    """The write is one transaction: a failure after the files are in (the hook of the fields, the graph) takes all of
    it back — the old result, its files and its rows of the graph stay; the staged files go with `discard`."""
    ident = make(w, "Карьер")
    before, sha = counts(w), sha_of(w, ident)
    info = rd(w, st.info, ident)
    files = {p.name for p in w.store.root.glob("??/??/*")}

    def boom(*_a: Any, **_k: Any) -> Any:
        raise ApiError(404, "lineage_node_not_found", "Объект не найден.", "Обновите дерево проекта.")

    b = built(w, shells_of(2))  # another result
    monkeypatch.setattr(st, "_record", boom)
    with pytest.raises(ApiError):
        with dialect.write_tx(w.project.engine) as conn:
            st.write_result(conn, w.store, ident, b)
    b.staged.discard()
    assert counts(w) == before and sha_of(w, ident) == sha and rd(w, st.info, ident) == info
    assert rd(w, st.read_shells, w.store, ident) == shells_of()
    monkeypatch.undo()

    def hook(conn: sa.Connection, _ident: int) -> None:  # the fields of the model are written here in the real run
        conn.execute(sa.update(objects).where(objects.c.id == ident).values(name="сломано"))
        raise RuntimeError("the fields did not go")

    c = built(w, shells_of(2), on_write=hook)
    with pytest.raises(RuntimeError, match="fields"):
        with dialect.write_tx(w.project.engine) as conn:
            st.write_result(conn, w.store, ident, c)
    c.staged.discard()
    assert counts(w) == before and rd(w, st.info, ident)["name"] == "Карьер" and sha_of(w, ident) == sha
    assert not list(w.store.tmp_dir.glob("*"))  # nothing is left in the temp folder
    from bf_core.storage import gc

    gc.collect(w.project.engine, w.store, now=time.time() + 86_400)
    assert {p.name for p in w.store.root.glob("??/??/*")} == files  # the placed files are not left either


def test_the_hook_runs_in_the_transaction_after_the_files(w: W) -> None:
    ident = new(w, "Карьер")
    seen: list[Any] = []

    def hook(conn: sa.Connection, i: int) -> None:
        seen.append((i, owned_sha(conn, i, "data")))  # the file of the result is already the scenario's

    b = built(w, on_write=hook)
    with dialect.write_tx(w.project.engine) as conn:
        st.write_result(conn, w.store, ident, b)
    assert seen == [(ident, sha_of(w, ident))]


def test_a_job_cancelled_before_the_write_leaves_nothing(w: W) -> None:
    ident = new(w, "Карьер")
    before = counts(w)
    b = built(w)
    b.staged.discard()  # the job saw the cancel after the files were staged
    assert not list(w.store.tmp_dir.glob("*")) and counts(w) == before and not rd(w, st.info, ident)["has_result"]


def test_a_changed_input_is_model_changed_and_nothing_is_written(w: W) -> None:
    from bf_core import lineage as lg
    from bf_core.db.schema import dh_tables  # noqa: F401  (the graph of the world: the model and its fields)

    ident = new(w, "Карьер")
    p = params_of(w)
    names = sg.read_fields(p)
    prints = rd(w, sg.input_prints, p, names)
    assert [n.kind for n in prints] == ["object:block_model", "bm_field"] and all(prints.values())
    prints[lg.Node("bm_field", w.model, "au")] = "other"  # the field is another file than the pass read
    b = built(w, params=p, prints=prints)
    before = counts(w)
    with pytest.raises(ApiError) as e:
        with dialect.write_tx(w.project.engine) as conn:
            st.write_result(conn, w.store, ident, b)
    assert (e.value.status, e.value.code) == (409, "model_changed")
    # the prints are compared BEFORE the files are put: no file went to the store (no blob without an owner until gc)
    assert not any(w.store.path_of(f[1]).is_file() for _role, f in b.staged.files() if f is not None)
    b.staged.discard()
    assert counts(w) == before and not rd(w, st.info, ident)["has_result"]
    ok = built(w, params=p, prints=rd(w, sg.input_prints, p, names))  # the same inputs as they are: written
    with dialect.write_tx(w.project.engine) as conn:
        st.write_result(conn, w.store, ident, ok)
    assert rd(w, st.info, ident)["has_result"]


# --- the parameters edited after a run ------------------------------------------------------------------------------


def test_edited_parameters_wait_for_a_run_and_the_calendar_does_not(w: W) -> None:
    ident = make(w, "Карьер")
    stored = rd(w, st.info, ident)["params"]
    other = params_of(w, mining={"c_m0": 3.5})
    with dialect.write_tx(w.project.engine) as conn:
        out = st.update_params(conn, ident, 2, params=other)
    assert out["params_changed"] is True and out["version"] == 3
    got = rd(w, st.info, ident)
    assert got["params_changed"] is True and got["params"] == other and got["params_hash"] == hash_of(other)
    assert got["meta"]["params"] == stored and got["meta"]["params_hash"] == hash_of(stored)  # the result's own
    assert rd(w, st.current_params, ident) == other and rd(w, st.result_params, ident) == stored
    assert rd(w, st.rebuild_params, ident)["params"] == stored  # «Перестроить» runs the same parameters again
    assert rd(w, st.list_scenarios)[0]["params_changed"] is True and got["has_result"] is True
    # the calendar alone: not a draft, the report sees it at once; the draft is not lost
    with dialect.write_tx(w.project.engine) as conn:
        st.update_params(conn, ident, 3, calendar={"q_ore": 1.0e6, "rate": "0,1"})
    now = rd(w, st.info, ident)
    assert now["params_changed"] is True and now["params"]["calendar"] == {"q_ore": 1.0e6, "q_mass": None, "rate": 0.1}
    assert now["meta"]["params"]["calendar"]["q_ore"] == 1.0e6
    assert now["meta"]["params_hash"] == got["meta"]["params_hash"]
    # the parameters back as the result has them: no draft, no flag; only the calendar and the solver are taken
    back = {**stored, "solver": "ortools", "calendar": {"q_ore": 5.0e5, "q_mass": None, "rate": 0.05}}
    with dialect.write_tx(w.project.engine) as conn:
        out = st.update_params(conn, ident, 4, params=back)
    assert out["params_changed"] is False
    done = rd(w, st.info, ident)
    assert done["params_changed"] is False and done["meta"]["params"]["solver"] == "ortools"
    assert done["meta"]["params"]["calendar"]["rate"] == 0.05 and done["params_hash"] == hash_of(stored)
    # a run clears the draft: «параметры изменены» goes with the new result
    with dialect.write_tx(w.project.engine) as conn:
        st.update_params(conn, ident, 5, params=other)
    b = built(w, params=other)
    with dialect.write_tx(w.project.engine) as conn:
        st.write_result(conn, w.store, ident, b)
    after = rd(w, st.info, ident)
    assert after["params_changed"] is False and after["meta"]["params"] == other and "draft" not in after["meta"]


# --- the state «идёт расчёт» ----------------------------------------------------------------------------------------


def test_the_state_running_is_in_the_meta_only(w: W) -> None:
    ident = make(w, "Карьер")
    sha = sha_of(w, ident)
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_running(conn, ident)  # a mark without a job is trusted
    with dialect.read_tx(w.project.engine) as conn:
        row = conn.execute(sa.select(objects).where(objects.c.id == ident)).one()
        assert row.state == "ready" and row.meta["state"] == "building"  # `objects.state` stays `ready` (19.29б)
        assert st.info(conn, ident)["state"] == "running" and st.list_scenarios(conn)[0]["state"] == "running"
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.mark_running(conn, ident), 409, "pit_running")
        refused(lambda: st.rename(conn, ident, "x", 2), 409, "pit_running")
        refused(lambda: st.update_params(conn, ident, 2, name="x"), 409, "pit_running")
        refused(lambda: st.delete(conn, ident), 409, "pit_running")
        refused(lambda: st.rebuild_params(conn, ident), 409, "pit_running")
    assert rd(w, st.read_shells, w.store, ident) == shells_of()  # the old numbers are read while it runs
    with dialect.write_tx(w.project.engine) as conn:
        st.settle(conn, ident)  # the job was cancelled: the scenario is as it was
        st.settle(conn, ident)
    got = rd(w, st.info, ident)
    assert (got["state"], got["version"], got["job_id"], got["error"]) == ("ready", 2, 3, None)
    assert sha_of(w, ident) == sha
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_running(conn, ident, 987_654)  # a job that is gone (a crash, a restart): the mark is not trusted
    assert rd(w, st.info, ident)["state"] == "ready"


def test_a_failed_run_leaves_the_error_until_the_next_run(w: W) -> None:
    ident = make(w, "Карьер")
    err = {"code": "pit_empty", "message": "Нет блоков.", "hint": "Проверьте цены."}
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_running(conn, ident)
        st.settle(conn, ident, err)
    got = rd(w, st.info, ident)
    assert got["state"] == "error" and got["error"] == err and got["has_result"] is True  # the old result stays
    assert rd(w, st.list_scenarios)[0]["state"] == "error"
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_running(conn, ident)  # a new run starts clean
    assert rd(w, st.info, ident)["error"] is None
    with dialect.write_tx(w.project.engine) as conn:
        st.settle(conn, ident)
        st.write_result(conn, w.store, ident, built(w))
    assert rd(w, st.info, ident)["state"] == "ready"


# --- delete, broken -------------------------------------------------------------------------------------------------


def test_delete_takes_the_files_and_the_rows_of_the_graph_and_says_what_stays(w: W) -> None:
    a, b = make(w, "Один"), make(w, "Два")
    sha = sha_of(w, a)
    with dialect.write_tx(w.project.engine) as conn:
        out = st.delete(conn, a)
        refused(lambda: st.delete(conn, a), 404, "pit_scenario_not_found")
    assert out == {"id": a, "name": "Один", "model_id": w.model, "outputs": {"fields": ["PIT", "ORE"],
                                                                              "wireframes": []}}  # fmt: skip
    with dialect.read_tx(w.project.engine) as conn:
        assert conn.execute(sa.select(objects.c.id).where(objects.c.id == a)).first() is None
        assert conn.execute(sa.select(blob_owners.c.sha256).where(blob_owners.c.owner_id == a)).first() is None
        assert conn.execute(sa.select(lineage.c.dst_id).where(lineage.c.dst_kind == "object:pit_scenario",
                                                              lineage.c.dst_id == a)).first() is None
        refused(lambda: st.info(conn, a), 404, "pit_scenario_not_found")
    assert [r["id"] for r in rd(w, st.list_scenarios)] == [b] and sha_of(w, b) == sha  # the file is shared by content
    assert rd(w, st.read_shells, w.store, b) == shells_of()


def test_a_file_that_is_gone_is_broken_not_an_empty_result(w: W) -> None:
    ident = make(w, "Карьер")
    w.store.path_of(str(sha_of(w, ident))).unlink()
    refused(lambda: rd(w, st.read_data, w.store, ident), 409, "pit_scenario_broken")
    refused(lambda: rd(w, st.read_shells, w.store, ident), 409, "pit_scenario_broken")


def test_a_start_locks_the_row_on_postgresql_and_the_view_is_in_its_own_module(w: W) -> None:
    from sqlalchemy.dialects import postgresql

    from bf_core.mine import store_view

    ident = make(w, "Карьер")
    seen: list[str] = []

    class Spy:
        """Forwards to the connection and keeps the text of every statement for PostgreSQL."""

        def __init__(self, conn: sa.Connection) -> None:
            self.conn = conn

        def execute(self, stmt: Any, *a: Any, **kw: Any) -> Any:
            seen.append(str(stmt.compile(dialect=postgresql.dialect())))
            return self.conn.execute(stmt, *a, **kw)

    with dialect.write_tx(w.project.engine) as conn:
        st.mark_running(Spy(conn), ident)  # type: ignore[arg-type]
    assert "FOR UPDATE" in seen[0] and seen[0].startswith("SELECT")  # the first thing a start does: the locked read
    assert rd(w, st.info, ident)["state"] == "running"  # SQLite: the same behaviour as before
    assert (st.info, st.list_scenarios, st.graph_of) == (store_view.info, store_view.list_scenarios,
                                                         store_view.graph_of)  # the imports of the callers stay


# --- the fields the graph reads -------------------------------------------------------------------------------------


def test_the_fields_of_the_parameters_are_those_the_run_reads(w: W) -> None:
    two = [{"field": "AU", "unit": "g/t", "price": 1.0}, {"field": "CU", "unit": "%", "price": 2.0}]
    domains = [{"label": "руда", "table": [{"angle": 40.0}]}]
    p = params_of(w, elements=two, density=None, default_density=2.6,
                  rocks={"by": {"kind": "field", "field": "DOM"}, "types": []},
                  slopes={"domain_field": "DOM", "domains": domains})  # fmt: skip
    assert sg.read_fields(p) == ["AU", "CU", "DOM", "FRAC"]
    rules = {"by": {"kind": "rules", "rules": [{"name": "Богатая", "rules": {"expr": "N > 2"}}]}, "types": []}
    q = params_of(w, rocks=rules)
    assert sg.read_fields(q) == ["AU", "N", "FRAC"] and sg.rock_rules(q) == [{"expr": "N > 2"}]
    assert sg.read_fields({}) == ["FRAC"] and sg.rock_rules({}) == []


# --- PostgreSQL (BF_TEST_PG; SQLite always) -------------------------------------------------------------------------


@pytest.mark.db
def test_the_store_on_both_databases(db_url: str, tmp_path: Path) -> None:
    w = world(tmp_path, db_url)
    try:
        ident = make(w, "Карьер")
        other = new(w, "Карьер")
        assert rd(w, st.info, other)["name"] == "Карьер (2)" and not rd(w, st.info, other)["has_result"]
        got = rd(w, st.info, ident, store=w.store)
        assert (got["state"], got["version"], got["has_result"]) == ("ready", 2, True)
        assert got["params"]["model_id"] == w.model
        assert rd(w, st.read_shells, w.store, ident) == shells_of()
        assert sd.table_benches(rd(w, st.read_benches, w.store, ident))["n"] == BENCHES["n"]
        assert rd(w, st.read_template, w.store, ident) == TEMPLATE and rd(w, st.read_control, w.store, ident) == CONTROL
        with dialect.write_tx(w.project.engine) as conn:
            st.mark_running(conn, ident)
        assert rd(w, st.list_scenarios)[0]["state"] == "running"
        with dialect.write_tx(w.project.engine) as conn:
            st.settle(conn, ident)
            assert st.update_params(conn, ident, 2, params=params_of(w, rf=[1.0]))["params_changed"] is True
            st.write_result(conn, w.store, ident, built(w, shells_of(2), rf=[0.9, 1.1]))
            assert st.rename(conn, ident, "Новое", 4)["version"] == 5
        assert [(r["name"], r["shells"]) for r in rd(w, st.list_scenarios)] == [("Карьер (2)", 0), ("Новое", 2)]
        with dialect.write_tx(w.project.engine) as conn:
            st.delete(conn, ident)
            st.delete(conn, other)
        assert rd(w, st.list_scenarios) == []
        with dialect.read_tx(w.project.engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(lineage).where(
                lineage.c.dst_kind == "object:pit_scenario")).scalar() == 0
    finally:
        w.project.engine.dispose()
