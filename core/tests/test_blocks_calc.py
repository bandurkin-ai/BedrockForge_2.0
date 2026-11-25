"""The calculator of a block model: the job `blocks.calc` and its routes (подшаг 17.9; ТЗ 8.7.4; ADR 0023 §7, № 69).

«Оставить» and NaN outside the restrictor, the type and the unit of the field (f8 / f4 / i4 / i2 / i1 / cat, the
rounding of whole numbers, null for gaps), «устарел» by the inputs, 422 with the place in the text, the replacement of
a field (only with `replace`), a cancel leaves nothing, the portions of any size give the same bits, the same on
PostgreSQL (`-m db`, BF_TEST_PG)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa
from fastapi import APIRouter

from bf_core.api.errors import ApiError
from bf_core.blocks import calc_job, routes_calc
from bf_core.blocks import fields_store as fs
from bf_core.blocks import store as bm
from bf_core.blocks.model import Model
from bf_core.db import dialect, migrate
from bf_core.db.schema import blob_owners
from bf_core.jobs.context import JobCancelled
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.dbutil import make_engine
from tests.test_archive import wait_job
from tests.test_blocks_fields_store import FIELDS, GRID, RG, make_model, sample, tmp_clean
from tests.test_filters_store import cond

API = "/api/v1"
BL = API + "/blocks"


@pytest.fixture()
def cc(monkeypatch: pytest.MonkeyPatch, pclient):  # type: ignore[no-untyped-def]
    """A core with an open project and a model of 350 rows; the job and the routes of the calculator are mounted
    until the lines of the orchestrator name them (`jobs/registry.py`, `blocks/router.py`)."""
    from bf_core.jobs.registry import HANDLER_MODULES

    if "bf_core.blocks.calc_job" not in HANDLER_MODULES:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.blocks.calc_job")
    from bf_core.jobs import registry

    registry.load_handlers()  # the API process knows the kind (the worker imports it by the same variable)
    if not any(getattr(r, "path", "") == f"{BL}/calc/check" for r in pclient.app.routes):
        mount = APIRouter(prefix="/blocks")
        mount.include_router(routes_calc.router)
        pclient.app.include_router(mount, prefix=API)
    assert pclient.post(f"{API}/projects", json={"name": "Калькулятор"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    pclient.model_id = make_model(pclient.project, pclient.store)
    return pclient


# --- helpers --------------------------------------------------------------------------------------


def opened(c: Any, model_id: int | None = None) -> Model:
    with dialect.read_tx(c.project.engine) as conn:
        return Model.open(conn, c.store, model_id or c.model_id)


def column(c: Any, name: str, model_id: int | None = None) -> np.ndarray:
    return opened(c, model_id).columns([name])[name]


def run(c: Any, chunk: int = 1 << 20, tick: Any = None, **body: Any) -> dict[str, Any]:
    """The job in this process on the current version of the model."""
    m = opened(c, body.pop("model_id", None))
    params = {"model_id": m.id, "expected_version": body.pop("expected_version", m.version), **body}
    kw = {} if tick is None else {"tick": tick}
    return calc_job.run_calc(c.project.engine, c.project.folder, params, chunk=chunk, **kw)


def spec_of(c: Any, name: str) -> dict[str, Any]:
    return opened(c).field(name)


def same(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.array_equal(a, b, equal_nan=True))


def away(x: np.ndarray) -> np.ndarray:
    """Half away from zero, NaN stays."""
    return np.sign(x) * np.floor(np.abs(x) + 0.5)


def sha_of(c: Any, role: str = "cells") -> str:
    with dialect.read_tx(c.project.engine) as conn:
        return bm.cells_sha(conn, c.model_id, role)


def blob_roles(c: Any) -> set[str]:
    with dialect.read_tx(c.project.engine) as conn:
        return set(conn.execute(sa.select(blob_owners.c.role).where(blob_owners.c.owner_id == c.model_id)).scalars())


def refused(c: Any, **body: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        run(c, **body)
    return e.value


VALUES = sample()[1]
AU, CU, N = VALUES["AU"], VALUES["CU"].astype(np.float32).astype(np.float64), VALUES["N"]
INSIDE = AU > 1.0  # NaN is false


# --- the job: values, source, version -------------------------------------------------------------


def test_the_job_over_http_writes_a_field_and_its_source(cc) -> None:  # type: ignore[no-untyped-def]
    cells = sha_of(cc)
    body = {"expected_version": 1, "expr": "IF(AU > 1, AU*2, 0)", "field": "AU2", "unit": "г/т",
            "description": "Удвоенное"}  # fmt: skip
    r = cc.post(f"{BL}/{cc.model_id}/calc", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(cc, r.json()["source"], r.json()["id"])
    log = cc.get(f"{API}/jobs/{r.json()['source']}/{r.json()['id']}/log").text
    assert job["status"] == "done", log
    out = job["result"]
    expected = np.where(np.isnan(AU), np.nan, np.where(AU > 1, AU * 2, 0.0))  # a gap in the condition: a gap
    assert same(column(cc, "AU2"), expected)
    assert (out["model_id"], out["field"], out["version"], out["rows"]) == (cc.model_id, "AU2", 2, 350)
    assert out["passed"] == 350 and out["missing"] == int(np.isnan(AU).sum()) and out["dtype"] == "f8"
    assert out["events"] == [["objects.changed", {"kind": "block_model", "id": cc.model_id}]]
    assert set(out["stages"]) <= {"read", "calc", "write"}
    f = spec_of(cc, "AU2")
    assert (f["dtype"], f["unit"], f["description"], f["role"]) == ("f8", "г/т", "Удвоенное", None)
    assert f["file"] != "cells" and sha_of(cc) == cells  # the cells are not touched
    src = f["source"]
    assert src["op"] == "calc" and src["algo"] == calc_job.ALGO and src["inputs"] == [cells]
    assert src["params"] == {"expr": "IF(AU > 1, AU*2, 0)", "text": "IF(AU > 1, AU * 2, 0)", "dtype": "f8",
                             "filter_id": None, "restrict": None, "outside": "keep"}  # fmt: skip
    assert set(src["versions"]) == {"numpy", "numexpr", "numba"} and src["versions"]["numpy"]


@pytest.mark.parametrize("outside", ["keep", "nan"])
def test_outside_of_the_restrictor_a_new_field_is_empty(cc, outside: str) -> None:  # type: ignore[no-untyped-def]
    out = run(cc, expr="AU * 2", field="NEW", restrict={"expr": "AU > 1"}, outside=outside)
    assert same(column(cc, "NEW"), np.where(INSIDE, AU * 2, np.nan))  # not 0
    assert out["passed"] == int(INSIDE.sum())
    assert spec_of(cc, "NEW")["source"]["params"]["restrict"] == {"expr": "AU > 1"}


def test_outside_keep_leaves_the_old_value_of_a_replaced_field_and_nan_removes_it(cc) -> None:  # type: ignore[no-untyped-def]
    rule = {"restrict": {"expr": "AU > 1"}, "replace": True}
    run(cc, expr="AU * 100", field="CU", outside="keep", dtype="f8", **rule)
    assert same(column(cc, "CU"), np.where(INSIDE, AU * 100, CU))  # old float32 values stay outside
    assert spec_of(cc, "CU")["dtype"] == "f8"
    run(cc, expr="1", field="N", outside="nan", **rule)  # the old gaps stay gaps, the others go
    assert same(column(cc, "N"), np.where(INSIDE, 1.0, np.nan))


def test_a_filter_of_the_library_and_a_quick_restrictor_pass_together(cc) -> None:  # type: ignore[no-untyped-def]
    f = cc.post(f"{API}/filters", json={"name": "AU выше 1", "rules": cond("AU", ">", 1)})
    assert f.status_code == 200, f.text
    out = run(cc, expr="AU", field="A", filter_id=f.json()["id"], restrict={"expr": "N > 20"})
    both = INSIDE & (N > 20)
    assert same(column(cc, "A"), np.where(both, AU, np.nan)) and out["passed"] == int(both.sum())
    gone = refused(cc, expr="AU", field="B", filter_id=99999)
    assert gone.status == 404 and gone.code == "filter_not_found"
    absent = refused(cc, expr="AU", field="B", restrict={"expr": "NOPE > 1"})
    assert absent.status == 422 and absent.code in ("expr_field", "filter_field_missing")


# --- the type and the unit ------------------------------------------------------------------------


def test_f4_keeps_single_precision_and_i2_rounds_half_away_from_zero(cc) -> None:  # type: ignore[no-untyped-def]
    run(cc, expr="AU / 3", field="S", dtype="f4", unit="%")
    assert same(column(cc, "S"), (AU / 3).astype(np.float32).astype(np.float64))
    f = spec_of(cc, "S")
    assert (f["dtype"], f["unit"]) == ("f4", "%")
    run(cc, expr="N / 2 - 0.25", field="H", dtype="i2")  # …x.25 and …x.75 → the whole number, half away from zero
    run(cc, expr="N / 2", field="H2", dtype="i4")  # exact halves: 0.5 → 1, 12.5 → 13
    assert same(column(cc, "H"), away(N / 2 - 0.25))
    assert same(column(cc, "H2"), away(N / 2)) and spec_of(cc, "H2")["dtype"] == "i4"
    run(cc, expr="-(N / 2)", field="H3", dtype="i1")
    assert same(column(cc, "H3"), away(-(N / 2)))  # −0.5 → −1; a gap is empty (null), not 0
    assert int(np.isnan(column(cc, "H3")).sum()) == int(np.isnan(N).sum())


def test_a_whole_number_that_does_not_fit_the_type_refuses_and_leaves_nothing(cc) -> None:  # type: ignore[no-untyped-def]
    roles = blob_roles(cc)
    err = refused(cc, expr="N * 10", field="BIG", dtype="i1")
    assert err.status == 422 and err.code == "field_values_invalid"
    assert opened(cc).version == 1 and blob_roles(cc) == roles and tmp_clean(cc.store)
    assert "BIG" not in [f["name"] for f in opened(cc).fields]


def test_the_default_type_follows_the_result_and_a_category_comes_from_text(cc) -> None:  # type: ignore[no-untyped-def]
    run(cc, expr="AU > 1", field="FLAG")  # a condition — i1 with 1 and 0
    assert spec_of(cc, "FLAG")["dtype"] == "i1" and same(column(cc, "FLAG"), INSIDE.astype(np.float64))
    run(cc, expr="IF(AU > 1, 'руда', 'порода')", field="TYPE", unit=None)
    m = opened(cc)
    assert m.field("TYPE")["dtype"] == "cat" and m.labels("TYPE") == ["руда", "порода"]
    expected = np.where(np.isnan(AU), -1, np.where(AU > 1, 0, 1))
    assert same(m.columns(["TYPE"])["TYPE"], expected.astype(np.int32))  # a gap is −1, never a code of a label


def test_a_category_from_a_number_and_a_number_from_text_are_expr_type_at_the_root(cc) -> None:  # type: ignore[no-untyped-def]
    a = refused(cc, expr="AU * 2", field="X", dtype="cat")
    assert (a.status, a.code, a.extra["pos"]) == (422, "expr_type", 0)
    b = refused(cc, expr="IF(AU > 1, 'a', 'b')", field="X", dtype="f8")
    assert (b.status, b.code) == (422, "expr_type") and b.extra["param"] == "expr"


def test_keep_maps_the_old_labels_into_the_new_ones_and_a_category_over_a_number_warns(cc) -> None:  # type: ignore[no-untyped-def]
    old = column(cc, "DOM")  # руда 0, порода 1, дайка 2
    out = run(cc, expr="IF(AU > 1, 'руда', 'новая')", field="DOM", restrict={"expr": "AU > 1"}, replace=True)
    m = opened(cc)
    assert m.labels("DOM") == ["руда", "новая", "порода", "дайка"] and m.field("DOM")["role"] == "code"
    lut = np.array([0, 2, 3], dtype=np.int32)
    expected = np.where(INSIDE, 0, np.where(old >= 0, lut[np.clip(old, 0, None)], -1))
    assert same(m.columns(["DOM"])["DOM"], expected.astype(np.int32)) and out["warnings"] == []
    swapped = run(cc, expr="IF(AU > 1, 'да', 'нет')", field="N", restrict={"expr": "AU > 1"}, replace=True)
    assert any("другого типа" in w for w in swapped["warnings"])
    assert same(opened(cc).columns(["N"])["N"], np.where(INSIDE, 0, -1).astype(np.int32))


# --- replacement (№ 69) ---------------------------------------------------------------------------


def test_an_existing_name_needs_replace_and_replace_keeps_the_place_and_the_role(cc) -> None:  # type: ignore[no-untyped-def]
    names = [f["name"] for f in opened(cc).fields]
    err = refused(cc, expr="AU + 1", field="au")  # the names are the same without the case
    assert (err.status, err.code, err.extra) == (409, "field_exists", {"field": "AU"})
    assert opened(cc).version == 1
    res = run(cc, expr="AU + 1", field="Au", replace=True)  # an expression may read the field it replaces
    m = opened(cc)
    assert res["field"] == "Au" and [f["name"] for f in m.fields] == [("Au" if n == "AU" else n) for n in names]
    assert same(m.columns(["Au"])["Au"], AU + 1) and m.version == 2
    f = m.field("Au")
    assert f["role"] == "grade" and f["unit"] is None and f["source"]["op"] == "calc"  # the unit is not inherited


# --- the errors with the place in the text --------------------------------------------------------


def test_check_gives_the_kind_the_type_and_the_fields(cc) -> None:  # type: ignore[no-untyped-def]
    def check(expr: str, **extra: Any) -> Any:
        return cc.post(f"{BL}/calc/check", json={"model_id": cc.model_id, "expr": expr, **extra})

    got = check("if(au>1, Cu * 2, 0)").json()
    assert got == {"text": "IF(au > 1, Cu * 2, 0)", "fields_used": ["AU", "CU"], "out_kind": "number", "dtype": "f8"}
    assert check("AU > 1 and N < 5").json()["out_kind"] == "bool" and check("AU > 1").json()["dtype"] == "i1"
    cat = check("IF(AU > 1, 'руда', 'порода')").json()
    assert (cat["out_kind"], cat["dtype"]) == ("category", "cat")
    assert check("AU * 2", dtype="f4").json()["dtype"] == "f4"
    bad = check("AU * 2", dtype="cat")
    assert (bad.status_code, bad.json()["code"], bad.json()["pos"]) == (422, "expr_type", 0)
    assert check("AU", dtype="x").json()["param"] == "dtype"
    assert cc.post(f"{BL}/calc/check", json={"model_id": 9999, "expr": "1"}).json()["code"] == "block_model_not_found"
    assert cc.post(f"{BL}/calc/check", json={"model_id": cc.model_id, "expr": "  "}).json()["param"] == "expr"


def test_422_carries_the_place_of_the_error(cc) -> None:  # type: ignore[no-untyped-def]
    r = cc.post(f"{BL}/calc/check", json={"model_id": cc.model_id, "expr": "AU + * 2"})
    body = r.json()
    assert r.status_code == 422 and body["code"] == "expr_syntax" and body["pos"] == 5
    assert (body["line"], body["column"], body["char"]) == (1, 6, "*") and body["message"] and body["hint"]
    f = cc.post(f"{BL}/calc/check", json={"model_id": cc.model_id, "expr": "AU + [Нет такого] + AUU"}).json()
    assert (f["code"], f["pos"], f["field"], f["param"]) == ("expr_field", 5, "Нет такого", "expr")
    t = cc.post(f"{BL}/calc/check", json={"model_id": cc.model_id, "expr": "DOM + 1"}).json()
    assert t["code"] == "expr_type" and isinstance(t["pos"], int)
    hack = cc.post(f"{BL}/calc/check", json={"model_id": cc.model_id, "expr": "__import__('os')"})
    assert hack.status_code == 422 and hack.json()["code"] in ("expr_syntax", "expr_field")  # nothing was executed
    over = {"expected_version": 1, "expr": "AU +", "field": "Z"}
    again = cc.post(f"{BL}/{cc.model_id}/calc", json=over)
    assert again.status_code == 422 and again.json()["code"] == "expr_syntax" and again.json()["pos"] == 4


def test_the_route_checks_before_it_starts_the_job(cc) -> None:  # type: ignore[no-untyped-def]
    base = {"expected_version": 1, "expr": "AU * 2", "field": "Z"}

    def post(**over: Any) -> Any:
        return cc.post(f"{BL}/{cc.model_id}/calc", json={**base, **over})

    taken = post(field="CU")
    assert (taken.status_code, taken.json()["code"], taken.json()["field"]) == (409, "field_exists", "CU")
    stale = post(expected_version=7)
    assert (stale.status_code, stale.json()["code"], stale.json()["version"]) == (409, "version_conflict", 1)
    assert post(dtype="u8").json()["param"] == "dtype" and post(outside="zero").json()["param"] == "outside"
    assert post(replace="yes").json()["param"] == "replace" and post(field="").json()["param"] == "field"
    missing = post(restrict={"expr": "NOPE > 1"})
    assert missing.status_code == 422 and missing.json()["code"] == "filter_field_missing"
    lost = cc.post(f"{BL}/99999/calc", json=base)
    assert (lost.status_code, lost.json()["code"]) == (404, "block_model_not_found")
    assert opened(cc).version == 1 and [f["name"] for f in opened(cc).fields] == [f["name"] for f in FIELDS]


def test_a_stale_version_in_the_job_is_a_409_and_changes_nothing(cc) -> None:  # type: ignore[no-untyped-def]
    run(cc, expr="1", field="ONE")
    err = refused(cc, expr="2", field="TWO", expected_version=1)
    assert (err.status, err.code, err.extra) == (409, "version_conflict", {"version": 2})
    assert opened(cc).version == 2 and tmp_clean(cc.store)


# --- «устарел» ------------------------------------------------------------------------------------


def stale(c: Any, name: str) -> bool:
    m = opened(c)
    with dialect.read_tx(c.project.engine) as conn:
        return calc_job.is_stale(conn, c.store, m, m.field(name))


def test_a_field_is_stale_when_an_input_file_changes_and_fresh_again_when_rebuilt(cc) -> None:  # type: ignore[no-untyped-def]
    run(cc, expr="AU * 2", field="A2")
    run(cc, expr="CU + N", field="CN", restrict={"expr": "AU > 1"})
    assert not stale(cc, "A2") and not stale(cc, "CN") and not stale(cc, "AU")  # AU is an import: not a calc field
    run(cc, expr="AU + 1", field="AU", replace=True)  # a new file of AU
    assert stale(cc, "A2") and stale(cc, "CN")  # CN reads AU through the restrictor
    assert not stale(cc, "AU")  # its own file is not its input: reading itself does not make it stale
    src = spec_of(cc, "A2")["source"]["params"]
    run(cc, field="A2", replace=True, **{k: v for k, v in src.items() if k in ("expr", "dtype", "outside")})
    assert not stale(cc, "A2") and stale(cc, "CN")
    old_algo = spec_of(cc, "A2")
    old_algo["source"] = {**old_algo["source"], "algo": "calc-0"}
    with dialect.read_tx(cc.project.engine) as conn:
        assert calc_job.is_stale(conn, cc.store, opened(cc), old_algo)  # another algorithm of the calculator


def test_a_field_is_stale_when_its_filter_changes(cc) -> None:  # type: ignore[no-untyped-def]
    f = cc.post(f"{API}/filters", json={"name": "Фильтр", "rules": cond("AU", ">", 1)}).json()
    run(cc, expr="AU", field="F", filter_id=f["id"])
    assert not stale(cc, "F")
    put = cc.put(f"{API}/filters/{f['id']}", json={"expected_version": f["version"], "rules": cond("AU", ">", 2)})
    assert put.status_code == 200, put.text
    assert stale(cc, "F")


def test_a_field_whose_input_is_gone_is_stale(cc) -> None:  # type: ignore[no-untyped-def]
    run(cc, expr="CU * 2", field="C2")
    with dialect.write_tx(cc.project.engine) as conn:
        fs.delete_fields(conn, cc.model_id, opened(cc).version, ["CU"])
    assert stale(cc, "C2")


# --- the portions, the cancel ---------------------------------------------------------------------


def test_the_portion_size_does_not_change_the_bits(cc) -> None:  # type: ignore[no-untyped-def]
    rule = {"expr": "IF(AU > 1, SQRT(ABS(CU)) * 3 + N, COALESCE(N, 7) / 3)", "restrict": {"expr": "N < 40"}}
    run(cc, field="WHOLE", chunk=1 << 20, **rule)
    for step in (1, 77, RG, 349):
        run(cc, field=f"P{step}", chunk=step, **rule)
        assert same(column(cc, f"P{step}"), column(cc, "WHOLE")), step
    inner = np.where(AU > 1, np.sqrt(np.abs(CU)) * 3 + N, np.where(np.isnan(N), 7.0, N) / 3)
    inner = np.where(np.isnan(AU), np.nan, inner)  # a gap in the condition is a gap
    assert same(column(cc, "WHOLE"), np.where(N < 40, inner, np.nan))  # a gap of N is outside the restrictor


def test_a_cancel_leaves_nothing(cc) -> None:  # type: ignore[no-untyped-def]
    roles, version = blob_roles(cc), opened(cc).version
    seen: list[str | None] = []

    def tick(_p: float | None = None, message: str | None = None) -> None:
        seen.append(message)
        if seen.count(None) >= 3:  # after two portions of the calculation
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        run(cc, expr="AU * 2", field="CANCEL", chunk=64, tick=tick)
    assert "Расчёт" in seen and "Запись" not in seen
    m = opened(cc)
    assert m.version == version and blob_roles(cc) == roles and tmp_clean(cc.store)
    assert "CANCEL" not in [f["name"] for f in m.fields]


def test_a_failed_commit_leaves_nothing(cc, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    roles = blob_roles(cc)

    def boom(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(calc_job, "commit_fields", boom)
    with pytest.raises(RuntimeError):
        run(cc, expr="AU * 2", field="LOST")
    assert opened(cc).version == 1 and blob_roles(cc) == roles
    assert "LOST" not in [f["name"] for f in opened(cc).fields]


def test_a_write_that_does_not_open_leaves_no_staged_file(cc, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    def broken(_engine: Any) -> Any:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(dialect, "write_tx", broken)
    with pytest.raises(RuntimeError):
        run(cc, expr="AU * 2", field="LOST")
    monkeypatch.undo()
    tmp = cc.store.tmp_dir
    assert not tmp.exists() or not any(tmp.iterdir())
    assert opened(cc).version == 1


def test_a_job_that_fails_reports_the_code_and_the_place(cc) -> None:  # type: ignore[no-untyped-def]
    # the values the type cannot hold are found while the job writes: the failure of the job keeps the code
    r = cc.post(f"{BL}/{cc.model_id}/calc", json={"expected_version": 1, "expr": "N * 10", "field": "BIG",
                                                  "dtype": "i1"})  # fmt: skip
    assert r.status_code == 200
    job = wait_job(cc, r.json()["source"], r.json()["id"])
    assert job["status"] == "failed" and job["error"]["code"] == "field_values_invalid"
    assert opened(cc).version == 1 and tmp_clean(cc.store)


# --- PostgreSQL (BF_TEST_PG; SQLite always) -------------------------------------------------------


@pytest.mark.db
def test_the_job_on_both_databases(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    store = BlobStore(tmp_path / "blobs")
    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    idx, values = sample()
    with bm.CellsWriterV2(store, GRID, FIELDS, row_group=RG) as w:
        w.write(idx, values)
        staged = w.finish()
    with dialect.write_tx(engine) as conn:
        bm_id = bm.commit_block_model(conn, store, staged, name="Модель")
    base = {"model_id": bm_id, "expected_version": 1, "expr": "IF(AU > 1, AU * 2, 0)", "field": "AU2",
            "restrict": {"expr": "N > 10"}, "outside": "nan"}  # fmt: skip
    out = calc_job.run_calc(engine, tmp_path / "blobs", base)
    assert out["version"] == 2
    with dialect.read_tx(engine) as conn:
        m = Model.open(conn, store, bm_id)
        assert calc_job.is_stale(conn, store, m, m.field("AU2")) is False
    au, n = values["AU"], values["N"]
    expected = np.where(n > 10, np.where(np.isnan(au), np.nan, np.where(au > 1, au * 2, 0.0)), np.nan)
    assert same(m.columns(["AU2"])["AU2"], expected)
    with pytest.raises(ApiError) as e:  # 409: the version moved
        calc_job.run_calc(engine, tmp_path / "blobs", {**base, "field": "AU3"})
    assert e.value.status == 409
    engine.dispose()
