"""The API and the job of the resource reports (подшаг 20.7; ТЗ 8.10; ADR 0027 решения 14–16, 28, 30; docs/progress/
e20-api.md §3, §5): the whole way through the routes (preview → save → list → page → form → rename → delete), Т8.10-1
through the API (12 500 t, 2,5 g/t, 31,25 kg = 1,004711 thousand ounces), the preview in a project «только просмотр»,
the 404 / 409 / 422 / 423 of the routes in Russian, the cancel at every stage (a new report is not there, a rebuilt one
is as it was), «Перестроить» — the same bits as from scratch, with any portion of rows, the atomic write, the
templates, the stubs of the export / the drill / the comparison, the registration of the job, the same on PostgreSQL
(`-m db`, BF_TEST_PG)."""

# ruff: noqa: F811 - the fixture `ra` is imported, and the tests take it by its name
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, lineage, objects
from bf_core.jobs.context import JobCancelled, JobError
from bf_core.resources import job as rj
from bf_core.resources import store as st
from bf_core.storage.blobs import BlobStore
from tests.resapi_world import (  # noqa: F401 - the fixture
    BODY,
    RS,
    finish,
    get,
    many_groups,
    model,
    ok,
    post,
    ra,
    read_only,
    refused,
    row,
    run,
)
from tests.test_import_job import wait_job
from tests.test_resources_result import close


def reports(c: Any) -> list[dict[str, Any]]:
    return ok(c.get(f"{RS}/reports"))["reports"]  # type: ignore[no-any-return]


def count(c: Any, kind: str = "resource_report") -> int:
    with dialect.read_tx(c.project.engine) as conn:
        return int(
            conn.execute(sa.select(sa.func.count()).select_from(objects).where(objects.c.kind == kind)).scalar_one()
        )


def data_sha(c: Any, report_id: int) -> str:
    with dialect.read_tx(c.project.engine) as conn:
        return st.data_sha(conn, report_id)


def graph_rows(c: Any, report_id: int) -> int:
    with dialect.read_tx(c.project.engine) as conn:
        return int(
            conn.execute(
                sa.select(sa.func.count())
                .select_from(lineage)
                .where(lineage.c.dst_kind == "object:resource_report", lineage.c.dst_id == report_id)
            ).scalar_one()
        )


def kinds_of(c: Any, name: str) -> list[Any]:
    return [d for n, d in c.events if n == name]


# --- the way through the routes -------------------------------------------------------------------


def test_defaults_without_a_project(pclient) -> None:  # type: ignore[no-untyped-def]
    got = ok(pclient.get(f"{RS}/defaults"))
    assert got["params"]["curve"] == 100 and got["params"]["cutoffs"] == [None] and "bench.h" in got["ranges"]


def test_preview_then_save_list_page_form_rename_delete(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    prev = run(ra, {"model_id": mid, **BODY, "template_id": "jorc"})
    assert prev["saved"] is False and prev["report_id"] is None and prev["version"] == 0 and not prev.get("events")
    assert count(ra) == 0 and reports(ra) == []  # a preview writes nothing
    t = row(prev)  # Т8.10-1
    assert (t["blocks"], t["volume"], t["tonnes"]) == (5, 5000.0, 12_500.0)
    assert t["elements"]["AU"]["grade"] == 2.5 and t["elements"]["AU"]["metal"] == 31_250.0
    assert prev["totals"]["tonnes"] == 12_500.0 and prev["rows_total"] == len(prev["rows"]) and not prev["truncated"]
    assert prev["model"]["id"] == mid and prev["algo"] == "resources-1" and prev["params"]["model_id"] == mid
    assert [e["field"] for e in prev["elements"]] == ["AU"] and prev["density"] == {"field": None, "value": 2.5}
    assert prev["keys"] == [{"kind": "field", "field": "CLASS",
                             "labels": ["Измеренные", "Выявленные", "Предполагаемые"]}]  # fmt: skip
    assert prev["control"]["sum_groups"]["ok"] is True and prev["excluded"]["filtered"] == 0
    assert set(prev["seconds"]) >= {"read", "kernel", "control", "total"}
    cells = {r["label"]: r["cells"] for r in prev["form"]["sections"][0]["rows"]}["Итого"]  # the form of JORC
    assert cells["tonnes"]["value"] == 12.5 and cells["grade:AU"]["text"] == "2,50"
    assert math.isclose(cells["metal:AU"]["value"], 1.004711, rel_tol=1e-6)

    done = run(ra, {"model_id": mid, **BODY, "curve": 20}, save=True, name="Подсчёт")
    rid = done["report_id"]
    assert done["saved"] is True and done["name"] == "Подсчёт" and done["version"] == 1 and rid
    assert done["events"] == [["resources.changed", {"kind": "resource_report", "id": rid}]]
    assert row(done)["tonnes"] == 12_500.0 and len(done["curve"]["cutoffs"]) == 20
    assert count(ra) == 1 and graph_rows(ra, rid) >= 2  # the object and the rows of the graph (the model, the fields)
    listed = reports(ra)
    assert [(r["id"], r["name"], r["state"], r["version"]) for r in listed] == [(rid, "Подсчёт", "ready", 1)]
    assert listed[0]["totals"]["tonnes"] == 12_500.0 and listed[0]["graph"]["stale"] is False
    assert listed[0]["model"] == {"id": mid, "name": "Модель"}

    page = get(ra, rid, template="jorc")  # the stored report: the page, the form, the curve, the control
    assert page["rows_total"] == done["rows_total"] and page["rows"] == done["rows"] and page["page"] == 1
    assert page["curve"] == done["curve"] and page["control"]["sum_groups"]["ok"] is True
    assert page["excluded"] == done["excluded"] and page["stale"] is False and page["state"] == "ready"
    assert page["params_hash"] == done["params_hash"] and page["keys"][0]["labels"][0] == "Измеренные"
    assert page["form"]["sections"][0]["rows"][-1]["cells"]["tonnes"]["text"] in ("13", "12,5")
    assert page["form"]["sections"][0]["rows"][-1]["cells"] == prev["form"]["sections"][0]["rows"][-1]["cells"]
    small = get(ra, rid, page=2, size=3)
    assert small["rows"] == done["rows"][3:6] and small["size"] == 3 and small["form"] is None

    r = ra.put(f"{RS}/reports/{rid}", json={"name": "Ресурсы 2026", "expected_version": 1})
    assert ok(r) == {"id": rid, "version": 2, "name": "Ресурсы 2026"}
    assert reports(ra)[0]["name"] == "Ресурсы 2026"
    assert ok(ra.delete(f"{RS}/reports/{rid}")) == {"id": rid, "deleted": True}
    assert count(ra) == 0 and graph_rows(ra, rid) == 0 and reports(ra) == []
    assert kinds_of(ra, "resources.changed")[-2:] == [
        {"kind": "resource_report", "id": rid},
        {"kind": "resource_report", "id": rid, "deleted": True},
    ]


def test_names_are_made_free_not_refused(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    names = [run(ra, {"model_id": mid, **BODY}, save=True, name="Отчёт")["name"] for _ in range(2)]
    assert names == ["Отчёт", "Отчёт (2)"]
    assert run(ra, {"model_id": mid, **BODY}, save=True)["name"] == "Отчёт по ресурсам"


def test_big_preview_is_cut_and_a_saved_report_is_read_by_pages(ra) -> None:  # type: ignore[no-untyped-def]
    mid = many_groups(ra)
    body = {"model_id": mid, "elements": ["AU"], "cutoffs": {"from": 0.1, "to": 5.0, "step": 0.05}, "density": 2.5,
            "groups": ["DOM"], "curve": 0}  # fmt: skip
    prev = run(ra, body)
    assert prev["truncated"] is True and len(prev["rows"]) == 20_000 and prev["rows_total"] > 20_000
    assert any(w["code"] == "rows_truncated" for w in prev["warnings"])
    done = run(ra, body, save=True)
    assert done["truncated"] is False and len(done["rows"]) == 500 and done["rows_total"] == prev["rows_total"]
    assert not any(w["code"] == "rows_truncated" for w in done["warnings"])
    last = get(ra, done["report_id"], page=1, size=5000)
    assert len(last["rows"]) == 5000 and last["rows"][:500] == done["rows"]
    tail = get(ra, done["report_id"], page=6, size=5000)
    assert len(tail["rows"]) == done["rows_total"] - 25_000 and tail["rows_total"] == done["rows_total"]


# --- «только просмотр» ----------------------------------------------------------------------------


def test_read_only_project_previews_but_does_not_write(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    saved = run(ra, {"model_id": mid, **BODY}, save=True)["report_id"]
    back = read_only(ra)
    try:
        assert row(run(ra, {"model_id": mid, **BODY}))["tonnes"] == 12_500.0  # the preview works
        assert get(ra, saved)["rows_total"] > 0 and len(reports(ra)) == 1  # so does the reading
        refused(post(ra, {"model_id": mid, **BODY}, save=True), 423, "project_read_only")
        refused(ra.put(f"{RS}/reports/{saved}", json={"name": "Х", "expected_version": 1}), 423, "project_read_only")
        refused(ra.delete(f"{RS}/reports/{saved}"), 423, "project_read_only")
        refused(ra.put(f"{RS}/templates/my", json={"template": {"name": "Х", "labels": ["A"]}}), 423,
                "project_read_only")  # fmt: skip
        refused(ra.delete(f"{RS}/templates/my"), 423, "project_read_only")
    finally:
        back()
    assert count(ra) == 1


# --- the refusals ---------------------------------------------------------------------------------


def test_refusals_before_the_job_in_russian(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    ok_body = {"model_id": mid, **BODY}
    refused(ra.post(f"{RS}/reports", json=[]), 422, "bad_request")
    refused(ra.post(f"{RS}/reports", json={}), 422, "bad_param", "params")
    refused(post(ra, {**ok_body, "cutoffs": [1, "a"]}), 422, "bad_param", "cutoffs.1")
    refused(post(ra, {**ok_body, "curve": 1}), 422, "bad_param", "curve")
    refused(post(ra, {**ok_body, "groups": ["CLASS", "CLASS"]}), 422, "bad_param", "groups.1")
    refused(post(ra, {**ok_body, "density": 0}), 422, "bad_param")
    refused(post(ra, {**ok_body, "groups": ["AU"]}), 422, "group_numeric", "groups.0")
    refused(post(ra, {**ok_body, "groups": ["NOPE"]}), 404, "block_field_not_found", "groups.0")
    refused(post(ra, {**ok_body, "elements": ["NOPE"]}), 404, "block_field_not_found", "elements.0.field")
    refused(post(ra, {**ok_body, "model_id": 99_999}), 404, "block_model_not_found")
    refused(post(ra, {**ok_body, "filter_id": 99_999}), 404, "filter_not_found")
    refused(post(ra, {**ok_body, "template_id": "nope"}), 404, "template_not_found")
    q = {"cond": {"field": "PIT", "op": "<=", "value": 3}}
    assert refused(post(ra, {**ok_body, "restrict": q}), 422, "filter_field_missing")["field"] == "PIT"
    refused(post(ra, {**ok_body, "bench": {"h": 0}, "groups": [{"kind": "bench"}]}), 422, "bad_param", "bench.h")
    refused(ra.get(f"{RS}/reports/99999"), 404, "resource_report_not_found")
    refused(ra.get(f"{RS}/reports/99999", params={"template": "jorc"}), 404, "resource_report_not_found")
    refused(ra.put(f"{RS}/reports/99999", json={"name": "Х", "expected_version": 1}), 404, "resource_report_not_found")
    refused(ra.delete(f"{RS}/reports/99999"), 404, "resource_report_not_found")
    assert count(ra) == 0


def test_no_project_is_409(pclient) -> None:  # type: ignore[no-untyped-def]
    refused(post(pclient, {"model_id": 1, **BODY}), 409, "no_project")
    refused(pclient.get(f"{RS}/reports"), 409, "no_project")


def test_too_big_is_422(ra) -> None:  # type: ignore[no-untyped-def]
    mid = many_groups(ra, 3000)  # 3001 groups × 152 bins × 5 sums > 2·10⁶ cells
    body = {"model_id": mid, "elements": ["AU"], "density": 2.5, "groups": ["DOM"], "curve": 0,
            "cutoffs": [0.01 * i for i in range(150)]}  # fmt: skip
    got = refused(post(ra, body), 422, "report_too_big")
    assert got["message"] and got["hint"]


def test_version_building_and_name_conflicts(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    a = run(ra, {"model_id": mid, **BODY}, save=True, name="А")["report_id"]
    b = run(ra, {"model_id": mid, **BODY}, save=True, name="Б")["report_id"]
    assert refused(ra.put(f"{RS}/reports/{a}", json={"name": "В", "expected_version": 7}), 409,
                   "version_conflict")["version"] == 1  # fmt: skip
    refused(ra.put(f"{RS}/reports/{a}", json={"name": "б", "expected_version": 1}), 409, "name_taken", "name")
    refused(ra.put(f"{RS}/reports/{a}", json={"name": "В"}), 422, "bad_param", "expected_version")
    refused(ra.put(f"{RS}/reports/{a}", json={"expected_version": 1}), 422, "bad_param", "name")
    with dialect.write_tx(ra.project.engine) as conn:
        st.mark_building(conn, b)
    assert reports(ra)[1]["state"] == "building"
    refused(ra.put(f"{RS}/reports/{b}", json={"name": "Б2", "expected_version": 1}), 409, "report_building")
    refused(ra.delete(f"{RS}/reports/{b}"), 409, "report_building")
    assert get(ra, b)["state"] == "building"  # the stored rows are still read
    with dialect.write_tx(ra.project.engine) as conn:
        st.settle(conn, b)
    assert ok(ra.delete(f"{RS}/reports/{b}"))["deleted"] is True


# --- the cancel -----------------------------------------------------------------------------------


class Cancel:
    """A tick that cancels when a message of the stage is shown (`at` — the start of the text), or at the n-th call."""

    def __init__(self, at: str | None = None, nth: int | None = None) -> None:
        self.at, self.nth, self.calls = at, nth, 0

    def __call__(self, _p: float | None = None, m: str | None = None) -> None:
        self.calls += 1
        if (self.at is not None and m is not None and m.startswith(self.at)) or self.nth == self.calls:
            raise JobCancelled()


def direct(c: Any, params: dict[str, Any], tick: Any = None, **kw: Any) -> dict[str, Any]:
    return rj.run_report(c.project.engine, c.project.folder, params, tick or rj._noop, **kw)  # noqa: SLF001


def test_cancel_at_every_stage_leaves_nothing_of_a_new_report(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    p = {"params": {"model_id": mid, **BODY, "curve": 10}, "save": True, "name": "Отмена"}
    for tick in (Cancel("Чтение"), Cancel("Расчёт"), Cancel("Запись: файлы"), Cancel("Запись: запись"),
                 Cancel(nth=3)):  # fmt: skip
        with pytest.raises(JobCancelled):
            direct(ra, p, tick, chunk=3)
        assert count(ra) == 0 and reports(ra) == []
        with dialect.read_tx(ra.project.engine) as conn:
            assert (
                conn.execute(
                    sa.select(sa.func.count())
                    .select_from(blob_owners)
                    .where(blob_owners.c.role == "data", blob_owners.c.owner_kind == "object")
                ).scalar_one()
                == 0
            )
    assert direct(ra, p, chunk=3)["saved"] is True and count(ra) == 1  # and it can still be made


def test_cancel_of_a_rebuild_leaves_the_report_as_it_was(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    rid = run(ra, {"model_id": mid, **BODY, "curve": 10}, save=True)["report_id"]
    before, listed = data_sha(ra, rid), reports(ra)[0]
    with dialect.read_tx(ra.project.engine) as conn:
        again = st.rebuild_params(conn, rid)
    for tick in (Cancel("Расчёт"), Cancel("Запись: запись")):
        with pytest.raises(JobCancelled) as e:
            direct(ra, again, tick, job_id=None)
        assert e.value.events == [["resources.changed", {"kind": "resource_report", "id": rid}]]
        now = reports(ra)[0]
        assert now["state"] == "ready" and now["version"] == 1 and data_sha(ra, rid) == before
        assert now["totals"] == listed["totals"]


def test_failures_are_job_errors_in_russian_and_settle_the_report(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    rid = run(ra, {"model_id": mid, **BODY}, save=True)["report_id"]
    with dialect.read_tx(ra.project.engine) as conn:
        again = st.rebuild_params(conn, rid)
    ctx = SimpleNamespace(params={**again, "expected_version": 9}, project_folder=ra.project.folder, id=5,
                          engine=lambda: ra.project.engine, tick=lambda *_a: None)  # fmt: skip
    with pytest.raises(JobError) as e:
        rj.report_job(ctx)  # type: ignore[arg-type]
    assert e.value.code == "version_conflict" and e.value.message and e.value.hint and not e.value.retry
    gone = SimpleNamespace(**{**ctx.__dict__, "params": {**again, "expected_version": 1, "report_id": 99_999}})
    with pytest.raises(JobError) as e2:
        rj.report_job(gone)  # type: ignore[arg-type]
    assert e2.value.code == "resource_report_not_found"
    assert reports(ra)[0]["state"] == "ready"  # the mark was never put (the first check failed) or was taken off


def test_a_model_that_changed_during_the_pass_is_refused(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)

    def tick(_p: float | None = None, m: str | None = None) -> None:
        if m and m.startswith("Запись: запись"):
            with dialect.write_tx(ra.project.engine) as conn:
                conn.execute(sa.update(objects).where(objects.c.id == mid).values(version=objects.c.version + 1))

    with pytest.raises(ApiError) as e:
        direct(ra, {"params": {"model_id": mid, **BODY}, "save": True}, tick)
    assert (e.value.status, e.value.code) == (409, "model_changed") and e.value.hint
    assert count(ra) == 0


def test_too_many_reports_refused_by_the_write_and_nothing_stays(ra, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    run(ra, {"model_id": mid, **BODY}, save=True)
    monkeypatch.setattr(st, "MAX_REPORTS", 1)
    with pytest.raises(ApiError) as e:
        direct(ra, {"params": {"model_id": mid, **BODY}, "save": True})
    assert e.value.code == "too_many_reports" and count(ra) == 1


# --- «Перестроить» --------------------------------------------------------------------------------


def test_rebuild_is_the_same_bits_as_from_scratch_with_any_portion(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    body = {"model_id": mid, "elements": ["AU"], "cutoffs": [None, 0.5, 1.0], "density": 2.5, "curve": 12,
            "groups": ["CLASS", {"kind": "bench", "h": 7}], "bench": {"h": 7},
            "balance": {"cutoff": 1.0, "off_cutoff": 0.5}}  # fmt: skip
    first = run(ra, body, save=True, name="Раз")
    rid, sha = first["report_id"], data_sha(ra, rid := first["report_id"])
    with dialect.read_tx(ra.project.engine) as conn:
        again = st.rebuild_params(conn, rid)
    assert again["save"] is True and again["report_id"] == rid and again["expected_version"] == 1
    for chunk in (1, 3, 1 << 20):
        out = direct(ra, again, chunk=chunk)  # a rebuild with any portion: version + 1, the same `data`
        again = {**again, "expected_version": out["version"]}
        assert out["report_id"] == rid and out["name"] == "Раз" and out["rows"] == first["rows"]
        assert data_sha(ra, rid) == sha and out["curve"] == first["curve"] and out["control"] == first["control"]
    assert out["version"] == 4 and count(ra) == 1 and len(reports(ra)) == 1
    other = run(ra, body, save=True, name="Два")  # from scratch with another portion: the same file
    assert data_sha(ra, other["report_id"]) == sha
    assert direct(ra, {"params": body, "save": True, "name": "Три"}, chunk=2)["rows"] == first["rows"]


def test_rebuild_through_the_graph_after_the_data_changed(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    rid = run(ra, {"model_id": mid, **BODY}, save=True)["report_id"]
    sha = data_sha(ra, rid)
    r = ra.post(
        f"{RS.replace('/resources', '/lineage')}/rebuild",
        json={"node": {"kind": "object:resource_report", "id": rid}, "with_dependents": False},
    )
    assert r.status_code == 202, r.text
    job = wait_job(ra, r.json()["source"], r.json()["id"], timeout=120)
    assert job["status"] == "done", job
    rep = reports(ra)[0]
    assert (
        rep["version"] == 2 and rep["state"] == "ready" and data_sha(ra, rid) == sha
    )  # the same numbers: the same file
    assert ["resources.changed", {"kind": "resource_report", "id": rid}] in job["result"]["events"]


# --- the templates, the export and the rest of the contract ---------------------------------------


def test_templates_through_the_routes(ra) -> None:  # type: ignore[no-untyped-def]
    got = ok(ra.get(f"{RS}/templates"))["templates"]
    assert [(t["id"], t["builtin"], t["version"]) for t in got] == [("jorc", True, 1), ("gkz", True, 1)]
    jorc = ok(ra.get(f"{RS}/templates/jorc"))["template"]
    assert jorc["labels"][0] == "Измеренные" and jorc["builtin"] is True and jorc["id"] == "jorc"
    refused(ra.get(f"{RS}/templates/nope"), 404, "template_not_found")
    mine = {"name": "Свой", "labels": ["Руда", "Порода"], "subtotals": [], "total": "Всего"}
    assert ok(ra.put(f"{RS}/templates/svoy", json={"template": mine})) == {"id": "svoy", "version": 1}
    assert ok(ra.put(f"{RS}/templates/svoy", json={"template": mine, "expected_version": 1}))["version"] == 2
    assert refused(ra.put(f"{RS}/templates/svoy", json={"template": mine, "expected_version": 1}), 409,
                   "version_conflict")["version"] == 2  # fmt: skip
    refused(ra.put(f"{RS}/templates/svoy", json={"name": "Х"}), 422, "bad_param", "template")
    refused(ra.put(f"{RS}/templates/svoy", json={"template": {"name": "", "labels": []}}), 422, "bad_param")
    assert [t["id"] for t in ok(ra.get(f"{RS}/templates"))["templates"]] == ["jorc", "gkz", "svoy"]
    refused(ra.delete(f"{RS}/templates/svoy", params={"expected_version": 1}), 409, "version_conflict")
    refused(ra.delete(f"{RS}/templates/jorc"), 422, "bad_param")
    assert ok(ra.delete(f"{RS}/templates/svoy", params={"expected_version": 2})) == {"id": "svoy", "deleted": True}
    refused(ra.delete(f"{RS}/templates/svoy", params={"expected_version": 1}), 404, "template_not_found")
    assert [d["kind"] for d in kinds_of(ra, "resources.changed")] == ["template"] * 3
    # a template of the project in a report: the form is made by it, the report does not change
    mid = model(ra)
    ok(ra.put(f"{RS}/templates/svoy", json={"template": {**mine, "key": "class"}}))
    done = run(ra, {"model_id": mid, **BODY, "template_id": "svoy"}, save=True)
    labels = [r["label"] for r in done["form"]["sections"][0]["rows"]]
    assert labels[:2] == ["Руда", "Порода"] and labels[-1] == "Всего"
    assert any(w.startswith("Группы вне шаблона") for w in done["form"]["warnings"])


def test_the_gkz_form_has_the_balance_columns(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    body = {"model_id": mid, **BODY, "cutoffs": [None], "balance": {"cutoff": 1.0, "off_cutoff": 0.5}}
    done = run(ra, body, save=True)
    form = get(ra, done["report_id"], template="gkz")["form"]
    assert {c["group"] for c in form["columns"]} == {"Балансовые", "Забалансовые"}
    via_job = run(ra, {**body, "template_id": "gkz"})["form"]
    assert via_job["columns"] == form["columns"] and via_job["sections"] == form["sections"]


def test_export_is_checked_and_waits_for_its_job(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    rid = run(ra, {"model_id": mid, **BODY}, save=True)["report_id"]
    path = str(Path(ra.project.folder).parent / "out.xlsx")
    refused(ra.post(f"{RS}/reports/{rid}/export", json={"format": "pdf", "path": path}), 422, "bad_param", "format")
    refused(ra.post(f"{RS}/reports/{rid}/export", json={"format": "xlsx"}), 422, "bad_param", "path")
    refused(ra.post(f"{RS}/reports/99999/export", json={"format": "xlsx", "path": path}), 404,
            "resource_report_not_found")  # fmt: skip
    from bf_core.jobs.registry import KINDS

    if "resources.export" not in KINDS:  # 20.5 registers it: until then the route says so
        got = refused(ra.post(f"{RS}/reports/{rid}/export", json={"format": "xlsx", "path": path}), 501,
                      "not_implemented")  # fmt: skip
        assert got["hint"]
    back = read_only(ra)  # the export writes outside the project: it is not refused in «только просмотр»
    try:
        assert ra.post(f"{RS}/reports/{rid}/export", json={"format": "xlsx", "path": path}).status_code != 423
    finally:
        back()


def test_drill_and_compare_have_their_paths_and_checks(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    rid = run(ra, {"model_id": mid, **BODY}, save=True)["report_id"]
    cell = {"group": ["Измеренные"], "cutoff": 1.0}
    refused(ra.post(f"{RS}/reports/{rid}/drill", json={"group": ["Х"], "cutoff": 1.0, "kind": "nope"}), 422,
            "bad_param", "kind")  # fmt: skip
    refused(ra.post(f"{RS}/reports/99999/drill", json=cell), 404, "resource_report_not_found")
    refused(ra.post(f"{RS}/reports/{rid}/drill/rows", json={**cell, "size": 5000}), 422, "bad_param", "size")
    refused(ra.post(f"{RS}/compare", json={"report_a": rid}), 422, "bad_param", "report_b")
    refused(ra.post(f"{RS}/compare", json={"report_a": rid, "report_b": 99_999}), 404, "resource_report_not_found")
    for path, body in ((f"reports/{rid}/drill", cell), (f"reports/{rid}/drill/rows", cell),
                       ("compare", {"report_a": rid, "report_b": rid})):  # fmt: skip
        r = ra.post(f"{RS}/{path}", json=body)
        if r.status_code == 501:  # until 20.9 / 20.10
            assert r.json()["code"] == "not_implemented" and r.json()["hint"]


# --- the registration -----------------------------------------------------------------------------


def test_the_job_and_the_router_are_registered() -> None:
    from bf_core.api.modules import ROUTER_MODULES
    from bf_core.jobs import registry

    assert "resources" in ROUTER_MODULES and "bf_core.resources.job" in registry.HANDLER_MODULES
    registry.load_handlers()
    kind = registry.KINDS["resources.report"]
    assert kind.queue == "quick" and kind.estimate_memory({}) == 640 and kind.max_attempts == 1


def test_the_modules_of_the_api_do_not_load_the_kernels_at_import() -> None:
    code = ("import sys, bf_core.resources.router, bf_core.resources.job; "
            "raise SystemExit(3 if {'numpy', 'numba', 'pyarrow'} & set(sys.modules) else 0)")  # fmt: skip
    run_ = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert run_.returncode == 0, run_.stderr[-400:]


def test_warm_step_is_named_in_both_profiles() -> None:
    from bf_core.ops import warm

    assert "resources" in warm.QUICK and "resources" in warm.HEAVY and "resources" in warm.OTHER


# --- Т8.10 through the job on the sums of the table -----------------------------------------------


def test_the_form_is_the_same_numbers_as_the_rows(ra) -> None:  # type: ignore[no-untyped-def]
    mid = model(ra)
    done = run(ra, {"model_id": mid, **BODY, "template_id": "jorc"}, save=True)
    rows = {tuple(r["g"] or []): r for r in done["rows"] if r["kind"] == "cutoff"}
    form = {r["label"]: r["cells"] for r in done["form"]["sections"][0]["rows"]}
    for label in ("Измеренные", "Выявленные", "Предполагаемые"):
        t = rows[(label,)]["tonnes"]
        assert close(form[label]["tonnes"]["value"], t * 1e-3)
    assert close(form["Изм. + Выяв."]["tonnes"]["value"], (2500.0 + 5000.0) * 1e-3)
    assert close(form["Итого"]["tonnes"]["value"], 12.5)


# --- PostgreSQL (BF_TEST_PG; SQLite always) -------------------------------------------------------


@pytest.mark.db
def test_the_job_on_both_databases(db_url: str, tmp_path: Path) -> None:
    from bf_core.db import migrate
    from tests.dbutil import make_engine
    from tests.test_blocks_report import put
    from tests.test_resources_result import AU8, CLASS, GRID8

    engine = make_engine(db_url, tmp_path)
    try:
        migrate.upgrade(engine, "project")
        project = SimpleNamespace(engine=engine, uid=f"test:{tmp_path}:{db_url}", folder=tmp_path)
        c = SimpleNamespace(project=project, store=BlobStore(tmp_path))
        fields = [{"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"}, CLASS]
        import numpy as np

        mid = put(c, GRID8, fields, np.arange(8, dtype=np.uint64), {"AU": AU8, "CLASS": np.zeros(8, dtype=np.int32)})
        p = {"params": {"model_id": mid, **BODY, "curve": 8}, "save": True, "name": "Ресурсы"}
        first = rj.run_report(engine, tmp_path, p)
        assert row(first)["tonnes"] == 12_500.0 and first["version"] == 1
        with dialect.read_tx(engine) as conn:
            sha = st.data_sha(conn, first["report_id"])
            again = st.rebuild_params(conn, first["report_id"])
        out = rj.run_report(engine, tmp_path, again, chunk=3)
        with dialect.read_tx(engine) as conn:
            assert out["version"] == 2 and st.data_sha(conn, first["report_id"]) == sha
            assert [r["name"] for r in st.list_reports(conn)] == ["Ресурсы"]
        with pytest.raises(JobCancelled):
            rj.run_report(engine, tmp_path, {**p, "name": "Другой"}, Cancel("Запись: запись"))
        with dialect.read_tx(engine) as conn:
            assert len(st.list_reports(conn)) == 1
    finally:
        engine.dispose()
