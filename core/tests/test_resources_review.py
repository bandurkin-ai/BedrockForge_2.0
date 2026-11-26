"""The findings of the review of the core of the resource reports (подшаг 20.18б; ADR 0027): every test failed before
its fix. A project really opened «только просмотр» (the lock is held, the scheduler has no project), the ranges of
the parameters (no `OverflowError`), the graph that changed during the pass, a value that is not finite, the header of
a CSV, a file that appears while the export is written, the portion of the pass, the limit of the reports before the
pass, and the sums of the tables taken once (the same bits as `range_sums` of each table)."""

# ruff: noqa: F811 - the fixture `ra` is imported, and the tests take it by its name
from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.lock import ProjectLock
from bf_core.projects.service import projects_of
from bf_core.resources import compute as cp
from bf_core.resources import control as ct
from bf_core.resources import drill as dr
from bf_core.resources import export_job as ej
from bf_core.resources import job as rj
from bf_core.resources import kernels as rk
from bf_core.resources import result as rr
from bf_core.resources import spec, spec_parts
from bf_core.resources import store as st
from bf_core.resources import store_data as sd
from tests.resapi_world import BODY, RS, get, model, ok, post, ra, read_only, refused, row, run  # noqa: F401
from tests.test_blocks_calc import cond
from tests.test_resources_api import count, direct
from tests.test_resources_result import prepared, report
from tests.test_resources_spec import refused as spec_refused
from tests.test_resources_spec import resolved

API = "/api/v1"


# --- 1, 2: a project really opened «только просмотр» ----------------------------------------------------------


def really_read_only(c: Any) -> Any:
    """Closes the open project and opens it again while another holder keeps its lock (ТЗ 5.7): the project is
    read-only and the scheduler has no project. → the function that lets the lock go."""
    uid, folder = c.project.uid, Path(c.project.folder)
    assert c.post(f"{API}/projects/close").status_code == 200
    holder = ProjectLock(folder / "project.lock")
    assert holder.acquire()
    opened = c.post(f"{API}/projects/{uid}/open")
    assert opened.status_code == 200 and opened.json()["read_only"] is True, opened.text
    c.project = projects_of(c.core).require_current()
    assert c.project.read_only
    return holder.release


def test_a_project_really_read_only_answers_423_in_russian_not_500(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    rid = run(ra, {"model_id": mid, **BODY}, save=True)["report_id"]
    out = Path(ra.project.folder).parent / "out.xlsx"
    release = really_read_only(ra)
    try:
        for save in (False, True):
            got = refused(post(ra, {"model_id": mid, **BODY}, save=save), 423, "project_read_only")
            assert got["hint"] and (save or "для записи" in got["hint"])  # «Откройте проект для записи…»
        got = refused(ra.post(f"{RS}/reports/{rid}/export", json={"format": "xlsx", "path": str(out)}), 423,
                      "project_read_only")  # fmt: skip
        assert got["hint"] and not out.exists()
        assert get(ra, rid)["rows_total"] > 0  # the reading works
    finally:
        release()


def test_the_export_of_a_read_only_project_does_not_write_into_it(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    rid = run(ra, {"model_id": mid, **BODY}, save=True)["report_id"]
    folder = Path(ra.project.folder)
    release = really_read_only(ra)
    try:
        with dialect.read_tx(ra.project.engine) as conn:
            params = ej.request(conn, folder, rid, {"format": "xlsx", "path": str(folder.parent / "ro.xlsx")})
        # the route says `record: false` of such a project; the engine cannot write, the export must not try
        out = ej.run_export(ra.project.engine, folder, {**params, "record": False})
        assert out["recorded"] is False and Path(out["path"]).is_file() and "events" not in out
    finally:
        release()
    with dialect.read_tx(ra.project.engine) as conn:
        assert st.info(conn, rid)["files"] == []


def test_the_route_marks_the_export_of_a_read_only_project_as_not_recorded(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    rid = run(ra, {"model_id": mid, **BODY}, save=True)["report_id"]
    path = Path(ra.project.folder).parent / "flag.xlsx"
    back = read_only(ra)  # the flag (the scheduler still holds the project): the job runs, but must not record
    try:
        started = ra.post(f"{RS}/reports/{rid}/export", json={"format": "xlsx", "path": str(path)})
        assert started.status_code == 200, started.text
        from tests.resapi_world import finish

        job = finish(ra, started.json(), timeout=120)
        assert job["status"] == "done" and job["result"]["recorded"] is False, job
    finally:
        back()
    with dialect.read_tx(ra.project.engine) as conn:
        assert st.info(conn, rid)["files"] == []


# --- 5: no OverflowError ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("step", [1e-320, 1e-300, 1e-12])
def test_a_tiny_step_of_the_cutoffs_is_a_bad_param(step: float) -> None:
    got = spec_refused({"model_id": 1, "cutoffs": {"from": 0, "to": 1e12, "step": step}})
    assert got == "cutoffs.step"


@pytest.mark.parametrize("h", [1e-320, 1e-300, 1e-7])
def test_a_tiny_bench_height_is_a_bad_param(h: float) -> None:
    assert spec_refused({"model_id": 1, "groups": [{"kind": "bench"}], "bench": {"h": h}}) == "bench.h"


def test_a_bench_range_that_does_not_fit_a_float_is_a_bad_param() -> None:
    with pytest.raises(ApiError) as e:
        resolved({"groups": [{"kind": "bench"}], "bench": {"h": 1e-6}}, z=(-1e308, 1e308))
    assert (e.value.status, e.value.code, e.value.extra) == (422, "bad_param", {"param": "bench.h"})
    assert e.value.message and e.value.hint
    bench = spec_parts.Bench(1e-6)
    with pytest.raises(ApiError) as e2:
        spec_parts.resolve_bench(bench, (-1e308, 1e308))
    assert e2.value.code == "bad_param"


def test_the_range_of_h_in_the_contract_has_the_lower_limit() -> None:
    assert spec.ranges_json()["bench.h"]["lo"] == 1e-6


# --- 6: the inputs that changed during the pass ------------------------------------------------------------------


def test_a_filter_changed_during_the_pass_is_model_changed(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    made = ra.post(f"{API}/filters", json={"name": "AU выше 1", "rules": cond("AU", ">", 1)})
    assert made.status_code == 200, made.text
    flt = made.json()

    def tick(_p: float | None = None, m: str | None = None) -> None:
        if m and m.startswith("Расчёт") and not tick.done:
            tick.done = True
            put = ra.put(
                f"{API}/filters/{flt['id']}", json={"expected_version": flt["version"], "rules": cond("AU", ">", 4)}
            )
            assert put.status_code == 200, put.text

    tick.done = False  # type: ignore[attr-defined]
    body = {"params": {"model_id": mid, **BODY, "filter_id": flt["id"]}, "save": True}
    with pytest.raises(ApiError) as e:
        direct(ra, body, tick)
    assert tick.done  # type: ignore[attr-defined]
    assert (e.value.status, e.value.code) == (409, "model_changed") and e.value.hint
    assert count(ra) == 0
    done = direct(ra, body)  # the same request with the filter as it is now: a report, current by the new rules
    assert done["saved"] is True and count(ra) == 1


# --- 7: a value that is not finite -----------------------------------------------------------------------------


def _huge(c: Any, mid: int) -> tuple[Any, Any, Any]:
    """A pass whose accumulators hold a mean that does not fit a double: the sum 1e300 over the weight 1e-10."""
    model_, plan, r = prepared(c, mid, {"elements": ["AU"], "cutoffs": [None], "density": 2.5, "curve": 0})
    sums = cp.new_sums(plan.core)
    sums.s0[1, 0:4] = (1.0, 1e-10, 1e-10, 1e300)
    sums.n0[1, :] = 1
    return model_, plan, sums


def test_a_non_finite_cell_is_a_gap_with_a_warning(ra) -> None:  # type: ignore[no-untyped-def]
    _model, plan, sums = _huge(ra, model(ra))
    res = rr.build(sums, plan)
    cell = res.rows[0]["elements"]["AU"]
    assert cell["grade"] is None and cell["sum"] == 1e300 and cell["wt"] == 1e-10  # a gap, never inf or 0
    warn = [w for w in res.warnings if w["code"] == "value_not_finite"]
    assert len(warn) == 1 and warn[0]["message"] and warn[0]["hint"] and "AU" in warn[0]["message"]
    table = rj._table(sums, plan)  # noqa: SLF001 - the Parquet of `data` is made of every row
    assert table.column("AU:grade").to_pylist() == [None] and table.column("AU:wt").to_pylist() == [1e-10]


def test_a_non_finite_value_does_not_fail_the_write(ra, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    _model, plan, sums = _huge(ra, mid)
    monkeypatch.setattr(cp, "run", lambda *_a, **_k: sums)  # the job in this process (`direct`), not in a worker
    body = {"model_id": mid, "elements": ["AU"], "cutoffs": [None], "density": 2.5, "curve": 0}
    prev = direct(ra, {"params": body})
    assert prev["rows"][0]["elements"]["AU"]["grade"] is None
    assert any(w["code"] == "value_not_finite" for w in prev["warnings"])
    saved = direct(ra, {"params": body, "save": True})
    assert saved["saved"] is True and any(w["code"] == "value_not_finite" for w in saved["warnings"])
    page = get(ra, saved["report_id"])
    assert page["rows"][0]["elements"]["AU"]["grade"] is None


def test_the_writer_of_the_table_takes_an_infinity_for_a_gap() -> None:
    table = sd.build_table({"g1": [None], "total": [True], "kind": ["all"], "cutoff": [None], "cutoff_to": [None],
                            "blocks": [1], "volume": [math.inf], "tonnes": [-math.inf]}, [], 1)  # fmt: skip
    assert table.column("volume").to_pylist() == [None] and table.column("tonnes").to_pylist() == [None]


# --- 8: the header of a CSV ---------------------------------------------------------------------------------------


def test_the_header_of_the_csv_of_the_drill_is_not_a_formula() -> None:
    text = dr._csv([], ["=cmd|' /C calc'!A0", "Уступ"], ["+AU", "-CU", "@X", "AG"])  # noqa: SLF001
    head = next(csv.reader(io.StringIO(text), delimiter=";"))
    keys, elements = head[len(dr.HEAD) : len(dr.HEAD) + 2], head[len(dr.HEAD) + 2 :]
    assert keys == ["'=cmd|' /C calc'!A0", "Уступ"] and elements == ["'+AU", "'-CU", "'@X", "AG"]
    assert head[: len(dr.HEAD)] == list(dr.HEAD)  # the fixed titles are as they were


# --- 9: a file that appears while the export is written ----------------------------------------------------------


def test_a_file_that_appears_during_the_export_is_not_replaced(ra, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    rid = run(ra, {"model_id": mid, **BODY}, save=True)["report_id"]
    folder = Path(ra.project.folder)
    target = folder.parent / "late.csv"
    with dialect.read_tx(ra.project.engine) as conn:
        params = ej.request(conn, folder, rid, {"format": "csv", "path": str(target)})
    original = ej._write_csv  # noqa: SLF001

    def late(*a: Any, **k: Any) -> int:
        n = original(*a, **k)
        target.write_text("чужой файл", encoding="utf-8")  # appears after the check of the start, before the swap
        return n

    monkeypatch.setattr(ej, "_write_csv", late)
    with pytest.raises(ApiError) as e:
        ej.run_export(ra.project.engine, folder, params)
    assert (e.value.status, e.value.code) == (409, "files_exist") and e.value.hint
    assert target.read_text(encoding="utf-8") == "чужой файл"
    assert not [p for p in target.parent.iterdir() if p.name.endswith(".part")]
    monkeypatch.undo()
    with dialect.read_tx(ra.project.engine) as conn:
        again = ej.request(conn, folder, rid, {"format": "csv", "path": str(target), "overwrite": True})
    assert ej.run_export(ra.project.engine, folder, again)["recorded"] is True  # with `overwrite` it is replaced


# --- 10: the portion of the pass ---------------------------------------------------------------------------------


def test_a_file_with_more_rows_than_the_meta_says_does_not_write_out_of_bounds(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    body = {"elements": ["AU"], "cutoffs": [None, 1.0], "density": 2.5, "groups": ["CLASS"], "curve": 0}
    model_, plan, r = prepared(ra, mid, body)
    want = cp.run(model_, plan.core, r, chunk=8)
    model_.rows = 3  # the meta says 3 rows, the file has 8 (a portion is bigger than `rows`)
    got = cp.run(model_, plan.core, r, chunk=8)
    for name in ("s0", "c0", "n0", "sg", "cg", "ng"):
        assert np.array_equal(getattr(got, name), getattr(want, name)), name
    assert got.excluded == want.excluded


# --- 13: the limit of the reports is checked before the pass -----------------------------------------------------


def test_too_many_reports_is_refused_before_the_job(ra, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    run(ra, {"model_id": mid, **BODY}, save=True)
    monkeypatch.setattr(st, "MAX_REPORTS", 1)
    got = refused(post(ra, {"model_id": mid, **BODY}, save=True), 422, "too_many_reports")
    assert got["hint"] and count(ra) == 1
    assert row(run(ra, {"model_id": mid, **BODY}))["tonnes"] == 12_500.0  # a preview needs no room


# --- 4: the sums of the tables are taken once ---------------------------------------------------------------------


def _ref_rows(sums: cp.Sums, plan: rr.Plan) -> list[dict[str, Any]]:
    """`iter_rows` as it was: `range_sums` of every table, anew."""
    spec_ = plan.spec
    present = rr.present_groups(sums)
    labels = [list(spec_.group_labels(int(c))) for c in present]  # type: ignore[arg-type]
    gs, gc, gn = sums.group_view()
    out: list[dict[str, Any]] = []
    for r in rr.row_ranges(plan):
        q, n = rk.range_sums(*sums.total_view(), r.lo, r.hi)
        out.append(rr._row([None] * plan.nkeys, True, r, q[0], n[0], plan))  # noqa: SLF001
        if len(present):
            qg, ng = rk.range_sums(gs, gc, gn, r.glo, r.ghi)
            out += [rr._row(lab, False, r, qg[c], ng[c], plan) for c, lab in zip(present, labels, strict=True)]  # noqa: SLF001
    return out


CASE = {"elements": ["AU"], "cutoffs": [None, 0.5, 1.0, 2.0, 3.0], "density": 2.5, "curve": 12,
        "groups": ["CLASS", {"kind": "bench", "h": 7}], "bench": {"h": 7},
        "balance": {"cutoff": 1.0, "off_cutoff": 0.5}}  # fmt: skip


def test_the_rows_from_the_suffix_sums_are_the_same_bits(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    _plan, sums, _res = report(ra, mid, CASE)
    plan = _plan
    assert list(rr.iter_rows(sums, plan)) == _ref_rows(sums, plan)  # == on floats: bit for bit
    assert len(_ref_rows(sums, plan)) > 40


def test_the_sums_of_the_groups_are_taken_once_for_every_user(ra, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    calls: list[tuple[int, int]] = []
    real = rk.range_sums

    def counting(s: Any, c: Any, n: Any, lo: int, hi: int) -> Any:
        calls.append((lo, hi))
        return real(s, c, n, lo, hi)

    def used(cutoffs: list[float | None]) -> int:
        plan, sums, _res = report(ra, mid, {**CASE, "cutoffs": cutoffs})
        calls.clear()
        monkeypatch.setattr(rk, "range_sums", counting)
        try:
            for _ in range(2):  # the rows of the answer and the rows of the table of `data`
                list(rr.iter_rows(sums, plan))
            ct.sum_groups(sums, plan)
            ct.sum_groups(sums, plan)
            rr.curve_of(sums, plan)
        finally:
            monkeypatch.setattr(rk, "range_sums", real)
        return len(calls)

    few = used([None, 1.0])
    many = used([None, *[0.1 * i for i in range(1, 30)]])
    assert many == few <= 4, (few, many)  # «all» and the off-balance table only, whatever the cut-offs
