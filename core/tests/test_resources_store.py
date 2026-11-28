"""The resource reports in the project (подшаг 20.6; ADR 0027 решения 14–16; docs/progress/e20-api.md §7): the object
`resource_report` with its files (`data`, `sidecar:curve`, `sidecar:control`, `sidecar:xlsx`), the state
«идёт построение» in `meta` only, the atomic write (a cancel leaves nothing), reading by pages, rename, delete,
«Перестроить» in place, the same on PostgreSQL (`-m db`, BF_TEST_PG). The rows are made here by hand (the result of
`resources/result.py` is 20.3); the graph — `test_lineage_resources.py`."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, lineage, objects, valid_blob_role
from bf_core.resources import spec
from bf_core.resources import store as st
from bf_core.resources import store_data as sd
from tests.estutil import W, world

ELEMENTS = ["AU", "CU"]
GRID = {"corner": [1000.5, 2000.25, -300.0], "counts": [10, 8, 6], "size": [5.0, 5.0, 2.5], "rotation": 30}


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


# --- the rows ---------------------------------------------------------------------------------------------------------


def part(wt: float | None, total: float | None, k: float = 1.0, mt: float = 0.0, mn: int = 0) -> dict[str, Any]:
    """An element of a row: tonnes with the grade, Σ t·g, the grade (null when no tonnes), the metal, the gaps."""
    return {"wt": wt, "sum": total, "grade": None if not wt else total / wt,  # type: ignore[operator]
            "metal": None if total is None else total * k, "missing_t": mt, "missing_n": mn}  # fmt: skip


def row(g: list[str] | None, kind: str, cutoff: float | None, blocks: int, volume: float, tonnes: float,
        au: dict[str, Any], cu: dict[str, Any], total: bool = False,
        cutoff_to: float | None = None) -> dict[str, Any]:  # fmt: skip
    return {"g": g, "total": total, "kind": kind, "cutoff": cutoff, "cutoff_to": cutoff_to, "blocks": blocks,
            "volume": volume, "tonnes": tonnes, "elements": {"AU": au, "CU": cu}}  # fmt: skip


ROWS = [
    row(None, "all", None, 9, 9000.0, 22500.0, part(20000.0, 30000.0, mt=2500.0, mn=1), part(22500.0, 11000.0, 0.01),
        total=True),
    row(["руда"], "all", None, 5, 5000.0, 12500.0, part(12500.0, 25000.0), part(12500.0, 6000.0, 0.01)),
    row(["порода"], "all", None, 3, 3000.0, 7500.0, part(7500.0, 5000.0), part(7500.0, 4000.0, 0.01)),
    row(["(пусто)"], "all", None, 1, 1000.0, 2500.0, part(0.0, 0.0, mt=2500.0, mn=1), part(2500.0, 1000.0, 0.01)),
    row(None, "cutoff", 1.0, 4, 4000.0, 10000.0, part(10000.0, 22000.0), part(10000.0, 5000.0, 0.01), total=True),
    row(["руда"], "cutoff", 1.0, 4, 4000.0, 10000.0, part(10000.0, 22000.0), part(10000.0, 5000.0, 0.01)),
]
CURVE = {"field": "AU", "cutoffs": [0.2, 0.6, 1.0], "blocks": [9, 7, 4], "volume": [9000.0, 7000.0, 4000.0],
         "tonnes": [22500.0, 17500.0, 10000.0],
         "elements": {"AU": {"grade": [1.5, 1.8, None], "metal": [30000.0, 28000.0, None]},
                      "CU": {"grade": [0.5, 0.5, 0.5], "metal": [110.0, 90.0, 50.0]}}}  # fmt: skip
CONTROL = {"tolerance": 1e-12, "sum_groups": {"tonnes": {"sum": 22500.0, "total": 22500.0, "rel": 0.0}, "ok": True},
           "no_density": {"blocks": 0, "volume": 0.0}, "warnings": []}  # fmt: skip
TOTALS = {"kind": "all", "cutoff": None, "blocks": 9, "volume": 9000.0, "tonnes": 22500.0,
          "elements": {"AU": {"grade": 1.5, "metal": 30000.0}, "CU": {"grade": 0.49, "metal": 110.0}}}  # fmt: skip


# --- building a report ------------------------------------------------------------------------------------------------


def params_of(w: W, **over: Any) -> dict[str, Any]:
    body = {"model_id": w.model, "elements": [{"field": "AU", "unit": "g/t"}, {"field": "CU", "unit": "%"}],
            "density": 2.5, "groups": ["DOM"], "cutoffs": [None, 1.0], **over}  # fmt: skip
    with dialect.read_tx(w.project.engine) as conn:
        fields = Model.open(conn, w.store, w.model).fields
    return spec.parse_params(body).resolved(fields).canonical()


def built(w: W, rows: list[dict[str, Any]] | None = None, curve: bool = True,
          fields: tuple[str, ...] = ("AU", "CU", "DOM"), **over: Any) -> st.Built:  # fmt: skip
    table = sd.rows_table(rows or ROWS, ELEMENTS, 1)
    staged = st.stage(w.store, table, sd.curve_table(CURVE) if curve else None, CONTROL)
    return st.Built(params_of(w, **over), {"id": w.model, "name": "Модель", "grid": GRID}, fields, TOTALS, staged,
                    {"numpy": "2"})  # fmt: skip


def make(w: W, name: str = "Отчёт", **kw: Any) -> dict[str, Any]:
    b = built(w, **kw)
    with dialect.write_tx(w.project.engine) as conn:
        return st.create(conn, w.store, name, b)


def rd(w: W, fn: Any, *args: Any, **kw: Any) -> Any:
    with dialect.read_tx(w.project.engine) as conn:
        return fn(conn, *args, **kw)


def refused(fn: Any, status: int, code: str) -> ApiError:
    with pytest.raises(ApiError) as e:
        fn()
    assert (e.value.status, e.value.code) == (status, code), e.value
    return e.value


def roles(w: W, ident: int) -> set[str]:
    with dialect.read_tx(w.project.engine) as conn:
        return set(conn.execute(sa.select(blob_owners.c.role).where(blob_owners.c.owner_id == ident,
                                                                    blob_owners.c.owner_kind == "object")).scalars())


def table_sha(w: W, ident: int) -> str:
    return rd(w, st.data_sha, ident)


# --- the Parquet of the rows ------------------------------------------------------------------------------------------


def test_the_columns_are_those_of_the_adr() -> None:
    names = [n for n, _ in sd.row_columns(ELEMENTS, 2)]
    assert names[:2] == ["g1", "g2"] and names[2:9] == ["total", "kind", "cutoff", "cutoff_to", "blocks", "volume",
                                                         "tonnes"]  # fmt: skip
    assert names[9:15] == [f"AU:{p}" for p in ("wt", "sum", "metal", "grade", "missing_t", "missing_n")]
    assert len(names) == 9 + 12 and names[-1] == "CU:missing_n"
    assert [n for n, _ in sd.row_columns([], 0)][:2] == ["total", "kind"]  # no keys: no g columns


def test_the_rows_come_back_as_they_went_and_a_gap_is_null() -> None:
    table = sd.rows_table(ROWS, ELEMENTS, 1)
    assert table.num_rows == len(ROWS) and sd.keys_of(table) == 1 and sd.elements_of(table) == ELEMENTS
    back = sd.table_rows(table)
    assert back == ROWS  # null in the total row g, null cutoff, null grade of the empty group
    assert back[3]["elements"]["AU"]["grade"] is None  # wt = 0: the grade is empty, not 0
    nan = [{**ROWS[1], "elements": {"AU": {**ROWS[1]["elements"]["AU"], "grade": math.nan, "metal": math.nan},
                                    "CU": ROWS[1]["elements"]["CU"]}}]  # fmt: skip
    got = sd.table_rows(sd.rows_table(nan, ELEMENTS, 1))[0]["elements"]["AU"]
    assert got["grade"] is None and got["metal"] is None and got["wt"] == 12500.0  # NaN is a null, not 0
    nokey = sd.table_rows(sd.rows_table([{**ROWS[0], "g": []}], ELEMENTS, 0))
    assert nokey[0]["g"] == [] and nokey[0]["total"] is True


def test_the_table_of_arrays_and_its_refusals() -> None:
    import numpy as np

    cols: dict[str, Any] = {"total": [True, False], "kind": ["all", "all"], "cutoff": [np.nan, 0.5],
                            "cutoff_to": [None, None], "blocks": np.array([3, 2]), "volume": np.array([1.0, 2.0]),
                            "tonnes": [2.5, 5.0], "AU:wt": [1.0, 2.0], "AU:sum": [1.0, 2.0], "AU:metal": [1.0, np.nan],
                            "AU:grade": [1.0, np.nan], "AU:missing_t": [0.0, 0.0], "AU:missing_n": [0, 0]}  # fmt: skip
    table = sd.build_table(cols, ["AU"], 0)
    rows = sd.table_rows(table)
    assert [r["cutoff"] for r in rows] == [None, 0.5] and rows[1]["elements"]["AU"]["grade"] is None
    with pytest.raises(ValueError, match="no columns"):
        sd.build_table({k: v for k, v in cols.items() if k != "tonnes"}, ["AU"], 0)
    inf = sd.build_table({**cols, "volume": [math.inf, 1.0]}, ["AU"], 0)  # ±inf is a gap too: a null (20.18б, № 7)
    assert [r["volume"] for r in sd.table_rows(inf)] == [None, 1.0]


def test_the_same_rows_are_the_same_bytes(w: W) -> None:
    a = sd.stage_parquet(w.store, sd.rows_table(ROWS, ELEMENTS, 1))
    b = sd.stage_parquet(w.store, sd.rows_table(ROWS, ELEMENTS, 1))
    c = sd.stage_parquet(w.store, sd.rows_table(ROWS[:-1], ELEMENTS, 1))
    assert a[1] == b[1] != c[1]  # «Перестроить» gives the same `data`: the same SHA-256
    for f in (a, b, c):
        f[0].unlink()


def test_a_page_reads_only_its_rows_across_row_groups(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sd, "ROW_GROUP", 4)  # 6 rows: groups of 4 + 2
    ident = make(w)["id"]
    page1 = rd(w, st.read_rows, w.store, ident, 1, 4)
    page2 = rd(w, st.read_rows, w.store, ident, 2, 4)
    assert (page1["rows_total"], len(page1["rows"]), len(page2["rows"])) == (6, 4, 2)
    assert page1["rows"] + page2["rows"] == ROWS
    mid = rd(w, st.read_rows, w.store, ident, 2, 2)  # rows 2, 3 — inside the first group
    assert mid["rows"] == ROWS[2:4]
    assert rd(w, st.read_rows, w.store, ident, 9, 4)["rows"] == []


# --- create, read ----------------------------------------------------------------------------------------------------


def test_create_writes_the_object_the_files_and_the_graph_in_one_go(w: W) -> None:
    out = make(w, "Ресурсы ЮЗ")
    ident = out["id"]
    assert out == {"id": ident, "version": 1, "name": "Ресурсы ЮЗ"}
    row_ = rd(w, lambda c: c.execute(sa.select(objects).where(objects.c.id == ident)).one())
    assert (row_.kind, row_.parent_id, row_.state, row_.version) == ("resource_report", None, "ready", 1)
    assert roles(w, ident) == {"data", "sidecar:curve", "sidecar:control"}
    assert all(valid_blob_role(r) for r in roles(w, ident))  # the existing roles: no migration
    meta = row_.meta
    assert (meta["v"], meta["algo"], meta["state"], meta["rows"], meta["curve"]) == (1, "resources-1", "ready", 6, 3)
    assert meta["params_hash"] == spec.parse_params(meta["params"]).params_hash() and meta["totals"] == TOTALS
    assert meta["model"]["id"] == w.model and meta["model"]["cells_sha"] and meta["model"]["grid"] == GRID
    assert sorted(meta["fields"]) == ["AU", "CU", "DOM"] and all(meta["fields"].values())
    kinds = [(i["kind"], i["id"]) for i in meta["inputs"]]
    assert kinds == [("object:block_model", w.model), ("bm_field", f"{w.model}:AU"), ("bm_field", f"{w.model}:CU"),
                     ("bm_field", f"{w.model}:DOM")]  # fmt: skip
    assert all(i["fp"] for i in meta["inputs"]) and meta["inputs"][1]["name"] == "Модель.AU"
    with dialect.read_tx(w.project.engine) as conn:  # the same in the table of the graph
        edges = conn.execute(sa.select(lineage.c.src_kind, lineage.c.src_sub).where(
            lineage.c.dst_kind == "object:resource_report", lineage.c.dst_id == ident).order_by(lineage.c.pos)).all()
    assert [(k, s) for k, s in edges] == [("object:block_model", ""), ("bm_field", "au"), ("bm_field", "cu"),
                                          ("bm_field", "dom")]  # fmt: skip


def test_read_the_files_back(w: W) -> None:
    ident = make(w)["id"]
    assert sd.table_rows(rd(w, st.read_table, w.store, ident)) == ROWS
    assert rd(w, st.read_curve, w.store, ident) == CURVE
    assert rd(w, st.read_control, w.store, ident) == CONTROL
    got = rd(w, st.info, ident, store=w.store)
    assert (got["name"], got["version"], got["state"], got["rows"], got["totals"]) == ("Отчёт", 1, "ready", 6, TOTALS)
    assert got["params"]["model_id"] == w.model and got["graph"]["stale"] is False and got["size"] > 0
    assert got["model"]["name"] == "Модель" and got["files"] == [] and got["xlsx"] is False
    only = make(w, "Без кривой", curve=False)["id"]
    assert rd(w, st.read_curve, w.store, only) is None and "sidecar:curve" not in roles(w, only)


def test_the_list_has_the_totals_and_the_state_in_the_graph(w: W) -> None:
    a, b = make(w, "Б отчёт")["id"], make(w, "А отчёт")["id"]
    got = rd(w, st.list_reports)
    assert [r["name"] for r in got] == ["А отчёт", "Б отчёт"] and [r["id"] for r in got] == [b, a]
    first = got[0]
    assert first["model"] == {"id": w.model, "name": "Модель"} and first["totals"] == TOTALS
    assert first["graph"] == {"stale": False, "stale_reason": None, "source_lost": False, "pinned": False}
    assert first["state"] == "ready" and first["files"] == [] and first["created_at"] and first["size"] > 0


def test_names_are_made_free_and_the_number_is_limited(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    assert [make(w, "Ресурсы")["name"] for _ in range(3)] == ["Ресурсы", "Ресурсы (2)", "Ресурсы (3)"]
    assert make(w, "  ресурсы  ")["name"] == "ресурсы (4)"  # the key is NFC + casefold
    assert make(w, None)["name"] == "Отчёт по ресурсам"  # type: ignore[arg-type]
    monkeypatch.setattr(st, "MAX_REPORTS", 5)
    b = built(w)
    with dialect.write_tx(w.project.engine) as conn:
        err = refused(lambda: st.create(conn, w.store, "Лишний", b), 422, "too_many_reports")
    b.staged.discard()
    assert "1000" in err.message or "5" in err.message


def counts(w: W) -> tuple[int, int, int]:
    with dialect.read_tx(w.project.engine) as conn:
        return tuple(  # type: ignore[return-value]
            int(conn.execute(sa.select(sa.func.count()).select_from(t)).scalar_one())
            for t in (objects, blob_owners, lineage)
        )


def test_a_failure_in_the_write_leaves_nothing(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    """The write is one transaction: a failure after the object, the files and the owners are in (here the graph)
    takes them all back; the staged files go with `discard`, the files placed are strays for `storage.gc`."""
    before = counts(w)
    files = {p.name for p in w.store.root.glob("??/??/*")}
    b = built(w)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise ApiError(404, "lineage_node_not_found", "Объект не найден.", "Обновите дерево проекта.")

    monkeypatch.setattr(st, "_record", boom)
    with pytest.raises(ApiError):
        with dialect.write_tx(w.project.engine) as conn:
            st.create(conn, w.store, "Не будет", b)
    b.staged.discard()
    assert counts(w) == before and rd(w, st.list_reports) == []
    assert not list(w.store.tmp_dir.glob("*"))  # nothing is left in the temp folder
    from bf_core.storage import gc

    gc.collect(w.project.engine, w.store, now=time.time() + 86_400)
    assert {p.name for p in w.store.root.glob("??/??/*")} == files  # the placed files are not left either


def test_a_changed_input_is_model_changed_before_any_file_is_put(w: W) -> None:
    """Э26 В5: the fingerprints are compared BEFORE the files are put (as in `mine.store.write_result`): a 409
    `model_changed` of `create` and of `replace` leaves no file in the store (no blob without an owner until `gc`)."""
    ident = make(w, "Есть")["id"]
    before, files = counts(w), {p.name for p in w.store.root.glob("??/??/*")}
    for name in ("Новый", None):
        b = built(w, rows=ROWS[:3])  # other rows: other files than the ones in the store
        prints = rd(w, st.input_prints, w.model, b.fields)
        prints[next(iter(prints))] = "x"  # an input is another than the pass read
        b.prints = prints
        with pytest.raises(ApiError) as e:
            with dialect.write_tx(w.project.engine) as conn:
                if name:
                    st.create(conn, w.store, name, b)
                else:
                    st.replace(conn, w.store, ident, b)
        assert (e.value.status, e.value.code) == (409, "model_changed") and e.value.hint
        assert not w.store.path_of(b.staged.data[1]).is_file()  # the new table is not placed
        b.staged.discard()
        assert counts(w) == before and not list(w.store.tmp_dir.glob("*"))
        assert {p.name for p in w.store.root.glob("??/??/*")} == files  # no new file, `gc` is not needed
    ok = built(w, rows=ROWS[:3])  # the same inputs as they are: written
    ok.prints = rd(w, st.input_prints, w.model, ok.fields)
    with dialect.write_tx(w.project.engine) as conn:
        assert st.replace(conn, w.store, ident, ok)["version"] == 2


def test_a_job_cancelled_before_the_write_leaves_nothing(w: W) -> None:
    before = counts(w)
    b = built(w)
    b.staged.discard()  # the job saw the cancel after the files were staged
    assert not list(w.store.tmp_dir.glob("*")) and counts(w) == before and rd(w, st.list_reports) == []


# --- rename, delete, the state ---------------------------------------------------------------------------------------


def test_rename_takes_the_version_and_a_taken_name_is_refused(w: W) -> None:
    a, b = make(w, "Первый")["id"], make(w, "Второй")["id"]
    with dialect.write_tx(w.project.engine) as conn:
        assert st.rename(conn, a, "  Новое   имя ", 1) == {"id": a, "version": 2, "name": "Новое имя"}
    with dialect.write_tx(w.project.engine) as conn:
        e = refused(lambda: st.rename(conn, a, "Ещё", 1), 409, "version_conflict")
    assert e.extra == {"version": 2}
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.rename(conn, a, "второй", 2), 409, "name_taken")
        refused(lambda: st.rename(conn, a, "   ", 2), 422, "bad_param")
        refused(lambda: st.rename(conn, 99_999, "x", 1), 404, "resource_report_not_found")
        assert st.rename(conn, a, "Новое имя", 2)["version"] == 3  # its own name is not «taken»
    assert rd(w, st.info, b)["version"] == 1  # the other is not touched
    assert sd.table_rows(rd(w, st.read_table, w.store, a)) == ROWS  # a rename does not touch the numbers


def test_delete_takes_the_files_and_the_rows_of_the_graph(w: W) -> None:
    a, b = make(w, "Один")["id"], make(w, "Два")["id"]
    sha = table_sha(w, a)
    with dialect.write_tx(w.project.engine) as conn:
        st.delete(conn, a)
        refused(lambda: st.delete(conn, a), 404, "resource_report_not_found")
    with dialect.read_tx(w.project.engine) as conn:
        assert conn.execute(sa.select(objects.c.id).where(objects.c.id == a)).first() is None
        assert conn.execute(sa.select(blob_owners.c.sha256).where(blob_owners.c.owner_id == a)).first() is None
        assert conn.execute(sa.select(lineage.c.dst_id).where(lineage.c.dst_kind == "object:resource_report",
                                                              lineage.c.dst_id == a)).first() is None
        refused(lambda: st.info(conn, a), 404, "resource_report_not_found")
    assert [r["id"] for r in rd(w, st.list_reports)] == [b] and table_sha(w, b) == sha  # the file is shared by content
    assert sd.table_rows(rd(w, st.read_table, w.store, b)) == ROWS


def test_the_state_building_is_in_the_meta_only(w: W) -> None:
    ident = make(w)["id"]
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_building(conn, ident)  # a mark without a job is trusted
    with dialect.read_tx(w.project.engine) as conn:
        row_ = conn.execute(sa.select(objects).where(objects.c.id == ident)).one()
        assert row_.state == "ready" and row_.meta["state"] == "building"  # `objects.state` stays `ready` (19.29б)
        got = st.info(conn, ident)
        assert got["state"] == "building" and rd(w, st.list_reports)[0]["state"] == "building"
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.mark_building(conn, ident), 409, "report_building")
        refused(lambda: st.rename(conn, ident, "x", 1), 409, "report_building")
        refused(lambda: st.delete(conn, ident), 409, "report_building")
        refused(lambda: st.rebuild_params(conn, ident), 409, "report_building")
    assert sd.table_rows(rd(w, st.read_table, w.store, ident)) == ROWS  # the old numbers are read while it is rebuilt
    with dialect.write_tx(w.project.engine) as conn:
        st.settle(conn, ident)  # the job was cancelled: the report is as it was
        st.settle(conn, ident)
    got = rd(w, st.info, ident)
    assert got["state"] == "ready" and got["version"] == 1 and got["job_id"] is None
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_building(conn, ident, 987_654)  # a job that is gone (a crash, a restart): the mark is not trusted
    assert rd(w, st.info, ident)["state"] == "ready"


# --- «Перестроить»: in place ------------------------------------------------------------------------------------------


def test_replace_rebuilds_in_place_and_keeps_the_name_and_the_exports(w: W) -> None:
    ident = make(w, "Ресурсы")["id"]
    sha = table_sha(w, ident)
    staged = w.store.stage_bytes(b"PK-book")
    with dialect.write_tx(w.project.engine) as conn:
        st.put_xlsx(conn, w.store, ident, staged)
        st.record_file(conn, ident, {"format": "xlsx", "path": "C:/out/a.xlsx", "sha256": "ab", "at": "t1"})
        st.record_file(conn, ident, {"format": "xlsx", "path": "C:/out/a.xlsx", "sha256": "cd", "at": "t2"})
        st.record_file(conn, ident, {"format": "csv", "path": "C:/out/a.csv", "sha256": "ef", "at": "t3"})
    got = rd(w, st.info, ident)
    assert got["xlsx"] is True
    assert [(f["path"], f["sha256"]) for f in got["files"]] == [("C:/out/a.xlsx", "cd"), ("C:/out/a.csv", "ef")]
    assert got["version"] == 1 and "sidecar:xlsx" in roles(w, ident)
    # the same params, other numbers: same id, version + 1, the old book is gone (its numbers are old), the exports stay
    b = built(w, rows=ROWS[:-1], curve=False)
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.replace(conn, w.store, ident, b, expected_version=5), 409, "version_conflict")
        assert st.replace(conn, w.store, ident, b, expected_version=1) == {"id": ident, "version": 2, "name": "Ресурсы"}
    after = rd(w, st.info, ident)
    assert (after["version"], after["name"], after["rows"], after["xlsx"]) == (2, "Ресурсы", 5, False)
    assert len(after["files"]) == 2 and after["created_at"] == got["created_at"]
    assert roles(w, ident) == {"data", "sidecar:control"} and table_sha(w, ident) != sha
    assert [r["id"] for r in rd(w, st.list_reports)] == [ident]
    # the same rows again: the same bytes (a rebuild from scratch is bit for bit)
    c = built(w, rows=ROWS[:-1], curve=False)
    with dialect.write_tx(w.project.engine) as conn:
        st.replace(conn, w.store, ident, c)
    assert rd(w, st.info, ident)["version"] == 3
    again = built(w, rows=ROWS[:-1], curve=False)
    assert again.staged.data[1] == table_sha(w, ident)
    again.staged.discard()


def test_a_cancelled_rebuild_leaves_the_report_as_it_was(w: W) -> None:
    ident = make(w)["id"]
    before = rd(w, st.info, ident)
    sha = table_sha(w, ident)
    b = built(w, rows=ROWS[:-1])
    with pytest.raises(RuntimeError, match="cancel"):
        with dialect.write_tx(w.project.engine) as conn:
            st.mark_building(conn, ident, None)
            st.replace(conn, w.store, ident, b)
            raise RuntimeError("cancel")  # the job is cancelled inside the write: everything rolls back
    b.staged.discard()
    after = rd(w, st.info, ident)
    assert (after["version"], after["state"], after["rows"]) == (before["version"], "ready", before["rows"])
    assert table_sha(w, ident) == sha and rd(w, st.read_curve, w.store, ident) == CURVE


def test_rebuild_params_are_the_stored_ones(w: W) -> None:
    ident = make(w, "Ресурсы", cutoffs=[None, 0.5, 1.5], filter_id=w.flt)["id"]
    got = rd(w, st.rebuild_params, ident)
    stored = rd(w, st.info, ident)["params"]
    assert got == {"params": stored, "name": "Ресурсы", "save": True, "report_id": ident, "expected_version": 1}
    assert stored["cutoffs"] == [None, 0.5, 1.5] and stored["filter_id"] == w.flt
    assert spec.parse_params(got["params"]).params_hash() == rd(w, st.info, ident)["params_hash"]


# --- PostgreSQL (BF_TEST_PG; SQLite always) --------------------------------------------------------------------------


@pytest.mark.db
def test_the_store_on_both_databases(db_url: str, tmp_path: Path) -> None:
    w = world(tmp_path, db_url)
    try:
        ident = make(w, "Ресурсы")["id"]
        other = make(w, "Ресурсы")
        assert other["name"] == "Ресурсы (2)"
        got = rd(w, st.info, ident, store=w.store)
        assert (got["state"], got["version"], got["rows"], got["params"]["model_id"]) == ("ready", 1, 6, w.model)
        assert sd.table_rows(rd(w, st.read_table, w.store, ident)) == ROWS
        assert rd(w, st.read_curve, w.store, ident) == CURVE and rd(w, st.read_control, w.store, ident) == CONTROL
        assert rd(w, st.read_rows, w.store, ident, 2, 4)["rows"] == ROWS[4:]
        with dialect.write_tx(w.project.engine) as conn:
            st.mark_building(conn, ident)
        assert rd(w, st.list_reports)[0]["state"] == "building"
        with dialect.write_tx(w.project.engine) as conn:
            st.settle(conn, ident)
            st.replace(conn, w.store, ident, built(w, rows=ROWS[:3]))
            assert st.rename(conn, ident, "Новое", 2)["version"] == 3
        assert [(r["name"], r["rows"]) for r in rd(w, st.list_reports)] == [("Новое", 3), ("Ресурсы (2)", 6)]
        with dialect.write_tx(w.project.engine) as conn:
            st.delete(conn, ident)
            st.delete(conn, other["id"])
        assert rd(w, st.list_reports) == []
        with dialect.read_tx(w.project.engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(lineage).where(
                lineage.c.dst_kind == "object:resource_report")).scalar() == 0
    finally:
        w.project.engine.dispose()
