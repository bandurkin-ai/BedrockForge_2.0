"""The jump from a cell of the resource report to its blocks (подшаг 20.9; ТЗ 7.35 Д-36, ТД36-1; ADR 0027 решения
22–24, 30; docs/progress/e20-api.md §8): the tree of the rules of every cell of a report (the keys, the bench by share,
«(пусто)», the cut-off, the balance and the off-balance, «без борта»), the numbers of the cell = the row of the table
(blocks, volume, tonnes to 1e−12), the filter of the tree passes exactly the blocks of the cell, the table of the
blocks by pages (Σ of the pages = the cell, the same set again, any portion of the file, a model with sub-blocks), the
CSV of a page, `stale`, the answers 404 / 409 / 422 in Russian, the work in a project «только просмотр»."""

# ruff: noqa: F811 - the fixture `ra` is imported, and the tests take it by its name
from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.blocks import restrict
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.resources import drill as dr
from bf_core.resources import spec
from bf_core.resources import store as st
from tests.resapi_world import RS, ok, ra, read_only, refused, run  # noqa: F401 - the fixture
from tests.test_blocks_report import bf_blocks, bf_model, put

GRID = {"corner": [100.0, 200.0, 0.0], "counts": [6, 5, 4], "size": [10.0, 10.0, 5.0]}  # 120 blocks of 500 m³
CLASSES = ["Измеренные", "Выявленные", "Предполагаемые"]


def world(c: Any, seed: int = 5) -> int:
    """120 blocks: AU with gaps, the density SG with three gaps, CLASS (some blocks have none), DOM."""
    rng = np.random.default_rng(seed)
    n = 120
    au = rng.lognormal(0.0, 0.8, n)
    au[rng.random(n) < 0.1] = np.nan
    sg = rng.uniform(2.3, 3.0, n)
    sg[[3, 50, 77]] = np.nan
    fields = [{"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"},
              {"name": "SG", "dtype": "f8", "role": "density"},
              {"name": "CLASS", "dtype": "cat", "labels": CLASSES, "role": "class"},
              {"name": "DOM", "dtype": "cat", "labels": ["Окисл", "Сульф"]}]  # fmt: skip
    values = {"AU": au, "SG": sg, "CLASS": rng.integers(-1, 3, n).astype(np.int32),
              "DOM": rng.integers(0, 2, n).astype(np.int32)}  # fmt: skip
    return put(c, GRID, fields, np.arange(n, dtype=np.uint64), values, name="Мир")


def params(mid: int, **over: Any) -> dict[str, Any]:
    base = {"model_id": mid, "elements": ["AU"], "cutoffs": [None, 0.5, 1.0],
            "balance": {"cutoff": 1.0, "off_cutoff": 0.5}, "density": "SG", "groups": ["CLASS", {"kind": "bench"}],
            "bench": {"h": 7.5, "z_max": 15.0}, "curve": 0}  # fmt: skip
    return {**base, **over}


def close(a: float, b: float, rel: float = 1e-12) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b), 1.0)


def cell_of(row: dict[str, Any]) -> dict[str, Any]:
    return {"group": None if row["total"] else row["g"], "cutoff": row["cutoff"], "kind": row["kind"]}


def post(c: Any, rid: int, tail: str, body: dict[str, Any]) -> Any:
    return c.post(f"{RS}/reports/{rid}/{tail}", json=body)


def pages(c: Any, rid: int, cell: dict[str, Any], size: int = 1000) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every page of the table of the cell (the first answer is returned for its `cell` and `total`)."""
    first = ok(post(c, rid, "drill/rows", {**cell, "page": 1, "size": size}))
    rows = list(first["rows"])
    page = 1
    while len(rows) < first["total"]:
        page += 1
        got = ok(post(c, rid, "drill/rows", {**cell, "page": page, "size": size}))
        assert got["rows"] and got["cell"] == first["cell"] and got["total"] == first["total"]
        rows += got["rows"]
    return rows, first


# --- ТД36-1: every cell of the report -------------------------------------------------------------------------------


def test_td36_1_every_cell_is_the_row_of_the_table_and_its_filter_passes_its_blocks(ra) -> None:  # type: ignore[no-untyped-def]
    mid = world(ra)
    res = run(ra, params(mid), save=True)
    rid, rows = res["report_id"], res["rows"]
    assert res["rows_total"] == len(rows) > 40
    kinds = {r["kind"] for r in rows}
    assert kinds == {"all", "cutoff", "balance", "offbalance"}
    empties = 0
    for r in rows:
        a = ok(post(ra, rid, "drill", cell_of(r)))
        c = a["cell"]
        assert c["blocks"] == r["blocks"], (r["g"], r["kind"], r["cutoff"], c, r["blocks"])
        assert close(c["volume"], r["volume"]) and close(c["tonnes"], r["tonnes"]), (r["g"], r["kind"], c, r)
        assert a["model_id"] == mid and a["stale"] is False and a["stale_reason"] is None
        # the filter of 3D (`blocks.filter {restrict: rules}`) passes exactly the blocks of the cell
        got = restrict.test_rules(ra.project, mid, a["rules"])
        assert got["passed"] == c["blocks"], (r["g"], r["kind"], r["cutoff"], a["rules"])
        empties += r["blocks"] == 0
    assert empties < len(rows)  # the model fills most cells (the empty ones are checked too)


def test_the_rules_of_a_cell(ra) -> None:  # type: ignore[no-untyped-def]
    mid = world(ra)
    rid = run(ra, params(mid, restrict={"cond": {"field": "DOM", "op": "=", "value": "Окисл"}}), save=True)["report_id"]
    tree = ok(post(ra, rid, "drill", {"group": ["Выявленные", "+7,5…+15"], "cutoff": 1.0}))["rules"]
    items = tree["items"]
    assert tree["op"] == "and" and items[0] == {"cond": {"field": "DOM", "op": "=", "value": "Окисл"}}
    assert items[1] == {"cond": {"field": "CLASS", "op": "=", "value": "Выявленные"}}
    assert items[2] == {"slab": {"axis": "z", "from": 7.5, "to": 15.0, "by": "share"}}
    assert items[3] == {"expr": "AU >= 1"} and len(items) == 4, items  # no block of this cell lacks the density
    empty = ok(post(ra, rid, "drill", {"group": ["(пусто)", "(пусто)"], "cutoff": None}))["rules"]["items"]
    assert {"cond": {"field": "CLASS", "op": "empty"}} in empty
    assert {"op": "not", "items": [{"slab": {"axis": "z", "from": 0.0, "to": 15.0, "by": "share"}}]} in empty
    bal = ok(post(ra, rid, "drill", {"group": None, "kind": "balance", "cutoff": 1.0}))["rules"]["items"]
    off = ok(post(ra, rid, "drill", {"group": None, "kind": "offbalance", "cutoff": 0.5}))["rules"]["items"]
    assert {"expr": "AU >= 1"} in bal and {"expr": "AU >= 0.5 AND AU < 1"} in off
    total = ok(post(ra, rid, "drill", {"group": None, "cutoff": None}))["rules"]["items"]
    assert total[0] == {"cond": {"field": "DOM", "op": "=", "value": "Окисл"}}  # only the restrictor of the report
    again = ok(post(ra, rid, "drill", {"group": ["Выявленные", "+7,5…+15"], "cutoff": 1.0}))["rules"]
    assert again == tree  # the same inputs — the same tree


def test_a_cell_by_numpy_a_bench_takes_the_share_of_the_block(ra) -> None:  # type: ignore[no-untyped-def]
    mid = world(ra)
    rid = run(ra, params(mid), save=True)["report_id"]
    with dialect.read_tx(ra.project.engine) as conn:
        from bf_core.blocks.model import Model

        model = Model.open(conn, ra.store, mid)
        cols = model.columns(["AU", "SG", "CLASS"])
    zlo = np.repeat(np.arange(4) * 5.0, 30)  # the layer of a block: z = 5·k (P = i + 6·(j + 5·k))
    share = np.clip(np.minimum(zlo + 5.0, 15.0) - np.maximum(zlo, 7.5), 0.0, None) / 5.0
    ok_ = (cols["CLASS"] == 1) & (cols["AU"] >= 1.0) & np.isfinite(cols["SG"])
    vol = float((500.0 * share[ok_]).sum())
    tonnes = float((500.0 * share[ok_] * cols["SG"][ok_]).sum())
    c = ok(post(ra, rid, "drill", {"group": ["Выявленные", "+7,5…+15"], "cutoff": 1.0}))["cell"]
    assert c["blocks"] == int((ok_ & (share > 0)).sum()) > 0
    assert close(c["volume"], vol) and close(c["tonnes"], tonnes)


# --- the table of the blocks ----------------------------------------------------------------------------------------


def test_the_pages_add_up_to_the_cell_and_a_repeat_gives_the_same_set(ra) -> None:  # type: ignore[no-untyped-def]
    mid = world(ra)
    rid = run(ra, params(mid), save=True)["report_id"]
    rows = run(ra, params(mid))["rows"]  # the same report as a preview: the cells to take
    checked = 0
    for r in rows[::4]:
        cell = cell_of(r)
        every, first = pages(ra, rid, cell)
        by_seven, _ = pages(ra, rid, cell, size=7)
        assert by_seven == every and first["total"] == len(every) == first["cell"]["blocks"]
        assert close(math.fsum(x["volume"] for x in every), first["cell"]["volume"])
        assert close(math.fsum(x["tonnes"] for x in every), first["cell"]["tonnes"])
        again, second = pages(ra, rid, cell)
        assert json.dumps(again) == json.dumps(every) and second["cell"] == first["cell"]
        assert first["rules"] == ok(post(ra, rid, "drill", cell))["rules"]
        assert [x["f"] for x in every] == [x["f"] for x in by_seven]
        checked += len(every)
    assert checked > 100


def test_a_row_of_the_table(ra) -> None:  # type: ignore[no-untyped-def]
    mid = world(ra)
    rid = run(ra, params(mid, groups=["CLASS"], bench=None), save=True)["report_id"]
    got = ok(post(ra, rid, "drill/rows", {"group": ["Измеренные"], "cutoff": 1.0, "page": 1, "size": 1000}))
    assert got["csv"] is None and got["page"] == 1 and got["size"] == 1000 and got["total"] == len(got["rows"]) > 0
    r = got["rows"][0]
    assert sorted(r) == ["dx", "dy", "dz", "elements", "f", "i", "j", "k", "keys", "tonnes", "volume", "x", "y", "z"]
    assert (r["dx"], r["dy"], r["dz"], r["f"], r["volume"]) == (10.0, 10.0, 5.0, 1.0, 500.0)
    assert r["keys"] == {"CLASS": "Измеренные"} and r["elements"]["AU"] >= 1.0
    assert (r["x"], r["y"], r["z"]) == (105.0 + 10.0 * r["i"], 205.0 + 10.0 * r["j"], 2.5 + 5.0 * r["k"])
    assert close(r["tonnes"], 500.0 * 2.3) or r["tonnes"] > 500.0 * 2.2  # the density SG of the block
    # the table of the total shows the real key of each block, the bench among them
    rid2 = run(ra, params(mid), save=True, name="С уступами")["report_id"]
    total = ok(post(ra, rid2, "drill/rows", {"group": None, "cutoff": 1.0, "page": 1, "size": 5}))
    assert [sorted(x["keys"]) for x in total["rows"]] == [["CLASS", "Уступ"]] * 5
    assert {x["keys"]["Уступ"] for x in total["rows"]} <= {"0…+7,5", "+7,5…+15", "(пусто)"}


def test_the_page_beyond_the_end_and_the_csv(ra) -> None:  # type: ignore[no-untyped-def]
    mid = world(ra)
    rid = run(ra, params(mid, groups=["CLASS"], bench=None), save=True)["report_id"]
    cell = {"group": ["Измеренные"], "cutoff": 1.0}
    total = ok(post(ra, rid, "drill/rows", {**cell, "page": 1, "size": 1000}))["total"]
    gone = ok(post(ra, rid, "drill/rows", {**cell, "page": 5, "size": 1000}))
    assert gone["rows"] == [] and gone["total"] == total and gone["csv"] is None
    two = ok(post(ra, rid, "drill/rows", {**cell, "page": 1, "size": 2, "csv": True}))
    lines = two["csv"].split("\r\n")
    assert (
        lines[-1] == "" and len(lines) == 1 + 2 + 1 and lines[0] == "i;j;k;X;Y;Z;dX;dY;dZ;Доля;Объём, м³;Тонны;CLASS;AU"
    )
    first = two["rows"][0]
    cells = lines[1].split(";")
    assert cells[:3] == [str(first["i"]), str(first["j"]), str(first["k"])] and cells[12] == "Измеренные"
    assert cells[3] == f"{first['x']:g}".replace(".", ",") and cells[9] == "1"
    assert "." not in lines[1].split("Измеренные")[0]
    assert float(cells[13].replace(",", ".")) == first["elements"]["AU"]


def test_a_model_with_sub_blocks_and_any_portion_of_the_file(ra) -> None:  # type: ignore[no-untyped-def]
    m = bf_blocks()
    mid = bf_model(ra, m)
    body = {"model_id": mid, "elements": ["AU_CUT"], "cutoffs": [None, 0.5], "density": "SG",
            "groups": ["ALT", {"kind": "bench"}], "bench": {"h": 2.0}, "curve": 0}  # fmt: skip
    res = run(ra, body, save=True)
    rid, rows = res["report_id"], res["rows"]
    assert res["rows_total"] == len(rows) and any(r["blocks"] for r in rows)
    with dialect.read_tx(ra.project.engine) as conn:
        for r in rows:
            d = spec.parse_drill({**cell_of(r), "report_id": rid})
            whole = dr.answer(conn, ra.store, d)
            assert whole["cell"]["blocks"] == r["blocks"], (r["g"], r["cutoff"])
            assert close(whole["cell"]["volume"], r["volume"]) and close(whole["cell"]["tonnes"], r["tonnes"])
            for chunk in (1, 7):  # a portion of the file does not change a bit
                assert dr.answer(conn, ra.store, d, chunk=chunk) == whole
        d2 = spec.parse_drill_rows({**cell_of(rows[3]), "report_id": rid, "page": 1, "size": 1000})
        for chunk in (1, 5):
            assert dr.rows_answer(conn, ra.store, d2, chunk=chunk) == dr.rows_answer(conn, ra.store, d2)
    # the filter passes the blocks (rows) of the cell: with sub-blocks the cell counts rows of the model
    for r in rows[::3]:
        a = ok(post(ra, rid, "drill", cell_of(r)))
        assert restrict.test_rules(ra.project, mid, a["rules"])["passed"] == r["blocks"]


def test_the_blocks_of_a_cell_without_density_are_out_of_the_filter(ra) -> None:  # type: ignore[no-untyped-def]
    mid = world(ra)
    rid = run(ra, params(mid, groups=[], bench=None, cutoffs=[None]), save=True)["report_id"]
    a = ok(post(ra, rid, "drill", {"group": None, "cutoff": None}))
    assert a["cell"]["blocks"] == 117 and a["rules"] == {"expr": "SG >= 0"}  # 120 blocks, 3 with no density
    assert restrict.test_rules(ra.project, mid, a["rules"])["passed"] == 117
    rows, first = pages(ra, rid, {"group": None, "cutoff": None})
    assert len(rows) == 117 and first["total"] == 117
    assert {3, 50, 77}.isdisjoint({x["i"] + 6 * (x["j"] + 5 * x["k"]) for x in rows})


# --- stale ----------------------------------------------------------------------------------------------------------


def test_stale_when_the_graph_says_so_or_the_model_has_changed(ra, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mid = world(ra)
    rid = run(ra, params(mid), save=True)["report_id"]
    cell = {"group": ["Измеренные", "(пусто)"], "cutoff": 0.5}
    assert ok(post(ra, rid, "drill", cell))["stale"] is False
    with dialect.write_tx(ra.project.engine) as conn:  # the model is edited (a new version)
        conn.execute(sa.update(objects).where(objects.c.id == mid).values(version=objects.c.version + 1))
    a = ok(post(ra, rid, "drill", cell))
    assert a["stale"] is True and "Блочная модель изменилась" in a["stale_reason"]
    assert a["cell"]["blocks"] >= 0 and ok(post(ra, rid, "drill/rows", {**cell, "page": 1, "size": 3}))["stale"] is True
    monkeypatch.undo()

    def graph(_conn: Any, ids: Any, store: Any = None) -> dict[int, dict[str, Any]]:
        return {i: {"stale": True, "stale_reason": "изменено: таблица «Пробы»", "source_lost": False, "pinned": False}
                for i in ids}  # fmt: skip

    mid2 = world(ra, seed=9)
    rid2 = run(ra, params(mid2), save=True, name="Второй")["report_id"]
    monkeypatch.setattr(st, "graph_of", graph)
    b = ok(post(ra, rid2, "drill", cell))
    assert b["stale"] is True and b["stale_reason"] == "изменено: таблица «Пробы»"


# --- the answers -----------------------------------------------------------------------------------------------------


def test_the_refusals_are_russian_and_the_jump_works_read_only(ra) -> None:  # type: ignore[no-untyped-def]
    mid = world(ra)
    rid = run(ra, params(mid, balance=None, cutoffs=[0.5, 1.0]), save=True)["report_id"]
    cell = {"group": ["Измеренные", "0…+7,5"], "cutoff": 1.0}
    refused(post(ra, 99_999, "drill", cell), 404, "resource_report_not_found")
    refused(post(ra, 99_999, "drill/rows", cell), 404, "resource_report_not_found")
    refused(post(ra, rid, "drill", {**cell, "group": ["Измеренные"]}), 422, "bad_param", "group")
    refused(post(ra, rid, "drill", {**cell, "group": ["Х", "0…+7,5"]}), 422, "bad_param", "group.0")
    refused(post(ra, rid, "drill", {**cell, "group": ["Измеренные", "+100…+110"]}), 422, "bad_param", "group.1")
    refused(post(ra, rid, "drill", {**cell, "cutoff": 3.0}), 422, "bad_param", "cutoff")
    refused(post(ra, rid, "drill", {"group": None, "cutoff": None}), 422, "bad_param", "cutoff")  # no «без борта»
    refused(post(ra, rid, "drill", {**cell, "kind": "balance"}), 422, "bad_param", "kind")
    refused(post(ra, rid, "drill", {**cell, "kind": "nope"}), 422, "bad_param", "kind")
    refused(post(ra, rid, "drill/rows", {**cell, "size": 1001}), 422, "bad_param", "size")
    with dialect.write_tx(ra.project.engine) as conn:
        st.mark_building(conn, rid)
    refused(post(ra, rid, "drill", cell), 409, "report_building")
    with dialect.write_tx(ra.project.engine) as conn:
        st.settle(conn, rid)
    back = read_only(ra)
    try:
        assert ok(post(ra, rid, "drill", cell))["cell"]["blocks"] >= 0
        assert ok(post(ra, rid, "drill/rows", {**cell, "page": 1, "size": 10}))["total"] >= 0
    finally:
        back()


def test_the_tree_is_made_of_its_items() -> None:
    assert dr.join([]) is None
    one = {"cond": {"field": "A", "op": "=", "value": "x"}}
    assert dr.join([one]) == one
    two = dr.join([one, {"expr": "AU >= 1"}])
    assert two == {"op": "and", "items": [one, {"expr": "AU >= 1"}]}


@pytest.mark.parametrize("what", ["drill", "drill/rows"])
def test_the_body_of_a_cell_is_checked_before_the_report(ra, what: str) -> None:  # type: ignore[no-untyped-def]
    refused(post(ra, 1, what, {"group": ["А"], "cutoff": "x"}), 422, "bad_param", "cutoff")


def test_the_table_of_layers_gives_the_shares_of_the_generic_restrictor(ra) -> None:  # type: ignore[no-untyped-def]
    mid = world(ra)
    rid = run(ra, params(mid), save=True)["report_id"]
    with dialect.read_tx(ra.project.engine) as conn:
        for group in (["Выявленные", "+7,5…+15"], ["Выявленные", "(пусто)"], ["(пусто)", "0…+7,5"]):
            cell = dr.open_cell(conn, ra.store, spec.parse_drill({"report_id": rid, "group": group, "cutoff": 0.5}))
            fast = dr._restrictor(cell, cell.items, conn, ra.store)  # noqa: SLF001
            slow = restrict.evaluate(cell.model, dr.join(cell.items), conn=conn, store=ra.store)
            got = np.concatenate([f for _, f in fast.iter(cell.model, chunk=7)])
            assert got.tobytes() == slow.tobytes() and 0.0 < float(got.max()) <= 1.0, group
